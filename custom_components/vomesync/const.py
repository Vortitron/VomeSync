"""Constants for VomeSync integration."""

# Integration domain
DOMAIN = "vomesync"

# Configuration keys
CONF_PERSONAL_KEY = "personal_key"
CONF_SERVER_URL = "server_url"
CONF_WEBSOCKET_URL = "websocket_url"
CONF_SWITCHES = "switches"
CONF_SUBSCRIPTIONS = "subscriptions"
CONF_AUTH_MODE = "auth_mode"
CONF_CRYPTO_SEED = "crypto_seed"
CONF_GENERATE_NEW_KEY = "generate_new_key"
CONF_USE_DEFAULT_URLS = "use_default_urls"
CONF_ACCESS_KEY = "access_key"

AUTH_MODE_CRYPTO = "crypto"

# Default configuration
DEFAULT_SERVER_URL = "https://sync.vome.io"
DEFAULT_WEBSOCKET_URL = "wss://sync.vome.io"

# Relay: connect this Home Assistant to a Vome account over an outbound tunnel,
# so Vome (and the home-assistant-mcp server) can broker scoped, audited calls
# to it without any inbound exposure (no public IP / port-forward / Nabu Casa).
CONF_RELAY = "relay"  # options key holding the dict below
CONF_RELAY_SERVER_ID = "server_id"
CONF_RELAY_SECRET = "secret"  # noqa: S105 - dict key, not a secret value
CONF_RELAY_WS_URL = "ws_url"
CONF_RELAY_LOCAL_TOKEN = "local_token"  # noqa: S105 - optional non-supervised fallback
CONF_RELAY_LOCAL_URL = "local_url"
CONF_RELAY_ESPHOME_URL = "esphome_url"  # optional explicit ESPHome dashboard base URL

# The portal (account / device-authorisation) lives on vome.io; the relay
# WebSocket lives on sync.vome.io.  The portal tells us the WS URL at link time.
DEFAULT_PORTAL_URL = "https://vome.io"
DEFAULT_RELAY_WS_URL = "wss://sync.vome.io/ws/relay"
RELAY_DEVICE_CODE_PATH = "/api/v1/relay/device/code"
RELAY_DEVICE_TOKEN_PATH = "/api/v1/relay/device/token"

# Local Home Assistant core API for executing relayed calls.  Supervisor token
# first (HAOS / Supervised); a configured long-lived token is the fallback.
# NOTE: the Supervisor's /core/api proxy is add-on-only — it 401s core's own
# token — so local core calls use a minted long-lived token instead (see
# relay_client.async_ensure_local_access_token).  The Supervisor API is still
# used (token below) for ESPHome add-on discovery.
DEFAULT_LOCAL_CORE_URL = "http://127.0.0.1:8123"
SUPERVISOR_TOKEN_ENV = "SUPERVISOR_TOKEN"  # noqa: S105 - env var name, not a secret

# Relay WebSocket message types + reconnect tuning.
RELAY_WS_MSG_HELLO = "hello"
RELAY_WS_MSG_HA_RPC = "ha_rpc"
RELAY_WS_MSG_HA_RPC_RESPONSE = "ha_rpc_response"
RELAY_WS_MSG_PING = "ping"
RELAY_WS_MSG_PONG = "pong"
RELAY_RECONNECT_DELAY = 5
RELAY_RECONNECT_MAX_DELAY = 60
RELAY_RPC_TIMEOUT = 30
RELAY_ALLOWED_METHODS = ("GET", "POST", "PUT", "DELETE")

# An RPC targets either the local HA core REST API ("core", the default) or the
# local ESPHome dashboard ("esphome").  ESPHome flows through the same tunnel so
# the home-assistant-mcp server can list devices and read/write device YAML
# without any inbound exposure.
RELAY_RPC_TARGET_CORE = "core"
RELAY_RPC_TARGET_ESPHOME = "esphome"

