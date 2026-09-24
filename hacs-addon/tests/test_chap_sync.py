# flake8: noqa
"""Tests for the add-on's CHAP config sync worker (vome/panel/chap_sync.py).

The worker keeps a standby install's /config in step with the active one's.
What matters most is what it must never do: write under a running Core, hand
an older Core newer ``.storage``, wipe a standby from an incomplete snapshot,
or act on a role it guessed.
"""
import importlib.util
import io
import json
import sqlite3
import tarfile
import urllib.error
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
	"vome_chap_sync", ROOT / "vome_chap" / "chap_sync.py"
)
cs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cs)


def make_config(root: Path, version="2026.9.1") -> Path:
	(root / ".storage").mkdir(parents=True)
	(root / ".storage" / "core.config_entries").write_text('{"entries": []}')
	(root / ".storage" / "auth").write_text('{"users": []}')
	(root / ".HA_VERSION").write_text(version)
	(root / "configuration.yaml").write_text("default_config:\n")
	(root / "automations.yaml").write_text("[]\n")
	(root / "home-assistant_v2.db").write_bytes(b"recorder history")
	(root / "home-assistant.log").write_text("log line\n")
	(root / "backups").mkdir()
	(root / "backups" / "abc.tar").write_bytes(b"tar")
	(root / "custom_components" / "x" / "__pycache__").mkdir(parents=True)
	(root / "custom_components" / "x" / "__init__.py").write_text("")
	(root / "custom_components" / "x" / "__pycache__" / "a.pyc").write_bytes(b"pyc")
	return root


class TestWhatIsSynced:
	@pytest.mark.parametrize("rel", [
		".storage/core.config_entries", ".storage/auth", "configuration.yaml",
		"automations.yaml", "custom_components/x/__init__.py", "zigbee.db",
		"esphome/node.yaml", "www/icon.png", ".HA_VERSION", "secrets.yaml",
	])
	def test_configuration_is_synced(self, rel):
		assert cs.is_synced(rel)

	@pytest.mark.parametrize("rel", [
		"home-assistant_v2.db", "home-assistant_v2.db-wal", "home-assistant.log",
		"home-assistant.log.1", "backups/abc.tar", "tts/x.mp3", "deps/lib.py",
		"custom_components/x/__pycache__/a.pyc", "zigbee.db-wal", ".ha_run.lock",
		".vome_chap_staging/.storage/auth", ".storage/core.restore_state.tmp",
	])
	def test_machine_local_and_live_files_are_not(self, rel):
		assert not cs.is_synced(rel)

	def test_walk_skips_excluded_trees_and_symlinks(self, tmp_path):
		root = make_config(tmp_path / "config")
		(root / "link.yaml").symlink_to("/etc/passwd")
		files = cs.synced_files(root)
		assert ".storage/core.config_entries" in files
		assert "custom_components/x/__init__.py" in files
		assert not any(f.startswith(("backups/", "home-assistant")) for f in files)
		assert "link.yaml" not in files


class TestBuild:
	def test_hash_is_stable_and_follows_content(self, tmp_path):
		root = make_config(tmp_path / "config")
		_, a = cs.build_snapshot(root)
		_, b = cs.build_snapshot(root)
		assert a["sha256"] == b["sha256"]
		assert a["ha_version"] == "2026.9.1"
		(root / "automations.yaml").write_text("- id: new\n")
		_, c = cs.build_snapshot(root)
		assert c["sha256"] != a["sha256"]

	def test_live_sqlite_database_is_copied_consistently(self, tmp_path):
		"""A WAL-mode database's newest rows live in -wal until a checkpoint.

		Copying the .db bytes alone would miss them; the backup API does not.
		"""
		root = make_config(tmp_path / "config")
		db = sqlite3.connect(root / "zigbee.db")
		db.execute("PRAGMA journal_mode=WAL")
		db.execute("PRAGMA wal_autocheckpoint=0")
		db.execute("CREATE TABLE devices (ieee TEXT)")
		db.execute("INSERT INTO devices VALUES ('00:11')")
		db.commit()  # left open: the row is only in zigbee.db-wal
		try:
			blob, _ = cs.build_snapshot(root)
		finally:
			db.close()
		with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tar:
			names = tar.getnames()
			data = tar.extractfile("zigbee.db").read()
		assert "zigbee.db-wal" not in names
		out = tmp_path / "copy.db"
		out.write_bytes(data)
		rows = sqlite3.connect(out).execute("SELECT ieee FROM devices").fetchall()
		assert rows == [("00:11",)]


def tar_of(files: dict) -> bytes:
	buf = io.BytesIO()
	with tarfile.open(fileobj=buf, mode="w:gz") as tar:
		for name, data in files.items():
			info = tarfile.TarInfo(name)
			info.size = len(data)
			tar.addfile(info, io.BytesIO(data))
	return buf.getvalue()


class TestKeepOwnIdentity:
	"""In reverse mode each install keeps its own Vome link: syncing the
	entry made the house fallback answer the relay as the hosted home."""

	@staticmethod
	def _entries(domains_ids):
		return json.dumps({"version": 1, "key": "core.config_entries", "data": {"entries": [
			{"domain": d, "entry_id": i} for d, i in domains_ids]}})

	def _pair(self, tmp_path):
		active = make_config(tmp_path / "active")
		(active / ".storage" / "core.config_entries").write_text(
			self._entries([("vomesync", "hosted-link"), ("tuya", "t1")]))
		standby = make_config(tmp_path / "standby")
		(standby / ".storage" / "core.config_entries").write_text(
			self._entries([("vomesync", "house-link"), ("tuya", "old")]))
		blob, _ = cs.build_snapshot(active)
		return standby, blob

	def _ids(self, standby):
		data = json.loads((standby / ".storage" / "core.config_entries").read_text())["data"]
		return sorted((e["domain"], e["entry_id"]) for e in data["entries"])

	def test_reverse_mode_keeps_this_installs_link(self, tmp_path):
		standby, blob = self._pair(tmp_path)
		cs.apply_snapshot(standby, blob, keep_own=("vomesync",))
		assert self._ids(standby) == [("tuya", "t1"), ("vomesync", "house-link")]

	def test_the_usual_direction_takes_the_homes_link(self, tmp_path):
		"""A hosted standby that takes over should answer as the home."""
		standby, blob = self._pair(tmp_path)
		cs.apply_snapshot(standby, blob)
		assert self._ids(standby) == [("tuya", "t1"), ("vomesync", "hosted-link")]

	def test_an_install_with_no_link_of_its_own_takes_none(self, tmp_path):
		standby, blob = self._pair(tmp_path)
		(standby / ".storage" / "core.config_entries").write_text(self._entries([]))
		cs.apply_snapshot(standby, blob, keep_own=("vomesync",))
		assert self._ids(standby) == [("tuya", "t1")]


