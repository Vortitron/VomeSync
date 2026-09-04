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


def test_connect_is_on_overview_even_when_ha_is_not_ready():
	overview = PANEL_JS.split("function renderOverview")[1].split("const fixExternal")[0]
	assert 'id="ov-connect"' in overview
	assert "vomeHomeLinked()" in overview
	assert "const hideConnect = vomeHomeLinked();" in overview
	assert "Restart Home Assistant once" in overview
	assert 'setView("link")' in overview
	assert "const goConnect = () => setView(\"link\");" in overview
	assert 'class="primary" id="qa-rdp"' not in overview
	assert 'id="qa-rdp" class="primary"' not in overview
	assert 'id="qa-rdp"' in overview
	assert 'class="primary" id="ov-connect"' in overview
	assert 'id="qa-connect"' in overview


def test_connect_lives_in_the_page_chrome():
	html = (ROOT / "vome" / "panel" / "static" / "index.html").read_text(encoding="utf-8")
	assert 'id="header-connect"' in html
	assert 'id="nav-link-label">Connect to Vome' in html
	assert 'id="portal-url"' not in html
	assert 'id="portal-staging"' not in html
	assert "site-switch" not in html
	assert "#qa-rdp.primary" in html
	assert "function syncChrome" in PANEL_JS
	assert "header-connect" in PANEL_JS
	assert "switch-sync leftovers" in PANEL_JS
	assert "Vome Home" in PANEL_JS


def test_connect_page_explains_the_flow_and_uses_addon_portal_url():
	html = (ROOT / "vome" / "panel" / "static" / "index.html").read_text(encoding="utf-8")
	assert "dials out" in PANEL_JS
	assert "addon_portal_url" in PANEL_JS
	assert "configuredPortalUrl" in PANEL_JS
	assert "only works on" in PANEL_JS
	assert "addon_portal_url" in PANEL_JS.split("function renderAbout")[1]
	assert "staging.vome.io" not in html
	assert "#qa-rdp.primary" in PANEL_CSS


def test_multiple_entries_error_is_plain_language():
	assert "MULTI_ENTRY_MSG" in PANEL_JS
	assert "More than one Vome integration" in PANEL_JS
	assert "pass entry_id" in PANEL_JS
	assert "extraEntriesCard" in PANEL_JS


# ── The health score in the panel ───────────────────────────────────────────

PANEL_HTML = (ROOT / "vome" / "panel" / "static" / "index.html").read_text(encoding="utf-8")
PANEL_SERVER = (ROOT / "vome" / "panel" / "server.py").read_text(encoding="utf-8")


def test_the_panel_can_run_a_check_and_read_the_result():
	"""Both halves, or the button is decoration: a POST that starts one
	and a GET that reads the last one."""
	assert '"/api/health_score/run": ("health_score_run", body)' in PANEL_SERVER
	assert 'call_service("health_score_get"' in PANEL_SERVER


def test_the_health_view_exists_and_is_reachable():
	assert 'health: "Health score"' in PANEL_JS
	assert 'current === "health"' in PANEL_JS
	# Reachable without hunting: a quick action on the overview.
	assert 'id="qa-health"' in PANEL_JS


def test_an_unsaved_run_shows_its_clock_and_the_way_to_keep_it():
	"""A guest check is deleted in two hours. A panel that showed the
	score without saying so would be the dishonest half of the feature."""
	assert "saved_to_account !== false" in PANEL_JS
	assert "keep_it_url" in PANEL_JS
	assert "deleted_in_seconds" in PANEL_JS
	assert "unless you sign in" in PANEL_JS


def test_it_says_what_leaves_the_house():
	"""The AI writes the summary; the page has to say what is sent."""
	assert "Only the findings are sent" in PANEL_JS
	assert "never your states, history, configuration or backups" in PANEL_JS


def test_the_check_is_polled_rather_than_waited_on():
	"""It takes a minute or two — holding the request open would look
	like a hung panel."""
	assert "function watchHealth(" in PANEL_JS
	assert "loadHealth(true)" in PANEL_JS
