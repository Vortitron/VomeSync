const winston = require('winston');
const fs = require('fs');
const path = require('path');
const config = require('../config/config');
const redact = require('./redact');

// Redact in place. `redact()` rebuilds plain objects from Object.entries(),
// which keeps every string key but silently drops the Symbol ones winston
// carries on `info` — Symbol(level) above all. A transport checks
// `this.levels[info[Symbol.for('level')]] <= this.levels[level]`, and with the
// symbol gone that compares `undefined <= n`, which is false, so the transport
// discards the line. Returning a fresh object therefore threw away *every* log
// the process ever emitted, on every transport, with no error anywhere.
// Assigning the redacted values back over the original keeps the symbols.
const SPLAT = Symbol.for('splat');
const redactFormat = winston.format((info) => {
	try {
		const redacted = redact(info);
		if (!redacted || typeof redacted !== 'object') {
			return info;
		}
		// Format arguments live on a symbol, so they never reached redact()
		// above; they are only interpolated if format.splat() is in the chain,
		// but redact them anyway so adding it later cannot leak a key.
		if (Array.isArray(info[SPLAT])) {
			info[SPLAT] = redact(info[SPLAT]);
		}
		return Object.assign(info, redacted);
	} catch (_err) {
		return info;
	}
});

const consoleFormat = winston.format.printf(({ level, message, timestamp: ts, stack, ...rest }) => {
	const meta = Object.keys(rest).filter(k => k !== 'service').length > 0
		? ' ' + JSON.stringify(Object.fromEntries(Object.entries(rest).filter(([k]) => k !== 'service')))
		: '';
	const msg = stack || message;
	return `${ts} ${level}: ${msg}${meta}`;
});

const logger = winston.createLogger({
	level: config.logging.level,
	format: winston.format.combine(
		redactFormat(),
		winston.format.timestamp({ format: 'YYYY-MM-DD HH:mm:ss.SSS' }),
		winston.format.errors({ stack: true }),
		winston.format.json()
	),
	defaultMeta: { service: 'vomesync-webserver' },
	transports: [
		new winston.transports.Console({
			format: winston.format.combine(
				redactFormat(),
				winston.format.timestamp({ format: 'YYYY-MM-DD HH:mm:ss.SSS' }),
				winston.format.colorize(),
				consoleFormat
			)
		})
	]
});

function _canWriteLogFile(filePath) {
	try {
		const dir = path.dirname(filePath);
		fs.mkdirSync(dir, { recursive: true });
		fs.accessSync(dir, fs.constants.W_OK);
		const fd = fs.openSync(filePath, 'a');
		fs.closeSync(fd);
		return true;
	} catch (_err) {
		return false;
	}
}

// Add file transport in production
if (config.server.env === 'production') {
	if (_canWriteLogFile(config.logging.file)) {
		logger.add(new winston.transports.File({
			filename: config.logging.file,
			maxsize: 5242880, // 5MB
			maxFiles: 5
		}));
	} else {
		logger.warn('File logging disabled (log path not writable): %s', config.logging.file);
	}
}

module.exports = logger;
