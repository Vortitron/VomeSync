#!/usr/bin/env python3
"""CHAP config sync — keep two Home Assistant installs in step.

A CHAP pair is two installs of the same home: the *active* one running, the
*standby* one with its Core stopped (inert), ready to take over. They are
seeded once with a full restore; after that this worker keeps them in step
(docs/chap_plan.md §10 in the portal repo):

- on the **active** side it snapshots the configuration — ``.storage/``, the
  YAML, custom components, dashboards — and uploads it to the portal when it
  has changed;
- on the **standby** side it fetches the newest snapshot and writes it into
  ``/config``, but **only while that install's Core is stopped**. Nothing
  reads the files until Core starts at takeover, so a half-written copy is
  never loaded and the running side's files are never touched.

It runs in the add-on, not in the integration, because the integration lives
inside Core — and on the side that receives, Core is off.

Which side this install is comes from the portal on every poll, never from
anything in ``/config``: the seed restore clones ``/config`` (and this
add-on's own data), so both installs would otherwise believe they are the
same one. The per-install binding is ``/data/chap.json`` — written by the
portal into a hosted install after the seed, or by pairing on a local one.

Silence changes nothing. If the portal cannot be reached, the worker neither
uploads nor applies; it never guesses its role.

Stdlib only: the add-on image is built without network access.
"""
from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import re
import shutil
import sqlite3
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

LOG = logging.getLogger("vome-chap-sync")

CONFIG_DIR = Path(os.environ.get("VOME_CHAP_CONFIG_DIR", "/homeassistant"))
DATA_DIR = Path(os.environ.get("VOME_CHAP_DATA_DIR", "/data"))
SUPERVISOR = os.environ.get("VOME_SUPERVISOR_URL", "http://supervisor")
SUPERVISOR_TOKEN = os.environ.get("SUPERVISOR_TOKEN", "")

BINDING_FILE = "chap.json"
STATE_FILE = "chap_state.json"
# Inside /config rather than /data: a rename across the two mounts fails
# (EXDEV), and the whole point is that each file lands with one rename.
STAGING_NAME = ".vome_chap_staging"

API_ROLE = "/api/sync/chap/config/role"
API_SNAPSHOT = "/api/sync/chap/config/snapshot"
API_APPLIED = "/api/sync/chap/config/applied"
API_PAIR = "/api/sync/chap/config/pair"

DEFAULT_INTERVAL = 300
# Files Core rewrites on a timer whatever anyone does. They travel in every
# snapshot but do not count as a change, or the active side would re-send the
# whole config every pass (core.restore_state: every ~15 min, measured on the
# staging pair — ~19 MB each time). REFRESH_SECONDS bounds how stale they get.
VOLATILE = frozenset({".storage/core.restore_state"})
REFRESH_SECONDS = 6 * 3600
IDLE_INTERVAL = 60
MAX_SNAPSHOT_BYTES = 256 * 1024 * 1024

ROLE_ACTIVE = "active"
ROLE_STANDBY = "standby"

# ── What is synced ────────────────────────────────────────────────────────
#
# Everything under /config except what is local to one machine, rebuilt on
# start, or too big and too live to copy. The same rules decide what is
# uploaded and what the standby may delete, so a file the active side never
# sends is never removed from the standby either.

# Top-level entries skipped entirely.
EXCLUDED_TOP = frozenset({
	"backups",            # backup tars: large, and not configuration
	"tts",                # speech cache, regenerated
	"deps",               # pip installs, rebuilt for the local machine
	".cache",
	".vome_chap_staging", # our own staging area
	".ha_run.lock",       # the running Core's lock file
	".HA_RESTART",
})

# Top-level file-name patterns skipped (recorder database and logs).
EXCLUDED_TOP_PATTERNS = (
	re.compile(r"^home-assistant_v2\.db"),  # history: large, live; not config
	re.compile(r"^home-assistant\.log"),
	re.compile(r"^OZW_Log\.txt$"),
)

