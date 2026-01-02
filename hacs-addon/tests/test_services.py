"""Tests for VomeSync integration services."""
import pytest
from unittest.mock import AsyncMock, MagicMock

from custom_components.vomesync.const import DOMAIN
from custom_components.vomesync import _register_services


@pytest.mark.asyncio
async def test_services_call_coordinator_methods(hass, config_entry):
	"""Services should be registered with expected schemas."""
	mock_coordinator = MagicMock()
	mock_coordinator.create_switch = AsyncMock(return_value="uid-1")
	mock_coordinator.subscribe_to_switch = AsyncMock(return_value=True)
	mock_coordinator.is_switch_owner = MagicMock(return_value=True)
	mock_coordinator.delete_switch = AsyncMock(return_value=True)

	hass.data = {DOMAIN: {config_entry.entry_id: mock_coordinator}}
	hass.services = MagicMock()
	hass.services.async_register = MagicMock()

	_register_services(hass)

	assert hass.services.async_register.call_count == 3
	calls = hass.services.async_register.call_args_list
	
	# Ensure the subscribe service schema only requires uid (no name)
	subscribe = [c for c in calls if c[0][1] == "subscribe_switch"][0]
	schema = subscribe[1]["schema"]
	assert "uid" in schema.schema
	assert "name" not in schema.schema

	# Create switch should support optional theming fields
	create = [c for c in calls if c[0][1] == "create_switch"][0]
	create_schema = create[1]["schema"]
	assert "link" in create_schema.schema
	assert "icon_url" in create_schema.schema
	assert "banner_url" in create_schema.schema
	assert "captcha_token" in create_schema.schema

