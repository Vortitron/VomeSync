"""Binary sensor platform: is this home reachable through Vome right now?

One entity per Vome entry, ``binary_sensor.vome_remote_access``, on the
same "Vome" device as the health score.  It is the relay's own state —
the tunnel is open or it is not — rather than anything inferred, so an
automation can say "tell me if the house drops off Vome for an hour" and
a dashboard can show a plain green dot.

Unavailable, not off, while the entry has no relay link: a home that was
never linked is not disconnected.  (A Vome-hosted home has no tunnel to
itself either, and so no sensor state here; the portal watches that VM.)
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from urllib.parse import urlparse

from homeassistant.components.binary_sensor import (
	BinarySensorDeviceClass,
	BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DEVICE_MANUFACTURER, DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
	hass: HomeAssistant,
	config_entry: ConfigEntry,
	async_add_entities: AddEntitiesCallback,
) -> None:
	"""One remote-access sensor per entry, linked or not (it links later)."""
	async_add_entities([VomeRemoteAccessSensor(hass, config_entry)])


def _iso(ts: Optional[float]) -> Optional[str]:
	if not ts:
		return None
	return datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat()


class VomeRemoteAccessSensor(BinarySensorEntity):
	"""On while the outbound relay tunnel to Vome is open."""

	_attr_has_entity_name = True
	_attr_should_poll = False
	_attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
	_attr_icon = "mdi:cloud-check-variant"

	def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
		self.hass = hass
		self._entry = entry
		self._attr_unique_id = f"vomesync_remote_access_{entry.entry_id}"
		self._attr_name = "Remote access"
		self._attr_device_info = {
			"identifiers": {(DOMAIN, f"vome_account_{entry.entry_id}")},
			"name": "Vome",
			"manufacturer": DEVICE_MANUFACTURER,
		}

	async def async_added_to_hass(self) -> None:
		from homeassistant.helpers.dispatcher import async_dispatcher_connect
		from .relay_client import SIGNAL_RELAY_STATE

		@callback
		def _changed(entry_id: str, connected: bool) -> None:
			if entry_id == self._entry.entry_id:
				self.async_write_ha_state()

		self.async_on_remove(
			async_dispatcher_connect(self.hass, SIGNAL_RELAY_STATE, _changed)
		)

	@property
	def _client(self):
		from .relay_client import get_relay_client
		return get_relay_client(self.hass, self._entry.entry_id)

	@property
	def available(self) -> bool:
		return self._client is not None

	@property
	def is_on(self) -> bool:
		client = self._client
		return bool(client is not None and client.connected)

	@property
	def extra_state_attributes(self) -> Dict[str, Any]:
		client = self._client
		if client is None:
			return {}
		attributes: Dict[str, Any] = {
			"server_id": client.server_id,
			"relay": urlparse(client.ws_url or "").hostname or "",
			"connected_since": _iso(client.connected_since),
			"disconnected_since": _iso(client.disconnected_since),
			# Off because Vome refused the link, not because it is unreachable.
			"rejected": bool(getattr(client, "rejected", False)),
		}
		return attributes
