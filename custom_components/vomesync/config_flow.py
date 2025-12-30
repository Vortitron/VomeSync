"""Config flow for VomeSync integration."""
import asyncio
import inspect
import logging
from typing import Any, Dict, Optional

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er

from .api_client import VomeSyncAPIClient, VomeSyncAPIError
from .const import (
	DOMAIN,
	CONF_PERSONAL_KEY,
	CONF_SERVER_URL,
	CONF_WEBSOCKET_URL,
	CONF_AUTH_MODE,
	CONF_CRYPTO_SEED,
	AUTH_MODE_CRYPTO,
	DEFAULT_SERVER_URL,
	DEFAULT_WEBSOCKET_URL,
	CONF_SWITCH_UID,
	CONF_SWITCH_NAME,
	CONF_SWITCH_DESCRIPTION,
	CONF_SWITCH_LOCATION,
	CONF_SWITCH_CATEGORY,
	CONF_SWITCH_PUBLICIZE,
	SWITCH_CATEGORIES,
)

from .crypto import generate_master_seed_b64url, owner_pubkey_b64url

_LOGGER = logging.getLogger(__name__)


class VomeSyncConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
	"""Handle a config flow for VomeSync."""

	VERSION = 1

	def __init__(self) -> None:
		"""Initialize the config flow."""
		self._personal_key: Optional[str] = None
		self._auth_mode: str = AUTH_MODE_CRYPTO
		self._crypto_seed: Optional[str] = None
		self._server_url: str = DEFAULT_SERVER_URL
		self._websocket_url: str = DEFAULT_WEBSOCKET_URL

	@staticmethod
	def _derive_websocket_url(server_url: str) -> str:
		"""Derive a WebSocket URL from the HTTP(S) server URL."""
		base = (server_url or "").strip().rstrip("/")
		if not base:
			return DEFAULT_WEBSOCKET_URL
		if base.startswith("https://"):
			ws_base = "wss://" + base[len("https://"):]
		elif base.startswith("http://"):
			ws_base = "ws://" + base[len("http://"):]
		elif base.startswith("wss://") or base.startswith("ws://"):
			ws_base = base
		else:
			# Fallback: assume http scheme
			ws_base = f"ws://{base}"
		return f"{ws_base}/ws"

	async def async_step_user(
		self, user_input: Optional[Dict[str, Any]] = None
	) -> FlowResult:
		"""Handle the initial step."""
		errors = {}

		if user_input is not None:
			self._server_url = user_input.get(CONF_SERVER_URL, DEFAULT_SERVER_URL)
			if user_input.get(CONF_WEBSOCKET_URL):
				self._websocket_url = user_input[CONF_WEBSOCKET_URL]
			else:
				self._websocket_url = self._derive_websocket_url(self._server_url)
			
			# Keypair mode is the only supported mode (the integration never launched with personal keys).
			self._auth_mode = AUTH_MODE_CRYPTO
			self._crypto_seed = user_input.get(CONF_CRYPTO_SEED) or generate_master_seed_b64url()
			self._personal_key = ""
			return await self._create_entry()
		else:
			# First load: keep websocket URL in sync with the server URL
			self._websocket_url = self._derive_websocket_url(self._server_url)

		data_schema = vol.Schema({
			vol.Optional(CONF_SERVER_URL, default=self._server_url): str,
			vol.Optional(CONF_CRYPTO_SEED): str,
		})

		return self.async_show_form(
			step_id="user",
			data_schema=data_schema,
			errors=errors,
			description_placeholders={
				"warning": "⚠️ Public mode is NOT private - anyone with UID can view/toggle switches. Use only for non-sensitive devices.",
				"keypair_hint": "Leave the signing key empty to generate one. Keep it safe if you want to migrate servers/instances later.",
				"websocket_info": "WebSocket URL will be automatically set to: " + self._derive_websocket_url(self._server_url)
			}
		)

	async def async_step_generate_key(
		self, user_input: Optional[Dict[str, Any]] = None
	) -> FlowResult:
		"""Deprecated: personal-key mode never launched and is no longer supported."""
		return self.async_abort(reason="not_supported")

	async def _create_entry(self) -> FlowResult:
		"""Create the config entry."""
		unique = None
		title = "VomeSync"
		if not self._crypto_seed:
			raise ValueError("crypto_seed missing for keypair auth mode")
		owner_pub = owner_pubkey_b64url(self._crypto_seed)
		unique = owner_pub
		title = f"VomeSync (keypair {owner_pub[:8]}...)"

		# Check for existing entries (skip if context is immutable in tests)
		try:
			if unique:
				await self.async_set_unique_id(unique)
				self._abort_if_unique_id_configured()
		except TypeError:
			_LOGGER.debug("Skipping unique_id setup (context not mutable in test environment)")

		# Ensure websocket URL stored matches server_url if user left it blank
		websocket_url = self._websocket_url or self._derive_websocket_url(self._server_url)

		return self.async_create_entry(
			title=title,
			data={
				CONF_PERSONAL_KEY: self._personal_key or "",
				CONF_SERVER_URL: self._server_url,
				CONF_WEBSOCKET_URL: websocket_url,
				CONF_AUTH_MODE: self._auth_mode,
				CONF_CRYPTO_SEED: self._crypto_seed or "",
			}
		)

	@staticmethod
	@callback
	def async_get_options_flow(config_entry: config_entries.ConfigEntry) -> "VomeSyncOptionsFlow":
		"""Create the options flow."""
		return VomeSyncOptionsFlow(config_entry)


