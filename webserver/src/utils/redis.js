const redis = require('redis');
const crypto = require('crypto');
const config = require('../config/config');
const logger = require('./logger');
let testRedisServer = null;

const DEFAULT_REDIS_CONNECT_MAX_ATTEMPTS = 30;
const DEFAULT_REDIS_CONNECT_RETRY_BASE_MS = 1000;
const MAX_REDIS_CONNECT_RETRY_MS = 5000;

const SECRET_ID_HASH_ALGO = 'sha256';
const SECRET_ID_HEX_LENGTH = 64;

const PERSONAL_KEY_TTL_SECONDS = 365 * 24 * 60 * 60; // 1 year
const DEFAULT_SWITCH_TTL_DAYS = 90;
const USER_INDEX_TTL_SECONDS = DEFAULT_SWITCH_TTL_DAYS * 24 * 60 * 60;
const SWITCH_TTL_SECONDS = parseInt(process.env.SWITCH_TTL_DAYS, 10) > 0
	? parseInt(process.env.SWITCH_TTL_DAYS, 10) * 24 * 60 * 60
	: DEFAULT_SWITCH_TTL_DAYS * 24 * 60 * 60;

const BLOCKED_OWNER_IDS_SET = 'blocked:owner_ids';
const BLOCKED_PERSONAL_KEY_IDS_SET = 'blocked:personal_key_ids';
const BLOCKED_API_KEY_IDS_SET = 'blocked:api_key_ids';
const SWITCH_REDIRECTS_HASH = 'switch_redirects';
const SWITCH_LISTING_OVERRIDES_PREFIX = 'switch_override:';
const ALL_SWITCHES_SORTED_SET = 'all_switches';
const DAILY_SWITCH_STATS_PREFIX = 'daily_switches:';

function _sleep(ms) {
	return new Promise((resolve) => setTimeout(resolve, ms));
}

class RedisClient {
	constructor() {
		this.client = null;
		this.pubClient = null;
		this.subClient = null;
		this.isConnected = false;
	}

	_isSecretIdHex(value) {
		return typeof value === 'string' && /^[0-9a-f]+$/i.test(value) && value.length === SECRET_ID_HEX_LENGTH;
	}

	_deriveSecretId(kind, secret) {
		if (typeof secret !== 'string' || secret.length === 0) {
			return '';
		}
		const hashSecret = config?.security?.keyHashSecret || config?.security?.jwtSecret || '';
		if (!hashSecret) {
			throw new Error('Missing KEY_HASH_SECRET/JWT_SECRET; cannot derive secret IDs');
		}
		return crypto
			.createHmac(SECRET_ID_HASH_ALGO, hashSecret)
			.update(`${kind}:${secret}`, 'utf8')
			.digest('hex');
	}

	/**
	 * Derive the secret ID using the OLD hash secret (rotation support).
	 * Returns '' if no old secret is configured or if old === current.
	 */
	_deriveSecretIdOld(kind, secret) {
		const oldSecret = config?.security?.keyHashSecretOld || '';
		if (!oldSecret || typeof secret !== 'string' || secret.length === 0) {
			return '';
		}
		const oldId = crypto
			.createHmac(SECRET_ID_HASH_ALGO, oldSecret)
			.update(`${kind}:${secret}`, 'utf8')
			.digest('hex');
		// Don't return same ID as current secret (no rotation happened)
		const currentId = this._deriveSecretId(kind, secret);
		return oldId !== currentId ? oldId : '';
	}

	/**
	 * Try to find a Redis hash record stored under the OLD hash-secret-derived ID.
	 * If found, migrates (renames) the record to the current-secret ID.
	 * @param {string} kind – 'personalKey' | 'apiKey' | 'sessionToken'
	 * @param {string} rawSecret – the raw key/token value
	 * @param {function} recordKeyFn – e.g. this._apiKeyRecordKey.bind(this)
	 * @returns {Promise<{id: string, data: object}|null>}
	 */
	async _tryOldSecretFallback(kind, rawSecret, recordKeyFn) {
		const oldId = this._deriveSecretIdOld(kind, rawSecret);
		if (!oldId) return null;

		const oldRecordKey = recordKeyFn(oldId);
		const data = await this.client.hGetAll(oldRecordKey);
		if (!data || Object.keys(data).length === 0) return null;

		const currentId = this._deriveSecretId(kind, rawSecret);
		const newRecordKey = recordKeyFn(currentId);

		try {
			// RENAME preserves TTL; atomically move the record to the new key
			await this.client.rename(oldRecordKey, newRecordKey);
			// Update the stored ID field inside the hash if present
			if (data.personalKeyId) {
				await this.client.hSet(newRecordKey, 'personalKeyId', currentId);
			} else if (data.apiKeyId) {
				await this.client.hSet(newRecordKey, 'apiKeyId', currentId);
			} else if (data.tokenId) {
				await this.client.hSet(newRecordKey, 'tokenId', currentId);
			}
			logger.info(`Migrated ${kind} record from old hash secret (${oldId.substring(0, 8)}… → ${currentId.substring(0, 8)}…)`);
		} catch (err) {
			logger.warn(`Old-secret migration failed for ${kind}: ${err.message}`);
		}

		return { id: currentId, data };
	}

	_getPersonalKeyId(personalKeyOrId) {
		if (this._isSecretIdHex(personalKeyOrId)) {
			return personalKeyOrId;
		}
		return this._deriveSecretId('personalKey', personalKeyOrId);
	}

	_getApiKeyId(apiKeyOrId) {
		if (this._isSecretIdHex(apiKeyOrId)) {
			return apiKeyOrId;
		}
		return this._deriveSecretId('apiKey', apiKeyOrId);
	}

	_getSessionTokenId(tokenOrId) {
		if (this._isSecretIdHex(tokenOrId)) {
			return tokenOrId;
		}
		return this._deriveSecretId('sessionToken', tokenOrId);
	}

	_switchOverrideKey(uid) {
		return `${SWITCH_LISTING_OVERRIDES_PREFIX}${uid}`;
	}

	getPersonalKeyId(personalKeyOrId) {
		return this._getPersonalKeyId(personalKeyOrId);
	}

	getApiKeyId(apiKeyOrId) {
		return this._getApiKeyId(apiKeyOrId);
	}

	getSessionTokenId(tokenOrId) {
		return this._getSessionTokenId(tokenOrId);
	}

	async isOwnerBlocked(ownerId) {
		if (!ownerId) return false;
		return await this.client.sIsMember(BLOCKED_OWNER_IDS_SET, ownerId);
	}

	async blockOwnerId(ownerId) {
		if (!ownerId) return false;
		await this.client.sAdd(BLOCKED_OWNER_IDS_SET, ownerId);
		return true;
	}

	async unblockOwnerId(ownerId) {
		if (!ownerId) return false;
		await this.client.sRem(BLOCKED_OWNER_IDS_SET, ownerId);
		return true;
	}

	async isPersonalKeyBlocked(personalKeyOrId) {
		const personalKeyId = this._getPersonalKeyId(personalKeyOrId);
		if (!personalKeyId) return false;
		return await this.client.sIsMember(BLOCKED_PERSONAL_KEY_IDS_SET, personalKeyId);
	}

	async blockPersonalKeyId(personalKeyOrId) {
		const personalKeyId = this._getPersonalKeyId(personalKeyOrId);
		if (!personalKeyId) return false;
		await this.client.sAdd(BLOCKED_PERSONAL_KEY_IDS_SET, personalKeyId);
		return true;
	}

	async unblockPersonalKeyId(personalKeyOrId) {
		const personalKeyId = this._getPersonalKeyId(personalKeyOrId);
		if (!personalKeyId) return false;
		await this.client.sRem(BLOCKED_PERSONAL_KEY_IDS_SET, personalKeyId);
		return true;
	}

	async isApiKeyBlocked(apiKeyOrId) {
		const apiKeyId = this._getApiKeyId(apiKeyOrId);
		if (!apiKeyId) return false;
		return await this.client.sIsMember(BLOCKED_API_KEY_IDS_SET, apiKeyId);
	}

	async blockApiKeyId(apiKeyOrId) {
		const apiKeyId = this._getApiKeyId(apiKeyOrId);
		if (!apiKeyId) return false;
		await this.client.sAdd(BLOCKED_API_KEY_IDS_SET, apiKeyId);
		return true;
	}

	async unblockApiKeyId(apiKeyOrId) {
		const apiKeyId = this._getApiKeyId(apiKeyOrId);
		if (!apiKeyId) return false;
		await this.client.sRem(BLOCKED_API_KEY_IDS_SET, apiKeyId);
		return true;
	}

