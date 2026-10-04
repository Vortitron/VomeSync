"""Keep "local network only" users local when the relay forwards the UI.

Home Assistant lets a user be marked *Can only log in from the local
network* (``user.local_only``).  It enforces that by looking at where the
request came from.  Forwarded traffic reaches core from this component over
loopback, so every remote browser looks local and a local-only user could
sign in from anywhere through ``*.home.vome.io``.  That includes the service
logins Vome itself creates local-only on purpose.

So the component checks what core cannot, on every way in a user can bring:

* a bearer token on a forwarded HTTP request,
* the tokens ``/auth/token`` hands back (sign-in and refresh) — refused and
  the refresh token revoked, so the sign-in leaves nothing behind,
* the ``auth`` message that opens the frontend's WebSocket,
* a signed media URL (``authSig``), whose issuer is a refresh token.

It only ever refuses.  Admitting stays core's decision.
"""
from __future__ import annotations

import inspect
import json
import logging
from typing import Any, Optional
from urllib.parse import parse_qs, urlparse

_LOGGER = logging.getLogger(__name__)

REFUSAL = "User is local only"
TOKEN_PATH = "/auth/token"


def _refresh_for_access_token(hass, token: str):
	try:
		return hass.auth.async_validate_access_token(token)
	except Exception:  # noqa: BLE001 - an unreadable token is core's to refuse
		return None


def _is_local_only(refresh_token) -> bool:
	user = getattr(refresh_token, "user", None)
	return bool(user is not None and getattr(user, "local_only", False))


def _bearer(headers) -> Optional[str]:
	for name, value in headers or []:
		if str(name).lower() == "authorization":
			value = str(value)
			if value[:7].lower() == "bearer ":
				return value[7:].strip()
	return None


def _signed_path_issuer(path: str) -> Optional[str]:
	"""The refresh token id a signed URL was issued under, unverified.

	Read without checking the signature, which is fine: this only decides
	whether to refuse, and core still verifies anything we let through.
	"""
	sig = parse_qs(urlparse(path).query).get("authSig")
	if not sig:
		return None
	try:
		import jwt
		claims = jwt.decode(sig[0], options={"verify_signature": False})
	except Exception:  # noqa: BLE001 - malformed: core refuses it anyway
		return None
	issuer = claims.get("iss")
	return str(issuer) if issuer else None


def refuse_request(hass, path: str, headers) -> Optional[str]:
	"""Why a forwarded request must not reach core, or None."""
	token = _bearer(headers)
	if token and _is_local_only(_refresh_for_access_token(hass, token)):
		return REFUSAL
	issuer = _signed_path_issuer(path)
	if issuer:
		try:
			refresh = hass.auth.async_get_refresh_token(issuer)
		except Exception:  # noqa: BLE001
			refresh = None
		if _is_local_only(refresh):
			return REFUSAL
	return None


def is_token_path(path: str) -> bool:
	return urlparse(path).path == TOKEN_PATH


async def refuse_token_response(hass, body: bytes) -> bool:
	"""True when ``/auth/token`` just issued tokens to a local-only user.

	The refresh token it created is revoked before saying so, so a refused
	sign-in does not leave a working login behind for the next request.
	"""
	try:
		payload = json.loads(body or b"{}")
	except ValueError:
		return False
	token = payload.get("access_token") if isinstance(payload, dict) else None
	if not token:
		return False
	refresh = _refresh_for_access_token(hass, str(token))
	if not _is_local_only(refresh):
		return False
	try:
		result = hass.auth.async_remove_refresh_token(refresh)
		if inspect.isawaitable(result):  # a coroutine before Core 2024.x
			await result
	except Exception:  # noqa: BLE001 - still refuse even if revoking failed
		_LOGGER.warning("Could not revoke a local-only user's remote sign-in", exc_info=True)
	_LOGGER.warning(
		"Refused a remote sign-in for local-only user %s through Vome",
		getattr(getattr(refresh, "user", None), "name", "?"),
	)
	return True


def refuse_ws_auth(hass, text: Any) -> Optional[str]:
	"""Why the frontend socket's ``auth`` message must not reach core, or None.

	Anything that is not an auth message with a token is left alone: core
	answers it, and core refuses a socket that never authenticates.
	"""
	try:
		message = json.loads(text)
	except (TypeError, ValueError):
		return None
	if not isinstance(message, dict) or message.get("type") != "auth":
		return None
	token = message.get("access_token")
	if token and _is_local_only(_refresh_for_access_token(hass, str(token))):
		return REFUSAL
	return None
