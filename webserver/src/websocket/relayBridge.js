/**
 * Shared WebSocket↔relay bridge: an accepted client socket ↔ relayManager ↔
 * a component-side local socket (or, for a `tcp`-scheme LAN route, a raw TCP
 * connection — the component treats both identically, see relay_client.py's
 * _open_bridged_ws / _open_bridged_tcp).
 *
 * Extracted from proxy/uiProxy.js's original `bridge()` so a second entry
 * point (websocket/tcpTunnelManager.js, bearer-token CLI tunnels e.g. RDP)
 * doesn't duplicate the queue-until-ack / frame-pump / close plumbing.
 */
const WebSocket = require('ws');
const { v4: uuidv4 } = require('uuid');
const logger = require('../utils/logger');

/**
 * Bridge `clientWs` (an already-accepted WebSocket) to a component-side local
 * socket for `serverId`, via `relayManager`. `openPayload` is passed to
 * `relayManager.openWs` verbatim alongside the generated `socketId` — e.g.
 * `{ path, headers }` for a browser tunnel, or just `{ path: '/t/<slug>' }`
 * for a bearer-token CLI tunnel with no headers to forward.
 *
 * Returns the generated socketId (useful for logging/tests).
 */
function bridgeSocket(clientWs, relayManager, serverId, openPayload = {}) {
	const socketId = uuidv4();
	let acked = false;
	const queue = [];

	relayManager.registerTunnel(socketId, serverId, {
		onAck: () => {
			acked = true;
			for (const frame of queue) {
				relayManager.sendWs(serverId, frame);
			}
			queue.length = 0;
		},
		onData: (data) => {
			if (clientWs.readyState !== WebSocket.OPEN) {
				return;
			}
			if (data.dataB64 != null) {
				clientWs.send(Buffer.from(data.dataB64, 'base64'));
			} else if (data.text != null) {
				clientWs.send(data.text);
			}
		},
		onClose: (data) => {
			try {
				clientWs.close(data.code || 1000, data.reason || '');
			} catch (_err) { /* already closing */ }
		}
	});

	if (!relayManager.openWs(serverId, { socketId, ...openPayload })) {
		relayManager.unregisterTunnel(socketId);
		try {
			clientWs.close(1011, 'Relay offline');
		} catch (_err) { /* ignore */ }
		return socketId;
	}

	clientWs.on('message', (data, isBinary) => {
		const frame = isBinary
			? { socketId, dataB64: Buffer.from(data).toString('base64') }
			: { socketId, text: data.toString() };
		if (acked) {
			relayManager.sendWs(serverId, frame);
		} else {
			queue.push(frame);
		}
	});
	clientWs.on('close', (code, reason) => {
		relayManager.unregisterTunnel(socketId);
		relayManager.closeWs(serverId, { socketId, code, reason: reason ? reason.toString() : '' });
	});
	clientWs.on('error', (err) => {
		logger.warn(`Relay bridge socket error (${serverId}):`, err.message || err);
	});

	return socketId;
}

module.exports = { bridgeSocket };
