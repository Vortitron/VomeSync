# flake8: noqa
"""The whole entry on a real Core: the Remote access sensor, the Repairs
issue and Download diagnostics, as Home Assistant itself wires them.

Nothing dials out: the relay's loop, the switch API and the portal
calls are stubbed, so what is checked is the integration's plumbing —
that the binary_sensor and diagnostics platforms load for the entry, that
the sensor follows the relay's state, and that the secret never leaves.
"""
import asyncio
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.diagnostics import (
	get_diagnostics_for_config_entry,
)

from custom_components.vomesync import relay_health as rh
from custom_components.vomesync.const import (
	CONF_RELAY,
	CONF_RELAY_SECRET,
	CONF_RELAY_SERVER_ID,
	DOMAIN,
)
from custom_components.vomesync.relay_client import RelayClient, get_relay_client

SECRET = "relay-secret-never-shown"


async def _never_connects(self):
	await asyncio.Event().wait()


@pytest.fixture(autouse=True)
def _custom_integrations(enable_custom_integrations):
	yield


@pytest.fixture
def entry(hass):
	entry = MockConfigEntry(
		domain=DOMAIN,
		title="Vome",
		data={"personal_key": "pk-never-shown", "server_url": "https://sync.vome.test",
			"websocket_url": "wss://sync.vome.test"},
		options={CONF_RELAY: {CONF_RELAY_SERVER_ID: "srv-1", CONF_RELAY_SECRET: SECRET}},
	)
	entry.add_to_hass(hass)
	return entry


async def _noop(*args, **kwargs):
	return None


@contextmanager
def _stubbed():
	"""Everything that would otherwise leave the test machine."""
	with patch.object(RelayClient, "_run", _never_connects), \
		patch("custom_components.vomesync.relay_client.async_ensure_local_access_token", _noop), \
		patch("custom_components.vomesync.coordinator.VomeSyncCoordinator.async_config_entry_first_refresh", _noop), \
		patch("custom_components.vomesync.coordinator.VomeSyncCoordinator.async_setup_entity_links", _noop), \
		patch("custom_components.vomesync.coordinator.VomeSyncCoordinator.async_shutdown", _noop), \
		patch("custom_components.vomesync.ice_servers.async_start_ice_servers", _noop), \
		patch("custom_components.vomesync.ice_servers.async_stop_ice_servers", _noop), \
		patch("custom_components.vomesync.e2e_remote.async_start_e2e", _noop), \
		patch("custom_components.vomesync.e2e_remote.async_stop_e2e", _noop), \
		patch("custom_components.vomesync.health_score.async_restore_report", _noop), \
		patch("custom_components.vomesync.hacs_refresh.async_start", lambda *a, **k: None):
		yield


@pytest.fixture
async def loaded(hass, entry):
	with _stubbed():
		assert await hass.config_entries.async_setup(entry.entry_id)
		await hass.async_block_till_done()
		yield entry
		assert await hass.config_entries.async_unload(entry.entry_id)
		await hass.async_block_till_done()


async def test_remote_access_sensor_follows_the_relay(hass, loaded):
	state = hass.states.get("binary_sensor.vome_remote_access")
	assert state is not None, [s.entity_id for s in hass.states.async_all()]
	assert state.state == "off"
	assert state.attributes["device_class"] == "connectivity"
	assert state.attributes["server_id"] == "srv-1"
	assert state.attributes["relay"] == "sync.vome.io"

	client = get_relay_client(hass, loaded.entry_id)
	client._ws = MagicMock(closed=False)
	client._note_state(True)
	await hass.async_block_till_done()
	state = hass.states.get("binary_sensor.vome_remote_access")
	assert state.state == "on"
	assert state.attributes["connected_since"]

	client._ws = None
	client._note_state(False)
	await hass.async_block_till_done()
	assert hass.states.get("binary_sensor.vome_remote_access").state == "off"


async def test_repairs_issue_raised_and_cleared(hass, loaded):
	registry = ir.async_get(hass)
	issue_id = rh.issue_id(loaded.entry_id)
	assert registry.async_get_issue(DOMAIN, issue_id) is None

	# The grace period runs out while still down.
	rh._raise_if_still_down(hass, loaded.entry_id)
	issue = registry.async_get_issue(DOMAIN, issue_id)
	assert issue is not None
	assert issue.translation_key == rh.ISSUE_RELAY_DOWN
	assert issue.translation_placeholders == {"server_id": "srv-1", "minutes": "10"}
	assert issue.is_fixable is False

	# Back: cleared.
	client = get_relay_client(hass, loaded.entry_id)
	client._ws = MagicMock(closed=False)
	client._note_state(True)
	await hass.async_block_till_done()
	assert registry.async_get_issue(DOMAIN, issue_id) is None


