const dotenv = require('dotenv');

// Load environment variables
dotenv.config();

const config = {
	server: {
		port: parseInt(process.env.PORT, 10) || 3000,
		wsPort: parseInt(process.env.WS_PORT, 10) || 3001,
		env: process.env.NODE_ENV || 'development',
		corsOrigins: process.env.CORS_ORIGINS ? process.env.CORS_ORIGINS.split(',') : ['*']
	},
	redis: {
		host: process.env.REDIS_HOST || 'localhost',
		port: parseInt(process.env.REDIS_PORT, 10) || 6379,
		password: process.env.REDIS_PASSWORD || undefined,
		db: parseInt(process.env.REDIS_DB, 10) || 0,
		retryDelayOnFailover: 100,
		maxRetriesPerRequest: 3
	},
	security: {
		jwtSecret: process.env.JWT_SECRET || 'dev-secret-change-in-production',
		rateLimitWindowMs: parseInt(process.env.RATE_LIMIT_WINDOW_MS, 10) || 900000, // 15 minutes
		rateLimitMaxRequests: parseInt(process.env.RATE_LIMIT_MAX_REQUESTS, 10) || 100
	},
	ssl: {
		certPath: process.env.SSL_CERT_PATH,
		keyPath: process.env.SSL_KEY_PATH,
		enabled: !!(process.env.SSL_CERT_PATH && process.env.SSL_KEY_PATH)
	},
	analytics: {
		enabled: process.env.ENABLE_ANALYTICS === 'true',
		differentialPrivacyEpsilon: parseFloat(process.env.DIFFERENTIAL_PRIVACY_EPSILON) || 1.0
	},
	logging: {
		level: process.env.LOG_LEVEL || 'info',
		file: process.env.LOG_FILE || 'logs/vomesync.log'
	}
};

// Validation
if (config.server.env === 'production' && config.security.jwtSecret === 'dev-secret-change-in-production') {
	throw new Error('JWT_SECRET must be set in production environment');
}

module.exports = config;
