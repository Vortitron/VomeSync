"""Keeping HACS's check of custom repositories alive across restarts.

HACS 2.x checks the repositories a user added by URL (custom repositories)
for new releases on a 48-hour ``async_track_time_interval``, and never at
startup.  The timer lives only in memory, so every restart starts the 48 hours
again.  A home that restarts more often than that -- Core updates, add-on
updates, a CHAP switch and its switch-back -- never runs the check at all, and
its owner simply stops being offered updates for those integrations.
GamlaBio went about two weeks without seeing a release of its own projects
this way (1 October 2026).  Repositories from HACS's default list are not
affected: their data is refreshed at startup and every 6 hours.

This keeps the cadence HACS intended by remembering, in our own store, when
the check last ran, and running HACS's own job once that is more than 48 hours
ago.  The job only queues the work; HACS's queue then runs it within its
GitHub rate-limit budget, as it would on its own timer.  A home that does stay
up for 48 hours can get one extra pass, because HACS's own timer cannot be
seen from here; that costs one GitHub call per custom repository.

This reaches into HACS's internals, which can change.  A missing HACS, a
HACS without the job (1.x refreshed every 2 hours and never had the problem)
or one that raises leaves the home exactly as HACS alone would.  Nothing is
recorded unless the job ran, so a failed attempt is retried on the next tick.
"""
from __future__ import annotations

import logging
import time
from datetime import timedelta
from typing import Any, Callable, Optional

from homeassistant.core import HomeAssistant, callback

_LOGGER = logging.getLogger(__name__)

HACS_DOMAIN = "hacs"
# The job HACS 2.x schedules every 48 hours (hacs/base.py, startup_tasks).
HACS_JOB = "async_update_downloaded_custom_repositories"
HACS_STAGE_RUNNING = "running"

CHECK_EVERY = timedelta(hours=48)
# Let HACS finish its own startup (loading repositories, its first data
# fetch) before asking anything of it.
FIRST_LOOK_AFTER = timedelta(minutes=10)
LOOK_EVERY = timedelta(hours=1)

STORE_KEY = "vomesync.hacs_refresh"
STORE_VERSION = 1


def _hacs_ready(hacs: Any) -> bool:
	"""Whether HACS is set up, running and able to talk to GitHub."""
	if not callable(getattr(hacs, HACS_JOB, None)):
		return False
	stage = getattr(hacs, "stage", None)
	if getattr(stage, "value", stage) != HACS_STAGE_RUNNING:
		return False
	# Disabled covers a rate limit or a bad token; the job would return
	# without doing anything, and recording it as run would skip 48 hours.
	return not getattr(getattr(hacs, "system", None), "disabled", False)


class HacsRefresher:
	"""Runs HACS's custom-repository check when it is overdue."""

	def __init__(
		self,
		hass: HomeAssistant,
		store: Any,
		now: Callable[[], float] = time.time,
	) -> None:
		self._hass = hass
		self._store = store
		self._now = now
		self._busy = False
		self._unsubs: list[Callable[[], None]] = []

	async def async_check(self) -> bool:
		"""Run the check if it is due. Returns whether it ran."""
		if self._busy:
			return False
		self._busy = True
		try:
			return await self._async_check()
		except Exception as err:  # noqa: BLE001 - never let HACS's trouble be ours
			_LOGGER.debug("HACS custom-repository check not run: %s", err)
			return False
		finally:
			self._busy = False

	async def _async_check(self) -> bool:
		hacs = self._hass.data.get(HACS_DOMAIN)
		if hacs is None or not _hacs_ready(hacs):
			return False
		stored = await self._store.async_load() or {}
		last_run: Optional[float] = stored.get("last_run")
		now = self._now()
		if last_run is not None and now - last_run < CHECK_EVERY.total_seconds():
			return False
		await getattr(hacs, HACS_JOB)()
		await self._store.async_save({"last_run": now})
		_LOGGER.info(
			"Asked HACS to check custom repositories for updates "
			"(its own 48-hour check is reset by every restart)"
		)
		return True

	def start(self) -> None:
		"""Look shortly after startup, then hourly."""
		from homeassistant.helpers.event import (
			async_call_later,
			async_track_time_interval,
		)

		@callback
		def _tick(_now=None) -> None:
			self._hass.async_create_task(self.async_check())

		self._unsubs.append(
			async_call_later(self._hass, FIRST_LOOK_AFTER.total_seconds(), _tick)
		)
		self._unsubs.append(async_track_time_interval(self._hass, _tick, LOOK_EVERY))

	def stop(self) -> None:
		for unsub in self._unsubs:
			unsub()
		self._unsubs.clear()


def async_start(hass: HomeAssistant, data: dict) -> None:
	"""Start the refresher once per Home Assistant. Idempotent."""
	if data.get("_hacs_refresh") is not None:
		return
	from homeassistant.helpers.storage import Store

	refresher = HacsRefresher(hass, Store(hass, STORE_VERSION, STORE_KEY))
	refresher.start()
	data["_hacs_refresh"] = refresher
