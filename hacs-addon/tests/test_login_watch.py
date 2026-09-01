# flake8: noqa
"""Tests for login_watch — the home reporting its own failed logins.

The behaviour that matters: it recognises Core's line, ignores everything else
in a busy home's log, cannot break the event bus, and never queues without
limit while the relay is down.  The coupling to Core's exact wording is pinned
separately in test_ha_compat_contract.py, against the installed Home Assistant.
"""
import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from custom_components.vomesync import login_watch

FAILURE_LINE = (
	"Login attempt or request with invalid authentication from somebox.lan "
	"(10.0.0.5). Requested URL: '/auth/token'. (Mozilla/5.0)"
)


class _Bus:
	"""Minimal event bus that hands listeners straight back to the test."""

	def __init__(self):
		self.listeners = {}

	def async_listen(self, event_type, listener):
		self.listeners.setdefault(event_type, []).append(listener)
		return lambda: self.listeners[event_type].remove(listener)

	def fire(self, event_type, data):
		for listener in list(self.listeners.get(event_type, [])):
			listener(SimpleNamespace(data=data))


@pytest.fixture
def watcher():
	sent = []

	async def send(batch):
		sent.append(batch)

	hass = MagicMock()
	hass.bus = _Bus()
	# The watcher schedules its flush as a task.  Drive it to completion on a
	# loop of this test's own: borrowing whichever loop happens to be current
	# makes the result depend on what else ran earlier in the session.
	def _run(coro):
		loop = asyncio.new_event_loop()
		try:
			loop.run_until_complete(coro)
		finally:
			loop.close()

	hass.async_create_task = _run
	watch = login_watch.LoginWatcher(hass, send)
	watch.start()
	return SimpleNamespace(watch=watch, hass=hass, sent=sent)


# ── Parsing ─────────────────────────────────────────────────────────────────

def test_a_failed_login_is_recognised():
	parsed = login_watch.parse_record(login_watch.BAN_LOGGER, FAILURE_LINE)
	assert parsed["event"] == login_watch.EVENT_LOGIN_FAILED
	assert parsed["client_ip"] == "10.0.0.5"
	assert parsed["path"] == "/auth/token"
	assert parsed["user_agent"] == "Mozilla/5.0"


def test_a_line_from_another_logger_is_not_ours():
	"""A busy home logs plenty of warnings; none of them is an access event."""
	assert login_watch.parse_record("homeassistant.components.zwave_js", FAILURE_LINE) is None


def test_an_unrelated_ban_logger_line_is_ignored():
	assert login_watch.parse_record(login_watch.BAN_LOGGER, "Serving HTTP") is None


def test_a_message_list_is_joined():
	"""system_log delivers `message` as a list."""
	parsed = login_watch.parse_record(login_watch.BAN_LOGGER, [FAILURE_LINE])
	assert parsed["client_ip"] == "10.0.0.5"


def test_a_line_without_a_user_agent_still_parses():
	line = ("Login attempt or request with invalid authentication from "
	        "10.0.0.5 (10.0.0.5). Requested URL: '/api/'.")
	parsed = login_watch.parse_record(login_watch.BAN_LOGGER, line)
	assert parsed["client_ip"] == "10.0.0.5"
	assert parsed["path"] == "/api/"
	assert parsed["user_agent"] is None


def test_an_ipv6_address_survives():
	line = ("Login attempt or request with invalid authentication from "
	        "host (2001:db8::5). Requested URL: '/'.")
	assert login_watch.parse_record(login_watch.BAN_LOGGER, line)["client_ip"] == "2001:db8::5"


# ── The listener ────────────────────────────────────────────────────────────

def test_a_failure_is_reported_with_the_home_as_its_source(watcher):
	watcher.hass.bus.fire(login_watch.EVENT_SYSTEM_LOG, {
		"name": login_watch.BAN_LOGGER, "message": [FAILURE_LINE], "level": "WARNING",
	})
	assert watcher.sent, "nothing was reported"
	event = watcher.sent[0][0]
	# 'home' is what tells the owner this attempt never passed Vome's edge —
	# the case Vome cannot see any other way.
	assert event["source"] == "home"
	assert event["event"] == login_watch.EVENT_LOGIN_FAILED
	assert event["client_ip"] == "10.0.0.5"


def test_core_s_own_occurrence_count_is_carried(watcher):
	watcher.hass.bus.fire(login_watch.EVENT_SYSTEM_LOG, {
		"name": login_watch.BAN_LOGGER, "message": [FAILURE_LINE], "count": 12,
	})
	assert watcher.sent[0][0]["count"] == 12


def test_unrelated_log_records_are_not_reported(watcher):
	watcher.hass.bus.fire(login_watch.EVENT_SYSTEM_LOG, {
		"name": "homeassistant.components.hue", "message": ["Bridge unreachable"],
	})
	assert watcher.sent == []


def test_a_broken_send_cannot_break_the_event_bus(watcher):
	"""A reporting failure must never propagate into Core's bus."""
	async def explode(_batch):
		raise RuntimeError("relay is down")

	watcher.watch._send = explode
	watcher.hass.bus.fire(login_watch.EVENT_SYSTEM_LOG, {
		"name": login_watch.BAN_LOGGER, "message": [FAILURE_LINE],
	})  # must not raise


def test_a_malformed_event_is_ignored(watcher):
	watcher.hass.bus.fire(login_watch.EVENT_SYSTEM_LOG, {})
	watcher.hass.bus.fire(login_watch.EVENT_SYSTEM_LOG, {"name": None, "message": None})
	assert watcher.sent == []


def test_the_queue_is_bounded_while_the_relay_is_down():
	"""An attack is exactly when this matters, and when it must not grow."""
	async def never(_batch):
		raise RuntimeError('down')

	hass = MagicMock()
	hass.bus = _Bus()
	hass.async_create_task = lambda coro: coro.close()  # never actually flush
	watch = login_watch.LoginWatcher(hass, never)
	watch.start()
	for _ in range(login_watch.MAX_QUEUED + 50):
		hass.bus.fire(login_watch.EVENT_SYSTEM_LOG, {
			"name": login_watch.BAN_LOGGER, "message": [FAILURE_LINE],
		})
	assert len(watch._queue) == login_watch.MAX_QUEUED


def test_stopping_unsubscribes(watcher):
	watcher.watch.stop()
	watcher.hass.bus.fire(login_watch.EVENT_SYSTEM_LOG, {
		"name": login_watch.BAN_LOGGER, "message": [FAILURE_LINE],
	})
	assert watcher.sent == []
