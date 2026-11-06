"""Switch extras: fixed properties, per-change notes and params, commerce and rewards.

The contract is docs/SWITCH_EXTRAS.md in the VomeSync server repository.
The server checks everything again; the checks here are so that somebody
calling an action gets a clear reason straight away instead of a signed
request the server refuses.  The limits mirror the server's
``utils/switchExtras.js``.

Standard library only, no Core imports, so the tests can use it directly.
"""
from __future__ import annotations

import json
import math
import re
from typing import Any, Dict, Optional

MAX_PROPERTIES = 12
PROPERTY_KEY = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
MAX_PROPERTY_VALUE = 200

MAX_NOTE = 280
MAX_PARAMS_KEYS = 16
PARAM_KEY = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,31}$")
MAX_PARAM_STRING = 200
MAX_PARAM_ARRAY = 8
MAX_PARAMS_JSON = 2048

TOGGLE_MODES = ("owner", "public", "paid")
ACTIONS = ("toggle", "on", "off")
REWARD_ON = ("on", "off", "any")
MAX_SECONDS = 3600
MAX_COMMERCE_NOTE = 200
MAX_URL = 500

# Minor units.  SEK has a higher floor because 1 kr does not cover the card fee.
CURRENCIES: Dict[str, Dict[str, int]] = {
	"eur": {"min": 100, "max": 50000},
	"gbp": {"min": 100, "max": 50000},
	"usd": {"min": 100, "max": 50000},
	"sek": {"min": 1000, "max": 500000},
}

# Every field a commerce object may hold.  A signed update replaces the whole
# object and the server refuses a field it does not know, so this is also
# what is kept from the copy the server last sent.
COMMERCE_FIELDS = (
	"toggleMode",
	"action",
	"holdSeconds",
	"cooldownSeconds",
	"price",
	"tips",
	"allowMessage",
	"note",
	"payLink",
	"webhookUrl",
)
# What the public sees of it, and so what an entity shows: never the
# webhook address (it may carry the owner's own token) or its secret.
COMMERCE_SUMMARY_FIELDS = (
	"toggleMode",
	"action",
	"holdSeconds",
	"cooldownSeconds",
	"price",
	"tips",
	"allowMessage",
	"note",
	"payLink",
	"canTakePayments",
)

REWARD_FIELDS = ("amount", "currency", "on", "windowSeconds", "maxPerDay", "note")
SOURCES = ("owner", "key", "public", "paid", "auto")

# Same characters the server strips: C0/C1 controls, zero-width marks and
# the bidirectional overrides that can make a note read differently.
_CONTROL_CHARS = re.compile(
	"[\u0000-\u001f\u007f-\u009f​-‏‪-‮⁦-⁩]"
)
_LINK = re.compile(r"https?:|www\.|\.[a-z]{2,}/", re.IGNORECASE)


class ExtrasError(ValueError):
	"""A value the contract does not allow; the message is safe to show."""


def clean_text(value: Any) -> str:
	"""Strip control characters and collapse runs of whitespace."""
	text = _CONTROL_CHARS.sub(" ", str(value))
	return re.sub(r"\s+", " ", text).strip()


def normalise_number(value: Any) -> Any:
	"""A whole float as an int, so Python and JavaScript sign the same text.

	``json.dumps(1.0)`` is ``1.0``; the server parses that to the number 1
	and its canonical JSON says ``1``, so a signature over ``1.0`` fails.
	"""
	if isinstance(value, float) and math.isfinite(value) and value.is_integer():
		return int(value)
	return value


def normalise_json(value: Any) -> Any:
	"""``normalise_number`` all the way down, and tuples as lists."""
	if isinstance(value, dict):
		return {k: normalise_json(v) for k, v in value.items()}
	if isinstance(value, (list, tuple)):
		return [normalise_json(v) for v in value]
	return normalise_number(value)


def _is_number(value: Any) -> bool:
	return isinstance(value, (int, float)) and not isinstance(value, bool)


def clean_note(raw: Any, max_len: int = MAX_NOTE) -> str:
	"""A change's note: text, cleaned, at most ``max_len`` characters."""
	if raw is None:
		return ""
	if not isinstance(raw, str):
		raise ExtrasError("The note must be text")
	text = clean_text(raw)
	if len(text) > max_len:
		raise ExtrasError(f"The note is longer than {max_len} characters")
	return text


def _param_value_clipped(value: Any) -> bool:
	"""Whether the server would cut or rewrite this params value."""
	if value is None or isinstance(value, bool) or _is_number(value):
		return False
	if isinstance(value, str):
		return len(value) > MAX_PARAM_STRING
	if isinstance(value, (list, tuple)):
		if len(value) > MAX_PARAM_ARRAY:
			return True
		return not all(
			(_is_number(item) and not isinstance(item, bool))
			or (isinstance(item, str) and len(item) <= MAX_PARAM_STRING)
			for item in value
		)
	# A nested object becomes short JSON text.
	return True


