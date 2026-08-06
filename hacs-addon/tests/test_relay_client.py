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
import asyncio
import base64
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest

import custom_components.vomesync.relay_client as rc
from custom_components.vomesync.relay_client import (
	RELAY_TOKEN_CLIENT_NAME,
	RelayClient,
	_filter_forward_headers,
	_safe_path_portion,
	_to_ws_url,
	async_ensure_local_access_token,
	async_poll_device_token,
	async_request_device_code,
	resolve_local_core_url,
)
from custom_components.vomesync.const import (
	DEFAULT_LOCAL_CORE_URL,
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


def _fake_hass(
	*,
	port=None,
	ssl=False,
	server_host=None,
	api_port=None,
	api_ssl=False,
	internal_url=None,
):
	"""A minimal stand-in for the bits of hass the URL resolver reads."""
	return SimpleNamespace(
		http=SimpleNamespace(
			server_port=port,
			ssl_certificate="/ssl/fullchain.pem" if ssl else None,
			server_host=server_host,
		),
		config=SimpleNamespace(
			api=SimpleNamespace(port=api_port, use_ssl=api_ssl),
			internal_url=internal_url,
		),
	)


def _client_on(hass, session, **kwargs):
	return RelayClient(hass, server_id="rly-1", secret="sek", session=session, **kwargs)


class TestSafePathPortion:
	def test_returns_portion_without_query(self):
		assert _safe_path_portion("/api/states") == "/api/states"
		assert _safe_path_portion("/edit?configuration=x.yaml") == "/edit"

	def test_rejects_non_absolute_or_non_string(self):
		assert _safe_path_portion("api/states") is None
		assert _safe_path_portion(None) is None
		assert _safe_path_portion(123) is None

	def test_rejects_dot_segments_literal_and_encoded(self):
		# URL clients normalise '..' when building the URL, so these would
		# otherwise escape a startswith('/api/') allowlist.
		assert _safe_path_portion("/api/../auth/token") is None
		assert _safe_path_portion("/api/%2e%2e/auth/token") is None
		assert _safe_path_portion("/api/./states") is None
		assert _safe_path_portion("/edit/../delete?configuration=x") is None


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
	async def test_rejects_dot_segment_escape(self):
		session, _ = _mock_session_with_response()
		client = _client(session, local_token="llt")
		for hostile in ("/api/../auth/token", "/api/%2e%2e/auth/token"):
			status, body, error = await client.execute("GET", hostile, None)
			assert status == 0 and "non-/api" in error
		session.request.assert_not_called()

	@pytest.mark.asyncio
	async def test_allows_api_path_with_query(self):
		session, _ = _mock_session_with_response(status=200, text="[]")
		client = _client(session, local_token="llt")
		status, _body, error = await client.execute(
			"GET", "/api/history/period?filter_entity_id=light.k", None
		)
		assert status == 200 and error is None
		args, _kwargs = session.request.call_args
		assert args[1] == "http://127.0.0.1:8123/api/history/period?filter_entity_id=light.k"

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
	async def test_websocket_list_dashboards(self):
		fake_ws = AsyncMock()
		fake_ws.receive_json = AsyncMock(side_effect=[
			{"type": "auth_required"},
			{"type": "auth_ok"},
			{"type": "result", "id": 1, "success": True, "result": [{"url_path": "lovelace"}]},
		])
		fake_ws.send_json = AsyncMock()
		fake_cm = MagicMock()
		fake_cm.__aenter__ = AsyncMock(return_value=fake_ws)
		fake_cm.__aexit__ = AsyncMock(return_value=False)
		session = AsyncMock(spec=aiohttp.ClientSession)
		session.ws_connect = MagicMock(return_value=fake_cm)
		client = _client(session, local_token="llt")
		status, body, error = await client.execute(
			None, None, {"type": "lovelace/dashboards/list"}, target="websocket"
		)
		assert status == 200 and error is None
		assert json.loads(body) == [{"url_path": "lovelace"}]
		session.ws_connect.assert_called_once()

	@pytest.mark.asyncio
	async def test_websocket_registry_list_allowed(self):
		fake_ws = AsyncMock()
		fake_ws.receive_json = AsyncMock(side_effect=[
			{"type": "auth_required"},
			{"type": "auth_ok"},
			{"type": "result", "id": 1, "success": True, "result": [{"area_id": "kitchen"}]},
		])
		fake_ws.send_json = AsyncMock()
		fake_cm = MagicMock()
		fake_cm.__aenter__ = AsyncMock(return_value=fake_ws)
		fake_cm.__aexit__ = AsyncMock(return_value=False)
		session = AsyncMock(spec=aiohttp.ClientSession)
		session.ws_connect = MagicMock(return_value=fake_cm)
		client = _client(session, local_token="llt")
		status, body, error = await client.execute(
			None, None, {"type": "config/area_registry/list"}, target="websocket"
		)
		assert status == 200 and error is None
		assert json.loads(body) == [{"area_id": "kitchen"}]

	@pytest.mark.asyncio
	async def test_websocket_rejects_malformed_command(self):
		session, _ = _mock_session_with_response()
		client = _client(session, local_token="llt")
		status, body, error = await client.execute(
			None, None, {"type": "not valid"}, target="websocket"
		)
		assert status == 0 and "malformed" in error
		session.ws_connect.assert_not_called()

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
	async def test_rejects_prefix_lookalikes_and_traversal(self):
		# startswith() would have passed all of these; exact matching must not.
		session, _ = _mock_session_with_response()
		client = _client(session, esphome_url="http://esp:6052")
		for hostile in (
			"/devices-x",
			"/versions",
			"/editanything",
			"/edit/../delete?configuration=x.yaml",
		):
			status, _body, error = await client.execute("GET", hostile, None, "esphome")
			assert status == 0 and "non-allowlisted" in error, hostile
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

	# The official add-on's shape: host-networked, web port disabled, dashboard
	# behind a dynamic ingress port (nginx admits only Supervisor + localhost).
	_INGRESS_INFO = {"ingress": True, "ingress_port": 64279, "network": {"6052/tcp": None}}

	@staticmethod
	def _session_with_addons(addons, info=None, info_status=200, status=200, text="[]"):
		"""Mock session serving /addons (list), /addons/<slug>/info, and the dashboard."""
		session, _ = _mock_session_with_response(status=status, text=text)
		list_resp = AsyncMock()
		list_resp.status = 200
		list_resp.json.return_value = {"data": {"addons": addons}}
		info_resp = AsyncMock()
		info_resp.status = info_status
		info_resp.json.return_value = {"data": info or {}}

		def _get(url, **_kwargs):
			cm = AsyncMock()
			cm.__aenter__.return_value = info_resp if url.endswith("/info") else list_resp
			return cm

		session.get = MagicMock(side_effect=_get)
		return session

	@pytest.mark.asyncio
	async def test_discovery_via_supervisor_uses_ingress_port(self, monkeypatch):
		# Default add-on install: nothing listens on <hostname>:6052; the
		# dashboard is only reachable on localhost at the ingress port.
		monkeypatch.setenv(SUPERVISOR_TOKEN_ENV, "sup")
		session = self._session_with_addons(
			[{"slug": "5c53de3b_esphome", "name": "ESPHome", "state": "started"}],
			info=self._INGRESS_INFO,
		)
		client = _client(session)  # no explicit esphome_url
		status, body, error = await client.execute("GET", "/devices", None, "esphome")
		assert status == 200 and error is None
		args, kwargs = session.request.call_args
		assert args[1] == "http://127.0.0.1:64279/devices"

	@pytest.mark.asyncio
	async def test_discovery_prefers_mapped_web_port(self, monkeypatch):
		# A user-enabled web port wins over ingress (it is the documented API).
		monkeypatch.setenv(SUPERVISOR_TOKEN_ENV, "sup")
		session = self._session_with_addons(
			[{"slug": "5c53de3b_esphome", "name": "ESPHome", "state": "started"}],
			info={"ingress": True, "ingress_port": 64279, "network": {"6052/tcp": 6052}},
		)
		client = _client(session)
		status, body, error = await client.execute("GET", "/devices", None, "esphome")
		assert status == 200 and error is None
		args, _kwargs = session.request.call_args
		assert args[1] == "http://5c53de3b-esphome:6052/devices"

	@pytest.mark.asyncio
	async def test_discovery_falls_back_when_info_unavailable(self, monkeypatch):
		# If the info call fails, fall back to the legacy <hostname>:6052 guess
		# rather than refusing outright (covers third-party dashboards).
		monkeypatch.setenv(SUPERVISOR_TOKEN_ENV, "sup")
		session = self._session_with_addons(
			[{"slug": "a0d7b954_esphome", "name": "ESPHome", "state": "started"}],
			info_status=500,
		)
		client = _client(session)
		status, body, error = await client.execute("GET", "/devices", None, "esphome")
		assert status == 200 and error is None
		args, _kwargs = session.request.call_args
		assert args[1] == "http://a0d7b954-esphome:6052/devices"

	@pytest.mark.asyncio
	async def test_discovery_skips_stopped_addon(self, monkeypatch):
		# A stopped add-on is unreachable: report it, don't connect.
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
		], info=self._INGRESS_INFO)
		client = _client(session)
		status, body, error = await client.execute("GET", "/devices", None, "esphome")
		assert status == 200 and error is None
		args, _kwargs = session.request.call_args
		assert args[1] == "http://127.0.0.1:64279/devices"

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
			[{"slug": "5c53de3b_esphome", "name": "ESPHome", "state": "started"}],
			info=self._INGRESS_INFO,
		)
		session.request.return_value.__aenter__.side_effect = aiohttp.ClientError("no route")
		client = _client(session)
		status, body, error = await client.execute("GET", "/devices", None, "esphome")
		assert status == 0 and "Check the ESPHome add-on is running" in error
		assert client._esphome_base_cache is None
		# Second call re-runs discovery (list + info per attempt) rather than
		# reusing a stale base.
		await client.execute("GET", "/devices", None, "esphome")
		assert session.get.call_count == 4


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


