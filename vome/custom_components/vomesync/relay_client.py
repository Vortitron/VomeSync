"""Relay client — connect this Home Assistant to Vome over an outbound tunnel.

This is the component side of the "Connect your own Home Assistant" feature.  It
lets a user's own Home Assistant be brokered by Vome — and therefore by the
``home-assistant-mcp`` server inside Cursor / VS Code — **without any inbound
exposure**: no public IP, no port-forwarding, no Nabu Casa.

How it works:
  * We dial out to ``wss://sync.vome.io/ws/relay`` and authenticate with a
    per-instance relay secret obtained from the device-authorisation flow.
  * Vome pushes ``ha_rpc`` requests (a method + ``/api/...`` path + body) down the
    socket; we execute each against the LOCAL Home Assistant core REST API and
    reply with ``ha_rpc_response``.
  * Local execution authenticates with a long-lived access token the component
    mints for the owner user via ``hass.auth`` (works on every install type —
    the Supervisor's ``/core/api`` proxy rejects core's own token with a 401,
    so it cannot be used from a custom component).  A manually configured
    token + URL (the "alternative connection" options) overrides the minted
    one for unusual setups (custom ``server_host``, TLS on the local API…).

Security: only ``/api/...`` paths are executed; the same scoped, audited Vome
token + server-side deny-list that guards a Vome VM guards this transport too —
this client just carries the request, it does not widen what is permitted.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import re
import uuid
from contextlib import suppress
from datetime import timedelta
from typing import Any, Optional
from urllib.parse import unquote, urlparse

import aiohttp
from homeassistant.auth.models import TOKEN_TYPE_LONG_LIVED_ACCESS_TOKEN
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
	CONF_RELAY,
	CONF_RELAY_ESPHOME_URL,
	CONF_RELAY_FORWARD_UI,
	CONF_RELAY_LAN_ROUTES,
	CONF_RELAY_LOCAL_TOKEN,
	CONF_RELAY_WEBHOOKS,
	CONF_RELAY_LOCAL_URL,
	CONF_RELAY_SECRET,
	CONF_RELAY_SERVER_ID,
	CONF_RELAY_WS_URL,
	DEFAULT_LOCAL_CORE_URL,
	DEFAULT_PORTAL_URL,
	DEFAULT_RELAY_WS_URL,
	DOMAIN,
	ESPHOME_ADDON_STATE_STARTED,
	ESPHOME_ALLOWED_METHODS,
	ESPHOME_ALLOWED_PATHS,
	ESPHOME_CONFIG_RE,
	ESPHOME_DEFAULT_PORT,
	ESPHOME_STREAM_COMMANDS,
	ESPHOME_INGRESS_HOST,
	ESPHOME_WEB_PORT_KEY,
	LAN_TCP_TOKEN_DEFAULT_TTL,
	RELAY_ALLOWED_METHODS,
	RELAY_DEVICE_CODE_PATH,
	RELAY_DEVICE_TOKEN_PATH,
	RELAY_FORWARD_HTTP_TIMEOUT,
	RELAY_FORWARD_MAX_BODY,
	RELAY_FORWARD_BODY_TIMEOUT,
	FORWARD_HOST_HEADER,
	FORWARD_HOST_KEY,
	RELAY_FORWARD_STRIP_HEADERS,
	RELAY_FORWARD_WS_PATHS,
	RELAY_MINT_TOKEN_TIMEOUT,
	RELAY_RECONNECT_DELAY,
	RELAY_RECONNECT_MAX_DELAY,
	RELAY_RPC_TARGET_ESPHOME,
	RELAY_RPC_TARGET_WEBSOCKET,
	RELAY_RPC_TIMEOUT,
	RELAY_WS_MAX_COMMAND_BYTES,
	WS_COMMAND_TYPE_RE,
	RELAY_WS_MSG_HA_RPC,
	RELAY_WS_MSG_HA_RPC_RESPONSE,
	RELAY_WS_MSG_HELLO,
	RELAY_WS_MSG_HTTP_PROXY,
	RELAY_WS_MSG_HTTP_PROXY_RESPONSE,
	RELAY_WS_MSG_HTTP_PROXY_ABORT,
	RELAY_WS_MSG_HTTP_PROXY_CHUNK,
	RELAY_WS_MSG_HTTP_PROXY_END,
	ACCESS_EVENTS_MAX_BATCH,
	RELAY_WS_MSG_ACCESS_EVENTS,
	RELAY_WS_MSG_MINT_LAN_TCP_TOKEN,
	RELAY_WS_MSG_MINT_LAN_TCP_TOKEN_RESPONSE,
	RELAY_WS_MSG_PING,
	RELAY_WS_MSG_PONG,
	RELAY_WS_MSG_WS_CLOSE,
	RELAY_WS_MSG_WS_DATA,
	RELAY_WS_MSG_WS_OPEN,
	RELAY_WS_MSG_WS_OPEN_ACK,
	SUPERVISOR_ADDON_INFO_URL,
	SUPERVISOR_ADDONS_URL,
	SUPERVISOR_TOKEN_ENV,
)
from .login_watch import LoginWatcher
from .webhooks import is_forwardable_webhook, normalise_webhooks
from .lan_routes import (
	ROUTE_HOST,
	ROUTE_PORT,
	ROUTE_SCHEME,
	find_route,
	normalise_routes,
	parse_lan_path,
	rewrite_response_headers,
	route_allows_websocket,
	route_base_url,
)

_LOGGER = logging.getLogger(__name__)

_RELAYS_KEY = "_relays"
_HTTP_TIMEOUT = aiohttp.ClientTimeout(total=15)

# Client name of the long-lived access token we mint for local API calls.  It
# shows up under the owner's profile → Security → long-lived access tokens.
RELAY_TOKEN_CLIENT_NAME = "Vome relay"
_RELAY_TOKEN_LIFETIME = timedelta(days=3650)


# ── Full-UI forwarding helpers (shared by request + response paths) ──────────

def _normalise_header_input(headers: Any) -> list[tuple[str, str]]:
	"""Accept headers as a dict, a multidict view, or a list/iterable of pairs.

	Returns ``(name, value)`` tuples.  The backend sends a list of pairs to
	preserve duplicates (notably several ``Set-Cookie``); the local response
	arrives as ``CIMultiDict.items()`` (a *view*, not a list).  Both — plus a
	plain dict — must work, so accept any non-string iterable of pairs.
	"""
	if isinstance(headers, dict):
		return [(str(k), str(v)) for k, v in headers.items()]
	if headers is None or isinstance(headers, (str, bytes)):
		return []
	try:
		pairs = list(headers)
	except TypeError:
		return []
	return [
		(str(p[0]), str(p[1]))
		for p in pairs
		if isinstance(p, (list, tuple)) and len(p) >= 2
	]


def _filter_forward_headers(pairs: Any) -> list[list[str]]:
	"""Return ``[[name, value], …]`` with hop-by-hop headers removed.

	Hop-by-hop headers (RFC 7230 §6.1) describe a single transport hop and must
	not be tunnelled; ``Host``/``Content-Length`` are re-derived by each hop.
	Used for both the inbound browser request and the local HA response.
	"""
	out: list[list[str]] = []
	for name, value in _normalise_header_input(pairs):
		if name.lower() in RELAY_FORWARD_STRIP_HEADERS:
			continue
		out.append([name, value])
	return out


class _StreamSink:
	"""Carries one forwarded response back in pieces instead of all at once.

	Used only for a body that has not finished arriving by the time the read
	budget runs out. Everything that completes promptly still goes back whole,
	which keeps the ordinary path -- and the header rewriting that happens
	after it -- exactly as it was.
	"""

	def __init__(self, client, ws, request_id):
		self._client = client
		self._ws = ws
		self._request_id = request_id
		self.started = False
		self.aborted = False

	async def begin(self, status: int, headers: Any) -> None:
		"""Send the head. The body follows as chunks."""
		self.started = True
		await self._client._send(self._ws, {
			"type": RELAY_WS_MSG_HTTP_PROXY_RESPONSE,
			"requestId": self._request_id,
			"status": status,
			"headers": headers,
			"streaming": True,
		})

	async def chunk(self, data: bytes) -> None:
		if self.aborted or not data:
			return
		await self._client._send(self._ws, {
			"type": RELAY_WS_MSG_HTTP_PROXY_CHUNK,
			"requestId": self._request_id,
			"dataB64": base64.b64encode(data).decode("ascii"),
		})

	async def finish(self, error: Optional[str] = None) -> None:
		message: dict[str, Any] = {
			"type": RELAY_WS_MSG_HTTP_PROXY_END,
			"requestId": self._request_id,
		}
		if error:
			message["error"] = error
		await self._client._send(self._ws, message)


async def _read_forwardable_body(
	resp, *, sink: Optional["_StreamSink"] = None
) -> tuple[Optional[bytes], Optional[str]]:
	"""Read a response body the relay can actually carry.

	Returns ``(body, None)`` or ``(None, reason)``.

	``resp.read()`` waits for the body to end.  A Home Assistant endpoint whose
	body never ends -- ``/api/hassio/supervisor/logs/follow``, Server-Sent
	Events, a camera stream -- therefore consumed the whole forward timeout and
	surfaced as a blank 502 a minute later, indistinguishable from the instance
	being down.  Forwarding buffers whole responses, so these genuinely cannot
	be carried; the point here is to say so straight away and say which it is.

	Server-Sent Events announce themselves in the content type, so those are
	refused outright.  Everything else gets a read budget well short of the
	overall timeout: a body still arriving after that is either a stream or so
	slow it is not worth waiting on, and either way the browser is better told
	than left hanging.  A declared Content-Length over the cap is refused
	before a byte is read, rather than after buffering 25 MiB.
	"""
	content_type = (resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()
	is_event_stream = content_type == "text/event-stream"

	declared = resp.headers.get("Content-Length")
	declared_over_cap = (
		declared is not None
		and declared.strip().isdigit()
		and int(declared) > RELAY_FORWARD_MAX_BODY
	)

	if sink is None:
		# No way to send a body in pieces: refuse what cannot be buffered, and
		# say which of the two it is rather than running out the clock.
		if is_event_stream:
			return None, (
				"That page streams updates continuously, which cannot be carried "
				"over the relay."
			)
		if declared_over_cap:
			return None, "Response body too large to forward."
		try:
			raw = await asyncio.wait_for(resp.read(), timeout=RELAY_FORWARD_BODY_TIMEOUT)
		except asyncio.TimeoutError:
			return None, (
				"That page did not finish loading. It may stream continuously, like "
				"a live log or a camera feed, which cannot be carried over the relay."
			)
		if len(raw) > RELAY_FORWARD_MAX_BODY:
			return None, "Response body too large to forward."
		return raw, None

	headers = _filter_forward_headers(resp.headers.items())

	# Known up front to be uncarryable whole: start streaming without waiting
	# to find out the slow way.
	if is_event_stream or declared_over_cap:
		await sink.begin(resp.status, headers)
		await _pump(resp, sink)
		return None, None

	# Otherwise buffer, but only for as long as the budget allows. A body that
	# finishes in time goes back whole, so the ordinary path -- and everything
	# that happens to a whole response afterwards -- is untouched. One that
	# does not is handed over mid-flight, keeping what has already been read.
	chunks: list[bytes] = []
	total = 0
	deadline = asyncio.get_event_loop().time() + RELAY_FORWARD_BODY_TIMEOUT
	while True:
		remaining = deadline - asyncio.get_event_loop().time()
		if remaining <= 0:
			break
		try:
			chunk = await asyncio.wait_for(resp.content.readany(), timeout=remaining)
		except asyncio.TimeoutError:
			break
		if not len(chunk):
			return b"".join(chunks), None
		chunks.append(chunk)
		total += len(chunk)
		if total > RELAY_FORWARD_MAX_BODY:
			break

	await sink.begin(resp.status, headers)
	for chunk in chunks:
		await sink.chunk(chunk)
	await _pump(resp, sink)
	return None, None


async def _pump(resp, sink: "_StreamSink") -> None:
	"""Send the rest of a response body as chunks until it ends or is aborted."""
	while not sink.aborted:
		chunk = await resp.content.readany()
		if not len(chunk):
			return
		await sink.chunk(chunk)


# ── Local core URL resolution ────────────────────────────────────────────────
#
# Home Assistant 2026.8 made the HTTP listen port a first-class UI setting
# (Settings → System → Network) and switched new installs to port 80, so a
# hardcoded ``127.0.0.1:8123`` is no longer a safe assumption — and it never was
# for installs that terminate TLS locally or bind to a single interface.  Resolve
# it from the running instance on every use instead of caching it at setup: the
# port can now change under a live relay, and the five-minute auto-rollback means
# it can change twice in quick succession.  Getting this wrong is silent — the
# relay stays connected and reports healthy while every dispatched request fails.

# Addresses that mean "this machine" — if HA binds to one of these (or to
# nothing, i.e. all interfaces) then loopback is listening and is the safe dial.
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost", "0.0.0.0", "::"})

# A bare hostname, nothing else — this value is offered to the user as the
# address to publish as their external URL, so it must not be able to smuggle a
# path, credentials or a second host into that field.
_SAFE_FORWARD_HOST_RE = re.compile(r"[A-Za-z0-9]([A-Za-z0-9.-]{0,251}[A-Za-z0-9])?")


def _local_host(server_host: Any) -> str:
	"""Return the host to dial for a locally bound Home Assistant.

	``server_host`` is unset on most installs (all interfaces).  2026.8's network
	page lets a user bind to one specific interface instead, in which case
	loopback is *not* listening and we have to dial the bound address.
	"""
	if isinstance(server_host, str):
		hosts = [server_host]
	elif isinstance(server_host, (list, tuple, set)):
		hosts = [host for host in server_host if isinstance(host, str)]
	else:
		hosts = []
	if not hosts or any(host in _LOOPBACK_HOSTS for host in hosts):
		return "127.0.0.1"
	host = hosts[0]
	return f"[{host}]" if ":" in host else host


def _internal_url_base(hass: HomeAssistant) -> Optional[str]:
	"""Return ``scheme://netloc`` of hass.config.internal_url, if it is usable."""
	internal_url = getattr(getattr(hass, "config", None), "internal_url", None)
	if isinstance(internal_url, str) and internal_url:
		parsed = urlparse(internal_url)
		if parsed.scheme in ("http", "https") and parsed.netloc:
			return f"{parsed.scheme}://{parsed.netloc}"
	return None


