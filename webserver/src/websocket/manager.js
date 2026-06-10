const WebSocket = require('ws');
const url = require('url');
const { v4: uuidv4 } = require('uuid');
const redisClient = require('../utils/redis');
const logger = require('../utils/logger');
const { isValidSwitchUid } = require('../utils/validation');
const { abortUpgrade } = require('./upgradeRouter');

class WebSocketManager {
	constructor() {
		this.wss = null;
		this.clients = new Map(); // Map of client ID to client info
		this.subscriptions = new Map(); // Map of UID to Set of client IDs
	}

	async initialize() {
		// noServer: upgrades arrive via handleUpgrade() from the shared
		// upgrade router (see ./upgradeRouter.js for why path-attached
		// servers must not be used).
		this.wss = new WebSocket.Server({ noServer: true });

		this.wss.on('connection', (ws, req) => {
			this.handleConnection(ws, req);
		});

		// Set up Redis subscription for switch updates
		await this.setupRedisSubscription();

		logger.info('WebSocket manager initialized');
	}

	/** Validate and complete a `/ws` upgrade (called by the upgrade router). */
	handleUpgrade(req, socket, head) {
		const query = url.parse(req.url, true).query;
		if (!query.uid || !this.isValidUID(query.uid)) {
			abortUpgrade(socket, 401, 'Unauthorized');
			return;
		}
		this.wss.handleUpgrade(req, socket, head, (ws) => {
			this.wss.emit('connection', ws, req);
		});
	}

	isValidUID(uid) {
		return isValidSwitchUid(uid);
	}

	handleConnection(ws, req) {
		const clientId = uuidv4();
		const query = url.parse(req.url, true).query;
		const uid = query.uid;
		const clientIP = req.headers['x-forwarded-for'] || req.connection.remoteAddress;

		// Store client information
		const clientInfo = {
			id: clientId,
			ws,
			uid,
			ip: clientIP,
			connectedAt: Date.now(),
			lastActivity: Date.now()
		};

		this.clients.set(clientId, clientInfo);

		// Subscribe client to switch updates
		this.subscribeClient(clientId, uid);

		logger.info(`WebSocket client connected: ${clientId} for switch ${uid}`);

		// Send current switch state
		this.sendCurrentState(clientId, uid);

		// Set up message handlers
		ws.on('message', (message) => {
			this.handleMessage(clientId, message);
		});

		ws.on('close', () => {
			this.handleDisconnection(clientId);
		});

		ws.on('error', (error) => {
			logger.error(`WebSocket error for client ${clientId}:`, error);
			this.handleDisconnection(clientId);
		});

		// Set up ping/pong for connection health
		ws.on('pong', () => {
			const client = this.clients.get(clientId);
			if (client) {
				client.lastActivity = Date.now();
			}
		});

		// Send initial ping
		ws.ping();
	}

	handleMessage(clientId, message) {
		try {
			const client = this.clients.get(clientId);
			if (!client) return;

			client.lastActivity = Date.now();

			const data = JSON.parse(message);

			switch (data.type) {
			case 'ping':
				this.sendToClient(clientId, { type: 'pong', timestamp: Date.now() });
				break;

			case 'subscribe':
				if (data.uid && this.isValidUID(data.uid)) {
					this.subscribeClient(clientId, data.uid);
					this.sendCurrentState(clientId, data.uid);
				}
				break;

			case 'unsubscribe':
				if (data.uid) {
					this.unsubscribeClient(clientId, data.uid);
				}
				break;

			default:
				logger.warn(`Unknown message type from client ${clientId}:`, data.type);
			}
		} catch (error) {
			logger.error(`Error handling message from client ${clientId}:`, error);
		}
	}

	handleDisconnection(clientId) {
		const client = this.clients.get(clientId);
		if (!client) return;

		// Remove from all subscriptions
		for (const [uid, clientIds] of this.subscriptions.entries()) {
			clientIds.delete(clientId);
			if (clientIds.size === 0) {
				this.subscriptions.delete(uid);
			}
		}

		// Remove client
		this.clients.delete(clientId);

		logger.info(`WebSocket client disconnected: ${clientId}`);
	}

