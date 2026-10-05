# flake8: noqa
"""The door rules the home applies on its end-to-end name (e2e_door).

These mirror the edge proxy on *.home.vome.io, number for number, because a
home must behave the same whichever address it was reached on. The proxy
wiring is tested against a real Core in tests_core/test_e2e_remote_core.py.
"""
import json
import time

import jwt
import pytest

from custom_components.vomesync import e2e_door as door

KEY = b"k" * 32
HOST = "nyvyn.e2e.vome.io"


def _token(**over):
	claims = {"sub": "u1", "sid": "rly-1", "host": HOST, "scope": "ha-forward",
	          "iat": int(time.time()), "exp": int(time.time()) + 60}
	claims.update(over)
	key = claims.pop("_key", KEY)
	return jwt.encode(claims, key, algorithm="HS256")


class TestToken:
	def test_a_token_for_this_home_and_name(self):
		assert door.verify_token(_token(), KEY, HOST, "rly-1")["sub"] == "u1"
		assert door.verify_token(_token(host=HOST.upper()), KEY, HOST, "rly-1")

	@pytest.mark.parametrize("over", [
		{"_key": b"x" * 32},              # another home's key, or the shared secret
		{"host": "other.e2e.vome.io"},    # minted for another name
		{"sid": "rly-2"},                 # for another home
		{"scope": "lan-tcp"},             # some other kind of token
		{"exp": int(time.time()) - 1},    # expired
	])
	def test_everything_else_is_refused(self, over):
		assert door.verify_token(_token(**over), KEY, HOST, "rly-1") is None

	def test_no_token_or_key(self):
		assert door.verify_token(None, KEY, HOST, "rly-1") is None
		assert door.verify_token(_token(), b"", HOST, "rly-1") is None
		assert door.verify_token("not.a.jwt", KEY, HOST, "rly-1") is None

	def test_alg_none_is_refused(self):
		forged = jwt.encode({"sid": "rly-1", "host": HOST, "scope": "ha-forward"}, key=None, algorithm="none")
		assert door.verify_token(forged, KEY, HOST, "rly-1") is None


class TestRateLimiter:
	def test_per_visitor_per_bucket(self):
		now = [0.0]
		rl = door.RateLimiter({"general": 2, "auth": 1, "static": 5, "ws": 1}, clock=lambda: now[0])
		assert rl.spend("1.1.1.1", "auth") is None
		assert rl.spend("1.1.1.1", "auth") > 0
		assert rl.spend("2.2.2.2", "auth") is None  # someone else is unaffected
		assert rl.spend("1.1.1.1", "general") is None  # another bucket is separate
		now[0] = 301
		assert rl.spend("1.1.1.1", "auth") is None  # the window slides on

	def test_buckets(self):
		assert door.bucket_for("GET", "/frontend_latest/app.js", False) == "static"
		assert door.bucket_for("POST", "/auth/login_flow/x", False) == "auth"
		assert door.bucket_for("GET", "/api/websocket", True) == "ws"
		assert door.bucket_for("GET", "/lovelace/0", False) == "general"


class TestLoginGuard:
	def test_five_failures_block_and_the_ladder_climbs(self):
		now = [0.0]
		g = door.LoginGuard(clock=lambda: now[0])
		for _ in range(4):
			assert g.observe("1.1.1.1", "failure") is None
		assert g.observe("1.1.1.1", "failure") == 900
		assert g.blocked_for("1.1.1.1") > 0
		assert g.blocked_for("2.2.2.2") is None  # never per home: no lockout button
		now[0] += 901
		assert g.blocked_for("1.1.1.1") is None
		for _ in range(5):
			started = g.observe("1.1.1.1", "failure")
		assert started == 3600

	def test_a_success_clears_the_failures(self):
		g = door.LoginGuard(clock=lambda: 0.0)
		for _ in range(4):
			g.observe("1.1.1.1", "failure")
		g.observe("1.1.1.1", "success")
		assert g.observe("1.1.1.1", "failure") is None

	def test_failures_expire(self):
		now = [0.0]
		g = door.LoginGuard(clock=lambda: now[0])
		for _ in range(4):
			g.observe("1.1.1.1", "failure")
		now[0] = 901
		assert g.observe("1.1.1.1", "failure") is None

	def test_classify_what_core_counts(self):
		fail = json.dumps({"type": "form", "errors": {"base": "invalid_auth"}}).encode()
		assert door.classify_login_response(200, fail) == "failure"
		assert door.classify_login_response(200, json.dumps({"type": "create_entry"}).encode()) == "success"
		assert door.classify_login_response(200, json.dumps({"type": "form", "errors": {}}).encode()) is None
		assert door.classify_login_response(400, fail) is None
		assert door.classify_login_response(200, b"x" * (64 * 1024 + 1)) is None


class TestRequestShapes:
	def test_webhooks(self):
		assert door.is_webhook("POST", "/api/webhook/abc-123")
		assert not door.is_webhook("DELETE", "/api/webhook/abc")
		assert not door.is_webhook("POST", "/api/webhook/abc/../states")

	def test_login_flow(self):
		assert door.is_login_flow("POST", "/auth/login_flow/abc")
		assert not door.is_login_flow("GET", "/auth/login_flow")

	def test_cookies(self):
		header = "a=1; vome_e2e=tok%3D; vome_fwd=other"
		assert door.read_cookie(header, "vome_e2e") == "tok="
		assert door.strip_cookie(header, "vome_e2e") == "a=1; vome_fwd=other"
		assert door.read_cookie("", "vome_e2e") is None


class TestDoorFromVome:
	"""The home serves only behind a complete door; anything less stays shut."""

	def _settings(self, **door_over):
		import base64
		door = {"forward_key": base64.b64encode(KEY).decode(), "server_id": "rly-1",
		        "cookie": "vome_e2e", "cookie_ttl": 600, "gate_url": "https://vome.io/remote/gate"}
		door.update(door_over)
		return {"enabled": True, "host": HOST.upper(), "door": door}

	def test_a_complete_door(self):
		from custom_components.vomesync.e2e_remote import E2ERemote
		got = E2ERemote.door_from(self._settings())
		assert got["key"] == KEY and got["host"] == HOST and got["server_id"] == "rly-1"

	@pytest.mark.parametrize("over", [
		{"forward_key": ""}, {"forward_key": "c2hvcnQ="}, {"forward_key": "not base64!"},
		{"gate_url": ""}, {"gate_url": "http://vome.io/remote/gate"}, {"server_id": ""},
	])
	def test_anything_less_stays_shut(self, over):
		from custom_components.vomesync.e2e_remote import E2ERemote
		assert E2ERemote.door_from(self._settings(**over)) is None

	def test_no_door_at_all(self):
		from custom_components.vomesync.e2e_remote import E2ERemote
		assert E2ERemote.door_from({"enabled": True, "host": HOST}) is None
