"""Actions for switch extras: notes, properties, commerce, payments and rewards.

The contract is docs/SWITCH_EXTRAS.md in the VomeSync server repository.
Every action here is for administrators only: each one signs with this
Home Assistant's owner key, changes a switch for everyone who watches it,
or reads money owed.  They are called from automations and scripts rather
than the Vome app's panel, so a refusal raises with its reason (Home
Assistant shows it) instead of answering ``{"error": ...}``.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional, Tuple

import voluptuous as vol
from homeassistant.components import persistent_notification
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv

try:
	from homeassistant.exceptions import ServiceValidationError
except ImportError:  # Core before 2023.11 (the tests pin 2023.7; the manifest needs 2025.6)
	ServiceValidationError = HomeAssistantError

from .api_client import VomeSyncAPIError
from .const import DOMAIN
from .switch_extras import (
	CURRENCIES,
	ACTIONS,
	REWARD_ON,
	TOGGLE_MODES,
	ExtrasError,
	clean_note,
	clean_params,
	params_clipping,
)

_LOGGER = logging.getLogger(__name__)

CONNECT_NOTIFICATION_ID = "vomesync_connect_payments"

SERVICE_SET_STATE = "set_state"
SERVICE_SET_PROPERTIES = "set_properties"
SERVICE_SET_COMMERCE = "set_commerce"
SERVICE_SET_REWARD = "set_reward"
SERVICE_CONNECT_PAYMENTS = "connect_payments"
SERVICE_PAYMENTS_STATUS = "payments_status"
SERVICE_REWARD_CLAIMS = "reward_claims"
SERVICE_MY_REWARDS = "my_rewards"

EXTRAS_SERVICES = frozenset({
	SERVICE_SET_STATE,
	SERVICE_SET_PROPERTIES,
	SERVICE_SET_COMMERCE,
	SERVICE_SET_REWARD,
	SERVICE_CONNECT_PAYMENTS,
	SERVICE_PAYMENTS_STATUS,
	SERVICE_REWARD_CLAIMS,
	SERVICE_MY_REWARDS,
})

# One switch per call: a list from a UI target is fine as long as it holds one.
_ONE_ENTITY = vol.All(cv.entity_ids, vol.Length(min=1, max=1, msg="Give exactly one Vome switch"))


def _yaml_on_off(value: Any) -> Any:
	"""YAML reads a bare on/off as true/false; take it back to the word."""
	if isinstance(value, bool):
		return "on" if value else "off"
	return value


# Action field -> commerce field.
_COMMERCE_FIELD_MAP = {
	"toggle_mode": "toggleMode",
	"action": "action",
	"hold_seconds": "holdSeconds",
	"cooldown_seconds": "cooldownSeconds",
	"tips": "tips",
	"allow_message": "allowMessage",
	"note": "note",
	"pay_link": "payLink",
	"webhook_url": "webhookUrl",
}
_REWARD_FIELD_MAP = {
	"amount": "amount",
	"currency": "currency",
	"on": "on",
	"window_seconds": "windowSeconds",
	"max_per_day": "maxPerDay",
	"note": "note",
}


def _errors(handler):
	"""Turn a contract or server refusal into an error Home Assistant shows."""
	async def _wrapped(call: ServiceCall) -> ServiceResponse:
		try:
			return await handler(call)
		except ExtrasError as err:
			raise ServiceValidationError(str(err)) from err
		except VomeSyncAPIError as err:
			raise HomeAssistantError(str(err)) from err
	_wrapped.__name__ = getattr(handler, "__name__", "service")
	return _wrapped


def _json_safe(value: Any) -> Any:
	return json.loads(json.dumps(value, default=str))


def _coordinator_for_entry(hass: HomeAssistant, entry_id: Optional[str]):
	from . import _get_coordinator_for_service
	try:
		return _get_coordinator_for_service(hass, entry_id)
	except ValueError as err:
		raise ServiceValidationError(str(err)) from err


def resolve_switch(hass: HomeAssistant, entity_id: str) -> Tuple[Any, str]:
	"""The coordinator and uid behind a Vome switch (or status sensor) entity."""
	from homeassistant.helpers import entity_registry as er
	from .coordinator import VomeSyncCoordinator

	uid: Optional[str] = None
	entry_id: Optional[str] = None
	try:
		reg_entry = er.async_get(hass).async_get(entity_id)
	except Exception:  # noqa: BLE001 - fall back to the state's attributes
		reg_entry = None
	if reg_entry is not None and getattr(reg_entry, "platform", None) == DOMAIN:
		unique_id = str(getattr(reg_entry, "unique_id", "") or "")
		for prefix in ("vomesync_sensor_", "vomesync_"):
			if unique_id.startswith(prefix):
				uid = unique_id[len(prefix):]
				break
		entry_id = getattr(reg_entry, "config_entry_id", None)
	if not uid:
		state = hass.states.get(entity_id)
		attrs = getattr(state, "attributes", None) or {}
		raw = attrs.get("switch_uid") if isinstance(attrs, dict) else None
		uid = raw if isinstance(raw, str) and raw else None
	if not uid:
		raise ServiceValidationError(f"{entity_id} is not a Vome switch")

	domain_data = hass.data.get(DOMAIN, {}) or {}
	if entry_id and isinstance(domain_data.get(entry_id), VomeSyncCoordinator):
		coordinator = domain_data[entry_id]
		if coordinator.get_switch_data(uid) is not None:
			return coordinator, uid
	for coordinator in domain_data.values():
		if isinstance(coordinator, VomeSyncCoordinator) and coordinator.get_switch_data(uid) is not None:
			return coordinator, uid
	raise ServiceValidationError(f"Vome has no data for {entity_id} yet; try again in a minute")


def async_register_extras_services(hass: HomeAssistant) -> None:
	"""Register the switch-extras actions (administrators only)."""
	from .services_remote import async_register_admin

	async def _set_state(call: ServiceCall) -> ServiceResponse:
		coordinator, uid = resolve_switch(hass, call.data["entity_id"][0])
		state = bool(call.data["state"])
		note = clean_note(call.data.get("note"))
		params = clean_params(call.data.get("params"))
		clipping = params_clipping(params)
		if clipping:
			# The server clips params rather than refusing them: the change
			# still happens, with the cut-down copy stored and broadcast.
			_LOGGER.warning(
				"vomesync.set_state: Vome will clip these params (%s)", "; ".join(clipping),
			)
		if coordinator.is_switch_owner(uid):
			if (note or params) and not coordinator.crypto_enabled:
				raise ServiceValidationError(
					"A note or params needs a switch made with a signing key"
				)
			ok = await coordinator.set_switch_state(uid, state, params=params or None, note=note or None)
		else:
			access_key = coordinator.get_subscription_access_key(uid)
			if not access_key:
				raise ServiceValidationError(
					"This Home Assistant only watches that switch: changing it needs an access key from its owner"
				)
			ok = await coordinator.toggle_switch_with_access_key(
				uid, access_key, desired_state=state, note=note or None, params=params or None,
			)
		if not ok:
			raise HomeAssistantError("Vome did not change the switch; the log says why")
		return None

	async def _set_properties(call: ServiceCall) -> ServiceResponse:
		coordinator, uid = resolve_switch(hass, call.data["entity_id"][0])
		await coordinator.set_properties(
			uid, dict(call.data["properties"]), replace=bool(call.data.get("replace", False)),
		)
		return None

	async def _set_commerce(call: ServiceCall) -> ServiceResponse:
		coordinator, uid = resolve_switch(hass, call.data["entity_id"][0])
		fields: Dict[str, Any] = {
			commerce_key: call.data[field]
			for field, commerce_key in _COMMERCE_FIELD_MAP.items()
			if field in call.data
		}
		if "price" in call.data or "currency" in call.data:
			current = (coordinator.get_switch_data(uid) or {}).get("commerce") or {}
			current_price = current.get("price") if isinstance(current.get("price"), dict) else {}
			amount = call.data.get("price", current_price.get("amount"))
			currency = call.data.get("currency", current_price.get("currency") or "eur")
			# A price of 0 takes the price off.
			fields["price"] = None if not amount else {"amount": amount, "currency": currency}
		rotate = bool(call.data.get("rotate_webhook_secret", False))
		if not fields and not rotate:
			raise ServiceValidationError("Give at least one commerce setting to change")
		result = await coordinator.set_commerce(uid, fields, rotate_webhook_secret=rotate)
		answer: Dict[str, Any] = {"commerce": _json_safe(result.get("commerce"))}
		if result.get("webhookSecret"):
			answer["webhook_secret"] = result["webhookSecret"]
		return answer

	async def _set_reward(call: ServiceCall) -> ServiceResponse:
		coordinator, uid = resolve_switch(hass, call.data["entity_id"][0])
		if call.data.get("clear"):
			await coordinator.set_reward(uid, None)
			return None
		given = {
			reward_key: call.data[field]
			for field, reward_key in _REWARD_FIELD_MAP.items()
			if field in call.data
		}
		# The signed update replaces the offer whole: start from the current one.
		current = (coordinator.get_switch_data(uid) or {}).get("reward")
		reward = {**(current if isinstance(current, dict) else {}), **given}
		if not reward:
			raise ServiceValidationError("Give the reward's amount and currency, or clear: true")
		await coordinator.set_reward(uid, reward)
		return None

	async def _connect_payments(call: ServiceCall) -> ServiceResponse:
		coordinator = _coordinator_for_entry(hass, call.data.get("entry_id"))
		result = await coordinator.start_connect_onboarding()
		url = result.get("url") if isinstance(result, dict) else None
		if not url:
			raise HomeAssistantError("Vome did not return an onboarding link")
		persistent_notification.async_create(
			hass,
			(
				"To take payments for your switches, finish setting up your "
				f"payout account with Stripe:\n\n[Continue with Stripe]({url})\n\n"
				"The link works once and runs out after a few minutes. Call "
				"vomesync.connect_payments again for a new one."
			),
			title="Vome: take payments",
			notification_id=CONNECT_NOTIFICATION_ID,
		)
		return {"url": url}

	async def _payments_status(call: ServiceCall) -> ServiceResponse:
		coordinator = _coordinator_for_entry(hass, call.data.get("entry_id"))
		result = await coordinator.get_connect_status() or {}
		answer: Dict[str, Any] = {
			"connected": bool(result.get("connected")),
			"ready": bool(result.get("ready")),
			"requirements_due": _json_safe(result.get("requirementsDue") or []),
		}
		if result.get("dashboardUrl"):
			answer["dashboard_url"] = result["dashboardUrl"]
		return answer

	async def _reward_claims(call: ServiceCall) -> ServiceResponse:
		coordinator, uid = resolve_switch(hass, call.data["entity_id"][0])
		return _json_safe(await coordinator.list_reward_claims(uid) or {})

	async def _my_rewards(call: ServiceCall) -> ServiceResponse:
		coordinator = _coordinator_for_entry(hass, call.data.get("entry_id"))
		return _json_safe(await coordinator.get_my_rewards() or {})

	async_register_admin(
		hass, SERVICE_SET_STATE, _errors(_set_state),
		schema=vol.Schema({
			vol.Required("entity_id"): _ONE_ENTITY,
			vol.Required("state"): cv.boolean,
			vol.Optional("note"): vol.Any(None, cv.string),
			vol.Optional("params"): vol.Any(None, dict),
		}),
		supports_response=SupportsResponse.NONE,
	)
	async_register_admin(
		hass, SERVICE_SET_PROPERTIES, _errors(_set_properties),
		schema=vol.Schema({
			vol.Required("entity_id"): _ONE_ENTITY,
			vol.Required("properties"): dict,
			vol.Optional("replace", default=False): cv.boolean,
		}),
		supports_response=SupportsResponse.NONE,
	)
	async_register_admin(
		hass, SERVICE_SET_COMMERCE, _errors(_set_commerce),
		schema=vol.Schema({
			vol.Required("entity_id"): _ONE_ENTITY,
			vol.Optional("toggle_mode"): vol.In(TOGGLE_MODES),
			vol.Optional("action"): vol.All(_yaml_on_off, vol.In(ACTIONS)),
			vol.Optional("hold_seconds"): vol.Coerce(int),
			vol.Optional("cooldown_seconds"): vol.Coerce(int),
			vol.Optional("price"): vol.Coerce(int),
			vol.Optional("currency"): vol.All(vol.Lower, vol.In(tuple(CURRENCIES))),
			vol.Optional("tips"): cv.boolean,
			vol.Optional("allow_message"): cv.boolean,
			vol.Optional("note"): vol.Any(None, cv.string),
			vol.Optional("pay_link"): vol.Any(None, cv.string),
			vol.Optional("webhook_url"): vol.Any(None, cv.string),
			vol.Optional("rotate_webhook_secret", default=False): cv.boolean,
		}),
		supports_response=SupportsResponse.OPTIONAL,
	)
	async_register_admin(
		hass, SERVICE_SET_REWARD, _errors(_set_reward),
		schema=vol.Schema({
			vol.Required("entity_id"): _ONE_ENTITY,
			vol.Optional("amount"): vol.Coerce(int),
			vol.Optional("currency"): vol.All(vol.Lower, vol.In(tuple(CURRENCIES))),
			vol.Optional("on"): vol.All(_yaml_on_off, vol.In(REWARD_ON)),
			vol.Optional("window_seconds"): vol.Coerce(int),
			vol.Optional("max_per_day"): vol.Coerce(int),
			vol.Optional("note"): vol.Any(None, cv.string),
			vol.Optional("clear", default=False): cv.boolean,
		}),
		supports_response=SupportsResponse.NONE,
	)
	async_register_admin(
		hass, SERVICE_CONNECT_PAYMENTS, _errors(_connect_payments),
		schema=vol.Schema({vol.Optional("entry_id"): cv.string}),
		supports_response=SupportsResponse.OPTIONAL,
	)
	async_register_admin(
		hass, SERVICE_PAYMENTS_STATUS, _errors(_payments_status),
		schema=vol.Schema({vol.Optional("entry_id"): cv.string}),
		supports_response=SupportsResponse.ONLY,
	)
	async_register_admin(
		hass, SERVICE_REWARD_CLAIMS, _errors(_reward_claims),
		schema=vol.Schema({vol.Required("entity_id"): _ONE_ENTITY}),
		supports_response=SupportsResponse.ONLY,
	)
	async_register_admin(
		hass, SERVICE_MY_REWARDS, _errors(_my_rewards),
		schema=vol.Schema({vol.Optional("entry_id"): cv.string}),
		supports_response=SupportsResponse.ONLY,
	)
