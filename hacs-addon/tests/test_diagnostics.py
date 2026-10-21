# flake8: noqa
"""Download diagnostics: everything useful, nothing secret."""
import json
from unittest.mock import MagicMock, patch

import pytest

from custom_components.vomesync import diagnostics as diag
from custom_components.vomesync.const import DOMAIN


def test_redact_blanks_secret_looking_keys_at_any_depth():
	value = {
		"personal_key": "pk",
		"crypto_seed": "seed",
		"relay": {"server_id": "srv", "secret": "s3", "local_token": "t", "guest_claim_url": "https://x/claim/abc",
			"webhooks": ["wh-1"], "lan_routes": [{"slug": "nas", "host": "10.0.0.5"}]},
		"backup": {"secret": "vbk_x"},
		"imported_switches": {"uid": {"name": "Mine", "access_key": "ak", "is_owner": True}},
		"empty_token": "",
	}
	out = diag.redact(value)
	assert out["personal_key"] == diag.REDACTED
	assert out["crypto_seed"] == diag.REDACTED
	assert out["relay"]["secret"] == diag.REDACTED
	assert out["relay"]["local_token"] == diag.REDACTED
	assert out["relay"]["guest_claim_url"] == diag.REDACTED
	assert out["relay"]["webhooks"] == diag.REDACTED
	assert out["backup"]["secret"] == diag.REDACTED
	assert out["imported_switches"]["uid"]["access_key"] == diag.REDACTED
	# Kept: ids, names, flags, the LAN route table, and an empty secret.
	assert out["relay"]["server_id"] == "srv"
	assert out["relay"]["lan_routes"] == [{"slug": "nas", "host": "10.0.0.5"}]
	assert out["imported_switches"]["uid"]["name"] == "Mine"
	assert out["empty_token"] == ""
	# The input is untouched.
	assert value["relay"]["secret"] == "s3"


@pytest.mark.asyncio
async def test_diagnostics_shape_and_no_secret_survives(hass, config_entry):
	config_entry.title = "Vome"
	config_entry.data = {"personal_key": "PERSONAL", "server_url": "https://sync.vome.io"}
	config_entry.options = {
		"relay": {"server_id": "srv-1", "secret": "RELAYSECRET", "forward_ui": True,
			"lan_routes": [{"slug": "nas"}], "webhooks": ["WH"], "remote_url": "https://a.home.vome.io",
			"guest": True},
		"imported_switches": {"u1": {"is_owner": True, "access_key": "AK"}, "u2": {"is_owner": False}},
	}
	client = MagicMock()
	client.connected = True
	client.ws_url = "wss://dev.sync.vome.io/ws/relay"
	client.connected_since = 1_700_000_000
	client.disconnected_since = None
	client.rejected = False
	coordinator = MagicMock()
	coordinator.switches = {"u1": {}}
	coordinator.subscriptions = {"u2": {}}
	coordinator.last_update_success = True
	hass.data = {DOMAIN: {
		config_entry.entry_id: coordinator,
		"_relays": {config_entry.entry_id: client},
		"_health_reports": {},
	}}
	with patch("custom_components.vomesync.health_score.stored_report",
			return_value={"score": 72, "generated_at": "2026-10-20T10:00:00Z", "findings": [1, 2, 3]}), \
		patch("custom_components.vomesync.relay_health.is_down_too_long", return_value=False), \
		patch("custom_components.vomesync.relay_health.is_rejected_raised", return_value=False):
		out = await diag.async_get_config_entry_diagnostics(hass, config_entry)

	dumped = json.dumps(out)
	for secret in ("PERSONAL", "RELAYSECRET", "AK", "WH"):
		assert secret not in dumped
	assert out["integration_version"]
	assert out["entry"]["options"]["relay"]["server_id"] == "srv-1"
	assert out["relay"] == {
		"linked": True, "server_id": "srv-1", "client_running": True, "connected": True,
		"connected_since": "2023-11-14T22:13:20+00:00", "disconnected_since": None,
		"rejected": False, "down_too_long": False, "rejected_issue": False, "forward_ui": True, "lan_routes": 1, "webhooks": 1,
		"local_url_override": "", "remote_url": "https://a.home.vome.io", "guest": True,
		"agent_trial": False, "relay_host": "wss://dev.sync.vome.io/ws/relay",
	}
	assert out["health_score"] == {"has_report": True, "score": 72,
		"generated_at": "2026-10-20T10:00:00Z", "findings": 3}
	assert out["switches"] == {"coordinator": True, "owned": 1, "subscribed": 1, "last_update_success": True}


@pytest.mark.asyncio
async def test_diagnostics_for_an_unlinked_entry_with_nothing_running(hass, config_entry):
	config_entry.title = "Vome"
	hass.data = {DOMAIN: {}}
	with patch("custom_components.vomesync.health_score.stored_report", return_value=None), \
		patch("custom_components.vomesync.relay_health.is_down_too_long", return_value=None), \
		patch("custom_components.vomesync.relay_health.is_rejected_raised", return_value=None):
		out = await diag.async_get_config_entry_diagnostics(hass, config_entry)
	assert out["relay"]["linked"] is False
	assert out["relay"]["client_running"] is False
	assert out["relay"]["connected"] is False
	assert "relay_host" not in out["relay"]
	assert out["health_score"]["has_report"] is False
	assert out["switches"] == {"coordinator": False}
