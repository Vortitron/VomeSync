# flake8: noqa
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

	# One switch sensor (for uid-sub), plus the health score, which exists
	# for every entry whether or not anything is imported or even linked.
	switch_sensors = [e for e in added if e.unique_id.startswith("vomesync_sensor_")]
	assert len(switch_sensors) == 1
	assert switch_sensors[0].unique_id == "vomesync_sensor_uid-sub"
	assert [e.unique_id for e in added].count(
		f"vomesync_health_score_{config_entry.entry_id}") == 1


@pytest.mark.asyncio
async def test_sensor_skips_subscription_with_access_key(hass, config_entry):
	"""Sensors should not be created when access key enables toggle."""
	config_entry.options = {
		"imported_switches": {
			"uid-sub": {"name": "Remote One", "is_owner": False, "access_key": "access-123", "cached_data": {"state": False}},
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

	assert [e for e in added if e.unique_id.startswith("vomesync_sensor_")] == []
	# The health score is not a switch sensor and is always there.
	assert len(added) == 1

