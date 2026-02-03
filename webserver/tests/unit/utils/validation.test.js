/**
 * Unit tests for validation utilities
 */

const {
	schemas,
	validateRequest,
	validateUID,
	sanitizePublicSwitchData,
	sanitizePrivateSwitchData
} = require('../../../src/utils/validation');

describe('Validation Utilities', () => {
	describe('schemas', () => {
		describe('createSwitch', () => {
			test('should validate valid switch creation data', () => {
				const validData = {
					description: 'Test Switch',
					location: 'Test City',
					category: 'Community',
					publicize: true
				};

				const { error, value } = schemas.createSwitch.validate(validData);

				expect(error).toBeUndefined();
				expect(value).toMatchObject(validData);
				expect(value.link).toBe('');
			});

			test('should apply defaults for missing fields', () => {
				const minimalData = {};

				const { error, value } = schemas.createSwitch.validate(minimalData);

				expect(error).toBeUndefined();
				expect(value.description).toBe('');
				expect(value.location).toBe('');
				expect(value.category).toBe('Other');
				expect(value.publicize).toBe(false);
			});

			test('should reject invalid category', () => {
				const invalidData = {
					category: 'InvalidCategory'
				};

				const { error } = schemas.createSwitch.validate(invalidData);

				expect(error).toBeDefined();
				expect(error.details[0].message).toContain('must be one of');
			});

			test('should reject description that is too long', () => {
				const invalidData = {
					description: 'a'.repeat(501) // Exceeds 500 character limit
				};

				const { error } = schemas.createSwitch.validate(invalidData);

				expect(error).toBeDefined();
				expect(error.details[0].message).toContain('length must be less than or equal to 500');
			});

			test('should reject location that is too long', () => {
				const invalidData = {
					location: 'a'.repeat(101) // Exceeds 100 character limit
				};

				const { error } = schemas.createSwitch.validate(invalidData);

				expect(error).toBeDefined();
				expect(error.details[0].message).toContain('length must be less than or equal to 100');
			});
		});

		describe('toggleSwitch', () => {
			test('should validate valid toggle request', () => {
				const validData = {
					personalKey: global.testUtils.createTestPersonalKey()
				};

				const { error, value } = schemas.toggleSwitch.validate(validData);

				expect(error).toBeUndefined();
				expect(value).toEqual(validData);
			});

			test('should reject invalid UUID format', () => {
				const invalidData = {
					personalKey: 'not-a-uuid'
				};

				const { error } = schemas.toggleSwitch.validate(invalidData);

				expect(error).toBeDefined();
				expect(error.details[0].message).toContain('must be a valid GUID');
			});

			test('should require personal key', () => {
				const invalidData = {};

				const { error } = schemas.toggleSwitch.validate(invalidData);

				expect(error).toBeDefined();
				expect(error.details[0].message).toContain('is required');
			});
		});

		describe('generateKey', () => {
			test('should validate consent', () => {
				const validData = {
					consent: true
				};

				const { error, value } = schemas.generateKey.validate(validData);

				expect(error).toBeUndefined();
				expect(value).toEqual(validData);
			});

			test('should reject false consent', () => {
				const invalidData = {
					consent: false
				};

				const { error } = schemas.generateKey.validate(invalidData);

				expect(error).toBeDefined();
				expect(error.details[0].message).toContain('must be [true]');
			});
		});

		describe('deleteKey', () => {
			test('should validate valid deletion request', () => {
				const validData = {
					personalKey: global.testUtils.createTestPersonalKey(),
					confirmation: 'DELETE_ALL_DATA'
				};

				const { error, value } = schemas.deleteKey.validate(validData);

				expect(error).toBeUndefined();
				expect(value).toEqual(validData);
			});

			test('should reject incorrect confirmation', () => {
				const invalidData = {
					personalKey: global.testUtils.createTestPersonalKey(),
					confirmation: 'wrong-confirmation'
				};

				const { error } = schemas.deleteKey.validate(invalidData);

				expect(error).toBeDefined();
				expect(error.details[0].message).toContain('must be [DELETE_ALL_DATA]');
			});
		});
	});

	describe('validateUID', () => {
		test('should pass valid UUID', () => {
			const req = {
				params: {
					uid: global.testUtils.generateTestUUID()
				}
			};
			const res = {
				status: jest.fn().mockReturnThis(),
				json: jest.fn()
			};
			const next = jest.fn();

			validateUID(req, res, next);

			expect(next).toHaveBeenCalled();
			expect(res.status).not.toHaveBeenCalled();
		});

		test('should reject invalid UUID', () => {
			const req = {
				params: {
					uid: 'not-a-uuid'
				}
			};
			const res = {
				status: jest.fn().mockReturnThis(),
				json: jest.fn()
			};
			const next = jest.fn();

			validateUID(req, res, next);

			expect(next).not.toHaveBeenCalled();
			expect(res.status).toHaveBeenCalledWith(400);
			expect(res.json).toHaveBeenCalledWith({
				success: false,
				error: 'Invalid UID format'
			});
		});
	});

	describe('validateRequest', () => {
		test('should validate request body against schema', () => {
			const middleware = validateRequest(schemas.createSwitch);
			const req = {
				body: {
					description: 'Test Switch',
					category: 'Test'
				}
			};
			const res = {
				status: jest.fn().mockReturnThis(),
				json: jest.fn()
			};
			const next = jest.fn();

			middleware(req, res, next);

			expect(req.validatedData).toBeDefined();
			expect(req.validatedData.description).toBe('Test Switch');
			expect(req.validatedData.category).toBe('Test');
			expect(next).toHaveBeenCalled();
		});

		test('should return validation errors', () => {
			const middleware = validateRequest(schemas.createSwitch);
			const req = {
				body: {
					category: 'InvalidCategory'
				}
			};
			const res = {
				status: jest.fn().mockReturnThis(),
				json: jest.fn()
			};
			const next = jest.fn();

			middleware(req, res, next);

			expect(next).not.toHaveBeenCalled();
			expect(res.status).toHaveBeenCalledWith(400);
			expect(res.json).toHaveBeenCalledWith(
				expect.objectContaining({
					success: false,
					error: 'Validation failed',
					details: expect.any(Array)
				})
			);
		});
	});

	describe('sanitizePublicSwitchData', () => {
		test('should return only public fields', () => {
			const switchData = {
				uid: 'test-uid',
				personalKey: 'secret-key',
				name: 'Test Name',
				description: 'Test Switch',
				location: 'Test City',
				category: 'Test',
				state: true,
				lastToggled: 1234567890,
				createdAt: 1234567890,
				toggleCount: 5,
				publicize: true,
				link: ''
			};

			const sanitized = sanitizePublicSwitchData(switchData);

			expect(sanitized).toEqual({
				uid: 'test-uid',
				name: 'Test Name',
				description: 'Test Switch',
				location: 'Test City',
				category: 'Test',
				state: true,
				lastToggled: 1234567890,
				toggleCount: 5,
				userCount: 0,
				link: '',
				iconUrl: '',
				bannerUrl: '',
				ownerProfileUrl: ''
			});

			expect(sanitized.personalKey).toBeUndefined();
			expect(sanitized.createdAt).toBeUndefined();
		});

		test('should handle null input', () => {
			const sanitized = sanitizePublicSwitchData(null);
			expect(sanitized).toBeNull();
		});

		test('should handle missing optional fields', () => {
			const switchData = {
				uid: 'test-uid',
				state: false
			};

			const sanitized = sanitizePublicSwitchData(switchData);

			expect(sanitized.description).toBe('');
			expect(sanitized.location).toBe('');
			expect(sanitized.category).toBe('Other');
			expect(sanitized.lastToggled).toBe(0);
		});
	});

	describe('sanitizePrivateSwitchData', () => {
		test('should return private fields for owner', () => {
			const switchData = {
				uid: 'test-uid',
				personalKey: 'secret-key',
				name: 'Test Name',
				description: 'Test Switch',
				location: 'Test City',
				category: 'Test',
				state: true,
				lastToggled: 1234567890,
				createdAt: 1234567890,
				toggleCount: 5,
				publicize: true,
				link: ''
			};

			const sanitized = sanitizePrivateSwitchData(switchData);

			expect(sanitized).toEqual({
				uid: 'test-uid',
				name: 'Test Name',
				description: 'Test Switch',
				location: 'Test City',
				category: 'Test',
				state: true,
				lastToggled: 1234567890,
				createdAt: 1234567890,
				toggleCount: 5,
				publicize: true,
				link: '',
				iconUrl: '',
				bannerUrl: ''
			});

			// Personal key should still be excluded
			expect(sanitized.personalKey).toBeUndefined();
		});

		test('should handle null input', () => {
			const sanitized = sanitizePrivateSwitchData(null);
			expect(sanitized).toBeNull();
		});
	});
});
