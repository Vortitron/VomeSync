const express = require('express');
const http = require('http');
const https = require('https');
const fs = require('fs');
const path = require('path');
const cors = require('cors');
const helmet = require('helmet');
const config = require('./config/config');
const logger = require('./utils/logger');
const redisClient = require('./utils/redis');
const media = require('./utils/media');
const webSocketManager = require('./websocket/manager');
const apiRoutes = require('./routes/api');

class VomeSyncServer {
	constructor() {
		this.app = express();
		this.server = null;
		this.wsServer = null;
		this.isShuttingDown = false;
	}

	async initialize() {
		try {
			// Connect to Redis first
			await redisClient.connect();

			// Backfill global switch index if empty (one-off migration)
			try {
				const totalCount = await redisClient.getTotalSwitchCount();
				if (totalCount === 0) {
					logger.info('Global switch index empty – running backfill…');
					await redisClient.backfillGlobalSwitchIndex();
				}
			} catch (backfillError) {
				logger.warn('Global switch index backfill failed (non-fatal):', backfillError.message || backfillError);
			}

			// Configure Express middleware
			this.setupMiddleware();

			// Setup routes
			this.setupRoutes();

			// Create HTTP/HTTPS server
			await this.createServer();

			// Initialize WebSocket manager
			await webSocketManager.initialize(this.wsServer || this.server);

			// Start listening (only after WS is initialised, so readiness checks don't race)
			await this.startListening();

			// Start heartbeat for WebSocket connections
			webSocketManager.startHeartbeat();

			// Setup graceful shutdown
			this.setupGracefulShutdown();

			logger.info('VomeSync server initialized successfully');

		} catch (error) {
			logger.error('Failed to initialize server:', error);
			process.exit(1);
		}
	}

	setupMiddleware() {
		// Security middleware
		this.app.use(helmet({
			contentSecurityPolicy: {
				directives: {
					defaultSrc: ['\'self\''],
					connectSrc: ['\'self\'', 'wss:', 'ws:']
				}
			}
		}));

		// CORS configuration
		this.app.use(cors({
			origin: config.server.corsOrigins,
			credentials: true,
			methods: ['GET', 'POST', 'PUT', 'DELETE', 'OPTIONS'],
			allowedHeaders: ['Content-Type', 'Authorization', 'X-Personal-Key', 'X-Api-Key']
		}));

		// Body parsing
		this.app.use(express.json({ limit: '10mb' }));
		this.app.use(express.urlencoded({ extended: true, limit: '10mb' }));

		// Request logging
		this.app.use((req, res, next) => {
			const start = Date.now();

			res.on('finish', () => {
				const duration = Date.now() - start;
				logger.info(`${req.method} ${req.path} - ${res.statusCode} - ${duration}ms`);
			});

			next();
		});

		// Trust proxy headers if behind reverse proxy
		if (config.server.env === 'production') {
			this.app.set('trust proxy', 1);
		}
	}

	setupRoutes() {
		// Locally hosted media (icons/banners) served via the API origin.
		const mediaRoot = media.getMediaRootDir();
		try {
			fs.mkdirSync(mediaRoot, { recursive: true });
		} catch (_err) {
			// Non-fatal; endpoints will fail when attempting to store media.
		}
		this.app.use('/api/media', express.static(mediaRoot, {
			fallthrough: true,
			setHeaders: (res, filePath) => {
				if (String(filePath).endsWith('.webp')) {
					res.setHeader('Cache-Control', 'public, max-age=31536000, immutable');
				}
				// Best-effort: serve correct content type for common extensions
				const ext = path.extname(filePath).toLowerCase();
				if (ext === '.webp') {
					res.setHeader('Content-Type', 'image/webp');
				}
			}
		}));

		// API routes
		this.app.use('/api', apiRoutes);

		// Root endpoint
		this.app.get('/', (req, res) => {
			res.json({
				name: 'VomeSync WebServer',
				version: '1.0.0',
				description: 'Public Remote Switch API and WebSocket Server',
				endpoints: {
					api: '/api',
					websocket: '/ws?uid={switch_uid}',
					health: '/api/health',
					docs: 'https://github.com/Vortitron/VomeSync'
				},
				status: 'operational'
			});
		});

		// Handle 404s
		this.app.use('*', (req, res) => {
			res.status(404).json({
				success: false,
				error: 'Endpoint not found',
				path: req.originalUrl
			});
		});

		// Global error handler
		this.app.use((error, req, res, _next) => {
			logger.error('Unhandled server error:', error);

			res.status(500).json({
				success: false,
				error: 'Internal server error',
				...(config.server.env === 'development' && { details: error.message })
			});
		});
	}

