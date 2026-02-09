/**
 * V2 crypto-signed API endpoints.
 *
 * All owner-level operations require Ed25519 signatures and nonce replay
 * protection.  Delegated access-key endpoints (toggle, metadata, comment)
 * authenticate via bearer token.
 */
const express = require('express');
const redisClient = require('../utils/redis');
const authManager = require('../utils/auth');
const logger = require('../utils/logger');
const media = require('../utils/media');
const {
	validateRequest,
	validateUID,
	schemas,
	sanitizePrivateSwitchData
} = require('../utils/validation');
const {
	deriveOwnerIdFromOwnerPubKeyB64Url,
	deriveSwitchUidFromSwitchPubKeyB64Url,
	verifyEd25519SignatureB64Url
} = require('../utils/crypto_v2');
const {
	V2_ACCESS_KEY_MAX_TTL_SECONDS,
	assertFreshTimestamp,
	clockSkewError,
	checkFreeTierLimits,
	sendFreeTierLimitError,
	pickSwitchMetadata,
	pickSwitchMetadataUpdatesV2,
	abbreviateActor,
	uploadV2MetadataFiles,
	v2CanonicalCreate,
	v2CanonicalMySwitches,
	v2CanonicalSetState,
	v2CanonicalUpdateSwitch,
	v2CanonicalCreateAccessKey,
	v2CanonicalListAccessKeys,
	v2CanonicalRevokeAccessKey,
	v2CanonicalPauseAccessKey,
	v2CanonicalUpdateAccessKeyPermissions,
	v2CanonicalRedeemPromo,
	v2CanonicalGetOwnerTier
} = require('./route-helpers');

const router = express.Router();

// ── V2: Create switch (deterministic UID derived from switch pubkey, signed by owner + switch) ──

router.post('/v2/switch',
	authManager.rateLimit('v2_create_switch', 30, 3600000),
	validateRequest(schemas.v2CreateSwitch),
	async (req, res) => {
		try {
			const data = req.validatedData;
			const captchaToken = data.captchaToken;

			// Enforce CAPTCHA for public listings if configured
			if (data.publicize) {
				const captcha = await authManager.verifyCaptcha(captchaToken);
				if (!captcha.success) {
					return res.status(400).json({
						success: false,
						error: captcha.error || 'Captcha verification failed'
					});
				}
			}

			if (!assertFreshTimestamp(data.ts)) {
				return res.status(400).json(clockSkewError());
			}

			const ownerId = deriveOwnerIdFromOwnerPubKeyB64Url(data.ownerPubKey);
			const uid = deriveSwitchUidFromSwitchPubKeyB64Url(data.switchPubKey);

			if (await redisClient.isOwnerBlocked(ownerId)) {
				return res.status(403).json({ success: false, error: 'Owner blocked' });
			}

			const canonical = v2CanonicalCreate(data, uid);

			// Verify signatures first (cheap, no Redis writes yet)
			const ownerOk = verifyEd25519SignatureB64Url(data.ownerPubKey, canonical, data.sigOwner);
			if (!ownerOk) {
				return res.status(401).json({ success: false, error: 'Invalid owner signature' });
			}

			const switchOk = verifyEd25519SignatureB64Url(data.switchPubKey, canonical, data.sigSwitch);
			if (!switchOk) {
				return res.status(401).json({ success: false, error: 'Invalid switch signature' });
			}

			// Replay protection
			const claimed = await redisClient.claimV2Nonce(ownerId, data.nonce, 10 * 60 * 1000);
			if (!claimed) {
				return res.status(409).json({ success: false, error: 'Nonce already used' });
			}

			// Idempotency: if already exists for same owner, return it
			const existing = await redisClient.getSwitchState(uid);
			if (existing) {
				if (existing.authVersion === 2 && existing.ownerId === ownerId && existing.switchPubKey === data.switchPubKey) {
					return res.json({
						success: true,
						data: {
							uid,
							...sanitizePrivateSwitchData(existing),
							websocketUrl: `/ws?uid=${uid}`
						}
					});
				}
				return res.status(409).json({ success: false, error: 'Switch UID already exists' });
			}

			const limitCheck = await checkFreeTierLimits({
				ownerId,
				wantsPublicize: Boolean(data.publicize),
				currentPublicize: false
			});
			if (limitCheck) {
				return sendFreeTierLimitError(res, limitCheck.limit, limitCheck.max, limitCheck.tier);
			}

			const switchConfig = pickSwitchMetadata(data);
			// If icon/banner URLs are provided, ingest and re-host them (store only local URLs).
			try {
				if (typeof switchConfig.iconUrl === 'string' && switchConfig.iconUrl.trim()) {
					switchConfig.iconUrl = await media.ingestImageFromUrl(uid, 'icon', switchConfig.iconUrl);
				}
				if (typeof switchConfig.bannerUrl === 'string' && switchConfig.bannerUrl.trim()) {
					switchConfig.bannerUrl = await media.ingestImageFromUrl(uid, 'banner', switchConfig.bannerUrl);
				}
			} catch (imgErr) {
				return res.status(400).json({ success: false, error: imgErr && imgErr.message ? imgErr.message : 'Invalid image URL' });
			}

			await redisClient.createSwitchV2(uid, ownerId, data.ownerPubKey, data.switchPubKey, data.index, switchConfig);
			const parsedSwitch = await redisClient.getSwitchState(uid);

			logger.info(`Created v2 switch: ${uid} (owner=${ownerId.substring(0, 8)}...)`);

			return res.json({
				success: true,
				data: {
					uid,
					...sanitizePrivateSwitchData(parsedSwitch),
					websocketUrl: `/ws?uid=${uid}`
				}
			});
		} catch (error) {
			logger.error('Error creating v2 switch:', error);
			return res.status(500).json({ success: false, error: 'Failed to create switch' });
		}
	}
);

