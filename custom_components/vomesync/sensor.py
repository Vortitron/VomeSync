"""Sensor platform for VomeSync integration."""
import logging
from typing import Any, Dict, Optional

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
	DOMAIN,
	ATTR_SWITCH_UID,
	ATTR_DESCRIPTION,
	ATTR_LOCATION,
	ATTR_CATEGORY,
	ATTR_LINK,
	ATTR_ICON_URL,
	ATTR_BANNER_URL,
	ATTR_LAST_TOGGLED,
	ATTR_IS_OWNER,
	DEVICE_MANUFACTURER,
	DEVICE_MODEL,
)
from .coordinator import VomeSyncCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
	hass: HomeAssistant,
	config_entry: ConfigEntry,
	async_add_entities: AddEntitiesCallback,
) -> None:
	"""Set up VomeSync sensors from a config entry."""
	coordinator: VomeSyncCoordinator = hass.data.get(DOMAIN, {}).get(config_entry.entry_id)
	if coordinator is None:
		_LOGGER.warning("Coordinator not found during sensor setup; skipping sensor entities")
		return
	
	# Create sensor entities for subscribed switches (read-only monitoring)
	entities = []
	
	options = config_entry.options or {}
	imported_switches = options.get("imported_switches", {})
	
	# Create sensors for imported switches where is_owner is False
	for uid, info in imported_switches.items():
		if info.get("is_owner", False):
			continue
		name = info.get("name", f"Switch {uid[:8]}")
		entity = VomeSyncSensor(coordinator, uid, name)
		entities.append(entity)
	
	if entities:
		async_add_entities(entities)
		_LOGGER.info("Added %d VomeSync sensor entities", len(entities))


class VomeSyncSensor(CoordinatorEntity[VomeSyncCoordinator], SensorEntity):
	"""Representation of a VomeSync sensor for monitoring subscribed switches."""

	def __init__(
		self,
		coordinator: VomeSyncCoordinator,
		uid: str,
		name: str,
	) -> None:
		"""Initialize the sensor."""
		super().__init__(coordinator)
		self._uid = uid
		self._name = name
		
		# Generate unique_id
		self._attr_unique_id = f"vomesync_sensor_{uid}"
		self._attr_name = f"{name} Status"
		
		# Device info
		# Match the switch entity's device identifiers so the sensor groups under the same device.
		self._attr_device_info = {
			"identifiers": {(DOMAIN, uid)},
			"name": f"VomeSync Switch ({name})",
			"manufacturer": DEVICE_MANUFACTURER,
			"model": DEVICE_MODEL,
			"sw_version": "1.0.0",
		}

	@property
	def switch_data(self) -> Optional[Dict[str, Any]]:
		"""Get switch data from coordinator."""
		return self.coordinator.get_switch_data(self._uid)

	@property
	def available(self) -> bool:
		"""Return if entity is available."""
		return self.coordinator.last_update_success and self.switch_data is not None

	@property
	def native_value(self) -> str:
		"""Return the state of the sensor."""
		data = self.switch_data
		if not data:
			return "unknown"
		
		return "on" if data.get("state", False) else "off"

	@property
	def icon(self) -> str:
		"""Return the icon to use in the frontend."""
		data = self.switch_data
		if not data:
			return "mdi:help-circle"
		
		is_on = data.get("state", False)
		return "mdi:eye" if is_on else "mdi:eye-off"

	@property
	def extra_state_attributes(self) -> Dict[str, Any]:
		"""Return extra state attributes."""
		data = self.switch_data
		if not data:
			return {}

		attributes = {
			ATTR_SWITCH_UID: self._uid,
			ATTR_IS_OWNER: False,  # Sensors are always for subscribed switches
		}

		# Add available attributes
		for attr, key in [
			(ATTR_DESCRIPTION, "description"),
			(ATTR_LOCATION, "location"),
			(ATTR_CATEGORY, "category"),
			(ATTR_LINK, "link"),
			(ATTR_ICON_URL, "iconUrl"),
			(ATTR_BANNER_URL, "bannerUrl"),
			(ATTR_LAST_TOGGLED, "lastToggled"),
		]:
			if key in data:
				attributes[attr] = data[key]

		return attributes
