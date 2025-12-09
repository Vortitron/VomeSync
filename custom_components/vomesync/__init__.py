"""VomeSync Home Assistant Integration."""
import asyncio
import logging
from typing import Any, Dict

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.typing import ConfigType

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
	
	# Create coordinator
	coordinator = VomeSyncCoordinator(hass, entry)
	
	# Store coordinator
	hass.data.setdefault(DOMAIN, {})
	hass.data[DOMAIN][entry.entry_id] = coordinator
	
	# Setup platforms
	await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
	
	# Start coordinator
	await coordinator.async_config_entry_first_refresh()
	
	# Register options update listener
	entry.async_on_unload(entry.add_update_listener(async_reload_entry))
	
	return True


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
