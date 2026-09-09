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
