#!/usr/bin/env python3
"""Vome CHAP add-on — keep two Home Assistant installs in step.

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

It runs in an add-on, not in the integration, because the integration lives
inside Core — and on the side that receives, Core is off. And in its own
add-on (Vome CHAP), not the Vome one, because it needs the Supervisor's
manager role to stop and start Core and to make backups, which someone who
only wants switches or remote access should never have to grant.

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
import secrets
import shutil
import sqlite3
import tarfile
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable, Optional

LOG = logging.getLogger("vome-chap-sync")

CONFIG_DIR = Path(os.environ.get("VOME_CHAP_CONFIG_DIR", "/homeassistant"))
DATA_DIR = Path(os.environ.get("VOME_CHAP_DATA_DIR", "/data"))
SUPERVISOR = os.environ.get("VOME_SUPERVISOR_URL", "http://supervisor")
SUPERVISOR_TOKEN = os.environ.get("SUPERVISOR_TOKEN", "")
# The Supervisor's backup store, mapped in (config.yaml `backup:rw`): the
# standby puts the seed there for the Supervisor to restore.
BACKUP_DIR = Path(os.environ.get("VOME_CHAP_BACKUP_DIR", "/backup"))

BINDING_FILE = "chap.json"
STATE_FILE = "chap_state.json"
# Inside /config rather than /data: a rename across the two mounts fails
# (EXDEV), and the whole point is that each file lands with one rename.
STAGING_NAME = ".vome_chap_staging"

API_ROLE = "/api/sync/chap/config/role"
API_SNAPSHOT = "/api/sync/chap/config/snapshot"
API_APPLIED = "/api/sync/chap/config/applied"
API_PAIR = "/api/sync/chap/config/pair"
API_SEED = "/api/sync/chap/config/seed"

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
	".vome_chap_pairing.json",  # a pairing code the portal left; this install's only
	".vome_chap_poll_now",      # the portal asking this install to poll at once
	".vome_chap_status.json",   # this install's own status, for the Vome panel
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


def set_addons_running(slugs: list, running: bool, call=None) -> list:
	"""Start or stop add-ons and set their boot to match. Returns the failures."""
	call = call or _supervisor_call
	want = "started" if running else "stopped"
	failed = []
	for slug in slugs:
		call("POST", f"/addons/{slug}/options", {"boot": "auto" if running else "manual"}, timeout=60)
		_, info = call("GET", f"/addons/{slug}/info", None, timeout=60)
		if isinstance(info, dict) and (info.get("data") or {}).get("state") == want:
			continue  # already so; Supervisor refuses to start what is running
		status, _ = call("POST", f"/addons/{slug}/{'start' if running else 'stop'}", None, timeout=300)
		if status != 200:
			failed.append(slug)
	return failed


def enforce_addons(role: Optional[str], state: dict, state_path: Path,
                   call=None) -> Optional[str]:
	"""Run the home's add-ons only on the active side.

	Only the add-ons this install was seeded with: they came from the other
	install, carry its identity and data (a Matter controller, a torrent
	client writing to the house NAS), and would otherwise run on both at
	once -- the add-on form of two homes on one link. Started when this
	side becomes active, stopped again when it goes back to standby.
	"""
	held = list(state.get("held_addons") or [])
	if not held or role not in (ROLE_ACTIVE, ROLE_STANDBY):
		return None
	want = role == ROLE_ACTIVE
	if state.get("held_addons_running") is want:
		return None
	failed = set_addons_running(held, want, call)
	if failed:
		return f"could not {'start' if want else 'stop'} {', '.join(failed)}; will retry"
	state["held_addons_running"] = want
	save_json(state_path, state)
	return f"{'started' if want else 'stopped'} {len(held)} add-on(s): this install is the {role} one"


def core_version(opener=urllib.request.urlopen) -> str:
	status, body = _supervisor_get("/core/info", opener)
	if status == 200 and isinstance(body, dict):
		return str((body.get("data") or {}).get("version") or "")
	return ""


# ── The seed: a one-off full backup, only for filling the standby ─────────
#
# The first sync copies /config but not add-ons and their data. So when a
# pair is set up, the active install makes one full Supervisor backup, under
# a key generated for it alone, and sends backup and key to Vome only to be
# restored onto the standby. It is not one of the owner's backups: it is
# deleted here as soon as it is sent, and Vome deletes it and the key once
# the restore is done.

SEED_RETRY_SECONDS = 30 * 60
SEED_FILE = "seed.tar"


def _supervisor_call(method: str, path: str, body: Optional[dict] = None,
                     timeout: int = 60, opener=urllib.request.urlopen) -> tuple[int, Any]:
	req = urllib.request.Request(
		SUPERVISOR.rstrip("/") + path, method=method,
		data=json.dumps(body).encode() if body is not None else None,
		headers={"Authorization": f"Bearer {SUPERVISOR_TOKEN}", "Content-Type": "application/json"},
	)
	try:
		with opener(req, timeout=timeout) as resp:
			raw = resp.read()
			return resp.status, (json.loads(raw.decode("utf-8")) if raw else None)
	except urllib.error.HTTPError as err:
		return err.code, None
	except (urllib.error.URLError, OSError, ValueError):
		return 0, None


class SeedFailed(Exception):
	pass


# What a seed carries besides add-ons. Not Home Assistant itself: the
# standby never restores it (its configuration arrives by sync), and with
# the recorder database it can be most of the backup -- 4 GB of a 4.2 GB
# home -- to send over a house's upload for nothing.
SEED_FOLDERS = ("share", "ssl", "media", "addons/local")


def installed_addons(call=_supervisor_call) -> Optional[list]:
	"""Slugs of the installed add-ons except this one; None if unknown."""
	_, listed = call("GET", "/addons", None, timeout=60)
	found = ((listed or {}).get("data") or {}).get("addons") if isinstance(listed, dict) else None
	if found is None:
		return None
	return [a["slug"] for a in found
	        if isinstance(a, dict) and a.get("slug") and not str(a["slug"]).endswith("_vome_chap")]


def holdable(slugs) -> list:
	"""The home's own add-ons: not ours, which carry no identity of the home."""
	return sorted(s for s in slugs or [] if not s.endswith(("_vome", "_vome_chap")))


