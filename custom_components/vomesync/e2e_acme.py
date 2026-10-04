"""A small ACME client (RFC 8555) for this home's own certificate.

End-to-end remote access means TLS ends in this Home Assistant, so the
certificate and its key have to live here and nowhere else. This client gets
one from Let's Encrypt with the **TLS-ALPN-01** challenge (RFC 8737): Let's
Encrypt connects to ``<slug>.e2e.vome.io:443`` offering ALPN ``acme-tls/1``,
Vome's router sends that connection down the tunnel to
:class:`AlpnResponder`, and we prove control by presenting a certificate
that carries a digest of the key authorisation. No DNS records are written
for it, and Vome never holds anything that could impersonate the home.

Why not the ``acme`` library that ships with Core: it is synchronous, and
from 3.0 it no longer offers TLS-ALPN-01. Everything here is ``aiohttp``
and ``cryptography``, both part of Core.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import ssl
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import aiohttp
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.x509.oid import NameOID, ObjectIdentifier

_LOGGER = logging.getLogger(__name__)

LETS_ENCRYPT = "https://acme-v02.api.letsencrypt.org/directory"
LETS_ENCRYPT_STAGING = "https://acme-staging-v02.api.letsencrypt.org/directory"

ACME_TLS_ALPN = "acme-tls/1"
# id-pe-acmeIdentifier (RFC 8737 §6.1).
_ACME_IDENTIFIER_OID = ObjectIdentifier("1.3.6.1.5.5.7.1.31")

_POLL_INTERVAL = 2.0
_POLL_TIMEOUT = 120.0
_HTTP_TIMEOUT = aiohttp.ClientTimeout(total=30)
_NONCE_ATTEMPTS = 5


class AcmeError(Exception):
	"""ACME refused or failed; the message says which step and why."""


def _b64(data: bytes) -> str:
	return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def new_key() -> ec.EllipticCurvePrivateKey:
	"""A P-256 key, for the account and for the certificate."""
	return ec.generate_private_key(ec.SECP256R1())


def key_to_pem(key: ec.EllipticCurvePrivateKey) -> bytes:
	return key.private_bytes(
		serialization.Encoding.PEM,
		serialization.PrivateFormat.PKCS8,
		serialization.NoEncryption(),
	)


def key_from_pem(pem: bytes) -> ec.EllipticCurvePrivateKey:
	key = serialization.load_pem_private_key(pem, password=None)
	if not isinstance(key, ec.EllipticCurvePrivateKey):
		raise ValueError("not an EC key")
	return key


def jwk(key: ec.EllipticCurvePrivateKey) -> dict[str, str]:
	numbers = key.public_key().public_numbers()
	return {
		"crv": "P-256",
		"kty": "EC",
		"x": _b64(numbers.x.to_bytes(32, "big")),
		"y": _b64(numbers.y.to_bytes(32, "big")),
	}


def thumbprint(key: ec.EllipticCurvePrivateKey) -> str:
	"""RFC 7638: SHA-256 over the JWK's required members, sorted, no spaces."""
	canonical = json.dumps(jwk(key), sort_keys=True, separators=(",", ":"))
	return _b64(hashlib.sha256(canonical.encode()).digest())


def sign_jws(
	key: ec.EllipticCurvePrivateKey,
	url: str,
	nonce: str,
	payload: Optional[dict],
	kid: Optional[str] = None,
) -> dict[str, str]:
	"""A flattened JWS, ES256. ``payload=None`` is POST-as-GET (empty payload)."""
	protected: dict[str, Any] = {"alg": "ES256", "nonce": nonce, "url": url}
	if kid:
		protected["kid"] = kid
	else:
		protected["jwk"] = jwk(key)
	protected_b64 = _b64(json.dumps(protected).encode())
	payload_b64 = "" if payload is None else _b64(json.dumps(payload).encode())
	der = key.sign(f"{protected_b64}.{payload_b64}".encode(), ec.ECDSA(hashes.SHA256()))
	r, s = decode_dss_signature(der)
	return {
		"protected": protected_b64,
		"payload": payload_b64,
		"signature": _b64(r.to_bytes(32, "big") + s.to_bytes(32, "big")),
	}


def challenge_certificate(domain: str, key_authorization: str) -> tuple[bytes, bytes]:
	"""The self-signed certificate TLS-ALPN-01 validates: ``(cert_pem, key_pem)``.

	It names the domain and carries, as a critical extension, the SHA-256 of
	the key authorisation as a DER OCTET STRING (RFC 8737 §3).
	"""
	key = new_key()
	digest = hashlib.sha256(key_authorization.encode()).digest()
	now = datetime.now(timezone.utc)
	name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, domain)])
	cert = (
		x509.CertificateBuilder()
		.subject_name(name)
		.issuer_name(name)
		.public_key(key.public_key())
		.serial_number(x509.random_serial_number())
		.not_valid_before(now - timedelta(minutes=5))
		.not_valid_after(now + timedelta(days=1))
		.add_extension(x509.SubjectAlternativeName([x509.DNSName(domain)]), critical=False)
		.add_extension(
			x509.UnrecognizedExtension(_ACME_IDENTIFIER_OID, b"\x04\x20" + digest),
			critical=True,
		)
		.sign(key, hashes.SHA256())
	)
	return cert.public_bytes(serialization.Encoding.PEM), key_to_pem(key)


