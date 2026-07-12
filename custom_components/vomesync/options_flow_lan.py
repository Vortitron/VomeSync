"""Options flow mixin: friendly-domain remote access + LAN path tunnels.

Keeps config_flow under the refactor threshold.  Steps:
  * ``remote_access`` — toggle full-UI forwarding (HA itself on the friendly domain)
  * ``lan_routes`` — list / add / remove ``/t/<slug>/`` → LAN host:port routes

LAN routes are stored in the same ``options.relay`` dict as the link credentials
so ``async_start_relay`` reloads them atomically with the rest of the tunnel.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

import voluptuous as vol
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import config_validation as cv

from .const import (
	CONF_RELAY,
	CONF_RELAY_FORWARD_UI,
	CONF_RELAY_LAN_ROUTES,
)
from .lan_routes import (
	LAN_MAX_ROUTES,
	LAN_ROUTE_SCHEMES,
	ROUTE_ENABLED,
	ROUTE_HOST,
	ROUTE_NAME,
	ROUTE_PORT,
	ROUTE_SCHEME,
	ROUTE_SLUG,
	ROUTE_WEBSOCKET,
	normalise_routes,
	validate_route,
)
from .relay_client import async_start_relay

_LOGGER = logging.getLogger(__name__)


class VomeSyncOptionsFlowLanMixin:
	"""Mixin: remote access toggles and LAN tunnel management."""

	def _relay_options_dict(self) -> dict:
		relay = (self._config_entry.options or {}).get(CONF_RELAY)
		return dict(relay) if isinstance(relay, dict) else {}

	def _lan_routes_list(self) -> list:
		return normalise_routes(self._relay_options_dict().get(CONF_RELAY_LAN_ROUTES))

	async def _relay_save_and_restart(self, relay: dict) -> None:
		options = dict(self._config_entry.options or {})
		options[CONF_RELAY] = relay
		await self._async_update_entry_options(options)
		await async_start_relay(self.hass, self._config_entry)

	async def async_step_remote_access(
		self, user_input: Optional[Dict[str, Any]] = None
	) -> FlowResult:
		"""Toggle full-UI forwarding and jump to LAN route management."""
		if not self._relay_is_linked():
			return await self.async_step_init()
		relay = self._relay_options_dict()
		if user_input is not None:
			relay[CONF_RELAY_FORWARD_UI] = bool(user_input.get(CONF_RELAY_FORWARD_UI))
			await self._relay_save_and_restart(relay)
			if user_input.get("manage_lan"):
				return await self.async_step_lan_routes()
			return self.async_create_entry(title="", data=dict(self._config_entry.options or {}))

		routes = self._lan_routes_list()
		enabled = sum(1 for r in routes if r.get(ROUTE_ENABLED) is not False)
		return self.async_show_form(
			step_id="remote_access",
			data_schema=vol.Schema({
				vol.Required(
					CONF_RELAY_FORWARD_UI,
					default=bool(relay.get(CONF_RELAY_FORWARD_UI)),
				): bool,
				vol.Optional("manage_lan", default=False): bool,
			}),
			description_placeholders={
				"lan_count": str(len(routes)),
				"lan_enabled": str(enabled),
			},
		)

	async def async_step_lan_routes(
		self, user_input: Optional[Dict[str, Any]] = None
	) -> FlowResult:
		"""List LAN routes; offer add / remove."""
		if not self._relay_is_linked():
			return await self.async_step_init()
		routes = self._lan_routes_list()
		if user_input is not None:
			action = user_input.get("action")
			if action == "add":
				return await self.async_step_lan_route_add()
			if action == "remove":
				slug = user_input.get("remove_slug")
				if slug:
					relay = self._relay_options_dict()
					relay[CONF_RELAY_LAN_ROUTES] = [
						r for r in routes if r.get(ROUTE_SLUG) != slug
					]
					await self._relay_save_and_restart(relay)
				return await self.async_step_lan_routes()
			return await self.async_step_remote_access()

		lines = []
		for r in routes:
			state = "on" if r.get(ROUTE_ENABLED) is not False else "off"
			lines.append(
				f"• **{r.get(ROUTE_NAME)}** (`/t/{r.get(ROUTE_SLUG)}/`) → "
				f"`{r.get(ROUTE_SCHEME)}://{r.get(ROUTE_HOST)}:{r.get(ROUTE_PORT)}` [{state}]"
			)
		summary = "\n".join(lines) if lines else "_No LAN routes yet._"
		remove_options = {r[ROUTE_SLUG]: f"{r[ROUTE_NAME]} (/t/{r[ROUTE_SLUG]}/)" for r in routes}
		fields: Dict[Any, Any] = {
			vol.Required("action", default="back"): vol.In({
				"add": "Add a LAN route",
				"remove": "Remove a LAN route",
				"back": "Back to remote access",
			}),
		}
		if remove_options:
			fields[vol.Optional("remove_slug")] = vol.In(remove_options)
		return self.async_show_form(
			step_id="lan_routes",
			data_schema=vol.Schema(fields),
			description_placeholders={"routes_summary": summary},
		)

	async def async_step_lan_route_add(
		self, user_input: Optional[Dict[str, Any]] = None
	) -> FlowResult:
		"""Add one LAN route."""
		errors: Dict[str, str] = {}
		routes = self._lan_routes_list()
		if user_input is not None:
			if len(routes) >= LAN_MAX_ROUTES:
				errors["base"] = "lan_too_many"
			else:
				existing = {r[ROUTE_SLUG] for r in routes}
				route, err = validate_route({
					ROUTE_SLUG: user_input.get(ROUTE_SLUG),
					ROUTE_NAME: user_input.get(ROUTE_NAME),
					ROUTE_HOST: user_input.get(ROUTE_HOST),
					ROUTE_PORT: user_input.get(ROUTE_PORT),
					ROUTE_SCHEME: user_input.get(ROUTE_SCHEME),
					ROUTE_ENABLED: user_input.get(ROUTE_ENABLED, True),
					ROUTE_WEBSOCKET: user_input.get(ROUTE_WEBSOCKET, True),
				}, existing_slugs=existing)
				if err or route is None:
					errors["base"] = err or "lan_route_invalid"
				else:
					relay = self._relay_options_dict()
					relay[CONF_RELAY_LAN_ROUTES] = routes + [route]
					await self._relay_save_and_restart(relay)
					return await self.async_step_lan_routes()

		return self.async_show_form(
			step_id="lan_route_add",
			data_schema=vol.Schema({
				vol.Required(ROUTE_SLUG): cv.string,
				vol.Optional(ROUTE_NAME, default=""): cv.string,
				vol.Required(ROUTE_HOST): cv.string,
				vol.Required(ROUTE_PORT, default=80): vol.All(vol.Coerce(int), vol.Range(min=1, max=65535)),
				vol.Required(ROUTE_SCHEME, default="http"): vol.In(list(LAN_ROUTE_SCHEMES)),
				vol.Required(ROUTE_ENABLED, default=True): bool,
				vol.Required(ROUTE_WEBSOCKET, default=True): bool,
			}),
			errors=errors,
		)