def make_seed_backup(key: str, name: str, call=_supervisor_call,
                     addons: Optional[list] = None) -> str:
	"""Create the key-encrypted seed backup; return its Supervisor slug.

	Add-ons and folders only. If the add-ons cannot be listed it falls back
	to a full backup, which the standby restores the same way.
	"""
	if addons is None:
		addons = installed_addons(call)
	if addons is not None:
		status, body = call("POST", "/backups/new/partial", {
			"name": name, "password": key, "compressed": True, "homeassistant": False,
			"addons": addons, "folders": list(SEED_FOLDERS),
		}, timeout=3600)
	else:
		status, body = call("POST", "/backups/new/full",
		                    {"name": name, "password": key, "compressed": True}, timeout=3600)
	slug = ((body or {}).get("data") or {}).get("slug") if isinstance(body, dict) else None
	if status != 200 or not slug:
		raise SeedFailed(f"the Supervisor did not make the backup (HTTP {status})")
	return slug


def download_backup(slug: str, dest: Path, opener=urllib.request.urlopen) -> int:
	req = urllib.request.Request(
		f"{SUPERVISOR.rstrip('/')}/backups/{slug}/download",
		headers={"Authorization": f"Bearer {SUPERVISOR_TOKEN}"},
	)
	size = 0
	with opener(req, timeout=3600) as resp, open(dest, "wb") as out:
		while True:
			chunk = resp.read(1024 * 1024)
			if not chunk:
				break
			out.write(chunk)
			size += len(chunk)
	return size


