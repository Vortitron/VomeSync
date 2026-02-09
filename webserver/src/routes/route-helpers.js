/**
 * Shared middleware, helpers, and constants used across route modules.
 */
const crypto = require('crypto');
const multer = require('multer');
const config = require('../config/config');
const redisClient = require('../utils/redis');
const { stableJsonStringify } = require('../utils/crypto_v2');

// ── Constants ──────────────────────────────────────────────────────────────────

const V2_ACCESS_KEY_MAX_TTL_SECONDS = 30 * 24 * 60 * 60;
const V2_MAX_CLOCK_SKEW_MS = 5 * 60 * 1000;

const legacyEnabled = Boolean(config?.security?.legacyApiEnabled);
const sessionTokensEnabled = Boolean(config?.security?.sessionTokensEnabled);
const sessionTokenApiKeyTtlSeconds =
	Number.parseInt(config?.security?.sessionTokenApiKeyTtlSeconds, 10) || 900;
const adminApiKey = String(config?.security?.adminApiKey || '');

// ── Multer upload configuration ────────────────────────────────────────────────

const upload = multer({
	storage: multer.memoryStorage(),
	limits: {
		fileSize:
			Number.parseInt(process.env.MEDIA_MAX_UPLOAD_BYTES || '', 10) || 10_000_000
	}
});

const uploadV2MetadataFiles = upload.fields([
	{ name: 'iconFile', maxCount: 1 },
	{ name: 'bannerFile', maxCount: 1 }
]);

// ── Middleware ──────────────────────────────────────────────────────────────────

const requireLegacyEnabled = (_req, res, next) => {
	if (!legacyEnabled) {
		return res.status(410).json({ success: false, error: 'Legacy API disabled' });
	}
	return next();
};

const requireSessionTokensEnabled = (_req, res, next) => {
	if (!sessionTokensEnabled) {
		return res.status(410).json({ success: false, error: 'Session token API disabled' });
	}
	return next();
};

const extractBearerToken = (req) => {
	const authHeader = req.headers.authorization || '';
	if (typeof authHeader !== 'string') return '';
	const parts = authHeader.split(' ');
	if (parts.length === 2 && /^bearer$/i.test(parts[0])) {
		return parts[1];
	}
	return '';
};

const safeEquals = (left, right) => {
	if (!left || !right || typeof left !== 'string' || typeof right !== 'string') {
		return false;
	}
	const leftBuf = Buffer.from(left);
	const rightBuf = Buffer.from(right);
	if (leftBuf.length !== rightBuf.length) {
		return false;
	}
	return crypto.timingSafeEqual(leftBuf, rightBuf);
};

/**
 * Admin authentication middleware.
 *
 * Supports two modes (tried in order):
 *   1. HMAC challenge-response (preferred):
 *      – Client GETs /admin/challenge → receives { challenge }
 *      – Client computes HMAC-SHA256(adminApiKey, challenge)
 *      – Client sends X-Admin-Challenge + X-Admin-Signature headers
 *   2. Static key (legacy / simple scripts):
 *      – Client sends X-Admin-Key header or Bearer token
 */
const ADMIN_CHALLENGE_TTL_SECONDS = 30;
const ADMIN_CHALLENGE_PREFIX = 'admin_challenge:';

const requireAdmin = async (req, res, next) => {
	if (!adminApiKey) {
		return res.status(503).json({ success: false, error: 'Admin API not configured' });
	}

	// Mode 1: HMAC challenge-response
	const challenge = req.headers['x-admin-challenge'] || '';
	const signature = req.headers['x-admin-signature'] || '';
	if (challenge && signature) {
		try {
			const redisKey = `${ADMIN_CHALLENGE_PREFIX}${challenge}`;
			const stored = await redisClient.client.get(redisKey);
			if (!stored) {
				return res.status(401).json({ success: false, error: 'Invalid or expired challenge' });
			}
			// Consume the challenge (one-time use)
			await redisClient.client.del(redisKey);

			const expectedSig = crypto
				.createHmac('sha256', adminApiKey)
				.update(challenge)
				.digest('hex');

			if (!safeEquals(String(signature), expectedSig)) {
				return res.status(403).json({ success: false, error: 'Invalid admin signature' });
			}
			req.adminAuthMethod = 'hmac';
			return next();
		} catch (err) {
			return res.status(500).json({ success: false, error: 'HMAC verification failed' });
		}
	}

	// Mode 2: Static key (backwards compatible)
	const provided = req.headers['x-admin-key'] || extractBearerToken(req) || '';
	if (!provided) {
		return res.status(401).json({ success: false, error: 'Admin key required' });
	}
	if (!safeEquals(String(provided), adminApiKey)) {
		return res.status(403).json({ success: false, error: 'Invalid admin key' });
	}
	req.adminAuthMethod = 'static';
	return next();
};

// ── Helpers ────────────────────────────────────────────────────────────────────

const sendTierLimitError = (res, limit, max, tier = 'free') => {
	return res.status(403).json({
		success: false,
		error: `${tier === 'free' ? 'Free tier' : 'Account'} limit reached (${max} ${limit})`,
		code: 'tier_limit',
		details: { limit, max, tier }
	});
};

// Keep legacy alias
const sendFreeTierLimitError = sendTierLimitError;

/**
 * Check whether the owner/user has hit their tier limits.
 * Returns null if within limits, or { limit, max, tier } if exceeded.
 */
