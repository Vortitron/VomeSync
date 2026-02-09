/**
 * Public / unauthenticated API endpoints.
 *
 * These routes do not require any authentication and are freely
 * accessible (subject to rate limiting).
 */
const express = require('express');
const redisClient = require('../utils/redis');
const authManager = require('../utils/auth');
const logger = require('../utils/logger');
const {
	validateUID,
	sanitizePublicSwitchData
} = require('../utils/validation');
const webSocketManager = require('../websocket/manager');

const router = express.Router();

// ── Allocate a globally unique switch name (Northern Sami words) ───────────────

router.get('/next-switch-name',
	authManager.rateLimit('next_switch_name', 60, 60000),
	async (req, res) => {
		try {
			const name = await redisClient.allocateSwitchName();
			const count = await redisClient.getAllocatedSwitchNameCount();

			return res.json({
				success: true,
				data: { name, allocatedCount: count }
			});
		} catch (error) {
			logger.error('Error allocating switch name:', error);
			return res.status(500).json({ success: false, error: 'Failed to allocate switch name' });
		}
	}
);

// ── Get switch status ──────────────────────────────────────────────────────────

router.get('/status/:uid',
	validateUID,
	authManager.rateLimit('get_status', 500, 900000),
	async (req, res) => {
		try {
			const { uid } = req.params;
			const switchData = await redisClient.getSwitchState(uid);

			if (!switchData) {
				return res.status(404).json({ success: false, error: 'Switch not found' });
			}

			return res.json({
				success: true,
				data: sanitizePublicSwitchData(switchData)
			});
		} catch (error) {
			logger.error(`Error getting switch status ${req.params.uid}:`, error);
			return res.status(500).json({ success: false, error: 'Failed to get switch status' });
		}
	}
);

// ── Public switch directory listing ────────────────────────────────────────────

router.get('/public-switches',
	authManager.rateLimit('public_switches', 100, 900000),
	async (req, res) => {
		try {
			const publicSwitches = await redisClient.getPublicSwitches();

			return res.json({
				success: true,
				data: {
					switches: publicSwitches,
					count: publicSwitches.length,
					timestamp: Date.now()
				}
			});
		} catch (error) {
			logger.error('Error getting public switches:', error);
			return res.status(500).json({ success: false, error: 'Failed to get public switches' });
		}
	}
);

// ── Public switch detail (deep links) ──────────────────────────────────────────

router.get('/switch/:uid',
	validateUID,
	authManager.rateLimit('public_switch_detail', 200, 900000),
	async (req, res) => {
		try {
			const { uid } = req.params;
			const detail = await redisClient.getPublicSwitchDetail(uid);
			if (!detail) {
				return res.status(404).json({ success: false, error: 'Switch not found or not public' });
			}

			return res.json({
				success: true,
				data: detail
			});
		} catch (error) {
			logger.error(`Error getting switch detail ${req.params.uid}:`, error);
			return res.status(500).json({ success: false, error: 'Failed to get switch detail' });
		}
	}
);

// ── Public category listing ────────────────────────────────────────────────────

router.get('/categories',
	authManager.rateLimit('public_categories', 100, 900000),
	async (_req, res) => {
		try {
			const categories = await redisClient.getCategoryCounts();
			return res.json({
				success: true,
				data: categories
			});
		} catch (error) {
			logger.error('Error getting categories:', error);
			return res.status(500).json({ success: false, error: 'Failed to get categories' });
		}
	}
);

// ── Health check ───────────────────────────────────────────────────────────────

router.get('/health', (req, res) => {
	const health = {
		status: 'healthy',
		timestamp: Date.now(),
		uptime: process.uptime(),
		redis: redisClient.isConnected,
		websocket: {
			clients: webSocketManager.getStats().totalClients,
			subscriptions: webSocketManager.getStats().totalSubscriptions
		}
	};

	res.json(health);
});

// ── Server stats (monitoring) ──────────────────────────────────────────────────

router.get('/stats',
	authManager.rateLimit('stats', 60, 900000),
	async (req, res) => {
		try {
			const wsStats = webSocketManager.getStats();
			const publicSwitches = await redisClient.getPublicSwitches();

			return res.json({
				success: true,
				data: {
					websocket: wsStats,
					publicSwitchCount: publicSwitches.length,
					timestamp: Date.now()
				}
			});
		} catch (error) {
			logger.error('Error getting server stats:', error);
			return res.status(500).json({ success: false, error: 'Failed to get server stats' });
		}
	}
);

module.exports = router;

