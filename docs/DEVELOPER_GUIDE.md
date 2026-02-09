# VomeSync — Comprehensive Developer Guide

> **Last updated:** 2026-02-08
> **Purpose:** Give any developer (or AI agent) enough context to work on VomeSync without lengthy exploration. Includes architecture, conventions, known issues, and a prioritised improvement backlog.

---

## 1. What Is VomeSync?

VomeSync is a platform for creating, sharing, and toggling "switches" — lightweight boolean entities that can be controlled from a web dashboard, a Home Assistant integration, or any client that speaks the REST/WebSocket API. Think of it as a collaborative IoT toggle board.

### Core components

| Component | Tech | Location | Lines (approx.) |
|---|---|---|---|
| **Webserver (API + WS)** | Node.js / Express | `webserver/` | ~4,800 |
| **Website (SPA dashboard)** | Vanilla JS + CSS | `website/` | ~5,500 |
| **Home Assistant integration** | Python (async) | `custom_components/vomesync/` | ~5,800 |
| **Docker deployment** | Docker Compose + nginx | `docker/` | — |
| **Tests** | Jest (JS) + pytest (Python) | `webserver/tests/`, `hacs-addon/tests/`, `tests/e2e/` | — |

### Data store

All state lives in **Redis** (with AOF persistence). There is no SQL database.

---

## 2. Repository Layout

```
VomeSync/
├── custom_components/vomesync/   # Home Assistant integration (Python)
│   ├── __init__.py               # Integration setup, entry load/unload (197 lines)
│   ├── config_flow.py            # Config + options UI flows (2200 lines) ⚠️ LARGE
│   ├── coordinator.py            # DataUpdateCoordinator + WS bridge (1390 lines)
│   ├── api_client.py             # HTTP client for the VomeSync API (523 lines)
│   ├── crypto.py                 # Ed25519 key gen, signing, request building (572 lines)
│   ├── switch.py                 # HA SwitchEntity platform (410 lines)
│   ├── sensor.py                 # HA SensorEntity platform (180 lines)
│   ├── websocket_client.py       # Async WS client per switch (254 lines)
│   ├── const.py                  # Constants, endpoints, config keys (101 lines)
│   ├── options_flow_links.py     # Mixin: entity-linking options flow
│   ├── naming.py                 # Deterministic switch naming helpers
│   ├── time_utils.py             # Timestamp formatting
│   ├── log_utils.py              # Logging helpers
│   ├── services.yaml             # Custom HA services (link_entities)
│   ├── translations/en.json      # All user-facing strings
│   └── manifest.json             # HACS manifest
│
├── webserver/
│   ├── src/
│   │   ├── server.js             # Express + WS bootstrap (303 lines)
│   │   ├── routes/
│   │   │   ├── api.js            # Thin assembler — mounts sub-routers (34 lines)
│   │   │   ├── route-helpers.js  # Shared middleware, helpers, canonical builders (401 lines)
│   │   │   ├── v2-routes.js      # All v2 crypto-signed endpoints (930 lines)
│   │   │   ├── admin-routes.js   # Admin + promo-code endpoints (347 lines)
│   │   │   ├── legacy-routes.js  # Legacy v1 endpoints (481 lines)
│   │   │   └── public-routes.js  # Public/unauthenticated endpoints (171 lines)
│   │   ├── utils/redis.js        # Redis wrapper + helpers + tier/promo management (1870 lines) ⚠️ LARGE
│   │   ├── utils/validation.js   # Joi schemas, sanitisers (332 lines)
│   │   ├── utils/auth.js         # Key hashing, access-key auth, crypto verification, dual-secret JWT (352 lines)
│   │   ├── utils/media.js        # Image upload → WebP conversion
│   │   ├── utils/logger.js       # Winston logger with redaction + explicit timestamps (70 lines)
│   │   ├── websocket/manager.js  # WS pub/sub per-UID rooms (310 lines)
│   │   └── config/config.js      # Env → config object (incl. tier limits, old secrets) (79 lines)
│   └── tests/                    # Jest unit + integration tests
│
├── website/
│   ├── index.html                # Single-page shell (529 lines)
│   ├── script.js                 # All website logic (3264 lines) ⚠️ LARGE
│   └── styles.css                # All CSS (1720 lines) ⚠️ LARGE
│
├── docker/
│   ├── docker-compose.yml
│   ├── env.example
│   ├── nginx/                    # Reverse proxy config
│   └── scripts/deploy.sh
│
├── hacs-addon/tests/             # pytest tests for HA integration
│   ├── conftest.py               # Fixtures (mock hass, coordinator, etc.)
│   └── test_*.py                 # Test modules
│
├── tests/e2e/                    # End-to-end (Docker required)
│
└── docs/                         # Documentation
    ├── ARCHITECTURE.md
    ├── ARCHITECTURE_SERVER.md
    ├── ARCHITECTURE_INTEGRATION.md
    ├── ARCHITECTURE_WEBSITE.md
    ├── API.md
    ├── SETUP.md
    ├── TESTING.md
    ├── OPERATIONS.md
    └── this file → DEVELOPER_GUIDE.md
```

---

## 3. Cryptographic Model (v2 Signed API)

This is the most important concept to understand. The v2 API uses **Ed25519** signatures for all authenticated operations. No bearer tokens are sent over the wire for owner operations.

### Key hierarchy

```
Master seed (32 bytes, base64url)
  └─ stored in HA config entry as CONF_CRYPTO_SEED
  └─ Ed25519 keypair derived from seed
       ├── Public key  → deterministic Switch UID = base64url(SHA-256(pubkey))
       └── Private key → used to sign every v2 API request
```

### How a v2 request is signed

1. Build a JSON body containing the action payload + `nonce` (UUID) + `timestamp` (ms).
2. **Canonical-serialise** the body (keys sorted, no whitespace) — see `crypto.py → _canonical_json()`.
3. Sign the canonical bytes with the Ed25519 private key.
4. Send `{ "payload": <body>, "signature": <base64url sig>, "publicKey": <base64url pubkey> }`.

The server verifies the signature, derives the UID from the public key, and checks the nonce hasn't been replayed.

### Access keys

Owners can **delegate** limited access by creating **access keys** — bearer tokens with permission scopes (`toggle`, `comment`, `metadata`) and an optional TTL. These are stored server-side (hashed) and can be:
- **Created** — `POST /api/v2/switch/:uid/access-keys`
- **Listed** — `POST /api/v2/switch/:uid/access-keys/list`
- **Revoked** — `POST /api/v2/switch/:uid/access-keys/revoke`
- **Paused/unpaused** — `POST /api/v2/switch/:uid/access-keys/pause`
- **Permissions updated** — `POST /api/v2/switch/:uid/access-keys/permissions`

Access-key operations are owner-signed (the owner proves identity via Ed25519, then specifies which key ID to act on).

