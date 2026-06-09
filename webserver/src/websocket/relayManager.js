/**
 * Relay control channel — the outbound tunnel for users' own Home Assistant.
 *
 * This is deliberately **separate** from the public, UID-keyed switch socket
 * (./manager.js).  That socket is fire-and-forget and unauthenticated; a relay
 * carries privileged HA calls, so it gets its own path (`/ws/relay`), real
 * connect-time authentication, and a request/response (RPC) protocol.
 *
 * Flow:
 *   1. The Vome component dials `wss://sync.vome.io/ws/relay` presenting a
 *      per-instance relay secret (`Authorization: Bearer <secret>`).
 *   2. We authenticate it via the portal (utils/relayPortal.verifySecret) which
 *      returns the owning `server_id`; we register `server_id → ws`.
 *   3. The portal POSTs an HA call to our internal dispatch endpoint
 *      (routes/internal-routes.js → dispatch()).  We send an `ha_rpc` down the
 *      socket, await the component's `ha_rpc_response`, and return it.
 *
 * Scope note: the registry is in-process, like the switch rooms in ./manager.js.
 * Because the API server and this WS server run in the same Node process, the
 * dispatch route can call dispatch() directly.  Running multiple backend
 * processes would need a Redis route (same limitation, and same fix, as the
 * existing switch rooms) — see docs/DEVELOPER_GUIDE.md.
 */
const WebSocket = require('ws');
const { v4: uuidv4 } = require('uuid');
const logger = require('../utils/logger');
const config = require('../config/config');
const relayPortal = require('../utils/relayPortal');

const HEARTBEAT_INTERVAL_MS = 30000;
const IDLE_PING_AFTER_MS = 30000;
const MIN_RPC_MS = 1000;
const MAX_RPC_MS = 60000;
const RPC_BUFFER_MS = 2000;

/** Extract the bearer secret from a WS upgrade request (header, then ?secret=). */
function extractSecret(req) {
	const header = (req.headers && req.headers.authorization) || '';
	if (typeof header === 'string' && header.toLowerCase().startsWith('bearer ')) {
		return header.slice(7).trim();
	}
	try {
		const parsed = new URL(req.url, 'http://placeholder');
		return (parsed.searchParams.get('secret') || '').trim();
	} catch (_err) {
		return '';
	}
}

class RelayManager {
	constructor() {
		this.wss = null;
		this.connections = new Map(); // server_id -> { ws, connectedAt, lastActivity }
		this.pending = new Map(); // requestId -> { resolve, timer }
		// Injectable so tests don't need a live portal.
		this.verifyFn = (secret) => relayPortal.verifySecret(secret);
	}

	initialize(server, opts = {}) {
		if (typeof opts.verifyFn === 'function') {
			this.verifyFn = opts.verifyFn;
		}
		this.wss = new WebSocket.Server({
			server,
			path: '/ws/relay',
			verifyClient: (info, cb) => {
				const secret = extractSecret(info.req);
				if (!secret) {
					cb(false, 401, 'Unauthorized');
					return;
				}
				Promise.resolve(this.verifyFn(secret))
					.then((serverId) => {
						if (!serverId) {
							cb(false, 401, 'Unauthorized');
							return;
						}
						info.req.relayServerId = serverId;
						cb(true);
					})
					.catch((err) => {
						logger.error('Relay verifyClient error:', err.message || err);
						cb(false, 500, 'Verification error');
					});
			}
		});

		this.wss.on('connection', (ws, req) => this.handleConnection(ws, req));
		logger.info('Relay manager initialized');
	}

	handleConnection(ws, req) {
		const serverId = req.relayServerId;
		if (!serverId) {
			ws.close(4001, 'Unauthorized');
			return;
		}

		// One live relay per server: replace a stale/previous connection.
		const prev = this.connections.get(serverId);
		if (prev && prev.ws !== ws) {
			try {
				prev.ws.close(4000, 'Replaced by a newer connection');
			} catch (_err) { /* ignore */ }
		}

		this.connections.set(serverId, { ws, connectedAt: Date.now(), lastActivity: Date.now() });
		logger.info(`Relay connected: ${serverId}`);
		this._safeSend(ws, { type: 'hello', server_id: serverId });

		ws.on('message', (message) => this.handleMessage(serverId, message));
		ws.on('close', () => this.handleDisconnection(serverId, ws));
		ws.on('error', (error) => logger.error(`Relay error for ${serverId}:`, error.message || error));
		ws.on('pong', () => {
			const conn = this.connections.get(serverId);
			if (conn) {
				conn.lastActivity = Date.now();
			}
		});
	}

