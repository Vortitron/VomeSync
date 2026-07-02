/**
 * Unit tests for the browser-facing full-UI forwarding proxy.
 *
 * The proxy is built as a factory taking { relayManager, verifyAccessToken } so
 * we can drive it with fakes — no real relay socket, portal, or TLS needed.
 */
process.env.RELAY_FORWARD_SECRET = 'unit-test-forward-secret';

const { EventEmitter } = require('events');
const WebSocket = require('ws');
const config = require('../../../src/config/config');
const {
	createUiProxy,
	originalHost,
	collectRequestHeaders,
	filterResponseHeaders,
	applyResponseHeaders,
	stripOwnCookie,
	isWebhookPath
} = require('../../../src/proxy/uiProxy');

/** Let pending microtasks/immediates run (async authorise path in handlers). */
function tick() {
	return new Promise((resolve) => setImmediate(resolve));
}

function fakeReq({ method = 'GET', url = '/', headers = {}, rawHeaders = [] } = {}) {
	const req = new EventEmitter();
	req.method = method;
	req.url = url;
	req.headers = headers;
	req.rawHeaders = rawHeaders;
	return req;
}

function fakeRes() {
	let resolveDone;
	const done = new Promise((r) => { resolveDone = r; });
	return {
		statusCode: 0,
		headers: {},
		body: null,
		done,
		writeHead(status, hdrs) { this.statusCode = status; if (hdrs) { Object.assign(this.headers, hdrs); } },
		setHeader(k, v) { this.headers[k] = v; },
		end(b) { this.body = b; resolveDone(); }
	};
}

function fakeBrowserWs() {
	const ws = new EventEmitter();
	ws.readyState = WebSocket.OPEN;
	ws.sent = [];
	ws.closed = null;
	ws.send = (d) => ws.sent.push(d);
	ws.close = (code, reason) => { ws.closed = { code, reason }; };
	return ws;
}

describe('uiProxy.originalHost', () => {
	test('prefers X-HA-Original-Host (the shared wildcard rewrites Host)', () => {
		const req = fakeReq({ headers: { host: '127.0.0.1:8099', 'x-ha-original-host': 'nyvyn.home.vome.io' } });
		expect(originalHost(req)).toBe('nyvyn.home.vome.io');
	});

	test('falls back to Host when no original-host header is present', () => {
		expect(originalHost(fakeReq({ headers: { host: 'nyvyn.home.vome.io' } }))).toBe('nyvyn.home.vome.io');
		expect(originalHost(fakeReq({ headers: {} }))).toBe('');
	});
});

describe('uiProxy header helpers', () => {
	test('collectRequestHeaders strips hop-by-hop and our own cookie', () => {
		const req = fakeReq({ rawHeaders: [
			'Host', 'nyvyn.vome.io',
			'Connection', 'keep-alive',
			'Cookie', 'vome_fwd=secret; ha_theme=dark',
			'Accept', 'text/html'
		] });
		const out = collectRequestHeaders(req, config.relay.forwardCookieName);
		const map = Object.fromEntries(out);
		expect(map.Host).toBeUndefined();
		expect(map.Connection).toBeUndefined();
		expect(map.Accept).toBe('text/html');
		expect(map.Cookie).toBe('ha_theme=dark'); // our cookie removed, HA's kept
	});

	test('collectRequestHeaders drops a Cookie header that held only our cookie', () => {
		const req = fakeReq({ rawHeaders: ['Cookie', 'vome_fwd=secret'] });
		expect(collectRequestHeaders(req, 'vome_fwd')).toEqual([]);
	});

	test('stripOwnCookie keeps other cookies', () => {
		expect(stripOwnCookie('a=1; vome_fwd=x; b=2', 'vome_fwd')).toBe('a=1; b=2');
		expect(stripOwnCookie('vome_fwd=x', 'vome_fwd')).toBe('');
	});

	test('filterResponseHeaders drops hop-by-hop, keeps duplicates', () => {
		const out = filterResponseHeaders([
			['Content-Type', 'text/html'],
			['Set-Cookie', 'a=1'], ['Set-Cookie', 'b=2'],
			['Transfer-Encoding', 'chunked']
		]);
		expect(out).toEqual([
			['Content-Type', 'text/html'], ['Set-Cookie', 'a=1'], ['Set-Cookie', 'b=2']
		]);
	});

	test('applyResponseHeaders coalesces duplicate Set-Cookie into an array', () => {
		const res = fakeRes();
		applyResponseHeaders(res, [['Set-Cookie', 'a=1'], ['Set-Cookie', 'b=2'], ['X', 'y']]);
		expect(res.headers['Set-Cookie']).toEqual(['a=1', 'b=2']);
		expect(res.headers.X).toBe('y');
	});
});