# ── Full-UI forwarding (the paid friendly-domain remote access) ──────────────

def _mock_session_for_http(status=200, headers=None, body=b""):
	"""Session whose request() yields a response with read()/status/headers.

	Uses a real CIMultiDict so duplicate headers (several Set-Cookie) survive,
	exactly as aiohttp would surface them from the local Home Assistant.
	"""
	from multidict import CIMultiDict

	resp = AsyncMock()
	resp.status = status
	resp.read.return_value = body
	resp.headers = CIMultiDict(headers or [])
	session = AsyncMock(spec=aiohttp.ClientSession)
	session.request.return_value.__aenter__.return_value = resp
	return session, resp


class _FakeMsg:
	def __init__(self, type_, data):
		self.type = type_
		self.data = data


class _FakeLocalWS:
	"""A minimal stand-in for the local HA frontend WebSocket."""

	def __init__(self, incoming=None):
		self._incoming = list(incoming or [])
		self.sent_str: list[str] = []
		self.sent_bytes: list[bytes] = []
		self.closed = False

	def __aiter__(self):
		self._it = iter(self._incoming)
		return self

	async def __anext__(self):
		try:
			return next(self._it)
		except StopIteration:
			raise StopAsyncIteration

	async def send_str(self, s):
		self.sent_str.append(s)

	async def send_bytes(self, b):
		self.sent_bytes.append(b)

	async def close(self):
		self.closed = True


