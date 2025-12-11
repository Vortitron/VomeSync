"""Data update coordinator for VomeSync integration."""
import logging
from datetime import timedelta
from typing import Any, Dict, Optional

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .api_client import VomeSyncAPIClient, VomeSyncAPIError
from .websocket_client import VomeSyncWebSocketClient
from .const import (
	DOMAIN,
	CONF_PERSONAL_KEY,
	CONF_SERVER_URL,
	CONF_WEBSOCKET_URL,
	UPDATE_INTERVAL_SECONDS,
)

_LOGGER = logging.getLogger(__name__)


class VomeSyncCoordinator(DataUpdateCoordinator[Dict[str, Any]]):
	"""VomeSync data update coordinator."""

	def __init__(self, hass: HomeAssistant, config_entry: ConfigEntry) -> None:
		"""Initialize the coordinator."""
		self.config_entry = config_entry
		self.api_client = VomeSyncAPIClient(
			config_entry.data[CONF_SERVER_URL],
			config_entry.data[CONF_PERSONAL_KEY]
		)
		self.websocket_client = VomeSyncWebSocketClient(
			config_entry.data[CONF_WEBSOCKET_URL],
			self._handle_websocket_message
		)
		
		super().__init__(
			hass,
			_LOGGER,
			name=DOMAIN,
			update_interval=timedelta(seconds=UPDATE_INTERVAL_SECONDS),
		)
		# Ensure config_entry is retained after super call
		self.config_entry = config_entry
		
		# Store switch data and WebSocket connections
		self.switches: Dict[str, Dict[str, Any]] = {}
		self.subscriptions: Dict[str, Dict[str, Any]] = {}
		self._websocket_connections: Dict[str, bool] = {}
		
		# Callback for dynamically adding switch entities (set by switch platform)
		self.async_add_switch_entities = None
		
		# Entity name mapping (uid -> friendly_name)
		self.entity_names: Dict[str, str] = {}
		
		# Rate limiting for linked entity triggers (prevent loops)
		self._last_trigger_time: Dict[str, float] = {}
		self._trigger_cooldown = 2.0  # 2 seconds between triggers for same switch
		
		# Rate limiting for toggle requests (prevent API spam)
		self._last_toggle_time: Dict[str, float] = {}
		self._toggle_cooldown = 1.0  # 1 second between API toggles for same switch

	async def _async_update_data(self) -> Dict[str, Any]:
		"""Fetch data from API."""
		try:
			# Get user's switches
			my_switches = await self.api_client.get_my_switches()
			_LOGGER.debug("Fetched %d switches from API", len(my_switches))
			
			# Names from imported cache (if any)
			options = self.config_entry.options or {}
			imported_switches = options.get("imported_switches", {})
			
			# Build uid->name mapping from imported cache
			uid_to_name = {
				uid: info.get("name", uid[:8])
				for uid, info in imported_switches.items()
			}
			
			# Also check entity_names for existing mappings
			if not hasattr(self, 'entity_names') or self.entity_names is None:
				self.entity_names = {}
			
			# Update switches data
			switches_data = {}
			for switch in my_switches:
				uid = switch["uid"]
				# Priority: API name/description > cached name > entity_names > uid
				name = (
					switch.get("name")
					or switch.get("description")
					or uid_to_name.get(uid)
					or self.entity_names.get(uid)
					or uid[:8]
				)
				switches_data[uid] = {
					**switch,
					"name": name,
					"is_owner": True
				}
				# Update entity_names mapping
				if uid not in self.entity_names:
					self.entity_names[uid] = name
				
				# Ensure WebSocket connection for owned switches
				if uid not in self._websocket_connections:
					await self._ensure_websocket_connection(uid)
			
			if not switches_data:
				_LOGGER.warning("No owned switches returned from API; entities may appear unavailable")
			
			# Get status for subscribed switches from options
			options = self.config_entry.options or {}
			subscriptions = options.get("subscriptions", {})
			
			subscriptions_data = {}
			for name, sub_config in subscriptions.items():
				uid = sub_config["uid"]
				status = await self.api_client.get_switch_status(uid)
				if status:
					subscriptions_data[uid] = {
						**status,
						"name": name,
						"is_owner": False
					}
					
					# Ensure WebSocket connection for subscriptions
					if uid not in self._websocket_connections:
						await self._ensure_websocket_connection(uid)
				else:
					_LOGGER.warning("Subscription %s (uid=%s) returned no status from API", name, uid)
			
			# Store current data
			self.switches = switches_data
			self.subscriptions = subscriptions_data
			
			# Update cached data in options for imported switches
			options = self.config_entry.options or {}
			imported_switches = options.get("imported_switches", {})
			
			if imported_switches:
				updated_cache = False
				for uid in list(imported_switches.keys()):
					# Update cache with fresh data from API
					if uid in switches_data:
						imported_switches[uid]["cached_data"] = switches_data[uid]
						imported_switches[uid]["name"] = switches_data[uid].get("name", imported_switches[uid].get("name"))
						updated_cache = True
					elif uid in subscriptions_data:
						imported_switches[uid]["cached_data"] = subscriptions_data[uid]
						imported_switches[uid]["name"] = subscriptions_data[uid].get("name", imported_switches[uid].get("name"))
						updated_cache = True
				
				if updated_cache:
					# Update options with fresh cache
					new_options = dict(options)
					new_options["imported_switches"] = imported_switches
					self.hass.config_entries.async_update_entry(
						self.config_entry, options=new_options
					)
					_LOGGER.debug("Updated cache for %d imported switches", len(imported_switches))
			
			return {
				"switches": switches_data,
				"subscriptions": subscriptions_data,
				"last_update": self.hass.loop.time()
			}
			
		except VomeSyncAPIError as ex:
			_LOGGER.error("Error communicating with VomeSync API: %s", ex)
			return {}

	async def _ensure_websocket_connection(self, uid: str) -> None:
		"""Ensure WebSocket connection exists for UID."""
		if uid not in self._websocket_connections:
			try:
				await self.websocket_client.subscribe(uid)
				self._websocket_connections[uid] = True
				_LOGGER.debug("Established WebSocket connection for switch %s", uid)
			except Exception as ex:
				_LOGGER.warning("Failed to establish WebSocket connection for %s: %s", uid, ex)
				self._websocket_connections[uid] = False

	async def _handle_websocket_message(self, uid: Any, message: Optional[Dict[str, Any]] = None) -> None:
		"""Handle incoming WebSocket message."""
		# Support both call styles: (uid, message) and (message_with_uid)
		if message is None and isinstance(uid, dict):
			message = uid
			uid = message.get("uid")
		
		if message is None or uid is None:
			_LOGGER.warning("Received malformed WebSocket message: %s", message)
			return
		
		message_type = message.get("type")
		
		if message_type == "state_update":
			# Update local state
			if uid in self.switches:
				self.switches[uid]["state"] = message["state"]
				self.switches[uid]["lastToggled"] = message["timestamp"]
			
			if uid in self.subscriptions:
				self.subscriptions[uid]["state"] = message["state"]
				self.subscriptions[uid]["lastToggled"] = message["timestamp"]
			
			# Trigger linked entities
			params = message.get("params") or {}
			await self._trigger_linked_entities(uid, message["state"], params)
			
			# Trigger entity updates
			self.async_update_listeners()
			
			_LOGGER.debug("WebSocket state update for %s: %s", uid, message["state"])
			
		elif message_type == "error":
			_LOGGER.warning("WebSocket error for %s: %s", uid, message.get("message"))

	async def toggle_switch(self, uid: str) -> bool:
		"""Toggle a switch with rate limiting."""
		import time
		
		# Rate limiting to prevent API spam
		current_time = time.time()
		last_toggle = self._last_toggle_time.get(uid, 0)
		
		if current_time - last_toggle < self._toggle_cooldown:
			_LOGGER.warning(
				"Rate limit: Skipping toggle for %s (%.1fs since last toggle, cooldown: %.1fs)",
				uid, current_time - last_toggle, self._toggle_cooldown
			)
			return False
		
		try:
			# Update last toggle time before making the request
			self._last_toggle_time[uid] = current_time
			
			result = await self.api_client.toggle_switch(uid)
			
			# Update local state immediately
			timestamp = result.get("timestamp")
			if timestamp is None:
				timestamp = current_time
			
			new_state = result.get("state", not self.switches.get(uid, {}).get("state", False))
			if uid in self.switches:
				self.switches[uid]["state"] = new_state
				self.switches[uid]["lastToggled"] = timestamp
			
			if uid in self.subscriptions:
				self.subscriptions[uid]["state"] = new_state
				self.subscriptions[uid]["lastToggled"] = timestamp
			
			# Trigger entity updates
			self.async_update_listeners()
			
			return True
			
		except VomeSyncAPIError as ex:
			_LOGGER.error("Failed to toggle switch %s: %s", uid, ex)
			return False

	async def create_switch(
		self,
		name: str,
		description: str = "",
		location: str = "",
		category: str = "Other",
		publicize: bool = False,
	) -> Optional[str]:
		"""Create a new switch."""
		try:
			result = await self.api_client.create_switch(
				description=description,
				location=location,
				category=category,
				publicize=publicize
			)
			
			uid = result["uid"]
			
			_LOGGER.info("Switch created via API: uid=%s, name=%s", uid, name)
			
			# Add to coordinator's local cache
			self.switches[uid] = {
				**result,
				"name": name,
				"is_owner": True
			}
			
			# Automatically import this switch (add to local cache)
			options = dict(self.config_entry.options or {})
			imported_switches = options.get("imported_switches", {})
			imported_switches[uid] = {
				"name": name,
				"is_owner": True,
				"cached_data": self.switches[uid]
			}
			options["imported_switches"] = imported_switches
			
			self.hass.config_entries.async_update_entry(
				self.config_entry, options=options
			)
			_LOGGER.info("Auto-imported newly created switch: %s", name)
			
			# Establish WebSocket connection
			await self._ensure_websocket_connection(uid)
			
			# Dynamically add the new switch entity
			if self.async_add_switch_entities:
				from .switch import VomeSyncSwitch
				entity = VomeSyncSwitch(self, uid, name, True, self.config_entry)
				self.async_add_switch_entities([entity])
				self.entity_names[uid] = name
				_LOGGER.info("Dynamically added switch entity: %s (uid=%s)", name, uid)
			else:
				_LOGGER.warning("Cannot add entity dynamically - async_add_switch_entities not available")
			
			# Trigger a coordinator update to refresh all entities
			self.async_update_listeners()
			
			return uid
			
		except VomeSyncAPIError as ex:
			_LOGGER.error("Failed to create switch: %s", ex)
			return None

	async def subscribe_to_switch(self, name: str, uid: str) -> bool:
		"""Subscribe to an existing switch."""
		try:
			# Check if switch exists
			status = await self.api_client.get_switch_status(uid)
			if not status:
				return False
			
			_LOGGER.info("Subscribed to switch via API: uid=%s, name=%s", uid, name)
			
			# Add to coordinator's local cache
			self.subscriptions[uid] = {
				**status,
				"name": name,
				"is_owner": False
			}
			
			# Automatically import this subscription (add to local cache)
			options = dict(self.config_entry.options or {})
			imported_switches = options.get("imported_switches", {})
			imported_switches[uid] = {
				"name": name,
				"is_owner": False,
				"cached_data": self.subscriptions[uid]
			}
			options["imported_switches"] = imported_switches
			
			self.hass.config_entries.async_update_entry(
				self.config_entry, options=options
			)
			_LOGGER.info("Auto-imported newly subscribed switch: %s", name)
			
			# Establish WebSocket connection
			await self._ensure_websocket_connection(uid)
			
			# Dynamically add the new switch entity
			if self.async_add_switch_entities:
				from .switch import VomeSyncSwitch
				entity = VomeSyncSwitch(self, uid, name, False, self.config_entry)
				self.async_add_switch_entities([entity])
				self.entity_names[uid] = name
				_LOGGER.info("Dynamically added subscription entity: %s (uid=%s)", name, uid)
			else:
				_LOGGER.warning("Cannot add entity dynamically - async_add_switch_entities not available")
			
			# Trigger a coordinator update to refresh all entities
			self.async_update_listeners()
			
			return True
			
		except VomeSyncAPIError as ex:
			_LOGGER.error("Failed to subscribe to switch %s: %s", uid, ex)
			return False

	async def delete_switch(self, uid: str) -> bool:
		"""Delete a switch."""
		try:
			success = await self.api_client.delete_switch(uid)
			
			if success:
				_LOGGER.info("Switch deleted via API: uid=%s", uid)
				
				# Remove from local data
				self.switches.pop(uid, None)
				self.subscriptions.pop(uid, None)
				self.entity_names.pop(uid, None)
				
				# Remove from imported switches cache
				options = dict(self.config_entry.options or {})
				imported_switches = options.get("imported_switches", {})
				if uid in imported_switches:
					del imported_switches[uid]
					options["imported_switches"] = imported_switches
					self.hass.config_entries.async_update_entry(
						self.config_entry, options=options
					)
					_LOGGER.info("Removed switch %s from imported cache", uid)
				
				# Close WebSocket connection
				await self.websocket_client.unsubscribe(uid)
				self._websocket_connections.pop(uid, None)
				
				# Note: Entity will become unavailable and should be removed on integration reload
				_LOGGER.info("Switch %s removed - entity will be unavailable until integration reload", uid)
			
			return success
			
		except VomeSyncAPIError as ex:
			_LOGGER.error("Failed to delete switch %s: %s", uid, ex)
			return False

	async def async_shutdown(self) -> None:
		"""Shutdown the coordinator."""
		_LOGGER.info("Shutting down VomeSync coordinator")
		
		# Close WebSocket connections
		await self.websocket_client.disconnect()
		
		# Close API client
		await self.api_client.close()

	def get_switch_data(self, uid: str) -> Optional[Dict[str, Any]]:
		"""Get switch data by UID."""
		return self.switches.get(uid) or self.subscriptions.get(uid)

	def is_switch_owner(self, uid: str) -> bool:
		"""Check if user owns the switch."""
		switch_data = self.get_switch_data(uid)
		return bool(switch_data and switch_data.get("is_owner", False))

	@property
	def personal_key(self) -> str:
		"""Get personal key."""
		return self.config_entry.data[CONF_PERSONAL_KEY]

	async def _trigger_linked_entities(self, uid: str, state: bool, params: Optional[Dict[str, Any]] = None) -> None:
		"""Trigger linked entities when switch state changes."""
		import time
		
		# Rate limiting to prevent infinite loops
		current_time = time.time()
		last_trigger = self._last_trigger_time.get(uid, 0)
		
		if current_time - last_trigger < self._trigger_cooldown:
			_LOGGER.warning(
				"Rate limit: Skipping trigger for %s (%.1fs since last trigger, cooldown: %.1fs)",
				uid, current_time - last_trigger, self._trigger_cooldown
			)
			return
		
		options = self.config_entry.options or {}
		linked_entities = options.get("linked_entities", {})
		
		_LOGGER.debug("Checking linked entities for %s. All linked: %s", uid, linked_entities)
		
		entities_to_trigger = linked_entities.get(uid, [])
		if not entities_to_trigger:
			_LOGGER.debug("No linked entities found for switch %s", uid)
			return
		
		# Update last trigger time
		self._last_trigger_time[uid] = current_time
		
		_LOGGER.info(
			"Triggering %d linked entities for switch %s (state: %s): %s",
			len(entities_to_trigger),
			uid,
			state,
			entities_to_trigger,
		)
		
		# Call turn_on or turn_off for each linked entity (with graceful fallback if params not supported)
		service = "turn_on" if state else "turn_off"
		
		for entity_id in entities_to_trigger:
			try:
				domain = entity_id.split(".")[0]
				_LOGGER.info("Calling %s.%s for %s", domain, service, entity_id)
				
				# Build service data; include parameters for lights if provided
				service_data = {"entity_id": entity_id}
				if params and state:
					for key in ("rgb_color", "hs_color", "xy_color", "color_temp", "brightness", "transition", "effect", "color_mode"):
						if key in params:
							service_data[key] = params[key]
				
				await self.hass.services.async_call(
					domain,
					service,
					service_data=service_data,
					blocking=False
				)
			except Exception as err:
				_LOGGER.warning("Failed to trigger linked entity %s with params (%s); retrying without params", entity_id, err)
				try:
					# Retry with minimal payload
					await self.hass.services.async_call(
						domain,
						service,
						service_data={"entity_id": entity_id},
						blocking=False
					)
				except Exception as err2:
					_LOGGER.error("Failed to trigger linked entity %s (fallback): %s", entity_id, err2)

	async def async_setup_entity_links(self) -> None:
		"""Set up entity links (called after updating config options)."""
		options = self.config_entry.options or {}
		linked_entities = options.get("linked_entities", {})
		_LOGGER.info("Entity links configured: %s", linked_entities)
		# Refresh the data to ensure we have latest state
		await self.async_request_refresh()

	async def async_add_imported_entities(self) -> None:
		"""Dynamically add entities for imported switches without reloading the entry."""
		if not self.async_add_switch_entities:
			_LOGGER.warning("Cannot add imported entities dynamically; async_add_switch_entities not set")
			return
		
		options = self.config_entry.options or {}
		imported_switches = options.get("imported_switches", {})
		if not imported_switches:
			_LOGGER.info("No imported switches to add dynamically")
			return
		
		from .switch import VomeSyncSwitch  # local import to avoid circular
		
		new_entities = []
		for uid, info in imported_switches.items():
			if uid in self.entity_names:
				continue
			
			name = info.get("name", f"Switch {uid[:8]}")
			is_owner = info.get("is_owner", False)
			
			# Ensure websocket connection if possible
			try:
				await self._ensure_websocket_connection(uid)
			except Exception as ex:  # noqa: BLE001
				_LOGGER.debug("WebSocket ensure failed for %s (non-fatal during add): %s", uid, ex)
			
			entity = VomeSyncSwitch(self, uid, name, is_owner, self.config_entry)
			new_entities.append(entity)
			self.entity_names[uid] = name
		
		if new_entities:
			self.async_add_switch_entities(new_entities)
			_LOGGER.info("Dynamically added %d imported switch entities", len(new_entities))

	@property
	def server_url(self) -> str:
		"""Get server URL."""
		return self.config_entry.data[CONF_SERVER_URL]
