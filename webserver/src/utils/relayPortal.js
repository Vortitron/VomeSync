/**
 * Portal side of the relay trust boundary.
 *
 * The relay control channel (see ../websocket/relayManager) is the one place a
 * component proves who it is, so we never trust the WebSocket alone: when a
 * component connects it presents a per-instance relay secret, and we ask the
 * Vome portal — over a shared-secret internal channel — to turn that secret into
 * the owning server_id.  The portal is the source of truth (it minted the
 * secret and stores it server-side), so the backend never needs the user DB.
 */
const config = require('../config/config');
const logger = require('../utils/logger');

const VERIFY_TIMEOUT_MS = 8000;

/**
 * Resolve a component's presented relay secret to its server_id, or null.
 *
 * Fails closed: no shared secret configured, a non-200 from the portal, or any
 * transport error all yield null (connection refused) rather than a guess.
 *
 * @param {string} secret
 * @returns {Promise<string|null>}
 */
async function verifySecret(secret) {
	if (!config.relay.internalSecret) {
		logger.error('RELAY_INTERNAL_SECRET is not configured; refusing relay connections.');
		return null;
	}
	if (!secret || typeof secret !== 'string') {
		return null;
	}
	try {
		const resp = await fetch(config.relay.portalVerifyUrl, {
			method: 'POST',
			headers: {
				'Content-Type': 'application/json',
				Authorization: `Bearer ${config.relay.internalSecret}`
			},
			body: JSON.stringify({ secret }),
			signal: AbortSignal.timeout(VERIFY_TIMEOUT_MS)
		});
		if (!resp.ok) {
			return null;
		}
		const data = await resp.json();
		return data && data.ok && data.server_id ? String(data.server_id) : null;
	} catch (err) {
		logger.error('Relay secret verification failed:', err.message || err);
		return null;
	}
}

module.exports = { verifySecret };
