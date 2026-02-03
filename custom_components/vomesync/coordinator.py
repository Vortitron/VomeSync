"""Data update coordinator for VomeSync integration."""
from collections import deque
import inspect
import logging
import time
from datetime import timedelta
from typing import Any, Callable, Dict, Optional, Set

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_ON, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .api_client import VomeSyncAPIClient, VomeSyncAPIError
from .websocket_client import VomeSyncWebSocketClient
from .log_utils import log_throttled
from .const import (
	DOMAIN,
	CONF_PERSONAL_KEY,
	CONF_SERVER_URL,
	CONF_WEBSOCKET_URL,
	CONF_AUTH_MODE,
	CONF_CRYPTO_SEED,
	AUTH_MODE_CRYPTO,
	UPDATE_INTERVAL_SECONDS,
	DEFAULT_SWITCH_NAME,
)

_LOGGER = logging.getLogger(__name__)

_OPT_IMPORTED_SWITCHES = "imported_switches"
_OPT_CRYPTO_NEXT_INDEX = "crypto_next_index"
_OPT_CRYPTO_INDEX = "crypto_index"

_LINKED_TRIGGER_BURST_WINDOW_SECONDS = 10.0
_LINKED_TRIGGER_BURST_MAX = 6
_LINKED_TRIGGER_BLOCK_SECONDS = 30.0

_LINKED_ENTITY_SUPPRESSION_SECONDS = 2.0

_TOGGLE_COOLDOWN_SECONDS = 1.0

_LINK_CFG_ENTITIES = "entities"
_LINK_CFG_MODE = "mode"
_LINK_CFG_MASTER = "master"
_LINK_CFG_DIRECTION = "direction"

_LINK_MODE_MASTER = "master"
_LINK_MODE_OR = "or"
_LINK_MODE_AND = "and"

_VALID_LINK_MODES: Set[str] = {_LINK_MODE_MASTER, _LINK_MODE_OR, _LINK_MODE_AND}

_LINK_DIR_BOTH = "both"
_LINK_DIR_SWITCH_TO_ENTITIES = "switch_to_entities"
_LINK_DIR_ENTITIES_TO_SWITCH = "entities_to_switch"

_VALID_LINK_DIRECTIONS: Set[str] = {_LINK_DIR_BOTH, _LINK_DIR_SWITCH_TO_ENTITIES, _LINK_DIR_ENTITIES_TO_SWITCH}

_LINK_PARAM_KEYS: tuple[str, ...] = (
	"rgb_color",
	"hs_color",
	"xy_color",
	"color_temp",
	"brightness",
	"transition",
	"effect",
	"color_mode",
)


