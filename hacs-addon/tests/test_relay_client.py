# flake8: noqa
"""Tests for the Vome relay client (outbound tunnel) on the component side.

Covers the bits that make the relay safe and correct without a live WebSocket:
* execute() only runs /api paths, uses the configured/minted local token (the
  Supervisor /core/api proxy 401s core's own token, so it is never used), and
  turns local failures into status 0;
* a local access token is minted via hass.auth (find-or-create semantics);
* ha_rpc handling replies with a well-formed ha_rpc_response;
* the device-authorisation HTTP helpers post to the right endpoints.

Mirrors the AsyncMock(session) style used in test_api_client.py.
"""
import json
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest

from custom_components.vomesync.relay_client import (
	RELAY_TOKEN_CLIENT_NAME,
	RelayClient,
	async_ensure_local_access_token,
	async_poll_device_token,
	async_request_device_code,
)
from custom_components.vomesync.const import (
	SUPERVISOR_TOKEN_ENV,
)


def _mock_session_with_response(status=200, text="{}"):
	mock_response = AsyncMock()
	mock_response.status = status
	mock_response.text.return_value = text
	mock_response.raise_for_status = MagicMock()
	session = AsyncMock(spec=aiohttp.ClientSession)
	session.request.return_value.__aenter__.return_value = mock_response
	session.post.return_value.__aenter__.return_value = mock_response
	return session, mock_response


def _client(session, **kwargs):
	return RelayClient(None, server_id="rly-1", secret="sek", session=session, **kwargs)


class TestExecute:
	@pytest.mark.asyncio
	async def test_rejects_non_api_path(self):
		session, _ = _mock_session_with_response()
		client = _client(session)
		status, body, error = await client.execute("GET", "/auth/token", None)
		assert status == 0 and body is None and "non-/api" in error
		session.request.assert_not_called()

	@pytest.mark.asyncio
	async def test_rejects_unsupported_method(self):
		session, _ = _mock_session_with_response()
		client = _client(session)
		status, body, error = await client.execute("PATCH", "/api/states", None)
		assert status == 0 and "PATCH" in error
		session.request.assert_not_called()

	@pytest.mark.asyncio
	async def test_no_token_available(self, monkeypatch):
		# Even with a Supervisor env present, no minted/manual token → no call:
		# the /core/api proxy must never be used (it 401s core's own token).
		monkeypatch.setenv(SUPERVISOR_TOKEN_ENV, "supervisor-tok")
		session, _ = _mock_session_with_response()
		client = _client(session)  # no local_token
		status, body, error = await client.execute("GET", "/api/states", None)
		assert status == 0 and "No local access token" in error
		session.request.assert_not_called()

	@pytest.mark.asyncio
	async def test_local_token_used_against_local_url(self):
		session, _ = _mock_session_with_response(status=201, text="")
		client = _client(session, local_token="llt", local_url="http://127.0.0.1:8123")
		status, body, error = await client.execute("POST", "/api/services/light/turn_on", {"entity_id": "light.k"})
		assert status == 201 and error is None
		args, kwargs = session.request.call_args
		assert args[1] == "http://127.0.0.1:8123/api/services/light/turn_on"
		assert kwargs["headers"]["Authorization"] == "Bearer llt"
		assert kwargs["json"] == {"entity_id": "light.k"}

	@pytest.mark.asyncio
	async def test_default_local_url_when_not_configured(self):
		session, _ = _mock_session_with_response(status=200, text="[]")
		client = _client(session, local_token="llt")
		status, body, error = await client.execute("GET", "/api/states", None)
		assert status == 200 and error is None
		args, _kwargs = session.request.call_args
		assert args[1] == "http://127.0.0.1:8123/api/states"

	@pytest.mark.asyncio
	async def test_timeout_is_status_zero(self):
		session = AsyncMock(spec=aiohttp.ClientSession)
		import asyncio
		session.request.return_value.__aenter__.side_effect = asyncio.TimeoutError()
		client = _client(session, local_token="llt")
		status, body, error = await client.execute("GET", "/api/states", None)
		assert status == 0 and "timed out" in error

	@pytest.mark.asyncio
	async def test_client_error_is_status_zero(self):
		session = AsyncMock(spec=aiohttp.ClientSession)
		session.request.return_value.__aenter__.side_effect = aiohttp.ClientError("boom")
		client = _client(session, local_token="llt")
		status, body, error = await client.execute("GET", "/api/states", None)
		assert status == 0 and "error" in error.lower()


