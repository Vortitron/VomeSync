# flake8: noqa
"""The home side of end-to-end access, on a real Core with real users.

A real TLS client talks to E2EProxy (the home's own TLS server) which
forwards to a stand-in Home Assistant. Pinned: requests and WebSockets go
through, the body is untouched, local-only users are refused at every way
in, and — with Pebble — the certificate is issued to the home's own key,
renewed only when due, and validates against the CA's root.
"""
import asyncio
import json
import ssl
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import aiohttp
import pytest
from aiohttp import web
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.x509.oid import NameOID

from custom_components.vomesync import e2e_acme, e2e_remote
from custom_components.vomesync import remote_auth_guard as guard

from test_e2e_acme_pebble import PEBBLE_DIR, pebble  # noqa: F401  (fixture)

pytestmark = pytest.mark.enable_socket
NAME = "home1.e2e.vome.test"
CLIENT_ID = "https://example.com/"


def _self_signed(tmp_path, name=NAME):
	key = e2e_acme.new_key()
	subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
	now = datetime.now(timezone.utc)
	cert = (
		x509.CertificateBuilder().subject_name(subject).issuer_name(subject)
		.public_key(key.public_key()).serial_number(x509.random_serial_number())
		.not_valid_before(now - timedelta(hours=1)).not_valid_after(now + timedelta(days=1))
		.add_extension(x509.SubjectAlternativeName([x509.DNSName(name)]), critical=False)
		.sign(key, hashes.SHA256())
	)
	(tmp_path / "c.pem").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
	(tmp_path / "k.pem").write_bytes(e2e_acme.key_to_pem(key))
	return str(tmp_path / "c.pem"), str(tmp_path / "k.pem")


@pytest.fixture
async def fake_ha(socket_enabled):
	"""Just enough of Home Assistant's HTTP surface to forward to."""
	seen = []
	issued = {}

	async def states(request):
		seen.append(("states", request.headers.get("Authorization")))
		return web.json_response({
			"auth": request.headers.get("Authorization"), "host": request.host,
			"cookie": request.headers.get("Cookie"),
		})

	async def login_flow(request):
		seen.append(("login", None))
		return web.json_response({"type": "form", "errors": {"base": "invalid_auth"}})

	async def webhook(request):
		seen.append(("webhook", request.match_info["hook"]))
		return web.json_response({})

	async def token(request):
		seen.append(("token", None))
		return web.json_response({"access_token": issued["token"], "token_type": "Bearer"})

	async def gz(request):
		resp = web.Response(body=b"x" * 5000)
		resp.enable_compression()
		return resp

	async def websocket(request):
		ws = web.WebSocketResponse()
		await ws.prepare(request)
		await ws.send_json({"type": "auth_required"})
		async for msg in ws:
			seen.append(("ws", msg.data))
			await ws.send_json({"type": "auth_ok"})
		return ws

	app = web.Application()
	app.router.add_get("/api/states", states)
	app.router.add_post("/auth/token", token)
	app.router.add_get("/gz", gz)
	app.router.add_get("/api/websocket", websocket)
	app.router.add_post("/auth/login_flow/{flow}", login_flow)
	app.router.add_post("/api/webhook/{hook}", webhook)
	runner = web.AppRunner(app)
	await runner.setup()
	site = web.TCPSite(runner, "127.0.0.1", 0)
	await site.start()
	port = site._server.sockets[0].getsockname()[1]
	yield SimpleNamespace(url=f"http://127.0.0.1:{port}", seen=seen, issued=issued)
	await runner.cleanup()


DOOR_KEY = b"d" * 32
GATE = "https://staging.vome.io/remote/gate"


def _door_token(key=DOOR_KEY, **over):
	import time
	import jwt
	claims = {"sub": "u1", "sid": "rly-1", "host": NAME, "scope": "ha-forward",
	          "iat": int(time.time()), "exp": int(time.time()) + 600}
	claims.update(over)
	return jwt.encode(claims, key, algorithm="HS256")