	subscribeClient(clientId, uid) {
		const client = this.clients.get(clientId);
		if (!client) return;

		// Update client's current UID
		client.uid = uid;

		// Add to subscription map
		if (!this.subscriptions.has(uid)) {
			this.subscriptions.set(uid, new Set());
		}
		this.subscriptions.get(uid).add(clientId);

		logger.debug(`Client ${clientId} subscribed to switch ${uid}`);
	}

	unsubscribeClient(clientId, uid) {
		const subscriptions = this.subscriptions.get(uid);
		if (subscriptions) {
			subscriptions.delete(clientId);
			if (subscriptions.size === 0) {
				this.subscriptions.delete(uid);
			}
		}

		logger.debug(`Client ${clientId} unsubscribed from switch ${uid}`);
	}

	async sendCurrentState(clientId, uid) {
		try {
			const switchData = await redisClient.getSwitchState(uid);

			if (switchData) {
				const payload = {
					type: 'state_update',
					uid,
					state: switchData.state,
					timestamp: switchData.lastToggled || Date.now()
				};
				if (switchData.params && Object.keys(switchData.params).length > 0) {
					payload.params = switchData.params;
				}
				this.sendToClient(clientId, payload);
			} else {
				this.sendToClient(clientId, {
					type: 'error',
					message: 'Switch not found',
					uid
				});
			}
		} catch (error) {
			logger.error(`Error sending current state to client ${clientId}:`, error);
		}
	}

	sendToClient(clientId, data) {
		const client = this.clients.get(clientId);
		if (!client || client.ws.readyState !== WebSocket.OPEN) {
			return false;
		}

		try {
			client.ws.send(JSON.stringify(data));
			return true;
		} catch (error) {
			logger.error(`Error sending data to client ${clientId}:`, error);
			this.handleDisconnection(clientId);
			return false;
		}
	}

	broadcastToSwitch(uid, data) {
		const subscriptions = this.subscriptions.get(uid);
		if (!subscriptions || subscriptions.size === 0) {
			return 0;
		}

		let sentCount = 0;
		for (const clientId of subscriptions) {
			if (this.sendToClient(clientId, data)) {
				sentCount++;
			}
		}

		logger.debug(`Broadcasted to ${sentCount} clients for switch ${uid}`);
		return sentCount;
	}

	async setupRedisSubscription() {
		try {
			// Subscribe to all switch update channels using pattern
			await redisClient.subClient.pSubscribe('switch_updates:*', (message, channel) => {
				try {
					const uid = channel.split(':')[1];
					const updateData = JSON.parse(message);

					// Broadcast to WebSocket clients
					const payload = {
						type: 'state_update',
						uid: updateData.uid,
						state: updateData.state,
						timestamp: updateData.timestamp
					};
					if (updateData.params && Object.keys(updateData.params).length > 0) {
						payload.params = updateData.params;
					}
					this.broadcastToSwitch(uid, payload);
				} catch (error) {
					logger.error('Error processing Redis switch update:', error);
				}
			});

			logger.info('Redis WebSocket subscription established');
		} catch (error) {
			logger.error('Failed to setup Redis subscription:', error);
		}
	}

	// Health check and cleanup
	startHeartbeat() {
		setInterval(() => {
			const now = Date.now();
			const staleClients = [];

			// Check for stale connections
			for (const [clientId, client] of this.clients.entries()) {
				const timeSinceActivity = now - client.lastActivity;

				if (timeSinceActivity > 60000) { // 1 minute
					if (client.ws.readyState === WebSocket.OPEN) {
						// Send ping to check if client is alive
						client.ws.ping();
					} else {
						staleClients.push(clientId);
					}
				}

				if (timeSinceActivity > 300000) { // 5 minutes of no activity
					staleClients.push(clientId);
				}
			}

			// Clean up stale clients
			for (const clientId of staleClients) {
				this.handleDisconnection(clientId);
			}

			if (staleClients.length > 0) {
				logger.info(`Cleaned up ${staleClients.length} stale WebSocket connections`);
			}
		}, 30000); // Check every 30 seconds
	}

	getStats() {
		return {
			totalClients: this.clients.size,
			totalSubscriptions: this.subscriptions.size,
			clientsPerSwitch: Array.from(this.subscriptions.entries()).map(([uid, clients]) => ({
				uid,
				clientCount: clients.size
			}))
		};
	}
}

module.exports = new WebSocketManager();
// Exposed for unit tests (fresh instances avoid cross-test singleton state).
module.exports.WebSocketManager = WebSocketManager;