### Key files

- `custom_components/vomesync/crypto.py` — all crypto primitives
- `webserver/src/utils/auth.js` — server-side verification & access-key middleware
- `webserver/src/routes/v2-routes.js` — all v2 endpoint handlers
- `webserver/src/routes/route-helpers.js` — canonical builders & shared helpers

---

## 4. Webserver API Endpoints

Endpoints are split across focused route modules in `webserver/src/routes/`. The thin `api.js` assembler mounts each sub-router; `server.js` mounts the assembler at `/api`.

### Legacy v1 (disabled by default — `LEGACY_API_ENABLED=false`)

| Method | Path | Auth | Purpose |
|---|---|---|---|
| POST | `/api/generate-key` | none | Generate a personal key (legacy) |
| POST | `/api/create-switch` | personal key header | Create switch (legacy) |
| POST | `/api/toggle/:uid` | personal key header | Toggle (legacy) |
| GET | `/api/status/:uid` | none | Get switch state |
| GET | `/api/my-switches` | personal key header | List owned switches |
| PATCH | `/api/switch/:uid` | personal key header | Update metadata |
| DELETE | `/api/switch/:uid` | personal key header | Delete switch |

### v2 Signed API (preferred)

| Method | Path | Auth | Purpose |
|---|---|---|---|
| POST | `/api/v2/switch` | Ed25519 signed | Create switch |
| POST | `/api/v2/my-switches` | Ed25519 signed | List owned switches |
| POST | `/api/v2/switch/:uid/state` | Ed25519 signed | Set explicit state |
| POST | `/api/v2/switch/:uid` | Ed25519 signed | Update metadata |
| POST | `/api/v2/switch/:uid/toggle` | Ed25519 signed / access key | Toggle |
| POST | `/api/v2/switch/:uid/metadata` | Ed25519 signed / access key | Update metadata |
| POST | `/api/v2/switch/:uid/comment` | Ed25519 signed / access key | Set comment |
| POST | `/api/v2/switch/:uid/access-keys` | Ed25519 signed | Create access key |
| POST | `/api/v2/switch/:uid/access-keys/list` | Ed25519 signed | List access keys |
| POST | `/api/v2/switch/:uid/access-keys/revoke` | Ed25519 signed | Revoke access key |
| POST | `/api/v2/switch/:uid/access-keys/pause` | Ed25519 signed | Pause/unpause key |
| POST | `/api/v2/switch/:uid/access-keys/permissions` | Ed25519 signed | Update key permissions |

### Public / unauthenticated

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/public-switches` | List public switches (paginated, filterable) |
| GET | `/api/switch/:uid` | Get public switch details |
| GET | `/api/health` | Health check (incl. Redis status) |
| GET | `/api/categories` | List available categories |

### v2 Owner endpoints

| Method | Path | Auth | Purpose |
|---|---|---|---|
| POST | `/api/v2/owner/redeem-promo` | Ed25519 signed | Redeem a promo code for premium tier |
| POST | `/api/v2/owner/tier` | Ed25519 signed | Check current owner tier & expiry |

### Admin

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/admin/challenge` | Get HMAC challenge for admin auth |
| POST | `/api/admin/switch/:uid/delist` | Delist from public directory |
| POST | `/api/admin/switch/:uid/delete` | Admin delete |
| POST | `/api/admin/blocks` | Block owner/key |
| POST | `/api/admin/redirects` | Create UID redirect |
| DELETE | `/api/admin/redirects/:uid` | Remove redirect |
| POST | `/api/admin/switch/:uid/override` | Override listing fields |
| DELETE | `/api/admin/switch/:uid/override` | Remove override |
| POST | `/api/admin/promo-codes` | Create a promo code |
| GET | `/api/admin/promo-codes` | List all promo codes |
| DELETE | `/api/admin/promo-codes/:code` | Delete a promo code |
| GET | `/api/admin/owner/:ownerId/tier` | Get owner tier details |
| POST | `/api/admin/owner/:ownerId/tier` | Set owner tier manually |

### WebSocket

- **Endpoint:** `ws(s)://host/ws?uid=<switch-uid>`
- Server broadcasts JSON messages when a switch changes state, metadata, or comment.
- Message types: `state_update`, `metadata_update`, `comment_update`, `connection_established`
- Implementation: `webserver/src/websocket/manager.js`

---

## 5. Home Assistant Integration — How It Works

### Lifecycle

1. **Config flow** (`config_flow.py`):
   - User adds the integration → `async_step_user` collects server URL, WebSocket URL, signing key (or generates one).
   - Optionally accepts a switch UID (or `uid/key` composite) to subscribe immediately.
   - Creates a config entry with `CONF_CRYPTO_SEED`, `CONF_SERVER_URL`, `CONF_WEBSOCKET_URL`.

2. **Setup** (`__init__.py → async_setup_entry`):
   - Creates `VomeSyncCoordinator` (the central data hub).
   - If an initial switch UID was provided, subscribes to it (optionally with an access key).
   - Forwards to `switch` and `sensor` platforms.

3. **Coordinator** (`coordinator.py`):
   - Inherits `DataUpdateCoordinator`.
   - Periodically polls `GET /api/v2/my-switches` for owned switches.
   - Manages per-switch WebSocket connections for real-time updates.
   - Exposes methods: `toggle_switch`, `create_switch`, `subscribe_to_switch`, `update_switch`, `delete_switch`, `create_v2_access_key`, `revoke_v2_access_key`, `pause_v2_access_key`, `update_v2_access_key_permissions`, etc.
   - Rate-limits entity triggers (2s cooldown) and API toggles (1s cooldown) to prevent infinite loops.

4. **Switch entity** (`switch.py`):
   - One `VomeSyncSwitch` per subscribed/owned switch.
   - Exposes `toggle()`, attributes (uid, description, location, category, linked entities).
   - Entity linking: when toggled, can trigger other HA entities.

5. **Sensor entity** (`sensor.py`):
   - Exposes metadata as sensors (description, location, comment, etc.).

6. **Options flow** (`config_flow.py → VomeSyncOptionsFlowHandler`):
   - Rich menu: Create Switch, Subscribe, Manage Switches, Access Keys, Link Entities, Edit Connection, View Signing Key.
   - **Access key management**: dropdown of existing keys → detail view → pause/unpause, update permissions, revoke.
   - Supports `uid/key` composite format for subscribing (parses `_parse_uid_key_composite`).

### Config flow step map (simplified)

