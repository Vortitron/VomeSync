# Vome Home Assistant Add-on

Supervisor add-on that installs the Vome integration, serves a **sidebar
control panel** for remote access and LAN tunnels, and lets this Home Assistant
share virtual switches with other homes — without HACS, a public IP, or
port-forwarding.

The integration code is the **same** `custom_components/vomesync` tree HACS
installs. Use this add-on *or* HACS, not both, unless you know you want one
to overwrite the other.

## What it does

- **Virtual switches** — create switches other Home Assistant homes can watch
  or toggle (public directory at [sync.vome.io](https://sync.vome.io))
- **Remote access** — outbound relay to Vome so this instance can be reached
  without opening router ports
- **LAN tunnels** — expose selected LAN devices as `/t/<slug>/` on your Vome
  domain
- **Sidebar panel** — tree-view UI over the same options the integration menu
  exposes

## Add-on Store install

The GitHub repo **is** the add-on repository. In Home Assistant:

1. Settings → Add-ons → Add-on Store → ⋮ → Repositories
2. Paste: `https://github.com/Vortitron/VomeSync`
3. Install **Vome**, start it, open the sidebar panel
4. Restart Home Assistant once, then add the **Vome** integration

Required layout (Home Assistant rejects the URL otherwise):

```
repository.yaml          # at repo root
vome/
  config.yaml
  Dockerfile
  custom_components/vomesync/   # vendored copy of the HACS integration
  ...
```

The Store **builds the image on your Home Assistant**. That build must not
talk to Alpine's package index: the Dockerfile uses Home Assistant's
`base-python` image so `python3` is already present. If install fails with
`apk` / `python3 (no such package)`, you are on an old add-on version —
check for updates and rebuild.

## HACS vs add-on

| | HACS integration | This add-on |
|--|------------------|-------------|
| Switches / subscribe | yes | yes (same code) |
| Relay link to Vome Home | yes | yes |
| Full-UI + LAN `/t/<slug>/` | yes (options menu) | yes + **tree panel** |
| Extra sidecars (browser RDP, …) | no | yes (planned) |

**One codebase:** edit `custom_components/vomesync/`, then run `./vome/build.sh` so
`vome/custom_components/vomesync` stays in sync (CI enforces `diff -qr`).

## Developers (MCP)

With `home-assistant-mcp` against a **Supervised / HAOS** instance (including a
VomeHome sandbox VM):

```
vomehome_create_instance     →  name: "Vome addon test"
ha_addon_install_vome        →  adds the GitHub repo, installs + starts Vome
ha_integration_setup_vome    →  runs the vomesync config flow (defaults)
```

In brokered MCP mode the API key scopes decide (no local write/create env flags).
Container-only Home Assistant has no Supervisor store — use HACS for the
integration there.

If install fails with **no host internet connection**, Supervisor’s
`host_internet` job gate is blocking Docker pulls (the Store can still clone
git). Work around with `ha jobs options --ignore-conditions internet_host`
(or fix host DNS / enable IPv6 if that is what your host check needs), then
install again. MCP’s `ha_addon_install_vome` applies that ignore automatically
when it hits the same error.

The panel needs `python3` in the add-on image (supplied by `base-python`). If
logs show `exec: python3: not found`, update/rebuild the add-on to ≥0.3.18.

## Building locally

```bash
./vome/build.sh
# then Supervisor local build, or:
# docker build -t vome ./vome
# (Dockerfile defaults BUILD_FROM to ghcr.io/home-assistant/base-python:3.13-alpine3.22)
```
