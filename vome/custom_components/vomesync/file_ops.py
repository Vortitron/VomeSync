"""File operations under Home Assistant's config directory, with no Core imports.

Two processes use this, with the same rules: the integration's relay
``files`` target, for files that are not YAML, and the Vome app, which does
the YAML writes the integration refuses (an integration must not write the
owner's YAML configuration; an app with ``/config`` mapped, like the File
editor, may).  The app imports this file from its bundled copy of the
integration, so it must stay importable on its own: standard library only,
no relative imports.

Every function here blocks; the integration runs them in an executor.  Each
returns ``(status, json_body, error)``: ``status`` 200 with a body, or 0 with
the reason, as the relay expects.
"""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from typing import Any, Optional

# Files at the top of the config directory that a delete refuses, and that a
# move will neither take away nor replace: Home Assistant will not start
# without the first two, and the rest are its database and version marker.  An
# agent tidying up must not be able to take any of them with it.
DELETE_PROTECTED = (
	"configuration.yaml",
	"secrets.yaml",
	".HA_VERSION",
	"home-assistant_v2.db",
	"home-assistant_v2.db-wal",
	"home-assistant_v2.db-shm",
)
# Directories never served, whatever the caller asks for.  ``.storage`` is Home
# Assistant's own database -- registries, config entries, every credential the
# UI has stored.  Hand-editing it corrupts state, and reading it would hand over
# far more than a config file.
DENIED_DIRS = (".storage", ".cloud")
# A config file is kilobytes; anything near this is not one, and the relay
# frame carries the whole body.
MAX_WRITE_BYTES = 2 * 1024 * 1024
# What counts as YAML configuration, which only the Vome app writes.
YAML_SUFFIXES = (".yaml", ".yml")


def is_yaml(path: Path) -> bool:
	"""True for a YAML file, judged on the resolved path so a link cannot hide one."""
	return path.suffix.lower() in YAML_SUFFIXES


def resolve_under(
	base: Path, raw: Any, where: str, denied: tuple[str, ...] = ()
) -> tuple[Optional[Path], Optional[str]]:
	"""Resolve ``raw`` inside ``base``, or explain the refusal.

	Resolution happens *before* the containment check, so a symlink pointing
	out of the directory is caught as well as ``..`` — checking the string
	would only stop the obvious half.  An empty path means the directory
	itself, which is how listing works.  ``denied`` names directories that
	are never served from anywhere under ``base``.
	"""
	rel = (raw or "").strip() if isinstance(raw, str) else ""
	rel = rel.lstrip("/")
	try:
		target = (base / rel).resolve() if rel else base
	except (OSError, ValueError) as err:
		return None, f"Invalid path: {err}"
	if target != base and base not in target.parents:
		return None, f"Path is outside {where}."
	parts = target.relative_to(base).parts if target != base else ()
	if any(part in denied for part in parts):
		return None, (
			"Refusing to touch Home Assistant's internal storage. It holds the "
			"registries and every credential the UI has saved, and hand-editing "
			"it corrupts state."
		)
	return target, None


def resolve_config_path(base: Path, raw: Any) -> tuple[Optional[Path], Optional[str]]:
	"""``resolve_under`` the config directory, with its denied directories."""
	return resolve_under(base, raw, "the Home Assistant config directory", DENIED_DIRS)


def delete_file(base: Path, target: Path) -> tuple[int, Optional[str], Optional[str]]:
	"""Remove one file, never a directory and never one Home Assistant needs.

	One file at a time and never a directory: a tidy-up that names the wrong
	folder must fail, not empty it.  The protected names are checked on the
	resolved path, so a symlink called something else cannot be used to delete
	configuration.yaml through it.
	"""
	if target == base:
		return 0, None, "Refusing to delete the config directory."
	if target.is_dir():
		return 0, None, "Path is a directory; only single files can be deleted."
	if not target.is_file():
		return 0, None, "Not a file, or it does not exist."
	if target.parent == base and target.name in DELETE_PROTECTED:
		return 0, None, (
			f"Refusing to delete {target.name}: Home Assistant needs it to start "
			"or to keep its history."
		)
	try:
		target.unlink()
	except OSError as err:
		return 0, None, f"Could not delete the file: {err}"
	return 200, json.dumps({
		"path": str(target.relative_to(base)), "deleted": True,
	}), None