// ── V2: List my switches ───────────────────────────────────────────────────────

router.post('/v2/my-switches',
	authManager.rateLimit('v2_my_switches', 200, 900000),
	validateRequest(schemas.v2MySwitches),
	async (req, res) => {
		try {
			const data = req.validatedData;

			if (!assertFreshTimestamp(data.ts)) {
				return res.status(400).json(clockSkewError());
			}

			const ownerId = deriveOwnerIdFromOwnerPubKeyB64Url(data.ownerPubKey);

			if (await redisClient.isOwnerBlocked(ownerId)) {
				return res.status(403).json({ success: false, error: 'Owner blocked' });
			}

			const canonical = v2CanonicalMySwitches(data);
			const ok = verifyEd25519SignatureB64Url(data.ownerPubKey, canonical, data.sigOwner);
			if (!ok) {
				return res.status(401).json({ success: false, error: 'Invalid owner signature' });
			}

			const claimed = await redisClient.claimV2Nonce(ownerId, data.nonce, 10 * 60 * 1000);
			if (!claimed) {
				return res.status(409).json({ success: false, error: 'Nonce already used' });
			}

			const switches = await redisClient.getOwnerSwitches(ownerId);
			const sanitised = switches.map((sw) => sanitizePrivateSwitchData(sw));

			return res.json({
				success: true,
				data: {
					switches: sanitised,
					count: sanitised.length
				}
			});
		} catch (error) {
			logger.error('Error listing v2 owner switches:', error);
			return res.status(500).json({ success: false, error: 'Failed to list switches' });
		}
	}
);

// ── V2: Set switch state (signed by switch key; supports params passthrough) ──

