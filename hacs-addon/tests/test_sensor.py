"""Tests for VomeSync sensor platform."""
import pytest
from unittest.mock import MagicMock

from custom_components.vomesync.const import DOMAIN
from custom_components.vomesync.sensor import async_setup_entry


@pytest.mark.asyncio
async def test_sensor_created_from_imported_cache_for_subscriptions(hass, config_entry):
	"""Sensors should be created for imported switches where is_owner is False."""
	config_entry.options = {
		"imported_switches": {
			"uid-sub": {"name": "Remote One", "is_owner": False, "cached_data": {"state": False}},
			"uid-own": {"name": "Mine", "is_owner": True, "cached_data": {"state": True}},
		}
	}

	mock_coordinator = MagicMock()
	mock_coordinator.get_switch_data = MagicMock(return_value={"state": False})
	mock_coordinator.last_update_success = True

	hass.data = {DOMAIN: {config_entry.entry_id: mock_coordinator}}

	added = []

	def _add_entities(entities):
		added.extend(entities)

	await async_setup_entry(hass, config_entry, _add_entities)

	# Only one sensor should be created (for uid-sub)
	assert len(added) == 1
	assert added[0].unique_id == "vomesync_sensor_uid-sub"

