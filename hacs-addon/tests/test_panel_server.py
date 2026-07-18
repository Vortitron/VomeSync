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