```
async_step_user (initial setup)
  → _create_entry

Options flow:
  async_step_init → menu
    ├── async_step_create_switch
    │     ├── async_step_create_switch_confirm (signing key backup)
    │     └── async_step_create_switch_advanced (optional fields)
    ├── async_step_subscribe_switch (accepts uid or uid/key)
    ├── async_step_manage_switches → per-switch actions
    │     ├── async_step_edit_switch
    │     ├── async_step_delete_switch
    │     ├── async_step_manage_website_link
    │     └── async_step_view_switch_details
    ├── async_step_access_keys (dropdown of keys)
    │     └── async_step_access_key_detail
    │           ├── async_step_access_key_pause
    │           ├── async_step_access_key_permissions
    │           └── async_step_revoke_access_key_v2
    ├── async_step_create_access_key_v2
    ├── async_step_link_entities (mixin)
    ├── async_step_edit_connection
    └── async_step_view_signing_key
```

---

## 6. Website Dashboard

The website is a **single-page application** built with vanilla JS, CSS variables, and no build step. It communicates with the API via `fetch()` and opens WebSocket connections for live updates.

### Key features

- **Switch directory**: paginated grid of public switches with search, category filter, and sort.
- **Quick-view panel**: click a card to see details, toggle, comment.
- **"Manage on website" links**: the HA integration generates short-lived session/access keys that allow web-based management; the website redeems these tokens.
- **Dark theme only** (currently): uses CSS custom properties in `:root`.

### File structure

- `index.html` — skeleton with `<div id="app">`, modal containers.
- `script.js` — 3,264 lines, single file (see improvement notes below).
- `styles.css` — 1,720 lines, single file.

---

## 7. Redis Data Model

All data is stored in Redis. Key patterns (from `webserver/src/utils/redis.js`):

| Key pattern | Type | Purpose |
|---|---|---|
| `switch:<uid>` | Hash | Switch state + metadata |
| `owner:<hashed-key>:switches` | Set | UIDs owned by a personal key |
| `access_key:<uid>:<hashed-key-id>` | Hash | Delegated access key metadata |
| `access_keys:<uid>` | Set | Set of key IDs for a switch |
| `nonce:<nonce>` | String (TTL) | Replay protection for v2 signed requests |
| `session_token:<token>` | Hash (TTL) | Web session tokens |
| `public_switches` | Sorted Set | Public switch index (scored by creation time) |
| `admin:blocks` | Hash | Blocked owners/keys |
| `admin:redirects` | Hash | UID redirects |
| `admin:overrides:<uid>` | Hash | Admin listing overrides |
| `admin_challenge:<challenge>` | String (TTL 30s) | HMAC admin auth challenge (P2) |
| `owner_tier:<ownerId>` | Hash (TTL) | Owner premium tier record (`tier`, `expiresAt`, `promoCode`, `redeemedAt`) |
| `promo:<code>` | Hash | Promo code record (`code`, `tier`, `durationDays`, `maxRedemptions`, `redemptions`, `createdBy`, `createdAt`) |

---

## 8. Testing

### Running tests

```bash
# HA integration tests (pytest)
cd /var/www/VomeSync
pytest hacs-addon/tests/ -v

# Webserver tests (Jest)
cd /var/www/VomeSync/webserver
npm test

# E2E tests (requires Docker)
pytest tests/e2e/ -v

# All via script
./scripts/run-tests.sh
```

### Test locations

| Suite | Framework | Location | Count |
|---|---|---|---|
| HA config flow | pytest | `hacs-addon/tests/test_config_flow.py` | ~35 tests |
| HA coordinator | pytest | `hacs-addon/tests/test_coordinator.py` | |
| HA switch/sensor | pytest | `hacs-addon/tests/test_switch.py`, `test_sensor.py` | |
| Webserver unit | Jest | `webserver/tests/unit/` | |
| Webserver integration | Jest | `webserver/tests/integration/` | |
| End-to-end | pytest | `tests/e2e/` | |

### Key test fixtures

- `conftest.py` provides `hass`, `coordinator`, `config_entry`, `api_client` mocks.
- Tests mock the API client and coordinator, not real HTTP.
- The config flow tests exercise the full step-by-step UI flow.

---

## 9. Configuration & Environment

### Key environment variables (webserver)

| Variable | Default | Purpose |
|---|---|---|
| `PORT` | 3000 | API HTTP port |
| `WS_PORT` | 3001 | WebSocket port (can equal PORT) |
| `REDIS_HOST` | localhost | Redis connection |
| `REDIS_PORT` | 6379 | Redis port |
| `REDIS_PASSWORD` | — | Redis auth |
| `JWT_SECRET` | — | **Required.** Signs JWTs and hashes keys |
| `JWT_SECRET_OLD` | — | Previous JWT secret (accepted during rotation; see P5) |
| `KEY_HASH_SECRET` | JWT_SECRET | Dedicated key-hashing secret |
| `KEY_HASH_SECRET_OLD` | — | Previous hash secret (keys derived with old secret are still recognised during rotation) |
| `CORS_ORIGINS` | — | Comma-separated allowed origins |
| `LEGACY_API_ENABLED` | false | Enable v1 endpoints |
| `SESSION_TOKENS_ENABLED` | false | Enable web session tokens |
| `ADMIN_API_KEY` | — | Admin endpoint auth |
| `FREE_TIER_LIMITS_ENABLED` | true | Enforce switch count limits |
| `FREE_TIER_MAX_SWITCHES` | 8 | Max switches per owner (free tier) |
| `FREE_TIER_MAX_PUBLIC_SWITCHES` | 4 | Max public switches per owner (free tier) |
| `PREMIUM_MAX_SWITCHES` | 50 | Max switches per owner (premium tier) |
| `PREMIUM_MAX_PUBLIC_SWITCHES` | 25 | Max public switches per owner (premium tier) |
| `HCAPTCHA_SECRET` | — | hCaptcha verification |
| `ENABLE_SSL` | false | In-app TLS (usually handled by proxy) |

### HA config entry data

Stored in `entry.data`:
- `crypto_seed` — base64url Ed25519 master seed
- `server_url` — API base URL
- `websocket_url` — WebSocket base URL
- `auth_mode` — always `"crypto"` for v2

Stored in `entry.options`:
- `switches` — list of owned switch dicts `{uid, name, …}`
- `subscriptions` — list of subscribed switch dicts `{uid, name, access_key?, …}`
- `linked_entities` — dict of `uid → [entity_id, …]`

---

## 10. Conventions & Style

| Area | Convention |
|---|---|
| **Indentation** | Tabs everywhere (JS, CSS, Python, JSON) |
| **Modules** | ES Modules preferred (webserver uses CommonJS currently) |
| **Exports** | Never `export let` or export mutable objects; pass through function params |
| **English** | British English in user-facing text (colour, behaviour, etc.) |
| **Imports** | Static imports only (no dynamic `import()`) |
| **Error handling** | Log + raise/return meaningful errors; no silent swallows |
| **Constants** | Named constants, not magic numbers |
| **File size target** | < 1,500 lines per file; refactor when exceeded |

---

## 11. Known Issues & Improvement Backlog