class TestApply:
	def test_standby_becomes_a_mirror_and_local_files_are_left_alone(self, tmp_path):
		active = make_config(tmp_path / "active")
		(active / "automations.yaml").write_text("- id: kitchen\n")
		(active / "scripts.yaml").write_text("new: {}\n")
		standby = make_config(tmp_path / "standby")
		(standby / "old_package.yaml").write_text("deleted on the active side\n")
		(standby / "home-assistant_v2.db").write_bytes(b"standby's own history")

		blob, _ = cs.build_snapshot(active)
		result = cs.apply_snapshot(standby, blob)

		assert cs.synced_files(standby) == cs.synced_files(active)
		assert (standby / "automations.yaml").read_text() == "- id: kitchen\n"
		assert not (standby / "old_package.yaml").exists()
		assert result["removed"] == 1
		# Never synced, so never touched.
		assert (standby / "home-assistant_v2.db").read_bytes() == b"standby's own history"
		assert (standby / "backups" / "abc.tar").exists()
		assert not (standby / cs.STAGING_NAME).exists()

	def test_incomplete_snapshot_is_refused_and_deletes_nothing(self, tmp_path):
		standby = make_config(tmp_path / "standby")
		before = cs.synced_files(standby)
		with pytest.raises(cs.ApplyRefused, match="incomplete"):
			cs.apply_snapshot(standby, tar_of({"automations.yaml": b"[]"}))
		assert cs.synced_files(standby) == before

	@pytest.mark.parametrize("name", ["../escape.yaml", "/etc/passwd", "backups/x.tar"])
	def test_unsafe_or_unsynced_paths_are_refused(self, tmp_path, name):
		standby = make_config(tmp_path / "standby")
		blob = tar_of({".storage/core.config_entries": b"{}", ".HA_VERSION": b"2026.9.1",
		               name: b"x"})
		with pytest.raises(cs.ApplyRefused):
			cs.apply_snapshot(standby, blob)
		assert not (tmp_path / "escape.yaml").exists()

	def test_link_entries_are_refused(self, tmp_path):
		standby = make_config(tmp_path / "standby")
		buf = io.BytesIO()
		with tarfile.open(fileobj=buf, mode="w:gz") as tar:
			info = tarfile.TarInfo("secrets.yaml")
			info.type = tarfile.SYMTYPE
			info.linkname = "/etc/shadow"
			tar.addfile(info)
		with pytest.raises(cs.ApplyRefused, match="non-file"):
			cs.apply_snapshot(standby, buf.getvalue())


class TestVersionGuard:
	def test_older_or_equal_snapshot_is_fine(self):
		assert cs.version_blocker("2026.8.3", "2026.9.1") is None
		assert cs.version_blocker("2026.9.1", "2026.9.1") is None

	def test_newer_snapshot_needs_a_core_update(self):
		assert "update Core" in cs.version_blocker("2026.10.0", "2026.9.1")

	def test_unreadable_versions_only_pass_when_identical(self):
		assert cs.version_blocker("dev", "dev") is None
		assert cs.version_blocker("dev", "") is not None


class FakeResponse:
	def __init__(self, status, body=b"", headers=None):
		self.status = status
		self._body = body
		self.headers = headers or {}

	def read(self, *_):
		return self._body

	def __enter__(self):
		return self

	def __exit__(self, *exc):
		return False


def http_error(status, body):
	def opener(req, timeout=None):
		raise urllib.error.HTTPError(req.full_url, status, "err", {}, io.BytesIO(body))
	return opener


class TestCoreIsStopped:
	NOT_RUNNING = json.dumps({"result": "error", "message": "Home Assistant is not running",
	                          "error_key": "homeassistant_not_running_error"}).encode()

	def test_the_supervisors_not_running_answer_is_stopped(self):
		"""The exact body a stopped Core gave on the staging standby."""
		assert cs.core_is_stopped(http_error(400, self.NOT_RUNNING)) is True

	def test_core_stats_answering_is_running(self):
		ok = json.dumps({"result": "ok", "data": {"cpu_percent": 1.0}}).encode()
		assert cs.core_is_stopped(lambda req, timeout=None: FakeResponse(200, ok)) is False

	def test_other_errors_count_as_running(self):
		"""403 is what an add-on without hassio_role: homeassistant gets."""
		assert cs.core_is_stopped(http_error(403, b'{"result":"error"}')) is False
		assert cs.core_is_stopped(http_error(400, b'{"error_key":"something_else"}')) is False
		assert cs.core_is_stopped(http_error(502, b"Bad Gateway")) is False

	def test_supervisor_unreachable_is_treated_as_running(self):
		def down(req, timeout=None):
			raise urllib.error.URLError("no route")
		assert cs.core_is_stopped(down) is False


class FakePortal:
	def __init__(self, role, blob=b"", meta=None):
		self._role = role
		self.blob = blob
		self.meta = meta or {}
		self.uploads = []
		self.reports = []

	def role(self, primary_reachable=None, edge_reachable=None):
		self.reported = primary_reachable
		self.reported_edge = edge_reachable
		return self._role

	def upload(self, blob, meta):
		self.uploads.append(meta)
		return {"id": "snap-2"}

	def download(self):
		return self.blob, self.meta

	def report_applied(self, snapshot_id, ok, detail="", needs_core_version=""):
		self.reports.append((snapshot_id, ok, detail))
		self.needs = needs_core_version


