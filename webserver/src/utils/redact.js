const SENSITIVE_KEYS = new Set([
	'personalKey',
	'apiKey',
	'accessKey',
	'token',
	'jwt',
	'authorization',
	'cookie',
	'set-cookie',
	'password',
	'redis_password',
	'redisPassword',
	'x-api-key',
	'x-personal-key'
]);

function _isPlainObject(value) {
	return Boolean(value) && typeof value === 'object' && Object.getPrototypeOf(value) === Object.prototype;
}

function _redactString(value) {
	if (typeof value !== 'string') {
		return value;
	}
	// URL fragments like #accessKey=...
	const withoutFragmentKey = value.replace(/(accessKey=)[^&\s]+/gi, '$1[REDACTED]');
	// Header-like strings
	const withoutHeaderKey = withoutFragmentKey.replace(/(x-(?:api|personal)-key:\s*)([^\s,]+)/gi, '$1[REDACTED]');
	return withoutHeaderKey;
}

function redact(value, depth = 0, seen = undefined) {
	if (value === null || value === undefined) {
		return value;
	}
	if (typeof value === 'string') {
		return _redactString(value);
	}
	if (typeof value !== 'object') {
		return value;
	}

	const nextSeen = seen || new WeakSet();
	if (nextSeen.has(value)) {
		return '[REDACTED_CYCLE]';
	}
	nextSeen.add(value);

	if (depth > 8) {
		return '[REDACTED_DEPTH]';
	}

	if (Array.isArray(value)) {
		return value.map((item) => redact(item, depth + 1, nextSeen));
	}

	// Preserve Error shape but redact message strings inside (if any)
	if (value instanceof Error) {
		const out = {};
		for (const key of Object.keys(value)) {
			out[key] = redact(value[key], depth + 1, nextSeen);
		}
		out.message = _redactString(value.message);
		out.name = value.name;
		if (typeof value.stack === 'string') {
			out.stack = _redactString(value.stack);
		}
		return out;
	}

	if (_isPlainObject(value)) {
		const out = {};
		for (const [k, v] of Object.entries(value)) {
			if (SENSITIVE_KEYS.has(k)) {
				out[k] = '[REDACTED]';
				continue;
			}
			const lower = String(k).toLowerCase();
			if (SENSITIVE_KEYS.has(lower)) {
				out[k] = '[REDACTED]';
				continue;
			}
			out[k] = redact(v, depth + 1, nextSeen);
		}
		return out;
	}

	// Non-plain objects: best-effort shallow copy
	try {
		const out = {};
		for (const key of Object.keys(value)) {
			const lower = String(key).toLowerCase();
			if (SENSITIVE_KEYS.has(key) || SENSITIVE_KEYS.has(lower)) {
				out[key] = '[REDACTED]';
			} else {
				out[key] = redact(value[key], depth + 1, nextSeen);
			}
		}
		return out;
	} catch (_err) {
		return '[REDACTED_UNSERIALISABLE]';
	}
}

module.exports = redact;


