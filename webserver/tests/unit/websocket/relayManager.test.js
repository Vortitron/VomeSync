/**
 * Unit tests for the relay control channel (outbound tunnel).
 *
 * The RPC layer is the interesting bit: dispatch() must send an `ha_rpc`, match
 * the component's `ha_rpc_response` by requestId, return offline when nothing is
 * connected, and time out cleanly.  We drive a fresh RelayManager with fake
 * sockets so no real WebSocket server / Redis is needed.
 */
const WebSocket = require('ws');
const relaySingleton = require('../../../src/websocket/relayManager');

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
});

describe('extractSecret', () => {
	test('reads a bearer header, then a ?secret= fallback', () => {
		expect(_extractSecret({ headers: { authorization: 'Bearer abc' }, url: '/ws/relay' })).toBe('abc');
		expect(_extractSecret({ headers: { authorization: 'bearer xyz' }, url: '/ws/relay' })).toBe('xyz');
		expect(_extractSecret({ headers: {}, url: '/ws/relay?secret=qs' })).toBe('qs');
		expect(_extractSecret({ headers: {}, url: '/ws/relay' })).toBe('');
	});
});
