const Joi = require('joi');

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
		uid: Joi.string().uuid().required()
	}),

	generateKey: Joi.object({
		consent: Joi.boolean().valid(true).required()
	}),

	deleteKey: Joi.object({
		personalKey: Joi.string().uuid().required(),
		confirmation: Joi.string().valid('DELETE_ALL_DATA').required()
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

	const schema = Joi.string().uuid().required();
	const { error } = schema.validate(uid);

	if (error) {
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
		link: switchData.link || ''
	};
};

module.exports = {
	schemas,
	validateRequest,
	validateUID,
	sanitizePublicSwitchData,
	sanitizePrivateSwitchData
};
