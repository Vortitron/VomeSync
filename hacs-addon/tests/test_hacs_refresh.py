# flake8: noqa
"""Tests for hacs_refresh — HACS's custom-repository check, kept alive.

The behaviour that matters: a home that restarts more often than every 48
hours still gets the check every 48 hours, and anything wrong with HACS (not
installed, an older version, still starting, rate-limited, raising) leaves the
home as HACS alone would and is retried rather than recorded as done.
"""
import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from custom_components.vomesync import hacs_refresh

HOUR = 3600


def run(coro):
	loop = asyncio.new_event_loop()
	try:
		return loop.run_until_complete(coro)
	finally:
		loop.close()


class _Store:
	"""In-memory stand-in for homeassistant.helpers.storage.Store.

	Shared between refresher instances, the way .storage outlives a restart.
	"""

	def __init__(self, data=None):
		self.data = data

	async def async_load(self):
		return self.data

	async def async_save(self, data):
		self.data = data


class _Hacs:
	def __init__(self, stage="running", disabled=False, fails=False):
		self.stage = SimpleNamespace(value=stage)
		self.system = SimpleNamespace(disabled=disabled)
		self.fails = fails
		self.runs = 0

	async def async_update_downloaded_custom_repositories(self, _=None):
		if self.fails:
			raise RuntimeError("GitHub said no")
		self.runs += 1


class _Clock:
	def __init__(self, t=1_000_000.0):
		self.t = t

	def __call__(self):
		return self.t


def _refresher(hacs, store, clock):
	hass = MagicMock()
	hass.data = {} if hacs is None else {"hacs": hacs}
	return hacs_refresh.HacsRefresher(hass, store, now=clock)


def test_runs_when_never_run_before():
	hacs, store, clock = _Hacs(), _Store(), _Clock()
	assert run(_refresher(hacs, store, clock).async_check()) is True
	assert hacs.runs == 1
	assert store.data == {"last_run": clock.t}


def test_frequent_restarts_still_get_a_check_every_48_hours():
	"""The GamlaBio case: HA restarting daily used to mean no check, ever."""
	hacs, store, clock = _Hacs(), _Store(), _Clock()
	assert run(_refresher(hacs, store, clock).async_check()) is True

	# A restart every 20 hours: each boot is a fresh refresher, as after a
	# real restart, but the store survives.
	for _ in range(2):
		clock.t += 20 * HOUR
		assert run(_refresher(hacs, store, clock).async_check()) is False
	assert hacs.runs == 1

	clock.t += 9 * HOUR  # 49 hours since the last check
	assert run(_refresher(hacs, store, clock).async_check()) is True
	assert hacs.runs == 2


def test_hourly_looks_within_48_hours_do_nothing():
	hacs, store, clock = _Hacs(), _Store(), _Clock()
	refresher = _refresher(hacs, store, clock)
	run(refresher.async_check())
	for _ in range(47):
		clock.t += HOUR
		assert run(refresher.async_check()) is False
	assert hacs.runs == 1


@pytest.mark.parametrize(
	"hacs",
	[
		None,  # HACS not installed
		SimpleNamespace(stage="running"),  # HACS 1.x: no such job
		_Hacs(stage="startup"),  # still loading its repositories
		_Hacs(disabled=True),  # rate-limited or a bad token
	],
	ids=["no-hacs", "hacs-1x", "starting", "disabled"],
)
def test_hacs_not_ready_is_left_alone_and_retried(hacs):
	store, clock = _Store({"last_run": 0}), _Clock()
	assert run(_refresher(hacs, store, clock).async_check()) is False
	assert store.data == {"last_run": 0}


def test_a_failing_job_is_not_recorded_and_does_not_raise():
	hacs, store, clock = _Hacs(fails=True), _Store(), _Clock()
	assert run(_refresher(hacs, store, clock).async_check()) is False
	assert store.data is None

	hacs.fails = False
	clock.t += HOUR
	assert run(_refresher(hacs, store, clock).async_check()) is True


def test_plain_string_stage_is_understood():
	"""HacsStage is a StrEnum; a plain string must count the same."""
	hacs = _Hacs()
	hacs.stage = "running"
	assert run(_refresher(hacs, _Store(), _Clock()).async_check()) is True


def test_start_is_idempotent(monkeypatch):
	started = []
	monkeypatch.setattr(
		hacs_refresh.HacsRefresher, "start", lambda self: started.append(self)
	)
	data = {}
	hass = MagicMock()
	hacs_refresh.async_start(hass, data)
	hacs_refresh.async_start(hass, data)
	assert len(started) == 1
	assert data["_hacs_refresh"] is started[0]
