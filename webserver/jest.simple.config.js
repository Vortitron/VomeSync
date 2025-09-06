module.exports = {
	testEnvironment: 'node',
	roots: ['<rootDir>/src', '<rootDir>/tests'],
	testMatch: [
		'**/__tests__/**/*.js',
		'**/?(*.)+(spec|test).js'
	],
	collectCoverageFrom: [
		'src/**/*.js',
		'!src/server.js',
		'!**/node_modules/**',
		'!**/coverage/**'
	],
	coverageDirectory: 'coverage',
	coverageReporters: [
		'text',
		'lcov',
		'html'
	],
	setupFilesAfterEnv: ['<rootDir>/tests/simple-setup.js'],
	testTimeout: 30000,
	verbose: true,
	forceExit: true,
	detectOpenHandles: true,
	maxWorkers: 1
};
