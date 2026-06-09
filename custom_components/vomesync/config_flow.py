"""Config flow for VomeSync integration."""
import asyncio
import inspect
import logging
import re
from typing import Any, Dict, Optional, Set

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.selector import selector

from .api_client import VomeSyncAPIClient, VomeSyncAPIError
from .options_flow_links import VomeSyncOptionsFlowLinkEntitiesMixin
from .options_flow_relay import VomeSyncOptionsFlowRelayMixin
from .const import (
	DOMAIN,
	CONF_PERSONAL_KEY,
	CONF_SERVER_URL,
	CONF_WEBSOCKET_URL,
	CONF_AUTH_MODE,
	CONF_CRYPTO_SEED,
	CONF_GENERATE_NEW_KEY,
	CONF_USE_DEFAULT_URLS,
	CONF_ACCESS_KEY,
	AUTH_MODE_CRYPTO,
	DEFAULT_SERVER_URL,
	DEFAULT_WEBSOCKET_URL,
	CONF_SWITCH_UID,
	CONF_SWITCH_NAME,
	CONF_SWITCH_DESCRIPTION,
	CONF_SWITCH_LOCATION,
	CONF_SWITCH_CATEGORY,
	CONF_SWITCH_PUBLICIZE,
	CONF_SWITCH_LINK,
	CONF_SWITCH_ICON_URL,
	CONF_SWITCH_BANNER_URL,
	CONF_CAPTCHA_TOKEN,
	CONF_SWITCH_ADVANCED,
	CONF_SHOW_SIGNING_KEY_AFTER,
	FREE_TIER_MAX_SUBSCRIPTIONS,
	SWITCH_CATEGORIES,
	DEFAULT_SWITCH_NAME,
)

from .crypto import generate_master_seed_b64url, owner_pubkey_b64url
from .time_utils import format_timestamp_ms

_LOGGER = logging.getLogger(__name__)

_WEBSITE_SESSION_DEFAULT_TTL_SECONDS = 4 * 60 * 60
_WEBSITE_SESSION_STAY_TTL_SECONDS = 30 * 24 * 60 * 60
_MAX_SWITCH_NAME_LENGTH = 80
_MAX_DESCRIPTION_LENGTH = 500
_MAX_LOCATION_LENGTH = 100
_MAX_URL_LENGTH = 500


def _normalise_uid(raw_uid: Any) -> str:
	"""Normalise a UID string."""
	return str(raw_uid or "").strip()


def _is_valid_uid(uid: str) -> bool:
	"""Lightweight UID validation (avoid empty/whitespace only)."""
	return bool(uid) and " " not in uid


