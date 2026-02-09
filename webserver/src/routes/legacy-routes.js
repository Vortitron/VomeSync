/**
 * Legacy V1 API endpoints.
 *
 * All routes require the legacy API to be enabled in configuration
 * (LEGACY_API_ENABLED env var).  Authentication is via personal key /
 * JWT / API key.
 */
const express = require('express');
const { v4: uuidv4 } = require('uuid');
const redisClient = require('../utils/redis');
const authManager = require('../utils/auth');
const logger = require('../utils/logger');
const {
	validateRequest,
	validateUID,
	schemas,
	sanitizePrivateSwitchData
} = require('../utils/validation');
const {
	requireLegacyEnabled,
	requireSessionTokensEnabled,
	sessionTokenApiKeyTtlSeconds,
	checkFreeTierLimits,
	sendFreeTierLimitError,
	abbreviateActor
} = require('./route-helpers');

const router = express.Router();

// ── Generate personal key ──────────────────────────────────────────────────────

router.post('/generate-key',
	requireLegacyEnabled,
	authManager.rateLimit('generate_key', 10, 3600000),
	validateRequest(schemas.generateKey),
	async (req, res) => {
		try {
			const personalKey = authManager.generatePersonalKey();
			await redisClient.storePersonalKey(personalKey);
			const jwt = authManager.generateJWT(personalKey);

			logger.info('Generated new personal key');

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
			return res.status(500).json({ success: false, error: 'Failed to generate personal key' });
		}
	}
);

// ── Release a switch name (called when a switch is deleted) ────────────────────

router.post('/release-switch-name',
	requireLegacyEnabled,
	authManager.requireAuth(),
	authManager.rateLimit('release_switch_name', 30, 60000),
	async (req, res) => {
		try {
			const { name } = req.body || {};
			if (!name || typeof name !== 'string') {
				return res.status(400).json({
					success: false,
					error: 'Missing or invalid name parameter'
				});
			}

			const released = await redisClient.releaseSwitchName(name);

			return res.json({
				success: true,
				data: { released }
			});
		} catch (error) {
			logger.error('Error releasing switch name:', error);
			return res.status(500).json({ success: false, error: 'Failed to release switch name' });
		}
	}
);

// ── Create switch ──────────────────────────────────────────────────────────────

router.post('/create-switch',
	requireLegacyEnabled,
	authManager.rateLimit('create_switch', 20, 3600000),
	authManager.requireAuth(),
	validateRequest(schemas.createSwitch),
	async (req, res) => {
		try {
			const uid = uuidv4();
			const { personalKeyId } = req;
			const switchConfig = { ...req.validatedData };
			const captchaToken = switchConfig.captchaToken;
			delete switchConfig.captchaToken;

			const limitCheck = await checkFreeTierLimits({
				personalKeyId,
				wantsPublicize: Boolean(switchConfig.publicize),
				currentPublicize: false
			});
			if (limitCheck) {
				return sendFreeTierLimitError(res, limitCheck.limit, limitCheck.max, limitCheck.tier);
			}

			if (switchConfig.publicize) {
				const captcha = await authManager.verifyCaptcha(captchaToken);
				if (!captcha.success) {
					return res.status(400).json({
						success: false,
						error: captcha.error || 'Captcha verification failed'
					});
				}
			}

			const switchData = await redisClient.createSwitch(uid, personalKeyId, switchConfig);

			logger.info(`Created new switch: ${uid}`);

			const parsedSwitch = await redisClient.getSwitchState(uid);

			return res.json({
				success: true,
				data: {
					uid,
					...sanitizePrivateSwitchData(parsedSwitch || switchData),
					websocketUrl: `/ws?uid=${uid}`
				}
			});
		} catch (error) {
			logger.error('Error creating switch:', error);
			return res.status(500).json({ success: false, error: 'Failed to create switch' });
		}
	}
);

// ── Toggle switch ──────────────────────────────────────────────────────────────

