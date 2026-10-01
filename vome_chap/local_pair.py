"""Vome CHAP local pair — two Home Assistants in one house, no Vome needed.

Asked for on Facebook after the first internet-cut test (29 Sept 2026): a
second server in the house that takes over when the first dies, also when
the internet is down at the same time. The two installs coordinate between
themselves over the house network; Vome is never required.

How it works
------------

**Pairing.** On the main install the owner sets this add-on's ``local_pair``
option to ``main``; Home Assistant then shows a pairing code. On the other
install they set ``local_pair`` to ``standby`` and paste the code into
``pair_code``. The code carries the main install's address and a random
key. Everything between the two then goes over TLS 1.3 with that key as a
pre-shared key: only the two can connect, nothing on the network can read
or fake it, and there are no certificates to make.

**One install runs the home at a time.** Each pair keeps a ``holder`` (the
install running the home) and an ``epoch`` that goes up by one whenever the
home moves. When the two can talk, the higher epoch wins, so an install that
was cut off learns on its return that the home moved, and stops its own
Home Assistant.

**The standby takes over** when, for ``T_TAKE`` seconds together, it can
reach the house router but not the running install (neither this add-on nor
its Home Assistant). The router is the witness every home has: a standby
that cannot see the router either is the one cut off, and does nothing.

**The running install never stops itself** for losing sight of the others
(owner, 29 Sept 2026). When the house router dies, and it is often also the
switch the server hangs off, stopping would leave the house with no Home
Assistant at all -- worse than no standby. If only the running install's
cable was pulled, the standby takes over as well and both run until the
cable is back; then the one with the older epoch stops, and changes made on
it meanwhile are lost.

**Sync over the house network.** The running install builds the same
configuration snapshot as the Vome pairs (chap_sync.build_snapshot) and
serves it; the other fetches it when it changes and applies it while its
Core is stopped (chap_sync.apply_snapshot), following the running install's
Home Assistant version first when it is behind.

**The main install is the home's place** (owner, 29 Sept 2026: "we still
need to have one designated Live and the other standby"). After a takeover,
once the main install has been back and in step for ``BACK_AFTER``, the
home moves back to it the same careful way as a move the owner asks for. A
move the owner makes on purpose (to work on the main install) stays until
they move it back.

**The standby can be smaller**: on the main install's panel the owner picks
which of the standby's add-ons it runs when it stands in; the rest stay
stopped (all, until they choose).

Stdlib only, like the rest of the add-on. TLS with a pre-shared key needs
Python 3.13 (the add-on's base image); the rules here are plain functions.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import http.client
import http.server
import json
import logging
import secrets
import socket
import ssl
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional

LOG = logging.getLogger("vome-chap-local")

OPTIONS_FILE = "options.json"      # written by the Supervisor from the add-on's settings
PAIR_FILE = "local_pair.json"
SNAPSHOT_FILE = "lan_snapshot.tar"
SNAPSHOT_META = "lan_snapshot.json"
HELLO_FILE = "local_pair_hello.json"   # the server thread's news, taken up by the next pass

LAN_PORT = 8177
CODE_PREFIX = "vcp1."
# Two keys (security review, 1 Oct 2026). The code's key only introduces a
# standby: after that both sides use a key derived for that exact pair, so a
# pairing code seen later -- in a screenshot, an old notification -- opens
# nothing. CODE_IDENTITY keeps working for everything until the pair key has
# been used once, so pairs made with 0.2.0 carry on while they update.
PSK_IDENTITY = CODE_IDENTITY = "vome-chap-local-1"
PAIR_IDENTITY = "vome-chap-pair-1"
PAIR_SEEN_FILE = "local_pair_key_seen"

MAX_DOWNLOAD = 256 * 1024 * 1024   # a configuration snapshot from the other install
MAX_JSON = 1024 * 1024             # anything else it answers
MAX_CONNECTIONS = 16               # at once, on the house network

T_TAKE = 120               # the router in sight and the running install gone, this long
MOVE_TIMEOUT = 10 * 60     # a move the other install has not caught up with is called off
BACK_AFTER = 180           # the main install back, and in step, this long: the home goes home
PASS_SECONDS = 10          # how often a pass runs
SNAPSHOT_EVERY = 120       # the running install looks for configuration changes this often
PEER_TIMEOUT = 5
PEER_MISSING_NOTICE = 10 * 60   # tell the owner their standby has gone, after this long

MAIN, STANDBY, OFF = "main", "standby", "off"

NOTICE_CODE = "vome_chap_local_code"
NOTICE_RUNNING = "vome_chap_running_here"   # the same notice the Vome pairs use
NOTICE_PEER = "vome_chap_local_peer"

_lock = threading.RLock()


# ── Settings and state ────────────────────────────────────────────────────

def read_options(data_dir: Path) -> dict:
	"""The add-on's settings: ``{'mode': off|main|standby, 'code': str}``."""
	try:
		raw = json.loads((data_dir / OPTIONS_FILE).read_text(encoding="utf-8"))
	except (OSError, ValueError):
		raw = {}
	mode = raw.get("local_pair") if raw.get("local_pair") in (MAIN, STANDBY) else OFF
	return {"mode": mode, "code": str(raw.get("pair_code") or "").strip()}


def _settings_digest(options: dict) -> str:
	import hashlib
	return hashlib.sha256(f"{options['mode']}|{options['code']}".encode()).hexdigest()[:16]


def load_pair(data_dir: Path) -> dict:
	try:
		data = json.loads((data_dir / PAIR_FILE).read_text(encoding="utf-8"))
		return data if isinstance(data, dict) else {}
	except (OSError, ValueError):
		return {}


def save_pair(data_dir: Path, pair: dict) -> None:
	with _lock:
		path = data_dir / PAIR_FILE
		tmp = path.with_suffix(".tmp")
		tmp.write_text(json.dumps(pair), encoding="utf-8")
		tmp.chmod(0o600)
		tmp.replace(path)


def fresh_pair(options: dict, now: float) -> dict:
	"""A new identity for this install in a pair made from these settings.

	New whenever the settings change: a standby is often set up by restoring
	a backup of the main install, which brings this add-on's settings and
	data with it, and two installs must never share an identity.
	"""
	pair = {"id": secrets.token_hex(6), "mode": options["mode"], "settings": _settings_digest(options),
	        "created_at": now}
	if options["mode"] == MAIN:
		pair.update({"key": base64.b64encode(secrets.token_bytes(32)).decode(),
		             "holder": pair["id"], "epoch": 1})
	return pair


def current_pair(data_dir: Path, options: dict, now: float) -> Optional[dict]:
	"""This install's pair state for the settings as they are, or None when off."""
	if options["mode"] == OFF:
		return None
	with _lock:
		pair = load_pair(data_dir)
		if pair.get("settings") != _settings_digest(options) or not pair.get("id"):
			pair = fresh_pair(options, now)
			save_pair(data_dir, pair)
		return pair


