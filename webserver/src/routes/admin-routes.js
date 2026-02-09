/**
 * Admin API endpoints.
 *
 * All routes require the admin API key via X-Admin-Key header or bearer
 * token.
 */
const express = require('express');
const crypto = require('crypto');
const redisClient = require('../utils/redis');
const logger = require('../utils/logger');
const media = require('../utils/media');
const {
	validateRequest,
	validateUID,
	schemas
} = require('../utils/validation');
const {
	requireAdmin,
	ADMIN_CHALLENGE_TTL_SECONDS,
	ADMIN_CHALLENGE_PREFIX
} = require('./route-helpers');

const router = express.Router();

// ── HMAC challenge endpoint (no auth required) ─────────────────────────────────

router.get('/admin/challenge',
	async (_req, res) => {
		try {
			const challenge = crypto.randomBytes(32).toString('hex');
			const redisKey = `${ADMIN_CHALLENGE_PREFIX}${challenge}`;
			await redisClient.client.set(redisKey, '1', { EX: ADMIN_CHALLENGE_TTL_SECONDS });
			return res.json({
				success: true,
				data: {
					challenge,
					expiresInSeconds: ADMIN_CHALLENGE_TTL_SECONDS
				}
			});
		} catch (error) {
			logger.error('Failed to generate admin challenge:', error);
			return res.status(500).json({ success: false, error: 'Failed to generate challenge' });
		}
	}
);

// ── Delist a public switch ─────────────────────────────────────────────────────

router.post('/admin/switch/:uid/delist',
	requireAdmin,
	validateUID,
	async (req, res) => {
		try {
			const { uid } = req.params;
			const updated = await redisClient.updateSwitch(uid, { publicize: false });
			if (!updated) {
				return res.status(404).json({ success: false, error: 'Switch not found' });
			}
			return res.json({
				success: true,
				data: {
					uid,
					publicize: Boolean(updated.publicize)
				}
			});
		} catch (error) {
			logger.error(`Admin delist failed for ${req.params.uid}:`, error);
			return res.status(500).json({ success: false, error: 'Failed to delist switch' });
		}
	}
);

// ── Delete a switch and its metadata ───────────────────────────────────────────

router.post('/admin/switch/:uid/delete',
	requireAdmin,
	validateUID,
	async (req, res) => {
		try {
			const { uid } = req.params;
			const deleted = await redisClient.deleteSwitchAdmin(uid);
			if (!deleted) {
				return res.status(404).json({ success: false, error: 'Switch not found' });
			}
			await media.deleteSwitchMedia(uid);
			return res.json({ success: true, data: { uid } });
		} catch (error) {
			logger.error(`Admin delete failed for ${req.params.uid}:`, error);
			return res.status(500).json({ success: false, error: 'Failed to delete switch' });
		}
	}
);

// ── Block / unblock keys or owners ─────────────────────────────────────────────

router.post('/admin/blocks',
	requireAdmin,
	validateRequest(schemas.adminBlock),
	async (req, res) => {
		try {
			const { action, type, value } = req.validatedData;
			let targetType = type;
			let targetValue = value;

			if (type === 'uid') {
				const switchData = await redisClient.getSwitchState(value);
				if (!switchData) {
					return res.status(404).json({ success: false, error: 'Switch not found' });
				}
				if (switchData.authVersion === 2 && switchData.ownerId) {
					targetType = 'owner';
					targetValue = switchData.ownerId;
				} else if (switchData.ownerKeyId) {
					targetType = 'personal';
					targetValue = switchData.ownerKeyId;
				} else {
					return res.status(400).json({ success: false, error: 'Unable to resolve owner for switch' });
				}
			}

			let updated = false;
			if (targetType === 'owner') {
				updated = action === 'block'
					? await redisClient.blockOwnerId(targetValue)
					: await redisClient.unblockOwnerId(targetValue);
			} else if (targetType === 'personal') {
				updated = action === 'block'
					? await redisClient.blockPersonalKeyId(targetValue)
					: await redisClient.unblockPersonalKeyId(targetValue);
			} else if (targetType === 'api') {
				updated = action === 'block'
					? await redisClient.blockApiKeyId(targetValue)
					: await redisClient.unblockApiKeyId(targetValue);
			}

			return res.json({
				success: true,
				data: {
					action,
					type: targetType,
					value: targetValue,
					blocked: action === 'block',
					updated
				}
			});
		} catch (error) {
			logger.error('Admin block operation failed:', error);
			return res.status(500).json({ success: false, error: 'Failed to update block list' });
		}
	}
);

// ── Create / update a redirect ─────────────────────────────────────────────────

router.post('/admin/redirects',
	requireAdmin,
	validateRequest(schemas.adminRedirect),
	async (req, res) => {
		try {
			const { fromUid, toUid, reason } = req.validatedData;
			if (fromUid === toUid) {
				return res.status(400).json({ success: false, error: 'Redirect target must differ' });
			}
			const target = await redisClient.getSwitchState(toUid);
			if (!target) {
				return res.status(404).json({ success: false, error: 'Target switch not found' });
			}

			const redirect = await redisClient.setSwitchRedirect(fromUid, toUid, reason);
			return res.json({ success: true, data: redirect });
		} catch (error) {
			logger.error('Admin redirect failed:', error);
			return res.status(500).json({ success: false, error: 'Failed to set redirect' });
		}
	}
);

// ── Clear a redirect ───────────────────────────────────────────────────────────