# Anywhere in the tree.
EXCLUDED_ANYWHERE_DIRS = frozenset({"__pycache__", ".git"})
EXCLUDED_SUFFIXES = (".pyc", ".db-wal", ".db-shm", ".db-journal", ".tmp", ".log")


def is_synced(rel: str) -> bool:
	"""Is this path (relative to /config, POSIX separators) part of a snapshot?"""
	parts = rel.split("/")
	if not parts or parts[0] in ("", ".", ".."):
		return False
	top = parts[0]
	if top in EXCLUDED_TOP:
		return False
	if len(parts) == 1 and any(p.search(top) for p in EXCLUDED_TOP_PATTERNS):
		return False
	if any(p in EXCLUDED_ANYWHERE_DIRS for p in parts[:-1]):
		return False
	if parts[-1].endswith(EXCLUDED_SUFFIXES):
		return False
	return True


def synced_files(config_dir: Path) -> list[str]:
	"""Every regular file under config_dir that belongs in a snapshot, sorted.

	Symlinks are skipped: HA does not create them, and following one out of
	/config is how a snapshot would carry something it should not.
	"""
	out = []
	root = str(config_dir)
	for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
		rel_dir = os.path.relpath(dirpath, root)
		rel_dir = "" if rel_dir == "." else rel_dir.replace(os.sep, "/")
		# Prune excluded directories so their contents are never walked.
		keep = []
		for d in dirnames:
			rel = f"{rel_dir}/{d}" if rel_dir else d
			if os.path.islink(os.path.join(dirpath, d)):
				continue
			if d in EXCLUDED_ANYWHERE_DIRS or (not rel_dir and d in EXCLUDED_TOP):
				continue
			keep.append(d)
		dirnames[:] = keep
		for f in filenames:
			rel = f"{rel_dir}/{f}" if rel_dir else f
			full = os.path.join(dirpath, f)
			if os.path.islink(full) or not os.path.isfile(full):
				continue
			if is_synced(rel):
				out.append(rel)
	return sorted(out)


# ── Versions ──────────────────────────────────────────────────────────────

_VERSION_RE = re.compile(r"^(\d+)\.(\d+)(?:\.(\d+))?")


def parse_version(text: str) -> Optional[tuple[int, int, int]]:
	m = _VERSION_RE.match((text or "").strip())
	if not m:
		return None
	return int(m.group(1)), int(m.group(2)), int(m.group(3) or 0)


def version_blocker(snapshot_version: str, local_version: str) -> Optional[str]:
	"""Why a snapshot must not be applied under this Core version, or None.

	A newer Core migrates older ``.storage`` forward on start; an older Core
	handed newer ``.storage`` can refuse it or lose data. So the standby's
	Core must be at least the snapshot's version.
	"""
	snap = parse_version(snapshot_version)
	local = parse_version(local_version)
	if snap is None or local is None:
		if (snapshot_version or "").strip() == (local_version or "").strip():
			return None
		return (f"cannot compare Home Assistant versions "
		        f"(snapshot {snapshot_version!r}, this install {local_version!r})")
	if snap > local:
		return (f"snapshot is from Home Assistant {snapshot_version.strip()}, "
		        f"newer than this install's {local_version.strip()}; update Core first")
	return None


# ── Build (active side) ───────────────────────────────────────────────────

def _read_for_snapshot(full: Path) -> bytes:
	"""File bytes — through SQLite's backup API for a database.

	Copying a live SQLite file byte-for-byte (``zigbee.db`` while ZHA runs)
	can capture a torn page; the backup API gives a consistent copy.
	"""
	if full.suffix == ".db":
		try:
			with tempfile.TemporaryDirectory() as tmp:
				dest_path = os.path.join(tmp, "copy.db")
				src = sqlite3.connect(f"file:{full}?mode=ro", uri=True)
				try:
					dest = sqlite3.connect(dest_path)
					try:
						src.backup(dest)
					finally:
						dest.close()
				finally:
					src.close()
				return Path(dest_path).read_bytes()
		except sqlite3.DatabaseError:
			pass  # not actually SQLite: copy the bytes as they are
	return full.read_bytes()


