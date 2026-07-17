/**
 * Unit tests for the relay control channel (outbound tunnel).
 *
 * The RPC layer is the interesting bit: dispatch() must send an `ha_rpc`, match
 * the component's `ha_rpc_response` by requestId, return offline when nothing is
 * connected, and time out cleanly.  We drive a fresh RelayManager with fake
 * sockets so no real WebSocket server / Redis is needed.
 */
process.env.RELAY_FORWARD_SECRET = process.env.RELAY_FORWARD_SECRET || 'unit-test-forward-secret';

const WebSocket = require('ws');
const relaySingleton = require('../../../src/websocket/relayManager');
const uiAccess = require('../../../src/proxy/uiAccess');

const { RelayManager, _extractSecret } = relaySingleton;

function fakeSocket() {
	return {
		readyState: WebSocket.OPEN,
		sent: [],
		closed: null,
		handlers: {},
		on(event, handler) { this.handlers[event] = handler; return this; },
		send(payload) { this.sent.push(payload); },
		close(code, reason) { this.closed = { code, reason }; this.readyState = WebSocket.CLOSED; },
		ping() { this.pinged = true; }
	};
}

function connect(mgr, serverId) {
	const ws = fakeSocket();
	mgr.connections.set(serverId, { ws, connectedAt: Date.now(), lastActivity: Date.now() });
	return ws;
}

describe('RelayManager.dispatch', () => {
	let mgr;
	beforeEach(() => { mgr = new RelayManager(); });

	test('returns offline when no component is connected', async () => {
		await expect(mgr.dispatch('rly-missing', { path: '/api/states' }))
			.resolves.toEqual({ offline: true });
	});

	test('returns offline when the socket is not OPEN', async () => {
		const ws = connect(mgr, 'rly-1');
		ws.readyState = WebSocket.CLOSING;
		await expect(mgr.dispatch('rly-1', { path: '/api/states' }))
			.resolves.toEqual({ offline: true });
	});

	test('sends ha_rpc and resolves on the matching response', async () => {
		const ws = connect(mgr, 'rly-1');
		const promise = mgr.dispatch('rly-1', { method: 'GET', path: '/api/states/light.k', timeout: 5 });

		expect(ws.sent).toHaveLength(1);
		const sent = JSON.parse(ws.sent[0]);
		expect(sent.type).toBe('ha_rpc');
		expect(sent.method).toBe('GET');
		expect(sent.path).toBe('/api/states/light.k');
		expect(typeof sent.requestId).toBe('string');

		mgr.handleMessage('rly-1', JSON.stringify({
			type: 'ha_rpc_response', requestId: sent.requestId, status: 200, body: '{"state":"on"}'
		}));

		await expect(promise).resolves.toEqual({ status: 200, body: '{"state":"on"}', error: undefined });
		expect(mgr.pending.size).toBe(0);
	});

	test('forwards the target (esphome) in the ha_rpc, omitting it for core', async () => {
		const ws = connect(mgr, 'rly-1');
		const espP = mgr.dispatch('rly-1', { method: 'GET', path: '/devices', target: 'esphome', timeout: 5 });
		const esp = JSON.parse(ws.sent[0]);
		expect(esp.target).toBe('esphome');
		expect(esp.path).toBe('/devices');

		const coreP = mgr.dispatch('rly-1', { method: 'GET', path: '/api/states', timeout: 5 });
		const core = JSON.parse(ws.sent[1]);
		expect('target' in core).toBe(false); // undefined target is dropped → back-compat

		// Resolve both so no RPC timers linger past the test.
		mgr.handleMessage('rly-1', JSON.stringify({ type: 'ha_rpc_response', requestId: esp.requestId, status: 200 }));
		mgr.handleMessage('rly-1', JSON.stringify({ type: 'ha_rpc_response', requestId: core.requestId, status: 200 }));
		await Promise.all([espP, coreP]);
		expect(mgr.pending.size).toBe(0);
	});

	test('ignores a response with an unknown requestId', async () => {
		const ws = connect(mgr, 'rly-1');
		const promise = mgr.dispatch('rly-1', { path: '/api/states', timeout: 5 });
		const sent = JSON.parse(ws.sent[0]);

		mgr.handleMessage('rly-1', JSON.stringify({ type: 'ha_rpc_response', requestId: 'nope', status: 200 }));
		expect(mgr.pending.size).toBe(1); // still waiting

		mgr.handleMessage('rly-1', JSON.stringify({ type: 'ha_rpc_response', requestId: sent.requestId, status: 204 }));
		await expect(promise).resolves.toMatchObject({ status: 204 });
	});

	test('times out and resolves status 0', async () => {
		jest.useFakeTimers();
		try {
			connect(mgr, 'rly-1');
			const promise = mgr.dispatch('rly-1', { path: '/api/states', timeout: 3 });
			jest.advanceTimersByTime(3 * 1000 + 2000 + 50);
			await expect(promise).resolves.toEqual({ status: 0, error: 'Relay RPC timed out.' });
			expect(mgr.pending.size).toBe(0);
		} finally {
			jest.useRealTimers();
		}
	});

	test('resolves status 0 when send throws', async () => {
		const ws = connect(mgr, 'rly-1');
		ws.send = () => { throw new Error('socket gone'); };
		await expect(mgr.dispatch('rly-1', { path: '/api/states' }))
			.resolves.toEqual({ status: 0, error: 'Relay send failed.' });
		expect(mgr.pending.size).toBe(0);
	});
});

