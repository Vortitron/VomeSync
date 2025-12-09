const redis = require('redis');
const config = require('../config/config');
const logger = require('./logger');
let testRedisServer = null;

class RedisClient {
	constructor() {
		this.client = null;
		this.pubClient = null;
		this.subClient = null;
		this.isConnected = false;
	}

	async connect() {
		try {
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

			// Main Redis client for data operations
			this.client = redis.createClient({
				socket: {
					host,
					port
				},
				password: password || undefined,
				database
			});

			// Pub/Sub clients (Redis requires separate clients for pub/sub)
			this.pubClient = redis.createClient({
				socket: {
					host,
					port
				},
				password: password || undefined,
				database
			});

			this.subClient = redis.createClient({
				socket: {
					host,
					port
				},
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

		} catch (error) {
			logger.error('Failed to connect to Redis:', error);
			throw error;
		}
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
		await this.client.expire(key, 30 * 24 * 60 * 60);

		return switchData;
	}

	async getSwitchState(uid) {
		const key = `switch:${uid}`;
		const data = await this.client.hGetAll(key);

		if (!data || Object.keys(data).length === 0) {
			return null;
		}

		return {
			...data,
			uid: data.uid,
			state: data.state === 'on',
			publicize: data.publicize === 'true' || data.publicize === true,
			toggleCount: parseInt(data.toggleCount, 10) || 0,
			createdAt: parseInt(data.createdAt, 10) || 0,
			lastToggled: parseInt(data.lastToggled, 10) || 0,
			link: data.link || ''
		};
	}

	async createSwitch(uid, personalKey, switchConfig) {
		const key = `switch:${uid}`;
		const switchData = {
			uid,
			personalKey,
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
		await this.client.expire(key, 30 * 24 * 60 * 60); // 30 day expiry

		// Add to personal key index
		const userKey = `user:${personalKey}`;
		await this.client.sAdd(userKey, uid);
		await this.client.expire(userKey, 30 * 24 * 60 * 60);

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
		const userKey = `user:${personalKey}`;
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

	async getPublicSwitches() {
		const publicUIDs = await this.client.sMembers('public_switches');
		const switches = [];

		for (const uid of publicUIDs) {
			const switchData = await this.getSwitchState(uid);
			if (!switchData || !switchData.publicize) {
				continue;
			}
			const userCount = await this.getUserCount(uid);
			const ownerProfileUrl = await this.getProfileUrl(switchData.personalKey);
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

	async recordUserInteraction(uid, personalKey) {
		if (!personalKey) {
			return;
		}
		const key = `switch:${uid}:users`;
		await this.client.sAdd(key, personalKey);
		await this.client.expire(key, 30 * 24 * 60 * 60);
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
		await this.client.expire(key, 30 * 24 * 60 * 60);
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
	async publishSwitchUpdate(uid, state) {
		const channel = `switch_updates:${uid}`;
		const message = JSON.stringify({
			uid,
			state,
			timestamp: Date.now()
		});

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
	async storePersonalKey(personalKey) {
		const keyData = {
			key: personalKey,
			createdAt: Date.now(),
			lastUsed: Date.now()
		};

		await this.client.hSet(`key:${personalKey}`, keyData);
		await this.client.expire(`key:${personalKey}`, 365 * 24 * 60 * 60); // 1 year expiry

		return keyData;
	}

	async validatePersonalKey(personalKey) {
		const keyData = await this.client.hGetAll(`key:${personalKey}`);

		if (!keyData || Object.keys(keyData).length === 0) {
			return false;
		}

		// Update last used timestamp
		await this.client.hSet(`key:${personalKey}`, 'lastUsed', Date.now());

		return true;
	}

	// API key management
	async createApiKey(personalKey, name = '') {
		const { v4: uuidv4 } = require('uuid');
		const apiKey = uuidv4();

		const keyData = {
			apiKey,
			personalKey,
			name,
			createdAt: Date.now(),
			lastUsed: 0,
			revoked: false
		};

		await this.client.hSet(`apikey:${apiKey}`, this._serializeHash(keyData));
		await this.client.sAdd(`user:${personalKey}:api_keys`, apiKey);
		// API keys do not expire automatically; rely on explicit revoke or key deletion
		return keyData;
	}

	async listApiKeys(personalKey) {
		const apiKeys = await this.client.sMembers(`user:${personalKey}:api_keys`);
		const result = [];
		for (const key of apiKeys) {
			const data = await this.client.hGetAll(`apikey:${key}`);
			if (data && Object.keys(data).length > 0) {
				result.push(this._deserializeHash(data));
			}
		}
		return result;
	}

	async revokeApiKey(personalKey, apiKey) {
		// Ensure the key belongs to the user
		const members = await this.client.sMembers(`user:${personalKey}:api_keys`);
		if (!members.includes(apiKey)) {
			return false;
		}
		await this.client.hSet(`apikey:${apiKey}`, this._serializeHash({ revoked: true, lastUsed: Date.now() }));
		await this.client.sRem(`user:${personalKey}:api_keys`, apiKey);
		return true;
	}

	async resolvePersonalKeyFromApiKey(apiKey) {
		if (!apiKey) return null;
		const data = await this.client.hGetAll(`apikey:${apiKey}`);
		if (!data || Object.keys(data).length === 0) {
			return null;
		}
		if (data.revoked === 'true') {
			return null;
		}
		// Update lastUsed
		await this.client.hSet(`apikey:${apiKey}`, 'lastUsed', Date.now());
		return data.personalKey;
	}

	// Session token management (one-time tokens for web login)
	async createSessionToken(personalKey, ttlSeconds = 300) {
		const { v4: uuidv4 } = require('uuid');
		const token = uuidv4();
		const data = {
			token,
			personalKey,
			createdAt: Date.now(),
			expiresAt: Date.now() + ttlSeconds * 1000
		};
		await this.client.hSet(`session_token:${token}`, this._serializeHash(data));
		await this.client.expire(`session_token:${token}`, ttlSeconds);
		return data;
	}

	async redeemSessionToken(token) {
		const data = await this.client.hGetAll(`session_token:${token}`);
		if (!data || Object.keys(data).length === 0) {
			return null;
		}
		// One-time use
		await this.client.del(`session_token:${token}`);
		return this._deserializeHash(data);
	}

	async deletePersonalKey(personalKey) {
		// Get all switches for this user
		const userSwitches = await this.getUserSwitches(personalKey);

		// Delete all user switches
		for (const switchData of userSwitches) {
			await this.client.del(`switch:${switchData.uid}`);
			await this.client.sRem('public_switches', switchData.uid);
			await this.client.del(`switch:${switchData.uid}:users`);
			await this.client.del(`switch:${switchData.uid}:events`);
		}

		// Delete user key and index
		await this.client.del(`key:${personalKey}`);
		await this.client.del(`user:${personalKey}`);

		return userSwitches.length;
	}

	async getPublicSwitchDetail(uid) {
		const switchData = await this.getSwitchState(uid);
		if (!switchData || !switchData.publicize) {
			return null;
		}

		const userCount = await this.getUserCount(uid);
		const events = await this.getEvents(uid, 50);
		const ownerProfileUrl = await this.getProfileUrl(switchData.personalKey);

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
		await this.client.hSet(`key:${personalKey}`, this._serializeHash({
			profileUrl,
			lastUpdated: Date.now()
		}));
		return profileUrl;
	}

	async getProfileUrl(personalKey) {
		if (!personalKey) {
			return '';
		}
		const data = await this.client.hGetAll(`key:${personalKey}`);
		if (!data || Object.keys(data).length === 0) {
			return '';
		}
		return data.profileUrl || '';
	}
}

module.exports = new RedisClient();