class TestEsphome:
	@pytest.mark.asyncio
	async def test_rejects_disallowed_path(self):
		session, _ = _mock_session_with_response()
		client = _client(session, esphome_url="http://esp:6052")
		status, body, error = await client.execute("GET", "/secrets", None, "esphome")
		assert status == 0 and "non-allowlisted" in error
		session.request.assert_not_called()

	@pytest.mark.asyncio
	async def test_rejects_unsupported_method(self):
		session, _ = _mock_session_with_response()
		client = _client(session, esphome_url="http://esp:6052")
		status, body, error = await client.execute("DELETE", "/devices", None, "esphome")
		assert status == 0 and "Unsupported ESPHome method" in error
		session.request.assert_not_called()

	@pytest.mark.asyncio
	async def test_explicit_url_list_devices(self):
		session, _ = _mock_session_with_response(status=200, text='[{"name":"x"}]')
		client = _client(session, esphome_url="http://esp:6052/")
		status, body, error = await client.execute("GET", "/devices", None, "esphome")
		assert status == 200 and body == '[{"name":"x"}]' and error is None
		args, kwargs = session.request.call_args
		assert args[0] == "GET"
		assert args[1] == "http://esp:6052/devices"
		assert kwargs["data"] is None

	@pytest.mark.asyncio
	async def test_save_config_sends_yaml(self):
		session, _ = _mock_session_with_response(status=200, text="")
		client = _client(session, esphome_url="http://esp:6052")
		yaml = "esphome:\n  name: x\n"
		status, body, error = await client.execute(
			"POST", "/edit?configuration=x.yaml", yaml, "esphome"
		)
		assert status == 200 and error is None
		args, kwargs = session.request.call_args
		assert args[1] == "http://esp:6052/edit?configuration=x.yaml"
		assert kwargs["data"] == yaml
		assert kwargs["headers"]["Content-Type"] == "application/yaml"

	@staticmethod
	def _session_with_addons(addons, status=200, text="[]"):
		session, _ = _mock_session_with_response(status=status, text=text)
		disc = AsyncMock()
		disc.status = 200
		disc.json.return_value = {"data": {"addons": addons}}
		session.get.return_value.__aenter__.return_value = disc
		return session

	@pytest.mark.asyncio
	async def test_discovery_via_supervisor(self, monkeypatch):
		monkeypatch.setenv(SUPERVISOR_TOKEN_ENV, "sup")
		session = self._session_with_addons(
			[{"slug": "5c53de3b_esphome", "name": "ESPHome", "state": "started"}]
		)
		client = _client(session)  # no explicit esphome_url
		status, body, error = await client.execute("GET", "/devices", None, "esphome")
		assert status == 200 and error is None
		args, kwargs = session.request.call_args
		assert args[1] == "http://5c53de3b-esphome:6052/devices"

	@pytest.mark.asyncio
	async def test_discovery_skips_stopped_addon(self, monkeypatch):
		# A stopped add-on has no internal DNS entry: report it, don't connect.
		monkeypatch.setenv(SUPERVISOR_TOKEN_ENV, "sup")
		session = self._session_with_addons(
			[{"slug": "5c53de3b_esphome", "name": "ESPHome Device Builder", "state": "stopped"}]
		)
		client = _client(session)
		status, body, error = await client.execute("GET", "/devices", None, "esphome")
		assert status == 0 and "installed but not running" in error
		assert "ESPHome Device Builder" in error
		session.request.assert_not_called()

	@pytest.mark.asyncio
	async def test_discovery_prefers_started_addon(self, monkeypatch):
		monkeypatch.setenv(SUPERVISOR_TOKEN_ENV, "sup")
		session = self._session_with_addons([
			{"slug": "5c53de3b_esphome-beta", "name": "ESPHome (beta)", "state": "stopped"},
			{"slug": "5c53de3b_esphome", "name": "ESPHome", "state": "started"},
		])
		client = _client(session)
		status, body, error = await client.execute("GET", "/devices", None, "esphome")
		assert status == 200 and error is None
		args, _kwargs = session.request.call_args
		assert args[1] == "http://5c53de3b-esphome:6052/devices"

	@pytest.mark.asyncio
	async def test_no_esphome_addon_installed(self, monkeypatch):
		monkeypatch.setenv(SUPERVISOR_TOKEN_ENV, "sup")
		session = self._session_with_addons([{"slug": "core_mosquitto", "state": "started"}])
		client = _client(session)
		status, body, error = await client.execute("GET", "/devices", None, "esphome")
		assert status == 0 and "ESPHome add-on not found" in error
		session.request.assert_not_called()

	@pytest.mark.asyncio
	async def test_no_dashboard_found(self, monkeypatch):
		monkeypatch.delenv(SUPERVISOR_TOKEN_ENV, raising=False)
		session, _ = _mock_session_with_response()
		client = _client(session)  # no url, no supervisor token
		status, body, error = await client.execute("GET", "/devices", None, "esphome")
		assert status == 0 and "ESPHome dashboard not found" in error
		session.request.assert_not_called()

	@pytest.mark.asyncio
	async def test_connect_error_drops_cache_and_hints(self, monkeypatch):
		# First call discovers and caches, but the connection fails: the error
		# must hint at the add-on state and the cache must be dropped so the
		# next call re-discovers (add-on may have been restarted on a new IP).
		monkeypatch.setenv(SUPERVISOR_TOKEN_ENV, "sup")
		session = self._session_with_addons(
			[{"slug": "5c53de3b_esphome", "name": "ESPHome", "state": "started"}]
		)
		session.request.return_value.__aenter__.side_effect = aiohttp.ClientError("no route")
		client = _client(session)
		status, body, error = await client.execute("GET", "/devices", None, "esphome")
		assert status == 0 and "Check the ESPHome add-on is running" in error
		assert client._esphome_base_cache is None
		# Second call re-runs discovery rather than reusing a stale base.
		await client.execute("GET", "/devices", None, "esphome")
		assert session.get.call_count == 2


