/**
 * Internal (machine-to-machine) routes for the relay.
 *
 * Mounted at `/internal` — NOT under `/api` and not intended to be exposed
 * publicly by nginx.  The only client is the Vome portal, dispatching a brokered
 * HA call to a connected component.  Authenticated with the shared
 * `RELAY_INTERNAL_SECRET` (constant-time compared); fails closed when unset.
 */
const express = require('express');
const crypto = require('crypto');
const logger = require('../utils/logger');
const config = require('../config/config');
const relayManager = require('../websocket/relayManager');

const router = express.Router();

// Dispatch policy — mirrors the allowlist the component itself enforces
// (custom_components/vomesync/relay_client.py).  Defence in depth: the relay
// refuses to forward a call the component should refuse, so a stale or
// modified component is never the only guard between the portal and a
// customer's Home Assistant.
const RELAY_TARGET_CORE = 'core';
const RELAY_TARGET_ESPHOME = 'esphome';
const RELAY_TARGET_WEBSOCKET = 'websocket';
const CORE_ALLOWED_METHODS = new Set(['GET', 'POST', 'PUT', 'DELETE']);
const ESPHOME_ALLOWED_METHODS = new Set(['GET', 'POST']);
// Exact path portions (query string excluded) of the brokered ESPHome REST
// subset: list devices, dashboard version, read/write one configuration YAML.
const ESPHOME_ALLOWED_PATHS = new Set(['/devices', '/version', '/edit']);

function decodedSegment(segment) {
	try {
		return decodeURIComponent(segment);
	} catch (_err) {
		return segment;
	}
}

/**
 * Validate one dispatch request's target/method/path.  Returns an error string
 * to send back as a 400, or null when the request is acceptable.
 *
 * Dot segments are rejected (even percent-encoded) because HTTP clients
 * normalise `..` when building the URL, which would let `/api/../auth/x`
 * escape a prefix allowlist.
 */
function dispatchPolicyError({ method, path, target } = {}) {
	const kind = (target === undefined || target === null || target === '')
		? RELAY_TARGET_CORE
		: target;
	if (kind === RELAY_TARGET_WEBSOCKET) {
		// Lovelace dashboard commands — body is validated on the portal; the
		// component enforces its own allowlist before executing locally.
		return null;
	}
	if (typeof path !== 'string' || !path.startsWith('/')) {
		return 'path must be an absolute path string';
	}
	const pathPortion = path.split('?', 1)[0];
	for (const segment of pathPortion.split('/')) {
		const decoded = decodedSegment(segment);
		if (segment === '.' || segment === '..' || decoded === '.' || decoded === '..') {
			return 'path must not contain dot segments';
		}
	}
	const upperMethod = String(method || 'GET').toUpperCase();
	if (kind === RELAY_TARGET_ESPHOME) {
		if (!ESPHOME_ALLOWED_METHODS.has(upperMethod)) {
			return `method ${upperMethod} is not allowed for the esphome target`;
		}
		if (!ESPHOME_ALLOWED_PATHS.has(pathPortion)) {
			return 'esphome path is not allowlisted';
		}
		return null;
	}
	if (kind !== RELAY_TARGET_CORE) {
		return 'unknown dispatch target';
	}
	if (!CORE_ALLOWED_METHODS.has(upperMethod)) {
		return `method ${upperMethod} is not allowed`;
	}
	if (!pathPortion.startsWith('/api/')) {
		return 'core path must be under /api/';
	}
	return null;
}

function authorised(req) {
	const secret = config.relay.internalSecret;
	if (!secret) {
		return false;
	}
	const header = req.headers.authorization || '';
	if (!header.toLowerCase().startsWith('bearer ')) {
		return false;
	}
	const presented = header.slice(7).trim();
	const a = Buffer.from(presented);
	const b = Buffer.from(secret);
	if (a.length !== b.length) {
		return false;
	}
	try {
		return crypto.timingSafeEqual(a, b);
	} catch (_err) {
		return false;
	}
}

router.post('/relay/dispatch', async (req, res) => {
	if (!authorised(req)) {
		return res.status(401).json({ error: 'unauthorized' });
	}
	const { server_id: serverId, method, path, body, expect, timeout, target } = req.body || {};
	if (!serverId) {
		return res.status(400).json({ error: 'server_id is required' });
	}
	const kind = (target === undefined || target === null || target === '') ? RELAY_TARGET_CORE : target;
	if (kind !== RELAY_TARGET_WEBSOCKET && !path) {
		return res.status(400).json({ error: 'server_id and path are required' });
	}
	const policyError = dispatchPolicyError({ method, path, target });
	if (policyError) {
		logger.warn(`Relay dispatch refused for ${serverId}: ${policyError}`);
		return res.status(400).json({ error: policyError });
	}
	try {
		const result = await relayManager.dispatch(serverId, { method, path, body, expect, timeout, target });
		if (result && result.offline) {
			return res.status(404).json({ error: 'No relay connection for this server.' });
		}
		return res.json({ status: result.status || 0, body: result.body, error: result.error });
	} catch (err) {
		logger.error('Relay dispatch failed:', err.message || err);
		return res.status(500).json({ error: 'dispatch failed' });
	}
});

router.get('/relay/status', (req, res) => {
	if (!authorised(req)) {
		return res.status(401).json({ error: 'unauthorized' });
	}
	return res.json(relayManager.getStats());
});

router.post('/relay/disconnect', (req, res) => {
	if (!authorised(req)) {
		return res.status(401).json({ error: 'unauthorized' });
	}
	const serverId = (req.body || {}).server_id;
	if (!serverId) {
		return res.status(400).json({ error: 'server_id is required' });
	}
	return res.json({ disconnected: relayManager.disconnect(serverId) });
});

module.exports = router;
// Exposed for unit tests (pure policy helper).
module.exports._dispatchPolicyError = dispatchPolicyError;