def _derive_local_core_url(hass: HomeAssistant) -> Optional[str]:
	"""Derive the local core base URL from the running instance, or ``None``."""
	http = getattr(hass, "http", None)
	api = getattr(getattr(hass, "config", None), "api", None)

	# hass.http is the actual server object, so its port is what is really bound;
	# hass.config.api carries the same value and survives as a fallback.  Take
	# the scheme from whichever of the two supplied the port rather than OR-ing
	# them: they describe the same server, so if they ever disagree the one we
	# trusted for the port is the one to trust for the scheme too.
	port = getattr(http, "server_port", None)
	if isinstance(port, int):
		use_ssl = bool(getattr(http, "ssl_certificate", None))
	else:
		port = getattr(api, "port", None)
		use_ssl = bool(getattr(api, "use_ssl", False))

	# An instance terminating its own TLS holds a certificate for a *hostname*,
	# so https://127.0.0.1:port would fail verification even though the port is
	# right.  internal_url is the name the user gave it — prefer that when set.
	if use_ssl:
		internal = _internal_url_base(hass)
		if internal and internal.startswith("https://"):
			return internal

	if isinstance(port, int) and 0 < port < 65536:
		scheme = "https" if use_ssl else "http"
		return f"{scheme}://{_local_host(getattr(http, 'server_host', None))}:{port}"

	# Last resort before the constant: whatever the user told HA to call itself.
	return _internal_url_base(hass)


def resolve_local_core_url(
	hass: Optional[HomeAssistant], override: Optional[str] = None
) -> str:
	"""Return the base URL of this Home Assistant's own HTTP API.

	``override`` (the ``local_url`` relay option) always wins: derivation can be
	wrong behind unusual setups, and support needs a way to correct it without a
	code change.
	"""
	if override:
		return str(override).rstrip("/")
	if hass is not None:
		# Resolution must never be the reason the relay stops working.
		with suppress(Exception):
			derived = _derive_local_core_url(hass)
			if derived:
				return derived
	return DEFAULT_LOCAL_CORE_URL


