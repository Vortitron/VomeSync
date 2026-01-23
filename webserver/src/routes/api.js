const express = require('express');
const { v4: uuidv4 } = require('uuid');
const multer = require('multer');
const redisClient = require('../utils/redis');
const authManager = require('../utils/auth');
const logger = require('../utils/logger');
const media = require('../utils/media');
const {
	validateRequest,
	validateUID,
	schemas,
	sanitizePublicSwitchData,
	sanitizePrivateSwitchData
} = require('../utils/validation');
const webSocketManager = require('../websocket/manager');

const router = express.Router();

const upload = multer({
	storage: multer.memoryStorage(),
	limits: {
		// Allow enough for a banner upload; additional checks exist in media util.
		fileSize: Number.parseInt(process.env.MEDIA_MAX_UPLOAD_BYTES || '', 10) || 10_000_000
	}
});

const uploadV2MetadataFiles = upload.fields([
	{ name: 'iconFile', maxCount: 1 },
	{ name: 'bannerFile', maxCount: 1 }
]);

const abbreviateActor = (actorId) => {
	if (!actorId) {
		return 'unknown';
	}
	return `${String(actorId).substring(0, 8)}...`;
};

// V2 crypto-auth helpers
const {
	stableJsonStringify,
	deriveOwnerIdFromOwnerPubKeyB64Url,
	deriveSwitchUidFromSwitchPubKeyB64Url,
	verifyEd25519SignatureB64Url
} = require('../utils/crypto_v2');

const V2_MAX_CLOCK_SKEW_MS = 5 * 60 * 1000;

const assertFreshTimestamp = (ts) => {
	if (typeof ts !== 'number' || !Number.isFinite(ts) || ts <= 0) {
		return false;
	}
	return Math.abs(Date.now() - ts) <= V2_MAX_CLOCK_SKEW_MS;
};

const pickSwitchMetadata = (data) => ({
	description: data.description || '',
	location: data.location || '',
	category: data.category || 'Other',
	publicize: Boolean(data.publicize),
	link: data.link || '',
	// IMPORTANT: only include new fields when present, otherwise older v2 clients will fail signature checks
	...(typeof data.iconUrl === 'string' ? { iconUrl: data.iconUrl } : {}),
	...(typeof data.bannerUrl === 'string' ? { bannerUrl: data.bannerUrl } : {})
});

const v2CanonicalCreate = (data, uid) => stableJsonStringify({
	v: 2,
	action: 'create_switch',
	ownerPubKey: data.ownerPubKey,
	switchPubKey: data.switchPubKey,
	uid,
	index: data.index,
	ts: data.ts,
	nonce: data.nonce,
	payload: pickSwitchMetadata(data)
});

const v2CanonicalMySwitches = (data) => stableJsonStringify({
	v: 2,
	action: 'my_switches',
	ownerPubKey: data.ownerPubKey,
	ts: data.ts,
	nonce: data.nonce
});

const v2CanonicalSetState = (uid, data) => stableJsonStringify({
	v: 2,
	action: 'set_state',
	uid,
	ts: data.ts,
	nonce: data.nonce,
	state: Boolean(data.state),
	params: data.params || {}
});

const pickSwitchMetadataUpdatesV2 = (data) => {
	const updates = {};
	const maybeSet = (field) => {
		if (Object.prototype.hasOwnProperty.call(data, field)) {
			updates[field] = data[field];
		}
	};

	maybeSet('description');
	maybeSet('location');
	maybeSet('category');
	maybeSet('publicize');
	maybeSet('link');
	maybeSet('iconUrl');
	maybeSet('bannerUrl');

	return updates;
};

const v2CanonicalUpdateSwitch = (uid, data, updates) => stableJsonStringify({
	v: 2,
	action: 'update_switch',
	uid,
	ownerPubKey: data.ownerPubKey,
	ts: data.ts,
	nonce: data.nonce,
	payload: updates || {}
});

