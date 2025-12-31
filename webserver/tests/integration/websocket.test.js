/**
 * Integration tests for WebSocket functionality
 */

const WebSocket = require('ws');
const http = require('http');
const express = require('express');
const webSocketManager = require('../../src/websocket/manager');
const redisClient = require('../../src/utils/redis');

describe('WebSocket Integration Tests', () => {
	let server;
	let wsServer;
	let testPort;

	beforeAll(async () => {
		// Create HTTP server for WebSocket testing
		const app = express();
		server = http.createServer(app);

		// Initialize WebSocket manager
		await redisClient.connect();
		await webSocketManager.initialize(server);

		// Start server
		await new Promise((resolve, reject) => {
			server.once('error', reject);
			server.listen(0, () => {
				testPort = server.address().port;
				resolve();
			});
		});
	});

	afterAll(async () => {
		if (server) {
			await new Promise((resolve) => {
				server.close(resolve);
			});
		}

		if (redisClient.isConnected) {
			await redisClient.disconnect();
		}

		// Close WebSocket server to avoid open handles
		if (webSocketManager.wss) {
			webSocketManager.wss.close();
		}
	});

	beforeEach(async () => {
		// Clean up test data
		if (redisClient.isConnected) {
			await redisClient.client.flushDb();
		}
	});

	describe('WebSocket Connection', () => {
		test('should accept valid WebSocket connection with UID', (done) => {
			const testUID = global.testUtils.generateTestUUID();
			const ws = new WebSocket(`ws://localhost:${testPort}/ws?uid=${testUID}`);

			ws.on('open', () => {
				expect(ws.readyState).toBe(WebSocket.OPEN);
				ws.close();
				done();
			});

			ws.on('error', (error) => {
				done(error);
			});
		});

		test('should reject connection without UID', (done) => {
			const ws = new WebSocket(`ws://localhost:${testPort}/ws`);

			ws.on('error', (error) => {
				expect(error).toBeDefined();
				done();
			});

			ws.on('open', () => {
				done(new Error('Connection should have been rejected'));
			});
		});

		test('should reject connection with invalid UID', (done) => {
			const ws = new WebSocket(`ws://localhost:${testPort}/ws?uid=invalid-uid`);

			ws.on('error', (error) => {
				expect(error).toBeDefined();
				done();
			});

			ws.on('open', () => {
				done(new Error('Connection should have been rejected'));
			});
		});

		test('should handle connection close gracefully', (done) => {
			const testUID = global.testUtils.generateTestUUID();
			const ws = new WebSocket(`ws://localhost:${testPort}/ws?uid=${testUID}`);

			ws.on('open', () => {
				ws.close();
			});

			ws.on('close', (code) => {
				expect(code).toBeDefined();
				done();
			});
		});
	});

	describe('WebSocket Messaging', () => {
		test('should send current switch state on connection', async () => {
			const testUID = global.testUtils.generateTestUUID();
			const personalKey = global.testUtils.createTestPersonalKey();
			const switchConfig = global.testUtils.createTestSwitchData();

			// Create switch first
			await redisClient.createSwitch(testUID, personalKey, switchConfig);
			await redisClient.setSwitchState(testUID, true);

			return new Promise((resolve, reject) => {
				const ws = new WebSocket(`ws://localhost:${testPort}/ws?uid=${testUID}`);

				ws.on('message', (data) => {
					try {
						const message = JSON.parse(data.toString());

						if (message.type === 'state_update') {
							expect(message.uid).toBe(testUID);
							expect(message.state).toBe(true);
							expect(message.timestamp).toBeDefined();
							ws.close();
							resolve();
						}
					} catch (error) {
						reject(error);
					}
				});

				ws.on('error', reject);

				setTimeout(() => {
					reject(new Error('Timeout waiting for state update'));
				}, 5000);
			});
		});

		test('should handle ping/pong messages', async () => {
			const testUID = global.testUtils.generateTestUUID();

			return new Promise((resolve, reject) => {
				const ws = new WebSocket(`ws://localhost:${testPort}/ws?uid=${testUID}`);
				let timeout;

				ws.on('open', () => {
					const pingMessage = {
						type: 'ping',
						timestamp: Date.now()
					};
					ws.send(JSON.stringify(pingMessage));
				});

				ws.on('message', (data) => {
					try {
						const message = JSON.parse(data.toString());

						if (message.type === 'pong') {
							expect(message.timestamp).toBeDefined();
							ws.close();
							clearTimeout(timeout);
							resolve();
						}
					} catch (error) {
						reject(error);
					}
				});

				ws.on('error', reject);

				timeout = setTimeout(() => {
					reject(new Error('Timeout waiting for pong'));
				}, 5000);
			});
		});

		test('should handle subscribe message', async () => {
			const testUID1 = global.testUtils.generateTestUUID();
			const testUID2 = global.testUtils.generateTestUUID();
			const personalKey = global.testUtils.createTestPersonalKey();

			// Create switches
			await redisClient.createSwitch(testUID1, personalKey, global.testUtils.createTestSwitchData());
			await redisClient.createSwitch(testUID2, personalKey, global.testUtils.createTestSwitchData());

			return new Promise((resolve, reject) => {
				const ws = new WebSocket(`ws://localhost:${testPort}/ws?uid=${testUID1}`);
				let messageCount = 0;

				ws.on('open', () => {
					// Subscribe to different switch
					const subscribeMessage = {
						type: 'subscribe',
						uid: testUID2
					};
					ws.send(JSON.stringify(subscribeMessage));
				});

				ws.on('message', (data) => {
					try {
						const message = JSON.parse(data.toString());
						messageCount++;

						// Should receive state updates for both switches
						if (message.type === 'state_update' && messageCount >= 2) {
							ws.close();
							resolve();
						}
					} catch (error) {
						reject(error);
					}
				});

				ws.on('error', reject);

				setTimeout(() => {
					reject(new Error('Timeout waiting for subscribe response'));
				}, 5000);
			});
		});

		test('should broadcast state updates to all subscribers', async () => {
			const testUID = global.testUtils.generateTestUUID();
			const personalKey = global.testUtils.createTestPersonalKey();

			// Create switch
			await redisClient.createSwitch(testUID, personalKey, global.testUtils.createTestSwitchData());

			return new Promise((resolve, reject) => {
				let receivedCount = 0;
				const expectedClients = 2;

				const checkComplete = () => {
					receivedCount++;
					if (receivedCount === expectedClients) {
						resolve();
					}
				};

				// Create multiple WebSocket connections
				for (let i = 0; i < expectedClients; i++) {
					const ws = new WebSocket(`ws://localhost:${testPort}/ws?uid=${testUID}`);

					ws.on('message', (data) => {
						try {
							const message = JSON.parse(data.toString());

							if (message.type === 'state_update' && message.state === true) {
								ws.close();
								checkComplete();
							}
						} catch (error) {
							reject(error);
						}
					});

					ws.on('error', reject);
				}

				// Wait a bit for connections to establish, then publish update
				setTimeout(async () => {
					try {
						await redisClient.setSwitchState(testUID, true);
						await redisClient.publishSwitchUpdate(testUID, true);
					} catch (error) {
						reject(error);
					}
				}, 100);

				setTimeout(() => {
					reject(new Error('Timeout waiting for broadcast'));
				}, 5000);
			});
		});

		test('should handle malformed JSON messages gracefully', async () => {
			const testUID = global.testUtils.generateTestUUID();

			return new Promise((resolve, reject) => {
				const ws = new WebSocket(`ws://localhost:${testPort}/ws?uid=${testUID}`);
				let timeout;

				ws.on('open', () => {
					// Send malformed JSON
					ws.send('invalid json message');

					// Send valid message after malformed one
					setTimeout(() => {
						const validMessage = {
							type: 'ping',
							timestamp: Date.now()
						};
						ws.send(JSON.stringify(validMessage));
					}, 100);
				});

				ws.on('message', (data) => {
					try {
						const message = JSON.parse(data.toString());

						if (message.type === 'pong') {
							// Connection should still work after malformed message
							ws.close();
							clearTimeout(timeout);
							resolve();
						}
					} catch (error) {
						reject(error);
					}
				});

				ws.on('error', reject);

				timeout = setTimeout(() => {
					reject(new Error('Timeout - connection failed after malformed message'));
				}, 5000);
			});
		});
	});

	describe('WebSocket Manager Stats', () => {
		test('should track connection statistics', async () => {
			const testUID = global.testUtils.generateTestUUID();

			return new Promise((resolve, reject) => {
				const ws = new WebSocket(`ws://localhost:${testPort}/ws?uid=${testUID}`);

				ws.on('open', () => {
					const stats = webSocketManager.getStats();

					expect(stats.totalClients).toBeGreaterThan(0);
					expect(stats.totalSubscriptions).toBeGreaterThan(0);
					expect(stats.clientsPerSwitch).toBeDefined();

					ws.close();
					resolve();
				});

				ws.on('error', reject);
			});
		});

		test('should clean up stats when connections close', async () => {
			const testUID = global.testUtils.generateTestUUID();

			return new Promise((resolve, reject) => {
				const ws = new WebSocket(`ws://localhost:${testPort}/ws?uid=${testUID}`);

				ws.on('open', () => {
					const openStats = webSocketManager.getStats();
					const initialClients = openStats.totalClients;

					ws.close();

					// Wait a bit for cleanup
					setTimeout(() => {
						const closeStats = webSocketManager.getStats();
						expect(closeStats.totalClients).toBeLessThan(initialClients);
						resolve();
					}, 100);
				});

				ws.on('error', reject);
			});
		});
	});

	describe('WebSocket Error Handling', () => {
		test('should handle switch not found error', async () => {
			const nonExistentUID = global.testUtils.generateTestUUID();

			return new Promise((resolve, reject) => {
				const ws = new WebSocket(`ws://localhost:${testPort}/ws?uid=${nonExistentUID}`);
				let timeout;

				ws.on('message', (data) => {
					try {
						const message = JSON.parse(data.toString());

						if (message.type === 'error') {
							expect(message.message).toContain('Switch not found');
							ws.close();
							clearTimeout(timeout);
							resolve();
						}
					} catch (error) {
						reject(error);
					}
				});

				ws.on('error', reject);

				timeout = setTimeout(() => {
					ws.close();
					reject(new Error('Timeout waiting for error message'));
				}, 5000);
			});
		});

		test('should handle connection timeout gracefully', async () => {
			const testUID = global.testUtils.generateTestUUID();

			return new Promise((resolve, reject) => {
				const ws = new WebSocket(`ws://localhost:${testPort}/ws?uid=${testUID}`, {
					handshakeTimeout: 100 // Very short timeout
				});
				let timeout;

				ws.on('open', () => {
					// Connection opened successfully
					ws.close();
					clearTimeout(timeout);
					resolve();
				});

				ws.on('error', (error) => {
					// Timeout error is also acceptable for this test
					if (error.message && error.message.includes('timeout')) {
						clearTimeout(timeout);
						resolve();
					} else {
						reject(error);
					}
				});

				timeout = setTimeout(() => {
					// Either way is fine for this test
					ws.close();
					resolve();
				}, 1000);
			});
		});
	});

	describe('WebSocket Performance', () => {
		test('should handle multiple concurrent connections', async () => {
			const testUID = global.testUtils.generateTestUUID();
			const personalKey = global.testUtils.createTestPersonalKey();
			const numConnections = 10;

			// Create switch
			await redisClient.createSwitch(testUID, personalKey, global.testUtils.createTestSwitchData());

			return new Promise((resolve, reject) => {
				let connectedCount = 0;
				let receivedCount = 0;
				const connections = [];

				for (let i = 0; i < numConnections; i++) {
					const ws = new WebSocket(`ws://localhost:${testPort}/ws?uid=${testUID}`);
					connections.push(ws);

					ws.on('open', () => {
						connectedCount++;
						if (connectedCount === numConnections) {
							// All connected, now publish an update
							setTimeout(async () => {
								await redisClient.publishSwitchUpdate(testUID, true);
							}, 100);
						}
					});

					ws.on('message', (data) => {
						try {
							const message = JSON.parse(data.toString());
							if (message.type === 'state_update' && message.state === true) {
								receivedCount++;
								if (receivedCount === numConnections) {
									// All received the update
									connections.forEach(conn => conn.close());
									resolve();
								}
							}
						} catch (error) {
							reject(error);
						}
					});

					ws.on('error', reject);
				}

				setTimeout(() => {
					reject(new Error(`Timeout - only ${receivedCount}/${numConnections} received update`));
				}, 10000);
			});
		});
	});
});
