/**
 * Unit tests for the full-UI forwarding access gate (HS256 cookie verify).
 *
 * The secret must be present before config is required, so it is set here at the
 * top of the module (jest gives each test file its own module registry).
 */
process.env.RELAY_FORWARD_SECRET = 'unit-test-forward-secret';

const jwt = require('jsonwebtoken');
const { verifyAccessToken, mintAccessToken, readCookie, SCOPE } = require('../../../src/proxy/uiAccess');

describe('uiAccess.verifyAccessToken', () => {
	test('round-trips a minted token bound to its host', () => {
		const token = mintAccessToken({ serverId: 'rly-1', userId: 'u1', host: 'nyvyn.vome.io' });
		expect(verifyAccessToken(token, 'nyvyn.vome.io')).toEqual({ serverId: 'rly-1', userId: 'u1' });
	});

	test('is case-insensitive on the host', () => {
		const token = mintAccessToken({ serverId: 'rly-1', userId: 'u1', host: 'Nyvyn.Vome.IO' });
		expect(verifyAccessToken(token, 'nyvyn.vome.io')).toMatchObject({ serverId: 'rly-1' });
	});

	test('rejects a token presented on a different host', () => {
		const token = mintAccessToken({ serverId: 'rly-1', userId: 'u1', host: 'a.vome.io' });
		expect(verifyAccessToken(token, 'b.vome.io')).toBeNull();
	});

	test('rejects an expired token', () => {
		const token = mintAccessToken({ serverId: 'rly-1', userId: 'u1', host: 'a.vome.io', ttlSeconds: -10 });
		expect(verifyAccessToken(token, 'a.vome.io')).toBeNull();
	});

	test('rejects a token with the wrong scope', () => {
		const token = jwt.sign(
			{ sub: 'u1', sid: 'rly-1', host: 'a.vome.io', scope: 'something-else' },
			process.env.RELAY_FORWARD_SECRET, { algorithm: 'HS256', expiresIn: 60 }
		);
		expect(verifyAccessToken(token, 'a.vome.io')).toBeNull();
	});

	test('rejects a token without a server id', () => {
		const token = jwt.sign(
			{ sub: 'u1', host: 'a.vome.io', scope: SCOPE },
			process.env.RELAY_FORWARD_SECRET, { algorithm: 'HS256', expiresIn: 60 }
		);
		expect(verifyAccessToken(token, 'a.vome.io')).toBeNull();
	});

	test('rejects a token signed with the wrong secret', () => {
		const token = jwt.sign(
			{ sub: 'u1', sid: 'rly-1', host: 'a.vome.io', scope: SCOPE },
			'not-the-secret', { algorithm: 'HS256', expiresIn: 60 }
		);
		expect(verifyAccessToken(token, 'a.vome.io')).toBeNull();
	});

	test('returns null for an empty token', () => {
		expect(verifyAccessToken('', 'a.vome.io')).toBeNull();
		expect(verifyAccessToken(null, 'a.vome.io')).toBeNull();
	});
});

describe('uiAccess.readCookie', () => {
	test('extracts a named cookie and URL-decodes it', () => {
		const req = { headers: { cookie: 'foo=1; vome_fwd=ab%20cd; bar=2' } };
		expect(readCookie(req, 'vome_fwd')).toBe('ab cd');
		expect(readCookie(req, 'foo')).toBe('1');
	});

	test('returns empty string when absent', () => {
		expect(readCookie({ headers: {} }, 'vome_fwd')).toBe('');
		expect(readCookie({}, 'vome_fwd')).toBe('');
	});
});
