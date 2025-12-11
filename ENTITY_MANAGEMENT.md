# VomeSync Entity Management

## Entity-Level Features

### Enhanced Entity Attributes

Each VomeSync switch entity now includes the following attributes visible in the entity state:

```yaml
# Basic VomeSync Info
uid: "abc-123-def-456"
description: "My Switch Description"
location: "Living Room"
category: "Home"

# Linked Entities
linked_entities_count: 2
linked_entities:
  - light.living_room
  - switch.bedroom

# Management Info
integration_management: "Configure via: Settings → Devices & Services → VomeSync → Configure"
```

### Service Call: Link Entities

You can now link entities directly via service call:

```yaml
service: vomesync.link_entities
target:
  entity_id: switch.devtest
data:
  entities:
    - light.living_room
    - switch.bedroom
```

Or via Developer Tools → Services:
1. Service: `VomeSync: Link entities`
2. Target: Select your VomeSync switch
3. Entities: List of entity IDs to link

## Accessing Entity Settings

### From Entity Card:
1. Find your VomeSync switch in Lovelace
2. Click the entity
3. Click the gear icon (⚙️) in the top right
4. You'll see the entity settings page with all attributes

### From Developer Tools:
1. Go to **Developer Tools** → **States**
2. Search for your switch (e.g., `switch.devtest`)
3. View all attributes including linked entities

### Integration Options:
The main management interface is still available at:
**Settings** → **Devices & Services** → **VomeSync** → **Configure**

From there you can:
- Create/subscribe to switches
- Manage switch settings
- Link entities (GUI interface)
- View switch details
- Edit connection URLs
- Manage API keys

## Entity Information Display

When you view your VomeSync switch entity in Home Assistant, you'll now see:

**Attributes Tab:**
- UID for the switch
- Linked entities list
- Count of linked entities
- Management link reference

**History Tab:**
- Toggle history
- State changes

**Settings Tab (gear icon):**
- Entity ID
- Name
- Icon
- Area
- Device info

## Finding Your Switches

### Quick Access:
1. **Search**: Type the switch name in HA search
2. **Entities List**: Settings → Devices & Services → VomeSync → Click the device
3. **Developer Tools**: Developer Tools → States → Filter by "vomesync"

### Integration Management:
For full switch management (edit, delete, link entities via GUI):
1. Settings → Devices & Services
2. Find "VomeSync" integration
3. Click **Configure**
4. Select **Manage Switches**

## Troubleshooting

### Switch Shows as "Unavailable"

If your switch shows as "unavailable", check:

1. **WebSocket Connection**:
   ```
   Check logs for: "WebSocket connected for switch..."
   If you see errors, check integration connection URLs
   ```

2. **Integration Status**:
   - Settings → Devices & Services → VomeSync
   - Should show as "OK" not "Failed to set up"

3. **Check Logs**:
   ```bash
   # Filter for VomeSync logs
   grep -i "vomesync" /config/home-assistant.log | tail -50
   ```

4. **Reload Integration**:
   - Settings → Devices & Services → VomeSync
   - Click the three dots (⋮)
   - Select "Reload"

5. **Check Entity Registry**:
   ```
   Developer Tools → States → search for your entity
   If it doesn't exist, the entity wasn't created properly
   ```

### Common Causes of "Unavailable"

1. **WebSocket Not Connected**:
   - Check WebSocket URL in integration settings
   - Should be like `wss://sync.vome.io/ws`
   - See logs for connection errors

2. **Initial Data Not Loaded**:
   - Coordinator might not have fetched data yet
   - Wait 30-60 seconds and check again
   - Check logs for "Coordinator update failed" errors

3. **Switch Deleted on Server**:
   - UID might no longer exist on the server
   - Try deleting and recreating the switch

4. **Personal Key Invalid**:
   - Check integration options → Edit Connection
   - Verify personal key is still valid

### Debug Logging

Enable debug logging for VomeSync:

```yaml
# configuration.yaml
logger:
  default: info
  logs:
    custom_components.vomesync: debug
```

Then check `/config/home-assistant.log` for detailed information about:
- WebSocket connections
- State updates
- Entity registration
- Coordinator updates

### Key Log Messages to Look For

**During entity setup:**
```
Switch platform setup - Full options: {...}
Preparing entities from options: X owned switches, Y subscriptions
Creating owned switch entity: name='...', uid='...'
Successfully added X VomeSync switch entities
  - EntityName (uid=abc-123, owner=True)
```

**If no entities added:**
```
No VomeSync switch entities were added! 
Options contained: switches=[...], subscriptions=[...]
```

**During coordinator updates:**
```
Fetched X switches from API
```

**When entity becomes available/unavailable:**
```
Switch abc-123 unavailable: coordinator update failed
Switch abc-123 unavailable: no data from coordinator
```

## Next Steps

If switch is still unavailable after checking above:
1. Share relevant log entries
2. Check if switch exists on server (visit website)
3. Try deleting and recreating the integration
4. Verify personal key is valid

