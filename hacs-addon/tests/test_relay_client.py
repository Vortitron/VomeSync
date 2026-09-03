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
		data={},
	)


def _client_on(hass, session, **kwargs):
	return RelayClient(hass, server_id="rly-1", secret="sek", session=session, **kwargs)


def _mock_session_for_forward(status=200, body=b"ok", headers=None):
	"""Session for the *forwarding* path, which reads bytes via resp.read().

	The shared _mock_session_with_response only fills .text, which is what
	execute() uses — forwarding needs read() to return real bytes.
	"""
	resp = AsyncMock()
	resp.status = status
	resp.read = AsyncMock(return_value=body)
	resp.headers = MagicMock()
	resp.headers.items = MagicMock(return_value=list((headers or {}).items()))
	session = AsyncMock(spec=aiohttp.ClientSession)
	session.request.return_value.__aenter__.return_value = resp
	return session, resp


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
	async def test_save_config_writes_over_the_ws_api(self):
		# ESPHome deleted /edit when it split the dashboard out; the path now
		# serves the single-page app, so a write there went nowhere at all. The
		# relay contract above is unchanged — the translation happens here.
		dash = _FakeWsDashboard(responder=lambda c, mid, a: [{"message_id": mid, "result": None}])
		client = _client(_session_for_ws(dash), esphome_url="http://esp:6052")
		yaml = "esphome:\n  name: x\n"

		status, body, error = await client.execute(
			"POST", "/edit?configuration=x.yaml", yaml, "esphome"
		)

		assert status == 200 and error is None
		assert dash.sent[0]["command"] == "devices/update_config"
		assert dash.sent[0]["args"] == {"configuration": "x.yaml", "content": yaml}

	@pytest.mark.asyncio
	async def test_get_config_reads_over_the_ws_api(self):
		dash = _FakeWsDashboard(
			responder=lambda c, mid, a: [{"message_id": mid, "result": "esphome:\n  name: x\n"}]
		)
		client = _client(_session_for_ws(dash), esphome_url="http://esp:6052")

		status, body, error = await client.execute("GET", "/edit?configuration=x.yaml", None, "esphome")

		assert status == 200 and error is None
		assert body == "esphome:\n  name: x\n"
		assert dash.sent[0]["command"] == "devices/get_config"

	@pytest.mark.asyncio
	async def test_migrate_reports_the_rules_a_config_still_needs(self):
		# ESPHome shows these as a banner in its own UI, which an agent never
		# sees — so it goes on writing deprecated spellings.
		change = {
			"kind": "action",
			"old": "homeassistant.service",
			"new": "homeassistant.action",
			"since": "2024.8",
			"required": False,
		}

		def responder(command, mid, args):
			if command == "devices/get_config":
				return [{"message_id": mid, "result": "esphome:\n  name: x\n"}]
			return [{"message_id": mid, "result": {"yaml_diff": {}, "changes": [change]}}]

		dash = _FakeWsDashboard(responder=responder)
		client = _client(_session_for_ws(dash), esphome_url="http://esp:6052")

		status, body, error = await client.execute(
			"GET", "/migrate?configuration=x.yaml", None, "esphome"
		)

		assert status == 200 and error is None
		report = json.loads(body)
		assert report["migrations_pending"] is True
		assert report["required"] is False
		assert report["changes"][0]["new"] == "homeassistant.action"
		assert [f["command"] for f in dash.sent] == ["devices/get_config", "editor/migrate_config"]

	@pytest.mark.asyncio
	async def test_migrate_reports_a_clean_config_as_nothing_to_do(self):
		def responder(command, mid, args):
			if command == "devices/get_config":
				return [{"message_id": mid, "result": "esphome:\n"}]
			return [{"message_id": mid, "result": {"yaml_diff": None, "changes": []}}]

		dash = _FakeWsDashboard(responder=responder)
		client = _client(_session_for_ws(dash), esphome_url="http://esp:6052")
		status, body, _err = await client.execute(
			"GET", "/migrate?configuration=x.yaml", None, "esphome"
		)
		assert status == 200
		assert json.loads(body)["migrations_pending"] is False

	@pytest.mark.asyncio
	async def test_migrate_flags_a_rename_the_installed_esphome_already_rejects(self):
		# "required" is the difference between tidy-up and "this stops compiling".
		def responder(command, mid, args):
			if command == "devices/get_config":
				return [{"message_id": mid, "result": "esphome:\n"}]
			return [{"message_id": mid, "result": {"changes": [{"old": "a", "new": "b", "required": True}]}}]

		dash = _FakeWsDashboard(responder=responder)
		client = _client(_session_for_ws(dash), esphome_url="http://esp:6052")
		_status, body, _err = await client.execute(
			"GET", "/migrate?configuration=x.yaml", None, "esphome"
		)
		assert json.loads(body)["required"] is True

	@pytest.mark.asyncio
	async def test_config_over_ws_rejects_a_hostile_filename(self):
		dash = _FakeWsDashboard()
		client = _client(_session_for_ws(dash), esphome_url="http://esp:6052")
		status, _body, error = await client.execute(
			"GET", "/edit?configuration=../../secrets.yaml", None, "esphome"
		)
		assert status == 0 and "Invalid ESPHome configuration filename" in error
		assert dash.sent == []

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
		self.sent_json: list[dict] = []
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

	async def send_json(self, obj):
		self.sent_json.append(obj)

	async def send_bytes(self, b):
		self.sent_bytes.append(b)

	async def close(self):
		self.closed = True


