/**
 * Mock Redis client for unit tests
 */

class MockRedisClient {
	constructor() {
		this.data = new Map();
		this.isConnected = false;
	}

	async connect() {
		this.isConnected = true;
		return Promise.resolve();
	}

	async disconnect() {
		this.isConnected = false;
		return Promise.resolve();
	}

	async set(key, value) {
		this.data.set(key, value);
		return Promise.resolve('OK');
	}

	async get(key) {
		return Promise.resolve(this.data.get(key) || null);
	}

	async hSet(key, field, value) {
		if (typeof field === 'object') {
			// Multiple fields
			const existing = this.data.get(key) || {};
			this.data.set(key, { ...existing, ...field });
		} else {
			// Single field
			const existing = this.data.get(key) || {};
			existing[field] = value;
			this.data.set(key, existing);
		}
		return Promise.resolve(1);
	}

	async hGetAll(key) {
		return Promise.resolve(this.data.get(key) || {});
	}

	async del(...keys) {
		let deleted = 0;
		keys.forEach(key => {
			if (this.data.has(key)) {
				this.data.delete(key);
				deleted++;
			}
		});
		return Promise.resolve(deleted);
	}

	async exists(key) {
		return Promise.resolve(this.data.has(key) ? 1 : 0);
	}

	async expire(key, seconds) {
		// In a real Redis, this would set TTL, but for tests we'll just return OK
		return Promise.resolve(1);
	}

	async ttl(key) {
		// Mock TTL - return a reasonable value for tests
		return Promise.resolve(this.data.has(key) ? 3600 : -2);
	}

	async incr(key) {
		const current = parseInt(this.data.get(key) || '0', 10);
		const newValue = current + 1;
		this.data.set(key, newValue.toString());
		return Promise.resolve(newValue);
	}

	async sAdd(key, ...members) {
		const set = new Set(this.data.get(key) || []);
		members.forEach(member => set.add(member));
		this.data.set(key, Array.from(set));
		return Promise.resolve(members.length);
	}

	async sMembers(key) {
		return Promise.resolve(this.data.get(key) || []);
	}

	async sRem(key, ...members) {
		const set = new Set(this.data.get(key) || []);
		members.forEach(member => set.delete(member));
		this.data.set(key, Array.from(set));
		return Promise.resolve(members.length);
	}

	async publish(channel, message) {
		// Mock publish - just return number of subscribers
		return Promise.resolve(1);
	}

	async subscribe(channel, callback) {
		// Mock subscribe
		return Promise.resolve();
	}

	async unsubscribe(channel) {
		// Mock unsubscribe
		return Promise.resolve();
	}

	// Additional Redis methods for completeness
	async keys(pattern) {
		const keys = Array.from(this.data.keys());
		if (pattern === '*') {
			return Promise.resolve(keys);
		}
		// Simple pattern matching for test-*
		if (pattern.endsWith('*')) {
			const prefix = pattern.slice(0, -1);
			return Promise.resolve(keys.filter(key => key.startsWith(prefix)));
		}
		return Promise.resolve(keys.filter(key => key === pattern));
	}

	async flushdb() {
		this.data.clear();
		return Promise.resolve('OK');
	}
}

// Mock the Redis client structure
const mockRedisClient = {
	client: new MockRedisClient(),
	pubClient: new MockRedisClient(),
	subClient: new MockRedisClient(),
	isConnected: false,

	async connect() {
		await this.client.connect();
		await this.pubClient.connect();
		await this.subClient.connect();
		this.isConnected = true;
	},

	async disconnect() {
		await this.client.disconnect();
		await this.pubClient.disconnect();
		await this.subClient.disconnect();
		this.isConnected = false;
	},

	// Delegate methods to main client
	async setSwitchState(uid, state, metadata = {}) {
		const switchData = {
			state: state ? 'on' : 'off',
			lastToggled: Date.now(),
			...metadata
		};
		await this.client.hSet(`switch:${uid}`, switchData);
		return switchData;
	},

	async getSwitchState(uid) {
		const data = await this.client.hGetAll(`switch:${uid}`);
		if (!data || Object.keys(data).length === 0) {
			return null;
		}
		return {
			...data,
			state: data.state === 'on',
			lastToggled: parseInt(data.lastToggled, 10) || 0
		};
	},

	async createSwitch(uid, personalKey, switchConfig) {
		const switchData = {
			uid,
			personalKey,
			state: 'off',
			createdAt: Date.now(),
			lastToggled: 0,
			description: switchConfig.description || '',
			location: switchConfig.location || '',
			category: switchConfig.category || 'Other',
			publicize: switchConfig.publicize || false,
			toggleCount: 0
		};

		await this.client.hSet(`switch:${uid}`, switchData);
		await this.client.sAdd(`user:${personalKey}`, uid);

		if (switchConfig.publicize) {
			await this.client.sAdd('public_switches', uid);
		}

		return switchData;
	},

	async getUserSwitches(personalKey) {
		const switchUIDs = await this.client.sMembers(`user:${personalKey}`);
		const switches = [];
		for (const uid of switchUIDs) {
			const switchData = await this.getSwitchState(uid);
			if (switchData) {
				switches.push(switchData);
			}
		}
		return switches;
	},

	async getPublicSwitches() {
		const publicUIDs = await this.client.sMembers('public_switches');
		const switches = [];
		for (const uid of publicUIDs) {
			const switchData = await this.getSwitchState(uid);
			if (switchData && switchData.publicize) {
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
	},

	async incrementToggleCount(uid) {
		const current = await this.client.hGetAll(`switch:${uid}`);
		const newCount = (parseInt(current.toggleCount, 10) || 0) + 1;
		await this.client.hSet(`switch:${uid}`, 'toggleCount', newCount.toString());
	},

	async storePersonalKey(personalKey) {
		const keyData = {
			key: personalKey,
			createdAt: Date.now(),
			lastUsed: Date.now()
		};
		await this.client.hSet(`key:${personalKey}`, keyData);
		return keyData;
	},

	async validatePersonalKey(personalKey) {
		const keyData = await this.client.hGetAll(`key:${personalKey}`);
		if (!keyData || Object.keys(keyData).length === 0) {
			return false;
		}
		await this.client.hSet(`key:${personalKey}`, 'lastUsed', Date.now());
		return true;
	},

	async deletePersonalKey(personalKey) {
		const userSwitches = await this.getUserSwitches(personalKey);
		for (const switchData of userSwitches) {
			await this.client.del(`switch:${switchData.uid}`);
			await this.client.sRem('public_switches', switchData.uid);
		}
		await this.client.del(`key:${personalKey}`);
		await this.client.del(`user:${personalKey}`);
		return userSwitches.length;
	},

	async publishSwitchUpdate(uid, state) {
		const message = JSON.stringify({
			uid,
			state,
			timestamp: Date.now()
		});
		return this.pubClient.publish(`switch_updates:${uid}`, message);
	},

	async subscribeSwitchUpdates(uid, callback) {
		return this.subClient.subscribe(`switch_updates:${uid}`, callback);
	}
};

module.exports = mockRedisClient;
