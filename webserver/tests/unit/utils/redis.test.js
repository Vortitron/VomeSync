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
				expect(result.personalKey).toBeUndefined();
				expect(result.ownerKeyId).toBe(redisClient.getPersonalKeyId(personalKey));
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
			expect(ttl).toBeLessThanOrEqual(90 * 24 * 60 * 60); // 90 days default
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

		describe('refreshSwitchTTL', () => {
		test('should refresh TTL on an existing switch', async () => {
			const uid = global.testUtils.generateTestUUID();
			const personalKey = global.testUtils.createTestPersonalKey();
			await redisClient.createSwitch(uid, personalKey, global.testUtils.createTestSwitchData());

			// Artificially lower the TTL
			await redisClient.client.expire(`switch:${uid}`, 60);
			const lowTtl = await redisClient.client.ttl(`switch:${uid}`);
			expect(lowTtl).toBeLessThanOrEqual(60);

			const result = await redisClient.refreshSwitchTTL(uid);
			expect(result).toBe(true);

			const newTtl = await redisClient.client.ttl(`switch:${uid}`);
			expect(newTtl).toBeGreaterThan(60);
		});

		test('should return false for non-existent switch', async () => {
			const result = await redisClient.refreshSwitchTTL('nonexistent_uid');
			expect(result).toBe(false);
		});

		test('should also refresh owner index TTL', async () => {
			const uid = 'vs_test_refresh_owner';
			const ownerId = 'owner-refresh-test';
			await redisClient.createSwitchV2(uid, ownerId, 'pubkey', 'switchpub', 0, global.testUtils.createTestSwitchData());

			const ownerKey = `owner:${ownerId}`;
			await redisClient.client.expire(ownerKey, 60);

			await redisClient.refreshSwitchTTL(uid);

			const ownerTtl = await redisClient.client.ttl(ownerKey);
			expect(ownerTtl).toBeGreaterThan(60);
		});
	});

	describe('getUserSwitchCounts', () => {
			test('should count total and public switches', async () => {
				const personalKey = global.testUtils.createTestPersonalKey();
				const uid1 = global.testUtils.generateTestUUID();
				const uid2 = global.testUtils.generateTestUUID();

				await redisClient.createSwitch(uid1, personalKey,
					global.testUtils.createTestSwitchData({ publicize: true })
				);
				await redisClient.createSwitch(uid2, personalKey,
					global.testUtils.createTestSwitchData({ publicize: false })
				);

				const counts = await redisClient.getUserSwitchCounts(personalKey);
				expect(counts.total).toBe(2);
				expect(counts.public).toBe(1);
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

			test('should apply listing overrides', async () => {
				const owner = global.testUtils.createEd25519Keypair();
				const ownerPubKeyB64 = Buffer.from(owner.rawPublicKey).toString('base64url');
				const ownerId = deriveOwnerIdFromOwnerPubKeyB64Url(ownerPubKeyB64);
				const sw = global.testUtils.createEd25519Keypair();
				const switchPubKeyB64 = Buffer.from(sw.rawPublicKey).toString('base64url');
				const uid = deriveSwitchUidFromSwitchPubKeyB64Url(switchPubKeyB64);

				await redisClient.createSwitchV2(uid, ownerId, ownerPubKeyB64, switchPubKeyB64, 0, {
					name: 'Original Name',
					description: 'Original Desc',
					location: 'Original City',
					category: 'Test',
					publicize: true,
					link: ''
				});

				await redisClient.setSwitchListingOverride(uid, {
					name: 'Listing Name',
					description: 'Listing Desc',
					category: 'Community'
				});

				const publicSwitches = await redisClient.getPublicSwitches();
				const listing = publicSwitches.find(s => s.uid === uid);
				expect(listing.name).toBe('Listing Name');
				expect(listing.description).toBe('Listing Desc');
				expect(listing.category).toBe('Community');

				const detail = await redisClient.getPublicSwitchDetail(uid);
				expect(detail.name).toBe('Listing Name');
				expect(detail.description).toBe('Listing Desc');
				expect(detail.category).toBe('Community');
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

	describe('api key expiry', () => {
		test('should treat expired api keys as invalid and remove them', async () => {
			const personalKey = global.testUtils.createTestPersonalKey();
			const keyData = await redisClient.createApiKey(personalKey, 'session', 60);

			// Force expiry without waiting
			await redisClient.client.hSet(
				redisClient._apiKeyRecordKey(keyData.apiKeyId),
				'expiresAt',
				`${Date.now() - 1000}`
			);

			const resolved = await redisClient.resolvePersonalKeyFromApiKey(keyData.apiKey);
			expect(resolved).toBeNull();

			const list = await redisClient.listApiKeys(personalKey);
			expect(list).toHaveLength(0);
		});
	});

	describe('v2 access key expiry', () => {
		test('should treat expired v2 access keys as invalid and remove them', async () => {
			const ownerId = 'owner-test';
			const uid = 'vs_test_uid';
			const created = await redisClient.createV2AccessKey(ownerId, uid, 'session', ['toggle'], 60);

			await redisClient.client.hSet(
				redisClient._apiKeyRecordKey(created.apiKeyId),
				'expiresAt',
				`${Date.now() - 1000}`
			);

			const resolved = await redisClient.resolveV2AccessKey(created.apiKey);
			expect(resolved).toBeNull();

			const list = await redisClient.listV2AccessKeys(ownerId, uid);
			expect(list).toHaveLength(0);
		});
	});

	describe('admin helpers', () => {
		test('should block and unblock owner IDs', async () => {
			const ownerId = 'owner-test-123';
			const blockedBefore = await redisClient.isOwnerBlocked(ownerId);
			expect(blockedBefore).toBe(false);

			await redisClient.blockOwnerId(ownerId);
			const blockedAfter = await redisClient.isOwnerBlocked(ownerId);
			expect(blockedAfter).toBe(true);

			await redisClient.unblockOwnerId(ownerId);
			const blockedFinal = await redisClient.isOwnerBlocked(ownerId);
			expect(blockedFinal).toBe(false);
		});

		test('should set and clear switch redirects', async () => {
			const fromUid = 'vs_test_redirect_old';
			const toUid = 'vs_test_redirect_new';
			const reason = 'Migrated';

			const created = await redisClient.setSwitchRedirect(fromUid, toUid, reason);
			expect(created.toUid).toBe(toUid);

			const redirect = await redisClient.getSwitchRedirect(fromUid);
			expect(redirect.toUid).toBe(toUid);
			expect(redirect.reason).toBe(reason);

			await redisClient.clearSwitchRedirect(fromUid);
			const cleared = await redisClient.getSwitchRedirect(fromUid);
			expect(cleared).toBeNull();
		});
	});

	describe('personal key operations', () => {
		describe('storePersonalKey', () => {
			test('should store personal key', async () => {
				const personalKey = global.testUtils.createTestPersonalKey();

				const result = await redisClient.storePersonalKey(personalKey);

				expect(result.personalKeyId).toBe(redisClient.getPersonalKeyId(personalKey));
				expect(result.createdAt).toBeDefined();
				expect(result.lastUsed).toBeDefined();
			});

			test('should set expiry on personal key', async () => {
				const personalKey = global.testUtils.createTestPersonalKey();

				await redisClient.storePersonalKey(personalKey);

				const personalKeyId = redisClient.getPersonalKeyId(personalKey);
				const ttl = await redisClient.client.ttl(`pkey_h:${personalKeyId}`);
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

				const personalKeyId = redisClient.getPersonalKeyId(personalKey);
				const keyData = await redisClient.client.hGetAll(`pkey_h:${personalKeyId}`);
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

				const personalKeyId = redisClient.getPersonalKeyId(personalKey);
				const keyExists = await redisClient.client.exists(`pkey_h:${personalKeyId}`);
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
