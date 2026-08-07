"""Which webhook requests may be forwarded from the internet.

The Nabu Casa "cloudhook" equivalent: a webhook registered in Home Assistant
becomes callable at the friendly domain with **no login**, so a doorbell, a
payment provider or an IFTTT action can reach an automation without exposing
anything else.

The whole security model rests on one fact: a Home Assistant webhook id *is*
the credential.  Core authenticates the caller purely by knowing the id.  That
has two consequences this module exists to enforce:

* **Allowlist, never a blanket toggle.**  Opening ``/api/webhook/`` wholesale
  would expose every webhook the install has now *or gains later* — including
  ones an integration creates without the user ever seeing them.
* **Exact-match only.**  Anything clever in the path (traversal, an extra
  segment, a query string smuggled into the id) must be refused rather than
  normalised, because the thing being matched is a secret.
"""
from __future__ import annotations

from typing import Optional
from urllib.parse import unquote

from .const import (
	WEBHOOK_ALLOWED_METHODS,
	WEBHOOK_ID_RE,
	WEBHOOK_MAX,
	WEBHOOK_PATH_PREFIX,
)


def valid_webhook_id(webhook_id) -> bool:
	return bool(isinstance(webhook_id, str) and WEBHOOK_ID_RE.match(webhook_id))


def normalise_webhooks(raw) -> list[str]:
	"""Clean a stored/incoming allowlist into unique, valid ids (order kept)."""
	out: list[str] = []
	if not isinstance(raw, (list, tuple)):
		return out
	for item in raw:
		# Accept bare ids or ``{"id": ...}`` dicts so a richer record (a label,
		# a created-at) can be added later without breaking stored config.
		webhook_id = item.get("id") if isinstance(item, dict) else item
		if valid_webhook_id(webhook_id) and webhook_id not in out:
			out.append(webhook_id)
		if len(out) >= WEBHOOK_MAX:
			break
	return out


def webhook_id_for_path(path: str) -> Optional[str]:
	"""Return the webhook id a request path addresses, or ``None``.

	Refuses anything that is not exactly ``/api/webhook/<id>``.  A query string
	is allowed and ignored (HA webhooks may carry one); a further path segment,
	dot segment, or percent-encoded trickery is not.
	"""
	if not isinstance(path, str) or not path.startswith(WEBHOOK_PATH_PREFIX):
		return None

	remainder = path[len(WEBHOOK_PATH_PREFIX):]
	# Drop the query/fragment; the id is only the part before them.
	for sep in ("?", "#"):
		if sep in remainder:
			remainder = remainder.split(sep, 1)[0]

	# Decode *before* validating, so an encoded slash or dot cannot slip past a
	# check performed on the raw string and then be normalised by the HTTP
	# client into a different URL than the one we approved.
	try:
		decoded = unquote(remainder)
	except Exception:  # noqa: BLE001 - malformed encoding is simply not a match
		return None
	if decoded != remainder:
		# The id itself never needs encoding, so any difference means someone is
		# trying to express something the plain form would not allow.
		return None

	return remainder if valid_webhook_id(remainder) else None


def is_forwardable_webhook(path: str, method: str, allowed: list[str]) -> bool:
	"""True when this request is a call to an explicitly allowlisted webhook."""
	if not allowed:
		return False
	if str(method or "").upper() not in WEBHOOK_ALLOWED_METHODS:
		return False
	webhook_id = webhook_id_for_path(path)
	return webhook_id is not None and webhook_id in allowed