class VomeSyncCoordinator(DataUpdateCoordinator[Dict[str, Any]]):
	"""VomeSync data update coordinator."""

	async def _async_update_entry_options(self, options: Dict[str, Any]) -> None:
		"""Update config entry options, awaiting only if HA returns an awaitable."""
		result = self.hass.config_entries.async_update_entry(self.config_entry, options=options)
		if inspect.isawaitable(result):
			await result

	def __init__(self, hass: HomeAssistant, config_entry: ConfigEntry) -> None:
		"""Initialize the coordinator."""
		self.config_entry = config_entry
		entry_data = config_entry.data or {}
		self.api_client = VomeSyncAPIClient(
			entry_data[CONF_SERVER_URL],
			entry_data.get(CONF_PERSONAL_KEY),
			auth_mode=entry_data.get(CONF_AUTH_MODE),
			crypto_seed=entry_data.get(CONF_CRYPTO_SEED),
		)
		self.websocket_client = VomeSyncWebSocketClient(
			entry_data[CONF_WEBSOCKET_URL],
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
		self.async_add_sensor_entities = None
		
		# Entity name mapping (uid -> friendly_name)
		self.entity_names: Dict[str, str] = {}
		
		# Loop protection for linked entity triggers:
		# allow a short burst (user flicking on/off), but block runaway feedback loops.
		self._linked_trigger_history: Dict[str, deque[float]] = {}
		self._linked_trigger_blocked_until: Dict[str, float] = {}
		
		# Bidirectional linking (entity -> owned switch)
		self._linked_entity_listener_unsub: Optional[Callable[[], None]] = None
		self._linked_entity_to_uids: Dict[str, Set[str]] = {}
		self._linked_config_by_uid: Dict[str, Dict[str, Any]] = {}
		self._linked_entity_suppress_until: Dict[str, float] = {}
		
		# Rate limiting for toggle requests (prevent API spam)
		self._last_toggle_time: Dict[str, float] = {}
		self._toggle_cooldown = _TOGGLE_COOLDOWN_SECONDS
		self._warning_throttle: Dict[str, float] = {}
		self._last_owned_switch_count: Optional[int] = None

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
			pending_name_sync: list[tuple[str, str]] = []
			for switch in my_switches:
				uid = switch["uid"]
				# Priority: API name > cached name > entity_names > default
				name = (
					switch.get("name")
					or uid_to_name.get(uid)
					or self.entity_names.get(uid)
					or DEFAULT_SWITCH_NAME
				)
				if not switch.get("name") and name and name != DEFAULT_SWITCH_NAME:
					pending_name_sync.append((uid, name))
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

			owned_count = len(switches_data)
			if owned_count == 0 and (self._last_owned_switch_count is None or self._last_owned_switch_count > 0):
				_LOGGER.warning("No owned switches returned from API; entities may appear unavailable")
			
			# Get status for subscribed switches.
			#
			# Primary source: imported cache entries with is_owner=False (this is what the UI uses).
			# Backward-compat: older versions stored subscriptions under options["subscriptions"].
			options = self.config_entry.options or {}
			imported_switches = options.get("imported_switches", {}) or {}
			legacy_subscriptions = options.get("subscriptions", {}) or {}
			
			# Build uid->name mapping for subscriptions
			sub_uid_to_name: Dict[str, str] = {}
			for uid, info in imported_switches.items():
				if not isinstance(uid, str) or not isinstance(info, dict):
					continue
				if info.get("is_owner", False):
					continue
				sub_uid_to_name[uid] = info.get("name") or DEFAULT_SWITCH_NAME
			
			# Legacy format: {friendly_name: {"uid": "..."}}
			for name, sub_config in legacy_subscriptions.items():
				if not isinstance(sub_config, dict):
					continue
				uid = sub_config.get("uid")
				if isinstance(uid, str) and uid not in sub_uid_to_name:
					sub_uid_to_name[uid] = name or DEFAULT_SWITCH_NAME
			
			subscriptions_data: Dict[str, Dict[str, Any]] = {}
			for uid, name in sub_uid_to_name.items():
				status = await self.api_client.get_switch_status(uid)
				if status:
					subscriptions_data[uid] = {
						**status,
						"name": name,
						"is_owner": False,
					}
					
					# Ensure WebSocket connection for subscriptions
					if self._websocket_connections.get(uid) is not True:
						await self._ensure_websocket_connection(uid)
				else:
					log_throttled(
						_LOGGER.warning,
						self._warning_throttle,
						f"sub_status_missing:{uid}",
						600,
						"Imported subscription %s (uid=%s) returned no status from API",
						name,
						uid
					)
			
			self._last_owned_switch_count = owned_count

			# Store current data
			self.switches = switches_data
			self.subscriptions = subscriptions_data

			if pending_name_sync:
				for uid, name in pending_name_sync:
					try:
						await self.update_switch_metadata(uid, {"name": name})
					except Exception as ex:  # noqa: BLE001
						_LOGGER.debug("Name sync skipped for %s: %s", uid, ex)
			
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
					await self._async_update_entry_options(new_options)
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
				log_throttled(
					_LOGGER.warning,
					self._warning_throttle,
					f"ws_connect_failed:{uid}",
					600,
					"Failed to establish WebSocket connection for %s: %s",
					uid,
					ex
				)
				self._websocket_connections[uid] = False

	async def _handle_websocket_message(self, uid: Any, message: Optional[Dict[str, Any]] = None) -> None:
		"""Handle incoming WebSocket message."""
		# Support both call styles: (uid, message) and (message_with_uid)
		if message is None and isinstance(uid, dict):
			message = uid
			uid = message.get("uid")
		
		if message is None or uid is None:
			log_throttled(
				_LOGGER.warning,
				self._warning_throttle,
				"ws_message_malformed",
				600,
				"Received malformed WebSocket message: %s",
				message
			)
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
			log_throttled(
				_LOGGER.warning,
				self._warning_throttle,
				f"ws_error:{uid}",
				600,
				"WebSocket error for %s: %s",
				uid,
				message.get("message")
			)

	async def toggle_switch(self, uid: str) -> bool:
		"""Toggle a switch with rate limiting."""
		current = bool(self.get_switch_data(uid) and self.get_switch_data(uid).get("state", False))
		return await self.set_switch_state(uid, not current)

	async def toggle_switch_with_access_key(
		self,
		uid: str,
		access_key: str,
		desired_state: Optional[bool] = None,
	) -> bool:
		"""Toggle a switch via a delegated access key (non-owner)."""
		if not uid or not access_key:
			return False
		if self.is_switch_owner(uid):
			_LOGGER.warning("Refusing access-key toggle for owner switch %s", uid)
			return False

		current = bool(self.get_switch_data(uid) and self.get_switch_data(uid).get("state", False))
		if desired_state is not None and current == bool(desired_state):
			return True

		current_time = time.time()
		last_toggle = self._last_toggle_time.get(uid, 0)
		if current_time - last_toggle < self._toggle_cooldown:
			_LOGGER.warning(
				"Rate limit: Skipping access-key toggle for %s (%.1fs since last toggle, cooldown: %.1fs)",
				uid, current_time - last_toggle, self._toggle_cooldown
			)
			return False

		self._last_toggle_time[uid] = current_time
		try:
			result = await self.api_client.toggle_switch_with_access_key(uid, access_key)
			timestamp = result.get("timestamp", current_time)
			new_state = result.get("state", not current)
			if uid in self.subscriptions:
				self.subscriptions[uid]["state"] = new_state
				self.subscriptions[uid]["lastToggled"] = timestamp
				if "toggleCount" in result:
					self.subscriptions[uid]["toggleCount"] = result["toggleCount"]
			self.async_update_listeners()
			return True
		except VomeSyncAPIError as ex:
			_LOGGER.error("Failed to toggle switch via access key %s: %s", uid, ex)
			return False

	def _get_crypto_index_for_uid(self, uid: str) -> Optional[int]:
		"""Resolve crypto index for a v2-owned switch."""
		switch_data = self.switches.get(uid) or {}
		idx = switch_data.get("index")
		if isinstance(idx, int):
			return idx
		
		options = self.config_entry.options or {}
		imported = options.get(_OPT_IMPORTED_SWITCHES, {}) or {}
		info = imported.get(uid) or {}
		idx2 = info.get(_OPT_CRYPTO_INDEX)
		if isinstance(idx2, int):
			return idx2
		return None

	async def set_switch_state(self, uid: str, state: bool, params: Optional[Dict[str, Any]] = None) -> bool:
		"""Set a switch state (v2 signed endpoint)."""
		import time

		if not self.is_switch_owner(uid):
			_LOGGER.warning("Refusing to set state for %s: not owner", uid)
			return False
		
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

			# Crypto path (owned switches only)
			if self.crypto_enabled and self.is_switch_owner(uid):
				idx = self._get_crypto_index_for_uid(uid)
				if idx is None:
					_LOGGER.error("Cannot set state for %s: missing crypto index", uid)
					return False
				result = await self.api_client.set_switch_state_v2(uid, idx, state, params=params)
			else:
				# Legacy path: only toggle when change is required (toggle endpoint flips)
				current = bool(self.get_switch_data(uid) and self.get_switch_data(uid).get("state", False))
				if current == bool(state):
					return True
				result = await self.api_client.toggle_switch(uid)
			
			# Update local state immediately
			timestamp = result.get("timestamp")
			if timestamp is None:
				timestamp = current_time
			
			new_state = result.get("state", bool(state))
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

	async def reannounce_owned_switches(self) -> Dict[str, Any]:
		"""Re-announce (re-create if missing) v2-owned switches on the current server.

		This is useful when pointing the integration at a fresh server/DB: v2 switch UIDs are
		deterministic from the signing key + index, so we can re-create the same UIDs.
		"""
		result: Dict[str, Any] = {
			"eligible": 0,
			"attempted": 0,
			"succeeded": 0,
			"skipped": 0,
			"errors": [],
		}

		if not self.crypto_enabled:
			result["errors"].append("No signing key configured for this entry.")
			return result

		options = dict(self.config_entry.options or {})
		imported = options.get(_OPT_IMPORTED_SWITCHES, {}) or {}
		changed = False

		# Build a list first (avoid mutating while iterating)
		targets: list[tuple[str, int, Dict[str, Any]]] = []
		for uid, info in imported.items():
			if not isinstance(uid, str):
				result["skipped"] += 1
				continue
			if not isinstance(info, dict):
				result["skipped"] += 1
				continue
			if not info.get("is_owner", False):
				continue

			# Only v2 UIDs are re-announceable (deterministic)
			if not uid.startswith("vs_"):
				result["skipped"] += 1
				continue

			idx = info.get(_OPT_CRYPTO_INDEX)
			cached = info.get("cached_data") if isinstance(info.get("cached_data"), dict) else {}
			if not isinstance(idx, int):
				# Fall back to cached_data.index if present
				idx2 = cached.get("index") if isinstance(cached, dict) else None
				if isinstance(idx2, int):
					idx = idx2
					info[_OPT_CRYPTO_INDEX] = idx
					changed = True

			if not isinstance(idx, int):
				result["skipped"] += 1
				continue

			targets.append((uid, idx, cached if isinstance(cached, dict) else {}))

		result["eligible"] = len(targets)
		if changed:
			options[_OPT_IMPORTED_SWITCHES] = imported
			await self._async_update_entry_options(options)

		for uid, idx, cached in targets:
			result["attempted"] += 1
			try:
				# Best-effort metadata restore
				description = cached.get("description", "") if isinstance(cached, dict) else ""
				location = cached.get("location", "") if isinstance(cached, dict) else ""
				category = cached.get("category", "Other") if isinstance(cached, dict) else "Other"
				publicize = bool(cached.get("publicize", False)) if isinstance(cached, dict) else False
				link = cached.get("link", "") if isinstance(cached, dict) else ""
				icon_url = cached.get("iconUrl", "") if isinstance(cached, dict) else ""
				banner_url = cached.get("bannerUrl", "") if isinstance(cached, dict) else ""

				resp = await self.api_client.create_switch_v2(
					index=idx,
					description=description,
					location=location,
					category=category,
					publicize=publicize,
					link=link,
					icon_url=icon_url or None,
					banner_url=banner_url or None,
					captcha_token="",
				)
				created_uid = resp.get("uid")
				if created_uid and created_uid != uid:
					result["errors"].append(f"{uid}: server returned different UID ({created_uid}) for index={idx}")
					continue
				result["succeeded"] += 1
			except VomeSyncAPIError as ex:
				result["errors"].append(f"{uid}: {ex}")
			except Exception as ex:  # noqa: BLE001
				result["errors"].append(f"{uid}: unexpected error: {ex}")

		# Refresh local state after re-announcing
		try:
			await self.async_request_refresh()
		except Exception as ex:  # noqa: BLE001
			_LOGGER.debug("Refresh after re-announce skipped (non-fatal): %s", ex)

		return result

	async def create_switch(
		self,
		name: str,
		description: str = "",
		location: str = "",
		category: str = "Other",
		publicize: bool = False,
		link: str = "",
		icon_url: Optional[str] = None,
		banner_url: Optional[str] = None,
		captcha_token: str = "",
	) -> Optional[str]:
		"""Create a new switch."""
		try:
			# Keep description optional; name is stored separately on the server.
			
			options = dict(self.config_entry.options or {})
			imported_switches = options.get(_OPT_IMPORTED_SWITCHES, {}) or {}
			
			if self.crypto_enabled:
				# Pick the next free index for deterministic subkeys
				used_indices: set[int] = set()

				for info in imported_switches.values():
					idx = info.get(_OPT_CRYPTO_INDEX)
					if isinstance(idx, int):
						used_indices.add(idx)
				
				for sw in (self.switches or {}).values():
					idx = sw.get("index")
					if isinstance(idx, int):
						used_indices.add(idx)
				
				start_idx = options.get(_OPT_CRYPTO_NEXT_INDEX, 0)
				if not isinstance(start_idx, int) or start_idx < 0:
					start_idx = 0
				
				index = start_idx
				while index in used_indices:
					index += 1
				
				result = await self.api_client.create_switch_v2(
					index=index,
					name=name,
					description=description,
					location=location,
					category=category,
					publicize=publicize,
					link=link,
					icon_url=icon_url,
					banner_url=banner_url,
					captcha_token=captcha_token,
				)
				uid = result["uid"]
				# Ensure index is stored locally even if server omits it for any reason
				result.setdefault("index", index)
				options[_OPT_CRYPTO_NEXT_INDEX] = index + 1
			else:
				result = await self.api_client.create_switch(
					name=name,
					description=description,
					location=location,
					category=category,
					publicize=publicize,
					link=link,
					captcha_token=captcha_token,
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
			imported_switches[uid] = {
				"name": name,
				"is_owner": True,
				"cached_data": self.switches[uid],
				**({_OPT_CRYPTO_INDEX: self.switches[uid].get("index")} if self.crypto_enabled else {}),
			}
			options[_OPT_IMPORTED_SWITCHES] = imported_switches
			
			await self._async_update_entry_options(options)
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

	async def update_switch_metadata(self, uid: str, updates: Dict[str, Any], captcha_token: str = "") -> Optional[Dict[str, Any]]:
		"""Update switch metadata on the server (v2 signed when crypto is enabled)."""
		if not isinstance(uid, str) or not uid:
			return None
		if not isinstance(updates, dict) or not updates:
			return None
		if not self.is_switch_owner(uid):
			_LOGGER.warning("Refusing metadata update for non-owner switch uid=%s", uid)
			return None

		try:
			switch_data = self.switches.get(uid) or {}
			is_v2 = bool(uid.startswith("vs_") or switch_data.get("authVersion") == 2)
			updated: Dict[str, Any]

			if self.crypto_enabled and is_v2:
				updated = await self.api_client.update_switch_v2_metadata(uid, updates, captcha_token=captcha_token)
			else:
				# Legacy v1 update (personal key auth) - best effort
				endpoint_updates = dict(updates)
				if captcha_token:
					endpoint_updates["captchaToken"] = captcha_token
				updated = await self.api_client.update_switch(uid, endpoint_updates)

			# Merge back into local cache, preserving local-only fields
			local_name = (switch_data or {}).get("name") or DEFAULT_SWITCH_NAME
			updated_name = None
			if isinstance(updated, dict) and "name" in updated:
				updated_name = updated.get("name")
			elif "name" in updates:
				updated_name = updates.get("name")
			merged_name = updated_name if updated_name is not None else local_name
			merged = {
				**(switch_data if isinstance(switch_data, dict) else {}),
				**(updated if isinstance(updated, dict) else {}),
				"name": merged_name,
				"is_owner": True
			}
			self.switches[uid] = merged
			if hasattr(self, "entity_names") and isinstance(self.entity_names, dict):
				self.entity_names[uid] = merged_name

			# Persist to imported cache if present
			options = dict(self.config_entry.options or {})
			imported_switches = options.get(_OPT_IMPORTED_SWITCHES, {}) or {}
			if uid in imported_switches:
				imported_switches[uid]["cached_data"] = merged
				imported_switches[uid]["name"] = merged_name
				options[_OPT_IMPORTED_SWITCHES] = imported_switches
				await self._async_update_entry_options(options)

			self.async_update_listeners()
			return merged
		except VomeSyncAPIError as ex:
			_LOGGER.error("Failed to update switch metadata uid=%s: %s", uid, ex)
			raise
		except Exception as ex:  # noqa: BLE001
			_LOGGER.error("Failed to update switch metadata uid=%s (unexpected): %s", uid, ex)
			return None

	async def create_v2_access_key(
		self,
		uid: str,
		name: str = "",
		permissions: Optional[list[str]] = None,
		ttl_seconds: Optional[int] = None
	) -> Optional[Dict[str, Any]]:
		"""Create a delegated v2 access key for a switch (owner-only)."""
		if not self.crypto_enabled:
			_LOGGER.warning("Cannot create v2 access key: crypto mode not enabled")
			return None
		if not self.is_switch_owner(uid):
			_LOGGER.warning("Cannot create v2 access key: not owner (uid=%s)", uid)
			return None
		try:
			return await self.api_client.create_v2_access_key(
				uid,
				name=name,
				permissions=permissions,
				ttl_seconds=ttl_seconds
			)
		except VomeSyncAPIError as ex:
			_LOGGER.error("Failed to create v2 access key uid=%s: %s", uid, ex)
			return None

	async def list_v2_access_keys(self, uid: str) -> Optional[Dict[str, Any]]:
		"""List delegated v2 access keys for a switch (owner-only)."""
		if not self.crypto_enabled:
			_LOGGER.warning("Cannot list v2 access keys: crypto mode not enabled")
			return None
		if not self.is_switch_owner(uid):
			_LOGGER.warning("Cannot list v2 access keys: not owner (uid=%s)", uid)
			return None
		try:
			return await self.api_client.list_v2_access_keys(uid)
		except VomeSyncAPIError as ex:
			_LOGGER.error("Failed to list v2 access keys uid=%s: %s", uid, ex)
			return None

	async def revoke_v2_access_key(self, uid: str, api_key: str) -> bool:
		"""Revoke a delegated v2 access key for a switch (owner-only)."""
		if not self.crypto_enabled:
			_LOGGER.warning("Cannot revoke v2 access key: crypto mode not enabled")
			return False
		if not self.is_switch_owner(uid):
			_LOGGER.warning("Cannot revoke v2 access key: not owner (uid=%s)", uid)
			return False
		try:
			return await self.api_client.revoke_v2_access_key(uid, api_key)
		except VomeSyncAPIError as ex:
			_LOGGER.error("Failed to revoke v2 access key uid=%s: %s", uid, ex)
			return False

	async def subscribe_to_switch(self, uid: str, access_key: Optional[str] = None) -> bool:
		"""Subscribe to an existing switch."""
		try:
			# Check if switch exists
			status = await self.api_client.get_switch_status(uid)
			if not status:
				_LOGGER.warning("Subscribe failed: switch not found or no status returned (uid=%s)", uid)
				return False
			
			# Derive a sensible default name (user can rename the entity in HA UI)
			name = status.get("name") or DEFAULT_SWITCH_NAME
			
			_LOGGER.info("Subscribed to switch via API: uid=%s, name=%s", uid, name)
			
			# Add to coordinator's local cache
			self.subscriptions[uid] = {
				**status,
				"name": name,
				"is_owner": False
			}
			access_key = str(access_key or "").strip() or None
			
			# Automatically import this subscription (add to local cache)
			options = dict(self.config_entry.options or {})
			imported_switches = options.get("imported_switches", {})
			
			# If already imported, don't try to add a duplicate entity
			if uid in imported_switches:
				_LOGGER.info("Switch already imported in this installation; skipping entity add (uid=%s)", uid)
				await self._ensure_websocket_connection(uid)
				self.async_update_listeners()
				return True
			
			# If entity already exists in HA registry, also skip add (prevents duplicate unique_id errors)
			try:
				from homeassistant.helpers import entity_registry as er
				entity_reg = er.async_get(self.hass)
				existing_entity_id = entity_reg.async_get_entity_id("switch", DOMAIN, f"vomesync_{uid}")
				if existing_entity_id:
					_LOGGER.info(
						"Entity already exists for uid=%s (%s); updating cache only",
						uid,
						existing_entity_id,
					)
					imported_switches[uid] = {
						"name": imported_switches.get(uid, {}).get("name", name),
						"is_owner": False,
						"cached_data": self.subscriptions[uid],
						**({"access_key": access_key} if access_key else {}),
					}
					options["imported_switches"] = imported_switches
					await self._async_update_entry_options(options)
					await self._ensure_websocket_connection(uid)
					self.async_update_listeners()
					return True
			except Exception as ex:  # noqa: BLE001
				_LOGGER.debug("Entity registry check failed (non-fatal): %s", ex)
			
			imported_switches[uid] = {
				"name": name,
				"is_owner": False,
				"cached_data": self.subscriptions[uid],
				**({"access_key": access_key} if access_key else {}),
			}
			options["imported_switches"] = imported_switches
			
			await self._async_update_entry_options(options)
			_LOGGER.info("Auto-imported newly subscribed switch: %s", name)
			
			# Establish WebSocket connection
			await self._ensure_websocket_connection(uid)
			
			if access_key and self.async_add_switch_entities:
				from .switch import VomeSyncSwitch
				entity = VomeSyncSwitch(self, uid, name, False, self.config_entry)
				self.async_add_switch_entities([entity])
				self.entity_names[uid] = name
				_LOGGER.info("Dynamically added subscription switch with access key: %s (uid=%s)", name, uid)
			elif self.async_add_sensor_entities:
				from .sensor import VomeSyncSensor
				entity = VomeSyncSensor(self, uid, name)
				self.async_add_sensor_entities([entity])
				self.entity_names[uid] = name
				_LOGGER.info("Dynamically added subscription sensor: %s (uid=%s)", name, uid)
			else:
				_LOGGER.warning("Cannot add subscription entity dynamically; add_entities callbacks not available")
			
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
					await self._async_update_entry_options(options)
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
		
		# Unregister linked entity listener
		if self._linked_entity_listener_unsub:
			try:
				self._linked_entity_listener_unsub()
			except Exception as ex:  # noqa: BLE001
				_LOGGER.debug("Failed to unsubscribe linked-entity listener: %s", ex)
			self._linked_entity_listener_unsub = None
		
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

	def get_subscription_access_key(self, uid: str) -> Optional[str]:
		"""Return stored access key for a subscribed switch, if any."""
		if not isinstance(uid, str) or not uid:
			return None
		options = self.config_entry.options or {}
		info = (options.get(_OPT_IMPORTED_SWITCHES, {}) or {}).get(uid, {})
		if isinstance(info, dict):
			key = str(info.get("access_key", "") or "").strip()
			return key or None
		return None

	@property
	def personal_key(self) -> str:
		"""Get personal key."""
		return self.config_entry.data.get(CONF_PERSONAL_KEY, "")

	@property
	def crypto_enabled(self) -> bool:
		"""Whether this entry is configured for crypto auth."""
		return bool(self.config_entry.data.get(CONF_AUTH_MODE) == AUTH_MODE_CRYPTO and self.config_entry.data.get(CONF_CRYPTO_SEED))

	async def _trigger_linked_entities(self, uid: str, state: bool, params: Optional[Dict[str, Any]] = None) -> None:
		"""Trigger linked entities when switch state changes."""
		options = self.config_entry.options or {}
		linked_entities = options.get("linked_entities", {}) or {}
		
		_LOGGER.debug("Checking linked entities for %s. All linked: %s", uid, linked_entities)
		
		cfg = self._parse_link_config(linked_entities.get(uid))
		direction = cfg.get(_LINK_CFG_DIRECTION, _LINK_DIR_BOTH)
		if direction == _LINK_DIR_ENTITIES_TO_SWITCH:
			_LOGGER.debug("Linked entities direction for %s is entities->switch; skipping switch->entities triggers", uid)
			return
		
		entities_to_trigger = cfg.get(_LINK_CFG_ENTITIES, [])
		if not entities_to_trigger:
			_LOGGER.debug("No linked entities found for switch %s", uid)
			return
		
		now = time.monotonic()
		blocked_until = self._linked_trigger_blocked_until.get(uid, 0.0)
		if now < blocked_until:
			remaining = blocked_until - now
			_LOGGER.warning(
				"Loop protection: Skipping linked-entity trigger for %s (blocked for %.1fs)",
				uid,
				remaining,
			)
			return
		
		history = self._linked_trigger_history.setdefault(uid, deque())
		cutoff = now - _LINKED_TRIGGER_BURST_WINDOW_SECONDS
		while history and history[0] < cutoff:
			history.popleft()
		history.append(now)
		
		if len(history) > _LINKED_TRIGGER_BURST_MAX:
			self._linked_trigger_blocked_until[uid] = now + _LINKED_TRIGGER_BLOCK_SECONDS
			history.clear()
			_LOGGER.warning(
				"Loop protection: Blocking linked-entity triggers for %s for %.0fs (> %d triggers within %.0fs)",
				uid,
				_LINKED_TRIGGER_BLOCK_SECONDS,
				_LINKED_TRIGGER_BURST_MAX,
				_LINKED_TRIGGER_BURST_WINDOW_SECONDS,
			)
			return
		
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
				# Suppress the resulting state-change event so we don't echo back to the switch.
				self._linked_entity_suppress_until[entity_id] = now + _LINKED_ENTITY_SUPPRESSION_SECONDS
				
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

	def _extract_linked_entity_ids(self, raw_value: Any) -> list[str]:
		"""Normalise linked entity config value to a list of entity_ids."""
		if isinstance(raw_value, dict):
			entities = raw_value.get(_LINK_CFG_ENTITIES, [])
			if isinstance(entities, list):
				return [e for e in entities if isinstance(e, str) and e]
		return []

	def _parse_link_config(self, raw_value: Any) -> Dict[str, Any]:
		"""Parse a per-switch link config into a normalised dict."""
		entities = self._extract_linked_entity_ids(raw_value)
		mode = _LINK_MODE_MASTER
		master: Optional[str] = entities[0] if entities else None
		direction = _LINK_DIR_BOTH
		
		if isinstance(raw_value, dict):
			raw_mode = raw_value.get(_LINK_CFG_MODE)
			if isinstance(raw_mode, str) and raw_mode in _VALID_LINK_MODES:
				mode = raw_mode
			raw_master = raw_value.get(_LINK_CFG_MASTER)
			if isinstance(raw_master, str) and raw_master in entities:
				master = raw_master
			elif entities:
				master = entities[0]
			
			raw_direction = raw_value.get(_LINK_CFG_DIRECTION)
			if isinstance(raw_direction, str) and raw_direction in _VALID_LINK_DIRECTIONS:
				direction = raw_direction
		
		return {
			_LINK_CFG_ENTITIES: entities,
			_LINK_CFG_MODE: mode,
			_LINK_CFG_MASTER: master,
			_LINK_CFG_DIRECTION: direction,
		}

	def _schedule_hass_task(self, coro) -> None:
		"""Schedule a coroutine on Home Assistant without assuming hass.async_create_task exists."""
		try:
			create_task = getattr(self.hass, "async_create_task", None)
			if callable(create_task):
				create_task(coro)
				return
			self.hass.loop.create_task(coro)
		except Exception as ex:  # noqa: BLE001
			_LOGGER.debug("Failed to schedule task: %s", ex)

	def _state_is_on(self, state_obj: Any) -> Optional[bool]:
		"""Best-effort 'is on' conversion for a Home Assistant State-like object."""
		if state_obj is None:
			return None
		state = getattr(state_obj, "state", None)
		if not isinstance(state, str):
			return None
		if state in (STATE_UNKNOWN, STATE_UNAVAILABLE):
			return None
		return state == STATE_ON

	def _extract_link_params_from_state(self, state_obj: Any) -> Optional[Dict[str, Any]]:
		"""Extract supported parameter fields from a State-like object's attributes."""
		if state_obj is None:
			return None
		attrs = getattr(state_obj, "attributes", None)
		if not isinstance(attrs, dict) or not attrs:
			return None
		params: Dict[str, Any] = {}
		for key in _LINK_PARAM_KEYS:
			if key in attrs:
				params[key] = attrs[key]
		return params or None

	def _link_params_changed(self, old_state: Any, new_state: Any) -> bool:
		"""Whether any supported param attribute changed."""
		if old_state is None or new_state is None:
			return True
		old_attrs = getattr(old_state, "attributes", None)
		new_attrs = getattr(new_state, "attributes", None)
		if not isinstance(old_attrs, dict) or not isinstance(new_attrs, dict):
			return True
		for key in _LINK_PARAM_KEYS:
			if old_attrs.get(key) != new_attrs.get(key):
				return True
		return False

	async def _async_handle_linked_entity_state_change(self, entity_id: str, old_state: Any, new_state: Any) -> None:
		"""Handle a linked entity state change by updating owned switch state."""
		if not isinstance(entity_id, str) or not entity_id:
			return
		
		now = time.monotonic()
		if now < self._linked_entity_suppress_until.get(entity_id, 0.0):
			return
		
		uids = self._linked_entity_to_uids.get(entity_id)
		if not uids:
			return
		
		new_is_on = self._state_is_on(new_state)
		if new_is_on is None:
			return
		
		state_changed = True
		if old_state is not None and getattr(old_state, "state", None) == getattr(new_state, "state", None):
			state_changed = False
		
		params: Optional[Dict[str, Any]] = None
		if new_is_on:
			extracted = self._extract_link_params_from_state(new_state)
			if extracted and (state_changed or self._link_params_changed(old_state, new_state)):
				params = extracted
		
		for uid in uids:
			# Only owners can drive the upstream switch.
			if not self.is_switch_owner(uid):
				continue
			
			cfg = self._linked_config_by_uid.get(uid)
			if not isinstance(cfg, dict):
				continue
			direction = cfg.get(_LINK_CFG_DIRECTION, _LINK_DIR_BOTH)
			if direction == _LINK_DIR_SWITCH_TO_ENTITIES:
				continue
			
			entities = cfg.get(_LINK_CFG_ENTITIES, [])
			mode = cfg.get(_LINK_CFG_MODE, _LINK_MODE_MASTER)
			master = cfg.get(_LINK_CFG_MASTER)
			
			if not isinstance(entities, list) or not entities:
				continue
			
			desired: Optional[bool] = None
			params_to_send: Optional[Dict[str, Any]] = None
			
			if mode == _LINK_MODE_MASTER:
				# Only master changes drive the switch.
				if isinstance(master, str) and master and entity_id != master:
					continue
				desired = new_is_on
				params_to_send = params if desired else None
			
			elif mode in (_LINK_MODE_OR, _LINK_MODE_AND):
				# Aggregate across linked entities using the freshest known state for the changed entity.
				seen_any = False
				agg_or = False
				agg_and = True
				
				for eid in entities:
					if not isinstance(eid, str) or not eid:
						continue
					seen_any = True
					
					state_obj = new_state if eid == entity_id else getattr(getattr(self.hass, "states", None), "get", lambda _x: None)(eid)
					is_on = self._state_is_on(state_obj)
					if is_on is None:
						is_on = False
					
					agg_or = agg_or or is_on
					agg_and = agg_and and is_on
				
				if not seen_any:
					continue
				
				desired = agg_or if mode == _LINK_MODE_OR else agg_and
				# If we're sending "on", forward params from the entity that changed (best effort).
				params_to_send = params if desired else None
			
			else:
				# Unknown mode: be safe and do nothing.
				continue
			
			current = bool(self.get_switch_data(uid) and self.get_switch_data(uid).get("state", False))
			if current == bool(desired) and not params_to_send:
				continue
			
			await self.set_switch_state(uid, bool(desired), params=params_to_send)

	@callback
	def _linked_entity_state_change_event(self, event) -> None:
		"""Handle the HA state_changed event for linked entities."""
		entity_id = event.data.get("entity_id")
		old_state = event.data.get("old_state")
		new_state = event.data.get("new_state")
		self._schedule_hass_task(self._async_handle_linked_entity_state_change(entity_id, old_state, new_state))

	async def async_setup_entity_links(self) -> None:
		"""Set up entity links (called after updating config options)."""
		options = self.config_entry.options or {}
		linked_entities = options.get("linked_entities", {}) or {}
		
		self._linked_entity_to_uids = {}
		self._linked_config_by_uid = {}
		
		tracked_entity_ids: Set[str] = set()
		
		for uid, raw_cfg in linked_entities.items():
			if not isinstance(uid, str) or not uid:
				continue
			
			cfg = self._parse_link_config(raw_cfg)
			entities = cfg.get(_LINK_CFG_ENTITIES, [])
			if not entities:
				continue
			
			self._linked_config_by_uid[uid] = cfg
			
			# Only track HA entity state changes for bidirectional (owned + direction includes entities->switch) links.
			if not self.is_switch_owner(uid):
				continue
			direction = cfg.get(_LINK_CFG_DIRECTION, _LINK_DIR_BOTH)
			if direction == _LINK_DIR_SWITCH_TO_ENTITIES:
				continue
			
			for entity_id in entities:
				tracked_entity_ids.add(entity_id)
				self._linked_entity_to_uids.setdefault(entity_id, set()).add(uid)
		
		if self._linked_entity_listener_unsub:
			try:
				self._linked_entity_listener_unsub()
			except Exception as ex:  # noqa: BLE001
				_LOGGER.debug("Failed to unsubscribe previous linked-entity listener: %s", ex)
			self._linked_entity_listener_unsub = None
		
		if tracked_entity_ids:
			self._linked_entity_listener_unsub = async_track_state_change_event(
				self.hass,
				list(tracked_entity_ids),
				self._linked_entity_state_change_event,
			)
			_LOGGER.info(
				"Entity links configured for bidirectional sync: %d entities (%d switches)",
				len(tracked_entity_ids),
				len(self._linked_config_by_uid),
			)
		else:
			_LOGGER.info("No linked entities configured for bidirectional sync")

	async def async_add_imported_entities(self) -> None:
		"""Dynamically add entities for imported switches without reloading the entry."""
		if not self.async_add_switch_entities and not self.async_add_sensor_entities:
			_LOGGER.warning("Cannot add imported entities dynamically; add_entities callbacks not set")
			return
		
		options = self.config_entry.options or {}
		imported_switches = options.get("imported_switches", {})
		if not imported_switches:
			_LOGGER.info("No imported switches to add dynamically")
			return
		
		from .switch import VomeSyncSwitch  # local import to avoid circular
		from .sensor import VomeSyncSensor
		
		new_switches = []
		new_sensors = []
		for uid, info in imported_switches.items():
			if uid in self.entity_names:
				continue
			
			name = info.get("name", f"Switch {uid[:8]}")
			is_owner = info.get("is_owner", False)
			has_access_key = bool(str(info.get("access_key", "") or "").strip())
			
			# Ensure websocket connection if possible
			try:
				await self._ensure_websocket_connection(uid)
			except Exception as ex:  # noqa: BLE001
				_LOGGER.debug("WebSocket ensure failed for %s (non-fatal during add): %s", uid, ex)
			
			if is_owner or has_access_key:
				new_switches.append(VomeSyncSwitch(self, uid, name, is_owner, self.config_entry))
			else:
				new_sensors.append(VomeSyncSensor(self, uid, name))
			self.entity_names[uid] = name
		
		if new_switches and self.async_add_switch_entities:
			self.async_add_switch_entities(new_switches)
			_LOGGER.info("Dynamically added %d owned switch entities", len(new_switches))
		elif new_switches:
			_LOGGER.warning("Cannot add owned switch entities dynamically; async_add_switch_entities not set")
		
		if new_sensors and self.async_add_sensor_entities:
			self.async_add_sensor_entities(new_sensors)
			_LOGGER.info("Dynamically added %d subscription sensor entities", len(new_sensors))
		elif new_sensors:
			_LOGGER.warning("Cannot add subscription sensors dynamically; async_add_sensor_entities not set")

	@property
	def server_url(self) -> str:
		"""Get server URL."""
		return self.config_entry.data[CONF_SERVER_URL]
