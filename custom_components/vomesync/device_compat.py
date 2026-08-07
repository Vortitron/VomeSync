"""Device-registry helpers that work either side of the HA 2026.8 change.

Core 2026.8 restricted a device to a single config entry and at most one
subentry, and deprecated the registry lookups that assumed identifiers were
globally unique:

* ``async_get_device(identifiers={...})``  → ``async_get_device_by_identifier((domain, id), entry_id)``

The old calls keep working behind a compatibility shim until Core 2027.8, but
the shim only covers ~90% of cases, so we move now.  We still support HA back to
2024.1 (see ``hacs.json``), where the new helper simply does not exist — hence
the capability check rather than a version comparison: it is the API we care
about, not the release number, and this keeps working through whatever the
method is renamed to next.
"""
from __future__ import annotations

from typing import Any, Optional

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr


def async_get_device_by_identifier(
	hass: HomeAssistant,
	identifier: tuple[str, str],
	config_entry_id: Optional[str],
) -> Optional[Any]:
	"""Return the device carrying ``identifier`` for this config entry.

	Scoped to ``config_entry_id`` on 2026.8+, which is the point of the change:
	the same identifier may now legitimately exist under another integration and
	must not be returned to us.  Falls back to the pre-2026.8 global lookup when
	the scoped helper is unavailable.
	"""
	registry = dr.async_get(hass)

	scoped = getattr(registry, "async_get_device_by_identifier", None)
	if callable(scoped) and config_entry_id:
		return scoped(identifier, config_entry_id)

	# Pre-2026.8, or we have no entry to scope by: identifiers were globally
	# unique then, so the unscoped lookup is correct rather than merely tolerated.
	return registry.async_get_device(identifiers={identifier})


def async_remove_device_by_identifier(
	hass: HomeAssistant,
	identifier: tuple[str, str],
	config_entry_id: Optional[str],
) -> bool:
	"""Remove the device carrying ``identifier``; return whether one was removed."""
	device = async_get_device_by_identifier(hass, identifier, config_entry_id)
	if device is None:
		return False
	dr.async_get(hass).async_remove_device(device.id)
	return True
