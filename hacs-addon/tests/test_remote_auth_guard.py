# flake8: noqa
"""Local-only users stay local when the relay forwards the UI.

Core sees every forwarded request as coming from loopback, so it would let
a "local network only" user sign in from anywhere. These pin the four ways
in that the component now refuses (bearer, /auth/token, WebSocket auth,
signed URL), that ordinary users are untouched, and that a refused sign-in
leaves no working refresh token behind. The same checks run against a real
Core in tests_core/test_remote_auth_guard_core.py.
"""
import base64
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import jwt
import pytest

from custom_components.vomesync import remote_auth_guard as guard
from custom_components.vomesync.relay_client import RelayClient

from test_relay_client import (
	_FakeLocalWS,
	_mock_session_for_forward,
	_sent_payloads,
	_session_for_ws,
)


def _user(local_only):
	return SimpleNamespace(name="Svc" if local_only else "Andy", local_only=local_only)


class _Auth:
	def __init__(self):
		self.tokens = {
			"tok-local": SimpleNamespace(id="rt-local", user=_user(True)),
			"tok-normal": SimpleNamespace(id="rt-normal", user=_user(False)),
		}
		self.removed = []

	def async_validate_access_token(self, token):
		return self.tokens.get(token)

	def async_get_refresh_token(self, token_id):
		return next((rt for rt in self.tokens.values() if rt.id == token_id), None)

	def async_remove_refresh_token(self, refresh_token):
		self.removed.append(refresh_token.id)


@pytest.fixture
def hass():
	return SimpleNamespace(auth=_Auth(), data={})


def _signed(path, issuer):
	sig = jwt.encode({"iss": issuer, "path": path}, "x" * 32, algorithm="HS256")
	return f"{path}?authSig={sig}"


class TestGuard:
	def test_local_only_bearer_is_refused(self, hass):
		headers = [("Authorization", "Bearer tok-local")]
		assert guard.refuse_request(hass, "/api/states", headers) == guard.REFUSAL

	def test_other_users_pass(self, hass):
		assert guard.refuse_request(hass, "/api/states", [("authorization", "Bearer tok-normal")]) is None
		assert guard.refuse_request(hass, "/api/states", []) is None
		assert guard.refuse_request(hass, "/api/states", [("Authorization", "Bearer junk")]) is None

	def test_signed_url_of_a_local_only_user_is_refused(self, hass):
		assert guard.refuse_request(hass, _signed("/api/camera_proxy/x", "rt-local"), []) == guard.REFUSAL
		assert guard.refuse_request(hass, _signed("/api/camera_proxy/x", "rt-normal"), []) is None
		assert guard.refuse_request(hass, "/api/x?authSig=garbage", []) is None

	@pytest.mark.asyncio
	async def test_token_response_for_local_only_is_refused_and_revoked(self, hass):
		body = json.dumps({"access_token": "tok-local", "refresh_token": "r"}).encode()
		assert await guard.refuse_token_response(hass, body) is True
		assert hass.auth.removed == ["rt-local"]

	@pytest.mark.asyncio
	async def test_token_response_for_others_passes(self, hass):
		body = json.dumps({"access_token": "tok-normal"}).encode()
		assert await guard.refuse_token_response(hass, body) is False
		assert await guard.refuse_token_response(hass, b"not json") is False
		assert hass.auth.removed == []

	@pytest.mark.asyncio
	async def test_older_core_coroutine_revoke_is_awaited(self, hass):
		removed = []

		async def remove(rt):
			removed.append(rt.id)

		hass.auth.async_remove_refresh_token = remove
		body = json.dumps({"access_token": "tok-local"}).encode()
		assert await guard.refuse_token_response(hass, body) is True
		assert removed == ["rt-local"]

	def test_ws_auth(self, hass):
		assert guard.refuse_ws_auth(hass, json.dumps({"type": "auth", "access_token": "tok-local"})) == guard.REFUSAL
		assert guard.refuse_ws_auth(hass, json.dumps({"type": "auth", "access_token": "tok-normal"})) is None
		assert guard.refuse_ws_auth(hass, '{"type":"ping"}') is None
		assert guard.refuse_ws_auth(hass, "not json") is None

	def test_no_hass_never_raises(self):
		assert guard.refuse_request(None, "/x", [("Authorization", "Bearer t")]) is None


