# flake8: noqa
"""Chatty-device counts come from the recorder, not a 24 h history dump."""
import sqlite3
import time

from custom_components.vomesync.const import RECORDER_COUNTS_PATH
from custom_components.vomesync.recorder_counts import (
	COUNTS_SQL_SQLITE,
	counts_from_rows,
)


def test_the_portal_path_is_the_one_this_view_serves():
	assert RECORDER_COUNTS_PATH == "/api/vomesync/health/recorder_counts"


def test_counts_from_rows_accepts_tuples_and_mappings():
	assert counts_from_rows([
		("sensor.a", 4),
		{"entity_id": "light.b", "cnt": 2},
		("bad", "nope"),
		None,
	]) == {"sensor.a": 4, "light.b": 2}


def test_a_sqlite_recorder_shape_counts_changes_in_the_window():
	"""The query the live view runs, against HA's states + states_meta."""
	now = time.time()
	conn = sqlite3.connect(":memory:")
	conn.row_factory = sqlite3.Row
	conn.executescript("""
		CREATE TABLE states_meta (metadata_id INTEGER PRIMARY KEY, entity_id TEXT);
		CREATE TABLE states (
			state_id INTEGER PRIMARY KEY,
			metadata_id INTEGER,
			last_updated_ts FLOAT
		);
	""")
	conn.execute(
		"INSERT INTO states_meta (metadata_id, entity_id) VALUES (1, 'sensor.flappy')",
	)
	conn.execute(
		"INSERT INTO states_meta (metadata_id, entity_id) VALUES (2, 'light.quiet')",
	)
	conn.executemany(
		"INSERT INTO states (metadata_id, last_updated_ts) VALUES (1, ?)",
		[(now - 60,), (now - 120,), (now - 180,)],
	)
	conn.execute(
		"INSERT INTO states (metadata_id, last_updated_ts) VALUES (2, ?)",
		(now - 90000,),  # outside a 24 h window
	)
	conn.commit()
	rows = conn.execute(COUNTS_SQL_SQLITE, (now - 86400,)).fetchall()
	assert counts_from_rows(rows) == {"sensor.flappy": 3}


def test_no_http_means_the_view_is_not_registered():
	from types import SimpleNamespace
	from custom_components.vomesync.recorder_counts import async_register_view

	hass = SimpleNamespace(http=None)
	assert async_register_view(hass) is False


def test_the_count_runs_on_the_recorders_executor(monkeypatch):
	"""Home Assistant warns when database work runs anywhere but the
	recorder's own executor, so the count must go through it."""
	import asyncio
	import sys
	import types

	from custom_components.vomesync import recorder_counts

	ran_on = []

	class Recorder:
		async def async_add_executor_job(self, func, *args):
			ran_on.append("recorder")
			return {"light.kitchen": 3}

	class Hass:
		class config:
			components = {"recorder"}

		async def async_add_executor_job(self, func, *args):
			ran_on.append("hass")
			return {}

	fake = types.ModuleType("homeassistant.components.recorder")
	fake.get_instance = lambda hass: Recorder()
	monkeypatch.setitem(sys.modules, "homeassistant.components.recorder", fake)
	import homeassistant.components as components
	monkeypatch.setattr(components, "recorder", fake, raising=False)

	counts = asyncio.run(recorder_counts.async_recorder_counts(Hass()))
	assert counts == {"light.kitchen": 3}
	assert ran_on == ["recorder"]