const v2CanonicalCreateAccessKey = (uid, data, payload) => stableJsonStringify({
	v: 2,
	action: 'create_access_key',
	uid,
	ownerPubKey: data.ownerPubKey,
	ts: data.ts,
	nonce: data.nonce,
	payload: payload || {}
});

const v2CanonicalListAccessKeys = (uid, data) => stableJsonStringify({
	v: 2,
	action: 'list_access_keys',
	uid,
	ownerPubKey: data.ownerPubKey,
	ts: data.ts,
	nonce: data.nonce
});

const v2CanonicalRevokeAccessKey = (uid, data) => stableJsonStringify({
	v: 2,
	action: 'revoke_access_key',
	uid,
	ownerPubKey: data.ownerPubKey,
	ts: data.ts,
	nonce: data.nonce,
	...(data.keyId ? { keyId: data.keyId } : { apiKey: data.apiKey })
});

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
			res.status(500).json({
				success: false,
				error: 'Failed to generate personal key'
			});
		}
	}
);

// V2: Create switch (deterministic UID derived from switch pubkey, signed by owner + switch)
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
				return res.status(400).json({ success: false, error: 'Invalid or stale timestamp' });
			}

			const ownerId = deriveOwnerIdFromOwnerPubKeyB64Url(data.ownerPubKey);
			const uid = deriveSwitchUidFromSwitchPubKeyB64Url(data.switchPubKey);

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

// V2: List switches for owner (signed by owner key)
router.post('/v2/my-switches',
	authManager.rateLimit('v2_my_switches', 200, 900000),
	validateRequest(schemas.v2MySwitches),
	async (req, res) => {
		try {
			const data = req.validatedData;
			if (!assertFreshTimestamp(data.ts)) {
				return res.status(400).json({ success: false, error: 'Invalid or stale timestamp' });
			}

			const ownerId = deriveOwnerIdFromOwnerPubKeyB64Url(data.ownerPubKey);
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
			const sanitized = switches.map((s) => sanitizePrivateSwitchData(s));

			return res.json({
				success: true,
				data: {
					switches: sanitized,
					count: sanitized.length
				}
			});
		} catch (error) {
			logger.error('Error getting v2 my-switches:', error);
			return res.status(500).json({ success: false, error: 'Failed to get switches' });
		}
	}
);

