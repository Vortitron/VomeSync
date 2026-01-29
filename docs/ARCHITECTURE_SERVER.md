# Server Architecture (Public‑Safe)

This document describes the **server‑side deployment and runtime architecture** at a high level. It intentionally omits sensitive details (internal hostnames, ports, secrets, and provider‑specific metadata).

## 1) System Context

```mermaid
flowchart LR
	Client[Browser / Home Assistant]
	subgraph Edge["Public edge"]
		Proxy[Nginx reverse proxy]
	end
	subgraph Server["VomeSync server"]
		Website[Static website]
		API[Webserver API + WebSocket]
		Redis[(Redis)]
		Media[(Media volume)]
	end

	Client --> Proxy
	Proxy --> Website
	Proxy --> API
	API --> Redis
	API --> Media
```

## 2) Component Responsibilities

- **Reverse proxy**: TLS termination, routing to Website or API/WebSocket.
- **Website**: Static HTML/CSS/JS directory UI.
- **Webserver API**: REST + WebSocket endpoints, auth/permissions, metadata handling.
- **Redis**: Switch state, counters, and key‑metadata lookup (hashed IDs).
- **Media volume**: Stored WebP icons/banners served via the API.

## 2.1) Webserver Module Map (Public‑Safe)

```mermaid
flowchart LR
	Request[HTTP request]
	CORS[CORS / headers]
	Rate[Rate limit]
	Validate[Schema validation]
	Auth[Auth / permissions]
	Handler[Route handler]
	Redis[(Redis)]
	Media[(Media)]
	WS[WebSocket broadcaster]
	Log[Logger (redaction)]

	Request --> CORS --> Rate --> Validate --> Auth --> Handler
	Handler --> Redis
	Handler --> Media
	Handler --> WS
	Handler --> Log
```

## 3) Request and Update Flow

```mermaid
sequenceDiagram
	participant Client
	participant Proxy
	participant API
	participant Redis
	participant WS

	Client->>Proxy: API request
	Proxy->>API: Forward request
	API->>Redis: Read/write switch data
	API-->>Client: Response

	API-->>WS: Publish state update
	WS-->>Client: Real‑time event
```

## 3.1) Routing Summary (Public‑Safe)

- **Website**: public static pages for browsing and switch detail views.
- **API**: JSON + multipart endpoints for switch lifecycle and metadata.
- **WebSocket**: per‑switch subscriptions for real‑time updates.
- **Media**: WebP assets served from the API origin.

## 3.2) Request Pipeline (Public‑Safe)

```mermaid
sequenceDiagram
	participant Client
	participant API
	participant Auth
	participant Redis
	participant WS

	Client->>API: Request (JSON / multipart)
	API->>Auth: Validate + authorise
	Auth-->>API: OK / error
	API->>Redis: Read / write
	API-->>WS: Publish event (if state changed)
	API-->>Client: Response
```

## 4) WebSocket Lifecycle (High‑Level)

```mermaid
stateDiagram-v2
	[*] --> Connecting
	Connecting --> Subscribed: Subscribe to UID
	Subscribed --> Receiving: State updates
	Receiving --> Subscribed: Idle / heartbeat
	Subscribed --> Reconnecting: Connection lost
	Reconnecting --> Subscribed: Reconnect + resubscribe
	Reconnecting --> [*]: Shutdown
```

## 5) Data Persistence (Public‑Safe)

High‑level data groups stored in Redis:
- **Switch state**: current state, timestamps, counters.
- **Ownership metadata**: v2 owner identifiers and switch public key references.
- **Access keys**: delegated key metadata (stored by hashed IDs).
- **Public index**: list of public UIDs for discovery.

```mermaid
flowchart TB
	SwitchData[Switch state + counters]
	Ownership[Owner metadata]
	AccessKeys[Access key metadata]
	PublicIndex[Public UID index]
	Comments[Comments / activity log]

	SwitchData --- Ownership
	SwitchData --- AccessKeys
	SwitchData --- Comments
	PublicIndex --- SwitchData
```

## 6) Trust Boundaries (Public‑Safe)

```mermaid
flowchart LR
	Public[Public clients]
	Edge[Reverse proxy]
	API[API + WebSocket]
	Data[(Redis / Media)]

	Public --> Edge --> API --> Data
```

## 7) Media Ingestion (Icons / Banners)

```mermaid
flowchart LR
	Client --> API
	API --> Validate[URL + size checks]
	Validate --> Convert[Convert to WebP]
	Convert --> Store[Write to media volume]
	Store --> Serve[Serve via API media path]
```

## 8) Operational Considerations (Public‑Safe)

- The server is designed to be stateless aside from Redis + media volume.
- Redis and media persistence should be backed up regularly.
- WebSocket fans out to all subscribed clients for real‑time state updates.