def _session_for_ws(fake_local):
	# aiohttp's ws_connect returns a context manager that is *also* awaitable;
	# under spec= it mocks as sync, so wire it as an AsyncMock explicitly.
	session = AsyncMock(spec=aiohttp.ClientSession)
	session.ws_connect = AsyncMock(return_value=fake_local)
	return session


def _sent_payloads(ws):
	"""Decode every JSON frame written to a mocked relay socket."""
	return [json.loads(c[0][0]) for c in ws.send_str.call_args_list]


class TestForwardHelpers:
	def test_filter_strips_hop_by_hop_and_keeps_duplicates(self):
		pairs = [
			("Content-Type", "text/html"),
			("Set-Cookie", "a=1"), ("Set-Cookie", "b=2"),
			("Transfer-Encoding", "chunked"), ("Content-Length", "5"),
			("Connection", "keep-alive"), ("Host", "x"),
		]
		out = _filter_forward_headers(pairs)
		flat = [f"{k}: {v}" for k, v in out]
		assert "Content-Type: text/html" in flat
		assert flat.count("Set-Cookie: a=1") == 1 and "Set-Cookie: b=2" in flat
		lowered = {k.lower() for k, _ in out}
		assert not (lowered & {"transfer-encoding", "content-length", "connection", "host"})

	def test_filter_strips_content_encoding(self):
		# aiohttp's resp.read() transparently gunzips the body but leaves the
		# original Content-Encoding header on resp.headers; forwarding it
		# verbatim makes the browser try to gunzip already-plain bytes
		# (net::ERR_CONTENT_DECODING_FAILED).
		pairs = [("Content-Type", "text/html"), ("Content-Encoding", "gzip")]
		out = _filter_forward_headers(pairs)
		lowered = {k.lower() for k, _ in out}
		assert "content-encoding" not in lowered
		assert "Content-Type: text/html" in [f"{k}: {v}" for k, v in out]

	def test_filter_accepts_dict_input(self):
		out = _filter_forward_headers({"X-Test": "1", "Connection": "close"})
		assert out == [["X-Test", "1"]]

	def test_to_ws_url_maps_scheme(self):
		assert _to_ws_url("http://127.0.0.1:8123", "/api/websocket") == "ws://127.0.0.1:8123/api/websocket"
		assert _to_ws_url("https://ha.local", "/api/websocket") == "wss://ha.local/api/websocket"
		assert _to_ws_url(None, "/api/websocket") == "ws://127.0.0.1:8123/api/websocket"


