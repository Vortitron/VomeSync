"""Keep Vome CHAP up to date on an install whose Home Assistant is stopped.

GamlaBio.local, 6 Nov 2026: Vome CHAP stood at 0.2.8 a week after 0.2.13
was out. No app can update itself -- the Supervisor answers 403 ("App ...
can't update itself!", supervisor/api/store.py) -- and the Supervisor's own
automatic updates are carried out by Home Assistant (a websocket command to
Core, supervisor/misc/tasks.py), which a standby keeps stopped. So nothing
ever updated it. This app runs there too, and does what the Supervisor would
have done: only while Home Assistant is stopped (otherwise the Supervisor
does it itself, a day after a release), and only when automatic updates are
on for Vome CHAP (it turns them on for itself; an owner can turn them off).
Vome CHAP does the same for this app (portal chap_versions).

This is why the app asks for the manager role (config.yaml).
"""
from __future__ import annotations

import json
import logging
import os
import threading
import urllib.error
import urllib.request
from typing import Any, Callable, Optional

SUPERVISOR = os.environ.get("VOME_SUPERVISOR_API", "http://supervisor")
SUPERVISOR_TOKEN = os.environ.get("SUPERVISOR_TOKEN", "")
FIRST_LOOK_SECONDS = 10 * 60
LOOK_EVERY_SECONDS = 3600

LOG = logging.getLogger("vome-panel")


def _call(method: str, path: str, body: Optional[dict] = None, timeout: int = 60) -> tuple[int, Any]:
	req = urllib.request.Request(
		SUPERVISOR.rstrip("/") + path, method=method,
		data=json.dumps(body).encode() if body is not None else None,
		headers={"Authorization": f"Bearer {SUPERVISOR_TOKEN}", "Content-Type": "application/json"},
	)
	try:
		with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - fixed supervisor URL
			raw = resp.read()
			return resp.status, (json.loads(raw.decode("utf-8")) if raw else None)
	except urllib.error.HTTPError as err:
		try:
			return err.code, json.loads(err.read().decode("utf-8") or "null")
		except (OSError, ValueError):
			return err.code, None
	except (urllib.error.URLError, OSError, ValueError):
		return 0, None


def _data(body: Any) -> dict:
	data = body.get("data") if isinstance(body, dict) else None
	return data if isinstance(data, dict) else {}


def core_stopped(call: Callable = _call) -> bool:
	"""Only when the Supervisor positively says so (as Vome CHAP decides)."""
	status, body = call("GET", "/core/stats", None, 30)
	return (status == 400 and isinstance(body, dict)
	        and body.get("error_key") == "homeassistant_not_running_error")


def update_chap_once(call: Callable = _call) -> Optional[str]:
	"""Update Vome CHAP if it is installed, behind, set to update itself, and
	Home Assistant is stopped. Returns what happened, or None."""
	status, body = call("GET", "/addons", None, 30)
	listed = _data(body)
	apps = listed.get("addons") or listed.get("apps") or []
	slug = next((a.get("slug") for a in apps if isinstance(a, dict)
	             and str(a.get("slug") or "").endswith("_vome_chap")), None)
	if status != 200 or not slug:
		return None
	status, body = call("GET", f"/addons/{slug}/info", None, 30)
	info = _data(body)
	if status != 200 or not info.get("update_available") or not info.get("auto_update"):
		return None
	if not core_stopped(call):
		return None  # the Supervisor updates it itself
	have, latest = info.get("version"), info.get("version_latest")
	LOG.info("Vome CHAP %s is out (this install has %s) and Home Assistant is stopped; updating it", latest, have)
	status, body = call("POST", f"/store/addons/{slug}/update", {"backup": False}, 1800)
	if status != 200:
		said = body.get("message") if isinstance(body, dict) else None
		return f"could not update Vome CHAP to {latest} (HTTP {status}" + (f": {said}" if said else "") + ")"
	return f"updated Vome CHAP from {have} to {latest}"


def _look_forever(stop: threading.Event) -> None:
	if stop.wait(FIRST_LOOK_SECONDS):
		return
	while True:
		try:
			note = update_chap_once()
			if note:
				LOG.info("%s", note)
		except Exception:  # noqa: BLE001 - the panel must outlive this
			LOG.warning("Could not look at Vome CHAP's version", exc_info=True)
		if stop.wait(LOOK_EVERY_SECONDS):
			return


def start() -> threading.Event:
	stop = threading.Event()
	if SUPERVISOR_TOKEN:
		threading.Thread(target=_look_forever, args=(stop,), name="vome-chap-updater", daemon=True).start()
	return stop
