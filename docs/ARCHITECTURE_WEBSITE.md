# Website Architecture (Public‑Safe)

This document outlines the **public website** behaviour and data flow. It intentionally avoids sensitive details.

## 1) Page States

```mermaid
flowchart LR
	Directory[Public directory]
	SwitchDetail[Switch detail]
	QuickView[Quick view modal]

	Directory -->|card click| QuickView
	Directory -->|details| SwitchDetail
	QuickView -->|close| Directory
	SwitchDetail -->|back| Directory
```

## 1.1) UI Component Map (Detail Page)

```mermaid
flowchart TB
	Hero[Hero banner]
	Auth[Authenticate panel]
	Toggle[Toggle dialog]
	Comments[Comments + activity]
	Manage[Appearance / owner tools]

	Hero --> Auth
	Hero --> Toggle
	Hero --> Comments
	Hero --> Manage
```

## 2) Data Flow (Directory + Switch Detail)

```mermaid
sequenceDiagram
	participant Browser
	participant API
	participant WS

	Browser->>API: Load public switch list
	API-->>Browser: Switch list + metadata
	Browser->>API: Load switch detail
	API-->>Browser: Switch detail payload
	WS-->>Browser: Real‑time state updates
```

## 2.1) Update Strategy

- Initial directory load uses a single API call for public switches.
- Incremental updates are applied in place to avoid flicker.
- WebSocket messages update visible switches in real time.

## 2.2) Real‑Time Update Flow

```mermaid
sequenceDiagram
	participant Browser
	participant WS

	WS-->>Browser: State update
	Browser->>Browser: Update card + hero in place
```

## 3) Authentication (Per‑Switch)

```mermaid
flowchart TD
	KeyInput[Authenticate (enter key)]
	Validate[Validate permissions]
	ReadOnly[Read‑only]
	Write[Toggle / Comment / Manage]

	KeyInput --> Validate
	Validate --> ReadOnly
	Validate --> Write
```

Notes:
- Authentication applies **per switch** rather than a global account.
- Keys are stored in browser session storage and can be forgotten at any time.
- Optional “Stay logged in” uses local storage and expires server‑side (max 30 days).

## 3.1) Actions Gated by Auth

- **Read‑only**: view public switch details and activity.
- **Write**: toggle, comment, and appearance updates (if permitted).

## 3.2) Toggle Flow (Public‑Safe)

```mermaid
sequenceDiagram
	participant User
	participant UI
	participant API

	User->>UI: Click status
	alt Not authenticated
		UI->>User: Prompt key
	end
	UI->>API: Toggle (if permitted)
	API-->>UI: New state
```

## 4) Media Flow (Icons / Banners)

```mermaid
flowchart LR
	Browser --> API[Webserver API]
	API --> Validate[Validate + rehost]
	Validate --> WebP[Convert to WebP]
	WebP --> Serve[Serve via API media path]
```

## 4.1) Appearance Update Flow

```mermaid
sequenceDiagram
	participant User
	participant UI
	participant API

	User->>UI: Select file or URL
	UI->>API: Upload / URL ingest
	API-->>UI: New icon/banner URL
```

## 5) Quick View Modal

```mermaid
flowchart LR
	Card[Switch card]
	QuickView[Quick view modal]
	Actions[Copy UID / Add to HA / Details]

	Card --> QuickView
	QuickView --> Actions
```

## 6) Deep‑Link and Share Flows

- Switch detail URLs are shareable.
- Manage‑on‑website deep links are time‑limited and regenerate when needed.

