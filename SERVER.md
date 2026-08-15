# Server code moved

The sync.vome.io API, website, Docker stack and friendly-domain forward proxy
now live in **[Vortitron/VomeSync-server](https://github.com/Vortitron/VomeSync-server)**.

On this host that checkout is `/var/www/VomeSync-server`. Copy `docker/.env`
there before the first deploy from the new tree.

Jenkins (VomeHome instance, folder **VomeSync**):

- **Add-on CI** / **Add-on Release** — this HACS repo (`Vortitron/VomeSync`)
- **Server CI / E2E / Deploy / Auto-Deploy DEV+LIVE** — the server repo

A push here only runs add-on CI. Server deploys on push live in VomeSync-server.
See `konhas.com/jenkins/PIPELINES.md`.
