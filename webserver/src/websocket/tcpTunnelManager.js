/**
 * `/ws/tcp` — bearer-token raw-TCP LAN tunnels (e.g. RDP), for the
 * `npx @vortitron/home-assistant-mcp tunnel` CLI client rather than a browser.
 *
 * Unlike uiProxy's friendly-domain forwarding (cookie, host-bound, browser
 * only), a CLI has no browser session: it presents a short-lived bearer token
 * minted on demand by the linked Home Assistant, over the already-authenticated
 * relay control channel (see relayManager's `mint_lan_tcp_token` handling /
 * uiAccess.mintLanTcpToken, requested from custom_components/vomesync's
 * `mint_lan_tcp_token` service). The token carries which server_id + LAN
 * route slug it's for, so the connect path is fixed (`/ws/tcp`) — no
 * per-slug URL segment, and no defense-in-depth comparison needed.
 *
 * The actual byte-pumping reuses relayBridge.bridgeSocket (shared with
 * uiProxy.js) and, on the component side, relay_client.py's existing
 * ws_open/ws_data/ws_close handling — this file only adds the auth check.
 */
const WebSocket = require('ws');
const uiAccess = require('../proxy/uiAccess');
const relayBridge = require('./relayBridge');
const relayManagerSingleton = require('./relayManager');
const { abortUpgrade } = require('./upgradeRouter');

/** Extract the bearer token from an upgrade request's Authorization header. */
function extractBearer(req) {
	const header = (req.headers && req.headers.authorization) || '';
	if (typeof header === 'string' && header.toLowerCase().startsWith('bearer ')) {
		return header.slice(7).trim();
	}
	return '';
}

function createTcpTunnelManager(deps = {}) {
	const relay = deps.relayManager || relayManagerSingleton;
	const verify = deps.verifyLanTcpToken || uiAccess.verifyLanTcpToken;
	// noServer: this endpoint is dispatched to by the shared upgrade router
	// (see websocket/upgradeRouter.js) — see server.js's attachUpgradeRouter call.
	const wss = new WebSocket.Server({ noServer: true });

	/** Authenticate + complete a `/ws/tcp` upgrade (called by the router). */
	function handleUpgrade(req, socket, head) {
		const token = extractBearer(req);
		const access = token ? verify(token) : null;
		if (!access) {
			abortUpgrade(socket, 401, 'Unauthorized');
			return;
		}
		if (!relay.isConnected(access.serverId)) {
			abortUpgrade(socket, 502, 'Home Assistant offline');
			return;
		}
		wss.handleUpgrade(req, socket, head, (client) => {
			relayBridge.bridgeSocket(client, relay, access.serverId, {
				path: `/t/${access.slug}`
			});
		});
	}

	return { handleUpgrade, _wss: wss };
}

module.exports = createTcpTunnelManager();
// Exposed for unit tests (inject a fake relayManager / verifyLanTcpToken).
module.exports.createTcpTunnelManager = createTcpTunnelManager;
