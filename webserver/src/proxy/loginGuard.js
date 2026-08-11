/**
 * Failure-aware brute-force blocking for Home Assistant logins over the tunnel.
 *
 * The rate limit in uiProxy counts *requests*, which is the wrong unit for a
 * password guess: a browser loading the login page and a credential-stuffing
 * run spend the same budget, so the limit has to stay loose enough for the
 * former and is therefore useless against the latter.  This counts *failures*,
 * which only an attacker produces in quantity.
 *
 * It has to live here because Home Assistant cannot do it itself.  Core is
 * reached over loopback from inside the home, so every visitor on earth arrives
 * as 127.0.0.1: its own `login_attempts_threshold` sees one client, and a home
 * that switches it on would ban the relay — taking out all remote access for
 * everyone, at the whim of any stranger.  This proxy holds the only copy of the
 * real client address, so it is the only place the check can work.
 *
 * Deliberately per-IP only.  A per-home counter is kept for visibility, but
 * blocking on it would hand an attacker a lockout button: fail a few logins
 * from anywhere and the owner loses their own remote access.  A distributed
 * attack therefore gets logged, not blocked — the wrong trade to make silently.
 *
 * Failures expire on their own, and one success clears the record: an owner who
 * mistypes their password five times and then gets it right is not an attacker.
 */
const logger = require('../utils/logger');
const config = require('../config/config');
const redisClient = require('../utils/redis');

const FAIL_PREFIX = 'vome:loginfail';
const STRIKE_PREFIX = 'vome:loginstrike';
const BLOCK_PREFIX = 'vome:loginblock';

/**
 * Home Assistant's login flow answers a wrong password with **HTTP 200** and
 * the error inside the body — status codes alone cannot see a failed login.
 *
 * From `homeassistant/components/auth/login_flow.py`
 * (`_async_flow_result_to_response`): a form result whose `errors.base` is
 * `invalid_auth` or `invalid_code` is exactly the condition on which Core calls
 * its own `process_wrong_login`, and `create_entry` is the success case.  We
 * match that logic rather than inventing one, so we count what Core counts.
 *
 * This couples us to a response shape. `hacs-addon/tests/test_ha_compat_contract.py`
 * asserts it against the installed Home Assistant, so an upgrade that changes
 * it fails CI instead of silently switching the guard off.
 */
const AUTH_FAILURE_CODES = new Set(['invalid_auth', 'invalid_code']);

// A login flow step is a small JSON object; anything larger is not one, and
// parsing an arbitrarily large body on a hot path would be its own problem.
const MAX_CLASSIFY_BYTES = 64 * 1024;

/** True for the endpoints that progress a login flow. */
const LOGIN_FLOW_PATH_RE = /^\/auth\/login_flow(\/|$)/;

function isLoginFlowRequest(method, path) {
	return String(method || '').toUpperCase() === 'POST'
		&& LOGIN_FLOW_PATH_RE.test(String(path || '').split('?')[0]);
}

/**
 * Classify a login-flow response: 'failure', 'success', or null.
 *
 * null covers every legitimate in-between — an MFA form with no errors yet, a
 * malformed body, a flow that expired.  Only an unambiguous verdict counts,
 * because the cost of a false 'failure' is locking out a real user.
 */
function classifyLoginResponse(status, bodyB64) {
	if (status !== 200 || !bodyB64) {
		return null;
	}
	let parsed;
	try {
		const raw = Buffer.from(bodyB64, 'base64');
		if (raw.length > MAX_CLASSIFY_BYTES) {
			return null;
		}
		parsed = JSON.parse(raw.toString('utf8'));
	} catch {
		return null;
	}
	if (!parsed || typeof parsed !== 'object') {
		return null;
	}
	if (parsed.type === 'create_entry') {
		return 'success';
	}
	if (parsed.type === 'form'
		&& parsed.errors
		&& AUTH_FAILURE_CODES.has(parsed.errors.base)) {
		return 'failure';
	}
	return null;
}