This is the most important section for picking up work. Items are grouped by category and roughly prioritised within each group.

### 🔴 Critical / Security

| # | Issue | Details | Files |
|---|---|---|---|
| S1 | **Nonce replay window is unbounded in HA client** | The HA `crypto.py` generates a UUID nonce per request, and the server stores it with a TTL. However, if the server TTL is too short or too long, there's a window for replay. **Verify** the server nonce TTL is sensible (e.g. 5–10 minutes) and the HA client checks for clock skew. | `crypto.py`, `api.js` (nonce storage) |
| S2 | **Access keys are bearer tokens** | Anyone with the key string can act as that key. Consider adding IP-binding or HMAC challenge for sensitive operations. | `api.js`, `redis.js` |
| S3 | **~~No rate limiting on v2 signed endpoints~~** | ✅ **Verified 2026-02-08.** All v2 endpoints have per-action rate limiting (e.g. `v2_create` 50/15min, `v2_my_switches` 200/15min, `v2_toggle` 1000/15min, `v2_access_keys_*` 200/15min). Bearer endpoints now also use per-key composite rate limiting (§14.6). | `v2-routes.js`, `auth.js` |
| S4 | **~~Admin API key is a single static string~~** | ✅ **Mitigated.** HMAC challenge-response added as alternative auth (§14.7). Static `X-Admin-Key` still works for backward compatibility. | `route-helpers.js`, `admin-routes.js` |

### 🟠 Technical Debt

| # | Issue | Details | Files |
|---|---|---|---|
| T1 | **`config_flow.py` is 2,200 lines** | This is the largest Python file and hard to navigate. Split into multiple files: `config_flow_setup.py`, `config_flow_switches.py`, `config_flow_access_keys.py`, etc. The `options_flow_links.py` mixin is a good pattern to follow. | `config_flow.py` |
| T2 | **`website/script.js` is 3,264 lines** | Single monolithic JS file. Should be split into modules (switch management, UI rendering, API client, WebSocket handler, etc.) with a simple bundler or ES module imports. | `script.js` |
| T3 | **`website/styles.css` is 1,720 lines** | Could benefit from splitting into partials (layout, components, cards, modals, etc.). | `styles.css` |
| T4 | **~~`webserver/src/routes/api.js` is 1,928 lines~~** | ✅ **Completed 2026-02-08.** Split into `route-helpers.js` (401), `v2-routes.js` (930), `admin-routes.js` (347), `legacy-routes.js` (481), `public-routes.js` (171), and a thin `api.js` assembler (34). All 148 tests pass. Line counts increased from initial split due to security and premium feature additions. | `routes/` |
| T5 | **`webserver/src/utils/redis.js` is 1,870 lines** | Mixes data access, key hashing, migration logic, tier/promo management, and helpers. Growing with premium features. Split into `redis-client.js`, `switch-store.js`, `access-key-store.js`, `tier-store.js`, `migration.js`. | `redis.js` |
| T6 | **Webserver uses CommonJS** | `require()`/`module.exports` throughout. Migrate to ES Modules (`import`/`export`). Update `package.json` with `"type": "module"`. | All `webserver/src/` files |
| T7 | **No TypeScript** | The webserver has no type safety. Consider incremental TypeScript adoption, starting with the API types and Redis schemas. | `webserver/` |
| T8 | **Coordinator is approaching 1,400 lines** | Extract WebSocket management into a separate class/file and API method delegation into a thin wrapper. | `coordinator.py` |
| T9 | **`options_flow_links.py` mixin approach** | Good pattern — apply it to access key management and switch management to reduce `config_flow.py` size. | `config_flow.py` |
| T10 | **Website has no build step / bundler** | No minification, no tree-shaking, no source maps. Consider adding a lightweight bundler (esbuild, Vite) especially as the JS file grows. | `website/` |

### 🟡 User Experience / UI Flow

| # | Issue | Details | Files |
|---|---|---|---|
| U1 | **HA options flow is deep** | Users navigate 3–4 levels deep to manage access keys (Options → Access Keys → select key → action). Consider a flatter layout or at least breadcrumb-style descriptions. | `config_flow.py`, `translations/en.json` |
| U2 | **No "back" navigation in some flow steps** | Some steps (e.g. access key detail) redirect to the main menu on cancel rather than going back to the access keys list. Audit all steps for consistent back navigation. | `config_flow.py` |
| U3 | **Signing key backup UX** | The signing key backup confirmation is shown only on first switch creation. If the user dismisses it, there's no easy way to re-trigger the backup prompt. The "View Signing Key" option exists but isn't prominent. | `config_flow.py` |
| U4 | **Website quick-view lacks loading states** | When toggling or loading switch details, there's no spinner or skeleton UI — the user sees nothing until the response arrives. | `script.js` |
| U5 | **Website has no light theme** | Currently dark-only. Some users prefer light mode or system-preference matching. The CSS variable architecture supports this — just needs a second set of variables and a toggle. | `styles.css` |
| U6 | **No internationalisation on the website** | All strings are hardcoded in English in `script.js`. The HA integration has `translations/` but the website doesn't. | `script.js` |
| U7 | **Category selection is a free-text field on website** | The HA integration uses a dropdown; the website should match for consistency. | `script.js` |
| U8 | **"Manage on website" link TTL is confusing** | The HA integration generates a session key with a 4-hour default TTL (or 30 days with "stay signed in"). The user isn't clearly told when the link expires or how to regenerate it. | `config_flow.py`, `translations/en.json` |

### 🟢 Visual / CSS

| # | Issue | Details | Files |
|---|---|---|---|
| V1 | **Global link colours were missing** | ✅ **Fixed 2026-02-07.** Added global `a` tag styling using `--primary` / `--accent` variables. Links inside switch cards and quickview now inherit correct colours. Button-styled links (`a.btn`) excluded from underline-on-hover. | `styles.css` |
| V2 | **No focus/accessibility indicators** | Buttons and interactive elements lack visible `:focus` outlines for keyboard navigation. Add `outline` or `box-shadow` focus styles. | `styles.css` |
| V3 | **Mobile responsiveness needs audit** | The website has some responsive rules but no systematic mobile-first approach. Test on small screens (320px–480px) and fix layout overflow issues. | `styles.css`, `script.js` |
| V4 | **Card hover states are inconsistent** | Some cards have hover effects, others don't. Standardise hover/active states across all interactive cards. | `styles.css` |
| V5 | **No animation on state changes** | When a switch toggles, the card updates instantly. A brief transition (colour flash, checkbox animation) would improve feedback. | `styles.css`, `script.js` |

### 🔵 Features / Enhancements

