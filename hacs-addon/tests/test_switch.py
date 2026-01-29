# flake8: noqa
"""Tests for VomeSync switch platform."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from custom_components.vomesync.switch import VomeSyncSwitch, async_setup_entry


@pytest.mark.asyncio
async def test_switch_created_from_imported_cache(hass, config_entry):
	"""Test switches are created from imported_switches cache."""
	config_entry.options = {
		"imported_switches": {
			"uid-1": {
				"name": "Test Switch 1",
				"is_owner": True,
				"cached_data": {"state": False}
			},
			"uid-2": {
				"name": "Test Switch 2",
				"is_owner": False,
				"cached_data": {"state": True}
			}
		}
	}

	mock_coordinator = MagicMock()
	mock_coordinator.switches = {}
	mock_coordinator.subscriptions = {}
	mock_coordinator.async_config_entry_first_refresh = AsyncMock()
	mock_coordinator.async_add_listener = MagicMock()

	added_entities = []

	def mock_add_entities(entities):
		added_entities.extend(entities)

	with patch("custom_components.vomesync.switch.VomeSyncCoordinator", return_value=mock_coordinator):
		await async_setup_entry(hass, config_entry, mock_add_entities)

	# Verify owner + subscription entities are created from cache
	assert len(added_entities) == 2
	
	entity_by_uid = {entity._uid: entity for entity in added_entities}
	assert "uid-1" in entity_by_uid
	assert "uid-2" in entity_by_uid
	
	owner_entity = entity_by_uid["uid-1"]
	sub_entity = entity_by_uid["uid-2"]
	
	assert owner_entity._name == "Test Switch 1"
	assert owner_entity._is_owner == True
	assert owner_entity._uid == "uid-1"
	
	assert sub_entity._name == "Test Switch 2"
	assert sub_entity._is_owner == False
	assert sub_entity._uid == "uid-2"


@pytest.mark.asyncio
async def test_switch_created_for_subscription_with_access_key(hass, config_entry):
	"""Subscribed switch with access key should create a switch entity."""
	config_entry.options = {
		"imported_switches": {
			"uid-sub": {
				"name": "Remote Switch",
				"is_owner": False,
				"access_key": "access-123",
				"cached_data": {"state": False},
			},
		}
	}

	mock_coordinator = MagicMock()
	mock_coordinator.switches = {}
	mock_coordinator.subscriptions = {}
	mock_coordinator.async_config_entry_first_refresh = AsyncMock()
	mock_coordinator.async_add_listener = MagicMock()

	added_entities = []

	def mock_add_entities(entities):
		added_entities.extend(entities)

	with patch("custom_components.vomesync.switch.VomeSyncCoordinator", return_value=mock_coordinator):
		await async_setup_entry(hass, config_entry, mock_add_entities)

	assert len(added_entities) == 1
	entity = added_entities[0]
	assert entity._uid == "uid-sub"
	assert entity._is_owner == False


@pytest.mark.asyncio
async def test_switch_entity_state_from_coordinator(hass, config_entry):
	"""Test switch entity gets state from coordinator."""
	mock_coordinator = MagicMock()
	mock_coordinator.switches = {
		"test-uid": {
			"state": True,
			"description": "Test",
		}
	}
	mock_coordinator.subscriptions = {}
	mock_coordinator.last_update_success = True

	switch = VomeSyncSwitch(
		coordinator=mock_coordinator,
		uid="test-uid",
		name="Test Switch",
		is_owner=True,
		config_entry=config_entry
	)

	assert switch.is_on == True
	assert switch.available == True


@pytest.mark.asyncio
async def test_switch_entity_unavailable_when_no_data(hass, config_entry):
	"""Test switch entity is unavailable when no data from coordinator."""
	mock_coordinator = MagicMock()
	mock_coordinator.switches = {}
	mock_coordinator.subscriptions = {}
	mock_coordinator.last_update_success = True

	switch = VomeSyncSwitch(
		coordinator=mock_coordinator,
		uid="missing-uid",
		name="Missing Switch",
		is_owner=True,
		config_entry=config_entry
	)

	assert switch.available == False


@pytest.mark.asyncio
async def test_switch_turn_on_calls_coordinator(hass, config_entry):
	"""Test turning on switch calls coordinator set_switch_state."""
	mock_coordinator = MagicMock()
	mock_coordinator.switches = {
		"test-uid": {"state": False}
	}
	mock_coordinator.subscriptions = {}
	mock_coordinator.last_update_success = True
	mock_coordinator.set_switch_state = AsyncMock(return_value=True)

	switch = VomeSyncSwitch(
		coordinator=mock_coordinator,
		uid="test-uid",
		name="Test Switch",
		is_owner=True,
		config_entry=config_entry
	)

	await switch.async_turn_on()

	mock_coordinator.set_switch_state.assert_called_once_with("test-uid", True, params=None)


@pytest.mark.asyncio
async def test_switch_turn_off_calls_coordinator(hass, config_entry):
	"""Test turning off switch calls coordinator set_switch_state."""
	mock_coordinator = MagicMock()
	mock_coordinator.switches = {
		"test-uid": {"state": True}
	}
	mock_coordinator.subscriptions = {}
	mock_coordinator.last_update_success = True
	mock_coordinator.set_switch_state = AsyncMock(return_value=True)

	switch = VomeSyncSwitch(
		coordinator=mock_coordinator,
		uid="test-uid",
		name="Test Switch",
		is_owner=True,
		config_entry=config_entry
	)

	await switch.async_turn_off()

	mock_coordinator.set_switch_state.assert_called_once_with("test-uid", False, params=None)


@pytest.mark.asyncio
async def test_switch_toggle_denied_for_non_owners(hass, config_entry):
	"""Test non-owners cannot toggle subscribed switches."""
	mock_coordinator = MagicMock()
	mock_coordinator.subscriptions = {
		"test-uid": {"state": False}
	}
	mock_coordinator.switches = {}
	mock_coordinator.last_update_success = True

	switch = VomeSyncSwitch(
		coordinator=mock_coordinator,
		uid="test-uid",
		name="Subscribed Switch",
		is_owner=False,
		config_entry=config_entry
	)

	# Attempting to toggle should raise an exception or do nothing
	with pytest.raises(Exception):
		await switch.async_toggle()


@pytest.mark.asyncio
async def test_switch_turn_on_with_access_key(hass, config_entry):
	"""Subscribed switch should toggle when access key is present."""
	mock_coordinator = MagicMock()
	mock_coordinator.subscriptions = {"test-uid": {"state": False}}
	mock_coordinator.switches = {}
	mock_coordinator.last_update_success = True
	mock_coordinator.get_subscription_access_key = MagicMock(return_value="access-123")
	mock_coordinator.toggle_switch_with_access_key = AsyncMock(return_value=True)

	switch = VomeSyncSwitch(
		coordinator=mock_coordinator,
		uid="test-uid",
		name="Subscribed Switch",
		is_owner=False,
		config_entry=config_entry
	)

	await switch.async_turn_on()

	mock_coordinator.toggle_switch_with_access_key.assert_called_once_with("test-uid", "access-123", desired_state=True)

@pytest.mark.asyncio
async def test_switch_extra_attributes_include_linked_entities(hass, config_entry):
	"""Test switch extra attributes include linked entities info."""
	config_entry.options = {
		"linked_entities": {
			"test-uid": ["light.living_room", "switch.bedroom"]
		}
	}

	mock_coordinator = MagicMock()
	mock_coordinator.switches = {
		"test-uid": {
			"state": False,
			"description": "Test Switch",
			"location": "Home",
		}
	}
	mock_coordinator.subscriptions = {}
	mock_coordinator.last_update_success = True

	switch = VomeSyncSwitch(
		coordinator=mock_coordinator,
		uid="test-uid",
		name="Test Switch",
		is_owner=True,
		config_entry=config_entry
	)

	attributes = switch.extra_state_attributes

	assert "linked_entities" in attributes
	assert attributes["linked_entities"] == ["light.living_room", "switch.bedroom"]
	assert attributes["linked_entities_count"] == 2


@pytest.mark.asyncio
async def test_switch_link_entities_service_call(hass, config_entry):
	"""Test link_entities service call updates options."""
	config_entry.options = {}

	mock_coordinator = MagicMock()
	mock_coordinator.async_setup_entity_links = AsyncMock()

	switch = VomeSyncSwitch(
		coordinator=mock_coordinator,
		uid="test-uid",
		name="Test Switch",
		is_owner=True,
		config_entry=config_entry
	)
	switch.hass = hass

	await switch.async_link_entities(["light.new_light", "fan.ceiling_fan"])

	# Verify options were updated
	hass.config_entries.async_update_entry.assert_called_once()
	call_args = hass.config_entries.async_update_entry.call_args
	updated_options = call_args[1]["options"]
	
	assert "linked_entities" in updated_options
	cfg = updated_options["linked_entities"]["test-uid"]
	assert cfg["entities"] == ["light.new_light", "fan.ceiling_fan"]
	assert cfg["mode"] == "master"
	assert cfg["master"] == "light.new_light"
	assert cfg["direction"] == "both"
	
	# Verify coordinator was notified
	mock_coordinator.async_setup_entity_links.assert_called_once()


@pytest.mark.asyncio
async def test_switch_displays_owner_vs_subscribed_icon(hass, config_entry):
	"""Test switch icon differs for owned vs subscribed."""
	mock_coordinator = MagicMock()
	mock_coordinator.switches = {"uid-1": {"state": True}}
	mock_coordinator.subscriptions = {"uid-2": {"state": False}}
	mock_coordinator.last_update_success = True

	# Owned switch
	owned_switch = VomeSyncSwitch(
		coordinator=mock_coordinator,
		uid="uid-1",
		name="Owned",
		is_owner=True,
		config_entry=config_entry
	)

	# Subscribed switch
	subscribed_switch = VomeSyncSwitch(
		coordinator=mock_coordinator,
		uid="uid-2",
		name="Subscribed",
		is_owner=False,
		config_entry=config_entry
	)

	# Icons should be different for owner vs subscriber
	assert owned_switch.icon != subscribed_switch.icon

