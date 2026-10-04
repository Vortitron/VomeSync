# flake8: noqa
"""ice_servers against a real Core: the login Vome issues is what HA hands
to the browser (the ``web_rtc/ice_servers`` command and every camera's
client configuration both read ``async_get_ice_servers``, and go2rtc gets
the camera's configuration with each offer)."""
from types import SimpleNamespace

import pytest
from homeassistant.components.web_rtc import async_get_ice_servers
from homeassistant.setup import async_setup_component

from custom_components.vomesync import ice_servers as ice_mod

ON = {
	"enabled": True,
	"ice_servers": [
		{"urls": ["stun:stun.cloudflare.com:3478"]},
		{
			"urls": ["turns:turn.cloudflare.com:443?transport=tcp"],
			"username": "u-1",
			"credential": "c-1",
		},
	],
	"expires_at": 4_000_000_000,
	"refresh_after": 43200,
}


@pytest.fixture
def vome(monkeypatch):
	answers = []

	async def _agent_request(session, method, portal_url, path, secret, payload=None):
		return answers.pop(0)

	monkeypatch.setattr(ice_mod, "_agent_request", _agent_request)
	monkeypatch.setattr(ice_mod.hs, "_agent_credentials", lambda entry: ("srv-1", "secret"))
	monkeypatch.setattr(ice_mod.hs, "_portal_url", lambda entry: "https://vome.test")
	return answers


def _ours(hass):
	return [s for s in async_get_ice_servers(hass) if s.username == "u-1"]


async def test_the_login_reaches_web_rtc(hass, vome):
	assert await async_setup_component(hass, "web_rtc", {})
	ice = ice_mod.IceServers(hass, SimpleNamespace(entry_id="e1"))
	ice._register_when_loaded()
	vome.append(ON)
	await ice.refresh()

	(server,) = _ours(hass)
	assert server.urls == ["turns:turn.cloudflare.com:443?transport=tcp"]
	assert server.credential == "c-1"
	# What the browser is sent.
	assert {"urls": server.urls, "username": "u-1", "credential": "c-1"} in [
		s.to_dict() for s in async_get_ice_servers(hass)
	]


async def test_the_browser_command_returns_it(hass, hass_ws_client, vome):
	assert await async_setup_component(hass, "web_rtc", {})
	ice = ice_mod.IceServers(hass, SimpleNamespace(entry_id="e1"))
	ice._register_when_loaded()
	vome.append(ON)
	await ice.refresh()

	client = await hass_ws_client(hass)
	await client.send_json({"id": 1, "type": "web_rtc/ice_servers"})
	msg = await client.receive_json()
	assert msg["success"]
	assert any(s.get("username") == "u-1" for s in msg["result"])


async def test_registers_when_web_rtc_loads_later(hass, vome):
	ice = ice_mod.IceServers(hass, SimpleNamespace(entry_id="e1"))
	ice._register_when_loaded()
	vome.append(ON)
	await ice.refresh()
	assert await async_setup_component(hass, "web_rtc", {})
	await hass.async_block_till_done()
	assert len(_ours(hass)) == 1


async def test_off_leaves_hass_default_stun(hass, vome):
	assert await async_setup_component(hass, "web_rtc", {})
	ice = ice_mod.IceServers(hass, SimpleNamespace(entry_id="e1"))
	ice._register_when_loaded()
	vome.append({"enabled": False, "reason": "coming soon", "retry_after": 21600})
	await ice.refresh()
	urls = [u for s in async_get_ice_servers(hass) for u in s.urls]
	assert urls == ["stun:stun.home-assistant.io:3478", "stun:stun.home-assistant.io:80"]


async def test_start_and_stop_on_a_real_loop(hass, vome):
	assert await async_setup_component(hass, "web_rtc", {})
	ice = ice_mod.IceServers(hass, SimpleNamespace(entry_id="e1"))
	vome.append(ON)
	ice.start()
	await hass.async_block_till_done(wait_background_tasks=False)
	for _ in range(20):
		if _ours(hass):
			break
		await hass.async_block_till_done()
	assert len(_ours(hass)) == 1
	await ice.stop()
	assert _ours(hass) == []