def _check_finite(value: Any, key: str) -> None:
	if isinstance(value, float) and not math.isfinite(value):
		raise ExtrasError(f"params.{key} must be a finite number")
	if isinstance(value, dict):
		for inner in value.values():
			_check_finite(inner, key)
	elif isinstance(value, (list, tuple)):
		for inner in value:
			_check_finite(inner, key)


def clean_params(raw: Any) -> Dict[str, Any]:
	"""A change's params, as they will be sent and signed.

	The server clips params rather than refusing them (long text cut,
	nested objects turned to short text, bad keys and keys past the limits
	dropped), and the signature covers what was sent, so nothing is cut
	here: see :func:`params_clipping` for what the server will do.  Only a
	value that is not JSON at all is refused.
	"""
	if raw is None:
		return {}
	if not isinstance(raw, dict):
		raise ExtrasError("params must be an object")
	for key, value in raw.items():
		_check_finite(value, str(key))
	return normalise_json(raw)


def params_clipping(params: Dict[str, Any]) -> list:
	"""What the server will cut from ``params``, as short reasons (empty: nothing)."""
	if not isinstance(params, dict):
		return []
	reasons = []
	kept = 0
	for key, value in params.items():
		if not isinstance(key, str) or not PARAM_KEY.match(key):
			reasons.append(f'key "{str(key)[:40]}" is not allowed and is dropped')
			continue
		kept += 1
		if kept > MAX_PARAMS_KEYS:
			reasons.append(f'key "{key}" is past the first {MAX_PARAMS_KEYS} and is dropped')
			continue
		if _param_value_clipped(value):
			reasons.append(f'"{key}" is shortened')
	size = len(json.dumps(params, separators=(",", ":"), ensure_ascii=False, default=str))
	if size > MAX_PARAMS_JSON:
		reasons.append(f"keys past {MAX_PARAMS_JSON} bytes are dropped")
	return reasons


def _clean_property_value(key: str, value: Any) -> Any:
	if isinstance(value, bool):
		return value
	if _is_number(value):
		if not math.isfinite(value):
			raise ExtrasError(f"Property {key} must be a finite number")
		return normalise_number(value)
	if isinstance(value, str):
		text = clean_text(value)
		if len(text) > MAX_PROPERTY_VALUE:
			raise ExtrasError(f"Property {key} is longer than {MAX_PROPERTY_VALUE} characters")
		return text
	raise ExtrasError(f"Property {key} must be text, a number or true/false")


def clean_properties(
	raw: Any,
	current: Optional[Dict[str, Any]] = None,
	replace: bool = False,
) -> Dict[str, Any]:
	"""The full properties map to send.

	A signed update replaces the whole map, so without ``replace`` the given
	keys are merged onto ``current`` (a ``None`` value removes its key).
	"""
	if raw is None:
		raw = {}
	if not isinstance(raw, dict):
		raise ExtrasError("properties must be an object")
	base: Dict[str, Any] = {}
	if not replace and isinstance(current, dict):
		base = {k: v for k, v in current.items() if isinstance(k, str)}
	for key, value in raw.items():
		if not isinstance(key, str) or not PROPERTY_KEY.match(key):
			raise ExtrasError(
				f'Property name "{str(key)[:40]}" must be lower case letters, digits or _ '
				"(at most 32, starting with a letter)"
			)
		if value is None:
			base.pop(key, None)
			continue
		base[key] = _clean_property_value(key, value)
	if len(base) > MAX_PROPERTIES:
		raise ExtrasError(f"At most {MAX_PROPERTIES} properties")
	return normalise_json(base)


def _clean_seconds(value: Any, field: str) -> int:
	seconds = _whole(value)
	if seconds is None or seconds < 0 or seconds > MAX_SECONDS:
		raise ExtrasError(f"{field} must be a whole number of seconds from 0 to {MAX_SECONDS}")
	return seconds


def clean_currency(value: Any) -> str:
	currency = str(value or "").lower()
	if currency not in CURRENCIES:
		raise ExtrasError(f"Currency must be one of {', '.join(CURRENCIES)}")
	return currency


def _whole(value: Any) -> Optional[int]:
	if isinstance(value, bool) or not _is_number(value):
		return None
	if not math.isfinite(value) or float(value) != int(value):
		return None
	return int(value)


def clean_money(raw: Any, field: str) -> Optional[Dict[str, Any]]:
	"""``{amount, currency}`` in minor units, within the card floor and ceiling."""
	if raw is None:
		return None
	if not isinstance(raw, dict):
		raise ExtrasError(f"{field} must be {{ amount, currency }}")
	currency = clean_currency(raw.get("currency"))
	limits = CURRENCIES[currency]
	amount = _whole(raw.get("amount"))
	if amount is None or amount < limits["min"] or amount > limits["max"]:
		raise ExtrasError(
			f"{field}.amount must be between {limits['min']} and {limits['max']} "
			f"(minor units of {currency.upper()})"
		)
	return {"amount": amount, "currency": currency}