def describe_local_core_url(
	hass: Optional[HomeAssistant], override: Optional[str] = None
) -> tuple[str, str]:
	"""Return ``(url, source)`` where source is override / detected / fallback.

	The panel shows this because a wrong local URL is otherwise invisible: the
	relay connects, the status is green, and only the dispatched calls fail.
	"fallback" specifically means detection did not work and we are guessing —
	which is the state worth telling somebody about.
	"""
	if override:
		return str(override).rstrip("/"), "override"
	if hass is not None:
		with suppress(Exception):
			derived = _derive_local_core_url(hass)
			if derived:
				return derived, "detected"
	return DEFAULT_LOCAL_CORE_URL, "fallback"


def _to_ws_url(base_url: Optional[str], path: str) -> str:
	"""Map an ``http(s)`` base + path to the matching ``ws(s)://`` URL."""
	base = (base_url or DEFAULT_LOCAL_CORE_URL).rstrip("/")
	if base.startswith("https://"):
		base = "wss://" + base[len("https://"):]
	elif base.startswith("http://"):
		base = "ws://" + base[len("http://"):]
	return base + path


def _safe_path_portion(path: Any) -> Optional[str]:
	"""Return the path portion (before any query string) of a relayed path,
	or ``None`` when it is not a clean absolute path.

	Dot segments are rejected — literal or percent-encoded — because the HTTP
	client normalises ``..`` when building the URL, so ``/api/../auth/x`` would
	otherwise pass a ``startswith("/api/")`` check yet reach ``/auth/x``.
	"""
	if not isinstance(path, str) or not path.startswith("/"):
		return None
	portion = path.split("?", 1)[0]
	for segment in portion.split("/"):
		if segment in (".", "..") or unquote(segment) in (".", ".."):
			return None
	return portion


async def async_ensure_local_access_token(hass: HomeAssistant) -> Optional[str]:
	"""Return an access token for the local HA REST API, minting one if needed.

	Reuses the component's own long-lived refresh token (created once for the
	owner user, named ``RELAY_TOKEN_CLIENT_NAME``) and derives a fresh access
	token from it on every (re)start — nothing secret is persisted by us.
	Returns ``None`` when there is no owner/admin user to mint for.
	"""
	user = await hass.auth.async_get_owner()
	if user is None:
		# Rare (e.g. owner deleted): fall back to the first active local admin.
		for candidate in await hass.auth.async_get_users():
			if candidate.is_active and candidate.is_admin and not candidate.system_generated:
				user = candidate
				break
	if user is None:
		_LOGGER.error("Relay: no owner/admin user found to mint a local access token for")
		return None
	refresh = next(
		(
			rt for rt in user.refresh_tokens.values()
			if rt.client_name == RELAY_TOKEN_CLIENT_NAME
			and rt.token_type == TOKEN_TYPE_LONG_LIVED_ACCESS_TOKEN
		),
		None,
	)
	if refresh is None:
		refresh = await hass.auth.async_create_refresh_token(
			user,
			client_name=RELAY_TOKEN_CLIENT_NAME,
			token_type=TOKEN_TYPE_LONG_LIVED_ACCESS_TOKEN,
			access_token_expiration=_RELAY_TOKEN_LIFETIME,
		)
		_LOGGER.info("Relay: created the '%s' long-lived token for %s", RELAY_TOKEN_CLIENT_NAME, user.name)
	return hass.auth.async_create_access_token(refresh)