def make_csr(domain: str, key: ec.EllipticCurvePrivateKey) -> bytes:
	csr = (
		x509.CertificateSigningRequestBuilder()
		.subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, domain)]))
		.add_extension(x509.SubjectAlternativeName([x509.DNSName(domain)]), critical=False)
		.sign(key, hashes.SHA256())
	)
	return csr.public_bytes(serialization.Encoding.DER)


class AlpnResponder:
	"""Answers TLS-ALPN-01 validation connections, and nothing else.

	A loopback TLS server that presents, per SNI name, the challenge
	certificate for a validation in progress. The router sends only
	connections offering ``acme-tls/1`` here; one for a name with no
	validation in progress fails its handshake.
	"""

	def __init__(self) -> None:
		self._contexts: dict[str, ssl.SSLContext] = {}
		self._server: Optional[asyncio.base_events.Server] = None
		self.port: Optional[int] = None

	def _context_for(self, cert_pem: bytes, key_pem: bytes) -> ssl.SSLContext:
		import os
		import tempfile
		ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
		ctx.minimum_version = ssl.TLSVersion.TLSv1_2
		ctx.set_alpn_protocols([ACME_TLS_ALPN])
		# load_cert_chain only reads files; keep them for as long as it takes.
		with tempfile.TemporaryDirectory() as tmp:
			cert_path = os.path.join(tmp, "c.pem")
			key_path = os.path.join(tmp, "k.pem")
			with open(cert_path, "wb") as fh:
				fh.write(cert_pem)
			with open(key_path, "wb") as fh:
				fh.write(key_pem)
			ctx.load_cert_chain(cert_path, key_path)
		return ctx

	def add(self, domain: str, cert_pem: bytes, key_pem: bytes) -> None:
		self._contexts[domain.lower()] = self._context_for(cert_pem, key_pem)

	def remove(self, domain: str) -> None:
		self._contexts.pop(domain.lower(), None)

	def _server_context(self) -> ssl.SSLContext:
		base = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
		base.set_alpn_protocols([ACME_TLS_ALPN])

		def pick(sock, server_name, _ctx):
			ctx = self._contexts.get((server_name or "").lower())
			if ctx is None:
				return ssl.ALERT_DESCRIPTION_UNRECOGNIZED_NAME
			sock.context = ctx
			return None

		base.sni_callback = pick
		return base

	async def start(self, host: str = "127.0.0.1", port: int = 0) -> int:
		async def handle(_reader, writer):
			# The handshake is the whole exchange; the validator hangs up.
			writer.close()

		self._server = await asyncio.start_server(
			handle, host, port, ssl=self._server_context(),
		)
		self.port = self._server.sockets[0].getsockname()[1]
		return self.port

	async def stop(self) -> None:
		if self._server is not None:
			self._server.close()
			await self._server.wait_closed()
			self._server = None


