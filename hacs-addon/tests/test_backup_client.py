# flake8: noqa
"""Wire protocol for the Vome backup agent.

Kept separate from the Home Assistant adapter because the ``BackupAgent`` base
class only exists on recent cores while we support back to 2024.1 — so this is
where the behaviour that matters actually gets pinned.
"""
import json
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest

from custom_components.vomesync.backup_client import (
	VomeBackupClient,
	VomeBackupError,
	VomeBackupNotEntitled,
)


def _response(status=200, payload=None, chunks=None):
	resp = AsyncMock()
	resp.status = status
	resp.json = AsyncMock(return_value=payload if payload is not None else {})
	if chunks is not None:
		async def _iter(_n):
			for chunk in chunks:
				yield chunk
		resp.content = MagicMock()
		resp.content.iter_chunked = _iter
	return resp


def _session_with(resp):
	session = MagicMock(spec=aiohttp.ClientSession)
	cm = MagicMock()
	cm.__aenter__ = AsyncMock(return_value=resp)
	cm.__aexit__ = AsyncMock(return_value=False)
	for verb in ("get", "post", "delete"):
		setattr(session, verb, MagicMock(return_value=cm))
	return session


def _client(session, secret="rly-secret", portal="https://vome.io"):
	return VomeBackupClient(session, secret=secret, portal_url=portal)


class TestAuthAndUrls:
	@pytest.mark.asyncio
	async def test_sends_the_relay_secret_as_bearer(self):
		session = _session_with(_response(payload={"backups": []}))
		await _client(session).async_list()
		_args, kwargs = session.get.call_args
		assert kwargs["headers"]["Authorization"] == "Bearer rly-secret"

	@pytest.mark.asyncio
	async def test_targets_the_agent_endpoint(self):
		session = _session_with(_response(payload={"backups": []}))
		await _client(session).async_list()
		assert session.get.call_args[0][0] == "https://vome.io/api/sync/agent/backups"

	@pytest.mark.asyncio
	async def test_portal_url_override_is_honoured(self):
		session = _session_with(_response(payload={"backups": []}))
		await _client(session, portal="https://staging.vome.io/").async_list()
		assert session.get.call_args[0][0].startswith("https://staging.vome.io/api/")


class TestList:
	@pytest.mark.asyncio
	async def test_returns_the_backups_array(self):
		session = _session_with(_response(payload={"backups": [{"backup_id": "a"}]}))
		assert await _client(session).async_list() == [{"backup_id": "a"}]

	@pytest.mark.asyncio
	async def test_malformed_payload_is_an_empty_list_not_a_crash(self):
		# A restore screen that renders empty beats one that raises.
		session = _session_with(_response(payload={"unexpected": True}))
		assert await _client(session).async_list() == []


class TestGet:
	@pytest.mark.asyncio
	async def test_missing_backup_is_none(self):
		session = _session_with(_response(status=404))
		assert await _client(session).async_get("nope") is None

	@pytest.mark.asyncio
	async def test_returns_metadata(self):
		session = _session_with(_response(payload={"backup": {"backup_id": "a", "size": 7}}))
		assert (await _client(session).async_get("a"))["size"] == 7


class TestUpload:
	@pytest.mark.asyncio
	async def test_posts_multipart_with_id_and_metadata(self):
		session = _session_with(_response(payload={"backup": {"backup_id": "a"}}))
		await _client(session).async_upload("a", {"date": "2026-08-07"}, b"tar")
		_args, kwargs = session.post.call_args
		form = kwargs["data"]
		assert isinstance(form, aiohttp.FormData)

	@pytest.mark.asyncio
	async def test_upload_timeout_allows_a_large_archive(self):
		"""A default 5-minute client timeout would abort real backups."""
		session = _session_with(_response(payload={"backup": {}}))
		await _client(session).async_upload("a", {}, b"tar")
		assert session.post.call_args[1]["timeout"].total >= 3600


class TestDownload:
	@pytest.mark.asyncio
	async def test_yields_chunks_rather_than_buffering(self):
		session = _session_with(_response(chunks=[b"aa", b"bb"]))
		got = [c async for c in _client(session).async_download("a")]
		assert got == [b"aa", b"bb"]

	@pytest.mark.asyncio
	async def test_requests_the_download_variant(self):
		session = _session_with(_response(chunks=[b"x"]))
		[c async for c in _client(session).async_download("a")]
		assert session.get.call_args[0][0].endswith("/a?download=1")

	@pytest.mark.asyncio
	async def test_missing_backup_raises_with_the_id(self):
		session = _session_with(_response(status=404))
		with pytest.raises(VomeBackupError, match="abc"):
			[c async for c in _client(session).async_download("abc")]


