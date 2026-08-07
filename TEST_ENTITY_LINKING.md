# Testing Entity Linking Feature

## Setup

1. Install the VomeSync integration in Home Assistant
2. Create or subscribe to a VomeSync switch
3. Have at least one local entity (switch, light, etc.) available in HA

## Test Steps

### 1. Link an Entity

1. Go to **Settings** → **Devices & Services** → **VomeSync**
2. Click **Configure** on the VomeSync integration
3. Select **Manage Switches**
4. Select a switch you own or are subscribed to
5. Select **Link Local Entities**
6. Choose one or more entities to link
7. Click **Submit**

**Expected Result:** Should show "Successfully saved" or similar message

### 2. Verify Link is Saved

1. Go back through the same menu path
2. Select **Manage Switches** → Same switch → **Link Local Entities**

**Expected Result:** The previously selected entities should still be checked/selected

### 2b. Access Device Settings

1. From **Manage Switches**, select your switch
2. Select **View Details**
3. You'll see the Entity ID displayed (e.g., `switch.devtest`)
4. The description will show how to navigate to device settings in Home Assistant
5. You can copy the Entity ID and search for it in Settings → Devices & Services

**Expected Result:** Easy navigation to the Home Assistant device settings page

### 3. Test Automatic Toggling

1. Enable Home Assistant logging for VomeSync:
   ```yaml
   logger:
     default: info
     logs:
       custom_components.vomesync: debug
   ```
2. Restart Home Assistant or reload logging
3. Toggle the VomeSync switch (either locally or remotely via the website)

**Expected Result:**
- Check the HA logs (`/config/home-assistant.log` or via **Settings** → **System** → **Logs**)
- You should see log entries like:
  ```
  Triggering 1 linked entities for switch abc123 (state: True): ['light.living_room']
  Calling light.turn_on for light.living_room
  ```
- The linked entity should automatically toggle to match the VomeSync switch state

## Debugging

If linking doesn't work, check the following:

### Check Logs for Errors
```bash
grep -i "vomesync" /config/home-assistant.log | tail -50
```

Look for:
- "Linking entities for switch..." (when saving)
- "Current links for..." (when loading the form)
- "Triggering N linked entities..." (when switch changes state)
- Any error messages

### Verify Options are Saved

Check the config entry data:
1. Go to **Tools** → **States**
2. Search for your VomeSync switch entities
3. Check the integration's config entry in `.storage/core.config_entries` (requires file access)

### Common Issues

1. **Entities not showing as linked when reopening the form**
   - Options might not be persisting
   - Check logs for "Saving options:" message

2. **Entities not toggling automatically**
   - WebSocket connection might not be active
   - Check logs for "Checking linked entities for..." message when switch changes
   - Verify the switch UID matches (compare logged UID with entity UID)

3. **"No linkable entities found" error**
   - Make sure you have at least one switch, light, fan, input_boolean, automation, or script entity
   - These entities must not be VomeSync entities

4. **Rate limiting messages**
   - If you see "Rate limit: Skipping trigger..." messages, this is normal
   - Rate limiting prevents infinite loops when switches are inversely linked
   - Current limits:
     - Entity triggers: 2 seconds cooldown per switch
     - API toggles: 1 second cooldown per switch

5. **WebSocket connection errors (HTTP 400)**
   - Check the WebSocket URL in integration settings
   - Should be like `wss://sync.vome.io/ws` (not `wss://sync.vome.io/ws/ws`)
   - Use "Edit Connection URLs" in integration options to fix if needed

## Expected Log Flow

When working correctly, you should see:

**When saving links:**
```
INFO: Linking entities for switch abc-123-def: ['light.living_room', 'switch.bedroom']
DEBUG: Saving options: {'linked_entities': {'abc-123-def': ['light.living_room', 'switch.bedroom']}}
INFO: Entity links configured: {'abc-123-def': ['light.living_room', 'switch.bedroom']}
```

**When switch state changes:**
```
DEBUG: WebSocket state update for abc-123-def: True
DEBUG: Checking linked entities for abc-123-def. All linked: {'abc-123-def': ['light.living_room', 'switch.bedroom']}
INFO: Triggering 2 linked entities for switch abc-123-def (state: True): ['light.living_room', 'switch.bedroom']
INFO: Calling light.turn_on for light.living_room
INFO: Calling switch.turn_on for switch.bedroom
```