	async getSwitchRedirect(uid) {
		if (!uid) return null;
		const raw = await this.client.hGet(SWITCH_REDIRECTS_HASH, uid);
		if (!raw) return null;
		try {
			const parsed = JSON.parse(raw);
			if (parsed && typeof parsed.toUid === 'string' && parsed.toUid) {
				return parsed;
			}
		} catch {
			// ignore
		}
		return null;
	}

	async setSwitchRedirect(fromUid, toUid, reason = '') {
		if (!fromUid || !toUid) return null;
		const payload = {
			toUid,
			reason: reason || '',
			updatedAt: Date.now()
		};
		await this.client.hSet(SWITCH_REDIRECTS_HASH, fromUid, JSON.stringify(payload));
		return payload;
	}

	async clearSwitchRedirect(fromUid) {
		if (!fromUid) return false;
		await this.client.hDel(SWITCH_REDIRECTS_HASH, fromUid);
		return true;
	}

	async connect() {
		if (this.isConnected) {
			return;
		}

		// Resolve connection settings (supports in-memory Redis for tests)
		let host = config.redis.host;
		let port = config.redis.port;
		let password = config.redis.password;
		let database = config.redis.db;

		const isTestEnv = process.env.NODE_ENV === 'test';

		// Prefer global test redis (if provided by test harness)
		if (isTestEnv && global.__REDIS_HOST__ && global.__REDIS_PORT__) {
			host = global.__REDIS_HOST__;
			port = global.__REDIS_PORT__;
			password = undefined;
			database = 0;
		} else if (isTestEnv) {
			// Start an in-memory Redis for tests
			// Lazy require to avoid adding prod dep
			// eslint-disable-next-line global-require
			const { RedisMemoryServer } = require('redis-memory-server');
			testRedisServer = await RedisMemoryServer.create({
				instance: { port: 0 }
			});
			host = await testRedisServer.getHost();
			port = await testRedisServer.getPort();
			password = undefined;
			database = 0;

			global.__REDIS_HOST__ = host;
			global.__REDIS_PORT__ = port;
			global.__REDIS_SERVER__ = testRedisServer;

			logger.info(`Started in-memory Redis for tests at ${host}:${port}`);
		}

		const maxAttempts = isTestEnv
			? 1
			: (Number.parseInt(process.env.REDIS_CONNECT_MAX_ATTEMPTS || '', 10) || DEFAULT_REDIS_CONNECT_MAX_ATTEMPTS);

		let lastError = null;

		for (let attempt = 1; attempt <= maxAttempts; attempt += 1) {
			try {
				// Main Redis client for data operations
				this.client = redis.createClient({
					socket: { host, port },
					password: password || undefined,
					database
				});

				// Pub/Sub clients (Redis requires separate clients for pub/sub)
				this.pubClient = redis.createClient({
					socket: { host, port },
					password: password || undefined,
					database
				});

				this.subClient = redis.createClient({
					socket: { host, port },
					password: password || undefined,
					database
				});

				// Connect all clients
				await Promise.all([
					this.client.connect(),
					this.pubClient.connect(),
					this.subClient.connect()
				]);

				// Set up error handlers
				this.client.on('error', (err) => {
					logger.error('Redis client error:', err);
					this.isConnected = false;
				});

				this.pubClient.on('error', (err) => {
					logger.error('Redis pub client error:', err);
				});

				this.subClient.on('error', (err) => {
					logger.error('Redis sub client error:', err);
				});

				this.isConnected = true;
				logger.info('Redis clients connected successfully');
				return;

			} catch (error) {
				lastError = error;
				this.isConnected = false;

				try { this.client?.disconnect(); } catch (_err) { /* ignore */ }
				try { this.pubClient?.disconnect(); } catch (_err) { /* ignore */ }
				try { this.subClient?.disconnect(); } catch (_err) { /* ignore */ }
				this.client = null;
				this.pubClient = null;
				this.subClient = null;

				if (attempt >= maxAttempts) {
					break;
				}

				const retryMs = Math.min(DEFAULT_REDIS_CONNECT_RETRY_BASE_MS * attempt, MAX_REDIS_CONNECT_RETRY_MS);
				logger.warn(
					'Failed to connect to Redis (attempt %d/%d). Retrying in %dms. Error: %s',
					attempt,
					maxAttempts,
					retryMs,
					error && error.message ? error.message : String(error)
				);
				await _sleep(retryMs);
			}
		}

		logger.error('Failed to connect to Redis:', lastError);
		throw lastError;
	}

	async disconnect() {
		try {
			if (this.client) await this.client.quit();
			if (this.pubClient) await this.pubClient.quit();
			if (this.subClient) await this.subClient.quit();
			this.isConnected = false;
			logger.info('Redis clients disconnected');

			if (testRedisServer) {
				await testRedisServer.stop();
				testRedisServer = null;
				global.__REDIS_SERVER__ = undefined;
				logger.info('In-memory Redis server stopped');
			}
		} catch (error) {
			logger.error('Error disconnecting from Redis:', error);
		}
	}

	// Switch state operations
	async setSwitchState(uid, state, metadata = {}) {
		const switchData = {
			state: state ? 'on' : 'off',
			lastToggled: Date.now(),
			...metadata
		};

		const key = `switch:${uid}`;
		await this.client.hSet(key, this._serializeHash(switchData));

		// Set expiry (30 days for inactive switches)
		await this.client.expire(key, SWITCH_TTL_SECONDS);

		return switchData;
	}

	async refreshSwitchTTL(uid) {
		const key = `switch:${uid}`;
		const exists = await this.client.exists(key);
		if (!exists) return false;
		await this.client.expire(key, SWITCH_TTL_SECONDS);
		const ownerField = await this.client.hGet(key, 'ownerId');
		if (ownerField) {
			await this.client.expire(`owner:${ownerField}`, USER_INDEX_TTL_SECONDS);
		}
		const ownerKeyField = await this.client.hGet(key, 'ownerKeyId');
		if (ownerKeyField) {
			await this.client.expire(`user:${ownerKeyField}:switches`, USER_INDEX_TTL_SECONDS);
		}
		return true;
	}

	async getSwitchState(uid) {
		const key = `switch:${uid}`;
		const raw = await this.client.hGetAll(key);

		if (!raw || Object.keys(raw).length === 0) {
			return null;
		}

		const data = this._deserializeHash(raw);

		return {
			...data,
			uid: data.uid,
			state: data.state === 'on',
			publicize: Boolean(data.publicize),
			toggleCount: Number(data.toggleCount) || 0,
			createdAt: Number(data.createdAt) || 0,
			lastToggled: Number(data.lastToggled) || 0,
			link: data.link || '',
			iconUrl: data.iconUrl || '',
			bannerUrl: data.bannerUrl || '',
			params: (data.params && typeof data.params === 'object') ? data.params : undefined
		};
	}

	async createSwitch(uid, personalKey, switchConfig) {
		const ownerKeyId = this._getPersonalKeyId(personalKey);
		const key = `switch:${uid}`;
		const switchData = {
			uid,
			ownerKeyId,
			state: 'off',
			createdAt: Date.now(),
			lastToggled: 0,
			name: switchConfig.name || '',
			description: switchConfig.description || '',
			location: switchConfig.location || '',
			category: switchConfig.category || '',
			publicize: switchConfig.publicize || false,
			link: switchConfig.link || '',
			toggleCount: 0
		};

		await this.client.hSet(key, this._serializeHash(switchData));
		await this.client.expire(key, SWITCH_TTL_SECONDS); // 30 day expiry

		// Add to personal key index
		const userKey = `user:${ownerKeyId}:switches`;
		await this.client.sAdd(userKey, uid);
		await this.client.expire(userKey, USER_INDEX_TTL_SECONDS);

		// Add to public index if publicized
		if (switchConfig.publicize) {
			await this.client.sAdd('public_switches', uid);
		}

		// Track in global index for stats
		await this.recordSwitchCreation(uid, switchData.createdAt);

		return switchData;
	}

	async createSwitchV2(uid, ownerId, ownerPubKey, switchPubKey, index, switchConfig) {
		const key = `switch:${uid}`;
		const switchData = {
			uid,
			ownerId,
			ownerPubKey,
			switchPubKey,
			authVersion: 2,
			index,
			state: 'off',
			createdAt: Date.now(),
			lastToggled: 0,
			name: switchConfig.name || '',
			description: switchConfig.description || '',
			location: switchConfig.location || '',
			category: switchConfig.category || '',
			publicize: switchConfig.publicize || false,
			link: switchConfig.link || '',
			iconUrl: switchConfig.iconUrl || '',
			bannerUrl: switchConfig.bannerUrl || '',
			toggleCount: 0,
			params: {}
		};

		await this.client.hSet(key, this._serializeHash(switchData));
		await this.client.expire(key, SWITCH_TTL_SECONDS); // 30 day expiry

		// Add to owner index
		const ownerKey = `owner:${ownerId}`;
		await this.client.sAdd(ownerKey, uid);
		await this.client.expire(ownerKey, USER_INDEX_TTL_SECONDS);

		// Add to public index if publicized
		if (switchConfig.publicize) {
			await this.client.sAdd('public_switches', uid);
		}

		// Track in global index for stats
		await this.recordSwitchCreation(uid, switchData.createdAt);

		return switchData;
	}