class _FakeWsDashboard:
	"""Stand-in for esphome-device-builder's multiplexed ``/ws`` endpoint.

	Opens with a server-info frame like the real one, then answers each command
	through ``responder`` so a test can script the exact frame sequence it wants
	to see translated.
	"""

	def __init__(self, *, requires_auth=False, responder=None):
		self.sent: list[dict] = []
		self.closed = False
		self._queue: list[dict] = [{
			"server_version": "1.13.1",
			"esphome_version": "2026.8.2",
			"port": 6052,
			"ha_addon": True,
			"requires_auth": requires_auth,
		}]
		self._responder = responder or (lambda command, message_id, args: [])

	async def send_json(self, obj):
		self.sent.append(obj)
		self._queue.extend(
			self._responder(obj.get("command"), obj.get("message_id"), obj.get("args") or {})
		)

	async def receive(self):
		if not self._queue:
			return _FakeMsg(aiohttp.WSMsgType.CLOSED, None)
		return _FakeMsg(aiohttp.WSMsgType.TEXT, json.dumps(self._queue.pop(0)))

	async def close(self):
		self.closed = True


def _ws_frames(ws):
	"""The line/exit frames the relay forwarded upward, decoded."""
	return [
		json.loads(p["text"])
		for p in _sent_payloads(ws)
		if p.get("type") == "ws_data" and "text" in p
	]


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

	def test_filter_strips_relay_side_proxy_hops(self):
		# The relay's nginx adds these so it can rate limit per visitor. We
		# reach core over loopback, so it is not behind those proxies — and a
		# stock install without http.use_x_forwarded_for answers 400 to every
		# request carrying X-Forwarded-For, taking the whole domain down.
		pairs = [
			("Content-Type", "text/html"),
			("X-Forwarded-For", "203.0.113.9, 10.0.0.1"),
			("X-Real-IP", "203.0.113.9"),
			("X-Forwarded-Proto", "https"),
			("X-Forwarded-Host", "gamlabio.home.vome.io"),
			("X-Forwarded-Port", "443"),
			("Forwarded", "for=203.0.113.9;proto=https"),
		]
		out = _filter_forward_headers(pairs)
		assert out == [["Content-Type", "text/html"]]

	def test_notes_the_friendly_host_it_was_served_on(self):
		# Core is reached over loopback and so cannot learn its own public
		# name; watching the forwarded traffic is the only source for it.
		from custom_components.vomesync.const import DOMAIN, FORWARD_HOST_KEY
		client = _client_on(_fake_hass(), None)
		client._note_forward_host([["X-HA-Original-Host", "myhome.home.vome.io"]])
		assert client._hass.data[DOMAIN][FORWARD_HOST_KEY] == "myhome.home.vome.io"

	@pytest.mark.parametrize("bad", [
		"evil.com/../path", "evil.com host", "https://evil.com",
		"user:pw@evil.com", "", "a" * 300,
	])
	def test_refuses_a_host_it_could_not_safely_offer_as_a_url(self, bad):
		# This value is handed to the user as the address to publish, so it
		# must not be able to carry a path, credentials or a second host.
		from custom_components.vomesync.const import DOMAIN, FORWARD_HOST_KEY
		client = _client_on(_fake_hass(), None)
		client._note_forward_host([["X-HA-Original-Host", bad]])
		assert not (client._hass.data.get(DOMAIN) or {}).get(FORWARD_HOST_KEY)

	def test_a_missing_header_is_not_an_error(self):
		client = _client_on(_fake_hass(), None)
		client._note_forward_host([["Accept", "text/html"]])
		client._note_forward_host(None)

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


