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


def test_two_unlinked_entries_status_returns_an_id():
	"""HACS + adding the integration again left a blank entry_id, so the panel
	Connect button failed with 'pass entry_id' while Devices still worked."""
	import asyncio
	from types import SimpleNamespace
	from unittest.mock import MagicMock
	import custom_components.vomesync.services_remote as sr

	e1 = SimpleNamespace(entry_id="e1", options={}, title="Vome (keypair abcd...)")
	e2 = SimpleNamespace(entry_id="e2", options={}, title="Vome (keypair efgh...)")
	hass = MagicMock()
	hass.data = {}
	hass.config_entries.async_entries.return_value = [e1, e2]
	fake = _hass_on(port=80)
	hass.http = fake.http
	hass.config = fake.config

	handlers = _register_and_capture(hass)
	status = asyncio.run(handlers["get_remote_status"](SimpleNamespace(data={})))
	assert "error" not in status
	assert status["entry_id"] == "e1"
	assert status["linked"] is False
	assert len(status["vome_entries"]) == 2
	assert "more than one Vome" in status["warning"]


def test_link_start_with_two_unlinked_entries_picks_one(monkeypatch):
	import asyncio
	from types import SimpleNamespace
	from unittest.mock import AsyncMock, MagicMock
	import custom_components.vomesync.services_remote as sr

	e1 = SimpleNamespace(entry_id="e1", options={}, title="Vome")
	e2 = SimpleNamespace(entry_id="e2", options={}, title="Vome 2")
	hass = MagicMock()
	hass.data = {}
	hass.config_entries.async_entries.return_value = [e1, e2]
	hass.config.location_name = "Home"

	monkeypatch.setattr(sr, "async_get_clientsession", lambda h: MagicMock())
	monkeypatch.setattr(sr, "async_request_device_code", AsyncMock(return_value={
		"device_code": "dev-123", "user_code": "WXYZ-1234",
		"verification_uri": "https://vome.io/account/link-ha", "interval": 5, "expires_in": 900,
	}))

	handlers = _register_and_capture(hass)
	started = asyncio.run(handlers["link_start"](SimpleNamespace(data={})))
	assert "error" not in started
	assert started["status"] == "started"
	assert started["entry_id"] == "e1"
	assert hass.data[sr.DOMAIN][sr._PENDING_LINK_KEY]["e1"]["device_code"] == "dev-123"


def test_preferred_entry_picks_the_linked_one():
	from types import SimpleNamespace
	from custom_components.vomesync.services_remote import _preferred_vome_entry

	unlinked = SimpleNamespace(entry_id="e1", options={}, title="old")
	linked = SimpleNamespace(
		entry_id="e2", options={"relay": {"server_id": "rly-1"}}, title="live",
	)
	assert _preferred_vome_entry([unlinked, linked]).entry_id == "e2"


def test_pick_vome_entry_empty_is_a_plain_language_error():
	from unittest.mock import MagicMock
	import pytest
	from custom_components.vomesync.services_remote import _pick_vome_entry

	hass = MagicMock()
	hass.config_entries.async_entries.return_value = []
	with pytest.raises(ValueError, match="isn't set up"):
		_pick_vome_entry(hass, None)


def _hass_on(port=None, trusted=None, internal_url=None, external_url=None,
			 forward_host=None):
	"""Fake hass exposing just what the status diagnostics read."""
	from types import SimpleNamespace
	from ipaddress import ip_network
	from custom_components.vomesync.const import DOMAIN, FORWARD_HOST_KEY
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
			external_url=external_url,
		),
		data={DOMAIN: {FORWARD_HOST_KEY: forward_host}} if forward_host else {},
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


class TestExternalUrlCheck:
	"""Core cannot learn the public name it is served under, and nothing tells
	the user it is missing — the companion app just fails to reconnect."""

	def _entry(self, forward_ui=True):
		return _FakeEntry("abc", {
			"relay": {"server_id": "rly-1", "forward_ui": forward_ui},
		})

	def test_unset_while_forwarding_warns(self):
		hass = _hass_on(port=80, forward_host="myhome.home.vome.io")
		check = remote_status_payload(hass, self._entry())["external_url"]
		assert check["ok"] is False
		assert check["expected"] == "https://myhome.home.vome.io"

	def test_matching_url_is_fine(self):
		hass = _hass_on(port=80, external_url="https://myhome.home.vome.io",
						forward_host="myhome.home.vome.io")
		assert remote_status_payload(hass, self._entry())["external_url"]["ok"] is True

	def test_trailing_slash_is_not_a_mismatch(self):
		hass = _hass_on(port=80, external_url="https://myhome.home.vome.io/",
						forward_host="myhome.home.vome.io")
		assert remote_status_payload(hass, self._entry())["external_url"]["ok"] is True

	def test_pointing_somewhere_else_warns(self):
		hass = _hass_on(port=80, external_url="https://old.example.com",
						forward_host="myhome.home.vome.io")
		check = remote_status_payload(hass, self._entry())["external_url"]
		assert check["ok"] is False
		assert "old.example.com" in check["hint"]

	def test_silent_when_forwarding_is_off(self):
		# Nothing of ours depends on it then, so nagging would be noise.
		hass = _hass_on(port=80)
		assert remote_status_payload(hass, self._entry(forward_ui=False))["external_url"]["ok"] is True

	def test_unset_with_no_observed_host_still_warns_without_a_target(self):
		hass = _hass_on(port=80)
		check = remote_status_payload(hass, self._entry())["external_url"]
		assert check["ok"] is False
		assert check["expected"] == ""


