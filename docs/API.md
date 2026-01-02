# VomeSync API Documentation

Base URL: `https://sync.vome.io`

## Authentication

Most endpoints require a personal key for authentication. Include it in:
- Request body as `personalKey`
- Header as `X-Personal-Key`

### V2 keypair authentication (recommended)

V2 endpoints use **Ed25519 signatures** instead of server-issued keys.

- **Owner key**: a master private key stored locally (e.g. in the Home Assistant integration config entry).
- **Switch subkeys**: deterministic per-switch keys derived from the owner key + an integer index.
- **Switch UID**: deterministically derived from the switch public key and uses the prefix **`vs_`**.

This enables:

- Stable switch UIDs across servers (migrate without changing UIDs)
- Recovery after catastrophic DB loss (owner can re-create/announce the same UIDs)

V2 requests include:

- `ts`: client timestamp (ms)
- `nonce`: unique per request (replay protection)
- Signatures over a canonical JSON payload (sorted keys, compact JSON)

## Rate Limiting

- Most endpoints: 100 requests per 15 minutes
- Key generation: 10 requests per hour
- Switch creation: 20 requests per hour

Rate limit headers are included in responses:
- `X-RateLimit-Limit`: Maximum requests allowed
- `X-RateLimit-Remaining`: Requests remaining in window
- `X-RateLimit-Reset`: When limit resets (ISO 8601)

## Endpoints

## V2 Endpoints (keypair identity)

### Create Switch (v2)

Create a deterministic switch (UID is derived by the server from `switchPubKey`).

**POST** `/api/v2/switch`

**Request Body:**
```json
{
  "ownerPubKey": "base64url-raw-ed25519-pubkey",
  "switchPubKey": "base64url-raw-ed25519-pubkey",
  "index": 0,
  "ts": 1712345678901,
  "nonce": "random-string",
  "sigOwner": "base64url-ed25519-signature",
  "sigSwitch": "base64url-ed25519-signature",
  "description": "",
  "location": "",
  "category": "Other",
  "publicize": false,
  "link": "",
  "iconUrl": "https://example.com/icon.png",
  "bannerUrl": "https://example.com/banner.jpg",
  "captchaToken": ""
}
```

**Response:**
```json
{
  "success": true,
  "data": {
    "uid": "vs_...",
    "authVersion": 2,
    "index": 0,
    "state": false,
    "publicize": false,
    "iconUrl": "",
    "bannerUrl": ""
  }
}
```

### Update Switch Metadata (v2)

Update a v2 switch's metadata (signed by the owner key).

**POST** `/api/v2/switch/{uid}`

**Request Body (example):**
```json
{
  "ownerPubKey": "base64url-raw-ed25519-pubkey",
  "ts": 1712345678901,
  "nonce": "random-string",
  "sigOwner": "base64url-ed25519-signature",
  "description": "My Switch",
  "link": "https://example.com",
  "iconUrl": "https://example.com/icon.png",
  "bannerUrl": "https://example.com/banner.jpg"
}
```

Notes:
- You must include **at least one** metadata field to update.
- To clear `iconUrl`/`bannerUrl`, send an empty string.
- When setting `"publicize": true`, the server may require `captchaToken` if CAPTCHA is configured.

### Access Keys (v2 delegation)

Access keys are **server-generated API keys** scoped to a single v2 switch. Owners can create them via an owner-signed request and share them with other people.

#### Create access key (v2)

**POST** `/api/v2/switch/{uid}/access-keys`

**Request Body (example):**
```json
{
  "ownerPubKey": "base64url-raw-ed25519-pubkey",
  "ts": 1712345678901,
  "nonce": "random-string",
  "sigOwner": "base64url-ed25519-signature",
  "name": "Friend",
  "permissions": ["toggle", "comment", "metadata"]
}
```

**Response (example):**
```json
{
  "success": true,
  "data": {
    "apiKey": "uuid-v4-string",
    "name": "Friend",
    "permissions": ["toggle", "comment", "metadata"],
    "createdAt": 1712345678901
  }
}
```

