"""A random ``*.home.vome.io`` from inside Home Assistant, before an account.

The portal mints the hostname on ``POST /api/v1/relay/guest`` for a
house that has never linked, and on ``POST /api/sync/agent/remote-address``
for one that already has.  This module is the house-side of that gift:
the options-flow button, the persistent notification with the URL, and
turning full-UI forwarding on so the companion app can actually reach
Core.

One tunnel, two gifts, one claim.
"""
from __future__ import annotations

from typing import Optional

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.components import persistent_notification

from .const import (
	CONF_RELAY,
	CONF_RELAY_FORWARD_UI,
	CONF_RELAY_GUEST_EXPIRES,
	CONF_RELAY_REMOTE_URL,
	CONF_RELAY_SECRET,
	CONF_RELAY_SERVER_ID,
	DOMAIN,
)
from . import health_score as hs
from .relay_client import async_request_remote_address, async_start_relay

NOTIFICATION_ID = f"{DOMAIN}_remote_address"


def stored_url(entry: ConfigEntry) -> str:
	relay = (entry.options or {}).get(CONF_RELAY)
	if not isinstance(relay, dict):
		return ""
	return str(relay.get(CONF_RELAY_REMOTE_URL) or "")


def _has_relay_secret(entry: ConfigEntry) -> bool:
	return bool(hs._relay(entry).get(CONF_RELAY_SECRET))


async def async_ensure_address(hass: HomeAssistant, entry: ConfigEntry) -> dict:
	"""Hand back a remote URL, opening a guest run if this house has none.

	Returns ``{'status', 'remote_url', 'guest', 'claim_url', 'expires_at',
	'server_id'}``.  ``status`` is ``ready`` (already had one, or minted
	onto an existing link), ``opened`` (just provisioned a guest run) or
	``no_address`` (this house cannot have a random Vome hostname —
	external URL, or the portal has no forward proxy).
	"""
	existing = stored_url(entry)
	if existing:
		if _has_relay_secret(entry):
			await _ensure_forwarding(hass, entry)
		await _notify(hass, entry, existing, hs.claim_url(entry))
		return _result(entry, status="ready", remote_url=existing)

	if not hs.is_linked(entry):
		opened = await hs._open_guest_run(hass, entry, use_ai=True)
		url = str(opened.get("remote_url") or stored_url(entry) or "")
		await _notify(hass, entry, url, opened.get("claim_url") or "")
		# The same call queued a health check; watch for it so the sensor
		# still lights up.  The person asked for an address, not a score,
		# so this is a side-effect, not something to wait on.
		hass.async_create_task(hs.async_watch_for_report(hass, entry))
		return {
			"status": "opened",
			"remote_url": url,
			"guest": True,
			"claim_url": opened.get("claim_url") or "",
			"expires_at": opened.get("expires_at"),
			"server_id": opened.get("server_id") or "",
		}

	url = await _mint_on_linked(hass, entry)
	if not url:
		return _result(entry, status="no_address", remote_url="")
	await _notify(hass, entry, url, hs.claim_url(entry))
	return _result(entry, status="ready", remote_url=url)


def _result(entry: ConfigEntry, *, status: str, remote_url: str) -> dict:
	relay = hs._relay(entry)
	server_id = relay.get(CONF_RELAY_SERVER_ID) or ""
	if not server_id:
		server_id, _secret = hs._agent_credentials(entry)
	return {
		"status": status,
		"remote_url": remote_url,
		"guest": hs.is_guest(entry),
		"claim_url": hs.claim_url(entry),
		"expires_at": relay.get(CONF_RELAY_GUEST_EXPIRES),
		"server_id": server_id or "",
	}


async def _mint_on_linked(hass: HomeAssistant, entry: ConfigEntry) -> str:
	"""Ask Vome for a hostname on a house that already has credentials."""
	session = hs.async_get_clientsession(hass)
	_server_id, secret = hs._agent_credentials(entry)
	published = await async_request_remote_address(
		session, hs._portal_url(entry), secret,
	)
	url = str(published.get("remote_url") or "")
	if not url:
		return ""
	relay = hs._relay(entry)
	relay[CONF_RELAY_REMOTE_URL] = url
	if relay.get(CONF_RELAY_SECRET):
		relay[CONF_RELAY_FORWARD_UI] = True
		await hs._save_relay(hass, entry, relay)
		await async_start_relay(hass, entry)
	else:
		await hs._save_relay(hass, entry, relay)
	return url


async def _ensure_forwarding(hass: HomeAssistant, entry: ConfigEntry) -> None:
	"""A stored URL is useless if the tunnel is not forwarding the UI."""
	if not _has_relay_secret(entry):
		return
	relay = hs._relay(entry)
	if relay.get(CONF_RELAY_FORWARD_UI):
		return
	relay[CONF_RELAY_FORWARD_UI] = True
	await hs._save_relay(hass, entry, relay)
	await async_start_relay(hass, entry)


async def _notify(
	hass: HomeAssistant, entry: ConfigEntry, url: str, claim_url: str,
) -> None:
	if not url and not claim_url:
		return
	clock = hs.clock_phrase(entry)
	lines = []
	if url:
		lines.append(
			f"Your Home Assistant is reachable at:\n\n{url}\n\n"
			"Point the Home Assistant app at it. Sign in to Home Assistant as "
			"you usually would — no router ports, no domain to buy."
		)
	if hs.is_guest(entry) and claim_url:
		lines.append(
			f"Keep this address by signing in at {claim_url} — it lasts "
			f"{clock} unless you do. A health check is running on the same "
			"link; the report stays here either way."
		)
	elif claim_url:
		lines.append(f"Open it online: {claim_url}")
	persistent_notification.async_create(
		hass,
		"\n\n".join(lines),
		title="Vome remote address",
		notification_id=NOTIFICATION_ID,
	)


def step_info(entry: ConfigEntry, *, result: Optional[dict] = None) -> str:
	"""Copy for the options-flow form, British English, no marketing."""
	url = (result or {}).get("remote_url") or stored_url(entry)
	claim = (result or {}).get("claim_url") or hs.claim_url(entry)
	if url:
		lines = [
			f"This Home Assistant is reachable at:\n\n`{url}`\n",
			"Point the Home Assistant app at that address. Sign in to Home "
			"Assistant as you usually would.",
		]
		if hs.is_guest(entry) and claim:
			lines.append(
				f"Keep it by signing in at {claim} — about "
				f"{hs.clock_phrase(entry)} left unless you do."
			)
		return "\n\n".join(lines)
	if hs.is_linked(entry):
		return (
			"Press **Submit** to issue a random web address on the tunnel "
			"this Home Assistant already has. No domain to buy, no router "
			"ports. A memorable name is bought on vome.io."
		)
	return (
		"Press **Submit** to get a private web address for this Home "
		"Assistant. No domain to buy, no router ports, no Vome account "
		"until you want to keep it. The address lasts a day unless you "
		"sign in from the link that appears with it."
	)