class AcmeClient:
	"""Just enough ACME to get one certificate for one name."""

	def __init__(
		self,
		session: aiohttp.ClientSession,
		directory_url: str,
		account_key: ec.EllipticCurvePrivateKey,
		*,
		ssl_context: Any = None,
	) -> None:
		self._session = session
		self._directory_url = directory_url
		self._key = account_key
		self._ssl = ssl_context
		self._directory: Optional[dict] = None
		self._nonce: Optional[str] = None
		self.kid: Optional[str] = None

	async def _get_directory(self) -> dict:
		if self._directory is None:
			async with self._session.get(
				self._directory_url, ssl=self._ssl, timeout=_HTTP_TIMEOUT,
			) as resp:
				if resp.status != 200:
					raise AcmeError(f"directory: HTTP {resp.status}")
				self._directory = await resp.json(content_type=None)
		return self._directory

	async def _new_nonce(self) -> str:
		url = (await self._get_directory())["newNonce"]
		async with self._session.head(url, ssl=self._ssl, timeout=_HTTP_TIMEOUT) as resp:
			nonce = resp.headers.get("Replay-Nonce")
		if not nonce:
			raise AcmeError("newNonce: no nonce")
		return nonce

	async def _post(
		self, url: str, payload: Optional[dict], *, use_jwk: bool = False,
		accept_pem: bool = False,
	) -> tuple[int, Any, Any]:
		"""Signed POST, retried on badNonce. Returns ``(status, headers, body)``.

		A CA may refuse any nonce (Let's Encrypt rarely does, Pebble does 5% on
		purpose), and the fresh one it hands back can be refused too.
		"""
		for attempt in range(1, _NONCE_ATTEMPTS + 1):
			nonce = self._nonce or await self._new_nonce()
			self._nonce = None
			body = sign_jws(self._key, url, nonce, payload, None if use_jwk else self.kid)
			async with self._session.post(
				url, data=json.dumps(body), ssl=self._ssl, timeout=_HTTP_TIMEOUT,
				headers={"Content-Type": "application/jose+json"},
			) as resp:
				self._nonce = resp.headers.get("Replay-Nonce")
				data: Any = await resp.read()
				if not accept_pem or resp.status >= 400:
					try:
						data = json.loads(data or b"{}")
					except ValueError:
						data = {"detail": data[:200].decode(errors="replace")}
				if (
					resp.status == 400 and attempt < _NONCE_ATTEMPTS and isinstance(data, dict)
					and data.get("type") == "urn:ietf:params:acme:error:badNonce"
				):
					continue
				return resp.status, resp.headers, data
		raise AcmeError("badNonce")  # pragma: no cover - the last attempt always returns

	@staticmethod
	def _fail(step: str, status: int, body: Any) -> AcmeError:
		detail = body.get("detail") if isinstance(body, dict) else body
		return AcmeError(f"{step}: HTTP {status}: {detail}")

	async def register(self, email: Optional[str] = None) -> str:
		"""Find or create the account for our key; returns its URL (the kid)."""
		payload: dict[str, Any] = {"termsOfServiceAgreed": True}
		if email:
			payload["contact"] = [f"mailto:{email}"]
		url = (await self._get_directory())["newAccount"]
		status, headers, body = await self._post(url, payload, use_jwk=True)
		if status not in (200, 201):
			raise self._fail("newAccount", status, body)
		self.kid = headers.get("Location")
		if not self.kid:
			raise AcmeError("newAccount: no account URL")
		return self.kid

	async def _poll(self, url: str, step: str, done: set[str]) -> dict:
		loop = asyncio.get_running_loop()
		deadline = loop.time() + _POLL_TIMEOUT
		while True:
			status, _h, body = await self._post(url, None)
			if status != 200:
				raise self._fail(step, status, body)
			state = body.get("status")
			if state in done:
				return body
			if state == "invalid":
				problem = body.get("error") or next(
					(c.get("error") for c in body.get("challenges", []) if c.get("error")), {},
				)
				raise AcmeError(f"{step}: invalid: {(problem or {}).get('detail', problem)}")
			if loop.time() > deadline:
				raise AcmeError(f"{step}: still {state} after {_POLL_TIMEOUT:.0f}s")
			await asyncio.sleep(_POLL_INTERVAL)

	async def obtain(
		self,
		domain: str,
		cert_key: ec.EllipticCurvePrivateKey,
		responder: AlpnResponder,
	) -> bytes:
		"""Order, validate over TLS-ALPN-01, finalise; return the PEM chain."""
		if not self.kid:
			await self.register()
		url = (await self._get_directory())["newOrder"]
		status, headers, order = await self._post(
			url, {"identifiers": [{"type": "dns", "value": domain}]},
		)
		if status != 201:
			raise self._fail("newOrder", status, order)
		order_url = headers.get("Location")

		for authz_url in order.get("authorizations", []):
			status, _h, authz = await self._post(authz_url, None)
			if status != 200:
				raise self._fail("authorization", status, authz)
			if authz.get("status") == "valid":
				continue
			challenge = next(
				(c for c in authz.get("challenges", []) if c.get("type") == "tls-alpn-01"),
				None,
			)
			if challenge is None:
				raise AcmeError("authorization: the CA offered no tls-alpn-01 challenge")
			key_authorization = f"{challenge['token']}.{thumbprint(self._key)}"
			responder.add(domain, *challenge_certificate(domain, key_authorization))
			try:
				status, _h, body = await self._post(challenge["url"], {})
				if status != 200:
					raise self._fail("challenge", status, body)
				await self._poll(authz_url, "validation", {"valid"})
			finally:
				responder.remove(domain)

		order = await self._poll(order_url, "order", {"ready", "valid"})
		if order["status"] == "ready":
			status, _h, body = await self._post(
				order["finalize"], {"csr": _b64(make_csr(domain, cert_key))},
			)
			if status != 200:
				raise self._fail("finalize", status, body)
			order = await self._poll(order_url, "issuance", {"valid"})
		status, _h, chain = await self._post(order["certificate"], None, accept_pem=True)
		if status != 200:
			raise self._fail("certificate", status, chain)
		return chain