class RelayClient:
	"""A single outbound relay connection for one Vome-linked HA instance."""

	def __init__(
		self,
		hass: Optional[HomeAssistant],
		*,
		server_id: str,
		secret: str,
		ws_url: Optional[str] = None,
		local_token: Optional[str] = None,
		local_url: Optional[str] = None,
		esphome_url: Optional[str] = None,
		forward_ui: bool = False,
		lan_routes: Optional[list] = None,
		webhooks: Optional[list] = None,
		session: Optional[aiohttp.ClientSession] = None,
	) -> None:
		self._hass = hass
		self._server_id = server_id
		self._secret = secret
		self._ws_url = ws_url or DEFAULT_RELAY_WS_URL
		self._local_token = local_token
		# Stored as an override only — the effective URL is resolved per use by
		# the ``local_url`` property so a port change is picked up without a
		# restart.  See resolve_local_core_url().
		self._local_url_override = (local_url or "").rstrip("/") or None
		self._esphome_url = (esphome_url or "").rstrip("/") or None
		# Full-UI forwarding is opt-in: it brokers the whole browser session, not
		# just the scoped /api surface, so the owner must enable it deliberately.
		self._forward_ui = bool(forward_ui)
		# Path→LAN tunnels (``/t/<slug>/…``).  Independent of forward_ui.
		self._lan_routes = normalise_routes(lan_routes)
		# Publicly callable webhook ids — also independent of forward_ui.
		self._webhooks = normalise_webhooks(webhooks)
		# Cache for the auto-discovered ESPHome dashboard base (Supervisor installs).
		self._esphome_base_cache: Optional[str] = None
		self._session = session
		self._task: Optional[asyncio.Task] = None
		self._ws: Optional[aiohttp.ClientWebSocketResponse] = None
		self._closing = False
		# Live frontend-WebSocket bridges, keyed by the backend's socketId: the
		# local HA socket plus the task pumping its frames back up the tunnel.
		self._ws_local: dict[str, aiohttp.ClientWebSocketResponse] = {}
		self._ws_pumps: dict[str, asyncio.Task] = {}
		# Same idea for raw-TCP LAN tunnels (e.g. RDP): a socketId maps to a
		# local (reader, writer) pair instead of a WebSocket, but rides the
		# same ws_open/ws_data/ws_close messages — see _handle_lan_ws_open.
		self._tcp_local: dict[str, tuple[asyncio.StreamReader, asyncio.StreamWriter]] = {}
		self._tcp_pumps: dict[str, asyncio.Task] = {}
		# Component-initiated requests awaiting a backend reply on this same
		# socket (the reverse direction of ha_rpc/http_proxy/ws_open, which are
		# all backend-initiated) — currently only mint_lan_tcp_token.
		self._pending_requests: dict[str, asyncio.Future] = {}
		# Reports Core's own failed logins up the relay (see login_watch.py).
		self._login_watcher: Optional[LoginWatcher] = None
		# Streaming forwards in flight, so an abort from the backend can find
		# the read it needs to stop: requestId -> _StreamSink.
		self._streaming: dict[Any, "_StreamSink"] = {}
		# Serialise writes to the single relay socket: HTTP responses and many
		# concurrent WS frames share it, and aiohttp does not guard interleaving.
		self._send_lock = asyncio.Lock()

	@property
	def local_url(self) -> str:
		"""Base URL of this Home Assistant's own API, resolved on every use.

		Deliberately not cached: 2026.8 lets the listen port change under a
		running instance (and roll back again five minutes later), so anything
		captured at setup goes stale silently.
		"""
		return resolve_local_core_url(self._hass, self._local_url_override)

	def _get_session(self) -> aiohttp.ClientSession:
		if self._session is not None:
			return self._session
		return async_get_clientsession(self._hass)

	# ── Lifecycle ───────────────────────────────────────────────────────────

	def start(self) -> None:
		"""Start (or restart) the reconnecting relay loop."""
		if self._task is not None and not self._task.done():
			return
		self._closing = False
		self._task = self._hass.loop.create_task(self._run())
		# Core's own failed-login records go up the same socket, so the owner
		# can see attempts that never reached Vome's edge at all — a device on
		# their own network with a stale token, say.  See login_watch.py.
		if self._login_watcher is None:
			self._login_watcher = LoginWatcher(self._hass, self.send_access_events)
		self._login_watcher.start()

	async def stop(self) -> None:
		"""Stop the relay loop and close the socket."""
		self._closing = True
		if self._login_watcher is not None:
			self._login_watcher.stop()
		if self._ws is not None:
			with suppress(Exception):
				await self._ws.close()
		if self._task is not None:
			self._task.cancel()
			with suppress(asyncio.CancelledError):
				await self._task
			self._task = None

	async def _run(self) -> None:
		delay = RELAY_RECONNECT_DELAY
		while not self._closing:
			try:
				await self._connect_once()
				delay = RELAY_RECONNECT_DELAY  # a clean session resets backoff
			except asyncio.CancelledError:
				raise
			except Exception as err:  # noqa: BLE001 - keep the loop alive
				_LOGGER.warning("Relay (%s) connection error: %s", self._server_id, err)
			if self._closing:
				break
			await asyncio.sleep(delay)
			delay = min(delay * 2, RELAY_RECONNECT_MAX_DELAY)

	async def _connect_once(self) -> None:
		session = self._get_session()
		headers = {"Authorization": f"Bearer {self._secret}"}
		async with session.ws_connect(self._ws_url, headers=headers, heartbeat=30) as ws:
			self._ws = ws
			_LOGGER.info("Relay connected to Vome (%s)", self._server_id)
			try:
				async for msg in ws:
					if msg.type == aiohttp.WSMsgType.TEXT:
						await self._handle_text(ws, msg.data)
					elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
						break
			finally:
				self._ws = None
				# A dropped tunnel orphans every bridged browser socket: tear
				# them down so a reconnect starts from a clean slate.
				await self._close_all_tunnels()
		_LOGGER.info("Relay disconnected from Vome (%s)", self._server_id)

	async def _send(self, ws: aiohttp.ClientWebSocketResponse, payload: dict) -> None:
		"""Serialise one JSON write to the shared relay socket."""
		async with self._send_lock:
			await ws.send_str(json.dumps(payload))

	# ── Message handling ────────────────────────────────────────────────────

	async def _handle_text(self, ws: aiohttp.ClientWebSocketResponse, raw: str) -> None:
		try:
			data = json.loads(raw)
		except (ValueError, TypeError):
			_LOGGER.debug("Relay (%s): ignoring non-JSON message", self._server_id)
			return
		mtype = data.get("type")
		if mtype == RELAY_WS_MSG_HA_RPC:
			await self._handle_rpc(ws, data)
		elif mtype == RELAY_WS_MSG_HTTP_PROXY:
			await self._handle_http_proxy(ws, data)
		elif mtype == RELAY_WS_MSG_WS_OPEN:
			await self._handle_ws_open(ws, data)
		elif mtype == RELAY_WS_MSG_WS_DATA:
			await self._handle_ws_data(data)
		elif mtype == RELAY_WS_MSG_WS_CLOSE:
			await self._handle_ws_close(data)
		elif mtype == RELAY_WS_MSG_PING:
			await self._send(ws, {"type": RELAY_WS_MSG_PONG})
		elif mtype == RELAY_WS_MSG_HTTP_PROXY_ABORT:
			# The browser stopped listening. Without this a closed tab leaves a
			# log tail being read forever, one open request per abandoned page.
			sink = self._streaming.get(data.get("requestId"))
			if sink is not None:
				sink.aborted = True
		elif mtype == RELAY_WS_MSG_HELLO:
			_LOGGER.debug("Relay (%s): hello acknowledged", self._server_id)
		elif mtype == RELAY_WS_MSG_MINT_LAN_TCP_TOKEN_RESPONSE:
			# Resolve with the whole payload so request_lan_tcp_token can surface
			# a backend-side error (bad slug, misconfig) instead of a blank token.
			self._resolve_pending_request(data.get("requestId"), data)

	def _resolve_pending_request(self, request_id: Any, value: Any) -> None:
		"""Resolve one of our own outgoing requests (see request_lan_tcp_token)."""
		future = self._pending_requests.pop(request_id, None)
		if future is not None and not future.done():
			future.set_result(value)

	async def _handle_rpc(self, ws: aiohttp.ClientWebSocketResponse, data: dict) -> None:
		request_id = data.get("requestId")
		status, body, error = await self.execute(
			data.get("method"), data.get("path"), data.get("body"), data.get("target")
		)
		response: dict[str, Any] = {
			"type": RELAY_WS_MSG_HA_RPC_RESPONSE,
			"requestId": request_id,
			"status": status,
		}
		if body is not None:
			response["body"] = body
		if error:
			response["error"] = error
		await self._send(ws, response)

	# ── Full-UI forwarding: arbitrary HTTP ──────────────────────────────────

	async def _handle_http_proxy(self, ws: aiohttp.ClientWebSocketResponse, data: dict) -> None:
		"""Proxy one whole browser HTTP request to the local HA and reply.

		Unlike ``ha_rpc`` this carries *any* path/method and the browser's own
		headers (cookies, auth) — Vome injects nothing.  The reply mirrors the
		local status, headers (hop-by-hop stripped, duplicates preserved) and
		body (base64) so binary assets survive the JSON hop.
		"""
		request_id = data.get("requestId")
		self._note_forward_host(data.get("headers"))
		# Only offer a sink when the backend said it can take a response in
		# pieces. An older backend does not send `stream`, and would be left
		# holding a head with no body it understands.
		sink = (
			_StreamSink(self, ws, request_id)
			if data.get("stream") and request_id is not None
			else None
		)
		if sink is not None:
			self._streaming[request_id] = sink
		try:
			status, headers, body_b64, error = await self._execute_http_proxy(
				data, sink=sink
			)
		finally:
			if sink is not None:
				self._streaming.pop(request_id, None)
		# A started stream has already sent its head and body; all that is left
		# is to close it off.
		if sink is not None and sink.started:
			await sink.finish(error)
			return
		response: dict[str, Any] = {
			"type": RELAY_WS_MSG_HTTP_PROXY_RESPONSE,
			"requestId": request_id,
			"status": status,
		}
		if headers is not None:
			response["headers"] = headers
		if body_b64 is not None:
			response["bodyB64"] = body_b64
		if error:
			response["error"] = error
		await self._send(ws, response)

	def _note_forward_host(self, headers: Any) -> None:
		"""Remember the friendly host this request came in on.

		Core has no way to learn the public name it is being served under — the
		relay reaches it over loopback — so the panel cannot offer to set
		``external_url`` without watching the traffic for it.  Best effort by
		design: a miss only costs the panel a pre-filled value.
		"""
		with suppress(Exception):  # noqa: BLE001 - never break a forward over a hint
			for name, value in _normalise_header_input(headers):
				if name.lower() != FORWARD_HOST_HEADER:
					continue
				host = value.strip()
				# Straight into a URL for the user, so refuse anything that
				# could carry a path, port trick or injected whitespace.
				if host and _SAFE_FORWARD_HOST_RE.fullmatch(host):
					self._hass.data.setdefault(DOMAIN, {})[FORWARD_HOST_KEY] = host
				return

	async def _execute_http_proxy(
		self, data: dict, *, sink: Optional["_StreamSink"] = None
	) -> tuple[int, Optional[list], Optional[str], Optional[str]]:
		"""Run one forwarded HTTP request; return ``(status, headers, bodyB64, error)``.

		``status`` is 0 on a local failure so the backend surfaces a 502.  Local
		redirects are returned verbatim (``allow_redirects=False``) so the browser
		follows them within the friendly domain.

		Paths under ``/t/<slug>/`` go to a configured LAN target (independent of
		``forward_ui``).  Everything else requires full-UI forwarding to local HA.
		"""
		method = str(data.get("method") or "GET").upper()
		path = data.get("path")
		if not isinstance(path, str) or not path.startswith("/"):
			return 0, None, None, "Refusing to proxy a non-absolute path."

		lan = parse_lan_path(path)
		if lan is not None:
			return await self._execute_lan_http_proxy(method, lan[0], lan[1], data)

		# Allowlisted webhooks are reachable without full-UI forwarding and
		# without a login — that is the whole point of a cloudhook. Nothing
		# else about the instance opens up: this matches one exact path per
		# explicitly listed id (see webhooks.py for why it is an allowlist).
		if is_forwardable_webhook(path, method, self._webhooks):
			return await self._proxy_http_to(
				method, self.local_url + path, data,
				error_timeout="Local Home Assistant timed out.",
				error_client="Local Home Assistant error",
			)

		if not self._forward_ui:
			return 0, None, None, "Full-UI forwarding is disabled for this Home Assistant."
		return await self._proxy_http_to(
			method, self.local_url + path, data,
			error_timeout="Local Home Assistant timed out.",
			error_client="Local Home Assistant error",
			sink=sink,
		)

	async def _execute_lan_http_proxy(
		self,
		method: str,
		slug: str,
		remainder: str,
		data: dict,
	) -> tuple[int, Optional[list], Optional[str], Optional[str]]:
		"""Proxy one request to a configured LAN route under ``/t/<slug>/``."""
		route = find_route(self._lan_routes, slug)
		if route is None:
			return 0, None, None, f"No LAN route configured for '{slug}'."
		if route.get(ROUTE_SCHEME) == "tcp":
			return 0, None, None, (
				f"LAN route '{slug}' is TCP-only; open it as a tunnel, not HTTP."
			)
		base = route_base_url(route)
		if not base:
			return 0, None, None, f"LAN route '{slug}' has an invalid host/port."
		status, headers, body_b64, error = await self._proxy_http_to(
			method, base.rstrip("/") + remainder, data,
			error_timeout=f"LAN target '{slug}' timed out.",
			error_client=f"LAN target '{slug}' error",
		)
		if headers is not None:
			headers = rewrite_response_headers(headers, slug=slug, route=route)
		return status, headers, body_b64, error

	async def _proxy_http_to(
		self,
		method: str,
		url: str,
		data: dict,
		*,
		error_timeout: str,
		error_client: str,
		sink: Optional["_StreamSink"] = None,
	) -> tuple[int, Optional[list], Optional[str], Optional[str]]:
		"""Shared HTTP forward to ``url`` (local HA or a LAN device).

		With a ``sink``, a body still arriving when the read budget runs out is
		handed back in pieces rather than refused.  A LAN route passes no sink:
		its response headers are rewritten after this returns, which streaming
		would bypass.
		"""
		req_headers = {
			name: value for name, value in _normalise_header_input(data.get("headers"))
			if name.lower() not in RELAY_FORWARD_STRIP_HEADERS
		}
		body: Optional[bytes] = None
		raw_b64 = data.get("bodyB64")
		if raw_b64:
			try:
				body = base64.b64decode(raw_b64)
			except (ValueError, TypeError):
				return 0, None, None, "Malformed request body."
			if len(body) > RELAY_FORWARD_MAX_BODY:
				return 0, None, None, "Request body too large."
		session = self._get_session()
		try:
			async with session.request(
				method, url,
				headers=req_headers,
				data=body,
				allow_redirects=False,
				timeout=aiohttp.ClientTimeout(total=RELAY_FORWARD_HTTP_TIMEOUT),
			) as resp:
				raw, refusal = await _read_forwardable_body(resp, sink=sink)
				if sink is not None and sink.started:
					# Already delivered, head and body both.
					return resp.status, None, None, None
				if refusal is not None:
					return 0, None, None, refusal
				out_headers = _filter_forward_headers(resp.headers.items())
				return resp.status, out_headers, base64.b64encode(raw).decode("ascii"), None
		except asyncio.TimeoutError:
			return 0, None, None, error_timeout
		except aiohttp.ClientError as err:
			return 0, None, None, f"{error_client}: {err}"

	# ── Full-UI forwarding: frontend WebSocket bridge ───────────────────────

	async def _handle_ws_open(self, ws: aiohttp.ClientWebSocketResponse, data: dict) -> None:
		"""Open a local frontend WebSocket and bridge it to the browser."""
		socket_id = data.get("socketId")
		if not socket_id:
			return
		# An ESPHome build/log stream is its own target: it is not the frontend
		# socket and not a LAN route, and it must work regardless of whether
		# full-UI forwarding is enabled.
		if data.get("target") == RELAY_RPC_TARGET_ESPHOME:
			await self._handle_esphome_ws_open(ws, socket_id, data)
			return
		path = data.get("path") or "/api/websocket"
		lan = parse_lan_path(path)
		if lan is not None:
			await self._handle_lan_ws_open(ws, socket_id, lan[0], lan[1])
			return
		if not self._forward_ui:
			await self._send(ws, {
				"type": RELAY_WS_MSG_WS_CLOSE, "socketId": socket_id,
				"code": 1008, "reason": "Full-UI forwarding is disabled.",
			})
			return
		# Only the frontend's own socket is bridgeable; refuse anything else
		# (exact match on the path portion, not a spoofable prefix).
		portion = _safe_path_portion(str(path))
		if portion is None or portion not in RELAY_FORWARD_WS_PATHS:
			await self._send(ws, {
				"type": RELAY_WS_MSG_WS_CLOSE, "socketId": socket_id,
				"code": 1008, "reason": "WebSocket path not permitted.",
			})
			return
		await self._open_bridged_ws(ws, socket_id, _to_ws_url(self.local_url, path))

	async def _handle_lan_ws_open(
		self,
		ws: aiohttp.ClientWebSocketResponse,
		socket_id: str,
		slug: str,
		remainder: str,
	) -> None:
		"""Open a WebSocket (or, for a ``tcp`` route, a raw TCP socket) to a
		configured LAN route under ``/t/<slug>/``."""
		route = find_route(self._lan_routes, slug)
		if route is None:
			await self._send(ws, {
				"type": RELAY_WS_MSG_WS_CLOSE, "socketId": socket_id,
				"code": 1008, "reason": f"No LAN route configured for '{slug}'.",
			})
			return
		if route.get(ROUTE_SCHEME) == "tcp":
			await self._open_bridged_tcp(ws, socket_id, route, slug)
			return
		if not route_allows_websocket(route):
			await self._send(ws, {
				"type": RELAY_WS_MSG_WS_CLOSE, "socketId": socket_id,
				"code": 1008, "reason": f"LAN WebSocket not permitted for '{slug}'.",
			})
			return
		base = route_base_url(route)
		if not base:
			await self._send(ws, {
				"type": RELAY_WS_MSG_WS_CLOSE, "socketId": socket_id,
				"code": 1008, "reason": f"LAN route '{slug}' is invalid.",
			})
			return
		await self._open_bridged_ws(ws, socket_id, _to_ws_url(base, remainder))

	async def _handle_esphome_ws_open(
		self,
		ws: aiohttp.ClientWebSocketResponse,
		socket_id: str,
		data: dict,
	) -> None:
		"""Bridge one ESPHome streaming build command (validate/compile/upload/…).

		This is what lets a remote agent flash a device and read its logs without
		any inbound exposure and without reaching the dashboard directly.  It
		matters more than it looks: the official add-on is host-networked with its
		web port disabled, behind an ingress nginx that admits only the Supervisor
		and 127.0.0.1, so on a default install the component is the *only* thing
		that can reach the dashboard at all.

		The command and configuration are validated here and the ``spawn`` frame is
		built locally, so nothing the caller sends is forwarded verbatim to a
		dashboard that has no authentication of its own.
		"""
		command = str(data.get("command") or "")
		if command not in ESPHOME_STREAM_COMMANDS:
			await self._send(ws, {
				"type": RELAY_WS_MSG_WS_CLOSE, "socketId": socket_id,
				"code": 1008, "reason": f"Unsupported ESPHome command: {command!r}.",
			})
			return
		configuration = str(data.get("configuration") or "")
		if not ESPHOME_CONFIG_RE.match(configuration):
			await self._send(ws, {
				"type": RELAY_WS_MSG_WS_CLOSE, "socketId": socket_id,
				"code": 1008, "reason": "Invalid ESPHome configuration filename.",
			})
			return
		base, problem = await self._resolve_esphome_base()
		if not base:
			await self._send(ws, {
				"type": RELAY_WS_MSG_WS_CLOSE, "socketId": socket_id,
				"code": 1011, "reason": problem or "ESPHome dashboard not found.",
			})
			return
		spawn: dict = {"type": "spawn", "configuration": configuration}
		port = data.get("port")
		if isinstance(port, str) and port:
			spawn["port"] = port
		_LOGGER.debug(
			"Relay (%s): ESPHome %s %s", self._server_id, command, configuration
		)
		await self._open_bridged_ws(
			ws, socket_id, _to_ws_url(base, f"/{command}"), initial=spawn
		)

	async def _open_bridged_ws(
		self,
		ws: aiohttp.ClientWebSocketResponse,
		socket_id: str,
		local_url: str,
		initial: Optional[dict] = None,
	) -> None:
		"""Connect to ``local_url`` and register the pump for this bridge.

		``initial`` is a frame sent as soon as the socket opens — the ESPHome
		``spawn`` command, which the component composes itself rather than
		relaying (see :meth:`_handle_esphome_ws_open`).
		"""
		try:
			local = await self._get_session().ws_connect(local_url, heartbeat=30)
		except (aiohttp.ClientError, asyncio.TimeoutError) as err:
			# A cached dashboard address may be stale (add-on restarted since
			# discovery), so drop it and let the next call re-discover.
			self._esphome_base_cache = None
			await self._send(ws, {
				"type": RELAY_WS_MSG_WS_CLOSE, "socketId": socket_id,
				"code": 1011, "reason": f"Local WebSocket error: {err}",
			})
			return
		if initial is not None:
			try:
				await local.send_json(initial)
			except (aiohttp.ClientError, asyncio.TimeoutError) as err:
				with suppress(Exception):
					await local.close()
				await self._send(ws, {
					"type": RELAY_WS_MSG_WS_CLOSE, "socketId": socket_id,
					"code": 1011, "reason": f"Local WebSocket error: {err}",
				})
				return
		self._ws_local[socket_id] = local
		await self._send(ws, {"type": RELAY_WS_MSG_WS_OPEN_ACK, "socketId": socket_id})
		self._ws_pumps[socket_id] = asyncio.ensure_future(
			self._pump_local_ws(ws, socket_id, local)
		)

	async def _pump_local_ws(
		self,
		ws: aiohttp.ClientWebSocketResponse,
		socket_id: str,
		local: aiohttp.ClientWebSocketResponse,
	) -> None:
		"""Forward frames from the local HA socket up to the browser, until closed."""
		close_code, close_reason = 1000, ""
		try:
			async for msg in local:
				if msg.type == aiohttp.WSMsgType.TEXT:
					await self._send(ws, {
						"type": RELAY_WS_MSG_WS_DATA, "socketId": socket_id, "text": msg.data,
					})
				elif msg.type == aiohttp.WSMsgType.BINARY:
					await self._send(ws, {
						"type": RELAY_WS_MSG_WS_DATA, "socketId": socket_id,
						"dataB64": base64.b64encode(msg.data).decode("ascii"),
					})
				elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
					break
		except (aiohttp.ClientError, asyncio.CancelledError):
			pass
		finally:
			self._ws_local.pop(socket_id, None)
			self._ws_pumps.pop(socket_id, None)
			with suppress(Exception):
				await self._send(ws, {
					"type": RELAY_WS_MSG_WS_CLOSE, "socketId": socket_id,
					"code": close_code, "reason": close_reason,
				})

	async def _open_bridged_tcp(
		self,
		ws: aiohttp.ClientWebSocketResponse,
		socket_id: str,
		route: dict,
		slug: str,
	) -> None:
		"""Open a raw TCP connection to ``route`` and register the pump for it.

		Reuses ws_open_ack/ws_data/ws_close unchanged: the other end (backend +
		CLI tunnel client) doesn't know or care that this bridge's local side is
		a TCP socket rather than a WebSocket.
		"""
		host = str(route.get(ROUTE_HOST) or "")
		try:
			port = int(route.get(ROUTE_PORT) or 0)
		except (TypeError, ValueError):
			port = 0
		if not host or port < 1 or port > 65535:
			await self._send(ws, {
				"type": RELAY_WS_MSG_WS_CLOSE, "socketId": socket_id,
				"code": 1011, "reason": f"LAN route '{slug}' has an invalid host/port.",
			})
			return
		try:
			reader, writer = await asyncio.open_connection(host, port)
		except (OSError, asyncio.TimeoutError) as err:
			await self._send(ws, {
				"type": RELAY_WS_MSG_WS_CLOSE, "socketId": socket_id,
				"code": 1011, "reason": f"LAN target '{slug}' connect failed: {err}",
			})
			return
		self._tcp_local[socket_id] = (reader, writer)
		await self._send(ws, {"type": RELAY_WS_MSG_WS_OPEN_ACK, "socketId": socket_id})
		self._tcp_pumps[socket_id] = asyncio.ensure_future(
			self._pump_local_tcp(ws, socket_id, reader)
		)

	async def _pump_local_tcp(
		self,
		ws: aiohttp.ClientWebSocketResponse,
		socket_id: str,
		reader: asyncio.StreamReader,
	) -> None:
		"""Forward bytes from the local TCP socket up to the tunnel, until closed."""
		close_code, close_reason = 1000, ""
		try:
			while True:
				chunk = await reader.read(65536)
				if not chunk:
					break
				await self._send(ws, {
					"type": RELAY_WS_MSG_WS_DATA, "socketId": socket_id,
					"dataB64": base64.b64encode(chunk).decode("ascii"),
				})
		except (OSError, asyncio.CancelledError):
			pass
		finally:
			tcp = self._tcp_local.pop(socket_id, None)
			self._tcp_pumps.pop(socket_id, None)
			if tcp is not None:
				with suppress(Exception):
					tcp[1].close()
			with suppress(Exception):
				await self._send(ws, {
					"type": RELAY_WS_MSG_WS_CLOSE, "socketId": socket_id,
					"code": close_code, "reason": close_reason,
				})

	async def request_lan_tcp_token(
		self, slug: str, ttl_seconds: int = LAN_TCP_TOKEN_DEFAULT_TTL
	) -> tuple[Optional[str], Optional[str]]:
		"""Ask the backend to mint a short-lived bearer token for a ``tcp`` LAN
		route, over the already-authenticated relay socket.

		Returns ``(token, None)`` or ``(None, error)``.  Called from the
		``mint_lan_tcp_token`` service (see services_remote.py) so a CLI tunnel
		client (e.g. ``npx home-assistant-mcp tunnel``) never needs its own
		credential — the token is scoped server-side to this server_id + slug.
		"""
		ws = self._ws
		if ws is None:
			return None, "Relay is not connected."
		request_id = str(uuid.uuid4())
		future: asyncio.Future = self._hass.loop.create_future()
		self._pending_requests[request_id] = future
		await self._send(ws, {
			"type": RELAY_WS_MSG_MINT_LAN_TCP_TOKEN,
			"requestId": request_id,
			"slug": slug,
			"ttlSeconds": ttl_seconds,
		})
		try:
			result = await asyncio.wait_for(future, timeout=RELAY_MINT_TOKEN_TIMEOUT)
		except asyncio.TimeoutError:
			self._pending_requests.pop(request_id, None)
			return None, "Timed out waiting for a tunnel token from Vome."
		if isinstance(result, dict):
			if result.get("error"):
				return None, str(result["error"])
			token = result.get("token")
		else:
			token = result  # legacy backends resolved with the bare token
		if not token:
			return None, "Vome did not return a tunnel token."
		return str(token), None

	async def send_access_events(self, events: list) -> None:
		"""Report this home's own access events to Vome (fire and forget).

		Silently does nothing while the relay is down: these are a convenience
		for the owner's log, and a home that cannot reach Vome has more
		pressing problems than a missing log line.  Nothing waits on a reply —
		there is none.
		"""
		ws = self._ws
		if ws is None or not events:
			return
		with suppress(Exception):
			await self._send(ws, {
				"type": RELAY_WS_MSG_ACCESS_EVENTS,
				"events": list(events)[:ACCESS_EVENTS_MAX_BATCH],
			})

	async def _handle_ws_data(self, data: dict) -> None:
		"""Forward one frame down to the local HA socket or TCP connection."""
		socket_id = data.get("socketId")
		local = self._ws_local.get(socket_id)
		if local is not None:
			try:
				if data.get("dataB64") is not None:
					await local.send_bytes(base64.b64decode(data["dataB64"]))
				elif data.get("text") is not None:
					await local.send_str(str(data["text"]))
			except (aiohttp.ClientError, ValueError, TypeError) as err:
				_LOGGER.debug("Relay (%s) ws_data forward failed: %s", self._server_id, err)
			return
		tcp = self._tcp_local.get(socket_id)
		if tcp is not None and data.get("dataB64") is not None:
			_, writer = tcp
			try:
				writer.write(base64.b64decode(data["dataB64"]))
				await writer.drain()
			except (OSError, ValueError, TypeError) as err:
				_LOGGER.debug("Relay (%s) tcp_data forward failed: %s", self._server_id, err)

	async def _handle_ws_close(self, data: dict) -> None:
		"""Close a bridged socket because the browser side went away."""
		await self._teardown_tunnel(data.get("socketId"))

	async def _teardown_tunnel(self, socket_id: Optional[str]) -> None:
		"""Cancel the pump and close the local socket (WebSocket or TCP) for one bridge."""
		if not socket_id:
			return
		pump = self._ws_pumps.pop(socket_id, None) or self._tcp_pumps.pop(socket_id, None)
		if pump is not None:
			pump.cancel()
			with suppress(asyncio.CancelledError):
				await pump
		local = self._ws_local.pop(socket_id, None)
		if local is not None:
			with suppress(Exception):
				await local.close()
		tcp = self._tcp_local.pop(socket_id, None)
		if tcp is not None:
			with suppress(Exception):
				tcp[1].close()

	async def _close_all_tunnels(self) -> None:
		"""Tear down every bridged socket (called when the relay drops)."""
		socket_ids = set(self._ws_local) | set(self._ws_pumps) | set(self._tcp_local) | set(self._tcp_pumps)
		for socket_id in socket_ids:
			await self._teardown_tunnel(socket_id)

	# ── Local Home Assistant execution ──────────────────────────────────────

	def _resolve_local(self) -> tuple[Optional[str], Optional[str]]:
		"""Return ``(base_url, token)`` for the local core API, or ``(None, None)``.

		``local_token`` is either the user's manual long-lived token (the
		"alternative connection" options) or the one async_start_relay minted
		via ``hass.auth``.  The Supervisor ``/core/api`` proxy is deliberately
		not used: it 401s requests authenticated with core's own token.
		"""
		if self._local_token:
			return self.local_url, self._local_token
		return None, None

	async def execute(
		self,
		method: Optional[str],
		path: Optional[str],
		body: Any,
		target: Optional[str] = None,
	) -> tuple[int, Optional[str], Optional[str]]:
		"""Execute one relayed call locally; return ``(status, body_text, error)``.

		``target`` selects the local service: the HA core REST API (``core``, the
		default), the ESPHome dashboard (``esphome``), or one allowlisted HA
		WebSocket command (``websocket``).  ``status`` is 0 on a local failure.
		"""
		if target == RELAY_RPC_TARGET_ESPHOME:
			return await self._execute_esphome(method, path, body)
		if target == RELAY_RPC_TARGET_WEBSOCKET:
			return await self._execute_websocket(body)
		return await self._execute_core(method, path, body)

	async def _execute_websocket(
		self, command: Any
	) -> tuple[int, Optional[str], Optional[str]]:
		"""Run one HA WebSocket command (portal validates scope before dispatch)."""
		if not isinstance(command, dict):
			return 0, None, "WebSocket command must be a JSON object."
		cmd_type = command.get("type")
		if not isinstance(cmd_type, str) or not WS_COMMAND_TYPE_RE.match(cmd_type):
			return 0, None, "Refusing to execute a malformed WebSocket command."
		try:
			raw = json.dumps(command)
		except (TypeError, ValueError):
			return 0, None, "WebSocket command must be JSON-serialisable."
		if len(raw) > RELAY_WS_MAX_COMMAND_BYTES:
			return 0, None, "WebSocket command too large."
		base, token = self._resolve_local()
		if not token:
			return 0, None, (
				"No local access token available; the component could not mint "
				"one (no owner user?) and none is configured in the relay options."
			)
		ws_url = _to_ws_url(base, "/api/websocket")
		session = self._get_session()
		payload = {k: v for k, v in command.items() if k != "id"}
		payload["id"] = 1
		try:
			async with session.ws_connect(
				ws_url, heartbeat=30, timeout=aiohttp.ClientTimeout(total=RELAY_RPC_TIMEOUT),
			) as ws:
				msg = await ws.receive_json(timeout=RELAY_RPC_TIMEOUT)
				if msg.get("type") != "auth_required":
					return 0, None, f"Unexpected WebSocket greeting: {msg.get('type')}"
				await ws.send_json({"type": "auth", "access_token": token})
				while True:
					auth_msg = await ws.receive_json(timeout=RELAY_RPC_TIMEOUT)
					if auth_msg.get("type") == "auth_ok":
						break
					if auth_msg.get("type") == "auth_invalid":
						return 0, None, "Local Home Assistant rejected the access token."
				await ws.send_json(payload)
				while True:
					result = await ws.receive_json(timeout=RELAY_RPC_TIMEOUT)
					if result.get("type") == "result" and result.get("id") == 1:
						if result.get("success"):
							return 200, json.dumps(result.get("result")), None
						err = result.get("error") or {}
						code = int(err.get("code") or 400) if isinstance(err, dict) else 400
						return code, json.dumps({"error": err}), None
		except asyncio.TimeoutError:
			return 0, None, "Local Home Assistant WebSocket timed out."
		except aiohttp.ClientError as err:
			return 0, None, f"Local Home Assistant WebSocket error: {err}"

	async def _execute_core(
		self, method: Optional[str], path: Optional[str], body: Any
	) -> tuple[int, Optional[str], Optional[str]]:
		"""Proxy one HA core REST call.  Only ``/api/...`` paths are permitted."""
		portion = _safe_path_portion(path)
		if portion is None or not (portion.startswith("/api/") or portion == "/api/"):
			return 0, None, "Refusing to execute a non-/api path."
		method = (method or "GET").upper()
		if method not in RELAY_ALLOWED_METHODS:
			return 0, None, f"Unsupported method: {method}"
		base, token = self._resolve_local()
		if not token:
			return 0, None, (
				"No local access token available; the component could not mint "
				"one (no owner user?) and none is configured in the relay options."
			)
		url = base + path
		headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
		session = self._get_session()
		try:
			async with session.request(
				method,
				url,
				headers=headers,
				json=body if body is not None else None,
				timeout=aiohttp.ClientTimeout(total=RELAY_RPC_TIMEOUT),
			) as resp:
				text = await resp.text()
				return resp.status, text, None
		except asyncio.TimeoutError:
			return 0, None, "Local Home Assistant timed out."
		except aiohttp.ClientError as err:
			return 0, None, f"Local Home Assistant error: {err}"

	async def _execute_esphome(
		self, method: Optional[str], path: Optional[str], body: Any
	) -> tuple[int, Optional[str], Optional[str]]:
		"""Proxy one ESPHome dashboard REST call (list / version / read+write YAML).

		Only the allow-listed REST paths/methods are permitted; the streaming build
		commands are not tunnelled.  ``body`` for a YAML write is sent verbatim as
		``application/yaml``; reads carry no body.
		"""
		# Exact match on the path portion (query excluded) — a prefix check would
		# let /devices-x or /edit/../delete slip through.
		portion = _safe_path_portion(path)
		if portion is None or portion not in ESPHOME_ALLOWED_PATHS:
			return 0, None, "Refusing to proxy a non-allowlisted ESPHome path."
		method = (method or "GET").upper()
		if method not in ESPHOME_ALLOWED_METHODS:
			return 0, None, f"Unsupported ESPHome method: {method}"
		base, problem = await self._resolve_esphome_base()
		if not base:
			return 0, None, problem or (
				"ESPHome dashboard not found. Install the ESPHome add-on, or set the "
				"ESPHome dashboard URL in the Vome relay options."
			)
		url = base + path
		session = self._get_session()
		# A YAML write is raw text; everything else is a bodyless read.
		data = body if isinstance(body, str) else None
		headers = {"Content-Type": "application/yaml"} if data is not None else {}
		try:
			async with session.request(
				method,
				url,
				headers=headers,
				data=data,
				timeout=aiohttp.ClientTimeout(total=RELAY_RPC_TIMEOUT),
			) as resp:
				text = await resp.text()
				return resp.status, text, None
		except asyncio.TimeoutError:
			return 0, None, "ESPHome dashboard timed out."
		except aiohttp.ClientError as err:
			# The cached address may be stale (add-on stopped or restarted since
			# discovery) — drop it so the next call re-discovers.
			self._esphome_base_cache = None
			return 0, None, (
				f"ESPHome dashboard error: {err}. "
				"Check the ESPHome add-on is running, then retry."
			)

	async def _resolve_esphome_base(self) -> tuple[Optional[str], Optional[str]]:
		"""Return ``(base_url, None)`` or ``(None, problem)`` for the dashboard.

		An explicitly configured URL wins; otherwise, on HAOS / Supervised installs
		the ESPHome add-on is discovered via the Supervisor API.  Only a *started*
		add-on is reachable, so the add-on state is checked here and reported
		clearly instead of surfacing an opaque connect error.  Successful lookups
		are cached; the cache is dropped after a connection failure so a restarted
		add-on is re-discovered.
		"""
		if self._esphome_url:
			return self._esphome_url, None
		if self._esphome_base_cache:
			return self._esphome_base_cache, None
		supervisor = os.environ.get(SUPERVISOR_TOKEN_ENV)
		if not supervisor:
			return None, (
				"ESPHome dashboard not found. Set the ESPHome dashboard URL in the "
				"Vome relay options (no Supervisor on this install)."
			)
		session = self._get_session()
		headers = {"Authorization": f"Bearer {supervisor}"}
		payload, problem = await self._supervisor_get(session, headers, SUPERVISOR_ADDONS_URL)
		if payload is None:
			return None, problem
		addons = [
			addon
			for addon in ((payload or {}).get("data") or {}).get("addons") or []
			if "esphome" in (addon.get("slug") or "")
		]
		if not addons:
			return None, (
				"ESPHome add-on not found. Install the ESPHome Device Builder add-on, "
				"or set the ESPHome dashboard URL in the Vome relay options."
			)
		started = next(
			(a for a in addons if a.get("state") == ESPHOME_ADDON_STATE_STARTED), None
		)
		if started is None:
			names = ", ".join(sorted(a.get("name") or a.get("slug") or "?" for a in addons))
			return None, (
				f"The ESPHome add-on ({names}) is installed but not running. "
				"Start it under Settings → Add-ons, then retry."
			)
		slug = started.get("slug") or ""
		base = await self._esphome_base_for_addon(session, headers, slug)
		self._esphome_base_cache = base
		_LOGGER.info(
			"Relay (%s): discovered ESPHome dashboard at %s",
			self._server_id, self._esphome_base_cache,
		)
		return self._esphome_base_cache, None

	async def _esphome_base_for_addon(
		self, session: aiohttp.ClientSession, headers: dict, slug: str
	) -> str:
		"""Build the dashboard base URL for a started ESPHome add-on.

		The official add-on is host-networked with its web port disabled by
		default: nothing listens on ``<hostname>:6052``.  The dashboard sits
		behind a dynamic *ingress* port whose nginx admits only the Supervisor
		and 127.0.0.1 — and core shares the host network, so localhost is the
		admitted route (the add-on's own hassio discovery payload says the
		same).  An explicitly mapped web port takes precedence; the legacy
		``<hostname>:6052`` is kept as the last resort for third-party add-ons.
		"""
		hostname = slug.replace("_", "-")
		fallback = f"http://{hostname}:{ESPHOME_DEFAULT_PORT}"
		info_payload, problem = await self._supervisor_get(
			session, headers, SUPERVISOR_ADDON_INFO_URL.format(slug=slug)
		)
		if info_payload is None:
			_LOGGER.debug(
				"Relay (%s): add-on info lookup failed (%s); falling back to %s",
				self._server_id, problem, fallback,
			)
			return fallback
		info = (info_payload or {}).get("data") or {}
		web_port = (info.get("network") or {}).get(ESPHOME_WEB_PORT_KEY)
		if web_port:
			return f"http://{hostname}:{web_port}"
		ingress_port = info.get("ingress_port")
		if info.get("ingress") and ingress_port:
			return f"http://{ESPHOME_INGRESS_HOST}:{ingress_port}"
		return fallback

	async def _supervisor_get(
		self, session: aiohttp.ClientSession, headers: dict, url: str
	) -> tuple[Optional[dict], Optional[str]]:
		"""GET a Supervisor API URL; return ``(json, None)`` or ``(None, problem)``."""
		try:
			async with session.get(
				url, headers=headers, timeout=aiohttp.ClientTimeout(total=10)
			) as resp:
				if resp.status != 200:
					return None, f"Supervisor add-on lookup failed (HTTP {resp.status})."
				return await resp.json(), None
		except (asyncio.TimeoutError, aiohttp.ClientError, ValueError) as err:
			_LOGGER.debug("Relay (%s): Supervisor lookup failed: %s", self._server_id, err)
			return None, f"Supervisor add-on lookup failed: {err}"