	_serializeHash(data) {
		// Ensure Redis hash values are strings/numbers
		const serialized = {};
		Object.entries(data).forEach(([field, value]) => {
			if (value === undefined || value === null) {
				return;
			}
			if (typeof value === 'object') {
				serialized[field] = JSON.stringify(value);
			} else if (typeof value === 'boolean') {
				serialized[field] = value ? 'true' : 'false';
			} else {
				serialized[field] = `${value}`;
			}
		});
		return serialized;
	}

	_deserializeHash(data) {
		// Convert Redis hash string values back to their original types
		const deserialized = {};
		Object.entries(data).forEach(([field, value]) => {
			if (value === undefined || value === null || value === '') {
				deserialized[field] = value;
				return;
			}
			// Handle boolean strings
			if (value === 'true') {
				deserialized[field] = true;
			} else if (value === 'false') {
				deserialized[field] = false;
			}
			// Handle numeric strings
			else if (/^-?\d+$/.test(value)) {
				deserialized[field] = parseInt(value, 10);
			} else if (/^-?\d+\.\d+$/.test(value)) {
				deserialized[field] = parseFloat(value);
			}
			// Handle JSON strings (objects/arrays)
			else if ((value.startsWith('{') && value.endsWith('}')) || 
					 (value.startsWith('[') && value.endsWith(']'))) {
				try {
					deserialized[field] = JSON.parse(value);
				} catch {
					deserialized[field] = value; // Keep as string if JSON parse fails
				}
			}
			// Otherwise keep as string
			else {
				deserialized[field] = value;
			}
		});
		return deserialized;
	}

	_serializePairs(data) {
		const hash = this._serializeHash(data);
		return Object.entries(hash).flatMap(([k, v]) => [k, v]);
	}

	async getUserSwitches(personalKey) {
		const ownerKeyId = this._getPersonalKeyId(personalKey);
		const userKey = `user:${ownerKeyId}:switches`;
		const switchUIDs = await this.client.sMembers(userKey);

		const switches = [];
		for (const uid of switchUIDs) {
			const switchData = await this.getSwitchState(uid);
			if (switchData) {
				switches.push(switchData);
				this.refreshSwitchTTL(uid).catch(() => {});
			}
		}

		await this.client.expire(userKey, USER_INDEX_TTL_SECONDS);
		return switches;
	}

	async getOwnerSwitches(ownerId) {
		const ownerKey = `owner:${ownerId}`;
		const switchUIDs = await this.client.sMembers(ownerKey);

		const switches = [];
		for (const uid of switchUIDs) {
			const switchData = await this.getSwitchState(uid);
			if (switchData) {
				switches.push(switchData);
				this.refreshSwitchTTL(uid).catch(() => {});
			}
		}

		await this.client.expire(ownerKey, USER_INDEX_TTL_SECONDS);
		return switches;
	}

	async _countSwitchesInSet(setKey) {
		if (!setKey) {
			return { total: 0, public: 0 };
		}
		const switchUIDs = await this.client.sMembers(setKey);
		if (!switchUIDs || switchUIDs.length === 0) {
			return { total: 0, public: 0 };
		}
		const pipeline = this.client.multi();
		for (const uid of switchUIDs) {
			pipeline.hGet(`switch:${uid}`, 'publicize');
		}
		const results = await pipeline.exec();
		let publicCount = 0;
		for (const result of results) {
			const value = Array.isArray(result) ? result[1] : result;
			if (value === 'true' || value === true) {
				publicCount += 1;
			}
		}
		return { total: switchUIDs.length, public: publicCount };
	}

	async getUserSwitchCounts(personalKeyOrId) {
		const ownerKeyId = this._getPersonalKeyId(personalKeyOrId);
		if (!ownerKeyId) {
			return { total: 0, public: 0 };
		}
		return this._countSwitchesInSet(`user:${ownerKeyId}:switches`);
	}

	async getOwnerSwitchCounts(ownerId) {
		if (!ownerId) {
			return { total: 0, public: 0 };
		}
		return this._countSwitchesInSet(`owner:${ownerId}`);
	}

	async getSwitchListingOverride(uid) {
		if (!uid) {
			return null;
		}
		const data = await this.client.hGetAll(this._switchOverrideKey(uid));
		if (!data || Object.keys(data).length === 0) {
			return null;
		}
		return this._deserializeHash(data);
	}

	async setSwitchListingOverride(uid, overrides) {
		if (!uid || !overrides || typeof overrides !== 'object') {
			return null;
		}
		const payload = {
			...overrides,
			updatedAt: Date.now()
		};
		await this.client.hSet(this._switchOverrideKey(uid), this._serializeHash(payload));
		await this.client.expire(this._switchOverrideKey(uid), SWITCH_TTL_SECONDS);
		return this.getSwitchListingOverride(uid);
	}

	async clearSwitchListingOverride(uid) {
		if (!uid) {
			return false;
		}
		await this.client.del(this._switchOverrideKey(uid));
		return true;
	}

	_applyListingOverride(switchData, override) {
		if (!switchData || !override) {
			return switchData;
		}
		const fields = ['name', 'description', 'location', 'category', 'link', 'iconUrl', 'bannerUrl'];
		const merged = { ...switchData };
		for (const field of fields) {
			if (Object.prototype.hasOwnProperty.call(override, field)) {
				merged[field] = override[field];
			}
		}
		return merged;
	}

	async getPublicSwitches() {
		const publicUIDs = await this.client.sMembers('public_switches');
		const redirectMap = await this.client.hGetAll(SWITCH_REDIRECTS_HASH);
		const blockedOwners = new Set(await this.client.sMembers(BLOCKED_OWNER_IDS_SET));
		const switches = [];

		for (const uid of publicUIDs) {
			if (redirectMap && redirectMap[uid]) {
				continue;
			}
			const switchData = await this.getSwitchState(uid);
			if (!switchData || !switchData.publicize) {
				continue;
			}
			// Ignore legacy v1 switches (UUID + personalKey auth). Public directory should be v2-only.
			if (switchData.authVersion !== 2) {
				continue;
			}
			if (switchData.ownerId && blockedOwners.has(switchData.ownerId)) {
				continue;
			}
			const override = await this.getSwitchListingOverride(uid);
			const listingData = this._applyListingOverride(switchData, override);
			const userCount = await this.getUserCount(uid);
			const ownerProfileUrl = '';
			switches.push({
				uid: listingData.uid,
				name: listingData.name || '',
				description: listingData.description,
				location: listingData.location,
				category: listingData.category,
				state: listingData.state,
				lastToggled: listingData.lastToggled,
				toggleCount: listingData.toggleCount || 0,
				userCount,
				link: listingData.link || '',
				iconUrl: listingData.iconUrl || '',
				bannerUrl: listingData.bannerUrl || '',
				ownerProfileUrl
			});
		}

		return switches;
	}

	// Analytics operations
	async incrementToggleCount(uid) {
		const key = `switch:${uid}`;
		return this.client.hIncrBy(key, 'toggleCount', 1);
	}

	async recordUserInteraction(uid, actorId) {
		if (!actorId) {
			return;
		}
		const key = `switch:${uid}:users`;
		await this.client.sAdd(key, actorId);
		await this.client.expire(key, SWITCH_TTL_SECONDS);
	}

	async claimV2Nonce(scopeId, nonce, ttlMs = 10 * 60 * 1000) {
		if (!scopeId || !nonce) {
			return false;
		}
		const key = `nonce:v2:${scopeId}:${nonce}`;
		const result = await this.client.set(key, '1', { NX: true, PX: ttlMs });
		return result === 'OK';
	}

	async getUserCount(uid) {
		const key = `switch:${uid}:users`;
		const count = await this.client.sCard(key);
		return count || 0;
	}

	async appendEvent(uid, event, maxEvents = 200) {
		const key = `switch:${uid}:events`;
		await this.client.lPush(key, JSON.stringify(event));
		await this.client.lTrim(key, 0, maxEvents - 1);
		await this.client.expire(key, SWITCH_TTL_SECONDS);
	}

	async getEvents(uid, limit = 50) {
		const key = `switch:${uid}:events`;
		const rows = await this.client.lRange(key, 0, limit - 1);
		return rows.map((row) => {
			try {
				return JSON.parse(row);
			} catch (error) {
				return null;
			}
		}).filter(Boolean);
	}