class TestForwardHttp:
	@pytest.mark.asyncio
	async def test_disabled_by_default(self):
		session, _ = _mock_session_for_http()
		client = _client(session)  # forward_ui defaults to False
		status, headers, body_b64, error = await client._execute_http_proxy(
			{"method": "GET", "path": "/lovelace"}
		)
		assert status == 0 and headers is None and "disabled" in error
		session.request.assert_not_called()

	@pytest.mark.asyncio
	async def test_rejects_non_absolute_path(self):
		session, _ = _mock_session_for_http()
		client = _client(session, forward_ui=True)
		status, _h, _b, error = await client._execute_http_proxy({"path": "lovelace"})
		assert status == 0 and "non-absolute" in error
		session.request.assert_not_called()

	@pytest.mark.asyncio
	async def test_forwards_request_and_mirrors_response(self):
		body = b"<html>hi</html>"
		session, _ = _mock_session_for_http(
			status=200,
			headers=[
				("Content-Type", "text/html"),
				("Set-Cookie", "s=1"), ("Set-Cookie", "t=2"),
				("Transfer-Encoding", "chunked"),
			],
			body=body,
		)
		client = _client(session, forward_ui=True, local_url="http://127.0.0.1:8123")
		status, headers, body_b64, error = await client._execute_http_proxy({
			"method": "POST", "path": "/api/foo?x=1",
			"headers": [["Cookie", "z=9"], ["Host", "drop.me"], ["Connection", "keep-alive"]],
			"bodyB64": base64.b64encode(b"payload").decode(),
		})
		assert status == 200 and error is None
		assert base64.b64decode(body_b64) == body
		# Response headers: hop-by-hop dropped, duplicate Set-Cookie preserved.
		flat = [f"{k}: {v}" for k, v in headers]
		assert "Content-Type: text/html" in flat
		assert "Set-Cookie: s=1" in flat and "Set-Cookie: t=2" in flat
		assert all(not k.lower() == "transfer-encoding" for k, _ in headers)
		# Outgoing request: redirects not followed, body decoded, host stripped.
		args, kwargs = session.request.call_args
		assert args == ("POST", "http://127.0.0.1:8123/api/foo?x=1")
		assert kwargs["allow_redirects"] is False
		assert kwargs["data"] == b"payload"
		assert "Cookie" in kwargs["headers"]
		assert "Host" not in kwargs["headers"] and "Connection" not in kwargs["headers"]

	@pytest.mark.asyncio
	async def test_redirect_passed_through(self):
		session, _ = _mock_session_for_http(status=302, headers=[("Location", "/auth")], body=b"")
		client = _client(session, forward_ui=True)
		status, headers, _b, error = await client._execute_http_proxy({"path": "/"})
		assert status == 302 and error is None
		assert ["Location", "/auth"] in headers

	@pytest.mark.asyncio
	async def test_request_body_too_large(self, monkeypatch):
		monkeypatch.setattr(rc, "RELAY_FORWARD_MAX_BODY", 4)
		session, _ = _mock_session_for_http()
		client = _client(session, forward_ui=True)
		status, _h, _b, error = await client._execute_http_proxy({
			"path": "/api/x", "bodyB64": base64.b64encode(b"toolong").decode(),
		})
		assert status == 0 and "too large" in error
		session.request.assert_not_called()

	@pytest.mark.asyncio
	async def test_response_body_too_large(self, monkeypatch):
		monkeypatch.setattr(rc, "RELAY_FORWARD_MAX_BODY", 4)
		session, _ = _mock_session_for_http(body=b"abcdefgh")
		client = _client(session, forward_ui=True)
		status, _h, _b, error = await client._execute_http_proxy({"path": "/"})
		assert status == 0 and "too large" in error

	@pytest.mark.asyncio
	async def test_timeout_is_status_zero(self):
		session = AsyncMock(spec=aiohttp.ClientSession)
		import asyncio
		session.request.return_value.__aenter__.side_effect = asyncio.TimeoutError()
		client = _client(session, forward_ui=True)
		status, _h, _b, error = await client._execute_http_proxy({"path": "/"})
		assert status == 0 and "timed out" in error

	@pytest.mark.asyncio
	async def test_handle_http_proxy_sends_response(self):
		session, _ = _mock_session_for_http(status=200, headers=[("Content-Type", "text/plain")], body=b"ok")
		client = _client(session, forward_ui=True)
		ws = AsyncMock()
		await client._handle_http_proxy(ws, {"requestId": "h1", "method": "GET", "path": "/manifest.json"})
		sent = _sent_payloads(ws)[0]
		assert sent["type"] == "http_proxy_response" and sent["requestId"] == "h1"
		assert sent["status"] == 200 and base64.b64decode(sent["bodyB64"]) == b"ok"


