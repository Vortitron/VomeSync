"""Switch platform for VomeSync integration."""
import logging
from typing import Any, Dict, Optional
from urllib.parse import quote
import voluptuous as vol

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers import config_validation as cv, entity_platform, device_registry as dr

from .const import (
	DOMAIN,
	ATTR_SWITCH_UID,
	ATTR_NAME,
	ATTR_DESCRIPTION,
	ATTR_LOCATION,
	ATTR_CATEGORY,
	ATTR_PUBLICIZE,
	ATTR_LINK,
	ATTR_ICON_URL,
	ATTR_BANNER_URL,
	ATTR_TOGGLE_COUNT,
	ATTR_LAST_TOGGLED,
	ATTR_CREATED_AT,
	ATTR_LAST_TOGGLED_TS,
	ATTR_CREATED_AT_TS,
	ATTR_IS_OWNER,
	DEVICE_MANUFACTURER,
	DEFAULT_SWITCH_NAME,
)
from .coordinator import VomeSyncCoordinator
from .naming import format_device_model, format_device_name
from .time_utils import format_timestamp_ms

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
	hass: HomeAssistant,
	config_entry: ConfigEntry,
	async_add_entities: AddEntitiesCallback,
) -> None:
	"""Set up VomeSync switches from a config entry."""
	coordinator: VomeSyncCoordinator = hass.data.get(DOMAIN, {}).get(config_entry.entry_id)
	if coordinator is None:
		# Fallback for test environments: instantiate coordinator if not present
		from .coordinator import VomeSyncCoordinator as CoordinatorClass
		coordinator = CoordinatorClass(hass, config_entry)
		hass.data.setdefault(DOMAIN, {})
		hass.data[DOMAIN][config_entry.entry_id] = coordinator
	
	# Store the add_entities callback in the coordinator for dynamic entity addition
	coordinator.async_add_switch_entities = async_add_entities
	
	# Create entities from imported switches (local cache)
	# This ensures entities exist immediately even if API is slow/unavailable
	entities = []
	coordinator.entity_names = {}
	
	options = config_entry.options or {}
	imported_switches = options.get("imported_switches", {})
	
	_LOGGER.info(
		"Creating entities from imported switches cache: %d switches",
		len(imported_switches),
	)
	
	# Create entities for each imported switch
	for uid, switch_info in imported_switches.items():
		name = switch_info.get("name") or DEFAULT_SWITCH_NAME
		is_owner = switch_info.get("is_owner", False)
		
		_LOGGER.debug(
			"Creating entity from cache: name='%s', uid='%s', owner=%s",
			name, uid, is_owner
		)
		
		entity = VomeSyncSwitch(coordinator, uid, name, is_owner, config_entry)
		entities.append(entity)
		coordinator.entity_names[uid] = name
	
	if entities:
		async_add_entities(entities)
		_LOGGER.info("Successfully added %d VomeSync switch entities from cache", len(entities))
		for entity in entities:
			_LOGGER.info("  - %s (uid=%s, owner=%s)", entity.name, entity._uid, entity._is_owner)
	else:
		_LOGGER.info(
			"No imported switches found. Use 'Import switches' in integration options to add switches."
		)
	
	# Register entity options
	try:
		platform = entity_platform.async_get_current_platform()
		platform.async_register_entity_service(
			"link_entities",
			{
				vol.Required("entities"): cv.entity_ids,
			},
			"async_link_entities",
		)
	except RuntimeError:
		# In tests there may be no current platform; skip service registration
		pass