describe('RelayManager.forwardHttp', () => {
	let mgr;
	beforeEach(() => { mgr = new RelayManager(); });

	test('returns offline when no component is connected', async () => {
		await expect(mgr.forwardHttp('rly-missing', { path: '/lovelace' }))
			.resolves.toEqual({ offline: true });
	});

	test('sends http_proxy and resolves on the matching response', async () => {
		const ws = connect(mgr, 'rly-1');
		const promise = mgr.forwardHttp('rly-1', {
			method: 'GET', path: '/manifest.json', headers: [['Cookie', 'z=9']]
		});
		const sent = JSON.parse(ws.sent[0]);
		expect(sent.type).toBe('http_proxy');
		expect(sent.path).toBe('/manifest.json');
		expect(sent.headers).toEqual([['Cookie', 'z=9']]);
		expect(typeof sent.requestId).toBe('string');

		mgr.handleMessage('rly-1', JSON.stringify({
			type: 'http_proxy_response', requestId: sent.requestId,
			status: 200, headers: [['Content-Type', 'text/html']], bodyB64: 'aGk='
		}));
		await expect(promise).resolves.toEqual({
			status: 200, headers: [['Content-Type', 'text/html']], bodyB64: 'aGk=', error: undefined
		});
		expect(mgr.pending.size).toBe(0);
	});

	test('times out and resolves status 0', async () => {
		jest.useFakeTimers();
		try {
			connect(mgr, 'rly-1');
			const promise = mgr.forwardHttp('rly-1', { path: '/' });
			jest.advanceTimersByTime(60000 + 2000 + 50);
			await expect(promise).resolves.toEqual({ status: 0, error: 'Relay HTTP forward timed out.' });
			expect(mgr.pending.size).toBe(0);
		} finally {
			jest.useRealTimers();
		}
	});

	test('resolves status 0 when send throws', async () => {
		const ws = connect(mgr, 'rly-1');
		ws.send = () => { throw new Error('socket gone'); };
		await expect(mgr.forwardHttp('rly-1', { path: '/' }))
			.resolves.toEqual({ status: 0, error: 'Relay send failed.' });
		expect(mgr.pending.size).toBe(0);
	});
});

