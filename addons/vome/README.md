# Vome Home Assistant Add-on

Supervisor add-on that installs the **same** `custom_components/vomesync` tree
HACS uses, and serves a **tree-view control panel** (ingress) for remote access
and LAN tunnels. Heavier companions (browser RDP / Guacamole, …) will land here.

## HACS vs add-on

| | HACS integration | This add-on |
|--|------------------|-------------|
| Switches / subscribe | yes | yes (same code) |
| Relay link to Vome Home | yes | yes |
| Full-UI + LAN `/t/<slug>/` | yes (options menu) | yes + **tree panel** |
| Extra sidecars (browser RDP, …) | no | yes (planned) |
| Install without HACS | no | yes |

**One codebase:** `custom_components/vomesync/`. Before building the image, run
`./build.sh` to stage that tree into `staged_integration/`.

Jenkins (see `jenkins/casc.yaml` in the VomeHome repo):

- `VomeSync/vome-addon-ci` — stage + package checks + LAN/relay tests
- `VomeSync/vome-addon-release` — ZIP artifact for distribution

## Install (custom repository)

1. Settings → Add-ons → Add-on Store → ⋮ → Repositories
2. Add the VomeSync add-ons repo (`addons/repository.yaml`)
3. Install **Vome**, start it — open the **Vome** sidebar panel
4. Restart Home Assistant once so it picks up `custom_components/vomesync`
5. Settings → Devices & services → Add Integration → **Vome** (if not present)
6. Link to Vome Home, then manage forwarding / LAN tunnels in the add-on panel

## Control panel

Ingress UI with a left-hand tree:

- Overview
- Remote access → Home Assistant UI / LAN tunnels
- Account → Link status
- About

Mutations call the shared integration services (`vomesync.set_forward_ui`,
`add_lan_route`, …) so the HACS options menu and the panel stay in sync.

## LAN tunnels

```
https://your-slug.home.vome.io/t/nas/     →  http://192.168.1.5:5000/
https://your-slug.home.vome.io/t/router/ →  http://192.168.1.1/
```

Sign in via the Vome authorise gate first (same cookie as HA remote UI).

## Building locally

```bash
cd addons/vome
./build.sh
# then use the HA Supervisor local add-on build, or docker build with BUILD_FROM
```

Do not edit Python under `staged_integration/` — it is overwritten by `build.sh`.