	async addComment(uid, commentData) {
		const event = {
			...commentData,
			type: 'comment',
			timestamp: commentData.timestamp || Date.now()
		};
		await this.appendEvent(uid, event);
	}

	// Pub/Sub operations for real-time updates
	async publishSwitchUpdate(uid, state, params = undefined) {
		const channel = `switch_updates:${uid}`;
		const payload = {
			uid,
			state,
			timestamp: Date.now()
		};
		if (params && typeof params === 'object' && Object.keys(params).length > 0) {
			payload.params = params;
		}
		const message = JSON.stringify(payload);

		await this.pubClient.publish(channel, message);
	}

	async subscribeSwitchUpdates(uid, callback) {
		const channel = `switch_updates:${uid}`;

		await this.subClient.subscribe(channel, (message) => {
			try {
				const data = JSON.parse(message);
				callback(data);
			} catch (error) {
				logger.error('Error parsing switch update message:', error);
			}
		});
	}

	async unsubscribeSwitchUpdates(uid) {
		const channel = `switch_updates:${uid}`;
		await this.subClient.unsubscribe(channel);
	}

	// Personal key management
	_personalKeyRecordKey(personalKeyId) {
		return `pkey_h:${personalKeyId}`;
	}

	_userSwitchIndexKey(personalKeyId) {
		return `user:${personalKeyId}:switches`;
	}

	_userApiKeyIndexKey(personalKeyId) {
		return `user:${personalKeyId}:api_keys`;
	}

	_apiKeyRecordKey(apiKeyId) {
		return `apikey_h:${apiKeyId}`;
	}

	_sessionTokenRecordKey(tokenId) {
		return `session_token_h:${tokenId}`;
	}

	async storePersonalKey(personalKey) {
		const personalKeyId = this._getPersonalKeyId(personalKey);
		const keyData = {
			personalKeyId,
			createdAt: Date.now(),
			lastUsed: Date.now()
		};

		await this.client.hSet(this._personalKeyRecordKey(personalKeyId), this._serializeHash(keyData));
		await this.client.expire(this._personalKeyRecordKey(personalKeyId), PERSONAL_KEY_TTL_SECONDS); // 1 year expiry

		return keyData;
	}

	async validatePersonalKey(personalKey) {
		const personalKeyId = this._getPersonalKeyId(personalKey);
		const recordKey = this._personalKeyRecordKey(personalKeyId);

		const keyData = await this.client.hGetAll(recordKey);
		if (keyData && Object.keys(keyData).length > 0) {
			// Update last used timestamp
			await this.client.hSet(recordKey, 'lastUsed', `${Date.now()}`);
			return true;
		}

		// Old hash-secret fallback: record stored under previous KEY_HASH_SECRET
		const oldResult = await this._tryOldSecretFallback(
			'personalKey', personalKey, this._personalKeyRecordKey.bind(this)
		);
		if (oldResult) {
			await this.client.hSet(this._personalKeyRecordKey(personalKeyId), 'lastUsed', `${Date.now()}`);
			return true;
		}

		// Legacy fallback: key:<personalKey> (plaintext) -> migrate on first validation
		const legacyKey = `key:${personalKey}`;
		const legacyRaw = await this.client.hGetAll(legacyKey);
		if (!legacyRaw || Object.keys(legacyRaw).length === 0) {
			return false;
		}

		try {
			await this._migrateLegacyPersonalKey(personalKey, personalKeyId, legacyRaw);
		} catch (error) {
			// Non-fatal: validation should still succeed, but we log without exposing the secret.
			logger.warn('Legacy personal key migration failed for %s: %s', personalKeyId.substring(0, 8), error && error.message ? error.message : String(error));
		}

		// Update last used timestamp on the new record (best effort)
		try {
			await this.client.hSet(recordKey, 'lastUsed', `${Date.now()}`);
		} catch (_err) { /* ignore */ }

		return true;
	}

	async _migrateLegacyPersonalKey(personalKey, personalKeyId, legacyRaw) {
		if (!personalKey || !personalKeyId || !legacyRaw) {
			return;
		}

		const legacyKey = `key:${personalKey}`;
		const legacyTtl = await this.client.ttl(legacyKey);
		const ttlSeconds = legacyTtl > 0 ? legacyTtl : PERSONAL_KEY_TTL_SECONDS;

		const legacy = this._deserializeHash(legacyRaw);
		const recordKey = this._personalKeyRecordKey(personalKeyId);
		const migrated = {
			personalKeyId,
			createdAt: Number(legacy.createdAt) || Date.now(),
			lastUsed: Number(legacy.lastUsed) || Date.now(),
			...(typeof legacy.profileUrl === 'string' ? { profileUrl: legacy.profileUrl } : {}),
			...(typeof legacy.lastUpdated === 'number' ? { lastUpdated: legacy.lastUpdated } : {})
		};

		await this.client.hSet(recordKey, this._serializeHash(migrated));
		await this.client.expire(recordKey, ttlSeconds);

		// Migrate user switches index: user:<personalKey> -> user:<personalKeyId>:switches
		const legacyUserKey = `user:${personalKey}`;
		const newUserKey = this._userSwitchIndexKey(personalKeyId);
		const userTtl = await this.client.ttl(legacyUserKey);
		const userTtlSeconds = userTtl > 0 ? userTtl : USER_INDEX_TTL_SECONDS;

		const switchUIDs = await this.client.sMembers(legacyUserKey);
		if (Array.isArray(switchUIDs) && switchUIDs.length > 0) {
			await this.client.sAdd(newUserKey, switchUIDs);
			await this.client.expire(newUserKey, userTtlSeconds);

			for (const uid of switchUIDs) {
				const switchKey = `switch:${uid}`;
				try {
					await this.client.hSet(switchKey, this._serializeHash({ ownerKeyId: personalKeyId }));
					await this.client.hDel(switchKey, 'personalKey');
				} catch (_err) { /* ignore */ }

				// Remove plaintext key from user-count sets (best effort)
				try {
					const usersKey = `switch:${uid}:users`;
					await this.client.sRem(usersKey, personalKey);
					await this.client.sAdd(usersKey, personalKeyId);
				} catch (_err) { /* ignore */ }
			}
		}

		// Migrate v1 api key index: user:<personalKey>:api_keys -> user:<personalKeyId>:api_keys
		const legacyApiIndexKey = `user:${personalKey}:api_keys`;
		const newApiIndexKey = this._userApiKeyIndexKey(personalKeyId);
		const legacyApiTtl = await this.client.ttl(legacyApiIndexKey);
		const legacyApiTtlSeconds = legacyApiTtl > 0 ? legacyApiTtl : USER_INDEX_TTL_SECONDS;

		const legacyApiKeys = await this.client.sMembers(legacyApiIndexKey);
		if (Array.isArray(legacyApiKeys) && legacyApiKeys.length > 0) {
			for (const apiKey of legacyApiKeys) {
				const legacyApiKey = `apikey:${apiKey}`;
				const legacyApiRaw = await this.client.hGetAll(legacyApiKey);
				if (!legacyApiRaw || Object.keys(legacyApiRaw).length === 0) {
					continue;
				}

				const apiKeyId = this._getApiKeyId(apiKey);
				const parsed = this._deserializeHash(legacyApiRaw);
				// Skip non-v1 keys in this index (defence in depth)
				if (parsed && parsed.type && parsed.type !== 'api_key') {
					continue;
				}

				await this.client.hSet(this._apiKeyRecordKey(apiKeyId), this._serializeHash({
					type: 'api_key',
					apiKeyId,
					personalKeyId,
					name: parsed.name || '',
					createdAt: Number(parsed.createdAt) || Date.now(),
					lastUsed: Number(parsed.lastUsed) || 0,
					revoked: Boolean(parsed.revoked)
				}));

				await this.client.sAdd(newApiIndexKey, apiKeyId);
				await this.client.del(legacyApiKey);
			}
			await this.client.expire(newApiIndexKey, legacyApiTtlSeconds);
		}

		// Cleanup legacy structures (best effort)
		try { await this.client.del(legacyApiIndexKey); } catch (_err) { /* ignore */ }
		try { await this.client.del(legacyUserKey); } catch (_err) { /* ignore */ }
		try { await this.client.del(legacyKey); } catch (_err) { /* ignore */ }
	}