class TestRelayWiring:
	@pytest.mark.asyncio
	async def test_forwarded_request_with_local_only_token_never_reaches_core(self, hass):
		session, _resp = _mock_session_for_forward()
		client = RelayClient(hass, server_id="rly-1", secret="s", session=session,
			forward_ui=True, local_url="http://127.0.0.1:8123")
		status, headers, body_b64, error = await client._execute_http_proxy({
			"method": "GET", "path": "/api/states",
			"headers": [["Authorization", "Bearer tok-local"]],
		})
		assert status == 403 and error is None
		assert json.loads(base64.b64decode(body_b64))["message"] == guard.REFUSAL
		session.request.assert_not_called()

	@pytest.mark.asyncio
	async def test_sign_in_of_a_local_only_user_is_refused(self, hass):
		issued = json.dumps({"access_token": "tok-local", "refresh_token": "r", "token_type": "Bearer"})
		session, _resp = _mock_session_for_forward(body=issued.encode())
		client = RelayClient(hass, server_id="rly-1", secret="s", session=session,
			forward_ui=True, local_url="http://127.0.0.1:8123")
		status, _h, body_b64, _e = await client._execute_http_proxy({
			"method": "POST", "path": "/auth/token", "headers": [], "stream": True,
		}, sink=AsyncMock(started=False))
		body = json.loads(base64.b64decode(body_b64))
		assert status == 403 and body["error"] == "access_denied"
		assert "tok-local" not in base64.b64decode(body_b64).decode()
		assert hass.auth.removed == ["rt-local"]

	@pytest.mark.asyncio
	async def test_sign_in_of_others_passes_through(self, hass):
		issued = json.dumps({"access_token": "tok-normal"}).encode()
		session, _resp = _mock_session_for_forward(body=issued)
		client = RelayClient(hass, server_id="rly-1", secret="s", session=session,
			forward_ui=True, local_url="http://127.0.0.1:8123")
		status, _h, body_b64, _e = await client._execute_http_proxy({
			"method": "POST", "path": "/auth/token", "headers": [],
		})
		assert status == 200 and base64.b64decode(body_b64) == issued

	@pytest.mark.asyncio
	async def test_websocket_auth_of_local_only_user_is_answered_and_closed(self, hass):
		local = _FakeLocalWS()
		client = RelayClient(hass, server_id="rly-1", secret="s", session=_session_for_ws(local),
			forward_ui=True, local_url="http://127.0.0.1:8123")
		relay_ws = AsyncMock()
		client._ws = relay_ws
		await client._handle_ws_open(relay_ws, {"socketId": "s1", "path": "/api/websocket"})
		await client._handle_ws_data({"socketId": "s1", "text": json.dumps(
			{"type": "auth", "access_token": "tok-local"})})
		assert local.sent_str == []
		frames = [p for p in _sent_payloads(relay_ws) if p.get("type") == "ws_data"]
		assert json.loads(frames[-1]["text"]) == {"type": "auth_invalid", "message": guard.REFUSAL}
		assert "s1" not in client._ws_local

	@pytest.mark.asyncio
	async def test_websocket_auth_of_others_is_forwarded_and_checked_once(self, hass):
		local = _FakeLocalWS()
		client = RelayClient(hass, server_id="rly-1", secret="s", session=_session_for_ws(local),
			forward_ui=True, local_url="http://127.0.0.1:8123")
		relay_ws = AsyncMock()
		client._ws = relay_ws
		await client._handle_ws_open(relay_ws, {"socketId": "s1", "path": "/api/websocket"})
		auth = json.dumps({"type": "auth", "access_token": "tok-normal"})
		await client._handle_ws_data({"socketId": "s1", "text": auth})
		await client._handle_ws_data({"socketId": "s1", "text": '{"id":1,"type":"get_states"}'})
		assert local.sent_str == [auth, '{"id":1,"type":"get_states"}']
		assert "s1" not in client._ws_auth_pending
