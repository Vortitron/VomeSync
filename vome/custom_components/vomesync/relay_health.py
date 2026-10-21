"""A Repairs issue when a linked home has been off Vome for a while.

The relay reconnects on its own and logs every attempt, but nobody reads
the log of a house that is working.  What an owner needs to know is the
one thing the log cannot say: *this has gone on too long*.  Vome's side
notices too (the portal shows the home offline), but the owner is not
looking there either — they are in Home Assistant, where Settings →
Repairs is the place things that need a person go.

So: when the tunnel closes and stays closed for ``RELAY_DOWN_GRACE_SECONDS``
an issue is raised; the moment it reopens, the issue is deleted.  Short
drops (a router reboot, a Vome deploy) never surface.  A deliberate stop —
unlinking, reloading, shutting down — calls :func:`forget_relay` so an
absent link is never reported as a broken one.

Everything here is best-effort and is reached through ``try`` from the
relay: a bug in a notification must never cost anyone their tunnel.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from homeassistant.core import HomeAssistant

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

# How long the tunnel may be down before Repairs hears about it.  Ten
# minutes outlasts a Home Assistant restart, a router reboot and the
# relay's own reconnect backoff (capped well below this).
RELAY_DOWN_GRACE_SECONDS = 600

ISSUE_RELAY_DOWN = "relay_down"

_KEY = "_relay_health"


def _timers(hass: HomeAssistant) -> dict[str, Any]:
	return hass.data.setdefault(DOMAIN, {}).setdefault(_KEY, {})


def issue_id(entry_id: str) -> str:
	return f"{ISSUE_RELAY_DOWN}_{entry_id}"


def _cancel(hass: HomeAssistant, entry_id: str) -> None:
	handle = _timers(hass).pop(entry_id, None)
	if handle is not None:
		try:
			handle.cancel()
		except Exception:  # noqa: BLE001
			pass


def _delete_issue(hass: HomeAssistant, entry_id: str) -> None:
	try:
		from homeassistant.helpers import issue_registry as ir

		ir.async_delete_issue(hass, DOMAIN, issue_id(entry_id))
	except Exception as err:  # noqa: BLE001
		_LOGGER.debug("Repairs: could not clear %s: %s", issue_id(entry_id), err)


def _raise_if_still_down(hass: HomeAssistant, entry_id: str) -> None:
	"""The grace period is over: say so, unless it came back meanwhile."""
	_timers(hass).pop(entry_id, None)
	from .relay_client import get_relay_client

	client = get_relay_client(hass, entry_id)
	if client is None or client.connected:
		return
	try:
		from homeassistant.helpers import issue_registry as ir

		ir.async_create_issue(
			hass,
			DOMAIN,
			issue_id(entry_id),
			is_fixable=False,
			severity=ir.IssueSeverity.WARNING,
			translation_key=ISSUE_RELAY_DOWN,
			translation_placeholders={
				"server_id": str(client.server_id or ""),
				"minutes": str(RELAY_DOWN_GRACE_SECONDS // 60),
			},
		)
	except Exception as err:  # noqa: BLE001
		_LOGGER.debug("Repairs: could not raise %s: %s", issue_id(entry_id), err)
		return
	_LOGGER.warning(
		"Remote access has been down for %d minutes (%s); raised a Repairs issue",
		RELAY_DOWN_GRACE_SECONDS // 60, client.server_id,
	)


def note_relay_state(hass: HomeAssistant, entry_id: str, connected: bool) -> None:
	"""The tunnel just opened (``True``) or closed (``False``)."""
	_cancel(hass, entry_id)
	if connected:
		_delete_issue(hass, entry_id)
		return
	_timers(hass)[entry_id] = hass.loop.call_later(
		RELAY_DOWN_GRACE_SECONDS, _raise_if_still_down, hass, entry_id,
	)


def forget_relay(hass: HomeAssistant, entry_id: str) -> None:
	"""The link is being stopped on purpose: no timer, no issue."""
	_cancel(hass, entry_id)
	_delete_issue(hass, entry_id)


def is_down_too_long(hass: HomeAssistant, entry_id: str) -> Optional[bool]:
	"""For diagnostics: whether the issue is currently raised, if knowable."""
	try:
		from homeassistant.helpers import issue_registry as ir

		registry = ir.async_get(hass)
		return registry.async_get_issue(DOMAIN, issue_id(entry_id)) is not None
	except Exception:  # noqa: BLE001
		return None
