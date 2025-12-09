"""Config flow for VomeSync integration."""
import logging
from typing import Any, Dict, Optional
import uuid

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResult

from .api_client import VomeSyncAPIClient, VomeSyncAPIError
from .const import (
	DOMAIN,
	CONF_PERSONAL_KEY,
	CONF_SERVER_URL,
	CONF_WEBSOCKET_URL,
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

_LOGGER = logging.getLogger(__name__)


class VomeSyncConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
	"""Handle a config flow for VomeSync."""

	VERSION = 1

	def __init__(self) -> None:
		"""Initialize the config flow."""
		self._personal_key: Optional[str] = None
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
			
			# Check if we have a personal key
			if user_input.get(CONF_PERSONAL_KEY):
				self._personal_key = user_input[CONF_PERSONAL_KEY]
				
				# Validate the key
				api_client = VomeSyncAPIClient(self._server_url)
				try:
					is_valid = await api_client.validate_personal_key(self._personal_key)
					
					if is_valid:
						return await self._create_entry()
					else:
						errors[CONF_PERSONAL_KEY] = "invalid_key"
				finally:
					await api_client.close()
			else:
				# Generate new key
				return await self.async_step_generate_key()
		else:
			# First load: keep websocket URL in sync with the server URL
			self._websocket_url = self._derive_websocket_url(self._server_url)

		data_schema = vol.Schema({
			vol.Optional(CONF_SERVER_URL, default=self._server_url): str,
			vol.Optional(CONF_WEBSOCKET_URL, default=self._websocket_url): str,
			vol.Optional(CONF_PERSONAL_KEY): str,
		})

		return self.async_show_form(
			step_id="user",
			data_schema=data_schema,
			errors=errors,
			description_placeholders={
				"warning": "⚠️ Public mode is NOT private - anyone with UID can view/toggle switches. Use only for non-sensitive devices.",
				"personal_key_hint": "Leave personal key empty to generate a new one on the next step."
			}
		)

	async def async_step_generate_key(
		self, user_input: Optional[Dict[str, Any]] = None
	) -> FlowResult:
		"""Generate a new personal key."""
		errors = {}

		if user_input is not None:
			if user_input.get("consent", False):
				# Generate new personal key
				api_client = VomeSyncAPIClient(self._server_url)
				
				try:
					key_data = await api_client.generate_personal_key()
					self._personal_key = key_data["personalKey"]
					return await self._create_entry()
				except Exception as ex:
					_LOGGER.error("Failed to generate personal key: %s", ex)
					errors["base"] = "generate_key_failed"
				finally:
					await api_client.close()
			else:
				errors["consent"] = "consent_required"

		data_schema = vol.Schema({
			vol.Required("consent", default=False): bool,
		})

		return self.async_show_form(
			step_id="generate_key",
			data_schema=data_schema,
			errors=errors,
			description_placeholders={
				"privacy_notice": "By ticking consent you allow VomeSync to create and store a personal key for your account on the server. You can delete it later from the integration options (GDPR delete key)."
			}
		)

	async def _create_entry(self) -> FlowResult:
		"""Create the config entry."""
		# Check for existing entries
		await self.async_set_unique_id(self._personal_key)
		self._abort_if_unique_id_configured()

		title = f"VomeSync ({self._personal_key[:8]}...)"

		# Ensure websocket URL stored matches server_url if user left it blank
		websocket_url = self._websocket_url or self._derive_websocket_url(self._server_url)

		return self.async_create_entry(
			title=title,
			data={
				CONF_PERSONAL_KEY: self._personal_key,
				CONF_SERVER_URL: self._server_url,
				CONF_WEBSOCKET_URL: websocket_url,
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

	async def async_step_init(
		self, user_input: Optional[Dict[str, Any]] = None
	) -> FlowResult:
		"""Manage the options."""
		return self.async_show_menu(
			step_id="init",
			menu_options=["create_switch", "subscribe_switch", "manage_switches", "manage_api_keys", "connect_website", "show_details"]
		)

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
					# Return empty entry to close options flow
					return self.async_create_entry(title="", data={})
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
				
				# Validate UID format
				uuid.UUID(uid)
				
				# Get coordinator
				coordinator = self.hass.data[DOMAIN][self._config_entry.entry_id]
				
				# Subscribe via coordinator (handles API check + dynamic entity addition)
				switch_name = user_input[CONF_SWITCH_NAME]
				success = await coordinator.subscribe_to_switch(switch_name, uid)
				
				if success:
					# Return empty entry to close options flow
					return self.async_create_entry(title="", data={})
				else:
					errors[CONF_SWITCH_UID] = "switch_not_found"
					
			except ValueError:
				errors[CONF_SWITCH_UID] = "invalid_uid"
			except Exception as ex:
				_LOGGER.error("Failed to subscribe to switch: %s", ex)
				errors["base"] = "subscribe_failed"

		data_schema = vol.Schema({
			vol.Required(CONF_SWITCH_NAME): str,
			vol.Required(CONF_SWITCH_UID): str,
		})

		return self.async_show_form(
			step_id="subscribe_switch",
			data_schema=data_schema,
			errors=errors,
			description_placeholders={
				"uid_info": "Enter the UID of the switch you want to subscribe to. You can find public switches at remoteswitch.vome.io"
			}
		)

	async def async_step_manage_switches(
		self, user_input: Optional[Dict[str, Any]] = None
	) -> FlowResult:
		"""Manage existing switches."""
		# Get coordinator to access actual switches
		coordinator = self.hass.data[DOMAIN][self._config_entry.entry_id]
		
		# Get switches from coordinator's live state
		switches = coordinator.switches
		subscriptions = coordinator.subscriptions
		
		if not switches and not subscriptions:
			return self.async_abort(reason="no_switches")
		
		# Try to get entity names from entity registry for better display
		from homeassistant.helpers import entity_registry as er
		entity_reg = er.async_get(self.hass)
		
		# Get all VomeSync entities
		vomesync_entities = entity_reg.entities.get_entries_for_config_entry_id(
			self._config_entry.entry_id
		)
		
		# Build uid -> entity mapping
		uid_to_entity = {}
		for entity in vomesync_entities:
			if entity.unique_id and entity.unique_id.startswith("vomesync_"):
				uid = entity.unique_id.replace("vomesync_", "")
				uid_to_entity[uid] = entity
		
		# Build list of switches to manage using entity names with UID hints
		# Only show switches that have actual entities
		switch_list = {}
		for uid, switch_data in switches.items():
			# Skip if no entity exists
			entity = uid_to_entity.get(uid)
			if not entity:
				continue
			
			# Get name from entity
			name = entity.name or entity.original_name or uid[:8]
			
			# Add last 6 chars of UID for disambiguation
			uid_hint = uid[-6:]
			switch_list[uid] = f"🔧 {name} (…{uid_hint})"
			
		for uid, sub_data in subscriptions.items():
			# Skip if no entity exists
			entity = uid_to_entity.get(uid)
			if not entity:
				continue
			
			# Get name from entity
			name = entity.name or entity.original_name or uid[:8]
			
			uid_hint = uid[-6:]
			switch_list[uid] = f"👁️ {name} (…{uid_hint})"
		
		if user_input is not None:
			# Store selected switch UID and show options
			selected_uid = user_input["switch"]
			
			# Get name from entity registry or coordinator
			entity = uid_to_entity.get(selected_uid)
			if entity:
				selected_name = entity.name or entity.original_name or selected_uid[:8]
			elif hasattr(coordinator, 'entity_names') and selected_uid in coordinator.entity_names:
				selected_name = coordinator.entity_names[selected_uid]
			else:
				switch_data = switches.get(selected_uid) or subscriptions.get(selected_uid, {})
				selected_name = switch_data.get("name", selected_uid[:8])
			
			self._step_data["selected_uid"] = selected_uid
			self._step_data["selected_name"] = selected_name
			self._step_data["is_owner"] = selected_uid in switches
			
			return self.async_show_menu(
				step_id="manage_switch_action",
				menu_options=["view_switch", "edit_switch", "delete_switch"]
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
		
		# Build URLs
		ws_url = self._config_entry.data.get(CONF_WEBSOCKET_URL, "")
		websocket_full = f"{ws_url}?uid={selected_uid}" if ws_url else "Not configured"
		
		# Build schema fields (treated as read-only; we ignore user_input)
		# Defaults encourage HA to show copy icons.
		schema_fields = {
			vol.Required("uid", default=selected_uid): str,
			vol.Required("websocket_url", default=websocket_full): str,
		}
		
		if is_owner:
			server_url = self._config_entry.data.get(CONF_SERVER_URL, "")
			personal_key = self._config_entry.data.get(CONF_PERSONAL_KEY, "")
			webhook_url = f"{server_url}/api/toggle/{selected_uid}?personalKey={personal_key}"
			curl_cmd = f'curl -X POST "{webhook_url}"'
			
			schema_fields[vol.Required("webhook_url", default=webhook_url)] = str
			schema_fields[vol.Required("curl_command", default=curl_cmd)] = str
			
			description = f"""**{selected_name}** (Owner)

**Description:** {switch_config.get('description', 'None')}
**Location:** {switch_config.get('location', 'None')}
**Category:** {switch_config.get('category', 'Other')}
**Publicise:** {"Yes" if switch_config.get('publicize', False) else "No"}

**Webhook Usage:**
• Keep this webhook private. Anyone with it can toggle the switch.
• Browser: Visit the URL to toggle
• curl: Copy the curl command below
• Automation: Use as HTTP POST action
• Docs: https://vome.io/webhooks"""
		else:
			description = f"""**{selected_name}** (Subscribed - read-only)"""
		
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

	async def async_step_show_details(
		self, user_input: Optional[Dict[str, Any]] = None
	) -> FlowResult:
		"""Show stored connection details."""
		return self.async_show_form(
			step_id="show_details",
			data_schema=vol.Schema({}),  # informational only
			description_placeholders={
				"server_url": self._config_entry.data.get(CONF_SERVER_URL, ""),
				"websocket_url": self._config_entry.data.get(CONF_WEBSOCKET_URL, ""),
				"personal_key": self._config_entry.data.get(CONF_PERSONAL_KEY, "")
			}
		)
