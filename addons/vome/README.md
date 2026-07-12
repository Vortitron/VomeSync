# Vome Home Assistant Add-on

Supervisor add-on that installs the **same** `custom_components/vomesync` tree
HACS uses, and is the place for heavier companions (LAN helpers that need extra
software, future browser-RDP / Guacamole, diagnostics).

## HACS vs add-on

| | HACS integration | This add-on |
|--|------------------|-------------|
| Switches / subscribe | yes | yes (same code) |
| Relay link to Vome Home | yes | yes |
| Full-UI forwarding + LAN `/t/<slug>/` tunnels | yes (in the shared component) | yes |
| Extra sidecars (browser RDP, …) | no | yes (planned) |
| Install without HACS | no | yes |

**One codebase:** `custom_components/vomesync/`. Before building the add-on image,
run `./build.sh` in this directory to stage that tree into `staged_integration/`.

## Install (custom repository)

1. Settings → Add-ons → Add-on Store → ⋮ → Repositories
2. Add the VomeSync repo URL (or the path that serves `addons/repository.yaml`)
3. Install **Vome**, start it
4. Restart Home Assistant once so it picks up `custom_components/vomesync`
5. Settings → Devices & services → Add Integration → **Vome**
6. Link to Vome Home, enable remote access / LAN tunnels as needed

## LAN tunnels

Configured in the integration options (Remote access & LAN tunnels), not in
add-on options — the relay runs inside Home Assistant Core and must own the
LAN target list. After a friendly domain is active on vome.io:

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
