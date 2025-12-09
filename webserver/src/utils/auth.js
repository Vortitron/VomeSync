const jwt = require('jsonwebtoken');
const { v4: uuidv4 } = require('uuid');
const config = require('../config/config');
const redisClient = require('./redis');
const logger = require('./logger');
class AuthManager {
	generatePersonalKey() {
		return uuidv4();
	}

	generateJWT(personalKey) {
		return jwt.sign(
			{ personalKey, type: 'vomesync_key' },
			config.security.jwtSecret,
			{ expiresIn: '1y' }
		);
	}

	verifyJWT(token) {
		try {
			const decoded = jwt.verify(token, config.security.jwtSecret);
			return decoded;
		} catch (error) {
			logger.warn('JWT verification failed:', error.message);
			return null;
		}
	}

	async validatePersonalKey(personalKey) {
		if (!personalKey) {
			return false;
		}

		try {
			const isValid = await redisClient.validatePersonalKey(personalKey);
			return isValid;
		} catch (error) {
			logger.error('Error validating personal key:', error);
			return false;
		}
	}

	async authenticateSwitch(uid, personalKey) {
		try {
			// Get switch data
			const switchData = await redisClient.getSwitchState(uid);

			if (!switchData) {
				return { success: false, error: 'Switch not found' };
			}

			// Check if personal key matches
			if (switchData.personalKey !== personalKey) {
				return { success: false, error: 'Unauthorized: Invalid personal key for this switch' };
			}

			return { success: true, switchData };
		} catch (error) {
			logger.error('Error authenticating switch:', error);
			return { success: false, error: 'Authentication failed' };
		}
	}

	async verifyCaptcha(token) {
		const { secret, bypassToken } = config.hcaptcha;

		// Disabled when secret is not set
		if (!secret) {
			return { success: true, reason: 'captcha_disabled' };
		}

		if (!token) {
			return { success: false, error: 'Captcha required' };
		}

		// Test/staging bypass
		if (bypassToken) {
			if (token === bypassToken) {
				return { success: true, reason: 'bypass_token' };
			}
			// Fail fast when bypass token is configured but mismatched (avoids external call in tests)
			return { success: false, error: 'Captcha verification failed' };
		}

		try {
			const params = new URLSearchParams();
			params.append('response', token);
			params.append('secret', secret);

			const response = await fetch('https://hcaptcha.com/siteverify', {
				method: 'POST',
				headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
				body: params
			});

			const data = await response.json();
			if (data.success) {
				return { success: true };
			}

			logger.warn('Captcha verification failed', data['error-codes']);
			return { success: false, error: 'Captcha verification failed' };
		} catch (error) {
			logger.error('Captcha verification error:', error);
			return { success: false, error: 'Captcha verification failed' };
		}
	}

	// Middleware for protecting routes that require personal key
	requireAuth() {
		return async (req, res, next) => {
			const personalKey = req.body.personalKey || req.headers['x-personal-key'];

			if (!personalKey) {
				return res.status(401).json({
					success: false,
					error: 'Personal key required'
				});
			}

			const isValid = await this.validatePersonalKey(personalKey);

			if (!isValid) {
				return res.status(401).json({
					success: false,
					error: 'Invalid or expired personal key'
				});
			}

			req.personalKey = personalKey;
			next();
		};
	}

	// Middleware for switch-specific authentication (supports personalKey or apiKey)
	requireSwitchAuth() {
		return async (req, res, next) => {
			const { uid } = req.params;
			const apiKey = req.body.apiKey || req.headers['x-api-key'] || req.query.apiKey;
			let personalKey = req.body.personalKey || req.headers['x-personal-key'] || req.query.personalKey;

			// If apiKey provided, resolve to personalKey
			if (!personalKey && apiKey) {
				personalKey = await redisClient.resolvePersonalKeyFromApiKey(apiKey);
			}

			if (!personalKey) {
				return res.status(401).json({
					success: false,
					error: 'Personal key or API key required'
				});
			}

			const authResult = await this.authenticateSwitch(uid, personalKey);

			if (!authResult.success) {
				return res.status(401).json({
					success: false,
					error: authResult.error
				});
			}

			req.personalKey = personalKey;
			req.apiKeyUsed = apiKey || null;
			req.switchData = authResult.switchData;
			next();
		};
	}

	// Rate limiting helper
	createRateLimitKey(identifier, action) {
		return `rate_limit:${action}:${identifier}`;
	}

	async checkRateLimit(identifier, action, limit = 100, windowMs = 900000) {
		const key = this.createRateLimitKey(identifier, action);

		try {
			const current = await redisClient.client.incr(key);

			if (current === 1) {
				await redisClient.client.expire(key, Math.ceil(windowMs / 1000));
			}

			return {
				allowed: current <= limit,
				current,
				limit,
				resetTime: Date.now() + windowMs
			};
		} catch (error) {
			logger.error('Rate limit check failed:', error);
			// Allow request if Redis fails
			return { allowed: true, current: 0, limit, resetTime: Date.now() + windowMs };
		}
	}

	// Rate limiting middleware
	rateLimit(action, limit = null, windowMs = null) {
		// Disable rate limiting during automated tests
		if (process.env.NODE_ENV === 'test') {
			return (_req, res, next) => {
				res.set({
					'X-RateLimit-Limit': limit || config.security.rateLimitMaxRequests,
					'X-RateLimit-Remaining': (limit || config.security.rateLimitMaxRequests),
					'X-RateLimit-Reset': new Date(Date.now() + (windowMs || config.security.rateLimitWindowMs)).toISOString()
				});
				next();
			};
		}

		const effectiveLimit = limit || config.security.rateLimitMaxRequests;
		const effectiveWindow = windowMs || config.security.rateLimitWindowMs;

		return async (req, res, next) => {
			const identifier = req.ip || 'unknown';
			const rateLimitResult = await this.checkRateLimit(identifier, action, effectiveLimit, effectiveWindow);

			// Set rate limit headers
			res.set({
				'X-RateLimit-Limit': effectiveLimit,
				'X-RateLimit-Remaining': Math.max(0, effectiveLimit - rateLimitResult.current),
				'X-RateLimit-Reset': new Date(rateLimitResult.resetTime).toISOString()
			});

			if (!rateLimitResult.allowed) {
				return res.status(429).json({
					success: false,
					error: 'Rate limit exceeded',
					retryAfter: Math.ceil((rateLimitResult.resetTime - Date.now()) / 1000)
				});
			}

			next();
		};
	}
}

module.exports = new AuthManager();
