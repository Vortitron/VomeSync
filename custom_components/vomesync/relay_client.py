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
from contextlib import suppress
from datetime import timedelta
from typing import Any, Optional
from urllib.parse import unquote

import aiohttp
from homeassistant.auth.models import TOKEN_TYPE_LONG_LIVED_ACCESS_TOKEN
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
	CONF_RELAY,
	CONF_RELAY_ESPHOME_URL,
	CONF_RELAY_FORWARD_UI,
	CONF_RELAY_LOCAL_TOKEN,
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
	ESPHOME_DEFAULT_PORT,
	ESPHOME_INGRESS_HOST,
	ESPHOME_WEB_PORT_KEY,
	RELAY_ALLOWED_METHODS,
	RELAY_DEVICE_CODE_PATH,
	RELAY_DEVICE_TOKEN_PATH,
	RELAY_FORWARD_HTTP_TIMEOUT,
	RELAY_FORWARD_MAX_BODY,
	RELAY_FORWARD_STRIP_HEADERS,
	RELAY_FORWARD_WS_PATHS,
	RELAY_RECONNECT_DELAY,
	RELAY_RECONNECT_MAX_DELAY,
	RELAY_RPC_TARGET_ESPHOME,
	RELAY_RPC_TIMEOUT,
	RELAY_WS_MSG_HA_RPC,
	RELAY_WS_MSG_HA_RPC_RESPONSE,
	RELAY_WS_MSG_HELLO,
	RELAY_WS_MSG_HTTP_PROXY,
	RELAY_WS_MSG_HTTP_PROXY_RESPONSE,
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
		session: Optional[aiohttp.ClientSession] = None,
	) -> None:
		self._hass = hass
		self._server_id = server_id
		self._secret = secret
		self._ws_url = ws_url or DEFAULT_RELAY_WS_URL
		self._local_token = local_token
		self._local_url = local_url or DEFAULT_LOCAL_CORE_URL
		self._esphome_url = (esphome_url or "").rstrip("/") or None
		# Full-UI forwarding is opt-in: it brokers the whole browser session, not
		# just the scoped /api surface, so the owner must enable it deliberately.
		self._forward_ui = bool(forward_ui)
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
		# Serialise writes to the single relay socket: HTTP responses and many
		# concurrent WS frames share it, and aiohttp does not guard interleaving.
		self._send_lock = asyncio.Lock()

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

	async def stop(self) -> None:
		"""Stop the relay loop and close the socket."""
		self._closing = True
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
		elif mtype == RELAY_WS_MSG_HELLO:
			_LOGGER.debug("Relay (%s): hello acknowledged", self._server_id)

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
		status, headers, body_b64, error = await self._execute_http_proxy(data)
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

	async def _execute_http_proxy(
		self, data: dict
	) -> tuple[int, Optional[list], Optional[str], Optional[str]]:
		"""Run one forwarded HTTP request; return ``(status, headers, bodyB64, error)``.

		``status`` is 0 on a local failure so the backend surfaces a 502.  Local
		redirects are returned verbatim (``allow_redirects=False``) so the browser
		follows them within the friendly domain.
		"""
		if not self._forward_ui:
			return 0, None, None, "Full-UI forwarding is disabled for this Home Assistant."
		method = str(data.get("method") or "GET").upper()
		path = data.get("path")
		if not isinstance(path, str) or not path.startswith("/"):
			return 0, None, None, "Refusing to proxy a non-absolute path."
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
		url = self._local_url.rstrip("/") + path
		session = self._get_session()
		try:
			async with session.request(
				method, url,
				headers=req_headers,
				data=body,
				allow_redirects=False,
				timeout=aiohttp.ClientTimeout(total=RELAY_FORWARD_HTTP_TIMEOUT),
			) as resp:
				raw = await resp.read()
				if len(raw) > RELAY_FORWARD_MAX_BODY:
					return 0, None, None, "Response body too large to forward."
				out_headers = _filter_forward_headers(resp.headers.items())
				return resp.status, out_headers, base64.b64encode(raw).decode("ascii"), None
		except asyncio.TimeoutError:
			return 0, None, None, "Local Home Assistant timed out."
		except aiohttp.ClientError as err:
			return 0, None, None, f"Local Home Assistant error: {err}"

	# ── Full-UI forwarding: frontend WebSocket bridge ───────────────────────

	async def _handle_ws_open(self, ws: aiohttp.ClientWebSocketResponse, data: dict) -> None:
		"""Open a local frontend WebSocket and bridge it to the browser."""
		socket_id = data.get("socketId")
		if not socket_id:
			return
		if not self._forward_ui:
			await self._send(ws, {
				"type": RELAY_WS_MSG_WS_CLOSE, "socketId": socket_id,
				"code": 1008, "reason": "Full-UI forwarding is disabled.",
			})
			return
		path = data.get("path") or "/api/websocket"
		# Only the frontend's own socket is bridgeable; refuse anything else
		# (exact match on the path portion, not a spoofable prefix).
		portion = _safe_path_portion(str(path))
		if portion is None or portion not in RELAY_FORWARD_WS_PATHS:
			await self._send(ws, {
				"type": RELAY_WS_MSG_WS_CLOSE, "socketId": socket_id,
				"code": 1008, "reason": "WebSocket path not permitted.",
			})
			return
		try:
			local = await self._get_session().ws_connect(
				_to_ws_url(self._local_url, path), heartbeat=30,
			)
		except (aiohttp.ClientError, asyncio.TimeoutError) as err:
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

	async def _handle_ws_data(self, data: dict) -> None:
		"""Forward one browser frame down to the local HA socket."""
		socket_id = data.get("socketId")
		local = self._ws_local.get(socket_id)
		if local is None:
			return
		try:
			if data.get("dataB64") is not None:
				await local.send_bytes(base64.b64decode(data["dataB64"]))
			elif data.get("text") is not None:
				await local.send_str(str(data["text"]))
		except (aiohttp.ClientError, ValueError, TypeError) as err:
			_LOGGER.debug("Relay (%s) ws_data forward failed: %s", self._server_id, err)

	async def _handle_ws_close(self, data: dict) -> None:
		"""Close a bridged socket because the browser side went away."""
		await self._teardown_tunnel(data.get("socketId"))

	async def _teardown_tunnel(self, socket_id: Optional[str]) -> None:
		"""Cancel the pump and close the local socket for one bridge."""
		if not socket_id:
			return
		pump = self._ws_pumps.pop(socket_id, None)
		if pump is not None:
			pump.cancel()
			with suppress(asyncio.CancelledError):
				await pump
		local = self._ws_local.pop(socket_id, None)
		if local is not None:
			with suppress(Exception):
				await local.close()

	async def _close_all_tunnels(self) -> None:
		"""Tear down every bridged socket (called when the relay drops)."""
		for socket_id in list(self._ws_local) + list(self._ws_pumps):
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
			return self._local_url, self._local_token
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
		default) or the ESPHome dashboard (``esphome``).  ``status`` is 0 on a
		local failure, so the broker surfaces it as a 502.
		"""
		if target == RELAY_RPC_TARGET_ESPHOME:
			return await self._execute_esphome(method, path, body)
		return await self._execute_core(method, path, body)

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
	)
	hass.data.setdefault(DOMAIN, {}).setdefault(_RELAYS_KEY, {})[entry.entry_id] = client
	client.start()
	_LOGGER.info("Relay started for entry %s (%s)", entry.entry_id, relay[CONF_RELAY_SERVER_ID])


async def async_stop_relay(hass: HomeAssistant, entry) -> None:
	"""Stop and remove the relay client for ``entry`` if running."""
	relays = hass.data.get(DOMAIN, {}).get(_RELAYS_KEY, {})
	client = relays.pop(entry.entry_id, None)
	if client is not None:
		await client.stop()
