/**
 * Unit tests for the `/ws/tcp` bearer-token upgrade gate.
 *
 * The manager is a factory taking { relayManager, verifyLanTcpToken } so we can
 * drive it with fakes — no real relay socket or JWT secret needed. The actual
 * byte-pumping (relayBridge.bridgeSocket) is shared with uiProxy.js's browser
 * bridge and is covered there (`uiProxy.test.js`'s `describe('uiProxy.bridge', ...)`
 * block); this file only covers the auth/offline gating this module adds.
 */
const { createTcpTunnelManager } = require('../../../src/websocket/tcpTunnelManager');

function fakeSocket() {
	return {
		writable: true, written: '', destroyed: false,
		write(s) { this.written += s; }, destroy() { this.destroyed = true; }
	};
}

function fakeReq({ headers = {} } = {}) {
	return { headers };
}

describe('tcpTunnelManager.handleUpgrade', () => {
	test('aborts with 401 when no Authorization header is present', () => {
		const mgr = createTcpTunnelManager({
			relayManager: { isConnected: () => true },
			verifyLanTcpToken: () => { throw new Error('should not be called'); }
		});
		const socket = fakeSocket();
		mgr.handleUpgrade(fakeReq(), socket, Buffer.alloc(0));
		expect(socket.written).toContain('401');
		expect(socket.destroyed).toBe(true);
	});

	test('aborts with 401 when the bearer token fails verification', () => {
		const mgr = createTcpTunnelManager({
			relayManager: { isConnected: () => true },
			verifyLanTcpToken: () => null
		});
		const socket = fakeSocket();
		mgr.handleUpgrade(fakeReq({ headers: { authorization: 'Bearer bad' } }), socket, Buffer.alloc(0));
		expect(socket.written).toContain('401');
		expect(socket.destroyed).toBe(true);
	});

	test('accepts a lowercase "bearer" scheme and trims the token', () => {
		let seen = null;
		const mgr = createTcpTunnelManager({
			relayManager: { isConnected: () => false },
			verifyLanTcpToken: (token) => { seen = token; return { serverId: 'rly-1', slug: 'rdp' }; }
		});
		const socket = fakeSocket();
		mgr.handleUpgrade(fakeReq({ headers: { authorization: 'bearer  abc123  ' } }), socket, Buffer.alloc(0));
		expect(seen).toBe('abc123');
	});

	test('aborts with 502 when the linked Home Assistant is offline', () => {
		const mgr = createTcpTunnelManager({
			relayManager: { isConnected: () => false },
			verifyLanTcpToken: () => ({ serverId: 'rly-1', slug: 'rdp' })
		});
		const socket = fakeSocket();
		mgr.handleUpgrade(fakeReq({ headers: { authorization: 'Bearer good' } }), socket, Buffer.alloc(0));
		expect(socket.written).toContain('502');
		expect(socket.destroyed).toBe(true);
	});

	test('checks isConnected against the token\'s serverId, not a caller-supplied one', () => {
		const seenIds = [];
		const mgr = createTcpTunnelManager({
			relayManager: { isConnected: (id) => { seenIds.push(id); return false; } },
			verifyLanTcpToken: () => ({ serverId: 'rly-from-token', slug: 'rdp' })
		});
		const socket = fakeSocket();
		mgr.handleUpgrade(fakeReq({ headers: { authorization: 'Bearer good' } }), socket, Buffer.alloc(0));
		expect(seenIds).toEqual(['rly-from-token']);
	});
});