router.post('/toggle/:uid',
	requireLegacyEnabled,
	validateUID,
	authManager.rateLimit('toggle_switch', 200, 900000),
	authManager.requireSwitchAuth(),
	async (req, res) => {
		try {
			const { uid } = req.params;
			const { switchData } = req;
			const actorLabel = abbreviateActor(req.apiKeyId || req.personalKeyId);
			const timestamp = Date.now();

			const newState = !switchData.state;

			await redisClient.setSwitchState(uid, newState);
			const toggleCount = await redisClient.incrementToggleCount(uid);
			await redisClient.recordUserInteraction(uid, req.personalKeyId);
			await redisClient.appendEvent(uid, {
				type: 'state',
				state: newState,
				actor: actorLabel,
				viaApiKey: Boolean(req.apiKeyId),
				timestamp
			});

			await redisClient.publishSwitchUpdate(uid, newState);

			logger.info(`Toggled switch ${uid} to ${newState ? 'on' : 'off'}`);

			return res.json({
				success: true,
				data: { uid, state: newState, timestamp, toggleCount }
			});
		} catch (error) {
			logger.error(`Error toggling switch ${req.params.uid}:`, error);
			return res.status(500).json({ success: false, error: 'Failed to toggle switch' });
		}
	}
);

// ── List user's switches ───────────────────────────────────────────────────────

router.get('/my-switches',
	requireLegacyEnabled,
	authManager.requireAuth(),
	authManager.rateLimit('my_switches', 100, 900000),
	async (req, res) => {
		try {
			const { personalKeyId } = req;
			const userSwitches = await redisClient.getUserSwitches(personalKeyId);
			const sanitisedSwitches = userSwitches.map((sw) => sanitizePrivateSwitchData(sw));

			return res.json({
				success: true,
				data: {
					switches: sanitisedSwitches,
					count: sanitisedSwitches.length
				}
			});
		} catch (error) {
			logger.error('Error getting user switches:', error);
			return res.status(500).json({ success: false, error: 'Failed to get user switches' });
		}
	}
);

// ── Update switch metadata ─────────────────────────────────────────────────────

router.patch('/switch/:uid',
	requireLegacyEnabled,
	validateUID,
	authManager.requireSwitchAuth(),
	validateRequest(schemas.updateSwitch),
	async (req, res) => {
		try {
			const { uid } = req.params;
			const updates = { ...req.validatedData };
			const captchaToken = updates.captchaToken;
			delete updates.captchaToken;

			const limitCheck = await checkFreeTierLimits({
				personalKeyId: req.personalKeyId,
				wantsPublicize: updates.publicize === true,
				currentPublicize: Boolean(req.switchData && req.switchData.publicize)
			});
			if (limitCheck) {
				return sendFreeTierLimitError(res, limitCheck.limit, limitCheck.max, limitCheck.tier);
			}

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

			return res.json({
				success: true,
				data: sanitizePrivateSwitchData(updated)
			});
		} catch (error) {
			logger.error(`Error updating switch ${req.params.uid}:`, error);
			return res.status(500).json({ success: false, error: 'Failed to update switch' });
		}
	}
);

// ── Comments and timeline notes ────────────────────────────────────────────────

router.post('/switch/:uid/comment',
	requireLegacyEnabled,
	validateUID,
	authManager.requireSwitchAuth(),
	validateRequest(schemas.addComment),
	async (req, res) => {
		try {
			const { uid } = req.params;
			const actor = abbreviateActor(req.apiKeyId || req.personalKeyId);
			const timestamp = Date.now();
			const commentEvent = {
				uid,
				comment: req.validatedData.comment,
				actor,
				viaApiKey: Boolean(req.apiKeyId),
				timestamp
			};

			await redisClient.recordUserInteraction(uid, req.personalKeyId);
			await redisClient.addComment(uid, commentEvent);

			return res.json({
				success: true,
				data: commentEvent
			});
		} catch (error) {
			logger.error(`Error adding comment for ${req.params.uid}:`, error);
			return res.status(500).json({ success: false, error: 'Failed to add comment' });
		}
	}
);

// ── API key management ─────────────────────────────────────────────────────────

router.get('/api-keys',
	requireLegacyEnabled,
	authManager.requireAuth(),
	async (req, res) => {
		try {
			const keys = await redisClient.listApiKeys(req.personalKeyId);
			return res.json({ success: true, data: keys });
		} catch (error) {
			logger.error('Error listing API keys:', error);
			return res.status(500).json({ success: false, error: 'Failed to list API keys' });
		}
	}
);

router.post('/api-keys',
	requireLegacyEnabled,
	authManager.requireAuth(),
	authManager.rateLimit('create_api_key', 50, 900000),
	async (req, res) => {
		try {
			const name = req.body.name || '';
			const keyData = await redisClient.createApiKey(req.personalKeyId, name);
			return res.json({ success: true, data: keyData });
		} catch (error) {
			logger.error('Error creating API key:', error);
			return res.status(500).json({ success: false, error: 'Failed to create API key' });
		}
	}
);

