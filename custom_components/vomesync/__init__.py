"""VomeSync Home Assistant Integration."""
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType
import voluptuous as vol

from .const import DOMAIN
from .coordinator import VomeSyncCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SWITCH, Platform.SENSOR]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
	"""Set up VomeSync integration from configuration.yaml."""
	hass.data.setdefault(DOMAIN, {})
	return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
	"""Set up VomeSync from a config entry."""
	_LOGGER.info("Setting up VomeSync integration")
	_LOGGER.info("Entry data keys: %s", list(entry.data.keys()))
	_LOGGER.info("Entry options keys: %s", list(entry.options.keys()) if entry.options else "None")
	_LOGGER.info("Entry options content: %s", entry.options)
	
	# Create coordinator
	coordinator = VomeSyncCoordinator(hass, entry)
	
	# Store coordinator
	hass.data.setdefault(DOMAIN, {})
	hass.data[DOMAIN][entry.entry_id] = coordinator
	
	# Start coordinator and fetch data from API before setting up platforms
	await coordinator.async_config_entry_first_refresh()
	
	# Set up entity linking (including bidirectional tracking for owned switches)
	await coordinator.async_setup_entity_links()
	
	# Setup platforms (entities will be created from coordinator data)
	await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
	
	_register_services(hass)
	
	return True


def _get_coordinator_for_service(hass: HomeAssistant, entry_id: str | None) -> VomeSyncCoordinator:
	"""Resolve coordinator for a service call."""
	domain_data = hass.data.get(DOMAIN, {})
	if entry_id:
		coordinator = domain_data.get(entry_id)
		if coordinator is None:
			raise ValueError(f"Unknown config entry_id: {entry_id}")
		return coordinator
	
	# If only one entry exists, use it
	if len(domain_data) == 1:
		return next(iter(domain_data.values()))
	
	raise ValueError("Multiple VomeSync entries found; provide entry_id in service data")


def _register_services(hass: HomeAssistant) -> None:
	"""Register integration services (idempotent)."""
	# Only register once per HA instance
	if hass.data.setdefault(DOMAIN, {}).get("_services_registered"):
		return
	hass.data[DOMAIN]["_services_registered"] = True
	
	async def _svc_create_switch(call) -> None:
		entry_id = call.data.get("entry_id")
		coordinator = _get_coordinator_for_service(hass, entry_id)
		uid = await coordinator.create_switch(
			name=call.data["name"],
			description=call.data.get("description", ""),
			location=call.data.get("location", ""),
			category=call.data.get("category", "Other"),
			publicize=call.data.get("publicize", False),
		)
		if not uid:
			raise ValueError("Failed to create switch")
	
	async def _svc_subscribe_switch(call) -> None:
		entry_id = call.data.get("entry_id")
		coordinator = _get_coordinator_for_service(hass, entry_id)
		ok = await coordinator.subscribe_to_switch(call.data["uid"])
		if not ok:
			raise ValueError("Failed to subscribe to switch (UID not found or API error)")
	
	async def _svc_delete_switch(call) -> None:
		entry_id = call.data.get("entry_id")
		coordinator = _get_coordinator_for_service(hass, entry_id)
		uid = call.data["uid"]
		if not coordinator.is_switch_owner(uid):
			raise ValueError("Only owners can delete switches")
		ok = await coordinator.delete_switch(uid)
		if not ok:
			raise ValueError("Failed to delete switch")
	
	hass.services.async_register(
		DOMAIN,
		"create_switch",
		_svc_create_switch,
		schema=vol.Schema({
			vol.Optional("entry_id"): cv.string,
			vol.Required("name"): cv.string,
			vol.Optional("description", default=""): cv.string,
			vol.Optional("location", default=""): cv.string,
			vol.Optional("category", default="Other"): cv.string,
			vol.Optional("publicize", default=False): cv.boolean,
		}),
	)
	
	hass.services.async_register(
		DOMAIN,
		"subscribe_switch",
		_svc_subscribe_switch,
		schema=vol.Schema({
			vol.Optional("entry_id"): cv.string,
			vol.Required("uid"): cv.string,
		}),
	)
	
	hass.services.async_register(
		DOMAIN,
		"delete_switch",
		_svc_delete_switch,
		schema=vol.Schema({
			vol.Optional("entry_id"): cv.string,
			vol.Required("uid"): cv.string,
		}),
	)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
	"""Unload a config entry."""
	_LOGGER.info("Unloading VomeSync integration")
	
	# Unload platforms
	unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
	
	if unload_ok:
		# Clean up coordinator
		coordinator = hass.data[DOMAIN].pop(entry.entry_id)
		await coordinator.async_shutdown()
	
	return unload_ok


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
	"""Reload config entry."""
	await async_unload_entry(hass, entry)
	await async_setup_entry(hass, entry)
