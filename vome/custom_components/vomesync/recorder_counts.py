"""How many times each entity wrote to the recorder, without dumping history.

The health score's "Chatty devices" check used to pull
``GET /api/history/period`` for every entity over 24 hours. On a busy
recorder that response is huge: it times out, blows the relay body cap,
or comes back as non-JSON, and the check always reads as "could not be
checked".

Counting in the recorder database is the same measurement and a few
kilobytes. This view is what the portal asks first.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Optional

from homeassistant.core import HomeAssistant

from .const import HISTORY_WINDOW_HOURS, RECORDER_COUNTS_PATH

_LOGGER = logging.getLogger(__name__)

# One row per entity that changed in the window. SQLite and MariaDB both
# accept this shape; ``:start`` is for SQLAlchemy, ``?`` for the unit test.
_COUNTS_SQL = """
SELECT sm.entity_id AS entity_id, COUNT(*) AS cnt
FROM states s
INNER JOIN states_meta sm ON sm.metadata_id = s.metadata_id
WHERE s.last_updated_ts > {placeholder}
GROUP BY sm.metadata_id, sm.entity_id
"""
COUNTS_SQL = _COUNTS_SQL.format(placeholder=":start")
COUNTS_SQL_SQLITE = _COUNTS_SQL.format(placeholder="?")


def counts_from_rows(rows) -> dict:
	"""Turn query rows into ``{entity_id: change_count}``."""
	counts: dict[str, int] = {}
	for row in rows or []:
		entity_id = None
		cnt = 0
		if isinstance(row, dict) or hasattr(row, "keys"):
			try:
				entity_id = row["entity_id"]
				cnt = row["cnt"]
			except Exception:  # noqa: BLE001 - a malformed row is skipped
				entity_id = None
		elif isinstance(row, (tuple, list)) and len(row) >= 2:
			entity_id, cnt = row[0], row[1]
		else:
			entity_id = getattr(row, "entity_id", None)
			cnt = getattr(row, "cnt", 0)
		if not entity_id:
			continue
		try:
			n = int(cnt)
		except (TypeError, ValueError):
			continue
		if n > 0:
			counts[str(entity_id)] = n
	return counts


def _query_sync(hass: HomeAssistant, start_ts: float) -> dict:
	"""Run the count on the recorder thread. Must not touch the event loop."""
	from homeassistant.components.recorder.util import session_scope
	from sqlalchemy import text

	with session_scope(hass=hass, read_only=True) as session:
		result = session.execute(text(COUNTS_SQL), {"start": start_ts})
		rows = result.mappings() if hasattr(result, "mappings") else result
		return counts_from_rows(rows)


async def async_recorder_counts(
	hass: HomeAssistant, *, hours: int = HISTORY_WINDOW_HOURS,
) -> Optional[dict]:
	"""``{entity_id: count}`` for the window, or None if the recorder is off."""
	if "recorder" not in hass.config.components:
		return None
	try:
		from homeassistant.components import recorder
		if recorder.get_instance(hass) is None:
			return None
	except Exception:  # noqa: BLE001 - recorder missing is a skip, not a crash
		_LOGGER.debug("Recorder is not available for chatty-device counts")
		return None

	start_ts = time.time() - max(1, int(hours)) * 3600
	# Let a query failure propagate: the view answers 503 so Vome can
	# still try the history dump. Returning None here would look like
	# the recorder is off.
	return await hass.async_add_executor_job(_query_sync, hass, start_ts)


def _hass_from_request(request) -> Any:
	app = request.app
	hass = app.get("hass")
	if hass is not None:
		return hass
	try:
		from homeassistant.components.http.const import KEY_HASS
	except ImportError:
		from homeassistant.components.http import KEY_HASS
	return app[KEY_HASS]


def async_register_view(hass: HomeAssistant) -> bool:
	"""Expose the count endpoint. Idempotent; False when HTTP is not up."""
	http = getattr(hass, "http", None)
	if http is None:
		return False
	from homeassistant.components.http import HomeAssistantView

	class VomeRecorderCountsView(HomeAssistantView):
		"""Authenticated ``GET`` of recorder write counts for the health score."""

		url = RECORDER_COUNTS_PATH
		name = "api:vomesync:health:recorder_counts"

		async def get(self, request):
			core = _hass_from_request(request)
			try:
				counts = await async_recorder_counts(core)
			except Exception:  # noqa: BLE001 - Vome falls back to history
				_LOGGER.warning("Recorder count query failed", exc_info=True)
				return self.json({"error": "recorder_query_failed"}, status_code=503)
			if counts is None:
				return self.json({"error": "recorder_unavailable"}, status_code=404)
			return self.json({
				"window_hours": HISTORY_WINDOW_HOURS,
				"counts": counts,
			})

	http.register_view(VomeRecorderCountsView())
	return True