class TestRunOnce:
	def test_silent_portal_changes_nothing(self, tmp_path):
		standby = make_config(tmp_path / "standby")
		outcome, _ = cs.run_once(FakePortal(None), standby, tmp_path,
		                         core_stopped=lambda: True, local_version=lambda: "2026.9.1")
		assert "unreachable" in outcome

	def test_active_uploads_only_what_changed(self, tmp_path):
		active = make_config(tmp_path / "active")
		_, meta = cs.build_snapshot(active)
		import time as _t
		same = FakePortal({"role": "active", "latest": {"sha256": meta["sha256"],
		                                                "created_at": _t.time()}})
		cs.run_once(same, active, tmp_path)
		assert same.uploads == []

		changed = FakePortal({"role": "active", "latest": {"sha256": "older"}})
		cs.run_once(changed, active, tmp_path)
		assert [u["sha256"] for u in changed.uploads] == [meta["sha256"]]

	def _standby_case(self, tmp_path, snapshot_version="2026.9.1"):
		active = make_config(tmp_path / "active", version=snapshot_version)
		(active / "automations.yaml").write_text("- id: synced\n")
		blob, meta = cs.build_snapshot(active)
		standby = make_config(tmp_path / "standby")
		data = tmp_path / "data"
		data.mkdir()
		portal = FakePortal(
			{"role": "standby", "latest": {"id": "snap-1", "sha256": meta["sha256"],
			                                "ha_version": snapshot_version}},
			blob, {"id": "snap-1", "sha256": meta["sha256"]},
		)
		return portal, standby, data

	def test_a_seeded_standby_stops_the_add_ons_in_the_same_pass(self, tmp_path, monkeypatch):
		"""Live on the rig the seeded Matter Server ran ~10 minutes, until the
		next check-in; the restore now ends with them stopped."""
		portal, standby, data = self._standby_case(tmp_path)
		portal._role["seed_restore"] = {"id": "r1"}
		portal.report_seed = lambda *a, **k: None
		monkeypatch.setattr(cs, "restore_seed", lambda p, sid: ("seed restored", ["core_matter_server"]))
		calls = []
		def sup(method, path, body=None, timeout=60):
			calls.append((method, path))
			return 200, {"data": {"state": "started"}}
		monkeypatch.setattr(cs, "_supervisor_call", sup)
		cs.run_once(portal, standby, data, core_stopped=lambda: True, local_version=lambda: "2026.9.1")
		assert ("POST", "/addons/core_matter_server/stop") in calls

	def test_standby_never_writes_under_a_running_core(self, tmp_path):
		portal, standby, data = self._standby_case(tmp_path)
		outcome, _ = cs.run_once(portal, standby, data,
		                         core_stopped=lambda: False, local_version=lambda: "2026.9.1")
		assert "Core is running" in outcome
		assert (standby / "automations.yaml").read_text() == "[]\n"
		assert portal.reports == []

	def test_standby_applies_once_and_reports(self, tmp_path):
		portal, standby, data = self._standby_case(tmp_path)
		kw = dict(core_stopped=lambda: True, local_version=lambda: "2026.9.1")
		cs.run_once(portal, standby, data, **kw)
		assert (standby / "automations.yaml").read_text() == "- id: synced\n"
		assert portal.reports[0][:2] == ("snap-1", True)

		outcome, _ = cs.run_once(portal, standby, data, **kw)
		assert "already in step" in outcome
		assert len(portal.reports) == 1

	def test_content_hash_matches_what_the_active_side_computed(self, tmp_path):
		active = make_config(tmp_path / "active")
		blob, meta = cs.build_snapshot(active)
		assert cs.content_hash(blob) == meta["sha256"]

	def test_standby_does_not_apply_bytes_that_miss_their_hash(self, tmp_path):
		portal, standby, data = self._standby_case(tmp_path)
		other = make_config(tmp_path / "other")
		(other / "automations.yaml").write_text("- id: not what was announced\n")
		portal.blob, _ = cs.build_snapshot(other)
		outcome, _ = cs.run_once(portal, standby, data,
		                         core_stopped=lambda: True, local_version=lambda: "2026.9.1")
		assert "did not match" in outcome
		assert (standby / "automations.yaml").read_text() == "[]\n"
		assert portal.reports == []

	def test_standby_on_an_older_core_refuses_and_says_why(self, tmp_path):
		portal, standby, data = self._standby_case(tmp_path, snapshot_version="2026.10.0")
		cs.run_once(portal, standby, data,
		            core_stopped=lambda: True, local_version=lambda: "2026.9.1")
		assert (standby / "automations.yaml").read_text() == "[]\n"
		assert portal.reports[0][1] is False
		assert "update Core" in portal.reports[0][2]
		assert portal.needs == "2026.10.0"

	def test_unknown_role_does_nothing(self, tmp_path):
		portal, standby, data = self._standby_case(tmp_path)
		portal._role = {"role": "none"}
		cs.run_once(portal, standby, data, core_stopped=lambda: True,
		            local_version=lambda: "2026.9.1")
		assert portal.reports == [] and portal.uploads == []


class TestPairing:
	def _opener(self, calls, answer=None, error=None):
		def opener(req, timeout=None):
			calls.append((req.full_url, json.loads(req.data)))
			if error:
				raise urllib.error.HTTPError(req.full_url, error, "err", {}, io.BytesIO(b"{}"))
			return FakeResponse(200, json.dumps(answer).encode())
		return opener

	def test_a_code_is_redeemed_once_and_replaced_by_the_credential(self, tmp_path):
		cs.save_json(tmp_path / "chap.json", {"portal_url": "https://p.example/",
		                                      "pairing_code": "vcp_sb.abc"})
		cs.save_json(tmp_path / "chap_state.json", {"applied_sha256": "from the seed"})
		calls = []
		out = cs.redeem_pairing(tmp_path, self._opener(
			calls, {"server_id": "sb", "secret": "vcs_sb.xyz"}))
		assert out == "paired as sb"
		assert calls == [("https://p.example/api/sync/chap/config/pair", {"code": "vcp_sb.abc"})]
		binding = cs.load_binding(tmp_path)
		assert binding == {"portal_url": "https://p.example", "server_id": "sb", "token": "vcs_sb.xyz"}
		assert cs.load_json(tmp_path / "chap_state.json") == {}
		# Nothing left to redeem.
		assert cs.redeem_pairing(tmp_path, self._opener(calls)) is None
		assert len(calls) == 1

	def test_a_refused_code_is_dropped_rather_than_retried_forever(self, tmp_path):
		cs.save_json(tmp_path / "chap.json", {"portal_url": "https://p.example",
		                                      "pairing_code": "vcp_sb.spent"})
		out = cs.redeem_pairing(tmp_path, self._opener([], error=403))
		assert "new one" in out
		assert "pairing_code" not in cs.load_json(tmp_path / "chap.json")
		assert cs.load_binding(tmp_path) is None

	def test_a_portal_outage_keeps_the_code_for_the_next_pass(self, tmp_path):
		cs.save_json(tmp_path / "chap.json", {"portal_url": "https://p.example",
		                                      "pairing_code": "vcp_sb.abc"})
		out = cs.redeem_pairing(tmp_path, self._opener([], error=503))
		assert "retry" in out
		assert cs.load_json(tmp_path / "chap.json")["pairing_code"] == "vcp_sb.abc"


