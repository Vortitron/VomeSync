"""Options flow mixin: link this Home Assistant to a Vome account (relay).

Implements the component side of the RFC 8628-style device-authorisation flow:
we ask the portal for a short code, the user approves it on vome.io with their
GitHub session, and we store the returned relay credentials in the entry options
and start the outbound relay immediately.  Kept in its own mixin so config_flow
stays under the refactor threshold (mirrors options_flow_links.py).

Step layout (UX): ``link_vome`` shows the code and two ways forward — a plain
"connect" confirm (no text boxes on HAOS / Supervised, where the Supervisor
token makes everything automatic) and an "alternative connection" form holding
the manual fields (long-lived token, ESPHome dashboard URL) for the rare
setups that need them.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

import voluptuous as vol
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
	CONF_RELAY,
	CONF_RELAY_ESPHOME_URL,
	CONF_RELAY_LOCAL_TOKEN,
	CONF_RELAY_SECRET,
	CONF_RELAY_SERVER_ID,
	CONF_RELAY_WS_URL,
	DEFAULT_PORTAL_URL,
	SUPERVISOR_TOKEN_ENV,
)
from .relay_client import (
	async_poll_device_token,
	async_request_device_code,
	async_start_relay,
	async_stop_relay,
)

_LOGGER = logging.getLogger(__name__)

_SD_DEVICE_CODE = "relay_device_code"
_SD_USER_CODE = "relay_user_code"
_SD_VERIFICATION_URI = "relay_verification_uri"


class VomeSyncOptionsFlowRelayMixin:
	"""Mixin providing the "Connect this Home Assistant to Vome" steps."""

	def _relay_is_linked(self) -> bool:
		relay = (self._config_entry.options or {}).get(CONF_RELAY)
		return isinstance(relay, dict) and bool(relay.get(CONF_RELAY_SERVER_ID))

	def _relay_portal_url(self) -> str:
		# Overridable later; the portal returns the WS URL so only this is needed.
		return DEFAULT_PORTAL_URL

	async def _relay_clear_pending(self) -> None:
		for key in (_SD_DEVICE_CODE, _SD_USER_CODE, _SD_VERIFICATION_URI):
			self._step_data.pop(key, None)

	async def _relay_request_new_code(self, session) -> Optional[str]:
		"""Fetch a fresh device code into step data; return an error key or None."""
		try:
			started = await async_request_device_code(
				session, self._relay_portal_url(), name=self._config_entry.title
			)
		except Exception as err:  # noqa: BLE001
			_LOGGER.warning("Relay device-code request failed: %s", err)
			return "relay_code_failed"
		self._step_data[_SD_DEVICE_CODE] = started.get("device_code")
		self._step_data[_SD_USER_CODE] = started.get("user_code")
		self._step_data[_SD_VERIFICATION_URI] = started.get(
			"verification_uri", DEFAULT_PORTAL_URL + "/account/link-ha"
		)
		return None

	def _relay_supervisor_available(self) -> bool:
		"""True on HAOS / Supervised, where no manual token is needed."""
		return bool(os.environ.get(SUPERVISOR_TOKEN_ENV))

	def _relay_code_placeholders(self) -> Dict[str, str]:
		return {
			"user_code": self._step_data.get(_SD_USER_CODE, "") or "—",
			"verification_uri": self._step_data.get(
				_SD_VERIFICATION_URI, DEFAULT_PORTAL_URL + "/account/link-ha"
			),
		}

	async def _relay_poll_and_finish(
		self, user_input: Dict[str, Any], errors: Dict[str, str]
	) -> Optional[FlowResult]:
		"""Poll for approval; store the link and finish on success.

		Mutates ``errors`` and returns ``None`` when the flow should re-show
		its form (pending / expired / failure).
		"""
		session = async_get_clientsession(self.hass)
		try:
			result = await async_poll_device_token(
				session, self._relay_portal_url(), self._step_data[_SD_DEVICE_CODE]
			)
		except Exception as err:  # noqa: BLE001
			_LOGGER.warning("Relay device-token poll failed: %s", err)
			errors["base"] = "relay_poll_failed"
			return None

		status = result.get("status")
		if status == "approved":
			relay = {
				CONF_RELAY_SERVER_ID: result.get("server_id"),
				CONF_RELAY_SECRET: result.get("relay_secret"),
				CONF_RELAY_WS_URL: result.get("relay_ws_url"),
			}
			local_token = (user_input.get("local_token") or "").strip()
			if local_token:
				relay[CONF_RELAY_LOCAL_TOKEN] = local_token
			esphome_url = (user_input.get("esphome_url") or "").strip()
			if esphome_url:
				relay[CONF_RELAY_ESPHOME_URL] = esphome_url
			options = dict(self._config_entry.options or {})
			options[CONF_RELAY] = relay
			await self._async_update_entry_options(options)
			await async_start_relay(self.hass, self._config_entry)
			await self._relay_clear_pending()
			return self.async_create_entry(title="", data=options)
		if status == "pending":
			errors["base"] = "relay_pending"
			return None
		# expired / unknown — mint a fresh code so the form shows a valid one.
		await self._relay_clear_pending()
		code_err = await self._relay_request_new_code(session)
		errors["base"] = code_err or "relay_expired"
		return None

	async def _relay_show_link_form(
		self, step_id: str, errors: Dict[str, str], include_alt_fields: bool
	) -> FlowResult:
		"""Render a link form; manual fields only where they're required."""
		fields: Dict[Any, Any] = {}
		if include_alt_fields or not self._relay_supervisor_available():
			fields[vol.Optional("local_token", default="")] = str
		if include_alt_fields:
			fields[vol.Optional("esphome_url", default="")] = str
		return self.async_show_form(
			step_id=step_id,
			data_schema=vol.Schema(fields),
			errors=errors,
			description_placeholders=self._relay_code_placeholders(),
		)

	async def async_step_link_vome(
		self, user_input: Optional[Dict[str, Any]] = None
	) -> FlowResult:
		"""Entry point: fetch a code, show it, offer the two ways forward."""
		if not self._step_data.get(_SD_DEVICE_CODE):
			session = async_get_clientsession(self.hass)
			code_err = await self._relay_request_new_code(session)
			if code_err:
				# No code to show — surface the error on a retry form.
				return await self._relay_show_link_form(
					"link_vome_retry", {"base": code_err}, include_alt_fields=False
				)
		return self.async_show_menu(
			step_id="link_vome",
			menu_options=["link_vome_confirm", "link_vome_alt"],
			description_placeholders=self._relay_code_placeholders(),
		)

	async def async_step_link_vome_retry(
		self, user_input: Optional[Dict[str, Any]] = None
	) -> FlowResult:
		"""Submit on the failure form retries the code request."""
		return await self.async_step_link_vome()

	async def async_step_link_vome_confirm(
		self, user_input: Optional[Dict[str, Any]] = None
	) -> FlowResult:
		"""Plain connect: no text boxes unless a local token is required."""
		if not self._step_data.get(_SD_DEVICE_CODE):
			return await self.async_step_link_vome()
		errors: Dict[str, str] = {}
		if user_input is not None:
			finished = await self._relay_poll_and_finish(user_input, errors)
			if finished is not None:
				return finished
		return await self._relay_show_link_form(
			"link_vome_confirm", errors, include_alt_fields=False
		)

	async def async_step_link_vome_alt(
		self, user_input: Optional[Dict[str, Any]] = None
	) -> FlowResult:
		"""Alternative connection: manual token + ESPHome dashboard URL."""
		if not self._step_data.get(_SD_DEVICE_CODE):
			return await self.async_step_link_vome()
		errors: Dict[str, str] = {}
		if user_input is not None:
			finished = await self._relay_poll_and_finish(user_input, errors)
			if finished is not None:
				return finished
		return await self._relay_show_link_form(
			"link_vome_alt", errors, include_alt_fields=True
		)

	async def async_step_unlink_vome(
		self, user_input: Optional[Dict[str, Any]] = None
	) -> FlowResult:
		"""Remove the Vome link and stop the relay."""
		options = dict(self._config_entry.options or {})
		options.pop(CONF_RELAY, None)
		await self._async_update_entry_options(options)
		await async_stop_relay(self.hass, self._config_entry)
		return self.async_create_entry(title="", data=options)
