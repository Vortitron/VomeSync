# flake8: noqa
"""The ACME client against Pebble, Let's Encrypt's own test CA.

Pebble's validator really connects to our TLS-ALPN-01 responder, checks the
acmeIdentifier extension, and issues a real certificate for our CSR. It also
rejects 5% of nonces at random, which exercises the badNonce retry.

Needs the Pebble binaries (github.com/letsencrypt/pebble releases):
``PEBBLE_DIR=<dir holding pebble and pebble-challtestsrv> scripts/run-core-tests.sh``.
Skipped without them.
"""
import asyncio
import json
import os
import shutil
import socket
import ssl
import subprocess
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import aiohttp
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.x509.oid import NameOID

from custom_components.vomesync import e2e_acme

PEBBLE_DIR = os.environ.get("PEBBLE_DIR")
pytestmark = [
	pytest.mark.skipif(not PEBBLE_DIR, reason="PEBBLE_DIR not set"),
	# HA's test harness blocks sockets; these talk to a local CA on purpose.
	pytest.mark.enable_socket,
]


def _bin(name):
	for path in Path(PEBBLE_DIR).rglob(name):
		if path.is_file():
			path.chmod(0o755)
			return str(path)
	raise FileNotFoundError(name)


def _free_port():
	with socket.socket() as s:
		s.bind(("127.0.0.1", 0))
		return s.getsockname()[1]


def _wait_port(port, timeout=10):
	end = time.time() + timeout
	while time.time() < end:
		with socket.socket() as s:
			if s.connect_ex(("127.0.0.1", port)) == 0:
				return
		time.sleep(0.1)
	raise TimeoutError(port)


def _self_signed(tmp):
	key = e2e_acme.new_key()
	name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
	now = datetime.now(timezone.utc)
	cert = (
		x509.CertificateBuilder().subject_name(name).issuer_name(name)
		.public_key(key.public_key()).serial_number(x509.random_serial_number())
		.not_valid_before(now - timedelta(hours=1)).not_valid_after(now + timedelta(days=1))
		.add_extension(x509.SubjectAlternativeName([
			x509.DNSName("localhost"), x509.IPAddress(__import__("ipaddress").ip_address("127.0.0.1")),
		]), critical=False)
		.sign(key, hashes.SHA256())
	)
	(tmp / "cert.pem").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
	(tmp / "key.pem").write_bytes(e2e_acme.key_to_pem(key))
	return tmp / "cert.pem", tmp / "key.pem"


@pytest.fixture
def pebble(socket_enabled, tmp_path):
	acme_port, mgmt_port, dns_port, tls_port = (_free_port() for _ in range(4))
	cert, key = _self_signed(tmp_path)
	config = {"pebble": {
		"listenAddress": f"127.0.0.1:{acme_port}",
		"managementListenAddress": f"127.0.0.1:{mgmt_port}",
		"certificate": str(cert), "privateKey": str(key),
		"httpPort": _free_port(), "tlsPort": tls_port,
		"ocspResponderURL": "", "externalAccountBindingRequired": False,
		"domainBlocklist": [], "retryAfter": {"authz": 1, "order": 1},
		"profiles": {"default": {"description": "test", "validityPeriod": 7776000}},
	}}
	(tmp_path / "pebble.json").write_text(json.dumps(config))
	env = {**os.environ, "PEBBLE_VA_NOSLEEP": "1"}
	procs = [
		subprocess.Popen([
			_bin("pebble-challtestsrv"), "-defaultIPv4", "127.0.0.1", "-defaultIPv6", "",
			"-dnsserver", f"127.0.0.1:{dns_port}", "-http01", "", "-https01", "",
			"-tlsalpn01", "", "-doh", "", "-management", f"127.0.0.1:{_free_port()}",
		], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL),
		subprocess.Popen([
			_bin("pebble"), "-config", str(tmp_path / "pebble.json"),
			"-dnsserver", f"127.0.0.1:{dns_port}",
		], env=env, stdout=open(tmp_path / "pebble.log", "wb"), stderr=subprocess.STDOUT),
	]
	try:
		_wait_port(dns_port)  # it serves DNS over TCP as well as UDP
		_wait_port(acme_port)
		_wait_port(mgmt_port)
		ca = ssl.create_default_context(cafile=str(cert))
		yield {
			"directory": f"https://127.0.0.1:{acme_port}/dir", "ssl": ca, "tls_port": tls_port,
			"root_url": f"https://127.0.0.1:{mgmt_port}/roots/0",
		}
	finally:
		for p in procs:
			p.terminate()
			p.wait(timeout=5)


async def test_a_real_certificate_over_tls_alpn(pebble):
	responder = e2e_acme.AlpnResponder()
	await responder.start(port=pebble["tls_port"])
	cert_key = e2e_acme.new_key()
	try:
		async with aiohttp.ClientSession() as session:
			client = e2e_acme.AcmeClient(
				session, pebble["directory"], e2e_acme.new_key(), ssl_context=pebble["ssl"],
			)
			chain = await client.obtain("home1.e2e.vome.test", cert_key, responder)
	finally:
		await responder.stop()

	leaf = x509.load_pem_x509_certificates(chain)[0]
	names = leaf.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
	assert names.get_values_for_type(x509.DNSName) == ["home1.e2e.vome.test"]
	# Issued for the key that never left us.
	assert leaf.public_key().public_numbers() == cert_key.public_key().public_numbers()


async def test_the_same_account_is_found_again(pebble):
	account = e2e_acme.new_key()
	async with aiohttp.ClientSession() as session:
		first = await e2e_acme.AcmeClient(session, pebble["directory"], account, ssl_context=pebble["ssl"]).register()
		again = await e2e_acme.AcmeClient(session, pebble["directory"], account, ssl_context=pebble["ssl"]).register()
	assert first == again


async def test_a_name_we_cannot_prove_fails_cleanly(pebble):
	responder = e2e_acme.AlpnResponder()
	await responder.start(port=pebble["tls_port"])
	real_add = responder.add
	# Present the challenge certificate for the wrong name.
	responder.add = lambda domain, cert, key: real_add("someone-else.e2e.vome.test", cert, key)
	try:
		async with aiohttp.ClientSession() as session:
			client = e2e_acme.AcmeClient(
				session, pebble["directory"], e2e_acme.new_key(), ssl_context=pebble["ssl"],
			)
			with pytest.raises(e2e_acme.AcmeError, match="validation: invalid") as err:
				await client.obtain("home2.e2e.vome.test", e2e_acme.new_key(), responder)
	finally:
		await responder.stop()
	# Refused for the right reason: the handshake, not DNS.
	assert "resolving" not in str(err.value) and "refused" not in str(err.value)