	async _migrateLegacyApiKey(apiKey, apiKeyId, legacyRaw) {
		if (!apiKey || !apiKeyId || !legacyRaw) {
			return;
		}
		const legacy = this._deserializeHash(legacyRaw);
		if (legacy && legacy.type && legacy.type !== 'api_key') {
			return;
		}
		const personalKey = legacy.personalKey;
		if (!personalKey) {
			return;
		}

		const personalKeyId = this._getPersonalKeyId(personalKey);

		// If we still have the plaintext personal key record, migrate the whole user (covers this API key too).
		try {
			const legacyPkRaw = await this.client.hGetAll(`key:${personalKey}`);
			if (legacyPkRaw && Object.keys(legacyPkRaw).length > 0) {
				await this._migrateLegacyPersonalKey(personalKey, personalKeyId, legacyPkRaw);
			}
		} catch (_err) { /* ignore */ }

		// Ensure this API key exists in hashed form even if we couldn't migrate the full user.
		await this.client.hSet(this._apiKeyRecordKey(apiKeyId), this._serializeHash({
			type: 'api_key',
			apiKeyId,
			personalKeyId,
			name: legacy.name || '',
			createdAt: Number(legacy.createdAt) || Date.now(),
			lastUsed: Number(legacy.lastUsed) || 0,
			revoked: Boolean(legacy.revoked)
		}));
		await this.client.sAdd(this._userApiKeyIndexKey(personalKeyId), apiKeyId);

		// Remove legacy plaintext artefacts
		try { await this.client.del(`apikey:${apiKey}`); } catch (_err) { /* ignore */ }
		try { await this.client.sRem(`user:${personalKey}:api_keys`, apiKey); } catch (_err) { /* ignore */ }
	}

	async _migrateLegacyV2AccessKey(apiKey, apiKeyId, legacyRaw) {
		if (!apiKey || !apiKeyId || !legacyRaw) {
			return;
		}
		const legacy = this._deserializeHash(legacyRaw);
		if (!legacy || legacy.type !== 'v2_access_key' || legacy.authVersion !== 2) {
			return;
		}
		if (!legacy.ownerId || !legacy.uid) {
			return;
		}

		await this.client.hSet(this._apiKeyRecordKey(apiKeyId), this._serializeHash({
			apiKeyId,
			ownerId: legacy.ownerId,
			uid: legacy.uid,
			authVersion: 2,
			type: 'v2_access_key',
			name: legacy.name || '',
			permissions: Array.isArray(legacy.permissions) ? legacy.permissions : (legacy.permissions ? [legacy.permissions] : ['toggle']),
			createdAt: Number(legacy.createdAt) || Date.now(),
			lastUsed: Number(legacy.lastUsed) || 0,
			revoked: Boolean(legacy.revoked)
		}));

		// Replace membership in indexes
		try {
			await this.client.sAdd(`switch:${legacy.uid}:access_keys`, apiKeyId);
			await this.client.sRem(`switch:${legacy.uid}:access_keys`, apiKey);
		} catch (_err) { /* ignore */ }
		try {
			await this.client.sAdd(`owner:${legacy.ownerId}:access_keys`, apiKeyId);
			await this.client.sRem(`owner:${legacy.ownerId}:access_keys`, apiKey);
		} catch (_err) { /* ignore */ }

		// Remove legacy plaintext record
		try { await this.client.del(`apikey:${apiKey}`); } catch (_err) { /* ignore */ }
	}

	async _migrateLegacySessionToken(token, tokenId, legacyRaw) {
		if (!token || !tokenId || !legacyRaw) {
			return;
		}
		const legacyKey = `session_token:${token}`;
		const legacyTtl = await this.client.ttl(legacyKey);

		const legacy = this._deserializeHash(legacyRaw);
		const personalKey = legacy.personalKey;
		if (!personalKey) {
			return;
		}
		const personalKeyId = this._getPersonalKeyId(personalKey);

		const recordKey = this._sessionTokenRecordKey(tokenId);
		await this.client.hSet(recordKey, this._serializeHash({
			tokenId,
			personalKeyId,
			createdAt: Number(legacy.createdAt) || Date.now(),
			expiresAt: Number(legacy.expiresAt) || (Date.now() + 300 * 1000)
		}));

		const ttlSeconds = legacyTtl > 0 ? legacyTtl : Math.max(1, Math.ceil((Number(legacy.expiresAt) - Date.now()) / 1000));
		await this.client.expire(recordKey, ttlSeconds);

		// Remove legacy plaintext record
		try { await this.client.del(legacyKey); } catch (_err) { /* ignore */ }
	}

	// API key management
	async createApiKey(personalKey, name = '', ttlSeconds = null) {
		const { v4: uuidv4 } = require('uuid');
		const apiKey = uuidv4();
		const personalKeyId = this._getPersonalKeyId(personalKey);
		const apiKeyId = this._getApiKeyId(apiKey);
		const now = Date.now();
		const ttl = Number.isFinite(ttlSeconds) && ttlSeconds > 0 ? Math.floor(ttlSeconds) : null;
		const expiresAt = ttl ? now + (ttl * 1000) : null;

		const keyData = {
			type: 'api_key',
			apiKeyId,
			personalKeyId,
			name,
			createdAt: now,
			lastUsed: 0,
			revoked: false
		};
		if (expiresAt) {
			keyData.expiresAt = expiresAt;
		}

		const recordKey = this._apiKeyRecordKey(apiKeyId);
		await this.client.hSet(recordKey, this._serializeHash(keyData));
		await this.client.sAdd(this._userApiKeyIndexKey(personalKeyId), apiKeyId);
		if (expiresAt) {
			await this.client.expire(recordKey, ttl);
		}
		// API keys do not expire automatically; rely on explicit revoke or key deletion
		return {
			apiKey,
			apiKeyId,
			name: keyData.name || '',
			createdAt: keyData.createdAt,
			expiresAt: expiresAt || 0
		};
	}

	async listApiKeys(personalKey) {
		const personalKeyId = this._getPersonalKeyId(personalKey);
		const apiKeys = await this.client.sMembers(this._userApiKeyIndexKey(personalKeyId));
		const result = [];
		const now = Date.now();
		for (const key of apiKeys) {
			const data = await this.client.hGetAll(this._apiKeyRecordKey(key));
			if (data && Object.keys(data).length > 0) {
				const parsed = this._deserializeHash(data);
				if (parsed && parsed.expiresAt && parsed.expiresAt <= now) {
					await this.client.del(this._apiKeyRecordKey(key));
					await this.client.sRem(this._userApiKeyIndexKey(personalKeyId), key);
					continue;
				}
				// Never return the plaintext API key; only stable IDs + metadata.
				result.push({
					apiKeyId: parsed.apiKeyId || key,
					name: parsed.name || '',
					createdAt: parsed.createdAt || 0,
					lastUsed: parsed.lastUsed || 0,
					revoked: Boolean(parsed.revoked),
					expiresAt: parsed.expiresAt || 0
				});
			}
		}
		return result;
	}

	async revokeApiKey(personalKey, apiKey) {
		const personalKeyId = this._getPersonalKeyId(personalKey);
		const apiKeyId = this._getApiKeyId(apiKey);
		// Ensure the key belongs to the user
		const members = await this.client.sMembers(this._userApiKeyIndexKey(personalKeyId));
		if (!members.includes(apiKeyId)) {
			return false;
		}
		await this.client.hSet(this._apiKeyRecordKey(apiKeyId), this._serializeHash({ revoked: true, lastUsed: Date.now() }));
		await this.client.sRem(this._userApiKeyIndexKey(personalKeyId), apiKeyId);
		return true;
	}

	async resolvePersonalKeyFromApiKey(apiKey) {
		if (!apiKey) return null;
		const apiKeyId = this._getApiKeyId(apiKey);

		const data = await this.client.hGetAll(this._apiKeyRecordKey(apiKeyId));
		if (data && Object.keys(data).length > 0) {
			if (data.revoked === 'true') {
				return null;
			}
			const parsed = this._deserializeHash(data);
			if (!parsed || parsed.type !== 'api_key' || !parsed.personalKeyId) {
				return null;
			}
			if (parsed.expiresAt && parsed.expiresAt <= Date.now()) {
				await this.client.del(this._apiKeyRecordKey(apiKeyId));
				await this.client.sRem(this._userApiKeyIndexKey(parsed.personalKeyId), apiKeyId);
				return null;
			}
			// Update lastUsed
			await this.client.hSet(this._apiKeyRecordKey(apiKeyId), 'lastUsed', `${Date.now()}`);
			return parsed.personalKeyId;
		}

		// Legacy fallback: apikey:<apiKey> (plaintext) -> migrate on first use
		if (typeof apiKey === 'string' && apiKey.includes('-')) {
			const legacyKey = `apikey:${apiKey}`;
			const legacyRaw = await this.client.hGetAll(legacyKey);
			if (legacyRaw && Object.keys(legacyRaw).length > 0) {
				try {
					await this._migrateLegacyApiKey(apiKey, apiKeyId, legacyRaw);
				} catch (error) {
					logger.warn('Legacy api key migration failed for %s: %s', apiKeyId.substring(0, 8), error && error.message ? error.message : String(error));
				}
				const migrated = await this.client.hGetAll(this._apiKeyRecordKey(apiKeyId));
				if (migrated && Object.keys(migrated).length > 0 && migrated.revoked !== 'true') {
					await this.client.hSet(this._apiKeyRecordKey(apiKeyId), 'lastUsed', `${Date.now()}`);
					const parsed = this._deserializeHash(migrated);
					if (parsed && parsed.type === 'api_key' && parsed.personalKeyId) {
						return parsed.personalKeyId;
					}
				}
			}
		}

		return null;
	}