class TestVolatileFiles:
	"""core.restore_state is rewritten by Core every ~15 min regardless."""

	def test_restore_state_travels_but_is_not_a_change(self, tmp_path):
		root = make_config(tmp_path / "config")
		(root / ".storage" / "core.restore_state").write_text('{"v": 1}')
		blob_a, a = cs.build_snapshot(root)
		(root / ".storage" / "core.restore_state").write_text('{"v": 2}')
		blob_b, b = cs.build_snapshot(root)
		assert a["sha256"] == b["sha256"]
		with tarfile.open(fileobj=io.BytesIO(blob_b), mode="r:gz") as tar:
			assert tar.extractfile(".storage/core.restore_state").read() == b'{"v": 2}'
		assert cs.content_hash(blob_b) == b["sha256"]

	def test_active_refreshes_an_unchanged_config_after_a_while(self, tmp_path):
		import time as _t
		active = make_config(tmp_path / "active")
		_, meta = cs.build_snapshot(active)
		fresh = FakePortal({"role": "active", "latest": {"sha256": meta["sha256"],
		                                                 "created_at": _t.time() - 60}})
		cs.run_once(fresh, active, tmp_path)
		assert fresh.uploads == []
		stale = FakePortal({"role": "active", "latest": {"sha256": meta["sha256"],
		                                                 "created_at": _t.time() - cs.REFRESH_SECONDS - 1}})
		cs.run_once(stale, active, tmp_path)
		assert len(stale.uploads) == 1

	def test_standby_applies_a_refresh_with_the_same_hash(self, tmp_path):
		active = make_config(tmp_path / "active")
		(active / ".storage" / "core.restore_state").write_text('{"v": 2}')
		blob, meta = cs.build_snapshot(active)
		standby = make_config(tmp_path / "standby")
		data = tmp_path / "data"
		data.mkdir()
		cs.save_json(data / cs.STATE_FILE, {"applied_id": "snap-1", "applied_sha256": meta["sha256"]})
		portal = FakePortal({"role": "standby", "latest": {"id": "snap-2", "sha256": meta["sha256"],
		                                                   "ha_version": "2026.9.1"}},
		                    blob, {"id": "snap-2"})
		out, _ = cs.run_once(portal, standby, data, core_stopped=lambda: True,
		                     local_version=lambda: "2026.9.1")
		assert "applied snapshot snap-2" in out
		assert (standby / ".storage" / "core.restore_state").read_text() == '{"v": 2}'


class TestUploadNow:
	def test_a_handback_gets_a_snapshot_even_when_nothing_changed(self, tmp_path):
		import time as _t
		active = make_config(tmp_path / "active")
		_, meta = cs.build_snapshot(active)
		portal = FakePortal({"role": "active", "upload_now": True,
		                     "latest": {"sha256": meta["sha256"], "created_at": _t.time()}})
		cs.run_once(portal, active, tmp_path)
		assert [u["sha256"] for u in portal.uploads] == [meta["sha256"]]


class TestCoreDirective:
	"""Only a home behind the relay gets these; see portal core_directive."""

	def _run(self, tmp_path, directive, stopped, state=None):
		calls = []
		data = tmp_path / "data"
		data.mkdir(exist_ok=True)
		if state is not None:
			cs.save_json(data / cs.STATE_FILE, state)
		standby = tmp_path / "cfg"
		if not standby.exists():
			make_config(standby)
		portal = FakePortal({"role": "standby", "core": directive, "latest": {}})
		cs.run_once(portal, standby, data, core_stopped=lambda: stopped,
		            local_version=lambda: "2026.9.1",
		            set_running=lambda running: calls.append(running) or True)
		return calls, cs.load_json(data / cs.STATE_FILE)

	def test_stop_stops_a_running_core_once_and_remembers_it(self, tmp_path):
		calls, state = self._run(tmp_path, "stop", stopped=False)
		assert calls == [False] and state["core_stopped_by_vome"] is True
		calls, _ = self._run(tmp_path, "stop", stopped=True)
		assert calls == []

	def test_a_core_already_down_is_taken_over_so_the_handback_can_start_it(self, tmp_path):
		calls, state = self._run(tmp_path, "stop", stopped=True, state={})
		assert calls == [False] and state["core_stopped_by_vome"] is True
		calls, state = self._run(tmp_path, "start", stopped=True)
		assert calls == [True] and state["core_stopped_by_vome"] is False

	def test_start_leaves_a_core_its_owner_stopped_alone(self, tmp_path):
		calls, _ = self._run(tmp_path, "start", stopped=True, state={})
		assert calls == []

	def test_no_instruction_or_no_portal_changes_nothing(self, tmp_path):
		calls, _ = self._run(tmp_path, None, stopped=False, state={"core_stopped_by_vome": True})
		assert calls == []
		data = tmp_path / "data"
		out, _ = cs.run_once(FakePortal(None), tmp_path / "cfg", data,
		                     core_stopped=lambda: True, local_version=lambda: "2026.9.1",
		                     set_running=lambda r: pytest.fail("acted with no portal"))
		assert "unreachable" in out

	def test_a_failed_stop_is_retried_and_not_recorded_as_done(self, tmp_path):
		data = tmp_path / "data"
		data.mkdir()
		make_config(tmp_path / "cfg")
		cs.run_once(FakePortal({"role": "standby", "core": "stop", "latest": {}}), tmp_path / "cfg", data,
		            core_stopped=lambda: False, local_version=lambda: "2026.9.1",
		            set_running=lambda r: False)
		assert not cs.load_json(data / cs.STATE_FILE).get("core_stopped_by_vome")

	def test_re_pairing_keeps_the_fact_that_this_worker_stopped_core(self, tmp_path):
		cs.save_json(tmp_path / "chap.json", {"portal_url": "https://p", "pairing_code": "vcp_x.y"})
		cs.save_json(tmp_path / "chap_state.json", {"core_stopped_by_vome": True, "applied_id": "old"})
		ok = lambda req, timeout=None: FakeResponse(200, json.dumps({"server_id": "x", "secret": "vcs_x.z"}).encode())
		cs.redeem_pairing(tmp_path, ok)
		assert cs.load_json(tmp_path / "chap_state.json") == {"core_stopped_by_vome": True}


