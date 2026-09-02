# flake8: noqa
"""Tests for the Vome add-on control-panel HTTP helper (vome/panel/server.py).

The panel must never hand the browser a non-JSON body: the UI parses every
response as JSON and shows a generic "Invalid JSON from panel API" otherwise,
which masks the real reason (and made a linked HA look unlinked). ``_unwrap``
normalises Core's success wrapper and its several error shapes into the flat
dict the UI expects.
"""
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
	"vome_panel_server", ROOT / "vome" / "panel" / "server.py"
)
server = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(server)


def test_unwrap_service_response():
	payload = {"changed_states": [], "service_response": {"linked": True, "server_id": "rly-1"}}
	assert server._unwrap(payload) == {"linked": True, "server_id": "rly-1"}


def test_unwrap_core_message_becomes_error():
	assert server._unwrap({"message": "not allowed"}) == {"error": "not allowed"}


def test_unwrap_non_json_raw_becomes_first_line_error():
	out = server._unwrap({"raw": "500 Internal Server Error\nServer got itself in trouble"})
	assert out == {"error": "500 Internal Server Error"}


def test_unwrap_empty_raw_has_a_reason():
	assert server._unwrap({"raw": ""}) == {"error": "empty response from Home Assistant"}


def test_unwrap_passes_through_existing_error_and_plain_dicts():
	assert server._unwrap({"error": "boom"}) == {"error": "boom"}
	assert server._unwrap({"linked": False}) == {"linked": False}


def test_read_body_chunked_transfer_encoding():
	"""HA's ingress forwards add-on POST bodies chunked with no Content-Length.

	Regression for the write-400 bug: reading only Content-Length dropped the
	whole payload, so every mutation reached Core with {} and was rejected.
	"""
	import io
	import types

	body = b'{"slug":"rdp","host":"192.168.1.5","port":3389}'
	framed = b"%x\r\n%s\r\n0\r\n\r\n" % (len(body), body)
	fake = types.SimpleNamespace(
		headers={"Transfer-Encoding": "chunked"},
		rfile=io.BytesIO(framed),
	)
	assert server.PanelHandler._read_body(fake) == body


def test_read_body_content_length_still_works():
	import io
	import types

	body = b'{"forward_ui":true}'
	fake = types.SimpleNamespace(
		headers={"Content-Length": str(len(body))},
		rfile=io.BytesIO(body),
	)
	assert server.PanelHandler._read_body(fake) == body


def test_read_body_no_body_is_empty():
	import io
	import types

	fake = types.SimpleNamespace(headers={}, rfile=io.BytesIO(b""))
	assert server.PanelHandler._read_body(fake) == b""


def _panel_post_services():
	"""Service names the panel's POST table dispatches to, read from the source.

	The mapping lives inside a request handler, so introspecting the AST is the
	only way to check it without standing up a live HTTP request.
	"""
	import ast

	source = (ROOT / "vome" / "panel" / "server.py").read_text(encoding="utf-8")
	names: set[str] = set()
	for node in ast.walk(ast.parse(source)):
		if not isinstance(node, ast.Dict):
			continue
		for key, value in zip(node.keys, node.values):
			if (
				isinstance(key, ast.Constant)
				and isinstance(key.value, str)
				and key.value.startswith("/api/")
				and isinstance(value, ast.Tuple)
				and value.elts
				and isinstance(value.elts[0], ast.Constant)
			):
				names.add(value.elts[0].value)
	return names


def test_panel_only_calls_services_the_integration_registers():
	"""A panel route pointing at a non-existent service fails as a bare 400 with
	no message — indistinguishable from every other panel failure. Pin it."""
	from unittest.mock import MagicMock

	from custom_components.vomesync.services_remote import async_register_remote_services

	hass = MagicMock()
	hass.data = {}
	async_register_remote_services(hass)
	remote = {c.args[1] for c in hass.services.async_register.call_args_list}

	# Switch services come from services.py rather than services_remote.py.
	from custom_components.vomesync.const import DOMAIN  # noqa: F401
	switch_services = {
		"create_switch", "subscribe_switch", "delete_switch",
		"list_switches", "forget_switch",
	}

	called = _panel_post_services()
	assert called, "no panel POST routes found — did the mapping move?"
	unknown = called - remote - switch_services
	assert not unknown, f"panel calls services that are not registered: {sorted(unknown)}"