def key_of(pair: dict) -> Optional[bytes]:
	try:
		key = base64.b64decode(pair.get("key") or "")
	except ValueError:
		return None
	return key if len(key) == 32 else None


def pair_key(pair: dict) -> Optional[bytes]:
	"""The key for this pair alone, once both installs know each other."""
	key, peer = key_of(pair), (pair.get("peer") or {}).get("id")
	if not key or not peer or not pair.get("id"):
		return None
	main, standby = (pair["id"], peer) if pair.get("mode") == MAIN else (peer, pair["id"])
	return hmac.new(key, f"vome-chap-pair|{main}|{standby}".encode(), hashlib.sha256).digest()


# ── The pairing code ──────────────────────────────────────────────────────

def make_code(address: str, port: int, key: bytes, main_id: str, name: str) -> str:
	body = json.dumps({"a": address, "p": port, "k": base64.b64encode(key).decode(),
	                   "i": main_id, "n": name}, separators=(",", ":"))
	return CODE_PREFIX + base64.urlsafe_b64encode(body.encode()).decode().rstrip("=")


def read_code(code: str) -> dict:
	"""``{'address', 'port', 'key', 'id', 'name'}`` from a pairing code.

	Raises ValueError with a sentence for the owner when it is not one.
	"""
	code = "".join((code or "").split())  # pasted across lines, or with spaces
	if not code.startswith(CODE_PREFIX):
		raise ValueError("that is not a Vome CHAP pairing code (it should start with vcp1.)")
	body = code[len(CODE_PREFIX):]
	try:
		data = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
		key = base64.b64decode(data["k"])
		out = {"address": str(data["a"]), "port": int(data["p"]), "key": key,
		       "id": str(data["i"]), "name": str(data.get("n") or data["a"])}
	except (ValueError, KeyError, TypeError):
		raise ValueError("the pairing code is incomplete; copy it again from the main install") from None
	if len(key) != 32 or not 0 < out["port"] < 65536:
		raise ValueError("the pairing code is damaged; copy it again from the main install")
	return out


# ── The rules (pure) ──────────────────────────────────────────────────────

def adopt(pair: dict, holder: Optional[str], epoch: Any) -> bool:
	"""Take the other install's view of who runs the home, if it is newer.

	The higher epoch wins; two installs on the same epoch that disagree
	(each thought the other gone at once) settle on the lower id, the same
	answer on both sides. Returns True when this install's view changed.
	"""
	try:
		epoch = int(epoch)
	except (TypeError, ValueError):
		return False
	if not holder:
		return False
	mine = int(pair.get("epoch") or 0)
	if epoch > mine or (epoch == mine and holder != pair.get("holder") and holder < str(pair.get("holder") or "~")):
		pair["holder"], pair["epoch"] = holder, epoch
		return True
	return False


def take_hello(pair: dict, data_dir: Path, now: float) -> Optional[str]:
	"""A standby that introduced itself since the last pass becomes the peer."""
	with _lock:
		path = data_dir / HELLO_FILE
		hello = _load(path)
		path.unlink(missing_ok=True)
	if not hello.get("id") or hello["id"] == pair.get("id"):
		return None
	new = (pair.get("peer") or {}).get("id") != hello["id"]
	pair["peer"] = {k: hello.get(k) for k in ("id", "name", "address", "port", "ha_port")}
	pair["peer_seen_at"] = now
	return f"{hello['name']} is this install's standby now" if new else None


def is_holder(pair: dict) -> bool:
	return bool(pair.get("holder")) and pair.get("holder") == pair.get("id")


def takeover_due(pair: dict, now: float, router_ok: bool, peer_ok: bool) -> Optional[str]:
	"""Should this install take the home? Updates the watch in ``pair``.

	Only an install that is not running the home, is paired, and has taken
	the running install's configuration at least once. The running install
	must have been out of reach -- its add-on and its Home Assistant -- for
	``T_TAKE`` while the router answered all along: an install that loses
	the router is the one cut off, and its clock starts again.
	"""
	if is_holder(pair) or not pair.get("peer"):
		pair.pop("peer_lost_since", None)
		return None
	if peer_ok or not router_ok:
		pair.pop("peer_lost_since", None)
		return None
	since = pair.setdefault("peer_lost_since", now)
	if not pair.get("applied_sha256"):
		return None  # nothing to run the home with yet
	if now - float(since) < T_TAKE:
		return None
	return (f"{peer_name(pair)} has not answered for {int(now - float(since))} s "
	        "while the house router did")


def peer_name(pair: dict) -> str:
	peer = pair.get("peer") or {}
	return peer.get("name") or peer.get("address") or "the other install"


# ── Transport: TLS 1.3 with the pair's key ────────────────────────────────

def _require_psk() -> None:
	if not getattr(ssl, "HAS_PSK", False) or not hasattr(ssl.SSLContext, "set_psk_client_callback"):
		raise RuntimeError("this Python cannot do TLS with a pre-shared key (needs 3.13)")


# Which key the connection being served proved: the handshake runs in the
# request's own thread (_TLSServer.get_request), so a thread-local is its.
_conn = threading.local()


def server_context(lookup: Callable[[str], Optional[bytes]]) -> ssl.SSLContext:
	"""A server context whose key is looked up, per handshake, by identity."""
	_require_psk()
	ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
	ctx.minimum_version = ssl.TLSVersion.TLSv1_3
	ctx.set_ciphers("PSK")

	def callback(identity):
		key = lookup(identity)
		_conn.identity = identity if key else None
		return key or b""
	ctx.set_psk_server_callback(callback, None)
	return ctx


def client_context(key: bytes, identity: str = CODE_IDENTITY) -> ssl.SSLContext:
	_require_psk()
	ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
	ctx.check_hostname = False
	ctx.verify_mode = ssl.CERT_NONE  # the key is the proof, both ways
	ctx.minimum_version = ssl.TLSVersion.TLSv1_3
	ctx.set_ciphers("PSK")
	ctx.set_psk_client_callback(lambda hint: (identity, key))
	return ctx