class TestSetCoreRunning:
	def _opener(self, calls, fail_options=False):
		def opener(req, timeout=None):
			calls.append((req.get_method(), req.full_url.rsplit("supervisor", 1)[1], json.loads(req.data or b"{}")))
			if fail_options and req.full_url.endswith("/core/options"):
				raise urllib.error.HTTPError(req.full_url, 403, "no", {}, io.BytesIO(b"{}"))
			return FakeResponse(200, b"{}")
		return opener

	def test_stop_turns_boot_off_then_stops(self):
		calls = []
		assert cs.set_core_running(False, self._opener(calls)) is True
		assert calls == [("POST", "/core/options", {"boot": False}), ("POST", "/core/stop", {})]

	def test_start_turns_boot_on_then_starts(self):
		calls = []
		assert cs.set_core_running(True, self._opener(calls)) is True
		assert calls == [("POST", "/core/options", {"boot": True}), ("POST", "/core/start", {})]

	def test_a_refused_boot_flag_stops_there(self):
		calls = []
		assert cs.set_core_running(False, self._opener(calls, fail_options=True)) is False
		assert [c[1] for c in calls] == ["/core/options"]


class TestPairingRace:
	CODE = "vcp_rly-abc123.ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-abcd"

	def test_losing_a_race_does_not_undo_the_winners_pairing(self, tmp_path):
		"""The panel and the worker can both try to redeem one code."""
		cs.save_json(tmp_path / "chap.json", {"portal_url": "https://p", "pairing_code": self.CODE})

		def refused_after_the_other_side_won(req, timeout=None):
			cs.save_json(tmp_path / "chap.json", {"portal_url": "https://p", "server_id": "x",
			                                      "token": "vcs_x.won"})
			raise urllib.error.HTTPError(req.full_url, 403, "spent", {}, io.BytesIO(b"{}"))

		cs.redeem_pairing(tmp_path, refused_after_the_other_side_won)
		assert cs.load_binding(tmp_path)["token"] == "vcs_x.won"



class TestLocalFallback:
	"""This install as the local fallback for a hosted home (reverse mode)."""

	FB = {"probe_url": "https://h.home.vome.io/", "takeover_after": 480}

	def _pass(self, tmp_path, portal, now, reachable, stopped_by_us=True, state=None):
		data = tmp_path / "data"
		data.mkdir(exist_ok=True)
		if state is not None:
			cs.save_json(data / cs.STATE_FILE, state)
		cfg = tmp_path / "cfg"
		if not cfg.exists():
			make_config(cfg)
		started = []
		import unittest.mock as um
		with um.patch.object(cs.time, "time", return_value=now):
			out, _ = cs.run_once(portal, cfg, data, core_stopped=lambda: True,
			                     local_version=lambda: "2026.9.1",
			                     set_running=lambda r: started.append(r) or True,
			                     probe=lambda url: reachable, edge_probe=lambda url: True)
		return out, started, cs.load_json(data / cs.STATE_FILE)

	def test_it_reports_what_it_sees_of_the_hosted_home(self, tmp_path):
		portal = FakePortal({"role": "standby", "fallback": self.FB, "latest": {}})
		self._pass(tmp_path, portal, 1000, True, state={})  # learns the probe URL
		self._pass(tmp_path, portal, 1060, False)
		assert portal.reported is False

	def test_it_also_reports_whether_the_link_is_up(self, tmp_path):
		"""C38: after a stand-down the hosted Core is stopped on purpose, so the
		home never answers; the edge answering is what says the link is back."""
		portal = FakePortal({"role": "active", "fallback": self.FB, "latest": {}})
		self._pass(tmp_path, portal, 1000, True, state={})
		self._pass(tmp_path, portal, 1060, False)
		assert portal.reported is False and portal.reported_edge is True

	def test_with_both_unreachable_long_enough_it_takes_over(self, tmp_path):
		state = {"fallback": self.FB, "portal_ok_at": 1000, "core_stopped_by_vome": True}
		out, started, st = self._pass(tmp_path, FakePortal(None), 1060, False, state=state)
		assert started == [] and "nothing changed" in out
		out, started, st = self._pass(tmp_path, FakePortal(None), 1060 + 480, False)
		assert started == [True] and "took over locally" in out
		assert st["took_over_locally"] and st["core_stopped_by_vome"] is False
		out, started, _ = self._pass(tmp_path, FakePortal(None), 2000, False)
		assert started == []  # once

	def test_a_reachable_hosted_home_means_no_takeover_whatever_the_portal(self, tmp_path):
		"""The portal being down is not the link being down."""
		state = {"fallback": self.FB, "portal_ok_at": 1000, "core_stopped_by_vome": True}
		out, started, _ = self._pass(tmp_path, FakePortal(None), 5000, True, state=state)
		assert started == []

	def test_without_an_anchor_it_never_takes_over_alone(self, tmp_path):
		state = {"fallback": {**self.FB, "takeover_after": None}, "portal_ok_at": 1000,
		         "primary_unreachable_since": 1000, "core_stopped_by_vome": True}
		_, started, _ = self._pass(tmp_path, FakePortal(None), 9000, False, state=state)
		assert started == []

	def test_a_core_it_did_not_stop_is_not_its_to_start(self, tmp_path):
		state = {"fallback": self.FB, "portal_ok_at": 1000,
		         "primary_unreachable_since": 1000, "core_stopped_by_vome": False}
		_, started, _ = self._pass(tmp_path, FakePortal(None), 9000, False, state=state)
		assert started == []

	def test_once_the_portal_answers_its_instruction_rules_again(self, tmp_path):
		"""The portal did not stand the hosted side down: stop again."""
		state = {"fallback": self.FB, "portal_ok_at": 1000, "took_over_locally": 1500,
		         "core_stopped_by_vome": False}
		portal = FakePortal({"role": "standby", "fallback": self.FB, "core": "stop", "latest": {}})
		import unittest.mock as um
		calls = []
		data = tmp_path / "data"; data.mkdir()
		cs.save_json(data / cs.STATE_FILE, state)
		make_config(tmp_path / "cfg")
		with um.patch.object(cs.time, "time", return_value=2000):
			cs.run_once(portal, tmp_path / "cfg", data, core_stopped=lambda: False,
			            local_version=lambda: "2026.9.1",
			            set_running=lambda r: calls.append(r) or True, probe=lambda u: True, edge_probe=lambda u: True)
		st = cs.load_json(data / cs.STATE_FILE)
		assert calls == [False] and "took_over_locally" not in st

	def test_only_home_assistants_own_manifest_counts(self):
		"""Seen on staging: the edge's gate answers 403 with a page of its own."""
		for code in (403, 502, 404):
			def edge(req, timeout=None, code=code):
				raise urllib.error.HTTPError(req.full_url, code, "no", {}, io.BytesIO(b""))
			assert cs.probe_primary("https://h/manifest.json", edge) is False
		html = FakeResponse(200, b"<html>", {"Content-Type": "text/html"})
		assert cs.probe_primary("https://h/manifest.json", lambda r, timeout=None: html) is False
		manifest = FakeResponse(200, b"{}", {"Content-Type": "application/manifest+json; charset=utf-8"})
		assert cs.probe_primary("https://h/manifest.json", lambda r, timeout=None: manifest) is True


