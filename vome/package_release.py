#!/usr/bin/env python3
"""Build a distributable ZIP of one of this repository's Supervisor add-ons.

Used by jenkins/pipelines/Jenkinsfile.vome-addon-release (kept as a real file
so Groovy/shell indentation cannot corrupt a heredoc). ``ADDON`` picks which:
``vome`` (the default, as before) or ``vome_chap`` (owner, 30 Sept 2026: make
CHAP's releases match). Each has its own tag prefix, since the repository's
tags are shared with the HACS integration's plain ``vX.Y.Z``.

Writes the zip, ``dist/release.txt`` (tag, version, artefact, notes) and, when
the add-on's CHANGELOG.md has a section for this version,
``dist/release-notes.md`` for the GitHub Release's text.
"""
from __future__ import annotations

import os
import pathlib
import re
import zipfile


# add-on folder -> tag prefix
PREFIXES = {"vome": "vome-addon", "vome_chap": "vome-chap"}


def changelog_section(changelog: str, version: str) -> str:
	"""The CHANGELOG.md section for ``version`` (its ``## version`` heading up to
	the next one), without the heading; '' when there is none."""
	out, taking = [], False
	for line in changelog.splitlines():
		if line.startswith("## "):
			if taking:
				break
			taking = re.match(rf"## {re.escape(version)}(\s|$)", line) is not None
			continue
		if taking:
			out.append(line)
	return "\n".join(out).strip()


def main() -> None:
	addon = os.getenv("ADDON", "").strip() or "vome"
	if addon not in PREFIXES:
		raise SystemExit(f"Unknown add-on {addon!r}: one of {', '.join(PREFIXES)}")
	prefix = PREFIXES[addon]
	addon_root = pathlib.Path(addon)
	config_text = (addon_root / "config.yaml").read_text(encoding="utf-8")
	match = re.search(r'^version:\s*"([^"]+)"', config_text, re.M)
	config_version = match.group(1) if match else "0.0.0"
	default_tag = f"v{config_version}" if addon == "vome" else f"{prefix}-v{config_version}"
	release_tag = os.getenv("RELEASE_TAG", "").strip() or default_tag

	dist_dir = pathlib.Path("dist")
	dist_dir.mkdir(exist_ok=True)
	# The Vome add-on's zips keep their old name; others are named after the tag.
	artifact_path = dist_dir / (f"vome-addon-{release_tag}.zip" if addon == "vome" else f"{release_tag}.zip")

	skip_dirs = {"__pycache__", ".git"}
	skip_names = {".gitignore"}

	with zipfile.ZipFile(artifact_path, "w", zipfile.ZIP_DEFLATED) as zf:
		for path in addon_root.rglob("*"):
			if any(part in skip_dirs for part in path.parts):
				continue
			if path.name in skip_names or path.suffix == ".pyc":
				continue
			if path.is_file():
				zf.write(path, path.relative_to(addon_root.parent))
		repo = pathlib.Path("repository.yaml")
		if repo.is_file():
			zf.write(repo, "repository.yaml")

	notes_name = ""
	changelog = addon_root / "CHANGELOG.md"
	if changelog.is_file():
		notes = changelog_section(changelog.read_text(encoding="utf-8"), config_version)
		if notes:
			notes_name = "release-notes.md"
			(dist_dir / notes_name).write_text(notes + "\n", encoding="utf-8")

	info = (f"addon={addon}\ntag={release_tag}\nconfig_version={config_version}\n"
	        f"artifact={artifact_path.name}\nnotes={notes_name}\n")
	(dist_dir / "release.txt").write_text(info, encoding="utf-8")
	if addon == "vome":
		(dist_dir / "vome-addon-release.txt").write_text(info, encoding="utf-8")  # as before
	print(f"Built: {artifact_path}")


if __name__ == "__main__":
	main()