def send_seed(portal: "Portal", request_id: str, data_dir: Path = DATA_DIR,
              call=_supervisor_call, download=download_backup) -> tuple[str, list]:
	"""Make, send and clean up the seed. Returns what happened."""
	key = secrets.token_urlsafe(32)
	local = data_dir / SEED_FILE
	slug = None
	try:
		addons = installed_addons(call)
		slug = make_seed_backup(key, seed_backup_name(request_id), call, addons)
		size = download(slug, local)
		portal.upload_seed(request_id, local, size, key)
		return f"seed sent ({size} bytes)", holdable(addons)
	finally:
		# Whatever happened: this backup is not the owner's, and it must not
		# stay behind on this install.
		try:
			local.unlink()
		except OSError:
			pass
		if slug:
			call("DELETE", f"/backups/{slug}", None, timeout=120)


def maybe_send_seed(portal: "Portal", info: dict, state: dict, state_path: Path,
                    now: float, data_dir: Path = DATA_DIR, sender=None) -> Optional[str]:
	"""Send the seed the portal asked for, once per request; retry failures slowly."""
	request_id = str(((info.get("seed") or {}).get("request")) or "")
	if not request_id or state.get("seed_sent") == request_id:
		return None
	if state.get("seed_failed_request") == request_id and \
			now - float(state.get("seed_failed_at") or 0) < SEED_RETRY_SECONDS:
		return None
	try:
		outcome = (sender or send_seed)(portal, request_id, data_dir)
	except (SeedFailed, urllib.error.URLError, OSError) as exc:
		state.update({"seed_failed_request": request_id, "seed_failed_at": now})
		save_json(state_path, state)
		return f"seed failed ({exc}); will retry"
	outcome, seeded = outcome if isinstance(outcome, tuple) else (outcome, [])
	state["seed_sent"] = request_id
	state.pop("seed_failed_request", None)
	# The same add-ons now exist on the standby. The rule for them is the
	# same on both sides: they run only where the home is active, so a
	# failover with this install still up does not leave two of each.
	state["held_addons"] = sorted(set(state.get("held_addons") or []) | set(seeded))
	state["held_addons_running"] = None
	save_json(state_path, state)
	return outcome


def seed_backup_name(seed_id: str) -> str:
	"""What the active side calls the seed, so the standby can find it."""
	return f"Vome CHAP seed {seed_id}"


def restore_seed(portal: "Portal", seed_id: str, backup_dir: Path = BACKUP_DIR,
                 call=_supervisor_call) -> tuple[str, list]:
	"""Fetch the seed, restore its add-ons and folders, and remove it.

	Never Home Assistant itself: /config arrives by sync, and restoring it
	would start Core here -- a second copy of the home, on its relay link
	(C34). Nor this add-on: its /data is this install's own pairing.
	"""
	safe = re.sub(r"[^A-Za-z0-9-]", "", seed_id)[:64] or "seed"
	local = backup_dir / f"vome_chap_seed_{safe}.tar"
	slug = None
	try:
		key = portal.download_seed(seed_id, local)
		call("POST", "/backups/reload", None, timeout=300)
		_, body = call("GET", "/backups", None, timeout=60)
		backups = (((body or {}).get("data") or {}).get("backups") or []) if isinstance(body, dict) else []
		slug = next((b.get("slug") for b in backups
		             if isinstance(b, dict) and b.get("name") == seed_backup_name(seed_id)), None)
		if not slug:
			raise SeedFailed("the Supervisor did not pick up the seed backup")
		_, body = call("GET", f"/backups/{slug}/info", None, timeout=60)
		info = ((body or {}).get("data") or {}) if isinstance(body, dict) else {}
		addons = [a["slug"] for a in info.get("addons") or []
		          if isinstance(a, dict) and a.get("slug") and not a["slug"].endswith("_vome_chap")]
		folders = [f for f in info.get("folders") or [] if isinstance(f, str) and f]
		status, body = call("POST", f"/backups/{slug}/restore/partial", {
			"homeassistant": False, "addons": addons, "folders": folders, "password": key,
		}, timeout=3600)
		if status != 200 or not isinstance(body, dict) or body.get("result") != "ok":
			raise SeedFailed(f"the Supervisor did not restore the seed (HTTP {status})")
		return f"seed restored ({len(addons)} add-ons, {len(folders)} folders)", addons
	finally:
		# Whatever happened: this backup is not the owner's either.
		if slug:
			call("DELETE", f"/backups/{slug}", None, timeout=120)
		try:
			local.unlink()
		except OSError:
			pass


