"""Tests for VomeSync config and options flows."""
import pytest
from unittest.mock import AsyncMock, patch

from homeassistant.data_entry_flow import FlowResultType
from homeassistant.const import CONF_NAME

from custom_components.vomesync.config_flow import (
	VomeSyncConfigFlow,
	VomeSyncOptionsFlow,
)
from custom_components.vomesync.const import (
	CONF_SERVER_URL,
	CONF_WEBSOCKET_URL,
	CONF_SWITCH_NAME,
	CONF_SWITCH_DESCRIPTION,
	CONF_SWITCH_LOCATION,
	CONF_SWITCH_CATEGORY,
	CONF_SWITCH_PUBLICIZE,
)


@pytest.mark.asyncio
async def test_config_flow_derives_websocket_when_blank(hass):
	"""WebSocket URL should auto-derive from HTTP when left blank."""
	flow = VomeSyncConfigFlow()
	flow.hass = hass

	result = await flow.async_step_user({
		CONF_SERVER_URL: "http://example.com:3000",
		CONF_WEBSOCKET_URL: "",
	})

	assert result["type"] == FlowResultType.FORM
	assert result["step_id"] == "generate_key"
	assert flow._websocket_url == "ws://example.com:3000/ws"


@pytest.mark.asyncio
async def test_options_flow_create_switch_success(config_entry):
	"""Options flow should create switch and store options."""
	mock_api = AsyncMock()
	mock_api.create_switch.return_value = {
		"uid": "new-switch-uid",
		"description": "Made in test",
		"location": "Here",
		"category": "Test",
		"state": False,
		"publicize": False,
	}

	with patch(
		"custom_components.vomesync.config_flow.VomeSyncAPIClient",
		return_value=mock_api
	):
		flow = VomeSyncOptionsFlow(config_entry)
		result = await flow.async_step_create_switch({
			CONF_SWITCH_NAME: "My Test Switch",
			CONF_SWITCH_DESCRIPTION: "Made in test",
			CONF_SWITCH_LOCATION: "Here",
			CONF_SWITCH_CATEGORY: "Test",
			CONF_SWITCH_PUBLICIZE: False,
		})

	assert result["type"] == FlowResultType.CREATE_ENTRY
	options = result["data"]
	assert "switches" in options
	assert "My Test Switch" in options["switches"]
	assert options["switches"]["My Test Switch"]["uid"] == "new-switch-uid"