| # | Issue | Details | Files |
|---|---|---|---|
| F1 | **Configurable rate limits via HA options** | Currently hardcoded in `coordinator.py` (2s trigger cooldown, 1s toggle cooldown). Should be configurable in the options flow. | `coordinator.py`, `config_flow.py` |
| F2 | **Per-entity rate limiting** | Current rate limiting is per-switch, not per-entity. If one switch has 5 linked entities, they all share the same cooldown. | `coordinator.py` |
| F3 | **WebSocket reconnection indicator in HA** | Users have no visibility into whether the WS connection is healthy. Consider a diagnostic sensor or attribute. | `websocket_client.py`, `sensor.py` |
| F4 | **Batch operations for access keys** | Currently, keys are managed one at a time. Batch revoke/pause would be useful for users with many keys. | `config_flow.py`, `api_client.py` |
| F5 | **Search/filter in HA switch management** | If a user has many switches (10+), the management dropdown becomes unwieldy. Add search or pagination. | `config_flow.py` |
| F6 | **Webhook/callback support** | Allow switches to trigger webhooks on state change (complementing WebSocket for server-to-server integrations). | `webserver/` |
| F7 | **Multi-user / team support** | Currently, a signing key is per-HA-instance. No concept of teams or shared ownership beyond access keys. | Architecture-level |

---

## 12. How to Make Changes — Quick Reference

### Adding a new v2 API endpoint (server)

1. **Add the canonical builder** (if signed) in `webserver/src/routes/route-helpers.js` and export it.
2. **Add the route handler** in `webserver/src/routes/v2-routes.js` (or `admin-routes.js`, `public-routes.js` as appropriate).
3. **Add validation schema** in `webserver/src/utils/validation.js` if needed.
4. **Add Redis operations** in `webserver/src/utils/redis.js` if new data is stored.
5. **Add Jest tests** in `webserver/tests/`.

### Adding a new v2 API call (HA integration)

1. **Add the endpoint constant** in `const.py`.
2. **Add the request builder** in `crypto.py` (e.g. `build_v2_<action>_request()`).
3. **Add the API client method** in `api_client.py`.
4. **Add the coordinator method** in `coordinator.py` (wraps the API client call).
5. **Add the config flow step** in `config_flow.py` if user-facing.
6. **Add translations** in `translations/en.json`.
7. **Add pytest tests** in `hacs-addon/tests/`.

### Adding a new options flow step

1. Add `async_step_<name>` method in `config_flow.py` (or a mixin file).
2. Add translations under `options.step.<name>` in `translations/en.json`.
3. Wire it from an existing step (menu or form submission).
4. Add a test in `hacs-addon/tests/test_config_flow.py`.

### Modifying the website

1. Edit `website/script.js` for logic, `website/styles.css` for styling.
2. The website is served as static files — no build step required.
3. Test locally with `python3 -m http.server 8080` from the `website/` directory.
4. Ensure the API URL is correct (defaults to `https://sync.vome.io`; configurable in the JS).

---

## 13. Deployment

### Deploy script (recommended)

The `docker/scripts/deploy.sh` script automates the full lifecycle of VomeSync Docker services. It handles prerequisite checks, environment setup, volume management, port resolution, building, and health-checking.

```bash
cd docker/scripts
./deploy.sh <command>
```

| Command | What it does |
|---|---|
| `deploy` | **Initial deployment** (default). Checks prerequisites, creates `.env` from template with auto-generated secrets, pulls images, builds, starts all services, waits for health. |
| `update` | Updates **all** services (dev + live). Pulls latest git changes (fast-forward only), rebuilds without cache, force-recreates containers. |
| `update-dev` | Rebuilds and restarts **dev** services only (`vomesync-webserver-dev`, `vomesync-website-dev`, `vomesync-redis-dev`). |
| `update-live` / `push-live` | Rebuilds and restarts **live** services only (`vomesync-webserver`, `vomesync-website`, `vomesync-redis`, `vomesync-proxy`). Pulls git changes first. |
| `status` | Shows running containers and all service URLs (live + dev). |
| `logs [service]` | Tails logs for all or a specific service. |
| `stop` | Stops all services (`docker compose down`). |
| `restart` | Restarts all services in-place. |
| `backup` | Creates a timestamped backup in `backups/` containing the Redis RDB dump, logs, and `.env`. |
| `clean` | **Destructive.** Removes all containers, images, volumes, and data. Prompts for confirmation. |

#### Smart volume management

The deploy script automatically resolves Docker volume names. If you rename the project directory or the Compose project name changes, the script detects existing volumes by suffix (e.g. `_redis_data`) and adopts them to prevent data loss. You can override this by setting `VOMESYNC_REDIS_VOLUME_NAME` or `VOMESYNC_LOGS_VOLUME_NAME` in `.env`.

#### Smart port management

Default ports are: API `3090`, WebSocket `3001`, Website `8111`, Proxy HTTP `8080`, HTTPS `8443`. Dev services use `3091`, `3002`, `8112`, `6381`. If a default port is already in use, the script automatically picks the next free port and warns you. Existing container port mappings are preserved across upgrades.

#### First-time setup

```bash
cd docker/scripts
./deploy.sh deploy
# → Creates .env with auto-generated JWT_SECRET and REDIS_PASSWORD
# → Review docker/.env and update CORS_ORIGINS, ADMIN_API_KEY, etc.
```

#### Updating after code changes

```bash
cd docker/scripts
./deploy.sh update-live    # production only
./deploy.sh update-dev     # dev only
./deploy.sh update         # both
```

### Docker stack

The `docker-compose.yml` stack includes:
- **vomesync-webserver**: Node.js API + WebSocket (production)
- **vomesync-redis**: Redis 7 with AOF persistence
- **vomesync-website**: Static file server (nginx)
- **vomesync-proxy**: nginx reverse proxy (HTTP/HTTPS/WSS)
- **vomesync-webserver-dev**: Development API server (hot reload)
- **vomesync-website-dev**: Development website server
- **vomesync-redis-dev**: Development Redis instance

### Health check

```bash
curl -f http://localhost:3090/api/health
# Expected: {"success":true,"redis":true}
```

### Backup

```bash
cd docker/scripts
./deploy.sh backup
# Creates: backups/YYYYMMDD_HHMMSS/{redis_dump.rdb, logs/, .env}
```

See also `docs/OPERATIONS.md` for detailed backup/restore procedures.

---

## 14. Recent Changes (2026-02-08)

This section documents changes made during the recent refactoring and improvement pass.

### 14.1 API route split (T4 — completed)

The monolithic `routes/api.js` (1,928 lines) was split into focused modules:

