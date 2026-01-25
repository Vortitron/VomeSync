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
const USER_INDEX_TTL_SECONDS = 30 * 24 * 60 * 60; // 30 days
const SWITCH_TTL_SECONDS = 30 * 24 * 60 * 60; // 30 days

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

	getPersonalKeyId(personalKeyOrId) {
		return this._getPersonalKeyId(personalKeyOrId);
	}

	getApiKeyId(apiKeyOrId) {
		return this._getApiKeyId(apiKeyOrId);
	}

	getSessionTokenId(tokenOrId) {
		return this._getSessionTokenId(tokenOrId);
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
			}
		}

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
			}
		}

		return switches;
	}

	async getPublicSwitches() {
		const publicUIDs = await this.client.sMembers('public_switches');
		const switches = [];

		for (const uid of publicUIDs) {
			const switchData = await this.getSwitchState(uid);
			if (!switchData || !switchData.publicize) {
				continue;
			}
			// Ignore legacy v1 switches (UUID + personalKey auth). Public directory should be v2-only.
			if (switchData.authVersion !== 2) {
				continue;
			}
			const userCount = await this.getUserCount(uid);
			const ownerProfileUrl = '';
			switches.push({
				uid: switchData.uid,
				description: switchData.description,
				location: switchData.location,
				category: switchData.category,
				state: switchData.state,
				lastToggled: switchData.lastToggled,
				toggleCount: switchData.toggleCount || 0,
				userCount,
				link: switchData.link || '',
				iconUrl: switchData.iconUrl || '',
				bannerUrl: switchData.bannerUrl || '',
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
	async createApiKey(personalKey, name = '') {
		const { v4: uuidv4 } = require('uuid');
		const apiKey = uuidv4();
		const personalKeyId = this._getPersonalKeyId(personalKey);
		const apiKeyId = this._getApiKeyId(apiKey);

		const keyData = {
			type: 'api_key',
			apiKeyId,
			personalKeyId,
			name,
			createdAt: Date.now(),
			lastUsed: 0,
			revoked: false
		};

		await this.client.hSet(this._apiKeyRecordKey(apiKeyId), this._serializeHash(keyData));
		await this.client.sAdd(this._userApiKeyIndexKey(personalKeyId), apiKeyId);
		// API keys do not expire automatically; rely on explicit revoke or key deletion
		return {
			apiKey,
			apiKeyId,
			name: keyData.name || '',
			createdAt: keyData.createdAt
		};
	}

	async listApiKeys(personalKey) {
		const personalKeyId = this._getPersonalKeyId(personalKey);
		const apiKeys = await this.client.sMembers(this._userApiKeyIndexKey(personalKeyId));
		const result = [];
		for (const key of apiKeys) {
			const data = await this.client.hGetAll(this._apiKeyRecordKey(key));
			if (data && Object.keys(data).length > 0) {
				const parsed = this._deserializeHash(data);
				// Never return the plaintext API key; only stable IDs + metadata.
				result.push({
					apiKeyId: parsed.apiKeyId || key,
					name: parsed.name || '',
					createdAt: parsed.createdAt || 0,
					lastUsed: parsed.lastUsed || 0,
					revoked: Boolean(parsed.revoked)
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
			// Update lastUsed
			await this.client.hSet(this._apiKeyRecordKey(apiKeyId), 'lastUsed', `${Date.now()}`);
			const parsed = this._deserializeHash(data);
			if (!parsed || parsed.type !== 'api_key' || !parsed.personalKeyId) {
				return null;
			}
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
	async createV2AccessKey(ownerId, uid, name = '', permissions = ['toggle']) {
		if (!ownerId || !uid) {
			return null;
		}
		const { v4: uuidv4 } = require('uuid');
		const apiKey = uuidv4();
		const apiKeyId = this._getApiKeyId(apiKey);

		const keyData = {
			apiKeyId,
			ownerId,
			uid,
			authVersion: 2,
			type: 'v2_access_key',
			name,
			permissions: Array.isArray(permissions) ? permissions : ['toggle'],
			createdAt: Date.now(),
			lastUsed: 0,
			revoked: false
		};

		await this.client.hSet(this._apiKeyRecordKey(apiKeyId), this._serializeHash(keyData));
		await this.client.sAdd(`switch:${uid}:access_keys`, apiKeyId);
		await this.client.sAdd(`owner:${ownerId}:access_keys`, apiKeyId);
		return {
			apiKey,
			apiKeyId,
			name: keyData.name || '',
			permissions: keyData.permissions || [],
			createdAt: keyData.createdAt
		};
	}

	async listV2AccessKeys(ownerId, uid) {
		if (!ownerId || !uid) {
			return [];
		}
		const apiKeys = await this.client.sMembers(`switch:${uid}:access_keys`);
		const result = [];
		for (const key of apiKeys) {
			const data = await this.client.hGetAll(this._apiKeyRecordKey(key));
			if (data && Object.keys(data).length > 0) {
				const parsed = this._deserializeHash(data);
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

	async resolveV2AccessKey(apiKey) {
		if (!apiKey) {
			return null;
		}
		const apiKeyId = this._getApiKeyId(apiKey);

		const data = await this.client.hGetAll(this._apiKeyRecordKey(apiKeyId));
		if (data && Object.keys(data).length > 0) {
			if (data.revoked === 'true') {
				return null;
			}
			const parsed = this._deserializeHash(data);
			if (!parsed || parsed.type !== 'v2_access_key' || parsed.authVersion !== 2) {
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

		const data = await this.client.hGetAll(recordKey);
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
		const switchData = await this.getSwitchState(uid);
		if (!switchData || !switchData.publicize) {
			return null;
		}
		// Ignore legacy v1 switches (UUID + personalKey auth). Public pages should be v2-only.
		if (switchData.authVersion !== 2) {
			return null;
		}

		const userCount = await this.getUserCount(uid);
		const events = await this.getEvents(uid, 50);
		const ownerProfileUrl = '';

		return {
			uid: switchData.uid,
			description: switchData.description || '',
			location: switchData.location || '',
			category: switchData.category || 'Other',
			state: switchData.state,
			lastToggled: switchData.lastToggled,
			toggleCount: switchData.toggleCount || 0,
			userCount,
			link: switchData.link || '',
			iconUrl: switchData.iconUrl || '',
			bannerUrl: switchData.bannerUrl || '',
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
}

module.exports = new RedisClient();
