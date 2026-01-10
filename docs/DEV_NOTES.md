# Developer notes (local HA + integration testing)

This page is intentionally developer-focused and is not required for beta/prod operations.

## Local Home Assistant test instance

If you keep a separate Home Assistant VM/container for testing:
- Make sure the VM can reach your VomeSync server URL (use an IP/hostname that’s reachable from that network).
- WebSocket URL is derived from the server URL (http→ws, https→wss, and appends `/ws`).

### Syncing the integration into a HA dev environment

If you have a shared folder/path for HA custom components, you can sync the integration like this:

```bash
rsync -a --exclude='__pycache__' custom_components/vomesync/ /path/to/ha/config/custom_components/vomesync/
```

Then restart Home Assistant and re-load the integration via the UI.

## Useful test commands

If you use the project’s test helpers:
- `./test-ha-integration.sh test`
- `./test-ha-integration.sh restart`
- `./test-backend.sh <server_url>`