describe('RelayManager WebSocket tunnel bridge', () => {
	let mgr;
	beforeEach(() => { mgr = new RelayManager(); });

	// Callbacks are stored unbound, so the real consumer (uiProxy) passes
	// closures — model that here with arrow fns over a shared state object.
	function fakeTunnel() {
		const state = { acks: 0, frames: [], closed: null };
		return {
			state,
			onAck: () => { state.acks += 1; },
			onData: (d) => { state.frames.push(d); },
			onClose: (d) => { state.closed = d; }
		};
	}

	test('openWs sends ws_open down the component socket', () => {
		const ws = connect(mgr, 'rly-1');
		const ok = mgr.openWs('rly-1', { socketId: 's1', path: '/api/websocket', headers: [] });
		expect(ok).toBe(true);
		const sent = JSON.parse(ws.sent[0]);
		expect(sent).toMatchObject({ type: 'ws_open', socketId: 's1', path: '/api/websocket' });
	});

	test('openWs returns false when the component is offline', () => {
		expect(mgr.openWs('rly-missing', { socketId: 's1' })).toBe(false);
	});

	test('routes ack/data/close to the registered tunnel and unregisters on close', () => {
		connect(mgr, 'rly-1');
		const tunnel = fakeTunnel();
		mgr.registerTunnel('s1', 'rly-1', tunnel);

		mgr.handleMessage('rly-1', JSON.stringify({ type: 'ws_open_ack', socketId: 's1' }));
		mgr.handleMessage('rly-1', JSON.stringify({ type: 'ws_data', socketId: 's1', text: 'hello' }));
		expect(tunnel.state.acks).toBe(1);
		expect(tunnel.state.frames).toEqual([{ type: 'ws_data', socketId: 's1', text: 'hello' }]);

		mgr.handleMessage('rly-1', JSON.stringify({ type: 'ws_close', socketId: 's1', code: 1000 }));
		expect(tunnel.state.closed).toMatchObject({ code: 1000 });
		expect(mgr.tunnels.has('s1')).toBe(false);
	});

	test('frames for an unknown socketId are ignored', () => {
		connect(mgr, 'rly-1');
		expect(() => mgr.handleMessage('rly-1', JSON.stringify({ type: 'ws_data', socketId: 'ghost' })))
			.not.toThrow();
	});

	test('sendWs and closeWs forward frames to the component', () => {
		const ws = connect(mgr, 'rly-1');
		mgr.sendWs('rly-1', { socketId: 's1', text: 'ping' });
		mgr.closeWs('rly-1', { socketId: 's1', code: 1000, reason: 'bye' });
		const types = ws.sent.map((s) => JSON.parse(s));
		expect(types[0]).toMatchObject({ type: 'ws_data', socketId: 's1', text: 'ping' });
		expect(types[1]).toMatchObject({ type: 'ws_close', socketId: 's1', code: 1000 });
	});

	test('a relay disconnect closes every tunnel bound to that server', () => {
		const ws = connect(mgr, 'rly-1');
		const t1 = fakeTunnel();
		const t2 = fakeTunnel();
		mgr.registerTunnel('s1', 'rly-1', t1);
		mgr.registerTunnel('s2', 'rly-2', t2);
		mgr.handleDisconnection('rly-1', ws);
		expect(t1.state.closed).toMatchObject({ code: 1011 });
		expect(t2.state.closed).toBeNull();
		expect(mgr.tunnels.has('s1')).toBe(false);
		expect(mgr.tunnels.has('s2')).toBe(true);
	});
});

describe('RelayManager.handleMessage', () => {
	let mgr;
	beforeEach(() => { mgr = new RelayManager(); });

	test('ignores non-JSON and unknown types without throwing', () => {
		connect(mgr, 'rly-1');
		expect(() => mgr.handleMessage('rly-1', 'not json')).not.toThrow();
		expect(() => mgr.handleMessage('rly-1', JSON.stringify({ type: 'mystery' }))).not.toThrow();
	});

	test('replies to a ping with a pong', () => {
		const ws = connect(mgr, 'rly-1');
		mgr.handleMessage('rly-1', JSON.stringify({ type: 'ping' }));
		const replies = ws.sent.map((s) => JSON.parse(s));
		expect(replies.some((r) => r.type === 'pong')).toBe(true);
	});
});