class TestForwardWebSocket:
	@pytest.mark.asyncio
	async def test_ws_open_refused_when_disabled(self):
		session = _session_for_ws(_FakeLocalWS())
		client = _client(session)  # forwarding off
		ws = AsyncMock()
		await client._handle_ws_open(ws, {"socketId": "s1", "path": "/api/websocket"})
		session.ws_connect.assert_not_called()
		closed = _sent_payloads(ws)[0]
		assert closed["type"] == "ws_close" and closed["code"] == 1008

	@pytest.mark.asyncio
	async def test_ws_open_rejects_foreign_path(self):
		session = _session_for_ws(_FakeLocalWS())
		client = _client(session, forward_ui=True)
		ws = AsyncMock()
		await client._handle_ws_open(ws, {"socketId": "s1", "path": "/evil"})
		session.ws_connect.assert_not_called()
		assert _sent_payloads(ws)[0]["type"] == "ws_close"

	@pytest.mark.asyncio
	async def test_ws_open_rejects_lookalike_and_traversal_paths(self):
		session = _session_for_ws(_FakeLocalWS())
		client = _client(session, forward_ui=True)
		for hostile in ("/api/websocketX", "/api/websocket/../evil"):
			ws = AsyncMock()
			await client._handle_ws_open(ws, {"socketId": "s1", "path": hostile})
			assert _sent_payloads(ws)[0]["type"] == "ws_close", hostile
		session.ws_connect.assert_not_called()

	@pytest.mark.asyncio
	async def test_ws_open_acks_then_pumps_frames(self):
		local = _FakeLocalWS([
			_FakeMsg(aiohttp.WSMsgType.TEXT, '{"type":"auth_required"}'),
			_FakeMsg(aiohttp.WSMsgType.BINARY, b"\x00\x01"),
		])
		session = _session_for_ws(local)
		client = _client(session, forward_ui=True, local_url="http://127.0.0.1:8123")
		ws = AsyncMock()
		await client._handle_ws_open(ws, {"socketId": "s1", "path": "/api/websocket"})
		# The local socket was opened with the ws:// scheme + same path.
		assert session.ws_connect.call_args[0][0] == "ws://127.0.0.1:8123/api/websocket"
		pump = client._ws_pumps["s1"]
		await pump  # drain the (finite) fake frame source
		payloads = _sent_payloads(ws)
		assert payloads[0] == {"type": "ws_open_ack", "socketId": "s1"}
		text_frame = next(p for p in payloads if p.get("type") == "ws_data" and "text" in p)
		assert text_frame["text"] == '{"type":"auth_required"}'
		bin_frame = next(p for p in payloads if p.get("type") == "ws_data" and "dataB64" in p)
		assert base64.b64decode(bin_frame["dataB64"]) == b"\x00\x01"
		assert payloads[-1]["type"] == "ws_close"
		# Pump cleaned itself out of the live-bridge maps.
		assert "s1" not in client._ws_local and "s1" not in client._ws_pumps

	@pytest.mark.asyncio
	async def test_ws_data_forwarded_to_local(self):
		local = _FakeLocalWS()
		client = _client(_session_for_ws(local), forward_ui=True)
		client._ws_local["s1"] = local
		await client._handle_ws_data({"socketId": "s1", "text": "ping"})
		await client._handle_ws_data({"socketId": "s1", "dataB64": base64.b64encode(b"\x09").decode()})
		assert local.sent_str == ["ping"] and local.sent_bytes == [b"\x09"]

	@pytest.mark.asyncio
	async def test_ws_data_unknown_socket_is_noop(self):
		local = _FakeLocalWS()
		client = _client(_session_for_ws(local), forward_ui=True)
		await client._handle_ws_data({"socketId": "ghost", "text": "x"})
		assert local.sent_str == []

	@pytest.mark.asyncio
	async def test_ws_close_tears_down_bridge(self):
		import asyncio
		local = _FakeLocalWS()
		client = _client(_session_for_ws(local), forward_ui=True)
		client._ws_local["s1"] = local
		client._ws_pumps["s1"] = asyncio.ensure_future(asyncio.sleep(60))
		await client._handle_ws_close({"socketId": "s1"})
		assert local.closed is True
		assert "s1" not in client._ws_local and "s1" not in client._ws_pumps

	@pytest.mark.asyncio
	async def test_handle_text_routes_forwarding_messages(self):
		client = _client(AsyncMock(spec=aiohttp.ClientSession), forward_ui=True)
		client._handle_http_proxy = AsyncMock()
		client._handle_ws_open = AsyncMock()
		client._handle_ws_data = AsyncMock()
		client._handle_ws_close = AsyncMock()
		ws = AsyncMock()
		await client._handle_text(ws, json.dumps({"type": "http_proxy", "requestId": "h"}))
		await client._handle_text(ws, json.dumps({"type": "ws_open", "socketId": "s"}))
		await client._handle_text(ws, json.dumps({"type": "ws_data", "socketId": "s"}))
		await client._handle_text(ws, json.dumps({"type": "ws_close", "socketId": "s"}))
		client._handle_http_proxy.assert_called_once()
		client._handle_ws_open.assert_called_once()
		client._handle_ws_data.assert_called_once()
		client._handle_ws_close.assert_called_once()


# ── LAN TCP tunnels (raw TCP to a LAN device, e.g. RDP) ──────────────────────

class _FakeStreamReader:
	"""Yields each chunk in ``chunks`` from read(), then b"" (EOF)."""

	def __init__(self, chunks=None):
		self._chunks = list(chunks or [])

	async def read(self, _n):
		if self._chunks:
			return self._chunks.pop(0)
		return b""


class _FakeStreamWriter:
	def __init__(self):
		self.written: list[bytes] = []
		self.closed = False

	def write(self, data):
		self.written.append(data)

	async def drain(self):
		pass

	def close(self):
		self.closed = True


