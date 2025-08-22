"""Config flow for VomeSync integration."""
import logging
from typing import Any, Dict, Optional
import uuid

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResult

from .api_client import VomeSyncAPIClient
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

	async def async_step_user(
		self, user_input: Optional[Dict[str, Any]] = None
	) -> FlowResult:
		"""Handle the initial step."""
		errors = {}

		if user_input is not None:
			self._server_url = user_input.get(CONF_SERVER_URL, DEFAULT_SERVER_URL)
			self._websocket_url = user_input.get(CONF_WEBSOCKET_URL, DEFAULT_WEBSOCKET_URL)
			
			# Check if we have a personal key
			if user_input.get(CONF_PERSONAL_KEY):
				self._personal_key = user_input[CONF_PERSONAL_KEY]
				
				# Validate the key
				api_client = VomeSyncAPIClient(self._server_url)
				is_valid = await api_client.validate_personal_key(self._personal_key)
				
				if is_valid:
					return await self._create_entry()
				else:
					errors[CONF_PERSONAL_KEY] = "invalid_key"
			else:
				# Generate new key
				return await self.async_step_generate_key()

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
				"warning": "⚠️ Public mode is NOT private - anyone with UID can view/toggle switches. Use only for non-sensitive devices."
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
				"privacy_notice": "By generating a personal key, you consent to the VomeSync privacy policy. Your key will be stored securely and used only for switch authentication."
			}
		)

	async def _create_entry(self) -> FlowResult:
		"""Create the config entry."""
		# Check for existing entries
		await self.async_set_unique_id(self._personal_key)
		self._abort_if_unique_id_configured()

		title = f"VomeSync ({self._personal_key[:8]}...)"

		return self.async_create_entry(
			title=title,
			data={
				CONF_PERSONAL_KEY: self._personal_key,
				CONF_SERVER_URL: self._server_url,
				CONF_WEBSOCKET_URL: self._websocket_url,
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
		self.config_entry = config_entry
		self._step_data: Dict[str, Any] = {}

	async def async_step_init(
		self, user_input: Optional[Dict[str, Any]] = None
	) -> FlowResult:
		"""Manage the options."""
		return self.async_show_menu(
			step_id="init",
			menu_options=["create_switch", "subscribe_switch", "manage_switches"]
		)

	async def async_step_create_switch(
		self, user_input: Optional[Dict[str, Any]] = None
	) -> FlowResult:
		"""Create a new switch."""
		errors = {}

		if user_input is not None:
			try:
				# Get API client
				api_client = VomeSyncAPIClient(
					self.config_entry.data[CONF_SERVER_URL],
					self.config_entry.data[CONF_PERSONAL_KEY]
				)
				
				# Create switch
				switch_data = await api_client.create_switch(
					description=user_input.get(CONF_SWITCH_DESCRIPTION, ""),
					location=user_input.get(CONF_SWITCH_LOCATION, ""),
					category=user_input.get(CONF_SWITCH_CATEGORY, "Other"),
					publicize=user_input.get(CONF_SWITCH_PUBLICIZE, False)
				)
				
				# Store in options for coordinator to pick up
				options = dict(self.config_entry.options)
				switches = options.setdefault("switches", {})
				
				switch_name = user_input[CONF_SWITCH_NAME]
				switches[switch_name] = {
					CONF_SWITCH_UID: switch_data["uid"],
					CONF_SWITCH_DESCRIPTION: user_input.get(CONF_SWITCH_DESCRIPTION, ""),
					CONF_SWITCH_LOCATION: user_input.get(CONF_SWITCH_LOCATION, ""),
					CONF_SWITCH_CATEGORY: user_input.get(CONF_SWITCH_CATEGORY, "Other"),
					CONF_SWITCH_PUBLICIZE: user_input.get(CONF_SWITCH_PUBLICIZE, False),
					"is_owner": True
				}
				
				return self.async_create_entry(title="", data=options)
				
			except Exception as ex:
				_LOGGER.error("Failed to create switch: %s", ex)
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
				
				# Get API client
				api_client = VomeSyncAPIClient(self.config_entry.data[CONF_SERVER_URL])
				
				# Check if switch exists
				switch_status = await api_client.get_switch_status(uid)
				
				if switch_status:
					# Store in options
					options = dict(self.config_entry.options)
					subscriptions = options.setdefault("subscriptions", {})
					
					switch_name = user_input[CONF_SWITCH_NAME]
					subscriptions[switch_name] = {
						CONF_SWITCH_UID: uid,
						"is_owner": False
					}
					
					return self.async_create_entry(title="", data=options)
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
		# This would show current switches and allow deletion
		# For now, redirect back to main menu
		return await self.async_step_init()
