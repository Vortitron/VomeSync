const Joi = require('joi');

const schemas = {
	createSwitch: Joi.object({
		description: Joi.string().max(200).allow('').default(''),
		location: Joi.string().max(100).allow('').default(''),
		category: Joi.string().valid('Community', 'Personal', 'Event', 'Test', 'Other').default('Other'),
		publicize: Joi.boolean().default(false)
	}),

	toggleSwitch: Joi.object({
		personalKey: Joi.string().uuid().required()
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
		lastToggled: switchData.lastToggled || 0
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
		publicize: switchData.publicize || false
	};
};

module.exports = {
	schemas,
	validateRequest,
	validateUID,
	sanitizePublicSwitchData,
	sanitizePrivateSwitchData
};
