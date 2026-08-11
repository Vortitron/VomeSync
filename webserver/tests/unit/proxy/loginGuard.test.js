/**
 * Home Assistant cannot see who is guessing its passwords over the relay —
 * everyone arrives as 127.0.0.1 — so this guard is the only thing standing
 * between a friendly domain in `open` mode and unlimited password attempts.
 */
const {
	createLoginGuard,
	classifyLoginResponse,
	isLoginFlowRequest,
	blockDurationMs
} = require('../../../src/proxy/loginGuard');
const config = require('../../../src/config/config');

const b64 = (obj) => Buffer.from(JSON.stringify(obj)).toString('base64');

/** Minimal in-memory Redis with the handful of commands the guard uses. */
function fakeRedis() {
	const store = new Map(); // key -> { value, expiresAt }
	const live = (key) => {
		const hit = store.get(key);
		if (!hit) return null;
		if (hit.expiresAt && hit.expiresAt <= Date.now()) {
			store.delete(key);
			return null;
		}
		return hit;
	};
	return {
		store,
		client: {
			async incr(key) {
				const hit = live(key);
				const next = (hit ? Number(hit.value) : 0) + 1;
				store.set(key, { value: String(next), expiresAt: hit ? hit.expiresAt : 0 });
				return next;
			},
			async expire(key, seconds) {
				const hit = live(key);
				if (hit) hit.expiresAt = Date.now() + seconds * 1000;
				return 1;
			},
			async set(key, value, opts = {}) {
				store.set(key, {
					value,
					expiresAt: opts.EX ? Date.now() + opts.EX * 1000 : 0
				});
				return 'OK';
			},
			async del(key) {
				return store.delete(key) ? 1 : 0;
			},
			async ttl(key) {
				const hit = live(key);
				if (!hit) return -2;
				if (!hit.expiresAt) return -1;
				return Math.ceil((hit.expiresAt - Date.now()) / 1000);
			}
		}
	};
}

describe('loginGuard.classifyLoginResponse', () => {
	// The whole guard rests on this: HA returns 200 for a wrong password, so a
	// status-code check would see every failed login as a success.
	test('a wrong password is a failure despite the 200', () => {
		expect(classifyLoginResponse(200, b64({
			type: 'form', errors: { base: 'invalid_auth' }
		}))).toBe('failure');
	});

	test('a wrong MFA code counts too', () => {
		expect(classifyLoginResponse(200, b64({
			type: 'form', errors: { base: 'invalid_code' }
		}))).toBe('failure');
	});

	test('a completed login is a success', () => {
		expect(classifyLoginResponse(200, b64({
			type: 'create_entry', result: 'auth-code'
		}))).toBe('success');
	});

	// Anything ambiguous must not count: a false failure locks out a real user.
	test.each([
		['an MFA prompt with no error yet', 200, b64({ type: 'form', errors: {} })],
		['a form with an unrelated error', 200, b64({ type: 'form', errors: { base: 'too_many_retries' } })],
		['a non-200 response', 400, b64({ type: 'form', errors: { base: 'invalid_auth' } })],
		['a body that is not JSON', 200, Buffer.from('<html>').toString('base64')],
		['an empty body', 200, undefined],
		['a JSON array', 200, b64([1, 2, 3])]
	])('%s is not counted', (_label, status, body) => {
		expect(classifyLoginResponse(status, body)).toBeNull();
	});

	test('refuses to parse an implausibly large body', () => {
		const huge = Buffer.alloc(64 * 1024 + 1, 0x20).toString('base64');
		expect(classifyLoginResponse(200, huge)).toBeNull();
	});
});

describe('loginGuard.isLoginFlowRequest', () => {
	test.each([
		['POST', '/auth/login_flow', true],
		['POST', '/auth/login_flow/abc123', true],
		['POST', '/auth/login_flow/abc123?x=1', true],
		['GET', '/auth/login_flow/abc123', false],
		['POST', '/auth/token', false],
		['POST', '/api/states', false],
		['POST', '/auth/login_flowery', false]
	])('%s %s -> %s', (method, path, expected) => {
		expect(isLoginFlowRequest(method, path)).toBe(expected);
	});
});

