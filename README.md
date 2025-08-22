# VomeSync: Public Remote Switch for Home Assistant

**VomeSync** is a Home Assistant add-on and server-based service that enables users to create and share virtual switches, allowing one Home Assistant instance to toggle a switch in another, either publicly or (in future) privately. Designed for the global Home Assistant community (1M+ users in 2025), VomeSync offers a unique, user-friendly way to sync smart home devices or create community-driven events (e.g., "Flash porch lights for a local festival"). This project is hosted under [vome.io](https://vome.io), a brand for innovative IoT solutions.

> **Warning**: Public mode shares switch states globally via a unique identifier (UID). **Do not use for sensitive devices** (e.g., locks, alarms). Private mode is planned for secure, user-controlled syncing.

## Project Overview
VomeSync consists of three components:
1. **Webserver** (sync.vome.io): Handles switch creation, toggling, and state broadcasting via WebSockets for real-time updates. Hosted on a dedicated server, it ensures privacy and scalability.
2. **Website** (remoteswitch.vome.io): Public directory listing anonymized switch details (optional description, location, category) for community discovery.
3. **HACS Add-On**: A Home Assistant custom component that creates virtual switches, connects to the webserver, and manages user interactions (creation, toggling, subscribing).

This README outlines the architecture, setup, and user flow for developers, contributors, and users.
The project is maintained by Callycode Limited, with monetization via subscriptions for premium features.

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
- **Low Admin**: Automated sales via Gumroad, minimal support (~2 hours/month), simple tax reporting under Enskild Firma.
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

## User Flow
1. **Install Add-On**:
   - User installs `VomeSync` via HACS.
   - Add-on generates a personal key via `POST /generate-key` to sync.vome.io.

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
   - User sets automations (e.g., "If sensor.remote_public_1 is on, turn on switch.my_light").

5. **Public Directory**:
   - Users browse remoteswitch.vome.io for public switches (e.g., "Festival Light, Stockholm").
   - Copy UID to subscribe in their add-on.

## Monetization
- **Free Tier**: Basic switch creation/subscription (1-2 switches, limited updates).
- **Premium Tier**: €5-15/month via Gumroad for unlimited switches, analytics (e.g., trigger history), and future private mode.
- **Potential**: 50-100 users at €10/month = €500-1,000/month semi-passive. Scales with community adoption.
- **Sales**: Automated via Gumroad (~8% fee). License keys unlock premium features.

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
1. **Users**:
   - Install VomeSync via HACS (link to repository).
   - Configure add-on in HA UI, generate personal key.
   - Create/subscribe to switches, set automations.
   - Browse remoteswitch.vome.io for public switches.
2. **Developers**:
   - Clone this repository (link to GitHub).
   - Deploy webserver: Node.js, Docker, Redis (see server setup guide).
   - Set up website: WordPress on vome.io server.
   - Test add-on: Python, HA dev environment.
3. **Contribute**:
   - Submit issues/PRs on GitHub.
   - Focus areas: WebSocket optimization, UI improvements, analytics.

## Contact
- **Support**: Email [support@vome.io](mailto:support@vome.io) or paid tier (€20/hour).
- **Community**: Join discussions on [r/homeassistant](https://reddit.com/r/homeassistant) or Home Assistant forums.
- **Website**: [remoteswitch.vome.io](https://remoteswitch.vome.io)

## License
- Add-On: MIT License (open-source).
- Server/Website: Proprietary, managed by [Your Name/Company].

_Disclaimer: VomeSync is not liable for misuse of public switches. Consult a professional for tax or privacy advice._
