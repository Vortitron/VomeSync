/**
 * Unit tests for the logger.
 *
 * redact.test.js covers redaction as a pure function, and it passed happily
 * for seven months while the logger delivered nothing at all: the redaction
 * *format* returned a new object, dropping winston's Symbol(level), so every
 * transport failed its level check and dropped the line. Docker captured an
 * empty stream and no error was raised anywhere.
 *
 * So test the property that actually matters — a log call reaches a transport,
 * still redacted — rather than only testing the helper in isolation.
 */

const winston = require('winston');
const Transport = require('winston-transport');
const logger = require('../../../src/utils/logger');

class CaptureTransport extends Transport {
	constructor() {
		super();
		this.entries = [];
	}

	log(info, next) {
		this.entries.push(info);
		next();
	}
}

describe('logger', () => {
	let capture;
	let previousLevel;
	let consoleTransport;

	beforeEach(() => {
		// tests/setup.js pins LOG_LEVEL=error to keep the suite quiet, which
		// would filter out the info/warn lines these tests assert on. Open the
		// level up and mute the console transport instead, so the assertions
		// exercise the real chain without printing through it.
		previousLevel = logger.level;
		logger.level = 'debug';
		consoleTransport = logger.transports.find(t => t instanceof winston.transports.Console);
		if (consoleTransport) {
			consoleTransport.silent = true;
		}

		capture = new CaptureTransport();
		logger.add(capture);
	});

	afterEach(() => {
		logger.remove(capture);
		logger.level = previousLevel;
		if (consoleTransport) {
			consoleTransport.silent = false;
		}
	});

	test('delivers a log line to its transports', () => {
		logger.info('hello from the logger');

		expect(capture.entries).toHaveLength(1);
		expect(capture.entries[0].message).toBe('hello from the logger');
	});

	test('delivers at every configured level', () => {
		logger.error('an error');
		logger.warn('a warning');
		logger.info('some info');

		expect(capture.entries.map(e => e.level)).toEqual(['error', 'warn', 'info']);
	});

	test('keeps the level symbol transports filter on', () => {
		logger.info('symbol check');

		expect(capture.entries[0][Symbol.for('level')]).toBe('info');
	});

	test('still redacts sensitive metadata on the way through', () => {
		logger.info('request received', {
			token: 'super-secret-token',
			headers: { 'x-api-key': '22222222-2222-4222-8222-222222222222' },
			switchId: 'vs_test'
		});

		const entry = capture.entries[0];
		expect(entry.token).toBe('[REDACTED]');
		expect(entry.headers['x-api-key']).toBe('[REDACTED]');
		expect(entry.switchId).toBe('vs_test');
	});

	test('redacts accessKey fragments in the message itself', () => {
		logger.info('open https://example.com/switch/vs_test#accessKey=secret123');

		expect(capture.entries[0].message).toContain('accessKey=[REDACTED]');
		expect(capture.entries[0].message).not.toContain('secret123');
	});

	test('redacts format arguments held on the splat symbol', () => {
		logger.info('two values: %s %s', { token: 'secret-one' }, 'plain');

		const splat = capture.entries[0][Symbol.for('splat')];
		expect(splat[0].token).toBe('[REDACTED]');
		expect(splat[1]).toBe('plain');
	});

	test('logs an Error with its stack intact', () => {
		logger.error(new Error('something broke'));

		const entry = capture.entries[0];
		expect(entry.message).toBe('something broke');
		expect(typeof entry.stack).toBe('string');
	});
});