async def test_unload_clears_a_raised_issue(hass, entry):
	registry = ir.async_get(hass)
	issue_id = rh.issue_id(entry.entry_id)
	with _stubbed():
		assert await hass.config_entries.async_setup(entry.entry_id)
		await hass.async_block_till_done()
		rh._raise_if_still_down(hass, entry.entry_id)
		assert registry.async_get_issue(DOMAIN, issue_id) is not None
		assert await hass.config_entries.async_unload(entry.entry_id)
		await hass.async_block_till_done()
	assert registry.async_get_issue(DOMAIN, issue_id) is None
	assert hass.states.get("binary_sensor.vome_remote_access").state == "unavailable"


async def test_diagnostics_download_has_no_secret(hass, hass_client, loaded):
	result = await get_diagnostics_for_config_entry(hass, hass_client, loaded)
	dumped = str(result)
	assert SECRET not in dumped
	assert "pk-never-shown" not in dumped
	assert result["relay"]["linked"] is True
	assert result["relay"]["client_running"] is True
	assert result["relay"]["connected"] is False
	assert result["entry"]["options"]["relay"]["server_id"] == "srv-1"
	assert result["switches"]["coordinator"] is True


async def test_a_refused_trial_link_says_so_not_check_your_internet(hass, entry):
	hass.config_entries.async_update_entry(entry, options={CONF_RELAY: {
		CONF_RELAY_SERVER_ID: "srv-1", CONF_RELAY_SECRET: SECRET, "agent_trial": True,
	}})
	registry = ir.async_get(hass)
	with _stubbed():
		assert await hass.config_entries.async_setup(entry.entry_id)
		await hass.async_block_till_done()
		client = get_relay_client(hass, entry.entry_id)
		client._refused("refused the link (HTTP 401)")
		await hass.async_block_till_done()

		issue = registry.async_get_issue(DOMAIN, rh.rejected_issue_id(entry.entry_id))
		assert issue is not None
		assert issue.translation_key == rh.REJECTED_TRIAL
		assert issue.severity == ir.IssueSeverity.ERROR
		assert registry.async_get_issue(DOMAIN, rh.issue_id(entry.entry_id)) is None
		state = hass.states.get("binary_sensor.vome_remote_access")
		assert state.state == "off" and state.attributes["rejected"] is True

		# The outage clock must not raise its own issue on top.
		rh._raise_if_still_down(hass, entry.entry_id)
		assert registry.async_get_issue(DOMAIN, rh.issue_id(entry.entry_id)) is None

		client._ws = MagicMock(closed=False)
		client._note_state(True)
		await hass.async_block_till_done()
		assert registry.async_get_issue(DOMAIN, rh.rejected_issue_id(entry.entry_id)) is None
		assert hass.states.get("binary_sensor.vome_remote_access").attributes["rejected"] is False
		assert await hass.config_entries.async_unload(entry.entry_id)
		await hass.async_block_till_done()


async def test_a_trial_near_its_end_warns_and_the_warning_goes_when_kept(hass, entry):
	import time
	from homeassistant.util import dt as dt_util
	from pytest_homeassistant_custom_component.common import async_fire_time_changed
	from custom_components.vomesync import trial_clock as tc

	hass.config_entries.async_update_entry(entry, options={CONF_RELAY: {
		CONF_RELAY_SERVER_ID: "srv-1", CONF_RELAY_SECRET: SECRET,
		"agent_trial": True, "agent_trial_expires_at": int(time.time()) + 3 * 3600,
	}})
	registry = ir.async_get(hass)
	with _stubbed():
		assert await hass.config_entries.async_setup(entry.entry_id)
		await hass.async_block_till_done()
		issue = registry.async_get_issue(DOMAIN, tc.issue_id(entry.entry_id))
		assert issue is not None
		assert issue.translation_key == tc.ENDING_TRIAL
		assert issue.translation_placeholders == {"left": "3 hours"}

		# Linked to an account: the trial flag goes, and the next check
		# withdraws the warning.
		hass.config_entries.async_update_entry(entry, options={CONF_RELAY: {
			CONF_RELAY_SERVER_ID: "srv-1", CONF_RELAY_SECRET: SECRET,
		}})
		async_fire_time_changed(hass, dt_util.utcnow() + tc.CHECK_EVERY)
		await hass.async_block_till_done()
		assert registry.async_get_issue(DOMAIN, tc.issue_id(entry.entry_id)) is None

		assert await hass.config_entries.async_unload(entry.entry_id)
		await hass.async_block_till_done()
	assert entry.entry_id not in hass.data.get(DOMAIN, {}).get(tc._KEY, {})
