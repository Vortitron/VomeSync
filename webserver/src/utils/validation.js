const Joi = require('joi');

const MAX_DESCRIPTION_LENGTH = 500;
const MAX_SWITCH_NAME_LENGTH = 80;
const MAX_LOCATION_LENGTH = 100;
const MAX_URL_LENGTH = 500;
const MAX_CAPTCHA_TOKEN_LENGTH = 2000;
const MAX_ACCESS_KEY_NAME_LENGTH = 100;
const MAX_REDIRECT_REASON_LENGTH = 500;
const V2_ACCESS_KEY_PERMISSIONS = ['toggle', 'comment', 'metadata'];

const UUID_REGEX = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const V2_UID_REGEX = /^vs_[0-9a-hjkmnpqrstvwxyz]{26}$/i;

const isValidSwitchUid = (uid) => {
	if (typeof uid !== 'string' || uid.length === 0) {
		return false;
	}
	return UUID_REGEX.test(uid) || V2_UID_REGEX.test(uid);
};

const switchUidSchema = Joi.string().required().custom((value, helpers) => {
	if (!isValidSwitchUid(value)) {
		return helpers.error('any.invalid');
	}
	return value;
}, 'Switch UID validation');

const schemas = {
	createSwitch: Joi.object({
		name: Joi.string().max(MAX_SWITCH_NAME_LENGTH).allow('').default(''),
		description: Joi.string().max(MAX_DESCRIPTION_LENGTH).allow('').default(''),
		location: Joi.string().max(MAX_LOCATION_LENGTH).allow('').default(''),
		category: Joi.string().valid('Community', 'Personal', 'Event', 'Test', 'Other').default('Other'),
		publicize: Joi.boolean().default(false),
		link: Joi.string().uri({ scheme: ['http', 'https'] }).max(MAX_URL_LENGTH).allow('').default(''),
		captchaToken: Joi.string().max(MAX_CAPTCHA_TOKEN_LENGTH).allow('')
	}),

	updateSwitch: Joi.object({
		name: Joi.string().max(MAX_SWITCH_NAME_LENGTH).allow(''),
		description: Joi.string().max(MAX_DESCRIPTION_LENGTH).allow(''),
		location: Joi.string().max(MAX_LOCATION_LENGTH).allow(''),
		category: Joi.string().valid('Community', 'Personal', 'Event', 'Test', 'Other'),
		publicize: Joi.boolean(),
		link: Joi.string().uri({ scheme: ['http', 'https'] }).max(MAX_URL_LENGTH).allow(''),
		captchaToken: Joi.string().max(MAX_CAPTCHA_TOKEN_LENGTH).allow('')
	}).min(1),

	toggleSwitch: Joi.object({
		personalKey: Joi.string().uuid().required()
	}),

	addComment: Joi.object({
		comment: Joi.string().min(1).max(500).required()
	}),

	updateProfile: Joi.object({
		profileUrl: Joi.string().uri({ scheme: ['http', 'https'] }).max(MAX_URL_LENGTH).allow('').default('')
	}),

	subscribeSwitch: Joi.object({
		uid: switchUidSchema
	}),

	generateKey: Joi.object({
		consent: Joi.boolean().valid(true).required()
	}),

	deleteKey: Joi.object({
		personalKey: Joi.string().uuid().required(),
		confirmation: Joi.string().valid('DELETE_ALL_DATA').required()
	}),

	// V2 signed endpoints (crypto identity)
	v2CreateSwitch: Joi.object({
		ownerPubKey: Joi.string().max(120).required(),
		switchPubKey: Joi.string().max(120).required(),
		index: Joi.number().integer().min(0).max(1000000).required(),
		ts: Joi.number().integer().min(0).required(),
		nonce: Joi.string().min(8).max(128).required(),
		sigOwner: Joi.string().max(200).required(),
		sigSwitch: Joi.string().max(200).required(),
		name: Joi.string().max(MAX_SWITCH_NAME_LENGTH).allow(''),
		description: Joi.string().max(MAX_DESCRIPTION_LENGTH).allow('').default(''),
		location: Joi.string().max(MAX_LOCATION_LENGTH).allow('').default(''),
		category: Joi.string().valid('Community', 'Personal', 'Event', 'Test', 'Other').default('Other'),
		publicize: Joi.boolean().default(false),
		link: Joi.string().uri({ scheme: ['http', 'https'] }).max(MAX_URL_LENGTH).allow('').default(''),
		// IMPORTANT: do NOT default these, otherwise older v2 clients will fail signature checks
		// because the server would introduce new fields into the signed canonical payload.
		iconUrl: Joi.string().uri({ scheme: ['http', 'https'] }).max(MAX_URL_LENGTH).min(1),
		bannerUrl: Joi.string().uri({ scheme: ['http', 'https'] }).max(MAX_URL_LENGTH).min(1),
		captchaToken: Joi.string().max(MAX_CAPTCHA_TOKEN_LENGTH).allow('')
	}),

	v2MySwitches: Joi.object({
		ownerPubKey: Joi.string().max(120).required(),
		ts: Joi.number().integer().min(0).required(),
		nonce: Joi.string().min(8).max(128).required(),
		sigOwner: Joi.string().max(200).required()
	}),

	v2UpdateSwitch: Joi.object({
		ownerPubKey: Joi.string().max(120).required(),
		ts: Joi.number().integer().min(0).required(),
		nonce: Joi.string().min(8).max(128).required(),
		sigOwner: Joi.string().max(200).required(),
		name: Joi.string().max(MAX_SWITCH_NAME_LENGTH).allow(''),
		description: Joi.string().max(MAX_DESCRIPTION_LENGTH).allow(''),
		location: Joi.string().max(MAX_LOCATION_LENGTH).allow(''),
		category: Joi.string().valid('Community', 'Personal', 'Event', 'Test', 'Other'),
		publicize: Joi.boolean(),
		link: Joi.string().uri({ scheme: ['http', 'https'] }).max(MAX_URL_LENGTH).allow(''),
		iconUrl: Joi.string().uri({ scheme: ['http', 'https'] }).max(MAX_URL_LENGTH).allow(''),
		bannerUrl: Joi.string().uri({ scheme: ['http', 'https'] }).max(MAX_URL_LENGTH).allow(''),
		captchaToken: Joi.string().max(MAX_CAPTCHA_TOKEN_LENGTH).allow('')
	}).custom((value, helpers) => {
		const updatableFields = ['name', 'description', 'location', 'category', 'publicize', 'link', 'iconUrl', 'bannerUrl'];
		const hasUpdate = updatableFields.some((field) => Object.prototype.hasOwnProperty.call(value, field));
		if (!hasUpdate) {
			return helpers.message('At least one metadata field is required');
		}
		return value;
	}, 'V2 update switch validation'),

	v2CreateAccessKey: Joi.object({
		ownerPubKey: Joi.string().max(120).required(),
		ts: Joi.number().integer().min(0).required(),
		nonce: Joi.string().min(8).max(128).required(),
		sigOwner: Joi.string().max(200).required(),
		name: Joi.string().max(MAX_ACCESS_KEY_NAME_LENGTH).allow(''),
		ttlSeconds: Joi.number().integer().min(60).max(2592000),
		permissions: Joi.array()
			.items(Joi.string().valid(...V2_ACCESS_KEY_PERMISSIONS))
			.min(1)
			.max(V2_ACCESS_KEY_PERMISSIONS.length)
	}),

	v2ListAccessKeys: Joi.object({
		ownerPubKey: Joi.string().max(120).required(),
		ts: Joi.number().integer().min(0).required(),
		nonce: Joi.string().min(8).max(128).required(),
		sigOwner: Joi.string().max(200).required()
	}),

	v2RevokeAccessKey: Joi.object({
		ownerPubKey: Joi.string().max(120).required(),
		ts: Joi.number().integer().min(0).required(),
		nonce: Joi.string().min(8).max(128).required(),
		sigOwner: Joi.string().max(200).required(),
		// Prefer keyId (hashed ID) to avoid ever needing to send/store plaintext keys for management.
		keyId: Joi.string().hex().length(64),
		// Backwards-compatible (older clients revoke by plaintext apiKey).
		apiKey: Joi.string().uuid()
	}).custom((value, helpers) => {
		if (!value || (!value.apiKey && !value.keyId)) {
			return helpers.message('apiKey or keyId is required');
		}
		return value;
	}, 'V2 revoke access key validation'),

	v2PauseAccessKey: Joi.object({
		ownerPubKey: Joi.string().max(120).required(),
		ts: Joi.number().integer().min(0).required(),
		nonce: Joi.string().min(8).max(128).required(),
		sigOwner: Joi.string().max(200).required(),
		keyId: Joi.string().hex().length(64).required(),
		paused: Joi.boolean().required()
	}),

	v2UpdateAccessKeyPermissions: Joi.object({
		ownerPubKey: Joi.string().max(120).required(),
		ts: Joi.number().integer().min(0).required(),
		nonce: Joi.string().min(8).max(128).required(),
		sigOwner: Joi.string().max(200).required(),
		keyId: Joi.string().hex().length(64).required(),
		permissions: Joi.array()
			.items(Joi.string().valid(...V2_ACCESS_KEY_PERMISSIONS))
			.min(1)
			.max(V2_ACCESS_KEY_PERMISSIONS.length)
			.required()
	}),

	// V2: Update switch metadata via delegated access key (no signatures)
	// Intentionally excludes "publicize" to avoid bypassing CAPTCHA requirements.
	v2UpdateSwitchViaAccessKey: Joi.object({
		name: Joi.string().max(MAX_SWITCH_NAME_LENGTH).allow(''),
		description: Joi.string().max(MAX_DESCRIPTION_LENGTH).allow(''),
		location: Joi.string().max(MAX_LOCATION_LENGTH).allow(''),
		category: Joi.string().valid('Community', 'Personal', 'Event', 'Test', 'Other'),
		link: Joi.string().uri({ scheme: ['http', 'https'] }).max(MAX_URL_LENGTH).allow(''),
		iconUrl: Joi.string().uri({ scheme: ['http', 'https'] }).max(MAX_URL_LENGTH).allow(''),
		bannerUrl: Joi.string().uri({ scheme: ['http', 'https'] }).max(MAX_URL_LENGTH).allow('')
	}),

	v2SetState: Joi.object({
		ts: Joi.number().integer().min(0).required(),
		nonce: Joi.string().min(8).max(128).required(),
		sigSwitch: Joi.string().max(200).required(),
		state: Joi.boolean().required(),
		params: Joi.object().unknown(true).default({})
	}),

	adminBlock: Joi.object({
		action: Joi.string().valid('block', 'unblock').default('block'),
		type: Joi.string().valid('owner', 'personal', 'api', 'uid').required(),
		value: Joi.string().min(3).max(200).required()
	}),

	adminRedirect: Joi.object({
		fromUid: switchUidSchema,
		toUid: switchUidSchema,
		reason: Joi.string().max(MAX_REDIRECT_REASON_LENGTH).allow('').default('')
	}),

	adminListingOverride: Joi.object({
		name: Joi.string().max(MAX_SWITCH_NAME_LENGTH).allow(''),
		description: Joi.string().max(MAX_DESCRIPTION_LENGTH).allow(''),
		location: Joi.string().max(MAX_LOCATION_LENGTH).allow(''),
		category: Joi.string().valid('Community', 'Personal', 'Event', 'Test', 'Other'),
		link: Joi.string().uri({ scheme: ['http', 'https'] }).max(MAX_URL_LENGTH).allow(''),
		iconUrl: Joi.string().uri({ scheme: ['http', 'https'] }).max(MAX_URL_LENGTH).allow(''),
		bannerUrl: Joi.string().uri({ scheme: ['http', 'https'] }).max(MAX_URL_LENGTH).allow('')
	}).min(1),

	// ── V2: Premium / promo / tier schemas ────────────────────────────────────

	v2RedeemPromo: Joi.object({
		ownerPubKey: Joi.string().max(200).required(),
		ts: Joi.number().integer().min(0).required(),
		nonce: Joi.string().min(8).max(128).required(),
		sigOwner: Joi.string().max(200).required(),
		promoCode: Joi.string().min(3).max(64).required()
	}),

	v2GetOwnerTier: Joi.object({
		ownerPubKey: Joi.string().max(200).required(),
		ts: Joi.number().integer().min(0).required(),
		nonce: Joi.string().min(8).max(128).required(),
		sigOwner: Joi.string().max(200).required()
	})
};

