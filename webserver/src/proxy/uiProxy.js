/**
 * Browser-facing reverse proxy for full-UI forwarding (the paid friendly domain).
 *
 * A friendly domain (`<slug>.home.vome.io`) reuses the SAME `*.<SERVER_DOMAIN>`
 * wildcard vhost that hosted-VM subdomains use; its nginx map.d entry routes the
 * host to this proxy's loopback port (so no new vhost/cert/DNS).  Because that
 * shared vhost rewrites the upstream `Host`, the real friendly host arrives in
 * `X-HA-Original-Host` (see originalHost).  Every request here is therefore *for*
 * a home behind the relay.  The flow:
 *
 *   browser ──HTTPS/WSS──▶ nginx (*.home.vome.io) ──▶ this proxy ──relay WS──▶ component ──▶ HA
 *
 * HTTP: each request is buffered and handed to relayManager.forwardHttp, then the
 * component's response (status + headers + base64 body) is written back verbatim.
 * WebSocket (`/api/websocket`): the browser socket is bridged to a component-side
 * local HA socket, frame-for-frame, via relayManager's tunnel registry.
 *
 * Auth: a viewer must present a valid forwarding cookie (see ./uiAccess) — the
 * portal mints it only for an owner with an active subscription.  No cookie ⇒ a
 * 302 to the portal authorise page (HTTP) or a 401 (WebSocket).  Vome handles the
 * gate + TLS; the user still signs in to their own Home Assistant as normal.
 */
const WebSocket = require('ws');
const { v4: uuidv4 } = require('uuid');
const logger = require('../utils/logger');
const config = require('../config/config');
const relayManagerSingleton = require('../websocket/relayManager');
const uiAccess = require('./uiAccess');
const { abortUpgrade } = require('../websocket/upgradeRouter');

// Hop-by-hop headers are per-connection and must not cross the tunnel (RFC 7230
// §6.1); Host/Content-Length are re-derived at each hop.
const HOP_BY_HOP = new Set([
	'connection', 'keep-alive', 'proxy-authenticate', 'proxy-authorization',
	'te', 'trailer', 'transfer-encoding', 'upgrade', 'host', 'content-length'
]);

/**
 * The browser's real host (the friendly domain).
 *
 * Friendly domains reuse the shared `*.<SERVER_DOMAIN>` wildcard vhost, whose
 * map.d route rewrites the upstream `Host` to this proxy's loopback address —
 * so the genuine `<slug>.<base>` arrives in `X-HA-Original-Host` instead.  Fall
 * back to `Host` for a direct hit (tests / a future dedicated vhost).
 */
function originalHost(req) {
	const h = req.headers || {};
	return String(h['x-ha-original-host'] || h.host || '');
}

/** Drop our own forwarding cookie from a Cookie header value (don't leak it to HA). */
function stripOwnCookie(value, cookieName) {
	const kept = String(value)
		.split(';')
		.map((p) => p.trim())
		.filter((p) => p && p.split('=')[0].trim() !== cookieName);
	return kept.join('; ');
}

/** Build a forwardable [name,value] header list from a request's raw headers. */
function collectRequestHeaders(req, cookieName) {
	const raw = req.rawHeaders || [];
	const out = [];
	for (let i = 0; i + 1 < raw.length; i += 2) {
		const name = raw[i];
		let value = raw[i + 1];
		if (HOP_BY_HOP.has(name.toLowerCase())) {
			continue;
		}
		if (name.toLowerCase() === 'cookie') {
			value = stripOwnCookie(value, cookieName);
			if (!value) {
				continue;
			}
		}
		out.push([name, value]);
	}
	return out;
}

/** Filter a component response's header pairs (drop hop-by-hop; keep duplicates). */
function filterResponseHeaders(pairs) {
	const out = [];
	for (const pair of pairs || []) {
		if (!Array.isArray(pair) || pair.length < 2) {
			continue;
		}
		if (HOP_BY_HOP.has(String(pair[0]).toLowerCase())) {
			continue;
		}
		out.push([String(pair[0]), String(pair[1])]);
	}
	return out;
}

/** Apply header pairs to a Node response, coalescing duplicates (e.g. Set-Cookie). */
function applyResponseHeaders(res, pairs) {
	const merged = new Map();
	for (const [name, value] of pairs) {
		if (merged.has(name)) {
			const cur = merged.get(name);
			merged.set(name, Array.isArray(cur) ? cur.concat(value) : [cur, value]);
		} else {
			merged.set(name, value);
		}
	}
	for (const [name, value] of merged.entries()) {
		res.setHeader(name, value);
	}
}

