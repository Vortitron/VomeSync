# flake8: noqa
"""A subscription to a switch the server has forgotten must stop being polled.

The server keeps switch state in Redis under a TTL. Once it lapses, nothing
recreates it, so ``GET /api/status/<uid>`` 404s for good. The subscription
lives on in the config entry regardless, and the coordinator polled it every
30 seconds forever -- 2 880 requests a day that could only ever fail. One
stale subscription was producing a fifth of all 404s reaching the server.

The warning was throttled to one in ten minutes, which hid the volume without
reducing it.
"""
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from custom_components.vomesync.coordinator import (  # noqa: E402
	VomeSyncCoordinator,
	_SUB_BACKOFF_MAX_SECONDS,
	_SUB_MISSES_BEFORE_BACKOFF,
)
from custom_components.vomesync.const import UPDATE_INTERVAL_SECONDS  # noqa: E402

DEAD_UID = "vs_twrx6z13hhm4x6rhy9pdsshax8"


def _coordinator(status_response=None):
	"""A coordinator wired up just far enough to run one update cycle.

	``__init__`` needs a real Home Assistant, an API client and a websocket
	client; this test is about one loop inside ``_async_update_data``, so the
	instance is built directly and given only what that loop reads.
	"""
	c = object.__new__(VomeSyncCoordinator)
	c.api_client = MagicMock()
	c.api_client.get_my_switches = AsyncMock(return_value=[])
	c.api_client.get_switch_status = AsyncMock(return_value=status_response)
	c.config_entry = MagicMock()
	c.config_entry.options = {
		"imported_switches": {
			DEAD_UID: {"name": "Hall lamp", "is_owner": False},
		}
	}
	c.hass = MagicMock()
	c.hass.loop.time.return_value = 0.0
	c.entity_names = {}
	c.switches = {}
	c.subscriptions = {}
	c._websocket_connections = {}
	c._ensure_websocket_connection = AsyncMock()
	c._async_update_entry_options = AsyncMock()
	c._warning_throttle = {}
	c._last_owned_switch_count = None
	c._sub_misses = {}
	c._sub_next_poll = {}
	return c


@pytest.mark.asyncio
async def test_a_missing_subscription_is_polled_at_full_rate_at_first():
	"""Backoff must not kick in on a blip: a switch can come back."""
	c = _coordinator(status_response=None)
	for _ in range(_SUB_MISSES_BEFORE_BACKOFF - 1):
		await c._async_update_data()
	assert c.api_client.get_switch_status.await_count == _SUB_MISSES_BEFORE_BACKOFF - 1
	assert c._sub_next_poll == {}, 'a couple of misses is not yet a dead switch'


@pytest.mark.asyncio
async def test_repeated_misses_stop_the_polling():
	"""The behaviour that was missing: eventually stop asking every cycle."""
	c = _coordinator(status_response=None)
	for _ in range(_SUB_MISSES_BEFORE_BACKOFF):
		await c._async_update_data()
	polled = c.api_client.get_switch_status.await_count

	# Several more cycles pass; none of them should reach the network.
	for _ in range(20):
		await c._async_update_data()
	assert c.api_client.get_switch_status.await_count == polled, \
		'a subscription in backoff was polled again before its next-poll time'


@pytest.mark.asyncio
async def test_the_backoff_is_capped():
	"""Uncapped doubling would silently become "never check again"."""
	c = _coordinator(status_response=None)
	c._sub_misses[DEAD_UID] = 40  # far past any sane doubling
	# _sub_next_poll holds an absolute monotonic deadline, so the wait has to
	# be measured against a reading taken here rather than compared directly.
	before = time.monotonic()
	await c._async_update_data()
	wait = c._sub_next_poll[DEAD_UID] - before
	assert wait <= _SUB_BACKOFF_MAX_SECONDS + 1, 'doubling was not capped'
	assert wait >= _SUB_BACKOFF_MAX_SECONDS - 1, 'expected the wait to reach the cap'


@pytest.mark.asyncio
async def test_a_switch_that_comes_back_is_polled_normally_again():
	"""Backoff must be recoverable — a server outage is not a dead switch."""
	c = _coordinator(status_response=None)
	for _ in range(_SUB_MISSES_BEFORE_BACKOFF):
		await c._async_update_data()
	assert c._sub_next_poll, 'expected the subscription to be in backoff'

	# The switch returns. Clear the wait as the elapsed time would.
	c._sub_next_poll[DEAD_UID] = 0.0
	c.api_client.get_switch_status = AsyncMock(return_value={"uid": DEAD_UID, "state": "on"})
	result = await c._async_update_data()

	assert c._sub_misses == {}, 'a successful poll must clear the miss count'
	assert c._sub_next_poll == {}, 'a successful poll must clear the backoff'
	assert DEAD_UID in result["subscriptions"]


@pytest.mark.asyncio
async def test_a_switch_in_backoff_is_not_reported_as_present():
	"""Skipping the poll must not resurrect a stale state.

	Holding the last known reading would show the switch as on or off when it
	no longer exists at all. Absent means unavailable, which is the truth and
	is what prompts the owner to remove it.
	"""
	c = _coordinator(status_response=None)
	for _ in range(_SUB_MISSES_BEFORE_BACKOFF + 1):
		result = await c._async_update_data()
	assert DEAD_UID not in result["subscriptions"]
