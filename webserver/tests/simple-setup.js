/**
 * Simplified test setup without Redis Memory Server
 * Uses environment variables for Redis connection
 */

// Set test environment variables
process.env.NODE_ENV = 'test';
process.env.JWT_SECRET = 'test-jwt-secret-for-testing-only';
process.env.LOG_LEVEL = 'error';
process.env.REDIS_HOST = 'localhost';
process.env.REDIS_PORT = '6379';
process.env.REDIS_PASSWORD = '';

// Increase timeout for async operations
jest.setTimeout(30000);

// Mock console methods to reduce noise
const originalConsoleError = console.error;
const originalConsoleWarn = console.warn;

beforeAll(() => {
	console.error = jest.fn();
	console.warn = jest.fn();
});

afterAll(() => {
	console.error = originalConsoleError;
	console.warn = originalConsoleWarn;
});

// Mock Redis client for tests
jest.mock('../src/utils/redis', () => {
	return require('./mocks/redis');
});

// Global test utilities with proper UUID generation
global.testUtils = {
	generateTestUUID: () => {
		const { v4: uuidv4 } = require('uuid');
		return uuidv4();
	},
	
	sleep: (ms) => new Promise(resolve => setTimeout(resolve, ms)),
	
	createTestSwitchData: (overrides = {}) => ({
		description: 'Test Switch',
		location: 'Test City',
		category: 'Test',
		publicize: false,
		...overrides
	}),
	
	createTestPersonalKey: () => {
		const { v4: uuidv4 } = require('uuid');
		return uuidv4();
	}
};