def maybe_restore_seed(portal: "Portal", info: dict, state: dict, state_path: Path,
                       now: float, core_stopped: Callable[[], bool],
                       restorer=None) -> Optional[str]:
	"""Restore the seed waiting for this standby, once.

	A network failure is retried slowly; a restore the Supervisor refused is
	reported, and the portal then deletes the seed either way.
	"""
	seed_id = str(((info.get("seed_restore") or {}).get("id")) or "")
	if not seed_id or state.get("seed_restored") == seed_id:
		return None
	if state.get("seed_restore_failed_id") == seed_id and \
			now - float(state.get("seed_restore_failed_at") or 0) < SEED_RETRY_SECONDS:
		return None
	if not core_stopped():
		return None  # a standby's Core runs only for a takeover; not now
	try:
		outcome = (restorer or restore_seed)(portal, seed_id)
	except SeedFailed as exc:
		portal.report_seed(seed_id, False, str(exc))
		state["seed_restored"] = seed_id
		save_json(state_path, state)
		return f"seed not restored ({exc})"
	except (urllib.error.URLError, OSError) as exc:
		state.update({"seed_restore_failed_id": seed_id, "seed_restore_failed_at": now})
		save_json(state_path, state)
		return f"seed restore failed ({exc}); will retry"
	outcome, restored = outcome if isinstance(outcome, tuple) else (outcome, [])
	portal.report_seed(seed_id, True, outcome)
	state["seed_restored"] = seed_id
	# The add-ons just restored are the home's services, and add-ons run
	# whether Core does or not: held stopped until this side is active.
	state["held_addons"] = sorted(set(state.get("held_addons") or []) | set(holdable(restored)))
	state["held_addons_running"] = None  # unknown: enforce on the next pass
	state.pop("seed_restore_failed_id", None)
	save_json(state_path, state)
	return outcome


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


RELAY_PAIRING_FILE = ".vome_chap_pairing.json"


def collect_relay_pairing(data_dir: Path = DATA_DIR, config_dir: Path = CONFIG_DIR) -> bool:
	"""Take a pairing code the portal left in /config over the relay.

	A home behind the relay cannot be reached over a guest agent, so the
	portal writes ``{"portal_url", "pairing_code"}`` to
	``/config/.vome_chap_pairing.json`` through the Vome component's file
	access instead. It is moved into this add-on's own /data and deleted
	from /config at once; it is never synced, so it cannot reach the other
	install. The code is single use either way.
	"""
	src = config_dir / RELAY_PAIRING_FILE
	if not src.is_file():
		return False
	left = load_json(src)
	try:
		src.unlink()
	except OSError:
		pass
	code, portal_url = left.get("pairing_code"), (left.get("portal_url") or "").rstrip("/")
	if not code or not portal_url.startswith("https://"):
		return False
	save_json(data_dir / BINDING_FILE, {"portal_url": portal_url, "pairing_code": code})
	return True


