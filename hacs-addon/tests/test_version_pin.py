# flake8: noqa
"""Pin INTEGRATION_VERSION (what running code reports) to manifest.json.

The add-on panel detects "update installed but Home Assistant not restarted"
by comparing the constant the loaded module reports against the manifest on
disk.  That only works if the two are bumped together — this test enforces it.
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
COMPONENT = ROOT / "custom_components" / "vomesync"


def test_integration_version_matches_manifest():
	import sys
	sys.path.insert(0, str(ROOT))
	from custom_components.vomesync.const import INTEGRATION_VERSION

	manifest = json.loads((COMPONENT / "manifest.json").read_text())
	assert manifest["version"] == INTEGRATION_VERSION, (
		"custom_components/vomesync/manifest.json version and "
		"const.INTEGRATION_VERSION must be bumped together"
	)


def test_vendored_addon_copy_matches():
	vendored = ROOT / "vome" / "custom_components" / "vomesync" / "manifest.json"
	manifest = json.loads((COMPONENT / "manifest.json").read_text())
	vendored_manifest = json.loads(vendored.read_text())
	assert vendored_manifest["version"] == manifest["version"], (
		"run ./vome/build.sh — the add-on's vendored integration is stale"
	)
