# Vome Home Assistant Add-on

Supervisor add-on that installs the **same** `custom_components/vomesync` tree
HACS uses, and serves a **tree-view control panel** (ingress) for remote access
and LAN tunnels.

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

The panel needs `python3` in the add-on image (Dockerfile). If logs show
`exec: python3: not found`, update/rebuild the add-on to ≥0.2.3.

## Building locally

```bash
./vome/build.sh
# then Supervisor local build, or:
# docker build --build-arg BUILD_FROM=ghcr.io/home-assistant/amd64-base:3.20 -t vome ./vome
```
