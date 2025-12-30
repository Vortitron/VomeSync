const Joi = require('joi');

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
		description: Joi.string().max(200).allow('').default(''),
		location: Joi.string().max(100).allow('').default(''),
		category: Joi.string().valid('Community', 'Personal', 'Event', 'Test', 'Other').default('Other'),
		publicize: Joi.boolean().default(false),
		link: Joi.string().uri({ scheme: ['http', 'https'] }).max(500).allow('').default(''),
		captchaToken: Joi.string().max(2000).allow('')
	}),

	updateSwitch: Joi.object({
		description: Joi.string().max(200).allow(''),
		location: Joi.string().max(100).allow(''),
		category: Joi.string().valid('Community', 'Personal', 'Event', 'Test', 'Other'),
		publicize: Joi.boolean(),
		link: Joi.string().uri({ scheme: ['http', 'https'] }).max(500).allow(''),
		captchaToken: Joi.string().max(2000).allow('')
	}).min(1),

	toggleSwitch: Joi.object({
		personalKey: Joi.string().uuid().required()
	}),

	addComment: Joi.object({
		comment: Joi.string().min(1).max(500).required()
	}),

	updateProfile: Joi.object({
		profileUrl: Joi.string().uri({ scheme: ['http', 'https'] }).max(500).allow('').default('')
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
		description: Joi.string().max(200).allow('').default(''),
		location: Joi.string().max(100).allow('').default(''),
		category: Joi.string().valid('Community', 'Personal', 'Event', 'Test', 'Other').default('Other'),
		publicize: Joi.boolean().default(false),
		link: Joi.string().uri({ scheme: ['http', 'https'] }).max(500).allow('').default(''),
		captchaToken: Joi.string().max(2000).allow('')
	}),

	v2MySwitches: Joi.object({
		ownerPubKey: Joi.string().max(120).required(),
		ts: Joi.number().integer().min(0).required(),
		nonce: Joi.string().min(8).max(128).required(),
		sigOwner: Joi.string().max(200).required()
	}),

	v2SetState: Joi.object({
		ts: Joi.number().integer().min(0).required(),
		nonce: Joi.string().min(8).max(128).required(),
		sigSwitch: Joi.string().max(200).required(),
		state: Joi.boolean().required(),
		params: Joi.object().unknown(true).default({})
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
		description: switchData.description || '',
		location: switchData.location || '',
		category: switchData.category || 'Other',
		state: switchData.state,
		lastToggled: switchData.lastToggled || 0,
		toggleCount: switchData.toggleCount || 0,
		userCount: switchData.userCount || 0,
		link: switchData.link || '',
		ownerProfileUrl: switchData.ownerProfileUrl || ''
	};
};

const sanitizePrivateSwitchData = (switchData) => {
	if (!switchData) return null;

	return {
		uid: switchData.uid,
		description: switchData.description || '',
		location: switchData.location || '',
		category: switchData.category || 'Other',
		state: switchData.state,
		lastToggled: switchData.lastToggled || 0,
		createdAt: switchData.createdAt || 0,
		toggleCount: switchData.toggleCount || 0,
		publicize: switchData.publicize || false,
		link: switchData.link || '',
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
