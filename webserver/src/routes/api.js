const express = require('express');
const { v4: uuidv4 } = require('uuid');
const redisClient = require('../utils/redis');
const authManager = require('../utils/auth');
const logger = require('../utils/logger');
const {
	validateRequest,
	validateUID,
	schemas,
	sanitizePublicSwitchData,
	sanitizePrivateSwitchData
} = require('../utils/validation');
const webSocketManager = require('../websocket/manager');

const router = express.Router();

// Generate personal key endpoint
router.post('/generate-key',
	authManager.rateLimit('generate_key', 10, 3600000), // 10 per hour
	validateRequest(schemas.generateKey),
	async (req, res) => {
		try {
			const personalKey = authManager.generatePersonalKey();

			// Store the key in Redis
			await redisClient.storePersonalKey(personalKey);

			// Generate JWT for additional security (optional)
			const jwt = authManager.generateJWT(personalKey);

			logger.info(`Generated new personal key: ${personalKey.substring(0, 8)}...`);

			return res.json({
				success: true,
				data: {
					personalKey,
					jwt,
					expiresIn: '1 year',
					message: 'Store this key securely - it cannot be recovered if lost'
				}
			});
		} catch (error) {
			logger.error('Error generating personal key:', error);
			res.status(500).json({
				success: false,
				error: 'Failed to generate personal key'
			});
		}
	}
);

// Create switch endpoint
router.post('/create-switch',
	authManager.rateLimit('create_switch', 20, 3600000), // 20 per hour
	authManager.requireAuth(),
	validateRequest(schemas.createSwitch),
	async (req, res) => {
		try {
			const uid = uuidv4();
			const { personalKey } = req;
			const switchConfig = req.validatedData;

			// Create switch in Redis
			const switchData = await redisClient.createSwitch(uid, personalKey, switchConfig);

			logger.info(`Created new switch: ${uid} by ${personalKey.substring(0, 8)}...`);

			res.json({
				success: true,
				data: {
					uid,
					...sanitizePrivateSwitchData(switchData),
					websocketUrl: `/ws?uid=${uid}`
				}
			});
		} catch (error) {
			logger.error('Error creating switch:', error);
			res.status(500).json({
				success: false,
				error: 'Failed to create switch'
			});
		}
	}
);

// Toggle switch endpoint
router.post('/toggle/:uid',
	validateUID,
	authManager.rateLimit('toggle_switch', 200, 900000), // 200 per 15 minutes
	authManager.requireSwitchAuth(),
	async (req, res) => {
		try {
			const { uid } = req.params;
			const { switchData } = req;

			// Toggle state
			const newState = !switchData.state;

			// Update in Redis
			await redisClient.setSwitchState(uid, newState);
			await redisClient.incrementToggleCount(uid);

			// Publish update via Redis for WebSocket broadcasting
			await redisClient.publishSwitchUpdate(uid, newState);

			logger.info(`Toggled switch ${uid} to ${newState ? 'on' : 'off'}`);

			res.json({
				success: true,
				data: {
					uid,
					state: newState,
					timestamp: Date.now()
				}
			});
		} catch (error) {
			logger.error(`Error toggling switch ${req.params.uid}:`, error);
			res.status(500).json({
				success: false,
				error: 'Failed to toggle switch'
			});
		}
	}
);

// Get switch status (public endpoint)
router.get('/status/:uid',
	validateUID,
	authManager.rateLimit('get_status', 500, 900000), // 500 per 15 minutes
	async (req, res) => {
		try {
			const { uid } = req.params;

			const switchData = await redisClient.getSwitchState(uid);

			if (!switchData) {
				return res.status(404).json({
					success: false,
					error: 'Switch not found'
				});
			}

			// Return public data only
			res.json({
				success: true,
				data: sanitizePublicSwitchData(switchData)
			});
		} catch (error) {
			logger.error(`Error getting switch status ${req.params.uid}:`, error);
			res.status(500).json({
				success: false,
				error: 'Failed to get switch status'
			});
		}
	}
);