def _parse_uid_key_composite(value: str) -> tuple:
	"""Parse a uid/key composite string.

	Accepts either:
	  - "uid/key"  → (uid, key)
	  - "uid"      → (uid, "")

	The split is on the *first* '/' only, so UIDs that happen to contain '/'
	are not supported (they don't in practice).
	"""
	value = (value or "").strip()
	if "/" in value:
		uid, _, key = value.partition("/")
		return uid.strip(), key.strip()
	return value, ""


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
		self._generate_new_key: bool = True
		self._use_default_urls: bool = True
		self._initial_switch_uid: Optional[str] = None
		self._initial_switch_access_key: str = ""
		self._pending_switch_uid: str = ""

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

	def _build_user_schema(self, show_advanced: bool) -> vol.Schema:
		"""Build the user step schema, optionally showing advanced fields."""
		schema = {
			vol.Optional(CONF_SWITCH_UID, default=self._pending_switch_uid): str,
			vol.Optional(CONF_GENERATE_NEW_KEY, default=self._generate_new_key): cv.boolean,
			vol.Optional(CONF_USE_DEFAULT_URLS, default=self._use_default_urls): cv.boolean,
		}

		if show_advanced or not self._generate_new_key:
			schema[vol.Optional(CONF_CRYPTO_SEED, default=self._crypto_seed or "")] = str

		if show_advanced or not self._use_default_urls:
			schema[vol.Optional(CONF_SERVER_URL, default=self._server_url)] = str
			schema[vol.Optional(CONF_WEBSOCKET_URL, default=self._websocket_url)] = str

		return vol.Schema(schema)

	async def async_step_user(
		self, user_input: Optional[Dict[str, Any]] = None
	) -> FlowResult:
		"""Handle the initial step."""
		errors = {}
		show_advanced = False

		if user_input is not None:
			self._generate_new_key = bool(user_input.get(CONF_GENERATE_NEW_KEY, True))
			self._use_default_urls = bool(user_input.get(CONF_USE_DEFAULT_URLS, True))
			show_advanced = (not self._generate_new_key) or (not self._use_default_urls)

			raw_switch_input = _normalise_uid(user_input.get(CONF_SWITCH_UID, ""))
			# Support uid/key composite format on initial setup
			parsed_uid, parsed_key = _parse_uid_key_composite(raw_switch_input)
			self._pending_switch_uid = _normalise_uid(parsed_uid)
			self._initial_switch_access_key = parsed_key  # stored for _create_entry
			self._initial_switch_uid = None

			if self._use_default_urls:
				self._server_url = DEFAULT_SERVER_URL
				self._websocket_url = self._derive_websocket_url(self._server_url)

			needs_crypto_input = not self._generate_new_key
			needs_url_input = not self._use_default_urls
			has_crypto_input = CONF_CRYPTO_SEED in user_input
			has_url_input = CONF_SERVER_URL in user_input or CONF_WEBSOCKET_URL in user_input

			if needs_crypto_input and not has_crypto_input:
				return self.async_show_form(
					step_id="user",
					data_schema=self._build_user_schema(True),
					errors=errors,
					description_placeholders={
						"warning": "⚠️ Public mode is NOT private — anyone with the UID can view switch state and activity. Toggling requires an access key.",
						"keypair_hint": "Untick generate key to enter an existing signing key.",
						"websocket_info": "Default URLs use: " + self._derive_websocket_url(DEFAULT_SERVER_URL)
					}
				)

			if needs_url_input and not has_url_input:
				return self.async_show_form(
					step_id="user",
					data_schema=self._build_user_schema(True),
					errors=errors,
					description_placeholders={
						"warning": "⚠️ Public mode is NOT private — anyone with the UID can view switch state and activity. Toggling requires an access key.",
						"keypair_hint": "Untick generate key to enter an existing signing key.",
						"websocket_info": "Default URLs use: " + self._derive_websocket_url(DEFAULT_SERVER_URL)
					}
				)

			if self._use_default_urls:
				self._server_url = DEFAULT_SERVER_URL
				self._websocket_url = self._derive_websocket_url(self._server_url)
			else:
				self._server_url = str(user_input.get(CONF_SERVER_URL, "")).strip()
				if not self._server_url:
					errors[CONF_SERVER_URL] = "missing_server_url"
				if user_input.get(CONF_WEBSOCKET_URL):
					self._websocket_url = user_input[CONF_WEBSOCKET_URL]
				else:
					self._websocket_url = self._derive_websocket_url(self._server_url or DEFAULT_SERVER_URL)

			if self._generate_new_key:
				self._crypto_seed = generate_master_seed_b64url()
			else:
				self._crypto_seed = str(user_input.get(CONF_CRYPTO_SEED, "")).strip()
				if not self._crypto_seed:
					errors[CONF_CRYPTO_SEED] = "missing_signing_key"

			if self._pending_switch_uid:
				if not _is_valid_uid(self._pending_switch_uid):
					errors[CONF_SWITCH_UID] = "invalid_uid"
				elif not errors.get(CONF_SERVER_URL):
					client = VomeSyncAPIClient(self._server_url)
					try:
						status = await client.get_switch_status(self._pending_switch_uid)
					finally:
						await client.close()
					if not status:
						errors[CONF_SWITCH_UID] = "switch_not_found"
					else:
						self._initial_switch_uid = self._pending_switch_uid

			if not errors:
				# Keypair mode is the only supported mode (the integration never launched with personal keys).
				self._auth_mode = AUTH_MODE_CRYPTO
				self._personal_key = ""
				return await self._create_entry()
		else:
			# First load: keep websocket URL in sync with the server URL
			self._websocket_url = self._derive_websocket_url(self._server_url)

		return self.async_show_form(
			step_id="user",
			data_schema=self._build_user_schema(show_advanced),
			errors=errors,
			description_placeholders={
				"warning": "⚠️ Public mode is NOT private — anyone with the UID can view switch state and activity. Toggling requires an access key.",
				"keypair_hint": "Keep your signing key safe if you want to migrate servers later.",
				"websocket_info": "Default URLs use: " + self._derive_websocket_url(DEFAULT_SERVER_URL)
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
		title = "Vome"
		if not self._crypto_seed:
			raise ValueError("crypto_seed missing for keypair auth mode")
		owner_pub = owner_pubkey_b64url(self._crypto_seed)
		unique = owner_pub
		title = f"Vome (keypair {owner_pub[:8]}...)"

		# Check for existing entries (skip if context is immutable in tests)
		try:
			if unique:
				await self.async_set_unique_id(unique)
			self._abort_if_unique_id_configured()
		except TypeError:
			_LOGGER.debug("Skipping unique_id setup (context not mutable in test environment)")

		# Ensure websocket URL stored matches server_url if user left it blank
		websocket_url = self._websocket_url or self._derive_websocket_url(self._server_url)

		data = {
			CONF_PERSONAL_KEY: self._personal_key or "",
			CONF_SERVER_URL: self._server_url,
			CONF_WEBSOCKET_URL: websocket_url,
			CONF_AUTH_MODE: self._auth_mode,
			CONF_CRYPTO_SEED: self._crypto_seed or "",
		}
		if self._initial_switch_uid:
			data[CONF_SWITCH_UID] = self._initial_switch_uid
			# If a composite uid/key was pasted, pass the access key too
			initial_key = getattr(self, "_initial_switch_access_key", "")
			if initial_key:
				data["initial_access_key"] = initial_key

		return self.async_create_entry(
			title=title,
			data=data
		)

	@staticmethod
	@callback
	def async_get_options_flow(config_entry: config_entries.ConfigEntry) -> "VomeSyncOptionsFlow":
		"""Create the options flow."""
		return VomeSyncOptionsFlow(config_entry)


class VomeSyncOptionsFlow(
	config_entries.OptionsFlow,
	VomeSyncOptionsFlowLinkEntitiesMixin,
	VomeSyncOptionsFlowRelayMixin,
):
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

	def _crypto_enabled(self) -> bool:
		"""Whether this entry is configured for v2 crypto auth."""
		return bool(
			self._config_entry.data.get(CONF_AUTH_MODE) == AUTH_MODE_CRYPTO
			and self._config_entry.data.get(CONF_CRYPTO_SEED)
		)

	def _default_location_hint(self) -> str:
		"""Best-effort default location using HA config name."""
		location = getattr(self.hass.config, "location_name", "") if self.hass else ""
		if not isinstance(location, str):
			return ""
		location = location.strip()
		if not location:
			return ""
		if location.lower() in {"home", "house"}:
			return ""
		return location

	def _extract_invalid_fields(self, error_detail: str) -> Set[str]:
		"""Extract invalid field names from an API error message."""
		if not error_detail:
			return set()
		fields = set(re.findall(r'"([^"]+)"', error_detail))
		if not fields:
			for candidate in ("name", "description", "location", "category", "publicize", "link", "iconUrl", "bannerUrl"):
				if candidate in error_detail:
					fields.add(candidate)
		return fields

	def _apply_invalid_defaults(self, defaults: Dict[str, Any], invalid_fields: Set[str]) -> Dict[str, Any]:
		"""Blank only the invalid fields so other values are preserved."""
		field_map = {
			"name": CONF_SWITCH_NAME,
			"description": "description",
			"location": "location",
			"category": "category",
			"publicize": "publicize",
			"link": CONF_SWITCH_LINK,
			"iconUrl": CONF_SWITCH_ICON_URL,
			"bannerUrl": CONF_SWITCH_BANNER_URL
		}
		for field in invalid_fields:
			form_key = field_map.get(field)
			if not form_key or form_key not in defaults:
				continue
			if isinstance(defaults[form_key], bool):
				defaults[form_key] = False
			else:
				defaults[form_key] = ""
		return defaults

	def _generate_switch_name_fallback(self) -> str:
		"""Generate a numbered fallback name when server is unreachable."""
		# Count existing switches to generate a unique number
		options = self._config_entry.options or {}
		existing_count = len(options.get("imported_switches", {}))
		return f"Vome Switch {existing_count + 1}"

	async def _fetch_next_switch_name(self) -> str:
		"""Fetch a globally unique switch name from the server."""
		try:
			coordinator = self.hass.data.get(DOMAIN, {}).get(self._config_entry.entry_id)
			if coordinator and hasattr(coordinator, "api_client"):
				name = await coordinator.api_client.get_next_switch_name()
				if name:
					return name
		except Exception as ex:
			_LOGGER.debug("Failed to fetch switch name from server: %s", ex)
		# Fallback to numbered name
		return self._generate_switch_name_fallback()

	def _is_signing_key_backup_confirmed(self) -> bool:
		"""Check whether the user confirmed backing up their signing key."""
		options = self._config_entry.options or {}
		return bool(options.get("signing_key_backup_confirmed"))

	def _signing_key_description(self) -> str:
		"""Build the signing key backup description."""
		signing_key = self._config_entry.data.get(CONF_CRYPTO_SEED, "")
		server_url = self._config_entry.data.get(CONF_SERVER_URL, "")
		return (
			"Back up your signing key and keep it safe.\n\n"
			f"Server: `{server_url}`\n\n"
			f"Signing key:\n`{signing_key}`"
		)

	def _build_create_switch_schema(
		self,
		default_name: str = "Vome Switch",
		defaults: Optional[Dict[str, Any]] = None,
	) -> vol.Schema:
		"""Base create-switch schema."""
		values = defaults or {}
		default_location = values.get(CONF_SWITCH_LOCATION)
		if default_location is None:
			default_location = self._default_location_hint()
		return vol.Schema({
			vol.Required(CONF_SWITCH_NAME, default=values.get(CONF_SWITCH_NAME, default_name)): vol.All(
				str, vol.Length(max=_MAX_SWITCH_NAME_LENGTH)
			),
			vol.Optional(CONF_SWITCH_DESCRIPTION, default=values.get(CONF_SWITCH_DESCRIPTION, "")): vol.All(
				str, vol.Length(max=_MAX_DESCRIPTION_LENGTH)
			),
			vol.Optional(CONF_SWITCH_LOCATION, default=default_location): vol.All(
				str, vol.Length(max=_MAX_LOCATION_LENGTH)
			),
			vol.Optional(CONF_SWITCH_CATEGORY, default=values.get(CONF_SWITCH_CATEGORY, "Other")): selector({
				"select": {
					"options": SWITCH_CATEGORIES,
					"mode": "dropdown",
				}
			}),
			vol.Optional(CONF_SWITCH_PUBLICIZE, default=bool(values.get(CONF_SWITCH_PUBLICIZE, False))): bool,
			vol.Optional(CONF_SWITCH_ADVANCED, default=bool(values.get(CONF_SWITCH_ADVANCED, False))): cv.boolean,
			vol.Optional(CONF_SHOW_SIGNING_KEY_AFTER, default=bool(values.get(CONF_SHOW_SIGNING_KEY_AFTER, False))): cv.boolean,
		})

	def _build_create_switch_advanced_schema(self) -> vol.Schema:
		"""Advanced metadata fields for create-switch."""
		return vol.Schema({
			vol.Optional(CONF_SWITCH_LINK, default=""): vol.All(str, vol.Length(max=_MAX_URL_LENGTH)),
			vol.Optional(CONF_SWITCH_ICON_URL, default=""): vol.All(str, vol.Length(max=_MAX_URL_LENGTH)),
			vol.Optional(CONF_SWITCH_BANNER_URL, default=""): vol.All(str, vol.Length(max=_MAX_URL_LENGTH)),
			# Ensure this renders as a normal text field (not a password box)
			vol.Optional(CONF_CAPTCHA_TOKEN, default=""): selector({"text": {"type": "text"}}),
		})

	async def _create_switch_from_data(
		self,
		base_data: Dict[str, Any],
		advanced_data: Optional[Dict[str, Any]] = None,
		show_signing_key_after: bool = False,
	) -> FlowResult:
		"""Create switch from base + advanced data."""
		errors: Dict[str, str] = {}
		error_detail = ""
		try:
			coordinator = self.hass.data[DOMAIN][self._config_entry.entry_id]
			payload = dict(base_data or {})
			payload.update(advanced_data or {})
			switch_name = payload[CONF_SWITCH_NAME]
			uid = await coordinator.create_switch(
				name=switch_name,
				description=payload.get(CONF_SWITCH_DESCRIPTION, ""),
				location=payload.get(CONF_SWITCH_LOCATION, ""),
				category=payload.get(CONF_SWITCH_CATEGORY, "Other"),
				publicize=payload.get(CONF_SWITCH_PUBLICIZE, False),
				link=payload.get(CONF_SWITCH_LINK, ""),
				icon_url=(payload.get(CONF_SWITCH_ICON_URL) or None),
				banner_url=(payload.get(CONF_SWITCH_BANNER_URL) or None),
				captcha_token=payload.get(CONF_CAPTCHA_TOKEN, ""),
			)
			if uid:
				self._step_data["selected_uid"] = uid
				self._step_data["selected_name"] = switch_name
				self._step_data["is_owner"] = True
				self._step_data["has_access_key"] = False
				if show_signing_key_after and self._crypto_enabled():
					self._step_data["post_create_manage_uid"] = uid
					self._step_data["post_create_manage_name"] = switch_name
					return await self.async_step_post_create_signing_key()
				return self._show_manage_switch_action_menu(uid, switch_name, True)
			errors["base"] = "create_failed"
		except VomeSyncAPIError as ex:
			_LOGGER.error(
				"Failed to create switch (server=%s, websocket=%s): %s",
				self._config_entry.data[CONF_SERVER_URL],
				self._config_entry.data[CONF_WEBSOCKET_URL],
				ex
			)
			error_detail = str(ex)
			errors["base"] = "create_failed"
		except Exception as ex:
			_LOGGER.error("Failed to create switch (unexpected): %s", ex)
			error_detail = str(ex)
			errors["base"] = "create_failed"

		# On error, preserve the name they entered
		attempted_name = base_data.get(CONF_SWITCH_NAME) or self._generate_switch_name_fallback()
		defaults = dict(base_data or {})
		return self.async_show_form(
			step_id="create_switch",
			data_schema=self._build_create_switch_schema(attempted_name, defaults=defaults),
			errors=errors,
			description_placeholders={"error": f"Error: {error_detail}" if error_detail else ""},
		)

	def _build_manage_switch_actions(self, uid: str, is_owner: bool) -> list[str]:
		actions = ["view_switch", "link_entities"]
		if is_owner:
			actions.append("edit_switch")
			# v2-only delegation (access keys + website management)
			if self._crypto_enabled() and isinstance(uid, str) and uid.startswith("vs_"):
				actions.append("access_keys")
				actions.append("manage_on_website")
			actions.append("delete_switch")
		# Everyone can remove from this installation
		actions.append("remove_from_installation")
		return actions

	def _resolve_entity_id_for_uid(self, uid: str) -> Optional[str]:
		"""Best-effort entity_id lookup for UI context (non-fatal)."""
		try:
			entity_reg = er.async_get(self.hass)
			entity_id = entity_reg.async_get_entity_id("switch", DOMAIN, f"vomesync_{uid}")
			if entity_id:
				return entity_id
			for entity in entity_reg.entities.values():
				if entity.config_entry_id == self._config_entry.entry_id and entity.unique_id == f"vomesync_{uid}":
					return entity.entity_id
			return None
		except Exception as ex:  # noqa: BLE001
			_LOGGER.debug("Failed to resolve entity_id for %s: %s", uid, ex)
			return None

	def _show_manage_switch_action_menu(self, uid: str, name: str, is_owner: bool) -> FlowResult:
		actions = self._build_manage_switch_actions(uid, is_owner)
		entity_id = self._resolve_entity_id_for_uid(uid)
		uid_hint = uid[-6:] if isinstance(uid, str) and len(uid) >= 6 else uid
		return self.async_show_menu(
			step_id="manage_switch_action",
			menu_options=actions,
			description_placeholders={
				"name": name,
				"uid": uid,
				"uid_hint": uid_hint,
				"entity_id": entity_id or "Not created yet",
			},
		)

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
			"backup_signing_key",
			"import_switches",
			"reannounce_owned_switches",
			"cleanup_orphaned_devices",
			"edit_connection",
		]
		# Connect this Home Assistant to a Vome account over the outbound relay.
		menu_options.append("unlink_vome" if self._relay_is_linked() else "link_vome")
		menu_options.append("back")
		return self.async_show_menu(step_id="more", menu_options=menu_options)

	async def async_step_back(
		self, user_input: Optional[Dict[str, Any]] = None
	) -> FlowResult:
		"""Return to the main options menu."""
		return await self.async_step_init()

	async def async_step_backup_signing_key(
		self, user_input: Optional[Dict[str, Any]] = None
	) -> FlowResult:
		"""Show the signing key for backup."""
		if not self._crypto_enabled():
			return self.async_abort(reason="crypto_required")
		
		if user_input is not None:
			return self.async_create_entry(title="", data=dict(self._config_entry.options or {}))
		
		signing_key = self._config_entry.data.get(CONF_CRYPTO_SEED, "")
		server_url = self._config_entry.data.get(CONF_SERVER_URL, "")
		description = (
			"Save this signing key somewhere safe. You need it to migrate or restore your VomeSync switches.\n\n"
			f"Server: `{server_url}`\n\n"
			f"Signing key:\n`{signing_key}`"
		)
		
		return self.async_show_form(
			step_id="backup_signing_key",
			data_schema=vol.Schema({}),
			description_placeholders={"info": description},
		)

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
							"name": switch_data.get("name") or DEFAULT_SWITCH_NAME,
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
				name = switch_data.get("name") or DEFAULT_SWITCH_NAME
				status = " (already imported)" if uid in already_imported else ""
				available_switches[uid] = f"[OWNED] {name}{status}"
			
			# Add subscriptions
			for uid, sub_data in coordinator.subscriptions.items():
				if uid not in available_switches:  # Don't duplicate
					name = sub_data.get("name") or DEFAULT_SWITCH_NAME
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
		if user_input is None and self._crypto_enabled() and not self._is_signing_key_backup_confirmed():
			return await self.async_step_confirm_backup_signing_key()

		if user_input is not None:
			base_data = dict(user_input)
			advanced = bool(base_data.pop(CONF_SWITCH_ADVANCED, False))
			show_signing_key_after = bool(base_data.pop(CONF_SHOW_SIGNING_KEY_AFTER, False))
			if advanced:
				self._step_data["create_switch_base"] = base_data
				self._step_data["create_switch_show_signing_key_after"] = show_signing_key_after
				return await self.async_step_create_switch_advanced()
			return await self._create_switch_from_data(base_data, show_signing_key_after=show_signing_key_after)

		# Fetch a globally unique name from the server
		default_name = await self._fetch_next_switch_name()

		return self.async_show_form(
			step_id="create_switch",
			data_schema=self._build_create_switch_schema(default_name),
			errors={},
			description_placeholders={"error": ""},
		)

	async def async_step_confirm_backup_signing_key(
		self, user_input: Optional[Dict[str, Any]] = None
	) -> FlowResult:
		"""Prompt for signing key backup before first switch creation."""
		if not self._crypto_enabled():
			return self.async_abort(reason="crypto_required")

		return self.async_show_menu(
			step_id="confirm_backup_signing_key",
			menu_options=["reveal_signing_key", "confirm_backup_signing_key_done"]
		)

	async def async_step_confirm_backup_signing_key_done(
		self, user_input: Optional[Dict[str, Any]] = None
	) -> FlowResult:
		"""Record confirmation and continue to switch creation."""
		if not self._crypto_enabled():
			return self.async_abort(reason="crypto_required")

		options = dict(self._config_entry.options or {})
		options["signing_key_backup_confirmed"] = True
		await self._async_update_entry_options(options)
		return await self.async_step_create_switch()

	async def async_step_reveal_signing_key(
		self, user_input: Optional[Dict[str, Any]] = None
	) -> FlowResult:
		"""Show the signing key and require backup confirmation."""
		if not self._crypto_enabled():
			return self.async_abort(reason="crypto_required")

		errors: Dict[str, str] = {}
		if user_input is not None:
			confirmed = bool(user_input.get("confirmed", False))
			if confirmed:
				options = dict(self._config_entry.options or {})
				options["signing_key_backup_confirmed"] = True
				await self._async_update_entry_options(options)
				return await self.async_step_create_switch()
			errors["base"] = "backup_required"

		description = self._signing_key_description()

		return self.async_show_form(
			step_id="reveal_signing_key",
			data_schema=vol.Schema({
				vol.Optional("confirmed", default=False): cv.boolean,
			}),
			errors=errors,
			description_placeholders={"info": description},
		)

	async def async_step_post_create_signing_key(
		self, user_input: Optional[Dict[str, Any]] = None
	) -> FlowResult:
		"""Show signing key after creating a switch (optional)."""
		if not self._crypto_enabled():
			return self.async_create_entry(title="", data=dict(self._config_entry.options or {}))

		if user_input is not None:
			uid = self._step_data.pop("post_create_manage_uid", None)
			name = self._step_data.pop("post_create_manage_name", None)
			if uid and name:
				self._step_data["selected_uid"] = uid
				self._step_data["selected_name"] = name
				self._step_data["is_owner"] = True
				self._step_data["has_access_key"] = False
				return self._show_manage_switch_action_menu(uid, name, True)
			return self.async_create_entry(title="", data=dict(self._config_entry.options or {}))

		return self.async_show_form(
			step_id="post_create_signing_key",
			data_schema=vol.Schema({}),
			description_placeholders={"info": self._signing_key_description()},
		)

	async def async_step_create_switch_advanced(
		self, user_input: Optional[Dict[str, Any]] = None
	) -> FlowResult:
		"""Advanced fields for creating a switch."""
		base_data = self._step_data.get("create_switch_base")
		if not isinstance(base_data, dict):
			return await self.async_step_create_switch()

		if user_input is not None:
			self._step_data.pop("create_switch_base", None)
			show_signing_key_after = bool(self._step_data.pop("create_switch_show_signing_key_after", False))
			return await self._create_switch_from_data(
				base_data,
				user_input,
				show_signing_key_after=show_signing_key_after
			)

		return self.async_show_form(
			step_id="create_switch_advanced",
			data_schema=self._build_create_switch_advanced_schema(),
			errors={},
			description_placeholders={
				"info": "Optional website fields. Leave blank to skip."
			}
		)

	async def async_step_subscribe_switch(
		self, user_input: Optional[Dict[str, Any]] = None
	) -> FlowResult:
		"""Subscribe to an existing switch."""
		errors = {}

		if user_input is not None:
			try:
				raw_uid = _normalise_uid(user_input.get(CONF_SWITCH_UID))
				# Support uid/key composite format
				uid, composite_key = _parse_uid_key_composite(raw_uid)
				uid = _normalise_uid(uid)
				if not _is_valid_uid(uid):
					raise ValueError("invalid uid")
				options = self._config_entry.options or {}
				imported_switches = options.get("imported_switches", {}) or {}
				subscription_count = sum(
					1 for info in imported_switches.values()
					if isinstance(info, dict) and not info.get("is_owner", False)
				)
				if subscription_count >= FREE_TIER_MAX_SUBSCRIPTIONS:
					errors["base"] = "subscription_limit_reached"
				else:
					# Use composite key if present, otherwise fall back to the explicit field
					access_key = composite_key or str(user_input.get(CONF_ACCESS_KEY, "") or "").strip()
					
					# Get coordinator
					coordinator = self.hass.data[DOMAIN][self._config_entry.entry_id]
					
					# Subscribe via coordinator (handles API check + dynamic entity addition)
					success = await coordinator.subscribe_to_switch(uid, access_key=access_key)
					
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
			vol.Optional(CONF_ACCESS_KEY, default=""): str,
		})

		return self.async_show_form(
			step_id="subscribe_switch",
			data_schema=data_schema,
			errors=errors,
			description_placeholders={
				"uid_info": "Enter a switch UID, or paste a uid/key composite (e.g. vs_abc123/your-access-key). Access keys allow toggling. Find public switches at sync.vome.io"
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
			name = switch_info.get("name") or DEFAULT_SWITCH_NAME
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
			selected_name = switch_info.get("name") or DEFAULT_SWITCH_NAME
			is_owner = switch_info.get("is_owner", False)
			has_access_key = bool(str(switch_info.get("access_key", "") or "").strip())
			
			self._step_data["selected_uid"] = selected_uid
			self._step_data["selected_name"] = selected_name
			self._step_data["is_owner"] = is_owner
			self._step_data["has_access_key"] = has_access_key
			return self._show_manage_switch_action_menu(selected_uid, selected_name, is_owner)
		
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
			return self.async_create_entry(title="", data=dict(self._config_entry.options or {}))

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
				return self.async_create_entry(title="", data=dict(self._config_entry.options or {}))

		# Build the schema
		schema_dict = {}
		if api_keys:
			key_options = {k["apiKey"]: f"{k.get('name', 'Unnamed')} ({k['apiKey'][:8]}...)" for k in api_keys if k.get("apiKey")}
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
				api_key_value = new_key.get("apiKey", "") if isinstance(new_key, dict) else str(new_key)
				self._step_data["new_api_key"] = api_key_value
				return self.async_show_form(
					step_id="create_api_key_success",
					data_schema=vol.Schema({
						vol.Required("api_key", default=api_key_value): str
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
			if not is_owner:
				options = dict(self._config_entry.options or {})
				imported_switches = options.get("imported_switches", {}) or {}
				info = dict(imported_switches.get(selected_uid, {}))
				access_key = str(user_input.get(CONF_ACCESS_KEY, "") or "").strip()
				if access_key:
					info["access_key"] = access_key
				else:
					info.pop("access_key", None)
				if info:
					imported_switches[selected_uid] = info
					options["imported_switches"] = imported_switches
					await self._async_update_entry_options(options)
					await self.hass.config_entries.async_reload(self._config_entry.entry_id)
			return self.async_create_entry(title="", data=dict(self._config_entry.options or {}))
		
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

		# Website deep link (v2-friendly)
		server_url = self._config_entry.data.get(CONF_SERVER_URL, "").rstrip("/")
		website_url = f"{server_url}/switch/{selected_uid}" if server_url else ""
		
		# Build schema fields (treated as read-only; we ignore user_input)
		# Defaults encourage HA to show copy icons.
		schema_fields = {
			vol.Required("uid", default=selected_uid): str,
			vol.Required("websocket_url", default=websocket_full): str,
		}

		if website_url:
			schema_fields[vol.Required("website_url", default=website_url)] = str
		
		# Add entity_id if found
		if entity_id:
			schema_fields[vol.Required("entity_id", default=entity_id)] = str
		
		if is_owner:
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
			
			last_toggled = format_timestamp_ms(switch_config.get("lastToggled"))
			created_at = format_timestamp_ms(switch_config.get("createdAt"))

			description = f"""**{selected_name}** (Owner)

**Description:** {switch_config.get('description', 'None')}
**Location:** {switch_config.get('location', 'None')}
**Category:** {switch_config.get('category', 'Other')}
**Last toggled:** {last_toggled or "Unknown"}
**Created at:** {created_at or "Unknown"}
**Publicise:** {"Yes" if switch_config.get('publicize', False) else "No"}{device_settings_note}

**Website:** {website_url or "Not configured"}

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
			
			current_access_key = ""
			options = self._config_entry.options or {}
			info = (options.get("imported_switches", {}) or {}).get(selected_uid, {})
			if isinstance(info, dict):
				current_access_key = str(info.get("access_key", "") or "")
			
			schema_fields[vol.Optional(CONF_ACCESS_KEY, default=current_access_key)] = str
			
			last_toggled = format_timestamp_ms(switch_config.get("lastToggled"))
			created_at = format_timestamp_ms(switch_config.get("createdAt"))

			description = f"""**{selected_name}** (Subscribed){device_settings_note}

**Last toggled:** {last_toggled or "Unknown"}
**Created at:** {created_at or "Unknown"}

Provide an access key to enable toggling from this Home Assistant instance."""
		
		return self.async_show_form(
			step_id="view_switch",
			data_schema=vol.Schema(schema_fields),
			description_placeholders={"info": description}
		)

	async def async_step_manage_on_website(
		self, user_input: Optional[Dict[str, Any]] = None
	) -> FlowResult:
		"""Create a delegated access key and show a website management link (v2 only)."""
		selected_uid = self._step_data.get("selected_uid")
		selected_name = self._step_data.get("selected_name", selected_uid)
		is_owner = self._step_data.get("is_owner", False)
		stay_default = bool(self._step_data.get("manage_on_website_stay_logged_in", False))

		if not is_owner:
			return self.async_abort(reason="not_owner")
		if not isinstance(selected_uid, str) or not selected_uid.startswith("vs_"):
			return self.async_abort(reason="not_v2_switch")
		if not (
			self._config_entry.data.get(CONF_AUTH_MODE) == AUTH_MODE_CRYPTO
			and self._config_entry.data.get(CONF_CRYPTO_SEED)
		):
			return self.async_abort(reason="crypto_required")

		# On submit, optionally regenerate the key, otherwise return to the action menu
		if user_input is not None:
			stay_logged_in = bool(user_input.get("stay_logged_in", stay_default))
			if user_input.get("regenerate") or stay_logged_in != stay_default:
				self._step_data.pop("manage_on_website_key", None)
				self._step_data["manage_on_website_stay_logged_in"] = stay_logged_in
			else:
				return self._show_manage_switch_action_menu(selected_uid, selected_name, is_owner)
		else:
			stay_logged_in = stay_default

		coordinator = self.hass.data[DOMAIN][self._config_entry.entry_id]
		server_url = (self._config_entry.data.get(CONF_SERVER_URL, "") or "").rstrip("/")
		ttl_seconds = _WEBSITE_SESSION_STAY_TTL_SECONDS if stay_logged_in else _WEBSITE_SESSION_DEFAULT_TTL_SECONDS
		ttl_label = "30 days" if stay_logged_in else "4 hours"

		created = await coordinator.create_v2_access_key(
			selected_uid,
			name=f"website_session:{selected_name}" if selected_name else "website_session",
			permissions=["metadata", "toggle", "comment"],
			ttl_seconds=ttl_seconds,
		)
		api_key = created.get("apiKey", "") if isinstance(created, dict) else ""
		self._step_data["manage_on_website_key"] = api_key
		website_url = f"{server_url}/switch/{selected_uid}" if server_url else ""
		remember_suffix = f"&remember=1&ttlSeconds={ttl_seconds}" if stay_logged_in else ""
		management_url = f"{website_url}#accessKey={api_key}{remember_suffix}" if (website_url and api_key) else website_url

		link_md = f"[Open website management page]({management_url})" if management_url else ""
		info = (
			f"{link_md}\n\n"
			f"`{management_url}`\n\n"
			f"Access key:\n`{api_key}`\n\n"
			f"Session duration: **{ttl_label}**\n\n"
			"Tip: the key is stored in the URL fragment (#…), so it is not sent to the server in requests.\n\n"
			"This key grants **metadata, toggle, and comment** permissions for this switch.\n"
			"Tick **Regenerate** and submit to issue a new key.\n"
		)

		return self.async_show_form(
			step_id="manage_on_website",
			data_schema=vol.Schema({
				vol.Optional("regenerate", default=False): cv.boolean,
				vol.Optional("stay_logged_in", default=stay_logged_in): cv.boolean,
			}),
			description_placeholders={"info": info}
		)

	async def async_step_edit_switch(
		self, user_input: Optional[Dict[str, Any]] = None
	) -> FlowResult:
		"""Edit switch settings."""
		selected_uid = self._step_data.get("selected_uid")
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
		error_detail = ""
		
		if user_input is not None:
			# Only send changed fields to the server (important: avoids re-triggering CAPTCHA when already public)
			updates: Dict[str, Any] = {}

			if user_input.get(CONF_SWITCH_NAME, "") != (switch_config.get("name", "") if isinstance(switch_config, dict) else ""):
				updates["name"] = user_input.get(CONF_SWITCH_NAME, "")
			if user_input.get("description", "") != (switch_config.get("description", "") if isinstance(switch_config, dict) else ""):
				updates["description"] = user_input.get("description", "")
			if user_input.get("location", "") != (switch_config.get("location", "") if isinstance(switch_config, dict) else ""):
				updates["location"] = user_input.get("location", "")
			if user_input.get("category", "Other") != (switch_config.get("category", "Other") if isinstance(switch_config, dict) else "Other"):
				updates["category"] = user_input.get("category", "Other")

			new_publicize = bool(user_input.get("publicize", False))
			old_publicize = bool(switch_config.get("publicize", False)) if isinstance(switch_config, dict) else False
			if new_publicize != old_publicize:
				updates["publicize"] = new_publicize

			if user_input.get(CONF_SWITCH_LINK, "") != (switch_config.get("link", "") if isinstance(switch_config, dict) else ""):
				updates["link"] = user_input.get(CONF_SWITCH_LINK, "")

			new_icon = user_input.get(CONF_SWITCH_ICON_URL, "")
			old_icon = switch_config.get("iconUrl", "") if isinstance(switch_config, dict) else ""
			if new_icon != old_icon:
				updates["iconUrl"] = new_icon

			new_banner = user_input.get(CONF_SWITCH_BANNER_URL, "")
			old_banner = switch_config.get("bannerUrl", "") if isinstance(switch_config, dict) else ""
			if new_banner != old_banner:
				updates["bannerUrl"] = new_banner

			captcha_token = user_input.get(CONF_CAPTCHA_TOKEN, "")
			if updates.get("publicize") is True and not captcha_token:
				errors["base"] = "captcha_required"
			else:
				# Apply update to server, then persist updated cache in coordinator/options
				if updates:
					try:
						updated = await coordinator.update_switch_metadata(selected_uid, updates, captcha_token=captcha_token)
					except VomeSyncAPIError as ex:
						error_detail = str(ex)
						errors["base"] = "update_failed"
						updated = None
					if not updated and not errors.get("base"):
						error_detail = "Update rejected by server. Check name/description length and allowed characters."
						errors["base"] = "update_failed"

				if not errors:
					# IMPORTANT: return current options so the options flow doesn't overwrite them with {}
					return self.async_create_entry(title="", data=dict(self._config_entry.options or {}))
		
		name_default = switch_config.get("name", "")
		desc_default = switch_config.get("description", "")
		loc_default = switch_config.get("location", "")
		cat_default = switch_config.get("category", "Other")
		pub_default = switch_config.get("publicize", False)
		link_default = switch_config.get("link", "")
		icon_default = switch_config.get("iconUrl", "")
		banner_default = switch_config.get("bannerUrl", "")
		if user_input:
			name_default = user_input.get(CONF_SWITCH_NAME, name_default)
			desc_default = user_input.get("description", desc_default)
			loc_default = user_input.get("location", loc_default)
			cat_default = user_input.get("category", cat_default)
			pub_default = user_input.get("publicize", pub_default)
			link_default = user_input.get(CONF_SWITCH_LINK, link_default)
			icon_default = user_input.get(CONF_SWITCH_ICON_URL, icon_default)
			banner_default = user_input.get(CONF_SWITCH_BANNER_URL, banner_default)

		invalid_fields = self._extract_invalid_fields(error_detail)
		defaults = {
			CONF_SWITCH_NAME: name_default,
			"description": desc_default,
			"location": loc_default,
			"category": cat_default,
			"publicize": pub_default,
			CONF_SWITCH_LINK: link_default,
			CONF_SWITCH_ICON_URL: icon_default,
			CONF_SWITCH_BANNER_URL: banner_default
		}
		defaults = self._apply_invalid_defaults(defaults, invalid_fields)
		name_default = defaults[CONF_SWITCH_NAME]
		desc_default = defaults["description"]
		loc_default = defaults["location"]
		cat_default = defaults["category"]
		pub_default = defaults["publicize"]
		link_default = defaults[CONF_SWITCH_LINK]
		icon_default = defaults[CONF_SWITCH_ICON_URL]
		banner_default = defaults[CONF_SWITCH_BANNER_URL]

		data_schema = vol.Schema({
			vol.Optional(CONF_SWITCH_NAME, default=name_default): vol.All(
				str, vol.Length(max=_MAX_SWITCH_NAME_LENGTH)
			),
			vol.Optional("description", default=desc_default): vol.All(
				str, vol.Length(max=_MAX_DESCRIPTION_LENGTH)
			),
			vol.Optional("location", default=loc_default): vol.All(
				str, vol.Length(max=_MAX_LOCATION_LENGTH)
			),
			vol.Optional("category", default=cat_default): selector({
				"select": {
					"options": SWITCH_CATEGORIES,
					"mode": "dropdown",
				}
			}),
			vol.Optional("publicize", default=pub_default): bool,
			vol.Optional(CONF_SWITCH_LINK, default=link_default): vol.All(str, vol.Length(max=_MAX_URL_LENGTH)),
			vol.Optional(CONF_SWITCH_ICON_URL, default=icon_default): vol.All(str, vol.Length(max=_MAX_URL_LENGTH)),
			vol.Optional(CONF_SWITCH_BANNER_URL, default=banner_default): vol.All(str, vol.Length(max=_MAX_URL_LENGTH)),
			# Ensure this renders as a normal text field (not a password box)
			vol.Optional(CONF_CAPTCHA_TOKEN, default=""): selector({"text": {"type": "text"}}),
		})
		
		return self.async_show_form(
			step_id="edit_switch",
			data_schema=data_schema,
			errors=errors,
			description_placeholders={"error": f"\n\nError: {error_detail}" if error_detail else ""}
		)

	def _access_keys_guard(self) -> Optional[FlowResult]:
		"""Check preconditions for access key steps; returns abort result or None."""
		selected_uid = self._step_data.get("selected_uid")
		is_owner = self._step_data.get("is_owner", False)
		if not is_owner:
			return self.async_abort(reason="not_owner")
		if not isinstance(selected_uid, str) or not selected_uid.startswith("vs_"):
			return self.async_abort(reason="not_v2_switch")
		if not (
			self._config_entry.data.get(CONF_AUTH_MODE) == AUTH_MODE_CRYPTO
			and self._config_entry.data.get(CONF_CRYPTO_SEED)
		):
			return self.async_abort(reason="crypto_required")
		return None

	@staticmethod
	def _format_ts(ts_val) -> str:
		"""Format a timestamp (ms or s) into a human-readable string."""
		import datetime
		if ts_val is None:
			return "never"
		try:
			ts_num = int(ts_val)
			if ts_num > 1e12:
				ts_num = ts_num // 1000
			return datetime.datetime.fromtimestamp(ts_num, tz=datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
		except (ValueError, TypeError, OSError):
			return str(ts_val)

	async def _fetch_access_keys(self) -> list:
		"""Fetch access keys for the selected switch."""
		selected_uid = self._step_data.get("selected_uid")
		coordinator = self.hass.data[DOMAIN][self._config_entry.entry_id]
		resp = await coordinator.list_v2_access_keys(selected_uid)
		return resp.get("keys", []) if isinstance(resp, dict) else []

	async def async_step_access_keys(
		self, user_input: Optional[Dict[str, Any]] = None
	) -> FlowResult:
		"""Manage delegated v2 access keys – list keys to select or create new."""
		guard = self._access_keys_guard()
		if guard is not None:
			return guard

		# If user selected a key from the list, go to its detail page
		if user_input is not None:
			selected = user_input.get("selected_key")
			if selected == "__create__":
				return await self.async_step_create_access_key_v2()
			if selected:
				self._step_data["selected_key_id"] = selected
				return await self.async_step_access_key_detail()
			# Back / empty → return to switch actions
			return await self.async_step_manage_switch_action()

		keys = await self._fetch_access_keys()

		# Build a dropdown: each key is a selectable option → detail page
		key_options = {}
		for k in keys:
			kid = k.get("keyId", "")
			if not kid:
				continue
			label = k.get("name", "") or "Unnamed"
			hint = kid[:8]
			paused = " ⏸" if k.get("paused") else ""
			perms = ", ".join(k.get("permissions", []) or [])
			key_options[kid] = f"{label} ({hint}…) [{perms}]{paused}"

		key_options["__create__"] = "➕ Create new access key"

		return self.async_show_form(
			step_id="access_keys",
			data_schema=vol.Schema({
				vol.Required("selected_key"): vol.In(key_options),
			}),
			description_placeholders={
				"count": str(len(keys)),
			},
		)

	async def async_step_access_key_detail(
		self, user_input: Optional[Dict[str, Any]] = None
	) -> FlowResult:
		"""Show detail page for a single access key with action menu."""
		guard = self._access_keys_guard()
		if guard is not None:
			return guard

		key_id = self._step_data.get("selected_key_id")
		if not key_id:
			return await self.async_step_access_keys()

		# If user picked an action
		if user_input is not None:
			action = user_input.get("action")
			if action == "pause":
				return await self.async_step_access_key_pause()
			if action == "permissions":
				return await self.async_step_access_key_permissions()
			if action == "revoke":
				return await self.async_step_revoke_access_key_v2()
			# Back
			return await self.async_step_access_keys()

		# Fetch key details
		keys = await self._fetch_access_keys()
		key_data = next((k for k in keys if k.get("keyId") == key_id), None)
		if not key_data:
			return self.async_abort(reason="no_access_keys")

		label = key_data.get("name", "") or "Unnamed"
		perms = ", ".join(key_data.get("permissions", []) or []) or "none"
		created = self._format_ts(key_data.get("created"))
		last_used = self._format_ts(key_data.get("lastUsed"))
		paused = key_data.get("paused", False)
		status = "⏸ Paused" if paused else "✅ Active"

		info_lines = [
			f"**{label}**",
			f"ID: `{key_id[:8]}…`",
			f"Status: {status}",
			f"Permissions: {perms}",
			f"Created: {created}",
			f"Last used: {last_used}",
		]
		info = "\n".join(info_lines)

		action_options = {}
		if paused:
			action_options["pause"] = "▶️ Unpause key"
		else:
			action_options["pause"] = "⏸ Pause key"
		action_options["permissions"] = "🔑 Change permissions"
		action_options["revoke"] = "🗑️ Revoke key"

		return self.async_show_form(
			step_id="access_key_detail",
			data_schema=vol.Schema({
				vol.Required("action"): vol.In(action_options),
			}),
			description_placeholders={"info": info},
		)

	async def async_step_access_key_pause(
		self, user_input: Optional[Dict[str, Any]] = None
	) -> FlowResult:
		"""Toggle pause/unpause on the selected access key."""
		guard = self._access_keys_guard()
		if guard is not None:
			return guard

		selected_uid = self._step_data.get("selected_uid")
		key_id = self._step_data.get("selected_key_id")
		if not key_id or not selected_uid:
			return await self.async_step_access_keys()

		# Determine current pause state
		keys = await self._fetch_access_keys()
		key_data = next((k for k in keys if k.get("keyId") == key_id), None)
		if not key_data:
			return self.async_abort(reason="no_access_keys")

		currently_paused = key_data.get("paused", False)
		new_paused = not currently_paused

		if user_input is not None:
			if user_input.get("confirm", False):
				coordinator = self.hass.data[DOMAIN][self._config_entry.entry_id]
				ok = await coordinator.pause_v2_access_key(selected_uid, key_id, new_paused)
				if not ok:
					return self.async_show_form(
						step_id="access_key_pause",
						data_schema=vol.Schema({
							vol.Required("confirm", default=False): bool,
						}),
						errors={"base": "access_key_pause_failed"},
						description_placeholders={
							"action": "pause" if new_paused else "unpause",
							"key_name": key_data.get("name") or "Unnamed",
							"key_hint": key_id[:8],
						},
					)
			# Success or user declined – back to detail
			return await self.async_step_access_key_detail()

		action_word = "Pause" if new_paused else "Unpause"
		return self.async_show_form(
			step_id="access_key_pause",
			data_schema=vol.Schema({
				vol.Required("confirm", default=False): bool,
			}),
			description_placeholders={
				"action": action_word.lower(),
				"key_name": key_data.get("name") or "Unnamed",
				"key_hint": key_id[:8],
			},
		)

	async def async_step_access_key_permissions(
		self, user_input: Optional[Dict[str, Any]] = None
	) -> FlowResult:
		"""Update permissions on the selected access key."""
		guard = self._access_keys_guard()
		if guard is not None:
			return guard

		selected_uid = self._step_data.get("selected_uid")
		key_id = self._step_data.get("selected_key_id")
		if not key_id or not selected_uid:
			return await self.async_step_access_keys()

		errors: Dict[str, str] = {}
		permission_options = {
			"toggle": "Toggle",
			"comment": "Comment",
			"metadata": "Metadata (icon/banner/link)",
		}

		# Fetch current permissions
		keys = await self._fetch_access_keys()
		key_data = next((k for k in keys if k.get("keyId") == key_id), None)
		if not key_data:
			return self.async_abort(reason="no_access_keys")

		current_perms = key_data.get("permissions", []) or ["toggle"]

		if user_input is not None:
			new_perms = user_input.get("permissions")
			if not new_perms:
				errors["base"] = "access_key_permissions_empty"
			else:
				new_perms_list = list(new_perms) if not isinstance(new_perms, list) else new_perms
				coordinator = self.hass.data[DOMAIN][self._config_entry.entry_id]
				ok = await coordinator.update_v2_access_key_permissions(
					selected_uid, key_id, new_perms_list
				)
				if not ok:
					errors["base"] = "access_key_permissions_failed"
				else:
					return await self.async_step_access_key_detail()

		return self.async_show_form(
			step_id="access_key_permissions",
			data_schema=vol.Schema({
				vol.Optional("permissions", default=list(current_perms)): cv.multi_select(permission_options),
			}),
			errors=errors,
			description_placeholders={
				"key_name": key_data.get("name") or "Unnamed",
				"key_hint": key_id[:8],
			},
		)

	async def async_step_create_access_key_v2(
		self, user_input: Optional[Dict[str, Any]] = None
	) -> FlowResult:
		"""Create a new delegated v2 access key for the selected switch."""
		guard = self._access_keys_guard()
		if guard is not None:
			return guard

		selected_uid = self._step_data.get("selected_uid")
		errors: Dict[str, str] = {}
		permission_options = {
			"toggle": "Toggle",
			"comment": "Comment",
			"metadata": "Metadata (icon/banner/link)",
		}

		if user_input is not None:
			coordinator = self.hass.data[DOMAIN][self._config_entry.entry_id]
			name = user_input.get("name", "")
			permissions = user_input.get("permissions") or ["toggle"]
			created = await coordinator.create_v2_access_key(selected_uid, name=name, permissions=permissions)
			if not created or not created.get("apiKey"):
				errors["base"] = "access_key_create_failed"
			else:
				api_key = created.get("apiKey", "")
				composite_key = f"{selected_uid}/{api_key}"
				self._step_data["created_access_key"] = api_key
				self._step_data["created_access_key_composite"] = composite_key
				return self.async_show_form(
					step_id="create_access_key_v2_success",
					data_schema=vol.Schema({
						vol.Required("api_key", default=api_key): str,
						vol.Required("api_key_with_uid", default=composite_key): str,
					}),
					description_placeholders={
						"info": "Save this access key securely. It won't be shown again.",
					}
				)

		return self.async_show_form(
			step_id="create_access_key_v2",
			data_schema=vol.Schema({
				vol.Optional("name", default=""): str,
				vol.Optional("permissions", default=["toggle"]): cv.multi_select(permission_options),
			}),
			errors=errors
		)

	async def async_step_create_access_key_v2_success(
		self, user_input: Optional[Dict[str, Any]] = None
	) -> FlowResult:
		"""Show a newly created access key."""
		if user_input is not None:
			return await self.async_step_access_keys()

		return self.async_show_form(
			step_id="create_access_key_v2_success",
			data_schema=vol.Schema({
				vol.Required("api_key", default=self._step_data.get("created_access_key", "")): str,
				vol.Required("api_key_with_uid", default=self._step_data.get("created_access_key_composite", "")): str,
			}),
			description_placeholders={
				"info": "Save this access key securely. It won't be shown again.",
			}
		)

	async def async_step_revoke_access_key_v2(
		self, user_input: Optional[Dict[str, Any]] = None
	) -> FlowResult:
		"""Revoke the selected access key (from detail page)."""
		guard = self._access_keys_guard()
		if guard is not None:
			return guard

		selected_uid = self._step_data.get("selected_uid")
		key_id = self._step_data.get("selected_key_id")
		if not key_id or not selected_uid:
			return await self.async_step_access_keys()

		errors: Dict[str, str] = {}

		# Fetch key info for display
		keys = await self._fetch_access_keys()
		key_data = next((k for k in keys if k.get("keyId") == key_id), None)
		if not key_data:
			return self.async_abort(reason="no_access_keys")

		if user_input is not None:
			if user_input.get("confirm", False):
				coordinator = self.hass.data[DOMAIN][self._config_entry.entry_id]
				ok = await coordinator.revoke_v2_access_key(selected_uid, key_id)
				if not ok:
					errors["base"] = "access_key_revoke_failed"
				else:
					return self.async_show_form(
						step_id="revoke_access_key_v2_success",
						data_schema=vol.Schema({}),
						description_placeholders={
							"info": f"Access key **{key_data.get('name') or 'Unnamed'}** ({key_id[:8]}…) has been permanently revoked."
						}
					)
			else:
				# User declined
				return await self.async_step_access_key_detail()

		return self.async_show_form(
			step_id="revoke_access_key_v2",
			data_schema=vol.Schema({
				vol.Required("confirm", default=False): bool,
			}),
			errors=errors,
			description_placeholders={
				"key_name": key_data.get("name") or "Unnamed",
				"key_hint": key_id[:8],
			},
		)

	async def async_step_revoke_access_key_v2_success(
		self, user_input: Optional[Dict[str, Any]] = None
	) -> FlowResult:
		"""Show success after revoking an access key."""
		if user_input is not None:
			return await self.async_step_access_keys()

		return self.async_show_form(
			step_id="revoke_access_key_v2_success",
			data_schema=vol.Schema({}),
			description_placeholders={"info": "Access key revoked."}
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
				
				return self.async_create_entry(title="", data=dict(self._config_entry.options or {}))
			else:
				return self.async_create_entry(title="", data=dict(self._config_entry.options or {}))
		
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
				return self.async_create_entry(title="", data=dict(self._config_entry.options or {}))
		
		return self.async_show_form(
			step_id="remove_from_installation",
			data_schema=vol.Schema({
				vol.Required("confirm", default=False): bool
			}),
			description_placeholders={
				"warning": f"Remove '{selected_name}' from this Home Assistant installation?\n\n"
				f"⚠️ This will remove the entity from this HA instance only.\n"
				f"The switch will still exist on the Vome server and in other installations.\n\n"
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
			
			return self.async_create_entry(title="", data=dict(self._config_entry.options or {}))
		
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
