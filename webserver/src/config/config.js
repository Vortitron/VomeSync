const dotenv = require('dotenv');

// Load environment variables
dotenv.config();

const sslEnabled = process.env.ENABLE_SSL === 'true';
const parsePositiveInt = (value, fallback) => {
	const parsed = parseInt(value, 10);
	return Number.isFinite(parsed) && parsed >= 0 ? parsed : fallback;
};

const config = {
	server: {
		port: parseInt(process.env.PORT, 10) || 3000,
		wsPort: parseInt(process.env.WS_PORT, 10) || 3001,
		env: process.env.NODE_ENV || 'development',
		corsOrigins: process.env.CORS_ORIGINS ? process.env.CORS_ORIGINS.split(',') : ['*']
	},
	redis: {
		host: process.env.REDIS_HOST || 'localhost',
		port: parseInt(process.env.REDIS_PORT, 10) || 6379,
		password: process.env.REDIS_PASSWORD || undefined,
		db: parseInt(process.env.REDIS_DB, 10) || 0,
		retryDelayOnFailover: 100,
		maxRetriesPerRequest: 3
	},
	security: {
		jwtSecret: process.env.JWT_SECRET || 'dev-secret-change-in-production',
		// Previous JWT secret (accepted during rotation window). Leave empty when not rotating.
		jwtSecretOld: process.env.JWT_SECRET_OLD || '',
		// Used to derive stable, non-reversible IDs for bearer secrets stored in Redis.
		// Defaults to JWT_SECRET so existing deployments don't require extra config.
		keyHashSecret: process.env.KEY_HASH_SECRET || process.env.JWT_SECRET || 'dev-secret-change-in-production',
		// Previous hash secret (keys derived with old secret are still recognised during rotation).
		keyHashSecretOld: process.env.KEY_HASH_SECRET_OLD || '',
		rateLimitWindowMs: parseInt(process.env.RATE_LIMIT_WINDOW_MS, 10) || 900000, // 15 minutes
		rateLimitMaxRequests: parseInt(process.env.RATE_LIMIT_MAX_REQUESTS, 10) || 100,
		legacyApiEnabled: process.env.LEGACY_API_ENABLED === 'true',
		sessionTokensEnabled: process.env.SESSION_TOKENS_ENABLED === 'true',
		sessionTokenApiKeyTtlSeconds: parseInt(process.env.SESSION_TOKEN_API_KEY_TTL_SECONDS, 10) || 900,
		adminApiKey: process.env.ADMIN_API_KEY || ''
	},
	ssl: {
		certPath: process.env.SSL_CERT_PATH || '',
		keyPath: process.env.SSL_KEY_PATH || '',
		enabled: sslEnabled && !!(process.env.SSL_CERT_PATH && process.env.SSL_KEY_PATH)
	},
	relay: {
		// Shared secret authenticating portal⇄backend internal calls (dispatch
		// in, secret-verify out).  Empty disables the relay (fails closed).
		internalSecret: process.env.RELAY_INTERNAL_SECRET || '',
		// Portal endpoint that authenticates a component's presented relay secret.
		portalVerifyUrl: process.env.RELAY_PORTAL_VERIFY_URL || 'https://vome.io/api/internal/relay/verify',
		// How long the backend waits for a component's ha_rpc_response.
		rpcTimeoutMs: parsePositiveInt(process.env.RELAY_RPC_TIMEOUT_MS, 20000),
		// ── Full-UI forwarding (paid "friendly domain" remote access) ────────
		// HS256 secret shared with the portal: the portal mints a short-lived
		// access cookie after checking ownership + active subscription, and the
		// browser proxy verifies it here.  Empty disables the proxy (fails closed).
		forwardSecret: process.env.RELAY_FORWARD_SECRET || '',
		// Port for the browser-facing reverse proxy.  Bound on all interfaces
		// (0.0.0.0) because nginx runs on the SEPARATE portal host and points the
		// existing `*.home.vome.io` wildcard here per-slug via map.d
		// (the portal's RELAY_FORWARD_PROXY_TARGET = this host:port).  Restrict
		// it to the portal host with a firewall rule.  0 disables the proxy.
		forwardPort: parseInt(process.env.FORWARD_PORT, 10) || 0,
		// Where the proxy sends an unauthenticated browser to obtain a cookie.
		forwardAuthoriseUrl: process.env.RELAY_FORWARD_AUTHORISE_URL || 'https://vome.io/remote/authorise',
		// Portal endpoint resolving a friendly host to its forwarding policy
		// (webhook pass-through / open companion-app access). Same shared
		// secret as portalVerifyUrl; misses fail closed to cookie-only.
		forwardPolicyUrl: process.env.RELAY_FORWARD_POLICY_URL || 'https://vome.io/api/internal/relay/forward-policy',
		// Cookie carrying the access token (scoped to .vome.io by the portal).
		forwardCookieName: process.env.RELAY_FORWARD_COOKIE || 'vome_fwd',
		// Largest request body the proxy will buffer before forwarding (25 MiB).
		forwardMaxBodyBytes: parsePositiveInt(process.env.RELAY_FORWARD_MAX_BODY, 26214400),
		// ── Rate limits for *unauthenticated* forwarded traffic ──────────────
		// A request carrying a valid forwarding cookie is never limited here:
		// Vome already checked ownership + subscription to mint it.  Everything
		// else — the 302-to-authorise path, webhook deliveries, and all traffic
		// in `open` (companion-app) mode — reaches Home Assistant without Vome
		// vouching for it, so it gets a per-client budget.  Set a max to 0 to
		// disable that bucket.
		forwardRateWindowMs: parsePositiveInt(process.env.RELAY_FORWARD_RATE_WINDOW_MS, 300000),
		// Generous: one cold load of the HA frontend is ~100 requests, and a
		// household NATs several devices behind one address.
		forwardRateMax: parsePositiveInt(process.env.RELAY_FORWARD_RATE_MAX, 600),
		// Tight: this is HA's own login/token surface, where guessing pays off.
		forwardAuthRateMax: parsePositiveInt(process.env.RELAY_FORWARD_AUTH_RATE_MAX, 30),
		// Each accepted upgrade pins a relay tunnel until it closes.
		forwardWsRateMax: parsePositiveInt(process.env.RELAY_FORWARD_WS_RATE_MAX, 60),
		// Failed Home Assistant logins per client before it is blocked from
		// trying again (see proxy/loginGuard.js).  Counting failures rather
		// than requests is what makes this a brute-force check instead of a
		// rate limit; five leaves room for a mistyped password.  Home Assistant
		// cannot do this itself — over the relay every visitor reaches it as
		// 127.0.0.1, so this proxy holds the only real client address.
		loginFailMax: parsePositiveInt(process.env.RELAY_LOGIN_FAIL_MAX, 5),
		loginFailWindowMs: parsePositiveInt(process.env.RELAY_LOGIN_FAIL_WINDOW_MS, 900000),
		// Escalating block: 15m, 1h, 6h, then a day for anyone still going.
		loginBlockLadderMs: (process.env.RELAY_LOGIN_BLOCK_LADDER_MS
			? process.env.RELAY_LOGIN_BLOCK_LADDER_MS.split(',')
				.map((s) => parsePositiveInt(s.trim(), 0)).filter((n) => n > 0)
			: [900000, 3600000, 21600000, 86400000]),
		// How long a client's strike count survives, so that going quiet for a
		// while and returning does not reset it to the shortest block.
		loginStrikeTtlMs: parsePositiveInt(process.env.RELAY_LOGIN_STRIKE_TTL_MS, 604800000),
		// Peers whose `X-Real-IP` the proxy believes.  The proxy listens on
		// 0.0.0.0 for the portal host's nginx, so a client that reaches the port
		// directly could otherwise claim any address and mint itself a fresh
		// rate-limit bucket per request.  Empty = trust the header from anyone,
		// which is only safe while the firewall rule restricting this port to
		// the portal host holds; set it to nginx's address to stop depending on
		// that.  Untrusted peers are keyed on their real socket address.
		forwardTrustedProxies: process.env.RELAY_FORWARD_TRUSTED_PROXIES
			? process.env.RELAY_FORWARD_TRUSTED_PROXIES.split(',').map((s) => s.trim()).filter(Boolean)
			: []
	},
	analytics: {
		enabled: process.env.ENABLE_ANALYTICS === 'true',
		differentialPrivacyEpsilon: parseFloat(process.env.DIFFERENTIAL_PRIVACY_EPSILON) || 1.0
	},
	hcaptcha: {
		secret: process.env.HCAPTCHA_SECRET || '',
		siteKey: process.env.HCAPTCHA_SITEKEY || '',
		bypassToken: process.env.HCAPTCHA_BYPASS_TOKEN || ''
	},
	logging: {
		level: process.env.LOG_LEVEL || 'info',
		file: process.env.LOG_FILE || 'logs/vomesync.log'
	},
	limits: {
		freeTierEnabled: process.env.FREE_TIER_LIMITS_ENABLED !== 'false',
		freeTierMaxSwitches: parsePositiveInt(process.env.FREE_TIER_MAX_SWITCHES, 8),
		freeTierMaxPublicSwitches: parsePositiveInt(process.env.FREE_TIER_MAX_PUBLIC_SWITCHES, 4),
		premiumMaxSwitches: parsePositiveInt(process.env.PREMIUM_MAX_SWITCHES, 50),
		premiumMaxPublicSwitches: parsePositiveInt(process.env.PREMIUM_MAX_PUBLIC_SWITCHES, 25)
	}
};

// Validation
if (config.server.env === 'production' && config.security.jwtSecret === 'dev-secret-change-in-production') {
	throw new Error('JWT_SECRET must be set in production environment');
}

if (sslEnabled && (!config.ssl.certPath || !config.ssl.keyPath)) {
	throw new Error('ENABLE_SSL is true but SSL_CERT_PATH / SSL_KEY_PATH are not set');
}

module.exports = config;
