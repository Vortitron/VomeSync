# VomeSync Integration Fixes

## Version: Dev Branch (2025-12-09)

### Critical Fixes

#### 1. WebSocket Connection Issues (HTTP 400 Errors)
**Problem:** WebSocket connections were being rejected with HTTP 400 errors, causing entities to show as "Unavailable".

**Root Cause:** The WebSocket URL was being constructed incorrectly, adding `/ws` twice (e.g., `wss://sync.vome.io/ws/ws?uid=...`).

**Fix:** 
- Updated `_connect_to_switch()` in `websocket_client.py` to check if `/ws` is already in the URL
- Only appends `/ws` if not already present
- Logs the full URL for debugging

**Files Changed:**
- `custom_components/vomesync/websocket_client.py`

#### 2. WebSocket Retry Spam
**Problem:** WebSocket client was retrying connections every 5 seconds without backoff, causing log spam (200+ messages).

**Fix:**
- Implemented exponential backoff: 5s → 10s → 20s → 40s → max 60s
- Added reconnection attempt counter per switch
- Resets backoff on successful connection

**Files Changed:**
- `custom_components/vomesync/websocket_client.py`

#### 3. Entity Linking Infinite Loops
**Problem:** If users created inverse automations or linked switches to each other, they could create infinite toggle loops.

**Fix:**
- Added rate limiting to `_trigger_linked_entities()`:
  - 2-second cooldown between entity triggers per switch
  - Logs warnings when rate limit is hit
- Added rate limiting to `toggle_switch()`:
  - 1-second cooldown between API toggles per switch
  - Prevents API spam

**Files Changed:**
- `custom_components/vomesync/coordinator.py`

### Configuration Changes

#### WebSocket URL Simplification
**Change:** Initial setup no longer requires manual WebSocket URL entry.

**Details:**
- During initial setup, WebSocket URL is auto-derived from Server URL
- Shows user what WebSocket URL will be used
- Can still be edited manually via "Edit Connection URLs" in integration options

**Files Changed:**
- `custom_components/vomesync/config_flow.py`
- `custom_components/vomesync/translations/en.json`

### Rate Limiting Configuration

Current rate limits can be adjusted in `coordinator.py`:

```python
self._trigger_cooldown = 2.0  # Entity triggers (seconds)
self._toggle_cooldown = 1.0   # API toggles (seconds)
```

### Testing

See `TEST_ENTITY_LINKING.md` for:
- Step-by-step testing instructions
- How to verify fixes are working
- Common issues and debugging steps
- Expected log outputs

### Upgrade Notes

**For Existing Installations:**
1. Check your WebSocket URL: Settings → Integrations → VomeSync → Configure → Edit Connection URLs
2. If it contains `/ws/ws`, remove one `/ws` (should be like `wss://sync.vome.io/ws`)
3. Restart Home Assistant
4. Check logs to verify WebSocket connections are successful

**Log Verbosity:**
The integration now logs more information at INFO level to help with debugging:
- Entity linking setup
- WebSocket connection attempts
- Rate limiting actions

To see DEBUG logs, add to `configuration.yaml`:
```yaml
logger:
  default: info
  logs:
    custom_components.vomesync: debug
```

### Known Limitations

1. **Rate Limiting:** If you toggle a VomeSync switch faster than once per second, some requests will be dropped
2. **Entity Linking Delay:** There's a 2-second cooldown before linked entities can be triggered again
3. **WebSocket Backoff:** After multiple failed connections, retries can take up to 60 seconds

These limitations are intentional to prevent system overload and infinite loops.

### Future Improvements

- [ ] Configurable rate limits via integration options
- [ ] Per-entity rate limiting (instead of per-switch)
- [ ] WebSocket URL validation during setup
- [ ] Automatic WebSocket URL correction on 400 errors
- [ ] Connection health dashboard in integration UI