def redeem_pairing(data_dir: Path = DATA_DIR, opener=urllib.request.urlopen) -> Optional[str]:
	"""Swap a pairing code in /data/chap.json for this install's credential.

	The portal (over the guest agent) or the owner (in the panel) leaves
	``{"portal_url", "pairing_code"}``; the code is single use, so the file is
	rewritten with the credential the moment it is redeemed. Returns what
	happened, or None when there was no code to redeem.
	"""
	path = data_dir / BINDING_FILE
	collect_relay_pairing(data_dir)
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
			# it. A new code has to be issued. Only if the file still holds
			# *this* code, though: the panel and the worker can race to redeem
			# the same one, and the loser must not overwrite the winner's
			# fresh credential with a failure.
			if load_json(path).get("pairing_code") == code:
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

	def role(self, primary_reachable: Optional[bool] = None) -> Optional[dict]:
		"""The portal's view of this install, or None when it cannot be asked.

		A local fallback for a hosted home also says, on each poll, whether
		it can reach that hosted home — the portal's evidence of the link.
		"""
		headers = {}
		if primary_reachable is not None:
			headers["X-Primary-Reachable"] = "1" if primary_reachable else "0"
		try:
			with self._request("GET", API_ROLE, timeout=30, headers=headers) as resp:
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

	def upload_seed(self, request_id: str, path: Path, size: int, key: str) -> dict:
		"""Stream the seed backup to Vome, with the one-off key it needs."""
		with open(path, "rb") as fh:
			with self._request("POST", API_SEED, body=fh, timeout=3600, headers={
				"Content-Type": "application/x-tar",
				"Content-Length": str(size),
				"X-Seed-Request": request_id,
				"X-Seed-Key": key,
			}) as resp:
				return json.loads(resp.read().decode("utf-8") or "{}")

	def download_seed(self, seed_id: str, dest: Path) -> str:
		"""Save the seed waiting for this install to ``dest``; return its key."""
		path = f"{API_SEED}/{urllib.parse.quote(seed_id, safe='')}"
		with self._request("GET", path, timeout=3600) as resp:
			key = resp.headers.get("X-Seed-Key") or ""
			with open(dest, "wb") as out:
				shutil.copyfileobj(resp, out, 1024 * 1024)
		if not key:
			raise SeedFailed("the portal sent the seed without its key")
		return key

	def report_seed(self, seed_id: str, ok: bool, detail: str = "") -> None:
		body = json.dumps({"ok": ok, "detail": detail[:300]}).encode()
		path = f"{API_SEED}/{urllib.parse.quote(seed_id, safe='')}/restored"
		try:
			with self._request("POST", path, body=body, timeout=30,
			                   headers={"Content-Type": "application/json"}):
				pass
		except (urllib.error.URLError, OSError) as exc:
			LOG.warning("Could not report the seed restore: %s", exc)

	def report_applied(self, snapshot_id: str, ok: bool, detail: str = "",
	                   needs_core_version: str = "") -> None:
		report = {"id": snapshot_id, "ok": ok, "detail": detail[:500]}
		if needs_core_version:
			# Structured, so the portal can update a hosted install's Core
			# without parsing the message.
			report["needs_core_version"] = needs_core_version
		body = json.dumps(report).encode()
		try:
			with self._request("POST", API_APPLIED, body=body, timeout=30,
			                   headers={"Content-Type": "application/json"}):
				pass
		except (urllib.error.URLError, OSError) as exc:
			LOG.warning("Could not report the apply result: %s", exc)


# ── Panel ─────────────────────────────────────────────────────────────────

def panel_status(data_dir: Path = DATA_DIR) -> dict:
	"""What the panel shows. Never the credential."""
	b = load_json(data_dir / BINDING_FILE)
	st = load_json(data_dir / STATE_FILE)
	return {
		"paired": bool(b.get("server_id") and b.get("token")),
		"server_id": b.get("server_id"),
		"portal_url": b.get("portal_url"),
		"pairing_pending": bool(b.get("pairing_code")),
		"pairing_failed": b.get("pairing_failed"),
		"applied_id": st.get("applied_id"),
		"applied_at": st.get("applied_at"),
		"uploaded_at": st.get("uploaded_at"),
		"core_stopped_by_vome": bool(st.get("core_stopped_by_vome")),
	}


