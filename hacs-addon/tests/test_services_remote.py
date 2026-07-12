# flake8: noqa
"""Tests for remote-access service helpers (status payload, no live HA needed)."""
from custom_components.vomesync.services_remote import remote_status_payload


class _FakeEntry:
	def __init__(self, entry_id, options):
		self.entry_id = entry_id
		self.options = options


def test_remote_status_payload_strips_secrets():
	entry = _FakeEntry("abc", {
		"relay": {
			"server_id": "rly-1",
			"secret": "must-not-appear",
			"forward_ui": True,
			"lan_routes": [
				{"slug": "nas", "host": "192.168.1.5", "port": 5000, "scheme": "http", "enabled": True},
			],
		}
	})
	payload = remote_status_payload(None, entry)
	assert payload["linked"] is True
	assert payload["server_id"] == "rly-1"
	assert payload["forward_ui"] is True
	assert payload["lan_routes"][0]["slug"] == "nas"
	assert "secret" not in payload
	assert "must-not-appear" not in str(payload)