@pytest.fixture
async def proxy(hass, fake_ha, tmp_path):
	policy = {"ok": True, "open": False, "webhooks": False, "gate": True}
	reports = []

	async def fetch_policy(host):
		return dict(policy) if host == NAME else {"ok": False}

	p = e2e_remote.E2EProxy(hass, fake_ha.url, policy=fetch_policy, report=reports.extend)
	p.door = {"key": DOOR_KEY, "host": NAME, "server_id": "rly-1", "cookie": "vome_e2e",
	          "ttl": 600, "gate_url": GATE}
	cert, key = _self_signed(tmp_path)
	p.load_certificate(cert, key)
	await p.start()
	ctx = ssl.create_default_context(cafile=cert)
	yield SimpleNamespace(port=p.port, ctx=ctx, p=p, policy=policy, reports=reports)
	await p.stop()


async def _token(hass, local_only):
	user = await hass.auth.async_create_user("Svc" if local_only else "Andy", local_only=local_only)
	refresh = await hass.auth.async_create_refresh_token(user, CLIENT_ID)
	return refresh, hass.auth.async_create_access_token(refresh)


def _url(proxy, path):
	return f"https://{NAME}:{proxy.port}{path}"


def _session(proxy, *, signed_in=True, local_port=None, peer=None):
	# Resolve the e2e name to loopback, as the router would deliver it.
	if local_port is not None:
		proxy.p.peers[local_port] = peer

	class Loopback(aiohttp.abc.AbstractResolver):
		async def resolve(self, host, port=0, family=0):
			return [{"hostname": host, "host": "127.0.0.1", "port": port,
				"family": 2, "proto": 0, "flags": 0}]

		async def close(self):
			pass

	connector = aiohttp.TCPConnector(
		ssl=proxy.ctx, resolver=Loopback(),
		local_addr=("127.0.0.1", local_port) if local_port else None,
	)
	headers = {"Cookie": f"vome_e2e={_door_token()}; theme=dark"} if signed_in else {}
	return aiohttp.ClientSession(connector=connector, headers=headers)


async def test_a_request_goes_through_with_its_auth(hass, proxy, fake_ha):
	_r, tok = await _token(hass, local_only=False)
	async with _session(proxy) as s:
		async with s.get(_url(proxy, "/api/states"), headers={"Authorization": f"Bearer {tok}", "X-Forwarded-For": "1.2.3.4"}) as r:
			assert r.status == 200
			body = await r.json()
	assert body["auth"] == f"Bearer {tok}"
	# The hop is ours: forwarded-for never reaches core, nor the public name.
	assert fake_ha.seen == [("states", f"Bearer {tok}")]


async def test_compressed_bodies_pass_through_untouched(hass, proxy):
	async with _session(proxy) as s:
		async with s.get(_url(proxy, "/gz"), headers={"Accept-Encoding": "gzip"}) as r:
			assert r.headers.get("Content-Encoding") == "gzip"
			assert await r.read() == b"x" * 5000


async def test_local_only_bearer_never_reaches_core(hass, proxy, fake_ha):
	_r, tok = await _token(hass, local_only=True)
	async with _session(proxy) as s:
		async with s.get(_url(proxy, "/api/states"), headers={"Authorization": f"Bearer {tok}"}) as r:
			assert r.status == 403
	assert fake_ha.seen == []


async def test_local_only_sign_in_is_refused_and_revoked(hass, proxy, fake_ha):
	refresh, tok = await _token(hass, local_only=True)
	fake_ha.issued["token"] = tok
	async with _session(proxy) as s:
		async with s.post(_url(proxy, "/auth/token"), data={"grant_type": "authorization_code"}) as r:
			assert r.status == 403
			assert tok not in await r.text()
	assert hass.auth.async_get_refresh_token(refresh.id) is None