// Get public switches for website directory
router.get('/public-switches',
	authManager.rateLimit('public_switches', 100, 900000), // 100 per 15 minutes
	async (req, res) => {
		try {
			const publicSwitches = await redisClient.getPublicSwitches();

			res.json({
				success: true,
				data: {
					switches: publicSwitches,
					count: publicSwitches.length,
					timestamp: Date.now()
				}
			});
		} catch (error) {
			logger.error('Error getting public switches:', error);
			res.status(500).json({
				success: false,
				error: 'Failed to get public switches'
			});
		}
	}
);

// Get user's switches
router.get('/my-switches',
	authManager.requireAuth(),
	authManager.rateLimit('my_switches', 100, 900000),
	async (req, res) => {
		try {
			const { personalKey } = req;

			const userSwitches = await redisClient.getUserSwitches(personalKey);

			const sanitizedSwitches = userSwitches.map(switchData =>
				sanitizePrivateSwitchData(switchData)
			);

			res.json({
				success: true,
				data: {
					switches: sanitizedSwitches,
					count: sanitizedSwitches.length
				}
			});
		} catch (error) {
			logger.error('Error getting user switches:', error);
			res.status(500).json({
				success: false,
				error: 'Failed to get user switches'
			});
		}
	}
);

// Delete switch
router.delete('/switch/:uid',
	validateUID,
	authManager.requireSwitchAuth(),
	authManager.rateLimit('delete_switch', 50, 3600000),
	async (req, res) => {
		try {
			const { uid } = req.params;
			const { personalKey } = req;

			// Remove from Redis
			await redisClient.client.del(`switch:${uid}`);
			await redisClient.client.sRem(`user:${personalKey}`, uid);
			await redisClient.client.sRem('public_switches', uid);

			logger.info(`Deleted switch ${uid}`);

			res.json({
				success: true,
				data: {
					message: 'Switch deleted successfully',
					uid
				}
			});
		} catch (error) {
			logger.error(`Error deleting switch ${req.params.uid}:`, error);
			res.status(500).json({
				success: false,
				error: 'Failed to delete switch'
			});
		}
	}
);

// Delete personal key and all associated data (GDPR compliance)
router.post('/delete-key',
	validateRequest(schemas.deleteKey),
	authManager.rateLimit('delete_key', 5, 3600000), // 5 per hour
	async (req, res) => {
		try {
			const { personalKey } = req.validatedData;

			// Validate the key exists
			const isValid = await redisClient.validatePersonalKey(personalKey);
			if (!isValid) {
				return res.status(404).json({
					success: false,
					error: 'Personal key not found'
				});
			}

			// Delete all data
			const deletedSwitchCount = await redisClient.deletePersonalKey(personalKey);

			logger.info(`Deleted personal key ${personalKey.substring(0, 8)}... and ${deletedSwitchCount} switches`);

			res.json({
				success: true,
				data: {
					message: 'All personal data deleted successfully',
					deletedSwitches: deletedSwitchCount
				}
			});
		} catch (error) {
			logger.error('Error deleting personal key:', error);
			res.status(500).json({
				success: false,
				error: 'Failed to delete personal data'
			});
		}
	}
);

// Health check endpoint
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

// Server stats (for monitoring)
router.get('/stats',
	authManager.rateLimit('stats', 60, 900000),
	async (req, res) => {
		try {
			const wsStats = webSocketManager.getStats();
			const publicSwitches = await redisClient.getPublicSwitches();

			res.json({
				success: true,
				data: {
					websocket: wsStats,
					publicSwitchCount: publicSwitches.length,
					timestamp: Date.now()
				}
			});
		} catch (error) {
			logger.error('Error getting server stats:', error);
			res.status(500).json({
				success: false,
				error: 'Failed to get server stats'
			});
		}
	}
);

// Error handling middleware
router.use((error, req, res, _next) => {
	logger.error('API route error:', error);

	res.status(500).json({
		success: false,
		error: 'Internal server error',
		...(process.env.NODE_ENV === 'development' && { details: error.message })
	});
});

module.exports = router;