def call_peer(address: str, port: int, key: bytes, method: str, path: str,
              body: Optional[dict] = None, timeout: float = PEER_TIMEOUT,
              sink: Optional[Path] = None, identity: str = CODE_IDENTITY) -> tuple[int, Any, dict]:
	"""One request to the other install. ``(status, json or None, headers)``;
	status 0 when it could not be reached. With ``sink`` a body is written
	there instead of parsed (a snapshot). Nothing larger than MAX_DOWNLOAD
	(a snapshot) or MAX_JSON (anything else) is taken: 413."""
	conn = http.client.HTTPSConnection(address, port, timeout=timeout,
	                                   context=client_context(key, identity))
	try:
		data = json.dumps(body).encode() if body is not None else None
		conn.request(method, path, body=data, headers={"Content-Type": "application/json"} if data else {})
		resp = conn.getresponse()
		headers = {k.lower(): v for k, v in resp.getheaders()}
		if sink is not None and resp.status == 200:
			taken = 0
			with open(sink, "wb") as fh:
				while True:
					chunk = resp.read(1 << 20)
					if not chunk:
						break
					taken += len(chunk)
					if taken > MAX_DOWNLOAD:
						break
					fh.write(chunk)
			if taken > MAX_DOWNLOAD:
				sink.unlink(missing_ok=True)
				return 413, None, headers
			return resp.status, None, headers
		raw = resp.read(MAX_JSON + 1)
		if len(raw) > MAX_JSON:
			return 413, None, headers
		try:
			return resp.status, (json.loads(raw) if raw else None), headers
		except ValueError:
			return resp.status, None, headers
	except (OSError, ssl.SSLError, http.client.HTTPException):
		return 0, None, {}
	finally:
		conn.close()


class _Handler(http.server.BaseHTTPRequestHandler):
	server_version = "VomeCHAP"
	timeout = 60

	def log_message(self, fmt, *args):  # the add-on log is for the owner
		LOG.debug("lan: " + fmt, *args)

	def _json(self, status: int, body: Any) -> None:
		raw = json.dumps(body).encode()
		self.send_response(status)
		self.send_header("Content-Type", "application/json")
		self.send_header("Content-Length", str(len(raw)))
		self.end_headers()
		self.wfile.write(raw)

	def _send(self, status: int, answer: Any, headers: dict) -> None:
		if not isinstance(answer, Path):
			return self._json(status, answer)
		self.send_response(status)
		self.send_header("Content-Type", "application/x-tar")
		self.send_header("Content-Length", str(answer.stat().st_size))
		for k, v in headers.items():
			self.send_header(k, v)
		self.end_headers()
		with open(answer, "rb") as fh:
			while True:
				chunk = fh.read(1 << 20)
				if not chunk:
					break
				self.wfile.write(chunk)
		return None

	def do_GET(self):  # noqa: N802 - http.server's naming
		self._send(*self.server.lan.answer("GET", self.path, None,  # type: ignore[attr-defined]
		                                   identity=getattr(_conn, "identity", None)))

	def do_POST(self):  # noqa: N802
		try:
			length = min(int(self.headers.get("Content-Length") or 0), 64 * 1024)
			body = json.loads(self.rfile.read(length) or b"{}")
		except (ValueError, OSError):
			return self._json(400, {"error": "bad request"})
		self._send(*self.server.lan.answer("POST", self.path, body,  # type: ignore[attr-defined]
		                                   identity=getattr(_conn, "identity", None)))


class _TLSServer(http.server.ThreadingHTTPServer):
	daemon_threads = True
	allow_reuse_address = True

	def __init__(self, address, ctx: ssl.SSLContext):
		super().__init__(address, _Handler)
		self.ctx = ctx
		self._slots = threading.BoundedSemaphore(MAX_CONNECTIONS)

	def process_request(self, request, client_address):
		# A few at a time: the other install makes one request a pass.
		if not self._slots.acquire(blocking=False):
			self.shutdown_request(request)
			return
		super().process_request(request, client_address)

	def process_request_thread(self, request, client_address):
		try:
			super().process_request_thread(request, client_address)
		finally:
			self._slots.release()

	def get_request(self):
		sock, addr = self.socket.accept()
		sock.settimeout(30)
		# The handshake happens in the request's own thread, on first read:
		# a slow or hostile client cannot hold up the others.
		return self.ctx.wrap_socket(sock, server_side=True, do_handshake_on_connect=False), addr


class LanServer:
	"""This install's side of the pair on the house network (``LAN_PORT``)."""

	def __init__(self, data_dir: Path, status: Callable[[], dict], port: int = LAN_PORT,
	             bind: str = "0.0.0.0"):
		self.data_dir, self._status, self.port, self.bind = data_dir, status, port, bind
		self._httpd: Optional[_TLSServer] = None

	def status(self) -> dict:
		return self._status()

	def lookup(self, identity: str) -> Optional[bytes]:
		"""The key a connecting install must hold, by the identity it gives."""
		pair = load_pair(self.data_dir)
		if identity == PAIR_IDENTITY:
			return pair_key(pair)
		if identity == CODE_IDENTITY:
			return key_of(pair)
		return None

	def answer(self, method: str, path: str, body: Any, identity: Optional[str] = PAIR_IDENTITY) -> tuple[int, Any, dict]:
		"""One request from the other install: ``(status, json or file, headers)``.

		``identity``: the key it proved. The code's key introduces a standby
		(hello); for anything else it is refused once the pair key has been
		used.
		"""
		seen = self.data_dir / PAIR_SEEN_FILE
		if identity == PAIR_IDENTITY and not seen.exists():
			try:
				seen.touch(mode=0o600)
			except OSError:
				pass
		elif identity != PAIR_IDENTITY and not (method == "POST" and path == "/v1/hello") and seen.exists():
			return 403, {"error": "use the pair's own key"}, {}
		if method == "GET" and path == "/v1/status":
			return 200, self.status(), {}
		if method == "GET" and path == "/v1/snapshot":
			snap = self.data_dir / SNAPSHOT_FILE
			meta = _load(self.data_dir / SNAPSHOT_META)
			if not is_holder(load_pair(self.data_dir)) or not snap.exists() or not meta.get("sha256"):
				return 404, {"error": "no snapshot here"}, {}
			return 200, snap, {"X-Sha256": meta["sha256"]}
		if method == "POST" and path == "/v1/hello":
			status, reply = self.hello(body if isinstance(body, dict) else {})
			return status, reply, {}
		return 404, {"error": "unknown"}, {}

	def hello(self, body: dict) -> tuple[int, dict]:
		"""The standby introduces itself to the main install.

		Written to its own file for the next pass to take up: a pass holds the
		pair's state for seconds at a time and would write over it.
		"""
		pair = load_pair(self.data_dir)
		peer_id = str(body.get("id") or "")
		if not peer_id or not body.get("address"):
			return 400, {"error": "who are you?"}
		if peer_id == pair.get("id"):
			return 409, {"error": "this install has the same identity as the main one"}
		known = (pair.get("peer") or {}).get("id")
		if known and known != peer_id:
			# A second install with the code: it does not replace the standby.
			return 409, {"error": "this main install already has a standby; to pair another, "
			                      "turn its local_pair off and on again for a new code"}
		try:
			port = int(body.get("port") or LAN_PORT)
		except (TypeError, ValueError):
			return 400, {"error": "bad port"}
		with _lock:
			path = self.data_dir / HELLO_FILE
			path.write_text(json.dumps({
				"id": peer_id, "name": str(body.get("name") or body["address"])[:80],
				"address": str(body["address"])[:64], "port": port, "ha_port": body.get("ha_port"),
				"at": time.time()}), encoding="utf-8")
			path.chmod(0o600)
		return 200, self.status()

	def ensure(self, key: Optional[bytes] = None) -> None:
		"""Listening (the keys are looked up per connection, so once is enough)."""
		if self._httpd:
			return
		httpd = _TLSServer((self.bind, self.port), server_context(self.lookup))
		httpd.lan = self  # type: ignore[attr-defined]
		threading.Thread(target=httpd.serve_forever, name="vome-chap-lan", daemon=True).start()
		self._httpd = httpd
		LOG.info("listening on the house network, port %s", httpd.server_address[1])

	@property
	def bound_port(self) -> Optional[int]:
		return self._httpd.server_address[1] if self._httpd else None

	def stop(self) -> None:
		if self._httpd:
			self._httpd.shutdown()
			self._httpd.server_close()
			self._httpd = None


