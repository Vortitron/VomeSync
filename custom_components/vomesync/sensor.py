"""Sensor platform for VomeSync integration."""
import logging
from typing import Any, Dict, Optional

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
	DOMAIN,
	ATTR_SWITCH_UID,
	ATTR_NAME,
	ATTR_DESCRIPTION,
	ATTR_LOCATION,
	ATTR_CATEGORY,
	ATTR_LINK,
	ATTR_ICON_URL,
	ATTR_BANNER_URL,
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
	"""Set up VomeSync sensors from a config entry."""
	coordinator: VomeSyncCoordinator = hass.data.get(DOMAIN, {}).get(config_entry.entry_id)
	if coordinator is None:
		_LOGGER.warning("Coordinator not found during sensor setup; skipping sensor entities")
		return
	
	# Store the add_entities callback in the coordinator for dynamic entity addition
	coordinator.async_add_sensor_entities = async_add_entities
	
	# Create sensor entities for subscribed switches (read-only monitoring)
	entities = []
	
	options = config_entry.options or {}
	imported_switches = options.get("imported_switches", {})
	
	# Create sensors for imported switches where is_owner is False
	for uid, info in imported_switches.items():
		if info.get("is_owner", False):
			continue
		if str(info.get("access_key", "") or "").strip():
			continue
		name = info.get("name") or DEFAULT_SWITCH_NAME
		entity = VomeSyncSensor(coordinator, uid, name)
		entities.append(entity)
	
	# The health score, whether or not this instance is linked to an account
	# yet: it is the one entity that exists before anything is configured,
	# because running the check is how somebody finds out what Vome is for.
	entities.append(VomeHealthScoreSensor(hass, config_entry))

	if entities:
		async_add_entities(entities)
		_LOGGER.info("Added %d VomeSync sensor entities", len(entities))


class VomeHealthScoreSensor(SensorEntity):
	"""This instance's Vome health score, with the findings on it.

	Vome computes the score (the collectors and the write-up are its), but
	the answer is about *this* system, so it belongs here — and for a
	login-free run it is the copy that survives: Vome deletes its own two
	hours later.  See health_score.py.

	Unavailable rather than zero when there has been no check: a health
	score of nothing is not a health score of 0.
	"""

	_attr_has_entity_name = True
	_attr_should_poll = False
	_attr_icon = "mdi:heart-pulse"
	_attr_native_unit_of_measurement = "/100"

	def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
		self.hass = hass
		self._entry = entry
		self._attr_unique_id = f"vomesync_health_score_{entry.entry_id}"
		self._attr_name = "Vome health score"
		self._attr_device_info = {
			"identifiers": {(DOMAIN, f"vome_account_{entry.entry_id}")},
			"name": "Vome",
			"manufacturer": DEVICE_MANUFACTURER,
		}

	async def async_added_to_hass(self) -> None:
		from homeassistant.helpers.dispatcher import async_dispatcher_connect
		from .health_score import SIGNAL_HEALTH_UPDATED

		@callback
		def _updated(entry_id: str) -> None:
			if entry_id == self._entry.entry_id:
				self.async_write_ha_state()

		self.async_on_remove(
			async_dispatcher_connect(self.hass, SIGNAL_HEALTH_UPDATED, _updated)
		)

	@property
	def _report(self) -> Optional[Dict[str, Any]]:
		from .health_score import stored_report
		return stored_report(self.hass, self._entry.entry_id)

	@property
	def available(self) -> bool:
		return self._report is not None

	@property
	def native_value(self) -> Optional[int]:
		report = self._report or {}
		score = report.get("score")
		return int(score) if isinstance(score, (int, float)) else None

	@property
	def extra_state_attributes(self) -> Dict[str, Any]:
		"""The findings themselves, so the report is readable from here.

		A dashboard card, an automation, or a person clicking the entity
		all read the same thing — no round trip to a website, and nothing
		lost when Vome deletes its copy of a login-free run.
		"""
		from . import health_score

		report = self._report or {}
		attributes: Dict[str, Any] = {
			"summary": report.get("summary") or "",
			"findings": report.get("findings") or [],
			"categories": report.get("categories") or [],
			"generated_at": report.get("generated_at"),
		}
		if health_score.is_guest(self._entry):
			# Said plainly on the entity as well as in the notification:
			# this number is on a clock unless somebody signs in.
			attributes["saved_to_account"] = False
			attributes["keep_it_url"] = health_score.claim_url(self._entry)
			attributes["deleted_in_seconds"] = health_score.guest_seconds_left(self._entry)
		else:
			attributes["saved_to_account"] = health_score.is_linked(self._entry)
		return attributes


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
			"name": format_device_name(name),
			"manufacturer": DEVICE_MANUFACTURER,
			"model": format_device_model(False),
			"sw_version": "1.0.0",
		}

	def _update_name_from_data(self) -> None:
		data = self.switch_data or {}
		name = data.get("name") or DEFAULT_SWITCH_NAME
		entity_name = f"{name} Status"
		if entity_name != self._attr_name:
			self._attr_name = entity_name
			self._name = name
			try:
				self._attr_device_info["name"] = format_device_name(name)
			except Exception:  # noqa: BLE001
				pass

	def _handle_coordinator_update(self) -> None:
		self._update_name_from_data()
		super()._handle_coordinator_update()

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
		name = data.get("name")
		if name:
			attributes[ATTR_NAME] = name

		# Add available attributes
		for attr, key in [
			(ATTR_DESCRIPTION, "description"),
			(ATTR_LOCATION, "location"),
			(ATTR_CATEGORY, "category"),
			(ATTR_LINK, "link"),
			(ATTR_ICON_URL, "iconUrl"),
			(ATTR_BANNER_URL, "bannerUrl"),
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

		return attributes
