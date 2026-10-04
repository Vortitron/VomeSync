"""Vome's TURN logins, handed to Home Assistant's WebRTC cameras.

A WebRTC camera stream starts with the browser and go2rtc (inside HA)
swapping candidates over HA's websocket, then sending media straight to
each other.  On a phone network, behind carrier NAT or on a hosted VM whose
host filters UDP, neither can reach the other and the stream never starts.
A TURN server both can reach relays the media instead — which is what
Nabu Casa's cloud provides its subscribers.

Vome mints a login per home (``GET /api/sync/agent/ice-servers``, with the
credential this home already holds) and this module registers it through
``web_rtc.async_register_ice_servers``.  HA then gives it to the browser
and to go2rtc alike.  Logins last about a day; we ask again at half-life.

When Vome says TURN is not on for this home (not open yet, a guest run,
the month's budget spent) nothing is registered and HA keeps its default
STUN, so cameras behave exactly as they did before.

``web_rtc`` arrived in Core 2024.11.  On an older Core, or one with no
cameras (web_rtc never loads), this does nothing.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Callable, Optional

from homeassistant.const import EVENT_COMPONENT_LOADED
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from . import health_score as hs
from .const import AGENT_ICE_SERVERS_PATH, DOMAIN
from .relay_client import _agent_request

_LOGGER = logging.getLogger(__name__)

_KEY = "_ice_servers"

# After a failure: start at five minutes, double to at most an hour.
_BACKOFF_START = 300
_BACKOFF_MAX = 3600
# Never ask more often than this, whatever Vome says.
_MIN_DELAY = 60


def _web_rtc() -> Optional[tuple[Callable, type]]:
	"""``(async_register_ice_servers, RTCIceServer)``, or None on an old Core."""
	try:
		from homeassistant.components.web_rtc import async_register_ice_servers
		from webrtc_models import RTCIceServer
	except ImportError:
		return None
	return async_register_ice_servers, RTCIceServer


class IceServers:
	"""Keeps one home's TURN login registered with HA and fresh."""

	def __init__(self, hass: HomeAssistant, entry) -> None:
		self.hass = hass
		self.entry = entry
		self._servers: list = []
		self._expires_at = 0.0
		self._unregister: Optional[Callable[[], None]] = None
		self._unlisten: Optional[Callable[[], None]] = None
		self._task: Optional[asyncio.Task] = None
		self._backoff = _BACKOFF_START
		# What the last answer said, for diagnostics and the log.
		self.status: dict[str, Any] = {"enabled": False, "reason": "not asked yet"}

	# ── HA's side ───────────────────────────────────────────────────────

	def current(self) -> list:
		"""What HA asks for each time a stream starts; nothing once expired."""
		if self._servers and time.time() >= self._expires_at:
			self._servers = []
		return self._servers

	def _register(self) -> None:
		web_rtc = _web_rtc()
		if web_rtc is None or self._unregister is not None:
			return
		register, _cls = web_rtc
		self._unregister = register(self.hass, self.current)
		_LOGGER.debug("Vome TURN registered with web_rtc")

	def _register_when_loaded(self) -> None:
		"""web_rtc loads with the first camera, which may be after us."""
		if "web_rtc" in self.hass.config.components:
			self._register()
			return

		def loaded(event) -> None:
			if event.data.get("component") != "web_rtc":
				return
			self._register()
			if self._unlisten:
				self._unlisten()
				self._unlisten = None

		self._unlisten = self.hass.bus.async_listen(EVENT_COMPONENT_LOADED, loaded)

	# ── Vome's side ─────────────────────────────────────────────────────

	def _apply(self, payload: dict) -> float:
		"""Take Vome's answer; return how many seconds until we ask again."""
		if not payload.get("enabled"):
			self._servers = []
			self._expires_at = 0.0
			self.status = {"enabled": False, "reason": str(payload.get("reason") or "")}
			return float(payload.get("retry_after") or _BACKOFF_MAX)

		web_rtc = _web_rtc()
		cls = web_rtc[1] if web_rtc else None
		servers = []
		for raw in payload.get("ice_servers") or []:
			urls = raw.get("urls") if isinstance(raw, dict) else None
			if not urls:
				continue
			fields = {
				"urls": urls,
				"username": raw.get("username"),
				"credential": raw.get("credential"),
			}
			servers.append(cls(**fields) if cls else fields)
		self._servers = servers
		self._expires_at = float(payload.get("expires_at") or 0)
		self.status = {"enabled": True, "servers": len(servers), "expires_at": self._expires_at}
		return float(payload.get("refresh_after") or _BACKOFF_MAX)

	async def refresh(self) -> float:
		"""Ask Vome once; return the delay before the next ask."""
		_server_id, secret = hs._agent_credentials(self.entry)
		try:
			payload = await _agent_request(
				async_get_clientsession(self.hass), "GET",
				hs._portal_url(self.entry), AGENT_ICE_SERVERS_PATH, secret,
			)
		except Exception as err:  # noqa: BLE001 - the camera path must never break setup
			delay = self._backoff
			self._backoff = min(self._backoff * 2, _BACKOFF_MAX)
			# Keep a login we already hold: it is good until it expires.
			self.status = {**self.status, "last_error": str(err)}
			_LOGGER.debug("Vome TURN: asking failed, retry in %ss: %s", delay, err)
			return delay
		self._backoff = _BACKOFF_START
		was = self.status.get("enabled")
		delay = self._apply(payload)
		if self.status["enabled"] != was:
			if self.status["enabled"]:
				_LOGGER.info("Vome TURN: cameras can stream from anywhere (%d servers)",
					self.status["servers"])
			else:
				_LOGGER.info("Vome TURN is off for this home: %s", self.status["reason"])
		return max(_MIN_DELAY, delay)

	async def _run(self) -> None:
		while True:
			delay = await self.refresh()
			await asyncio.sleep(delay)

	# ── Lifecycle ───────────────────────────────────────────────────────

	def start(self) -> None:
		if _web_rtc() is None:
			_LOGGER.debug("Vome TURN: this Core has no web_rtc; skipping")
			return
		self._register_when_loaded()
		self._task = self.hass.async_create_background_task(
			self._run(), f"{DOMAIN} ice servers",
		)

	async def stop(self) -> None:
		if self._task is not None:
			self._task.cancel()
			try:
				await self._task
			except (asyncio.CancelledError, Exception):  # noqa: BLE001
				pass
			self._task = None
		if self._unlisten:
			self._unlisten()
			self._unlisten = None
		if self._unregister:
			self._unregister()
			self._unregister = None
		self._servers = []


async def async_start_ice_servers(hass: HomeAssistant, entry) -> None:
	"""Start keeping this entry's TURN login registered, if it is linked."""
	await async_stop_ice_servers(hass, entry)
	try:
		if not hs.is_linked(entry):
			return
		ice = IceServers(hass, entry)
		hass.data.setdefault(DOMAIN, {}).setdefault(_KEY, {})[entry.entry_id] = ice
		ice.start()
	except Exception:  # noqa: BLE001 - cameras must never cost the integration its setup
		_LOGGER.exception("Vome TURN could not start")


async def async_stop_ice_servers(hass: HomeAssistant, entry) -> None:
	ice = hass.data.get(DOMAIN, {}).get(_KEY, {}).pop(entry.entry_id, None)
	if ice is not None:
		await ice.stop()
