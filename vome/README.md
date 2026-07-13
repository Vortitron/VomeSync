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
vomehome_create_instance  →  name: "Vome addon test"
ha_addon_install_vome     →  adds the GitHub repo, installs + starts Vome
```

Requires `HA_ALLOW_WRITE=true` (and `VOMEHOME_ALLOW_CREATE=true` to create).
Container-only Home Assistant has no Supervisor store — use HACS for the
integration there.

## Building locally

```bash
./vome/build.sh
# then Supervisor local build, or:
# docker build --build-arg BUILD_FROM=ghcr.io/home-assistant/amd64-base:3.20 -t vome ./vome
```
