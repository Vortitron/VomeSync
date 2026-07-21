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


def _register_and_capture(hass):
	import custom_components.vomesync.services_remote as sr
	sr.async_register_remote_services(hass)
	return {c.args[1]: c.args[2] for c in hass.services.async_register.call_args_list}


def test_in_app_link_flow(monkeypatch):
	"""link_start stores a pending code; link_poll approves + saves relay; unlink clears it."""
	import asyncio
	from types import SimpleNamespace
	from unittest.mock import AsyncMock, MagicMock
	import custom_components.vomesync.services_remote as sr

	entry = SimpleNamespace(entry_id="e1", options={})
	hass = MagicMock()
	hass.data = {}
	hass.config_entries.async_entries.return_value = [entry]
	hass.config.location_name = "Home"

	def _update(e, options=None):
		e.options = options
	hass.config_entries.async_update_entry.side_effect = _update

	monkeypatch.setattr(sr, "async_get_clientsession", lambda h: MagicMock())
	monkeypatch.setattr(sr, "async_request_device_code", AsyncMock(return_value={
		"device_code": "dev-123", "user_code": "WXYZ-1234",
		"verification_uri": "https://vome.io/account/link-ha", "interval": 5, "expires_in": 900,
	}))
	monkeypatch.setattr(sr, "async_start_relay", AsyncMock())
	monkeypatch.setattr(sr, "async_stop_relay", AsyncMock())

	handlers = _register_and_capture(hass)
	for name in ("link_start", "link_poll", "unlink"):
		assert name in handlers
	call = SimpleNamespace(data={})

	started = asyncio.run(handlers["link_start"](call))
	assert started["status"] == "started"
	assert started["user_code"] == "WXYZ-1234"
	assert hass.data[sr.DOMAIN][sr._PENDING_LINK_KEY]["e1"]["device_code"] == "dev-123"

	monkeypatch.setattr(sr, "async_poll_device_token", AsyncMock(return_value={"status": "pending"}))
	assert asyncio.run(handlers["link_poll"](call))["status"] == "pending"

	monkeypatch.setattr(sr, "async_poll_device_token", AsyncMock(return_value={
		"status": "approved", "server_id": "rly-9", "relay_secret": "s", "relay_ws_url": "wss://x/ws/relay",
	}))
	linked = asyncio.run(handlers["link_poll"](call))
	assert linked == {"status": "linked", "server_id": "rly-9"}
	assert entry.options["relay"]["server_id"] == "rly-9"
	assert "e1" not in hass.data[sr.DOMAIN][sr._PENDING_LINK_KEY]

	unlinked = asyncio.run(handlers["unlink"](call))
	assert unlinked == {"status": "unlinked", "was_linked": True}
	assert "relay" not in entry.options