describe('uiProxy.httpHandler', () => {
	const authorised = () => ({ serverId: 'rly-1', userId: 'u1' });

	test('redirects an unauthenticated browser to the portal authorise page', async () => {
		const proxy = createUiProxy({ relayManager: {}, verifyAccessToken: () => null });
		const req = fakeReq({ headers: { host: 'nyvyn.vome.io' } });
		const res = fakeRes();
		proxy.httpHandler(req, res);
		await res.done;
		expect(res.statusCode).toBe(302);
		expect(res.headers.Location).toContain(config.relay.forwardAuthoriseUrl);
		expect(res.headers.Location).toContain('nyvyn.vome.io');
	});

	test('verifies + redirects using the real host from X-HA-Original-Host', async () => {
		let seenHost;
		const proxy = createUiProxy({
			relayManager: {},
			verifyAccessToken: (_t, host) => { seenHost = host; return null; }
		});
		// Via the shared wildcard, Host is the loopback upstream; the friendly
		// host arrives in X-HA-Original-Host.
		const req = fakeReq({ headers: { host: '127.0.0.1:8099', 'x-ha-original-host': 'nyvyn.home.vome.io' } });
		const res = fakeRes();
		proxy.httpHandler(req, res);
		await res.done;
		expect(seenHost).toBe('nyvyn.home.vome.io');
		expect(res.headers.Location).toContain('nyvyn.home.vome.io');
		expect(res.headers.Location).not.toContain('127.0.0.1');
	});

	test('returns 502 when the home is offline', async () => {
		const relay = { forwardHttp: async () => ({ offline: true }) };
		const proxy = createUiProxy({ relayManager: relay, verifyAccessToken: authorised });
		const req = fakeReq({ headers: { host: 'nyvyn.vome.io' } });
		const res = fakeRes();
		proxy.httpHandler(req, res);
		req.emit('end');
		await res.done;
		expect(res.statusCode).toBe(502);
	});

	test('mirrors the component response (status, headers, body)', async () => {
		const relay = {
			forwardHttp: async (serverId, opts) => {
				expect(serverId).toBe('rly-1');
				expect(opts.path).toBe('/lovelace/0');
				return {
					status: 200,
					headers: [['Content-Type', 'text/html'], ['Set-Cookie', 's=1'], ['Set-Cookie', 't=2']],
					bodyB64: Buffer.from('<html>hi</html>').toString('base64')
				};
			}
		};
		const proxy = createUiProxy({ relayManager: relay, verifyAccessToken: authorised });
		const req = fakeReq({ method: 'GET', url: '/lovelace/0', headers: { host: 'nyvyn.vome.io' } });
		const res = fakeRes();
		proxy.httpHandler(req, res);
		req.emit('end');
		await res.done;
		expect(res.statusCode).toBe(200);
		expect(res.headers['Content-Type']).toBe('text/html');
		expect(res.headers['Set-Cookie']).toEqual(['s=1', 't=2']);
		expect(res.headers['Content-Length']).toBe(Buffer.byteLength('<html>hi</html>'));
		expect(res.body.toString()).toBe('<html>hi</html>');
	});

	test('forwards the request body as base64', async () => {
		let seen;
		const relay = { forwardHttp: async (_s, opts) => { seen = opts; return { status: 204, headers: [] }; } };
		const proxy = createUiProxy({ relayManager: relay, verifyAccessToken: authorised });
		const req = fakeReq({ method: 'POST', url: '/api/x', headers: { host: 'h.vome.io' } });
		const res = fakeRes();
		proxy.httpHandler(req, res);
		req.emit('data', Buffer.from('hello'));
		req.emit('end');
		await res.done;
		expect(Buffer.from(seen.bodyB64, 'base64').toString()).toBe('hello');
	});

	test('rejects an over-sized request body with 413', async () => {
		const relay = { forwardHttp: jest.fn() };
		const proxy = createUiProxy({ relayManager: relay, verifyAccessToken: authorised });
		const req = fakeReq({ method: 'POST', url: '/api/x', headers: { host: 'h.vome.io' } });
		const res = fakeRes();
		proxy.httpHandler(req, res);
		req.emit('data', Buffer.alloc(config.relay.forwardMaxBodyBytes + 1));
		req.emit('end');
		await res.done;
		expect(res.statusCode).toBe(413);
		expect(relay.forwardHttp).not.toHaveBeenCalled();
	});
});