def _load(path: Path) -> dict:
	try:
		data = json.loads(path.read_text(encoding="utf-8"))
		return data if isinstance(data, dict) else {}
	except (OSError, ValueError):
		return {}


# ── The witness: the house router ─────────────────────────────────────────

def router_answers(gateway: Optional[str], timeout: float = 2.0) -> bool:
	"""Does the house router answer? A refused connection is an answer too."""
	if not gateway:
		return False
	for port in (53, 80, 443):
		try:
			socket.create_connection((gateway, port), timeout=timeout).close()
			return True
		except ConnectionRefusedError:
			return True
		except OSError:
			continue
	try:
		return subprocess.run(["ping", "-c", "1", "-W", "2", gateway], capture_output=True,
		                      timeout=5).returncode == 0
	except (OSError, subprocess.SubprocessError):
		return False


def ha_answers(address: str, port: Optional[int], timeout: float = 3.0) -> bool:
	"""Is a Home Assistant serving at this address? (manifest.json, no login)"""
	import urllib.request
	for p in ([port] if port else [8123, 80]):
		try:
			with urllib.request.urlopen(f"http://{address}:{p}/manifest.json", timeout=timeout) as resp:
				if resp.status == 200:
					return True
		except OSError:
			continue
	return False


# ── One pass ──────────────────────────────────────────────────────────────

class Env:
	"""What a pass needs from the Supervisor and the rest of the add-on.

	Built from chap_sync (the worker module) in the add-on; tests pass their
	own. Kept here as one object so the rules above stay plain functions.
	"""

	def __init__(self, cs, data_dir: Path, config_dir: Path, server: Optional[LanServer] = None,
	             clock: Callable[[], float] = time.time):
		self.cs, self.data_dir, self.config_dir, self.clock = cs, data_dir, config_dir, clock
		self.server = server

	# Overridable in tests
	def network(self) -> list:
		return self.cs.network_addresses()

	def router_ok(self, gateway: Optional[str]) -> bool:
		return router_answers(gateway)

	def ha_ok(self, address: str, port: Optional[int]) -> bool:
		return ha_answers(address, port)

	def peer(self, pair: dict, method: str, path: str, body=None, sink=None):
		"""The pair's own key; the code's key only until the pair key has
		worked once (the other side may still be on 0.2.0, or not know us)."""
		peer = pair.get("peer") or {}
		keys = []
		own = pair_key(pair)
		if own:
			keys.append((PAIR_IDENTITY, own))
		if not own or not pair.get("pair_key_ok"):
			keys.append((CODE_IDENTITY, key_of(pair)))
		for identity, key in keys:
			got = call_peer(peer["address"], int(peer.get("port") or LAN_PORT), key,
			                method, path, body, sink=sink, identity=identity)
			if got[0]:
				if identity == PAIR_IDENTITY and got[0] != 403:
					pair["pair_key_ok"] = True
				return got
		return 0, None, {}

	def core_stopped(self) -> bool:
		return self.cs.core_is_stopped()

	def set_core(self, running: bool) -> bool:
		return self.cs.set_core_running(running)

	def core_version(self) -> str:
		return self.cs.core_version()

	def core_port(self) -> Optional[int]:
		return self.cs.core_port()

	def notify(self, notice_id: str, title: str, message: str) -> bool:
		status, _ = self.cs._supervisor_call("POST", "/core/api/services/persistent_notification/create",
		                                     {"notification_id": notice_id, "title": title, "message": message},
		                                     timeout=15)
		return status == 200

	def dismiss(self, notice_id: str) -> bool:
		status, _ = self.cs._supervisor_call("POST", "/core/api/services/persistent_notification/dismiss",
		                                     {"notification_id": notice_id}, timeout=15)
		return status == 200

	def home_addons(self) -> Optional[list]:
		return self.cs.installed_addons()

	def set_addons(self, slugs: list, running: bool) -> list:
		return self.cs.set_addons_running(slugs, running)

	def addon_names(self) -> Optional[dict]:
		listed = self.cs.addon_list()
		return {a["slug"]: a["name"] for a in listed} if listed is not None else None


def _house_address(network: list) -> tuple[Optional[str], Optional[str]]:
	"""This install's first private IPv4 address on the house network, and its router."""
	import ipaddress
	for entry in network or []:
		address = str(entry.get("address") or "").split("/")[0]
		try:
			ip = ipaddress.ip_address(address)
		except ValueError:
			continue
		if ip.version == 4 and ip.is_private and not ip.is_loopback and not ip.is_link_local:
			return address, entry.get("gateway")
	return None, None


def my_status(pair: dict, env: Env) -> dict:
	"""What this install tells the other on each request."""
	meta = _load(env.data_dir / SNAPSHOT_META)
	return {"id": pair.get("id"), "name": pair.get("name"), "holder": pair.get("holder"),
	        "mode": pair.get("mode"), "home_addons": pair.get("home_addons"),
	        "standby_addons": pair.get("standby_addons") if pair.get("mode") == MAIN else None,
	        "epoch": pair.get("epoch"), "address": pair.get("address"), "ha_port": pair.get("ha_port"),
	        "ha_version": pair.get("ha_version"), "applied_sha256": pair.get("applied_sha256"),
	        "moving_to": pair.get("moving_to"),
	        "snapshot": {k: meta.get(k) for k in ("sha256", "created_at", "ha_version", "file_count")}
	        if is_holder(pair) and meta.get("sha256") else None}