	// V2 access keys (delegation): per-switch keys created by owner signature, stored server-side
	async createV2AccessKey(ownerId, uid, name = '', permissions = ['toggle'], ttlSeconds = null) {
		if (!ownerId || !uid) {
			return null;
		}
		const { v4: uuidv4 } = require('uuid');
		const apiKey = uuidv4();
		const apiKeyId = this._getApiKeyId(apiKey);
		const now = Date.now();
		const ttl = Number.isFinite(ttlSeconds) && ttlSeconds > 0 ? Math.floor(ttlSeconds) : null;
		const expiresAt = ttl ? now + (ttl * 1000) : null;

		const keyData = {
			apiKeyId,
			ownerId,
			uid,
			authVersion: 2,
			type: 'v2_access_key',
			name,
			permissions: Array.isArray(permissions) ? permissions : ['toggle'],
			createdAt: now,
			lastUsed: 0,
			revoked: false
		};
		if (expiresAt) {
			keyData.expiresAt = expiresAt;
		}

		const recordKey = this._apiKeyRecordKey(apiKeyId);
		await this.client.hSet(recordKey, this._serializeHash(keyData));
		if (expiresAt) {
			await this.client.expire(recordKey, ttl);
		}
		await this.client.sAdd(`switch:${uid}:access_keys`, apiKeyId);
		await this.client.sAdd(`owner:${ownerId}:access_keys`, apiKeyId);
		return {
			apiKey,
			apiKeyId,
			name: keyData.name || '',
			permissions: keyData.permissions || [],
			createdAt: keyData.createdAt,
			expiresAt: expiresAt || 0
		};
	}

	async listV2AccessKeys(ownerId, uid) {
		if (!ownerId || !uid) {
			return [];
		}
		const apiKeys = await this.client.sMembers(`switch:${uid}:access_keys`);
		const result = [];
		const now = Date.now();
		for (const key of apiKeys) {
			const data = await this.client.hGetAll(this._apiKeyRecordKey(key));
			if (data && Object.keys(data).length > 0) {
				const parsed = this._deserializeHash(data);
				if (parsed && parsed.expiresAt && parsed.expiresAt <= now) {
					await this.client.del(this._apiKeyRecordKey(key));
					await this.client.sRem(`switch:${uid}:access_keys`, key);
					await this.client.sRem(`owner:${ownerId}:access_keys`, key);
					continue;
				}
				// Only list keys for this switch + owner (defence in depth)
				if (parsed && parsed.uid === uid && parsed.ownerId === ownerId && parsed.type === 'v2_access_key') {
					result.push(parsed);
				}
			}
		}
		return result;
	}

	async revokeV2AccessKey(ownerId, uid, apiKey) {
		if (!ownerId || !uid || !apiKey) {
			return false;
		}
		const apiKeyId = this._getApiKeyId(apiKey);
		const data = await this.client.hGetAll(this._apiKeyRecordKey(apiKeyId));
		if (!data || Object.keys(data).length === 0) {
			return false;
		}
		const parsed = this._deserializeHash(data);
		if (!parsed || parsed.type !== 'v2_access_key' || parsed.ownerId !== ownerId || parsed.uid !== uid) {
			return false;
		}

		await this.client.hSet(this._apiKeyRecordKey(apiKeyId), this._serializeHash({ revoked: true, lastUsed: Date.now() }));
		await this.client.sRem(`switch:${uid}:access_keys`, apiKeyId);
		await this.client.sRem(`owner:${ownerId}:access_keys`, apiKeyId);
		return true;
	}

	async pauseV2AccessKey(ownerId, uid, keyIdOrApiKey, paused = true) {
		if (!ownerId || !uid || !keyIdOrApiKey) {
			return false;
		}
		const apiKeyId = this._getApiKeyId(keyIdOrApiKey);
		const data = await this.client.hGetAll(this._apiKeyRecordKey(apiKeyId));
		if (!data || Object.keys(data).length === 0) {
			return false;
		}
		const parsed = this._deserializeHash(data);
		if (!parsed || parsed.type !== 'v2_access_key' || parsed.ownerId !== ownerId || parsed.uid !== uid) {
			return false;
		}
		if (parsed.revoked) {
			return false; // Cannot pause a revoked key
		}
		await this.client.hSet(this._apiKeyRecordKey(apiKeyId), this._serializeHash({ paused: Boolean(paused) }));
		return true;
	}

	async updateV2AccessKeyPermissions(ownerId, uid, keyIdOrApiKey, permissions) {
		if (!ownerId || !uid || !keyIdOrApiKey || !Array.isArray(permissions)) {
			return false;
		}
		const apiKeyId = this._getApiKeyId(keyIdOrApiKey);
		const data = await this.client.hGetAll(this._apiKeyRecordKey(apiKeyId));
		if (!data || Object.keys(data).length === 0) {
			return false;
		}
		const parsed = this._deserializeHash(data);
		if (!parsed || parsed.type !== 'v2_access_key' || parsed.ownerId !== ownerId || parsed.uid !== uid) {
			return false;
		}
		if (parsed.revoked) {
			return false; // Cannot update a revoked key
		}
		await this.client.hSet(this._apiKeyRecordKey(apiKeyId), this._serializeHash({ permissions }));
		return true;
	}

	async resolveV2AccessKey(apiKey) {
		if (!apiKey) {
			return null;
		}
		const apiKeyId = this._getApiKeyId(apiKey);

		let data = await this.client.hGetAll(this._apiKeyRecordKey(apiKeyId));

		// Old hash-secret fallback: record stored under previous KEY_HASH_SECRET
		if (!data || Object.keys(data).length === 0) {
			const oldResult = await this._tryOldSecretFallback(
				'apiKey', apiKey, this._apiKeyRecordKey.bind(this)
			);
			if (oldResult) {
				data = oldResult.data;
			}
		}

		if (data && Object.keys(data).length > 0) {
			if (data.revoked === 'true') {
				return null;
			}
			if (data.paused === 'true') {
				return null; // Paused keys are temporarily inactive
			}
			const parsed = this._deserializeHash(data);
			if (!parsed || parsed.type !== 'v2_access_key' || parsed.authVersion !== 2) {
				return null;
			}
			if (parsed.expiresAt && parsed.expiresAt <= Date.now()) {
				await this.client.del(this._apiKeyRecordKey(apiKeyId));
				await this.client.sRem(`switch:${parsed.uid}:access_keys`, apiKeyId);
				await this.client.sRem(`owner:${parsed.ownerId}:access_keys`, apiKeyId);
				return null;
			}
			await this.client.hSet(this._apiKeyRecordKey(apiKeyId), 'lastUsed', `${Date.now()}`);
			return parsed;
		}

		// Legacy fallback: apikey:<apiKey> (plaintext) -> migrate on first use
		if (typeof apiKey === 'string' && apiKey.includes('-')) {
			const legacyKey = `apikey:${apiKey}`;
			const legacyRaw = await this.client.hGetAll(legacyKey);
			if (legacyRaw && Object.keys(legacyRaw).length > 0) {
				try {
					await this._migrateLegacyV2AccessKey(apiKey, apiKeyId, legacyRaw);
				} catch (error) {
					logger.warn('Legacy v2 access key migration failed for %s: %s', apiKeyId.substring(0, 8), error && error.message ? error.message : String(error));
				}
				const migrated = await this.client.hGetAll(this._apiKeyRecordKey(apiKeyId));
				if (migrated && Object.keys(migrated).length > 0 && migrated.revoked !== 'true') {
					const parsed = this._deserializeHash(migrated);
					if (parsed && parsed.type === 'v2_access_key' && parsed.authVersion === 2) {
						await this.client.hSet(this._apiKeyRecordKey(apiKeyId), 'lastUsed', `${Date.now()}`);
						return parsed;
					}
				}
			}
		}

		return null;
	}

	// Session token management (one-time tokens for web login)
	async createSessionToken(personalKey, ttlSeconds = 300) {
		const { v4: uuidv4 } = require('uuid');
		const token = uuidv4();
		const tokenId = this._getSessionTokenId(token);
		const personalKeyId = this._getPersonalKeyId(personalKey);
		const data = {
			tokenId,
			personalKeyId,
			createdAt: Date.now(),
			expiresAt: Date.now() + ttlSeconds * 1000
		};
		await this.client.hSet(this._sessionTokenRecordKey(tokenId), this._serializeHash(data));
		await this.client.expire(this._sessionTokenRecordKey(tokenId), ttlSeconds);
		return {
			token,
			expiresAt: data.expiresAt
		};
	}

