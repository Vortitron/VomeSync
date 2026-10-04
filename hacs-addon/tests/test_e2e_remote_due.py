# flake8: noqa
"""When the home asks for a new certificate (e2e_remote.E2ERemote.certificate_due).

A certificate is replaced when there is none, when it names another host,
when it came from a different CA than Vome now asks for, and in its last
30 days. The CA case was found live: a home moved from Let's Encrypt's
staging CA kept the staging certificate, which a browser refuses outright
on vome.io (HSTS) with no way past.
"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.x509.oid import NameOID

from custom_components.vomesync import e2e_acme, e2e_remote

PROD = e2e_acme.LETS_ENCRYPT
STAGING = e2e_acme.LETS_ENCRYPT_STAGING


def _cert(days):
	key = e2e_acme.new_key()
	name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "h.e2e.vome.io")])
	now = datetime.now(timezone.utc)
	return (
		x509.CertificateBuilder().subject_name(name).issuer_name(name)
		.public_key(key.public_key()).serial_number(1)
		.not_valid_before(now - timedelta(days=1)).not_valid_after(now + timedelta(days=days))
		.sign(key, hashes.SHA256())
	).public_bytes(serialization.Encoding.PEM)


def _remote(tmp_path, *, host="h.e2e.vome.io", directory=PROD, cert_days=89,
		stored_host="h.e2e.vome.io", stored_directory=PROD):
	r = e2e_remote.E2ERemote(SimpleNamespace(), SimpleNamespace(entry_id="e"), "http://127.0.0.1:8123",
		store_dir=tmp_path)
	r.host, r.directory = host, directory
	if cert_days is not None:
		r._write_private("cert.pem", _cert(cert_days))
	if stored_host is not None:
		r._write_private("cert.host", stored_host.encode())
	if stored_directory is not None:
		r._write_private("cert.directory", stored_directory.encode())
	return r


def test_a_fresh_matching_certificate_is_kept(tmp_path):
	assert _remote(tmp_path).certificate_due() is False


def test_no_certificate(tmp_path):
	assert _remote(tmp_path, cert_days=None).certificate_due() is True


def test_another_name(tmp_path):
	assert _remote(tmp_path, host="new.e2e.vome.io").certificate_due() is True


def test_another_ca(tmp_path):
	assert _remote(tmp_path, stored_directory=STAGING).certificate_due() is True


def test_a_certificate_from_before_the_ca_was_recorded(tmp_path):
	assert _remote(tmp_path, stored_directory=None).certificate_due() is True


def test_the_last_30_days(tmp_path):
	assert _remote(tmp_path, cert_days=29).certificate_due() is True
	assert _remote(tmp_path, cert_days=31).certificate_due() is False