describe('uiProxy.isWebhookPath', () => {
	test('accepts a single opaque webhook id (query ignored)', () => {
		expect(isWebhookPath('/api/webhook/abc123DEF')).toBe(true);
		expect(isWebhookPath('/api/webhook/my-hook_1.2~x')).toBe(true);
		expect(isWebhookPath('/api/webhook/abc?x=1')).toBe(true);
	});

	test('rejects everything else', () => {
		expect(isWebhookPath('/api/webhook/')).toBe(false);
		expect(isWebhookPath('/api/webhook/a/b')).toBe(false);
		expect(isWebhookPath('/api/webhook/..')).toBe(false);
		expect(isWebhookPath('/api/webhook/%2e%2e')).toBe(false); // '%' not in the id alphabet
		expect(isWebhookPath('/api/states')).toBe(false);
		expect(isWebhookPath('/lovelace/0')).toBe(false);
		expect(isWebhookPath('')).toBe(false);
	});
});

describe('uiProxy cookie-less forwarding policy', () => {
	const host = { host: 'nyvyn.home.vome.io' };

	function policyProxy(policy, relayOverrides = {}) {
		const forwarded = [];
		const fetchForwardPolicy = jest.fn(async () => policy);
		const relay = {
			isConnected: () => true,
			forwardHttp: async (serverId, opts) => {
				forwarded.push({ serverId, opts });
				return { status: 200, headers: [], bodyB64: undefined };
			},
			...relayOverrides
		};
		const proxy = createUiProxy({ relayManager: relay, verifyAccessToken: () => null, fetchForwardPolicy });
		return { proxy, forwarded, fetchForwardPolicy };
	}

	test('webhook policy admits a cookie-less webhook POST', async () => {
		const { proxy, forwarded } = policyProxy({ serverId: 'rly-1', webhooks: true, open: false });
		const req = fakeReq({ method: 'POST', url: '/api/webhook/abc123', headers: host });
		const res = fakeRes();
		proxy.httpHandler(req, res);
		await tick();
		req.emit('end');
		await res.done;
		expect(res.statusCode).toBe(200);
		expect(forwarded[0].serverId).toBe('rly-1');
		expect(forwarded[0].opts.path).toBe('/api/webhook/abc123');
	});

	test('webhook policy does NOT admit non-webhook paths or odd methods', async () => {
		const { proxy, forwarded } = policyProxy({ serverId: 'rly-1', webhooks: true, open: false });
		for (const [method, url] of [
			['POST', '/api/states'],
			['DELETE', '/api/webhook/abc123'],
			['POST', '/api/webhook/abc/extra'],
			['GET', '/lovelace/0']
		]) {
			const req = fakeReq({ method, url, headers: host });
			const res = fakeRes();
			proxy.httpHandler(req, res);
			await res.done;
			expect(res.statusCode).toBe(302);
		}
		expect(forwarded).toHaveLength(0);
	});

	test('open policy admits any cookie-less request', async () => {
		const { proxy, forwarded } = policyProxy({ serverId: 'rly-1', webhooks: false, open: true });
		const req = fakeReq({ method: 'GET', url: '/lovelace/0', headers: host });
		const res = fakeRes();
		proxy.httpHandler(req, res);
		await tick();
		req.emit('end');
		await res.done;
		expect(res.statusCode).toBe(200);
		expect(forwarded[0].opts.path).toBe('/lovelace/0');
	});

	test('policy miss keeps the cookie gate (302 to authorise)', async () => {
		const { proxy } = policyProxy(null);
		const req = fakeReq({ method: 'POST', url: '/api/webhook/abc123', headers: host });
		const res = fakeRes();
		proxy.httpHandler(req, res);
		await res.done;
		expect(res.statusCode).toBe(302);
		expect(res.headers.Location).toContain(config.relay.forwardAuthoriseUrl);
	});

	test('policy lookups are cached per host', async () => {
		const { proxy, fetchForwardPolicy } = policyProxy({ serverId: 'rly-1', webhooks: true, open: false });
		for (let i = 0; i < 3; i++) {
			const req = fakeReq({ method: 'POST', url: '/api/webhook/abc123', headers: host });
			const res = fakeRes();
			proxy.httpHandler(req, res);
			await tick();
			req.emit('end');
			await res.done;
		}
		expect(fetchForwardPolicy).toHaveBeenCalledTimes(1);
	});

	test('a failing policy lookup fails closed, not crashed', async () => {
		const fetchForwardPolicy = jest.fn(async () => { throw new Error('portal down'); });
		const proxy = createUiProxy({
			relayManager: {},
			verifyAccessToken: () => null,
			fetchForwardPolicy
		});
		const req = fakeReq({ method: 'POST', url: '/api/webhook/abc123', headers: host });
		const res = fakeRes();
		proxy.httpHandler(req, res);
		await res.done;
		expect(res.statusCode).toBe(302);
	});

	test('open policy admits a cookie-less WebSocket; webhook-only does not', async () => {
		function fakeSocket() {
			return { writable: true, written: '', destroyed: false,
				write(s) { this.written += s; }, destroy() { this.destroyed = true; } };
		}
		const open = policyProxy({ serverId: 'rly-1', webhooks: false, open: true },
			{ isConnected: () => false }); // offline → 502 proves auth passed
		let socket = fakeSocket();
		await open.proxy.handleUpgrade(
			fakeReq({ url: '/api/websocket', headers: host }), socket, Buffer.alloc(0));
		expect(socket.written).toContain('502');

		const hooksOnly = policyProxy({ serverId: 'rly-1', webhooks: true, open: false });
		socket = fakeSocket();
		await hooksOnly.proxy.handleUpgrade(
			fakeReq({ url: '/api/websocket', headers: host }), socket, Buffer.alloc(0));
		expect(socket.written).toContain('401');
	});
});