class TestSetExternalUrlService:
	"""The panel offers to set Core's own External URL, so a wrong or silently
	dropped write would leave the user believing a broken setup is fixed."""

	def _handlers(self, monkeypatch, *, forward_host=None, accepts=True):
		import asyncio
		from types import SimpleNamespace
		from unittest.mock import AsyncMock, MagicMock
		import custom_components.vomesync.services_remote as sr
		from custom_components.vomesync.const import DOMAIN, FORWARD_HOST_KEY

		entry = SimpleNamespace(entry_id="e1", options={"relay": {"server_id": "rly-1"}})
		hass = MagicMock()
		hass.data = {DOMAIN: {FORWARD_HOST_KEY: forward_host}} if forward_host else {}
		hass.config_entries.async_entries.return_value = [entry]
		hass.http.trusted_proxies = []
		hass.config.external_url = None
		hass.config.internal_url = None
		hass.config.api = SimpleNamespace(port=None, use_ssl=False)

		async def _async_update(external_url=None, **_kw):
			if accepts:
				hass.config.external_url = external_url
		hass.config.async_update = _async_update
		monkeypatch.setattr(sr, "async_start_relay", AsyncMock())
		return _register_and_capture(hass), hass, asyncio

	def test_blank_uses_the_address_we_have_been_served_on(self, monkeypatch):
		from types import SimpleNamespace
		handlers, hass, asyncio = self._handlers(
			monkeypatch, forward_host="myhome.home.vome.io")
		result = asyncio.run(handlers["set_external_url"](SimpleNamespace(data={})))
		assert "error" not in result
		assert hass.config.external_url == "https://myhome.home.vome.io"

	def test_blank_with_nothing_observed_explains_itself(self, monkeypatch):
		from types import SimpleNamespace
		handlers, _hass, asyncio = self._handlers(monkeypatch)
		result = asyncio.run(handlers["set_external_url"](SimpleNamespace(data={})))
		assert "No address to set yet" in result["error"]

	def test_rejects_a_url_with_a_path(self, monkeypatch):
		from types import SimpleNamespace
		handlers, _hass, asyncio = self._handlers(monkeypatch)
		result = asyncio.run(handlers["set_external_url"](
			SimpleNamespace(data={"external_url": "https://ha.example.com/lovelace"})
		))
		assert "must not include a path" in result["error"]

	def test_rejects_a_bare_host(self, monkeypatch):
		from types import SimpleNamespace
		handlers, _hass, asyncio = self._handlers(monkeypatch)
		result = asyncio.run(handlers["set_external_url"](
			SimpleNamespace(data={"external_url": "ha.example.com"})
		))
		assert "full address" in result["error"]

	def test_a_yaml_pinned_url_is_reported_not_silently_ignored(self, monkeypatch):
		# hass.config.async_update is a no-op when configuration.yaml sets the
		# value, so a naive handler would report success and change nothing.
		from types import SimpleNamespace
		handlers, _hass, asyncio = self._handlers(monkeypatch, accepts=False)
		result = asyncio.run(handlers["set_external_url"](
			SimpleNamespace(data={"external_url": "https://ha.example.com"})
		))
		assert "configuration.yaml" in result["error"]


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
	assert status["external_url"]["ok"] is True


class TestWebhookServices:
	"""Publishing a webhook makes it callable from the internet with no login,
	so the services that curate that list are a security surface too."""

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
		monkeypatch.setattr(sr, "async_start_relay", AsyncMock())
		return _register_and_capture(hass)

	def _entry(self):
		from types import SimpleNamespace
		return SimpleNamespace(entry_id="e1", options={"relay": {"server_id": "rly-1"}})

	def test_add_then_remove(self, monkeypatch):
		import asyncio
		from types import SimpleNamespace
		entry = self._entry()
		h = self._handlers(entry, monkeypatch)

		result = asyncio.run(h["add_webhook"](SimpleNamespace(data={"webhook_id": "hook1"})))
		assert result["webhooks"] == ["hook1"]

		asyncio.run(h["remove_webhook"](SimpleNamespace(data={"webhook_id": "hook1"})))
		assert entry.options["relay"]["webhooks"] == []

	def test_adding_twice_is_idempotent(self, monkeypatch):
		import asyncio
		from types import SimpleNamespace
		entry = self._entry()
		h = self._handlers(entry, monkeypatch)
		asyncio.run(h["add_webhook"](SimpleNamespace(data={"webhook_id": "hook1"})))
		result = asyncio.run(h["add_webhook"](SimpleNamespace(data={"webhook_id": "hook1"})))
		assert result["webhooks"] == ["hook1"]

	def test_a_malformed_id_is_refused_with_a_useful_message(self, monkeypatch):
		import asyncio
		from types import SimpleNamespace
		entry = self._entry()
		h = self._handlers(entry, monkeypatch)
		result = asyncio.run(h["add_webhook"](SimpleNamespace(data={"webhook_id": "../states"})))
		assert "webhook id" in result["error"]
		assert "webhooks" not in entry.options["relay"]

	def test_set_webhooks_drops_invalid_entries(self, monkeypatch):
		import asyncio
		from types import SimpleNamespace
		entry = self._entry()
		h = self._handlers(entry, monkeypatch)
		result = asyncio.run(h["set_webhooks"](
			SimpleNamespace(data={"webhooks": ["good", "bad/id", "good"]})))
		assert result["webhooks"] == ["good"]

	def test_status_reports_webhooks_and_the_cap(self, monkeypatch):
		import asyncio
		from types import SimpleNamespace
		entry = self._entry()
		h = self._handlers(entry, monkeypatch)
		status = asyncio.run(h["get_remote_status"](SimpleNamespace(data={})))
		assert status["webhooks"] == []
		assert status["webhook_max"] >= 1
