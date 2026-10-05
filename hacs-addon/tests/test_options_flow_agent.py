# flake8: noqa
"""The Coding agent page on the integration's Configure menu: the free MCP key
for installs (HACS) that have no add-on panel."""
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.data_entry_flow import FlowResultType

from custom_components.vomesync.config_flow import VomeSyncOptionsFlow
from custom_components.vomesync.options_flow_agent import (
	CLAUDE_CONNECT,
	CLAUDE_PANES,
	issued_info,
	manage_info,
)

ISSUED = {
	"token": "vome_tok_abc123",
	"scopes": ["ha:read", "ha:write"],
	"expires_at": 1_000_000 + 47 * 3600,
	"mcp": {
		"json": '{"mcpServers": {"vome": {}}}',
		"clients": [{"id": "claude", "connect": {
			"command": "/plugin install vome-connect --marketplace Vortitron/home-assistant-mcp",
			"all_panes": "/plugin install vome-panes --marketplace Vortitron/home-assistant-mcp",
		}}],
	},
}


def test_the_issued_page_shows_the_key_the_commands_and_when_it_ends():
	text = issued_info(ISSUED, now=1_000_000)
	assert "vome_tok_abc123" in text
	assert CLAUDE_CONNECT in text
	assert CLAUDE_PANES in text
	assert '{"mcpServers"' in text
	assert "see states, history and settings, control devices" in text
	assert "in 47 hours" in text


def test_the_commands_fall_back_when_vome_sends_none():
	text = issued_info({"token": "t", "scopes": [], "mcp": {}}, now=0)
	assert CLAUDE_CONNECT in text and CLAUDE_PANES in text
	assert "Cursor" not in text  # no mcp.json to show


def test_the_manage_page_never_shows_a_key():
	text = manage_info({"active": True, "scopes": ["ha:read"], "expires_at": 7200}, now=0)
	assert "in 2 hours" in text
	assert "shown once" in text
	assert "vome_tok" not in text


def _flow(hass, config_entry):
	flow = VomeSyncOptionsFlow(config_entry)
	flow.hass = hass
	return flow


@pytest.mark.asyncio
async def test_a_house_without_a_key_is_offered_the_permissions(hass, config_entry):
	state = {"offer": "issue", "offered_scopes": ["ha:read", "ha:write", "ha:config", "ha:files"],
		"default_scopes": ["ha:read", "ha:write", "ha:config"]}
	with patch("custom_components.vomesync.agent_key.async_panel_state", new=AsyncMock(return_value=state)):
		result = await _flow(hass, config_entry).async_step_coding_agent()
	assert result["type"] == FlowResultType.FORM
	assert result["step_id"] == "coding_agent"


@pytest.mark.asyncio
async def test_issuing_shows_the_key_once_then_forgets_it(hass, config_entry):
	state = {"offer": "issue", "offered_scopes": ["ha:read"], "default_scopes": ["ha:read"]}
	flow = _flow(hass, config_entry)
	with patch("custom_components.vomesync.agent_key.async_panel_state", new=AsyncMock(return_value=state)), \
		patch("custom_components.vomesync.agent_key.async_request_key", new=AsyncMock(return_value=ISSUED)) as request:
		shown = await flow.async_step_coding_agent({"scopes": ["ha:read"]})
	request.assert_awaited_once()
	assert shown["step_id"] == "coding_agent_issued"
	assert "vome_tok_abc123" in shown["description_placeholders"]["info"]
	with patch.object(flow, "async_step_init", new=AsyncMock(return_value={"step_id": "init"})):
		after = await flow.async_step_coding_agent_issued({})
	assert after["step_id"] == "init"
	assert "agent_issued" not in flow._step_data


@pytest.mark.asyncio
async def test_a_house_with_a_key_gets_the_manage_menu(hass, config_entry):
	state = {"offer": "manage", "active": True, "scopes": ["ha:read"], "expires_at": None, "seconds_left": 3600}
	with patch("custom_components.vomesync.agent_key.async_panel_state", new=AsyncMock(return_value=state)):
		result = await _flow(hass, config_entry).async_step_coding_agent()
	assert result["type"] == FlowResultType.MENU
	assert set(result["menu_options"]) >= {"coding_agent_scopes", "coding_agent_reissue", "coding_agent_revoke"}


@pytest.mark.asyncio
async def test_a_linked_house_is_sent_to_its_account(hass, config_entry):
	state = {"offer": "linked_account", "portal_url": "https://vome.io"}
	with patch("custom_components.vomesync.agent_key.async_panel_state", new=AsyncMock(return_value=state)):
		result = await _flow(hass, config_entry).async_step_coding_agent()
	assert result["step_id"] == "coding_agent_elsewhere"
	assert "https://vome.io/account/api-tokens" in result["description_placeholders"]["info"]


def test_the_menu_offers_it_and_every_step_has_words():
	import json
	from pathlib import Path

	root = Path(__file__).resolve().parents[2]
	steps = json.loads((root / "custom_components/vomesync/translations/en.json").read_text())["options"]["step"]
	assert "coding_agent" in steps["init"]["menu_options"]
	for step in ("coding_agent", "coding_agent_issued", "coding_agent_manage",
		"coding_agent_elsewhere", "coding_agent_scopes", "coding_agent_reissue", "coding_agent_revoke"):
		assert steps[step]["title"], step