class TestMessageHandling:
	@pytest.mark.asyncio
	async def test_ha_rpc_sends_response(self, monkeypatch):
		session, _ = _mock_session_with_response()
		client = _client(session)
		client.execute = AsyncMock(return_value=(200, '{"ok":true}', None))
		ws = AsyncMock()
		await client._handle_rpc(ws, {"requestId": "r1", "method": "GET", "path": "/api/states", "body": None})
		ws.send_str.assert_called_once()
		sent = json.loads(ws.send_str.call_args[0][0])
		assert sent == {"type": "ha_rpc_response", "requestId": "r1", "status": 200, "body": '{"ok":true}'}

	@pytest.mark.asyncio
	async def test_ha_rpc_error_included(self):
		session, _ = _mock_session_with_response()
		client = _client(session)
		client.execute = AsyncMock(return_value=(0, None, "offline"))
		ws = AsyncMock()
		await client._handle_rpc(ws, {"requestId": "r2", "method": "GET", "path": "/api/x"})
		sent = json.loads(ws.send_str.call_args[0][0])
		assert sent["status"] == 0 and sent["error"] == "offline" and "body" not in sent

	@pytest.mark.asyncio
	async def test_handle_text_ping_pongs(self):
		session, _ = _mock_session_with_response()
		client = _client(session)
		ws = AsyncMock()
		await client._handle_text(ws, json.dumps({"type": "ping"}))
		sent = json.loads(ws.send_str.call_args[0][0])
		assert sent["type"] == "pong"

	@pytest.mark.asyncio
	async def test_handle_text_ignores_garbage(self):
		session, _ = _mock_session_with_response()
		client = _client(session)
		ws = AsyncMock()
		await client._handle_text(ws, "not json")
		ws.send_str.assert_not_called()

	@pytest.mark.asyncio
	async def test_handle_text_routes_ha_rpc(self):
		session, _ = _mock_session_with_response()
		client = _client(session)
		client._handle_rpc = AsyncMock()
		ws = AsyncMock()
		await client._handle_text(ws, json.dumps({"type": "ha_rpc", "requestId": "r"}))
		client._handle_rpc.assert_called_once()