class TestRelayPairing:
	"""A home behind the relay gets its code from the portal via /config."""

	def test_a_code_left_in_config_is_moved_to_data_and_removed(self, tmp_path):
		cfg, data = tmp_path / "cfg", tmp_path / "data"
		cfg.mkdir(); data.mkdir()
		cs.save_json(cfg / cs.RELAY_PAIRING_FILE, {"portal_url": "https://staging.vome.io/",
		                                           "pairing_code": "vcp_rly-1.abc"})
		assert cs.collect_relay_pairing(data, cfg) is True
		assert not (cfg / cs.RELAY_PAIRING_FILE).exists()
		assert cs.load_json(data / "chap.json") == {"portal_url": "https://staging.vome.io",
		                                            "pairing_code": "vcp_rly-1.abc"}

	def test_it_is_never_synced(self):
		assert not cs.is_synced(cs.RELAY_PAIRING_FILE)

	def test_a_non_https_portal_is_refused_and_the_file_still_removed(self, tmp_path):
		cfg, data = tmp_path / "cfg", tmp_path / "data"
		cfg.mkdir(); data.mkdir()
		cs.save_json(cfg / cs.RELAY_PAIRING_FILE, {"portal_url": "http://evil", "pairing_code": "vcp_x.y"})
		assert cs.collect_relay_pairing(data, cfg) is False
		assert not (cfg / cs.RELAY_PAIRING_FILE).exists()
		assert not (data / "chap.json").exists()


class TestPollNow:
	def test_a_nudge_cuts_the_wait_short_and_is_consumed(self, tmp_path):
		slept = []
		(tmp_path / cs.POLL_NOW_FILE).write_text("now")
		assert cs.sleep_unless_nudged(300, tmp_path, slept.append) is True
		assert slept == [] and not (tmp_path / cs.POLL_NOW_FILE).exists()

	def test_a_nudge_arriving_mid_wait_is_noticed(self, tmp_path):
		slept = []
		def sleep(s):
			slept.append(s)
			if len(slept) == 3:
				(tmp_path / cs.POLL_NOW_FILE).write_text("now")
		assert cs.sleep_unless_nudged(300, tmp_path, sleep) is True
		assert sum(slept) == 3 * cs.NUDGE_CHECK_SECONDS

	def test_without_one_it_waits_the_full_time(self, tmp_path):
		slept = []
		assert cs.sleep_unless_nudged(10, tmp_path, slept.append) is False
		assert sum(slept) == 10

	def test_it_is_never_synced(self):
		assert not cs.is_synced(cs.POLL_NOW_FILE)


class TestVomePanelHandsOffToChap:
	"""The Vome panel and the Vome CHAP add-on share only /config."""

	CODE = "vcp_rly-abc123.ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-abcd"

	@pytest.fixture
	def panel(self):
		spec = importlib.util.spec_from_file_location("vome_panel_server_chap", ROOT / "vome" / "panel" / "server.py")
		server = importlib.util.module_from_spec(spec)
		spec.loader.exec_module(server)
		return server

	def test_without_the_chap_add_on_it_says_to_install_it(self, panel, tmp_path):
		assert panel.chap_status(str(tmp_path)) == {"installed": False}
		with pytest.raises(LookupError, match="Vome CHAP"):
			panel.chap_leave_pairing_code(self.CODE, "https://vome.io", str(tmp_path))

	def test_a_pasted_code_is_left_for_the_chap_add_on_to_collect(self, panel, tmp_path):
		cs.save_json(tmp_path / cs.STATUS_FILE_NAME, {"paired": False})
		panel.chap_leave_pairing_code(" " + self.CODE, "https://staging.vome.io/", str(tmp_path))
		data = tmp_path / "data"; data.mkdir()
		assert cs.collect_relay_pairing(data, tmp_path) is True
		assert cs.load_json(data / "chap.json")["pairing_code"] == self.CODE

	def test_a_bad_code_or_portal_is_refused(self, panel, tmp_path):
		cs.save_json(tmp_path / cs.STATUS_FILE_NAME, {})
		with pytest.raises(ValueError, match="vcp_"):
			panel.chap_leave_pairing_code("nope", "https://vome.io", str(tmp_path))
		with pytest.raises(ValueError, match="https"):
			panel.chap_leave_pairing_code(self.CODE, "http://vome.io", str(tmp_path))

	def test_the_worker_publishes_status_without_the_credential(self, tmp_path):
		data, cfg = tmp_path / "data", tmp_path / "cfg"
		data.mkdir(); cfg.mkdir()
		cs.save_json(data / "chap.json", {"portal_url": "https://p", "server_id": "x", "token": "vcs_x.secret"})
		cs.publish_status("active: unchanged", data, cfg)
		published = cs.load_json(cfg / cs.STATUS_FILE_NAME)
		assert published["paired"] is True and published["last_outcome"] == "active: unchanged"
		assert "vcs_x.secret" not in json.dumps(published)
		assert not cs.is_synced(cs.STATUS_FILE_NAME)

	def test_the_vome_add_on_asks_for_no_supervisor_role(self):
		import yaml
		vome = yaml.safe_load((ROOT / "vome" / "config.yaml").read_text())
		chap = yaml.safe_load((ROOT / "vome_chap" / "config.yaml").read_text())
		assert "hassio_role" not in vome
		assert chap["hassio_role"] == "manager" and chap["slug"] == "vome_chap"


