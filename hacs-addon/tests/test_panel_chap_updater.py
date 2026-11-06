# flake8: noqa
"""The Vome app updates Vome CHAP while Home Assistant is stopped
(vome/panel/chap_updater.py).

GamlaBio.local, 6 Nov 2026: Vome CHAP 0.2.8 a week after 0.2.13. The
Supervisor refuses an app updating itself (403), and its own automatic
updates are sent through Home Assistant, which a standby keeps stopped.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "vome" / "panel"))
import chap_updater  # noqa: E402

STOPPED = (400, {"result": "error", "error_key": "homeassistant_not_running_error",
                 "message": "Home Assistant is not running"})


def supervisor(info, core=STOPPED, update=(200, {}), apps_key="addons"):
	calls = []

	def call(method, path, body=None, timeout=60):
		calls.append((method, path, body))
		if path == "/addons":
			return 200, {"data": {apps_key: [{"slug": "core_mosquitto"}, {"slug": "b1bff62e_vome"},
			                                  {"slug": "b1bff62e_vome_chap"}]}}
		if path == "/addons/b1bff62e_vome_chap/info":
			return 200, {"data": info}
		if path == "/core/stats":
			return core
		return update
	return call, calls


BEHIND = {"version": "0.2.8", "version_latest": "0.2.13", "update_available": True, "auto_update": True}


def test_a_stopped_install_s_vome_chap_is_updated():
	call, calls = supervisor(BEHIND)
	assert chap_updater.update_chap_once(call) == "updated Vome CHAP from 0.2.8 to 0.2.13"
	assert calls[-1] == ("POST", "/store/addons/b1bff62e_vome_chap/update", {"backup": False})


def test_the_renamed_apps_list_is_read_too():
	call, calls = supervisor(BEHIND, apps_key="apps")
	assert chap_updater.update_chap_once(call).startswith("updated")


def test_not_while_home_assistant_runs():
	"""Then the Supervisor's own automatic update does it."""
	call, calls = supervisor(BEHIND, core=(200, {"data": {"cpu_percent": 3}}))
	assert chap_updater.update_chap_once(call) is None
	assert not any(c[0] == "POST" for c in calls)


def test_not_when_its_owner_turned_automatic_updates_off():
	call, calls = supervisor({**BEHIND, "auto_update": False})
	assert chap_updater.update_chap_once(call) is None
	assert not any(c[0] == "POST" for c in calls)


def test_nothing_waiting_or_no_vome_chap_is_quiet():
	call, calls = supervisor({**BEHIND, "update_available": False})
	assert chap_updater.update_chap_once(call) is None

	def no_chap(method, path, body=None, timeout=60):
		return 200, {"data": {"addons": [{"slug": "core_mosquitto"}]}}
	assert chap_updater.update_chap_once(no_chap) is None


def test_a_refusal_says_why():
	call, _ = supervisor(BEHIND, update=(400, {"result": "error", "message": "No space left"}))
	assert chap_updater.update_chap_once(call) == "could not update Vome CHAP to 0.2.13 (HTTP 400: No space left)"


def test_the_panel_starts_it():
	source = (ROOT / "vome" / "panel" / "server.py").read_text()
	assert "chap_updater.start()" in source
	assert "COPY panel/ " in (ROOT / "vome" / "Dockerfile").read_text()
