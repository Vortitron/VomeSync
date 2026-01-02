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

AUTH_MODE_CRYPTO = "crypto"

# Default configuration
DEFAULT_SERVER_URL = "https://sync.vome.io"
DEFAULT_WEBSOCKET_URL = "wss://sync.vome.io"

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
ATTR_IS_OWNER = "is_owner"

# Device info
DEVICE_MANUFACTURER = "Vortitron"
DEVICE_MODEL = "VomeSync Remote Switch"
