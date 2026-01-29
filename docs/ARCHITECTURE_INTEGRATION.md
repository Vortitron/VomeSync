# Home Assistant Integration Architecture (Public‑Safe)

This document details the **Home Assistant integration** design and options flow, without sensitive specifics.

## 1) Components

```mermaid
flowchart TB
	subgraph HA["Home Assistant"]
		ConfigFlow[Config flow]
		OptionsFlow[Options flow]
		Coordinator[Coordinator]
		WSClient[WebSocket client]
		SwitchEnt[Switch entity]
		SensorEnt[Sensor entity]
		LinkedEnt[Linked local entities]
	end

	ConfigFlow --> Coordinator
	OptionsFlow --> Coordinator
	Coordinator --> SwitchEnt
	Coordinator --> SensorEnt
	Coordinator --> LinkedEnt
	Coordinator <--> WSClient
	Coordinator <--> API[Webserver API]
```

## 2) Update Cycle (Simplified)

```mermaid
sequenceDiagram
	participant HA as Home Assistant
	participant Coord as Coordinator
	participant API
	participant WS

	HA->>Coord: Scheduled refresh
	Coord->>API: Fetch owned + subscribed switch data
	API-->>Coord: Switch payloads
	Coord-->>HA: Update entities

	WS-->>Coord: Real‑time state update
	Coord-->>HA: Update entities immediately
```

## 2.1) Config Flow (Initial Setup)

```mermaid
sequenceDiagram
	participant User
	participant HA
	participant API

	User->>HA: Add VomeSync integration
	HA->>API: Generate personal key (optional)
	API-->>HA: Personal key
	HA-->>User: Config entry created
```

## 2.2) Create Switch (v2)

```mermaid
sequenceDiagram
	participant User
	participant HA
	participant API

	User->>HA: Create switch (options flow)
	HA->>API: Create v2 switch (signed)
	API-->>HA: UID + metadata
	HA-->>User: Switch entity created
```

## 2.3) Subscribe to Switch

```mermaid
sequenceDiagram
	participant User
	participant HA
	participant API

	User->>HA: Subscribe to UID
	HA->>API: Fetch switch status
	API-->>HA: State + metadata
	HA-->>User: Sensor / switch entity created
```

## 2.4) Access‑Key Toggle (Subscription)

```mermaid
sequenceDiagram
	participant HA
	participant API

	HA->>API: Toggle with access key
	API-->>HA: New state
```

## 3) Entity Types

- **Switch entity**: created for owners and for subscriptions with an access key.
- **Sensor entity**: used for listen‑only subscriptions (read‑only state).
- **Both** entities share the same device identifiers so they group together.

## 3.1) Ownership vs Subscriptions

- **Owners** can toggle and update metadata through signed v2 requests.
- **Subscriptions** are read‑only unless a delegated access key is provided.
- **Access keys** enable per‑switch toggling without full ownership.

## 4) Options Flow Menu Map

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

## 5) Linking Local Entities (Behaviour)

```mermaid
flowchart LR
	SwitchState[VomeSync state change]
	LocalEntities[Linked local entities]
	MasterEntity[Master local entity]
	SwitchState --> LocalEntities

	MasterEntity -->|Master / OR / AND| SwitchState
```

Notes:
- **Listen‑only switches** hide the direction selector and force **switch → entities**.
- Linking supports **Master**, **OR**, or **AND** behaviour for multiple entities.
- Linked local entities are never shared outside the local HA instance.

## 6) Caching and Startup Behaviour

- The integration caches imported switches in options for fast startup.
- On refresh, the coordinator updates cache with the latest API data.
- If the API is slow or unavailable, cached entities still appear in HA.

## 7) Manage on Website (Owner)

```mermaid
sequenceDiagram
	participant HA
	participant API
	participant Website

	HA->>API: Create short‑lived v2 access key
	API-->>HA: Access key
	HA->>Website: Open link with #accessKey
```

Notes:
- Website management links use **short‑lived** access keys by default.
- “Stay logged in” extends the session up to **30 days** (max).

