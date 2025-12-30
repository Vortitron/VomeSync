/**
 * Integration tests for V2 crypto-auth endpoints
 */

const request = require('supertest');
const express = require('express');
const cors = require('cors');
const helmet = require('helmet');
const apiRoutes = require('../../src/routes/api');
const redisClient = require('../../src/utils/redis');
const { deriveSwitchUidFromSwitchPubKeyB64Url, stableJsonStringify } = require('../../src/utils/crypto_v2');

describe('API V2 Integration Tests', () => {
	let app;

	beforeAll(async () => {
		app = express();
		app.use(helmet({ contentSecurityPolicy: false }));
		app.use(cors());
		app.use(express.json({ limit: '10mb' }));
		app.use(express.urlencoded({ extended: true, limit: '10mb' }));
		app.use('/api', apiRoutes);
		await redisClient.connect();
	});

	afterAll(async () => {
		if (redisClient.isConnected) {
			await redisClient.disconnect();
		}
	});

	beforeEach(async () => {
		if (redisClient.isConnected) {
			const keys = await redisClient.client.keys('*');
			if (keys.length > 0) {
				await redisClient.client.del(...keys);
			}
		}
	});

	test('should create a v2 switch, list it, and set state with params', async () => {
		const owner = global.testUtils.createEd25519Keypair();
		const sw = global.testUtils.createEd25519Keypair();
		const ownerPubKeyB64 = Buffer.from(owner.rawPublicKey).toString('base64url');
		const switchPubKeyB64 = Buffer.from(sw.rawPublicKey).toString('base64url');

		const ts = Date.now();
		const nonceCreate = `n-${Date.now()}-create`;
		const index = 0;
		const meta = {
			description: 'Crypto Switch',
			location: 'Test City',
			category: 'Test',
			publicize: false,
			link: ''
		};

		const uid = deriveSwitchUidFromSwitchPubKeyB64Url(switchPubKeyB64);
		const canonicalCreate = stableJsonStringify({
			v: 2,
			action: 'create_switch',
			ownerPubKey: ownerPubKeyB64,
			switchPubKey: switchPubKeyB64,
			uid,
			index,
			ts,
			nonce: nonceCreate,
			payload: meta
		});

		const sigOwner = global.testUtils.ed25519SignBase64Url(owner.privateKey, canonicalCreate);
		const sigSwitch = global.testUtils.ed25519SignBase64Url(sw.privateKey, canonicalCreate);

		const createResp = await request(app)
			.post('/api/v2/switch')
			.send({
				ownerPubKey: ownerPubKeyB64,
				switchPubKey: switchPubKeyB64,
				index,
				ts,
				nonce: nonceCreate,
				sigOwner,
				sigSwitch,
				...meta
			})
			.expect(200);

		expect(createResp.body.success).toBe(true);
		expect(createResp.body.data.uid).toBe(uid);
		expect(createResp.body.data.authVersion).toBe(2);

		const nonceList = `n-${Date.now()}-list`;
		const canonicalList = stableJsonStringify({
			v: 2,
			action: 'my_switches',
			ownerPubKey: ownerPubKeyB64,
			ts,
			nonce: nonceList
		});
		const sigList = global.testUtils.ed25519SignBase64Url(owner.privateKey, canonicalList);

		const listResp = await request(app)
			.post('/api/v2/my-switches')
			.send({
				ownerPubKey: ownerPubKeyB64,
				ts,
				nonce: nonceList,
				sigOwner: sigList
			})
			.expect(200);

		expect(listResp.body.success).toBe(true);
		expect(listResp.body.data.count).toBe(1);
		expect(listResp.body.data.switches[0].uid).toBe(uid);

		const nonceState = `n-${Date.now()}-state`;
		const params = { rgb_color: [10, 20, 30], brightness: 200 };
		const canonicalState = stableJsonStringify({
			v: 2,
			action: 'set_state',
			uid,
			ts,
			nonce: nonceState,
			state: true,
			params
		});
		const sigState = global.testUtils.ed25519SignBase64Url(sw.privateKey, canonicalState);

		const stateResp = await request(app)
			.post(`/api/v2/switch/${uid}/state`)
			.send({
				ts,
				nonce: nonceState,
				sigSwitch: sigState,
				state: true,
				params
			})
			.expect(200);

		expect(stateResp.body.success).toBe(true);
		expect(stateResp.body.data.state).toBe(true);

		// Stored params should be returned via getSwitchState
		const stored = await redisClient.getSwitchState(uid);
		expect(stored.params).toEqual(params);
	});
});


