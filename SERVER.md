# Server code moved

The sync.vome.io API, website, Docker stack and friendly-domain forward proxy
now live in **[Vortitron/VomeSync-server](https://github.com/Vortitron/VomeSync-server)**.

On this host that checkout is `/var/www/VomeSync-server`. Copy `docker/.env`
there before the first deploy from the new tree; Jenkins `vomesync-ci` /
`vomesync-e2e` / `vomesync-deploy` check out that repo.
