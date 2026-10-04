# flake8: noqa
"""remote_auth_guard against real Core auth: real users, real tokens."""
import json
from types import SimpleNamespace

from homeassistant.components.http.auth import async_user_not_allowed_do_auth

from custom_components.vomesync import remote_auth_guard as guard

CLIENT_ID = "https://example.com/"


async def _user_and_token(hass, local_only):
	user = await hass.auth.async_create_user("Svc" if local_only else "Andy", local_only=local_only)
	refresh = await hass.auth.async_create_refresh_token(user, CLIENT_ID)
	return refresh, hass.auth.async_create_access_token(refresh)


async def test_the_gap_core_admits_local_only_from_loopback(hass):
	"""Why the guard exists: forwarded requests arrive from 127.0.0.1."""
	refresh, _token = await _user_and_token(hass, local_only=True)
	assert async_user_not_allowed_do_auth(hass, refresh.user, SimpleNamespace(remote="127.0.0.1")) is None
	assert async_user_not_allowed_do_auth(hass, refresh.user, SimpleNamespace(remote="8.8.8.8"))


async def test_bearer_and_ws_auth(hass):
	_r, local = await _user_and_token(hass, local_only=True)
	_r, normal = await _user_and_token(hass, local_only=False)
	assert guard.refuse_request(hass, "/api/states", [("Authorization", f"Bearer {local}")]) == guard.REFUSAL
	assert guard.refuse_request(hass, "/api/states", [("Authorization", f"Bearer {normal}")]) is None
	assert guard.refuse_ws_auth(hass, json.dumps({"type": "auth", "access_token": local})) == guard.REFUSAL
	assert guard.refuse_ws_auth(hass, json.dumps({"type": "auth", "access_token": normal})) is None


async def test_signed_path(hass):
	from homeassistant.components.http.auth import async_sign_path
	from datetime import timedelta
	refresh, _t = await _user_and_token(hass, local_only=True)
	signed = async_sign_path(hass, "/api/camera_proxy/camera.x", timedelta(minutes=5), refresh_token_id=refresh.id)
	assert guard.refuse_request(hass, signed, []) == guard.REFUSAL


async def test_refused_sign_in_is_revoked(hass):
	refresh, local = await _user_and_token(hass, local_only=True)
	assert await guard.refuse_token_response(hass, json.dumps({"access_token": local}).encode())
	assert hass.auth.async_get_refresh_token(refresh.id) is None
	assert hass.auth.async_validate_access_token(local) is None