def _clean_url(value: Any, field: str) -> str:
	if value is None or value == "":
		return ""
	if not isinstance(value, str) or len(value) > MAX_URL:
		raise ExtrasError(f"{field} must be an address of at most {MAX_URL} characters")
	text = value.strip()
	allowed = ("https://", "http://") if field == "payLink" else ("https://",)
	if not text.lower().startswith(allowed):
		raise ExtrasError(f"{field} must use https")
	host = text.split("://", 1)[1].split("/", 1)[0]
	if not host:
		raise ExtrasError(f"{field} must be a valid address")
	if "@" in host:
		raise ExtrasError(f"{field} cannot contain a user name or password")
	return text


def _clean_commerce_field(key: str, value: Any) -> Any:
	if key == "toggleMode":
		if value not in TOGGLE_MODES:
			raise ExtrasError(f"toggleMode must be one of {', '.join(TOGGLE_MODES)}")
		return value
	if key == "action":
		if value not in ACTIONS:
			raise ExtrasError(f"action must be one of {', '.join(ACTIONS)}")
		return value
	if key in ("holdSeconds", "cooldownSeconds"):
		return _clean_seconds(value, key)
	if key == "price":
		return clean_money(value, "price")
	if key in ("tips", "allowMessage"):
		return bool(value)
	if key == "note":
		return clean_note(value, MAX_COMMERCE_NOTE)
	if key in ("payLink", "webhookUrl"):
		return _clean_url(value, key)
	raise ExtrasError(f"Unknown commerce field {str(key)[:40]}")


def clean_commerce(
	patch: Dict[str, Any],
	current: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
	"""The full commerce object to send: ``patch`` over the current settings.

	The signed update replaces the object whole, and the server fills any
	missing field with its default.  Starting from what the server last sent
	keeps the fields the caller did not mention.
	"""
	if not isinstance(patch, dict):
		raise ExtrasError("commerce must be an object")
	merged: Dict[str, Any] = {}
	if isinstance(current, dict):
		merged = {k: current[k] for k in COMMERCE_FIELDS if k in current}
	for key, value in patch.items():
		merged[key] = _clean_commerce_field(key, value)
	if merged.get("toggleMode") == "paid" and not merged.get("price"):
		raise ExtrasError("Paid mode needs a price")
	return normalise_json(merged)


def clean_reward(raw: Any) -> Optional[Dict[str, Any]]:
	"""A reward offer, with the server's defaults filled in; ``None`` removes it."""
	if raw is None:
		return None
	if not isinstance(raw, dict):
		raise ExtrasError("reward must be an object")
	for key in raw:
		if key not in REWARD_FIELDS:
			raise ExtrasError(f"Unknown reward field {str(key)[:40]}")
	currency = clean_currency(raw.get("currency"))
	# Rewards are per action, so they may be smaller than a card payment.
	amount = _whole(raw.get("amount"))
	ceiling = CURRENCIES[currency]["max"]
	if amount is None or amount < 1 or amount > ceiling:
		raise ExtrasError(f"reward amount must be between 1 and {ceiling} (minor units)")
	on = raw.get("on", "on")
	if on not in REWARD_ON:
		raise ExtrasError(f"reward on must be one of {', '.join(REWARD_ON)}")
	window = _whole(raw.get("windowSeconds", 900))
	if window is None or window < 60 or window > 86400:
		raise ExtrasError("reward windowSeconds must be from 60 to 86400")
	per_day = _whole(raw.get("maxPerDay", 5))
	if per_day is None or per_day < 1 or per_day > 100:
		raise ExtrasError("reward maxPerDay must be from 1 to 100")
	return {
		"amount": amount,
		"currency": currency,
		"on": on,
		"windowSeconds": window,
		"maxPerDay": per_day,
		"note": clean_note(raw.get("note"), MAX_COMMERCE_NOTE),
	}


def commerce_summary(commerce: Any) -> Optional[Dict[str, Any]]:
	"""What an entity may show of the commerce settings (the public summary)."""
	if not isinstance(commerce, dict) or not commerce:
		return None
	return {k: commerce[k] for k in COMMERCE_SUMMARY_FIELDS if k in commerce}


def reward_qualifies(reward: Any, state: bool) -> bool:
	"""Whether a change to ``state`` earns the offered reward."""
	if not isinstance(reward, dict) or not reward:
		return False
	on = reward.get("on", "on")
	if on == "any":
		return True
	return on == ("on" if state else "off")


def extras_attributes(data: Dict[str, Any]) -> Dict[str, Any]:
	"""The switch-extras state attributes for an entity's data.

	Always present, so a template or card can rely on them; empty when the
	switch has none.
	"""
	properties = data.get("properties")
	params = data.get("lastParams")
	reward = data.get("reward")
	return {
		"properties": dict(properties) if isinstance(properties, dict) else {},
		"last_note": data.get("note") or "",
		"last_params": dict(params) if isinstance(params, dict) else {},
		"last_source": data.get("lastSource"),
		"commerce": commerce_summary(data.get("commerce")),
		"reward": dict(reward) if isinstance(reward, dict) and reward else None,
	}
