"""An MCP key for a coding agent, issued from inside the house.

Pointing Cursor, VS Code or Claude at your own Home Assistant is the
best thing this add-on has to show a developer, and until now it was
five steps and a website away: sign in at vome.io, link this instance,
open the tokens page, tick the right box, copy a snippet.

So the button is here instead, and it works before there is an account:
tick the permissions you want, press it, paste the JSON it hands back.
Nothing is typed and nobody signs up.

**It is a trial.**  What it grants is a brokered tunnel into this house
for somebody Vome cannot identify, so it covers one Home Assistant and
it ends in two days unless its owner signs in.  Linking properly keeps
the same key working — the expiry is lifted rather than the key being
replaced — so the ``mcp.json`` already pasted into an editor does not
break at the moment somebody pays.

**Why the key is safe to paste.**  It is not a Home Assistant token: it
reaches this instance only, only through Vome's broker, and only within
the scopes ticked here, which Vome enforces server-side and logs.  The
one thing it cannot be is quietly widened — changing what it may do
means changing it here, in this house.

The link is marked as a trial in options (``CONF_RELAY_AGENT_TRIAL``),
the same way a guest health-score run is, and for the same reason: it
is a real relay link on a clock, and this integration must never show a
temporary link as a finished one.

Note what is deliberately *not* switched on: ``CONF_RELAY_FORWARD_UI``.
A guest score run turns it on because its owner is given a web address
to open; an agent key needs no such thing, and publishing this house's
login page is not a side effect anybody asked for.
"""
from __future__ import annotations

import logging
import time
from typing import Optional

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
	CONF_RELAY,
	CONF_RELAY_AGENT_EXPIRES,
	CONF_RELAY_AGENT_TRIAL,
	CONF_RELAY_SECRET,
	CONF_RELAY_SERVER_ID,
	CONF_RELAY_WS_URL,
	DEFAULT_PORTAL_URL,
	relay_ws_url_for_portal,
)
from .relay_client import (
	async_agent_key,
	async_instance_id,
	async_request_agent_key,
	async_start_relay,
	async_stop_relay,
)

_LOGGER = logging.getLogger(__name__)

# What the panel offers and what it ticks for you.  ``ha:files`` reads
# secrets.yaml, so it is shown unticked rather than withheld: the visible
# boundary is the point of the brokered model.
SCOPE_READ = "ha:read"
SCOPE_WRITE = "ha:write"
SCOPE_CONFIG = "ha:config"
SCOPE_FILES = "ha:files"
DEFAULT_SCOPES = (SCOPE_READ, SCOPE_WRITE, SCOPE_CONFIG)
OFFERED_SCOPES = (SCOPE_READ, SCOPE_WRITE, SCOPE_CONFIG, SCOPE_FILES)


class AgentKeyError(Exception):
	"""Something a person needs to be told, in words they can act on."""


def _relay(entry: ConfigEntry) -> dict:
	relay = (entry.options or {}).get(CONF_RELAY)
	return dict(relay) if isinstance(relay, dict) else {}


def _portal_url(entry: ConfigEntry) -> str:
	data = {**(entry.data or {}), **(entry.options or {})}
	return str(data.get("portal_url") or DEFAULT_PORTAL_URL).rstrip("/")


def _secret(entry: ConfigEntry) -> str:
	return str(_relay(entry).get(CONF_RELAY_SECRET) or "")


def is_trial(entry: ConfigEntry) -> bool:
	"""Whether this house's link was opened by an agent key."""
	return bool(_relay(entry).get(CONF_RELAY_AGENT_TRIAL))


def trial_seconds_left(entry: ConfigEntry, *, now: Optional[float] = None) -> int:
	expires = _relay(entry).get(CONF_RELAY_AGENT_EXPIRES)
	if not expires:
		return 0
	now = time.time() if now is None else now
	return max(0, int(expires) - int(now))


def _clean_scopes(scopes) -> list:
	"""Keep only permissions this panel offers, in a stable order.

	A scope this build does not know about is dropped rather than
	forwarded: the portal clamps it anyway, and forwarding it would let
	a stale panel ask for something it cannot then display.
	"""
	wanted = {str(s).strip() for s in (scopes or [])}
	return [s for s in OFFERED_SCOPES if s in wanted]


async def _save_relay(hass: HomeAssistant, entry: ConfigEntry, relay: dict,
                      *, portal_url: Optional[str] = None) -> None:
	options = dict(entry.options or {})
	options[CONF_RELAY] = relay
	if portal_url:
		# The credentials only work against the Vome that issued them;
		# without this every later call falls back to production.
		options["portal_url"] = portal_url
	hass.config_entries.async_update_entry(entry, options=options)


# ── Issuing one ─────────────────────────────────────────────────────────