def _hold_core(pair: dict, env: Env, running: bool) -> Optional[str]:
	"""Core running here only on the install that runs the home.

	Started again only if the pair stopped it: a Core the owner stopped
	stays stopped. Stopping also turns its boot off, so a restart of a
	standby does not bring it back (chap_sync.set_core_running).
	"""
	if running:
		if not pair.get("core_stopped_by_pair"):
			return None
		if not env.set_core(True):
			return "could not start Home Assistant; will retry"
		pair["core_stopped_by_pair"] = False
		return "started Home Assistant: this install runs the home"
	if pair.get("core_stopped_by_pair") and env.core_stopped():
		return None
	if not env.set_core(False):
		return "could not stop Home Assistant; will retry"
	pair["core_stopped_by_pair"] = True
	return f"stopped Home Assistant: {peer_name(pair)} runs the home"


# Tools for looking after the box itself, not part of the home: they keep
# running on a standby, where they are how the owner reaches it.
ADMIN_ADDONS = ("_ssh", "_configurator", "_vscode", "_samba", "_terminal")


def held_addons(slugs) -> list:
	return [s for s in slugs or [] if not s.endswith(("_vome", "_vome_chap") + ADMIN_ADDONS)
	        and s not in ("core_ssh", "core_configurator", "core_samba")]


def _hold_addons(pair: dict, env: Env, running: bool) -> Optional[str]:
	"""The home's add-ons run on one install only, like its Home Assistant.

	The ones stopped here are remembered and started again on a takeover.
	"""
	if not running and not pair.get("held_addons"):
		found = env.home_addons()
		if found is None:
			return None
		pair["held_addons"] = held_addons(found)
		names = env.addon_names() or {}
		pair["home_addons"] = [{"slug": s, "name": names.get(s, s)} for s in pair["held_addons"]]
	held = list(pair.get("held_addons") or [])
	if not held:
		return None
	# A standby standing in runs what the owner chose for it on the main
	# install's panel; None (never chosen) is all of them.
	allowed = pair.get("allowed_addons") if pair.get("mode") == STANDBY else None
	target = [s for s in held if allowed is None or s in allowed] if running else []
	if pair.get("held_addons_target") == target:
		return None
	failed = env.set_addons([s for s in held if s not in target], False) + env.set_addons(target, True)
	if failed:
		return f"add-ons not yet as they should be: {', '.join(failed)}; will retry"
	pair["held_addons_target"] = target
	return (f"running {len(target)} of {len(held)} add-on(s)" if running else f"stopped {len(held)} add-on(s)")


def _snapshot_if_due(pair: dict, env: Env, now: float, force: bool = False) -> Optional[str]:
	meta = _load(env.data_dir / SNAPSHOT_META)
	if not force and meta.get("sha256") and now - float(meta.get("built_at") or 0) < SNAPSHOT_EVERY:
		return None
	blob, info = env.cs.build_snapshot(env.config_dir)
	if len(blob) > env.cs.MAX_SNAPSHOT_BYTES:
		return f"configuration too large to send ({len(blob)} bytes)"
	changed = info["sha256"] != meta.get("sha256")
	if changed:
		tmp = env.data_dir / (SNAPSHOT_FILE + ".tmp")
		tmp.write_bytes(blob)
		tmp.replace(env.data_dir / SNAPSHOT_FILE)
	env.cs.save_json(env.data_dir / SNAPSHOT_META, {
		"sha256": info["sha256"], "file_count": info.get("file_count"),
		"ha_version": info.get("ha_version") or pair.get("ha_version"),
		"created_at": now if changed else meta.get("created_at", now), "built_at": now})
	return "configuration changed; ready for the other install" if changed else None


def _take_snapshot(pair: dict, env: Env, peer_status: dict, now: float) -> Optional[str]:
	"""Bring this stopped install up to the running one's configuration."""
	snap = peer_status.get("snapshot") or {}
	sha = snap.get("sha256")
	if not sha or sha == pair.get("applied_sha256"):
		return None
	if not env.core_stopped():
		return "Home Assistant is running here; not taking the configuration"
	target = str(snap.get("ha_version") or peer_status.get("ha_version") or "").strip()
	local = env.core_version()
	if env.cs.version_blocker(target, local):
		done = env.cs.follow_core(target, local, pair, env.data_dir / PAIR_FILE, now,
		                          core_stopped=env.core_stopped, set_running=env.set_core)
		return done or env.cs.version_blocker(target, local)
	sink = env.data_dir / (SNAPSHOT_FILE + ".in")
	status, _, headers = env.peer(pair, "GET", "/v1/snapshot", sink=sink)
	if status != 200:
		return f"could not fetch the configuration ({status or 'no answer'})"
	try:
		blob = sink.read_bytes()
	finally:
		sink.unlink(missing_ok=True)
	if env.cs.content_hash(blob) != sha or headers.get("x-sha256") not in (None, sha):
		return "the configuration changed while it came; fetching again"
	try:
		result = env.cs.apply_snapshot(env.config_dir, blob)
	except env.cs.ApplyRefused as exc:
		return f"did not take the configuration: {exc}"
	pair.update({"applied_sha256": sha, "applied_at": now})
	return f"took the configuration ({result['written']} written, {result['removed']} removed)"


def ask_move(data_dir: Path, cancel: bool = False) -> tuple[bool, str]:
	"""The owner's "move the home to the other install" (or calling it off).

	Only on the install running the home, with a standby that answers. The
	pass does the rest (_move_step).
	"""
	with _lock:
		pair = load_pair(data_dir)
		if cancel:
			if not pair.get("moving_to"):
				return False, "no move to call off"
			pair["move_cancelled"] = True
			save_pair(data_dir, pair)
			return True, "calling the move off"
		if not is_holder(pair):
			return False, "this install is not the one running the home"
		if not pair.get("peer"):
			return False, "there is no other install to move the home to"
		if pair.get("moving_to"):
			return False, "a move is already under way"
		pair["moving_to"] = pair["peer"].get("id")  # the pass stamps it, by its own clock
		for k in ("move_cancelled", "move_asked_at", "move_snapshot"):
			pair.pop(k, None)
		save_pair(data_dir, pair)
	return True, f"moving the home to {peer_name(pair)}"


def choose_standby_addons(data_dir: Path, slugs: Optional[list]) -> tuple[bool, str]:
	"""The owner's pick, on the main install, of what the standby runs when it
	stands in. None: all of them."""
	with _lock:
		pair = load_pair(data_dir)
		if pair.get("mode") != MAIN:
			return False, "choose on the main install"
		known = {a.get("slug") for a in pair.get("peer_addons") or []}
		pair["standby_addons"] = None if slugs is None else sorted(s for s in slugs if s in known)
		save_pair(data_dir, pair)
	return True, "saved what the standby runs when it stands in"


