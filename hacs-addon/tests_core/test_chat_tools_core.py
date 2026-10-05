# flake8: noqa
"""The AI chat tools against a real Core: the "Vome" LLM API that Assist's
agents are given, and the tools doing their work on a real house — states,
areas, an input_boolean, and automations.yaml written the way the editor
writes it and picked up by a reload."""
import pytest
from homeassistant.core import Context
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import llm
from homeassistant.setup import async_setup_component

from custom_components.vomesync import chat_tools as ct
from custom_components.vomesync.llm_api import API_ID, async_register_llm_api


def _context(user):
	return llm.LLMContext(
		platform="test", context=Context(user_id=user.id if user else None),
		language="en", assistant="conversation", device_id=None,
	)


@pytest.fixture
async def house(hass, tmp_path):
	(tmp_path / "configuration.yaml").write_text("automation: !include automations.yaml\n")
	(tmp_path / "automations.yaml").write_text("[]\n")
	hass.config.config_dir = str(tmp_path)
	assert await async_setup_component(hass, "automation", {"automation": []})
	assert await async_setup_component(hass, "input_boolean", {
		"input_boolean": {"hall_light": {"name": "Hall light"}},
	})
	await hass.async_block_till_done()
	area = ar.async_get(hass).async_create("Hall")
	er.async_get(hass).async_update_entity("input_boolean.hall_light", area_id=area.id)
	assert async_register_llm_api(hass)
	return tmp_path


async def test_assist_is_offered_vome(hass, house, hass_admin_user):
	assert API_ID in [api.id for api in llm.async_get_apis(hass)]
	instance = await llm.async_get_api(hass, API_ID, _context(hass_admin_user))
	names = {t.name for t in instance.tools}
	assert {"find_entities", "call_service", "save_automation"} <= names
	assert "show the owner" in instance.api_prompt


async def test_a_household_member_gets_the_read_tools_only(hass, house, hass_read_only_user):
	instance = await llm.async_get_api(hass, API_ID, _context(hass_read_only_user))
	assert {t.name for t in instance.tools} == {t.name for t in ct.tools_for(["read"], admin=False)}
	assert "render_template" not in {t.name for t in instance.tools}
	assert "look but not change" in instance.api_prompt


async def test_tools_find_and_switch_through_the_llm_api(hass, house, hass_admin_user):
	llm_context = _context(hass_admin_user)
	instance = await llm.async_get_api(hass, API_ID, llm_context)
	tools = {t.name: t for t in instance.tools}

	async def call(name, args):
		# What APIInstance.async_call_tool does, minus its conversation
		# trace (that import needs hassil, which this venv lacks).
		return await tools[name].async_call(
			hass, llm.ToolInput(tool_name=name, tool_args=args), llm_context)

	found = await call("find_entities", {"area": "hall"})
	(row,) = found["result"]["entities"]
	assert row == {"entity_id": "input_boolean.hall_light", "name": "Hall light",
	               "state": "off", "area": "Hall"}

	done = await call("call_service", {
			"domain": "input_boolean", "service": "turn_on",
			"entity_ids": ["input_boolean.hall_light"]})
	assert done["result"]["now"] == {"input_boolean.hall_light": "on"}
	assert hass.states.get("input_boolean.hall_light").context.user_id == hass_admin_user.id

	refused = await call("call_service", {"domain": "lock", "service": "unlock"})
	assert "not available" in refused["error"]

	counted = await call("render_template",
		{"template": "{{ states.input_boolean | selectattr('state','eq','on') | list | count }}"})
	assert counted["result"]["result"] == "1"


AUTOMATION = """
alias: Hall light at sunset
description: Made by the chat
triggers:
  - trigger: sun
    event: sunset
actions:
  - action: input_boolean.turn_on
    target:
      entity_id: input_boolean.hall_light
mode: single
"""


async def test_an_automation_is_written_reloaded_read_and_deleted(hass, house, hass_admin_user):
	saved = await ct.async_call_tool(
		hass, "save_automation", {"yaml": AUTOMATION, "automation_id": "chat_1"},
		hass_admin_user.id)
	assert saved == {"id": "chat_1", "alias": "Hall light at sunset", "result": "created"}
	await hass.async_block_till_done()
	state = hass.states.get("automation.hall_light_at_sunset")
	assert state is not None and state.attributes["id"] == "chat_1"
	assert "id: chat_1" in (house / "automations.yaml").read_text()

	read = await ct.async_call_tool(
		hass, "get_automation", {"automation_id": "automation.hall_light_at_sunset"},
		hass_admin_user.id)
	assert "sunset" in read["yaml"]

	again = await ct.async_call_tool(
		hass, "save_automation",
		{"yaml": AUTOMATION.replace("sunset\n", "sunrise\n", 1), "automation_id": "chat_1"},
		hass_admin_user.id)
	assert again["result"] == "updated"
	assert (house / "automations.yaml").read_text().count("id: chat_1") == 1

	with pytest.raises(ct.ChatToolError, match="would not accept"):
		await ct.async_call_tool(hass, "save_automation",
		                         {"yaml": "alias: broken\ntriggers: nonsense\n"},
		                         hass_admin_user.id)

	await ct.async_call_tool(hass, "delete_automation", {"automation_id": "chat_1"},
	                         hass_admin_user.id)
	await hass.async_block_till_done()
	assert hass.states.get("automation.hall_light_at_sunset") is None
	assert "chat_1" not in (house / "automations.yaml").read_text()
