# VomeSync Project Structure

This repository is the **Home Assistant** side of VomeSync (HACS + Supervisor add-on).
The sync.vome.io API / website / Docker stack is **[Vortitron/VomeSync-server](https://github.com/Vortitron/VomeSync-server)** — see `SERVER.md`.

```
VomeSync/
├── README.md
├── LICENSE
├── hacs.json                    # HACS custom repository metadata
├── repository.yaml              # Supervisor add-on store listing
├── custom_components/vomesync/  # HACS integration (source of truth)
├── vome/                        # Official HA add-on (vendors the integration)
├── hacs-addon/tests/            # pytest suite for the integration
├── jenkins/pipelines/           # vome-addon-ci + vome-addon-release
└── docs/
    ├── ARCHITECTURE.md
    ├── ARCHITECTURE_INTEGRATION.md
    └── TESTING.md
```

`vome/build.sh` copies `custom_components/vomesync` into `vome/custom_components/vomesync` (committed) because the Supervisor build context cannot see the parent tree.