const checkFreeTierLimits = async ({ ownerId, personalKeyId, wantsPublicize, currentPublicize }) => {
	const limits = config?.limits || {};
	const freeTierEnabled = limits.freeTierEnabled !== false;

	if (!freeTierEnabled) {
		return null;
	}

	// Determine the owner's tier
	let tier = { tier: 'free' };
	if (ownerId) {
		tier = await redisClient.getOwnerTier(ownerId);
	}
	const isPremium = tier.tier === 'premium';

	const maxSwitches = isPremium
		? (Number.isFinite(Number(limits.premiumMaxSwitches)) ? Number(limits.premiumMaxSwitches) : 50)
		: (Number.isFinite(Number(limits.freeTierMaxSwitches)) ? Number(limits.freeTierMaxSwitches) : 8);
	const maxPublicSwitches = isPremium
		? (Number.isFinite(Number(limits.premiumMaxPublicSwitches)) ? Number(limits.premiumMaxPublicSwitches) : 25)
		: (Number.isFinite(Number(limits.freeTierMaxPublicSwitches)) ? Number(limits.freeTierMaxPublicSwitches) : 4);

	let counts = null;
	if (ownerId) {
		counts = await redisClient.getOwnerSwitchCounts(ownerId);
	} else if (personalKeyId) {
		counts = await redisClient.getUserSwitchCounts(personalKeyId);
	}
	if (!counts) {
		return null;
	}
	if (maxSwitches >= 0 && counts.total >= maxSwitches) {
		return { limit: 'switches', max: maxSwitches, tier: tier.tier };
	}
	if (
		wantsPublicize &&
		!currentPublicize &&
		maxPublicSwitches >= 0 &&
		counts.public >= maxPublicSwitches
	) {
		return { limit: 'public switches', max: maxPublicSwitches, tier: tier.tier };
	}
	return null;
};

const abbreviateActor = (actorId) => {
	if (!actorId) {
		return 'unknown';
	}
	return `${String(actorId).substring(0, 8)}...`;
};

const assertFreshTimestamp = (ts) => {
	if (typeof ts !== 'number' || !Number.isFinite(ts) || ts <= 0) {
		return false;
	}
	return Math.abs(Date.now() - ts) <= V2_MAX_CLOCK_SKEW_MS;
};

/**
 * Build a clock-skew error response body.
 * Includes `serverTime` so the client can self-correct.
 */
const clockSkewError = () => ({
	success: false,
	error: 'Invalid or stale timestamp',
	serverTime: Date.now(),
	toleranceMs: V2_MAX_CLOCK_SKEW_MS
});

// ── V2 metadata helpers ────────────────────────────────────────────────────────

const pickSwitchMetadata = (data) => ({
	...(typeof data.name === 'string' ? { name: data.name } : {}),
	description: data.description || '',
	location: data.location || '',
	category: data.category || 'Other',
	publicize: Boolean(data.publicize),
	link: data.link || '',
	...(typeof data.iconUrl === 'string' ? { iconUrl: data.iconUrl } : {}),
	...(typeof data.bannerUrl === 'string' ? { bannerUrl: data.bannerUrl } : {})
});

const pickSwitchMetadataUpdatesV2 = (data) => {
	const updates = {};
	const maybeSet = (field) => {
		if (Object.prototype.hasOwnProperty.call(data, field)) {
			updates[field] = data[field];
		}
	};

	maybeSet('name');
	maybeSet('description');
	maybeSet('location');
	maybeSet('category');
	maybeSet('publicize');
	maybeSet('link');
	maybeSet('iconUrl');
	maybeSet('bannerUrl');

	return updates;
};

// ── V2 canonical JSON builders ─────────────────────────────────────────────────

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

const v2CanonicalPauseAccessKey = (uid, data) => stableJsonStringify({
	v: 2,
	action: 'pause_access_key',
	uid,
	ownerPubKey: data.ownerPubKey,
	ts: data.ts,
	nonce: data.nonce,
	keyId: data.keyId,
	paused: data.paused
});

const v2CanonicalUpdateAccessKeyPermissions = (uid, data) => stableJsonStringify({
	v: 2,
	action: 'update_access_key_permissions',
	uid,
	ownerPubKey: data.ownerPubKey,
	ts: data.ts,
	nonce: data.nonce,
	keyId: data.keyId,
	permissions: data.permissions
});

const v2CanonicalRedeemPromo = (data) => stableJsonStringify({
	v: 2,
	action: 'redeem_promo',
	ownerPubKey: data.ownerPubKey,
	ts: data.ts,
	nonce: data.nonce,
	promoCode: data.promoCode
});

const v2CanonicalGetOwnerTier = (data) => stableJsonStringify({
	v: 2,
	action: 'get_owner_tier',
	ownerPubKey: data.ownerPubKey,
	ts: data.ts,
	nonce: data.nonce
});

module.exports = {
	// Constants
	V2_ACCESS_KEY_MAX_TTL_SECONDS,
	V2_MAX_CLOCK_SKEW_MS,
	sessionTokenApiKeyTtlSeconds,

	// Middleware
	requireLegacyEnabled,
	requireSessionTokensEnabled,
	requireAdmin,
	uploadV2MetadataFiles,
	ADMIN_CHALLENGE_TTL_SECONDS,
	ADMIN_CHALLENGE_PREFIX,

	// Helpers
	sendFreeTierLimitError,
	checkFreeTierLimits,
	abbreviateActor,
	assertFreshTimestamp,
	pickSwitchMetadata,
	pickSwitchMetadataUpdatesV2,

	clockSkewError,

	// V2 canonical builders
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
};