describe('uiProxy.handleUpgrade', () => {
	function fakeSocket() {
		return { writable: true, written: '', destroyed: false,
			write(s) { this.written += s; }, destroy() { this.destroyed = true; } };
	}

	test('aborts an unauthenticated upgrade with 401', async () => {
		const proxy = createUiProxy({
			relayManager: { isConnected: () => true },
			verifyAccessToken: () => null,
			fetchForwardPolicy: async () => null
		});
		const socket = fakeSocket();
		await proxy.handleUpgrade(fakeReq({ url: '/api/websocket', headers: { host: 'h.vome.io' } }), socket, Buffer.alloc(0));
		expect(socket.written).toContain('401');
		expect(socket.destroyed).toBe(true);
	});

	test('aborts a non-/api/websocket upgrade with 404', () => {
		const proxy = createUiProxy({ relayManager: { isConnected: () => true }, verifyAccessToken: () => ({ serverId: 'rly-1' }) });
		const socket = fakeSocket();
		proxy.handleUpgrade(fakeReq({ url: '/other', headers: { host: 'h.vome.io' } }), socket, Buffer.alloc(0));
		expect(socket.written).toContain('404');
	});

	test('aborts with 502 when the home is offline', () => {
		const proxy = createUiProxy({ relayManager: { isConnected: () => false }, verifyAccessToken: () => ({ serverId: 'rly-1' }) });
		const socket = fakeSocket();
		proxy.handleUpgrade(fakeReq({ url: '/api/websocket', headers: { host: 'h.vome.io' } }), socket, Buffer.alloc(0));
		expect(socket.written).toContain('502');
	});
});

