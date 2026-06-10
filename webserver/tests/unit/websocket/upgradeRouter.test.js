/**
 * Regression tests for the shared WS upgrade router.
 *
 * The production bug this guards against: two `WebSocket.Server({ server,
 * path })` instances on one HTTP server (ws 8.x) abort each other's
 * handshakes with 400 — every `/ws/relay` connection was rejected by the
 * `/ws` endpoint's listener.  These tests stand up both endpoints exactly as
 * src/server.js does and prove each path completes its own handshake.
 */
const http = require('http');
const WebSocket = require('ws');
const { attachUpgradeRouter, abortUpgrade } = require('../../../src/websocket/upgradeRouter');
const { RelayManager } = require('../../../src/websocket/relayManager');
const { WebSocketManager } = require('../../../src/websocket/manager');

const VALID_SECRET = 'sec-valid';
const RELAY_SERVER_ID = 'rly-test-1';
// A uid the legacy manager accepts (matches isValidSwitchUid's UUID format).
const VALID_UID = '123e4567-e89b-42d3-a456-426614174000';

function wsUrl(port, path) {
	return `ws://127.0.0.1:${port}${path}`;
}

/** Open a WS; resolves { ws, firstMessage } on open or { code, err } on failure. */
function tryConnect(urlStr, headers) {
	return new Promise((resolve) => {
		const ws = new WebSocket(urlStr, { headers });
		// Attach before 'open': the 101 and the first frame can share a TCP
		// packet, making ws emit 'message' synchronously right after 'open'.
		const firstMessage = new Promise((res) => {
			ws.on('message', (msg) => res(JSON.parse(msg)));
		});
		ws.on('open', () => resolve({ ws, firstMessage }));
		ws.on('unexpected-response', (_req, res) => {
			ws.terminate();
			resolve({ code: res.statusCode });
		});
		ws.on('error', (err) => resolve({ err }));
	});
}

describe('attachUpgradeRouter with /ws and /ws/relay endpoints', () => {
	let server;
	let port;
	let relayMgr;
	let legacyMgr;

	beforeAll(async () => {
		relayMgr = new RelayManager();
		relayMgr.verifyFn = (secret) => (secret === VALID_SECRET ? RELAY_SERVER_ID : null);
		relayMgr.wss = new WebSocket.Server({ noServer: true });
		relayMgr.wss.on('connection', (ws, req) => relayMgr.handleConnection(ws, req));

		legacyMgr = new WebSocketManager();
		legacyMgr.wss = new WebSocket.Server({ noServer: true });
		// Track connections only — the full handleConnection needs Redis.
		legacyMgr.wss.on('connection', (ws) => ws.close(1000, 'ok'));

		server = http.createServer();
		attachUpgradeRouter(server, { '/ws': legacyMgr, '/ws/relay': relayMgr });
		await new Promise((resolve) => server.listen(0, () => resolve()));
		port = server.address().port;
	});

	afterAll(async () => {
		relayMgr.wss.close();
		legacyMgr.wss.close();
		await new Promise((resolve) => server.close(resolve));
	});

	test('relay handshake succeeds alongside the legacy endpoint (the 400 regression)', async () => {
		const result = await tryConnect(wsUrl(port, '/ws/relay'), {
			Authorization: `Bearer ${VALID_SECRET}`
		});
		expect(result.ws).toBeDefined();
		// The relay greets with a hello carrying the verified server id.
		const hello = await result.firstMessage;
		expect(hello).toMatchObject({ type: 'hello', server_id: RELAY_SERVER_ID });
		result.ws.close();
	});

	test('legacy /ws handshake still succeeds with a valid uid', async () => {
		const result = await tryConnect(wsUrl(port, `/ws?uid=${VALID_UID}`));
		expect(result.ws).toBeDefined();
		result.ws.close();
	});

	test('relay rejects a missing secret with 401', async () => {
		const result = await tryConnect(wsUrl(port, '/ws/relay'));
		expect(result.code).toBe(401);
	});

	test('relay rejects a wrong secret with 401', async () => {
		const result = await tryConnect(wsUrl(port, '/ws/relay'), {
			Authorization: 'Bearer nope'
		});
		expect(result.code).toBe(401);
	});

	test('legacy /ws rejects a missing/invalid uid with 401', async () => {
		expect((await tryConnect(wsUrl(port, '/ws'))).code).toBe(401);
		expect((await tryConnect(wsUrl(port, '/ws?uid=../etc'))).code).toBe(401);
	});

	test('unknown upgrade paths get a 404 instead of hanging', async () => {
		const result = await tryConnect(wsUrl(port, '/ws/other'));
		expect(result.code).toBe(404);
	});
});

describe('abortUpgrade', () => {
	test('writes an HTTP response and destroys the socket', () => {
		const writes = [];
		const socket = {
			writable: true,
			write: (chunk) => writes.push(chunk),
			destroy: jest.fn()
		};
		abortUpgrade(socket, 401, 'Unauthorized');
		expect(writes.join('')).toContain('HTTP/1.1 401 Unauthorized');
		expect(socket.destroy).toHaveBeenCalled();
	});

	test('skips the write on an unwritable socket but still destroys it', () => {
		const socket = { writable: false, write: jest.fn(), destroy: jest.fn() };
		abortUpgrade(socket, 404, 'Unknown');
		expect(socket.write).not.toHaveBeenCalled();
		expect(socket.destroy).toHaveBeenCalled();
	});
});