# ── Device-authorisation HTTP helpers (used by the config/options flow) ──────

async def _post_portal_json(
	session: aiohttp.ClientSession, url: str, payload: dict
) -> dict:
	"""POST JSON to the portal, raising a diagnosable error on failure.

	``raise_for_status`` alone hides the response body, which is where the
	portal explains *why* (e.g. a CSRF or proxy rejection) — include it.
	"""
	async with session.post(url, json=payload, timeout=_HTTP_TIMEOUT) as resp:
		if resp.status >= 400:
			body = (await resp.text())[:300]
			raise RuntimeError(f"Portal returned HTTP {resp.status} for {url}: {body}")
		return await resp.json()


async def async_request_device_code(
	session: aiohttp.ClientSession, portal_url: str, name: Optional[str] = None
) -> dict:
	"""Start a device-authorisation; returns the portal's JSON (codes + URL)."""
	url = (portal_url or DEFAULT_PORTAL_URL).rstrip("/") + RELAY_DEVICE_CODE_PATH
	return await _post_portal_json(session, url, {"name": name or ""})


async def async_poll_device_token(
	session: aiohttp.ClientSession, portal_url: str, device_code: str
) -> dict:
	"""Poll for approval; returns ``{'status': ...}`` (and creds once approved)."""
	url = (portal_url or DEFAULT_PORTAL_URL).rstrip("/") + RELAY_DEVICE_TOKEN_PATH
	return await _post_portal_json(session, url, {"device_code": device_code})


