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

// Clean up any open handles after each test
afterEach(async () => {
	// Close any Redis connections
	const redisClient = require('../src/utils/redis');
	if (redisClient.isConnected) {
		await redisClient.disconnect();
	}
});

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
	}
};
