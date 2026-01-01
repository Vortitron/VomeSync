/**
 * Unit tests for Redis utilities
 */

const redisClient = require('../../../src/utils/redis');
const { deriveOwnerIdFromOwnerPubKeyB64Url, deriveSwitchUidFromSwitchPubKeyB64Url } = require('../../../src/utils/crypto_v2');

describe('Redis Client', () => {
	beforeEach(async () => {
		await redisClient.connect();
		if (redisClient.isConnected) {
			await redisClient.client.flushDb();
		}
	});

	afterEach(async () => {
		if (redisClient.isConnected) {
			// Clean up test data
			const testKeys = await redisClient.client.keys('test-*');
			if (testKeys.length > 0) {
				await redisClient.client.del(...testKeys);
			}
			await redisClient.disconnect();
		}
	});

	describe('connection', () => {
		test('should connect to Redis', async () => {
			expect(redisClient.isConnected).toBe(true);
		});

		test('should disconnect from Redis', async () => {
			await redisClient.disconnect();
			expect(redisClient.isConnected).toBe(false);
		});
	});

	describe('switch operations', () => {
		describe('createSwitch', () => {
			test('should create a new switch', async () => {
				const uid = global.testUtils.generateTestUUID();
				const personalKey = global.testUtils.createTestPersonalKey();
				const switchConfig = global.testUtils.createTestSwitchData();

				const result = await redisClient.createSwitch(uid, personalKey, switchConfig);

				expect(result.uid).toBe(uid);
				expect(result.personalKey).toBe(personalKey);
				expect(result.state).toBe('off');
				expect(result.description).toBe(switchConfig.description);
				expect(result.location).toBe(switchConfig.location);
				expect(result.category).toBe(switchConfig.category);
				expect(result.createdAt).toBeDefined();
				expect(result.toggleCount).toBe(0);
			});

			test('should add switch to user index', async () => {
				const uid = global.testUtils.generateTestUUID();
				const personalKey = global.testUtils.createTestPersonalKey();
				const switchConfig = global.testUtils.createTestSwitchData();

				await redisClient.createSwitch(uid, personalKey, switchConfig);

				const userSwitches = await redisClient.getUserSwitches(personalKey);
				expect(userSwitches).toHaveLength(1);
				expect(userSwitches[0].uid).toBe(uid);
			});

			test('should add to public index if publicized', async () => {
				const uid = global.testUtils.generateTestUUID();
				const personalKey = global.testUtils.createTestPersonalKey();
				const switchConfig = global.testUtils.createTestSwitchData({ publicize: true });

				await redisClient.createSwitch(uid, personalKey, switchConfig);

				const publicUIDs = await redisClient.client.sMembers('public_switches');
				expect(publicUIDs).toContain(uid);
			});
		});

		describe('setSwitchState', () => {
			test('should update switch state', async () => {
				const uid = global.testUtils.generateTestUUID();
				const personalKey = global.testUtils.createTestPersonalKey();
				const switchConfig = global.testUtils.createTestSwitchData();

				await redisClient.createSwitch(uid, personalKey, switchConfig);

				const result = await redisClient.setSwitchState(uid, true);

				expect(result.state).toBe('on');
				expect(result.lastToggled).toBeDefined();
			});

			test('should set expiry on switch', async () => {
				const uid = global.testUtils.generateTestUUID();
				const personalKey = global.testUtils.createTestPersonalKey();
				const switchConfig = global.testUtils.createTestSwitchData();

				await redisClient.createSwitch(uid, personalKey, switchConfig);
				await redisClient.setSwitchState(uid, true);

				const ttl = await redisClient.client.ttl(`switch:${uid}`);
				expect(ttl).toBeGreaterThan(0);
				expect(ttl).toBeLessThanOrEqual(30 * 24 * 60 * 60); // 30 days
			});
		});

		describe('getSwitchState', () => {
			test('should retrieve switch state', async () => {
				const uid = global.testUtils.generateTestUUID();
				const personalKey = global.testUtils.createTestPersonalKey();
				const switchConfig = global.testUtils.createTestSwitchData();

				await redisClient.createSwitch(uid, personalKey, switchConfig);
				await redisClient.setSwitchState(uid, true);

				const state = await redisClient.getSwitchState(uid);

				expect(state.state).toBe(true);
				expect(state.uid).toBe(uid);
				expect(state.description).toBe(switchConfig.description);
			});

			test('should return null for non-existent switch', async () => {
				const nonExistentUID = global.testUtils.generateTestUUID();

				const state = await redisClient.getSwitchState(nonExistentUID);

				expect(state).toBeNull();
			});
		});

		describe('getUserSwitches', () => {
			test('should return user switches', async () => {
				const personalKey = global.testUtils.createTestPersonalKey();
				const uid1 = global.testUtils.generateTestUUID();
				const uid2 = global.testUtils.generateTestUUID();

				await redisClient.createSwitch(uid1, personalKey, global.testUtils.createTestSwitchData());
				await redisClient.createSwitch(uid2, personalKey, global.testUtils.createTestSwitchData());

				const userSwitches = await redisClient.getUserSwitches(personalKey);

				expect(userSwitches).toHaveLength(2);
				const uids = userSwitches.map(s => s.uid);
				expect(uids).toContain(uid1);
				expect(uids).toContain(uid2);
			});

			test('should return empty array for user with no switches', async () => {
				const personalKey = global.testUtils.createTestPersonalKey();

				const userSwitches = await redisClient.getUserSwitches(personalKey);

				expect(userSwitches).toEqual([]);
			});
		});

		describe('getPublicSwitches', () => {
			test('should return only public switches', async () => {
				const personalKey = global.testUtils.createTestPersonalKey();
				const uid1 = global.testUtils.generateTestUUID();
				const uid2 = global.testUtils.generateTestUUID();

				// Create public switch
				await redisClient.createSwitch(uid1, personalKey,
					global.testUtils.createTestSwitchData({ publicize: true })
				);

				// Create private switch
				await redisClient.createSwitch(uid2, personalKey,
					global.testUtils.createTestSwitchData({ publicize: false })
				);

				// V1 switches are ignored in the public directory (v2-only)
				const publicSwitchesEmpty = await redisClient.getPublicSwitches();
				expect(publicSwitchesEmpty).toEqual([]);

				// Create v2 public + private switches
				const owner = global.testUtils.createEd25519Keypair();
				const ownerPubKeyB64 = Buffer.from(owner.rawPublicKey).toString('base64url');
				const ownerId = deriveOwnerIdFromOwnerPubKeyB64Url(ownerPubKeyB64);

				const sw1 = global.testUtils.createEd25519Keypair();
				const sw2 = global.testUtils.createEd25519Keypair();
				const switchPubKey1 = Buffer.from(sw1.rawPublicKey).toString('base64url');
				const switchPubKey2 = Buffer.from(sw2.rawPublicKey).toString('base64url');
				const v2uid1 = deriveSwitchUidFromSwitchPubKeyB64Url(switchPubKey1);
				const v2uid2 = deriveSwitchUidFromSwitchPubKeyB64Url(switchPubKey2);

				await redisClient.createSwitchV2(v2uid1, ownerId, ownerPubKeyB64, switchPubKey1, 0, {
					description: 'Public v2',
					location: 'Test City',
					category: 'Test',
					publicize: true,
					link: ''
				});
				await redisClient.createSwitchV2(v2uid2, ownerId, ownerPubKeyB64, switchPubKey2, 1, {
					description: 'Private v2',
					location: 'Test City',
					category: 'Test',
					publicize: false,
					link: ''
				});

				const publicSwitches = await redisClient.getPublicSwitches();
				const uids = publicSwitches.map(s => s.uid);

				expect(uids).toContain(v2uid1);
				expect(uids).not.toContain(v2uid2);
			});

			test('should return switches with only public fields', async () => {
				const personalKey = global.testUtils.createTestPersonalKey();
				const uid = global.testUtils.generateTestUUID();

				await redisClient.createSwitch(uid, personalKey,
					global.testUtils.createTestSwitchData({ publicize: true })
				);

				// V1 switches are ignored by getPublicSwitches()
				expect(await redisClient.getPublicSwitches()).toEqual([]);

				const owner = global.testUtils.createEd25519Keypair();
				const ownerPubKeyB64 = Buffer.from(owner.rawPublicKey).toString('base64url');
				const ownerId = deriveOwnerIdFromOwnerPubKeyB64Url(ownerPubKeyB64);
				const sw = global.testUtils.createEd25519Keypair();
				const switchPubKeyB64 = Buffer.from(sw.rawPublicKey).toString('base64url');
				const v2uid = deriveSwitchUidFromSwitchPubKeyB64Url(switchPubKeyB64);

				await redisClient.createSwitchV2(v2uid, ownerId, ownerPubKeyB64, switchPubKeyB64, 0, {
					description: 'Public v2',
					location: 'Test City',
					category: 'Test',
					publicize: true,
					link: '',
					iconUrl: 'https://example.com/icon.png',
					bannerUrl: 'https://example.com/banner.jpg'
				});

				const publicSwitches = await redisClient.getPublicSwitches();
				const publicSwitch = publicSwitches.find(s => s.uid === v2uid);

				expect(publicSwitch).toBeDefined();
				expect(publicSwitch.personalKey).toBeUndefined();
				expect(publicSwitch.uid).toBeDefined();
				expect(publicSwitch.description).toBe('Public v2');
				expect(publicSwitch.state).toBeDefined();
				expect(publicSwitch.iconUrl).toBe('https://example.com/icon.png');
				expect(publicSwitch.bannerUrl).toBe('https://example.com/banner.jpg');
			});
		});

		describe('incrementToggleCount', () => {
			test('should increment toggle count', async () => {
				const uid = global.testUtils.generateTestUUID();
				const personalKey = global.testUtils.createTestPersonalKey();

				await redisClient.createSwitch(uid, personalKey, global.testUtils.createTestSwitchData());

				await redisClient.incrementToggleCount(uid);
				await redisClient.incrementToggleCount(uid);

				const state = await redisClient.getSwitchState(uid);
				expect(state.toggleCount).toBe(2);
			});
		});
	});

	describe('personal key operations', () => {
		describe('storePersonalKey', () => {
			test('should store personal key', async () => {
				const personalKey = global.testUtils.createTestPersonalKey();

				const result = await redisClient.storePersonalKey(personalKey);

				expect(result.key).toBe(personalKey);
				expect(result.createdAt).toBeDefined();
				expect(result.lastUsed).toBeDefined();
			});

			test('should set expiry on personal key', async () => {
				const personalKey = global.testUtils.createTestPersonalKey();

				await redisClient.storePersonalKey(personalKey);

				const ttl = await redisClient.client.ttl(`key:${personalKey}`);
				expect(ttl).toBeGreaterThan(0);
				expect(ttl).toBeLessThanOrEqual(365 * 24 * 60 * 60); // 1 year
			});
		});

		describe('validatePersonalKey', () => {
			test('should validate existing personal key', async () => {
				const personalKey = global.testUtils.createTestPersonalKey();

				await redisClient.storePersonalKey(personalKey);

				const isValid = await redisClient.validatePersonalKey(personalKey);
				expect(isValid).toBe(true);
			});

			test('should reject non-existent personal key', async () => {
				const nonExistentKey = global.testUtils.createTestPersonalKey();

				const isValid = await redisClient.validatePersonalKey(nonExistentKey);
				expect(isValid).toBe(false);
			});

			test('should update last used timestamp', async () => {
				const personalKey = global.testUtils.createTestPersonalKey();

				await redisClient.storePersonalKey(personalKey);
				await global.testUtils.sleep(10); // Small delay

				await redisClient.validatePersonalKey(personalKey);

				const keyData = await redisClient.client.hGetAll(`key:${personalKey}`);
				expect(parseInt(keyData.lastUsed)).toBeGreaterThan(parseInt(keyData.createdAt));
			});
		});

		describe('deletePersonalKey', () => {
			test('should delete personal key and associated switches', async () => {
				const personalKey = global.testUtils.createTestPersonalKey();
				const uid = global.testUtils.generateTestUUID();

				await redisClient.storePersonalKey(personalKey);
				await redisClient.createSwitch(uid, personalKey, global.testUtils.createTestSwitchData());

				const deletedCount = await redisClient.deletePersonalKey(personalKey);

				expect(deletedCount).toBe(1);

				const keyExists = await redisClient.client.exists(`key:${personalKey}`);
				const switchExists = await redisClient.client.exists(`switch:${uid}`);

				expect(keyExists).toBe(0);
				expect(switchExists).toBe(0);
			});
		});
	});

	describe('pub/sub operations', () => {
		describe('publishSwitchUpdate', () => {
			test('should publish switch update', async () => {
				const uid = global.testUtils.generateTestUUID();

				// This test mainly ensures no errors are thrown
				await expect(redisClient.publishSwitchUpdate(uid, true)).resolves.not.toThrow();
			});
		});

		describe('subscribeSwitchUpdates', () => {
			test('should subscribe to switch updates', async () => {
				const uid = global.testUtils.generateTestUUID();
				const callback = jest.fn();

				// This test mainly ensures no errors are thrown
				await expect(redisClient.subscribeSwitchUpdates(uid, callback)).resolves.not.toThrow();
			});
		});
	});
});