# ESPHome dashboard: discovered via the Supervisor add-on API on HAOS / Supervised
# installs, or set explicitly (CONF_RELAY_ESPHOME_URL).  Only the REST subset is
# proxied — the streaming build commands (compile/upload/run/logs) need a direct
# dashboard connection and are intentionally not tunnelled.
SUPERVISOR_ADDONS_URL = "http://supervisor/addons"
ESPHOME_DEFAULT_PORT = 6052
ESPHOME_ALLOWED_PATHS = ("/devices", "/version", "/edit")
ESPHOME_ALLOWED_METHODS = ("GET", "POST")

# Switch configuration keys
CONF_SWITCH_UID = "uid"
CONF_SWITCH_NAME = "name"
CONF_SWITCH_DESCRIPTION = "description"
CONF_SWITCH_LOCATION = "location"
CONF_SWITCH_CATEGORY = "category"
CONF_SWITCH_PUBLICIZE = "publicize"
CONF_SWITCH_LINK = "link"
CONF_SWITCH_ICON_URL = "icon_url"
CONF_SWITCH_BANNER_URL = "banner_url"
CONF_CAPTCHA_TOKEN = "captcha_token"
CONF_SWITCH_ADVANCED = "advanced_fields"
CONF_SHOW_SIGNING_KEY_AFTER = "show_signing_key_after"

# Categories
SWITCH_CATEGORIES = [
	"Community",
	"Personal", 
	"Event",
	"Test",
	"Other"
]

# API endpoints
API_GENERATE_KEY = "/api/generate-key"
API_CREATE_SWITCH = "/api/create-switch"
API_TOGGLE_SWITCH = "/api/toggle/{uid}"
API_GET_STATUS = "/api/status/{uid}"
API_MY_SWITCHES = "/api/my-switches"
API_PUBLIC_SWITCHES = "/api/public-switches"

# API v2 (crypto identity)
API_V2_CREATE_SWITCH = "/api/v2/switch"
API_V2_MY_SWITCHES = "/api/v2/my-switches"
API_V2_SET_STATE = "/api/v2/switch/{uid}/state"
API_V2_UPDATE_SWITCH = "/api/v2/switch/{uid}"
API_V2_ACCESS_KEYS_CREATE = "/api/v2/switch/{uid}/access-keys"
API_V2_ACCESS_KEYS_LIST = "/api/v2/switch/{uid}/access-keys/list"
API_V2_ACCESS_KEYS_REVOKE = "/api/v2/switch/{uid}/access-keys/revoke"
API_V2_ACCESS_KEYS_PAUSE = "/api/v2/switch/{uid}/access-keys/pause"
API_V2_ACCESS_KEYS_PERMISSIONS = "/api/v2/switch/{uid}/access-keys/permissions"
API_V2_TOGGLE = "/api/v2/switch/{uid}/toggle"

# WebSocket message types
WS_MSG_STATE_UPDATE = "state_update"
WS_MSG_ERROR = "error"
WS_MSG_PING = "ping"
WS_MSG_PONG = "pong"
WS_MSG_SUBSCRIBE = "subscribe"
WS_MSG_UNSUBSCRIBE = "unsubscribe"

# Update intervals
UPDATE_INTERVAL_SECONDS = 30
WEBSOCKET_RECONNECT_DELAY = 5

# Entity attributes
ATTR_SWITCH_UID = "switch_uid"
ATTR_NAME = "name"
ATTR_DESCRIPTION = "description"
ATTR_LOCATION = "location"
ATTR_CATEGORY = "category"
ATTR_PUBLICIZE = "publicize"
ATTR_LINK = "link"
ATTR_ICON_URL = "icon_url"
ATTR_BANNER_URL = "banner_url"
ATTR_TOGGLE_COUNT = "toggle_count"
ATTR_LAST_TOGGLED = "last_toggled"
ATTR_CREATED_AT = "created_at"
ATTR_LAST_TOGGLED_TS = "last_toggled_ts"
ATTR_CREATED_AT_TS = "created_at_ts"
ATTR_IS_OWNER = "is_owner"
DEFAULT_SWITCH_NAME = "Unnamed switch"
FREE_TIER_MAX_SUBSCRIPTIONS = 16

# Device info
DEVICE_MANUFACTURER = "Vortitron"
DEVICE_MODEL_OWNED = "Vome Switch"
DEVICE_MODEL_REMOTE = "Vome Remote Switch"