def _tcp_route(scheme="tcp", host="192.168.1.50", port=3389, enabled=True):
	return {
		"slug": "rdp", "name": "RDP", "host": host, "port": port,
		"scheme": scheme, "enabled": enabled, "websocket": True,
	}


class TestLanTcpOpen:
	@pytest.mark.asyncio
	async def test_tcp_scheme_opens_raw_connection_not_websocket(self, monkeypatch):
		reader, writer = _FakeStreamReader(), _FakeStreamWriter()
		open_conn = AsyncMock(return_value=(reader, writer))
		monkeypatch.setattr(rc.asyncio, "open_connection", open_conn)
		session = _session_for_ws(_FakeLocalWS())  # ws_connect must not be used
		client = _client(session, lan_routes=[_tcp_route()])
		ws = AsyncMock()
		await client._handle_lan_ws_open(ws, "s1", "rdp", "/")
		open_conn.assert_called_once_with("192.168.1.50", 3389)
		session.ws_connect.assert_not_called()
		assert "s1" in client._tcp_local
		payloads = _sent_payloads(ws)
		assert payloads[0] == {"type": "ws_open_ack", "socketId": "s1"}

	@pytest.mark.asyncio
	async def test_missing_route_closes(self):
		client = _client(AsyncMock(spec=aiohttp.ClientSession), lan_routes=[])
		ws = AsyncMock()
		await client._handle_lan_ws_open(ws, "s1", "rdp", "/")
		closed = _sent_payloads(ws)[0]
		assert closed["type"] == "ws_close" and closed["code"] == 1008
		assert "No LAN route configured" in closed["reason"]

	@pytest.mark.asyncio
	async def test_connect_failure_closes_with_reason(self, monkeypatch):
		monkeypatch.setattr(
			rc.asyncio, "open_connection", AsyncMock(side_effect=OSError("refused"))
		)
		client = _client(AsyncMock(spec=aiohttp.ClientSession), lan_routes=[_tcp_route()])
		ws = AsyncMock()
		await client._handle_lan_ws_open(ws, "s1", "rdp", "/")
		closed = _sent_payloads(ws)[0]
		assert closed["type"] == "ws_close" and closed["code"] == 1011
		assert "connect failed" in closed["reason"]
		assert "s1" not in client._tcp_local

	@pytest.mark.asyncio
	async def test_invalid_port_closes_without_connecting(self, monkeypatch):
		open_conn = AsyncMock()
		monkeypatch.setattr(rc.asyncio, "open_connection", open_conn)
		client = _client(
			AsyncMock(spec=aiohttp.ClientSession),
			lan_routes=[_tcp_route(port=0)],
		)
		ws = AsyncMock()
		await client._handle_lan_ws_open(ws, "s1", "rdp", "/")
		open_conn.assert_not_called()
		assert _sent_payloads(ws)[0]["type"] == "ws_close"

	@pytest.mark.asyncio
	async def test_http_scheme_still_uses_websocket(self):
		# Unaffected sibling behaviour: an http-scheme route still bridges via
		# ws_connect, not raw TCP — the branch in _handle_lan_ws_open must not
		# swallow the existing path.
		local = _FakeLocalWS()
		session = _session_for_ws(local)
		client = _client(session, lan_routes=[{
			"slug": "nas", "host": "192.168.1.5", "port": 80,
			"scheme": "http", "enabled": True, "websocket": True,
		}])
		ws = AsyncMock()
		await client._handle_lan_ws_open(ws, "s1", "nas", "/")
		session.ws_connect.assert_called_once()
		assert "s1" not in client._tcp_local


class TestLanTcpPumpAndData:
	@pytest.mark.asyncio
	async def test_pump_forwards_bytes_then_closes(self, monkeypatch):
		reader = _FakeStreamReader([b"\x01\x02", b"\x03"])
		writer = _FakeStreamWriter()
		monkeypatch.setattr(
			rc.asyncio, "open_connection", AsyncMock(return_value=(reader, writer))
		)
		client = _client(AsyncMock(spec=aiohttp.ClientSession), lan_routes=[_tcp_route()])
		ws = AsyncMock()
		await client._handle_lan_ws_open(ws, "s1", "rdp", "/")
		await client._tcp_pumps["s1"]  # drain the finite fake stream
		payloads = _sent_payloads(ws)
		data_frames = [p for p in payloads if p.get("type") == "ws_data"]
		assert base64.b64decode(data_frames[0]["dataB64"]) == b"\x01\x02"
		assert base64.b64decode(data_frames[1]["dataB64"]) == b"\x03"
		assert payloads[-1]["type"] == "ws_close"
		assert "s1" not in client._tcp_local and "s1" not in client._tcp_pumps
		assert writer.closed is True

	@pytest.mark.asyncio
	async def test_ws_data_writes_to_tcp_socket(self):
		writer = _FakeStreamWriter()
		client = _client(AsyncMock(spec=aiohttp.ClientSession))
		client._tcp_local["s1"] = (_FakeStreamReader(), writer)
		await client._handle_ws_data({"socketId": "s1", "dataB64": base64.b64encode(b"hi").decode()})
		assert writer.written == [b"hi"]

	@pytest.mark.asyncio
	async def test_teardown_closes_tcp_writer(self):
		writer = _FakeStreamWriter()
		client = _client(AsyncMock(spec=aiohttp.ClientSession))
		client._tcp_local["s1"] = (_FakeStreamReader(), writer)
		client._tcp_pumps["s1"] = asyncio.ensure_future(asyncio.sleep(60))
		await client._teardown_tunnel("s1")
		assert writer.closed is True
		assert "s1" not in client._tcp_local and "s1" not in client._tcp_pumps


