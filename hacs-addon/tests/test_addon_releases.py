# flake8: noqa
"""Both Supervisor add-ons are released the same way (owner, 30 Sept 2026:
"yeah please make CHAP match"): vome/package_release.py packages either, with
its own tag prefix, and Vome CHAP's release notes -- and the text Home
Assistant shows in its update dialog -- come from vome_chap/CHANGELOG.md.
"""
import importlib.util
import os
import re
import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("package_release", ROOT / "vome" / "package_release.py")
pr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pr)


def _version(addon):
	return re.search(r'^version:\s*"([^"]+)"', (ROOT / addon / "config.yaml").read_text(), re.M).group(1)


def test_every_chap_version_has_a_changelog_entry():
	"""A bump without one would ship an empty update dialog and release."""
	changelog = (ROOT / "vome_chap" / "CHANGELOG.md").read_text(encoding="utf-8")
	assert pr.changelog_section(changelog, _version("vome_chap"))


def test_a_section_is_only_its_own_version():
	text = "# Changelog\n\n## 0.2.0 — today\n\n- new\n\n## 0.1.18 — before\n\n- old\n"
	assert pr.changelog_section(text, "0.2.0") == "- new"
	assert pr.changelog_section(text, "0.1.18") == "- old"
	assert pr.changelog_section(text, "0.1.1") == ""


@pytest.mark.parametrize("addon, tag, artifact", [
	("vome_chap", "vome-chap-v{v}", "vome-chap-v{v}.zip"),
	("vome", "v{v}", "vome-addon-v{v}.zip"),   # as it always was
])
def test_packaging_either_add_on(tmp_path, monkeypatch, addon, tag, artifact):
	shutil.copytree(ROOT / addon, tmp_path / addon)
	shutil.copy(ROOT / "repository.yaml", tmp_path / "repository.yaml")
	monkeypatch.chdir(tmp_path)
	monkeypatch.setenv("ADDON", addon)
	monkeypatch.delenv("RELEASE_TAG", raising=False)
	pr.main()
	v = _version(addon)
	info = dict(line.split("=", 1) for line in (tmp_path / "dist" / "release.txt").read_text().splitlines())
	assert info["tag"] == tag.format(v=v) and info["artifact"] == artifact.format(v=v)
	assert (tmp_path / "dist" / info["artifact"]).exists()
	if addon == "vome_chap":
		assert info["notes"] == "release-notes.md" and (tmp_path / "dist" / "release-notes.md").read_text().strip()


def test_an_unknown_add_on_is_refused(monkeypatch):
	monkeypatch.setenv("ADDON", "vome_nope")
	with pytest.raises(SystemExit):
		pr.main()


def test_the_pipelines_know_both():
	auto = (ROOT / "jenkins" / "pipelines" / "Jenkinsfile.vome-addon-autorelease").read_text()
	release = (ROOT / "jenkins" / "pipelines" / "Jenkinsfile.vome-addon-release").read_text()
	assert "vome_chap/config.yaml" in auto or "dir: 'vome_chap'" in auto
	assert "prefix: 'vome-chap'" in auto and "prefix: 'vome-addon'" in auto
	# A new parameter is dropped until the job has run once: the tag decides too.
	assert "tag.startsWith('vome-chap-')" in release
