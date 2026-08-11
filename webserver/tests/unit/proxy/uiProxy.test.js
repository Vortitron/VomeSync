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
	isWebhookPath,
	clientIp
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

	// Regression: nginx adds these so the rate limit can tell visitors apart.
	// If they cross the tunnel, HA's forwarded middleware 400s *every* request
	// on a stock install (no http.use_x_forwarded_for) — a total outage of the
	// friendly domain, not a degraded feature.
	test('collectRequestHeaders strips the proxy hop headers nginx adds', () => {
		const req = fakeReq({ rawHeaders: [
			'X-Forwarded-For', '203.0.113.9, 10.0.0.1',
			'X-Real-IP', '203.0.113.9',
			'X-Forwarded-Proto', 'https',
			'X-Forwarded-Host', 'gamlabio.home.vome.io',
			'X-Forwarded-Port', '443',
			'Forwarded', 'for=203.0.113.9;proto=https',
			'Accept', 'text/html'
		] });
		const map = Object.fromEntries(collectRequestHeaders(req, config.relay.forwardCookieName));
		for (const name of ['X-Forwarded-For', 'X-Real-IP', 'X-Forwarded-Proto',
			'X-Forwarded-Host', 'X-Forwarded-Port', 'Forwarded']) {
			expect(map[name]).toBeUndefined();
		}
		expect(map.Accept).toBe('text/html'); // ordinary headers still cross
	});

	test('collectRequestHeaders strips hop headers whatever their casing', () => {
		const req = fakeReq({ rawHeaders: ['x-forwarded-for', '203.0.113.9'] });
		expect(collectRequestHeaders(req, 'vome_fwd')).toEqual([]);
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

	test('admits a LAN-tunnel WebSocket path past the path gate (auth still applies)', async () => {
		const proxy = createUiProxy({
			relayManager: { isConnected: () => false },
			verifyAccessToken: () => ({ serverId: 'rly-1' }),
			fetchForwardPolicy: async () => null
		});
		const socket = fakeSocket();
		await proxy.handleUpgrade(
			fakeReq({ url: '/t/nas/ws', headers: { host: 'h.vome.io' } }), socket, Buffer.alloc(0));
		// Path allowed; home offline → 502 (not 404).
		expect(socket.written).toContain('502');
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

describe('uiProxy.clientIp', () => {
	const original = config.relay.forwardTrustedProxies;

	afterEach(() => { config.relay.forwardTrustedProxies = original; });

	function req({ peer = '10.0.0.1', realIp = null } = {}) {
		const r = fakeReq({ headers: realIp ? { 'x-real-ip': realIp } : {} });
		r.socket = { remoteAddress: peer };
		return r;
	}

	test('trusts X-Real-IP when no trusted-proxy list is configured', () => {
		config.relay.forwardTrustedProxies = [];
		expect(clientIp(req({ peer: '10.0.0.1', realIp: '203.0.113.7' }))).toBe('203.0.113.7');
	});

	test('falls back to the socket peer when the header is absent', () => {
		config.relay.forwardTrustedProxies = [];
		expect(clientIp(req({ peer: '10.0.0.1' }))).toBe('10.0.0.1');
	});

	test('honours X-Real-IP from a listed proxy', () => {
		config.relay.forwardTrustedProxies = ['10.0.0.1'];
		expect(clientIp(req({ peer: '10.0.0.1', realIp: '203.0.113.7' }))).toBe('203.0.113.7');
	});

	test('an unlisted peer cannot mint itself a fresh bucket', () => {
		// The security property: once a trusted list exists, someone who reaches
		// the proxy port directly is keyed on where they actually came from, so
		// rotating X-Real-IP does not reset their budget.
		config.relay.forwardTrustedProxies = ['10.0.0.1'];
		expect(clientIp(req({ peer: '198.51.100.4', realIp: '203.0.113.7' }))).toBe('198.51.100.4');
	});

	test('degrades to a single shared bucket rather than throwing', () => {
		config.relay.forwardTrustedProxies = [];
		expect(clientIp(fakeReq({ headers: {} }))).toBe('unknown');
	});
});

describe('uiProxy rate limits on unauthenticated traffic', () => {
	const host = { host: 'nyvyn.home.vome.io' };

	/**
	 * A proxy whose limiter refuses everything (`allow: false`) or nothing,
	 * recording the calls so we can assert which bucket was charged.
	 */
	function limitedProxy({ allow = true, policy = null, cookieOk = false } = {}) {
		const calls = [];
		const checkRateLimit = jest.fn(async (identifier, action, limit, windowMs) => {
			calls.push({ identifier, action, limit, windowMs });
			return { allowed: allow, current: allow ? 1 : limit + 1, limit, resetTime: Date.now() + 60000 };
		});
		const fetchForwardPolicy = jest.fn(async () => policy);
		const relay = {
			isConnected: () => true,
			forwardHttp: async () => ({ status: 200, headers: [], bodyB64: undefined })
		};
		const proxy = createUiProxy({
			relayManager: relay,
			verifyAccessToken: () => (cookieOk ? { serverId: 'rly-1', userId: 'u1' } : null),
			fetchForwardPolicy,
			checkRateLimit
		});
		return { proxy, calls, checkRateLimit, fetchForwardPolicy };
	}

	async function runHttp(proxy, reqOpts) {
		const req = fakeReq(reqOpts);
		const res = fakeRes();
		proxy.httpHandler(req, res);
		await tick();
		req.emit('end');
		await res.done;
		return res;
	}

	test('refuses an over-budget request with 429 and Retry-After', async () => {
		const { proxy } = limitedProxy({ allow: false });
		const res = await runHttp(proxy, { url: '/lovelace', headers: host });
		expect(res.statusCode).toBe(429);
		expect(Number(res.headers['Retry-After'])).toBeGreaterThan(0);
	});

	test('never charges a request that carries a valid forwarding cookie', async () => {
		// Vome already checked ownership and subscription to mint it, so paid
		// remote access must not be throttled by a noisy neighbour on its NAT.
		const { proxy, checkRateLimit } = limitedProxy({ allow: false, cookieOk: true });
		const res = await runHttp(proxy, { url: '/lovelace', headers: host });
		expect(res.statusCode).toBe(200);
		expect(checkRateLimit).not.toHaveBeenCalled();
	});

	test('open mode is rate limited — that is the whole point', async () => {
		// `open` skips the cookie gate, so without this an attacker who learns
		// the hostname gets an unmetered channel to Home Assistant's login form.
		const { proxy, calls } = limitedProxy({ allow: false, policy: { serverId: 'rly-1', open: true } });
		const res = await runHttp(proxy, { url: '/lovelace', headers: host });
		expect(res.statusCode).toBe(429);
		expect(calls[0].action).toBe('fwd_unauth');
	});

	test('charges the tight bucket for HA login and token endpoints', async () => {
		const { proxy, calls } = limitedProxy({ policy: { serverId: 'rly-1', open: true } });
		await runHttp(proxy, { method: 'POST', url: '/auth/login_flow', headers: host });
		expect(calls[0].action).toBe('fwd_unauth_auth');
		expect(calls[0].limit).toBe(config.relay.forwardAuthRateMax);
		expect(calls[0].limit).toBeLessThan(config.relay.forwardRateMax);
	});

	test('a query string cannot smuggle a login attempt into the loose bucket', async () => {
		const { proxy, calls } = limitedProxy({ policy: { serverId: 'rly-1', open: true } });
		await runHttp(proxy, { url: '/auth/token?redirect=/lovelace', headers: host });
		expect(calls[0].action).toBe('fwd_unauth_auth');
	});

	test('spends the budget before touching the portal', async () => {
		// Otherwise a flood becomes portal load instead of a cheap 429.
		const { proxy, fetchForwardPolicy } = limitedProxy({ allow: false, policy: { serverId: 'rly-1', open: true } });
		await runHttp(proxy, { url: '/lovelace', headers: host });
		expect(fetchForwardPolicy).not.toHaveBeenCalled();
	});

	test('fails open when the limiter itself is broken', async () => {
		// Redis being unreachable must not take remote access down with it.
		const proxy = createUiProxy({
			relayManager: { isConnected: () => true, forwardHttp: async () => ({ status: 200, headers: [] }) },
			verifyAccessToken: () => null,
			fetchForwardPolicy: async () => ({ serverId: 'rly-1', open: true }),
			checkRateLimit: async () => { throw new Error('redis down'); }
		});
		const res = await runHttp(proxy, { url: '/lovelace', headers: host });
		expect(res.statusCode).toBe(200);
	});

	test('aborts an over-budget WebSocket upgrade with 429', async () => {
		const { proxy, calls } = limitedProxy({ allow: false, policy: { serverId: 'rly-1', open: true } });
		const socket = { writable: true, written: '', destroyed: false,
			write(s) { this.written += s; }, destroy() { this.destroyed = true; } };
		await proxy.handleUpgrade(fakeReq({ url: '/api/websocket', headers: host }), socket, Buffer.alloc(0));
		expect(socket.written).toContain('429');
		expect(socket.destroyed).toBe(true);
		expect(calls[0].action).toBe('fwd_unauth_ws');
		expect(calls[0].limit).toBe(config.relay.forwardWsRateMax);
	});

	test('a bucket set to 0 is disabled', async () => {
		const originalMax = config.relay.forwardRateMax;
		config.relay.forwardRateMax = 0;
		try {
			const { proxy, checkRateLimit } = limitedProxy({ allow: false, policy: { serverId: 'rly-1', open: true } });
			const res = await runHttp(proxy, { url: '/lovelace', headers: host });
			expect(checkRateLimit).not.toHaveBeenCalled();
			expect(res.statusCode).toBe(200);
		} finally {
			config.relay.forwardRateMax = originalMax;
		}
	});
});