class TestEnsureLocalAccessToken:
	"""Find-or-create semantics for the minted local access token."""

	@staticmethod
	def _fake_hass(owner, users=()):
		from homeassistant.auth.models import TOKEN_TYPE_LONG_LIVED_ACCESS_TOKEN

		hass = MagicMock()
		hass.auth.async_get_owner = AsyncMock(return_value=owner)
		hass.auth.async_get_users = AsyncMock(return_value=list(users))
		new_refresh = MagicMock()
		new_refresh.client_name = RELAY_TOKEN_CLIENT_NAME
		new_refresh.token_type = TOKEN_TYPE_LONG_LIVED_ACCESS_TOKEN
		hass.auth.async_create_refresh_token = AsyncMock(return_value=new_refresh)
		hass.auth.async_create_access_token = MagicMock(return_value="jwt-token")
		return hass, new_refresh

	@staticmethod
	def _user(refresh_tokens=None, *, active=True, admin=True, system=False):
		user = MagicMock()
		user.refresh_tokens = refresh_tokens or {}
		user.is_active = active
		user.is_admin = admin
		user.system_generated = system
		user.name = "Owner"
		return user

	@pytest.mark.asyncio
	async def test_creates_token_for_owner_when_missing(self):
		owner = self._user()
		hass, new_refresh = self._fake_hass(owner)
		token = await async_ensure_local_access_token(hass)
		assert token == "jwt-token"
		_args, kwargs = hass.auth.async_create_refresh_token.call_args
		assert kwargs["client_name"] == RELAY_TOKEN_CLIENT_NAME
		hass.auth.async_create_access_token.assert_called_once_with(new_refresh)

	@pytest.mark.asyncio
	async def test_reuses_existing_relay_refresh_token(self):
		from homeassistant.auth.models import TOKEN_TYPE_LONG_LIVED_ACCESS_TOKEN

		existing = MagicMock()
		existing.client_name = RELAY_TOKEN_CLIENT_NAME
		existing.token_type = TOKEN_TYPE_LONG_LIVED_ACCESS_TOKEN
		owner = self._user({"rt1": existing})
		hass, _ = self._fake_hass(owner)
		token = await async_ensure_local_access_token(hass)
		assert token == "jwt-token"
		hass.auth.async_create_refresh_token.assert_not_called()
		hass.auth.async_create_access_token.assert_called_once_with(existing)

	@pytest.mark.asyncio
	async def test_ignores_other_long_lived_tokens(self):
		from homeassistant.auth.models import TOKEN_TYPE_LONG_LIVED_ACCESS_TOKEN

		other = MagicMock()
		other.client_name = "Some other tool"
		other.token_type = TOKEN_TYPE_LONG_LIVED_ACCESS_TOKEN
		owner = self._user({"rt1": other})
		hass, _ = self._fake_hass(owner)
		await async_ensure_local_access_token(hass)
		hass.auth.async_create_refresh_token.assert_called_once()

	@pytest.mark.asyncio
	async def test_falls_back_to_active_admin_without_owner(self):
		admin = self._user()
		non_admin = self._user(admin=False)
		hass, _ = self._fake_hass(None, users=[non_admin, admin])
		token = await async_ensure_local_access_token(hass)
		assert token == "jwt-token"
		args, _kwargs = hass.auth.async_create_refresh_token.call_args
		assert args[0] is admin

	@pytest.mark.asyncio
	async def test_returns_none_without_any_eligible_user(self):
		system_user = self._user(system=True)
		hass, _ = self._fake_hass(None, users=[system_user])
		assert await async_ensure_local_access_token(hass) is None
		hass.auth.async_create_refresh_token.assert_not_called()


class TestDeviceHelpers:
	@pytest.mark.asyncio
	async def test_request_device_code(self):
		session, _ = _mock_session_with_response(
			text=json.dumps({"device_code": "dc", "user_code": "AAAA-BBBB"})
		)
		# json() must return the parsed body
		session.post.return_value.__aenter__.return_value.json.return_value = {
			"device_code": "dc", "user_code": "AAAA-BBBB"
		}
		result = await async_request_device_code(session, "https://vome.io", name="My HA")
		assert result["device_code"] == "dc"
		args, kwargs = session.post.call_args
		assert args[0] == "https://vome.io/api/v1/relay/device/code"
		assert kwargs["json"] == {"name": "My HA"}

	@pytest.mark.asyncio
	async def test_poll_device_token(self):
		session, _ = _mock_session_with_response()
		session.post.return_value.__aenter__.return_value.json.return_value = {"status": "pending"}
		result = await async_poll_device_token(session, "https://vome.io", "dc")
		assert result == {"status": "pending"}
		args, kwargs = session.post.call_args
		assert args[0] == "https://vome.io/api/v1/relay/device/token"
		assert kwargs["json"] == {"device_code": "dc"}