router.post('/v2/switch/:uid/state',
	validateUID,
	authManager.rateLimit('v2_set_state', 500, 900000),
	validateRequest(schemas.v2SetState),
	async (req, res) => {
		try {
			const { uid } = req.params;
			const data = req.validatedData;

			if (!assertFreshTimestamp(data.ts)) {
				return res.status(400).json(clockSkewError());
			}

			const switchData = await redisClient.getSwitchState(uid);
			if (!switchData) {
				return res.status(404).json({ success: false, error: 'Switch not found' });
			}
			if (switchData.ownerId && await redisClient.isOwnerBlocked(switchData.ownerId)) {
				return res.status(403).json({ success: false, error: 'Owner blocked' });
			}
			if (switchData.authVersion !== 2 || !switchData.switchPubKey) {
				return res.status(400).json({ success: false, error: 'Switch is not crypto-authenticated' });
			}

			const ownerId = switchData.ownerId || 'unknown';
			if (ownerId && ownerId !== 'unknown') {
				await redisClient.recordUserInteraction(uid, ownerId);
			}
			const claimed = await redisClient.claimV2Nonce(ownerId, data.nonce, 10 * 60 * 1000);
			if (!claimed) {
				return res.status(409).json({ success: false, error: 'Nonce already used' });
			}

			const canonical = v2CanonicalSetState(uid, data);
			const ok = verifyEd25519SignatureB64Url(switchData.switchPubKey, canonical, data.sigSwitch);
			if (!ok) {
				return res.status(401).json({ success: false, error: 'Invalid switch signature' });
			}

			const newState = Boolean(data.state);
			const oldState = Boolean(switchData.state);
			const params = (data.params && typeof data.params === 'object') ? data.params : {};
			const timestamp = Date.now();

			await redisClient.setSwitchState(uid, newState, { params });

			let toggleCount = switchData.toggleCount || 0;
			if (newState !== oldState) {
				toggleCount = await redisClient.incrementToggleCount(uid);
			}

			await redisClient.appendEvent(uid, {
				type: 'state',
				state: newState,
				actor: `owner:${(ownerId || '').substring(0, 8)}...`,
				viaApiKey: false,
				params,
				timestamp
			});

			await redisClient.publishSwitchUpdate(uid, newState, params);

			return res.json({
				success: true,
				data: {
					uid,
					state: newState,
					timestamp,
					toggleCount
				}
			});
		} catch (error) {
			logger.error(`Error setting v2 switch state ${req.params.uid}:`, error);
			return res.status(500).json({ success: false, error: 'Failed to set switch state' });
		}
	}
);

// ── V2: Update switch metadata ─────────────────────────────────────────────────