def _move_step(pair: dict, env: Env, peer_status: Optional[dict], now: float) -> Optional[str]:
	"""Hand the home to the other install, losing nothing.

	Home Assistant here stops first, so the last copy of its configuration
	is complete; the other install takes that copy; only then does the home
	move (a new epoch naming it), and it starts there on its next pass. Called
	off by the owner, or when the other install has not caught up within
	``MOVE_TIMEOUT``: then Home Assistant starts here again.
	"""
	if not pair.get("moving_to") or not is_holder(pair):
		return None
	pair.setdefault("move_asked_at", now)
	if pair.get("move_cancelled") or now - float(pair.get("move_asked_at") or now) > MOVE_TIMEOUT:
		why = "called off" if pair.get("move_cancelled") else f"{peer_name(pair)} did not catch up in time"
		for k in ("moving_to", "move_asked_at", "move_cancelled", "move_snapshot"):
			pair.pop(k, None)
		# Home Assistant here was stopped for the move: _hold_core starts it again.
		return f"move {why}; this install keeps the home"
	if not pair.get("move_snapshot"):
		if not env.core_stopped():
			if not env.set_core(False):
				return "move: could not stop Home Assistant here; will retry"
			pair["core_stopped_by_pair"] = True
			return "move: stopped Home Assistant here for a last, complete copy"
		_snapshot_if_due(pair, env, now, force=True)
		pair["move_snapshot"] = _load(env.data_dir / SNAPSHOT_META).get("sha256")
		return "move: last copy made; waiting for the other install to take it"
	if not peer_status or peer_status.get("applied_sha256") != pair["move_snapshot"]:
		return None  # it fetches on its own pass
	pair.update({"holder": pair["moving_to"], "epoch": int(pair.get("epoch") or 0) + 1})
	for k in ("moving_to", "move_asked_at", "move_snapshot", "took_over", "main_back_since", "move_back"):
		pair.pop(k, None)
	return f"moved the home to {peer_name(pair)} (move {pair['epoch']})"


def back_due(pair: dict, peer_status: Optional[dict], now: float, in_step: bool) -> Optional[str]:
	"""Should the home go back to the main install now? Updates the watch.

	Only on a standby running the home because it took over (not because the
	owner moved it here), with the main install answering and in step for
	``BACK_AFTER`` together: a main install that flaps keeps its clock at 0.
	"""
	if not (is_holder(pair) and pair.get("mode") == STANDBY and pair.get("took_over")) or pair.get("moving_to"):
		pair.pop("main_back_since", None)
		return None
	if not peer_status or peer_status.get("mode") != MAIN or not in_step:
		pair.pop("main_back_since", None)
		return None
	since = pair.setdefault("main_back_since", now)
	if now - float(since) < BACK_AFTER:
		return None
	return f"{peer_name(pair)} has been back and in step for {int(now - float(since))} s"


def _say_running_here(pair: dict, env: Env) -> None:
	kind = pair.get("say_running_here")
	this = pair.get("name") or "This install"
	if kind == "back":
		title, message = (f"Back on {this}",
		                  f"**{this}** is running your home again, with everything changed on "
		                  f"{peer_name(pair)} while it was away. {peer_name(pair)} is standing by.")
	elif kind == "moved":
		title, message = (f"You are on {this}",
		                  f"As you asked, **{this}** is running your home; {peer_name(pair)} is "
		                  "standing by. Move it back from the Vome CHAP panel when you are done.")
	else:
		when = time.strftime("%H:%M", time.localtime(float(pair.get("took_over_at") or env.clock())))
		title, message = ("This Home Assistant is running your home",
		                  f"**{this}** took over your home at {when}: {peer_name(pair)} stopped "
		                  "answering while the house router still did.\n\nWhen it is back and has "
		                  "taken this one's changes, your home moves back to it by itself.")
	if env.notify(NOTICE_RUNNING, title, message):
		pair.pop("say_running_here", None)


