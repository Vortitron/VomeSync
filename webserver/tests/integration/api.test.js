/**
\t* Integration tests for API endpoints
\t*/

process.env.HCAPTCHA_SECRET = process.env.HCAPTCHA_SECRET || 'test-secret';
process.env.HCAPTCHA_BYPASS_TOKEN = process.env.HCAPTCHA_BYPASS_TOKEN || 'bypass-me';
process.env.HCAPTCHA_SITEKEY = process.env.HCAPTCHA_SITEKEY || 'test-sitekey';

const request = require('supertest');
const express = require('express');
const cors = require('cors');
const helmet = require('helmet');
const apiRoutes = require('../../src/routes/api');
const redisClient = require('../../src/utils/redis');
const {
	deriveOwnerIdFromOwnerPubKeyB64Url,
	deriveSwitchUidFromSwitchPubKeyB64Url,
	stableJsonStringify
} = require('../../src/utils/crypto_v2');

const ONE_BY_ONE_GIF = Buffer.from(
	'R0lGODlhAQABAIABAP///wAAACwAAAAAAQABAAACAkQBADs=',
	'base64'
);

async function createV2PublicSwitch(app, metaOverrides = {}, index = 0) {
	const owner = global.testUtils.createEd25519Keypair();
	const sw = global.testUtils.createEd25519Keypair();
	const ownerPubKeyB64 = Buffer.from(owner.rawPublicKey).toString('base64url');
	const switchPubKeyB64 = Buffer.from(sw.rawPublicKey).toString('base64url');
	const uid = deriveSwitchUidFromSwitchPubKeyB64Url(switchPubKeyB64);

	const ts = Date.now();
	const nonce = `n-${Date.now()}-${Math.random().toString(16).slice(2)}-create`;
	const meta = {
		description: 'Public Switch',
		location: 'Test City',
		category: 'Community',
		publicize: true,
		link: '',
		...metaOverrides
	};

	const canonical = stableJsonStringify({
		v: 2,
		action: 'create_switch',
		ownerPubKey: ownerPubKeyB64,
		switchPubKey: switchPubKeyB64,
		uid,
		index,
		ts,
		nonce,
		payload: meta
	});
	const sigOwner = global.testUtils.ed25519SignBase64Url(owner.privateKey, canonical);
	const sigSwitch = global.testUtils.ed25519SignBase64Url(sw.privateKey, canonical);

	const response = await request(app)
		.post('/api/v2/switch')
		.send({
			ownerPubKey: ownerPubKeyB64,
			switchPubKey: switchPubKeyB64,
			index,
			ts,
			nonce,
			sigOwner,
			sigSwitch,
			...meta,
			captchaToken: process.env.HCAPTCHA_BYPASS_TOKEN
		})
		.expect(200);

	return {
		uid: response.body.data.uid,
		owner,
		sw,
		ownerPubKeyB64,
		switchPubKeyB64,
		ownerId: deriveOwnerIdFromOwnerPubKeyB64Url(ownerPubKeyB64)
	};
}

async function createV2AccessKey(app, uid, owner, ownerPubKeyB64, permissions, name = '') {
	const ts = Date.now();
	const nonce = `n-${Date.now()}-${Math.random().toString(16).slice(2)}-ak`;
	const canonical = stableJsonStringify({
		v: 2,
		action: 'create_access_key',
		uid,
		ownerPubKey: ownerPubKeyB64,
		ts,
		nonce,
		payload: {
			name,
			permissions
		}
	});
	const sigOwner = global.testUtils.ed25519SignBase64Url(owner.privateKey, canonical);

	const response = await request(app)
		.post(`/api/v2/switch/${uid}/access-keys`)
		.send({
			ownerPubKey: ownerPubKeyB64,
			ts,
			nonce,
			sigOwner,
			name,
			permissions
		})
		.expect(200);

	return response.body.data.apiKey;
}

