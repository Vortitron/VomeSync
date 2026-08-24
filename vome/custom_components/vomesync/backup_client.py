"""HTTP transport for the Vome backup agent.

Deliberately separate from ``backup.py``: the Home Assistant ``BackupAgent``
base class only exists on recent cores (we support back to 2024.1 per
``hacs.json``), so keeping the wire protocol in a plain class means it can be
tested everywhere while ``backup.py`` stays a thin adapter that is only ever
imported by the cores which have the platform.

Authenticates with the relay secret.  The integration has no access to the
portal's signing key, so it cannot produce the HMAC the older sync endpoints
expect; the portal's ``_agent_authenticate`` accepts the relay secret and
recovers the owning server from it.
"""
from __future__ import annotations

import inspect
import json
import logging
from typing import Any, AsyncIterator, Optional

import aiohttp

from .const import (
	CONF_BACKUP,
	CONF_BACKUP_SECRET,
	CONF_RELAY,
	CONF_RELAY_SECRET,
	CONF_RELAY_SERVER_ID,
	DEFAULT_PORTAL_URL,
	backup_secret_server_id,
)

_LOGGER = logging.getLogger(__name__)

AGENT_PATH = "/api/sync/agent/backups"

# Uploads are sent as a stream with the metadata framed at the head of the
# body, not as multipart.  Multipart made the portal buffer the entire archive
# to a temporary file before its own streaming store ever ran -- on a 4 GB
# tmpfs, against backups several times that, which filled the disk and 500'd
# every upload.  Framing keeps both ends streaming end to end.
#
# Layout: 4-byte big-endian metadata length, that many bytes of JSON, then the
# archive.  Metadata is framed rather than sent as a header because HA's
# ``extra_metadata`` is free-form and a large one would breach the server's
# 8 KB header ceiling -- a limit that would only ever bite whoever had the
# most add-ons.
STREAM_CONTENT_TYPE = "application/vnd.vome.backup"
_UPLOAD_CHUNK = 1024 * 1024

# Uploads are streamed, so the timeout has to cover a multi-gigabyte archive on
# a domestic uplink rather than a typical API call.
UPLOAD_TIMEOUT = aiohttp.ClientTimeout(total=6 * 60 * 60, connect=30)
METADATA_TIMEOUT = aiohttp.ClientTimeout(total=60)
DOWNLOAD_TIMEOUT = aiohttp.ClientTimeout(total=6 * 60 * 60, connect=30)


def credentials_for_entry(entry) -> tuple:
	"""``(server_id, secret)`` an entry can back up with, or ``(None, None)``.

	Two ways to hold one.  A **relay link** carries a server id and secret for
	the tunnel, and backing up rides on that credential.  A **backup key** is
	the standalone form, for an instance with no relay link to carry it — a
	VomeHome-hosted VM has no tunnel to itself, so it holds no relay secret
	and never gets one, and before the key existed its Home Assistant could
	not authenticate to the backup agent at all.

	The backup key wins where both are present: it is the narrower grant of
	the two — that server's backup storage and nothing else — while the relay
	secret also authenticates the tunnel that brokers calls into the home.  If
	the narrow one works there is no reason to send the wide one.

	Lives here rather than in ``backup.py`` for the reason that module's
	docstring gives: importing it needs ``homeassistant.components.backup``,
	which only exists on recent cores, so anything that can be tested without
	one belongs on this side of the line.
	"""
	options = getattr(entry, "options", None) or {}

	backup = options.get(CONF_BACKUP) or {}
	secret = backup.get(CONF_BACKUP_SECRET)
	server_id = backup_secret_server_id(secret)
	if server_id and secret:
		return server_id, secret

	relay = options.get(CONF_RELAY) or {}
	if relay.get(CONF_RELAY_SERVER_ID) and relay.get(CONF_RELAY_SECRET):
		return relay[CONF_RELAY_SERVER_ID], relay[CONF_RELAY_SECRET]

	return None, None


class VomeBackupError(Exception):
	"""A backup operation failed in a way worth showing the user."""


class VomeBackupNotEntitled(VomeBackupError):
	"""No active backup plan — actionable by the user, so distinguished."""