async def async_request_key(
	hass: HomeAssistant, entry: ConfigEntry, *, scopes=None,
) -> dict:
	"""Get a key for this Home Assistant and bring its tunnel up.

	Returns Vome's answer, including the one-time ``token`` and the
	paste-ready ``mcp`` block.  The token is handed straight to whoever
	pressed the button and stored nowhere: Vome keeps only a hash, and
	an add-on that kept a copy would be a second place to steal it from.
	"""
	from . import health_score

	if is_trial(entry):
		raise AgentKeyError(
			"This Home Assistant already has an agent key. Use Replace key "
			"if you have lost it, or Revoke to start again."
		)
	if health_score.is_guest(entry):
		raise AgentKeyError(
			"This Home Assistant is on a temporary health-score link. Keep "
			"it by signing in from the link in the score, or let it expire, "
			"and then issue an agent key."
		)
	if health_score.is_linked(entry):
		raise AgentKeyError(
			"This Home Assistant is linked to a Vome account, so its keys "
			"live there — issue one under Account, API tokens."
		)

	session = async_get_clientsession(hass)
	portal_url = _portal_url(entry)
	wanted = _clean_scopes(scopes) or list(DEFAULT_SCOPES)
	opened = await async_request_agent_key(
		session, portal_url,
		name=hass.config.location_name or "",
		# Recorded now so that linking this house later finds the row it
		# already has — and keeps the key working — rather than minting a
		# second one beside it.
		instance_id=await async_instance_id(hass),
		scopes=wanted,
	)
	relay = _relay(entry)
	relay.update({
		CONF_RELAY_SERVER_ID: opened.get("server_id"),
		CONF_RELAY_SECRET: opened.get("relay_secret"),
		CONF_RELAY_WS_URL: relay_ws_url_for_portal(
			portal_url, opened.get("relay_ws_url"),
		),
		CONF_RELAY_AGENT_TRIAL: True,
		CONF_RELAY_AGENT_EXPIRES: opened.get("expires_at"),
	})
	await _save_relay(hass, entry, relay, portal_url=portal_url)
	# Nothing can be brokered until the tunnel is up, and the key is
	# useless until something can be.
	await async_start_relay(hass, entry)
	_LOGGER.info(
		"Vome agent key issued (server %s), expires at %s",
		opened.get("server_id"), opened.get("expires_at"),
	)
	return opened


# ── Living with one ─────────────────────────────────────────────────────

async def async_status(hass: HomeAssistant, entry: ConfigEntry) -> dict:
	"""What the key grants and when it ends, asked of Vome.

	Asked rather than remembered, because the clock is Vome's and a
	panel that reported its own stale copy would show a key as live
	after it had stopped working.
	"""
	secret = _secret(entry)
	if not secret:
		return {"active": False}
	session = async_get_clientsession(hass)
	state = await async_agent_key(session, "GET", _portal_url(entry), secret)
	state.pop("_status", None)
	return state


async def async_set_scopes(hass: HomeAssistant, entry: ConfigEntry, scopes) -> dict:
	"""Re-grant the key in place.  Its secret does not change.

	So ticking a box never means going back to the editor and pasting a
	new ``mcp.json``.
	"""
	wanted = _clean_scopes(scopes)
	if not wanted:
		raise AgentKeyError("Choose at least one permission for the key.")
	secret = _secret(entry)
	if not secret:
		raise AgentKeyError("There is no agent key on this Home Assistant.")
	session = async_get_clientsession(hass)
	state = await async_agent_key(
		session, "PATCH", _portal_url(entry), secret, {"scopes": wanted},
	)
	state.pop("_status", None)
	return state


async def async_reissue(hass: HomeAssistant, entry: ConfigEntry) -> dict:
	"""Replace a lost key, keeping its permissions and its clock."""
	secret = _secret(entry)
	if not secret:
		raise AgentKeyError("There is no agent key on this Home Assistant.")
	session = async_get_clientsession(hass)
	issued = await async_agent_key(session, "POST", _portal_url(entry), secret)
	issued.pop("_status", None)
	relay = _relay(entry)
	if issued.get("expires_at"):
		relay[CONF_RELAY_AGENT_EXPIRES] = issued["expires_at"]
		await _save_relay(hass, entry, relay)
	return issued


async def async_revoke(hass: HomeAssistant, entry: ConfigEntry) -> dict:
	"""End it: the key, the link, and the throwaway account behind it.

	Vome's side goes first.  If that fails we have changed nothing and
	can say so; if it succeeds, the credentials left here are already
	dead and clearing them is bookkeeping.  The other order would leave
	a house that looks unlinked while a live key still reaches it.
	"""
	secret = _secret(entry)
	if not secret:
		return {"revoked": False}
	session = async_get_clientsession(hass)
	result = await async_agent_key(session, "DELETE", _portal_url(entry), secret)
	result.pop("_status", None)

	await async_stop_relay(hass, entry)
	options = dict(entry.options or {})
	options.pop(CONF_RELAY, None)
	hass.config_entries.async_update_entry(entry, options=options)
	_LOGGER.info("Vome agent key revoked; the link went with it")
	return {"revoked": bool(result.get("revoked", True))}


# ── What the panel renders ──────────────────────────────────────────────

async def async_panel_state(hass: HomeAssistant, entry: ConfigEntry) -> dict:
	"""Everything the panel's agent view needs, in one answer.

	``offer`` is the panel's instruction for what to draw, so the
	wording of each case lives in one place rather than being inferred
	from three booleans at the other end of an HTTP call.
	"""
	from . import health_score

	state = {
		"offered_scopes": list(OFFERED_SCOPES),
		"default_scopes": list(DEFAULT_SCOPES),
		"portal_url": _portal_url(entry),
		"trial": is_trial(entry),
		"seconds_left": trial_seconds_left(entry),
	}
	if is_trial(entry):
		try:
			state.update(await async_status(hass, entry))
		except Exception as err:  # noqa: BLE001 - a panel must still render
			_LOGGER.debug("Agent key status unavailable: %s", err)
			state["error"] = str(err)
		state["offer"] = "manage"
		return state
	if health_score.is_guest(entry):
		state["offer"] = "guest_link_first"
		return state
	if health_score.is_linked(entry):
		state["offer"] = "linked_account"
		return state
	state["offer"] = "issue"
	return state
