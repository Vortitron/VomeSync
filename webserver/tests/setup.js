/**
 * Jest test setup file
 * Runs before each test file
 */

// Redis Memory Server is handled in globalSetup.js

// Global test configuration
process.env.NODE_ENV = 'test';
process.env.JWT_SECRET = 'test-jwt-secret-for-testing-only';
process.env.LOG_LEVEL = 'error'; // Reduce log noise during tests

// Increase timeout for async operations
jest.setTimeout(30000);

// Mock console methods in tests to reduce noise
const originalConsoleError = console.error;
const originalConsoleWarn = console.warn;

beforeAll(() => {
	// Only show critical errors during tests
	console.error = jest.fn();
	console.warn = jest.fn();
});

afterAll(() => {
	// Restore console methods
	console.error = originalConsoleError;
	console.warn = originalConsoleWarn;
});

// Note: individual test files manage Redis lifecycle to avoid cross-test interference.

// Global test utilities
global.testUtils = {
	// Generate test UUIDs (proper UUID v4 format)
	generateTestUUID: () => {
		const { v4: uuidv4 } = require('uuid');
		return uuidv4();
	},

	// Wait for async operations
	sleep: (ms) => new Promise(resolve => setTimeout(resolve, ms)),

	// Create test switch data
	createTestSwitchData: (overrides = {}) => ({
		description: 'Test Switch',
		location: 'Test City',
		category: 'Test',
		publicize: false,
		...overrides
	}),

	// Create test personal key
	createTestPersonalKey: () => {
		const { v4: uuidv4 } = require('uuid');
		return uuidv4();
	},

	// Create Ed25519 keypair and return raw public key + key objects
	createEd25519Keypair: () => {
		const crypto = require('crypto');
		const { publicKey, privateKey } = crypto.generateKeyPairSync('ed25519');
		const spki = publicKey.export({ format: 'der', type: 'spki' });
		const rawPublicKey = spki.subarray(spki.length - 32); // Ed25519 raw key is final 32 bytes in SPKI
		return { publicKey, privateKey, rawPublicKey };
	},

	// Sign a UTF-8 message with an Ed25519 private key; returns base64url
	ed25519SignBase64Url: (privateKey, message) => {
		const crypto = require('crypto');
		const sig = crypto.sign(null, Buffer.from(message, 'utf8'), privateKey);
		return Buffer.from(sig).toString('base64url');
	}
};