router.delete('/api-keys/:apiKey',
	requireLegacyEnabled,
	authManager.requireAuth(),
	async (req, res) => {
		try {
			const { apiKey } = req.params;
			const revoked = await redisClient.revokeApiKey(req.personalKeyId, apiKey);
			if (!revoked) {
				return res.status(404).json({ success: false, error: 'API key not found' });
			}
			return res.json({ success: true });
		} catch (error) {
			logger.error('Error revoking API key:', error);
			return res.status(500).json({ success: false, error: 'Failed to revoke API key' });
		}
	}
);

// ── Owner profile link ─────────────────────────────────────────────────────────

router.post('/profile/link',
	requireLegacyEnabled,
	authManager.requireAuth(),
	validateRequest(schemas.updateProfile),
	async (req, res) => {
		try {
			const { profileUrl } = req.validatedData;
			await redisClient.setProfileUrl(req.personalKeyId, profileUrl);
			return res.json({ success: true, data: { profileUrl } });
		} catch (error) {
			logger.error('Error saving profile link:', error);
			return res.status(500).json({ success: false, error: 'Failed to save profile link' });
		}
	}
);

// ── Session tokens (web login) ─────────────────────────────────────────────────

router.post('/session-token',
	requireLegacyEnabled,
	requireSessionTokensEnabled,
	authManager.requireAuth(),
	async (req, res) => {
		try {
			const tokenData = await redisClient.createSessionToken(req.personalKeyId, 300);
			return res.json({ success: true, data: tokenData });
		} catch (error) {
			logger.error('Error creating session token:', error);
			return res.status(500).json({ success: false, error: 'Failed to create session token' });
		}
	}
);

router.post('/session-token/redeem',
	requireLegacyEnabled,
	requireSessionTokensEnabled,
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

			const apiKeyData = await redisClient.createApiKey(
				tokenData.personalKeyId,
				'web-session',
				sessionTokenApiKeyTtlSeconds
			);

			return res.json({
				success: true,
				data: {
					apiKey: apiKeyData.apiKey,
					apiKeyId: apiKeyData.apiKeyId,
					expiresInSeconds: sessionTokenApiKeyTtlSeconds
				}
			});
		} catch (error) {
			logger.error('Error redeeming session token:', error);
			return res.status(500).json({ success: false, error: 'Failed to redeem session token' });
		}
	}
);

// ── Delete switch ──────────────────────────────────────────────────────────────

router.delete('/switch/:uid',
	requireLegacyEnabled,
	validateUID,
	authManager.requireSwitchAuth(),
	authManager.rateLimit('delete_switch', 50, 3600000),
	async (req, res) => {
		try {
			const { uid } = req.params;
			const { personalKeyId } = req;

			await redisClient.client.del(`switch:${uid}`);
			await redisClient.client.del(`switch:${uid}:users`);
			await redisClient.client.del(`switch:${uid}:events`);
			await redisClient.client.sRem(`user:${personalKeyId}:switches`, uid);
			await redisClient.client.sRem('public_switches', uid);

			logger.info(`Deleted switch ${uid}`);

			return res.json({
				success: true,
				data: {
					message: 'Switch deleted successfully',
					uid
				}
			});
		} catch (error) {
			logger.error(`Error deleting switch ${req.params.uid}:`, error);
			return res.status(500).json({ success: false, error: 'Failed to delete switch' });
		}
	}
);

// ── Delete personal key (GDPR compliance) ──────────────────────────────────────

router.post('/delete-key',
	requireLegacyEnabled,
	validateRequest(schemas.deleteKey),
	authManager.rateLimit('delete_key', 5, 3600000),
	async (req, res) => {
		try {
			const { personalKey } = req.validatedData;

			const isValid = await redisClient.validatePersonalKey(personalKey);
			if (!isValid) {
				return res.status(404).json({ success: false, error: 'Personal key not found' });
			}

			const deletedSwitchCount = await redisClient.deletePersonalKey(personalKey);

			logger.info(`Deleted personal key and ${deletedSwitchCount} switches`);

			return res.json({
				success: true,
				data: {
					message: 'All personal data deleted successfully',
					deletedSwitches: deletedSwitchCount
				}
			});
		} catch (error) {
			logger.error('Error deleting personal key:', error);
			return res.status(500).json({ success: false, error: 'Failed to delete personal data' });
		}
	}
);

module.exports = router;

