# flake8: noqa
"""Vome on Home Assistant's "Model Context Protocol" page (vome/panel/mcp_server.py).

ESPHome Device Builder announced an MCP server to the Supervisor in October
2026 and Home Assistant listed it with an Add button.  The Vome app now does
the same, serving Vome's own read-only tools through the integration, called
as nobody in particular.
"""
import json
import sys
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "vome" / "panel"))
import mcp_server  # noqa: E402


TOOLS = {
	"scopes": ["read"],
	"tools": [
		{"name": "vome_status", "description": "How Vome stands here.", "group": "vome",
		 "parameters": {"type": "object", "properties": {}, "required": []}},
		{"name": "find_entities", "description": "Search entities.", "group": "home",
		 "parameters": {"type": "object", "properties": {}, "required": []}},
	],
}


class FakeHA:
	def __init__(self, tools=TOOLS, result=None):
		self.calls = []
		self.tools = tools
		self.result = result if result is not None else {"ok": True, "result": {"linked": True}}

	def call_service(self, service, data=None):
		self.calls.append((service, data))
		if service == "chat_tools":
			return 200, {"service_response": self.tools}
		return 200, {"service_response": self.result}


def _unwrap(payload):
	return payload.get("service_response", payload)


def _rpc(method, params=None, message_id=1):
	return {"jsonrpc": "2.0", "id": message_id, "method": method, "params": params or {}}


def test_initialize_names_vome_and_offers_tools():
	reply = mcp_server.handle_message(
		_rpc("initialize", {"protocolVersion": "2025-03-26"}), mcp_server.Tools(FakeHA().call_service, _unwrap))
	result = reply["result"]
	assert result["serverInfo"]["name"] == "Vome"  # the entry's title on Home Assistant's page
	assert result["protocolVersion"] == "2025-03-26"
	assert "tools" in result["capabilities"]
	unknown = mcp_server.handle_message(
		_rpc("initialize", {"protocolVersion": "1999-01-01"}), mcp_server.Tools(FakeHA().call_service, _unwrap))
	assert unknown["result"]["protocolVersion"] == mcp_server.PROTOCOL_VERSIONS[0]


def test_lists_only_vome_tools_asking_as_nobody():
	ha = FakeHA()
	reply = mcp_server.handle_message(_rpc("tools/list"), mcp_server.Tools(ha.call_service, _unwrap))
	tools = reply["result"]["tools"]
	assert [t["name"] for t in tools] == ["vome_status"]
	assert tools[0]["annotations"]["readOnlyHint"] is True
	assert ha.calls == [("chat_tools", {"anonymous": True})]


def test_a_call_runs_as_nobody_and_answers_text():
	ha = FakeHA()
	reply = mcp_server.handle_message(
		_rpc("tools/call", {"name": "vome_status", "arguments": {}}), mcp_server.Tools(ha.call_service, _unwrap))
	assert reply["result"]["isError"] is False
	assert json.loads(reply["result"]["content"][0]["text"]) == {"linked": True}
	assert ("chat_tool_call", {"name": "vome_status", "arguments": {}, "anonymous": True}) in ha.calls


def test_entity_tools_are_not_served_here():
	ha = FakeHA()
	reply = mcp_server.handle_message(
		_rpc("tools/call", {"name": "find_entities", "arguments": {}}), mcp_server.Tools(ha.call_service, _unwrap))
	assert reply["result"]["isError"] is True
	assert not any(service == "chat_tool_call" for service, _ in ha.calls)


def test_a_refused_tool_is_an_error_result():
	ha = FakeHA(result={"ok": False, "error": "vome_status is switched off here."})
	reply = mcp_server.handle_message(
		_rpc("tools/call", {"name": "vome_status"}), mcp_server.Tools(ha.call_service, _unwrap))
	assert reply["result"]["isError"] is True
	assert "switched off" in reply["result"]["content"][0]["text"]


def test_no_integration_yet_is_an_empty_list_not_an_error():
	ha = FakeHA(tools={"error": "Set up the Vome integration first."})
	reply = mcp_server.handle_message(_rpc("tools/list"), mcp_server.Tools(ha.call_service, _unwrap))
	assert reply["result"]["tools"] == []


def test_notifications_get_no_reply_and_unknown_methods_do():
	tools = mcp_server.Tools(FakeHA().call_service, _unwrap)
	assert mcp_server.handle_message({"jsonrpc": "2.0", "method": "notifications/initialized"}, tools) is None
	assert mcp_server.handle_message(_rpc("resources/list"), tools)["error"]["code"] == mcp_server.METHOD_NOT_FOUND


def test_the_secret_is_made_once_and_kept(tmp_path):
	path = tmp_path / "mcp.json"
	first = mcp_server.load_secret(path)
	assert len(first) >= 32
	assert mcp_server.load_secret(path) == first
	assert oct(path.stat().st_mode & 0o777) == "0o600"


@pytest.fixture
def served():
	ha = FakeHA()
	handler = mcp_server.make_handler("s" * 43, mcp_server.Tools(ha.call_service, _unwrap))
	server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
	threading.Thread(target=server.serve_forever, daemon=True).start()
	yield f"http://127.0.0.1:{server.server_address[1]}"
	server.shutdown()


def _post(url, body):
	req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
	                             headers={"Content-Type": "application/json",
	                                      "Accept": "application/json, text/event-stream"})
	return urllib.request.urlopen(req, timeout=5)


def test_over_http_only_the_secret_path_answers(served):
	with _post(f"{served}/mcp/{'s' * 43}", _rpc("tools/list")) as resp:
		assert resp.status == 200
		assert json.loads(resp.read())["result"]["tools"][0]["name"] == "vome_status"
	with pytest.raises(urllib.error.HTTPError) as wrong:
		_post(f"{served}/mcp/{'t' * 43}", _rpc("tools/list"))
	assert wrong.value.code == 404
	with pytest.raises(urllib.error.HTTPError) as bare:
		_post(f"{served}/mcp", _rpc("tools/list"))
	assert bare.value.code == 404


def test_over_http_a_notification_is_202_and_get_is_405(served):
	with _post(f"{served}/mcp/{'s' * 43}", {"jsonrpc": "2.0", "method": "notifications/initialized"}) as resp:
		assert resp.status == 202
	with pytest.raises(urllib.error.HTTPError) as get:
		urllib.request.urlopen(f"{served}/mcp/{'s' * 43}", timeout=5)
	assert get.value.code == 405  # what the Streamable HTTP client expects of a JSON-only server


def test_the_app_announces_mcp_to_the_supervisor():
	config = yaml.safe_load((ROOT / "vome" / "config.yaml").read_text())
	assert "mcp" in config.get("discovery", [])
	assert mcp_server.server_url("abc").endswith(f":{mcp_server.MCP_PORT}/mcp/abc")
