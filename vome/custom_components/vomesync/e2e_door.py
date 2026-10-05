"""The Vome door, held by the home, for end-to-end remote access.

On ``*.home.vome.io`` Vome's edge proxy checks every visitor before Home
Assistant sees them: a Vome sign-in (a host-bound token), else the home's
policy (app access open, webhooks), else the gate; with per-visitor rate
limits and a block on repeated failed logins. On ``*.e2e.vome.io`` Vome
cannot see the traffic, so the home does the same here, by the same rules
and with the same numbers.

Tokens are signed with this home's own key (``door.forward_key`` from Vome),
so a token minted for another home, or the vome.io-wide cookie, never
verifies here. The visitor's address comes from Vome's router with each
connection: Home Assistant itself sees only loopback, which is why none of
this can be left to Core.

Pure logic, no I/O: :class:`~.e2e_remote.E2EProxy` wires it in.
"""
from __future__ import annotations

import json
import re
import time
from collections import deque
from typing import Optional

import jwt

TOKEN_SCOPE = "ha-forward"
PASS_PARAM = "vome_pass"

WEBHOOK_PATH_RE = re.compile(r"^/api/webhook/[A-Za-z0-9_~.-]+$")
WEBHOOK_METHODS = frozenset({"POST", "PUT", "GET", "HEAD"})
LOGIN_FLOW_RE = re.compile(r"^/auth/login_flow(/|$)")
AUTH_PATH_RE = re.compile(r"^/auth(/|$)")
STATIC_RE = re.compile(
	r"^/(frontend_latest|frontend_es5|static|local|hacsfiles|api/hassio_ingress/[^/]+/(static|assets))/"
	r"|\.(js|css|map|png|svg|ico|woff2?|ttf|json|webmanifest)$"
)

# The edge proxy's defaults (VomeSync-server src/config/config.js), so a home
# behaves the same whichever address it was reached on.
RATE_WINDOW_S = 300
RATE_MAX = {"general": 2400, "static": 8000, "auth": 30, "ws": 60}
LOGIN_FAIL_MAX = 5
LOGIN_FAIL_WINDOW_S = 900
LOGIN_BLOCK_LADDER_S = (900, 3600, 21600, 86400)
LOGIN_STRIKE_TTL_S = 7 * 86400
_MAX_CLASSIFY_BYTES = 64 * 1024
_AUTH_FAILURE_CODES = frozenset({"invalid_auth", "invalid_code"})
# Bound every per-visitor table: a flood of addresses must not grow us forever.
_MAX_TRACKED = 20000


def verify_token(token: Optional[str], key: bytes, host: str, server_id: str) -> Optional[dict]:
	"""The claims of a token Vome minted for this home and this name, else None."""
	if not token or not key:
		return None
	try:
		claims = jwt.decode(token, key, algorithms=["HS256"])
	except jwt.PyJWTError:
		return None
	if claims.get("scope") != TOKEN_SCOPE or str(claims.get("sid")) != str(server_id):
		return None
	if str(claims.get("host") or "").lower() != host.lower():
		return None
	return claims


def bucket_for(method: str, path: str, websocket: bool) -> str:
	if websocket:
		return "ws"
	if AUTH_PATH_RE.match(path):
		return "auth"
	if method.upper() == "GET" and STATIC_RE.search(path):
		return "static"
	return "general"


def is_webhook(method: str, path: str) -> bool:
	return method.upper() in WEBHOOK_METHODS and bool(WEBHOOK_PATH_RE.match(path))


def is_login_flow(method: str, path: str) -> bool:
	return method.upper() == "POST" and bool(LOGIN_FLOW_RE.match(path))


def classify_login_response(status: int, body: bytes) -> Optional[str]:
	"""'failure', 'success' or None: what Core itself counts (login_flow.py)."""
	if status != 200 or not body or len(body) > _MAX_CLASSIFY_BYTES:
		return None
	try:
		parsed = json.loads(body)
	except ValueError:
		return None
	if not isinstance(parsed, dict):
		return None
	if parsed.get("type") == "create_entry":
		return "success"
	errors = parsed.get("errors")
	if parsed.get("type") == "form" and isinstance(errors, dict) and errors.get("base") in _AUTH_FAILURE_CODES:
		return "failure"
	return None


class RateLimiter:
	"""Requests per visitor per bucket in a sliding five-minute window."""

	def __init__(self, limits=None, window_s: int = RATE_WINDOW_S, clock=time.monotonic) -> None:
		self.limits = dict(limits or RATE_MAX)
		self.window = window_s
		self.clock = clock
		self._hits: dict[tuple, deque] = {}

	def spend(self, peer: str, bucket: str) -> Optional[int]:
		"""None if allowed, else seconds to wait."""
		now = self.clock()
		key = (peer, bucket)
		hits = self._hits.get(key)
		if hits is None:
			if len(self._hits) >= _MAX_TRACKED:
				self._prune(now)
			hits = self._hits[key] = deque()
		while hits and now - hits[0] >= self.window:
			hits.popleft()
		if len(hits) >= self.limits.get(bucket, self.limits["general"]):
			return max(1, int(self.window - (now - hits[0])))
		hits.append(now)
		return None

	def _prune(self, now: float) -> None:
		for key in [k for k, v in self._hits.items() if not v or now - v[-1] >= self.window]:
			del self._hits[key]
		if len(self._hits) >= _MAX_TRACKED:
			self._hits.clear()


class LoginGuard:
	"""Blocks a visitor after repeated failed Home Assistant logins.

	Per address only, as on the edge: blocking per home would hand anyone a
	button that locks the owner out. Failures expire; one success clears
	them; each block is longer than the last within a week.
	"""

	def __init__(self, clock=time.monotonic) -> None:
		self.clock = clock
		self._fails: dict[str, deque] = {}
		self._strikes: dict[str, tuple[int, float]] = {}
		self._blocked: dict[str, float] = {}

	def blocked_for(self, peer: str) -> Optional[int]:
		until = self._blocked.get(peer)
		if until is None:
			return None
		left = until - self.clock()
		if left <= 0:
			del self._blocked[peer]
			return None
		return int(left) + 1

	def observe(self, peer: str, verdict: Optional[str]) -> Optional[int]:
		"""Record a login outcome; returns the block length if this one started a block."""
		now = self.clock()
		if verdict == "success":
			self._fails.pop(peer, None)
			return None
		if verdict != "failure":
			return None
		if len(self._fails) >= _MAX_TRACKED:
			self._fails.clear()
		fails = self._fails.setdefault(peer, deque())
		while fails and now - fails[0] >= LOGIN_FAIL_WINDOW_S:
			fails.popleft()
		fails.append(now)
		if len(fails) < LOGIN_FAIL_MAX:
			return None
		count, since = self._strikes.get(peer, (0, now))
		if now - since >= LOGIN_STRIKE_TTL_S:
			count = 0
		count += 1
		self._strikes[peer] = (count, now)
		duration = LOGIN_BLOCK_LADDER_S[min(count, len(LOGIN_BLOCK_LADDER_S)) - 1]
		self._blocked[peer] = now + duration
		self._fails.pop(peer, None)
		return duration


def strip_cookie(header: str, name: str) -> str:
	"""A Cookie header without ours: Home Assistant has no business seeing it."""
	parts = [p.strip() for p in (header or "").split(";")]
	return "; ".join(p for p in parts if p and p.split("=", 1)[0].strip() != name)


def read_cookie(header: str, name: str) -> Optional[str]:
	for part in (header or "").split(";"):
		key, _, value = part.strip().partition("=")
		if key == name and value:
			from urllib.parse import unquote
			return unquote(value)
	return None