# ── Local fallback for a hosted home (reverse mode) ───────────────────────
#
# chap_plan §10: someone's home runs on a hosted instance and this install is
# the local fallback. The portal stands the hosted side down and tells this
# one to start whenever it can hear us; this only matters when it cannot.

PROBE_TIMEOUT = 10


def probe_primary(url: str, opener=urllib.request.urlopen) -> bool:
	"""Can this house reach the hosted home, the way the house reaches it?

	The portal points this at the home's ``/manifest.json``, which Home
	Assistant serves without a login as JSON. Only that counts: the edge in
	front of it answers too — a 403 "home network only" gate, a 502 for a
	VM that is down, a fallback page — and none of those means the home is
	there. So a 200 with a JSON body type, and nothing else.
	"""
	req = urllib.request.Request(url, method="GET")
	try:
		with opener(req, timeout=PROBE_TIMEOUT) as resp:
			ctype = (resp.headers.get("Content-Type") or "").lower()
			return resp.status == 200 and "json" in ctype
	except (urllib.error.URLError, OSError, ValueError):
		return False  # includes HTTPError: every non-2xx


def watch_primary(state: dict, now: float, probe: Callable[[str], bool]) -> Optional[bool]:
	"""Probe the hosted home if this install is its fallback; record the run."""
	fallback = state.get("fallback") or {}
	url = fallback.get("probe_url")
	if not url:
		return None
	reachable = probe(url)
	state["primary_reachable"] = reachable
	if reachable:
		state.pop("primary_unreachable_since", None)
	else:
		state.setdefault("primary_unreachable_since", now)
	return reachable


def maybe_take_over_locally(state: dict, now: float,
                            set_running: Callable[[bool], bool]) -> Optional[str]:
	"""Start Core with nobody to ask — only when it is safe to.

	All of: the portal said this install may (``takeover_after`` is only
	given when there is an anchor on site to tell a dead Pi from a dark
	house); the portal has been unreachable that long; the hosted home has
	been unreachable from here that long; and this worker is the one that
	stopped Core. The portal stands the hosted side down well inside that
	time (T_DOWN < T_TAKE), so the two never both run.
	"""
	fallback = state.get("fallback") or {}
	after = fallback.get("takeover_after")
	if not after or state.get("took_over_locally") or not state.get("core_stopped_by_vome"):
		return None
	portal_quiet = now - float(state.get("portal_ok_at") or now)
	primary_quiet = now - float(state.get("primary_unreachable_since") or now)
	if portal_quiet < after or primary_quiet < after:
		return None
	if not set_running(True):
		return "could not start Core to take over; will retry"
	state["core_stopped_by_vome"] = False
	state["took_over_locally"] = now
	return "took over locally: neither Vome nor the hosted home could be reached"


# ── One pass ──────────────────────────────────────────────────────────────

