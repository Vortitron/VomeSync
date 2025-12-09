"""Data update coordinator for VomeSync integration."""
import asyncio
import logging
from datetime import timedelta
from typing import Any, Dict, Optional

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

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

	async def _async_update_data(self) -> Dict[str, Any]:
		"""Fetch data from API."""
		try:
			# Get user's switches
			my_switches = await self.api_client.get_my_switches()
			
			# Get names from options if available
			options = self.config_entry.options or {}
			saved_switches = options.get("switches", {})
			
			# Build uid->name mapping from options
			uid_to_name = {}
			for name, switch_config in saved_switches.items():
				if "uid" in switch_config:
					uid_to_name[switch_config["uid"]] = name
			
			# Also check entity_names for existing mappings
			if not hasattr(self, 'entity_names') or self.entity_names is None:
				self.entity_names = {}
			
			# Update switches data
			switches_data = {}
			for switch in my_switches:
				uid = switch["uid"]
				# Priority: saved options > entity_names > description > uid
				name = uid_to_name.get(uid) or self.entity_names.get(uid) or switch.get("description") or uid[:8]
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
			
			# Store current data
			self.switches = switches_data
			self.subscriptions = subscriptions_data
			
			return {
				"switches": switches_data,
				"subscriptions": subscriptions_data,
				"last_update": self.hass.loop.time()
			}
			
		except VomeSyncAPIError as ex:
			raise UpdateFailed(f"Error communicating with VomeSync API: {ex}") from ex

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

	async def _handle_websocket_message(self, uid: str, message: Dict[str, Any]) -> None:
		"""Handle incoming WebSocket message."""
		message_type = message.get("type")
		
		if message_type == "state_update":
			# Update local state
			if uid in self.switches:
				self.switches[uid]["state"] = message["state"]
				self.switches[uid]["lastToggled"] = message["timestamp"]
			
			if uid in self.subscriptions:
				self.subscriptions[uid]["state"] = message["state"]
				self.subscriptions[uid]["lastToggled"] = message["timestamp"]
			
			# Trigger entity updates
			self.async_update_listeners()
			
			_LOGGER.debug("WebSocket state update for %s: %s", uid, message["state"])
			
		elif message_type == "error":
			_LOGGER.warning("WebSocket error for %s: %s", uid, message.get("message"))

	async def toggle_switch(self, uid: str) -> bool:
		"""Toggle a switch."""
		try:
			result = await self.api_client.toggle_switch(uid)
			
			# Update local state immediately
			new_state = result["state"]
			if uid in self.switches:
				self.switches[uid]["state"] = new_state
				self.switches[uid]["lastToggled"] = result["timestamp"]
			
			if uid in self.subscriptions:
				self.subscriptions[uid]["state"] = new_state
				self.subscriptions[uid]["lastToggled"] = result["timestamp"]
			
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
			
			# Add to switches
			self.switches[uid] = {
				**result,
				"name": name,
				"is_owner": True
			}
			
			# Establish WebSocket connection
			await self._ensure_websocket_connection(uid)
			
			# Update config entry options
			options = dict(self.config_entry.options or {})
			switches = options.setdefault("switches", {})
			switches[name] = {
				"uid": uid,
				"description": description,
				"location": location,
				"category": category,
				"publicize": publicize,
				"is_owner": True
			}
			
			self.hass.config_entries.async_update_entry(
				self.config_entry, options=options
			)
			
			# Dynamically add the new switch entity
			if self.async_add_switch_entities:
				from .switch import VomeSyncSwitch
				entity = VomeSyncSwitch(self, uid, name, True)
				self.async_add_switch_entities([entity])
				_LOGGER.info("Dynamically added switch entity: %s", name)
			else:
				_LOGGER.warning("Cannot add entity dynamically - async_add_switch_entities not available")
			
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
			
			# Add to subscriptions
			self.subscriptions[uid] = {
				**status,
				"name": name,
				"is_owner": False
			}
			
			# Establish WebSocket connection
			await self._ensure_websocket_connection(uid)
			
			# Update config entry options
			options = dict(self.config_entry.options or {})
			subscriptions = options.setdefault("subscriptions", {})
			subscriptions[name] = {
				"uid": uid,
				"is_owner": False
			}
			
			self.hass.config_entries.async_update_entry(
				self.config_entry, options=options
			)
			
			# Dynamically add the new switch entity
			if self.async_add_switch_entities:
				from .switch import VomeSyncSwitch
				entity = VomeSyncSwitch(self, uid, name, False)
				self.async_add_switch_entities([entity])
				_LOGGER.info("Dynamically added subscription entity: %s", name)
			else:
				_LOGGER.warning("Cannot add entity dynamically - async_add_switch_entities not available")
			
			return True
			
		except VomeSyncAPIError as ex:
			_LOGGER.error("Failed to subscribe to switch %s: %s", uid, ex)
			return False

	async def delete_switch(self, uid: str) -> bool:
		"""Delete a switch."""
		try:
			success = await self.api_client.delete_switch(uid)
			
			if success:
				# Remove from local data
				self.switches.pop(uid, None)
				self.subscriptions.pop(uid, None)
				
				# Close WebSocket connection
				await self.websocket_client.unsubscribe(uid)
				self._websocket_connections.pop(uid, None)
				
				# Remove from options
				options = dict(self.config_entry.options or {})
				switches = options.get("switches", {})
				subscriptions = options.get("subscriptions", {})
				
				# Find and remove from switches
				for name, switch_config in list(switches.items()):
					if switch_config.get("uid") == uid:
						del switches[name]
						break
				
				# Find and remove from subscriptions
				for name, sub_config in list(subscriptions.items()):
					if sub_config.get("uid") == uid:
						del subscriptions[name]
						break
				
				self.hass.config_entries.async_update_entry(
					self.config_entry, options=options
				)
				
				# Note: Entity will be removed on next HA restart or integration reload
				# Dynamic entity removal requires entity registry manipulation
				_LOGGER.info("Switch %s deleted from backend - entity will be removed on integration reload", uid)
			
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

	@property
	def server_url(self) -> str:
		"""Get server URL."""
		return self.config_entry.data[CONF_SERVER_URL]
