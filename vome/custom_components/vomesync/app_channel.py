"""The Vome app, when it is installed, and the YAML only it writes.

An integration must not write or patch the owner's YAML configuration.  An
app with ``/config`` mapped may (the File editor and Studio Code Server
exist for exactly that), so the YAML that the relay's file API and the
chat's automation tools change is handed to the Vome app, and refused with
a reason when there is no app.

The app checks in every couple of minutes with ``vomesync.app_check_in``,
an admin-only action, naming the address on Home Assistant's internal
network where it listens.  The answer carries the secret the app then
requires on every request from here.  The secret travels only in that
answer, never in a call's data, so it is not on the event bus; it lives in
memory, and a restart makes a new one, which the app picks up on its next
check-in.
"""
from __future__ import annotations

import asyncio
import ipaddress
import re
import secrets
import time
from typing import Any, Optional
from urllib.parse import urlparse

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import DOMAIN

_DATA_KEY = "_app_channel"
# The app checks in every two minutes; one missed check-in is not an absence.
STALE_AFTER = 600
REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=30)
# An app's hostname on Home Assistant's network is one label, like
# ``a0d7b954-vome``; an address there is private.  Nothing else is accepted,
# so the address cannot send configuration anywhere on the internet.
_HOST_LABEL_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")

APP_MISSING = (
	"Home Assistant integrations may not change YAML configuration, so the Vome "
	"app does that. Install the Vome app (Settings → Apps), or edit the file "
	"yourself."
)


def _state(hass: HomeAssistant) -> dict:
	return hass.data.setdefault(DOMAIN, {}).setdefault(_DATA_KEY, {})


def _secret(hass: HomeAssistant) -> str:
	state = _state(hass)
	if not state.get("secret"):
		state["secret"] = secrets.token_urlsafe(32)
	return state["secret"]


def _valid_url(raw: Any) -> Optional[str]:
	"""``http://<host>:<port>`` on Home Assistant's own network, or None."""
	if not isinstance(raw, str):
		return None
	parsed = urlparse(raw.strip())
	try:
		port = parsed.port
	except ValueError:
		return None
	host = (parsed.hostname or "").lower()
	if (
		parsed.scheme != "http" or not host or port is None
		or parsed.username or parsed.password
		or parsed.path not in ("", "/") or parsed.query or parsed.fragment
	):
		return None
	try:
		if not ipaddress.ip_address(host).is_private:
			return None
	except ValueError:
		if not _HOST_LABEL_RE.match(host):
			return None
	return f"http://{host}:{port}"


def check_in(hass: HomeAssistant, url: Any) -> dict:
	"""Record where the app listens; return the secret it must require."""
	clean = _valid_url(url)
	if clean is None:
		raise ValueError(
			"The app's address must be http://<host>:<port> on Home Assistant's "
			"internal network."
		)
	state = _state(hass)
	state["url"] = clean
	state["seen"] = time.monotonic()
	return {"secret": _secret(hass)}


def app_url(hass: HomeAssistant) -> Optional[str]:
	"""Where the app listens, if it checked in recently."""
	state = _state(hass)
	seen = state.get("seen")
	if not state.get("url") or seen is None or time.monotonic() - seen > STALE_AFTER:
		return None
	return state["url"]


def available(hass: HomeAssistant) -> bool:
	return app_url(hass) is not None


async def async_request(
	hass: HomeAssistant, path: str, payload: dict
) -> tuple[Optional[dict], Optional[str]]:
	"""POST ``payload`` to the app; ``(answer, None)`` or ``(None, why not)``."""
	url = app_url(hass)
	if url is None:
		return None, APP_MISSING
	try:
		async with async_get_clientsession(hass).post(
			url + path, json=payload, timeout=REQUEST_TIMEOUT,
			headers={"Authorization": f"Bearer {_secret(hass)}"},
		) as resp:
			status = resp.status
			data = await resp.json(content_type=None)
	except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as err:
		return None, f"The Vome app did not answer: {err or err.__class__.__name__}"
	if not isinstance(data, dict):
		return None, f"The Vome app answered HTTP {status} with no result."
	if status != 200 or data.get("error"):
		return None, str(data.get("error") or f"The Vome app answered HTTP {status}.")
	return data, None