class VomeSyncOptionsFlow(config_entries.OptionsFlow):
	"""Handle options flow for VomeSync."""

	def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
		"""Initialize options flow."""
		super().__init__()
		self._config_entry = config_entry
		self._step_data: Dict[str, Any] = {}

	async def _async_update_entry_options(self, options: Dict[str, Any]) -> None:
		"""Update config entry options, awaiting only if HA returns an awaitable."""
		result = self.hass.config_entries.async_update_entry(self._config_entry, options=options)
		if inspect.isawaitable(result):
			await result

	def _get_link_direction_labels(self) -> Dict[str, str]:
		"""Get available link directions for the UI."""
		return {
			"both": "Both (switch ↔ entities)",
			"switch_to_entities": "Switch → entities",
			"entities_to_switch": "Entities → switch",
		}

	def _normalise_link_direction(self, raw: Any, default: str = "both") -> str:
		"""Normalise a raw direction value into a supported internal value."""
		directions = self._get_link_direction_labels()
		if isinstance(raw, str) and raw in directions:
			return raw
		return default

	async def async_step_init(
		self, user_input: Optional[Dict[str, Any]] = None
	) -> FlowResult:
		"""Manage the options."""
		# Keep the highest-frequency actions near the top; move the rest under "More…"
		menu_options = ["create_switch", "subscribe_switch", "manage_switches", "more"]
		return self.async_show_menu(
			step_id="init",
			menu_options=menu_options
		)

	async def async_step_more(
		self, user_input: Optional[Dict[str, Any]] = None
	) -> FlowResult:
		"""Secondary menu to reduce clutter."""
		menu_options = [
			"import_switches",
			"reannounce_owned_switches",
			"cleanup_orphaned_devices",
			"edit_connection",
		]
		return self.async_show_menu(step_id="more", menu_options=menu_options)

	async def async_step_import_switches(
		self, user_input: Optional[Dict[str, Any]] = None
	) -> FlowResult:
		"""Import switches from API."""
		if user_input is not None:
			# Get selected switches
			selected_uids = user_input.get("switches", [])
			
			if not selected_uids:
				return self.async_abort(reason="no_switches_selected")
			
			# Get current options
			options = dict(self._config_entry.options or {})
			imported_switches = options.get("imported_switches", {})
			
			# Get coordinator to fetch switch details
			coordinator = self.hass.data[DOMAIN][self._config_entry.entry_id]
			
			# Add selected switches to imported list
			for uid in selected_uids:
				if uid not in imported_switches:
					# Find switch data from coordinator
					switch_data = coordinator.switches.get(uid) or coordinator.subscriptions.get(uid)
					if switch_data:
						imported_switches[uid] = {
							"name": switch_data.get("name", switch_data.get("description", f"Switch {uid[:8]}")),
							"is_owner": switch_data.get("is_owner", uid in coordinator.switches),
							"cached_data": switch_data,
							**({"crypto_index": switch_data.get("index")} if isinstance(switch_data.get("index"), int) else {}),
						}
			
			# Save to options
			options["imported_switches"] = imported_switches
			
			self.hass.config_entries.async_update_entry(
				self._config_entry,
				options=options
			)
			
			# Dynamically add imported entities without full reload
			try:
				coordinator = self.hass.data[DOMAIN][self._config_entry.entry_id]
				await coordinator.async_add_imported_entities()
			except Exception as ex:  # noqa: BLE001
				_LOGGER.error("Failed to add imported entities dynamically: %s", ex)
			
			return self.async_create_entry(title="", data=options)
		
		# Fetch all switches from API
		try:
			coordinator = self.hass.data[DOMAIN][self._config_entry.entry_id]
			
			# Trigger a refresh to get latest data (if method exists and is awaitable)
			if hasattr(coordinator, "async_request_refresh"):
				try:
					result = coordinator.async_request_refresh()
					if asyncio.iscoroutine(result):
						await result
				except Exception as ex:  # noqa: BLE001
					_LOGGER.debug("Refresh skipped during import (non-fatal): %s", ex)
			
			# Get currently imported switches
			options = self._config_entry.options or {}
			imported_switches = options.get("imported_switches", {})
			already_imported = set(imported_switches.keys())
			
			# Build list of available switches
			available_switches = {}
			
			# Add owned switches
			for uid, switch_data in coordinator.switches.items():
				name = switch_data.get("name", switch_data.get("description", f"Switch {uid[:8]}"))
				status = " (already imported)" if uid in already_imported else ""
				available_switches[uid] = f"[OWNED] {name}{status}"
			
			# Add subscriptions
			for uid, sub_data in coordinator.subscriptions.items():
				if uid not in available_switches:  # Don't duplicate
					name = sub_data.get("name", sub_data.get("description", f"Subscription {uid[:8]}"))
					status = " (already imported)" if uid in already_imported else ""
					available_switches[uid] = f"[SUBSCRIBED] {name}{status}"
			
			if not available_switches:
				return self.async_abort(reason="no_switches")
			
			return self.async_show_form(
				step_id="import_switches",
				data_schema=vol.Schema({
					vol.Required("switches"): cv.multi_select(available_switches)
				}),
				description_placeholders={
					"info": f"Select switches to import to this Home Assistant installation.\n\n"
					f"**Available:** {len(available_switches)} switches\n"
					f"**Already imported:** {len(already_imported)} switches\n\n"
					f"You can import the same switch to multiple HA installations."
				}
			)
		
		except Exception as ex:
			_LOGGER.error("Failed to fetch switches for import: %s", ex)
			return self.async_abort(reason="api_error")
	
	async def async_step_create_switch(
		self, user_input: Optional[Dict[str, Any]] = None
	) -> FlowResult:
		"""Create a new switch."""
		errors = {}

		if user_input is not None:
			try:
				# Get coordinator
				coordinator = self.hass.data[DOMAIN][self._config_entry.entry_id]
				
				# Create switch via coordinator (handles API call + dynamic entity addition)
				switch_name = user_input[CONF_SWITCH_NAME]
				uid = await coordinator.create_switch(
					name=switch_name,
					description=user_input.get(CONF_SWITCH_DESCRIPTION, ""),
					location=user_input.get(CONF_SWITCH_LOCATION, ""),
					category=user_input.get(CONF_SWITCH_CATEGORY, "Other"),
					publicize=user_input.get(CONF_SWITCH_PUBLICIZE, False)
				)
				
				if uid:
					# IMPORTANT: return current options so the options flow doesn't overwrite them with {}
					# Coordinator already updated options (imported cache) and added the entity dynamically.
					return self.async_create_entry(title="", data=dict(self._config_entry.options or {}))
				else:
					errors["base"] = "create_failed"
				
			except VomeSyncAPIError as ex:
				_LOGGER.error(
					"Failed to create switch (server=%s, websocket=%s): %s",
					self._config_entry.data[CONF_SERVER_URL],
					self._config_entry.data[CONF_WEBSOCKET_URL],
					ex
				)
				errors["base"] = "create_failed"
			except Exception as ex:
				_LOGGER.error("Failed to create switch (unexpected): %s", ex)
				errors["base"] = "create_failed"

		data_schema = vol.Schema({
			vol.Required(CONF_SWITCH_NAME): str,
			vol.Optional(CONF_SWITCH_DESCRIPTION, default=""): str,
			vol.Optional(CONF_SWITCH_LOCATION, default=""): str,
			vol.Optional(CONF_SWITCH_CATEGORY, default="Other"): vol.In(SWITCH_CATEGORIES),
			vol.Optional(CONF_SWITCH_PUBLICIZE, default=False): bool,
		})

		return self.async_show_form(
			step_id="create_switch",
			data_schema=data_schema,
			errors=errors,
			description_placeholders={
				"warning": "⚠️ If you enable 'publicize', this switch will be listed publicly and anyone can toggle it!"
			}
		)

	async def async_step_subscribe_switch(
		self, user_input: Optional[Dict[str, Any]] = None
	) -> FlowResult:
		"""Subscribe to an existing switch."""
		errors = {}

		if user_input is not None:
			try:
				uid = user_input[CONF_SWITCH_UID]
				
				# Get coordinator
				coordinator = self.hass.data[DOMAIN][self._config_entry.entry_id]
				
				# Subscribe via coordinator (handles API check + dynamic entity addition)
				success = await coordinator.subscribe_to_switch(uid)
				
				if success:
					# IMPORTANT: return current options so the options flow doesn't overwrite them with {}
					# Coordinator already updated options (imported cache) and added the entity dynamically.
					return self.async_create_entry(title="", data=dict(self._config_entry.options or {}))
				else:
					errors[CONF_SWITCH_UID] = "switch_not_found"
					
			except ValueError:
				errors[CONF_SWITCH_UID] = "invalid_uid"
			except Exception as ex:
				_LOGGER.error("Failed to subscribe to switch: %s", ex)
				errors["base"] = "subscribe_failed"

		data_schema = vol.Schema({
			vol.Required(CONF_SWITCH_UID): str,
		})

		return self.async_show_form(
			step_id="subscribe_switch",
			data_schema=data_schema,
			errors=errors,
			description_placeholders={
				"uid_info": "Enter the UID of the switch you want to subscribe to. You can find public switches at sync.vome.io"
			}
		)

	async def async_step_manage_switches(
		self, user_input: Optional[Dict[str, Any]] = None
	) -> FlowResult:
		"""Manage existing switches."""
		# Get imported switches from options
		options = self._config_entry.options or {}
		imported_switches = options.get("imported_switches", {})
		
		if not imported_switches:
			return self.async_abort(reason="no_switches")
		
		# Build list of imported switches to manage
		switch_list = {}
		for uid, switch_info in imported_switches.items():
			name = switch_info.get("name", f"Switch {uid[:8]}")
			is_owner = switch_info.get("is_owner", False)
			
			# Add ownership icon
			icon = "🔧" if is_owner else "👁️"
			
			# Add last 6 chars of UID for disambiguation
			uid_hint = uid[-6:]
			switch_list[uid] = f"{icon} {name} (…{uid_hint})"
		
		if user_input is not None:
			# Store selected switch UID and show options
			selected_uid = user_input["switch"]
			
			# Get name and ownership from imported switches
			switch_info = imported_switches.get(selected_uid, {})
			selected_name = switch_info.get("name", selected_uid[:8])
			is_owner = switch_info.get("is_owner", False)
			
			self._step_data["selected_uid"] = selected_uid
			self._step_data["selected_name"] = selected_name
			self._step_data["is_owner"] = is_owner
			
			# Determine available actions
			actions = ["view_switch", "link_entities"]
			
			# Only owners can edit or delete
			if is_owner:
				actions.append("edit_switch")
				actions.append("delete_switch")
			
			# Everyone can remove from this installation
			actions.append("remove_from_installation")
			
			# Best-effort entity_id lookup for UI context
			entity_id = None
			try:
				entity_reg = er.async_get(self.hass)
				entity_id = entity_reg.async_get_entity_id("switch", DOMAIN, f"vomesync_{selected_uid}")
				if not entity_id:
					for entity in entity_reg.entities.values():
						if entity.config_entry_id == self._config_entry.entry_id and entity.unique_id == f"vomesync_{selected_uid}":
							entity_id = entity.entity_id
							break
			except Exception as ex:  # noqa: BLE001
				_LOGGER.debug("Failed to resolve entity_id for %s: %s", selected_uid, ex)
			
			uid_hint = selected_uid[-6:]
			return self.async_show_menu(
				step_id="manage_switch_action",
				menu_options=actions,
				description_placeholders={
					"name": selected_name,
					"uid_hint": uid_hint,
					"entity_id": entity_id or "Not created yet",
				},
			)
		
		return self.async_show_form(
			step_id="manage_switches",
			data_schema=vol.Schema({
				vol.Required("switch"): vol.In(switch_list)
			})
		)

	async def async_step_manage_switch_action(
		self, user_input: Optional[Dict[str, Any]] = None
	) -> FlowResult:
		"""Handle switch action selection."""
		# This is just a menu, actual handling is in the specific steps
		pass

	async def async_step_connect_website(
		self, user_input: Optional[Dict[str, Any]] = None
	) -> FlowResult:
		"""Create a one-time session token and show a login link for the website."""
		errors: Dict[str, str] = {}

		if user_input is not None:
			return self.async_create_entry(title="", data={})

		api_client = VomeSyncAPIClient(
			self._config_entry.data[CONF_SERVER_URL],
			self._config_entry.data[CONF_PERSONAL_KEY]
		)

		login_url = ""
		token = ""

		try:
			token_data = await api_client.create_session_token()
			token = token_data.get("token", "")
			login_url = f"https://sync.vome.io/login?token={token}"
		except Exception as ex:
			_LOGGER.error("Failed to create session token: %s", ex)
			errors["base"] = "connect_failed"
		finally:
			await api_client.close()

		return self.async_show_form(
			step_id="connect_website",
			data_schema=vol.Schema({}),
			errors=errors,
			description_placeholders={
				"login_url": login_url,
				"session_token": token
			}
		)

	async def async_step_manage_api_keys(
		self, user_input: Optional[Dict[str, Any]] = None
	) -> FlowResult:
		"""Manage API keys for webhook authentication."""
		errors: Dict[str, str] = {}

		api_client = VomeSyncAPIClient(
			self._config_entry.data[CONF_SERVER_URL],
			self._config_entry.data[CONF_PERSONAL_KEY]
		)

		api_keys = []
		try:
			api_keys = await api_client.get_api_keys()
		except Exception as ex:
			_LOGGER.error("Failed to fetch API keys: %s", ex)
			errors["base"] = "fetch_failed"
		finally:
			await api_client.close()

		if user_input is not None:
			if "create_key" in user_input:
				return await self.async_step_create_api_key()
			elif "delete_key" in user_input:
				self._step_data["delete_key"] = user_input["delete_key"]
				return await self.async_step_delete_api_key()
			else:
				return self.async_create_entry(title="", data={})

		# Build the schema
		schema_dict = {}
		if api_keys:
			key_options = {k["key"]: f"{k.get('label', 'Unnamed')} ({k['key'][:8]}...)" for k in api_keys}
			schema_dict[vol.Optional("delete_key")] = vol.In(key_options)
		schema_dict[vol.Optional("create_key")] = bool

		return self.async_show_form(
			step_id="manage_api_keys",
			data_schema=vol.Schema(schema_dict),
			errors=errors,
			description_placeholders={
				"api_key_count": str(len(api_keys))
			}
		)

	async def async_step_create_api_key(
		self, user_input: Optional[Dict[str, Any]] = None
	) -> FlowResult:
		"""Create a new API key."""
		errors: Dict[str, str] = {}

		if user_input is not None:
			api_client = VomeSyncAPIClient(
				self._config_entry.data[CONF_SERVER_URL],
				self._config_entry.data[CONF_PERSONAL_KEY]
			)

			try:
				new_key = await api_client.create_api_key(user_input.get("label", ""))
				self._step_data["new_api_key"] = new_key
				return self.async_show_form(
					step_id="create_api_key_success",
					data_schema=vol.Schema({
						vol.Required("api_key", default=new_key): str
					}),
					description_placeholders={
						"info": "Save this API key securely. It won't be shown again."
					}
				)
			except Exception as ex:
				_LOGGER.error("Failed to create API key: %s", ex)
				errors["base"] = "create_failed"
			finally:
				await api_client.close()

		return self.async_show_form(
			step_id="create_api_key",
			data_schema=vol.Schema({
				vol.Optional("label", default=""): str
			}),
			errors=errors
		)

	async def async_step_create_api_key_success(
		self, user_input: Optional[Dict[str, Any]] = None
	) -> FlowResult:
		"""Show the newly created API key."""
		if user_input is not None:
			return await self.async_step_manage_api_keys()

		return self.async_show_form(
			step_id="create_api_key_success",
			data_schema=vol.Schema({
				vol.Required("api_key", default=self._step_data.get("new_api_key", "")): str
			}),
			description_placeholders={
				"info": "Save this API key securely. It won't be shown again."
			}
		)

	async def async_step_delete_api_key(
		self, user_input: Optional[Dict[str, Any]] = None
	) -> FlowResult:
		"""Delete an API key."""
		errors: Dict[str, str] = {}

		if user_input is not None and user_input.get("confirm"):
			api_client = VomeSyncAPIClient(
				self._config_entry.data[CONF_SERVER_URL],
				self._config_entry.data[CONF_PERSONAL_KEY]
			)

			try:
				await api_client.delete_api_key(self._step_data["delete_key"])
				return await self.async_step_manage_api_keys()
			except Exception as ex:
				_LOGGER.error("Failed to delete API key: %s", ex)
				errors["base"] = "delete_failed"
			finally:
				await api_client.close()

		return self.async_show_form(
			step_id="delete_api_key",
			data_schema=vol.Schema({
				vol.Required("confirm", default=False): bool
			}),
			errors=errors,
			description_placeholders={
				"api_key": self._step_data.get("delete_key", "")[:8] + "..."
			}
		)

	async def async_step_view_switch(
		self, user_input: Optional[Dict[str, Any]] = None
	) -> FlowResult:
		"""View switch details."""
		selected_uid = self._step_data.get("selected_uid")
		selected_name = self._step_data.get("selected_name", selected_uid)
		is_owner = self._step_data.get("is_owner", False)
		
		if user_input is not None:
			return self.async_create_entry(title="", data={})
		
		# Get coordinator
		coordinator = self.hass.data[DOMAIN][self._config_entry.entry_id]
		
		# Get switch config from coordinator
		if is_owner:
			switch_config = coordinator.switches.get(selected_uid, {})
		else:
			switch_config = coordinator.subscriptions.get(selected_uid, {})
		
		# Find entity_id for this switch
		entity_reg = er.async_get(self.hass)
		entity_id = None
		device_id = None
		
		for entity in entity_reg.entities.values():
			if entity.config_entry_id == self._config_entry.entry_id:
				# Check if this entity corresponds to our switch
				if entity.unique_id and selected_uid in entity.unique_id:
					entity_id = entity.entity_id
					device_id = entity.device_id
					break
		
		# Build URLs
		ws_url = self._config_entry.data.get(CONF_WEBSOCKET_URL, "")
		websocket_full = f"{ws_url}?uid={selected_uid}" if ws_url else "Not configured"
		
		# Build schema fields (treated as read-only; we ignore user_input)
		# Defaults encourage HA to show copy icons.
		schema_fields = {
			vol.Required("uid", default=selected_uid): str,
			vol.Required("websocket_url", default=websocket_full): str,
		}
		
		# Add entity_id if found
		if entity_id:
			schema_fields[vol.Required("entity_id", default=entity_id)] = str
		
		if is_owner:
			server_url = self._config_entry.data.get(CONF_SERVER_URL, "")
			personal_key = self._config_entry.data.get(CONF_PERSONAL_KEY, "")
			webhook_url = f"{server_url}/api/toggle/{selected_uid}?personalKey={personal_key}"
			curl_cmd = f'curl -X POST "{webhook_url}"'
			
			schema_fields[vol.Required("webhook_url", default=webhook_url)] = str
			schema_fields[vol.Required("curl_command", default=curl_cmd)] = str
			
			device_settings_note = ""
			if device_id:
				device_settings_note = f"\n\n**Device Settings:** Navigate to Settings → Devices & Services → {self._config_entry.title} → {selected_name}"
			elif entity_id:
				device_settings_note = f"\n\n**Entity:** Search for `{entity_id}` in Settings → Devices & Services"
			
			description = f"""**{selected_name}** (Owner)

**Description:** {switch_config.get('description', 'None')}
**Location:** {switch_config.get('location', 'None')}
**Category:** {switch_config.get('category', 'Other')}
**Publicise:** {"Yes" if switch_config.get('publicize', False) else "No"}{device_settings_note}

**Webhook Usage:**
• Keep this webhook private. Anyone with it can toggle the switch.
• Browser: Visit the URL to toggle
• curl: Copy the curl command below
• Automation: Use as HTTP POST action
• Docs: https://vome.io/webhooks"""
		else:
			device_settings_note = ""
			if device_id:
				device_settings_note = f"\n\n**Device Settings:** Navigate to Settings → Devices & Services → {self._config_entry.title} → {selected_name}"
			elif entity_id:
				device_settings_note = f"\n\n**Entity:** Search for `{entity_id}` in Settings → Devices & Services"
			
			description = f"""**{selected_name}** (Subscribed - read-only){device_settings_note}"""
		
		return self.async_show_form(
			step_id="view_switch",
			data_schema=vol.Schema(schema_fields),
			description_placeholders={"info": description}
		)

	async def async_step_edit_switch(
		self, user_input: Optional[Dict[str, Any]] = None
	) -> FlowResult:
		"""Edit switch settings."""
		selected_uid = self._step_data.get("selected_uid")
		selected_name = self._step_data.get("selected_name", selected_uid)
		is_owner = self._step_data.get("is_owner", False)
		
		if not is_owner:
			return self.async_abort(reason="not_owner")
		
		# Get coordinator
		coordinator = self.hass.data[DOMAIN][self._config_entry.entry_id]
		
		# Get switch config
		switch_config = coordinator.switches.get(selected_uid, {})
		
		if not selected_uid or not switch_config:
			return self.async_abort(reason="no_switches")
		
		errors = {}
		
		if user_input is not None:
			# Update coordinator state
			coordinator.switches[selected_uid].update({
				"description": user_input.get("description", ""),
				"location": user_input.get("location", ""),
				"category": user_input.get("category", "Other"),
				"publicize": user_input.get("publicize", False)
			})
			
			# Also save to options for persistence
			options = dict(self._config_entry.options or {})
			switches = options.setdefault("switches", {})
			if selected_name in switches:
				switches[selected_name].update({
					"description": user_input.get("description", ""),
					"location": user_input.get("location", ""),
					"category": user_input.get("category", "Other"),
					"publicize": user_input.get("publicize", False)
				})
			else:
				switches[selected_name] = {
					"uid": selected_uid,
					"description": user_input.get("description", ""),
					"location": user_input.get("location", ""),
					"category": user_input.get("category", "Other"),
					"publicize": user_input.get("publicize", False),
					"is_owner": True
				}
			
			return self.async_create_entry(title="", data=options)
		
		data_schema = vol.Schema({
			vol.Optional("description", default=switch_config.get("description", "")): str,
			vol.Optional("location", default=switch_config.get("location", "")): str,
			vol.Optional("category", default=switch_config.get("category", "Other")): vol.In(SWITCH_CATEGORIES),
			vol.Optional("publicize", default=switch_config.get("publicize", False)): bool,
		})
		
		return self.async_show_form(
			step_id="edit_switch",
			data_schema=data_schema,
			errors=errors
		)

	async def async_step_link_entities(
		self, user_input: Optional[Dict[str, Any]] = None
	) -> FlowResult:
		"""Link local entities to sync with this switch."""
		selected_uid = self._step_data.get("selected_uid")
		selected_name = self._step_data.get("selected_name", selected_uid)
		
		if user_input is not None:
			# Update links for this switch
			selected_entities = user_input.get("entities", [])
			if not isinstance(selected_entities, list):
				selected_entities = list(selected_entities)
			direction = self._normalise_link_direction(user_input.get("direction"), default="both")
			
			_LOGGER.info("Linking entities for switch %s: %s", selected_uid, selected_entities)
			
			# No entities selected => remove linkage
			if not selected_entities:
				options = dict(self._config_entry.options or {})
				linked_entities = dict(options.get("linked_entities", {}) or {})
				if selected_uid in linked_entities:
					del linked_entities[selected_uid]
				options["linked_entities"] = linked_entities
				await self._async_update_entry_options(options)
				
				# Rebuild listeners (best effort)
				try:
					coordinator = self.hass.data[DOMAIN][self._config_entry.entry_id]
					await coordinator.async_setup_entity_links()
				except Exception as err:  # noqa: BLE001
					_LOGGER.error("Failed to set up entity links: %s", err)
				
				return self.async_create_entry(title="", data=options)
			
			# One entity => no extra questions; just store as master mode
			if len(selected_entities) == 1:
				options = dict(self._config_entry.options or {})
				linked_entities = dict(options.get("linked_entities", {}) or {})
				linked_entities[selected_uid] = {
					"entities": list(selected_entities),
					"mode": "master",
					"master": selected_entities[0],
					"direction": direction,
				}
				options["linked_entities"] = linked_entities
				await self._async_update_entry_options(options)
				
				try:
					coordinator = self.hass.data[DOMAIN][self._config_entry.entry_id]
					await coordinator.async_setup_entity_links()
				except Exception as err:  # noqa: BLE001
					_LOGGER.error("Failed to set up entity links: %s", err)
				
				return self.async_create_entry(title="", data=options)
			
			# Multiple entities:
			# - If direction is switch->entities only, no behaviour is needed (we just toggle them all).
			# - Otherwise, ask how linked entities should drive the switch.
			if direction == "switch_to_entities":
				options = dict(self._config_entry.options or {})
				linked_entities = dict(options.get("linked_entities", {}) or {})
				linked_entities[selected_uid] = {
					"entities": list(selected_entities),
					"mode": "master",
					"master": selected_entities[0],
					"direction": direction,
				}
				options["linked_entities"] = linked_entities
				await self._async_update_entry_options(options)
				
				try:
					coordinator = self.hass.data[DOMAIN][self._config_entry.entry_id]
					await coordinator.async_setup_entity_links()
				except Exception as err:  # noqa: BLE001
					_LOGGER.error("Failed to set up entity links: %s", err)
				
				return self.async_create_entry(title="", data=options)
			
			# Ask for behaviour (master / OR / AND)
			self._step_data["pending_link_entities"] = list(selected_entities)
			self._step_data["pending_link_direction"] = direction
			return await self.async_step_link_entities_behaviour()
		
		# Get currently linked entities for this switch
		options = self._config_entry.options or {}
		linked_entities = options.get("linked_entities", {}) or {}
		current_cfg = linked_entities.get(selected_uid)
		current_links: list[str]
		current_direction = "both"
		if isinstance(current_cfg, dict):
			raw = current_cfg.get("entities", [])
			current_links = raw if isinstance(raw, list) else []
			current_direction = self._normalise_link_direction(current_cfg.get("direction"), default="both")
			if "direction" not in current_cfg and current_cfg.get("read_only") is True:
				# Backwards compatibility for short-lived "read_only".
				current_direction = "switch_to_entities"
		elif isinstance(current_cfg, list):
			current_links = current_cfg
			# Backwards compatibility for legacy list format (switch -> entities only).
			current_direction = "switch_to_entities"
		else:
			current_links = []
		
		# Get all switchable entities from HA
		entity_reg = er.async_get(self.hass)
		
		# Get all entities that support turn_on/turn_off
		switchable_domains = ["switch", "light", "fan", "input_boolean", "automation", "script"]
		# Sort linked entities first, then by domain/name for a predictable UI.
		candidates: list[tuple[int, str, str, str, str]] = []
		
		for entity in entity_reg.entities.values():
			if entity.domain in switchable_domains:
				# Skip our own VomeSync entities
				if entity.config_entry_id == self._config_entry.entry_id:
					continue
				
				# Get friendly name
				state = self.hass.states.get(entity.entity_id)
				name = (state.attributes.get("friendly_name") if state else None) or entity.original_name or entity.entity_id
				label = f"{name} ({entity.entity_id})"
				linked_rank = 0 if entity.entity_id in current_links else 1
				candidates.append((linked_rank, entity.domain, str(name).casefold(), entity.entity_id, label))
		
		if not candidates:
			return self.async_abort(reason="no_linkable_entities")
		
		available_entities: Dict[str, str] = {}
		for _linked_rank, _domain, _name_key, entity_id, label in sorted(candidates):
			available_entities[entity_id] = label
		
		_LOGGER.debug("Current links for %s: %s", selected_uid, current_links)
		_LOGGER.debug("Available entities: %s", list(available_entities.keys())[:5])
		
		# Keep available entity labels for the next step (behaviour/master selection)
		self._step_data["link_available_entities"] = available_entities
		self._step_data["link_direction_labels"] = self._get_link_direction_labels()
		
		direction_labels = self._get_link_direction_labels()
		return self.async_show_form(
			step_id="link_entities",
			data_schema=vol.Schema({
				vol.Optional("entities", default=current_links): cv.multi_select(available_entities),
				vol.Optional("direction", default=current_direction): vol.In(direction_labels),
			}),
			description_placeholders={
				"switch_name": selected_name,
				"info": (
					f"Link one or more entities to **{selected_name}**.\n\n"
					f"Currently linked: {len(current_links)} entities"
				),
			}
		)

	async def async_step_link_entities_behaviour(
		self, user_input: Optional[Dict[str, Any]] = None
	) -> FlowResult:
		"""Choose how multiple linked entities should drive the switch."""
		selected_uid = self._step_data.get("selected_uid")
		selected_name = self._step_data.get("selected_name", selected_uid)
		selected_entities = self._step_data.get("pending_link_entities") or []
		direction_default = self._normalise_link_direction(self._step_data.get("pending_link_direction"), default="both")
		available_entities = self._step_data.get("link_available_entities") or {}
		
		if not selected_uid or not isinstance(selected_entities, list) or len(selected_entities) <= 1:
			return self.async_abort(reason="no_linkable_entities")
		
		# Default: master=first entity
		master_default = selected_entities[0]
		
		# If existing config already has a mode/master, prefill
		options = self._config_entry.options or {}
		linked_entities = options.get("linked_entities", {}) or {}
		existing = linked_entities.get(selected_uid)
		mode_default = "master"
		if isinstance(existing, dict):
			mode_default = existing.get("mode") if isinstance(existing.get("mode"), str) else mode_default
			existing_master = existing.get("master")
			if isinstance(existing_master, str) and existing_master in selected_entities:
				master_default = existing_master
			direction_default = self._normalise_link_direction(existing.get("direction"), default=direction_default)
			if "direction" not in existing and existing.get("read_only") is True:
				direction_default = "switch_to_entities"
		
		if user_input is not None:
			mode = user_input.get("mode") if isinstance(user_input.get("mode"), str) else "master"
			master = user_input.get("master") if isinstance(user_input.get("master"), str) else master_default
			if master not in selected_entities:
				master = master_default
			direction = self._normalise_link_direction(user_input.get("direction"), default=direction_default)
			
			# Persist the config
			new_options = dict(self._config_entry.options or {})
			new_linked = dict(new_options.get("linked_entities", {}) or {})
			new_linked[selected_uid] = {
				"entities": list(selected_entities),
				"mode": mode,
				"master": master,
				"direction": direction,
			}
			new_options["linked_entities"] = new_linked
			await self._async_update_entry_options(new_options)
			
			# Trigger coordinator to (re)configure listeners
			try:
				coordinator = self.hass.data[DOMAIN][self._config_entry.entry_id]
				await coordinator.async_setup_entity_links()
			except Exception as err:  # noqa: BLE001
				_LOGGER.error("Failed to set up entity links: %s", err)
			
			# Clean up temporary step state
			self._step_data.pop("pending_link_entities", None)
			self._step_data.pop("pending_link_direction", None)
			return self.async_create_entry(title="", data=new_options)
		
		master_labels = {
			eid: (available_entities.get(eid) if isinstance(available_entities, dict) else None) or eid
			for eid in selected_entities
		}
		
		mode_labels = {
			"master": "Master (only one linked entity drives the switch)",
			"or": "Any on (OR) — switch turns on if any linked entity is on",
			"and": "All on (AND) — switch turns on only if all linked entities are on",
		}
		direction_labels = self._get_link_direction_labels()
		
		return self.async_show_form(
			step_id="link_entities_behaviour",
			data_schema=vol.Schema({
				vol.Required("mode", default=mode_default): vol.In(mode_labels),
				vol.Optional("master", default=master_default): vol.In(master_labels),
				vol.Required("direction", default=direction_default): vol.In(direction_labels),
			}),
			description_placeholders={
				"info": (
					f"Choose how multiple linked entities should update **{selected_name}**.\n\n"
					"Tip: If you want the switch to follow one specific entity, use **Master**.\n"
					"Note: The **Master** selection only applies when mode is **Master**."
				)
			},
		)

	async def async_step_delete_switch(
		self, user_input: Optional[Dict[str, Any]] = None
	) -> FlowResult:
		"""Delete a switch."""
		selected_uid = self._step_data.get("selected_uid")
		selected_name = self._step_data.get("selected_name", selected_uid)
		is_owner = self._step_data.get("is_owner", False)
		
		if user_input is not None:
			if user_input.get("confirm", False):
				# Get coordinator and delete
				coordinator = self.hass.data[DOMAIN][self._config_entry.entry_id]
				
				if is_owner and selected_uid:
					# Delete via coordinator (handles backend + entity removal)
					await coordinator.delete_switch(selected_uid)
				else:
					# Just remove from subscriptions
					if selected_uid:
						coordinator.subscriptions.pop(selected_uid, None)
						
						# Remove from options too
						options = dict(self._config_entry.options or {})
						subscriptions = options.get("subscriptions", {})
						if selected_name in subscriptions:
							del subscriptions[selected_name]
						
						return self.async_create_entry(title="", data=options)
				
				return self.async_create_entry(title="", data={})
			else:
				return self.async_create_entry(title="", data={})
		
		return self.async_show_form(
			step_id="delete_switch",
			data_schema=vol.Schema({
				vol.Required("confirm", default=False): bool
			}),
			description_placeholders={
				"warning": f"⚠️ Are you sure you want to delete '{selected_name}'?"
			}
		)

	async def async_step_remove_from_installation(
		self, user_input: Optional[Dict[str, Any]] = None
	) -> FlowResult:
		"""Remove switch from this Home Assistant installation (doesn't delete from server)."""
		selected_uid = self._step_data.get("selected_uid")
		selected_name = self._step_data.get("selected_name", selected_uid)
		
		if user_input is not None:
			if user_input.get("confirm", False):
				# Remove from imported switches
				options = dict(self._config_entry.options or {})
				imported_switches = options.get("imported_switches", {})
				
				if selected_uid in imported_switches:
					del imported_switches[selected_uid]
					options["imported_switches"] = imported_switches
					
					self.hass.config_entries.async_update_entry(
						self._config_entry,
						options=options
					)
					
					_LOGGER.info("Removed switch %s from installation (not deleted from server)", selected_uid)
					
					# Reload integration to remove entity
					await self.hass.config_entries.async_reload(self._config_entry.entry_id)
				
				return self.async_create_entry(title="", data=options)
			else:
				return self.async_create_entry(title="", data={})
		
		return self.async_show_form(
			step_id="remove_from_installation",
			data_schema=vol.Schema({
				vol.Required("confirm", default=False): bool
			}),
			description_placeholders={
				"warning": f"Remove '{selected_name}' from this Home Assistant installation?\n\n"
				f"⚠️ This will remove the entity from this HA instance only.\n"
				f"The switch will still exist on the VomeSync server and in other installations.\n\n"
				f"You can re-import it later using 'Import switches'."
			}
		)

	async def async_step_edit_connection(
		self, user_input: Optional[Dict[str, Any]] = None
	) -> FlowResult:
		"""Edit connection details."""
		errors = {}
		
		if user_input is not None:
			run_test = bool(user_input.get("test_connection", False))
			
			# Update config entry with new URLs
			new_data = dict(self._config_entry.data)
			new_data[CONF_SERVER_URL] = user_input[CONF_SERVER_URL]
			new_data[CONF_WEBSOCKET_URL] = user_input[CONF_WEBSOCKET_URL]

			# Optional test before saving (requested UX)
			if run_test:
				api_client = VomeSyncAPIClient(
					new_data[CONF_SERVER_URL],
					new_data.get(CONF_PERSONAL_KEY),
					auth_mode=new_data.get(CONF_AUTH_MODE),
					crypto_seed=new_data.get(CONF_CRYPTO_SEED),
				)
				try:
					test = await api_client.test_connection()
					if not test.get("health_ok", False):
						errors["base"] = "test_failed"
						# Fall through to show form with test output
					else:
						# Only show failure inline; on success we proceed to save+reload.
						pass
				finally:
					await api_client.close()
				
				if errors:
					# Show form again with the attempted values and failure details
					return self.async_show_form(
						step_id="edit_connection",
						data_schema=vol.Schema({
							vol.Required(CONF_SERVER_URL, default=new_data[CONF_SERVER_URL]): str,
							vol.Required(CONF_WEBSOCKET_URL, default=new_data[CONF_WEBSOCKET_URL]): str,
							vol.Optional("test_connection", default=True): bool,
						}),
						errors=errors,
						description_placeholders={
							"info": f"Connection test failed.\n\nServer URL: `{new_data[CONF_SERVER_URL]}`\nWebSocket URL: `{new_data[CONF_WEBSOCKET_URL]}`\n\n"
							f"Check the URLs and try again."
						}
					)
			
			self.hass.config_entries.async_update_entry(
				self._config_entry,
				data=new_data
			)
			
			# Reload the entry to apply new URLs
			await self.hass.config_entries.async_reload(self._config_entry.entry_id)
			
			return self.async_create_entry(title="", data={})
		
		current_server = self._config_entry.data.get(CONF_SERVER_URL, "")
		current_ws = self._config_entry.data.get(CONF_WEBSOCKET_URL, "")
		current_seed = self._config_entry.data.get(CONF_CRYPTO_SEED, "")
		owner_pub = ""
		if current_seed:
			try:
				owner_pub = owner_pubkey_b64url(current_seed)
			except Exception:  # noqa: BLE001
				owner_pub = ""
		
		# HA config forms don't support true read-only fields; show key info in the description instead.
		if current_seed:
			info = (
				"Signing key (keep safe):\n"
				f"`{current_seed}`\n\n"
				"Owner public key:\n"
				f"`{owner_pub}`\n\n"
				"Edit server and WebSocket URLs below. Changes will reload the integration."
			)
		else:
			info = (
				"No signing key is stored for this entry.\n\n"
				"Edit server and WebSocket URLs below. Changes will reload the integration."
			)
		
		schema_fields = {
			vol.Required(CONF_SERVER_URL, default=current_server): str,
			vol.Required(CONF_WEBSOCKET_URL, default=current_ws): str,
			vol.Optional("test_connection", default=False): bool,
		}
		
		return self.async_show_form(
			step_id="edit_connection",
			data_schema=vol.Schema(schema_fields),
			errors=errors,
			description_placeholders={
				"info": info
			}
		)

	async def async_step_reannounce_owned_switches(
		self, user_input: Optional[Dict[str, Any]] = None
	) -> FlowResult:
		"""Re-announce owned v2 switches to the current server (recovery/migration)."""
		# Determine eligibility from local cache (works even if server returns zero switches)
		options = self._config_entry.options or {}
		imported = options.get("imported_switches", {}) or {}

		eligible = 0
		owned_total = 0
		for uid, info in imported.items():
			if not isinstance(uid, str) or not isinstance(info, dict):
				continue
			if not info.get("is_owner", False):
				continue
			owned_total += 1
			idx = info.get("crypto_index")
			if not isinstance(idx, int):
				cached = info.get("cached_data") if isinstance(info.get("cached_data"), dict) else {}
				idx = cached.get("index") if isinstance(cached, dict) else None
			if uid.startswith("vs_") and isinstance(idx, int):
				eligible += 1

		if user_input is None:
			return self.async_show_form(
				step_id="reannounce_owned_switches",
				data_schema=vol.Schema({vol.Required("confirm", default=False): bool}),
				description_placeholders={
					"info": (
						"This will re-announce your **keypair-owned** switches to the currently configured server.\n\n"
						f"Owned switches in this installation: **{owned_total}**\n"
						f"Eligible (v2, UID starts with `vs_`): **{eligible}**\n\n"
						"Use this when switching servers or after a server data loss.\n"
						"⚠️ UUID-based (v1) switches cannot be re-announced because their UIDs are not deterministic."
					)
				},
			)

		if not user_input.get("confirm", False):
			return self.async_create_entry(title="", data=dict(self._config_entry.options or {}))

		coordinator = self.hass.data[DOMAIN][self._config_entry.entry_id]
		res = await coordinator.reannounce_owned_switches()
		self._step_data["reannounce_result"] = res

		return self.async_show_form(
			step_id="reannounce_owned_switches_result",
			data_schema=vol.Schema({}),
			description_placeholders={
				"info": (
					"Re-announce complete.\n\n"
					f"Eligible: **{res.get('eligible')}**\n"
					f"Attempted: **{res.get('attempted')}**\n"
					f"Succeeded: **{res.get('succeeded')}**\n"
					f"Skipped: **{res.get('skipped')}**\n"
					f"Errors: **{len(res.get('errors', []))}**\n\n"
					"Reload the integration (or wait for next refresh) and try importing again."
				)
			},
		)

	async def async_step_reannounce_owned_switches_result(
		self, user_input: Optional[Dict[str, Any]] = None
	) -> FlowResult:
		"""Final step for re-announce results."""
		return self.async_create_entry(title="", data=dict(self._config_entry.options or {}))

	async def async_step_cleanup_orphaned_devices(
		self, user_input: Optional[Dict[str, Any]] = None
	) -> FlowResult:
		"""Remove orphaned VomeSync devices (devices with no entities)."""
		from homeassistant.helpers import device_registry as dr
		from homeassistant.helpers import entity_registry as er

		device_reg = dr.async_get(self.hass)
		entity_reg = er.async_get(self.hass)

		# Devices belonging to this config entry
		devices = dr.async_entries_for_config_entry(device_reg, self._config_entry.entry_id)

		orphan_devices: Dict[str, str] = {}
		device_id_to_uid: Dict[str, str] = {}

		for dev in devices:
			dev_id = getattr(dev, "id", None)
			if not isinstance(dev_id, str) or not dev_id:
				continue

			entities = er.async_entries_for_device(entity_reg, dev_id)
			if entities:
				continue  # Not an orphan

			uid = None
			for ident in getattr(dev, "identifiers", set()) or set():
				try:
					ident_domain, ident_value = ident
				except Exception:  # noqa: BLE001
					continue
				if ident_domain == DOMAIN and isinstance(ident_value, str):
					uid = ident_value
					break

			# Prefer user name; fall back to uid suffix
			name = getattr(dev, "name_by_user", None) or getattr(dev, "name", None) or "VomeSync device"
			if uid:
				label = f"{name} (…{uid[-6:]})"
				device_id_to_uid[dev_id] = uid
			else:
				label = str(name)

			orphan_devices[dev_id] = label

		if not orphan_devices:
			return self.async_abort(reason="no_orphaned_devices")

		if user_input is None:
			return self.async_show_form(
				step_id="cleanup_orphaned_devices",
				data_schema=vol.Schema({
					vol.Required("devices"): cv.multi_select(orphan_devices)
				}),
				description_placeholders={
					"info": (
						"These devices have **no entities** and are safe to remove from Home Assistant.\n\n"
						f"Orphaned devices found: **{len(orphan_devices)}**"
					)
				},
			)

		selected = user_input.get("devices", [])
		if not selected:
			return self.async_create_entry(title="", data=dict(self._config_entry.options or {}))

		# Also remove from imported cache to prevent re-creation
		options = dict(self._config_entry.options or {})
		imported_switches = options.get("imported_switches", {}) or {}
		changed = False

		for dev_id in selected:
			if not isinstance(dev_id, str):
				continue
			uid = device_id_to_uid.get(dev_id)
			if uid and uid in imported_switches:
				del imported_switches[uid]
				changed = True

			try:
				device_reg.async_remove_device(dev_id)
			except Exception as ex:  # noqa: BLE001
				_LOGGER.warning("Failed to remove device %s: %s", dev_id, ex)

		if changed:
			options["imported_switches"] = imported_switches
			self.hass.config_entries.async_update_entry(self._config_entry, options=options)

		return self.async_create_entry(title="", data=dict(self._config_entry.options or {}))
