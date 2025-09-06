/**
 * Global Jest teardown
 * Runs once after all tests
 */

module.exports = async () => {
	// Stop the in-memory Redis server
	if (global.__REDIS_SERVER__) {
		await global.__REDIS_SERVER__.stop();
		console.log('Test Redis server stopped');
	}
};