def run_local_once(options: dict, env: Env) -> tuple[str, int]:
	"""One pass of the local pair. Returns ``(what happened, seconds to wait)``."""
	now = env.clock()
	pair = current_pair(env.data_dir, options, now)
	if pair is None:
		if env.server:
			env.server.stop()
		return "local pair off", 60
	notes: list[str] = []

	network = env.network()
	address, gateway = _house_address(network)
	pair.update({"address": address, "ha_port": env.core_port() or pair.get("ha_port"),
	             "ha_version": env.core_version() or pair.get("ha_version")})
	pair.setdefault("name", f"Home Assistant at {address}" if address else "this Home Assistant")

	if options["mode"] == STANDBY and not key_of(pair):
		try:
			code = read_code(options["code"])
		except ValueError as exc:
			save_pair(env.data_dir, pair)
			return f"standby: {exc}", 60
		pair.update({"key": base64.b64encode(code["key"]).decode(),
		             "peer": {"id": code["id"], "name": code["name"], "address": code["address"],
		                      "port": code["port"]},
		             "holder": code["id"], "epoch": 0})
	key = key_of(pair)
	if env.server and key:
		env.server.ensure(key)
	joined = take_hello(pair, env.data_dir, now)
	if joined:
		notes.append(joined)

	# Talk to the other install.
	peer_status = None
	if pair.get("peer"):
		if options["mode"] == STANDBY and not pair.get("introduced"):
			status, body, _ = env.peer(pair, "POST", "/v1/hello",
			                           {"id": pair["id"], "name": pair["name"], "address": address,
			                            "port": LAN_PORT, "ha_port": pair.get("ha_port")})
			if status == 200 and isinstance(body, dict):
				pair["introduced"] = now
				notes.append(f"paired with {peer_name(pair)}")
				peer_status = body
			elif status == 409:
				save_pair(env.data_dir, pair)
				return "standby: the main install says this one is a copy of it; change the settings and save again", 60
		if peer_status is None:
			status, body, _ = env.peer(pair, "GET", "/v1/status")
			peer_status = body if status == 200 and isinstance(body, dict) else None
	if peer_status:
		if peer_status.get("id") == pair.get("id"):
			peer_status = None  # a copy of this install answering: not the other one
		else:
			pair["peer_seen_at"] = now
			pair["peer_applied_sha256"] = peer_status.get("applied_sha256")
			if peer_status.get("mode") == MAIN and pair.get("mode") == STANDBY:
				pair["allowed_addons"] = peer_status.get("standby_addons")
			if pair.get("mode") == MAIN:
				pair["peer_addons"] = peer_status.get("home_addons")
			peer = pair.setdefault("peer", {})
			for k in ("name", "address", "ha_port", "id"):
				if peer_status.get(k):
					peer[k] = peer_status[k]
			if adopt(pair, peer_status.get("holder"), peer_status.get("epoch")):
				notes.append(("this install runs the home" if is_holder(pair)
				              else f"{peer_name(pair)} runs the home") + f" (move {pair['epoch']})")
				for k in ("took_over", "main_back_since", "moving_to", "move_asked_at", "move_snapshot"):
					pair.pop(k, None)
				if is_holder(pair):
					pair["say_running_here"] = "back" if pair.get("mode") == MAIN else "moved"

	peer_ok = bool(peer_status)
	if pair.get("peer") and not peer_ok:
		peer = pair["peer"]
		peer_ok = env.ha_ok(peer.get("address") or "", peer.get("ha_port"))
	router_ok = env.router_ok(gateway)
	pair["router_ok"] = router_ok

	reason = takeover_due(pair, now, router_ok, peer_ok)
	if reason:
		pair.update({"holder": pair["id"], "epoch": int(pair.get("epoch") or 0) + 1,
		             "took_over_at": now, "took_over": True, "say_running_here": True})
		pair.pop("peer_lost_since", None)
		notes.append(f"taking over the home: {reason}")
		LOG.warning("taking over the home: %s", reason)

	if is_holder(pair):
		mine = _load(env.data_dir / SNAPSHOT_META).get("sha256")
		back = back_due(pair, peer_status, now, bool(mine) and pair.get("peer_applied_sha256") == mine)
		if back:
			pair.update({"moving_to": pair["peer"]["id"], "move_back": True})
			notes.append(f"moving the home back: {back}")
			LOG.info("moving the home back to %s: %s", peer_name(pair), back)
	moved = _move_step(pair, env, peer_status, now)
	if moved:
		notes.append(moved)
	running_here = is_holder(pair)
	# Mid-move Home Assistant stays stopped here; the add-ons run until the home goes.
	core = None if pair.get("moving_to") else _hold_core(pair, env, running_here)
	for step in (core, _hold_addons(pair, env, running_here)):
		if step:
			notes.append(step)

	if running_here and not pair.get("moving_to"):
		made = _snapshot_if_due(pair, env, now)
		if made:
			notes.append(made)
		if pair.get("say_running_here"):
			_say_running_here(pair, env)
		if options["mode"] == MAIN and not pair.get("peer") and address and key:
			code = make_code(address, LAN_PORT, key, pair["id"], pair["name"])
			# The code itself only on the app's panel, which only administrators
			# can open: every user of Home Assistant sees its notifications, and
			# the code lets an install take a copy of everything here.
			if pair.get("code_shown") != code and env.notify(
					NOTICE_CODE, "Vome CHAP: pair a standby",
					"This Home Assistant is ready for a standby. Its pairing code is on the "
					"**Vome CHAP** app's page: *Settings → Apps → Vome CHAP → Open Web UI*. On "
					"the other Home Assistant, set *local_pair* to **standby** and paste the code "
					"into *pair_code*."):
				pair["code_shown"] = code
		if pair.get("peer") and pair.get("code_shown"):
			if env.dismiss(NOTICE_CODE):
				pair.pop("code_shown", None)
		# Nobody else will tell the owner their standby has gone.
		seen = float(pair.get("peer_seen_at") or pair.get("introduced") or now)
		if pair.get("peer") and not peer_ok and now - seen > PEER_MISSING_NOTICE and not pair.get("peer_missing_told"):
			if env.notify(NOTICE_PEER, "Vome CHAP: your standby is not answering",
			              f"{peer_name(pair)} has not answered since "
			              f"{time.strftime('%H:%M', time.localtime(seen))}. Until it is back, nothing "
			              "can take over if this install stops."):
				pair["peer_missing_told"] = True
		elif peer_ok and pair.get("peer_missing_told"):
			if env.dismiss(NOTICE_PEER):
				pair.pop("peer_missing_told", None)
	elif peer_status and not running_here:
		taken = _take_snapshot(pair, env, peer_status, now)
		if taken:
			notes.append(taken)

	save_pair(env.data_dir, pair)
	who = "running the home" if running_here else "standing by"
	return f"local pair, {who}: " + ("; ".join(notes) if notes else "nothing to do"), PASS_SECONDS


# ── The panel (Home Assistant's sidebar, through ingress) ─────────────────
#
# A stopped Home Assistant has no screen, so what the owner does -- see the
# pair, copy the pairing code, move the home -- happens on the install
# running the home. No outside assets (the privacy rule for everything Vome
# ships); forms carry a token made at start, so nothing but this page can
# move the home.

PANEL_PORT = 8099
INGRESS_PEER = "172.30.32.2"   # the Supervisor, the only caller ingress allows
_FORM_TOKEN = secrets.token_urlsafe(24)


def _esc(text: Any) -> str:
	import html
	return html.escape(str(text if text is not None else ""))


def panel_view(data_dir: Path, now: Optional[float] = None) -> dict:
	"""What the panel shows, as plain data (tested without a browser)."""
	now = now or time.time()
	options = read_options(data_dir)
	pair = load_pair(data_dir) if options["mode"] != OFF else {}
	if options["mode"] == OFF or not pair.get("id"):
		return {"mode": options["mode"], "state": "off"}
	meta = _load(data_dir / SNAPSHOT_META)
	peer = pair.get("peer") or {}
	seen = pair.get("peer_seen_at")
	view = {"mode": options["mode"], "running_here": is_holder(pair), "name": pair.get("name"),
	        "address": pair.get("address"), "peer": peer_name(pair) if peer else None,
	        "peer_address": peer.get("address"), "epoch": pair.get("epoch"),
	        "router_ok": pair.get("router_ok"), "moving": bool(pair.get("moving_to")),
	        "peer_answering": bool(seen and now - float(seen) < 3 * PASS_SECONDS + PEER_TIMEOUT),
	        "peer_seen_at": seen,
	        "in_step": bool(meta.get("sha256")) and pair.get("peer_applied_sha256") == meta.get("sha256"),
	        "code": (make_code(pair["address"], LAN_PORT, key_of(pair), pair["id"], pair.get("name") or "")
	                 if options["mode"] == MAIN and not peer and pair.get("address") and key_of(pair) else None),
	        "took_over": bool(pair.get("took_over")),
	        "is_main": options["mode"] == MAIN, "peer_addons": pair.get("peer_addons") or [],
	        "standby_addons": pair.get("standby_addons")}
	if not peer:
		view["state"] = "waiting_for_standby" if view["running_here"] else "pairing"
	elif view["moving"]:
		view["state"] = "moving"
	else:
		view["state"] = "running_here" if view["running_here"] else "standing_by"
	view["can_move"] = view["state"] == "running_here" and view["peer_answering"] and view["in_step"]
	return view


