"""Diagnostics: Settings → Devices & services → Vome → Download diagnostics.

What a person attaches to a bug report, or what support asks for, with
every secret already gone.  Nothing here is fetched: it is what the entry
holds, what the relay knows about itself, and the last health report's
headline.  Redaction is by key name, generously — a key that *looks* like
a credential is blanked even if it was not one, because the cost of the
two mistakes is not symmetrical.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping, Optional

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import (
	CONF_RELAY,
	CONF_RELAY_AGENT_TRIAL,
	CONF_RELAY_FORWARD_UI,
	CONF_RELAY_GUEST,
	CONF_RELAY_LAN_ROUTES,
	CONF_RELAY_LOCAL_URL,
	CONF_RELAY_REMOTE_URL,
	CONF_RELAY_SERVER_ID,
	CONF_RELAY_WEBHOOKS,
	DOMAIN,
	INTEGRATION_VERSION,
)

REDACTED = "**REDACTED**"

# Any key whose lower-cased name contains one of these is blanked, at any
# depth.  "key" covers personal_key, access_key, signing_key, agent_key;
# "claim" covers the guest claim URL, which carries a one-time code.
_SECRET_FRAGMENTS = ("secret", "token", "key", "seed", "password", "claim", "webhook")


def _is_secret_key(name: Any) -> bool:
	lowered = str(name).lower()
	return any(fragment in lowered for fragment in _SECRET_FRAGMENTS)


def redact(value: Any) -> Any:
	"""A deep copy of ``value`` with every secret-looking key blanked."""
	if isinstance(value, Mapping):
		return {
			k: (REDACTED if _is_secret_key(k) and v not in (None, "", [], {}) else redact(v))
			for k, v in value.items()
		}
	if isinstance(value, (list, tuple)):
		return [redact(v) for v in value]
	return value


def _iso(ts: Optional[float]) -> Optional[str]:
	if not ts:
		return None
	return datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat()


def _relay_section(hass: HomeAssistant, entry: ConfigEntry) -> dict[str, Any]:
	from .relay_client import get_relay_client
	from .relay_health import is_down_too_long, is_rejected_raised

	relay = dict((entry.options or {}).get(CONF_RELAY) or {})
	client = get_relay_client(hass, entry.entry_id)
	section: dict[str, Any] = {
		"linked": bool(relay.get(CONF_RELAY_SERVER_ID)),
		"server_id": relay.get(CONF_RELAY_SERVER_ID) or "",
		"client_running": client is not None,
		"connected": bool(client is not None and client.connected),
		"connected_since": _iso(client.connected_since) if client else None,
		"disconnected_since": _iso(client.disconnected_since) if client else None,
		"rejected": bool(client is not None and getattr(client, "rejected", False)),
		"down_too_long": is_down_too_long(hass, entry.entry_id),
		"rejected_issue": is_rejected_raised(hass, entry.entry_id),
		"forward_ui": bool(relay.get(CONF_RELAY_FORWARD_UI)),
		"lan_routes": len(relay.get(CONF_RELAY_LAN_ROUTES) or []),
		"webhooks": len(relay.get(CONF_RELAY_WEBHOOKS) or []),
		"local_url_override": relay.get(CONF_RELAY_LOCAL_URL) or "",
		"remote_url": relay.get(CONF_RELAY_REMOTE_URL) or "",
		"guest": bool(relay.get(CONF_RELAY_GUEST)),
		"agent_trial": bool(relay.get(CONF_RELAY_AGENT_TRIAL)),
	}
	if client is not None:
		section["relay_host"] = client.ws_url
	return section


def _health_section(hass: HomeAssistant, entry: ConfigEntry) -> dict[str, Any]:
	from .health_score import stored_report

	report = stored_report(hass, entry.entry_id) or {}
	return {
		"has_report": bool(report),
		"score": report.get("score"),
		"generated_at": report.get("generated_at"),
		"findings": len(report.get("findings") or []),
	}


def _switches_section(hass: HomeAssistant, entry: ConfigEntry) -> dict[str, Any]:
	coordinator = hass.data.get(DOMAIN, {}).get(entry.entry_id)
	if coordinator is None:
		return {"coordinator": False}
	return {
		"coordinator": True,
		"owned": len(getattr(coordinator, "switches", None) or {}),
		"subscribed": len(getattr(coordinator, "subscriptions", None) or {}),
		"last_update_success": bool(getattr(coordinator, "last_update_success", False)),
	}


async def async_get_config_entry_diagnostics(
	hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
	"""Diagnostics for one Vome entry, secrets removed."""
	return {
		"integration_version": INTEGRATION_VERSION,
		"entry": {
			"title": entry.title,
			"data": redact(dict(entry.data or {})),
			"options": redact(dict(entry.options or {})),
		},
		"relay": _relay_section(hass, entry),
		"health_score": _health_section(hass, entry),
		"switches": _switches_section(hass, entry),
	}
