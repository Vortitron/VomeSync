const winston = require('winston');
const config = require('../config/config');

const logger = winston.createLogger({
	level: config.logging.level,
	format: winston.format.combine(
		winston.format.timestamp(),
		winston.format.errors({ stack: true }),
		winston.format.json()
	),
	defaultMeta: { service: 'vomesync-webserver' },
	transports: [
		new winston.transports.Console({
			format: winston.format.combine(
				winston.format.colorize(),
				winston.format.simple()
			)
		})
	]
});

// Add file transport in production
if (config.server.env === 'production') {
	logger.add(new winston.transports.File({
		filename: config.logging.file,
		maxsize: 5242880, // 5MB
		maxFiles: 5
	}));
}

module.exports = logger;