async def test_websocket_auth(hass, proxy, fake_ha):
	_r, normal = await _token(hass, local_only=False)
	_r, local = await _token(hass, local_only=True)
	async with _session(proxy) as s:
		async with s.ws_connect(_url(proxy, "/api/websocket")) as ws:
			assert (await ws.receive_json())["type"] == "auth_required"
			await ws.send_json({"type": "auth", "access_token": normal})
			assert (await ws.receive_json())["type"] == "auth_ok"
		async with s.ws_connect(_url(proxy, "/api/websocket")) as ws:
			await ws.receive_json()
			await ws.send_json({"type": "auth", "access_token": local})
			assert await ws.receive_json() == {"type": "auth_invalid", "message": guard.REFUSAL}
	assert [m for m in fake_ha.seen if m[0] == "ws"] == [("ws", json.dumps({"type": "auth", "access_token": normal}))]


async def test_other_websocket_paths_are_refused(hass, proxy):
	async with _session(proxy) as s:
		with pytest.raises(aiohttp.WSServerHandshakeError):
			await s.ws_connect(_url(proxy, "/api/something_else"))


@pytest.mark.skipif(not PEBBLE_DIR, reason="PEBBLE_DIR not set")
async def test_certificate_issued_kept_and_trusted(hass, pebble, fake_ha, tmp_path):  # noqa: F811
	remote = e2e_remote.E2ERemote(hass, SimpleNamespace(entry_id="e1"), fake_ha.url, store_dir=tmp_path / "e2e")
	remote.host = NAME
	remote.directory = pebble["directory"]
	await remote.responder.start(port=pebble["tls_port"])
	remote.proxy = e2e_remote.E2EProxy(hass, fake_ha.url)
	await remote.proxy.start()
	try:
		async with aiohttp.ClientSession() as s:
			assert await remote.ensure_certificate(s, ssl_context=pebble["ssl"]) is True
			# Not due again: no second order.
			assert await remote.ensure_certificate(s, ssl_context=pebble["ssl"]) is False
			async with s.get(pebble["root_url"], ssl=pebble["ssl"]) as r:
				root = await r.text()
		assert (tmp_path / "e2e" / "key.pem").stat().st_mode & 0o777 == 0o600
		assert remote._load_into_proxy()
		remote.status["certificate"] = True
		assert remote.port("e2e") == remote.proxy.port
		assert remote.port("e2e-acme") == pebble["tls_port"]

		trust = ssl.create_default_context(cadata=root)
		remote.proxy.door = {"key": DOOR_KEY, "host": NAME, "server_id": "rly-1",
		                     "cookie": "vome_e2e", "ttl": 600, "gate_url": GATE}
		proxy = SimpleNamespace(port=remote.proxy.port, ctx=trust, p=remote.proxy)
		async with _session(proxy) as s:
			async with s.get(_url(proxy, "/api/states")) as r:
				assert r.status == 200
		# A different name makes the certificate due again.
		remote.host = "home9.e2e.vome.test"
		assert remote.certificate_due()
	finally:
		await remote._servers_down()



# ── the door ────────────────────────────────────────────────────────────────

async def test_a_visitor_without_a_sign_in_is_sent_to_the_gate(hass, proxy, fake_ha):
	async with _session(proxy, signed_in=False) as s:
		async with s.get(_url(proxy, "/lovelace/0"), allow_redirects=False,
		                 headers={"Accept": "text/html"}) as r:
			assert r.status == 302
			assert r.headers["Location"] == f"{GATE}?host={NAME}"
		async with s.ws_connect(_url(proxy, "/api/websocket")) if False else s.get(
			_url(proxy, "/api/websocket"), headers={"Upgrade": "websocket", "Connection": "Upgrade",
			"Sec-WebSocket-Key": "dGhlIHNhbXBsZSBub25jZQ==", "Sec-WebSocket-Version": "13"},
			allow_redirects=False) as r:
			assert r.status == 401
	assert fake_ha.seen == []
	assert [e["event"] for e in proxy.reports] == ["gate_shown"]


