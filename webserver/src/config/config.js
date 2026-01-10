const dotenv = require('dotenv');

// Load environment variables
dotenv.config();

const sslEnabled = process.env.ENABLE_SSL === 'true';

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
		// Used to derive stable, non-reversible IDs for bearer secrets stored in Redis.
		// Defaults to JWT_SECRET so existing deployments don't require extra config.
		keyHashSecret: process.env.KEY_HASH_SECRET || process.env.JWT_SECRET || 'dev-secret-change-in-production',
		rateLimitWindowMs: parseInt(process.env.RATE_LIMIT_WINDOW_MS, 10) || 900000, // 15 minutes
		rateLimitMaxRequests: parseInt(process.env.RATE_LIMIT_MAX_REQUESTS, 10) || 100
	},
	ssl: {
		certPath: process.env.SSL_CERT_PATH || '',
		keyPath: process.env.SSL_KEY_PATH || '',
		enabled: sslEnabled && !!(process.env.SSL_CERT_PATH && process.env.SSL_KEY_PATH)
	},
	analytics: {
		enabled: process.env.ENABLE_ANALYTICS === 'true',
		differentialPrivacyEpsilon: parseFloat(process.env.DIFFERENTIAL_PRIVACY_EPSILON) || 1.0
	},
	hcaptcha: {
		secret: process.env.HCAPTCHA_SECRET || '',
		siteKey: process.env.HCAPTCHA_SITEKEY || '',
		bypassToken: process.env.HCAPTCHA_BYPASS_TOKEN || ''
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

if (sslEnabled && (!config.ssl.certPath || !config.ssl.keyPath)) {
	throw new Error('ENABLE_SSL is true but SSL_CERT_PATH / SSL_KEY_PATH are not set');
}

module.exports = config;