class VomeSyncSwitch(CoordinatorEntity[VomeSyncCoordinator], SwitchEntity):
	"""Representation of a VomeSync switch."""

	def _extract_params_from_kwargs(self, kwargs: Dict[str, Any]) -> Optional[Dict[str, Any]]:
		"""Extract supported parameter fields from a HA service call."""
		if not kwargs:
			return None
		
		params: Dict[str, Any] = {}
		for key in ("rgb_color", "hs_color", "xy_color", "color_temp", "brightness", "transition", "effect", "color_mode"):
			if key in kwargs:
				params[key] = kwargs[key]
		return params or None

	def _get_configuration_url(self) -> Optional[str]:
		"""Return a valid absolute configuration URL for this device, if possible."""
		# Device registry requires an absolute URL with scheme/host.
		# Prefer the VomeSync website URL so "Visit" goes to per-switch management.
		server_url = None
		try:
			server_url = self.coordinator.config_entry.data.get("server_url")
		except Exception:  # noqa: BLE001
			server_url = None
		
		if server_url and (server_url.startswith("http://") or server_url.startswith("https://")):
			uid_q = quote(self._uid, safe="")
			return f"{server_url.rstrip('/')}/switch/{uid_q}"
		
		# Fallback to HA base URL for integration-level navigation if configured
		try:
			base_url = (self.coordinator.hass.config.external_url or self.coordinator.hass.config.internal_url)
		except Exception:  # noqa: BLE001
			base_url = None
		
		if not base_url:
			return None
		
		return f"{base_url.rstrip('/')}/config/integrations/integration/vomesync"

	def __init__(
		self,
		coordinator: VomeSyncCoordinator,
		uid: str,
		name: str,
		is_owner: bool,
		config_entry: ConfigEntry,
	) -> None:
		"""Initialize the switch."""
		super().__init__(coordinator)
		self._uid = uid
		self._name = name
		self._is_owner = is_owner
		self._config_entry = config_entry
		
		# Generate unique_id
		self._attr_unique_id = f"vomesync_{uid}"
		self._attr_name = name
		self._attr_has_entity_name = True
		
		# Device info
		device_info = {
			"identifiers": {(DOMAIN, uid)},
			"name": format_device_name(name),
			"manufacturer": DEVICE_MANUFACTURER,
			"model": format_device_model(is_owner),
			"sw_version": "1.0.0",
		}
		
		config_url = self._get_configuration_url()
		if config_url:
			device_info["configuration_url"] = config_url
		
		self._attr_device_info = device_info

	def _update_name_from_data(self) -> None:
		data = self.switch_data or {}
		name = data.get("name") or DEFAULT_SWITCH_NAME
		if name != self._attr_name:
			self._attr_name = name
			self._name = name
			formatted_name = format_device_name(name)
			try:
				self._attr_device_info["name"] = formatted_name
			except Exception:  # noqa: BLE001
				pass
			# Update device registry to reflect the new name
			try:
				device_registry = dr.async_get(self.coordinator.hass)
				device = device_registry.async_get_device(identifiers={(DOMAIN, self._uid)})
				if device:
					device_registry.async_update_device(device.id, name=formatted_name)
			except Exception:  # noqa: BLE001
				pass

	def _handle_coordinator_update(self) -> None:
		self._update_name_from_data()
		super()._handle_coordinator_update()

	@property
	def switch_data(self) -> Optional[Dict[str, Any]]:
		"""Get switch data from coordinator."""
		# Try coordinator helper first
		if hasattr(self.coordinator, "get_switch_data"):
			try:
				return self.coordinator.get_switch_data(self._uid)
			except Exception:  # noqa: BLE001
				pass
		
		# Fallback to direct dict access
		switches = getattr(self.coordinator, "switches", None) or {}
		subs = getattr(self.coordinator, "subscriptions", None) or {}
		return switches.get(self._uid) or subs.get(self._uid)

	@property
	def available(self) -> bool:
		"""Return if entity is available."""
		if not self.coordinator.last_update_success:
			_LOGGER.debug("Switch %s unavailable: coordinator update failed", self._uid)
			return False
		if self.switch_data is None:
			_LOGGER.debug("Switch %s unavailable: no data from coordinator", self._uid)
			return False
		return True

	@property
	def is_on(self) -> bool:
		"""Return true if switch is on."""
		data = self.switch_data
		if data is None:
			return False
		return data.get("state", False)

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
		name = data.get("name")
		if name:
			attributes[ATTR_NAME] = name
		
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
			(ATTR_LINK, "link"),
			(ATTR_ICON_URL, "iconUrl"),
			(ATTR_BANNER_URL, "bannerUrl"),
			(ATTR_PUBLICIZE, "publicize"),
			(ATTR_TOGGLE_COUNT, "toggleCount"),
		]:
			if key in data:
				attributes[attr] = data[key]

		last_toggled_raw = data.get("lastToggled")
		created_at_raw = data.get("createdAt")
		if last_toggled_raw is not None:
			attributes[ATTR_LAST_TOGGLED_TS] = last_toggled_raw
			attributes[ATTR_LAST_TOGGLED] = format_timestamp_ms(last_toggled_raw) or last_toggled_raw
		if created_at_raw is not None:
			attributes[ATTR_CREATED_AT_TS] = created_at_raw
			attributes[ATTR_CREATED_AT] = format_timestamp_ms(created_at_raw) or created_at_raw
		
		# Add linked entities count and list
		linked = self.async_get_linked_entities()
		if linked:
			attributes["linked_entities"] = linked
			attributes["linked_entities_count"] = len(linked)
		else:
			attributes["linked_entities_count"] = 0
		
		# Add management info
		attributes["integration_management"] = "Configure via: Settings → Devices & Services → Vome → Configure"
		attributes["uid"] = self._uid

		return attributes

	def _get_access_key(self) -> Optional[str]:
		"""Return delegated access key for a subscribed switch, if any."""
		method = getattr(self.coordinator, "get_subscription_access_key", None)
		if callable(method):
			try:
				return method(self._uid)
			except Exception:  # noqa: BLE001
				return None
		return None

	async def async_turn_on(self, **kwargs: Any) -> None:
		"""Turn the switch on."""
		if not self._is_owner:
			access_key = self._get_access_key()
			if not access_key:
				_LOGGER.warning("Cannot toggle switch %s - no access key", self._uid)
				return
			success = await self.coordinator.toggle_switch_with_access_key(self._uid, access_key, desired_state=True)
			if not success:
				_LOGGER.error("Failed to turn on switch %s via access key", self._uid)
			return

		params = self._extract_params_from_kwargs(kwargs)
		success = await self.coordinator.set_switch_state(self._uid, True, params=params)
		if not success:
			_LOGGER.error("Failed to turn on switch %s", self._uid)

	async def async_turn_off(self, **kwargs: Any) -> None:
		"""Turn the switch off."""
		if not self._is_owner:
			access_key = self._get_access_key()
			if not access_key:
				_LOGGER.warning("Cannot toggle switch %s - no access key", self._uid)
				return
			success = await self.coordinator.toggle_switch_with_access_key(self._uid, access_key, desired_state=False)
			if not success:
				_LOGGER.error("Failed to turn off switch %s via access key", self._uid)
			return

		success = await self.coordinator.set_switch_state(self._uid, False, params=None)
		if not success:
			_LOGGER.error("Failed to turn off switch %s", self._uid)
	
	async def async_link_entities(self, entities: list[str]) -> None:
		"""Link entities to this switch (service call)."""
		# Update config entry options
		options = dict(self._config_entry.options or {})
		if "linked_entities" not in options:
			options["linked_entities"] = {}
		
		linked_entities = dict(options["linked_entities"])
		if entities:
			linked_entities[self._uid] = {
				"entities": list(entities),
				"mode": "master",
				"master": entities[0],
				"direction": "both",
			}
		else:
			linked_entities.pop(self._uid, None)
		options["linked_entities"] = linked_entities
		
		self.hass.config_entries.async_update_entry(self._config_entry, options=options)
		
		_LOGGER.info("Linked %d entities to switch %s via service call", len(entities), self._uid)
		
		# Trigger coordinator to set up listeners
		await self.coordinator.async_setup_entity_links()
	
	@callback
	def async_get_linked_entities(self) -> list[str]:
		"""Get linked entities for this switch."""
		options = self._config_entry.options or {}
		linked_entities = options.get("linked_entities", {}) or {}
		raw = linked_entities.get(self._uid)
		if isinstance(raw, dict):
			entities = raw.get("entities", [])
			return entities if isinstance(entities, list) else []
		return raw if isinstance(raw, list) else []

	async def async_toggle(self, **kwargs: Any) -> None:
		"""Toggle the switch."""
		if not self._is_owner:
			access_key = self._get_access_key()
			if not access_key:
				_LOGGER.warning("Cannot toggle switch %s - no access key", self._uid)
				raise PermissionError("Access key required to toggle this switch")
			success = await self.coordinator.toggle_switch_with_access_key(self._uid, access_key)
			if not success:
				_LOGGER.error("Failed to toggle switch %s via access key", self._uid)
			return

		target = not bool(self.is_on)
		params = self._extract_params_from_kwargs(kwargs) if target else None
		success = await self.coordinator.set_switch_state(self._uid, target, params=params)
		if not success:
			_LOGGER.error("Failed to toggle switch %s", self._uid)