class TestSchemeFollowsThePortSource:
	"""hass.http and hass.config.api describe the same server, so the scheme must
	come from whichever one supplied the port — OR-ing them lets a stale flag on
	the source we did not use flip us to a scheme the server is not speaking."""

	def test_http_source_ignores_a_contradictory_api_ssl_flag(self):
		hass = _fake_hass(port=80, ssl=False, api_port=8123, api_ssl=True)
		assert resolve_local_core_url(hass) == "http://127.0.0.1:80"

	def test_api_source_uses_the_api_ssl_flag(self):
		hass = _fake_hass(port=None, api_port=8123, api_ssl=True)
		# No internal_url to prefer, so it falls through to the loopback form.
		assert resolve_local_core_url(hass) == "https://127.0.0.1:8123"


class TestWebhookForwarding:
	"""Allowlisted webhooks cross the tunnel with no login and without
	full-UI forwarding — the cloudhook equivalent. Nothing else opens up."""

	@pytest.mark.asyncio
	async def test_listed_webhook_forwards_with_forward_ui_off(self):
		session, _ = _mock_session_for_forward(status=200, body=b"ok")
		client = _client_on(_fake_hass(port=8123), session,
							forward_ui=False, webhooks=["hook1"])
		status, _h, _b, error = await client._execute_http_proxy(
			{"method": "POST", "path": "/api/webhook/hook1", "headers": []})
		assert error is None and status == 200
		assert session.request.call_args[0][1] == "http://127.0.0.1:8123/api/webhook/hook1"

	@pytest.mark.asyncio
	async def test_unlisted_webhook_is_refused_with_forward_ui_off(self):
		session, _ = _mock_session_with_response(status=200, text="ok")
		client = _client_on(_fake_hass(port=8123), session,
							forward_ui=False, webhooks=["hook1"])
		status, _h, _b, error = await client._execute_http_proxy(
			{"method": "POST", "path": "/api/webhook/other", "headers": []})
		assert status == 0 and "forwarding is disabled" in error
		session.request.assert_not_called()

	@pytest.mark.asyncio
	async def test_webhooks_do_not_open_any_other_path(self):
		# The point of the feature is that it is *narrow*.
		session, _ = _mock_session_with_response(status=200, text="[]")
		client = _client_on(_fake_hass(port=8123), session,
							forward_ui=False, webhooks=["hook1"])
		for path in ("/api/states", "/api/config", "/lovelace", "/auth/token"):
			status, _h, _b, error = await client._execute_http_proxy(
				{"method": "GET", "path": path, "headers": []})
			assert status == 0 and error, f"{path} was forwarded"
		session.request.assert_not_called()

	@pytest.mark.asyncio
	async def test_traversal_via_a_listed_id_is_refused(self):
		session, _ = _mock_session_with_response(status=200, text="ok")
		client = _client_on(_fake_hass(port=8123), session,
							forward_ui=False, webhooks=["hook1"])
		status, _h, _b, error = await client._execute_http_proxy(
			{"method": "POST", "path": "/api/webhook/hook1/../../states", "headers": []})
		assert status == 0 and error
		session.request.assert_not_called()

	@pytest.mark.asyncio
	async def test_no_webhooks_configured_changes_nothing(self):
		session, _ = _mock_session_with_response(status=200, text="ok")
		client = _client_on(_fake_hass(port=8123), session, forward_ui=False)
		status, _h, _b, error = await client._execute_http_proxy(
			{"method": "POST", "path": "/api/webhook/hook1", "headers": []})
		assert status == 0 and "forwarding is disabled" in error

	@pytest.mark.asyncio
	async def test_forward_ui_still_carries_everything_as_before(self):
		# Adding webhooks must not narrow the existing full-UI behaviour.
		session, _ = _mock_session_for_forward(status=200, body=b"ok")
		client = _client_on(_fake_hass(port=8123), session, forward_ui=True)
		status, _h, _b, error = await client._execute_http_proxy(
			{"method": "GET", "path": "/lovelace", "headers": []})
		assert error is None and status == 200