// V2: Set switch state (signed by switch key; supports params passthrough)
router.post('/v2/switch/:uid/state',
	validateUID,
	authManager.rateLimit('v2_set_state', 500, 900000),
	validateRequest(schemas.v2SetState),
	async (req, res) => {
		try {
			const { uid } = req.params;
			const data = req.validatedData;

			if (!assertFreshTimestamp(data.ts)) {
				return res.status(400).json({ success: false, error: 'Invalid or stale timestamp' });
			}

			const switchData = await redisClient.getSwitchState(uid);
			if (!switchData) {
				return res.status(404).json({ success: false, error: 'Switch not found' });
			}
			if (switchData.authVersion !== 2 || !switchData.switchPubKey) {
				return res.status(400).json({ success: false, error: 'Switch is not v2 (crypto) enabled' });
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

// V2: Update switch metadata (signed by owner key)
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
				return res.status(400).json({ success: false, error: 'Invalid or stale timestamp' });
			}

			const ownerId = deriveOwnerIdFromOwnerPubKeyB64Url(data.ownerPubKey);

			const switchData = await redisClient.getSwitchState(uid);
			if (!switchData) {
				return res.status(404).json({ success: false, error: 'Switch not found' });
			}
			if (switchData.authVersion !== 2 || !switchData.ownerId || !switchData.ownerPubKey) {
				return res.status(400).json({ success: false, error: 'Switch is not v2 (crypto) enabled' });
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

			// Ingest icon/banner URLs if provided (store only local URLs).
			try {
				if (typeof updates.iconUrl === 'string' && updates.iconUrl.trim()) {
					updates.iconUrl = await media.ingestImageFromUrl(uid, 'icon', updates.iconUrl);
				}
				if (typeof updates.bannerUrl === 'string' && updates.bannerUrl.trim()) {
					updates.bannerUrl = await media.ingestImageFromUrl(uid, 'banner', updates.bannerUrl);
				}
			} catch (imgErr) {
				return res.status(400).json({ success: false, error: imgErr && imgErr.message ? imgErr.message : 'Invalid image URL' });
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

// V2: Create an access key (delegation) for a switch (signed by owner key)
router.post('/v2/switch/:uid/access-keys',
	validateUID,
	authManager.rateLimit('v2_access_keys_create', 200, 900000),
	validateRequest(schemas.v2CreateAccessKey),
	async (req, res) => {
		try {
			const { uid } = req.params;
			const data = req.validatedData;

			if (!assertFreshTimestamp(data.ts)) {
				return res.status(400).json({ success: false, error: 'Invalid or stale timestamp' });
			}

			const switchData = await redisClient.getSwitchState(uid);
			if (!switchData) {
				return res.status(404).json({ success: false, error: 'Switch not found' });
			}
			if (switchData.authVersion !== 2 || !switchData.ownerId || !switchData.ownerPubKey) {
				return res.status(400).json({ success: false, error: 'Switch is not v2 (crypto) enabled' });
			}

			const ownerId = deriveOwnerIdFromOwnerPubKeyB64Url(data.ownerPubKey);
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
			const canonical = v2CanonicalCreateAccessKey(uid, data, payload);
			const ok = verifyEd25519SignatureB64Url(data.ownerPubKey, canonical, data.sigOwner);
			if (!ok) {
				return res.status(401).json({ success: false, error: 'Invalid owner signature' });
			}

			const claimed = await redisClient.claimV2Nonce(ownerId, data.nonce, 10 * 60 * 1000);
			if (!claimed) {
				return res.status(409).json({ success: false, error: 'Nonce already used' });
			}

			const created = await redisClient.createV2AccessKey(ownerId, uid, data.name || '', data.permissions);
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
					createdAt: created.createdAt
				}
			});
		} catch (error) {
			logger.error(`Error creating v2 access key for ${req.params.uid}:`, error);
			return res.status(500).json({ success: false, error: 'Failed to create API key' });
		}
	}
);

// V2: List access keys for a switch (signed by owner key)
router.post('/v2/switch/:uid/access-keys/list',
	validateUID,
	authManager.rateLimit('v2_access_keys_list', 200, 900000),
	validateRequest(schemas.v2ListAccessKeys),
	async (req, res) => {
		try {
			const { uid } = req.params;
			const data = req.validatedData;

			if (!assertFreshTimestamp(data.ts)) {
				return res.status(400).json({ success: false, error: 'Invalid or stale timestamp' });
			}

			const switchData = await redisClient.getSwitchState(uid);
			if (!switchData) {
				return res.status(404).json({ success: false, error: 'Switch not found' });
			}
			if (switchData.authVersion !== 2 || !switchData.ownerId || !switchData.ownerPubKey) {
				return res.status(400).json({ success: false, error: 'Switch is not v2 (crypto) enabled' });
			}

			const ownerId = deriveOwnerIdFromOwnerPubKeyB64Url(data.ownerPubKey);
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
			const sanitized = (keys || []).map((k) => ({
				keyId: k.apiKeyId,
				name: k.name || '',
				permissions: Array.isArray(k.permissions) ? k.permissions : [],
				createdAt: k.createdAt || 0,
				lastUsed: k.lastUsed || 0,
				revoked: Boolean(k.revoked)
			}));

			return res.json({
				success: true,
				data: {
					keys: sanitized,
					count: sanitized.length
				}
			});
		} catch (error) {
			logger.error(`Error listing v2 access keys for ${req.params.uid}:`, error);
			return res.status(500).json({ success: false, error: 'Failed to list API keys' });
		}
	}
);

// V2: Revoke an access key (signed by owner key)
router.post('/v2/switch/:uid/access-keys/revoke',
	validateUID,
	authManager.rateLimit('v2_access_keys_revoke', 200, 900000),
	validateRequest(schemas.v2RevokeAccessKey),
	async (req, res) => {
		try {
			const { uid } = req.params;
			const data = req.validatedData;

			if (!assertFreshTimestamp(data.ts)) {
				return res.status(400).json({ success: false, error: 'Invalid or stale timestamp' });
			}

			const switchData = await redisClient.getSwitchState(uid);
			if (!switchData) {
				return res.status(404).json({ success: false, error: 'Switch not found' });
			}
			if (switchData.authVersion !== 2 || !switchData.ownerId || !switchData.ownerPubKey) {
				return res.status(400).json({ success: false, error: 'Switch is not v2 (crypto) enabled' });
			}

			const ownerId = deriveOwnerIdFromOwnerPubKeyB64Url(data.ownerPubKey);
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

// V2: Toggle switch using a delegated access key (no signatures required)
router.post('/v2/switch/:uid/toggle',
	validateUID,
	authManager.rateLimit('v2_toggle_access_key', 500, 900000),
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
				data: {
					uid,
					state: newState,
					timestamp,
					toggleCount
				}
			});
		} catch (error) {
			logger.error(`Error toggling v2 switch via access key ${req.params.uid}:`, error);
			return res.status(500).json({ success: false, error: 'Failed to toggle switch' });
		}
	}
);

// V2: Update switch metadata using a delegated access key (no signatures required)
// Note: This is intentionally limited (no "publicize") to avoid bypassing CAPTCHA requirements.
router.post('/v2/switch/:uid/metadata',
	validateUID,
	authManager.rateLimit('v2_metadata_access_key', 200, 900000),
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

			// Ingest uploaded images (preferred over URLs)
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
				return res.status(400).json({ success: false, error: imgErr && imgErr.message ? imgErr.message : 'Invalid image' });
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

// V2: Comment using a delegated access key (no signatures required)
router.post('/v2/switch/:uid/comment',
	validateUID,
	authManager.rateLimit('v2_comment_access_key', 200, 900000),
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

// Create switch endpoint
router.post('/create-switch',
	authManager.rateLimit('create_switch', 20, 3600000), // 20 per hour
	authManager.requireAuth(),
	validateRequest(schemas.createSwitch),
	async (req, res) => {
		try {
			const uid = uuidv4();
			const { personalKeyId } = req;
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
			const switchData = await redisClient.createSwitch(uid, personalKeyId, switchConfig);

			logger.info(`Created new switch: ${uid}`);

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
			const actorLabel = abbreviateActor(req.apiKeyId || req.personalKeyId);
			const timestamp = Date.now();

			// Toggle state
			const newState = !switchData.state;

			// Update in Redis
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
			const { personalKeyId } = req;

			const userSwitches = await redisClient.getUserSwitches(personalKeyId);

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
			const keys = await redisClient.listApiKeys(req.personalKeyId);
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
			const keyData = await redisClient.createApiKey(req.personalKeyId, name);
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
			await redisClient.setProfileUrl(req.personalKeyId, profileUrl);
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
			const revoked = await redisClient.revokeApiKey(req.personalKeyId, apiKey);
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
			const tokenData = await redisClient.createSessionToken(req.personalKeyId, 300);
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
			const apiKeyData = await redisClient.createApiKey(tokenData.personalKeyId, 'web-session');

			res.json({
				success: true,
				data: {
					apiKey: apiKeyData.apiKey,
					apiKeyId: apiKeyData.apiKeyId,
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
			const { personalKeyId } = req;

			// Remove from Redis
			await redisClient.client.del(`switch:${uid}`);
			await redisClient.client.del(`switch:${uid}:users`);
			await redisClient.client.del(`switch:${uid}:events`);
			await redisClient.client.sRem(`user:${personalKeyId}:switches`, uid);
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

			logger.info(`Deleted personal key and ${deletedSwitchCount} switches`);

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