async function listV2AccessKeys(app, uid, owner, ownerPubKeyB64) {
	const ts = Date.now();
	const nonce = `n-${Date.now()}-${Math.random().toString(16).slice(2)}-ak-list`;
	const canonical = stableJsonStringify({
		v: 2,
		action: 'list_access_keys',
		uid,
		ownerPubKey: ownerPubKeyB64,
		ts,
		nonce
	});
	const sigOwner = global.testUtils.ed25519SignBase64Url(owner.privateKey, canonical);

	const response = await request(app)
		.post(`/api/v2/switch/${uid}/access-keys/list`)
		.send({
			ownerPubKey: ownerPubKeyB64,
			ts,
			nonce,
			sigOwner
		})
		.expect(200);

	return response.body.data.keys || [];
}

async function revokeV2AccessKey(app, uid, owner, ownerPubKeyB64, apiKey) {
	const ts = Date.now();
	const nonce = `n-${Date.now()}-${Math.random().toString(16).slice(2)}-ak-revoke`;
	const canonical = stableJsonStringify({
		v: 2,
		action: 'revoke_access_key',
		uid,
		ownerPubKey: ownerPubKeyB64,
		ts,
		nonce,
		apiKey
	});
	const sigOwner = global.testUtils.ed25519SignBase64Url(owner.privateKey, canonical);

	await request(app)
		.post(`/api/v2/switch/${uid}/access-keys/revoke`)
		.send({
			ownerPubKey: ownerPubKeyB64,
			ts,
			nonce,
			sigOwner,
			apiKey
		})
		.expect(200);
}

