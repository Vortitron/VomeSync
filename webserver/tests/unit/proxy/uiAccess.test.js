/**
 * Unit tests for the full-UI forwarding access gate (HS256 cookie verify).
 *
 * The secret must be present before config is required, so it is set here at the
 * top of the module (jest gives each test file its own module registry).
 */
process.env.RELAY_FORWARD_SECRET = 'unit-test-forward-secret';

const jwt = require('jsonwebtoken');
const {
	verifyAccessToken, mintAccessToken, readCookie, SCOPE,
	mintLanTcpToken, verifyLanTcpToken, LAN_TCP_SCOPE
} = require('../../../src/proxy/uiAccess');

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

describe('uiAccess.verifyLanTcpToken', () => {
	test('round-trips a minted token bound to server + slug', () => {
		const token = mintLanTcpToken({ serverId: 'rly-1', slug: 'rdp' });
		expect(verifyLanTcpToken(token)).toEqual({ serverId: 'rly-1', slug: 'rdp' });
	});

	test('rejects an expired token', () => {
		// Sign an already-expired token directly: mintLanTcpToken now clamps a
		// non-positive TTL up to the default, so it can no longer mint one.
		const token = jwt.sign(
			{ sid: 'rly-1', slug: 'rdp', scope: LAN_TCP_SCOPE },
			process.env.RELAY_FORWARD_SECRET, { algorithm: 'HS256', expiresIn: -10 }
		);
		expect(verifyLanTcpToken(token)).toBeNull();
	});

	test('clamps an absurd TTL to the 24h maximum', () => {
		const token = mintLanTcpToken({ serverId: 'rly-1', slug: 'rdp', ttlSeconds: 999999999 });
		const decoded = jwt.decode(token);
		expect(decoded.exp - decoded.iat).toBeLessThanOrEqual(24 * 60 * 60);
	});

	test('clamps a non-positive TTL up to a valid lifetime', () => {
		const token = mintLanTcpToken({ serverId: 'rly-1', slug: 'rdp', ttlSeconds: -10 });
		expect(verifyLanTcpToken(token)).toEqual({ serverId: 'rly-1', slug: 'rdp' });
	});

	test('rejects a token with the wrong scope', () => {
		const token = jwt.sign(
			{ sid: 'rly-1', slug: 'rdp', scope: 'something-else' },
			process.env.RELAY_FORWARD_SECRET, { algorithm: 'HS256', expiresIn: 60 }
		);
		expect(verifyLanTcpToken(token)).toBeNull();
	});

	test('rejects a token missing slug or server id', () => {
		const noSlug = jwt.sign(
			{ sid: 'rly-1', scope: LAN_TCP_SCOPE },
			process.env.RELAY_FORWARD_SECRET, { algorithm: 'HS256', expiresIn: 60 }
		);
		const noSid = jwt.sign(
			{ slug: 'rdp', scope: LAN_TCP_SCOPE },
			process.env.RELAY_FORWARD_SECRET, { algorithm: 'HS256', expiresIn: 60 }
		);
		expect(verifyLanTcpToken(noSlug)).toBeNull();
		expect(verifyLanTcpToken(noSid)).toBeNull();
	});

	test('rejects a token signed with the wrong secret', () => {
		const token = jwt.sign(
			{ sid: 'rly-1', slug: 'rdp', scope: LAN_TCP_SCOPE },
			'not-the-secret', { algorithm: 'HS256', expiresIn: 60 }
		);
		expect(verifyLanTcpToken(token)).toBeNull();
	});

	test('a ha-forward token does not verify as lan-tcp and vice versa', () => {
		const forward = mintAccessToken({ serverId: 'rly-1', userId: 'u1', host: 'a.vome.io' });
		expect(verifyLanTcpToken(forward)).toBeNull();
		const tcp = mintLanTcpToken({ serverId: 'rly-1', slug: 'rdp' });
		expect(verifyAccessToken(tcp, 'a.vome.io')).toBeNull();
	});

	test('returns null for an empty token', () => {
		expect(verifyLanTcpToken('')).toBeNull();
		expect(verifyLanTcpToken(null)).toBeNull();
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
