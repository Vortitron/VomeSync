# flake8: noqa
"""Vome backups fill Home Assistant's upload progress bar (4 Nov 2026).

Home Assistant hands a backup agent ``on_progress`` and expects
``on_progress(bytes_uploaded=...)`` with a running total; Dropbox, SFTP and
the others already did, and a Vome upload sat at nothing until it finished.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

backup = pytest.importorskip("custom_components.vomesync.backup")


def _entry():
	return SimpleNamespace(
		entry_id="e1", title="Vome", data={},
		options={"relay": {"server_id": "rly-1", "secret": "s3cret"}},
	)


async def _open_stream():
	async def _chunks():
		yield b"archive"
	return _chunks()


async def test_progress_reaches_home_assistant_as_bytes_uploaded(hass):
	agent = backup.VomeBackupAgent(hass, _entry())

	async def _upload(backup_id, metadata, stream, on_progress=None):
		on_progress(4)
		on_progress(7)
		return {}

	agent._client.async_upload = AsyncMock(side_effect=_upload)
	on_progress = MagicMock()
	await agent.async_upload_backup(
		open_stream=_open_stream, backup=MagicMock(backup_id="b1", as_dict=lambda: {}),
		on_progress=on_progress)
	assert [c.kwargs for c in on_progress.call_args_list] == [
		{"bytes_uploaded": 4}, {"bytes_uploaded": 7}]


async def test_an_older_core_that_passes_nothing_still_uploads(hass):
	agent = backup.VomeBackupAgent(hass, _entry())
	agent._client.async_upload = AsyncMock(return_value={})
	await agent.async_upload_backup(
		open_stream=_open_stream, backup=MagicMock(backup_id="b1", as_dict=lambda: {}))
	assert agent._client.async_upload.await_args.kwargs["on_progress"] is None