Notes:
- `permissions` can include:
  - `toggle`: toggle via access key
  - `comment`: comment via access key
  - `metadata`: update non-publicising metadata via access key (icon/banner/link/etc)

#### List access keys (v2)

**POST** `/api/v2/switch/{uid}/access-keys/list`

#### Revoke access key (v2)

**POST** `/api/v2/switch/{uid}/access-keys/revoke`

#### Toggle using access key (v2)

**POST** `/api/v2/switch/{uid}/toggle`

Header:
```
X-Api-Key: uuid-v4-string
```

#### Comment using access key (v2)

**POST** `/api/v2/switch/{uid}/comment`

Header:
```
X-Api-Key: uuid-v4-string
```

Body:
```json
{ "comment": "Reason for change" }
```

#### Update metadata using access key (v2)

Update a switch using a delegated access key (no signatures). This endpoint is intentionally limited and does **not** allow setting `publicize`.

**POST** `/api/v2/switch/{uid}/metadata`

Header:
```
X-Api-Key: uuid-v4-string
```

Body (example):
```json
{
  "iconUrl": "https://example.com/icon.png",
  "bannerUrl": "https://example.com/banner.jpg",
  "link": "https://example.com"
}
```

### Get My Switches (v2)

List switches owned by the signing owner key.

**POST** `/api/v2/my-switches`

**Request Body:**
```json
{
  "ownerPubKey": "base64url-raw-ed25519-pubkey",
  "ts": 1712345678901,
  "nonce": "random-string",
  "sigOwner": "base64url-ed25519-signature"
}
```

### Set Switch State (v2)

Set a switch state and optionally pass parameters (e.g. light colour/brightness). Parameters are forwarded to WebSocket subscribers.

**POST** `/api/v2/switch/{uid}/state`

**Request Body:**
```json
{
  "ts": 1712345678901,
  "nonce": "random-string",
  "sigSwitch": "base64url-ed25519-signature",
  "state": true,
  "params": {
    "rgb_color": [10, 20, 30],
    "brightness": 200
  }
}
```

### Generate Personal Key

Generate a new personal key for authentication.

**POST** `/api/generate-key`

**Request Body:**
```json
{
  "consent": true
}
```

**Response:**
```json
{
  "success": true,
  "data": {
    "personalKey": "uuid-v4-string",
    "jwt": "optional-jwt-token",
    "expiresIn": "1 year",
    "message": "Store this key securely - it cannot be recovered if lost"
  }
}
```

### Create Switch

Create a new virtual switch.

**POST** `/api/create-switch`

**Authentication:** Required

**Request Body:**
```json
{
  "personalKey": "your-personal-key",
  "description": "Festival Light Event",
  "location": "Stockholm",
  "category": "Community",
  "publicize": true
}
```

**Parameters:**
- `description` (string, optional): Human-readable description
- `location` (string, optional): City-level location for privacy
- `category` (string, optional): One of `Community`, `Personal`, `Event`, `Test`, `Other`
- `publicize` (boolean, optional): Whether to list publicly

**Response:**
```json
{
  "success": true,
  "data": {
    "uid": "switch-uuid",
    "state": false,
    "description": "Festival Light Event",
    "location": "Stockholm",
    "category": "Community",
    "publicize": true,
    "createdAt": 1640995200000,
    "lastToggled": 0,
    "toggleCount": 0,
    "websocketUrl": "/ws?uid=switch-uuid"
  }
}
```

### Toggle Switch

Toggle a switch's state.

**POST** `/api/toggle/{uid}`

**Authentication:** Required (must own the switch)

**Request Body:**
```json
{
  "personalKey": "your-personal-key"
}
```

**Response:**
```json
{
  "success": true,
  "data": {
    "uid": "switch-uuid",
    "state": true,
    "timestamp": 1640995200000
  }
}
```

