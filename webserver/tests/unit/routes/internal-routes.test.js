/**
 * Unit tests for the internal relay dispatch route.
 *
 * Covers the trust boundary (shared-secret auth, constant-time), input
 * validation, and the mapping of relayManager.dispatch() shapes onto HTTP:
 * offline → 404, success → 200 {status, body}.
 */
const express = require('express');
const request = require('supertest');

const config = require('../../../src/config/config');
const relayManager = require('../../../src/websocket/relayManager');

const SECRET = 'internal-test-secret';

function buildApp() {
	// internal-routes reads config.relay.internalSecret at request time.
	config.relay.internalSecret = SECRET;
	// Re-require fresh so the singleton route closure is bound to current config.
	const router = require('../../../src/routes/internal-routes');
	const app = express();
	app.use(express.json());
	app.use('/internal', router);
	return app;
}

describe('POST /internal/relay/dispatch', () => {
	let app;
	let origDispatch;
	beforeEach(() => {
		app = buildApp();
		origDispatch = relayManager.dispatch;
	});
	afterEach(() => {
		relayManager.dispatch = origDispatch;
	});

	test('401 without the shared secret', async () => {
		const res = await request(app).post('/internal/relay/dispatch').send({ server_id: 'rly-1', path: '/api/states' });
		expect(res.status).toBe(401);
	});

	test('401 with a wrong secret', async () => {
		const res = await request(app)
			.post('/internal/relay/dispatch')
			.set('Authorization', 'Bearer wrong')
			.send({ server_id: 'rly-1', path: '/api/states' });
		expect(res.status).toBe(401);
	});

	test('400 when server_id or path is missing', async () => {
		const res = await request(app)
			.post('/internal/relay/dispatch')
			.set('Authorization', `Bearer ${SECRET}`)
			.send({ server_id: 'rly-1' });
		expect(res.status).toBe(400);
	});

	test('200 maps a successful dispatch to {status, body}', async () => {
		relayManager.dispatch = async (serverId, payload) => {
			expect(serverId).toBe('rly-1');
			expect(payload.path).toBe('/api/states');
			return { status: 200, body: '{"ok":true}' };
		};
		const res = await request(app)
			.post('/internal/relay/dispatch')
			.set('Authorization', `Bearer ${SECRET}`)
			.send({ server_id: 'rly-1', method: 'GET', path: '/api/states' });
		expect(res.status).toBe(200);
		expect(res.body).toEqual({ status: 200, body: '{"ok":true}' });
	});

	test('404 when the component is offline', async () => {
		relayManager.dispatch = async () => ({ offline: true });
		const res = await request(app)
			.post('/internal/relay/dispatch')
			.set('Authorization', `Bearer ${SECRET}`)
			.send({ server_id: 'rly-1', path: '/api/states' });
		expect(res.status).toBe(404);
	});
});
