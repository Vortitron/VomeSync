# VomeSync - Home Assistant Integration

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/custom-components/hacs)
[![GitHub release (latest by date)](https://img.shields.io/github/v/release/Vortitron/VomeSync)](https://github.com/Vortitron/VomeSync/releases)
[![GitHub](https://img.shields.io/github/license/Vortitron/VomeSync)](https://github.com/Vortitron/VomeSync/blob/main/LICENSE)

**VomeSync** is a Home Assistant custom integration that enables public remote switch sharing between Home Assistant instances worldwide. Create virtual switches that others can monitor or toggle, perfect for community events, multi-property synchronisation, or sharing non-sensitive device states.

> **⚠️ Important Warning**: Public mode shares switch states globally. **Never use for sensitive devices** like locks, alarms, or security systems. All public switches can be viewed and toggled by anyone with the UID.

## Features

- 🌐 **Public Switch Sharing**: Create switches that others can discover and interact with
- 🔄 **Real-time Updates**: WebSocket connections ensure instant state synchronisation  
- 🏠 **Native Home Assistant**: Integrates seamlessly as standard switch/sensor entities
- 🔍 **Public Directory**: Browse community switches at [sync.vome.io](https://sync.vome.io)
- 🛡️ **Privacy Focused**: Optional anonymised metadata, city-level location only
- 📱 **Easy Setup**: Simple configuration flow with guided setup

## Use Cases

- **Community Events**: "Flash porch lights for local festival"
- **Multi-Property**: Sync switches between holiday home and main residence
- **Educational**: Demonstrate IoT concepts with shared classroom switches
- **Testing**: Share test switches with other developers
- **Fun Projects**: Create interactive community art installations

## Installation

### Via HACS (Recommended)

1. Install [HACS](https://hacs.xyz/) if you haven't already
2. Go to HACS → Integrations
3. Click the three dots menu → Custom repositories
4. Add repository URL: `https://github.com/Vortitron/VomeSync`
5. Select category: Integration
6. Find "VomeSync" and install
7. Restart Home Assistant

### Manual Installation

1. Download the `vomesync` folder from the latest release
2. Copy to `custom_components/vomesync/` in your Home Assistant config directory
3. Restart Home Assistant

## Configuration

1. Go to **Settings** → **Devices & Services**
2. Click **Add Integration** and search for "VomeSync"
3. Choose to generate a new personal key or enter an existing one
4. Accept the privacy consent to complete setup

Your personal key will be stored securely and used to authenticate your switches.

## Usage

### Creating a Switch

**Via Config Flow (Recommended):**
1. Go to VomeSync integration settings
2. Click **Configure** → **Create Switch**
3. Enter switch details:
   - **Name**: Local entity name (e.g., "Community Light")
   - **Description**: Public description (e.g., "Festival Event Light")
   - **Location**: City-level location (e.g., "Stockholm")
   - **Category**: Type of switch (Community, Personal, Event, Test, Other)
   - **Publicise**: Whether to list publicly for discovery
   - **Link** (optional): External link shown on the switch page
   - **Icon URL** (optional): Icon image shown on the switch page
   - **Banner URL** (optional): Banner image shown on the switch page
   - **CAPTCHA token** (optional): Only needed if your server enforces CAPTCHA for public switches

**Via Service Call:**
```yaml
service: vomesync.create_switch
data:
  name: "My Community Switch"
  description: "Neighbourhood event light"
  location: "London"
  category: "Community"
  publicize: true
  link: "https://example.com"
  icon_url: "https://example.com/icon.png"
  banner_url: "https://example.com/banner.jpg"
  # captcha_token: "..."  # only if required by your server
```

### Editing Switch Metadata (including icon/banner)

For switches you own:

1. Go to **Settings** → **Devices & Services**
2. Open **VomeSync** → **Configure**
3. **Manage switches** → select your switch → **Edit settings**
4. Set **Icon URL** / **Banner URL** (and other metadata). Changes are pushed to the server.

Notes:
- Leaving the URL blank will clear the icon/banner.
- If you turn on **Publicise** and your server enforces CAPTCHA, you must provide a token (or a configured bypass token).
- Home Assistant does not provide an "upload image" flow inside config/options screens; use publicly reachable image URLs.

### Manage switch page appearance on the website (v2)

For v2/crypto switches, you can generate a **website management link** from Home Assistant:

1. **Settings** → **Devices & Services** → **VomeSync** → **Configure**
2. **Manage switches** → select your v2 switch → **Manage on website**
3. Open the generated URL in your browser (it includes an access key in the `#…` fragment)

You can revoke that key afterwards via **Access keys (delegation)**.

### Subscribing to a Switch

**Via Config Flow:**
1. Go to VomeSync integration settings  
2. Click **Configure** → **Subscribe to Switch**
3. Enter the switch UID and a local name
4. A sensor entity will be created for monitoring

**Via Service Call:**
```yaml
service: vomesync.subscribe_switch
data:
  name: "Remote Festival Light"
  uid: "12345678-1234-1234-1234-123456789012"
```

### Using in Automations

**Toggle your switch:**
```yaml
automation:
  - alias: "Evening Community Light"
    trigger:
      platform: time
      at: "20:00:00"
    action:
      service: switch.turn_on
      target:
        entity_id: switch.my_community_switch
```

**React to remote switch changes:**
```yaml
automation:
  - alias: "Mirror Remote Switch"
    trigger:
      platform: state
      entity_id: sensor.remote_festival_light_status
      to: "on"
    action:
      service: switch.turn_on
      target:
        entity_id: switch.my_local_light
```

## Entity Types

### Switch Entities
- Created for switches you own
- Can be toggled on/off
- Shows in standard switch cards
- Entity ID: `switch.{switch_name}`

### Sensor Entities  
- Created for subscribed switches (monitoring only)
- Shows current state ("on"/"off")
- Cannot be controlled
- Entity ID: `sensor.{switch_name}_status`

## Entity Attributes

All entities include these attributes:
- `switch_uid`: Unique identifier
- `description`: Switch description
- `location`: Location (if provided)
- `category`: Switch category
- `last_toggled`: Timestamp of last state change
- `is_owner`: Whether you own this switch

Owner switches also include:
- `toggle_count`: Number of times toggled
- `created_at`: Creation timestamp
- `publicize`: Whether listed publicly

## Services

### `vomesync.create_switch`
Create a new switch with specified configuration.

### `vomesync.subscribe_switch`  
Subscribe to monitor an existing switch by UID.

### `vomesync.delete_switch`
Delete a switch you own (removes from server).

## Delegation (v2 access keys)

If you are using **v2/crypto** switches, owners can create **delegated access keys** for a switch and share them with other people (no personal key sharing).

In Home Assistant:
1. **Settings** → **Devices & Services** → **VomeSync** → **Configure**
2. **Manage switches** → select your v2 switch → **Access keys (delegation)**
3. Create/list/revoke keys, then share the key with the person you want to delegate to.

What delegated keys can do depends on permissions (e.g. `toggle`, `comment`). The website uses these keys for v2 comments.

## Finding Public Switches

Visit [sync.vome.io](https://sync.vome.io) to browse community switches. The directory shows:
- Switch description
- Location (city-level)
- Category  
- Current state
- UID for subscribing

## Privacy & Security

### What's Shared
- Switch state (on/off)
- Optional description, city-level location, category
- Anonymous usage statistics

### What's Private
- Your personal key
- IP addresses (not stored beyond logs)
- Device details
- Home network information

### GDPR Compliance
- Minimal data collection
- Opt-in consent required
- Data deletion available via personal key deletion
- EU-hosted servers with privacy protections

## Troubleshooting

### Connection Issues
- Check internet connectivity
- Verify server status at [sync.vome.io/api/health](https://sync.vome.io/api/health)
- Check Home Assistant logs for WebSocket errors

### Switch Not Updating
- WebSocket connections auto-reconnect after network issues
- Manual refresh: reload the integration
- Check entity availability in Developer Tools

### Invalid UID Errors
- Ensure UID is a valid UUID format
- Verify the switch exists using the status API
- Check for typos in the UID

## Support

- **Documentation**: [GitHub Repository](https://github.com/Vortitron/VomeSync)
- **Issues**: [GitHub Issues](https://github.com/Vortitron/VomeSync/issues)
- **Updates**: [@VomeHome on X](https://x.com/VomeHome)

## License

This integration is released under the MIT License. See [LICENSE](LICENSE) for details.

---

**⚠️ Disclaimer**: VomeSync is not liable for misuse of public switches. Only use for non-sensitive devices and always verify the source of shared switch UIDs.
