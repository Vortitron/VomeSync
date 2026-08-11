# flake8: noqa
"""Assertions about Home Assistant internals that Vome depends on.

Everything here is a coupling to Core's *behaviour* rather than to a documented
API, so an HA upgrade can invalidate it without any deprecation warning and
without breaking an import.  These tests run against the installed Home
Assistant, so a version bump that changes one of these fails CI loudly instead
of silently switching a security control off.

**When Home Assistant is upgraded, run this file first.**  A failure here is
not a flaky test — it means a released behaviour we rely on has changed, and
something of ours is now wrong.  Each test says what breaks and where.

The couplings are deliberately few. Prefer adding one here to spreading an
assumption about Core through the codebase with nothing watching it.
"""
import inspect

import pytest


class TestLoginFlowFailureShape:
	"""The relay's brute-force guard reads Home Assistant's login responses.

	`webserver/src/proxy/loginGuard.js` cannot ask Core whether a login failed:
	it only sees the forwarded HTTP response.  Home Assistant answers a wrong
	password with **HTTP 200** and the error inside the body, so the guard
	matches on that body.  If this shape changes, the guard stops counting
	failures — no error, no log, just an unlimited password oracle on every
	friendly domain in `open` mode.
	"""

	def test_wrong_password_is_still_a_200_with_the_error_in_the_body(self):
		# If HA ever starts returning a 4xx here, the guard can be simplified to
		# a status check — but until it does, a status check would see every
		# failed login as a success.
		from homeassistant.components.auth.login_flow import LoginFlowBaseView

		source = inspect.getsource(LoginFlowBaseView._async_flow_result_to_response)
		assert "process_wrong_login" in source, (
			"HA no longer flags failed logins in _async_flow_result_to_response; "
			"re-derive what loginGuard.classifyLoginResponse should match on."
		)
		assert '"invalid_auth"' in source or "'invalid_auth'" in source, (
			"The invalid_auth error key is gone from HA's login flow. "
			"Update AUTH_FAILURE_CODES in webserver/src/proxy/loginGuard.js."
		)
		assert '"invalid_code"' in source or "'invalid_code'" in source, (
			"The invalid_code (MFA) error key is gone from HA's login flow. "
			"Update AUTH_FAILURE_CODES in webserver/src/proxy/loginGuard.js."
		)

	def test_the_result_type_values_the_guard_matches_on(self):
		from homeassistant.data_entry_flow import FlowResultType

		# These strings are what reach the guard once the result is serialised.
		assert FlowResultType.FORM.value == "form"
		assert FlowResultType.CREATE_ENTRY.value == "create_entry"

	def test_the_login_flow_url_the_guard_watches(self):
		from homeassistant.components.auth.login_flow import LoginFlowResourceView

		# LOGIN_FLOW_PATH_RE in loginGuard.js matches this prefix.
		assert LoginFlowResourceView.url == "/auth/login_flow/{flow_id}"


class TestForwardedMiddleware:
	"""Why the relay strips X-Forwarded-* before the tunnel.

	Core rejects a request carrying X-Forwarded-For unless the instance opted
	into `use_x_forwarded_for`.  A stock install has not, so forwarding that
	header 400s *every* request — this took every friendly domain down once
	already (fixed in ec5a13b).  If Core ever stops rejecting, the strip can be
	revisited; until then it must stay.
	"""

	def test_untrusted_forwarded_header_is_still_rejected(self):
		from homeassistant.components.http import forwarded

		source = inspect.getsource(forwarded.async_setup_forwarded)
		assert "HTTPBadRequest" in source, (
			"HA no longer 400s on an unexpected X-Forwarded-For. The strip in "
			"uiProxy.VOME_HOP_HEADERS may no longer be load-bearing — check "
			"before changing it."
		)
		assert "use_x_forwarded_for" in source


class TestIpBanIsNotUsableOverTheRelay:
	"""Why brute-force defence lives at the proxy and not in Core.

	Every relayed visitor reaches Core over loopback, so Core's own ban would
	see one client for everyone — and banning it would cut the relay off,
	losing all remote access for that home.  It is also off by default.
	"""

	def test_core_bans_nothing_unless_the_user_opts_in(self):
		from homeassistant.components.http import NO_LOGIN_ATTEMPT_THRESHOLD

		assert NO_LOGIN_ATTEMPT_THRESHOLD == -1

	def test_core_ban_keys_on_the_socket_peer_not_a_header(self):
		from homeassistant.components.http import ban

		source = inspect.getsource(ban.process_wrong_login)
		# request.remote is the loopback address the component dialled from, so
		# Core cannot tell one remote visitor from another.
		assert "request.remote" in source
