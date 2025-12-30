# VomeSync Docker Deployment

This directory contains Docker configuration for deploying VomeSync services.

## Quick Start

1. **Copy environment file:**
   ```bash
   cp env.example .env
   ```

2. **Edit configuration:**
   ```bash
   nano .env
   ```
   Update the JWT_SECRET and REDIS_PASSWORD with secure values.

3. **Deploy services:**
   ```bash
   ./scripts/deploy.sh
   ```

## Services

The Docker Compose setup includes:

- **vomesync-redis**: Redis database for state storage and pub/sub
- **vomesync-webserver**: Node.js API server and WebSocket handler
- **vomesync-website**: Static website for public switch directory
- **vomesync-proxy**: Nginx reverse proxy for routing

## Environment Variables

### Required
- `JWT_SECRET`: Secret key for JWT tokens (generate with `openssl rand -base64 32`)
- `REDIS_PASSWORD`: Password for Redis database

### Optional
- `API_DOMAIN`: Domain for API server (default: sync.vome.io)
- `WEBSITE_DOMAIN`: Domain for website (default: sync.vome.io)
- `SSL_CERT_PATH`: Path to SSL certificate
- `SSL_KEY_PATH`: Path to SSL private key
- `CORS_ORIGINS`: Allowed CORS origins (comma-separated)

## SSL/HTTPS Setup

To enable HTTPS:

1. Place your SSL certificate and key files on the host
2. Update the environment variables:
   ```
   ENABLE_SSL=true
   SSL_CERT_PATH=/path/to/certificate.pem
   SSL_KEY_PATH=/path/to/private.key
   ```
3. Uncomment the SSL server blocks in `nginx/proxy.conf`
4. Restart services: `docker-compose restart`

## Management Commands

### Deploy (first time)
```bash
./scripts/deploy.sh deploy
```

### Update services
```bash
./scripts/deploy.sh update       # updates dev + live
./scripts/deploy.sh update-dev   # dev only
./scripts/deploy.sh update-live  # live only
./scripts/deploy.sh push-live    # alias for update-live
```

### View status
```bash
./scripts/deploy.sh status
```

### View logs
```bash
./scripts/deploy.sh logs [service-name]
```

### Stop services
```bash
./scripts/deploy.sh stop
```

### Restart services
```bash
./scripts/deploy.sh restart
```

### Backup data
```bash
./scripts/deploy.sh backup
```

### Clean up (DESTRUCTIVE)
```bash
./scripts/deploy.sh clean
```

## Manual Docker Compose Commands

If you prefer to use Docker Compose directly:

```bash
# Start services
docker compose up -d

# View logs
docker compose logs -f

# Stop services
docker compose down

# Rebuild specific service
docker compose build vomesync-webserver
docker compose up -d vomesync-webserver

# View service status
docker compose ps
```

## Port Mapping

Default port mappings:

- `3090`: API server (HTTP)
- `3001`: WebSocket server
- `8111`: Website (direct)
- `8080`: Nginx proxy (HTTP)
- `8443`: Nginx proxy (HTTPS)

Ports are configurable via `.env`:

- `VOMESYNC_API_HOST_PORT`
- `VOMESYNC_WS_HOST_PORT`
- `VOMESYNC_WEBSITE_HOST_PORT`
- `VOMESYNC_PROXY_HTTP_HOST_PORT`
- `VOMESYNC_PROXY_HTTPS_HOST_PORT`

## Data Persistence

Data is persisted in Docker volumes:

- `redis_data` (defaults to `vomesync_redis_data`): Redis database files (includes public switch directory data)
- `webserver_logs` (defaults to `vomesync_webserver_logs`): Application logs

### Important: do not accidentally switch volumes

If you change the Compose project name or volume names, Docker may create a **new empty Redis volume**, which can look like all switches have disappeared.

- To keep production data stable, set (and keep) `VOMESYNC_REDIS_VOLUME_NAME` in `.env`.
- The deployment script (`docker/scripts/deploy.sh`) will attempt to **auto-adopt** an existing `*_redis_data` volume if it can do so unambiguously.

## Monitoring

### Health Checks

All services include health checks:

```bash
# Check service health
docker compose ps

# View detailed health status
docker inspect $(docker compose ps -q vomesync-webserver) | grep Health -A 10
```

### Logs

View logs for troubleshooting:

```bash
# All services
docker-compose logs

# Specific service
docker-compose logs vomesync-webserver

# Follow logs in real-time
docker-compose logs -f

# Last 100 lines
docker-compose logs --tail=100
```

## Production Considerations

### Security

1. **Change default secrets** in `.env` file
2. **Enable SSL/HTTPS** for production deployment
3. **Firewall rules** - only expose necessary ports
4. **Regular updates** - keep base images updated
5. **Monitor logs** for suspicious activity

### Performance

1. **Resource limits** - add CPU/memory limits to services
2. **Redis persistence** - configure appropriate persistence settings
3. **Nginx caching** - enable caching for static assets
4. **Log rotation** - configure log rotation to prevent disk full

### Backup

1. **Automated backups** - set up regular Redis data backups
2. **Configuration backup** - backup `.env` and custom configs
3. **Test restoration** - regularly test backup restoration

### Monitoring

1. **Health checks** - monitor service health endpoints
2. **Log aggregation** - consider ELK stack or similar
3. **Metrics collection** - consider Prometheus/Grafana
4. **Alerting** - set up alerts for service failures

## Troubleshooting

### Common Issues

1. **Port conflicts**
   ```bash
   # Check what's using port
   ss -ltnp | grep :3090 || true
   
   # Kill process if needed
   kill -9 <PID>
   ```

2. **Permission issues**
   ```bash
   # Fix Docker permissions (if needed)
   sudo usermod -aG docker $USER
   # Logout and login again
   ```

3. **SSL certificate issues**
   ```bash
   # Test certificate
   openssl x509 -in /path/to/cert.pem -text -noout
   
   # Check private key
   openssl rsa -in /path/to/key.pem -check
   ```

4. **Redis connection issues**
   ```bash
   # Test Redis connection
   docker compose exec vomesync-redis redis-cli ping
   ```

5. **WebSocket connection issues**
   ```bash
   # Test WebSocket endpoint
   curl -i -N -H "Connection: Upgrade" -H "Upgrade: websocket" \
        -H "Sec-WebSocket-Key: test" -H "Sec-WebSocket-Version: 13" \
        http://localhost:3001/ws?uid=test-uid
   ```

### Logs Analysis

```bash
# Check for errors
docker compose logs | grep -i error

# Check API requests
docker compose logs vomesync-webserver | grep "POST\|GET"

# Check WebSocket connections
docker compose logs vomesync-webserver | grep -i websocket
```
