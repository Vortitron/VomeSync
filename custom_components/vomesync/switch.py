"""Switch platform for VomeSync integration."""
import logging
from typing import Any, Dict, Optional

from homeassistant.components.switch import SwitchEntity
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
	ATTR_PUBLICIZE,
	ATTR_TOGGLE_COUNT,
	ATTR_LAST_TOGGLED,
	ATTR_CREATED_AT,
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
	"""Set up VomeSync switches from a config entry."""
	coordinator: VomeSyncCoordinator = hass.data[DOMAIN][config_entry.entry_id]
	
	# Store the add_entities callback in the coordinator for dynamic entity addition
	coordinator.async_add_switch_entities = async_add_entities
	
	# Create switch entities for owned switches
	entities = []
	
	# Add switches from options (created via config flow)
	options = config_entry.options
	switches = options.get("switches", {})
	subscriptions = options.get("subscriptions", {})
	
	# Store entity name mapping in coordinator for config flow access
	coordinator.entity_names = {}
	
	for name, switch_config in switches.items():
		uid = switch_config["uid"]
		entity = VomeSyncSwitch(coordinator, uid, name, True)
		entities.append(entity)
		coordinator.entity_names[uid] = name
	
	for name, sub_config in subscriptions.items():
		uid = sub_config["uid"]
		entity = VomeSyncSwitch(coordinator, uid, name, False)
		entities.append(entity)
		coordinator.entity_names[uid] = name
	
	if entities:
		async_add_entities(entities)
		_LOGGER.info("Added %d VomeSync switch entities", len(entities))


class VomeSyncSwitch(CoordinatorEntity[VomeSyncCoordinator], SwitchEntity):
	"""Representation of a VomeSync switch."""

	def __init__(
		self,
		coordinator: VomeSyncCoordinator,
		uid: str,
		name: str,
		is_owner: bool,
	) -> None:
		"""Initialize the switch."""
		super().__init__(coordinator)
		self._uid = uid
		self._name = name
		self._is_owner = is_owner
		
		# Generate unique_id
		self._attr_unique_id = f"vomesync_{uid}"
		self._attr_name = name
		
		# Device info
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
	def is_on(self) -> bool:
		"""Return true if switch is on."""
		data = self.switch_data
		return data and data.get("state", False)

	@property
	def icon(self) -> str:
		"""Return the icon to use in the frontend."""
		if self._is_owner:
			return "mdi:light-switch" if self.is_on else "mdi:light-switch-off"
		else:
			return "mdi:eye" if self.is_on else "mdi:eye-off"

	@property
	def extra_state_attributes(self) -> Dict[str, Any]:
		"""Return extra state attributes."""
		data = self.switch_data
		if not data:
			return {}

		attributes = {
			ATTR_SWITCH_UID: self._uid,
			ATTR_IS_OWNER: self._is_owner,
		}
		
		# Add WebSocket URL for easy access
		ws_base_url = self.coordinator.config_entry.data.get("websocket_url", "")
		if ws_base_url:
			attributes["websocket_url"] = f"{ws_base_url}?uid={self._uid}"
		
		# Add webhook URL for remote toggling (owner only)
		if self._is_owner:
			server_url = self.coordinator.config_entry.data.get("server_url", "")
			personal_key = self.coordinator.config_entry.data.get("personal_key", "")
			if server_url and personal_key:
				attributes["webhook_url"] = f"{server_url}/api/toggle/{self._uid}?personalKey={personal_key}"

		# Add available attributes
		for attr, key in [
			(ATTR_DESCRIPTION, "description"),
			(ATTR_LOCATION, "location"),
			(ATTR_CATEGORY, "category"),
			(ATTR_PUBLICIZE, "publicize"),
			(ATTR_TOGGLE_COUNT, "toggleCount"),
			(ATTR_LAST_TOGGLED, "lastToggled"),
			(ATTR_CREATED_AT, "createdAt"),
		]:
			if key in data:
				attributes[attr] = data[key]

		return attributes

	async def async_turn_on(self, **kwargs: Any) -> None:
		"""Turn the switch on."""
		if not self._is_owner:
			_LOGGER.warning("Cannot toggle switch %s - not owner", self._uid)
			return

		success = await self.coordinator.toggle_switch(self._uid)
		if not success:
			_LOGGER.error("Failed to turn on switch %s", self._uid)

	async def async_turn_off(self, **kwargs: Any) -> None:
		"""Turn the switch off."""
		if not self._is_owner:
			_LOGGER.warning("Cannot toggle switch %s - not owner", self._uid)
			return

		success = await self.coordinator.toggle_switch(self._uid)
		if not success:
			_LOGGER.error("Failed to turn off switch %s", self._uid)

	async def async_toggle(self, **kwargs: Any) -> None:
		"""Toggle the switch."""
		if not self._is_owner:
			_LOGGER.warning("Cannot toggle switch %s - not owner", self._uid)
			return

		success = await self.coordinator.toggle_switch(self._uid)
		if not success:
			_LOGGER.error("Failed to toggle switch %s", self._uid)
