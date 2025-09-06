/**
 * Integration tests for API endpoints
 */

const request = require('supertest');
const express = require('express');
const cors = require('cors');
const helmet = require('helmet');
const apiRoutes = require('../../src/routes/api');
const redisClient = require('../../src/utils/redis');

describe('API Integration Tests', () => {
	let app;

	beforeAll(async () => {
		// Create Express app for testing
		app = express();

		// Apply middleware
		app.use(helmet({ contentSecurityPolicy: false }));
		app.use(cors());
		app.use(express.json({ limit: '10mb' }));
		app.use(express.urlencoded({ extended: true, limit: '10mb' }));

		// Add API routes
		app.use('/api', apiRoutes);

		// Connect to test Redis
		await redisClient.connect();
	});

	afterAll(async () => {
		if (redisClient.isConnected) {
			await redisClient.disconnect();
		}
	});

	beforeEach(async () => {
		// Clean up test data before each test
		if (redisClient.isConnected) {
			const testKeys = await redisClient.client.keys('*');
			if (testKeys.length > 0) {
				await redisClient.client.del(...testKeys);
			}
		}
	});

	describe('POST /api/generate-key', () => {
		test('should generate a new personal key', async () => {
			const response = await request(app)
				.post('/api/generate-key')
				.send({ consent: true })
				.expect(200);

			expect(response.body.success).toBe(true);
			expect(response.body.data.personalKey).toBeDefined();
			expect(response.body.data.personalKey).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i);
			expect(response.body.data.jwt).toBeDefined();
		});

		test('should require consent', async () => {
			const response = await request(app)
				.post('/api/generate-key')
				.send({ consent: false })
				.expect(400);

			expect(response.body.success).toBe(false);
			expect(response.body.error).toBe('Validation failed');
		});

		test('should validate request body', async () => {
			const response = await request(app)
				.post('/api/generate-key')
				.send({})
				.expect(400);

			expect(response.body.success).toBe(false);
			expect(response.body.error).toBe('Validation failed');
		});
	});

	describe('POST /api/create-switch', () => {
		let personalKey;

		beforeEach(async () => {
			// Generate a personal key for testing
			const keyResponse = await request(app)
				.post('/api/generate-key')
				.send({ consent: true });
			personalKey = keyResponse.body.data.personalKey;
		});

		test('should create a new switch', async () => {
			const switchData = {
				description: 'Test Switch',
				location: 'Test City',
				category: 'Test',
				publicize: false
			};

			const response = await request(app)
				.post('/api/create-switch')
				.set('X-Personal-Key', personalKey)
				.send(switchData)
				.expect(200);

			expect(response.body.success).toBe(true);
			expect(response.body.data.uid).toBeDefined();
			expect(response.body.data.description).toBe(switchData.description);
			expect(response.body.data.location).toBe(switchData.location);
			expect(response.body.data.category).toBe(switchData.category);
			expect(response.body.data.state).toBe(false);
			expect(response.body.data.websocketUrl).toContain(response.body.data.uid);
		});

		test('should require authentication', async () => {
			const response = await request(app)
				.post('/api/create-switch')
				.send({})
				.expect(401);

			expect(response.body.success).toBe(false);
			expect(response.body.error).toBe('Personal key required');
		});

		test('should validate switch data', async () => {
			const invalidData = {
				category: 'InvalidCategory'
			};

			const response = await request(app)
				.post('/api/create-switch')
				.set('X-Personal-Key', personalKey)
				.send(invalidData)
				.expect(400);

			expect(response.body.success).toBe(false);
			expect(response.body.error).toBe('Validation failed');
		});

		test('should apply defaults for missing fields', async () => {
			const response = await request(app)
				.post('/api/create-switch')
				.set('X-Personal-Key', personalKey)
				.send({})
				.expect(200);

			expect(response.body.data.description).toBe('');
			expect(response.body.data.location).toBe('');
			expect(response.body.data.category).toBe('Other');
			expect(response.body.data.publicize).toBe(false);
		});
	});

	describe('POST /api/toggle/:uid', () => {
		let personalKey;
		let switchUID;

		beforeEach(async () => {
			// Generate personal key and create switch
			const keyResponse = await request(app)
				.post('/api/generate-key')
				.send({ consent: true });
			personalKey = keyResponse.body.data.personalKey;

			const switchResponse = await request(app)
				.post('/api/create-switch')
				.set('X-Personal-Key', personalKey)
				.send({ description: 'Test Switch' });
			switchUID = switchResponse.body.data.uid;
		});

		test('should toggle switch state', async () => {
			const response = await request(app)
				.post(`/api/toggle/${switchUID}`)
				.set('X-Personal-Key', personalKey)
				.send({})
				.expect(200);

			expect(response.body.success).toBe(true);
			expect(response.body.data.uid).toBe(switchUID);
			expect(response.body.data.state).toBe(true);
			expect(response.body.data.timestamp).toBeDefined();
		});

		test('should require authentication', async () => {
			const response = await request(app)
				.post(`/api/toggle/${switchUID}`)
				.send({})
				.expect(401);

			expect(response.body.success).toBe(false);
		});

		test('should require switch ownership', async () => {
			// Create another personal key
			const otherKeyResponse = await request(app)
				.post('/api/generate-key')
				.send({ consent: true });
			const otherPersonalKey = otherKeyResponse.body.data.personalKey;

			const response = await request(app)
				.post(`/api/toggle/${switchUID}`)
				.set('X-Personal-Key', otherPersonalKey)
				.send({})
				.expect(401);

			expect(response.body.success).toBe(false);
			expect(response.body.error).toContain('Unauthorized');
		});

		test('should reject invalid UID', async () => {
			const response = await request(app)
				.post('/api/toggle/invalid-uid')
				.set('X-Personal-Key', personalKey)
				.send({})
				.expect(400);

			expect(response.body.success).toBe(false);
			expect(response.body.error).toBe('Invalid UID format');
		});

		test('should handle non-existent switch', async () => {
			const nonExistentUID = global.testUtils.generateTestUUID();

			const response = await request(app)
				.post(`/api/toggle/${nonExistentUID}`)
				.set('X-Personal-Key', personalKey)
				.send({})
				.expect(401);

			expect(response.body.success).toBe(false);
		});
	});

	describe('GET /api/status/:uid', () => {
		let personalKey;
		let switchUID;

		beforeEach(async () => {
			// Generate personal key and create switch
			const keyResponse = await request(app)
				.post('/api/generate-key')
				.send({ consent: true });
			personalKey = keyResponse.body.data.personalKey;

			const switchResponse = await request(app)
				.post('/api/create-switch')
				.set('X-Personal-Key', personalKey)
				.send({
					description: 'Test Switch',
					location: 'Test City',
					category: 'Test'
				});
			switchUID = switchResponse.body.data.uid;
		});

		test('should get switch status without authentication', async () => {
			const response = await request(app)
				.get(`/api/status/${switchUID}`)
				.expect(200);

			expect(response.body.success).toBe(true);
			expect(response.body.data.uid).toBe(switchUID);
			expect(response.body.data.description).toBe('Test Switch');
			expect(response.body.data.location).toBe('Test City');
			expect(response.body.data.category).toBe('Test');
			expect(response.body.data.state).toBe(false);

			// Should not include private fields
			expect(response.body.data.personalKey).toBeUndefined();
			expect(response.body.data.createdAt).toBeUndefined();
			expect(response.body.data.toggleCount).toBeUndefined();
		});

		test('should handle non-existent switch', async () => {
			const nonExistentUID = global.testUtils.generateTestUUID();

			const response = await request(app)
				.get(`/api/status/${nonExistentUID}`)
				.expect(404);

			expect(response.body.success).toBe(false);
			expect(response.body.error).toBe('Switch not found');
		});

		test('should reject invalid UID', async () => {
			const response = await request(app)
				.get('/api/status/invalid-uid')
				.expect(400);

			expect(response.body.success).toBe(false);
			expect(response.body.error).toBe('Invalid UID format');
		});
	});

	describe('GET /api/public-switches', () => {
		test('should return empty list when no public switches', async () => {
			const response = await request(app)
				.get('/api/public-switches')
				.expect(200);

			expect(response.body.success).toBe(true);
			expect(response.body.data.switches).toEqual([]);
			expect(response.body.data.count).toBe(0);
		});

		test('should return only public switches', async () => {
			// Create personal key
			const keyResponse = await request(app)
				.post('/api/generate-key')
				.send({ consent: true });
			const personalKey = keyResponse.body.data.personalKey;

			// Create public switch
			const publicSwitchResponse = await request(app)
				.post('/api/create-switch')
				.set('X-Personal-Key', personalKey)
				.send({
					description: 'Public Switch',
					publicize: true
				});

			// Create private switch
			await request(app)
				.post('/api/create-switch')
				.set('X-Personal-Key', personalKey)
				.send({
					description: 'Private Switch',
					publicize: false
				});

			const response = await request(app)
				.get('/api/public-switches')
				.expect(200);

			expect(response.body.success).toBe(true);
			expect(response.body.data.switches).toHaveLength(1);
			expect(response.body.data.switches[0].uid).toBe(publicSwitchResponse.body.data.uid);
			expect(response.body.data.switches[0].description).toBe('Public Switch');
			expect(response.body.data.count).toBe(1);
		});
	});

	describe('GET /api/my-switches', () => {
		let personalKey;

		beforeEach(async () => {
			const keyResponse = await request(app)
				.post('/api/generate-key')
				.send({ consent: true });
			personalKey = keyResponse.body.data.personalKey;
		});

		test('should return user switches', async () => {
			// Create two switches
			const switch1Response = await request(app)
				.post('/api/create-switch')
				.set('X-Personal-Key', personalKey)
				.send({ description: 'Switch 1' });

			const switch2Response = await request(app)
				.post('/api/create-switch')
				.set('X-Personal-Key', personalKey)
				.send({ description: 'Switch 2' });

			const response = await request(app)
				.get('/api/my-switches')
				.set('X-Personal-Key', personalKey)
				.expect(200);

			expect(response.body.success).toBe(true);
			expect(response.body.data.switches).toHaveLength(2);
			expect(response.body.data.count).toBe(2);

			const uids = response.body.data.switches.map(s => s.uid);
			expect(uids).toContain(switch1Response.body.data.uid);
			expect(uids).toContain(switch2Response.body.data.uid);
		});

		test('should require authentication', async () => {
			const response = await request(app)
				.get('/api/my-switches')
				.expect(401);

			expect(response.body.success).toBe(false);
		});

		test('should return empty list for user with no switches', async () => {
			const response = await request(app)
				.get('/api/my-switches')
				.set('X-Personal-Key', personalKey)
				.expect(200);

			expect(response.body.success).toBe(true);
			expect(response.body.data.switches).toEqual([]);
			expect(response.body.data.count).toBe(0);
		});
	});

	describe('DELETE /api/switch/:uid', () => {
		let personalKey;
		let switchUID;

		beforeEach(async () => {
			const keyResponse = await request(app)
				.post('/api/generate-key')
				.send({ consent: true });
			personalKey = keyResponse.body.data.personalKey;

			const switchResponse = await request(app)
				.post('/api/create-switch')
				.set('X-Personal-Key', personalKey)
				.send({ description: 'Test Switch' });
			switchUID = switchResponse.body.data.uid;
		});

		test('should delete switch', async () => {
			const response = await request(app)
				.delete(`/api/switch/${switchUID}`)
				.set('X-Personal-Key', personalKey)
				.expect(200);

			expect(response.body.success).toBe(true);
			expect(response.body.data.message).toContain('deleted successfully');
			expect(response.body.data.uid).toBe(switchUID);

			// Verify switch is deleted
			const statusResponse = await request(app)
				.get(`/api/status/${switchUID}`)
				.expect(404);
		});

		test('should require authentication', async () => {
			const response = await request(app)
				.delete(`/api/switch/${switchUID}`)
				.expect(401);

			expect(response.body.success).toBe(false);
		});

		test('should require ownership', async () => {
			const otherKeyResponse = await request(app)
				.post('/api/generate-key')
				.send({ consent: true });
			const otherPersonalKey = otherKeyResponse.body.data.personalKey;

			const response = await request(app)
				.delete(`/api/switch/${switchUID}`)
				.set('X-Personal-Key', otherPersonalKey)
				.expect(401);

			expect(response.body.success).toBe(false);
		});
	});

	describe('POST /api/delete-key', () => {
		let personalKey;
		let switchUID;

		beforeEach(async () => {
			const keyResponse = await request(app)
				.post('/api/generate-key')
				.send({ consent: true });
			personalKey = keyResponse.body.data.personalKey;

			const switchResponse = await request(app)
				.post('/api/create-switch')
				.set('X-Personal-Key', personalKey)
				.send({ description: 'Test Switch' });
			switchUID = switchResponse.body.data.uid;
		});

		test('should delete personal key and all data', async () => {
			const response = await request(app)
				.post('/api/delete-key')
				.send({
					personalKey,
					confirmation: 'DELETE_ALL_DATA'
				})
				.expect(200);

			expect(response.body.success).toBe(true);
			expect(response.body.data.message).toContain('deleted successfully');
			expect(response.body.data.deletedSwitches).toBe(1);

			// Verify switch is deleted
			const statusResponse = await request(app)
				.get(`/api/status/${switchUID}`)
				.expect(404);
		});

		test('should require correct confirmation', async () => {
			const response = await request(app)
				.post('/api/delete-key')
				.send({
					personalKey,
					confirmation: 'WRONG_CONFIRMATION'
				})
				.expect(400);

			expect(response.body.success).toBe(false);
		});

		test('should handle non-existent key', async () => {
			const nonExistentKey = global.testUtils.createTestPersonalKey();

			const response = await request(app)
				.post('/api/delete-key')
				.send({
					personalKey: nonExistentKey,
					confirmation: 'DELETE_ALL_DATA'
				})
				.expect(404);

			expect(response.body.success).toBe(false);
			expect(response.body.error).toBe('Personal key not found');
		});
	});

	describe('GET /api/health', () => {
		test('should return health status', async () => {
			const response = await request(app)
				.get('/api/health')
				.expect(200);

			expect(response.body.status).toBe('healthy');
			expect(response.body.timestamp).toBeDefined();
			expect(response.body.uptime).toBeDefined();
			expect(response.body.redis).toBe(true);
			expect(response.body.websocket).toBeDefined();
		});
	});

	describe('Rate Limiting', () => {
		test('should enforce rate limits', async () => {
			// This test would need to make many requests rapidly
			// For now, we'll just verify the rate limit headers are present
			const response = await request(app)
				.post('/api/generate-key')
				.send({ consent: true })
				.expect(200);

			expect(response.headers['x-ratelimit-limit']).toBeDefined();
			expect(response.headers['x-ratelimit-remaining']).toBeDefined();
		});
	});
});