def build_snapshot(config_dir: Path) -> tuple[bytes, dict]:
	"""A gzip tar of the synced files plus its metadata.

	``sha256`` is over (path, bytes) of every file except :data:`VOLATILE`,
	not over the tar, so an unchanged config hashes the same however tar and
	gzip stamp their headers — which is what lets the active side skip
	uploading nothing new.
	"""
	files = synced_files(config_dir)
	hasher = hashlib.sha256()
	buf = io.BytesIO()
	with tarfile.open(fileobj=buf, mode="w:gz") as tar:
		for rel in files:
			full = config_dir / rel
			try:
				data = _read_for_snapshot(full)
			except OSError:
				continue  # removed between the walk and the read
			if rel not in VOLATILE:
				hasher.update(rel.encode("utf-8") + b"\0")
				hasher.update(hashlib.sha256(data).digest())
			info = tarfile.TarInfo(rel)
			info.size = len(data)
			info.mode = 0o644
			info.mtime = int(full.stat().st_mtime) if full.exists() else int(time.time())
			tar.addfile(info, io.BytesIO(data))
	ha_version = ""
	try:
		ha_version = (config_dir / ".HA_VERSION").read_text().strip()
	except OSError:
		pass
	return buf.getvalue(), {
		"sha256": hasher.hexdigest(),
		"ha_version": ha_version,
		"file_count": len(files),
	}


def content_hash(blob: bytes) -> str:
	"""The ``sha256`` :func:`build_snapshot` gives, recomputed from the tar.

	Lets the standby check it received exactly what the active side hashed —
	not a corrupted transfer, and not a newer snapshot that replaced the one
	the portal named between the role check and the download.
	"""
	entries = []
	with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tar:
		for m in tar.getmembers():
			if m.isfile() and m.name not in VOLATILE:
				entries.append((m.name, hashlib.sha256(tar.extractfile(m).read()).digest()))
	hasher = hashlib.sha256()
	for name, digest in sorted(entries):
		hasher.update(name.encode("utf-8") + b"\0")
		hasher.update(digest)
	return hasher.hexdigest()


# ── Apply (standby side) ──────────────────────────────────────────────────

# Every Home Assistant config has these; a snapshot without them is not one.
REQUIRED_IN_SNAPSHOT = (".storage/core.config_entries", ".HA_VERSION")


class ApplyRefused(Exception):
	"""The snapshot was not applied; the message says why, for the portal."""


def _safe_members(tar: tarfile.TarFile) -> list[tarfile.TarInfo]:
	members = []
	for m in tar.getmembers():
		name = m.name
		if not m.isfile():
			raise ApplyRefused(f"snapshot holds a non-file entry: {name}")
		if name.startswith("/") or "\\" in name or ".." in name.split("/"):
			raise ApplyRefused(f"unsafe path in snapshot: {name}")
		if not is_synced(name):
			raise ApplyRefused(f"snapshot holds a path this side does not sync: {name}")
		members.append(m)
	names = {m.name for m in members}
	missing = [n for n in REQUIRED_IN_SNAPSHOT if n not in names]
	if missing:
		# Mirroring deletes what the snapshot lacks, so a truncated or empty
		# snapshot would wipe the standby. Refuse anything that is plainly
		# not a whole Home Assistant configuration.
		raise ApplyRefused(f"snapshot is incomplete (no {', '.join(missing)})")
	return members


