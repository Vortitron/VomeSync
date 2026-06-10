/**
 * Global Jest setup
 * Runs once before all tests
 */

const { RedisMemoryServer } = require('redis-memory-server');

module.exports = async () => {
	// Start in-memory Redis server for testing.  No fixed port: CI runs the
	// unit and integration jest processes in parallel, and a pinned port made
	// the second globalSetup fail with "Port 6380 already in use" — an
	// ephemeral port keeps every jest process self-contained.
	const redisServer = new RedisMemoryServer({
		instance: {
			args: ['--maxmemory', '50mb']
		}
	});

	await redisServer.start();

	const host = await redisServer.getHost();
	const port = await redisServer.getPort();

	// Store connection info globally
	global.__REDIS_HOST__ = host;
	global.__REDIS_PORT__ = port;
	global.__REDIS_SERVER__ = redisServer;

	// Set environment variables for tests
	process.env.REDIS_HOST = host;
	process.env.REDIS_PORT = port;
	process.env.REDIS_PASSWORD = '';
	process.env.REDIS_DB = 0;

	console.log(`Test Redis server started on ${host}:${port}`);
};