	handleMessage(serverId, message) {
		let data;
		try {
			data = JSON.parse(message);
		} catch (_err) {
			logger.warn(`Relay ${serverId}: non-JSON message ignored`);
			return;
		}
		const conn = this.connections.get(serverId);
		if (conn) {
			conn.lastActivity = Date.now();
		}

		if (data.type === 'ha_rpc_response') {
			const waiter = this.pending.get(data.requestId);
			if (waiter) {
				clearTimeout(waiter.timer);
				this.pending.delete(data.requestId);
				waiter.resolve({
					status: data.status || 0,
					body: data.body,
					error: data.error
				});
			}
			return;
		}
		if (data.type === 'ping' && conn) {
			this._safeSend(conn.ws, { type: 'pong', timestamp: Date.now() });
		}
	}

	handleDisconnection(serverId, ws) {
		const conn = this.connections.get(serverId);
		if (conn && conn.ws === ws) {
			this.connections.delete(serverId);
			logger.info(`Relay disconnected: ${serverId}`);
		}
	}

	isConnected(serverId) {
		const conn = this.connections.get(serverId);
		return !!(conn && conn.ws.readyState === WebSocket.OPEN);
	}

	/**
	 * Push one HA call to a connected component and await its reply.
	 *
	 * Resolves to ``{ offline: true }`` when no component is connected, or
	 * ``{ status, body, error }`` otherwise (``status: 0`` on a timeout / send
	 * failure).  Never rejects — the caller maps the shape onto an HTTP response.
	 */
	dispatch(serverId, { method, path, body, expect, timeout } = {}) {
		return new Promise((resolve) => {
			const conn = this.connections.get(serverId);
			if (!conn || conn.ws.readyState !== WebSocket.OPEN) {
				resolve({ offline: true });
				return;
			}
			const requestId = uuidv4();
			const seconds = Number(timeout) || Math.round(config.relay.rpcTimeoutMs / 1000);
			const waitMs = Math.min(Math.max(seconds * 1000, MIN_RPC_MS), MAX_RPC_MS) + RPC_BUFFER_MS;
			const timer = setTimeout(() => {
				this.pending.delete(requestId);
				resolve({ status: 0, error: 'Relay RPC timed out.' });
			}, waitMs);
			this.pending.set(requestId, { resolve, timer });
			try {
				conn.ws.send(JSON.stringify({
					type: 'ha_rpc', requestId, method, path, body, expect, timeout
				}));
			} catch (err) {
				clearTimeout(timer);
				this.pending.delete(requestId);
				logger.error(`Relay send to ${serverId} failed:`, err.message || err);
				resolve({ status: 0, error: 'Relay send failed.' });
			}
		});
	}

	getStats() {
		return {
			relays: this.connections.size,
			pending: this.pending.size,
			servers: Array.from(this.connections.keys())
		};
	}

	startHeartbeat() {
		this._heartbeat = setInterval(() => {
			const now = Date.now();
			for (const [serverId, conn] of this.connections.entries()) {
				if (conn.ws.readyState === WebSocket.OPEN) {
					if (now - conn.lastActivity > IDLE_PING_AFTER_MS) {
						try {
							conn.ws.ping();
						} catch (_err) { /* ignore */ }
					}
				} else {
					this.connections.delete(serverId);
				}
			}
		}, HEARTBEAT_INTERVAL_MS);
		// Don't keep the event loop alive solely for the heartbeat.
		if (this._heartbeat.unref) {
			this._heartbeat.unref();
		}
	}

	_safeSend(ws, payload) {
		if (!ws || ws.readyState !== WebSocket.OPEN) {
			return false;
		}
		try {
			ws.send(JSON.stringify(payload));
			return true;
		} catch (_err) {
			return false;
		}
	}
}

module.exports = new RelayManager();
// Exposed for unit tests (use a fresh instance / the pure helper in isolation).
module.exports.RelayManager = RelayManager;
module.exports._extractSecret = extractSecret;
