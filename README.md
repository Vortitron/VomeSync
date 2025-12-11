# VomeSync: Public Remote Switch for Home Assistant

**VomeSync** is a Home Assistant add-on and server-based service that enables users to create and share virtual switches, allowing one Home Assistant instance to toggle a switch in another, either publicly or (in future) privately. Designed for the global Home Assistant community (1M+ users in 2025), VomeSync offers a unique, user-friendly way to sync smart home devices or create community-driven events (e.g., "Flash porch lights for a local festival"). This project is hosted under [vome.io](https://vome.io), a brand for innovative IoT solutions.

> **Warning**: Public mode shares switch states globally via a unique identifier (UID). **Do not use for sensitive devices** (e.g., locks, alarms). Private mode is planned for secure, user-controlled syncing.

## Project Overview

VomeSync consists of four main components:

### 1. **Webserver** (`/webserver/`)
- **Purpose**: Core API server and WebSocket handler (sync.vome.io)
- **Technology**: Node.js, Express, WebSockets, Redis
- **Features**: Switch creation, state management, real-time broadcasting
- **Deployment**: Docker container with health checks

### 2. **Home Assistant Integration** (`/hacs-addon/`)
- **Purpose**: HACS custom component for Home Assistant
- **Technology**: Python, asyncio, WebSocket client
- **Features**: Config flow UI, switch/sensor entities, real-time updates
- **Installation**: Via HACS or manual installation

### 3. **Public Website** (`/website/`)
- **Purpose**: Community switch directory (remoteswitch.vome.io)
- **Technology**: Vanilla HTML/CSS/JavaScript
- **Features**: Browse public switches, search/filter, UID copying
- **Deployment**: Static files served via Nginx

### 4. **Docker Infrastructure** (`/docker/`)
- **Purpose**: Complete deployment orchestration
- **Technology**: Docker Compose, Nginx proxy, Redis
- **Features**: SSL termination, load balancing, monitoring
- **Management**: Automated deployment scripts

### 5. (Planned) **Official Home Assistant Add-on**
- **Status**: Planned – not yet implemented
- **Purpose**: Optional, non-HACS installation path that bundles a small companion service and auto-configures the integration
- **Why**: Broader availability for users without HACS; one-click install via Add-on Store (custom repo initially)
- **High-level design**:
  - Lightweight container acting as a proxy/utility (optional), health checks, and onboarding helper
  - Installs/links the VomeSync integration via config flow (no business logic duplication)
  - Exposes useful diagnostics and status page
- **Notes**: We will avoid duplicating integration logic. The integration remains the primary interface; the add-on is an optional convenience layer.

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
  - `POST /generate-key`: Issues a personal key (UUID/JWT) for user authentication.
  - `POST /create-switch`: Creates a virtual switch with UID, optional fields (description, geocode location, category), and public flag.
  - `POST /toggle/{UID}`: Toggles the virtual switch state (requires personal key).
  - `GET /status/{UID}`: Returns current switch state (publicly accessible).
  - `WSS /ws/{UID}`: WebSocket for real-time state updates to subscribed clients.
- **Privacy**:
  - Stores only encrypted personal keys and anonymized switch data (no IPs beyond logs).
  - Public mode uses differential privacy for aggregated analytics (e.g., trigger counts).
  - GDPR-compliant: Opt-in consent, data deletion via key.

### 2. Website (remoteswitch.vome.io)
- **Purpose**: Public directory for discovering switches with the "publicize" flag.
- **Tech Stack**:
  - WordPress (free theme) hosted on the dedicated server.
  - Optional Redis caching for performance.
- **Features**:
  - Lists switches (UID, description, city-level location, category) for users to browse/copy UIDs.
  - Simple table view (e.g., "Porch Light Event, Stockholm, Community").
  - No user accounts—public read-only access.
- **Privacy**: Anonymized data only (no personal identifiers). Links to privacy policy.

### 3. HACS Add-On
- **Purpose**: User interface in Home Assistant for creating, toggling, and subscribing to switches.
- **Tech Stack**:
  - Python using Home Assistant’s `switch` platform.
  - WebSocket client (`websocket-client` package) for real-time updates.
  - YAML configuration for user settings.
- **Features**:
  - Generates personal key on first run (stored in `secrets.yaml`).
  - UI to create switches (fields: description, location, category; checkbox: "Publicize on website").
  - Creates local `switch` entity (e.g., `switch.remote_public_1`) for toggling.
  - Subscribe to other UIDs (creates local sensor/switch to monitor/toggle).
  - Clear warning: "Public mode is NOT private—anyone with UID can view/toggle."
- **Configuration Example**:
  ```yaml
  switch:
    - platform: vomesync
      name: "Public Porch Light"
      unique_id: "remote_switch_1"
      personal_key: !secret vomesync_key
      uid: "abc123"
      mode: "public"
      description: "Festival Light Event"
      location: "Stockholm"
      category: "Community"
      publicize: true
  ```

## Installation

### Via HACS (Recommended)

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

## User Flow
1. **Install Integration**:
   - User installs `VomeSync` via HACS or manually.
   - Integration generates a personal key via `POST /generate-key` to sync.vome.io.

2. **Create Switch**:
   - In HA UI, user configures a new switch with optional fields (description, city-level location, category).
   - Check "Publicize on website" to list on remoteswitch.vome.io.
   - Add-on sends `POST /create-switch` to server, receives UID.
   - Local `switch` entity created (e.g., `switch.remote_public_1`).

3. **Toggle Switch**:
   - User toggles local switch in HA.
   - Add-on sends `POST /toggle/{UID}` with personal key.
   - Server updates virtual switch state and broadcasts via WebSocket to subscribers.

4. **Subscribe to Switch**:
   - User pastes UID in add-on UI (from remoteswitch.vome.io or shared directly).
   - Add-on creates local sensor/switch (e.g., `sensor.remote_public_1`) and connects to `WSS /ws/{UID}` for real-time updates.
   - User can either:
     - Set automations (e.g., "If sensor.remote_public_1 is on, turn on switch.my_light"), OR
     - Use **Entity Linking**: Link local entities directly to VomeSync switches via the integration options menu. Linked entities automatically toggle when the VomeSync switch state changes.

5. **Public Directory**:
   - Users browse remoteswitch.vome.io for public switches (e.g., "Festival Light, Stockholm").
   - Copy UID to subscribe in their add-on.

## Monetization
- **Free Tier**: Basic switch creation/subscription (1-2 switches, limited updates).
- **Premium Tier**: €5-15/month via Gumroad for unlimited switches, analytics (e.g., trigger history), and future private mode.


## Privacy and Security
- **Personal Key**: UUID/JWT for authentication, stored securely in HA `secrets.yaml`.
- **Public Mode**: Anonymized data (city-level location, no IPs). Differential privacy for analytics.
- **Warnings**: Add-on UI clearly states: "Public mode is NOT private—use for non-sensitive events only."
- **GDPR**: Consent via checkbox, data deletion via API (POST /delete-key).

## Future Plans
- **Private Mode**: Secure instance-to-instance syncing via encrypted tunnels (e.g., WireGuard VPN), aligning with multi-property VPN hosting vision.
- **MQTT Support**: Add MQTT broker option for advanced users (Mosquitto, free).
- **Analytics**: Premium feature for trigger history, geolocation trends.
- **ESP32 Kits**: Integrate with physical Vome-branded IoT devices.

## Getting Started

### For Users

1. **Install VomeSync Integration:**
   - Add via HACS: Settings → HACS → Integrations → Custom Repositories → Add `https://github.com/Vortitron/VomeSync`
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
   - Browse switches at [remoteswitch.vome.io](https://remoteswitch.vome.io)
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

## Local Test Home Assistant (fin)
- **HA VM**: `192.168.122.9:8123` (virsh domain: `haos`)
- **Console**: `sudo virsh console haos`
- **Sync Integration**: `rsync -a --exclude='__pycache__' custom_components/vomesync/ /var/www/ha-shared-components/vomesync/`
- **Server URL**: Use server IP `http://95.216.77.237:3000` (HA VM cannot resolve "fin" hostname)
- **WebSocket URL**: Automatically derived from server URL (appends `/ws`, converts http→ws, https→wss)
- **Restart HA**: `./test-ha-integration.sh restart` or via virsh console with `ha core restart`

### Automated Testing

**Backend API Test** (`./test-backend.sh`):
```bash
./test-backend.sh [server_url]  # Tests create-switch, toggle, status
```
✓ Verifies webserver functionality end-to-end  
✓ Fixed Redis serialization issues (boolean/number handling)  
✓ All backend tests passing

**HA Integration Test** (`./test-ha-integration.sh`):
```bash
./test-ha-integration.sh test      # Run full integration test
./test-ha-integration.sh restart   # Restart Home Assistant
./test-ha-integration.sh states    # Show VomeSync entity states
```
✓ Checks HA connectivity and VomeSync config entry  
✓ Displays current switch entities  
⚠ Switch creation requires manual UI testing (options flow)

### Manual HA Testing

1. Sync: `rsync -a --exclude='__pycache__' custom_components/vomesync/ /var/www/ha-shared-components/vomesync/`
2. Restart: `./test-ha-integration.sh restart`
3. HA UI: Settings → Devices & Services → VomeSync → ⚙️ (cog) → Create/Subscribe Switch

**Config Flow Notes**:
- Personal Key field can be left blank (generates new key with consent)
- Consent checkbox covers key generation/storage (GDPR-compliant, removable via delete-key)
- WebSocket URL auto-derives from Server URL (leave blank for default)

### Website directory (sync.vome.io)

- Public catalogue now supports category chips, user-count filtering, and refreshed cards showing toggle counts and website links.
- Each switch has a shareable detail view: `https://sync.vome.io/?switch=<uid>` showing history, comments, stats, and copyable links.
- Comments and notes can be posted by the owner/API-key holders via `/api/switch/:uid/comment` (key required; not stored client-side).
- New endpoints: `/api/switch/:uid` (public detail), `/api/categories`, `/api/switch/:uid` (PATCH for metadata), `/api/profile/link` (owner profile URL).
- User counts are tracked from authenticated interactions (toggles/comments) to help filter active/public switches.
- Website runs on port **8111** in Docker; served externally via nginx SSL proxy (`sync.vome.io`).
- CAPTCHA support: set `HCAPTCHA_SECRET`/`HCAPTCHA_SITEKEY` (and optional `HCAPTCHA_BYPASS_TOKEN` for staging) to require a captcha token whenever `publicize` is set to true on create/patch. Without these env vars, captcha is disabled.

### Backup & restore (Redis)
- **What to back up**: Redis data (keys: `switch:*`, `user:*`, `key:*`, `apikey:*`, `session_token:*`, `public_switches` set, event/user sets).
- **How**: enable Redis RDB snapshots (e.g., `save 900 1`); mount `/data` to a host path and copy the `.rdb` file; optional AOF for finer granularity if ops permits.
- **Schedule**: nightly snapshot with 7–14 day retention; encrypt at rest; store off-host (S3 or similar) with bucket-level SSE/KMS.
- **Restore**: stop webserver, place `.rdb` into Redis data dir, start Redis, then restart webserver; verify with `GET /api/health` and spot-check `GET /api/public-switches` and a known `GET /api/switch/<uid>`.
- **Testing**: quarterly restore drills into a staging environment; run `npm test -- api` afterward to validate behaviour.

### GDPR considerations
- **Personal data stored**: personal keys (UUID), API keys (UUID), optional profile links, timestamps, usage events tied to keys. No names/emails unless put in descriptions/links by users.
- **Data minimisation**: only store what is required for switch auth and activity; comments are owner/API-key only. Session tokens are short-lived and single-use.
- **Retention**: switches and key data expire after 30 days of inactivity (TTL set on switch and user sets). Backups retain at most 14 days by policy above.
- **Rights**: `/api/delete-key` deletes a personal key and all associated switches/events; also removes from public sets. This serves erasure/export needs; add export-on-request if required.
- **Security**: rate limiting in API, JWT for HA, API keys revocable, HTTPS via nginx. Backups must be encrypted; access to keys limited.
- **Incident response**: if compromise suspected, rotate Redis password, revoke API keys (`/api/api-keys/:apiKey`), encourage users to regenerate personal keys, and purge session tokens (`session_token:*`).

### Test Suite Status

- ✅ **Webserver Tests**: 100% passing (Jest unit + integration tests)
- ✅ **HA Integration Tests**: 39/39 passing (pytest with mocked HA core)
- ✅ **Backend API**: Fully functional (create, toggle, status, WebSocket)
- ⚠ **HA UI**: Requires manual testing through options flow

## Contact
- **Support**: Email [support@vome.io](mailto:support@vome.io) or paid tier (€20/hour).
- **Community**: Join discussions on [r/homeassistant](https://reddit.com/r/homeassistant) or Home Assistant forums.
- **Website**: [remoteswitch.vome.io](https://remoteswitch.vome.io)

## License
- Add-On: MIT License (open-source).
- Server/Website: Proprietary, managed by [Your Name/Company].

_Disclaimer: VomeSync is not liable for misuse of public switches. Consult a professional for tax or privacy advice._
