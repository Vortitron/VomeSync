# flake8: noqa
"""The Remote access sensor: the relay's own state, nothing inferred."""
from unittest.mock import MagicMock, patch

import pytest

from custom_components.vomesync.binary_sensor import VomeRemoteAccessSensor, async_setup_entry
from custom_components.vomesync.const import DOMAIN


@pytest.mark.asyncio
async def test_one_sensor_per_entry(hass, config_entry):
	added = []
	await async_setup_entry(hass, config_entry, added.extend)
	assert [e.unique_id for e in added] == [f"vomesync_remote_access_{config_entry.entry_id}"]
	assert added[0].device_class == "connectivity"


def test_unlinked_home_is_unavailable_not_off(hass, config_entry):
	hass.data = {DOMAIN: {}}
	sensor = VomeRemoteAccessSensor(hass, config_entry)
	assert sensor.available is False
	assert sensor.is_on is False
	assert sensor.extra_state_attributes == {}


def _with_client(hass, config_entry, connected):
	client = MagicMock()
	client.connected = connected
	client.server_id = "srv-1"
	client.ws_url = "wss://sync.vome.io/ws/relay"
	client.connected_since = 1_700_000_000 if connected else None
	client.disconnected_since = None if connected else 1_700_000_000
	client.rejected = False
	hass.data = {DOMAIN: {"_relays": {config_entry.entry_id: client}}}
	return client


def test_linked_and_connected_is_on(hass, config_entry):
	_with_client(hass, config_entry, True)
	sensor = VomeRemoteAccessSensor(hass, config_entry)
	assert sensor.available is True
	assert sensor.is_on is True
	attrs = sensor.extra_state_attributes
	assert attrs["server_id"] == "srv-1"
	assert attrs["relay"] == "sync.vome.io"
	assert attrs["connected_since"].startswith("2023-11-14T22:13:20")
	assert attrs["disconnected_since"] is None


def test_linked_but_dropped_is_off(hass, config_entry):
	_with_client(hass, config_entry, False)
	sensor = VomeRemoteAccessSensor(hass, config_entry)
	assert sensor.available is True
	assert sensor.is_on is False
	assert sensor.extra_state_attributes["disconnected_since"] is not None


def test_same_device_as_the_health_score(hass, config_entry):
	from custom_components.vomesync.sensor import VomeHealthScoreSensor

	assert (VomeRemoteAccessSensor(hass, config_entry).device_info["identifiers"]
		== VomeHealthScoreSensor(hass, config_entry).device_info["identifiers"])


@pytest.mark.asyncio
async def test_writes_state_on_its_own_entry_only(hass, config_entry):
	from custom_components.vomesync.relay_client import SIGNAL_RELAY_STATE

	sensor = VomeRemoteAccessSensor(hass, config_entry)
	sensor.async_write_ha_state = MagicMock()
	sensor.async_on_remove = MagicMock()
	captured = {}

	def _connect(h, signal, target):
		captured[signal] = target
		return MagicMock()

	with patch("homeassistant.helpers.dispatcher.async_dispatcher_connect", side_effect=_connect):
		await sensor.async_added_to_hass()
	listener = captured[SIGNAL_RELAY_STATE]
	listener("someone-else", True)
	sensor.async_write_ha_state.assert_not_called()
	listener(config_entry.entry_id, True)
	sensor.async_write_ha_state.assert_called_once()
