# VomeSync Project Structure

```
VomeSync/
├── README.md                     # Main project documentation
├── LICENSE                       # MIT license
├── PROJECT_STRUCTURE.md          # This file
│
├── webserver/                    # Node.js API server and WebSocket handler
│   ├── package.json             # Dependencies and scripts
│   ├── env.example              # Environment configuration template
│   ├── Dockerfile               # Container build instructions
│   ├── .dockerignore           # Docker build exclusions
│   │
│   ├── src/                     # Source code
│   │   ├── server.js           # Main server entry point
│   │   ├── config/
│   │   │   └── config.js       # Environment configuration
│   │   ├── utils/
│   │   │   ├── logger.js       # Winston logging setup
│   │   │   ├── redis.js        # Redis client and operations
│   │   │   ├── auth.js         # Authentication and rate limiting
│   │   │   └── validation.js   # Request validation schemas
│   │   ├── websocket/
│   │   │   └── manager.js      # WebSocket connection management
│   │   └── routes/
│   │       └── api.js          # REST API endpoints
│   │
│   └── tests/                   # Unit and integration tests (to be added)
│
├── hacs-addon/                  # Home Assistant Custom Component
│   ├── hacs.json               # HACS integration metadata
│   ├── README.md               # Integration documentation
│   │
│   └── custom_components/vomesync/
│       ├── __init__.py         # Integration setup
│       ├── manifest.json       # Integration manifest
│       ├── const.py           # Constants and configuration
│       ├── config_flow.py     # Configuration UI flow
│       ├── coordinator.py     # Data update coordinator
│       ├── api_client.py      # API communication client
│       ├── websocket_client.py # WebSocket client
│       ├── switch.py          # Switch platform entities
│       ├── sensor.py          # Sensor platform entities
│       └── services.yaml      # Service definitions
│
├── website/                     # Public switch directory website
│   ├── index.html              # Main website page
│   ├── styles.css              # Responsive CSS styling
│   └── script.js               # JavaScript for API interaction
│
├── docker/                      # Docker deployment configuration
│   ├── docker-compose.yml      # Multi-service orchestration
│   ├── env.example             # Environment variables template
│   ├── README.md               # Docker deployment guide
│   │
│   ├── nginx/                  # Nginx proxy configuration
│   │   ├── proxy.conf          # Reverse proxy rules
│   │   └── website.conf        # Static website serving
│   │
│   └── scripts/
│       └── deploy.sh           # Automated deployment script
│
└── docs/                        # Project documentation
    ├── API.md                   # REST and WebSocket API reference
    └── SETUP.md                 # Installation and configuration guide
```

## Planned Components

### Official Home Assistant Add-on
- Location: `addons/vome/` (+ `addons/repository.yaml` for the Add-on Store)
- Installs the shared `custom_components/vomesync` tree (staged by `addons/vome/build.sh`)
- Ingress tree control panel for remote access + LAN tunnels (same services as HACS options)
- CI/release: `jenkins/pipelines/Jenkinsfile.vome-addon-ci` and `Jenkinsfile.vome-addon-release`, wired in VomeHome `jenkins/casc.yaml` as `VomeSync/vome-addon-ci` / `vome-addon-release`
- Future: Guacamole / other companions as extra s6 services in the same add-on
- HACS remains the light path for switches + relay; the add-on is the full packaging + control UI path

### LAN path tunnels
- Friendly-domain paths `/t/<slug>/…` → LAN `host:port` (see `lan_routes.py`)
- Configured in integration options when linked to Vome Home
- Independent of full-UI HA forwarding

## Component Overview

### Webserver (`/webserver/`)
**Purpose**: Core backend service handling API requests and WebSocket connections

**Key Files**:
- `src/server.js` - Main application entry point with Express setup
- `src/utils/redis.js` - Redis operations for state storage and pub/sub
- `src/websocket/manager.js` - WebSocket connection lifecycle management
- `src/routes/api.js` - REST API endpoints for switch operations
- `src/utils/auth.js` - Authentication, rate limiting, and security

**Features**:
- JWT-based authentication with personal keys
- Real-time WebSocket broadcasting
- Redis pub/sub for scalable messaging
- Rate limiting and security headers
- Comprehensive error handling and logging

### Home Assistant Integration (`/hacs-addon/`)
**Purpose**: Custom component for seamless Home Assistant integration

**Key Files**:
- `__init__.py` - Integration setup and platform loading
- `config_flow.py` - User-friendly configuration interface
- `coordinator.py` - Central data management and API coordination
- `switch.py` - Switch entities for toggleable switches
- `sensor.py` - Sensor entities for read-only monitoring
- `websocket_client.py` - Real-time state synchronisation

**Features**:
- Native Home Assistant entities (switch/sensor)
- Real-time WebSocket updates
- Config flow for easy setup
- Support for both owned and subscribed switches
- Automatic reconnection and error handling

### Public Website (`/website/`)
**Purpose**: Community directory for discovering public switches

**Key Files**:
- `index.html` - Single-page application structure
- `styles.css` - Modern, responsive design
- `script.js` - API integration and interactive features

**Features**:
- Real-time switch status display
- Search and filter functionality
- One-click UID copying
- Mobile-responsive design
- Auto-refreshing content

### Docker Infrastructure (`/docker/`)
**Purpose**: Production-ready deployment orchestration

**Key Files**:
- `docker-compose.yml` - Multi-service configuration
- `nginx/proxy.conf` - Reverse proxy and SSL termination
- `scripts/deploy.sh` - Automated deployment management

**Features**:
- Multi-service orchestration (API, WebSocket, Website, Redis)
- Nginx reverse proxy with SSL support
- Health checks and automatic restarts
- Volume persistence for data and logs
- Production-ready security configuration

## Data Flow

1. **Switch Creation**:
   - Home Assistant → API Server → Redis → WebSocket broadcast

2. **Switch Toggle**:
   - Home Assistant → API Server → Redis → WebSocket → All subscribers

3. **Real-time Updates**:
   - WebSocket client → Redis pub/sub → WebSocket manager → Connected clients

4. **Public Discovery**:
   - Website → API Server → Redis → Public switch list

## Security Architecture

- **Authentication**: Personal key (UUID) for API access
- **Rate Limiting**: Per-IP and per-key request throttling
- **Data Privacy**: Optional anonymised metadata only
- **Transport Security**: TLS/WSS for all connections
- **Input Validation**: Joi schemas for all API inputs

## Deployment Options

1. **Docker Compose** (Recommended): Complete stack with one command
2. **Manual Installation**: Individual service deployment
3. **Development Mode**: Local development with hot reload
4. **Kubernetes**: Scalable cloud deployment (configuration to be added)

## Development Workflow

1. **Local Development**: Use Docker Compose with development overrides
2. **Testing**: Automated tests for API, WebSocket, and integration
3. **Building**: Multi-stage Docker builds for optimisation
4. **Deployment**: Automated CI/CD pipeline (GitHub Actions to be added)

## Monitoring and Observability

- **Health Checks**: Built-in endpoints for all services
- **Logging**: Structured JSON logs with Winston
- **Metrics**: Service statistics via API endpoints
- **Error Tracking**: Comprehensive error handling and reporting

This architecture provides a scalable, maintainable foundation for the VomeSync ecosystem while ensuring security, performance, and ease of deployment.
