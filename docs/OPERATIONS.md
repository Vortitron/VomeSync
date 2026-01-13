# VomeSync Operations (Pre-beta + Production)

This document is a practical checklist/runbook for operating VomeSync safely, especially before inviting a **beta test group**.

## Pre-beta checklist (do these early)

### Security essentials
- **Secrets set**:
	- `JWT_SECRET`: long, random, unique per environment (beta/prod).
	- `KEY_HASH_SECRET` (optional): dedicated secret for hashing bearer keys before storing in Redis (defaults to `JWT_SECRET`).
	- `REDIS_PASSWORD`: long, random, unique per environment; **never** expose Redis publicly.
- **TLS**:
	- Use HTTPS/WSS externally (reverse proxy recommended).
	- If you enable in-app SSL (`ENABLE_SSL=true`), you must set `SSL_CERT_PATH` + `SSL_KEY_PATH`.
- **CORS**:
	- Lock down `CORS_ORIGINS` to your real domains and any required Home Assistant origins.
- **CAPTCHA for public listings**:
	- If you want to reduce abuse, set `HCAPTCHA_SECRET`/`HCAPTCHA_SITEKEY` (and optionally `HCAPTCHA_BYPASS_TOKEN` for staging).
- **Key handling**:
	- Treat **personal keys** and **(v2) access keys** as **bearer secrets**. Anyone with the key can act as that user/key.
	- The server stores only **hashed key IDs** in Redis (no plaintext bearer keys). For legacy data, keys are migrated on first use; consider a one-time cleanup window if you had pre-existing plaintext keys.
	- Ensure logs never print full keys (redaction is enabled in the webserver logger as defence-in-depth).

### Network exposure
- **Expose only what you need**:
	- Prefer exposing only the reverse proxy (`VOMESYNC_PROXY_HTTP_HOST_PORT` / `VOMESYNC_PROXY_HTTPS_HOST_PORT`).
	- Keep API/WS ports (`VOMESYNC_API_HOST_PORT`, `VOMESYNC_WS_HOST_PORT`) firewalled to trusted sources if exposed at all.
- **Internal-only services**:
	- Redis should remain internal to the Compose network (no host port mapping in `docker-compose.yml`).

### Reliability basics
- **Volume stability**:
	- Keep `VOMESYNC_REDIS_VOLUME_NAME` stable. Changing it will look like “all switches disappeared” (new empty DB).
- **Health checks**:
	- Confirm `/api/health` returns `200` and `redis: true`.
	- If you proxy a `/health` shortcut in nginx, ensure it targets the **API port** (not the standalone WS port when `PORT` != `WS_PORT`).
- **Rollback plan**:
	- You should be able to redeploy the previous image/tag quickly (and keep the same Redis volume).

### Beta gating (strongly recommended)
- Run **beta** as its **own environment**:
	- separate domain or path routing
	- separate `.env`
	- separate Redis volume name (e.g. `vomesync_redis_beta`)

## Backups (Redis)

VomeSync stores its state in Redis and in Docker deployments uses Redis persistence (`appendonly yes`) with a named volume.

### What to back up
- **Redis volume**: `VOMESYNC_REDIS_VOLUME_NAME` (contains Redis AOF / metadata).
- **Media volume** (icons/banners): `VOMESYNC_MEDIA_VOLUME_NAME` (uploaded/downloaded images converted to WebP).
- **Configuration/secrets**: your `docker/.env` (store securely; do **not** commit).
- **Optional**: `VOMESYNC_LOGS_VOLUME_NAME` if you need log retention.

### Backup method (simple, consistent, small downtime)
This creates an encrypted archive **off-host** (recommended). The “small downtime” part is usually acceptable for beta.

1. Stop the API + Redis briefly:

```bash
cd docker
docker compose stop vomesync-webserver vomesync-redis
```

2. Archive the Redis volume to a file (example uses Alpine for `tar`):

```bash
cd docker
set -a
. ./.env
set +a

BACKUP_NAME="vomesync-redis-$(date -u +%Y%m%dT%H%M%SZ).tgz"
docker run --rm \
	-v "${VOMESYNC_REDIS_VOLUME_NAME:-vomesync_redis_data}:/data:ro" \
	-v "$PWD:/backup" \
	alpine:3.20 \
	tar -czf "/backup/${BACKUP_NAME}" -C /data .
```

3. Restart:

```bash
cd docker
docker compose up -d
```

4. Copy the backup off-host and encrypt it (age/gpg/KMS), with retention.

### Restore method (staging first)
**Always rehearse restores in staging/beta before relying on them.**

```bash
cd docker
set -a
. ./.env
set +a

docker compose down

# Recreate the Redis volume (DANGEROUS: deletes current DB)
docker volume rm "${VOMESYNC_REDIS_VOLUME_NAME:-vomesync_redis_data}"
docker volume create --name "${VOMESYNC_REDIS_VOLUME_NAME:-vomesync_redis_data}"

# Restore archive into the volume
docker run --rm \
	-v "${VOMESYNC_REDIS_VOLUME_NAME:-vomesync_redis_data}:/data" \
	-v "$PWD:/backup" \
	alpine:3.20 \
	tar -xzf "/backup/<your-backup>.tgz" -C /data

docker compose up -d
```

Post-restore checks:
- `curl -f "http://localhost:${VOMESYNC_API_HOST_PORT:-3090}/api/health"`
- Verify `GET /api/public-switches` returns plausible results.

### Suggested targets (beta)
- **RPO**: 24h (nightly backups).
- **RTO**: 30–60 minutes (restore + validation).

## Incident response quick steps
- Rotate `REDIS_PASSWORD` (and redeploy).
- Rotate `JWT_SECRET` (invalidates sessions/tokens; schedule as needed).
- Revoke compromised v2 access keys (owner-signed revoke).
- Purge `session_token:*` keys if you suspect website token misuse.


