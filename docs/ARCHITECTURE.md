# Architecture Diagrams (Public-Safe)

These diagrams are intentionally high‑level and **omit sensitive details** (internal hostnames, ports, secrets, and infra-specific deployment metadata). Use them as a public overview only.

## Detailed architecture docs

- [VomeSync-server](https://github.com/Vortitron/VomeSync-server) — sync.vome.io API, website, Docker (`docs/ARCHITECTURE_SERVER.md`, `docs/API.md`, `docs/ARCHITECTURE_WEBSITE.md` in that repo)
- `docs/ARCHITECTURE_INTEGRATION.md` — Home Assistant integration + menu map

## 1) Deployment Topology (Server + Backend Apps)

```mermaid
flowchart LR
	Browser[Web browser]
	HA[Home Assistant]

	subgraph Edge["Public edge"]
		Proxy[Nginx reverse proxy]
	end

	subgraph Server["VomeSync server"]
		Website[Website (static assets)]
		API[Webserver API + WebSocket]
		Redis[(Redis)]
		Media[(Media volume)]
	end

	Browser -- HTTPS --> Proxy
	HA -- HTTPS / WSS --> Proxy
	Proxy --> Website
	Proxy --> API
	API --> Redis
	API --> Media
```

## 2) API Flow (High‑Level)

```mermaid
sequenceDiagram
	participant Client
	participant API as Webserver API
	participant Redis
	participant WS as WebSocket layer

	Client->>API: Create switch (v2, signed)
	API->>Redis: Store switch metadata + ownership
	API-->>Client: UID + metadata

	Client->>API: Toggle switch (delegated access key)
	API->>Redis: Update state + counters
	API-->>WS: Publish state update
	WS-->>Client: Real‑time state updates
```

## 3) Home Assistant Integration (Components)

```mermaid
flowchart TB
	subgraph HA["Home Assistant"]
		Flow[Config + options flow]
		Coord[Coordinator]
		SwitchEnt[Switch entity]
		SensorEnt[Sensor entity]
		LocalEnt[Linked local entities]
	end

	Flow --> Coord
	Coord --> SwitchEnt
	Coord --> SensorEnt
	SwitchEnt -->|toggle| Coord
	Coord --> LocalEnt

	Coord <--> API[Webserver API]
	Coord <--> WS[WebSocket client]
```

## 4) Integration Menu Map (Options Flow)

```mermaid
flowchart TD
	Init[Options menu]
	More[More...]
	Manage[Manage switches]
	ActionMenu[Switch actions]

	Init --> Create[Create switch]
	Init --> Subscribe[Subscribe to switch]
	Init --> Manage
	Init --> More

	Manage --> ActionMenu
	ActionMenu --> View[View details]
	ActionMenu --> Edit[Edit settings (owners)]
	ActionMenu --> Keys[Access keys (owners, v2)]
	ActionMenu --> Website[Manage on website (owners, v2)]
	ActionMenu --> Link[Link local entities]
	ActionMenu --> Delete[Delete switch (owners)]
	ActionMenu --> Remove[Remove from this installation]

	More --> Backup[Backup signing key]
	More --> Import[Import switches]
	More --> Reannounce[Re‑announce owned switches]
	More --> Cleanup[Clean up orphaned devices]
	More --> EditURLs[Edit connection URLs]
	More --> Back[Back]
	Back --> Init
```

## 5) Media Ingestion (Icons / Banners)

```mermaid
flowchart LR
	Client[Website / HA] --> API[Webserver API]
	API --> Validate[URL + size checks]
	Validate --> Convert[Convert to WebP]
	Convert --> Store[Save to media volume]
	Store --> Serve[Serve via /api/media]
```

## 6) Key Handling (Public‑Safe View)

```mermaid
flowchart LR
	Client --> API[Webserver API]
	API --> Hash[Derive hashed key IDs]
	Hash --> Redis[(Redis)]
	API --> Log[Logger (redaction)]
```

