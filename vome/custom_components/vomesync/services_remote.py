"""Services for remote access + LAN tunnels (used by the Supervisor add-on panel).

Keeps the HA options flow and the add-on UI on the same code path: both mutate
``options.relay`` and restart the outbound relay via ``async_start_relay``.
"""
from __future__ import annotations

import logging
from ipaddress import ip_address
from typing import Any, Optional
from urllib.parse import urlparse

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
	CONF_RELAY,
	CONF_RELAY_FORWARD_UI,
	CONF_RELAY_LAN_ROUTES,
	CONF_RELAY_LOCAL_URL,
	CONF_RELAY_SECRET,
	CONF_RELAY_SERVER_ID,
	CONF_RELAY_WS_URL,
	DEFAULT_PORTAL_URL,
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
from .relay_client import (
	async_poll_device_token,
	async_request_device_code,
	async_start_relay,
	async_stop_relay,
	describe_local_core_url,
	get_relay_client,
)

_LOGGER = logging.getLogger(__name__)

# In-flight device-authorisation codes, keyed by entry_id, kept between the
# panel's link_start and link_poll calls (the options flow keeps the equivalent
# in its own step data).
_PENDING_LINK_KEY = "_pending_link"


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


def _pick_vome_entry(hass: HomeAssistant, entry_id: Optional[str]) -> ConfigEntry:
	"""Pick any Vome config entry — linked or not (used for linking/unlinking).

	Unlike ``_pick_entry`` this does not require a relay to be configured yet,
	because the whole point of the link services is to set one up.
	"""
	entries = list(hass.config_entries.async_entries(DOMAIN))
	if entry_id:
		for e in entries:
			if e.entry_id == entry_id:
				return e
		raise ValueError(f"No Vome entry with id {entry_id}")
	if len(entries) == 1:
		return entries[0]
	if not entries:
		raise ValueError("The Vome integration isn't set up in Home Assistant yet")
	raise ValueError("Multiple Vome entries; pass entry_id")


def _link_display_name(hass: HomeAssistant) -> str:
	"""Name shown for this HA in the user's Vome account (the HA instance name)."""
	location = getattr(hass.config, "location_name", "") if hass else ""
	if isinstance(location, str) and location.strip():
		return location.strip()
	return "My Home Assistant"


async def _save_relay(hass: HomeAssistant, entry: ConfigEntry, relay: dict) -> None:
	options = dict(entry.options or {})
	options[CONF_RELAY] = relay
	hass.config_entries.async_update_entry(entry, options=options)
	await async_start_relay(hass, entry)


def _trusted_proxy_check(hass: HomeAssistant, local_url: str) -> dict[str, Any]:
	"""Report whether forwarded requests will survive HA's trusted-proxy filter.

	Full-UI forwarding relays the browser's ``X-Forwarded-For`` through to Core,
	and the request arrives from the address we dial.  HA ignores the header
	entirely unless trusted proxies are configured — but 2026.8 turned that into
	a one-click setting on Settings → System → Network, so far more people will
	switch it on, and if our address is not on the list the forwarded request is
	rejected.  ``ok: None`` means we could not tell, which is not a warning.
	"""
	try:
		trusted = list(getattr(getattr(hass, "http", None), "trusted_proxies", None) or [])
	except Exception:  # noqa: BLE001 - a diagnostic must not raise
		return {"ok": None, "hint": ""}

	if not trusted:
		# Nothing configured, so HA is not filtering on the header at all.
		return {"ok": True, "hint": ""}

	host = urlparse(local_url).hostname or ""
	try:
		address = ip_address(host)
	except ValueError:
		# A hostname (from internal_url) — we cannot resolve it to compare here.
		return {"ok": None, "hint": ""}

	for network in trusted:
		try:
			if address in network:
				return {"ok": True, "hint": ""}
		except TypeError:  # noqa: PERF203 - mixed IPv4/IPv6 entries are normal
			continue

	return {
		"ok": False,
		"hint": (
			f"Home Assistant has trusted proxies configured but {host} is not "
			"among them. Remote access through your Vome address will be "
			"refused. Add it under Settings → System → Network."
		),
	}


