const redis = require('redis');
const config = require('../config/config');
const logger = require('./logger');

class RedisClient {
	constructor() {
		this.client = null;
		this.pubClient = null;
		this.subClient = null;
		this.isConnected = false;
	}

	async connect() {
		try {
			// Main Redis client for data operations
			this.client = redis.createClient({
				socket: {
					host: config.redis.host,
					port: config.redis.port
				},
				password: config.redis.password,
				database: config.redis.db,
				retry_strategy: (options) => {
					if (options.error && options.error.code === 'ECONNREFUSED') {
						logger.error('Redis server refused connection');
						return new Error('Redis server refused connection');
					}
					if (options.total_retry_time > 1000 * 60 * 60) {
						logger.error('Redis retry time exhausted');
						return new Error('Retry time exhausted');
					}
					if (options.attempt > 10) {
						logger.error('Redis max retry attempts reached');
						return undefined;
					}
					return Math.min(options.attempt * 100, 3000);
				}
			});

			// Pub/Sub clients (Redis requires separate clients for pub/sub)
			this.pubClient = redis.createClient({
				socket: {
					host: config.redis.host,
					port: config.redis.port
				},
				password: config.redis.password,
				database: config.redis.db
			});

			this.subClient = redis.createClient({
				socket: {
					host: config.redis.host,
					port: config.redis.port
				},
				password: config.redis.password,
				database: config.redis.db
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
		await this.client.hSet(key, switchData);
		
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
			state: data.state === 'on',
			lastToggled: parseInt(data.lastToggled, 10) || 0
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
			toggleCount: 0
		};

		await this.client.hSet(key, switchData);
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
			if (switchData && switchData.publicize) {
				// Only return public fields
				switches.push({
					uid: switchData.uid,
					description: switchData.description,
					location: switchData.location,
					category: switchData.category,
					state: switchData.state,
					lastToggled: switchData.lastToggled
				});
			}
		}
		
		return switches;
	}

	// Analytics operations
	async incrementToggleCount(uid) {
		const key = `switch:${uid}`;
		await this.client.hIncrBy(key, 'toggleCount', 1);
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

	async deletePersonalKey(personalKey) {
		// Get all switches for this user
		const userSwitches = await this.getUserSwitches(personalKey);
		
		// Delete all user switches
		for (const switchData of userSwitches) {
			await this.client.del(`switch:${switchData.uid}`);
			await this.client.sRem('public_switches', switchData.uid);
		}
		
		// Delete user key and index
		await this.client.del(`key:${personalKey}`);
		await this.client.del(`user:${personalKey}`);
		
		return userSwitches.length;
	}
}

module.exports = new RedisClient();
