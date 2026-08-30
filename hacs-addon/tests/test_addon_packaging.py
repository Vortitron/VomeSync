# flake8: noqa
"""Packaging contract for the Supervisor add-on (vome/).

Store installs *build this image on the user's Home Assistant*. Two things
have already broken that path in the wild:

1. ``ARG BUILD_FROM`` with no default — Supervisor 2026.04+ stopped injecting
   it, so Docker saw an empty ``FROM``.
2. ``apk add python3`` against Alpine 3.20 — that release went EOL in April
   2026, and apk then fails with ``python3 (no such package)`` even when the
   rest of Home Assistant is fine. Pre-imaged Vome Home VMs never hit this
   because they skip the Store build.

These tests keep the Dockerfile on ``base-python`` (interpreter already in
the image, no apk) and keep the store description about *what Vome does*.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ADDON = ROOT / "vome"
DOCKERFILE = (ADDON / "Dockerfile").read_text(encoding="utf-8")
BUILD_YAML = (ADDON / "build.yaml").read_text(encoding="utf-8")
CONFIG = (ADDON / "config.yaml").read_text(encoding="utf-8")

PYTHON_BASE_TAG = "3.13-alpine3.22"
PYTHON_BASE_IMAGE = f"ghcr.io/home-assistant/base-python:{PYTHON_BASE_TAG}"


def _folded_description() -> str:
	match = re.search(
		r"^description:\s*>\s*\n((?:  .+\n)+)",
		CONFIG,
		re.M,
	)
	assert match, "config.yaml must have a folded (>) description"
	return " ".join(line.strip() for line in match.group(1).splitlines())


def test_dockerfile_defaults_build_from_to_base_python():
	match = re.search(r"^ARG BUILD_FROM=(.+)$", DOCKERFILE, re.M)
	assert match, "Dockerfile must default ARG BUILD_FROM so Supervisor 2026.04+ can FROM it"
	default = match.group(1).strip()
	assert default == PYTHON_BASE_IMAGE, default
	assert "FROM ${BUILD_FROM}" in DOCKERFILE or "FROM $BUILD_FROM" in DOCKERFILE


def test_dockerfile_copies_addon_config_for_panel_version():
	assert "COPY config.yaml /usr/share/vome/config.yaml" in DOCKERFILE
	assert "apk add" not in DOCKERFILE, (
		"Store builds must not apk-add — Alpine indexes are a second network "
		"dependency and fail on EOL bases. Use base-python instead."
	)


def test_build_yaml_matches_dockerfile_python_base():
	assert f"aarch64-base-python:{PYTHON_BASE_TAG}" in BUILD_YAML
	assert f"amd64-base-python:{PYTHON_BASE_TAG}" in BUILD_YAML
	assert re.search(r"base:\s*3\.20\b", BUILD_YAML) is None
	assert "apk add" not in BUILD_YAML


def test_store_description_says_what_vome_does():
	description = _folded_description().lower()
	assert "virtual switch" in description
	assert "remote access" in description
	assert "lan tunnel" in description
	assert "hacs" in description
	assert not description.startswith("installs the shared"), description


def test_addon_version_is_quoted_semver():
	match = re.search(r'^version:\s*"([^"]+)"', CONFIG, re.M)
	assert match, "config.yaml version must be a quoted string"
	assert re.fullmatch(r"\d+\.\d+\.\d+", match.group(1)), match.group(1)


def test_addon_exposes_portal_url_config():
	assert "portal_url:" in CONFIG
	trans = (ADDON / "translations" / "en.yaml").read_text(encoding="utf-8")
	assert "staging.vome.io" in trans