class VomeBackupClient:
	"""Talks to the portal's backup-agent endpoints for one linked HA."""

	def __init__(
		self,
		session: aiohttp.ClientSession,
		*,
		secret: str,
		portal_url: Optional[str] = None,
	) -> None:
		self._session = session
		self._secret = secret
		self._base = (portal_url or DEFAULT_PORTAL_URL).rstrip("/")

	@property
	def _headers(self) -> dict:
		return {"Authorization": f"Bearer {self._secret}"}

	def _url(self, backup_id: Optional[str] = None) -> str:
		if backup_id:
			return f"{self._base}{AGENT_PATH}/{backup_id}"
		return f"{self._base}{AGENT_PATH}"

	async def _raise_for_status(self, resp: aiohttp.ClientResponse) -> None:
		if resp.status < 400:
			return
		message = ""
		try:
			body = await resp.json()
			if isinstance(body, dict):
				message = str(body.get("error") or "")
		except Exception:  # noqa: BLE001 - error bodies are best-effort
			message = ""
		if resp.status == 402:
			# Not a failure of ours: the plan simply is not active. Carry the
			# portal's wording through so the user reads one clear sentence.
			raise VomeBackupNotEntitled(message or "No active backup plan for this Home Assistant.")
		raise VomeBackupError(message or f"Vome returned HTTP {resp.status}")

	async def async_list(self) -> list[dict]:
		async with self._session.get(
			self._url(), headers=self._headers, timeout=METADATA_TIMEOUT
		) as resp:
			await self._raise_for_status(resp)
			data = await resp.json()
		backups = data.get("backups") if isinstance(data, dict) else None
		return backups if isinstance(backups, list) else []

	async def async_get(self, backup_id: str) -> Optional[dict]:
		async with self._session.get(
			self._url(backup_id), headers=self._headers, timeout=METADATA_TIMEOUT
		) as resp:
			if resp.status == 404:
				return None
			await self._raise_for_status(resp)
			data = await resp.json()
		return data.get("backup") if isinstance(data, dict) else None

	async def async_upload(
		self, backup_id: str, metadata: dict, stream: Any
	) -> dict:
		"""Stream ``stream`` up, metadata framed at the head of the body.

		``stream`` may be an async iterator (what Home Assistant's backup
		platform hands us) or a plain file-like; both are read a chunk at a
		time, so the archive is never held in memory at either end.
		"""
		blob = json.dumps(metadata or {}).encode()

		async def _body():
			yield len(blob).to_bytes(4, "big") + blob
			if isinstance(stream, (bytes, bytearray, memoryview)):
				# Not what Home Assistant sends, but a caller handing over a
				# whole archive in memory should upload it, not raise
				# AttributeError on a missing .read().
				yield bytes(stream)
				return
			if hasattr(stream, "__aiter__"):
				async for chunk in stream:
					if chunk:
						yield chunk
				return
			while True:
				chunk = stream.read(_UPLOAD_CHUNK)
				if inspect.isawaitable(chunk):
					chunk = await chunk
				if not chunk:
					return
				yield chunk

		headers = dict(self._headers)
		headers["X-Backup-Id"] = backup_id
		headers["Content-Type"] = STREAM_CONTENT_TYPE

		async with self._session.post(
			self._url(), headers=headers, data=_body(), timeout=UPLOAD_TIMEOUT
		) as resp:
			await self._raise_for_status(resp)
			data = await resp.json()
		return data.get("backup", {}) if isinstance(data, dict) else {}

	async def async_download(self, backup_id: str) -> AsyncIterator[bytes]:
		"""Yield the archive in chunks.

		Deliberately a generator over the live response: buffering a restore
		into memory is exactly the failure the streaming work removed on the
		server side, and it would be no better here.
		"""
		async with self._session.get(
			f"{self._url(backup_id)}?download=1",
			headers=self._headers,
			timeout=DOWNLOAD_TIMEOUT,
		) as resp:
			if resp.status == 404:
				raise VomeBackupError(f"Backup {backup_id} is not stored at Vome")
			await self._raise_for_status(resp)
			async for chunk in resp.content.iter_chunked(1024 * 1024):
				yield chunk

	async def async_delete(self, backup_id: str) -> None:
		async with self._session.delete(
			self._url(backup_id), headers=self._headers, timeout=METADATA_TIMEOUT
		) as resp:
			if resp.status == 404:
				return  # already gone is the desired end state
			await self._raise_for_status(resp)