### Get Switch Status

Get current switch state (public endpoint).

**GET** `/api/status/{uid}`

**Response:**
```json
{
  "success": true,
  "data": {
    "uid": "switch-uuid",
    "description": "Festival Light Event",
    "location": "Stockholm",
    "category": "Community",
    "state": true,
    "lastToggled": 1640995200000
  }
}
```

### Get Public Switches

List all public switches (**v2 only**). Legacy v1 UUID switches are not listed.

**GET** `/api/public-switches`

**Response:**
```json
{
  "success": true,
  "data": {
    "switches": [
      {
        "uid": "vs_...",
        "description": "Festival Light Event",
        "location": "Stockholm",
        "category": "Community",
        "state": true,
        "lastToggled": 1640995200000,
        "iconUrl": "",
        "bannerUrl": ""
      }
    ],
    "count": 1,
    "timestamp": 1640995200000
  }
}
```

### Get My Switches

Get switches owned by the authenticated user.

**GET** `/api/my-switches`

**Authentication:** Required

**Headers:**
```
X-Personal-Key: your-personal-key
```

**Response:**
```json
{
  "success": true,
  "data": {
    "switches": [
      {
        "uid": "switch-uuid",
        "description": "My Switch",
        "location": "Stockholm",
        "category": "Personal",
        "state": false,
        "publicize": false,
        "createdAt": 1640995200000,
        "lastToggled": 1640995200000,
        "toggleCount": 5
      }
    ],
    "count": 1
  }
}
```

### Delete Switch

Delete a switch (must be owner).

**DELETE** `/api/switch/{uid}`

**Authentication:** Required

**Headers:**
```
X-Personal-Key: your-personal-key
```

**Response:**
```json
{
  "success": true,
  "data": {
    "message": "Switch deleted successfully",
    "uid": "switch-uuid"
  }
}
```

### Delete Personal Key

Delete personal key and all associated data (GDPR compliance).

**POST** `/api/delete-key`

**Request Body:**
```json
{
  "personalKey": "your-personal-key",
  "confirmation": "DELETE_ALL_DATA"
}
```

**Response:**
```json
{
  "success": true,
  "data": {
    "message": "All personal data deleted successfully",
    "deletedSwitches": 3
  }
}
```

### Health Check

Check server health.

**GET** `/api/health`

**Response:**
```json
{
  "status": "healthy",
  "timestamp": 1640995200000,
  "uptime": 86400,
  "redis": true,
  "websocket": {
    "clients": 42,
    "subscriptions": 15
  }
}
```

### Server Stats

Get server statistics.

**GET** `/api/stats`

**Response:**
```json
{
  "success": true,
  "data": {
    "websocket": {
      "totalClients": 42,
      "totalSubscriptions": 15,
      "clientsPerSwitch": [
        {
          "uid": "switch-uuid",
          "clientCount": 3
        }
      ]
    },
    "publicSwitchCount": 8,
    "timestamp": 1640995200000
  }
}
```

## WebSocket API

Connect to real-time switch updates via WebSocket.

**URL:** `wss://sync.vome.io/ws?uid={switch-uuid}`

### Connection

Open WebSocket connection with switch UID as query parameter:

```javascript
const ws = new WebSocket('wss://sync.vome.io/ws?uid=your-switch-uuid');
```

### Message Types

#### State Update

Received when switch state changes:

```json
{
  "type": "state_update",
  "uid": "switch-uuid",
  "state": true,
  "timestamp": 1640995200000
}
```

#### Error

Received when an error occurs:

```json
{
  "type": "error",
  "message": "Switch not found",
  "uid": "switch-uuid"
}
```

#### Ping/Pong

For connection health monitoring:

```json
// Sent by client
{
  "type": "ping",
  "timestamp": 1640995200000
}

// Response from server
{
  "type": "pong",
  "timestamp": 1640995200000
}
```

#### Subscribe/Unsubscribe