def move_file(
	base: Path, source: Path, dest: Path, overwrite: bool
) -> tuple[int, Optional[str], Optional[str]]:
	"""Rename one file inside the config directory.

	Same rules as delete, because a move is a delete of the source: one file,
	never a directory, and none of the files Home Assistant needs, checked on
	the resolved paths.  Those are refused as the destination too, since
	replacing configuration.yaml is as fatal as taking it away.  An existing
	destination is only replaced when the caller says so.
	"""
	if source == base:
		return 0, None, "Refusing to move the config directory."
	if source.is_dir():
		return 0, None, "Path is a directory; only single files can be moved."
	if not source.is_file():
		return 0, None, "Not a file, or it does not exist."
	for verb, candidate in (("move", source), ("replace", dest)):
		if candidate.parent == base and candidate.name in DELETE_PROTECTED:
			return 0, None, (
				f"Refusing to {verb} {candidate.name}: Home Assistant needs it to "
				"start or to keep its history."
			)
	if dest == base or dest.is_dir():
		return 0, None, (
			"Destination is a directory; give the full path of the file to create."
		)
	if dest == source:
		return 0, None, "Source and destination are the same file."
	if dest.exists() and not overwrite:
		return 0, None, "Destination exists; pass overwrite=1 to replace it."
	try:
		dest.parent.mkdir(parents=True, exist_ok=True)
		os.replace(source, dest)
	except OSError as err:
		return 0, None, f"Could not move the file: {err}"
	return 200, json.dumps({
		"path": str(source.relative_to(base)),
		"to": str(dest.relative_to(base)),
		"moved": True,
	}), None


def write_file(base: Path, target: Path, body: Any) -> tuple[int, Optional[str], Optional[str]]:
	"""Write ``body['content']`` (UTF-8 text, or base64 bytes) to one file.

	Written to a temporary file in the same directory and then swapped in, so
	a failure part-way cannot leave configuration.yaml truncated -- the file
	Home Assistant refuses to start without.
	"""
	content = body.get("content") if isinstance(body, dict) else None
	if not isinstance(content, str):
		return 0, None, "Body must be JSON with a 'content' string."
	write_encoding = body.get("encoding") if isinstance(body, dict) else None
	if write_encoding not in (None, "utf8", "base64"):
		return 0, None, "encoding must be 'utf8' or 'base64'."
	if write_encoding == "base64":
		try:
			payload = base64.b64decode(content, validate=True)
		except ValueError as err:
			return 0, None, f"Invalid base64 content: {err}"
	else:
		payload = content.encode("utf-8")
	if len(payload) > MAX_WRITE_BYTES:
		return 0, None, f"Content exceeds {MAX_WRITE_BYTES} bytes."
	if target == base or (target.exists() and target.is_dir()):
		return 0, None, "Path is a directory."
	try:
		target.parent.mkdir(parents=True, exist_ok=True)
		tmp = target.with_name(f".{target.name}.vome-tmp")
		tmp.write_bytes(payload)
		os.replace(tmp, target)
	except OSError as err:
		return 0, None, f"Could not write the file: {err}"
	return 200, json.dumps({
		"path": str(target.relative_to(base)), "written": True,
		"bytes": len(payload),
	}), None


def apply(
	base: Path, op: str, path: Any, *, to: Any = None, overwrite: bool = False,
	body: Any = None, yaml_only: bool = False,
) -> tuple[int, Optional[str], Optional[str]]:
	"""Resolve and run one ``write``, ``delete`` or ``move`` under ``base``.

	The app's entry point.  ``yaml_only`` keeps it to what the integration
	hands over: a call naming anything but YAML is refused, judged on the
	resolved paths.
	"""
	target, problem = resolve_config_path(base, path)
	if target is None:
		return 0, None, problem
	if op == "move":
		if not isinstance(to, str) or not to.strip().lstrip("/"):
			return 0, None, "Move needs a destination."
		dest, problem = resolve_config_path(base, to)
		if dest is None:
			return 0, None, problem
		if yaml_only and not (is_yaml(target) or is_yaml(dest)):
			return 0, None, "Only YAML files are handled here."
		return move_file(base, target, dest, bool(overwrite))
	if yaml_only and not is_yaml(target):
		return 0, None, "Only YAML files are handled here."
	if op == "delete":
		return delete_file(base, target)
	if op == "write":
		return write_file(base, target, body)
	return 0, None, f"Unknown file operation: {op}"