def test_panel_exposes_the_local_url_route():
	# The address Vome dials HA on became user-settable in 2026.8; the panel is
	# the only place a non-technical user can correct a bad detection.
	assert "set_local_url" in _panel_post_services()


def test_addon_version_reads_config_yaml():
	# Read the expected value from config.yaml rather than pinning a literal:
	# what matters is that addon_version() parses the file, and a hardcoded
	# version turns every release into a test edit (and a red build when it is
	# forgotten). test_version_pin.py is where version *consistency* is enforced.
	config = (ROOT / "vome" / "config.yaml").read_text(encoding="utf-8")
	expected = next(
		line.split(":", 1)[1].strip().strip('"\'')
		for line in config.splitlines()
		if line.startswith("version:")
	)
	assert server.addon_version() == expected


def test_stamp_static_html_cache_busts_assets():
	html = (ROOT / "vome" / "panel" / "static" / "index.html").read_text(encoding="utf-8")
	stamped = server.stamp_static_html(html, "0.3.28")
	assert "static/app.js?v=0.3.28" in stamped
	assert "static/styles.css?v=0.3.28" in stamped
	assert 'name="vome-addon-version" content="0.3.28"' in stamped
	assert "header-connect" in stamped
	assert "?v=0.3.28?v=" not in stamped
	again = server.stamp_static_html(stamped, "0.3.28")
	assert again.count("static/app.js?v=0.3.28") == 1


def test_addon_portal_url_reads_options_json(tmp_path):
	options = tmp_path / "options.json"
	options.write_text('{"portal_url": "https://staging.vome.io"}', encoding="utf-8")
	assert server.addon_portal_url(options) == "https://staging.vome.io"
	assert server.addon_portal_url(tmp_path / "missing.json") == ""


def test_prepare_link_start_uses_addon_options_and_fetches(monkeypatch):
	"""Connect must hit the add-on Configuration origin, not whatever Core would."""
	monkeypatch.setattr(server, "addon_portal_url", lambda path=None: "https://staging.vome.io")

	def fake_post(url, payload):
		assert url == "https://staging.vome.io/api/v1/relay/device/code"
		assert payload["name"] == "Home Assistant"
		return {
			"device_code": "secret-dev",
			"user_code": "AB12-CD34",
			"verification_uri": "https://staging.vome.io/account/link-ha",
			"interval": 5,
			"expires_in": 900,
		}

	monkeypatch.setattr(server, "_portal_post_json", fake_post)
	out = server.prepare_link_start({"portal_url": "https://vome.io", "name": "Home Assistant"})
	assert out["portal_url"] == "https://staging.vome.io"
	assert out["user_code"] == "AB12-CD34"
	assert out["device_code"] == "secret-dev"
	assert out["verification_uri"] == "https://staging.vome.io/account/link-ha"


def test_prepare_link_start_falls_back_to_body_portal(monkeypatch):
	monkeypatch.setattr(server, "addon_portal_url", lambda path=None: "")
	monkeypatch.setattr(
		server,
		"_portal_post_json",
		lambda url, payload: {
			"device_code": "x",
			"user_code": "YY",
			"verification_uri": url.replace("/api/v1/relay/device/code", "/account/link-ha"),
		},
	)
	out = server.prepare_link_start({"portal_url": "staging.vome.io"})
	assert out["portal_url"] == "https://staging.vome.io"
	assert out["verification_uri"].startswith("https://staging.vome.io")


def test_finalise_link_start_rewrites_extra_keys_and_host_mismatch():
	status, body = server.finalise_link_start(
		400, {"error": "extra keys not allowed @ data['device_code']"},
		{"portal_url": "https://staging.vome.io"},
	)
	assert status == 409
	assert "older Vome integration" in body["error"]

	status, body = server.finalise_link_start(
		200,
		{"status": "started", "verification_uri": "https://vome.io/account/link-ha"},
		{"portal_url": "https://staging.vome.io"},
	)
	assert status == 409
	assert "vome.io" in body["error"]
	assert "staging.vome.io" in body["error"]

	status, body = server.finalise_link_start(
		200,
		{"status": "started", "verification_uri": "https://staging.vome.io/account/link-ha"},
		{"portal_url": "https://staging.vome.io"},
	)
	assert status == 200
	assert body["portal_url"] == "https://staging.vome.io"