class TestLanTcpHttpProxyRefused:
	@pytest.mark.asyncio
	async def test_tcp_route_refuses_http_proxy(self):
		client = _client(
			AsyncMock(spec=aiohttp.ClientSession), forward_ui=True, lan_routes=[_tcp_route()]
		)
		status, _h, _b, error = await client._execute_http_proxy({"method": "GET", "path": "/t/rdp/"})
		assert status == 0 and "TCP-only" in error


class TestRequestLanTcpToken:
	@staticmethod
	def _hass_with_loop():
		hass = MagicMock()
		hass.loop = asyncio.get_event_loop()
		return hass

	@pytest.mark.asyncio
	async def test_returns_token_on_response(self):
		hass = self._hass_with_loop()
		client = RelayClient(hass, server_id="rly-1", secret="sek")
		fake_ws = AsyncMock()
		client._ws = fake_ws

		async def _reply_soon():
			await asyncio.sleep(0)
			sent = json.loads(fake_ws.send_str.call_args[0][0])
			await client._handle_text(fake_ws, json.dumps({
				"type": "mint_lan_tcp_token_response",
				"requestId": sent["requestId"],
				"token": "jwt-abc",
			}))

		asyncio.ensure_future(_reply_soon())
		token, error = await client.request_lan_tcp_token("rdp", 3600)
		assert error is None and token == "jwt-abc"
		sent = json.loads(fake_ws.send_str.call_args[0][0])
		assert sent["type"] == "mint_lan_tcp_token" and sent["slug"] == "rdp"

	@pytest.mark.asyncio
	async def test_not_connected_returns_error(self):
		hass = self._hass_with_loop()
		client = RelayClient(hass, server_id="rly-1", secret="sek")
		token, error = await client.request_lan_tcp_token("rdp")
		assert token is None and "not connected" in error.lower()

	@pytest.mark.asyncio
	async def test_timeout_returns_error(self, monkeypatch):
		monkeypatch.setattr(rc, "RELAY_MINT_TOKEN_TIMEOUT", 0.01)
		hass = self._hass_with_loop()
		client = RelayClient(hass, server_id="rly-1", secret="sek")
		client._ws = AsyncMock()
		token, error = await client.request_lan_tcp_token("rdp")
		assert token is None and "Timed out" in error


class TestResolveLocalCoreUrl:
	"""HA 2026.8 made the listen port a UI setting and defaulted new installs to
	port 80, so the local core URL must come from the running instance rather
	than a constant.  Getting this wrong is silent: the relay stays connected and
	reports healthy while every dispatched request fails."""

	def test_explicit_override_wins(self):
		hass = _fake_hass(port=80)
		assert resolve_local_core_url(hass, "http://10.0.0.5:8123") == "http://10.0.0.5:8123"

	def test_override_trailing_slash_stripped(self):
		assert resolve_local_core_url(None, "http://10.0.0.5:8123/") == "http://10.0.0.5:8123"

	def test_derives_port_80(self):
		assert resolve_local_core_url(_fake_hass(port=80)) == "http://127.0.0.1:80"

	def test_derives_legacy_port_8123(self):
		assert resolve_local_core_url(_fake_hass(port=8123)) == "http://127.0.0.1:8123"

	def test_local_tls_gives_https(self):
		assert resolve_local_core_url(_fake_hass(port=8123, ssl=True)) == "https://127.0.0.1:8123"

	def test_config_api_is_used_when_http_has_no_port(self):
		hass = _fake_hass(port=None, api_port=80)
		assert resolve_local_core_url(hass) == "http://127.0.0.1:80"

	def test_config_api_ssl_flag_gives_https(self):
		hass = _fake_hass(port=None, api_port=8123, api_ssl=True)
		assert resolve_local_core_url(hass) == "https://127.0.0.1:8123"

	def test_bound_to_one_interface_dials_that_interface(self):
		# Loopback is not listening in this case, so 127.0.0.1 would fail.
		hass = _fake_hass(port=80, server_host=["192.168.1.5"])
		assert resolve_local_core_url(hass) == "http://192.168.1.5:80"

	def test_bind_list_including_loopback_prefers_loopback(self):
		hass = _fake_hass(port=80, server_host=["192.168.1.5", "127.0.0.1"])
		assert resolve_local_core_url(hass) == "http://127.0.0.1:80"

	def test_bind_all_interfaces_uses_loopback(self):
		assert resolve_local_core_url(_fake_hass(port=80, server_host=["0.0.0.0"])) == "http://127.0.0.1:80"

	def test_ipv6_bind_is_bracketed(self):
		hass = _fake_hass(port=80, server_host=["fd00::1"])
		assert resolve_local_core_url(hass) == "http://[fd00::1]:80"

	def test_falls_back_to_internal_url_without_a_port(self):
		hass = _fake_hass(internal_url="https://ha.example.com/")
		assert resolve_local_core_url(hass) == "https://ha.example.com"

	def test_ignores_nonsense_internal_url(self):
		hass = _fake_hass(internal_url="not a url")
		assert resolve_local_core_url(hass) == DEFAULT_LOCAL_CORE_URL

	def test_no_hass_falls_back_to_default(self):
		assert resolve_local_core_url(None) == DEFAULT_LOCAL_CORE_URL

	def test_out_of_range_port_falls_back(self):
		assert resolve_local_core_url(_fake_hass(port=0)) == DEFAULT_LOCAL_CORE_URL
		assert resolve_local_core_url(_fake_hass(port=99999)) == DEFAULT_LOCAL_CORE_URL

	def test_unusable_hass_never_raises(self):
		# A half-built hass (http integration not started) must not break setup.
		assert resolve_local_core_url(MagicMock()) == DEFAULT_LOCAL_CORE_URL
		assert resolve_local_core_url(SimpleNamespace()) == DEFAULT_LOCAL_CORE_URL


