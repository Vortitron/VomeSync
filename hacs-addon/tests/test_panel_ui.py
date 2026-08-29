# flake8: noqa
"""Panel UI contract: first-load must wait calmly, not dump diagnostics.

A new add-on install copies the integration to disk, then Home Assistant
needs one restart. Until then /api/status is 400 or 502. The old panel ran
diagnostics on that failure and painted a red "wrong version / not loaded"
card on every page — which stayed stale until the user clicked Refresh.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PANEL_JS = (ROOT / "vome" / "panel" / "static" / "app.js").read_text(encoding="utf-8")
PANEL_CSS = (ROOT / "vome" / "panel" / "static" / "styles.css").read_text(encoding="utf-8")


def _refresh_function() -> str:
	start = PANEL_JS.index("async function refresh(")
	end = PANEL_JS.index("\n	function setView(")
	return PANEL_JS[start:end]


def test_status_failure_polls_instead_of_dumping_diagnostics():
	refresh_fn = _refresh_function()
	assert "schedulePoll" in refresh_fn
	assert "classifyHaError" in refresh_fn
	assert "runDiagnostics" not in refresh_fn
	assert "await runDiagnostics()" not in PANEL_JS.split("async function refresh(")[1].split("function setView(")[0]


def test_expected_ha_gaps_are_info_not_errors():
	assert 'showBanner(kind.message, (kind.waiting || kind.info) ? "info" : "err")' in PANEL_JS
	assert "WAITING_RESTART" in PANEL_JS
	assert "WAITING_HA" in PANEL_JS
	assert "no need to click Refresh" in PANEL_JS
	assert "this page continues on its own" in PANEL_JS.lower() or "continues on its own afterwards" in PANEL_JS


def test_panel_retries_when_the_tab_becomes_visible():
	assert 'document.addEventListener("visibilitychange"' in PANEL_JS
	assert 'window.addEventListener("pageshow"' in PANEL_JS


def test_diagnostics_are_opt_in_on_about():
	assert "lastDiag && current === \"about\"" in PANEL_JS
	assert "Technical details" in PANEL_JS
	assert ".banner.info" in PANEL_CSS
	assert ".card.info-card" in PANEL_CSS


def test_502_and_400_classified_as_waiting():
	assert "502" in PANEL_JS
	assert "Bad Request" in PANEL_JS
	assert "Invalid JSON" in PANEL_JS or "HTTP ${res.status}" in PANEL_JS


def test_multiple_entries_error_is_plain_language():
	assert "MULTI_ENTRY_MSG" in PANEL_JS
	assert "More than one Vome integration" in PANEL_JS
	assert "pass entry_id" in PANEL_JS
	assert "extraEntriesCard" in PANEL_JS