async def test_the_pass_becomes_a_cookie_on_this_name_and_leaves_the_address(hass, proxy):
	from urllib.parse import quote
	async with _session(proxy, signed_in=False) as s:
		async with s.get(_url(proxy, f"/?vome_pass={quote(_door_token())}&tab=2"), allow_redirects=False) as r:
			assert r.status == 302 and r.headers["Location"] == "/?tab=2"
			assert r.headers["Cache-Control"] == "no-store"
			cookie = r.headers["Set-Cookie"]
			assert cookie.startswith("vome_e2e=") and "Secure" in cookie and "HttpOnly" in cookie
			assert "Domain" not in cookie
		bad = _door_token(key=b"x" * 32)
		async with s.get(_url(proxy, f"/?vome_pass={quote(bad)}"), allow_redirects=False) as r:
			assert r.status == 302 and "Set-Cookie" not in r.headers


@pytest.mark.parametrize("token", [
	lambda: _door_token(key=b"x" * 32),        # another home's key / the shared secret
	lambda: _door_token(host="other.e2e.vome.io"),
	lambda: _door_token(sid="rly-2"),
])
async def test_tokens_that_are_not_this_homes_get_the_gate(hass, proxy, fake_ha, token):
	async with _session(proxy, signed_in=False) as s:
		async with s.get(_url(proxy, "/api/states"), allow_redirects=False,
		                 headers={"Cookie": f"vome_e2e={token()}"}) as r:
			assert r.status == 302
	assert fake_ha.seen == []


async def test_our_cookie_never_reaches_home_assistant(hass, proxy):
	async with _session(proxy) as s:
		async with s.get(_url(proxy, "/api/states")) as r:
			assert (await r.json())["cookie"] == "theme=dark"


async def test_app_access_open_and_webhooks(hass, proxy, fake_ha):
	proxy.policy["webhooks"] = True
	async with _session(proxy, signed_in=False) as s:
		async with s.post(_url(proxy, "/api/webhook/abc"), json={}) as r:
			assert r.status == 200
		async with s.get(_url(proxy, "/api/states"), allow_redirects=False) as r:
			assert r.status == 302  # webhooks only, nothing else
	proxy.p._policy_cache.clear()
	proxy.policy["open"] = True
	async with _session(proxy, signed_in=False) as s:
		async with s.get(_url(proxy, "/api/states")) as r:
			assert r.status == 200
	assert ("webhook", "abc") in fake_ha.seen


async def test_repeated_failed_logins_block_that_visitor_only(hass, proxy, fake_ha, unused_tcp_port_factory):
	proxy.policy["open"] = True
	attacker, owner = unused_tcp_port_factory(), unused_tcp_port_factory()
	statuses = []
	# One kept-alive connection per visitor: the router hands the home one
	# address per connection, and a client port cannot be rebound at once.
	async with _session(proxy, signed_in=False, local_port=attacker, peer="203.0.113.9") as s:
		for _ in range(6):
			async with s.post(_url(proxy, "/auth/login_flow/f1"), json={"username": "a", "password": "b"}) as r:
				statuses.append(r.status)
	assert statuses == [200] * 5 + [429]
	async with _session(proxy, signed_in=False, local_port=owner, peer="198.51.100.7") as s:
		async with s.post(_url(proxy, "/auth/login_flow/f1"), json={}) as r:
			assert r.status == 200
	events = [(e["event"], e["client_ip"]) for e in proxy.reports]
	assert ("login_blocked", "203.0.113.9") in events
	assert sum(1 for e in events if e == ("login_failed", "203.0.113.9")) == 5


async def test_another_name_is_misdirected(hass, proxy):
	async with _session(proxy) as s:
		async with s.get(_url(proxy, "/api/states"), headers={"Host": "other.e2e.vome.io"}) as r:
			assert r.status == 421


async def test_no_door_no_service(hass, proxy):
	proxy.p.door = None
	async with _session(proxy) as s:
		async with s.get(_url(proxy, "/api/states")) as r:
			assert r.status == 503
