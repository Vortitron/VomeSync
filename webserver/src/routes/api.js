/**
 * API route assembler.
 *
 * Mounts the focused sub-routers onto a single Express router that is
 * attached to the /api prefix in server.js.
 */
const express = require('express');
const logger = require('../utils/logger');

const v2Routes = require('./v2-routes');
const adminRoutes = require('./admin-routes');
const legacyRoutes = require('./legacy-routes');
const publicRoutes = require('./public-routes');

const router = express.Router();

// Mount sub-routers (order matters for overlapping paths)
router.use(v2Routes);
router.use(adminRoutes);
router.use(legacyRoutes);
router.use(publicRoutes);

// Global error handler for all API routes
router.use((error, req, res, _next) => {
	logger.error('API route error:', error);

	res.status(500).json({
		success: false,
		error: 'Internal server error',
		...(process.env.NODE_ENV === 'development' && { details: error.message })
	});
});

module.exports = router;
