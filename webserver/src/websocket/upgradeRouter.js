/**
 * Single HTTP→WS upgrade router shared by every WebSocket endpoint.
 *
 * Why this exists: attaching several `WebSocket.Server({ server, path })`
 * instances to one HTTP server is broken in ws 8.x — every instance registers
 * its own `upgrade` listener and aborts handshakes whose path it doesn't own
 * with a 400.  In practice the first-registered endpoint (`/ws`) 400-ed every
 * `/ws/relay` handshake before the relay server could accept it.  The fix is
 * the pattern from the ws README: all endpoints run `noServer: true` and one
 * router owns the `upgrade` event, dispatching by exact pathname.
 */
const logger = require('../utils/logger');

const CRLF = '\r\n';

/** Reject an upgrade with a real HTTP response (sockets get no default reply). */
function abortUpgrade(socket, status, message) {
	if (socket.writable) {
		const body = message || '';
		socket.write(
			`HTTP/1.1 ${status} ${message || 'Error'}${CRLF}` +
			`Connection: close${CRLF}` +
			`Content-Type: text/plain${CRLF}` +
			`Content-Length: ${Buffer.byteLength(body)}${CRLF}${CRLF}` +
			body
		);
	}
	socket.destroy();
}

/**
 * Attach the upgrade router to an HTTP/S server.
 *
 * `routes` maps an exact pathname to a handler object exposing
 * `handleUpgrade(req, socket, head)` (our WS managers).  Unknown paths get a
 * 404 so misconfigured clients fail fast instead of hanging.
 */
function attachUpgradeRouter(server, routes) {
	server.on('upgrade', (req, socket, head) => {
		const pathname = (req.url || '').split('?')[0];
		const route = routes[pathname];
		if (!route) {
			abortUpgrade(socket, 404, 'Unknown WebSocket path');
			return;
		}
		try {
			route.handleUpgrade(req, socket, head);
		} catch (err) {
			logger.error(`Upgrade handler for ${pathname} threw:`, err.message || err);
			abortUpgrade(socket, 500, 'Upgrade failed');
		}
	});
}

module.exports = { attachUpgradeRouter, abortUpgrade };