def apply_snapshot(config_dir: Path, blob: bytes) -> dict:
	"""Make config_dir's synced files exactly the snapshot's.

	Each file is staged, then moved into place with one rename, so no file is
	ever half-written. Synced files the snapshot does not contain are removed
	— an automation deleted on the active side must not come back at takeover.
	Anything outside the synced set (the recorder database, logs, backups) is
	never touched.

	The caller must have checked that Core is stopped.
	"""
	staging_root = config_dir / STAGING_NAME
	shutil.rmtree(staging_root, ignore_errors=True)
	staging_root.mkdir(parents=True)
	try:
		with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tar:
			members = _safe_members(tar)
			for m in members:
				src = tar.extractfile(m)
				dest = staging_root / m.name
				dest.parent.mkdir(parents=True, exist_ok=True)
				with open(dest, "wb") as out:
					shutil.copyfileobj(src, out)
		incoming = {m.name for m in members}

		for rel in sorted(incoming):
			target = config_dir / rel
			target.parent.mkdir(parents=True, exist_ok=True)
			os.replace(staging_root / rel, target)

		removed = 0
		for rel in synced_files(config_dir):
			if rel not in incoming:
				try:
					(config_dir / rel).unlink()
					removed += 1
				except OSError:
					LOG.warning("Could not remove %s", rel)
		return {"written": len(incoming), "removed": removed}
	finally:
		shutil.rmtree(staging_root, ignore_errors=True)


# ── Supervisor ────────────────────────────────────────────────────────────

def _supervisor_get(path: str, opener=urllib.request.urlopen) -> tuple[int, Any]:
	req = urllib.request.Request(
		SUPERVISOR.rstrip("/") + path,
		headers={"Authorization": f"Bearer {SUPERVISOR_TOKEN}"},
	)
	try:
		with opener(req, timeout=15) as resp:
			raw = resp.read()
			status = resp.status
	except urllib.error.HTTPError as err:
		status = err.code
		try:
			raw = err.read()
		except OSError:
			raw = b""
	except (urllib.error.URLError, OSError):
		return 0, None
	try:
		return status, json.loads(raw.decode("utf-8")) if raw else None
	except ValueError:
		return status, None


def core_is_stopped(opener=urllib.request.urlopen) -> bool:
	"""True only when the Supervisor positively says Core is not running.

	``/core/stats`` answers 400 with ``homeassistant_not_running_error``
	while Core is stopped (seen on HAOS 2026.9 with Supervisor's own CLI).
	Anything else — stats, another error, a refusal because the add-on's
	``hassio_role`` does not reach ``/core/stats``, no Supervisor at all —
	counts as running, because writing under a running Core is the one thing
	this worker must never do.
	"""
	status, body = _supervisor_get("/core/stats", opener)
	return (status == 400 and isinstance(body, dict)
	        and body.get("error_key") == "homeassistant_not_running_error")


def _supervisor_post(path: str, body: Optional[dict] = None,
                     opener=urllib.request.urlopen) -> int:
	req = urllib.request.Request(
		SUPERVISOR.rstrip("/") + path, method="POST",
		data=json.dumps(body or {}).encode(),
		headers={"Authorization": f"Bearer {SUPERVISOR_TOKEN}", "Content-Type": "application/json"},
	)
	try:
		# Stopping or starting Core can take a minute or two.
		with opener(req, timeout=300) as resp:
			return resp.status
	except urllib.error.HTTPError as err:
		return err.code
	except (urllib.error.URLError, OSError):
		return 0


def set_core_running(running: bool, opener=urllib.request.urlopen) -> bool:
	"""Start or stop Core, with the boot flag to match (as the portal does
	for a hosted standby), so a reboot mid-failover does not bring it back."""
	if _supervisor_post("/core/options", {"boot": running}, opener) != 200:
		return False
	return _supervisor_post("/core/start" if running else "/core/stop", None, opener) == 200