def remote_status_payload(hass: HomeAssistant, entry: ConfigEntry) -> dict[str, Any]:
	"""Public status dict for the add-on panel (no secrets)."""
	relay = dict((entry.options or {}).get(CONF_RELAY) or {})
	routes = normalise_routes(relay.get(CONF_RELAY_LAN_ROUTES))
	local_url, local_url_source = describe_local_core_url(
		hass, relay.get(CONF_RELAY_LOCAL_URL)
	)
	return {
		"entry_id": entry.entry_id,
		"integration_version": INTEGRATION_VERSION,
		"linked": bool(relay.get(CONF_RELAY_SERVER_ID)),
		"server_id": relay.get(CONF_RELAY_SERVER_ID) or "",
		"forward_ui": bool(relay.get(CONF_RELAY_FORWARD_UI)),
		"lan_routes": routes,
		"lan_max": LAN_MAX_ROUTES,
		"addon_marker": _addon_marker_present(),
		# Which address we dial Home Assistant on, and whether we worked it out
		# or were told. Since 2026.8 the port is a user-facing setting, so this
		# can change under a running relay.
		"local_url": local_url,
		"local_url_source": local_url_source,
		"local_url_override": relay.get(CONF_RELAY_LOCAL_URL) or "",
		"trusted_proxy": _trusted_proxy_check(hass, local_url),
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
			# Not linked yet — still surface the (single) Vome entry's id so the
			# panel can drive the in-app linking services against it.
			vome_entries = list(hass.config_entries.async_entries(DOMAIN))
			# The local URL is a property of this Home Assistant, not of the
			# link, so report it even when unlinked — the panel shows it in the
			# same place either way, and it is worth being able to check the
			# address is right *before* connecting an account.
			local_url, local_url_source = describe_local_core_url(hass, None)
			return {
				"entry_id": vome_entries[0].entry_id if len(vome_entries) == 1 else "",
				"integration_version": INTEGRATION_VERSION,
				"linked": False,
				"server_id": "",
				"forward_ui": False,
				"lan_routes": [],
				"lan_max": LAN_MAX_ROUTES,
				"addon_marker": _addon_marker_present(),
				"local_url": local_url,
				"local_url_source": local_url_source,
				"local_url_override": "",
				"trusted_proxy": _trusted_proxy_check(hass, local_url),
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

	async def _set_local_url(call: ServiceCall) -> ServiceResponse:
		"""Override the address the relay dials Home Assistant on.

		Detection covers the normal cases, but it cannot see every setup — a
		reverse proxy in front of Core, a container with its own networking, a
		certificate that only matches one name.  A blank value clears the
		override and hands the decision back to detection.
		"""
		entry = _pick_entry(hass, call.data.get("entry_id"))
		relay = dict((entry.options or {}).get(CONF_RELAY) or {})
		local_url = str(call.data.get("local_url") or "").strip().rstrip("/")
		if local_url:
			parsed = urlparse(local_url)
			if parsed.scheme not in ("http", "https") or not parsed.hostname:
				raise ValueError(
					"local_url must be a full address like http://127.0.0.1:8123"
				)
			if parsed.path:
				raise ValueError("local_url must not include a path")
			relay[CONF_RELAY_LOCAL_URL] = local_url
		else:
			relay.pop(CONF_RELAY_LOCAL_URL, None)
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
	# NB: the panel calls every service below over REST with ?return_response,
	# and always consumes the returned status dict. They MUST therefore be
	# SupportsResponse.ONLY, not OPTIONAL — an OPTIONAL service invoked with
	# return_response over the REST API is rejected as a bare "400: Bad
	# Request" (no message), which is exactly what the panel was hitting on
	# every write while the ONLY-typed get_remote_status worked fine.
	hass.services.async_register(
		DOMAIN, "set_forward_ui", _guard(_set_forward_ui),
		schema=vol.Schema({
			vol.Optional("entry_id"): cv.string,
			vol.Required(CONF_RELAY_FORWARD_UI): cv.boolean,
		}),
		supports_response=SupportsResponse.ONLY,
	)
	hass.services.async_register(
		DOMAIN, "set_lan_routes", _guard(_set_lan_routes),
		schema=vol.Schema({
			vol.Optional("entry_id"): cv.string,
			vol.Required("routes"): list,
		}),
		supports_response=SupportsResponse.ONLY,
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
		DOMAIN, "set_local_url", _guard(_set_local_url),
		schema=vol.Schema({
			vol.Optional("entry_id"): cv.string,
			vol.Optional("local_url", default=""): cv.string,
		}),
		supports_response=SupportsResponse.ONLY,
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
		supports_response=SupportsResponse.ONLY,
	)
	hass.services.async_register(
		DOMAIN, "remove_lan_route", _guard(_remove_lan_route),
		schema=vol.Schema({
			vol.Optional("entry_id"): cv.string,
			vol.Required(ROUTE_SLUG): cv.string,
		}),
		supports_response=SupportsResponse.ONLY,
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

	# ── In-app account linking (device-authorisation) ────────────────────────
	# Mirror of the options-flow link_vome steps as panel-callable services, so
	# a user can connect this Home Assistant to their Vome account without
	# leaving the add-on. link_start fetches a code; link_poll checks approval
	# and, once granted, stores the relay credentials and starts the relay.

	async def _link_start(call: ServiceCall) -> ServiceResponse:
		entry = _pick_vome_entry(hass, call.data.get("entry_id"))
		if ((entry.options or {}).get(CONF_RELAY) or {}).get(CONF_RELAY_SERVER_ID):
			return {"status": "already_linked", "entry_id": entry.entry_id}
		session = async_get_clientsession(hass)
		started = await async_request_device_code(
			session, DEFAULT_PORTAL_URL, name=_link_display_name(hass)
		)
		pending = {
			"device_code": started.get("device_code"),
			"user_code": started.get("user_code"),
			"verification_uri": started.get("verification_uri")
			or (DEFAULT_PORTAL_URL + "/account/link-ha"),
		}
		if not pending["device_code"]:
			raise ValueError("Vome did not return a device code — try again.")
		hass.data.setdefault(DOMAIN, {}).setdefault(_PENDING_LINK_KEY, {})[entry.entry_id] = pending
		return {
			"status": "started",
			"entry_id": entry.entry_id,
			"user_code": pending["user_code"] or "",
			"verification_uri": pending["verification_uri"],
			"interval": int(started.get("interval") or 5),
			"expires_in": int(started.get("expires_in") or 900),
		}

	async def _link_poll(call: ServiceCall) -> ServiceResponse:
		entry = _pick_vome_entry(hass, call.data.get("entry_id"))
		pending = hass.data.get(DOMAIN, {}).get(_PENDING_LINK_KEY, {}).get(entry.entry_id)
		if not pending or not pending.get("device_code"):
			return {"status": "no_pending"}
		session = async_get_clientsession(hass)
		result = await async_poll_device_token(
			session, DEFAULT_PORTAL_URL, pending["device_code"]
		)
		status = result.get("status")
		if status == "approved":
			relay = {
				CONF_RELAY_SERVER_ID: result.get("server_id"),
				CONF_RELAY_SECRET: result.get("relay_secret"),
				CONF_RELAY_WS_URL: result.get("relay_ws_url") or DEFAULT_RELAY_WS_URL,
			}
			options = dict(entry.options or {})
			options[CONF_RELAY] = relay
			hass.config_entries.async_update_entry(entry, options=options)
			await async_start_relay(hass, entry)
			hass.data.get(DOMAIN, {}).get(_PENDING_LINK_KEY, {}).pop(entry.entry_id, None)
			return {"status": "linked", "server_id": relay[CONF_RELAY_SERVER_ID] or ""}
		if status == "pending":
			return {
				"status": "pending",
				"user_code": pending.get("user_code") or "",
				"verification_uri": pending.get("verification_uri") or "",
			}
		# expired / unknown — drop the stale code so the panel starts over.
		hass.data.get(DOMAIN, {}).get(_PENDING_LINK_KEY, {}).pop(entry.entry_id, None)
		return {"status": "expired"}

	async def _unlink(call: ServiceCall) -> ServiceResponse:
		entry = _pick_vome_entry(hass, call.data.get("entry_id"))
		options = dict(entry.options or {})
		was_linked = bool(options.pop(CONF_RELAY, None))
		hass.config_entries.async_update_entry(entry, options=options)
		await async_stop_relay(hass, entry)
		hass.data.get(DOMAIN, {}).get(_PENDING_LINK_KEY, {}).pop(entry.entry_id, None)
		return {"status": "unlinked", "was_linked": was_linked}

	_link_schema = vol.Schema({vol.Optional("entry_id"): cv.string})
	for _name, _handler in (
		("link_start", _link_start),
		("link_poll", _link_poll),
		("unlink", _unlink),
	):
		hass.services.async_register(
			DOMAIN, _name, _guard(_handler),
			schema=_link_schema, supports_response=SupportsResponse.ONLY,
		)
	_LOGGER.debug("Registered Vome remote-access services")
