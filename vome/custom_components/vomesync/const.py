"""Constants for VomeSync integration."""
import re

# Integration domain
DOMAIN = "vomesync"

# The version of THIS loaded code — must match manifest.json (a test pins the
# two together). Deliberately a constant rather than a manifest read: after the
# add-on copies a newer build into /config, the file on disk is new but the
# module Home Assistant is running is still old. Comparing this constant with
# the on-disk manifest is how the panel knows a restart is required.
INTEGRATION_VERSION = "0.9.12"

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
# Full-UI forwarding (the paid "friendly domain" remote access).  Off by default
# because it brokers the *entire* Home Assistant browser session — arbitrary
# HTTP plus the frontend's /api/websocket — not just the scoped /api surface the
# assistant uses.  The owner opts in explicitly; Vome still gates who may reach
# the address (login + active subscription) before any byte is tunnelled.
CONF_RELAY_FORWARD_UI = "forward_ui"
# Path-based LAN tunnels on the same friendly domain: ``/t/<slug>/…`` is
# proxied to a configured LAN host:port.  List of route dicts (see lan_routes.py).
# Independent of forward_ui — you can expose a NAS without opening the HA UI.
CONF_RELAY_LAN_ROUTES = "lan_routes"

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

# Full-UI forwarding protocol (opt-in, see CONF_RELAY_FORWARD_UI).  The backend
# brokers a whole browser session over the same socket: each browser HTTP
# request is one http_proxy/http_proxy_response pair (bodies base64 so binary
# assets survive JSON), and the frontend WebSocket (/api/websocket) is bridged
# frame-for-frame with ws_open → ws_open_ack/ws_data ↔ ws_data → ws_close.
# Unlike ha_rpc this carries *arbitrary* paths and the browser's own auth — Vome
# injects no token; the user signs in to their Home Assistant as normal.
RELAY_WS_MSG_HTTP_PROXY = "http_proxy"
RELAY_WS_MSG_HTTP_PROXY_RESPONSE = "http_proxy_response"
RELAY_WS_MSG_WS_OPEN = "ws_open"
RELAY_WS_MSG_WS_OPEN_ACK = "ws_open_ack"
RELAY_WS_MSG_WS_DATA = "ws_data"
RELAY_WS_MSG_WS_CLOSE = "ws_close"

# LAN TCP tunnels (raw TCP to a LAN device, e.g. RDP — see lan_routes.py's
# "tcp" scheme).  ws_open/ws_data/ws_close above are reused unchanged for the
# byte-pumping itself; these two are only for the component to request a
# short-lived bearer token a local CLI tunnel client can present to the
# backend's /ws/tcp endpoint (see services_remote.mint_lan_tcp_token).
RELAY_WS_MSG_MINT_LAN_TCP_TOKEN = "mint_lan_tcp_token"
RELAY_WS_MSG_MINT_LAN_TCP_TOKEN_RESPONSE = "mint_lan_tcp_token_response"
RELAY_MINT_TOKEN_TIMEOUT = 10
LAN_TCP_TOKEN_DEFAULT_TTL = 3600
LAN_TCP_TOKEN_MAX_TTL = 86400
# Forwarding limits/tuning.  Bodies larger than the cap are refused (502) rather
# than buffered unbounded; the frontend WebSocket carries small JSON frames.
RELAY_FORWARD_HTTP_TIMEOUT = 60
RELAY_FORWARD_MAX_BODY = 25 * 1024 * 1024
# Exact path portions (query excluded) a browser WebSocket bridge may open.
RELAY_FORWARD_WS_PATHS = ("/api/websocket",)
# Hop-by-hop headers are connection-scoped and must not be forwarded across the
# tunnel (RFC 7230 §6.1); Host/Content-Length are re-derived by each hop.
RELAY_FORWARD_STRIP_HEADERS = frozenset({
	"connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
	"te", "trailer", "transfer-encoding", "upgrade", "host", "content-length",
	# aiohttp transparently gunzips the response body on `resp.read()` but
	# leaves the original `Content-Encoding` header on `resp.headers` — if we
	# forwarded it verbatim the browser would try to gunzip already-plain
	# bytes and fail with ERR_CONTENT_DECODING_FAILED.
	"content-encoding",
})

# An RPC targets either the local HA core REST API ("core", the default) or the
# local ESPHome dashboard ("esphome").  ESPHome flows through the same tunnel so
# the home-assistant-mcp server can list devices and read/write device YAML
# without any inbound exposure.
RELAY_RPC_TARGET_CORE = "core"
RELAY_RPC_TARGET_ESPHOME = "esphome"
RELAY_RPC_TARGET_WEBSOCKET = "websocket"

# Allowlisted Home Assistant WebSocket commands for brokered Lovelace dashboard
# access (must match portal/ha_ws_command.py).
LOVELACE_WS_READ_COMMANDS = frozenset({
	"lovelace/dashboards/list",
	"lovelace/config",
})
LOVELACE_WS_WRITE_COMMANDS = frozenset({
	"lovelace/config/save",
	"lovelace/dashboards/create",
	"lovelace/dashboards/delete",
	"lovelace/dashboards/update",
})
LOVELACE_WS_ALLOWED_COMMANDS = LOVELACE_WS_READ_COMMANDS | LOVELACE_WS_WRITE_COMMANDS

# Portal validates scope before dispatch; the relay accepts any well-formed HA
# WebSocket command type (full mode for registries, etc.).
WS_COMMAND_TYPE_RE = re.compile(r"^[a-z][a-z0-9_]*/[a-z0-9_./-]{0,120}$")
RELAY_WS_MAX_COMMAND_BYTES = 2_000_000

# ESPHome dashboard: discovered via the Supervisor add-on API on HAOS / Supervised
# installs, or set explicitly (CONF_RELAY_ESPHOME_URL).  Only the REST subset is
# proxied — the streaming build commands (compile/upload/run/logs) need a direct
# dashboard connection and are intentionally not tunnelled.
SUPERVISOR_ADDONS_URL = "http://supervisor/addons"
SUPERVISOR_ADDON_INFO_URL = "http://supervisor/addons/{slug}/info"
ESPHOME_DEFAULT_PORT = 6052
ESPHOME_WEB_PORT_KEY = "6052/tcp"  # the add-on's optional direct web port mapping
# Exact path portions (query excluded) of the brokered ESPHome REST subset —
# matched exactly, never as prefixes, so /devices-x or /edit/../delete are refused.
ESPHOME_ALLOWED_PATHS = ("/devices", "/version", "/edit")
ESPHOME_ALLOWED_METHODS = ("GET", "POST")
# Supervisor add-on state: only a *started* add-on is reachable, so discovery
# must check this rather than surface an opaque connect error.
ESPHOME_ADDON_STATE_STARTED = "started"
# The official add-on is host-networked with the web port disabled by default;
# its dashboard is served on a dynamic ingress port whose nginx only admits the
# Supervisor and 127.0.0.1.  Core is host-networked too, so localhost is the
# admitted route (this mirrors the add-on's own hassio discovery payload).
ESPHOME_INGRESS_HOST = "127.0.0.1"

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