def run_once(portal: Portal, config_dir: Path = CONFIG_DIR, data_dir: Path = DATA_DIR,
             core_stopped: Callable[[], bool] = core_is_stopped,
             local_version: Callable[[], str] = core_version,
             set_running: Callable[[bool], bool] = set_core_running,
             probe: Callable[[str], bool] = probe_primary) -> tuple[str, int]:
	"""Do whatever this side's role calls for. Returns (what happened, next wait)."""
	state_path = data_dir / STATE_FILE
	state = load_json(state_path)
	now = time.time()
	reachable = watch_primary(state, now, probe)

	info = portal.role(reachable)
	if info is None:
		note = maybe_take_over_locally(state, now, set_running)
		save_json(state_path, state)
		if note:
			LOG.warning("%s", note)
			return note, IDLE_INTERVAL
		return "portal unreachable; nothing changed", IDLE_INTERVAL
	# The portal can hear us again, so it decides again: whatever this
	# install did on its own is now the portal's instruction to confirm.
	state["portal_ok_at"] = now
	state["fallback"] = info.get("fallback") or None
	state.pop("took_over_locally", None)
	if not state["fallback"]:
		for key in ("primary_reachable", "primary_unreachable_since"):
			state.pop(key, None)
	save_json(state_path, state)
	role = info.get("role")
	interval = int(info.get("interval_seconds") or DEFAULT_INTERVAL)

	core_note = enforce_core(info.get("core"), state, state_path, core_stopped, set_running)
	if core_note:
		LOG.info("%s", core_note)
	addons_note = enforce_addons(role, state, state_path)
	if addons_note:
		LOG.info("%s", addons_note)

	if role == ROLE_ACTIVE:
		seed_note = maybe_send_seed(portal, info, state, state_path, now, data_dir)
		if seed_note:
			LOG.info("%s", seed_note)
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
		seed_note = maybe_restore_seed(portal, info, state, state_path, now, core_stopped)
		if seed_note:
			LOG.info("%s", seed_note)
			# The restore started the seeded add-ons; stop them now, not
			# at the next check-in minutes later.
			addons_note = enforce_addons(role, state, state_path)
			if addons_note:
				LOG.info("%s", addons_note)
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
			portal.report_applied(latest["id"], False, blocker,
			                      needs_core_version=(latest.get("ha_version") or "").strip())
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


POLL_NOW_FILE = ".vome_chap_poll_now"
NUDGE_CHECK_SECONDS = 3


def sleep_unless_nudged(seconds: float, config_dir: Path = CONFIG_DIR,
                        sleep: Callable[[float], None] = time.sleep) -> bool:
	"""Wait up to ``seconds``, cut short if the portal asks for a poll now.

	A home behind the relay can only be reached through its Vome component,
	which serves file access to /config: the portal writes
	``/config/.vome_chap_poll_now`` when it needs this install to hear
	something promptly -- above all "stop" when a failover is declared,
	since until then the standby cannot safely start. Returns True if nudged.
	"""
	marker = config_dir / POLL_NOW_FILE
	pairing = config_dir / RELAY_PAIRING_FILE
	waited = 0.0
	while waited < seconds:
		if marker.exists():
			try:
				marker.unlink()
			except OSError:
				pass
			return True
		if pairing.exists():
			return True  # a code to redeem: collected on the next pass
		step = min(NUDGE_CHECK_SECONDS, seconds - waited)
		sleep(step)
		waited += step
	return False


STATUS_FILE_NAME = ".vome_chap_status.json"


def publish_status(outcome: str, data_dir: Path = DATA_DIR, config_dir: Path = CONFIG_DIR) -> None:
	"""Leave this install's status where the Vome add-on's panel can read it.

	The two add-ons share only /config. Never the credential; never synced.
	"""
	status = panel_status(data_dir)
	status.update({"last_outcome": outcome, "updated_at": int(time.time())})
	try:
		save_json(config_dir / STATUS_FILE_NAME, status)
	except OSError:
		LOG.warning("Could not write the status file for the Vome panel")


def main() -> None:
	logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
	while True:
		paired = redeem_pairing()
		if paired:
			LOG.info("%s", paired)
		binding = load_binding()
		if not binding:
			publish_status(paired or "not paired")
			sleep_unless_nudged(IDLE_INTERVAL)
			continue
		try:
			outcome, wait = run_once(Portal(binding))
		except Exception:  # noqa: BLE001 - one bad pass must not end the service
			LOG.exception("CHAP sync pass failed")
			outcome, wait = "pass failed", IDLE_INTERVAL
		LOG.info("%s", outcome)
		publish_status(outcome)
		if sleep_unless_nudged(max(5, wait)):
			LOG.info("asked by Vome to check in now")


if __name__ == "__main__":
	main()