	async redeemSessionToken(token) {
		const tokenId = this._getSessionTokenId(token);
		const recordKey = this._sessionTokenRecordKey(tokenId);

		let data = await this.client.hGetAll(recordKey);

		// Old hash-secret fallback: record stored under previous KEY_HASH_SECRET
		if (!data || Object.keys(data).length === 0) {
			const oldResult = await this._tryOldSecretFallback(
				'sessionToken', token, this._sessionTokenRecordKey.bind(this)
			);
			if (oldResult) {
				data = oldResult.data;
			}
		}

		if (data && Object.keys(data).length > 0) {
			// One-time use
			await this.client.del(recordKey);
			return this._deserializeHash(data);
		}

		// Legacy fallback: session_token:<token> (plaintext) -> migrate on redeem
		if (typeof token === 'string' && token.includes('-')) {
			const legacyKey = `session_token:${token}`;
			const legacyRaw = await this.client.hGetAll(legacyKey);
			if (legacyRaw && Object.keys(legacyRaw).length > 0) {
				try {
					await this._migrateLegacySessionToken(token, tokenId, legacyRaw);
				} catch (error) {
					logger.warn('Legacy session token migration failed for %s: %s', tokenId.substring(0, 8), error && error.message ? error.message : String(error));
				}
				const migrated = await this.client.hGetAll(recordKey);
				if (migrated && Object.keys(migrated).length > 0) {
					await this.client.del(recordKey);
					return this._deserializeHash(migrated);
				}
			}
		}

		return null;
	}

	async deletePersonalKey(personalKey) {
		const personalKeyId = this._getPersonalKeyId(personalKey);
		// Get all switches for this user
		const userSwitches = await this.getUserSwitches(personalKeyId);

		// Delete all user switches
		for (const switchData of userSwitches) {
			await this.client.del(`switch:${switchData.uid}`);
			await this.client.sRem('public_switches', switchData.uid);
			await this.client.zRem(ALL_SWITCHES_SORTED_SET, switchData.uid);
			await this.client.del(`switch:${switchData.uid}:users`);
			await this.client.del(`switch:${switchData.uid}:events`);
		}

		// Delete any v1 API keys belonging to this user
		try {
			const apiKeyIds = await this.client.sMembers(this._userApiKeyIndexKey(personalKeyId));
			for (const apiKeyId of apiKeyIds) {
				await this.client.del(this._apiKeyRecordKey(apiKeyId));
			}
			await this.client.del(this._userApiKeyIndexKey(personalKeyId));
		} catch (_err) { /* ignore */ }

		// Delete user key and index
		await this.client.del(this._personalKeyRecordKey(personalKeyId));
		await this.client.del(this._userSwitchIndexKey(personalKeyId));

		// Best-effort cleanup of legacy plaintext keys
		await this.client.del(`key:${personalKey}`);
		await this.client.del(`user:${personalKey}`);
		await this.client.del(`user:${personalKey}:api_keys`);

		return userSwitches.length;
	}

	async getPublicSwitchDetail(uid) {
		const redirect = await this.getSwitchRedirect(uid);
		if (redirect) {
			return {
				uid,
				redirect: true,
				redirectTo: redirect.toUid,
				redirectReason: redirect.reason || '',
				redirectAt: redirect.updatedAt || 0
			};
		}

		const switchData = await this.getSwitchState(uid);
		if (!switchData || !switchData.publicize) {
			return null;
		}
		// Ignore legacy v1 switches (UUID + personalKey auth). Public pages should be v2-only.
		if (switchData.authVersion !== 2) {
			return null;
		}
		if (switchData.ownerId && await this.isOwnerBlocked(switchData.ownerId)) {
			return null;
		}
		const override = await this.getSwitchListingOverride(uid);
		const listingData = this._applyListingOverride(switchData, override);

		const userCount = await this.getUserCount(uid);
		const events = await this.getEvents(uid, 50);
		const ownerProfileUrl = '';

		return {
			uid: listingData.uid,
			name: listingData.name || '',
			description: listingData.description || '',
			location: listingData.location || '',
			category: listingData.category || 'Other',
			state: listingData.state,
			lastToggled: listingData.lastToggled,
			toggleCount: listingData.toggleCount || 0,
			userCount,
			link: listingData.link || '',
			iconUrl: listingData.iconUrl || '',
			bannerUrl: listingData.bannerUrl || '',
			ownerProfileUrl,
			events
		};
	}

	async getCategoryCounts() {
		const publicSwitches = await this.getPublicSwitches();
		const counts = {};
		for (const sw of publicSwitches) {
			const category = sw.category || 'Other';
			counts[category] = (counts[category] || 0) + 1;
		}
		return counts;
	}

	async updateSwitch(uid, updates = {}) {
		const existing = await this.getSwitchState(uid);
		if (!existing) {
			return null;
		}

		const updated = {
			...existing,
			...updates,
			state: existing.state ? 'on' : 'off'
		};

		await this.client.hSet(`switch:${uid}`, this._serializeHash(updated));

		if (typeof updates.publicize === 'boolean') {
			if (updates.publicize) {
				await this.client.sAdd('public_switches', uid);
			} else {
				await this.client.sRem('public_switches', uid);
			}
		}

		return this.getSwitchState(uid);
	}

	async deleteSwitchAdmin(uid) {
		if (!uid) return null;
		const switchData = await this.getSwitchState(uid);
		if (!switchData) {
			return null;
		}

		const ownerKeyId = switchData.ownerKeyId || '';
		const ownerId = switchData.ownerId || '';

		const switchAccessKeys = await this.client.sMembers(`switch:${uid}:access_keys`);

		const pipeline = this.client.multi();
		pipeline.del(`switch:${uid}`);
		pipeline.del(`switch:${uid}:users`);
		pipeline.del(`switch:${uid}:events`);
		pipeline.del(`switch:${uid}:access_keys`);
		pipeline.del(this._switchOverrideKey(uid));
		pipeline.sRem('public_switches', uid);
		pipeline.zRem(ALL_SWITCHES_SORTED_SET, uid);
		if (ownerKeyId) {
			pipeline.sRem(`user:${ownerKeyId}:switches`, uid);
		}
		if (ownerId) {
			pipeline.sRem(`owner:${ownerId}`, uid);
		}

		for (const apiKeyId of switchAccessKeys) {
			pipeline.del(this._apiKeyRecordKey(apiKeyId));
			if (ownerId) {
				pipeline.sRem(`owner:${ownerId}:access_keys`, apiKeyId);
			}
		}

		await pipeline.exec();
		return switchData;
	}

	async setProfileUrl(personalKey, profileUrl) {
		if (!personalKey) {
			return null;
		}
		const personalKeyId = this._getPersonalKeyId(personalKey);
		await this.client.hSet(this._personalKeyRecordKey(personalKeyId), this._serializeHash({
			profileUrl,
			lastUpdated: Date.now()
		}));
		return profileUrl;
	}

	async getProfileUrl(personalKey) {
		if (!personalKey) {
			return '';
		}
		const personalKeyId = this._getPersonalKeyId(personalKey);
		const data = await this.client.hGetAll(this._personalKeyRecordKey(personalKeyId));
		if (!data || Object.keys(data).length === 0) {
			return '';
		}
		return data.profileUrl || '';
	}

	// ── Switch name allocation (globally unique Sami words) ─────────────────

	_switchNamesKey() {
		return 'switch_names:allocated';
	}

	_switchNameCounterKey() {
		return 'switch_names:counter';
	}

	async allocateSwitchName() {
		// eslint-disable-next-line global-require
		const { getWordList, formatSwitchName } = require('./switch_names');
		const words = getWordList();

		// First pass: find an unused base word
		for (const word of words) {
			const baseName = formatSwitchName(word);
			const added = await this.client.sAdd(this._switchNamesKey(), baseName.toLowerCase());
			if (added === 1) {
				return baseName;
			}
		}

		// All base words used — increment global counter and append suffix
		const counter = await this.client.incr(this._switchNameCounterKey());
		const suffix = Math.ceil(counter / words.length) + 1;
		const wordIndex = (counter - 1) % words.length;
		const word = words[wordIndex];
		const suffixedName = formatSwitchName(word, suffix);

		await this.client.sAdd(this._switchNamesKey(), suffixedName.toLowerCase());
		return suffixedName;
	}

	async releaseSwitchName(name) {
		if (!name || typeof name !== 'string') {
			return false;
		}
		const removed = await this.client.sRem(this._switchNamesKey(), name.toLowerCase());
		return removed === 1;
	}