const validateRequest = (schema) => {
	return (req, res, next) => {
		const { error, value } = schema.validate(req.body, {
			abortEarly: false,
			stripUnknown: true
		});

		if (error) {
			const errors = error.details.map(detail => ({
				field: detail.path.join('.'),
				message: detail.message
			}));

			return res.status(400).json({
				success: false,
				error: 'Validation failed',
				details: errors
			});
		}

		req.validatedData = value;
		next();
	};
};

const validateUID = (req, res, next) => {
	const { uid } = req.params;

	if (!isValidSwitchUid(uid)) {
		return res.status(400).json({
			success: false,
			error: 'Invalid UID format'
		});
	}

	next();
};

const sanitizePublicSwitchData = (switchData) => {
	if (!switchData) return null;

	return {
		uid: switchData.uid,
		name: switchData.name || '',
		description: switchData.description || '',
		location: switchData.location || '',
		category: switchData.category || 'Other',
		state: switchData.state,
		lastToggled: switchData.lastToggled || 0,
		toggleCount: switchData.toggleCount || 0,
		userCount: switchData.userCount || 0,
		link: switchData.link || '',
		iconUrl: switchData.iconUrl || '',
		bannerUrl: switchData.bannerUrl || '',
		ownerProfileUrl: switchData.ownerProfileUrl || ''
	};
};

const sanitizePrivateSwitchData = (switchData) => {
	if (!switchData) return null;

	return {
		uid: switchData.uid,
		name: switchData.name || '',
		description: switchData.description || '',
		location: switchData.location || '',
		category: switchData.category || 'Other',
		state: switchData.state,
		lastToggled: switchData.lastToggled || 0,
		createdAt: switchData.createdAt || 0,
		toggleCount: switchData.toggleCount || 0,
		publicize: switchData.publicize || false,
		link: switchData.link || '',
		iconUrl: switchData.iconUrl || '',
		bannerUrl: switchData.bannerUrl || '',
		...(typeof switchData.index === 'number' ? { index: switchData.index } : {}),
		...(switchData.authVersion ? { authVersion: switchData.authVersion } : {})
	};
};

module.exports = {
	schemas,
	validateRequest,
	validateUID,
	isValidSwitchUid,
	sanitizePublicSwitchData,
	sanitizePrivateSwitchData
};
