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


def _hass_on(port=None, trusted=None, internal_url=None):
	"""Fake hass exposing just what the status diagnostics read."""
	from types import SimpleNamespace
	from ipaddress import ip_network
	return SimpleNamespace(
		http=SimpleNamespace(
			server_port=port,
			ssl_certificate=None,
			server_host=None,
			trusted_proxies=[ip_network(n) for n in (trusted or [])],
		),
		config=SimpleNamespace(
			api=SimpleNamespace(port=None, use_ssl=False),
			internal_url=internal_url,
		),
	)


class TestLocalUrlInStatus:
	"""Since 2026.8 the listen port is a user-facing setting, so the panel has to
	show which address we actually dial — a wrong one is otherwise invisible."""

	def test_reports_detected_url_and_source(self):
		entry = _FakeEntry("abc", {"relay": {"server_id": "rly-1"}})
		payload = remote_status_payload(_hass_on(port=80), entry)
		assert payload["local_url"] == "http://127.0.0.1:80"
		assert payload["local_url_source"] == "detected"
		assert payload["local_url_override"] == ""

	def test_reports_override_and_source(self):
		entry = _FakeEntry("abc", {
			"relay": {"server_id": "rly-1", "local_url": "http://10.0.0.5:8123"},
		})
		payload = remote_status_payload(_hass_on(port=80), entry)
		assert payload["local_url"] == "http://10.0.0.5:8123"
		assert payload["local_url_source"] == "override"
		assert payload["local_url_override"] == "http://10.0.0.5:8123"

	def test_reports_fallback_when_detection_fails(self):
		entry = _FakeEntry("abc", {"relay": {"server_id": "rly-1"}})
		payload = remote_status_payload(None, entry)
		assert payload["local_url_source"] == "fallback"


class TestTrustedProxyCheck:
	def test_no_trusted_proxies_is_fine(self):
		# Without trusted proxies HA ignores the forwarded header entirely.
		entry = _FakeEntry("abc", {"relay": {"server_id": "rly-1"}})
		assert remote_status_payload(_hass_on(port=80), entry)["trusted_proxy"]["ok"] is True

	def test_loopback_covered_by_trusted_proxies(self):
		entry = _FakeEntry("abc", {"relay": {"server_id": "rly-1"}})
		hass = _hass_on(port=80, trusted=["127.0.0.0/8"])
		assert remote_status_payload(hass, entry)["trusted_proxy"]["ok"] is True

	def test_trusted_proxies_without_our_address_warns(self):
		entry = _FakeEntry("abc", {"relay": {"server_id": "rly-1"}})
		hass = _hass_on(port=80, trusted=["192.168.1.0/24"])
		check = remote_status_payload(hass, entry)["trusted_proxy"]
		assert check["ok"] is False
		assert "127.0.0.1" in check["hint"]

	def test_mixed_ipv4_ipv6_entries_do_not_break_the_check(self):
		entry = _FakeEntry("abc", {"relay": {"server_id": "rly-1"}})
		hass = _hass_on(port=80, trusted=["fd00::/8", "127.0.0.0/8"])
		assert remote_status_payload(hass, entry)["trusted_proxy"]["ok"] is True

	def test_hostname_target_is_unknown_not_a_warning(self):
		# internal_url gives a name we cannot resolve here; don't cry wolf.
		entry = _FakeEntry("abc", {
			"relay": {"server_id": "rly-1", "local_url": "https://ha.example.com"},
		})
		hass = _hass_on(trusted=["192.168.1.0/24"])
		assert remote_status_payload(hass, entry)["trusted_proxy"]["ok"] is None


class TestSetLocalUrlService:
	def _handlers(self, entry, monkeypatch):
		from unittest.mock import AsyncMock, MagicMock
		import custom_components.vomesync.services_remote as sr
		hass = MagicMock()
		hass.data = {}
		hass.config_entries.async_entries.return_value = [entry]
		hass.http.trusted_proxies = []

		def _update(e, options=None):
			e.options = options
		hass.config_entries.async_update_entry.side_effect = _update
		# monkeypatch, not assignment: a bare assignment here leaks the mock
		# into every later test in the session.
		monkeypatch.setattr(sr, "async_start_relay", AsyncMock())
		return _register_and_capture(hass), hass

	def test_sets_and_clears_the_override(self, monkeypatch):
		import asyncio
		from types import SimpleNamespace
		entry = SimpleNamespace(entry_id="e1", options={"relay": {"server_id": "rly-1"}})
		handlers, _hass = self._handlers(entry, monkeypatch)

		result = asyncio.run(handlers["set_local_url"](
			SimpleNamespace(data={"local_url": "http://10.0.0.5:8123/"})
		))
		assert "error" not in result
		assert entry.options["relay"]["local_url"] == "http://10.0.0.5:8123"

		asyncio.run(handlers["set_local_url"](SimpleNamespace(data={"local_url": ""})))
		assert "local_url" not in entry.options["relay"]

	def test_rejects_a_bare_host(self, monkeypatch):
		import asyncio
		from types import SimpleNamespace
		entry = SimpleNamespace(entry_id="e1", options={"relay": {"server_id": "rly-1"}})
		handlers, _hass = self._handlers(entry, monkeypatch)
		result = asyncio.run(handlers["set_local_url"](
			SimpleNamespace(data={"local_url": "127.0.0.1:8123"})
		))
		assert "full address" in result["error"]

	def test_rejects_a_path(self, monkeypatch):
		# A path here silently corrupts every relayed request URL.
		import asyncio
		from types import SimpleNamespace
		entry = SimpleNamespace(entry_id="e1", options={"relay": {"server_id": "rly-1"}})
		handlers, _hass = self._handlers(entry, monkeypatch)
		result = asyncio.run(handlers["set_local_url"](
			SimpleNamespace(data={"local_url": "http://127.0.0.1:8123/api"})
		))
		assert "path" in result["error"]


def test_unlinked_status_still_reports_the_local_url(monkeypatch):
	"""The address is a property of this HA, not of the link — and being able to
	check it before connecting an account is the point."""
	import asyncio
	from types import SimpleNamespace
	from unittest.mock import MagicMock
	import custom_components.vomesync.services_remote as sr

	entry = SimpleNamespace(entry_id="e1", options={})
	hass = MagicMock()
	hass.data = {}
	hass.config_entries.async_entries.return_value = [entry]
	# Both halves, so config.api's MagicMock defaults can't leak into the scheme.
	fake = _hass_on(port=80)
	hass.http = fake.http
	hass.config = fake.config

	handlers = _register_and_capture(hass)
	status = asyncio.run(handlers["get_remote_status"](SimpleNamespace(data={})))
	assert status["linked"] is False
	assert status["local_url"] == "http://127.0.0.1:80"
	assert status["local_url_source"] == "detected"
	assert status["trusted_proxy"]["ok"] is True
