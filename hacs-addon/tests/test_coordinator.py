"""Tests for VomeSync coordinator."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import time

from custom_components.vomesync.coordinator import VomeSyncCoordinator
from custom_components.vomesync.api_client import VomeSyncAPIError


@pytest.mark.asyncio
async def test_coordinator_updates_cache_on_fetch(hass, config_entry):
	"""Test coordinator updates imported switches cache on API fetch."""
	config_entry.options = {
		"imported_switches": {
			"uid-1": {
				"name": "Switch 1",
				"is_owner": True,
				"cached_data": {"state": False}
			}
		}
	}

	mock_api = AsyncMock()
	mock_api.get_my_switches.return_value = [
		{
			"uid": "uid-1",
			"name": "Switch 1 Updated",
			"description": "Updated description",
			"state": True,
			"is_owner": True
		}
	]
	mock_api.get_switch_status.return_value = {}

	with patch("custom_components.vomesync.coordinator.VomeSyncAPIClient", return_value=mock_api):
		coordinator = VomeSyncCoordinator(hass, config_entry)
		await coordinator._async_update_data()

	# Verify cache was updated
	hass.config_entries.async_update_entry.assert_called()
	call_args = hass.config_entries.async_update_entry.call_args
	updated_options = call_args[1]["options"]
	
	assert "uid-1" in updated_options["imported_switches"]
	assert updated_options["imported_switches"]["uid-1"]["cached_data"]["state"] == True
	assert updated_options["imported_switches"]["uid-1"]["name"] == "Switch 1 Updated"


@pytest.mark.asyncio
async def test_coordinator_auto_imports_new_switch(hass, config_entry):
	"""Test coordinator auto-imports newly created switch."""
	mock_api = AsyncMock()
	mock_api.create_switch.return_value = {
		"uid": "new-uid-123",
		"description": "New Switch",
		"location": "Test",
		"category": "Home",
		"state": False,
		"publicize": False
	}

	with patch("custom_components.vomesync.coordinator.VomeSyncAPIClient", return_value=mock_api):
		coordinator = VomeSyncCoordinator(hass, config_entry)
		coordinator.async_add_switch_entities = MagicMock()
		coordinator._ensure_websocket_connection = AsyncMock()

		uid = await coordinator.create_switch(
			name="New Switch",
			description="New Switch",
			location="Test",
			category="Home",
			publicize=False
		)

	assert uid == "new-uid-123"
	
	# Verify it was added to imported_switches
	hass.config_entries.async_update_entry.assert_called()
	call_args = hass.config_entries.async_update_entry.call_args
	updated_options = call_args[1]["options"]
	
	assert "imported_switches" in updated_options
	assert "new-uid-123" in updated_options["imported_switches"]
	assert updated_options["imported_switches"]["new-uid-123"]["name"] == "New Switch"
	assert updated_options["imported_switches"]["new-uid-123"]["is_owner"] == True


@pytest.mark.asyncio
async def test_coordinator_auto_imports_new_subscription(hass, config_entry):
	"""Test coordinator auto-imports newly subscribed switch."""
	mock_api = AsyncMock()
	mock_api.get_switch_status.return_value = {
		"uid": "remote-uid-456",
		"description": "Remote Switch",
		"state": False,
	}

	with patch("custom_components.vomesync.coordinator.VomeSyncAPIClient", return_value=mock_api):
		coordinator = VomeSyncCoordinator(hass, config_entry)
		coordinator.async_add_switch_entities = MagicMock()
		coordinator._ensure_websocket_connection = AsyncMock()

		success = await coordinator.subscribe_to_switch(
			name="Remote Switch",
			uid="remote-uid-456"
		)

	assert success == True
	
	# Verify it was added to imported_switches
	hass.config_entries.async_update_entry.assert_called()
	call_args = hass.config_entries.async_update_entry.call_args
	updated_options = call_args[1]["options"]
	
	assert "imported_switches" in updated_options
	assert "remote-uid-456" in updated_options["imported_switches"]
	assert updated_options["imported_switches"]["remote-uid-456"]["name"] == "Remote Switch"
	assert updated_options["imported_switches"]["remote-uid-456"]["is_owner"] == False


@pytest.mark.asyncio
async def test_coordinator_removes_from_cache_on_delete(hass, config_entry):
	"""Test coordinator removes switch from imported cache on delete."""
	config_entry.options = {
		"imported_switches": {
			"uid-to-delete": {
				"name": "Delete Me",
				"is_owner": True,
				"cached_data": {}
			}
		}
	}

	mock_api = AsyncMock()
	mock_api.delete_switch.return_value = True

	with patch("custom_components.vomesync.coordinator.VomeSyncAPIClient", return_value=mock_api):
		coordinator = VomeSyncCoordinator(hass, config_entry)
		coordinator.websocket_client = MagicMock()
		coordinator.websocket_client.unsubscribe = AsyncMock()

		success = await coordinator.delete_switch("uid-to-delete")

	assert success == True
	
	# Verify it was removed from imported_switches
	hass.config_entries.async_update_entry.assert_called()
	call_args = hass.config_entries.async_update_entry.call_args
	updated_options = call_args[1]["options"]
	
	assert "uid-to-delete" not in updated_options["imported_switches"]


@pytest.mark.asyncio
async def test_coordinator_rate_limits_toggle(hass, config_entry):
	"""Test coordinator rate limits rapid toggle requests."""
	mock_api = AsyncMock()
	mock_api.toggle_switch.return_value = {"uid": "test-uid", "state": True}

	with patch("custom_components.vomesync.coordinator.VomeSyncAPIClient", return_value=mock_api):
		coordinator = VomeSyncCoordinator(hass, config_entry)
		coordinator.switches = {"test-uid": {"state": False}}

		# First toggle should succeed
		result1 = await coordinator.toggle_switch("test-uid")
		assert result1 == True
		assert mock_api.toggle_switch.call_count == 1

		# Immediate second toggle should be rate limited
		result2 = await coordinator.toggle_switch("test-uid")
		assert result2 == False
		assert mock_api.toggle_switch.call_count == 1  # Still 1, not called again

		# After cooldown period, should work again
		coordinator._last_toggle_time["test-uid"] = time.time() - 2.0
		result3 = await coordinator.toggle_switch("test-uid")
		assert result3 == True
		assert mock_api.toggle_switch.call_count == 2


@pytest.mark.asyncio
async def test_coordinator_triggers_linked_entities(hass, config_entry):
	"""Test coordinator triggers linked entities on state change."""
	config_entry.options = {
		"linked_entities": {
			"test-uid": ["light.living_room", "switch.bedroom"]
		}
	}

	mock_api = AsyncMock()

	with patch("custom_components.vomesync.coordinator.VomeSyncAPIClient", return_value=mock_api):
		coordinator = VomeSyncCoordinator(hass, config_entry)
		coordinator.switches = {"test-uid": {"state": False}}
		coordinator.subscriptions = {}

		hass.services = MagicMock()
		hass.services.async_call = AsyncMock()

		# Simulate WebSocket state update message
		message = {
			"type": "state_update",
			"uid": "test-uid",
			"state": True,
			"timestamp": 1640995200000,
			"params": {
				"rgb_color": [0, 0, 255],
				"brightness": 180
			}
		}

		await coordinator._handle_websocket_message(message)

		# Verify linked entities were triggered
		assert hass.services.async_call.call_count == 2
		calls = hass.services.async_call.call_args_list
		
		# Check turn_on was called for both entities with params for light
		assert calls[0][0] == ("light", "turn_on")
		assert calls[0][1]["service_data"]["entity_id"] == "light.living_room"
		assert calls[0][1]["service_data"]["rgb_color"] == [0, 0, 255]
		assert calls[0][1]["service_data"]["brightness"] == 180
		
		assert calls[1][0] == ("switch", "turn_on")
		assert calls[1][1]["service_data"]["entity_id"] == "switch.bedroom"


@pytest.mark.asyncio
async def test_coordinator_triggers_linked_entities_with_unsupported_params(hass, config_entry):
	"""Test coordinator falls back when params not supported by target entity."""
	config_entry.options = {
		"linked_entities": {
			"test-uid": ["switch.bedroom"]
		}
	}

	mock_api = AsyncMock()

	with patch("custom_components.vomesync.coordinator.VomeSyncAPIClient", return_value=mock_api):
		coordinator = VomeSyncCoordinator(hass, config_entry)
		coordinator.switches = {"test-uid": {"state": False}}
		coordinator.subscriptions = {}

		# Mock services to fail on first call (with params) and succeed on fallback
		async def side_effect(domain, service, service_data=None, blocking=False):
			if "brightness" in (service_data or {}):
				raise Exception("Unsupported params")
			return True

		hass.services = MagicMock()
		hass.services.async_call = AsyncMock(side_effect=side_effect)

		message = {
			"type": "state_update",
			"uid": "test-uid",
			"state": True,
			"timestamp": 1640995200000,
			"params": {
				"brightness": 200
			}
		}

		await coordinator._handle_websocket_message(message)

		# Two calls: first with params (fail), second fallback without params
		assert hass.services.async_call.call_count == 2
		first_call = hass.services.async_call.call_args_list[0]
		second_call = hass.services.async_call.call_args_list[1]

		assert first_call[0][0] == "switch"
		assert first_call[0][1] == "turn_on"
		assert first_call[1]["service_data"]["entity_id"] == "switch.bedroom"
		assert "brightness" in first_call[1]["service_data"]

		assert second_call[0][0] == "switch"
		assert second_call[0][1] == "turn_on"
		assert second_call[1]["service_data"]["entity_id"] == "switch.bedroom"
		assert "brightness" not in second_call[1]["service_data"]


@pytest.mark.asyncio
async def test_coordinator_rate_limits_linked_entity_triggers(hass, config_entry):
	"""Test coordinator rate limits linked entity triggers to prevent loops."""
	config_entry.options = {
		"linked_entities": {
			"test-uid": ["light.living_room"]
		}
	}

	mock_api = AsyncMock()

	with patch("custom_components.vomesync.coordinator.VomeSyncAPIClient", return_value=mock_api):
		coordinator = VomeSyncCoordinator(hass, config_entry)

		hass.services = MagicMock()
		hass.services.async_call = AsyncMock()

		# First trigger should work
		await coordinator._trigger_linked_entities("test-uid", True)
		assert hass.services.async_call.call_count == 1

		# Immediate second trigger should be rate limited
		await coordinator._trigger_linked_entities("test-uid", False)
		assert hass.services.async_call.call_count == 1  # Still 1, not called again

		# After cooldown, should work again
		coordinator._last_trigger_time["test-uid"] = time.time() - 3.0
		await coordinator._trigger_linked_entities("test-uid", False)
		assert hass.services.async_call.call_count == 2


@pytest.mark.asyncio
async def test_coordinator_handles_api_errors_gracefully(hass, config_entry):
	"""Test coordinator handles API errors without crashing."""
	mock_api = AsyncMock()
	mock_api.get_my_switches.side_effect = VomeSyncAPIError("API Error")

	with patch("custom_components.vomesync.coordinator.VomeSyncAPIClient", return_value=mock_api):
		coordinator = VomeSyncCoordinator(hass, config_entry)
		
		# Should not raise exception
		result = await coordinator._async_update_data()
		
		# Should return empty data on error
		assert result is None or result == {}
