# flake8: noqa
"""Switch extras on a real Core: the actions are administrators' only, an
entity id finds its switch through the entity registry, and services.yaml
loads as Home Assistant reads it.

Nothing dials out: the switch API is stubbed, as in test_platforms_core.
"""
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import Context
from homeassistant.exceptions import HomeAssistantError, Unauthorized
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.vomesync.const import DOMAIN
from test_platforms_core import _stubbed

UID = "vs_extras0test"


@pytest.fixture(autouse=True)
def _custom_integrations(enable_custom_integrations):
	yield


@pytest.fixture
async def owned(hass):
	entry = MockConfigEntry(
		domain=DOMAIN,
		title="Vome",
		data={"personal_key": "", "server_url": "https://sync.vome.test",
			"websocket_url": "wss://sync.vome.test", "auth_mode": "crypto",
			"crypto_seed": "A" * 43},
		options={"imported_switches": {UID: {"name": "Lamp", "is_owner": True, "crypto_index": 0}}},
	)
	entry.add_to_hass(hass)
	with _stubbed(), patch("custom_components.vomesync.websocket_client.VomeSyncWebSocketClient.subscribe", AsyncMock()):
		assert await hass.config_entries.async_setup(entry.entry_id)
		await hass.async_block_till_done()
		coordinator = hass.data[DOMAIN][entry.entry_id]
		coordinator.switches = {UID: {"name": "Lamp", "state": False, "is_owner": True, "index": 0}}
		yield coordinator
		assert await hass.config_entries.async_unload(entry.entry_id)
		await hass.async_block_till_done()


def _entity_id(hass):
	from homeassistant.helpers import entity_registry as er
	return er.async_get(hass).async_get_entity_id("switch", DOMAIN, f"vomesync_{UID}")


@pytest.mark.parametrize("service,data,response", [
	("set_state", {"state": "on", "note": "hi"}, False),
	("set_properties", {"properties": {"wattage": 40}}, False),
	("set_reward", {"clear": True}, False),
	("set_commerce", {"toggle_mode": "public"}, True),
	("reward_claims", {}, True),
])
async def test_a_household_member_cannot_change_a_switchs_extras(
	hass, owned, hass_read_only_user, service, data, response,
):
	with pytest.raises(Unauthorized):
		await hass.services.async_call(
			DOMAIN, service, {"entity_id": _entity_id(hass), **data}, blocking=True,
			return_response=response, context=Context(user_id=hass_read_only_user.id),
		)


@pytest.mark.parametrize("service", ["connect_payments", "payments_status", "my_rewards"])
async def test_a_household_member_cannot_reach_payments(hass, owned, hass_read_only_user, service):
	with pytest.raises(Unauthorized):
		await hass.services.async_call(
			DOMAIN, service, {}, blocking=True, return_response=True,
			context=Context(user_id=hass_read_only_user.id),
		)


async def test_an_administrator_sets_a_note_on_the_registry_entity(hass, owned, hass_admin_user):
	owned.set_switch_state = AsyncMock(return_value=True)
	await hass.services.async_call(
		DOMAIN, "set_state",
		{"entity_id": _entity_id(hass), "state": "on", "note": "Now playing: Abba", "params": {"song": "SOS"}},
		blocking=True, context=Context(user_id=hass_admin_user.id),
	)
	owned.set_switch_state.assert_awaited_once_with(UID, True, params={"song": "SOS"}, note="Now playing: Abba")

	with pytest.raises(HomeAssistantError, match="280"):
		await hass.services.async_call(
			DOMAIN, "set_state", {"entity_id": _entity_id(hass), "state": "off", "note": "x" * 281},
			blocking=True, context=Context(user_id=hass_admin_user.id),
		)


async def test_the_actions_are_described(hass, owned):
	from homeassistant.helpers.service import async_get_all_descriptions
	descriptions = (await async_get_all_descriptions(hass))[DOMAIN]
	for name in ("set_state", "set_properties", "set_commerce", "set_reward",
			"connect_payments", "payments_status", "reward_claims", "my_rewards"):
		assert descriptions[name]["fields"] is not None, name
	assert "on" in descriptions["set_reward"]["fields"]