router.delete('/admin/redirects/:uid',
	requireAdmin,
	validateUID,
	async (req, res) => {
		try {
			const { uid } = req.params;
			await redisClient.clearSwitchRedirect(uid);
			return res.json({ success: true, data: { uid } });
		} catch (error) {
			logger.error(`Admin redirect clear failed for ${req.params.uid}:`, error);
			return res.status(500).json({ success: false, error: 'Failed to clear redirect' });
		}
	}
);

// ── Override listing fields for public switches ────────────────────────────────

router.post('/admin/switch/:uid/override',
	requireAdmin,
	validateUID,
	validateRequest(schemas.adminListingOverride),
	async (req, res) => {
		try {
			const { uid } = req.params;
			const switchData = await redisClient.getSwitchState(uid);
			if (!switchData) {
				return res.status(404).json({ success: false, error: 'Switch not found' });
			}
			if (!switchData.publicize) {
				return res.status(400).json({ success: false, error: 'Switch is not public' });
			}
			const overrides = { ...req.validatedData };
			try {
				if (typeof overrides.iconUrl === 'string' && overrides.iconUrl.trim()) {
					overrides.iconUrl = await media.ingestImageFromUrl(uid, 'icon', overrides.iconUrl);
				}
				if (typeof overrides.bannerUrl === 'string' && overrides.bannerUrl.trim()) {
					overrides.bannerUrl = await media.ingestImageFromUrl(uid, 'banner', overrides.bannerUrl);
				}
			} catch (imgErr) {
				return res.status(400).json({ success: false, error: imgErr && imgErr.message ? imgErr.message : 'Invalid image URL' });
			}
			const override = await redisClient.setSwitchListingOverride(uid, overrides);
			return res.json({ success: true, data: { uid, override } });
		} catch (error) {
			logger.error(`Admin override failed for ${req.params.uid}:`, error);
			return res.status(500).json({ success: false, error: 'Failed to set listing override' });
		}
	}
);

// ── Clear listing override ─────────────────────────────────────────────────────

router.delete('/admin/switch/:uid/override',
	requireAdmin,
	validateUID,
	async (req, res) => {
		try {
			const { uid } = req.params;
			await redisClient.clearSwitchListingOverride(uid);
			return res.json({ success: true, data: { uid } });
		} catch (error) {
			logger.error(`Admin override clear failed for ${req.params.uid}:`, error);
			return res.status(500).json({ success: false, error: 'Failed to clear listing override' });
		}
	}
);

// ── Promo Code Management ─────────────────────────────────────────────────────

router.post('/admin/promo-codes',
	requireAdmin,
	async (req, res) => {
		try {
			const { code, tier, durationDays, maxRedemptions } = req.body || {};
			if (!code || typeof code !== 'string' || code.trim().length < 3) {
				return res.status(400).json({ success: false, error: 'Code must be at least 3 characters' });
			}
			const result = await redisClient.createPromoCode({
				code: code.trim(),
				tier: tier || 'premium',
				durationDays: Number(durationDays) || 90,
				maxRedemptions: maxRedemptions !== undefined ? Number(maxRedemptions) : 1,
				createdBy: 'admin'
			});
			if (!result) {
				return res.status(409).json({ success: false, error: 'Promo code already exists' });
			}
			logger.info(`Admin created promo code: ${code.trim()}`);
			return res.json({ success: true, data: result });
		} catch (error) {
			logger.error('Error creating promo code:', error);
			return res.status(500).json({ success: false, error: 'Failed to create promo code' });
		}
	}
);

router.get('/admin/promo-codes',
	requireAdmin,
	async (_req, res) => {
		try {
			const codes = await redisClient.listPromoCodes();
			return res.json({ success: true, data: codes });
		} catch (error) {
			logger.error('Error listing promo codes:', error);
			return res.status(500).json({ success: false, error: 'Failed to list promo codes' });
		}
	}
);

router.delete('/admin/promo-codes/:code',
	requireAdmin,
	async (req, res) => {
		try {
			const { code } = req.params;
			const deleted = await redisClient.deletePromoCode(code);
			if (!deleted) {
				return res.status(404).json({ success: false, error: 'Promo code not found' });
			}
			logger.info(`Admin deleted promo code: ${code}`);
			return res.json({ success: true });
		} catch (error) {
			logger.error('Error deleting promo code:', error);
			return res.status(500).json({ success: false, error: 'Failed to delete promo code' });
		}
	}
);

// ── Owner tier lookup (admin) ─────────────────────────────────────────────────

router.get('/admin/owner/:ownerId/tier',
	requireAdmin,
	async (req, res) => {
		try {
			const { ownerId } = req.params;
			const tier = await redisClient.getOwnerTier(ownerId);
			return res.json({ success: true, data: tier });
		} catch (error) {
			logger.error('Error getting owner tier:', error);
			return res.status(500).json({ success: false, error: 'Failed to get owner tier' });
		}
	}
);

router.post('/admin/owner/:ownerId/tier',
	requireAdmin,
	async (req, res) => {
		try {
			const { ownerId } = req.params;
			const { tier, durationDays } = req.body || {};
			if (!tier) {
				return res.status(400).json({ success: false, error: 'tier is required' });
			}
			const expiresAt = durationDays
				? Date.now() + Number(durationDays) * 24 * 60 * 60 * 1000
				: 0;
			await redisClient.setOwnerTier(ownerId, tier, expiresAt, 'admin-grant');
			logger.info(`Admin set tier for ${ownerId}: ${tier} (expires: ${expiresAt || 'never'})`);
			return res.json({ success: true, data: { ownerId, tier, expiresAt } });
		} catch (error) {
			logger.error('Error setting owner tier:', error);
			return res.status(500).json({ success: false, error: 'Failed to set owner tier' });
		}
	}
);

module.exports = router;