class TestSeed:
	"""A one-off backup of add-ons and folders fills the standby; it never stays behind."""

	def _fake(self, fail_at=None):
		calls = []
		def call(method, path, body=None, timeout=60):
			calls.append((method, path, body))
			if method == "GET" and path == "/addons":
				if fail_at == "list":
					return 0, None
				return 200, {"data": {"addons": [
					{"slug": "core_mosquitto", "state": "started"}, {"slug": "b1bff62e_vome"},
					{"slug": "b1bff62e_vome_chap"}, {"slug": "jellyfin_spare", "state": "stopped"}]}}
			if method == "POST" and path in ("/backups/new/full", "/backups/new/partial"):
				if fail_at == "create":
					return 500, None
				return 200, {"result": "ok", "data": {"slug": "abc123"}}
			return 200, {"result": "ok"}
		def download(slug, dest):
			if fail_at == "download":
				raise OSError("disk full")
			dest.write_bytes(b"tar")
			return 3
		return calls, call, download

	class _Portal:
		def __init__(self, fail=False):
			self.got, self.fail = [], fail
		def upload_seed(self, request_id, path, size, key):
			if self.fail:
				raise urllib.error.URLError("portal down")
			self.got.append((request_id, path.read_bytes(), size, key))
			return {"ok": True}

	def test_the_seed_is_made_with_a_one_off_key_sent_and_removed(self, tmp_path):
		calls, call, download = self._fake()
		portal = self._Portal()
		out, held = cs.send_seed(portal, "r1", tmp_path, call, download)
		assert out.startswith("seed sent")
		assert held == ["core_mosquitto"]  # not our own add-ons
		(req, data, size, key) = portal.got[0]
		assert req == "r1" and data == b"tar" and len(key) > 30
		made = next(body for m, path, body in calls if path.startswith("/backups/new/"))
		assert made["password"] == key  # the backup is under that key
		assert ("DELETE", "/backups/abc123", None) in calls
		assert not (tmp_path / cs.SEED_FILE).exists()

	def test_the_seed_leaves_out_what_the_standby_would_throw_away(self):
		calls, call, _ = self._fake()
		cs.make_seed_backup("k" * 43, "Vome CHAP seed r1", call)
		(method, path, body), = [c for c in calls if c[1].startswith("/backups/new/")]
		assert path == "/backups/new/partial"
		assert body["homeassistant"] is False  # config comes by sync; the DB is not wanted
		assert body["addons"] == ["core_mosquitto", "b1bff62e_vome"]  # never itself, nor what is stopped
		assert "share" in body["folders"] and "media" in body["folders"]

	def test_a_seed_is_still_made_when_add_ons_cannot_be_listed(self):
		calls, call, _ = self._fake(fail_at="list")
		assert cs.make_seed_backup("k" * 43, "Vome CHAP seed r1", call) == "abc123"
		assert any(path == "/backups/new/full" for _, path, _ in calls)

	def test_a_failed_upload_still_removes_the_backup_everywhere(self, tmp_path):
		calls, call, download = self._fake()
		with pytest.raises(urllib.error.URLError):
			cs.send_seed(self._Portal(fail=True), "r1", tmp_path, call, download)
		assert ("DELETE", "/backups/abc123", None) in calls
		assert not (tmp_path / cs.SEED_FILE).exists()

	def test_each_request_is_sent_once_and_a_failure_waits(self, tmp_path):
		state_path = tmp_path / "state.json"
		sent = []
		ok = lambda portal, rid, data_dir: sent.append(rid) or "seed sent (3 bytes)"
		info = {"seed": {"request": "r1"}}
		state = {}
		assert cs.maybe_send_seed(None, info, state, state_path, 1000, tmp_path, ok).startswith("seed sent")
		assert cs.maybe_send_seed(None, info, state, state_path, 1001, tmp_path, ok) is None
		assert sent == ["r1"]

		def boom(portal, rid, data_dir):
			raise cs.SeedFailed("no backup")
		state = {}
		assert "will retry" in cs.maybe_send_seed(None, {"seed": {"request": "r2"}}, state, state_path, 1000, tmp_path, boom)
		assert cs.maybe_send_seed(None, {"seed": {"request": "r2"}}, state, state_path, 1100, tmp_path, boom) is None
		assert "will retry" in cs.maybe_send_seed(None, {"seed": {"request": "r2"}}, state, state_path,
		                                          1000 + cs.SEED_RETRY_SECONDS + 1, tmp_path, boom)