function createUiProxy(deps = {}) {
	const relay = deps.relayManager || relayManagerSingleton;
	const verify = deps.verifyAccessToken || uiAccess.verifyAccessToken;
	const cookieName = config.relay.forwardCookieName;
	const maxBody = config.relay.forwardMaxBodyBytes;
	// noServer: the dedicated proxy server's `upgrade` event calls handleUpgrade.
	const wss = new WebSocket.Server({ noServer: true });

	/** Resolve + authorise a request; returns access or null (caller responds). */
	function authorise(req) {
		const host = originalHost(req);
		const token = uiAccess.readCookie(req, cookieName);
		return verify(token, host);
	}

	function httpHandler(req, res) {
		const access = authorise(req);
		if (!access) {
			const dest = `${config.relay.forwardAuthoriseUrl}?host=${encodeURIComponent(originalHost(req))}`;
			res.writeHead(302, { Location: dest });
			res.end();
			return;
		}
		const chunks = [];
		let size = 0;
		let tooLarge = false;
		req.on('data', (chunk) => {
			size += chunk.length;
			if (size > maxBody) {
				tooLarge = true;
				return;
			}
			chunks.push(chunk);
		});
		req.on('error', () => {
			res.writeHead(400);
			res.end('Bad request');
		});
		req.on('end', async () => {
			if (tooLarge) {
				res.writeHead(413);
				res.end('Request too large');
				return;
			}
			const bodyB64 = chunks.length ? Buffer.concat(chunks).toString('base64') : undefined;
			const headers = collectRequestHeaders(req, cookieName);
			let result;
			try {
				result = await relay.forwardHttp(access.serverId, {
					method: req.method, path: req.url, headers, bodyB64
				});
			} catch (err) {
				logger.error('UI forward failed:', err.message || err);
				res.writeHead(502);
				res.end('Forwarding failed.');
				return;
			}
			if (result.offline) {
				res.writeHead(502);
				res.end('Home Assistant is offline.');
				return;
			}
			if (!result.status) {
				res.writeHead(502);
				res.end(result.error || 'Forwarding failed.');
				return;
			}
			const body = result.bodyB64 ? Buffer.from(result.bodyB64, 'base64') : Buffer.alloc(0);
			applyResponseHeaders(res, filterResponseHeaders(result.headers));
			res.setHeader('Content-Length', body.length);
			res.writeHead(result.status);
			res.end(body);
		});
	}

	function handleUpgrade(req, socket, head) {
		const pathname = (req.url || '').split('?')[0];
		if (pathname !== '/api/websocket') {
			abortUpgrade(socket, 404, 'Not found');
			return;
		}
		const access = authorise(req);
		if (!access) {
			abortUpgrade(socket, 401, 'Unauthorized');
			return;
		}
		if (!relay.isConnected(access.serverId)) {
			abortUpgrade(socket, 502, 'Home Assistant offline');
			return;
		}
		wss.handleUpgrade(req, socket, head, (browser) => bridge(browser, req, access.serverId));
	}

	/**
	 * Bridge one accepted browser WebSocket to a component-side local HA socket.
	 *
	 * Browser frames that arrive before the component acknowledges the open are
	 * queued (the HA frontend sends its `auth` message immediately), then flushed
	 * in order once `ws_open_ack` lands.
	 */
	function bridge(browser, req, serverId) {
		const socketId = uuidv4();
		let acked = false;
		const queue = [];

		relay.registerTunnel(socketId, serverId, {
			onAck: () => {
				acked = true;
				for (const frame of queue) {
					relay.sendWs(serverId, frame);
				}
				queue.length = 0;
			},
			onData: (data) => {
				if (browser.readyState !== WebSocket.OPEN) {
					return;
				}
				if (data.dataB64 != null) {
					browser.send(Buffer.from(data.dataB64, 'base64'));
				} else if (data.text != null) {
					browser.send(data.text);
				}
			},
			onClose: (data) => {
				try {
					browser.close(data.code || 1000, data.reason || '');
				} catch (_err) { /* already closing */ }
			}
		});

		const headers = collectRequestHeaders(req, cookieName);
		if (!relay.openWs(serverId, { socketId, path: req.url, headers })) {
			relay.unregisterTunnel(socketId);
			try {
				browser.close(1011, 'Relay offline');
			} catch (_err) { /* ignore */ }
			return;
		}

		browser.on('message', (data, isBinary) => {
			const frame = isBinary
				? { socketId, dataB64: Buffer.from(data).toString('base64') }
				: { socketId, text: data.toString() };
			if (acked) {
				relay.sendWs(serverId, frame);
			} else {
				queue.push(frame);
			}
		});
		browser.on('close', (code, reason) => {
			relay.unregisterTunnel(socketId);
			relay.closeWs(serverId, { socketId, code, reason: reason ? reason.toString() : '' });
		});
		browser.on('error', (err) => {
			logger.warn(`UI bridge socket error (${serverId}):`, err.message || err);
		});
	}

	return { httpHandler, handleUpgrade, bridge, _wss: wss };
}

module.exports = {
	createUiProxy,
	// Exposed for unit tests.
	originalHost,
	collectRequestHeaders,
	filterResponseHeaders,
	applyResponseHeaders,
	stripOwnCookie,
	HOP_BY_HOP
};
