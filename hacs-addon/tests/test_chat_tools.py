# flake8: noqa
"""The AI chat tools: who may use which, and what is never theirs to touch.

The behaviour against a real Home Assistant (states, automations.yaml, the
LLM API) is in ``tests_core/test_chat_tools_core.py``; these are the rules.
"""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.vomesync import chat_tools as ct


def _hass(options=None, admin=True, services=()):
	hass = MagicMock()
	entry = SimpleNamespace(options=options or {})
	hass.config_entries.async_entries.return_value = [entry]
	user = SimpleNamespace(is_admin=admin)
	hass.auth.async_get_user = AsyncMock(return_value=user)
	hass.services.has_service = lambda d, s: (d, s) in services
	hass.services.async_call = AsyncMock()
	hass.async_create_task = lambda coro: asyncio.ensure_future(coro)
	hass.states.get.return_value = None
	return hass


def run(coro):
	return asyncio.run(coro)


def test_every_tool_has_a_scope_and_a_schema_both_ways():
	names = [t.name for t in ct.TOOLS]
	assert len(names) == len(set(names))
	for tool in ct.TOOLS:
		assert tool.scope in ct.ALL_SCOPES
		schema = tool.json_schema()
		assert schema["type"] == "object"
		assert set(schema["required"]) <= set(schema["properties"])
		tool.vol_schema()  # builds


def test_only_configuration_tools_ask_first():
	assert {t.name for t in ct.TOOLS if t.confirm} == {"save_automation", "delete_automation"}
	assert all(t.scope == ct.SCOPE_CONFIGURE for t in ct.TOOLS if t.confirm)


def test_an_unconfigured_house_allows_everything_to_an_administrator():
	assert run(ct.async_effective_scopes(_hass(), "u1")) == list(ct.ALL_SCOPES)


def test_the_owners_choice_is_kept():
	hass = _hass({ct.CONF_CHAT_SCOPES: ["read"]})
	assert run(ct.async_effective_scopes(hass, "u1")) == ["read"]
	assert [t.scope for t in ct.tools_for(["read"])] == ["read"] * len(ct.tools_for(["read"]))


@pytest.mark.parametrize("user_id", [None, "household"])
def test_anyone_not_an_administrator_only_reads(user_id):
	hass = _hass(admin=False)
	assert run(ct.async_effective_scopes(hass, user_id)) == ["read"]
	with pytest.raises(ct.ChatToolError, match="switched off"):
		run(ct.async_call_tool(hass, "call_service",
		                       {"domain": "light", "service": "turn_on"}, user_id))


@pytest.mark.parametrize("domain,service,targets", [
	("lock", "unlock", ["lock.front_door"]),
	("cover", "open_cover", ["cover.garage"]),
	("homeassistant", "restart", []),
	("homeassistant", "turn_on", ["lock.front_door"]),
	("shell_command", "anything", []),
	("vomesync", "agent_key_issue", []),
])
def test_physical_security_and_system_actions_are_refused(domain, service, targets):
	hass = _hass(services={(domain, service)})
	with pytest.raises(ct.ChatToolError):
		run(ct.async_call_tool(hass, "call_service", {
			"domain": domain, "service": service, "entity_ids": targets}, "u1"))
	hass.services.async_call.assert_not_called()


def test_a_lock_hidden_in_the_data_is_still_refused():
	hass = _hass(services={("homeassistant", "turn_on")})
	with pytest.raises(ct.ChatToolError):
		run(ct.async_call_tool(hass, "call_service", {
			"domain": "homeassistant", "service": "turn_on",
			"data": {"entity_id": "lock.front_door"}}, "u1"))
	with pytest.raises(ct.ChatToolError, match="area_id"):
		run(ct.async_call_tool(hass, "call_service", {
			"domain": "homeassistant", "service": "turn_on",
			"data": {"area_id": "hall"}}, "u1"))
	hass.services.async_call.assert_not_called()


def test_a_light_is_switched_as_the_caller():
	hass = _hass(services={("light", "turn_on")})
	out = run(ct.async_call_tool(hass, "call_service", {
		"domain": "light", "service": "turn_on",
		"entity_ids": ["light.hall"], "data": {"brightness_pct": 40}}, "u1"))
	assert out["done"] == "light.turn_on"
	args, kwargs = hass.services.async_call.call_args
	assert args[:3] == ("light", "turn_on", {"brightness_pct": 40, "entity_id": ["light.hall"]})
	assert kwargs["context"].user_id == "u1"


def test_reads_home_assistant_keeps_to_administrators_stay_theirs():
	# Core's template, system log and automation-config APIs require admin.
	household = run(ct.async_tools_for_user(_hass(admin=False), "kid"))
	names = {t.name for t in household}
	assert names == {"find_entities", "get_state", "get_history", "list_areas", "list_automations"}
	with pytest.raises(ct.ChatToolError, match="switched off"):
		run(ct.async_call_tool(_hass(admin=False), "render_template", {"template": "{{ 1 }}"}, "kid"))
	admin = {t.name for t in run(ct.async_tools_for_user(_hass(), "u1"))}
	assert {"render_template", "get_error_log", "get_automation"} <= admin


def test_a_long_action_is_left_running_rather_than_waited_for(monkeypatch):
	monkeypatch.setattr(ct, "SERVICE_WAIT", 0.05)
	hass = _hass(services={("script", "long_one")})

	async def slow(*args, **kwargs):
		await asyncio.sleep(1)
	hass.services.async_call = slow

	async def go():
		return await ct.async_call_tool(hass, "call_service",
		                                {"domain": "script", "service": "long_one"}, "u1")
	out = run(go())
	assert out["started"] == "script.long_one" and "Still running" in out["note"]


def test_bad_arguments_are_explained_not_raised_raw():
	with pytest.raises(ct.ChatToolError, match="Bad arguments"):
		run(ct.async_call_tool(_hass(), "get_state", {}, "u1"))
	with pytest.raises(ct.ChatToolError, match="no tool"):
		run(ct.async_call_tool(_hass(), "rm_rf", {}, "u1"))


def test_automation_yaml_may_not_reach_other_files():
	with pytest.raises(ct.ChatToolError, match="not valid YAML"):
		ct._parse_automation("alias: x\naction: !include secrets.yaml\n")
	with pytest.raises(ct.ChatToolError, match="not valid YAML"):
		ct._parse_automation("alias: x\npassword: !secret wifi\n")
	assert ct._parse_automation("- alias: one\n  triggers: []\n") == {"alias": "one", "triggers": []}


def test_a_huge_answer_is_cut_and_says_so():
	out = ct._fit({"x": "y" * (ct.MAX_RESULT_CHARS + 10)})
	assert out["truncated"] is True
	assert len(out["partial"]) == ct.MAX_RESULT_CHARS
	assert ct._fit({"a": 1}) == {"a": 1}


def test_setting_scopes_needs_an_administrator():
	hass = _hass(admin=False)
	handlers = {}
	hass.services.async_register = lambda d, name, fn, **kw: handlers.__setitem__(name, fn)
	ct.async_register_chat_services(hass)
	call = SimpleNamespace(data={"scopes": ["read"]}, context=SimpleNamespace(user_id="kid"))
	assert "administrator" in run(handlers["chat_set_scopes"](call))["error"]
	hass.config_entries.async_update_entry.assert_not_called()
	listed = run(handlers["chat_tools"](call))
	assert listed["scopes"] == ["read"]
	assert all(t["scope"] == "read" for t in listed["tools"])