class TestSeedRestore:
	"""The standby restores the seed's add-ons and folders -- never Core (C34)."""

	class _Portal:
		def __init__(self, key="k" * 43, fail=None):
			self.key, self.fail, self.reports = key, fail, []
		def download_seed(self, seed_id, dest):
			if self.fail == "download":
				raise urllib.error.URLError("portal down")
			dest.write_bytes(b"tar")
			return self.key
		def report_seed(self, seed_id, ok, detail=""):
			self.reports.append((seed_id, ok))

	def _supervisor(self, refuse=False, listed=True):
		calls = []
		def call(method, path, body=None, timeout=60):
			calls.append((method, path, body))
			if path == "/backups":
				return 200, {"data": {"backups": [
					{"slug": "other", "name": "Automatic backup"},
					*([{"slug": "seed1", "name": cs.seed_backup_name("r1")}] if listed else []),
				]}}
			if path == "/backups/seed1/info":
				return 200, {"data": {"addons": [
					{"slug": "core_mosquitto"}, {"slug": "9ca546e0_vome"}, {"slug": "9ca546e0_vome_chap"},
				], "folders": ["share", "ssl"]}}
			if path.endswith("/restore/partial"):
				return (400, {"result": "error"}) if refuse else (200, {"result": "ok"})
			return 200, {"result": "ok"}
		return calls, call

	def test_add_ons_and_folders_only_and_nothing_left_behind(self, tmp_path):
		calls, call = self._supervisor()
		out, restored = cs.restore_seed(self._Portal(), "r1", tmp_path, call)
		assert out.startswith("seed restored") and restored == ["core_mosquitto", "9ca546e0_vome"]
		restore = next(body for m, path, body in calls if path == "/backups/seed1/restore/partial")
		assert restore["homeassistant"] is False  # Core is never started as a copy of the home
		assert restore["addons"] == ["core_mosquitto", "9ca546e0_vome"]  # not this add-on's own pairing
		assert restore["folders"] == ["share", "ssl"]
		assert restore["password"] == "k" * 43
		assert ("DELETE", "/backups/seed1", None) in calls
		assert list(tmp_path.iterdir()) == []

	def test_a_refused_restore_still_removes_it(self, tmp_path):
		calls, call = self._supervisor(refuse=True)
		with pytest.raises(cs.SeedFailed):
			cs.restore_seed(self._Portal(), "r1", tmp_path, call)
		assert ("DELETE", "/backups/seed1", None) in calls
		assert list(tmp_path.iterdir()) == []

	def test_a_seed_the_supervisor_did_not_list_is_not_restored(self, tmp_path):
		calls, call = self._supervisor(listed=False)
		with pytest.raises(cs.SeedFailed):
			cs.restore_seed(self._Portal(), "r1", tmp_path, call)
		assert not any(path.endswith("/restore/partial") for _, path, _ in calls)
		assert list(tmp_path.iterdir()) == []

	def test_once_only_with_core_stopped_and_reported(self, tmp_path):
		state_path, portal = tmp_path / "state.json", self._Portal()
		info = {"seed_restore": {"id": "r1"}}
		done = []
		ok = lambda p, sid: done.append(sid) or "seed restored (2 add-ons, 2 folders)"
		state = {}
		assert cs.maybe_restore_seed(portal, info, state, state_path, 1000, lambda: False, ok) is None
		assert cs.maybe_restore_seed(portal, info, state, state_path, 1000, lambda: True, ok).startswith("seed restored")
		assert cs.maybe_restore_seed(portal, info, state, state_path, 1001, lambda: True, ok) is None
		assert done == ["r1"] and portal.reports == [("r1", True)]

	def test_what_the_seed_restored_is_held_stopped_until_this_side_is_active(self, tmp_path):
		"""Add-ons run without Core: a seeded Matter Server or torrent client
		would otherwise run beside the live home's own."""
		state_path, portal = tmp_path / "state.json", self._Portal()
		restored = lambda p, sid: ("seed restored (2 add-ons, 1 folders)", ["core_matter_server", "x_transmission"])
		state = {}
		cs.maybe_restore_seed(portal, {"seed_restore": {"id": "r1"}}, state, state_path, 1000, lambda: True, restored)
		assert state["held_addons"] == ["core_matter_server", "x_transmission"]

		calls = []
		def call(method, path, body=None, timeout=60):
			calls.append((method, path, body))
			return 200, {"result": "ok"}
		assert cs.enforce_addons("standby", state, state_path, call).startswith("stopped 2")
		assert ("POST", "/addons/x_transmission/stop", None) in calls
		assert ("POST", "/addons/x_transmission/options", {"boot": "manual"}) in calls
		calls.clear()
		assert cs.enforce_addons("standby", state, state_path, call) is None and calls == []  # once
		assert cs.enforce_addons("active", state, state_path, call).startswith("started 2")
		assert ("POST", "/addons/core_matter_server/start", None) in calls
		assert cs.enforce_addons("standby", state, state_path, call).startswith("stopped")  # handed back

	def test_a_failed_stop_is_retried(self, tmp_path):
		state = {"held_addons": ["a"], "held_addons_running": None}
		fails = lambda method, path, body=None, timeout=60: (500, None) if path.endswith("/stop") else (200, {})
		assert "will retry" in cs.enforce_addons("standby", state, tmp_path / "s.json", fails)
		assert state["held_addons_running"] is None

	def test_the_sender_holds_the_same_add_ons(self, tmp_path):
		"""Symmetric: after a seed the same add-ons exist on both sides, and
		each side runs them only while it is the active one."""
		state = {}
		sent = lambda portal, rid, data_dir: ("seed sent (3 bytes)", ["core_matter_server"])
		cs.maybe_send_seed(None, {"seed": {"request": "r1"}}, state, tmp_path / "s.json", 1000, tmp_path, sent)
		assert state["held_addons"] == ["core_matter_server"] and state["held_addons_running"] is None
		ok = lambda method, path, body=None, timeout=60: (200, {})
		assert cs.enforce_addons("active", state, tmp_path / "s.json", ok).startswith("started")

	def test_our_own_add_ons_are_never_held(self, tmp_path):
		restored = lambda p, sid: ("seed restored", ["b1bff62e_vome", "core_mosquitto"])
		state = {}
		cs.maybe_restore_seed(self._Portal(), {"seed_restore": {"id": "r9"}}, state, tmp_path / "s.json",
		                      1000, lambda: True, restored)
		assert state["held_addons"] == ["core_mosquitto"]

	def test_an_add_on_already_in_that_state_is_left_alone(self, tmp_path):
		calls = []
		def call(method, path, body=None, timeout=60):
			calls.append((method, path))
			if path.endswith("/info"):
				return 200, {"data": {"state": "started"}}
			return 400, None  # Supervisor: already running
		state = {"held_addons": ["a"], "held_addons_running": None}
		assert cs.enforce_addons("active", state, tmp_path / "s.json", call).startswith("started")
		assert ("POST", "/addons/a/start") not in calls

	def test_nothing_held_nothing_touched(self, tmp_path):
		boom = lambda *a, **k: pytest.fail("no Supervisor call without held add-ons")
		assert cs.enforce_addons("standby", {}, tmp_path / "s.json", boom) is None

	def test_a_network_failure_retries_and_a_refusal_is_reported(self, tmp_path):
		state_path, portal = tmp_path / "state.json", self._Portal()
		def offline(p, sid):
			raise urllib.error.URLError("down")
		state = {}
		info = {"seed_restore": {"id": "r2"}}
		assert "will retry" in cs.maybe_restore_seed(portal, info, state, state_path, 1000, lambda: True, offline)
		assert cs.maybe_restore_seed(portal, info, state, state_path, 1100, lambda: True, offline) is None
		assert portal.reports == []
		def refused(p, sid):
			raise cs.SeedFailed("bad key")
		out = cs.maybe_restore_seed(portal, info, state, state_path,
		                            1000 + cs.SEED_RETRY_SECONDS + 1, lambda: True, refused)
		assert "not restored" in out and portal.reports == [("r2", False)]


class TestProbeEdge:
	"""Any answer from Vome's edge is the link; only no answer is not."""

	def test_a_502_or_a_gate_is_the_link_up(self):
		for code in (502, 403, 404):
			assert cs.probe_edge("https://h.home.vome.io/manifest.json", http_error(code, b"")) is True

	def test_a_home_answering_is_the_link_up(self):
		ok = lambda req, timeout=None: FakeResponse(200, b"{}")
		assert cs.probe_edge("https://h/", ok) is True

	def test_no_answer_is_the_link_down(self):
		def down(req, timeout=None):
			raise urllib.error.URLError("no route to host")
		assert cs.probe_edge("https://h/", down) is False

	def test_the_edge_is_only_asked_when_the_home_did_not_answer(self):
		asked = []
		state = {"fallback": {"probe_url": "https://h/"}}
		cs.watch_primary(state, 1000, lambda u: True, lambda u: asked.append(u) or False)
		assert state["edge_reachable"] is True and asked == []
		cs.watch_primary(state, 1060, lambda u: False, lambda u: asked.append(u) or True)
		assert state["edge_reachable"] is True and state["primary_reachable"] is False and asked == ["https://h/"]