class TestClientUsesResolvedLocalUrl:
	@pytest.mark.asyncio
	async def test_execute_dials_the_derived_port(self):
		session, _ = _mock_session_with_response(status=200, text="[]")
		client = _client_on(_fake_hass(port=80), session, local_token="llt")
		status, _body, error = await client.execute("GET", "/api/states", None)
		assert status == 200 and error is None
		args, _kwargs = session.request.call_args
		assert args[1] == "http://127.0.0.1:80/api/states"

	@pytest.mark.asyncio
	async def test_port_change_is_picked_up_without_a_restart(self):
		# 2026.8 can change the port under a live relay — and roll it back five
		# minutes later — so the URL must not be captured at construction time.
		session, _ = _mock_session_with_response(status=200, text="[]")
		hass = _fake_hass(port=8123)
		client = _client_on(hass, session, local_token="llt")
		await client.execute("GET", "/api/states", None)
		assert session.request.call_args[0][1] == "http://127.0.0.1:8123/api/states"

		hass.http.server_port = 80
		await client.execute("GET", "/api/states", None)
		assert session.request.call_args[0][1] == "http://127.0.0.1:80/api/states"

	@pytest.mark.asyncio
	async def test_override_still_wins_over_derivation(self):
		session, _ = _mock_session_with_response(status=200, text="[]")
		client = _client_on(
			_fake_hass(port=80), session, local_token="llt", local_url="http://10.0.0.5:8123"
		)
		await client.execute("GET", "/api/states", None)
		assert session.request.call_args[0][1] == "http://10.0.0.5:8123/api/states"

	def test_ws_bridge_target_follows_the_derived_port(self):
		client = _client_on(_fake_hass(port=80), AsyncMock(spec=aiohttp.ClientSession))
		assert _to_ws_url(client.local_url, "/api/websocket") == "ws://127.0.0.1:80/api/websocket"

	def test_ws_bridge_target_uses_wss_for_local_tls(self):
		client = _client_on(_fake_hass(port=8123, ssl=True), AsyncMock(spec=aiohttp.ClientSession))
		assert _to_ws_url(client.local_url, "/api/websocket") == "wss://127.0.0.1:8123/api/websocket"


class TestLocalTlsPrefersInternalUrl:
	"""An instance terminating its own TLS holds a cert for a hostname, so
	dialling https://127.0.0.1:port fails verification even with the right port."""

	def test_https_prefers_internal_url_over_loopback(self):
		hass = _fake_hass(port=8123, ssl=True, internal_url="https://ha.example.com:8123")
		assert resolve_local_core_url(hass) == "https://ha.example.com:8123"

	def test_https_without_internal_url_still_uses_loopback(self):
		hass = _fake_hass(port=8123, ssl=True)
		assert resolve_local_core_url(hass) == "https://127.0.0.1:8123"

	def test_plain_http_ignores_internal_url(self):
		# No cert to satisfy, so loopback is the cheaper and more reliable dial.
		hass = _fake_hass(port=80, internal_url="https://ha.example.com")
		assert resolve_local_core_url(hass) == "http://127.0.0.1:80"

	def test_override_still_beats_internal_url(self):
		hass = _fake_hass(port=8123, ssl=True, internal_url="https://ha.example.com")
		assert resolve_local_core_url(hass, "http://127.0.0.1:8123") == "http://127.0.0.1:8123"
