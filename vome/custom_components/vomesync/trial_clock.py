"""A warning in Repairs before a temporary Vome link runs out.

Two kinds of link are on a clock: a coding agent key opened without an
account (two days), and a login-free health-score run (one day).  Both
say so when they start.  Neither said anything again, so the first sign
most people got was the key failing in the middle of an agent session,
or the remote address going dark: the worst moment to learn that keeping
it was one click.

So a quiet check every few minutes: inside the last stretch of the clock
a Repairs warning says how long is left and how to keep the link; once
the link is kept, revoked or gone, the warning is withdrawn.  After the
end it is withdrawn too: what happened then is relay_health's
refused-link issue, which says the link has ended rather than that it is
about to.

The claim URL of a guest run carries a one-time code, so it is *not* put
in the issue; the issue points at the panel, which shows it.
"""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Optional

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

# How long before the end the warning appears.
TRIAL_WARN_SECONDS = 12 * 3600
GUEST_WARN_SECONDS = 4 * 3600
CHECK_EVERY = timedelta(minutes=5)

ISSUE_ENDING = "link_ending"
ENDING_TRIAL = "agent_trial_ending"
ENDING_GUEST = "guest_link_ending"

_KEY = "_trial_clock"


def issue_id(entry_id: str) -> str:
	return f"{ISSUE_ENDING}_{entry_id}"


def left_phrase(seconds: int) -> str:
	"""Time left in words: "11 hours", "an hour", "25 minutes"."""
	seconds = max(0, int(seconds))
	if seconds >= 90 * 60:
		hours = round(seconds / 3600)
		return "an hour" if hours == 1 else f"{hours} hours"
	if seconds >= 50 * 60:
		return "an hour"
	minutes = max(1, seconds // 60)
	return "a minute" if minutes == 1 else f"{minutes} minutes"


def ending(entry: ConfigEntry, *, now: Optional[float] = None) -> Optional[tuple[str, int]]:
	"""``(translation_key, seconds_left)`` when a warning is due, else None."""
	from . import agent_key, health_score

	if agent_key.is_trial(entry):
		left = agent_key.trial_seconds_left(entry, now=now)
		if 0 < left <= TRIAL_WARN_SECONDS:
			return ENDING_TRIAL, left
		return None
	if health_score.is_guest(entry):
		left = health_score.guest_seconds_left(entry, now=now)
		if 0 < left <= GUEST_WARN_SECONDS:
			return ENDING_GUEST, left
	return None


def evaluate(hass: HomeAssistant, entry: ConfigEntry, *, now: Optional[float] = None) -> Optional[str]:
	"""Raise, refresh or withdraw the warning.  Returns the key raised, if any."""
	from homeassistant.helpers import issue_registry as ir

	due = ending(entry, now=now)
	try:
		if due is None:
			ir.async_delete_issue(hass, DOMAIN, issue_id(entry.entry_id))
			return None
		key, left = due
		# Raised again on every check: the same issue, with the time left
		# brought up to date.
		ir.async_create_issue(
			hass,
			DOMAIN,
			issue_id(entry.entry_id),
			is_fixable=False,
			severity=ir.IssueSeverity.WARNING,
			translation_key=key,
			translation_placeholders={"left": left_phrase(left)},
		)
		return key
	except Exception as err:  # noqa: BLE001 - a warning must never break setup
		_LOGGER.debug("Repairs: link-ending check failed: %s", err)
		return None


def async_start(hass: HomeAssistant, entry: ConfigEntry) -> None:
	"""Check now and every few minutes until the entry unloads."""
	from homeassistant.helpers.event import async_track_time_interval

	async_stop(hass, entry)

	# @callback, or Home Assistant runs it in a worker thread, and the
	# issue registry may only be touched from the event loop.
	@callback
	def _tick(_now=None) -> None:
		current = hass.config_entries.async_get_entry(entry.entry_id) or entry
		evaluate(hass, current)

	_tick()
	hass.data.setdefault(DOMAIN, {}).setdefault(_KEY, {})[entry.entry_id] = (
		async_track_time_interval(hass, _tick, CHECK_EVERY)
	)


def async_stop(hass: HomeAssistant, entry: ConfigEntry) -> None:
	unsub = hass.data.get(DOMAIN, {}).get(_KEY, {}).pop(entry.entry_id, None)
	if unsub is not None:
		unsub()