describe('RelayManager.handleMessage mint_lan_tcp_token', () => {
	let mgr;
	beforeEach(() => { mgr = new RelayManager(); });

	test('mints a lan-tcp token and replies on the same socket', () => {
		const ws = connect(mgr, 'rly-1');
		mgr.handleMessage('rly-1', JSON.stringify({
			type: 'mint_lan_tcp_token', requestId: 'req-1', slug: 'rdp', ttlSeconds: 300
		}));

		expect(ws.sent).toHaveLength(1);
		const reply = JSON.parse(ws.sent[0]);
		expect(reply.type).toBe('mint_lan_tcp_token_response');
		expect(reply.requestId).toBe('req-1');
		expect(typeof reply.token).toBe('string');

		const verified = uiAccess.verifyLanTcpToken(reply.token);
		expect(verified).toEqual({ serverId: 'rly-1', slug: 'rdp' });
	});

	test('is a no-op when no component is connected for that server', () => {
		expect(() => mgr.handleMessage('rly-missing', JSON.stringify({
			type: 'mint_lan_tcp_token', requestId: 'req-1', slug: 'rdp'
		}))).not.toThrow();
	});
});

describe('RelayManager registry', () => {
	let mgr;
	beforeEach(() => { mgr = new RelayManager(); });

	test('isConnected reflects an OPEN socket', () => {
		const ws = connect(mgr, 'rly-1');
		expect(mgr.isConnected('rly-1')).toBe(true);
		ws.readyState = WebSocket.CLOSED;
		expect(mgr.isConnected('rly-1')).toBe(false);
		expect(mgr.isConnected('rly-other')).toBe(false);
	});

	test('handleConnection replaces a previous connection for the same server', () => {
		const oldWs = fakeSocket();
		mgr.handleConnection(oldWs, { relayServerId: 'rly-1' });
		const newWs = fakeSocket();
		mgr.handleConnection(newWs, { relayServerId: 'rly-1' });
		expect(oldWs.closed).toBeTruthy();
		expect(mgr.connections.get('rly-1').ws).toBe(newWs);
		expect(mgr.connections.size).toBe(1);
	});

	test('handleConnection without a server id closes the socket', () => {
		const ws = fakeSocket();
		mgr.handleConnection(ws, {});
		expect(ws.closed).toMatchObject({ code: 4001 });
		expect(mgr.connections.size).toBe(0);
	});

	test('handleDisconnection only removes the matching socket', () => {
		const ws = connect(mgr, 'rly-1');
		mgr.handleDisconnection('rly-1', fakeSocket()); // different socket → keep
		expect(mgr.connections.size).toBe(1);
		mgr.handleDisconnection('rly-1', ws); // same socket → remove
		expect(mgr.connections.size).toBe(0);
	});

	test('getStats summarises the registry', () => {
		connect(mgr, 'rly-1');
		connect(mgr, 'rly-2');
		const stats = mgr.getStats();
		expect(stats.relays).toBe(2);
		expect(stats.servers.sort()).toEqual(['rly-1', 'rly-2']);
	});

	test('disconnect force-closes and deregisters the socket', () => {
		// Revocation path: the portal deletes a link / rotates its secret and
		// must be able to drop the live socket (secrets are only verified at
		// connect time).
		const ws = connect(mgr, 'rly-1');
		expect(mgr.disconnect('rly-1')).toBe(true);
		expect(ws.closed).toMatchObject({ code: 4001 });
		expect(mgr.connections.size).toBe(0);
		// Idempotent: nothing left to close.
		expect(mgr.disconnect('rly-1')).toBe(false);
		expect(mgr.disconnect('rly-unknown')).toBe(false);
	});
});

describe('extractSecret', () => {
	test('reads the bearer header only — no query-string fallback (log leak)', () => {
		expect(_extractSecret({ headers: { authorization: 'Bearer abc' }, url: '/ws/relay' })).toBe('abc');
		expect(_extractSecret({ headers: { authorization: 'bearer xyz' }, url: '/ws/relay' })).toBe('xyz');
		// A secret in the query string would be copied into proxy/nginx access
		// logs, so it must NOT authenticate.
		expect(_extractSecret({ headers: {}, url: '/ws/relay?secret=qs' })).toBe('');
		expect(_extractSecret({ headers: {}, url: '/ws/relay' })).toBe('');
	});
});
