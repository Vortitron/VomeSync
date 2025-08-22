const express = require('express');
const http = require('http');
const https = require('https');
const fs = require('fs');
const cors = require('cors');
const helmet = require('helmet');
const config = require('./config/config');
const logger = require('./utils/logger');
const redisClient = require('./utils/redis');
const webSocketManager = require('./websocket/manager');
const apiRoutes = require('./routes/api');

class VomeSyncServer {
	constructor() {
		this.app = express();
		this.server = null;
		this.isShuttingDown = false;
	}

	async initialize() {
		try {
			// Connect to Redis first
			await redisClient.connect();
			
			// Configure Express middleware
			this.setupMiddleware();
			
			// Setup routes
			this.setupRoutes();
			
			// Create HTTP/HTTPS server
			await this.createServer();
			
			// Initialize WebSocket manager
			await webSocketManager.initialize(this.server);
			
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
					defaultSrc: ["'self'"],
					connectSrc: ["'self'", "wss:", "ws:"]
				}
			}
		}));

		// CORS configuration
		this.app.use(cors({
			origin: config.server.corsOrigins,
			credentials: true,
			methods: ['GET', 'POST', 'PUT', 'DELETE', 'OPTIONS'],
			allowedHeaders: ['Content-Type', 'Authorization', 'X-Personal-Key']
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
					docs: 'https://github.com/your-org/vomesync'
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
		this.app.use((error, req, res, next) => {
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

		// Start listening
		await new Promise((resolve, reject) => {
			this.server.listen(config.server.port, (error) => {
				if (error) {
					reject(error);
				} else {
					resolve();
				}
			});
		});

		const protocol = config.ssl.enabled ? 'https' : 'http';
		logger.info(`Server listening on ${protocol}://localhost:${config.server.port}`);
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
				// Stop accepting new connections
				this.server.close(async () => {
					logger.info('HTTP server closed');

					try {
						// Close WebSocket connections
						if (webSocketManager.wss) {
							webSocketManager.wss.close();
							logger.info('WebSocket server closed');
						}

						// Disconnect from Redis
						await redisClient.disconnect();
						logger.info('Redis disconnected');

						clearTimeout(shutdownTimeout);
						logger.info('Graceful shutdown completed');
						process.exit(0);
					} catch (error) {
						logger.error('Error during shutdown cleanup:', error);
						clearTimeout(shutdownTimeout);
						process.exit(1);
					}
				});
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
