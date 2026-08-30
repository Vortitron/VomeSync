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

## Supervisor add-on image

The Store builds `vome/` on the user's Home Assistant. Pin
`ghcr.io/home-assistant/base-python:3.13-alpine3.22` (see `vome/Dockerfile`
and `vome/build.yaml`) so the panel interpreter is already in the image.
Do not `apk add python3` — Alpine 3.20 went EOL in April 2026 and that is
what failed Store installs on machines that were not pre-imaged with Vome.

The overview panel keeps **Connect to Vome** visible while Home Assistant
still needs a restart (version mismatch / 400 / 502). Hide it only once the
home has a Vome Home relay link (`state.linked`) — leftover VomeSync
switch-sync entries are not that link. Remote Desktop is a secondary action
and must not be marked `.primary`. The header Connect button lives in
`index.html` so an old cached `app.js` cannot remove it. Remote Desktop is
forced off `.primary` in the HTML itself (`#qa-rdp.primary`) so a cached
script cannot paint it yellow. The Vome site URL lives in the sidebar and
in the add-on Configuration tab (`portal_url`; staging is
`https://staging.vome.io`). Panel JS/CSS URLs are stamped `?v=<addon version>`
so ingress cannot keep a previous build.

Local check (no Supervisor):

```bash
./vome/build.sh
docker build -t vome ./vome
docker run --rm --entrypoint python3 vome --version
```

## Useful test commands

If you use the project’s test helpers:
- `./test-ha-integration.sh test`
- `./test-ha-integration.sh restart`
- `./test-backend.sh <server_url>`