def render_panel(view: dict, token: str = "") -> str:
	rows, actions = [], ""
	state = view["state"]
	if state == "off":
		rows.append("<p>Local pairing is off. To pair two Home Assistants in your house, set "
		            "<b>local_pair</b> in this add-on's <i>Configuration</i> tab: <b>main</b> on the one "
		            "running your home, <b>standby</b> on the other.</p>")
	elif state == "pairing":
		rows.append("<p>Pairing with the main install&hellip; this install's Home Assistant stops once "
		            "they have met.</p>")
	elif state == "waiting_for_standby":
		code = view.get("code")
		rows.append("<p>This install runs your home. No standby yet: on the other Home Assistant, set "
		            "this add-on's <b>local_pair</b> to <b>standby</b> and paste this code into "
		            "<b>pair_code</b>.</p>")
		rows.append(f"<pre id='code'>{_esc(code) if code else 'The code appears here in a moment.'}</pre>"
		            "<p class='muted'>Keep it private: it lets an install take a copy of this one.</p>")
	else:
		peer = _esc(view["peer"])
		if view["running_here"] and not view.get("is_main"):
			rows.append(f"<p><b>{_esc(view['name'])}</b> (this one) is running your home in place of {peer}.</p>")
			if view.get("took_over"):
				rows.append(f"<p class='muted'>It took over; your home goes back to {peer} by itself once "
				            "that has been back and in step for three minutes.</p>")
		elif view["running_here"]:
			rows.append(f"<p><b>{_esc(view['name'])}</b> (this one) runs your home.</p>")
		if view["running_here"]:
			if not view["peer_answering"]:
				when = time.strftime("%H:%M", time.localtime(float(view["peer_seen_at"]))) if view.get("peer_seen_at") else "a while"
				rows.append(f"<p class='bad'>{peer} is not answering (last heard {when}). Nothing can take "
				            "over until it is back.</p>")
			elif view["in_step"]:
				rows.append(f"<p class='ok'>{peer} is standing by, in step, ready to take over.</p>")
			else:
				rows.append(f"<p class='warn'>{peer} is standing by, catching up with the latest changes.</p>")
		if view["router_ok"] is False:
			rows.append("<p class='warn'>This install cannot reach the house router just now.</p>")
		if state == "moving":
			rows.append(f"<p class='warn'>Moving the home to {peer}: Home Assistant here is stopped for a "
			            "last complete copy; it starts there once that copy has landed.</p>")
			actions = (f"<form method='post' action='cancel'><input type='hidden' name='t' value='{_esc(token)}'>"
			           "<button>Call the move off</button></form>")
		elif view["running_here"] and view.get("peer"):
			disabled = "" if view["can_move"] else " disabled"
			actions = (f"<form method='post' action='move'><input type='hidden' name='t' value='{_esc(token)}'>"
			           f"<button{disabled}>Move the home to {peer}</button></form>"
			           "<p class='muted'>Home Assistant stops here for a minute or two while the last "
			           "changes go across, then starts there.</p>")
	if view.get("is_main") and view.get("peer_addons") and state != "moving":
		chosen = view.get("standby_addons")
		boxes = "".join(
			f"<label><input type='checkbox' name='a' value='{_esc(a.get('slug'))}'"
			f"{' checked' if chosen is None or a.get('slug') in chosen else ''}> {_esc(a.get('name'))}</label><br>"
			for a in view["peer_addons"])
		actions += (f"<h3>When {_esc(view['peer'])} stands in</h3><form method='post' action='addons'>"
		            f"<input type='hidden' name='t' value='{_esc(token)}'><input type='hidden' name='sent' value='1'>"
		            f"{boxes}<p class='muted'>A smaller machine can run just what matters; the rest stay "
		            "stopped there.</p><button>Save</button></form>")
	return ("<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' "
	        "content='width=device-width,initial-scale=1'><title>Vome CHAP</title><style>"
	        "body{font-family:system-ui,sans-serif;margin:16px;max-width:720px;color:#1f2328;background:#fff}"
	        "@media(prefers-color-scheme:dark){body{color:#e6edf3;background:#111}}"
	        ".ok{color:#1a7f37}.warn{color:#b35900}.bad{color:#cf222e}.muted{opacity:.7;font-size:.9em}"
	        "pre{white-space:pre-wrap;word-break:break-all;padding:8px;border:1px solid #8886;border-radius:6px}"
	        "button{font:inherit;padding:8px 14px;border-radius:6px;border:1px solid #8888;cursor:pointer}"
	        "button[disabled]{opacity:.5;cursor:default}</style></head><body>"
	        "<h2>Vome CHAP &middot; local pair</h2>" + "".join(rows) + actions + "</body></html>")


class _PanelHandler(http.server.BaseHTTPRequestHandler):
	server_version = "VomeCHAP"

	def log_message(self, fmt, *args):
		LOG.debug("panel: " + fmt, *args)

	def _allowed(self) -> bool:
		return self.client_address[0] in (INGRESS_PEER, "127.0.0.1")

	def _html(self, status: int, body: str) -> None:
		raw = body.encode()
		self.send_response(status)
		self.send_header("Content-Type", "text/html; charset=utf-8")
		self.send_header("Content-Length", str(len(raw)))
		self.send_header("Cache-Control", "no-store")
		self.end_headers()
		self.wfile.write(raw)

	def do_GET(self):  # noqa: N802
		if not self._allowed():
			return self._html(403, "")
		self._html(200, render_panel(panel_view(self.server.data_dir), _FORM_TOKEN))  # type: ignore[attr-defined]

	def do_POST(self):  # noqa: N802
		import urllib.parse
		if not self._allowed():
			return self._html(403, "")
		length = min(int(self.headers.get("Content-Length") or 0), 4096)
		form = urllib.parse.parse_qs(self.rfile.read(length).decode("utf-8", "replace"))
		if not hmac.compare_digest(form.get("t", [""])[0].encode(), _FORM_TOKEN.encode()):
			return self._html(403, "<p>This page is out of date; reload it.</p>")
		action = self.path.rstrip("/").rsplit("/", 1)[-1]
		if action not in ("move", "cancel", "addons"):
			return self._html(404, "")
		if action == "addons":
			ok, message = choose_standby_addons(self.server.data_dir, form.get("a", []))  # type: ignore[attr-defined]
		else:
			ok, message = ask_move(self.server.data_dir, cancel=action == "cancel")  # type: ignore[attr-defined]
		LOG.info("panel: %s", message)
		# Back to the page (relative: ingress serves it under its own path).
		self.send_response(303)
		self.send_header("Location", "./")
		self.end_headers()


def start_panel(data_dir: Path, port: int = PANEL_PORT) -> Optional[http.server.ThreadingHTTPServer]:
	try:
		httpd = http.server.ThreadingHTTPServer(("0.0.0.0", port), _PanelHandler)
	except OSError as exc:
		LOG.warning("no panel: %s", exc)
		return None
	httpd.data_dir = data_dir  # type: ignore[attr-defined]
	httpd.daemon_threads = True
	threading.Thread(target=httpd.serve_forever, name="vome-chap-panel", daemon=True).start()
	return httpd
