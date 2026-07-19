"""VomeSync Home Assistant Integration."""
import inspect
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType
import voluptuous as vol

from .const import DOMAIN, CONF_SERVER_URL, CONF_SWITCH_UID
from .coordinator import VomeSyncCoordinator
from .naming import build_entry_title, is_default_entry_title
from .relay_client import async_start_relay, async_stop_relay

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SWITCH, Platform.SENSOR]

# Option keys whose values are secrets and must never be logged verbatim.
_SENSITIVE_OPTION_KEYS = frozenset({"secret", "local_token", "token", "password"})


def _redacted_options(options):
	"""Return a shallow copy of the config-entry options safe to log.

	Secret values (relay secret, optional local HA token) are replaced with a
	``***`` marker; nested dicts (e.g. the ``relay`` sub-dict) are redacted too.
	Presence is preserved so the logs still show how the entry is configured.
	"""
	def _clean(value):
		if isinstance(value, dict):
			return {
				k: ("***" if k in _SENSITIVE_OPTION_KEYS and v else _clean(v))
				for k, v in value.items()
			}
		return value

	return _clean(dict(options or {}))


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
	"""Set up VomeSync integration from configuration.yaml."""
	hass.data.setdefault(DOMAIN, {})
	# Services are hass-scoped, not entry-scoped: register them here so they
	# exist even when an entry fails to set up. Otherwise every vomesync.*
	# call surfaces as a bare "400: Bad Request" (ServiceNotFound) in the
	# add-on panel, with no hint of the real problem.
	_register_services(hass)
	return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
	"""Set up VomeSync from a config entry."""
	_LOGGER.info("Setting up VomeSync integration")
	_LOGGER.debug("Entry data keys: %s", list(entry.data.keys()))
	_LOGGER.debug("Entry options keys: %s", list(entry.options.keys()) if entry.options else "None")
	# NEVER log entry.options verbatim: the relay dict holds a live relay
	# secret (and optionally a local HA token).  Log a redacted summary so the
	# credential can't leak into home-assistant.log / log aggregators.
	_LOGGER.debug("Entry options (redacted): %s", _redacted_options(entry.options))

	try:
		server_url = entry.data.get(CONF_SERVER_URL, "")
	except Exception:  # noqa: BLE001
		server_url = ""
	desired_title = build_entry_title(server_url)
	if desired_title and is_default_entry_title(entry.title) and entry.title != desired_title:
		result = hass.config_entries.async_update_entry(entry, title=desired_title)
		if inspect.isawaitable(result):
			await result
	
	# Create coordinator
	coordinator = VomeSyncCoordinator(hass, entry)

	# Store coordinator
	hass.data.setdefault(DOMAIN, {})
	hass.data[DOMAIN][entry.entry_id] = coordinator

	_register_services(hass)

	# Start the outbound relay FIRST: remote access (the panel, LAN tunnels,
	# UI forwarding) must never be hostage to the switch-sync API. It only
	# needs the entry options, and async_start_relay is idempotent.
	await async_start_relay(hass, entry)

	# Switch sync is best-effort at startup: the coordinator refreshes on its
	# regular interval, so a failed first fetch degrades switch entities
	# until the API recovers instead of killing the whole entry (which would
	# also take down the relay and every service the panel depends on).
	try:
		await coordinator.async_config_entry_first_refresh()
		await coordinator.async_setup_entity_links()
	except Exception as err:  # noqa: BLE001
		_LOGGER.warning(
			"Initial switch sync failed; switch entities may be empty until "
			"the next poll succeeds. Remote access is unaffected. Error: %s",
			err,
		)

	# Setup platforms (entities will be created from coordinator data)
	await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

	pending_uid = (entry.data or {}).get(CONF_SWITCH_UID)
	if pending_uid:
		uid = str(pending_uid).strip()
		if uid:
			initial_access_key = str((entry.data or {}).get("initial_access_key", "") or "").strip()
			try:
				ok = await coordinator.subscribe_to_switch(uid, access_key=initial_access_key or None)
			except Exception as err:  # noqa: BLE001
				ok = False
				_LOGGER.warning("Initial switch subscribe raised (uid=%s): %s", uid, err)
			if ok:
				_LOGGER.info("Initial switch subscribed from setup (uid=%s)", uid)
			else:
				_LOGGER.warning(
					"Initial switch UID could not be subscribed; add via options flow (uid=%s)",
					uid,
				)
		new_data = dict(entry.data)
		new_data.pop(CONF_SWITCH_UID, None)
		new_data.pop("initial_access_key", None)
		hass.config_entries.async_update_entry(entry, data=new_data)

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
			link=call.data.get("link", ""),
			icon_url=call.data.get("icon_url") or None,
			banner_url=call.data.get("banner_url") or None,
			captcha_token=call.data.get("captcha_token", ""),
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
			vol.Optional("link", default=""): cv.string,
			vol.Optional("icon_url", default=""): cv.string,
			vol.Optional("banner_url", default=""): cv.string,
			vol.Optional("captcha_token", default=""): cv.string,
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

	from .services_remote import async_register_remote_services
	async_register_remote_services(hass)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
	"""Unload a config entry."""
	_LOGGER.info("Unloading VomeSync integration")
	
	# Stop the relay (if any) before tearing down platforms.
	await async_stop_relay(hass, entry)

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