Change subscription to different switch:

```json
// Subscribe to different switch
{
  "type": "subscribe",
  "uid": "new-switch-uuid"
}

// Unsubscribe from current switch
{
  "type": "unsubscribe",
  "uid": "current-switch-uuid"
}
```

## Error Handling

### Error Response Format

```json
{
  "success": false,
  "error": "Error message",
  "details": [
    {
      "field": "description",
      "message": "Description is too long"
    }
  ]
}
```

### HTTP Status Codes

- `200` - Success
- `400` - Bad Request (validation error)
- `401` - Unauthorized (invalid/missing personal key)
- `404` - Not Found (switch doesn't exist)
- `429` - Rate Limited
- `500` - Internal Server Error

### Common Error Messages

- `Invalid UID format` - UID is not a valid UUID
- `Switch not found` - Switch with given UID doesn't exist
- `Unauthorized: Invalid personal key for this switch` - You don't own this switch
- `Personal key required` - Authentication header missing
- `Rate limit exceeded` - Too many requests
- `Validation failed` - Request body validation errors

## SDK Examples

### JavaScript/Node.js

```javascript
class VomeSyncClient {
  constructor(personalKey) {
    this.baseUrl = 'https://sync.vome.io/api';
    this.personalKey = personalKey;
  }

  async createSwitch(config) {
    const response = await fetch(`${this.baseUrl}/create-switch`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-Personal-Key': this.personalKey
      },
      body: JSON.stringify(config)
    });
    return response.json();
  }

  async toggleSwitch(uid) {
    const response = await fetch(`${this.baseUrl}/toggle/${uid}`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-Personal-Key': this.personalKey
      },
      body: JSON.stringify({})
    });
    return response.json();
  }

  connectWebSocket(uid) {
    const ws = new WebSocket(`wss://sync.vome.io/ws?uid=${uid}`);
    
    ws.onmessage = (event) => {
      const data = JSON.parse(event.data);
      if (data.type === 'state_update') {
        console.log(`Switch ${data.uid} is now ${data.state ? 'on' : 'off'}`);
      }
    };
    
    return ws;
  }
}
```

### Python

```python
import requests
import websocket
import json

class VomeSyncClient:
    def __init__(self, personal_key):
        self.base_url = 'https://sync.vome.io/api'
        self.personal_key = personal_key
        self.headers = {
            'Content-Type': 'application/json',
            'X-Personal-Key': personal_key
        }

    def create_switch(self, config):
        response = requests.post(
            f'{self.base_url}/create-switch',
            headers=self.headers,
            json=config
        )
        return response.json()

    def toggle_switch(self, uid):
        response = requests.post(
            f'{self.base_url}/toggle/{uid}',
            headers=self.headers,
            json={}
        )
        return response.json()

    def connect_websocket(self, uid):
        def on_message(ws, message):
            data = json.loads(message)
            if data['type'] == 'state_update':
                print(f"Switch {data['uid']} is now {'on' if data['state'] else 'off'}")

        ws = websocket.WebSocketApp(
            f'wss://sync.vome.io/ws?uid={uid}',
            on_message=on_message
        )
        return ws
```

### curl Examples

```bash
# Generate personal key
curl -X POST https://sync.vome.io/api/generate-key \
  -H "Content-Type: application/json" \
  -d '{"consent": true}'

# Create switch
curl -X POST https://sync.vome.io/api/create-switch \
  -H "Content-Type: application/json" \
  -H "X-Personal-Key: your-key" \
  -d '{
    "description": "Test Switch",
    "category": "Test",
    "publicize": false
  }'

# Toggle switch
curl -X POST https://sync.vome.io/api/toggle/switch-uuid \
  -H "Content-Type: application/json" \
  -H "X-Personal-Key: your-key" \
  -d '{}'

# Get switch status
curl https://sync.vome.io/api/status/switch-uuid

# Get public switches
curl https://sync.vome.io/api/public-switches
```
