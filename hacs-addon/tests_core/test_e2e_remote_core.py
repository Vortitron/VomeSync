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
		return web.json_response({"auth": request.headers.get("Authorization"), "host": request.host})

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
	runner = web.AppRunner(app)
	await runner.setup()
	site = web.TCPSite(runner, "127.0.0.1", 0)
	await site.start()
	port = site._server.sockets[0].getsockname()[1]
	yield SimpleNamespace(url=f"http://127.0.0.1:{port}", seen=seen, issued=issued)
	await runner.cleanup()


@pytest.fixture
async def proxy(hass, fake_ha, tmp_path):
	p = e2e_remote.E2EProxy(hass, fake_ha.url)
	cert, key = _self_signed(tmp_path)
	p.load_certificate(cert, key)
	await p.start()
	ctx = ssl.create_default_context(cafile=cert)
	yield SimpleNamespace(port=p.port, ctx=ctx)
	await p.stop()


async def _token(hass, local_only):
	user = await hass.auth.async_create_user("Svc" if local_only else "Andy", local_only=local_only)
	refresh = await hass.auth.async_create_refresh_token(user, CLIENT_ID)
	return refresh, hass.auth.async_create_access_token(refresh)


def _url(proxy, path):
	return f"https://{NAME}:{proxy.port}{path}"


def _session(proxy):
	# Resolve the e2e name to loopback, as the router would deliver it.
	resolver = aiohttp.resolver.ThreadedResolver()

	class Loopback(aiohttp.abc.AbstractResolver):
		async def resolve(self, host, port=0, family=0):
			return [{"hostname": host, "host": "127.0.0.1", "port": port,
				"family": 2, "proto": 0, "flags": 0}]

		async def close(self):
			pass

	return aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=proxy.ctx, resolver=Loopback()))


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
		proxy = SimpleNamespace(port=remote.proxy.port, ctx=trust)
		async with _session(proxy) as s:
			async with s.get(_url(proxy, "/api/states")) as r:
				assert r.status == 200
		# A different name makes the certificate due again.
		remote.host = "home9.e2e.vome.test"
		assert remote.certificate_due()
	finally:
		await remote._servers_down()
