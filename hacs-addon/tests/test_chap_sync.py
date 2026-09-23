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
	"vome_chap_sync", ROOT / "vome" / "panel" / "chap_sync.py"
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

	def role(self):
		return self._role

	def upload(self, blob, meta):
		self.uploads.append(meta)
		return {"id": "snap-2"}

	def download(self):
		return self.blob, self.meta

	def report_applied(self, snapshot_id, ok, detail=""):
		self.reports.append((snapshot_id, ok, detail))


class TestRunOnce:
	def test_silent_portal_changes_nothing(self, tmp_path):
		standby = make_config(tmp_path / "standby")
		outcome, _ = cs.run_once(FakePortal(None), standby, tmp_path,
		                         core_stopped=lambda: True, local_version=lambda: "2026.9.1")
		assert "unreachable" in outcome

	def test_active_uploads_only_what_changed(self, tmp_path):
		active = make_config(tmp_path / "active")
		_, meta = cs.build_snapshot(active)
		same = FakePortal({"role": "active", "latest": {"sha256": meta["sha256"]}})
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
		assert not (tmp_path / "chap_state.json").exists()
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