	async createServer() {
		if (config.ssl.enabled) {
			// HTTPS server
			const sslOptions = {
				cert: fs.readFileSync(config.ssl.certPath),
				key: fs.readFileSync(config.ssl.keyPath)
			};

			this.server = https.createServer(sslOptions, this.app);
			logger.info('Created HTTPS server');
		} else {
			// HTTP server
			this.server = http.createServer(this.app);
			logger.info('Created HTTP server');
		}

		// WebSocket server: split onto WS_PORT (used by nginx proxy upstream), unless it matches API port.
		if (config.server.wsPort && config.server.wsPort !== config.server.port) {
			this.wsServer = http.createServer();
			logger.info('Created WebSocket HTTP server');
		} else {
			this.wsServer = this.server;
		}
	}

	async startListening() {
		// Start listening (API + WS)
		await Promise.all([
			new Promise((resolve, reject) => {
				this.server.listen(config.server.port, (error) => {
					if (error) {
						reject(error);
					} else {
						resolve();
					}
				});
			}),
			new Promise((resolve, reject) => {
				if (!this.wsServer || this.wsServer === this.server) {
					resolve();
					return;
				}
				this.wsServer.listen(config.server.wsPort, (error) => {
					if (error) {
						reject(error);
					} else {
						resolve();
					}
				});
			})
		]);

		const protocol = config.ssl.enabled ? 'https' : 'http';
		logger.info(`API listening on ${protocol}://localhost:${config.server.port}`);
		if (this.wsServer && this.wsServer !== this.server) {
			logger.info(`WebSocket listening on ws://localhost:${config.server.wsPort}`);
		}
	}

	setupGracefulShutdown() {
		const shutdown = async (signal) => {
			if (this.isShuttingDown) {
				logger.warn('Shutdown already in progress, forcing exit');
				process.exit(1);
				return;
			}

			this.isShuttingDown = true;
			logger.info(`Received ${signal}, starting graceful shutdown...`);

			const shutdownTimeout = setTimeout(() => {
				logger.error('Graceful shutdown timeout, forcing exit');
				process.exit(1);
			}, 30000); // 30 second timeout

			try {
				// Close WebSocket connections
				if (webSocketManager.wss) {
					webSocketManager.wss.close();
					logger.info('WebSocket server closed');
				}

				const closeServer = (srv, label) => new Promise((resolve) => {
					if (!srv) {
						resolve();
						return;
					}
					srv.close(() => {
						logger.info('%s server closed', label);
						resolve();
					});
				});

				await closeServer(this.server, 'HTTP');
				if (this.wsServer && this.wsServer !== this.server) {
					await closeServer(this.wsServer, 'WebSocket HTTP');
				}

				// Disconnect from Redis
				await redisClient.disconnect();
				logger.info('Redis disconnected');

				clearTimeout(shutdownTimeout);
				logger.info('Graceful shutdown completed');
				process.exit(0);
			} catch (error) {
				logger.error('Error during shutdown:', error);
				clearTimeout(shutdownTimeout);
				process.exit(1);
			}
		};

		// Handle shutdown signals
		process.on('SIGTERM', () => shutdown('SIGTERM'));
		process.on('SIGINT', () => shutdown('SIGINT'));

		// Handle uncaught exceptions
		process.on('uncaughtException', (error) => {
			logger.error('Uncaught exception:', error);
			shutdown('uncaughtException');
		});

		process.on('unhandledRejection', (reason, promise) => {
			logger.error('Unhandled rejection at:', promise, 'reason:', reason);
			shutdown('unhandledRejection');
		});
	}

	async start() {
		logger.info('Starting VomeSync WebServer...');
		await this.initialize();
	}
}

// Start server if this file is run directly
if (require.main === module) {
	const server = new VomeSyncServer();
	server.start().catch((error) => {
		logger.error('Failed to start server:', error);
		process.exit(1);
	});
}

module.exports = VomeSyncServer;
