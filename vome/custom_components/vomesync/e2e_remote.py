"""End-to-end encrypted remote access: TLS that ends in this home.

On ``<slug>.e2e.vome.io`` Vome's router reads only the name a browser asks
for and moves the still-encrypted bytes down this home's relay link. This
module is where they are decrypted:

* :class:`E2EProxy` — a loopback TLS server holding this home's own
  certificate. It reverse-proxies each request to Home Assistant with the
  same rules as the relay's forwarding, including ``remote_auth_guard``:
  core sees every one of these requests as local, so "Can only log in from
  the local network" is enforced here, not there. (Bridging the bytes
  straight into core's own web server would be less code and would lose
  exactly that check.)
* :class:`~.e2e_acme.AlpnResponder` — answers Let's Encrypt's TLS-ALPN-01
  validation, which the router sends to target ``e2e-acme``.
* :class:`E2ERemote` — asks Vome whether end-to-end access is on for this
  home and under which name, keeps a certificate for it (made here, renewed
  here at 30 days left; the key never leaves this directory), and runs the
  two servers. The relay bridges targets ``e2e`` / ``e2e-acme`` to their
  ports (:func:`port_for`).

Off unless Vome says on. See docs/e2e_remote_access.md in VomeHome.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
import ssl
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

import aiohttp
from aiohttp import web
from cryptography import x509
from multidict import CIMultiDict

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from . import e2e_acme
from . import health_score as hs
from . import remote_auth_guard as guard
from .const import (
	AGENT_E2E_PATH,
	CONF_RELAY,
	CONF_RELAY_LOCAL_URL,
	DOMAIN,
	RELAY_FORWARD_MAX_BODY,
	RELAY_FORWARD_WS_INGRESS_RE,
	RELAY_FORWARD_WS_PATHS,
)

_LOGGER = logging.getLogger(__name__)

_KEY = "_e2e"
TARGET_UI = "e2e"
TARGET_ACME = "e2e-acme"

RENEW_BEFORE = timedelta(days=30)
_CHECK_EVERY = 6 * 3600
_BACKOFF_START = 300
_BACKOFF_MAX = 6 * 3600

# Request headers that describe the hop to us, not the request. Forwarded-*
# also: core answers 400 to them unless it was told to trust a proxy.
_DROP_REQUEST = frozenset({
	"connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
	"te", "trailer", "transfer-encoding", "upgrade", "host", "content-length",
	"x-forwarded-for", "x-forwarded-host", "x-forwarded-proto", "x-forwarded-port",
	"x-real-ip", "forwarded",
})
_DROP_WS = frozenset({
	"sec-websocket-key", "sec-websocket-version", "sec-websocket-extensions",
	"sec-websocket-accept", "sec-websocket-protocol",
})
_DROP_RESPONSE = frozenset({
	"connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
	"te", "trailer", "transfer-encoding", "upgrade", "content-length",
})


def _safe_path(path: str) -> Optional[str]:
	from .relay_client import _safe_path_portion
	return _safe_path_portion(path)


def _json_403(message: str, *, error: Optional[str] = None) -> web.Response:
	body = {"error": error, "error_description": message} if error else {"message": message}
	return web.json_response(body, status=403)


class E2EProxy:
	"""A loopback TLS server that reverse-proxies to Home Assistant."""

	def __init__(self, hass: HomeAssistant, local_url: str) -> None:
		self.hass = hass
		self.local_url = local_url.rstrip("/")
		self.ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
		self.ssl_context.minimum_version = ssl.TLSVersion.TLSv1_2
		self.ssl_context.set_alpn_protocols(["http/1.1"])
		self._runner: Optional[web.AppRunner] = None
		self._session: Optional[aiohttp.ClientSession] = None
		self.port: Optional[int] = None

	def load_certificate(self, cert_path: str, key_path: str) -> None:
		"""Use this certificate from the next connection on (also on renewal)."""
		self.ssl_context.load_cert_chain(cert_path, key_path)

	async def start(self) -> int:
		# Bodies pass through untouched, compressed or not: no gunzip, no
		# second Content-Encoding to strip.
		self._session = aiohttp.ClientSession(auto_decompress=False)
		app = web.Application(client_max_size=RELAY_FORWARD_MAX_BODY)
		app.router.add_route("*", "/{tail:.*}", self._handle)
		self._runner = web.AppRunner(app, access_log=None)
		await self._runner.setup()
		sock = socket.socket()
		sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
		sock.bind(("127.0.0.1", 0))
		sock.setblocking(False)
		site = web.SockSite(self._runner, sock, ssl_context=self.ssl_context)
		await site.start()
		self.port = sock.getsockname()[1]
		return self.port

	async def stop(self) -> None:
		if self._runner is not None:
			await self._runner.cleanup()
			self._runner = None
		if self._session is not None:
			await self._session.close()
			self._session = None

	async def _handle(self, request: web.Request) -> web.StreamResponse:
		path = request.raw_path
		portion = _safe_path(path)
		if portion is None:
			return web.Response(status=400, text="Bad path.")
		headers = [
			(k, v) for k, v in request.headers.items() if k.lower() not in _DROP_REQUEST
		]
		refusal = guard.refuse_request(self.hass, path, headers)
		if refusal:
			return _json_403(refusal)
		if request.headers.get("Upgrade", "").lower() == "websocket":
			return await self._websocket(request, path, portion, headers)

		token_path = guard.is_token_path(path)
		if token_path:
			# The tokens have to be read before they leave, so ask for them plain.
			headers = [(k, v) for k, v in headers if k.lower() != "accept-encoding"]
		body = await request.read()
		assert self._session is not None
		try:
			async with self._session.request(
				request.method, self.local_url + path, headers=CIMultiDict(headers),
				data=body or None, allow_redirects=False,
			) as resp:
				out_headers = CIMultiDict(
					(k, v) for k, v in resp.headers.items() if k.lower() not in _DROP_RESPONSE
				)
				if token_path:
					raw = await resp.read()
					if resp.status == 200 and await guard.refuse_token_response(self.hass, raw):
						return _json_403(guard.REFUSAL, error="access_denied")
					return web.Response(status=resp.status, headers=out_headers, body=raw)
				out = web.StreamResponse(status=resp.status, headers=out_headers)
				await out.prepare(request)
				async for chunk in resp.content.iter_any():
					await out.write(chunk)
				await out.write_eof()
				return out
		except (aiohttp.ClientError, asyncio.TimeoutError) as err:
			_LOGGER.debug("E2E: forwarding %s failed: %s", portion, err)
			return web.Response(status=502, text="Home Assistant did not answer.")

	async def _websocket(
		self, request: web.Request, path: str, portion: str, headers: list,
	) -> web.StreamResponse:
		if portion not in RELAY_FORWARD_WS_PATHS and not RELAY_FORWARD_WS_INGRESS_RE.match(portion):
			return web.Response(status=403, text="WebSocket path not permitted.")
		assert self._session is not None
		upstream_headers = CIMultiDict(
			(k, v) for k, v in headers if k.lower() not in _DROP_WS
		)
		url = "ws" + self.local_url[4:] + path if self.local_url.startswith("http") else self.local_url + path
		try:
			upstream = await self._session.ws_connect(
				url, headers=upstream_headers, max_msg_size=0, heartbeat=30,
			)
		except (aiohttp.ClientError, asyncio.TimeoutError):
			return web.Response(status=502, text="Home Assistant did not answer.")
		browser = web.WebSocketResponse(max_msg_size=0, heartbeat=30)
		await browser.prepare(request)
		auth_pending = portion in RELAY_FORWARD_WS_PATHS

		async def down() -> None:
			async for msg in upstream:
				if msg.type == aiohttp.WSMsgType.TEXT:
					await browser.send_str(msg.data)
				elif msg.type == aiohttp.WSMsgType.BINARY:
					await browser.send_bytes(msg.data)
				else:
					break

		down_task = asyncio.ensure_future(down())
		try:
			async for msg in browser:
				if msg.type == aiohttp.WSMsgType.TEXT:
					if auth_pending:
						refusal = guard.refuse_ws_auth(self.hass, msg.data)
						if refusal:
							await browser.send_json({"type": "auth_invalid", "message": refusal})
							break
						if _is_auth_message(msg.data):
							auth_pending = False
					await upstream.send_str(msg.data)
				elif msg.type == aiohttp.WSMsgType.BINARY:
					await upstream.send_bytes(msg.data)
				else:
					break
		finally:
			down_task.cancel()
			await upstream.close()
			await browser.close()
		return browser


def _is_auth_message(text: str) -> bool:
	try:
		return json.loads(text).get("type") == "auth"
	except (ValueError, AttributeError):
		return False


def _not_after(cert_pem: bytes) -> Optional[datetime]:
	try:
		cert = x509.load_pem_x509_certificates(cert_pem)[0]
	except (ValueError, IndexError):
		return None
	return cert.not_valid_after_utc


class E2ERemote:
	"""This home's end-to-end access: settings from Vome, its certificate, its servers."""

	def __init__(self, hass: HomeAssistant, entry, local_url: str, *, store_dir: Optional[Path] = None) -> None:
		self.hass = hass
		self.entry = entry
		self.local_url = local_url
		self.store = store_dir or Path(hass.config.path(".storage", "vomesync_e2e"))
		self.host: Optional[str] = None
		self.directory = e2e_acme.LETS_ENCRYPT
		self.responder = e2e_acme.AlpnResponder()
		self.proxy: Optional[E2EProxy] = None
		self._task: Optional[asyncio.Task] = None
		self._backoff = _BACKOFF_START
		self.status: dict[str, Any] = {"enabled": False, "reason": "not asked yet"}

	# ── files ──────────────────────────────────────────────────────────

	def _path(self, name: str) -> Path:
		return self.store / name

	def _write_private(self, name: str, data: bytes) -> None:
		self.store.mkdir(mode=0o700, parents=True, exist_ok=True)
		path = self._path(name)
		tmp = path.with_suffix(path.suffix + ".tmp")
		fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
		with os.fdopen(fd, "wb") as fh:
			fh.write(data)
		os.replace(tmp, path)

	def _read(self, name: str) -> Optional[bytes]:
		try:
			return self._path(name).read_bytes()
		except OSError:
			return None

	def _key(self, name: str):
		pem = self._read(name)
		if pem:
			return e2e_acme.key_from_pem(pem)
		key = e2e_acme.new_key()
		self._write_private(name, e2e_acme.key_to_pem(key))
		return key

	# ── certificate ────────────────────────────────────────────────────

	def certificate_due(self, now: Optional[datetime] = None) -> bool:
		"""True when there is no certificate for the current name or it is near expiry."""
		pem = self._read("cert.pem")
		if not pem or self._read("cert.host") != (self.host or "").encode():
			return True
		# A certificate from another CA (Let's Encrypt staging, say) is not
		# what Vome asked for: browsers refuse it outright on an HSTS domain.
		if self._read("cert.directory") != (self.directory or "").encode():
			return True
		not_after = _not_after(pem)
		now = now or datetime.now(timezone.utc)
		return not_after is None or not_after - now < RENEW_BEFORE

	async def ensure_certificate(self, session: Optional[aiohttp.ClientSession] = None, *, ssl_context: Any = None) -> bool:
		"""Get or renew the certificate if due; True when one was issued."""
		# Reading the certificate is file I/O: never on the event loop.
		if not self.host or not await self.hass.async_add_executor_job(self.certificate_due):
			return False
		account_key = await self.hass.async_add_executor_job(self._key, "account.pem")
		cert_key = await self.hass.async_add_executor_job(self._key, "key.pem")
		client = e2e_acme.AcmeClient(
			session or async_get_clientsession(self.hass), self.directory, account_key,
			ssl_context=ssl_context,
		)
		chain = await client.obtain(self.host, cert_key, self.responder)
		await self.hass.async_add_executor_job(self._write_private, "cert.pem", chain)
		await self.hass.async_add_executor_job(self._write_private, "cert.host", self.host.encode())
		await self.hass.async_add_executor_job(
			self._write_private, "cert.directory", self.directory.encode(),
		)
		_LOGGER.info("Vome end-to-end: certificate for %s issued", self.host)
		return True

	def _load_into_proxy(self) -> bool:
		if self.proxy is None or not self._read("cert.pem"):
			return False
		self.proxy.load_certificate(str(self._path("cert.pem")), str(self._path("key.pem")))
		return True

	# ── lifecycle ──────────────────────────────────────────────────────

	async def _ask_vome(self) -> dict:
		from .relay_client import _agent_request
		_server_id, secret = hs._agent_credentials(self.entry)
		return await _agent_request(
			async_get_clientsession(self.hass), "GET", hs._portal_url(self.entry),
			AGENT_E2E_PATH, secret,
		)

	async def _servers_up(self) -> None:
		if self.responder.port is None:
			await self.responder.start()
		if self.proxy is None:
			self.proxy = E2EProxy(self.hass, self.local_url)
			await self.proxy.start()

	async def _servers_down(self) -> None:
		await self.responder.stop()
		self.responder.port = None
		if self.proxy is not None:
			await self.proxy.stop()
			self.proxy = None

	async def refresh(self) -> float:
		"""One round: ask Vome, then make reality match. Returns seconds to wait."""
		try:
			settings = await self._ask_vome()
		except Exception as err:  # noqa: BLE001 - never break the integration over this
			delay = self._backoff
			self._backoff = min(self._backoff * 2, _BACKOFF_MAX)
			self.status = {**self.status, "last_error": str(err)}
			return delay
		if not settings.get("enabled") or not settings.get("host"):
			self.host = None
			await self._servers_down()
			self.status = {"enabled": False, "reason": str(settings.get("reason") or "")}
			return float(settings.get("retry_after") or _CHECK_EVERY)
		self.host = str(settings["host"]).lower()
		if settings.get("acme_directory"):
			self.directory = str(settings["acme_directory"])
		await self._servers_up()
		try:
			await self.ensure_certificate()
		except e2e_acme.AcmeError as err:
			delay = self._backoff
			self._backoff = min(self._backoff * 2, _BACKOFF_MAX)
			self.status = {"enabled": True, "host": self.host, "certificate": False, "last_error": str(err)}
			_LOGGER.warning("Vome end-to-end: no certificate for %s yet: %s", self.host, err)
			return delay
		self._backoff = _BACKOFF_START
		ready = await self.hass.async_add_executor_job(self._load_into_proxy)
		self.status = {"enabled": True, "host": self.host, "certificate": ready}
		return _CHECK_EVERY

	def port(self, target: str) -> Optional[int]:
		if target == TARGET_ACME:
			return self.responder.port
		if target == TARGET_UI and self.status.get("certificate") and self.proxy is not None:
			return self.proxy.port
		return None

	async def _run(self) -> None:
		while True:
			await asyncio.sleep(await self.refresh())

	def start(self) -> None:
		self._task = self.hass.async_create_background_task(self._run(), f"{DOMAIN} e2e")

	async def stop(self) -> None:
		if self._task is not None:
			self._task.cancel()
			try:
				await self._task
			except (asyncio.CancelledError, Exception):  # noqa: BLE001
				pass
			self._task = None
		await self._servers_down()


def port_for(hass: HomeAssistant, target: str) -> Optional[int]:
	"""The loopback port the relay should bridge ``target`` to, or None (refuse)."""
	for remote in (hass.data.get(DOMAIN, {}).get(_KEY) or {}).values():
		port = remote.port(target)
		if port:
			return port
	return None


async def async_start_e2e(hass: HomeAssistant, entry) -> None:
	await async_stop_e2e(hass, entry)
	try:
		if not hs.is_linked(entry):
			return
		from .relay_client import resolve_local_core_url
		relay = (entry.options or {}).get(CONF_RELAY) or {}
		remote = E2ERemote(hass, entry, resolve_local_core_url(hass, relay.get(CONF_RELAY_LOCAL_URL)))
		hass.data.setdefault(DOMAIN, {}).setdefault(_KEY, {})[entry.entry_id] = remote
		remote.start()
	except Exception:  # noqa: BLE001 - remote access must never cost the integration its setup
		_LOGGER.exception("Vome end-to-end could not start")


async def async_stop_e2e(hass: HomeAssistant, entry) -> None:
	remote = (hass.data.get(DOMAIN, {}).get(_KEY) or {}).pop(entry.entry_id, None)
	if remote is not None:
		await remote.stop()
