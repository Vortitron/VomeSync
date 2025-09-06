module.exports = {
	testEnvironment: 'node',
	roots: ['<rootDir>/src', '<rootDir>/tests'],
	testMatch: [
		'**/__tests__/**/*.js',
		'**/?(*.)+(spec|test).js'
	],
	collectCoverageFrom: [
		'src/**/*.js',
		'!src/server.js', // Exclude main server file from coverage
		'!**/node_modules/**',
		'!**/coverage/**'
	],
	coverageDirectory: 'coverage',
	coverageReporters: [
		'text',
		'lcov',
		'html'
	],
	setupFilesAfterEnv: ['<rootDir>/tests/setup.js'],
	testTimeout: 30000,
	verbose: true,
	forceExit: true,
	detectOpenHandles: true,
	maxWorkers: 1, // Run tests sequentially to avoid Redis conflicts
	globalSetup: '<rootDir>/tests/globalSetup.js',
	globalTeardown: '<rootDir>/tests/globalTeardown.js'
};