describe('uiProxy.bridge', () => {
	function mockRelay() {
		const calls = [];
		let handlers = null;
		return {
			calls,
			get handlers() { return handlers; },
			isConnected: () => true,
			registerTunnel: (id, sid, h) => { handlers = h; calls.push(['register', id, sid]); },
			unregisterTunnel: (id) => calls.push(['unregister', id]),
			openWs: (sid, f) => { calls.push(['open', f]); return true; },
			sendWs: (sid, f) => calls.push(['data', f]),
			closeWs: (sid, f) => calls.push(['close', f])
		};
	}

	test('opens the component socket, queues browser frames until ack, then flushes', () => {
		const relay = mockRelay();
		const proxy = createUiProxy({ relayManager: relay, verifyAccessToken: () => ({ serverId: 'rly-1' }) });
		const browser = fakeBrowserWs();
		proxy.bridge(browser, fakeReq({ url: '/api/websocket', rawHeaders: [] }), 'rly-1');

		expect(relay.calls.find((c) => c[0] === 'open')).toBeTruthy();
		// A frame arriving before ack is queued, not sent.
		browser.emit('message', Buffer.from('{"type":"auth"}'), false);
		expect(relay.calls.filter((c) => c[0] === 'data')).toHaveLength(0);
		// Ack flushes the queue in order.
		relay.handlers.onAck();
		const dataFrames = relay.calls.filter((c) => c[0] === 'data');
		expect(dataFrames).toHaveLength(1);
		expect(dataFrames[0][1].text).toBe('{"type":"auth"}');
	});

	test('relays component frames to the browser (text + binary)', () => {
		const relay = mockRelay();
		const proxy = createUiProxy({ relayManager: relay, verifyAccessToken: () => ({ serverId: 'rly-1' }) });
		const browser = fakeBrowserWs();
		proxy.bridge(browser, fakeReq({ url: '/api/websocket', rawHeaders: [] }), 'rly-1');
		relay.handlers.onData({ text: 'hello' });
		relay.handlers.onData({ dataB64: Buffer.from([1, 2, 3]).toString('base64') });
		expect(browser.sent[0]).toBe('hello');
		expect(Buffer.isBuffer(browser.sent[1]) && Array.from(browser.sent[1])).toEqual([1, 2, 3]);
	});

	test('component close closes the browser socket', () => {
		const relay = mockRelay();
		const proxy = createUiProxy({ relayManager: relay, verifyAccessToken: () => ({ serverId: 'rly-1' }) });
		const browser = fakeBrowserWs();
		proxy.bridge(browser, fakeReq({ url: '/api/websocket', rawHeaders: [] }), 'rly-1');
		relay.handlers.onClose({ code: 1011, reason: 'gone' });
		expect(browser.closed).toMatchObject({ code: 1011 });
	});

	test('browser close tears down the tunnel and tells the component', () => {
		const relay = mockRelay();
		const proxy = createUiProxy({ relayManager: relay, verifyAccessToken: () => ({ serverId: 'rly-1' }) });
		const browser = fakeBrowserWs();
		proxy.bridge(browser, fakeReq({ url: '/api/websocket', rawHeaders: [] }), 'rly-1');
		relay.handlers.onAck();
		browser.emit('close', 1000, Buffer.from('bye'));
		expect(relay.calls.find((c) => c[0] === 'unregister')).toBeTruthy();
		expect(relay.calls.find((c) => c[0] === 'close')).toBeTruthy();
	});

	test('closes the browser when the component refuses to open', () => {
		const relay = mockRelay();
		relay.openWs = () => false;
		const proxy = createUiProxy({ relayManager: relay, verifyAccessToken: () => ({ serverId: 'rly-1' }) });
		const browser = fakeBrowserWs();
		proxy.bridge(browser, fakeReq({ url: '/api/websocket', rawHeaders: [] }), 'rly-1');
		expect(browser.closed).toMatchObject({ code: 1011 });
	});
});