# ── Entry lifecycle integration ─────────────────────────────────────────────

def _relay_config(entry) -> Optional[dict]:
	"""Return the relay link dict from an entry's options, or None."""
	options = entry.options or {}
	relay = options.get(CONF_RELAY)
	if isinstance(relay, dict) and relay.get(CONF_RELAY_SERVER_ID) and relay.get(CONF_RELAY_SECRET):
		return relay
	return None


async def async_start_relay(hass: HomeAssistant, entry) -> None:
	"""Start the relay for ``entry`` if it is linked; otherwise ensure it is stopped.

	Idempotent and safe to call after an options change: it tears down any
	existing client and starts a fresh one matching the current config.
	"""
	await async_stop_relay(hass, entry)
	relay = _relay_config(entry)
	if not relay:
		return
	# A manually configured token (alternative connection) wins; otherwise mint
	# a long-lived token for the local REST API via hass.auth.
	local_token = relay.get(CONF_RELAY_LOCAL_TOKEN)
	if not local_token:
		try:
			local_token = await async_ensure_local_access_token(hass)
		except Exception as err:  # noqa: BLE001 - never block the relay on minting
			_LOGGER.error("Relay: minting a local access token failed: %s", err)
			local_token = None
	client = RelayClient(
		hass,
		server_id=relay[CONF_RELAY_SERVER_ID],
		secret=relay[CONF_RELAY_SECRET],
		ws_url=relay.get(CONF_RELAY_WS_URL),
		local_token=local_token,
		local_url=relay.get(CONF_RELAY_LOCAL_URL),
		esphome_url=relay.get(CONF_RELAY_ESPHOME_URL),
		forward_ui=bool(relay.get(CONF_RELAY_FORWARD_UI)),
		lan_routes=relay.get(CONF_RELAY_LAN_ROUTES),
		webhooks=relay.get(CONF_RELAY_WEBHOOKS),
	)
	hass.data.setdefault(DOMAIN, {}).setdefault(_RELAYS_KEY, {})[entry.entry_id] = client
	client.start()
	# Log the resolved local URL: when it is wrong the relay still connects and
	# still looks healthy, so this line is the only external evidence of what we
	# are actually dialling.
	_LOGGER.info(
		"Relay started for entry %s (%s), local core %s%s",
		entry.entry_id,
		relay[CONF_RELAY_SERVER_ID],
		client.local_url,
		" (configured override)" if relay.get(CONF_RELAY_LOCAL_URL) else " (auto-detected)",
	)


async def async_stop_relay(hass: HomeAssistant, entry) -> None:
	"""Stop and remove the relay client for ``entry`` if running."""
	relays = hass.data.get(DOMAIN, {}).get(_RELAYS_KEY, {})
	client = relays.pop(entry.entry_id, None)
	if client is not None:
		await client.stop()


def get_relay_client(hass: HomeAssistant, entry_id: str) -> Optional[RelayClient]:
	"""Return the live ``RelayClient`` for a linked entry, or ``None``.

	Public accessor so other modules (e.g. services_remote.mint_lan_tcp_token)
	don't reach into the private ``_RELAYS_KEY`` registry directly.
	"""
	return hass.data.get(DOMAIN, {}).get(_RELAYS_KEY, {}).get(entry_id)