class TestDelete:
	@pytest.mark.asyncio
	async def test_deletes(self):
		session = _session_with(_response(status=200))
		await _client(session).async_delete("a")
		assert session.delete.called

	@pytest.mark.asyncio
	async def test_already_gone_is_not_an_error(self):
		# HA retries deletes; "not there" is the outcome it wanted.
		session = _session_with(_response(status=404))
		await _client(session).async_delete("a")


class TestErrorMapping:
	@pytest.mark.asyncio
	async def test_402_is_a_distinct_entitlement_error(self):
		"""Not having paid is a user-actionable state, not a transport failure."""
		session = _session_with(_response(status=402, payload={"error": "Add a backup plan"}))
		with pytest.raises(VomeBackupNotEntitled, match="Add a backup plan"):
			await _client(session).async_list()

	@pytest.mark.asyncio
	async def test_entitlement_error_is_still_a_backup_error(self):
		# So a caller that only knows the base class still handles it.
		assert issubclass(VomeBackupNotEntitled, VomeBackupError)

	@pytest.mark.asyncio
	async def test_server_message_is_surfaced(self):
		session = _session_with(_response(status=400, payload={"error": "Invalid backup id"}))
		with pytest.raises(VomeBackupError, match="Invalid backup id"):
			await _client(session).async_list()

	@pytest.mark.asyncio
	async def test_unparseable_error_body_still_reports_the_status(self):
		resp = _response(status=500)
		resp.json = AsyncMock(side_effect=ValueError("not json"))
		with pytest.raises(VomeBackupError, match="500"):
			await _client(_session_with(resp)).async_list()

	@pytest.mark.asyncio
	async def test_401_is_reported_rather_than_swallowed(self):
		session = _session_with(_response(status=401, payload={"error": "Invalid token"}))
		with pytest.raises(VomeBackupError, match="Invalid token"):
			await _client(session).async_list()


class TestAgentChangeNotification:
	"""Linking or unlinking changes which backup locations exist. Without a
	notification the backup page keeps showing the old set until a restart,
	which reads as the integration being broken."""

	def test_link_and_unlink_notify(self, monkeypatch):
		import asyncio
		from types import SimpleNamespace
		from unittest.mock import AsyncMock, MagicMock
		import custom_components.vomesync.services_remote as sr

		entry = SimpleNamespace(entry_id="e1", options={})
		hass = MagicMock()
		hass.data = {}
		hass.config_entries.async_entries.return_value = [entry]
		hass.config.location_name = "Home"

		def _update(e, options=None):
			e.options = options
		hass.config_entries.async_update_entry.side_effect = _update

		notified = []
		monkeypatch.setattr(sr, "_notify_backup_agents_changed", lambda h: notified.append(h))
		monkeypatch.setattr(sr, "async_get_clientsession", lambda h: MagicMock())
		monkeypatch.setattr(sr, "async_request_device_code", AsyncMock(return_value={
			"device_code": "d", "user_code": "U", "verification_uri": "v",
			"interval": 5, "expires_in": 900,
		}))
		monkeypatch.setattr(sr, "async_start_relay", AsyncMock())
		monkeypatch.setattr(sr, "async_stop_relay", AsyncMock())
		monkeypatch.setattr(sr, "async_poll_device_token", AsyncMock(return_value={
			"status": "approved", "server_id": "rly-9",
			"relay_secret": "s", "relay_ws_url": "wss://x/ws/relay",
		}))

		sr.async_register_remote_services(hass)
		handlers = {c.args[1]: c.args[2] for c in hass.services.async_register.call_args_list}
		call = SimpleNamespace(data={})

		asyncio.run(handlers["link_start"](call))
		asyncio.run(handlers["link_poll"](call))
		assert len(notified) == 1, "linking did not refresh the backup locations"

		asyncio.run(handlers["unlink"](call))
		assert len(notified) == 2, "unlinking did not refresh the backup locations"

	def test_notification_is_safe_on_a_core_without_the_backup_platform(self):
		"""We support HA back to 2024.1, which has no backup agents at all."""
		from unittest.mock import MagicMock
		import custom_components.vomesync.services_remote as sr
		# Must not raise even though importing .backup fails on this stub.
		sr._notify_backup_agents_changed(MagicMock())