describe('API Integration Tests', () => {
	let app;
	let originalFetch;

	beforeAll(async () => {
		originalFetch = global.fetch;
		// Stub image downloads used by server-side image ingestion (icon/banner URL re-hosting).
		global.fetch = jest.fn(async () => new Response(ONE_BY_ONE_GIF, {
			status: 200,
			headers: { 'content-type': 'image/gif' }
		}));

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
		if (originalFetch) {
			global.fetch = originalFetch;
		}
	});

	beforeEach(async () => {
		// Clean up test data before each test
		if (redisClient.isConnected) {
			await redisClient.client.flushDb();
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

		test('should require captcha when publicize is true and captcha enabled', async () => {
			const response = await request(app)
				.post('/api/create-switch')
				.set('X-Personal-Key', personalKey)
				.send({
					description: 'No captcha',
					publicize: true
				})
				.expect(400);

			expect(response.body.success).toBe(false);
		});

		test('should accept bypass captcha token when publicize is true', async () => {
			const response = await request(app)
				.post('/api/create-switch')
				.set('X-Personal-Key', personalKey)
				.send({
					description: 'With captcha',
					publicize: true,
					captchaToken: process.env.HCAPTCHA_BYPASS_TOKEN
				})
				.expect(200);

			expect(response.body.success).toBe(true);
			expect(response.body.data.publicize).toBe(true);
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
			expect(response.body.data.toggleCount).toBe(0);
			expect(response.body.data.userCount).toBeDefined();
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
			// Public directory is v2-only
			const createdPublic = await createV2PublicSwitch(app, {
				description: 'Public Switch',
				publicize: true,
				category: 'Community',
				link: 'https://example.com'
			}, 0);
			await createV2PublicSwitch(app, { description: 'Private Switch', publicize: false }, 1);

			const response = await request(app)
				.get('/api/public-switches')
				.expect(200);

			expect(response.body.success).toBe(true);
			expect(response.body.data.switches).toHaveLength(1);
			expect(response.body.data.switches[0].uid).toBe(createdPublic.uid);
			expect(response.body.data.switches[0].description).toBe('Public Switch');
			expect(response.body.data.count).toBe(1);
		});
	});

	describe('Switch detail, comments, and categories', () => {
		let publicUid;
		let owner;
		let ownerPubKeyB64;
		let accessKey;

		beforeEach(async () => {
			const created = await createV2PublicSwitch(app, {
				description: 'Public Switch',
				publicize: true,
				category: 'Community',
				link: 'https://example.com'
			}, 0);
			publicUid = created.uid;
			owner = created.owner;
			ownerPubKeyB64 = created.ownerPubKeyB64;
			accessKey = await createV2AccessKey(app, publicUid, owner, ownerPubKeyB64, ['toggle', 'comment'], 'test-key');
		});

		test('should return detail for public switch', async () => {
			const response = await request(app)
				.get(`/api/switch/${publicUid}`)
				.expect(200);

			expect(response.body.success).toBe(true);
			expect(response.body.data.uid).toBe(publicUid);
			expect(response.body.data.description).toBe('Public Switch');
			expect(Array.isArray(response.body.data.events)).toBe(true);
			expect(response.body.data.link).toBe('https://example.com');
			expect(response.body.data.ownerProfileUrl).toBe('');
		});

		test('should reject detail for private switch', async () => {
			const createdPrivate = await createV2PublicSwitch(app, { description: 'Private Switch', publicize: false }, 1);

			const detailResponse = await request(app)
				.get(`/api/switch/${createdPrivate.uid}`)
				.expect(404);

			expect(detailResponse.body.success).toBe(false);
		});

		test('should accept comments from owner', async () => {
			const commentResponse = await request(app)
				.post(`/api/v2/switch/${publicUid}/comment`)
				.set('X-Api-Key', accessKey)
				.send({ comment: 'Test note' })
				.expect(200);

			expect(commentResponse.body.success).toBe(true);
			expect(commentResponse.body.data.viaApiKey).toBe(true);

			const detailResponse = await request(app)
				.get(`/api/switch/${publicUid}`)
				.expect(200);

			const comments = (detailResponse.body.data.events || []).filter(e => e.type === 'comment');
			expect(comments.length).toBe(1);
			expect(comments[0].comment).toBe('Test note');
			expect(comments[0].viaApiKey).toBe(true);
		});

		test('should require authentication for comments', async () => {
			const commentResponse = await request(app)
				.post(`/api/v2/switch/${publicUid}/comment`)
				.send({ comment: 'No key' })
				.expect(401);

			expect(commentResponse.body.success).toBe(false);
		});

		test('should enforce access key permissions (toggle-only key cannot comment)', async () => {
			const toggleOnlyKey = await createV2AccessKey(app, publicUid, owner, ownerPubKeyB64, ['toggle'], 'toggle-only');

			const commentResponse = await request(app)
				.post(`/api/v2/switch/${publicUid}/comment`)
				.set('X-Api-Key', toggleOnlyKey)
				.send({ comment: 'Should fail' })
				.expect(403);

			expect(commentResponse.body.success).toBe(false);
		});

		test('should allow metadata updates only with metadata permission', async () => {
			const toggleOnlyKey = await createV2AccessKey(app, publicUid, owner, ownerPubKeyB64, ['toggle'], 'toggle-only');
			const metadataKey = await createV2AccessKey(app, publicUid, owner, ownerPubKeyB64, ['metadata'], 'metadata-only');

			// Non-metadata key must be rejected
			await request(app)
				.post(`/api/v2/switch/${publicUid}/metadata`)
				.set('X-Api-Key', toggleOnlyKey)
				.send({ iconUrl: 'https://8.8.8.8/icon.png' })
				.expect(403);

			// Metadata key can update theming
			const response = await request(app)
				.post(`/api/v2/switch/${publicUid}/metadata`)
				.set('X-Api-Key', metadataKey)
				.send({
					iconUrl: 'https://8.8.8.8/icon.png',
					bannerUrl: 'https://8.8.8.8/banner.jpg',
					link: 'https://example.com'
				})
				.expect(200);

			expect(response.body.success).toBe(true);
			expect(response.body.data.iconUrl).toMatch(new RegExp(`^/api/media/switch/${publicUid}/icon_[0-9a-f]{16}\\.webp$`));
			expect(response.body.data.bannerUrl).toMatch(new RegExp(`^/api/media/switch/${publicUid}/banner_[0-9a-f]{16}\\.webp$`));
			expect(response.body.data.link).toBe('https://example.com');
		});

		test('manage-on-website keys can be reused for metadata updates', async () => {
			const websiteKey = await createV2AccessKey(app, publicUid, owner, ownerPubKeyB64, ['metadata'], 'website_session:test');

			// First use should succeed
			await request(app)
				.post(`/api/v2/switch/${publicUid}/metadata`)
				.set('X-Api-Key', websiteKey)
				.send({ iconUrl: 'https://8.8.8.8/icon.png' })
				.expect(200);

			// Second use should also succeed
			await request(app)
				.post(`/api/v2/switch/${publicUid}/metadata`)
				.set('X-Api-Key', websiteKey)
				.send({ iconUrl: 'https://8.8.8.8/icon2.png' })
				.expect(200);
		});

		test('toggle should record user count and timeline', async () => {
			const toggleResponse = await request(app)
				.post(`/api/v2/switch/${publicUid}/toggle`)
				.set('X-Api-Key', accessKey)
				.send({})
				.expect(200);

			expect(toggleResponse.body.data.state).toBe(true);

			const detailResponse = await request(app)
				.get(`/api/switch/${publicUid}`)
				.expect(200);

			expect(detailResponse.body.data.userCount).toBeGreaterThanOrEqual(1);
			const events = detailResponse.body.data.events || [];
			const stateEvents = events.filter(e => e.type === 'state');
			expect(stateEvents.length).toBeGreaterThanOrEqual(1);
		});

		test('should list and revoke v2 access keys', async () => {
			const keysBefore = await listV2AccessKeys(app, publicUid, owner, ownerPubKeyB64);
			const keyIdsBefore = keysBefore.map((k) => k.keyId);
			expect(keyIdsBefore).toContain(redisClient.getApiKeyId(accessKey));

			await revokeV2AccessKey(app, publicUid, owner, ownerPubKeyB64, accessKey);

			const toggleResponse = await request(app)
				.post(`/api/v2/switch/${publicUid}/toggle`)
				.set('X-Api-Key', accessKey)
				.send({})
				.expect(401);

			expect(toggleResponse.body.success).toBe(false);
		});

		test('should list categories with counts', async () => {
			const response = await request(app)
				.get('/api/categories')
				.expect(200);

			expect(response.body.success).toBe(true);
			expect(response.body.data.Community).toBeGreaterThanOrEqual(1);
		});

		test('should update switch metadata', async () => {
			const ts = Date.now();
			const nonce = `n-${Date.now()}-${Math.random().toString(16).slice(2)}-meta`;
			const updates = {
				link: 'https://updated.example.com',
				description: 'Updated',
				iconUrl: 'https://8.8.8.8/icon.png',
				bannerUrl: 'https://8.8.8.8/banner.jpg'
			};
			const canonical = stableJsonStringify({
				v: 2,
				action: 'update_switch',
				uid: publicUid,
				ownerPubKey: ownerPubKeyB64,
				ts,
				nonce,
				payload: updates
			});
			const sigOwner = global.testUtils.ed25519SignBase64Url(owner.privateKey, canonical);

			const response = await request(app)
				.post(`/api/v2/switch/${publicUid}`)
				.send({
					ownerPubKey: ownerPubKeyB64,
					ts,
					nonce,
					sigOwner,
					...updates
				})
				.expect(200);

			expect(response.body.success).toBe(true);
			expect(response.body.data.link).toBe('https://updated.example.com');
			expect(response.body.data.description).toBe('Updated');
			expect(response.body.data.iconUrl).toMatch(new RegExp(`^/api/media/switch/${publicUid}/icon_[0-9a-f]{16}\\.webp$`));
			expect(response.body.data.bannerUrl).toMatch(new RegExp(`^/api/media/switch/${publicUid}/banner_[0-9a-f]{16}\\.webp$`));
		});
	});

describe('Security and validation', () => {
	let ownerKey;
	let otherKey;
	let switchUid;
	let apiKey;

	beforeEach(async () => {
		const ownerResp = await request(app)
			.post('/api/generate-key')
			.send({ consent: true });
		ownerKey = ownerResp.body.data.personalKey;

		const otherResp = await request(app)
			.post('/api/generate-key')
			.send({ consent: true });
		otherKey = otherResp.body.data.personalKey;

		const switchResp = await request(app)
			.post('/api/create-switch')
			.set('X-Personal-Key', ownerKey)
			.send({ description: 'Protected', publicize: true, captchaToken: process.env.HCAPTCHA_BYPASS_TOKEN });
		switchUid = switchResp.body.data.uid;

		const apiKeyRes = await request(app)
			.post('/api/api-keys')
			.set('X-Personal-Key', ownerKey)
			.send({ name: 'auth-test' });
		apiKey = apiKeyRes.body.data.apiKey;
	});

	test('should reject update from non-owner key', async () => {
		const response = await request(app)
			.patch(`/api/switch/${switchUid}`)
			.set('X-Personal-Key', otherKey)
			.send({ description: 'Hacked' })
			.expect(401);

		expect(response.body.success).toBe(false);
	});

	test('should reject invalid update payload', async () => {
		const response = await request(app)
			.patch(`/api/switch/${switchUid}`)
			.set('X-Personal-Key', ownerKey)
			.send({ category: 'InvalidCat' })
			.expect(400);

		expect(response.body.success).toBe(false);
		expect(response.body.error).toBe('Validation failed');
	});

	test('should reject empty comment body', async () => {
		const response = await request(app)
			.post(`/api/switch/${switchUid}/comment`)
			.set('X-Personal-Key', ownerKey)
			.send({ comment: '' })
			.expect(400);

		expect(response.body.success).toBe(false);
	});

	test('revoked API key cannot toggle', async () => {
		await request(app)
			.delete(`/api/api-keys/${apiKey}`)
			.set('X-Personal-Key', ownerKey)
			.expect(200);

		const response = await request(app)
			.post(`/api/toggle/${switchUid}`)
			.set('X-Api-Key', apiKey)
			.send({})
			.expect(401);

		expect(response.body.success).toBe(false);
	});

	test('profile link requires authentication', async () => {
		const response = await request(app)
			.post('/api/profile/link')
			.send({ profileUrl: 'https://example.com' })
			.expect(401);

		expect(response.body.success).toBe(false);
	});

	test('publicize update requires captcha', async () => {
		const privateSwitch = await request(app)
			.post('/api/create-switch')
			.set('X-Personal-Key', ownerKey)
			.send({ description: 'Private' })
			.expect(200);

		const response = await request(app)
			.patch(`/api/switch/${privateSwitch.body.data.uid}`)
			.set('X-Personal-Key', ownerKey)
			.send({ publicize: true })
			.expect(400);

		expect(response.body.success).toBe(false);
	});

	test('session token redeem requires token', async () => {
		const response = await request(app)
			.post('/api/session-token/redeem')
			.send({})
			.expect(400);

		expect(response.body.success).toBe(false);
		expect(response.body.error).toBe('Token required');
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

	describe('GET /api/next-switch-name', () => {
		test('should return a globally unique Sami name', async () => {
			const response = await request(app)
				.get('/api/next-switch-name')
				.expect(200);

			expect(response.body.success).toBe(true);
			expect(response.body.data.name).toBeDefined();
			expect(response.body.data.name).toMatch(/^VomeSync /);
			expect(response.body.data.allocatedCount).toBeGreaterThanOrEqual(1);
		});

		test('should return different names on consecutive calls', async () => {
			const response1 = await request(app)
				.get('/api/next-switch-name')
				.expect(200);

			const response2 = await request(app)
				.get('/api/next-switch-name')
				.expect(200);

			expect(response1.body.data.name).not.toBe(response2.body.data.name);
		});

		test('should handle many allocations gracefully', async () => {
			// Allocate several names and ensure they're all unique
			const names = new Set();
			for (let i = 0; i < 10; i += 1) {
				// eslint-disable-next-line no-await-in-loop
				const response = await request(app)
					.get('/api/next-switch-name')
					.expect(200);
				names.add(response.body.data.name);
			}
			// All 10 names should be unique
			expect(names.size).toBe(10);
		});
	});

	describe('POST /api/release-switch-name', () => {
		test('should release an allocated name', async () => {
			// First allocate a name
			const allocResponse = await request(app)
				.get('/api/next-switch-name')
				.expect(200);
			const name = allocResponse.body.data.name;

			// Release it
			const releaseResponse = await request(app)
				.post('/api/release-switch-name')
				.send({ name })
				.expect(200);

			expect(releaseResponse.body.success).toBe(true);
			expect(releaseResponse.body.data.released).toBe(true);
		});

		test('should return false for non-existent name', async () => {
			const response = await request(app)
				.post('/api/release-switch-name')
				.send({ name: 'VomeSync NonExistentWord' })
				.expect(200);

			expect(response.body.success).toBe(true);
			expect(response.body.data.released).toBe(false);
		});

		test('should reject missing name parameter', async () => {
			const response = await request(app)
				.post('/api/release-switch-name')
				.send({})
				.expect(400);

			expect(response.body.success).toBe(false);
		});
	});
});