def enforce_core(directive, state: dict, state_path: Path,
                 core_stopped: Callable[[], bool],
                 set_running: Callable[[bool], bool] = set_core_running) -> Optional[str]:
	"""Act on the portal's instruction for this install's own Core.

	Only ever sent to a home the portal cannot reach itself, and only in two
	forms. ``stop`` while a failover is active: one brain at a time, and a
	stopped Core is what lets this worker bring the home up to date for the
	handback. ``start`` afterwards — acted on only if this worker was the one
	that stopped it, so a Core its owner stopped stays stopped.

	The instruction arrives with every successful poll; when the portal
	cannot be reached nothing arrives and nothing changes.
	"""
	stopped_by_us = bool(state.get("core_stopped_by_vome"))
	if directive == "stop":
		if stopped_by_us and core_stopped():
			return None
		# Take it over even if Core is already down (it crashed, say): the
		# home is to stay inert until the handback, and then it is ours to
		# start again. set_running(False) also turns boot off.
		if not set_running(False):
			return "could not stop Core for the failover; will retry"
		state["core_stopped_by_vome"] = True
		save_json(state_path, state)
		return "stopped Core: the hosted standby is the active install"
	if directive == "start" and stopped_by_us:
		if not set_running(True):
			return "could not start Core after the handback; will retry"
		state["core_stopped_by_vome"] = False
		save_json(state_path, state)
		return "started Core: this install is the active one again"
	return None


def core_version(opener=urllib.request.urlopen) -> str:
	status, body = _supervisor_get("/core/info", opener)
	if status == 200 and isinstance(body, dict):
		return str((body.get("data") or {}).get("version") or "")
	return ""


# ── Portal ────────────────────────────────────────────────────────────────

def load_json(path: Path) -> dict:
	try:
		with open(path, encoding="utf-8") as fh:
			data = json.load(fh)
		return data if isinstance(data, dict) else {}
	except (OSError, ValueError):
		return {}


def save_json(path: Path, data: dict) -> None:
	tmp = path.with_suffix(path.suffix + ".tmp")
	with open(tmp, "w", encoding="utf-8") as fh:
		json.dump(data, fh)
	os.chmod(tmp, 0o600)
	os.replace(tmp, path)


def load_binding(data_dir: Path = DATA_DIR) -> Optional[dict]:
	"""This install's CHAP identity, or None when it is not in a pair."""
	b = load_json(data_dir / BINDING_FILE)
	if b.get("portal_url") and b.get("server_id") and b.get("token"):
		return b
	return None


def redeem_pairing(data_dir: Path = DATA_DIR, opener=urllib.request.urlopen) -> Optional[str]:
	"""Swap a pairing code in /data/chap.json for this install's credential.

	The portal (over the guest agent) or the owner (in the panel) leaves
	``{"portal_url", "pairing_code"}``; the code is single use, so the file is
	rewritten with the credential the moment it is redeemed. Returns what
	happened, or None when there was no code to redeem.
	"""
	path = data_dir / BINDING_FILE
	b = load_json(path)
	code = b.get("pairing_code")
	portal_url = (b.get("portal_url") or "").rstrip("/")
	if not code or not portal_url:
		return None
	req = urllib.request.Request(
		portal_url + API_PAIR, data=json.dumps({"code": code}).encode(),
		headers={"Content-Type": "application/json"}, method="POST",
	)
	try:
		with opener(req, timeout=30) as resp:
			got = json.loads(resp.read().decode("utf-8"))
	except urllib.error.HTTPError as exc:
		if exc.code in (400, 401, 403, 404):
			# Spent, expired or wrong: it will never work, so stop presenting
			# it. A new code has to be issued.
			save_json(path, {"portal_url": portal_url, "pairing_failed": f"HTTP {exc.code}"})
			return f"pairing code refused (HTTP {exc.code}); ask for a new one"
		return f"pairing failed (HTTP {exc.code}); will retry"
	except (urllib.error.URLError, OSError, ValueError) as exc:
		return f"pairing failed ({exc}); will retry"
	if not (isinstance(got, dict) and got.get("server_id") and got.get("secret")):
		return "pairing answer was malformed; will retry"
	save_json(path, {"portal_url": portal_url, "server_id": got["server_id"],
	                 "token": got["secret"]})
	# A fresh pairing is a fresh install as far as sync goes: whatever this
	# /data says it last applied came with the seed, from the other side.
	# Except whether this worker stopped Core — that is about this machine,
	# and losing it mid-failover would leave the home stopped after handback.
	previous = load_json(data_dir / STATE_FILE)
	kept = {k: previous[k] for k in ("core_stopped_by_vome",) if k in previous}
	save_json(data_dir / STATE_FILE, kept)
	return f"paired as {got['server_id']}"


