"""Services for remote access + LAN tunnels (used by the Supervisor add-on panel).

Keeps the HA options flow and the add-on UI on the same code path: both mutate
``options.relay`` and restart the outbound relay via ``async_start_relay``.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.helpers import config_validation as cv

from .const import (
	CONF_RELAY,
	CONF_RELAY_FORWARD_UI,
	CONF_RELAY_LAN_ROUTES,
	CONF_RELAY_SERVER_ID,
	CONF_RELAY_WS_URL,
	DEFAULT_RELAY_WS_URL,
	DOMAIN,
	INTEGRATION_VERSION,
	LAN_TCP_TOKEN_DEFAULT_TTL,
	LAN_TCP_TOKEN_MAX_TTL,
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
	find_route,
	normalise_routes,
	validate_route,
)
from .relay_client import async_start_relay, get_relay_client

_LOGGER = logging.getLogger(__name__)


def _guard(handler):
	"""Wrap a panel-facing service so its real error reaches the UI.

	Home Assistant collapses an exception from a service handler into an
	opaque ``400: Bad Request`` over the REST API, stripping the message —
	which is exactly why the add-on panel showed a bare 400 with no clue.
	Returning ``{"error": ...}`` instead keeps the message intact all the
	way to the panel (which renders it), so failures are diagnosable.
	"""
	async def _wrapped(call: ServiceCall) -> ServiceResponse:
		try:
			return await handler(call)
		except Exception as err:  # noqa: BLE001 - deliberately surface everything
			_LOGGER.warning("vomesync.%s failed: %s", getattr(handler, "__name__", "service"), err)
			return {"error": str(err) or err.__class__.__name__}
	return _wrapped


def _relay_entries(hass: HomeAssistant) -> list[ConfigEntry]:
	return [
		e for e in hass.config_entries.async_entries(DOMAIN)
		if isinstance((e.options or {}).get(CONF_RELAY), dict)
		and (e.options.get(CONF_RELAY) or {}).get(CONF_RELAY_SERVER_ID)
	]


def _pick_entry(hass: HomeAssistant, entry_id: Optional[str]) -> ConfigEntry:
	entries = _relay_entries(hass)
	if entry_id:
		for e in entries:
			if e.entry_id == entry_id:
				return e
		raise ValueError(f"No linked Vome entry with id {entry_id}")
	if len(entries) == 1:
		return entries[0]
	if not entries:
		raise ValueError("No Home Assistant is linked to Vome yet")
	raise ValueError("Multiple linked entries; pass entry_id")


async def _save_relay(hass: HomeAssistant, entry: ConfigEntry, relay: dict) -> None:
	options = dict(entry.options or {})
	options[CONF_RELAY] = relay
	hass.config_entries.async_update_entry(entry, options=options)
	await async_start_relay(hass, entry)


def remote_status_payload(hass: HomeAssistant, entry: ConfigEntry) -> dict[str, Any]:
	"""Public status dict for the add-on panel (no secrets)."""
	relay = dict((entry.options or {}).get(CONF_RELAY) or {})
	routes = normalise_routes(relay.get(CONF_RELAY_LAN_ROUTES))
	return {
		"entry_id": entry.entry_id,
		"integration_version": INTEGRATION_VERSION,
		"linked": bool(relay.get(CONF_RELAY_SERVER_ID)),
		"server_id": relay.get(CONF_RELAY_SERVER_ID) or "",
		"forward_ui": bool(relay.get(CONF_RELAY_FORWARD_UI)),
		"lan_routes": routes,
		"lan_max": LAN_MAX_ROUTES,
		"addon_marker": _addon_marker_present(),
	}


def _addon_marker_present() -> bool:
	try:
		from pathlib import Path
		return Path("/config/vome/addon.marker").is_file()
	except OSError:
		return False


def async_register_remote_services(hass: HomeAssistant) -> None:
	"""Register remote-access services (idempotent)."""
	if hass.data.setdefault(DOMAIN, {}).get("_remote_services_registered"):
		return
	hass.data[DOMAIN]["_remote_services_registered"] = True

	async def _get_status(call: ServiceCall) -> ServiceResponse:
		entries = _relay_entries(hass)
		entry_id = call.data.get("entry_id")
		if not entries and not entry_id:
			return {
				"entry_id": "",
				"integration_version": INTEGRATION_VERSION,
				"linked": False,
				"server_id": "",
				"forward_ui": False,
				"lan_routes": [],
				"lan_max": LAN_MAX_ROUTES,
				"addon_marker": _addon_marker_present(),
			}
		# Read-only status must never hard-fail on multiple linked entries:
		# pick the first so the panel loads, and warn. Writes still target a
		# specific entry via entry_id (the panel echoes back the one shown).
		if entry_id:
			entry = _pick_entry(hass, entry_id)
		else:
			entry = entries[0]
		payload = remote_status_payload(hass, entry)
		if len(entries) > 1 and not entry_id:
			payload["warning"] = (
				f"{len(entries)} Vome integrations are linked to this Home "
				"Assistant. Showing the first; remove the extras in Settings "
				"→ Devices & Services to avoid confusion."
			)
		return payload

	async def _set_forward_ui(call: ServiceCall) -> ServiceResponse:
		entry = _pick_entry(hass, call.data.get("entry_id"))
		relay = dict((entry.options or {}).get(CONF_RELAY) or {})
		relay[CONF_RELAY_FORWARD_UI] = bool(call.data.get(CONF_RELAY_FORWARD_UI))
		await _save_relay(hass, entry, relay)
		return remote_status_payload(hass, entry)

	async def _set_lan_routes(call: ServiceCall) -> ServiceResponse:
		entry = _pick_entry(hass, call.data.get("entry_id"))
		relay = dict((entry.options or {}).get(CONF_RELAY) or {})
		relay[CONF_RELAY_LAN_ROUTES] = normalise_routes(call.data.get("routes"))
		await _save_relay(hass, entry, relay)
		return remote_status_payload(hass, entry)

	async def _set_relay_server(call: ServiceCall) -> ServiceResponse:
		"""Point an existing link at a different relay WebSocket URL.

		Scriptable counterpart to the options-flow ``relay_server`` step, for
		aiming a linked Home Assistant at a dev/staging relay without redoing
		the portal device-auth dance. Blank ``ws_url`` resets to the default.
		"""
		entry = _pick_entry(hass, call.data.get("entry_id"))
		relay = dict((entry.options or {}).get(CONF_RELAY) or {})
		ws_url = str(call.data.get("ws_url") or "").strip()
		if ws_url and not (ws_url.startswith("ws://") or ws_url.startswith("wss://")):
			raise ValueError("ws_url must start with ws:// or wss://")
		relay[CONF_RELAY_WS_URL] = ws_url or DEFAULT_RELAY_WS_URL
		await _save_relay(hass, entry, relay)
		return remote_status_payload(hass, entry)

	async def _add_lan_route(call: ServiceCall) -> ServiceResponse:
		entry = _pick_entry(hass, call.data.get("entry_id"))
		relay = dict((entry.options or {}).get(CONF_RELAY) or {})
		routes = normalise_routes(relay.get(CONF_RELAY_LAN_ROUTES))
		if len(routes) >= LAN_MAX_ROUTES:
			raise ValueError("Maximum number of LAN routes reached")
		existing = {r[ROUTE_SLUG] for r in routes}
		route, err = validate_route({
			ROUTE_SLUG: call.data.get(ROUTE_SLUG),
			ROUTE_NAME: call.data.get(ROUTE_NAME),
			ROUTE_HOST: call.data.get(ROUTE_HOST),
			ROUTE_PORT: call.data.get(ROUTE_PORT),
			ROUTE_SCHEME: call.data.get(ROUTE_SCHEME, "http"),
			ROUTE_ENABLED: call.data.get(ROUTE_ENABLED, True),
			ROUTE_WEBSOCKET: call.data.get(ROUTE_WEBSOCKET, True),
		}, existing_slugs=existing)
		if err or route is None:
			raise ValueError(err or "Invalid LAN route")
		relay[CONF_RELAY_LAN_ROUTES] = routes + [route]
		await _save_relay(hass, entry, relay)
		return remote_status_payload(hass, entry)

	async def _remove_lan_route(call: ServiceCall) -> ServiceResponse:
		entry = _pick_entry(hass, call.data.get("entry_id"))
		relay = dict((entry.options or {}).get(CONF_RELAY) or {})
		slug = str(call.data.get(ROUTE_SLUG) or "").strip().lower()
		routes = [
			r for r in normalise_routes(relay.get(CONF_RELAY_LAN_ROUTES))
			if r.get(ROUTE_SLUG) != slug
		]
		relay[CONF_RELAY_LAN_ROUTES] = routes
		await _save_relay(hass, entry, relay)
		return remote_status_payload(hass, entry)

	hass.services.async_register(
		DOMAIN, "get_remote_status", _guard(_get_status),
		schema=vol.Schema({vol.Optional("entry_id"): cv.string}),
		supports_response=SupportsResponse.ONLY,
	)
	hass.services.async_register(
		DOMAIN, "set_forward_ui", _guard(_set_forward_ui),
		schema=vol.Schema({
			vol.Optional("entry_id"): cv.string,
			vol.Required(CONF_RELAY_FORWARD_UI): cv.boolean,
		}),
		supports_response=SupportsResponse.OPTIONAL,
	)
	hass.services.async_register(
		DOMAIN, "set_lan_routes", _guard(_set_lan_routes),
		schema=vol.Schema({
			vol.Optional("entry_id"): cv.string,
			vol.Required("routes"): list,
		}),
		supports_response=SupportsResponse.OPTIONAL,
	)
	hass.services.async_register(
		DOMAIN, "set_relay_server", _set_relay_server,
		schema=vol.Schema({
			vol.Optional("entry_id"): cv.string,
			vol.Optional("ws_url", default=""): cv.string,
		}),
		supports_response=SupportsResponse.OPTIONAL,
	)
	hass.services.async_register(
		DOMAIN, "add_lan_route", _guard(_add_lan_route),
		schema=vol.Schema({
			vol.Optional("entry_id"): cv.string,
			vol.Required(ROUTE_SLUG): cv.string,
			vol.Optional(ROUTE_NAME, default=""): cv.string,
			vol.Required(ROUTE_HOST): cv.string,
			vol.Required(ROUTE_PORT): vol.All(vol.Coerce(int), vol.Range(min=1, max=65535)),
			vol.Optional(ROUTE_SCHEME, default="http"): vol.In(list(LAN_ROUTE_SCHEMES)),
			vol.Optional(ROUTE_ENABLED, default=True): cv.boolean,
			vol.Optional(ROUTE_WEBSOCKET, default=True): cv.boolean,
		}),
		supports_response=SupportsResponse.OPTIONAL,
	)
	hass.services.async_register(
		DOMAIN, "remove_lan_route", _guard(_remove_lan_route),
		schema=vol.Schema({
			vol.Optional("entry_id"): cv.string,
			vol.Required(ROUTE_SLUG): cv.string,
		}),
		supports_response=SupportsResponse.OPTIONAL,
	)

	async def _mint_lan_tcp_token(call: ServiceCall) -> ServiceResponse:
		entry = _pick_entry(hass, call.data.get("entry_id"))
		relay = dict((entry.options or {}).get(CONF_RELAY) or {})
		routes = normalise_routes(relay.get(CONF_RELAY_LAN_ROUTES))
		slug = str(call.data.get(ROUTE_SLUG) or "").strip().lower()
		route = find_route(routes, slug)
		if route is None or route.get(ROUTE_SCHEME) != "tcp":
			raise ValueError(
				f"No enabled tcp-scheme LAN route named '{slug}'. "
				"Add one first (scheme: tcp) via the options flow or the panel."
			)
		client = get_relay_client(hass, entry.entry_id)
		if client is None:
			raise ValueError("Vome relay is not connected for this Home Assistant")
		ttl = int(call.data.get("ttl_seconds") or LAN_TCP_TOKEN_DEFAULT_TTL)
		ttl = max(60, min(ttl, LAN_TCP_TOKEN_MAX_TTL))
		token, error = await client.request_lan_tcp_token(slug, ttl)
		if error:
			raise ValueError(error)
		return {"token": token, "slug": slug, "ttl_seconds": ttl}

	hass.services.async_register(
		DOMAIN, "mint_lan_tcp_token", _guard(_mint_lan_tcp_token),
		schema=vol.Schema({
			vol.Optional("entry_id"): cv.string,
			vol.Required(ROUTE_SLUG): cv.string,
			vol.Optional("ttl_seconds"): vol.Coerce(int),
		}),
		supports_response=SupportsResponse.ONLY,
	)
	_LOGGER.debug("Registered Vome remote-access services")