describe('loginGuard blocking', () => {
	const IP = '203.0.113.9';
	const SERVER = 'rly-1';

	test('lets the threshold through, then blocks', async () => {
		const redis = fakeRedis();
		const guard = createLoginGuard({ redis });
		for (let i = 1; i < config.relay.loginFailMax; i += 1) {
			expect(await guard.recordFailure(SERVER, IP)).toBeNull();
			expect(await guard.isBlocked(SERVER, IP)).toBeNull();
		}
		const blocked = await guard.recordFailure(SERVER, IP);
		expect(blocked.retryAfter).toBeGreaterThan(0);
		expect((await guard.isBlocked(SERVER, IP)).retryAfter).toBeGreaterThan(0);
	});

	test('a success clears the record — a mistyped password is not an attack', async () => {
		const redis = fakeRedis();
		const guard = createLoginGuard({ redis });
		for (let i = 1; i < config.relay.loginFailMax; i += 1) {
			await guard.recordFailure(SERVER, IP);
		}
		await guard.recordSuccess(SERVER, IP);
		// The next failure starts a fresh count rather than tipping the block.
		expect(await guard.recordFailure(SERVER, IP)).toBeNull();
		expect(await guard.isBlocked(SERVER, IP)).toBeNull();
	});

	test('coming back for more earns a longer block', async () => {
		const redis = fakeRedis();
		const guard = createLoginGuard({ redis });
		const trip = async () => {
			for (let i = 0; i < config.relay.loginFailMax; i += 1) {
				const res = await guard.recordFailure(SERVER, IP);
				if (res) return res;
			}
			return null;
		};
		const first = await trip();
		// Clear the block itself, but not the strikes it recorded.
		redis.store.delete(`vome:loginblock:${SERVER}:${IP}`);
		const second = await trip();
		expect(second.retryAfter).toBeGreaterThan(first.retryAfter);
	});

	test('blocks are per client and per home, not global', async () => {
		const redis = fakeRedis();
		const guard = createLoginGuard({ redis });
		for (let i = 0; i < config.relay.loginFailMax; i += 1) {
			await guard.recordFailure(SERVER, IP);
		}
		expect(await guard.isBlocked(SERVER, '198.51.100.4')).toBeNull();
		expect(await guard.isBlocked('rly-2', IP)).toBeNull();
	});

	test('observe applies a verdict and ignores a null one', async () => {
		const redis = fakeRedis();
		const guard = createLoginGuard({ redis });
		await guard.observe(SERVER, IP, null);
		for (let i = 1; i < config.relay.loginFailMax; i += 1) {
			await guard.observe(SERVER, IP, 'failure');
		}
		expect(await guard.observe(SERVER, IP, 'failure')).not.toBeNull();
	});

	// Redis being unreachable must not lock every customer out of their home.
	test('fails open when the store is broken', async () => {
		const broken = {
			client: {
				incr: async () => { throw new Error('down'); },
				expire: async () => { throw new Error('down'); },
				set: async () => { throw new Error('down'); },
				del: async () => { throw new Error('down'); },
				ttl: async () => { throw new Error('down'); }
			}
		};
		const guard = createLoginGuard({ redis: broken });
		expect(await guard.isBlocked(SERVER, IP)).toBeNull();
		expect(await guard.recordFailure(SERVER, IP)).toBeNull();
		await guard.recordSuccess(SERVER, IP);
	});
});

describe('loginGuard.blockDurationMs', () => {
	test('escalates and then holds at the longest', () => {
		const ladder = config.relay.loginBlockLadderMs;
		expect(blockDurationMs(1)).toBe(ladder[0]);
		expect(blockDurationMs(ladder.length)).toBe(ladder[ladder.length - 1]);
		expect(blockDurationMs(ladder.length + 99)).toBe(ladder[ladder.length - 1]);
	});
});
