#!/usr/bin/env python3
"""Build a distributable ZIP of the Vome Supervisor add-on.

Used by jenkins/pipelines/Jenkinsfile.vome-addon-release (kept as a real file
so Groovy/shell indentation cannot corrupt a heredoc).
"""
from __future__ import annotations

import os
import pathlib
import re
import zipfile


def main() -> None:
	addon_root = pathlib.Path("vome")
	config_text = (addon_root / "config.yaml").read_text(encoding="utf-8")
	match = re.search(r'^version:\s*"([^"]+)"', config_text, re.M)
	config_version = match.group(1) if match else "0.0.0"
	release_tag = os.getenv("RELEASE_TAG", "").strip() or f"v{config_version}"

	dist_dir = pathlib.Path("dist")
	dist_dir.mkdir(exist_ok=True)
	artifact_path = dist_dir / f"vome-addon-{release_tag}.zip"

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

	(dist_dir / "vome-addon-release.txt").write_text(
		f"tag={release_tag}\nconfig_version={config_version}\nartifact={artifact_path.name}\n",
		encoding="utf-8",
	)
	print(f"Built: {artifact_path}")


if __name__ == "__main__":
	main()
