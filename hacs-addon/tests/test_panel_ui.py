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
	assert '"/api/remote_address": ("get_remote_address", body)' in PANEL_SERVER


def test_the_health_view_exists_and_is_reachable():
	assert 'health: "Health score"' in PANEL_JS
	assert 'current === "health"' in PANEL_JS
	# Reachable without hunting: a quick action on the overview.
	assert 'id="qa-health"' in PANEL_JS
	assert 'id="qa-remote"' in PANEL_JS
	assert "function remoteAddressCard(" in PANEL_JS
	assert "/api/remote_address" in PANEL_JS
	assert "Reachable at" in PANEL_JS


def test_an_unsaved_run_shows_its_clock_and_the_way_to_keep_it():
	"""A guest check is deleted in a day. A panel that showed the
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


def test_the_health_score_is_in_the_side_menu():
	"""It was reachable only from a quick action on the Overview, which
	is fine for somebody already reading that card and invisible to
	anybody who left the page."""
	nav = PANEL_HTML[PANEL_HTML.index("<nav>"):PANEL_HTML.index("</nav>")]
	assert 'data-view="health"' in nav
	assert "Health score" in nav
	# Above the sections: it is the one thing that works before any of
	# them are configured.
	assert nav.index('data-view="health"') < nav.index("Remote access")


def test_the_ai_doctor_is_offered_on_each_finding():
	"""It runs at Vome, against the account that owns this home — so the
	panel links to it rather than pretending to host it."""
	assert "Ask the AI Doctor about this" in PANEL_JS
	assert "function healthUrls(" in PANEL_JS
	assert "Ask the AI Doctor" in PANEL_JS
	# Built from the Vome this home actually talks to, not a guess.
	assert "healthData.portal_url" in PANEL_JS or "healthData && healthData.portal_url" in PANEL_JS
	assert "portal_url" in (ROOT / "custom_components" / "vomesync" /
	                        "services_remote.py").read_text(encoding="utf-8")


def test_the_score_always_has_an_online_link():
	"""The old panel hid Open / Doctor / Share behind a stale linked
	flag, so a finished check in the app had nowhere to go."""
	assert "Open in Vome" in PANEL_JS
	assert "function healthActionRow(" in PANEL_JS
	assert "health_url" in PANEL_JS
	assert "card_url" in PANEL_JS


def test_the_card_can_be_bought_from_here():
	"""The score is free; the shareable card is the thing on sale."""
	assert "Publish or buy a shareable card" in PANEL_JS
	assert "#score-card" in PANEL_JS
	# Honest about what it is: no device names on a public page.
	assert "never device names" in PANEL_JS
	assert "Hosting and Connect" in PANEL_JS or "hosting and Connect" in PANEL_JS


def test_an_unsaved_check_still_has_somewhere_to_open():
	assert "Open it online and sign in" in PANEL_JS
	assert "keep_it_url" in PANEL_JS


def test_a_hosted_home_gets_a_forwarding_pill_that_matches_reality():
	"""GamlaBio is hosted, has no relay tunnel to gate, and its friendly
	domain forwards the HA UI regardless — the pill used to read
	unconditionally off the relay-only flag and call that "off"."""
	assert "state.hosted" in PANEL_JS
	assert "hosted by Vome" in PANEL_JS


def test_overview_shows_which_vome_this_is():
	"""'Linked' on its own does not say *which* Vome account/home — add the
	identity (server id or friendly domain) with a link to open it there."""
	assert "function vomeIdentityLine(" in PANEL_JS
	assert "in Vome" in PANEL_JS
	assert "state.forward_url" in PANEL_JS


# ── The coding-agent key ────────────────────────────────────────────────
# This is the one screen where somebody decides what a key reaching into
# their house may do, and then handles the key itself. Both halves have a
# way of going quietly wrong: a permission that is ticked by default when
# it should not be, and a key that cannot be copied because the clipboard
# is unavailable inside ingress.

PANEL_HTML = (ROOT / "vome" / "panel" / "static" / "index.html").read_text(encoding="utf-8")


def _agent_view() -> str:
	start = PANEL_JS.index("\tfunction renderAgent(")
	end = PANEL_JS.index("\tasync function agentAction(")
	return PANEL_JS[start:end]


def test_the_coding_agent_view_is_in_the_side_menu():
	assert 'data-view="agent"' in PANEL_HTML
	assert 'agent: "Coding agent"' in PANEL_JS
	assert 'current === "agent"' in PANEL_JS


def test_files_access_is_offered_but_never_ticked_by_default():
	"""ha:files reads secrets.yaml, and nobody has signed anything."""
	assert '"ha:files"' in PANEL_JS
	default_line = [
		line for line in PANEL_JS.splitlines()
		if "default_scopes" in line and "ha:read" in line
	]
	assert default_line, "the issue view must state its defaults"
	assert all("ha:files" not in line for line in default_line)


def test_each_permission_says_what_it_actually_means():
	assert "secrets.yaml" in PANEL_JS
	assert "locks, alarms" in PANEL_JS
	assert "Call services" in PANEL_JS


def test_the_key_is_shown_once_and_says_so():
	view = PANEL_JS[PANEL_JS.index("function agentKeyCard("):]
	assert "Shown once" in view
	assert "keeps only a hash" in view
	assert "Replace key" in view


def test_copying_falls_back_when_the_clipboard_is_refused():
	"""Ingress can refuse clipboard access; without a fallback the button
	silently does nothing and the key is unreachable."""
	copy_fn = PANEL_JS[PANEL_JS.index("function agentCopyJson("):]
	assert "navigator.clipboard" in copy_fn
	assert "selectNodeContents" in copy_fn
	assert "Ctrl/Cmd+C" in copy_fn


def test_the_trial_is_never_presented_as_permanent():
	view = _agent_view()
	assert "two days" in PANEL_JS
	assert "left" in view  # the clock is rendered, not just stored
	assert "Revoke" in view


def test_saving_permissions_promises_the_key_does_not_change():
	view = _agent_view()
	assert "mcp.json</code> keeps working" in view


def test_a_linked_house_is_sent_to_its_account_not_offered_a_trial():
	view = _agent_view()
	assert "linked_account" in view
	assert "/account/api-tokens" in view


def test_the_json_box_scrolls_rather_than_widening_the_panel():
	assert ".pre-scroll" in PANEL_CSS
	assert "overflow-x: auto" in PANEL_CSS
	assert ".scope-row" in PANEL_CSS


def test_an_expired_key_is_not_offered_buttons_that_cannot_work():
	"""The clock is Vome's. Once it runs out nothing in the panel can
	revive the key, so Save permissions and Replace key must not be the
	things on offer — clearing up and starting again are."""
	view = _agent_view()
	assert "data.active === false" in view
	assert "This key has expired" in view
	assert "Clear it and start again" in view
