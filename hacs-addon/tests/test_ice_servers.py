# flake8: noqa
"""Tests for ice_servers — Vome's TURN logins handed to HA's web_rtc.

What matters: HA is given the login Vome issued and nothing once it has
expired, "off" from Vome means nothing registered (cameras as before), a
failed ask keeps the login already held and backs off, registration waits
for web_rtc to load, and stopping takes everything back out.

The installed Core in this venv predates web_rtc (2024.11), so the module's
``_web_rtc`` hook is replaced with a recorder; the real signature —
``async_register_ice_servers(hass, fn) -> remove`` — was read from Core.
"""
import asyncio
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Optional
from unittest.mock import MagicMock

import pytest

from custom_components.vomesync import ice_servers as ice_mod


@dataclass
class _IceServer:
	urls: object
	username: Optional[str] = None
	credential: Optional[str] = None


class _WebRtc:
	def __init__(self):
		self.fns = []

	def register(self, hass, fn):
		self.fns.append(fn)
		return lambda: self.fns.remove(fn)

	def servers(self):
		return [s for fn in self.fns for s in fn()]


class _Bus:
	def __init__(self):
		self.listeners = {}

	def async_listen(self, event_type, listener):
		self.listeners.setdefault(event_type, []).append(listener)
		return lambda: self.listeners[event_type].remove(listener)

	def fire(self, event_type, data):
		for listener in list(self.listeners.get(event_type, [])):
			listener(SimpleNamespace(data=data))


ON = {
	"enabled": True,
	"ice_servers": [
		{"urls": ["stun:stun.cloudflare.com:3478"]},
		{"urls": ["turns:turn.cloudflare.com:443?transport=tcp"], "username": "u", "credential": "c"},
	],
	"expires_at": 2_000_000_000,
	"refresh_after": 43200,
}


@pytest.fixture
def ctx(monkeypatch):
	web_rtc = _WebRtc()
	monkeypatch.setattr(ice_mod, "_web_rtc", lambda: (web_rtc.register, _IceServer))
	monkeypatch.setattr(ice_mod, "async_get_clientsession", lambda hass: object())
	monkeypatch.setattr(ice_mod.hs, "_agent_credentials", lambda entry: ("srv-1", "secret"))
	monkeypatch.setattr(ice_mod.hs, "_portal_url", lambda entry: "https://vome.test")

	answers = []
	asked = []

	async def _agent_request(session, method, portal_url, path, secret, payload=None):
		asked.append((method, portal_url, path, secret))
		answer = answers.pop(0)
		if isinstance(answer, Exception):
			raise answer
		return answer

	monkeypatch.setattr(ice_mod, "_agent_request", _agent_request)

	hass = MagicMock()
	hass.bus = _Bus()
	hass.config.components = {"web_rtc"}
	ice = ice_mod.IceServers(hass, SimpleNamespace(entry_id="e1"))
	return SimpleNamespace(ice=ice, hass=hass, web_rtc=web_rtc, answers=answers, asked=asked)


def _refresh(ctx):
	return asyncio.run(ctx.ice.refresh())


def test_asks_vome_with_the_homes_own_credential(ctx):
	ctx.answers.append(ON)
	_refresh(ctx)
	assert ctx.asked == [("GET", "https://vome.test", "/api/sync/agent/ice-servers", "secret")]


def test_issued_login_reaches_web_rtc(ctx):
	ctx.ice._register_when_loaded()
	ctx.answers.append(ON)
	assert _refresh(ctx) == 43200
	servers = ctx.web_rtc.servers()
	assert [s.urls for s in servers] == [
		["stun:stun.cloudflare.com:3478"], ["turns:turn.cloudflare.com:443?transport=tcp"],
	]
	assert servers[1].username == "u" and servers[1].credential == "c"


def test_an_expired_login_is_never_handed_out(ctx, monkeypatch):
	ctx.ice._register_when_loaded()
	ctx.answers.append({**ON, "expires_at": 1000})
	_refresh(ctx)
	monkeypatch.setattr(ice_mod.time, "time", lambda: 1001)
	assert ctx.web_rtc.servers() == []


def test_off_registers_nothing_and_waits_as_told(ctx):
	ctx.ice._register_when_loaded()
	ctx.answers.append({"enabled": False, "ice_servers": [], "reason": "coming soon", "retry_after": 21600})
	assert _refresh(ctx) == 21600
	assert ctx.web_rtc.servers() == []
	assert ctx.ice.status == {"enabled": False, "reason": "coming soon"}


def test_turning_off_withdraws_a_login_already_held(ctx):
	ctx.ice._register_when_loaded()
	ctx.answers.extend([ON, {"enabled": False, "retry_after": 21600}])
	_refresh(ctx)
	_refresh(ctx)
	assert ctx.web_rtc.servers() == []


def test_a_failed_ask_keeps_the_login_and_backs_off(ctx):
	ctx.ice._register_when_loaded()
	ctx.answers.extend([ON, RuntimeError("down"), RuntimeError("down"), ON])
	_refresh(ctx)
	assert _refresh(ctx) == 300
	assert _refresh(ctx) == 600
	assert len(ctx.web_rtc.servers()) == 2
	_refresh(ctx)
	assert ctx.ice._backoff == 300


def test_never_asks_more_often_than_a_minute(ctx):
	ctx.answers.append({**ON, "refresh_after": 1})
	assert _refresh(ctx) == 60


def test_waits_for_web_rtc_to_load(ctx):
	ctx.hass.config.components = set()
	ctx.ice._register_when_loaded()
	assert ctx.web_rtc.fns == []
	ctx.hass.bus.fire(ice_mod.EVENT_COMPONENT_LOADED, {"component": "camera"})
	assert ctx.web_rtc.fns == []
	ctx.hass.bus.fire(ice_mod.EVENT_COMPONENT_LOADED, {"component": "web_rtc"})
	assert len(ctx.web_rtc.fns) == 1
	assert ctx.hass.bus.listeners[ice_mod.EVENT_COMPONENT_LOADED] == []


def test_stop_takes_it_all_back(ctx):
	ctx.ice._register_when_loaded()
	ctx.answers.append(ON)
	_refresh(ctx)
	asyncio.run(ctx.ice.stop())
	assert ctx.web_rtc.fns == []


def test_old_core_without_web_rtc_does_nothing(monkeypatch):
	monkeypatch.setattr(ice_mod, "_web_rtc", lambda: None)
	hass = MagicMock()
	ice = ice_mod.IceServers(hass, SimpleNamespace(entry_id="e1"))
	ice.start()
	hass.async_create_background_task.assert_not_called()


def test_an_unlinked_home_starts_nothing(monkeypatch):
	monkeypatch.setattr(ice_mod.hs, "is_linked", lambda entry: False)
	hass = MagicMock()
	hass.data = {}
	asyncio.run(ice_mod.async_start_ice_servers(hass, SimpleNamespace(entry_id="e1")))
	assert hass.data.get(ice_mod.DOMAIN, {}).get(ice_mod._KEY, {}) == {}


def test_starting_can_never_fail_setup(monkeypatch):
	def boom(entry):
		raise RuntimeError("unreadable entry")
	monkeypatch.setattr(ice_mod.hs, "is_linked", boom)
	hass = MagicMock()
	hass.data = {}
	asyncio.run(ice_mod.async_start_ice_servers(hass, SimpleNamespace(entry_id="e1")))
