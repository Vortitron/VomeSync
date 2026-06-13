/**
 * Access gate for full-UI forwarding (the paid "friendly domain" remote access).
 *
 * A friendly domain (`<slug>.vome.io`) must only forward to a home for a viewer
 * who is signed in to Vome, owns that server, and has an active subscription.
 * The portal is the source of truth for all three, so it performs the checks and
 * mints a short-lived **HS256 JWT** bound to the host + server, dropped as a
 * cookie scoped to `.vome.io`.  This module is the backend half: it only
 * *verifies* that token (and mints one in tests / for parity).  No DB, no portal
 * round-trip per request — the signature is the proof.
 *
 * Claims: { sub: userId, sid: serverId, host, scope: 'ha-forward', iat, exp }.
 */
const jwt = require('jsonwebtoken');
const config = require('../config/config');

const SCOPE = 'ha-forward';
const DEFAULT_TTL_SECONDS = 12 * 60 * 60;

/** Parse one named cookie from a request's Cookie header (or '' when absent). */
function readCookie(req, name) {
	const header = (req && req.headers && req.headers.cookie) || '';
	if (!header) {
		return '';
	}
	for (const part of header.split(';')) {
		const eq = part.indexOf('=');
		if (eq === -1) {
			continue;
		}
		if (part.slice(0, eq).trim() === name) {
			return decodeURIComponent(part.slice(eq + 1).trim());
		}
	}
	return '';
}

/**
 * Verify a forwarding access token against the host it was presented on.
 *
 * Fails closed: no shared secret, bad signature, wrong scope, missing server,
 * expiry, or a host mismatch (a cookie minted for host A must not work on host
 * B) all return null.  Returns `{ serverId, userId }` on success.
 */
function verifyAccessToken(token, expectedHost) {
	if (!token || !config.relay.forwardSecret) {
		return null;
	}
	let decoded;
	try {
		decoded = jwt.verify(token, config.relay.forwardSecret, { algorithms: ['HS256'] });
	} catch (_err) {
		return null;
	}
	if (!decoded || decoded.scope !== SCOPE || !decoded.sid) {
		return null;
	}
	if (expectedHost && decoded.host
		&& String(decoded.host).toLowerCase() !== String(expectedHost).toLowerCase()) {
		return null;
	}
	return {
		serverId: String(decoded.sid),
		userId: decoded.sub != null ? String(decoded.sub) : null
	};
}

/**
 * Mint a forwarding access token.  The portal owns this in production (Python /
 * pyjwt with the same secret + claims); provided here for parity and tests.
 */
function mintAccessToken({ serverId, userId, host, ttlSeconds = DEFAULT_TTL_SECONDS } = {}) {
	if (!config.relay.forwardSecret) {
		throw new Error('RELAY_FORWARD_SECRET is not configured.');
	}
	return jwt.sign(
		{ sub: userId, sid: serverId, host, scope: SCOPE },
		config.relay.forwardSecret,
		{ algorithm: 'HS256', expiresIn: ttlSeconds }
	);
}

module.exports = { verifyAccessToken, mintAccessToken, readCookie, SCOPE };