	async isSwitchNameAllocated(name) {
		if (!name || typeof name !== 'string') {
			return false;
		}
		return await this.client.sIsMember(this._switchNamesKey(), name.toLowerCase());
	}

	async getAllocatedSwitchNameCount() {
		return await this.client.sCard(this._switchNamesKey());
	}

	// ── Owner Tier & Promo-Code Management ────────────────────────────────────────

	_ownerTierKey(ownerId) {
		return `owner_tier:${ownerId}`;
	}

	_promoCodeKey(code) {
		return `promo:${String(code).toLowerCase().trim()}`;
	}

	/**
	 * Get the current tier for an owner.
	 * Returns { tier: 'premium', expiresAt, promoCode } or { tier: 'free' }.
	 */
	async getOwnerTier(ownerId) {
		if (!ownerId) return { tier: 'free' };
		const data = await this.client.hGetAll(this._ownerTierKey(ownerId));
		if (!data || Object.keys(data).length === 0) {
			return { tier: 'free' };
		}
		const parsed = this._deserializeHash(data);
		// Check expiry
		if (parsed.expiresAt && parsed.expiresAt <= Date.now()) {
			await this.client.del(this._ownerTierKey(ownerId));
			return { tier: 'free' };
		}
		return parsed;
	}

	/**
	 * Set the tier for an owner.
	 */
	async setOwnerTier(ownerId, tier, expiresAt = 0, promoCode = '') {
		if (!ownerId || !tier) return false;
		const data = {
			tier,
			expiresAt: expiresAt || 0,
			promoCode: promoCode || '',
			redeemedAt: Date.now()
		};
		await this.client.hSet(this._ownerTierKey(ownerId), this._serializeHash(data));
		// Set Redis expiry to auto-clean if there is a time limit
		if (expiresAt > 0) {
			const ttlMs = expiresAt - Date.now();
			if (ttlMs > 0) {
				await this.client.expire(this._ownerTierKey(ownerId), Math.ceil(ttlMs / 1000));
			}
		}
		return true;
	}

	/**
	 * Create a promo code.
	 * @param {object} opts - { code, tier, durationDays, maxRedemptions, createdBy }
	 */
	async createPromoCode({ code, tier = 'premium', durationDays = 90, maxRedemptions = 1, createdBy = 'admin' }) {
		if (!code) return null;
		const normalised = String(code).toLowerCase().trim();
		const key = this._promoCodeKey(normalised);
		const existing = await this.client.hGetAll(key);
		if (existing && Object.keys(existing).length > 0) {
			return null; // code already exists
		}
		const data = {
			code: normalised,
			tier,
			durationDays,
			maxRedemptions,
			redemptions: 0,
			createdBy,
			createdAt: Date.now()
		};
		await this.client.hSet(key, this._serializeHash(data));
		return data;
	}

	/**
	 * Get a promo code record.
	 */
	async getPromoCode(code) {
		if (!code) return null;
		const key = this._promoCodeKey(code);
		const data = await this.client.hGetAll(key);
		if (!data || Object.keys(data).length === 0) return null;
		return this._deserializeHash(data);
	}

	/**
	 * Redeem a promo code for an owner.
	 * Returns { success, tier, expiresAt, error? }.
	 */
	async redeemPromoCode(code, ownerId) {
		if (!code || !ownerId) return { success: false, error: 'Missing code or owner' };
		const normalised = String(code).toLowerCase().trim();
		const promoKey = this._promoCodeKey(normalised);
		const data = await this.client.hGetAll(promoKey);
		if (!data || Object.keys(data).length === 0) {
			return { success: false, error: 'Invalid promo code' };
		}
		const promo = this._deserializeHash(data);

		// Check redemption limit
		if (promo.maxRedemptions > 0 && promo.redemptions >= promo.maxRedemptions) {
			return { success: false, error: 'Promo code has been fully redeemed' };
		}

		// Check if owner already has an active premium tier
		const currentTier = await this.getOwnerTier(ownerId);
		if (currentTier.tier === 'premium' && currentTier.expiresAt > Date.now()) {
			return { success: false, error: 'You already have an active premium subscription' };
		}

		// Calculate expiry
		const durationMs = (promo.durationDays || 90) * 24 * 60 * 60 * 1000;
		const expiresAt = Date.now() + durationMs;

		// Set owner tier
		await this.setOwnerTier(ownerId, promo.tier || 'premium', expiresAt, normalised);

		// Increment redemption counter
		await this.client.hIncrBy(promoKey, 'redemptions', 1);

		// If single-use, mark as fully redeemed
		if (promo.maxRedemptions === 1) {
			await this.client.hSet(promoKey, 'fullyRedeemed', 'true');
		}

		return {
			success: true,
			tier: promo.tier || 'premium',
			expiresAt,
			durationDays: promo.durationDays || 90
		};
	}

	/**
	 * List all promo codes (admin use).
	 */
	async listPromoCodes() {
		const keys = [];
		let cursor = '0';
		do {
			const result = await this.client.scan(cursor, { MATCH: 'promo:*', COUNT: 100 });
			cursor = result.cursor !== undefined ? String(result.cursor) : '0';
			if (result.keys && result.keys.length) {
				keys.push(...result.keys);
			}
		} while (cursor !== '0');

		const codes = [];
		for (const key of keys) {
			const data = await this.client.hGetAll(key);
			if (data && Object.keys(data).length > 0) {
				codes.push(this._deserializeHash(data));
			}
		}
		return codes;
	}

	/**
	 * Delete a promo code (admin use).
	 */
	async deletePromoCode(code) {
		if (!code) return false;
		const key = this._promoCodeKey(code);
		const result = await this.client.del(key);
		return result > 0;
	}

	// ── Global switch tracking (all_switches sorted set) ────────────────────

	/**
	 * Record a switch creation in the global sorted set.
	 * Score = creation timestamp (ms). Used for total count and daily stats.
	 */
	async recordSwitchCreation(uid, createdAt) {
		if (!uid) return;
		const score = Number(createdAt) || Date.now();
		await this.client.zAdd(ALL_SWITCHES_SORTED_SET, { score, value: uid });
	}

	/**
	 * Remove a switch from the global sorted set (on deletion).
	 */
	async removeSwitchFromGlobalIndex(uid) {
		if (!uid) return;
		await this.client.zRem(ALL_SWITCHES_SORTED_SET, uid);
	}

	/**
	 * Get the total number of switches ever tracked.
	 */
	async getTotalSwitchCount() {
		return await this.client.zCard(ALL_SWITCHES_SORTED_SET);
	}

	/**
	 * Get daily switch statistics for the last N days.
	 * Returns an array of { date, total, added } objects (newest first).
	 *
	 * - total: cumulative count of switches up to end of that day
	 * - added: switches created on that specific day
	 */
	async getDailySwitchStats(days = 30) {
		const MS_PER_DAY = 86400000;
		const now = Date.now();
		const results = [];

		for (let i = 0; i < days; i++) {
			const dayOffset = days - 1 - i;
			const dayStart = now - (dayOffset + 1) * MS_PER_DAY;
			const dayEnd = now - dayOffset * MS_PER_DAY;

			// Switches created on this day (score between dayStart and dayEnd)
			const added = await this.client.zCount(
				ALL_SWITCHES_SORTED_SET,
				dayStart,
				dayEnd - 1
			);

			// Cumulative total up to end of day
			const total = await this.client.zCount(
				ALL_SWITCHES_SORTED_SET,
				'-inf',
				dayEnd - 1
			);

			const dateStr = new Date(dayEnd).toISOString().slice(0, 10);
			results.push({ date: dateStr, total, added });
		}

		return results;
	}

	/**
	 * Backfill the all_switches sorted set by scanning existing switch:* keys.
	 * Idempotent – safe to run multiple times. Intended for one-off migration.
	 */
	async backfillGlobalSwitchIndex() {
		let cursor = '0';
		let backfilled = 0;
		do {
			const result = await this.client.scan(cursor, { MATCH: 'switch:*', COUNT: 200 });
			cursor = result.cursor !== undefined ? String(result.cursor) : '0';
			if (!result.keys || !result.keys.length) continue;

			for (const key of result.keys) {
				// Only process direct switch hashes, not sub-keys like switch:uid:events
				const parts = key.split(':');
				if (parts.length !== 2) continue;

				const uid = parts[1];
				const createdAt = await this.client.hGet(key, 'createdAt');
				const score = Number(createdAt) || Date.now();
				await this.client.zAdd(ALL_SWITCHES_SORTED_SET, { score, value: uid });
				backfilled++;
			}
		} while (cursor !== '0');

		logger.info(`Backfilled ${backfilled} switches into global index`);
		return backfilled;
	}
}

module.exports = new RedisClient();
