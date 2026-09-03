# VomeSync: Public Remote Switch for Home Assistant

**VomeSync** is a Home Assistant add-on and server-based service that enables users to create and share virtual switches, allowing one Home Assistant instance to toggle a switch in another, either publicly or (in future) privately. Designed for the global Home Assistant community (1M+ users in 2025), VomeSync offers a unique, user-friendly way to sync smart home devices or create community-driven events (e.g., "Flash porch lights for a local festival"). This project is hosted under [vome.io](https://vome.io), a brand for innovative IoT solutions.

[![Add to Home Assistant](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=Vortitron&repository=VomeSync&category=integration)

> **Warning**: Public mode shares switch state and activity globally via a unique identifier (UID); toggling requires an access key. **Do not use for sensitive devices** (e.g., locks, alarms). Private mode is planned for secure, user-controlled syncing.

Security audit: see `docs/SECURITY_AUDIT.html`.
Architecture diagrams: see `docs/ARCHITECTURE.md`.
Integration architecture: `docs/ARCHITECTURE_INTEGRATION.md`.
The sync.vome.io API, website and Docker stack live in **[Vortitron/VomeSync-server](https://github.com/Vortitron/VomeSync-server)**.

## Project Overview

This repository is the **Home Assistant** side of VomeSync (HACS custom repository and Supervisor add-on store URL). The public API at sync.vome.io is a separate repo.

### Home Assistant Integration (`/custom_components/vomesync/`)
- **Purpose**: HACS custom integration for Home Assistant
- **Technology**: Python, asyncio, `aiohttp`, `websockets`
- **Features**: Config flow UI, switch/sensor entities, real-time updates, entity linking, relay remote access
- **Installation**: Via HACS or manual installation

### Official Home Assistant Add-on (`/vome/` + root `repository.yaml`)
- **Purpose**: Supervisor path that installs the Vome integration, a sidebar panel for remote access / LAN tunnels, and virtual-switch sharing — without HACS. Same integration code as the HACS custom component.
- **Add-on Store URL**: `https://github.com/Vortitron/VomeSync` (requires `repository.yaml` at repo root)
- **Design**:
  - `vome/build.sh` syncs `custom_components/vomesync` → `vome/custom_components/vomesync` (committed; Supervisor build context cannot see the parent tree)
  - Image is built on the user's Home Assistant from `vome/Dockerfile`. Base is Home Assistant `base-python` so the panel interpreter is already in the image — the build must not `apk add` (Alpine package indexes are a second network dependency and fail on EOL bases).
  - MCP: `ha_addon_install_vome` (+ `ha_supervisor_api`) on Supervised/HAOS targets
  - Jenkins: `VomeSync/vome-addon-ci` + `VomeSync/vome-addon-release`

### Server (separate repo)
API, WebSocket, public directory and Docker deploy: **[Vortitron/VomeSync-server](https://github.com/Vortitron/VomeSync-server)** (`/var/www/VomeSync-server` on this host).

This README outlines the architecture, setup, and user flow for developers, contributors, and users.
The project is maintained by Vortitron, with monetization via subscriptions for premium features.

## Features
- **Public Switch Syncing**: Create a virtual switch in Home Assistant, share it via a unique UID, and let others toggle or monitor it (e.g., community light events).
- **Real-Time Updates**: WebSockets ensure instant state changes without polling, working behind firewalls (outbound connections only).
- **Privacy First**: Optional anonymized fields (description, city-level location, category). Clear warnings for public mode. GDPR-compliant.
- **Minimal Requirements**: Only requires Home Assistant and the add-on. No external MQTT or port forwarding needed (WebSocket-based).
- **Scalable**: Future support for private mode (secure instance-to-instance syncing) and MQTT for advanced users.
- **Monetization**: Free add-on for basic use; premium subscription (€5-15/month) for unlimited switches, analytics, or future private mode.

## Why VomeSync?
- **Unique**: No Home Assistant add-on offers plug-and-play public switch sharing with a community focus.
- **Global Appeal**: Relevant for Home Assistant users worldwide (EU, US, Asia) for community events or multi-property syncing.
- **Synergy**: Builds toward private VPN hosting for secure IoT syncing.

## Architecture
### 1. Webserver (sync.vome.io)
- **Purpose**: Manages switch creation, toggling, state storage, and real-time broadcasting.
- **Tech Stack**:
  - Node.js with `ws` library (or Flask-SocketIO) for WebSocket connections.
  - Redis for in-memory state storage and Pub/Sub for broadcasting to multiple clients.
  - Docker for deployment on a dedicated server.
  - TLS 1.3 (Let’s Encrypt) for secure connections.
- **Endpoints**:
  - `POST /generate-key`: Issues a personal key (UUID/JWT) for user authentication (not used by the Home Assistant integration).
  - `POST /create-switch`: Creates a virtual switch with UID, optional fields (description, geocode location, category), and public flag.
  - `POST /toggle/{UID}`: Toggles the virtual switch state (requires personal key).
  - `POST /api/v2/switch`: Creates a deterministic switch (Ed25519 signed; UID derived from switch public key).
  - `POST /api/v2/my-switches`: Lists switches owned by an Ed25519 owner key (signed).
  - `POST /api/v2/switch/{UID}/state`: Sets state + optional params (signed; params forwarded via WebSocket).
  - `POST /api/v2/switch/{UID}`: Updates v2 switch metadata (signed by owner; includes `iconUrl` + `bannerUrl`).
  - `POST /api/v2/switch/{UID}/access-keys`: Creates a delegated access key for a switch (signed by owner).
  - `POST /api/v2/switch/{UID}/toggle`: Toggles a switch using a delegated access key (no signatures).
  - `POST /api/v2/switch/{UID}/comment`: Posts a comment using a delegated access key (no signatures).
  - `GET /status/{UID}`: Returns current switch state (publicly accessible).
  - `WSS /ws?uid={uid}`: WebSocket for real-time state updates to subscribed clients.
- **Privacy & security notes**:
  - Personal keys and API keys are **bearer secrets** and are stored in Redis; treat Redis as sensitive (private network + strong password).
  - Public mode stores **user-provided metadata** (description/location/category/link). Do not include personal data.
  - GDPR: opt-in consent, data deletion via key.

### 2. Website (sync.vome.io)
- **Purpose**: Public directory for discovering switches with the "publicize" flag.
- **Tech Stack**:
  - Static site (vanilla HTML/CSS/JavaScript) served via Nginx.
- **Features**:
  - Lists switches (UID, description, city-level location, category) for users to browse/copy UIDs.
  - Optional theming: per-switch `iconUrl` + `bannerUrl` shown on switch pages (and `https://sync.vome.io/switch/<uid>` deep links).
  - Simple table view (e.g., "Porch Light Event, Stockholm, Community").
  - No user accounts—public read-only access.
- **Privacy**: Anonymized data only (no personal identifiers). Links to privacy policy.

### 3. Home Assistant Integration (HACS)
- **Purpose**: Home Assistant integration for creating, toggling, subscribing, importing, and linking switches.
- **Features**:
  - Config flow UI (no YAML required)
  - Fast startup from locally cached imported switches
  - Real-time updates via WebSocket (`/ws?uid=<uid>`)
  - Optional entity linking (turn local entities on/off when VomeSync changes)
  - v2 switch management: edit metadata including `iconUrl`/`bannerUrl` for nicer public pages
  - Manage on website: generates a session, fragment-based URL for editing `link`/`iconUrl`/`bannerUrl` on the website (regenerate in HA when needed)
  - v2 delegation: create/list/revoke per-switch access keys (scoped permissions like toggle/comment)
  - Subscriptions are read-only by default; add a delegated access key in HA to enable toggling
  - **Health score, with no account:** the `vomesync.health_score_run` action checks this Home Assistant — noisy sensors, a swelling database, dead entities, backups that stopped — and puts the score and every finding on `sensor.vome_health_score`. If this instance is not linked to Vome yet it links itself temporarily, so there is nothing to sign up for and nothing to type; Vome deletes that run after two hours unless you sign in from the link it gives you, and the report stays here either way. See [docs/HEALTH_SCORE.md](docs/HEALTH_SCORE.md).
  - **Connect to Vome Home (relay):** *Settings → Devices & Services → Vome → Configure → Connect to Vome Home* (a top-level menu option). A device-authorisation flow (show a code → approve on vome.io with GitHub) brings up an outbound tunnel so Vome and the [home-assistant-mcp](https://github.com/Vortitron/home-assistant-mcp) server can broker scoped, audited calls to this HA — with **no public IP, port-forwarding, or Nabu Casa**. No tokens to paste on any install type: the component mints its own long-lived access token via `hass.auth` (it appears in the owner's profile as "Vome relay"). The Supervisor's `/core/api` proxy is deliberately not used — it 401s core's own token. An "Alternative connection" step holds manual overrides (local token/URL, ESPHome dashboard URL) for unusual setups. See `custom_components/vomesync/relay_client.py`.
    - **ESPHome over the same tunnel:** the relay also proxies the ESPHome dashboard's REST subset (list devices, read/write device YAML), so the MCP can see and edit ESPHome device code without inbound exposure. The dashboard is auto-discovered via the Supervisor add-on API (or set an explicit URL in the link step). The official add-on is host-networked with its web port disabled by default, so discovery reads the add-on info and targets the dashboard's *ingress* port on `127.0.0.1` (the only route its nginx admits besides the Supervisor — core shares the host network); a user-mapped web port takes precedence, and `<hostname>:6052` remains the fallback for third-party dashboards. Discovery only accepts a *started* add-on — a stopped add-on is reported as "installed but not running" rather than a connect error, and a failed connection drops the cached address so a restarted add-on is re-discovered. Streaming build commands (compile/upload/run/logs) still need a directly-reachable dashboard.

## Installation

### Via the Home Assistant Add-on Store

This is the path that does **not** need HACS. It installs the integration, starts a sidebar panel, and is what Vome Home instances use.

1. **Settings** → **Add-ons** → **Add-on Store** → ⋮ → **Repositories**
2. Paste `https://github.com/Vortitron/VomeSync` and add it
3. Install **Vome**, start it, open the **Vome** sidebar panel
4. Restart Home Assistant once, then **Settings** → **Devices & Services** → **Add Integration** → **Vome**

Supervisor builds the add-on image on your machine. That build pulls Home Assistant's `base-python` image (which already includes `python3`) and does not run `apk add`. If a build fails talking to `dl-cdn.alpinelinux.org` / `python3 (no such package)`, you are on an add-on older than 0.3.18 — refresh the store and rebuild.

### Via HACS

[![Add to Home Assistant](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=Vortitron&repository=VomeSync&category=integration)

1. Open Home Assistant and go to **HACS** → **Integrations**
2. Click the **+** button and search for **"VomeSync"**
3. Click **Install**
4. Restart Home Assistant
5. Go to **Settings** → **Devices & Services** → **Add Integration**
6. Search for **VomeSync** and follow the setup wizard

### Manual Installation

1. Copy the `custom_components/vomesync` directory to your Home Assistant's `config/custom_components/` directory
2. Restart Home Assistant
3. Go to **Settings** → **Devices & Services** → **Add Integration**
4. Search for **VomeSync** and follow the setup wizard
5. If adding this repository manually in HACS, ensure the category is **Integration** (manifest lives in `custom_components/vomesync`).

## User Flow
1. **Install Integration**:
   - User installs `VomeSync` via the Add-on Store, HACS, or manually.
   - Integration can run in:
     - Generates/stores a signing key locally and uses v2 signed endpoints.

2. **Create Switch**:
   - In HA UI, user configures a new switch with optional fields (description, city-level location, category).
   - Check "Publicize on website" to list on sync.vome.io.
   - Add-on sends `POST /api/v2/switch`, receives UID.
   - Local `switch` entity created (e.g., `switch.remote_public_1`).

3. **Toggle Switch**:
   - User toggles local switch in HA.
   - Add-on sends `POST /api/v2/switch/{UID}/state`.
   - Server updates virtual switch state and broadcasts via WebSocket to subscribers.

4. **Subscribe to Switch**:
   - User pastes UID in the integration UI (from sync.vome.io or shared directly).
   - Integration imports the switch and connects to `WSS /ws?uid=<uid>` for real-time updates.
   - User can either:
     - Set automations (e.g., "If sensor.remote_public_1 is on, turn on switch.my_light"), OR
     - Use **Entity Linking**: Link local entities directly to VomeSync switches via the integration options menu.
       - When the VomeSync switch changes, linked entities are toggled.
       - For **owned** switches, changes to linked entities can also update the VomeSync switch (bidirectional).
       - If you link **multiple** entities, you can choose how they drive the switch: **Master**, **Any on (OR)**, or **All on (AND)**.
       - You can set the link **Direction**: **Both**, **Switch → entities**, or **Entities → switch**.

5. **Public Directory**:
   - Users browse sync.vome.io for public switches (e.g., "Festival Light, Stockholm").
   - Copy UID to subscribe in their add-on.

## Monetization
- **Free Tier**: Up to 8 switches (4 public listings) and up to 16 subscriptions per Home Assistant installation, plus rate limits.
- **Premium Tier**: €5-15/month for higher limits, private switches, analytics (e.g., trigger history), and priority support.
- **Payments**: Stripe planned; interim crypto payments (Base/Solana) are under consideration.
- **Premium Listings**: Paid featured placement in the public directory for community events or organisations.


## Privacy and Security
- **Auth Modes**:
  - **Keypair (v2)**: Ed25519 signing key stays local to Home Assistant; server only sees public keys and signatures.
  - **Personal keys (v1)** still exist on the server for non-HA clients, but the HA integration uses keypair authentication.
- **Public Mode**: Treat as global/public; only store user-provided metadata (description/location/category/link) and activity events; do not include personal data.
- **Warnings**: Add-on UI warns that public mode is not private and is intended for non-sensitive events only.
- **GDPR**: Consent via checkbox, data deletion via API (POST /delete-key).
- **Ops runbook**: See `docs/OPERATIONS.md` for the pre-beta checklist, backups, restore drills, and incident response.

## Future Plans
- **Private Mode**: Secure instance-to-instance syncing via encrypted tunnels (e.g., WireGuard VPN), aligning with multi-property VPN hosting vision.
- **MQTT Support**: Add MQTT broker option for advanced users (Mosquitto, free).
- **Analytics**: Premium feature for trigger history, geolocation trends.
- **ESP32 Kits**: Integrate with physical Vome-branded IoT devices.

## Getting Started

### For Users

1. **Install VomeSync Integration:**
   - Add-on Store (no HACS): Settings → Add-ons → Add-on Store → ⋮ → Repositories → `https://github.com/Vortitron/VomeSync` → install **Vome**
   - Or HACS: Settings → HACS → Integrations → Custom Repositories → Add `https://github.com/Vortitron/VomeSync`
   - Or download manually to `custom_components/vomesync/`
   - Restart Home Assistant

2. **Configure Integration:**
   - Settings → Devices & Services → Add Integration → VomeSync
   - Generate personal key or provide existing one
   - Accept privacy consent

3. **Create Your First Switch:**
   - Integration settings → Configure → Create Switch
   - Choose name, description, category
   - Enable "Publicize" to share with community

4. **Subscribe to Public Switches:**
   - Browse switches at [sync.vome.io](https://sync.vome.io)
   - Copy UID and subscribe via integration settings
   - Use in automations to react to remote events

### For Developers

1. **Quick Start with Docker:**
   ```bash
   git clone https://github.com/Vortitron/VomeSync.git
   cd vomesync/docker
   cp env.example .env
   # Edit .env with your configuration
   ./scripts/deploy.sh
   ```

2. **Production Deployment:**
   
   The VomeSync website runs on port **8111** (HTTP) and is designed to be proxied through nginx with SSL:
   
   ```bash
   # Start the Docker stack
   cd /var/www/VomeSync/docker
   docker-compose up -d
   ```
   
   **Nginx Configuration for sync.vome.io:**
   ```nginx
   server {
       listen 80;
       listen [::]:80;
       server_name sync.vome.io;
       
       # Redirect to HTTPS
       return 301 https://$server_name$request_uri;
   }
   
   server {
       listen 443 ssl http2;
       listen [::]:443 ssl http2;
       server_name sync.vome.io;
       
       # Certbot will add SSL configuration here
       ssl_certificate /etc/letsencrypt/live/sync.vome.io/fullchain.pem;
       ssl_certificate_key /etc/letsencrypt/live/sync.vome.io/privkey.pem;
       
       # Proxy to VomeSync website container
       location / {
           proxy_pass http://localhost:8111;
           proxy_set_header Host $host;
           proxy_set_header X-Real-IP $remote_addr;
           proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
           proxy_set_header X-Forwarded-Proto $scheme;
       }
       
       # WebSocket support for API
       location /ws {
           proxy_pass http://localhost:3090;
           proxy_http_version 1.1;
           proxy_set_header Upgrade $http_upgrade;
           proxy_set_header Connection "upgrade";
           proxy_set_header Host $host;
       }
       
       # API endpoints
       location /api/ {
           proxy_pass http://localhost:3090;
           proxy_set_header Host $host;
           proxy_set_header X-Real-IP $remote_addr;
           proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
           proxy_set_header X-Forwarded-Proto $scheme;
       }
   }
   ```
   
   **Port Summary:**
   - **3000**: API/WebSocket server (dev and production)
   - **8111**: Website (production, nginx proxy target)
   - **6380**: Redis (localhost only)

3. **Development Setup:**
   ```bash
   # Install dependencies
   cd webserver && npm install
   
   # Start Redis
   docker run -d --name redis -p 6379:6379 redis:alpine
   
   # Start development server
   npm run dev
   ```

4. **Documentation:**
   - [Setup Guide](docs/SETUP.md) - Complete installation instructions
   - [API Documentation](docs/API.md) - REST and WebSocket API reference
   - [Docker Guide](docker/README.md) - Container deployment

### For Contributors

1. **Areas for Contribution:**
   - WebSocket optimization and reliability
   - Home Assistant UI improvements
   - Analytics and monitoring features
   - Mobile-responsive website enhancements
   - Security auditing and testing

2. **Development Process:**
   - Fork repository and create feature branch
   - Follow existing code style and patterns
   - Add tests for new functionality
   - Submit pull request with detailed description

3. **Getting Support:**
   - GitHub Issues for bugs and feature requests
   - Discussions for questions and ideas
   - Community support via r/homeassistant

## Developer notes
See `docs/DEV_NOTES.md` for local Home Assistant testing notes and helper scripts.

### Website directory (sync.vome.io)

- Public catalogue now supports category chips, user-count filtering, and refreshed cards showing toggle counts and website links.
- Each switch has a shareable detail view: `https://sync.vome.io/?switch=<uid>` showing history, comments, stats, and copyable links.
- Comments and notes can be posted by the owner/API-key holders via `/api/switch/:uid/comment` (key required; not stored client-side).
- New endpoints: `/api/switch/:uid` (public detail), `/api/categories`, `/api/switch/:uid` (PATCH for metadata), `/api/profile/link` (owner profile URL).
- User counts are tracked from authenticated interactions (toggles/comments) to help filter active/public switches.
- Website runs on port **8111** in Docker; served externally via nginx SSL proxy (`sync.vome.io`).
- CAPTCHA support: set `HCAPTCHA_SECRET`/`HCAPTCHA_SITEKEY` (and optional `HCAPTCHA_BYPASS_TOKEN` for staging) to require a captcha token whenever `publicize` is set to true on create/patch. Without these env vars, captcha is disabled.

### Relay (outbound tunnel) — backend env

The relay control channel (`/ws/relay`, `webserver/src/websocket/relayManager.js`)
and internal dispatch endpoint (`/internal/relay/dispatch`) are **inert until
configured**, so existing switch sync is unaffected. To enable end-to-end:

- `RELAY_INTERNAL_SECRET` — shared secret, identical on the backend and the Vome
  portal (vome.io). Authenticates portal→backend dispatch and backend→portal
  secret-verify (both constant-time compared). Empty ⇒ relay disabled.
- `RELAY_PORTAL_VERIFY_URL` — the portal's verify endpoint
  (default `https://vome.io/api/internal/relay/verify`).

The portal side additionally needs `RELAY_DISPATCH_URL` (pointing at the
server's `/internal/relay/dispatch`) and `RELAY_WS_URL`
(default `wss://sync.vome.io/ws/relay`). Server env, forwarding rate limits,
Redis backup and GDPR notes: **[VomeSync-server](https://github.com/Vortitron/VomeSync-server)**.

### Test Suite Status

- ✅ **Webserver Tests**: 100% passing (Jest unit + integration tests)
- ✅ **HA Integration Tests**: 254 passing, 1 skipped (pytest with mocked HA core)
- ✅ **Backend API**: Fully functional (create, toggle, status, WebSocket)
- ⚠ **HA UI**: Requires manual testing through options flow

## Contact
- **Support**: Please use [GitHub Issues](https://github.com/Vortitron/VomeSync/issues).
- **Updates**: [@VomeHome on X](https://x.com/VomeHome)
- **Website**: [sync.vome.io](https://sync.vome.io)

## License
- Add-On: MIT License (open-source).
- Server/Website: Proprietary, managed by [Your Name/Company].

_Disclaimer: VomeSync is not liable for misuse of public switches. VomeSync is in Public Beta - Features may be changed to premium, changed functionality or removed with no notice._
