"""Home Assistant backup platform — Vome as a backup location.

Home Assistant discovers this module only on cores that support backup agents,
and calls :func:`async_get_backup_agents` to learn where backups may be stored.
Registering here is what makes Vome appear in Settings → System → Backups
alongside "Home Assistant Cloud".

Kept deliberately thin: everything that can be tested without a recent core
lives in :mod:`backup_client` (we support HA back to 2024.1, which predates the
``BackupAgent`` base class entirely).
"""
from __future__ import annotations

import logging
from typing import Any, AsyncIterator, Callable

from homeassistant.components.backup import AgentBackup, BackupAgent, BackupAgentError
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .backup_client import (
	VomeBackupClient,
	VomeBackupError,
	credentials_for_entry,
)
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

DATA_LISTENERS = "_backup_agent_listeners"


def _linked_entries(hass: HomeAssistant) -> list:
	"""Config entries that can store backups in a Vome account."""
	return [
		entry for entry in hass.config_entries.async_entries(DOMAIN)
		if all(credentials_for_entry(entry))
	]


async def async_get_backup_agents(hass: HomeAssistant, **kwargs: Any) -> list[BackupAgent]:
	"""Return one agent per linked Vome account (usually exactly one)."""
	return [VomeBackupAgent(hass, entry) for entry in _linked_entries(hass)]


@callback
def async_register_backup_agents_listener(
	hass: HomeAssistant, *, listener: Callable[[], None], **kwargs: Any
) -> Callable[[], None]:
	"""Let Home Assistant know when linking or unlinking changes the agents."""
	listeners = hass.data.setdefault(DOMAIN, {}).setdefault(DATA_LISTENERS, [])
	listeners.append(listener)

	@callback
	def _unregister() -> None:
		if listener in listeners:
			listeners.remove(listener)

	return _unregister


@callback
def async_notify_backup_agents_changed(hass: HomeAssistant) -> None:
	"""Call after link/unlink so the backup page reflects reality immediately."""
	for listener in list(hass.data.get(DOMAIN, {}).get(DATA_LISTENERS, [])):
		try:
			listener()
		except Exception:  # noqa: BLE001 - one bad listener must not block others
			_LOGGER.debug("A backup agent listener raised", exc_info=True)


class VomeBackupAgent(BackupAgent):
	"""Stores Home Assistant backups in the owner's Vome account."""

	domain = DOMAIN

	def __init__(self, hass: HomeAssistant, entry) -> None:
		self._hass = hass
		self._entry = entry
		server_id, secret = credentials_for_entry(entry)
		self._server_id = server_id or ""
		self._client = VomeBackupClient(
			async_get_clientsession(hass),
			secret=secret or "",
		)
		self.name = entry.title or "Vome"
		# Stable across restarts and unique per linked account, so Home
		# Assistant keeps associating stored backups with this location.
		self.unique_id = self._server_id

	async def async_get_backup(self, backup_id: str, **kwargs: Any) -> AgentBackup | None:
		try:
			record = await self._client.async_get(backup_id)
		except VomeBackupError as err:
			raise BackupAgentError(str(err)) from err
		return _to_agent_backup(record) if record else None

	async def async_list_backups(self, **kwargs: Any) -> list[AgentBackup]:
		try:
			records = await self._client.async_list()
		except VomeBackupError as err:
			raise BackupAgentError(str(err)) from err
		out = []
		for record in records:
			backup = _to_agent_backup(record)
			if backup is not None:
				out.append(backup)
		return out

	async def async_upload_backup(
		self, *, open_stream, backup: AgentBackup, **kwargs: Any
	) -> None:
		try:
			await self._client.async_upload(
				backup.backup_id, backup.as_dict(), await open_stream()
			)
		except VomeBackupError as err:
			raise BackupAgentError(str(err)) from err

	async def async_download_backup(self, backup_id: str, **kwargs: Any) -> AsyncIterator[bytes]:
		try:
			return self._client.async_download(backup_id)
		except VomeBackupError as err:
			raise BackupAgentError(str(err)) from err

	async def async_delete_backup(self, backup_id: str, **kwargs: Any) -> None:
		try:
			await self._client.async_delete(backup_id)
		except VomeBackupError as err:
			raise BackupAgentError(str(err)) from err


def _to_agent_backup(record: dict):
	"""Rebuild an ``AgentBackup`` from the metadata we stored.

	We persist ``backup.as_dict()`` verbatim on upload, so the round trip is
	usually exact.  A record written by an older version (or hand-repaired)
	may be missing fields — skip it rather than raise, so one bad entry cannot
	make the whole backup page fail to load.
	"""
	try:
		return AgentBackup.from_dict(record)
	except Exception:  # noqa: BLE001 - one unreadable record must not hide the rest
		_LOGGER.warning(
			"Ignoring a Vome backup record that could not be read: %s",
			record.get("backup_id", "<no id>"),
		)
		return None