class Portal:
	def __init__(self, binding: dict, opener=urllib.request.urlopen):
		self.base = binding["portal_url"].rstrip("/")
		self.server_id = binding["server_id"]
		self.token = binding["token"]
		self.opener = opener

	def _request(self, method: str, path: str, body: Optional[bytes] = None,
	             headers: Optional[dict] = None, timeout: int = 60):
		h = {"Authorization": f"Bearer {self.token}", "X-Server-ID": self.server_id}
		h.update(headers or {})
		req = urllib.request.Request(self.base + path, data=body, headers=h, method=method)
		return self.opener(req, timeout=timeout)

	def role(self) -> Optional[dict]:
		"""The portal's view of this install, or None when it cannot be asked."""
		try:
			with self._request("GET", API_ROLE, timeout=30) as resp:
				data = json.loads(resp.read().decode("utf-8"))
				return data if isinstance(data, dict) else None
		except (urllib.error.URLError, OSError, ValueError) as exc:
			LOG.info("Portal not reachable for role: %s", exc)
			return None

	def upload(self, blob: bytes, meta: dict) -> dict:
		with self._request("POST", API_SNAPSHOT, body=blob, timeout=300, headers={
			"Content-Type": "application/gzip",
			"X-Snapshot-SHA256": meta["sha256"],
			"X-HA-Version": meta.get("ha_version") or "",
			"X-File-Count": str(meta.get("file_count") or 0),
		}) as resp:
			return json.loads(resp.read().decode("utf-8"))

	def download(self) -> tuple[bytes, dict]:
		with self._request("GET", API_SNAPSHOT, timeout=300) as resp:
			blob = resp.read(MAX_SNAPSHOT_BYTES + 1)
			meta = {
				"id": resp.headers.get("X-Snapshot-Id") or "",
				"sha256": resp.headers.get("X-Snapshot-SHA256") or "",
				"ha_version": resp.headers.get("X-HA-Version") or "",
			}
		if len(blob) > MAX_SNAPSHOT_BYTES:
			raise ApplyRefused("snapshot is larger than this side accepts")
		return blob, meta

	def report_applied(self, snapshot_id: str, ok: bool, detail: str = "") -> None:
		body = json.dumps({"id": snapshot_id, "ok": ok, "detail": detail[:500]}).encode()
		try:
			with self._request("POST", API_APPLIED, body=body, timeout=30,
			                   headers={"Content-Type": "application/json"}):
				pass
		except (urllib.error.URLError, OSError) as exc:
			LOG.warning("Could not report the apply result: %s", exc)


# ── One pass ──────────────────────────────────────────────────────────────

