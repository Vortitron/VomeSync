"""The Home Assistant health score, run and read from inside the house.

Vome's health check reads an instance and writes up what it finds: noisy
sensors, a swelling database, dead entities, backups that stopped
happening.  Until now you had to go to vome.io, sign up, link this Home
Assistant and *then* find out.  The order was backwards, and every step
was a reason to stop.

So the button is here instead, and it works before there is an account:

* **Not linked** — :func:`async_run_check` asks Vome for a guest run
  (``POST /api/v1/relay/guest``).  That returns relay credentials and a
  ``claim_url``, so the tunnel comes up, the check runs, and the owner
  gets one link to open.  The whole thing deletes itself at Vome's end
  in two hours unless they sign in from that link.
* **Linked** — the same button just asks for a check on the account
  that already owns this instance.

Either way the finished report is pulled back here
(``GET /api/sync/agent/health-report``) and published as a sensor with
the findings on it, because a report about *this* system belongs on it.
That is also what makes the guest deal fair: when Vome deletes its copy,
the house still has one.

The guest link is deliberately marked as such in options
(``CONF_RELAY_GUEST``).  It is a real relay link — same shape, same
client — but it is on a clock, and this integration must never present a
temporary link as a finished one.  When Vome reports the run has been
claimed, the flag comes off and it becomes an ordinary link with no
reconnection needed.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Optional

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.components import persistent_notification
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .const import (
	CONF_RELAY,
	CONF_RELAY_GUEST,
	CONF_RELAY_GUEST_CLAIM_URL,
	CONF_RELAY_GUEST_EXPIRES,
	CONF_RELAY_SECRET,
	CONF_RELAY_SERVER_ID,
	CONF_RELAY_WS_URL,
	DEFAULT_PORTAL_URL,
	DOMAIN,
	relay_ws_url_for_portal,
)
from .relay_client import (
	async_fetch_health_report,
	async_instance_id,
	async_request_guest_run,
	async_start_health_check,
	async_start_relay,
)

_LOGGER = logging.getLogger(__name__)

# hass.data[DOMAIN][DATA_HEALTH][entry_id] -> the last report we were given.
DATA_HEALTH = "health_score"

# Told to the sensor when a report lands, so it repaints without polling.
SIGNAL_HEALTH_UPDATED = f"{DOMAIN}_health_score_updated"

NOTIFICATION_ID = f"{DOMAIN}_health_score"

# How long to wait for a queued check before giving up on watching it.  The
# check itself takes a minute or two; this is the ceiling, not the target.
POLL_INTERVAL_SECONDS = 15
POLL_TIMEOUT_SECONDS = 600


def _relay(entry: ConfigEntry) -> dict:
	relay = (entry.options or {}).get(CONF_RELAY)
	return dict(relay) if isinstance(relay, dict) else {}


def _portal_url(entry: ConfigEntry) -> str:
	data = {**(entry.data or {}), **(entry.options or {})}
	return str(data.get("portal_url") or DEFAULT_PORTAL_URL).rstrip("/")


def is_linked(entry: ConfigEntry) -> bool:
	"""Whether this entry has relay credentials at all (guest included)."""
	relay = _relay(entry)
	return bool(relay.get(CONF_RELAY_SERVER_ID) and relay.get(CONF_RELAY_SECRET))


def is_guest(entry: ConfigEntry) -> bool:
	"""Whether the only link we have is a temporary, login-free one."""
	return bool(_relay(entry).get(CONF_RELAY_GUEST))


def guest_seconds_left(entry: ConfigEntry, *, now: Optional[float] = None) -> int:
	expires = _relay(entry).get(CONF_RELAY_GUEST_EXPIRES)
	if not expires:
		return 0
	now = time.time() if now is None else now
	return max(0, int(expires) - int(now))


def claim_url(entry: ConfigEntry) -> str:
	return str(_relay(entry).get(CONF_RELAY_GUEST_CLAIM_URL) or "")


def stored_report(hass: HomeAssistant, entry_id: str) -> Optional[dict]:
	"""The last report we pulled back, or None."""
	return (hass.data.get(DOMAIN, {}).get(DATA_HEALTH) or {}).get(entry_id)


@callback
def _store_report(hass: HomeAssistant, entry_id: str, report: Optional[dict]) -> None:
	bucket = hass.data.setdefault(DOMAIN, {}).setdefault(DATA_HEALTH, {})
	if report is None:
		bucket.pop(entry_id, None)
	else:
		bucket[entry_id] = report
	async_dispatcher_send(hass, SIGNAL_HEALTH_UPDATED, entry_id)


async def _save_relay(hass: HomeAssistant, entry: ConfigEntry, relay: dict) -> None:
	options = dict(entry.options or {})
	options[CONF_RELAY] = relay
	hass.config_entries.async_update_entry(entry, options=options)


async def _open_guest_run(hass: HomeAssistant, entry: ConfigEntry, use_ai: bool) -> dict:
	"""Get a temporary link and a queued check from Vome, and connect it."""
	session = async_get_clientsession(hass)
	portal_url = _portal_url(entry)
	opened = await async_request_guest_run(
		session, portal_url, name=hass.config.location_name or "", use_ai=use_ai,
		# Recorded now so that claiming this run later can find the
		# account's own row for this house rather than adding another.
		instance_id=await async_instance_id(hass),
	)
	relay = _relay(entry)
	relay.update({
		CONF_RELAY_SERVER_ID: opened.get("server_id"),
		CONF_RELAY_SECRET: opened.get("relay_secret"),
		CONF_RELAY_WS_URL: relay_ws_url_for_portal(
			portal_url, opened.get("relay_ws_url"),
		),
		CONF_RELAY_GUEST: True,
		CONF_RELAY_GUEST_EXPIRES: opened.get("expires_at"),
		CONF_RELAY_GUEST_CLAIM_URL: opened.get("claim_url"),
	})
	await _save_relay(hass, entry, relay)
	# The check Vome queued needs the tunnel up to read anything.
	await async_start_relay(hass, entry)
	_LOGGER.info(
		"Vome health score: guest run opened (server %s), expires at %s",
		opened.get("server_id"), opened.get("expires_at"),
	)
	return opened


async def async_run_check(
	hass: HomeAssistant, entry: ConfigEntry, *, use_ai: bool = True,
) -> dict:
	"""Start a health check, linking this Home Assistant first if needed.

	Returns ``{'status', 'guest', 'claim_url', 'expires_at'}``.  Raises on
	a failure worth showing: the caller is a service the user pressed, so
	silence would be the wrong answer.
	"""
	if not is_linked(entry):
		opened = await _open_guest_run(hass, entry, use_ai)
		await _notify_guest_run(hass, entry, opened.get("claim_url") or "")
		return {
			"status": "queued",
			"guest": True,
			"claim_url": opened.get("claim_url"),
			"expires_at": opened.get("expires_at"),
		}

	session = async_get_clientsession(hass)
	relay = _relay(entry)
	result = await async_start_health_check(
		session, _portal_url(entry), relay.get(CONF_RELAY_SECRET), use_ai=use_ai,
	)
	return {
		"status": result.get("status") or "queued",
		"guest": is_guest(entry),
		"claim_url": claim_url(entry),
		"expires_at": relay.get(CONF_RELAY_GUEST_EXPIRES),
	}


async def async_refresh_report(
	hass: HomeAssistant, entry: ConfigEntry,
) -> Optional[dict]:
	"""Pull the latest finished report back into this Home Assistant.

	Returns the report, or None while one is still running.  A guest link
	that Vome has since deleted resolves itself here rather than being
	left to rot: the credentials stop working, so the flag and the link
	come off and the user is told once.
	"""
	if not is_linked(entry):
		return None
	session = async_get_clientsession(hass)
	relay = _relay(entry)
	try:
		payload = await async_fetch_health_report(
			session, _portal_url(entry), relay.get(CONF_RELAY_SECRET),
		)
	except RuntimeError as err:
		if is_guest(entry):
			await _forget_expired_guest(hass, entry)
			_LOGGER.info("Vome health score: the guest run has ended (%s)", err)
			return None
		raise

	# Vome tells us whether this house is still on a throwaway run, so the
	# "keep it" nagging stops the moment they have.
	if is_guest(entry) and payload.get("guest") is False:
		await _guest_was_claimed(hass, entry)

	report = payload.get("report")
	if not report:
		return None
	_store_report(hass, entry.entry_id, report)
	return report


async def async_watch_for_report(
	hass: HomeAssistant, entry: ConfigEntry, *, timeout: int = POLL_TIMEOUT_SECONDS,
) -> Optional[dict]:
	"""Poll until the queued check finishes, then publish it.

	Deliberately a bounded loop rather than a permanent timer: a check is
	something a person just asked for, and nothing here should keep
	talking to Vome after they have stopped waiting for it.
	"""
	import asyncio

	deadline = time.monotonic() + timeout
	while time.monotonic() < deadline:
		await asyncio.sleep(POLL_INTERVAL_SECONDS)
		try:
			report = await async_refresh_report(hass, entry)
		except Exception as err:  # noqa: BLE001 - a poll failure is not fatal
			_LOGGER.debug("Vome health score: poll failed (%s)", err)
			continue
		if report:
			await _notify_result(hass, entry, report)
			return report
	_LOGGER.info("Vome health score: gave up waiting for the check to finish")
	return None


# ── Telling the person what happened ────────────────────────────────────────

async def _notify_guest_run(hass: HomeAssistant, entry: ConfigEntry, url: str) -> None:
	"""One notification with the link, because the link is the whole flow."""
	if not url:
		return
	persistent_notification.async_create(
		hass,
		(
			f"Your health check is running. Open it here to see the score:\n\n"
			f"{url}\n\n"
			"It is not tied to an account yet — Vome deletes the check, and the "
			"link to this Home Assistant, in two hours unless you sign in from "
			"that page and keep it. The report stays here either way."
		),
		title="Vome health score",
		notification_id=NOTIFICATION_ID,
	)


async def _notify_result(hass: HomeAssistant, entry: ConfigEntry, report: dict) -> None:
	score = report.get("score")
	lines = [f"Health score: {score}/100." if score is not None else "Check finished."]
	if report.get("summary"):
		lines.append(str(report["summary"]))
	if is_guest(entry) and claim_url(entry):
		mins = guest_seconds_left(entry) // 60
		lines.append(
			f"This run is not saved to an account. Keep it (and this link to "
			f"Home Assistant) by signing in at {claim_url(entry)} — about "
			f"{mins} minutes left."
		)
	persistent_notification.async_create(
		hass, "\n\n".join(lines), title="Vome health score",
		notification_id=NOTIFICATION_ID,
	)


async def _guest_was_claimed(hass: HomeAssistant, entry: ConfigEntry) -> None:
	"""The run is on a real account now: same link, no clock."""
	relay = _relay(entry)
	for key in (CONF_RELAY_GUEST, CONF_RELAY_GUEST_EXPIRES, CONF_RELAY_GUEST_CLAIM_URL):
		relay.pop(key, None)
	await _save_relay(hass, entry, relay)
	persistent_notification.async_dismiss(hass, NOTIFICATION_ID)
	_LOGGER.info("Vome health score: this Home Assistant is now linked to an account")


async def _forget_expired_guest(hass: HomeAssistant, entry: ConfigEntry) -> None:
	"""Drop a temporary link Vome has deleted, and say so once."""
	relay = _relay(entry)
	for key in (
		CONF_RELAY_SERVER_ID, CONF_RELAY_SECRET, CONF_RELAY_WS_URL,
		CONF_RELAY_GUEST, CONF_RELAY_GUEST_EXPIRES, CONF_RELAY_GUEST_CLAIM_URL,
	):
		relay.pop(key, None)
	await _save_relay(hass, entry, relay)
	await async_start_relay(hass, entry)  # no credentials now: stops the tunnel
	persistent_notification.async_create(
		hass,
		(
			"Your Vome health check was not saved, so the temporary link to this "
			"Home Assistant has ended and Vome has deleted its copy. The last "
			"report is still here. Run another check any time — and sign in "
			"during that one to keep it."
		),
		title="Vome health score",
		notification_id=NOTIFICATION_ID,
	)
