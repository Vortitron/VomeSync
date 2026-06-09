# flake8: noqa
"""Tests for the Vome relay client (outbound tunnel) on the component side.

Covers the bits that make the relay safe and correct without a live WebSocket:
* execute() only runs /api paths, prefers the Supervisor token, falls back to a
  configured local token, and turns local failures into status 0;
* ha_rpc handling replies with a well-formed ha_rpc_response;
* the device-authorisation HTTP helpers post to the right endpoints.

Mirrors the AsyncMock(session) style used in test_api_client.py.
"""
import json
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest

from custom_components.vomesync.relay_client import (
	RelayClient,
	async_poll_device_token,
	async_request_device_code,
)
from custom_components.vomesync.const import (
	SUPERVISOR_CORE_BASE,
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
		monkeypatch.delenv(SUPERVISOR_TOKEN_ENV, raising=False)
		session, _ = _mock_session_with_response()
		client = _client(session)  # no local_token
		status, body, error = await client.execute("GET", "/api/states", None)
		assert status == 0 and "Supervisor token" in error
		session.request.assert_not_called()

	@pytest.mark.asyncio
	async def test_supervisor_token_path(self, monkeypatch):
		monkeypatch.setenv(SUPERVISOR_TOKEN_ENV, "supervisor-tok")
		session, _ = _mock_session_with_response(status=200, text='{"state":"on"}')
		client = _client(session)
		status, body, error = await client.execute("GET", "/api/states/light.k", None)
		assert status == 200 and body == '{"state":"on"}' and error is None
		args, kwargs = session.request.call_args
		assert args[0] == "GET"
		assert args[1] == SUPERVISOR_CORE_BASE + "/api/states/light.k"
		assert kwargs["headers"]["Authorization"] == "Bearer supervisor-tok"

	@pytest.mark.asyncio
	async def test_local_token_fallback(self, monkeypatch):
		monkeypatch.delenv(SUPERVISOR_TOKEN_ENV, raising=False)
		session, _ = _mock_session_with_response(status=201, text="")
		client = _client(session, local_token="llt", local_url="http://127.0.0.1:8123")
		status, body, error = await client.execute("POST", "/api/services/light/turn_on", {"entity_id": "light.k"})
		assert status == 201 and error is None
		args, kwargs = session.request.call_args
		assert args[1] == "http://127.0.0.1:8123/api/services/light/turn_on"
		assert kwargs["headers"]["Authorization"] == "Bearer llt"
		assert kwargs["json"] == {"entity_id": "light.k"}

	@pytest.mark.asyncio
	async def test_timeout_is_status_zero(self, monkeypatch):
		monkeypatch.setenv(SUPERVISOR_TOKEN_ENV, "tok")
		session = AsyncMock(spec=aiohttp.ClientSession)
		import asyncio
		session.request.return_value.__aenter__.side_effect = asyncio.TimeoutError()
		client = _client(session)
		status, body, error = await client.execute("GET", "/api/states", None)
		assert status == 0 and "timed out" in error

	@pytest.mark.asyncio
	async def test_client_error_is_status_zero(self, monkeypatch):
		monkeypatch.setenv(SUPERVISOR_TOKEN_ENV, "tok")
		session = AsyncMock(spec=aiohttp.ClientSession)
		session.request.return_value.__aenter__.side_effect = aiohttp.ClientError("boom")
		client = _client(session)
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

	@pytest.mark.asyncio
	async def test_discovery_via_supervisor(self, monkeypatch):
		monkeypatch.setenv(SUPERVISOR_TOKEN_ENV, "sup")
		session, _ = _mock_session_with_response(status=200, text="[]")
		disc = AsyncMock()
		disc.status = 200
		disc.json.return_value = {
			"data": {"addons": [{"slug": "5c53de3b_esphome", "name": "ESPHome"}]}
		}
		session.get.return_value.__aenter__.return_value = disc
		client = _client(session)  # no explicit esphome_url
		status, body, error = await client.execute("GET", "/devices", None, "esphome")
		assert status == 200 and error is None
		args, kwargs = session.request.call_args
		assert args[1] == "http://5c53de3b-esphome:6052/devices"

	@pytest.mark.asyncio
	async def test_no_dashboard_found(self, monkeypatch):
		monkeypatch.delenv(SUPERVISOR_TOKEN_ENV, raising=False)
		session, _ = _mock_session_with_response()
		client = _client(session)  # no url, no supervisor token
		status, body, error = await client.execute("GET", "/devices", None, "esphome")
		assert status == 0 and "ESPHome dashboard not found" in error
		session.request.assert_not_called()


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