| File | Lines | Contents |
|---|---|---|
| `routes/api.js` | 34 | Thin assembler — mounts sub-routers, global error handler |
| `routes/route-helpers.js` | 401 | Shared middleware (`validateUID`, `requireAdmin` with HMAC), canonical builders, helpers (`assertFreshTimestamp`, `clockSkewError`, `checkFreeTierLimits`), HMAC constants |
| `routes/v2-routes.js` | 930 | All v2 crypto-signed endpoints (create, toggle, state, metadata, comment, my-switches, access keys, promo redemption, tier check) |
| `routes/admin-routes.js` | 347 | Admin endpoints (delist, delete, block, redirect, override, HMAC challenge, promo codes, tier management) |
| `routes/legacy-routes.js` | 481 | Legacy v1 personal-key endpoints (behind `LEGACY_API_ENABLED` flag) |
| `routes/public-routes.js` | 171 | Public/unauthenticated endpoints (health, public-switches, categories, switch details) |

**Import path is unchanged** — `server.js` still imports `./routes/api` which now re-exports the composed router. All 148 tests pass.

### 14.2 Access key management — always show both key formats

The `include_uid` checkbox was removed from the "Create access key" HA options flow. Now when creating an access key, **both formats are always displayed**:

- **`api_key`** — the raw access key
- **`api_key_with_uid`** — the composite `uid/key` format

This avoids user confusion and ensures the composite format is always available for sharing.

**Files changed:** `config_flow.py`, `translations/en.json`

### 14.3 Removed "v2" from user-facing text

All user-facing error messages and display text that said "v2" have been updated to use neutral language. V2 is the only actively developed authentication mode, so calling it "v2" is unnecessary and confusing:

| Before | After | Files |
|---|---|---|
| `'Switch is not v2 (crypto) enabled'` | `'Switch is not crypto-authenticated'` | `v2-routes.js` (7 occurrences), `auth.js` (1 occurrence) |
| `"This action is only available for v2 (crypto) switches."` | `"This action is only available for crypto-authenticated switches."` | `translations/en.json` |

### 14.4 Access key pause & permissions endpoints preserved

The `POST /api/v2/switch/:uid/access-keys/pause` and `POST /api/v2/switch/:uid/access-keys/permissions` endpoints were confirmed as recently added features and preserved during the route split. Their canonical builders (`v2CanonicalPauseAccessKey`, `v2CanonicalUpdateAccessKeyPermissions`) are in `route-helpers.js`, validation schemas are in `validation.js`.

### 14.5 W4 — Explicit timestamps in logs + clock skew error responses