router.post('/v2/switch/:uid',
	validateUID,
	authManager.rateLimit('v2_update_switch', 200, 900000),
	validateRequest(schemas.v2UpdateSwitch),
	async (req, res) => {
		try {
			const { uid } = req.params;
			const data = req.validatedData;
			const captchaToken = data.captchaToken;

			if (!assertFreshTimestamp(data.ts)) {
				return res.status(400).json(clockSkewError());
			}

			const ownerId = deriveOwnerIdFromOwnerPubKeyB64Url(data.ownerPubKey);

			if (await redisClient.isOwnerBlocked(ownerId)) {
				return res.status(403).json({ success: false, error: 'Owner blocked' });
			}

			const switchData = await redisClient.getSwitchState(uid);
			if (!switchData) {
				return res.status(404).json({ success: false, error: 'Switch not found' });
			}
			if (switchData.authVersion !== 2 || !switchData.ownerId || !switchData.ownerPubKey) {
				return res.status(400).json({ success: false, error: 'Switch is not crypto-authenticated' });
			}
			if (switchData.ownerId !== ownerId || switchData.ownerPubKey !== data.ownerPubKey) {
				return res.status(401).json({ success: false, error: 'Invalid owner signature' });
			}

			const updates = pickSwitchMetadataUpdatesV2(data);
			if (!updates || Object.keys(updates).length === 0) {
				return res.status(400).json({ success: false, error: 'No metadata updates provided' });
			}

			const canonical = v2CanonicalUpdateSwitch(uid, data, updates);
			const ok = verifyEd25519SignatureB64Url(data.ownerPubKey, canonical, data.sigOwner);
			if (!ok) {
				return res.status(401).json({ success: false, error: 'Invalid owner signature' });
			}

			const limitCheck = await checkFreeTierLimits({
				ownerId,
				wantsPublicize: updates.publicize === true,
				currentPublicize: Boolean(switchData.publicize)
			});
			if (limitCheck) {
				return sendFreeTierLimitError(res, limitCheck.limit, limitCheck.max, limitCheck.tier);
			}

			const claimed = await redisClient.claimV2Nonce(ownerId, data.nonce, 10 * 60 * 1000);
			if (!claimed) {
				return res.status(409).json({ success: false, error: 'Nonce already used' });
			}

			// Enforce CAPTCHA when turning on public listing
			if (updates.publicize === true) {
				const captcha = await authManager.verifyCaptcha(captchaToken);
				if (!captcha.success) {
					return res.status(400).json({
						success: false,
						error: captcha.error || 'Captcha verification failed'
					});
				}
			}

			// Ingest icon/banner URLs if provided.
			try {
				if (typeof updates.iconUrl === 'string' && updates.iconUrl.trim()) {
					updates.iconUrl = await media.ingestImageFromUrl(uid, 'icon', updates.iconUrl);
				}
				if (typeof updates.bannerUrl === 'string' && updates.bannerUrl.trim()) {
					updates.bannerUrl = await media.ingestImageFromUrl(uid, 'banner', updates.bannerUrl);
				}
			} catch (imgErr) {
				return res.status(400).json({
					success: false,
					error: imgErr && imgErr.message ? imgErr.message : 'Invalid image URL'
				});
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
			logger.error(`Error updating v2 switch metadata ${req.params.uid}:`, error);
			return res.status(500).json({ success: false, error: 'Failed to update switch' });
		}
	}
);

// ── V2: Create access key ──────────────────────────────────────────────────────

router.post('/v2/switch/:uid/access-keys',
	validateUID,
	authManager.rateLimit('v2_access_keys_create', 200, 900000),
	validateRequest(schemas.v2CreateAccessKey),
	async (req, res) => {
		try {
			const { uid } = req.params;
			const data = req.validatedData;

			if (!assertFreshTimestamp(data.ts)) {
				return res.status(400).json(clockSkewError());
			}

			const switchData = await redisClient.getSwitchState(uid);
			if (!switchData) {
				return res.status(404).json({ success: false, error: 'Switch not found' });
			}
			if (switchData.authVersion !== 2 || !switchData.ownerId || !switchData.ownerPubKey) {
				return res.status(400).json({ success: false, error: 'Switch is not crypto-authenticated' });
			}

			const ownerId = deriveOwnerIdFromOwnerPubKeyB64Url(data.ownerPubKey);
			if (await redisClient.isOwnerBlocked(ownerId)) {
				return res.status(403).json({ success: false, error: 'Owner blocked' });
			}
			if (switchData.ownerId !== ownerId || switchData.ownerPubKey !== data.ownerPubKey) {
				return res.status(401).json({ success: false, error: 'Invalid owner signature' });
			}

			const payload = {};
			if (Object.prototype.hasOwnProperty.call(req.body, 'name')) {
				payload.name = data.name || '';
			}
			if (Object.prototype.hasOwnProperty.call(req.body, 'permissions')) {
				payload.permissions = Array.isArray(data.permissions) ? data.permissions : [];
			}
			if (Object.prototype.hasOwnProperty.call(req.body, 'ttlSeconds')) {
				payload.ttlSeconds = data.ttlSeconds;
			}
			const canonical = v2CanonicalCreateAccessKey(uid, data, payload);
			const ok = verifyEd25519SignatureB64Url(data.ownerPubKey, canonical, data.sigOwner);
			if (!ok) {
				return res.status(401).json({ success: false, error: 'Invalid owner signature' });
			}

			const claimed = await redisClient.claimV2Nonce(ownerId, data.nonce, 10 * 60 * 1000);
			if (!claimed) {
				return res.status(409).json({ success: false, error: 'Nonce already used' });
			}

			const ttlSeconds = Number.isFinite(data.ttlSeconds) ? data.ttlSeconds : null;
			if (ttlSeconds && ttlSeconds > V2_ACCESS_KEY_MAX_TTL_SECONDS) {
				return res.status(400).json({ success: false, error: 'ttlSeconds exceeds max of 30 days' });
			}

			const created = await redisClient.createV2AccessKey(
				ownerId,
				uid,
				data.name || '',
				data.permissions,
				ttlSeconds
			);
			if (!created) {
				return res.status(500).json({ success: false, error: 'Failed to create API key' });
			}

			return res.json({
				success: true,
				data: {
					apiKey: created.apiKey,
					keyId: created.apiKeyId,
					name: created.name || '',
					permissions: created.permissions || [],
					createdAt: created.createdAt,
					expiresAt: created.expiresAt || 0
				}
			});
		} catch (error) {
			logger.error(`Error creating v2 access key for ${req.params.uid}:`, error);
			return res.status(500).json({ success: false, error: 'Failed to create API key' });
		}
	}
);

// ── V2: List access keys ───────────────────────────────────────────────────────

router.post('/v2/switch/:uid/access-keys/list',
	validateUID,
	authManager.rateLimit('v2_access_keys_list', 200, 900000),
	validateRequest(schemas.v2ListAccessKeys),
	async (req, res) => {
		try {
			const { uid } = req.params;
			const data = req.validatedData;

			if (!assertFreshTimestamp(data.ts)) {
				return res.status(400).json(clockSkewError());
			}

			const switchData = await redisClient.getSwitchState(uid);
			if (!switchData) {
				return res.status(404).json({ success: false, error: 'Switch not found' });
			}
			if (switchData.authVersion !== 2 || !switchData.ownerId || !switchData.ownerPubKey) {
				return res.status(400).json({ success: false, error: 'Switch is not crypto-authenticated' });
			}

			const ownerId = deriveOwnerIdFromOwnerPubKeyB64Url(data.ownerPubKey);
			if (await redisClient.isOwnerBlocked(ownerId)) {
				return res.status(403).json({ success: false, error: 'Owner blocked' });
			}
			if (switchData.ownerId !== ownerId || switchData.ownerPubKey !== data.ownerPubKey) {
				return res.status(401).json({ success: false, error: 'Invalid owner signature' });
			}

			const canonical = v2CanonicalListAccessKeys(uid, data);
			const ok = verifyEd25519SignatureB64Url(data.ownerPubKey, canonical, data.sigOwner);
			if (!ok) {
				return res.status(401).json({ success: false, error: 'Invalid owner signature' });
			}

			const claimed = await redisClient.claimV2Nonce(ownerId, data.nonce, 10 * 60 * 1000);
			if (!claimed) {
				return res.status(409).json({ success: false, error: 'Nonce already used' });
			}

			const keys = await redisClient.listV2AccessKeys(ownerId, uid);
			const sanitised = (keys || []).map((k) => ({
				keyId: k.apiKeyId,
				name: k.name || '',
				permissions: Array.isArray(k.permissions) ? k.permissions : [],
				createdAt: k.createdAt || 0,
				lastUsed: k.lastUsed || 0,
				revoked: Boolean(k.revoked),
				paused: Boolean(k.paused),
				expiresAt: k.expiresAt || 0
			}));

			return res.json({
				success: true,
				data: { keys: sanitised, count: sanitised.length }
			});
		} catch (error) {
			logger.error(`Error listing v2 access keys for ${req.params.uid}:`, error);
			return res.status(500).json({ success: false, error: 'Failed to list API keys' });
		}
	}
);

// ── V2: Revoke access key ──────────────────────────────────────────────────────

router.post('/v2/switch/:uid/access-keys/revoke',
	validateUID,
	authManager.rateLimit('v2_access_keys_revoke', 200, 900000),
	validateRequest(schemas.v2RevokeAccessKey),
	async (req, res) => {
		try {
			const { uid } = req.params;
			const data = req.validatedData;

			if (!assertFreshTimestamp(data.ts)) {
				return res.status(400).json(clockSkewError());
			}

			const switchData = await redisClient.getSwitchState(uid);
			if (!switchData) {
				return res.status(404).json({ success: false, error: 'Switch not found' });
			}
			if (switchData.authVersion !== 2 || !switchData.ownerId || !switchData.ownerPubKey) {
				return res.status(400).json({ success: false, error: 'Switch is not crypto-authenticated' });
			}

			const ownerId = deriveOwnerIdFromOwnerPubKeyB64Url(data.ownerPubKey);
			if (await redisClient.isOwnerBlocked(ownerId)) {
				return res.status(403).json({ success: false, error: 'Owner blocked' });
			}
			if (switchData.ownerId !== ownerId || switchData.ownerPubKey !== data.ownerPubKey) {
				return res.status(401).json({ success: false, error: 'Invalid owner signature' });
			}

			const canonical = v2CanonicalRevokeAccessKey(uid, data);
			const ok = verifyEd25519SignatureB64Url(data.ownerPubKey, canonical, data.sigOwner);
			if (!ok) {
				return res.status(401).json({ success: false, error: 'Invalid owner signature' });
			}

			const claimed = await redisClient.claimV2Nonce(ownerId, data.nonce, 10 * 60 * 1000);
			if (!claimed) {
				return res.status(409).json({ success: false, error: 'Nonce already used' });
			}

			const keyRef = data.keyId || data.apiKey;
			const revoked = await redisClient.revokeV2AccessKey(ownerId, uid, keyRef);
			if (!revoked) {
				return res.status(404).json({ success: false, error: 'API key not found' });
			}

			return res.json({ success: true });
		} catch (error) {
			logger.error(`Error revoking v2 access key for ${req.params.uid}:`, error);
			return res.status(500).json({ success: false, error: 'Failed to revoke API key' });
		}
	}
);

// ── V2: Pause/unpause access key ───────────────────────────────────────────────

router.post('/v2/switch/:uid/access-keys/pause',
	validateUID,
	authManager.rateLimit('v2_access_keys_pause', 200, 900000),
	validateRequest(schemas.v2PauseAccessKey),
	async (req, res) => {
		try {
			const { uid } = req.params;
			const data = req.validatedData;

			if (!assertFreshTimestamp(data.ts)) {
				return res.status(400).json(clockSkewError());
			}

			const switchData = await redisClient.getSwitchState(uid);
			if (!switchData) {
				return res.status(404).json({ success: false, error: 'Switch not found' });
			}
			if (switchData.authVersion !== 2 || !switchData.ownerId || !switchData.ownerPubKey) {
				return res.status(400).json({ success: false, error: 'Switch is not crypto-authenticated' });
			}

			const ownerId = deriveOwnerIdFromOwnerPubKeyB64Url(data.ownerPubKey);
			if (await redisClient.isOwnerBlocked(ownerId)) {
				return res.status(403).json({ success: false, error: 'Owner blocked' });
			}
			if (switchData.ownerId !== ownerId || switchData.ownerPubKey !== data.ownerPubKey) {
				return res.status(401).json({ success: false, error: 'Invalid owner signature' });
			}

			const canonical = v2CanonicalPauseAccessKey(uid, data);
			const ok = verifyEd25519SignatureB64Url(data.ownerPubKey, canonical, data.sigOwner);
			if (!ok) {
				return res.status(401).json({ success: false, error: 'Invalid owner signature' });
			}

			const claimed = await redisClient.claimV2Nonce(ownerId, data.nonce, 10 * 60 * 1000);
			if (!claimed) {
				return res.status(409).json({ success: false, error: 'Nonce already used' });
			}

			const paused = await redisClient.pauseV2AccessKey(ownerId, uid, data.keyId, Boolean(data.paused));
			if (!paused) {
				return res.status(404).json({ success: false, error: 'API key not found or already revoked' });
			}

			return res.json({ success: true, data: { paused: Boolean(data.paused) } });
		} catch (error) {
			logger.error(`Error pausing access key for ${req.params.uid}:`, error);
			return res.status(500).json({ success: false, error: 'Failed to pause API key' });
		}
	}
);

// ── V2: Update access key permissions ──────────────────────────────────────────

router.post('/v2/switch/:uid/access-keys/permissions',
	validateUID,
	authManager.rateLimit('v2_access_keys_permissions', 200, 900000),
	validateRequest(schemas.v2UpdateAccessKeyPermissions),
	async (req, res) => {
		try {
			const { uid } = req.params;
			const data = req.validatedData;

			if (!assertFreshTimestamp(data.ts)) {
				return res.status(400).json(clockSkewError());
			}

			const switchData = await redisClient.getSwitchState(uid);
			if (!switchData) {
				return res.status(404).json({ success: false, error: 'Switch not found' });
			}
			if (switchData.authVersion !== 2 || !switchData.ownerId || !switchData.ownerPubKey) {
				return res.status(400).json({ success: false, error: 'Switch is not crypto-authenticated' });
			}

			const ownerId = deriveOwnerIdFromOwnerPubKeyB64Url(data.ownerPubKey);
			if (await redisClient.isOwnerBlocked(ownerId)) {
				return res.status(403).json({ success: false, error: 'Owner blocked' });
			}
			if (switchData.ownerId !== ownerId || switchData.ownerPubKey !== data.ownerPubKey) {
				return res.status(401).json({ success: false, error: 'Invalid owner signature' });
			}

			const canonical = v2CanonicalUpdateAccessKeyPermissions(uid, data);
			const ok = verifyEd25519SignatureB64Url(data.ownerPubKey, canonical, data.sigOwner);
			if (!ok) {
				return res.status(401).json({ success: false, error: 'Invalid owner signature' });
			}

			const claimed = await redisClient.claimV2Nonce(ownerId, data.nonce, 10 * 60 * 1000);
			if (!claimed) {
				return res.status(409).json({ success: false, error: 'Nonce already used' });
			}

			const updated = await redisClient.updateV2AccessKeyPermissions(ownerId, uid, data.keyId, data.permissions);
			if (!updated) {
				return res.status(404).json({ success: false, error: 'API key not found or already revoked' });
			}

			return res.json({ success: true, data: { permissions: data.permissions } });
		} catch (error) {
			logger.error(`Error updating access key permissions for ${req.params.uid}:`, error);
			return res.status(500).json({ success: false, error: 'Failed to update API key permissions' });
		}
	}
);

// ── V2: Toggle via delegated access key ────────────────────────────────────────

router.post('/v2/switch/:uid/toggle',
	validateUID,
	authManager.rateLimit('v2_toggle_access_key', 500, 900000, { perKey: true, keyLimit: 120 }),
	authManager.requireV2AccessKey('toggle'),
	async (req, res) => {
		try {
			const { uid } = req.params;
			const { switchData } = req;
			const actorLabel = abbreviateActor(req.apiKeyId);
			const timestamp = Date.now();

			const oldState = Boolean(switchData.state);
			const newState = !oldState;

			await redisClient.setSwitchState(uid, newState);

			let toggleCount = switchData.toggleCount || 0;
			if (newState !== oldState) {
				toggleCount = await redisClient.incrementToggleCount(uid);
			}

			await redisClient.recordUserInteraction(uid, req.apiKeyId);
			await redisClient.appendEvent(uid, {
				type: 'state',
				state: newState,
				actor: actorLabel,
				viaApiKey: true,
				timestamp
			});
			await redisClient.publishSwitchUpdate(uid, newState);

			return res.json({
				success: true,
				data: { uid, state: newState, timestamp, toggleCount }
			});
		} catch (error) {
			logger.error(`Error toggling v2 switch via access key ${req.params.uid}:`, error);
			return res.status(500).json({ success: false, error: 'Failed to toggle switch' });
		}
	}
);

// ── V2: Metadata via delegated access key ──────────────────────────────────────

router.post('/v2/switch/:uid/metadata',
	validateUID,
	authManager.rateLimit('v2_metadata_access_key', 200, 900000, { perKey: true }),
	authManager.requireV2AccessKey('metadata'),
	uploadV2MetadataFiles,
	validateRequest(schemas.v2UpdateSwitchViaAccessKey),
	async (req, res) => {
		try {
			const { uid } = req.params;
			const updates = pickSwitchMetadataUpdatesV2(req.validatedData || {});
			const iconFile = req.files && req.files.iconFile ? req.files.iconFile[0] : null;
			const bannerFile = req.files && req.files.bannerFile ? req.files.bannerFile[0] : null;

			const hasAnyUpdate = updates && Object.keys(updates).length > 0;
			const hasAnyFile = Boolean(iconFile || bannerFile);
			if (!hasAnyUpdate && !hasAnyFile) {
				return res.status(400).json({ success: false, error: 'No metadata updates provided' });
			}

			try {
				if (iconFile && iconFile.buffer) {
					updates.iconUrl = await media.ingestImageBuffer(uid, 'icon', iconFile.buffer);
				} else if (typeof updates.iconUrl === 'string' && updates.iconUrl.trim()) {
					updates.iconUrl = await media.ingestImageFromUrl(uid, 'icon', updates.iconUrl);
				}

				if (bannerFile && bannerFile.buffer) {
					updates.bannerUrl = await media.ingestImageBuffer(uid, 'banner', bannerFile.buffer);
				} else if (typeof updates.bannerUrl === 'string' && updates.bannerUrl.trim()) {
					updates.bannerUrl = await media.ingestImageFromUrl(uid, 'banner', updates.bannerUrl);
				}
			} catch (imgErr) {
				return res.status(400).json({
					success: false,
					error: imgErr && imgErr.message ? imgErr.message : 'Invalid image'
				});
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
			logger.error(`Error updating v2 switch metadata via access key for ${req.params.uid}:`, error);
			return res.status(500).json({ success: false, error: 'Failed to update switch' });
		}
	}
);

// ── V2: Comment via delegated access key ───────────────────────────────────────

router.post('/v2/switch/:uid/comment',
	validateUID,
	authManager.rateLimit('v2_comment_access_key', 200, 900000, { perKey: true }),
	authManager.requireV2AccessKey('comment'),
	validateRequest(schemas.addComment),
	async (req, res) => {
		try {
			const { uid } = req.params;
			const actor = abbreviateActor(req.apiKeyId);
			const timestamp = Date.now();

			const commentEvent = {
				uid,
				comment: req.validatedData.comment,
				actor,
				viaApiKey: true,
				timestamp
			};

			await redisClient.recordUserInteraction(uid, req.apiKeyId);
			await redisClient.addComment(uid, commentEvent);

			return res.json({
				success: true,
				data: commentEvent
			});
		} catch (error) {
			logger.error(`Error adding v2 comment via access key for ${req.params.uid}:`, error);
			return res.status(500).json({ success: false, error: 'Failed to add comment' });
		}
	}
);

// ── V2: Redeem promo code (owner-signed) ──────────────────────────────────────

router.post('/v2/owner/redeem-promo',
	authManager.rateLimit('v2_redeem_promo', 5, 3600000),
	validateRequest(schemas.v2RedeemPromo),
	async (req, res) => {
		try {
			const data = req.validatedData;

			if (!assertFreshTimestamp(data.ts)) {
				return res.status(400).json(clockSkewError());
			}

			const ownerId = deriveOwnerIdFromOwnerPubKeyB64Url(data.ownerPubKey);

			if (await redisClient.isOwnerBlocked(ownerId)) {
				return res.status(403).json({ success: false, error: 'Owner blocked' });
			}

			const canonical = v2CanonicalRedeemPromo(data);
			const ok = verifyEd25519SignatureB64Url(data.ownerPubKey, canonical, data.sigOwner);
			if (!ok) {
				return res.status(401).json({ success: false, error: 'Invalid owner signature' });
			}

			const claimed = await redisClient.claimV2Nonce(ownerId, data.nonce, 10 * 60 * 1000);
			if (!claimed) {
				return res.status(409).json({ success: false, error: 'Nonce already used' });
			}

			const result = await redisClient.redeemPromoCode(data.promoCode, ownerId);
			if (!result.success) {
				return res.status(400).json({ success: false, error: result.error });
			}

			logger.info(`Owner ${abbreviateActor(ownerId)} redeemed promo code for tier ${result.tier}`);
			return res.json({
				success: true,
				data: {
					tier: result.tier,
					expiresAt: result.expiresAt,
					durationDays: result.durationDays
				}
			});
		} catch (error) {
			logger.error('Error redeeming promo code:', error);
			return res.status(500).json({ success: false, error: 'Failed to redeem promo code' });
		}
	}
);

// ── V2: Get owner tier (owner-signed) ─────────────────────────────────────────

router.post('/v2/owner/tier',
	authManager.rateLimit('v2_get_owner_tier', 100, 900000),
	validateRequest(schemas.v2GetOwnerTier),
	async (req, res) => {
		try {
			const data = req.validatedData;

			if (!assertFreshTimestamp(data.ts)) {
				return res.status(400).json(clockSkewError());
			}

			const ownerId = deriveOwnerIdFromOwnerPubKeyB64Url(data.ownerPubKey);

			const canonical = v2CanonicalGetOwnerTier(data);
			const ok = verifyEd25519SignatureB64Url(data.ownerPubKey, canonical, data.sigOwner);
			if (!ok) {
				return res.status(401).json({ success: false, error: 'Invalid owner signature' });
			}

			const claimed = await redisClient.claimV2Nonce(ownerId, data.nonce, 10 * 60 * 1000);
			if (!claimed) {
				return res.status(409).json({ success: false, error: 'Nonce already used' });
			}

			const tierInfo = await redisClient.getOwnerTier(ownerId);
			return res.json({
				success: true,
				data: {
					tier: tierInfo.tier,
					expiresAt: tierInfo.expiresAt || null
				}
			});
		} catch (error) {
			logger.error('Error getting owner tier:', error);
			return res.status(500).json({ success: false, error: 'Failed to get owner tier' });
		}
	}
);

module.exports = router;