class TestEsphomeStream:
	"""The brokered path for validate/compile/upload/logs/clean.

	These are the commands that let a remote agent flash a device and read its
	logs through the relay, with no inbound exposure and without the agent ever
	touching the dashboard directly.
	"""

	@pytest.mark.asyncio
	async def test_rejects_an_unknown_command(self):
		session = _session_for_ws(_FakeLocalWS())
		client = _client(session, esphome_url="http://esp:6052")
		ws = AsyncMock()
		await client._handle_esphome_ws_open(
			ws, "s1", {"command": "rm -rf", "configuration": "lr.yaml"}
		)
		session.ws_connect.assert_not_called()
		closed = _sent_payloads(ws)[0]
		assert closed["type"] == "ws_close" and closed["code"] == 1008

	@pytest.mark.asyncio
	async def test_rejects_a_hostile_configuration_name(self):
		session = _session_for_ws(_FakeLocalWS())
		client = _client(session, esphome_url="http://esp:6052")
		for hostile in ("../../secrets.yaml", "a b.yaml", "", "x" * 200):
			ws = AsyncMock()
			await client._handle_esphome_ws_open(
				ws, "s1", {"command": "compile", "configuration": hostile}
			)
			assert _sent_payloads(ws)[0]["type"] == "ws_close", hostile
		session.ws_connect.assert_not_called()

	@pytest.mark.asyncio
	async def test_reports_why_the_dashboard_is_unavailable(self):
		session = _session_for_ws(_FakeLocalWS())
		client = _client(session)  # no configured URL, no Supervisor in tests
		ws = AsyncMock()
		await client._handle_esphome_ws_open(
			ws, "s1", {"command": "logs", "configuration": "lr.yaml"}
		)
		session.ws_connect.assert_not_called()
		closed = _sent_payloads(ws)[0]
		assert closed["type"] == "ws_close" and closed["code"] == 1011
		# The reason is the resolver's own explanation, not a generic failure.
		assert "ESPHome" in closed["reason"]

	@pytest.mark.asyncio
	async def test_streams_a_validate_and_translates_frames(self):
		# The dashboard speaks output/result on one multiplexed socket; the relay
		# above still sees the line/exit stream it always did.
		dash = _FakeWsDashboard(responder=lambda c, mid, a: [
			{"message_id": mid, "event": "output", "data": "INFO Reading configuration"},
			{"message_id": mid, "event": "output", "data": "INFO Configuration is valid!"},
			{"message_id": mid, "event": "result", "data": {"status": "completed", "exit_code": 0}},
		])
		client = _client(_session_for_ws(dash), esphome_url="http://esp:6052")
		ws = AsyncMock()

		await client._handle_esphome_ws_open(
			ws, "s1", {"command": "validate", "configuration": "lr.yaml"}
		)
		await client._ws_pumps["s1"]

		assert dash.sent[0]["command"] == "devices/validate"
		assert dash.sent[0]["args"] == {"configuration": "lr.yaml"}
		assert _ws_frames(ws) == [
			{"event": "line", "data": "INFO Reading configuration\n"},
			{"event": "line", "data": "INFO Configuration is valid!\n"},
			{"event": "exit", "code": 0},
		]
		payloads = _sent_payloads(ws)
		assert payloads[0] == {"type": "ws_open_ack", "socketId": "s1"}
		assert payloads[-1]["type"] == "ws_close"
		assert "s1" not in client._ws_local and "s1" not in client._ws_pumps

	@pytest.mark.asyncio
	async def test_compile_queues_a_job_then_follows_it(self):
		# Builds are jobs now. Going through the queue (rather than the
		# deprecated per-command socket) is also what makes an agent-triggered
		# build appear in the dashboard's own Firmware tasks panel.
		def responder(command, mid, args):
			if command == "firmware/compile":
				return [{"message_id": mid, "result": {"job_id": "job-7", "status": "queued"}}]
			return [
				{"message_id": mid, "event": "output", "data": "Compiling app..."},
				{"message_id": mid, "event": "result", "data": {"status": "completed", "exit_code": 0}},
			]

		dash = _FakeWsDashboard(responder=responder)
		client = _client(_session_for_ws(dash), esphome_url="http://esp:6052")
		ws = AsyncMock()

		await client._handle_esphome_ws_open(
			ws, "s1", {"command": "compile", "configuration": "lr.yaml"}
		)
		await client._ws_pumps["s1"]

		assert [f["command"] for f in dash.sent] == ["firmware/compile", "firmware/follow_job"]
		assert dash.sent[1]["args"] == {"job_id": "job-7"}
		assert _ws_frames(ws)[-1] == {"event": "exit", "code": 0}

	@pytest.mark.asyncio
	async def test_upload_sends_the_port_and_defaults_to_ota(self):
		def responder(command, mid, args):
			if command == "firmware/upload":
				return [{"message_id": mid, "result": {"job_id": "job-8"}}]
			return [{"message_id": mid, "event": "result", "data": {"exit_code": 0}}]

		for given, expected in ((None, "OTA"), ("192.168.1.34", "192.168.1.34")):
			dash = _FakeWsDashboard(responder=responder)
			client = _client(_session_for_ws(dash), esphome_url="http://esp:6052")
			data = {"command": "upload", "configuration": "lr.yaml"}
			if given is not None:
				data["port"] = given
			await client._handle_esphome_ws_open(AsyncMock(), "s1", data)
			await client._ws_pumps["s1"]
			assert dash.sent[0]["args"] == {"configuration": "lr.yaml", "port": expected}

	@pytest.mark.asyncio
	async def test_a_non_string_port_is_never_forwarded(self):
		def responder(command, mid, args):
			if command == "firmware/upload":
				return [{"message_id": mid, "result": {"job_id": "job-9"}}]
			return [{"message_id": mid, "event": "result", "data": {"exit_code": 0}}]

		dash = _FakeWsDashboard(responder=responder)
		client = _client(_session_for_ws(dash), esphome_url="http://esp:6052")
		await client._handle_esphome_ws_open(
			AsyncMock(),
			"s1",
			{"command": "upload", "configuration": "lr.yaml", "port": {"$ne": None}},
		)
		await client._ws_pumps["s1"]
		assert dash.sent[0]["args"]["port"] == "OTA"

	@pytest.mark.asyncio
	async def test_collapses_progress_bar_redraws(self):
		# One framework download redraws its bar hundreds of times. Forwarding
		# every redraw spends the whole line budget downstream on a bar and
		# crowds out the build output someone actually needs to read.
		def responder(command, mid, args):
			frames = [{"message_id": mid, "event": "output", "data": "INFO Downloading ESP-IDF"}]
			frames += [
				{"message_id": mid, "event": "output", "data": f"Downloading: [==  ] {pct}%"}
				for pct in range(0, 100)
			]
			frames.append({"message_id": mid, "event": "output", "data": "Downloading: [====] 100% Done..."})
			frames.append({"message_id": mid, "event": "output", "data": "INFO Compiling app..."})
			frames.append({"message_id": mid, "event": "result", "data": {"code": 0}})
			return frames

		dash = _FakeWsDashboard(responder=responder)
		client = _client(_session_for_ws(dash), esphome_url="http://esp:6052")
		ws = AsyncMock()

		await client._handle_esphome_ws_open(
			ws, "s1", {"command": "validate", "configuration": "lr.yaml"}
		)
		await client._ws_pumps["s1"]

		lines = [f["data"] for f in _ws_frames(ws) if f["event"] == "line"]
		# 101 redraws become one — the last, which carries "Done".
		assert lines == [
			"INFO Downloading ESP-IDF\n",
			"Downloading: [====] 100% Done...\n",
			"INFO Compiling app...\n",
		]

	@pytest.mark.asyncio
	async def test_keeps_adjacent_bars_that_are_different_measurements(self):
		# A build's summary ends with two lines that look exactly like progress
		# bars but are the result, not a redraw. Collapsing on "looks like a bar"
		# would report Flash and silently drop RAM.
		dash = _FakeWsDashboard(responder=lambda c, mid, a: [
			{"message_id": mid, "event": "output",
			 "data": "RAM:   [======    ]  63.7% (used 79324 bytes from 124580 bytes)"},
			{"message_id": mid, "event": "output",
			 "data": "Flash: [=======   ]  71.2% (used 1307351 bytes from 1835008 bytes)"},
			{"message_id": mid, "event": "result", "data": {"code": 0}},
		])
		client = _client(_session_for_ws(dash), esphome_url="http://esp:6052")
		ws = AsyncMock()

		await client._handle_esphome_ws_open(
			ws, "s1", {"command": "validate", "configuration": "lr.yaml"}
		)
		await client._ws_pumps["s1"]

		lines = [f["data"] for f in _ws_frames(ws) if f["event"] == "line"]
		assert len(lines) == 2
		assert lines[0].startswith("RAM:") and lines[1].startswith("Flash:")

	@pytest.mark.asyncio
	async def test_a_trailing_progress_bar_is_not_swallowed(self):
		# The hold-and-flush must not lose a bar that is the final output.
		dash = _FakeWsDashboard(responder=lambda c, mid, a: [
			{"message_id": mid, "event": "output", "data": "Writing: [====] 100%"},
			{"message_id": mid, "event": "result", "data": {"code": 0}},
		])
		client = _client(_session_for_ws(dash), esphome_url="http://esp:6052")
		ws = AsyncMock()

		await client._handle_esphome_ws_open(
			ws, "s1", {"command": "validate", "configuration": "lr.yaml"}
		)
		await client._ws_pumps["s1"]

		lines = [f["data"] for f in _ws_frames(ws) if f["event"] == "line"]
		assert lines == ["Writing: [====] 100%\n"]

	@pytest.mark.asyncio
	async def test_reads_the_exit_status_of_both_terminal_shapes(self):
		# A streamed subprocess reports `code`; a firmware job reports
		# `exit_code`. Reading only one left the other's exit as None, which
		# reads as failure even when the command plainly succeeded.
		for payload in ({"success": True, "code": 0}, {"status": "completed", "exit_code": 0}):
			dash = _FakeWsDashboard(responder=lambda c, mid, a, p=payload: [
				{"message_id": mid, "event": "output", "data": "INFO Configuration is valid!"},
				{"message_id": mid, "event": "result", "data": p},
			])
			client = _client(_session_for_ws(dash), esphome_url="http://esp:6052")
			ws = AsyncMock()
			await client._handle_esphome_ws_open(
				ws, "s1", {"command": "validate", "configuration": "lr.yaml"}
			)
			await client._ws_pumps["s1"]
			assert _ws_frames(ws)[-1] == {"event": "exit", "code": 0}, payload

	@pytest.mark.asyncio
	async def test_surfaces_a_refused_command_as_output_then_failure(self):
		dash = _FakeWsDashboard(responder=lambda c, mid, a: [
			{"message_id": mid, "error_code": "not_found", "details": "no such device"},
		])
		client = _client(_session_for_ws(dash), esphome_url="http://esp:6052")
		ws = AsyncMock()

		await client._handle_esphome_ws_open(
			ws, "s1", {"command": "validate", "configuration": "lr.yaml"}
		)
		await client._ws_pumps["s1"]

		frames = _ws_frames(ws)
		assert "no such device" in frames[0]["data"]
		assert frames[-1] == {"event": "exit", "code": 1}

	@pytest.mark.asyncio
	async def test_refuses_a_dashboard_that_demands_authentication(self):
		# Vome holds no dashboard credential, so say that plainly rather than
		# hanging or reporting a generic transport failure.
		dash = _FakeWsDashboard(requires_auth=True)
		client = _client(_session_for_ws(dash), esphome_url="http://esp:6052")
		ws = AsyncMock()

		await client._handle_esphome_ws_open(
			ws, "s1", {"command": "logs", "configuration": "lr.yaml"}
		)
		await client._ws_pumps["s1"]

		closed = _sent_payloads(ws)[-1]
		assert closed["type"] == "ws_close" and "authentication" in closed["reason"]

	@pytest.mark.asyncio
	async def test_routed_from_ws_open_even_when_ui_forwarding_is_off(self):
		# ESPHome streaming is its own target and must not depend on the
		# full-UI forwarding opt-in.
		session = _session_for_ws(_FakeLocalWS())
		client = _client(session, esphome_url="http://esp:6052")
		client._handle_esphome_ws_open = AsyncMock()
		await client._handle_ws_open(
			AsyncMock(),
			{"socketId": "s1", "target": "esphome", "command": "logs",
			 "configuration": "lr.yaml"},
		)
		client._handle_esphome_ws_open.assert_called_once()