- **Console log timestamps**: The Winston console transport now includes an explicit `YYYY-MM-DD HH:mm:ss` timestamp in every log line.
- **Clock skew error responses**: When a v2 request fails the `assertFreshTimestamp` check, the JSON error response now includes `serverTimeMs` (the server's current UTC time in milliseconds) and `maxSkewMs` (the configured tolerance). This allows clients to self-correct.
- **Configurable skew**: The tolerance is now sourced from `config.security.v2MaxClockSkewMs` (default 300 000 ms = 5 minutes), but the code retains a built-in 10-minute fallback if unconfigured.

**Files changed:** `logger.js`, `route-helpers.js` (`assertFreshTimestamp`, new `clockSkewError` helper), `v2-routes.js` (all timestamp checks updated to use `clockSkewError`), `config.js`.

### 14.6 W1 — Enhanced per-key rate limiting for access-key bearer endpoints

Access-key bearer endpoints (`/v2/switch/:uid/toggle`, `/v2/switch/:uid/metadata`, `/v2/switch/:uid/comment`) already had IP-based rate limiting. They now use a **composite key** of `IP:accessKeyId`, so a stolen or abused key is rate-limited independently from the legitimate owner's IP.

The `authManager.rateLimit()` middleware accepts an optional `getIdentifier` function as a fourth argument. When provided, it returns a per-request identifier (e.g. `req.apiKeyId`) that is combined with the IP to form the rate-limit key.

**Files changed:** `auth.js` (modified `rateLimit` to accept `getIdentifier`), `v2-routes.js` (access-key bearer endpoints pass `req => req.apiKeyId`).

### 14.7 P2 — HMAC challenge-response for admin API

The admin API now supports **HMAC challenge-response** authentication as an alternative to the static `X-Admin-Key` header:

1. **GET `/api/admin/challenge`** — returns a 32-byte hex challenge string, stored in Redis with a 30-second TTL (`admin_challenge:<challenge>`).
2. The client computes `HMAC-SHA256(challenge, adminApiKey)` and sends it in the `X-Admin-Signature` header along with the challenge in `X-Admin-Challenge`.
3. The `requireAdmin` middleware verifies the signature against the stored challenge and deletes the challenge after use (one-time use).
4. The existing `X-Admin-Key` header authentication continues to work alongside HMAC for backward compatibility.

**Files changed:** `route-helpers.js` (new HMAC constants + `requireAdmin` logic), `admin-routes.js` (new `/admin/challenge` endpoint).

### 14.8 P5 — Dual-secret rotation for JWT_SECRET and KEY_HASH_SECRET

Secret rotation is now supported without invalidating existing keys:

- **`JWT_SECRET_OLD`** — if set, the `verifyJWT` method in `auth.js` first tries the current `JWT_SECRET`; on failure it retries with the old secret and logs a warning ("JWT verified with old secret; consider reissuing token.").
- **`KEY_HASH_SECRET_OLD`** — if set, the `_deriveSecretIdWithOldSecret` helper in `redis.js` first derives the key ID using the current `KEY_HASH_SECRET`; if no record is found, it retries with the old secret. This covers `validatePersonalKey`, `resolveV2AccessKey`, and `redeemSessionToken`.

**Rotation procedure:**
1. Set `JWT_SECRET_OLD` / `KEY_HASH_SECRET_OLD` to the current secrets.
2. Generate new secrets and set them as `JWT_SECRET` / `KEY_HASH_SECRET`.
3. Restart the server. Existing tokens and keys continue to work via the fallback.
4. After a transition period (e.g. 30 days), remove the `_OLD` variables. Any keys not re-derived during that period will stop working.

**Files changed:** `config.js` (new `_OLD` env vars), `auth.js` (fallback JWT verification), `redis.js` (`_deriveSecretIdWithOldSecret` helper, updated `_getPersonalKeyId`, `_getApiKeyId`, `_getSessionTokenId`).

### 14.9 W5 — Premium tier system with promo codes

A tiered system allowing owners to be upgraded from the default `free` tier to `premium` (or other tiers) via redeemable promo codes.

#### Configuration (`config.js`)

```
limits.premiumMaxSwitches         → PREMIUM_MAX_SWITCHES (default 50)
limits.premiumMaxPublicSwitches   → PREMIUM_MAX_PUBLIC_SWITCHES (default 25)
```

#### Redis data model

| Key | Type | Fields |
|---|---|---|
| `owner_tier:<ownerId>` | Hash (auto-TTL) | `tier`, `expiresAt`, `promoCode`, `redeemedAt` |
| `promo:<code>` | Hash | `code`, `tier`, `durationDays`, `maxRedemptions`, `redemptions`, `createdBy`, `createdAt`, `fullyRedeemed` |

#### Redis methods (in `redis.js`)

- `getOwnerTier(ownerId)` → returns `{ tier, expiresAt, promoCode }` or `{ tier: 'free' }` if absent/expired.
- `setOwnerTier(ownerId, tier, expiresAt, promoCode)` → sets the tier hash with optional Redis key TTL.
- `createPromoCode({ code, tier, durationDays, maxRedemptions, createdBy })` → creates a promo code hash; returns `null` if code already exists.
- `getPromoCode(code)` → returns the promo hash or `null`.
- `redeemPromoCode(code, ownerId)` → validates, increments redemption counter, sets owner tier, returns `{ success, tier, expiresAt, durationDays }`.
- `listPromoCodes()` → scans all `promo:*` keys (admin use).
- `deletePromoCode(code)` → deletes a promo code.

#### Tier-aware limit checking (`route-helpers.js`)

The `checkFreeTierLimits` function now:
1. Resolves the owner's tier via `redisClient.getOwnerTier(ownerId)`.
2. Selects limits from `config.limits` based on the tier (`freeTierMax*` or `premiumMax*`).
3. Returns the tier in the limit-exceeded response so the error message displays the correct tier name.

`sendTierLimitError` replaces `sendFreeTierLimitError` (alias preserved for backward compatibility).

#### Admin endpoints (`admin-routes.js`)

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/admin/promo-codes` | Create promo code (`{ code, tier, durationDays, maxRedemptions }`) |
| GET | `/api/admin/promo-codes` | List all promo codes |
| DELETE | `/api/admin/promo-codes/:code` | Delete promo code |
| GET | `/api/admin/owner/:ownerId/tier` | Get owner tier details |
| POST | `/api/admin/owner/:ownerId/tier` | Manually set owner tier |

#### User-facing endpoints (`v2-routes.js`)

| Method | Path | Auth | Purpose |
|---|---|---|---|
| POST | `/api/v2/owner/redeem-promo` | Ed25519 signed | Redeem promo code → upgrade to premium |
| POST | `/api/v2/owner/tier` | Ed25519 signed | Check current tier & expiry |

**Files changed:** `config.js`, `redis.js`, `route-helpers.js`, `v2-routes.js`, `admin-routes.js`, `legacy-routes.js`, `validation.js`.

---

## 15. Pre-Release Checklist

> Items that should be addressed before promoting VomeSync as a public release. Ordered by severity.

### 🔴 Security — Must Fix

| # | Issue | Risk | Recommendation |
|---|---|---|---|
| **P1** | **Admin API key is a single static string** (S4) | Anyone who obtains `ADMIN_API_KEY` has permanent admin access. No rotation, no audit trail. | ⚠️ Logging added for admin operations. HMAC challenge-response added (see §14.7). Full rotation still recommended for production. |
| **P2** | ~~**Access keys are plain bearer tokens**~~ (S2) | ✅ **Mitigated.** Per-key rate limiting added (§14.6). HMAC challenge-response available for admin API (§14.7). Access keys are redacted in logs (verified). | Further: consider optional IP-binding or key expiry by default. |
| **P3** | **No HTTPS enforcement at app level** | The webserver defaults to `ENABLE_SSL=false` and relies on the nginx proxy for TLS. If someone runs the stack without the proxy, all traffic (including signatures and access keys) is plaintext. | Add a startup warning when `ENABLE_SSL=false` and `NODE_ENV=production`. Consider an `REQUIRE_PROXY` env var that refuses to start without a reverse proxy health check. |
| **P4** | **Redis has no TLS or ACL** | Redis listens on the Docker network without TLS. If the network is compromised, all data (including hashed keys) is exposed. | For public deployments, enable Redis TLS and ACLs. Document this in `SETUP.md`. |
| **P5** | ~~**JWT_SECRET and KEY_HASH_SECRET rotation**~~ | ✅ **Implemented.** Dual-secret rotation is now supported via `JWT_SECRET_OLD` and `KEY_HASH_SECRET_OLD` (see §14.8). | Rotation procedure documented in §14.8. |

### 🟠 Breaking Changes — Should Address

| # | Issue | Impact | Recommendation |
|---|---|---|---|
| **B1** | **Legacy v1 API deprecation** | V1 endpoints are behind `LEGACY_API_ENABLED=false` by default. Any public user still using personal-key auth will break. | Publish a deprecation timeline. Add a `Sunset` HTTP header to v1 responses. Consider a v1→v2 migration guide or helper tool. |
| **B2** | **`include_uid` option removed** | The HA create-access-key flow no longer offers a checkbox; it always shows both key formats. Existing users who had workflows depending on the old single-field output may be confused. | This is a UX improvement and unlikely to break automation. No action needed beyond release notes. |
| **B3** | **Error message wording changed** | API error messages changed from `'Switch is not v2 (crypto) enabled'` to `'Switch is not crypto-authenticated'`. Any client parsing error strings (instead of status codes) may break. | Clients should rely on HTTP status codes, not error message text. Document this in release notes. |

### 🟡 Weaknesses — Should Improve

| # | Issue | Impact | Recommendation |
|---|---|---|---|
| **W1** | ~~**No request signing for access-key operations**~~ | ✅ **Mitigated.** Per-key rate limiting added (§14.6). Composite rate-limit keys prevent a stolen key from exhausting the legitimate owner's quota. | Further: consider adding nonce+timestamp to access-key requests for full replay protection. |
| **W2** | **No audit log** | There is no persistent log of who toggled what, when. Redis events are ephemeral. | Add an append-only audit log (SQL or Redis Stream). Essential for any multi-user deployment. See §16 for SQL recommendations. |
| **W3** | **No email/notification on key events** | If an access key is compromised, the owner has no way of knowing until they check manually. The HA integration can send notifications as a workaround. | Add optional webhook notifications for sensitive operations (key creation, revocation, admin actions). |
| **W4** | ~~**Clock skew tolerance is implicit**~~ | ✅ **Fixed.** Timestamp tolerance is now documented and sourced from `config.security.v2MaxClockSkewMs` (default 5 min). Clock skew error responses include `serverTimeMs` and `maxSkewMs` (see §14.5). Console logs include explicit timestamps. | — |
| **W5** | ~~**Free tier limits are advisory**~~ | ✅ **Implemented.** Premium tier system with promo codes (see §14.9). Owners can be upgraded via redeemable codes. Admin endpoints for promo code management. Tier-aware limit checking in all switch-creation paths. | `FREE_TIER_LIMITS_ENABLED` now defaults to `true`. |
| **W6** | **Single-instance architecture** | The webserver is not designed for horizontal scaling (WebSocket rooms are in-process, rate limits are in-memory via `express-rate-limit`). | For a public release with significant traffic, move rate limits to Redis (e.g. `rate-limit-redis`), use Redis Pub/Sub for cross-instance WebSocket fan-out, and document the scaling story. |

---

## 16. Redis-Only Architecture — Pros & Cons

VomeSync uses **Redis as its sole data store** (with AOF persistence). This is an unconventional choice for a web application. Here is an honest assessment.

### Why it works for VomeSync today

| Advantage | Detail |
|---|---|
| **Simplicity** | One data store to deploy, back up, and monitor. No ORM, no migrations, no schema versioning. |
| **Speed** | All reads/writes are sub-millisecond. Switch toggling and real-time state updates are inherently latency-sensitive — Redis excels here. |
| **Pub/Sub built in** | Redis Pub/Sub is used for WebSocket fan-out. No need for a separate message broker. |
| **TTL / expiry natively supported** | Nonces, session tokens, and rate-limit windows use Redis TTLs. No cron jobs or cleanup tasks needed. |
| **Small data footprint** | Switch state is a small hash. Even with thousands of switches, the dataset fits comfortably in RAM. |
| **AOF persistence** | With `appendonly yes`, Redis provides durable writes. Data survives restarts. |

### Risks and limitations

| Risk | Detail | Mitigation |
|---|---|---|
| **RAM-bound storage** | All data must fit in memory. If the dataset grows beyond available RAM, Redis will OOM-kill or start evicting. | VomeSync's data model is compact. Even 100,000 switches would use < 1 GB. Monitor `used_memory` and set `maxmemory` with a `noeviction` policy. |
| **No relational queries** | Redis has no JOINs, no WHERE clauses, no aggregations. Complex queries (e.g. "all switches toggled in the last 24 hours by owner X") require client-side filtering or additional index keys. | Current query patterns are simple (get-by-key, scan-by-prefix, sorted-set pagination). If reporting needs grow, add a read-replica SQL database synced from Redis. |
| **No schema enforcement** | Redis stores blobs. There's no database-level guarantee that a switch hash contains all required fields. A bug in the application code can silently corrupt data. | Joi validation on API input mitigates write-side risk. Consider adding a periodic integrity checker that scans hashes against a schema. |
| **Single-instance durability** | AOF is durable, but if the disk fails, data is lost. There is no built-in replication in the current Docker setup. | **Add Redis replication** (sentinel or cluster) for production. At minimum, schedule regular RDB snapshots to off-host backup storage (the `deploy.sh backup` command helps, but should be automated via cron). |
| **No ACID transactions** | Redis transactions (`MULTI`/`EXEC`) are atomic but not isolated in the SQL sense. There's no rollback if business logic fails mid-pipeline. | Current usage is mostly single-key operations. For multi-key operations (e.g. creating a switch + adding to owner set + adding to public index), use `MULTI`/`EXEC` pipelines and handle partial failures. |
| **Migration complexity** | With no schema migrations, changing the data model requires hand-written Redis key scanning and transformation. | Maintain migration scripts (see `redis.js` for existing migration logic). Document every key-pattern change in release notes. |
| **No full-text search** | Redis doesn't support full-text search natively. The public switch directory relies on client-side filtering of sorted-set scans. | Acceptable at current scale. If the directory grows large, add RediSearch module or a lightweight search index (e.g. MeiliSearch). |

### Recommendation

**Redis-only is appropriate for VomeSync's current scale and use case.** The data model is simple (key-value hashes, sets, sorted sets), the dataset is small, and the latency requirements favour an in-memory store.

**Consider adding a SQL database when any of these become true:**
- You need complex reporting or analytics (queries that span multiple entity types).
- You need full audit trails with historical queries (who did what, when, with rollback).
- The dataset exceeds what fits comfortably in RAM (unlikely for switches, possible for audit logs).
- You add billing, user accounts, or team management (relational data with integrity constraints).

A pragmatic middle ground is to **keep Redis as the primary real-time store** and add a lightweight SQL database (SQLite or PostgreSQL) as a secondary store for audit logs, analytics, and relational data — synced via Redis Streams or application-level writes.

### Planned: hybrid Redis + SQL

The decision has been made to add SQL for two specific use cases:

1. **Security audit trail** — an append-only log of every authenticated action (toggle, key creation/revocation, admin operations, promo code redemption). This provides forensic capability and abuse detection that Redis's ephemeral event stream cannot support.
2. **User-facing switch history / reporting** — allowing owners to see historical toggle data, access key usage patterns, and tier changes over time.

**Implementation plan:**
- Use **SQLite** for single-instance deployments (zero-config, file-based) or **PostgreSQL** for multi-instance.
- Write to SQL in a **non-blocking, fire-and-forget** pattern so that SQL latency doesn't affect API response times.
- Keep Redis as the authoritative real-time store for switch state, rate limits, nonces, and WebSocket pub/sub.
- SQL tables: `audit_log` (timestamp, actor, action, target_uid, details JSON), `toggle_history` (uid, state, timestamp, actor), `tier_changes` (owner_id, old_tier, new_tier, promo_code, timestamp).
- Migration: no existing data needs migrating — SQL starts accumulating from deployment onwards.

---

## 17. Glossary

| Term | Meaning |
|---|---|
| **Signing key / master seed** | The Ed25519 seed stored locally in HA; never sent to the server |
| **Switch UID** | Deterministic identifier derived from the owner's public key: `base64url(SHA-256(pubkey))` |
| **Access key** | A delegated bearer token with limited permissions and optional TTL |
| **Personal key** | Legacy v1 auth token (UUID); being phased out in favour of crypto signing |
| **Canonical JSON** | Deterministic JSON serialisation (sorted keys, no whitespace) used for signing |
| **Coordinator** | HA `DataUpdateCoordinator` subclass that centralises data fetching and WebSocket management |
| **Config flow** | HA UI wizard for setting up and configuring the integration |
| **Options flow** | HA UI wizard for ongoing management (accessible via Configure button) |
| **Canonical builder** | A function in `route-helpers.js` that produces the canonical JSON string for a specific action, used for signature verification |
| **Route assembler** | The thin `api.js` that mounts sub-routers (`v2-routes`, `admin-routes`, etc.) onto a single Express router |

---

## 18. Further Reading

- `docs/ARCHITECTURE.md` — High-level architecture overview
- `docs/ARCHITECTURE_SERVER.md` — Webserver internals
- `docs/ARCHITECTURE_INTEGRATION.md` — HA integration internals
- `docs/ARCHITECTURE_WEBSITE.md` — Website internals
- `docs/API.md` — Full API documentation
- `docs/SETUP.md` — Setup and deployment guide
- `docs/TESTING.md` — Testing guide
- `docs/OPERATIONS.md` — Production operations and backups
- `CHANGELOG_FIXES.md` — Historical bug fixes
- `ENTITY_MANAGEMENT.md` — Entity management and troubleshooting

