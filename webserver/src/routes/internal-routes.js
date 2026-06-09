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
	const { server_id: serverId, method, path, body, expect, timeout } = req.body || {};
	if (!serverId || !path) {
		return res.status(400).json({ error: 'server_id and path are required' });
	}
	try {
		const result = await relayManager.dispatch(serverId, { method, path, body, expect, timeout });
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

module.exports = router;