/**
 * How long a block lasts, by how many times this client has been blocked
 * before.  Escalating, because a first offence is usually a fat-fingered owner
 * and a fifth is not; the last entry repeats for everything beyond it.
 */
function blockDurationMs(strikes) {
	const ladder = config.relay.loginBlockLadderMs;
	const index = Math.min(Math.max(strikes, 1), ladder.length) - 1;
	return ladder[index];
}

function createLoginGuard(deps = {}) {
	const redis = deps.redis || redisClient;
	const failMax = config.relay.loginFailMax;
	const failWindowSec = Math.ceil(config.relay.loginFailWindowMs / 1000);

	const key = (prefix, serverId, ip) => `${prefix}:${serverId}:${ip}`;

	/**
	 * Whether this client is currently barred from attempting a login.
	 *
	 * Fails **open** on a storage error, like the rate limiter: an unreachable
	 * Redis must not lock every customer out of their own home.
	 */
	async function isBlocked(serverId, ip) {
		try {
			const ttl = await redis.client.ttl(key(BLOCK_PREFIX, serverId, ip));
			if (typeof ttl === 'number' && ttl > 0) {
				return { retryAfter: ttl };
			}
		} catch (err) {
			logger.error('Login block check failed:', err.message || err);
		}
		return null;
	}

	/** Count one failed login; block the client once it passes the threshold. */
	async function recordFailure(serverId, ip) {
		try {
			const failKey = key(FAIL_PREFIX, serverId, ip);
			const failures = await redis.client.incr(failKey);
			if (failures === 1) {
				await redis.client.expire(failKey, failWindowSec);
			}
			// Per-home tally: visibility into a distributed attempt, which we
			// deliberately do not block on (see the file comment).
			const serverKey = `${FAIL_PREFIX}:${serverId}`;
			const serverFailures = await redis.client.incr(serverKey);
			if (serverFailures === 1) {
				await redis.client.expire(serverKey, failWindowSec);
			}
			if (failures < failMax) {
				return null;
			}
			// Strikes outlive the block itself, so coming back for more earns a
			// longer one rather than resetting to the shortest.
			const strikeKey = key(STRIKE_PREFIX, serverId, ip);
			const strikes = await redis.client.incr(strikeKey);
			await redis.client.expire(strikeKey, Math.ceil(config.relay.loginStrikeTtlMs / 1000));
			const durationMs = blockDurationMs(strikes);
			const seconds = Math.ceil(durationMs / 1000);
			await redis.client.set(key(BLOCK_PREFIX, serverId, ip), String(Date.now() + durationMs), {
				EX: seconds
			});
			await redis.client.del(failKey);
			logger.warn(
				`Blocked ${ip} from ${serverId} login for ${seconds}s `
				+ `(${failures} failures, strike ${strikes}, ${serverFailures} on this home)`
			);
			return { retryAfter: seconds };
		} catch (err) {
			logger.error('Login failure record failed:', err.message || err);
			return null;
		}
	}

	/** A success proves the client is legitimate — wipe its record. */
	async function recordSuccess(serverId, ip) {
		try {
			await redis.client.del(key(FAIL_PREFIX, serverId, ip));
			await redis.client.del(key(STRIKE_PREFIX, serverId, ip));
		} catch (err) {
			logger.error('Login success record failed:', err.message || err);
		}
	}

	/** Apply a classified login-flow outcome. Returns a block, if one started. */
	async function observe(serverId, ip, verdict) {
		if (verdict === 'failure') {
			return recordFailure(serverId, ip);
		}
		if (verdict === 'success') {
			await recordSuccess(serverId, ip);
		}
		return null;
	}

	return { isBlocked, recordFailure, recordSuccess, observe };
}

module.exports = {
	createLoginGuard,
	// Exposed for unit tests.
	classifyLoginResponse,
	isLoginFlowRequest,
	blockDurationMs,
	AUTH_FAILURE_CODES,
	LOGIN_FLOW_PATH_RE
};