def run_once(portal: Portal, config_dir: Path = CONFIG_DIR, data_dir: Path = DATA_DIR,
             core_stopped: Callable[[], bool] = core_is_stopped,
             local_version: Callable[[], str] = core_version,
             set_running: Callable[[bool], bool] = set_core_running) -> tuple[str, int]:
	"""Do whatever this side's role calls for. Returns (what happened, next wait)."""
	info = portal.role()
	if info is None:
		return "portal unreachable; nothing changed", IDLE_INTERVAL
	role = info.get("role")
	interval = int(info.get("interval_seconds") or DEFAULT_INTERVAL)
	state_path = data_dir / STATE_FILE
	state = load_json(state_path)

	core_note = enforce_core(info.get("core"), state, state_path, core_stopped, set_running)
	if core_note:
		LOG.info("%s", core_note)

	if role == ROLE_ACTIVE:
		blob, meta = build_snapshot(config_dir)
		latest = info.get("latest") or {}
		age = time.time() - float(latest.get("created_at") or 0)
		# upload_now: a handback is waiting for a snapshot taken after it was
		# asked for, changed or not.
		if (meta["sha256"] == latest.get("sha256") and age < REFRESH_SECONDS
		        and not info.get("upload_now")):
			return "active: unchanged since the last upload", interval
		if len(blob) > MAX_SNAPSHOT_BYTES:
			return f"active: snapshot too large to send ({len(blob)} bytes)", interval
		try:
			result = portal.upload(blob, meta)
		except urllib.error.HTTPError as exc:
			# 409: the portal no longer sees this side as active (a takeover
			# happened between the role check and the upload). Not an error.
			return f"active: upload refused ({exc.code})", interval
		except (urllib.error.URLError, OSError) as exc:
			return f"active: upload failed ({exc})", IDLE_INTERVAL
		state.update({"uploaded_sha256": meta["sha256"], "uploaded_at": int(time.time()),
		              "uploaded_id": result.get("id")})
		save_json(state_path, state)
		return f"active: uploaded {meta['file_count']} files ({len(blob)} bytes)", interval

	if role == ROLE_STANDBY:
		latest = info.get("latest") or {}
		if not latest.get("id"):
			return "standby: nothing to apply yet", interval
		# By id, not hash: a refresh re-sends the same config hash with newer
		# volatile files, and those should land too.
		if latest["id"] == state.get("applied_id"):
			return "standby: already in step", interval
		if not core_stopped():
			# Never write under a running Core. Not an error to report on
			# every pass: the portal starts Core only for a takeover, and
			# then this side is no longer the standby.
			return "standby: Core is running; not applying", interval
		blocker = version_blocker(latest.get("ha_version") or "", local_version())
		if blocker:
			portal.report_applied(latest["id"], False, blocker)
			return f"standby: {blocker}", interval
		try:
			blob, meta = portal.download()
			if meta.get("id") and meta["id"] != latest["id"]:
				return "standby: a newer snapshot arrived; will apply it next pass", 5
			if latest.get("sha256") and content_hash(blob) != latest["sha256"]:
				return "standby: snapshot did not match its hash; fetching again", 30
			result = apply_snapshot(config_dir, blob)
		except ApplyRefused as exc:
			portal.report_applied(latest["id"], False, str(exc))
			return f"standby: refused ({exc})", interval
		except (urllib.error.URLError, OSError, tarfile.TarError) as exc:
			return f"standby: apply failed ({exc})", IDLE_INTERVAL
		state.update({"applied_sha256": latest.get("sha256"), "applied_id": latest["id"],
		              "applied_at": int(time.time())})
		save_json(state_path, state)
		portal.report_applied(latest["id"], True,
		                      f"{result['written']} written, {result['removed']} removed")
		return (f"standby: applied snapshot {latest['id']} "
		        f"({result['written']} written, {result['removed']} removed)"), interval

	return f"not in a pair (role {role!r})", interval


def main() -> None:
	logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
	while True:
		paired = redeem_pairing()
		if paired:
			LOG.info("%s", paired)
		binding = load_binding()
		if not binding:
			time.sleep(IDLE_INTERVAL)
			continue
		try:
			outcome, wait = run_once(Portal(binding))
		except Exception:  # noqa: BLE001 - one bad pass must not end the service
			LOG.exception("CHAP sync pass failed")
			outcome, wait = "pass failed", IDLE_INTERVAL
		LOG.info("%s", outcome)
		time.sleep(max(5, wait))


if __name__ == "__main__":
	main()
