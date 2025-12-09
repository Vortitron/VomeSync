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

const abbreviateKey = (key) => {
	if (!key) {
		return 'unknown';
	}
	return `${key.substring(0, 8)}...`;
};

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
			const switchConfig = { ...req.validatedData };
			const captchaToken = switchConfig.captchaToken;
			delete switchConfig.captchaToken;

			// Enforce CAPTCHA for public listings if configured
			if (switchConfig.publicize) {
				const captcha = await authManager.verifyCaptcha(captchaToken);
				if (!captcha.success) {
					return res.status(400).json({
						success: false,
						error: captcha.error || 'Captcha verification failed'
					});
				}
			}

			// Create switch in Redis
			const switchData = await redisClient.createSwitch(uid, personalKey, switchConfig);

			logger.info(`Created new switch: ${uid} by ${personalKey.substring(0, 8)}...`);

			// Retrieve parsed state for response
			const parsedSwitch = await redisClient.getSwitchState(uid);

			res.json({
				success: true,
				data: {
					uid,
					...sanitizePrivateSwitchData(parsedSwitch || switchData),
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
			const actorLabel = abbreviateKey(req.apiKeyUsed || req.personalKey);
			const timestamp = Date.now();

			// Toggle state
			const newState = !switchData.state;

			// Update in Redis
			await redisClient.setSwitchState(uid, newState);
			const toggleCount = await redisClient.incrementToggleCount(uid);
			await redisClient.recordUserInteraction(uid, req.personalKey);
			await redisClient.appendEvent(uid, {
				type: 'state',
				state: newState,
				actor: actorLabel,
				viaApiKey: Boolean(req.apiKeyUsed),
				timestamp
			});

			// Publish update via Redis for WebSocket broadcasting
			await redisClient.publishSwitchUpdate(uid, newState);

			logger.info(`Toggled switch ${uid} to ${newState ? 'on' : 'off'}`);

			res.json({
				success: true,
				data: {
					uid,
					state: newState,
					timestamp,
					toggleCount
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

// Public switch detail (for website deep links)
router.get('/switch/:uid',
	validateUID,
	authManager.rateLimit('public_switch_detail', 200, 900000),
	async (req, res) => {
		try {
			const { uid } = req.params;
			const detail = await redisClient.getPublicSwitchDetail(uid);
			if (!detail) {
				return res.status(404).json({
					success: false,
					error: 'Switch not found or not public'
				});
			}

			res.json({
				success: true,
				data: detail
			});
		} catch (error) {
			logger.error(`Error getting switch detail ${req.params.uid}:`, error);
			res.status(500).json({
				success: false,
				error: 'Failed to get switch detail'
			});
		}
	}
);

// Public category listing
router.get('/categories',
	authManager.rateLimit('public_categories', 100, 900000),
	async (_req, res) => {
		try {
			const categories = await redisClient.getCategoryCounts();
			res.json({
				success: true,
				data: categories
			});
		} catch (error) {
			logger.error('Error getting categories:', error);
			res.status(500).json({
				success: false,
				error: 'Failed to get categories'
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

// Update switch metadata (owner/API key)
router.patch('/switch/:uid',
	validateUID,
	authManager.requireSwitchAuth(),
	validateRequest(schemas.updateSwitch),
	async (req, res) => {
		try {
			const { uid } = req.params;
			const updates = { ...req.validatedData };
			const captchaToken = updates.captchaToken;
			delete updates.captchaToken;

			if (updates.publicize === true) {
				const captcha = await authManager.verifyCaptcha(captchaToken);
				if (!captcha.success) {
					return res.status(400).json({ success: false, error: captcha.error || 'Captcha verification failed' });
				}
			}

			const updated = await redisClient.updateSwitch(uid, updates);
			if (!updated) {
				return res.status(404).json({ success: false, error: 'Switch not found' });
			}

			res.json({
				success: true,
				data: sanitizePrivateSwitchData(updated)
			});
		} catch (error) {
			logger.error(`Error updating switch ${req.params.uid}:`, error);
			res.status(500).json({
				success: false,
				error: 'Failed to update switch'
			});
		}
	}
);

// Comments and timeline notes
router.post('/switch/:uid/comment',
	validateUID,
	authManager.requireSwitchAuth(),
	validateRequest(schemas.addComment),
	async (req, res) => {
		try {
			const { uid } = req.params;
			const actor = abbreviateKey(req.apiKeyUsed || req.personalKey);
			const timestamp = Date.now();
			const commentEvent = {
				uid,
				comment: req.validatedData.comment,
				actor,
				viaApiKey: Boolean(req.apiKeyUsed),
				timestamp
			};

			await redisClient.recordUserInteraction(uid, req.personalKey);
			await redisClient.addComment(uid, commentEvent);

			res.json({
				success: true,
				data: commentEvent
			});
		} catch (error) {
			logger.error(`Error adding comment for ${req.params.uid}:`, error);
			res.status(500).json({
				success: false,
				error: 'Failed to add comment'
			});
		}
	}
);

// API key management
router.get('/api-keys',
	authManager.requireAuth(),
	async (req, res) => {
		try {
			const keys = await redisClient.listApiKeys(req.personalKey);
			res.json({ success: true, data: keys });
		} catch (error) {
			logger.error('Error listing API keys:', error);
			res.status(500).json({ success: false, error: 'Failed to list API keys' });
		}
	}
);

router.post('/api-keys',
	authManager.requireAuth(),
	authManager.rateLimit('create_api_key', 50, 900000),
	async (req, res) => {
		try {
			const name = req.body.name || '';
			const keyData = await redisClient.createApiKey(req.personalKey, name);
			res.json({ success: true, data: keyData });
		} catch (error) {
			logger.error('Error creating API key:', error);
			res.status(500).json({ success: false, error: 'Failed to create API key' });
		}
	}
);

// Owner profile link (for website display)
router.post('/profile/link',
	authManager.requireAuth(),
	validateRequest(schemas.updateProfile),
	async (req, res) => {
		try {
			const { profileUrl } = req.validatedData;
			await redisClient.setProfileUrl(req.personalKey, profileUrl);
			res.json({ success: true, data: { profileUrl } });
		} catch (error) {
			logger.error('Error saving profile link:', error);
			res.status(500).json({ success: false, error: 'Failed to save profile link' });
		}
	}
);

router.delete('/api-keys/:apiKey',
	authManager.requireAuth(),
	async (req, res) => {
		try {
			const { apiKey } = req.params;
			const revoked = await redisClient.revokeApiKey(req.personalKey, apiKey);
			if (!revoked) {
				return res.status(404).json({ success: false, error: 'API key not found' });
			}
			res.json({ success: true });
		} catch (error) {
			logger.error('Error revoking API key:', error);
			res.status(500).json({ success: false, error: 'Failed to revoke API key' });
		}
	}
);

// Session token for web login (one-time, short-lived)
router.post('/session-token',
	authManager.requireAuth(),
	async (req, res) => {
		try {
			const tokenData = await redisClient.createSessionToken(req.personalKey, 300);
			res.json({ success: true, data: tokenData });
		} catch (error) {
			logger.error('Error creating session token:', error);
			res.status(500).json({ success: false, error: 'Failed to create session token' });
		}
	}
);

// Redeem session token (called by website)
router.post('/session-token/redeem',
	async (req, res) => {
		try {
			const { token } = req.body;
			if (!token) {
				return res.status(400).json({ success: false, error: 'Token required' });
			}

			const tokenData = await redisClient.redeemSessionToken(token);
			if (!tokenData) {
				return res.status(404).json({ success: false, error: 'Token not found or expired' });
			}

			// Issue a short-lived API key tied to the personal key
			const apiKeyData = await redisClient.createApiKey(tokenData.personalKey, 'web-session');

			res.json({
				success: true,
				data: {
					personalKey: tokenData.personalKey,
					apiKey: apiKeyData.apiKey,
					expiresIn: '1 year' // aligns with existing keys; adjust if needed
				}
			});
		} catch (error) {
			logger.error('Error redeeming session token:', error);
			res.status(500).json({ success: false, error: 'Failed to redeem session token' });
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
			await redisClient.client.del(`switch:${uid}:users`);
			await redisClient.client.del(`switch:${uid}:events`);
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
