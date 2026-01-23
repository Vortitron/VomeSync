# VomeSync Setup Guide

This guide covers setting up VomeSync for development and production deployment.

## Table of Contents

1. [Development Setup](#development-setup)
2. [Production Deployment](#production-deployment)
3. [Home Assistant Integration](#home-assistant-integration)
4. [Configuration](#configuration)
5. [SSL/HTTPS Setup](#sslhttps-setup)
6. [Monitoring](#monitoring)
7. [Troubleshooting](#troubleshooting)
8. [Operations (pre-beta + backups)](OPERATIONS.md)

## Development Setup

### Prerequisites

- Node.js 18+ and npm
- Redis server
- Docker and Docker Compose (optional)
- Git

### Local Development

1. **Clone the repository:**
   ```bash
   git clone https://github.com/Vortitron/VomeSync.git
   cd vomesync
   ```

2. **Install webserver dependencies:**
   ```bash
   cd webserver
   npm install
   cp env.example .env
   ```

3. **Configure environment:**
   Edit `webserver/.env`:
   ```env
   NODE_ENV=development
   PORT=3000
   WS_PORT=3001
   REDIS_HOST=localhost
   REDIS_PORT=6379
   JWT_SECRET=dev-secret-change-in-production
   ```

4. **Start Redis:**
   ```bash
   # Using Docker
   docker run -d --name redis -p 6379:6379 redis:alpine
   
   # Or install locally (Ubuntu/Debian)
   sudo apt install redis-server
   sudo systemctl start redis-server
   ```

5. **Start the webserver:**
   ```bash
   cd webserver
   npm run dev
   ```

6. **Serve the website:**
   ```bash
   cd ../website
   python3 -m http.server 8080
   # Or use any static file server
   ```

7. **Test the setup:**
   - API: http://localhost:3000/api/health
   - Website: http://localhost:8080
   - WebSocket: ws://localhost:3001/ws?uid=test

### Using Docker for Development

1. **Use development Docker Compose:**
   ```bash
   cd docker
   cp env.example .env
   # Edit .env for development settings
   docker compose up -d
   ```

2. **Development with hot reload:**
   ```bash
   # Mount source code for hot reload
   docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d
   ```

## Production Deployment

### Option 1: Docker Deployment (Recommended)

1. **Prepare the server:**
   ```bash
   # Install Docker and Docker Compose
   curl -fsSL https://get.docker.com | sh
   sudo usermod -aG docker $USER
   
   # Install Docker Compose
   sudo curl -L "https://github.com/docker/compose/releases/download/v2.20.0/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose
   sudo chmod +x /usr/local/bin/docker-compose
   ```

2. **Deploy VomeSync:**
   ```bash
   git clone https://github.com/Vortitron/VomeSync.git
   cd vomesync/docker
   
   # Configure environment
   cp env.example .env
   nano .env  # Edit configuration
   
   # Deploy
   ./scripts/deploy.sh
   ```

#### Persistence note (important for production)

VomeSync persists switch directory data in a Docker volume. If you accidentally change the Compose project name or volume name, Docker may create a **new empty Redis volume**, which can look like “all switches disappeared”.

- Keep `VOMESYNC_REDIS_VOLUME_NAME` stable in `docker/.env` (recommended).
- `docker/scripts/deploy.sh` will attempt to auto-adopt an existing `*_redis_data` volume when possible.

3. **Configure reverse proxy (optional):**
   If you're not using the included nginx proxy, configure your web server:

   **Nginx:**
   ```nginx
   server {
       listen 80;
       server_name sync.vome.io;
       
       location /api/ {
           proxy_pass http://localhost:3090;
           proxy_set_header Host $host;
           proxy_set_header X-Real-IP $remote_addr;
       }
       
       location /ws {
           proxy_pass http://localhost:3001;
           proxy_http_version 1.1;
           proxy_set_header Upgrade $http_upgrade;
           proxy_set_header Connection "upgrade";
       }
   }
   ```

   **Apache:**
   ```apache
   <VirtualHost *:80>
       ServerName sync.vome.io
       
       ProxyPreserveHost On
       ProxyPass /api/ http://localhost:3090/api/
       ProxyPassReverse /api/ http://localhost:3090/api/
       
       ProxyPass /ws ws://localhost:3001/ws
       ProxyPassReverse /ws ws://localhost:3001/ws
   </VirtualHost>
   ```

### Option 2: Manual Installation

1. **Install Node.js and Redis:**
   ```bash
   # Ubuntu/Debian
   curl -fsSL https://deb.nodesource.com/setup_18.x | sudo -E bash -
   sudo apt-get install -y nodejs redis-server
   
   # CentOS/RHEL
   curl -fsSL https://rpm.nodesource.com/setup_18.x | sudo bash -
   sudo yum install -y nodejs redis
   ```

2. **Setup application:**
   ```bash
   # Create user and directories
   sudo useradd -r -s /bin/false vomesync
   sudo mkdir -p /opt/vomesync
   sudo chown vomesync:vomesync /opt/vomesync
   
   # Deploy code
   cd /opt/vomesync
   git clone https://github.com/Vortitron/VomeSync.git .
   cd webserver
   npm ci --production
   ```

3. **Configure systemd service:**
   ```bash
   sudo tee /etc/systemd/system/vomesync.service > /dev/null <<EOF
   [Unit]
   Description=VomeSync WebServer
   After=network.target redis.service
   
   [Service]
   Type=simple
   User=vomesync
   WorkingDirectory=/opt/vomesync/webserver
   ExecStart=/usr/bin/node src/server.js
   Restart=always
   RestartSec=10
   Environment=NODE_ENV=production
   EnvironmentFile=/opt/vomesync/webserver/.env
   
   [Install]
   WantedBy=multi-user.target
   EOF
   
   sudo systemctl enable vomesync
   sudo systemctl start vomesync
   ```

## Home Assistant Integration

### HACS Installation

1. **Install HACS** (if not already installed):
   - Follow instructions at https://hacs.xyz/

2. **Add VomeSync repository:**
   - HACS → Integrations → Three dots menu → Custom repositories
	- Add: `https://github.com/Vortitron/VomeSync`
   - Category: Integration

3. **Install VomeSync:**
   - Search for "VomeSync" in HACS
   - Click Install
   - Restart Home Assistant

### Manual Installation

1. **Download integration:**
   ```bash
   cd /config/custom_components
	git clone https://github.com/Vortitron/VomeSync.git
	mv VomeSync/custom_components/vomesync ./
   rm -rf vomesync
   ```

2. **Restart Home Assistant**

### Configuration

1. **Add integration:**
   - Settings → Devices & Services
   - Add Integration → Search "VomeSync"

2. **Configure defaults (optional):**
   - Leave **Generate new user key** ticked to create a signing key automatically
   - Leave **Default URLs** ticked to use sync.vome.io (WebSocket auto-derives)
   - Untick either option to enter a signing key or custom server/WebSocket URLs
   - Optional: paste a switch UID to subscribe immediately

3. **Create or subscribe switches:**
   - Integration settings → Configure
   - Create Switch or Subscribe to Switch
   - Manage on website links generate a session key (metadata/toggle/comment) for the web UI; regenerate in HA if needed

## Configuration

### Environment Variables

**Webserver Configuration (.env):**
```env
# Server
NODE_ENV=production
PORT=3000
WS_PORT=3001

# Redis
REDIS_HOST=localhost
REDIS_PORT=6379
REDIS_PASSWORD=your-password
REDIS_DB=0

# Security
JWT_SECRET=your-jwt-secret-32-chars-min
RATE_LIMIT_WINDOW_MS=900000
RATE_LIMIT_MAX_REQUESTS=100

# CORS
CORS_ORIGINS=https://sync.vome.io,http://localhost:8123

# SSL (optional)
SSL_CERT_PATH=/path/to/cert.pem
SSL_KEY_PATH=/path/to/private.key

# Privacy
ENABLE_ANALYTICS=true
DIFFERENTIAL_PRIVACY_EPSILON=1.0

# Logging
LOG_LEVEL=info
LOG_FILE=logs/vomesync.log
```

### Home Assistant Configuration

**configuration.yaml** (optional, for YAML-based config):
```yaml
# VomeSync switches (managed via UI typically)
switch:
  - platform: vomesync
    name: "Community Light"
    unique_id: "remote_switch_1"
    personal_key: !secret vomesync_key
    uid: "your-switch-uuid"
    mode: "public"
    description: "Festival Light Event"
    location: "Stockholm"
    category: "Community"
    publicize: true
```

**secrets.yaml:**
```yaml
vomesync_key: "your-personal-key-uuid"
```

## SSL/HTTPS Setup

### Let's Encrypt (Recommended)

1. **Install Certbot:**
   ```bash
   sudo apt install certbot
   ```

2. **Obtain certificates:**
   ```bash
   sudo certbot certonly --standalone -d sync.vome.io
   ```

3. **Update configuration:**
   ```env
   ENABLE_SSL=true
   SSL_CERT_PATH=/etc/letsencrypt/live/sync.vome.io/fullchain.pem
   SSL_KEY_PATH=/etc/letsencrypt/live/sync.vome.io/privkey.pem
   ```

4. **Setup auto-renewal:**
   ```bash
   sudo crontab -e
   # Add: 0 12 * * * /usr/bin/certbot renew --quiet
   ```

### Custom Certificate

1. **Generate certificate:**
   ```bash
   openssl req -x509 -newkey rsa:4096 -keyout private.key -out certificate.pem -days 365 -nodes
   ```

2. **Set permissions:**
   ```bash
   chmod 600 private.key
   chmod 644 certificate.pem
   ```

3. **Update configuration:**
   ```env
   ENABLE_SSL=true
   SSL_CERT_PATH=/path/to/certificate.pem
   SSL_KEY_PATH=/path/to/private.key
   ```

## Monitoring

### Health Checks

1. **API Health:**
   ```bash
   curl https://sync.vome.io/api/health
   ```

2. **WebSocket Health:**
   ```bash
   # Test WebSocket connection
   wscat -c wss://sync.vome.io/ws?uid=test-uid
   ```

3. **Redis Health:**
   ```bash
   redis-cli ping
   ```

### Logging

1. **Application logs:**
   ```bash
   # Docker
   docker compose logs -f vomesync-webserver
   
   # Systemd
   journalctl -u vomesync -f
   
   # File
   tail -f /opt/vomesync/webserver/logs/vomesync.log
   ```

2. **Nginx logs:**
   ```bash
   tail -f /var/log/nginx/access.log
   tail -f /var/log/nginx/error.log
   ```

### Metrics

1. **Server stats:**
   ```bash
   curl https://sync.vome.io/api/stats
   ```

2. **Docker stats:**
   ```bash
   docker stats
   ```

3. **System resources:**
   ```bash
   htop
   df -h
   free -h
   ```

## Troubleshooting

### Common Issues

1. **Port already in use:**
   ```bash
   # Check the port you configured (e.g. 3090 for API, 3001 for WS)
   netstat -tulpn | grep :3090
   ```

2. **Redis connection failed:**
   ```bash
   # Check Redis status
   redis-cli ping
   
   # Check Redis logs
   sudo journalctl -u redis -f
   ```

3. **WebSocket connection issues:**
   ```bash
   # Test with curl
   curl -i -N -H "Connection: Upgrade" -H "Upgrade: websocket" \
        -H "Sec-WebSocket-Key: test" -H "Sec-WebSocket-Version: 13" \
        http://localhost:3001/ws?uid=test-uid
   ```

4. **SSL certificate errors:**
   ```bash
   # Check certificate validity
   openssl x509 -in cert.pem -text -noout
   
   # Test SSL connection
   openssl s_client -connect sync.vome.io:443
   ```

5. **CORS issues:**
   - Check `CORS_ORIGINS` environment variable
   - Verify Home Assistant URL is included
   - Check browser developer console for errors

### Log Analysis

1. **API request errors:**
   ```bash
   grep -i error /var/log/vomesync/app.log
   ```

2. **Rate limiting:**
   ```bash
   grep "Rate limit" /var/log/vomesync/app.log
   ```

3. **Authentication failures:**
   ```bash
   grep -i "unauthorized\|invalid.*key" /var/log/vomesync/app.log
   ```

### Performance Issues

1. **High memory usage:**
   ```bash
   # Check Node.js heap usage
   node --inspect src/server.js
   
   # Monitor with htop
   htop
   ```

2. **Redis performance:**
   ```bash
   # Monitor Redis
   redis-cli monitor
   
   # Check slow queries
   redis-cli slowlog get 10
   ```

3. **Network issues:**
   ```bash
   # Test connection speed
   curl -w "@curl-format.txt" -o /dev/null -s https://sync.vome.io/api/health
   ```

### Getting Help

1. **Check logs first** - most issues can be diagnosed from logs
2. **GitHub Issues** - https://github.com/Vortitron/VomeSync/issues
