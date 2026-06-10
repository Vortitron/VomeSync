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
import json
import logging
import os
from contextlib import suppress
from datetime import timedelta
from typing import Any, Optional

import aiohttp
from homeassistant.auth.models import TOKEN_TYPE_LONG_LIVED_ACCESS_TOKEN
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
	CONF_RELAY,
	CONF_RELAY_ESPHOME_URL,
	CONF_RELAY_LOCAL_TOKEN,
	CONF_RELAY_LOCAL_URL,
	CONF_RELAY_SECRET,
	CONF_RELAY_SERVER_ID,
	CONF_RELAY_WS_URL,
	DEFAULT_LOCAL_CORE_URL,
	DEFAULT_PORTAL_URL,
	DEFAULT_RELAY_WS_URL,
	DOMAIN,
	ESPHOME_ALLOWED_METHODS,
	ESPHOME_ALLOWED_PATHS,
	ESPHOME_DEFAULT_PORT,
	RELAY_ALLOWED_METHODS,
	RELAY_DEVICE_CODE_PATH,
	RELAY_DEVICE_TOKEN_PATH,
	RELAY_RECONNECT_DELAY,
	RELAY_RECONNECT_MAX_DELAY,
	RELAY_RPC_TARGET_ESPHOME,
	RELAY_RPC_TIMEOUT,
	RELAY_WS_MSG_HA_RPC,
	RELAY_WS_MSG_HA_RPC_RESPONSE,
	RELAY_WS_MSG_HELLO,
	RELAY_WS_MSG_PING,
	RELAY_WS_MSG_PONG,
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
		session: Optional[aiohttp.ClientSession] = None,
	) -> None:
		self._hass = hass
		self._server_id = server_id
		self._secret = secret
		self._ws_url = ws_url or DEFAULT_RELAY_WS_URL
		self._local_token = local_token
		self._local_url = local_url or DEFAULT_LOCAL_CORE_URL
		self._esphome_url = (esphome_url or "").rstrip("/") or None
		# Cache for the auto-discovered ESPHome dashboard base (Supervisor installs).
		self._esphome_base_cache: Optional[str] = None
		self._session = session
		self._task: Optional[asyncio.Task] = None
		self._ws: Optional[aiohttp.ClientWebSocketResponse] = None
		self._closing = False

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
		_LOGGER.info("Relay disconnected from Vome (%s)", self._server_id)

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
		elif mtype == RELAY_WS_MSG_PING:
			await ws.send_str(json.dumps({"type": RELAY_WS_MSG_PONG}))
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
		await ws.send_str(json.dumps(response))

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
		if not isinstance(path, str) or not (path.startswith("/api/") or path == "/api/"):
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
		if not isinstance(path, str) or not path.startswith(ESPHOME_ALLOWED_PATHS):
			return 0, None, "Refusing to proxy a non-allowlisted ESPHome path."
		method = (method or "GET").upper()
		if method not in ESPHOME_ALLOWED_METHODS:
			return 0, None, f"Unsupported ESPHome method: {method}"
		base = await self._resolve_esphome_base()
		if not base:
			return 0, None, (
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
			return 0, None, f"ESPHome dashboard error: {err}"

	async def _resolve_esphome_base(self) -> Optional[str]:
		"""Return the local ESPHome dashboard base URL, or ``None``.

		An explicitly configured URL wins; otherwise, on HAOS / Supervised installs
		we discover the ESPHome add-on via the Supervisor API and address it by its
		internal hostname (cached after the first lookup).
		"""
		if self._esphome_url:
			return self._esphome_url
		if self._esphome_base_cache:
			return self._esphome_base_cache
		supervisor = os.environ.get(SUPERVISOR_TOKEN_ENV)
		if not supervisor:
			return None
		session = self._get_session()
		headers = {"Authorization": f"Bearer {supervisor}"}
		try:
			async with session.get(
				SUPERVISOR_ADDONS_URL,
				headers=headers,
				timeout=aiohttp.ClientTimeout(total=10),
			) as resp:
				if resp.status != 200:
					return None
				payload = await resp.json()
		except (asyncio.TimeoutError, aiohttp.ClientError, ValueError) as err:
			_LOGGER.debug("Relay (%s): ESPHome add-on discovery failed: %s", self._server_id, err)
			return None
		addons = ((payload or {}).get("data") or {}).get("addons") or []
		for addon in addons:
			slug = addon.get("slug") or ""
			if "esphome" in slug:
				# Supervisor exposes each add-on on the internal network by its slug
				# with underscores rendered as hyphens.
				hostname = slug.replace("_", "-")
				self._esphome_base_cache = f"http://{hostname}:{ESPHOME_DEFAULT_PORT}"
				_LOGGER.info("Relay (%s): discovered ESPHome dashboard at %s", self._server_id, self._esphome_base_cache)
				return self._esphome_base_cache
		return None


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
