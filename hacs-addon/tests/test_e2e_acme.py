# flake8: noqa
"""The pure parts of the ACME client: what is signed and what is presented.

The full exchange runs against Pebble in tests_core/test_e2e_acme_pebble.py.
"""
import base64
import hashlib
import json

from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature

from custom_components.vomesync import e2e_acme


def _unb64(s):
	return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def test_jws_verifies_and_carries_the_jwk_until_there_is_an_account():
	key = e2e_acme.new_key()
	jws = e2e_acme.sign_jws(key, "https://ca/new-acct", "n1", {"termsOfServiceAgreed": True})
	protected = json.loads(_unb64(jws["protected"]))
	assert protected == {"alg": "ES256", "nonce": "n1", "url": "https://ca/new-acct", "jwk": e2e_acme.jwk(key)}
	sig = _unb64(jws["signature"])
	assert len(sig) == 64
	der = encode_dss_signature(int.from_bytes(sig[:32], "big"), int.from_bytes(sig[32:], "big"))
	key.public_key().verify(der, f"{jws['protected']}.{jws['payload']}".encode(), ec.ECDSA(hashes.SHA256()))


def test_jws_uses_the_kid_once_registered_and_empty_payload_for_post_as_get():
	key = e2e_acme.new_key()
	jws = e2e_acme.sign_jws(key, "https://ca/authz/1", "n2", None, kid="https://ca/acct/7")
	protected = json.loads(_unb64(jws["protected"]))
	assert protected["kid"] == "https://ca/acct/7" and "jwk" not in protected
	assert jws["payload"] == ""


def test_thumbprint_is_rfc7638_over_sorted_members():
	key = e2e_acme.new_key()
	canonical = json.dumps(e2e_acme.jwk(key), sort_keys=True, separators=(",", ":"))
	assert canonical.startswith('{"crv":"P-256","kty":"EC","x":')
	expected = base64.urlsafe_b64encode(hashlib.sha256(canonical.encode()).digest()).rstrip(b"=").decode()
	assert e2e_acme.thumbprint(key) == expected


def test_challenge_certificate_carries_the_key_authorisation_digest():
	cert_pem, _key_pem = e2e_acme.challenge_certificate("h.e2e.vome.io", "tok.thumb")
	cert = x509.load_pem_x509_certificate(cert_pem)
	ext = cert.extensions.get_extension_for_oid(x509.ObjectIdentifier("1.3.6.1.5.5.7.1.31"))
	assert ext.critical
	assert ext.value.value == b"\x04\x20" + hashlib.sha256(b"tok.thumb").digest()
	sans = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
	assert sans.get_values_for_type(x509.DNSName) == ["h.e2e.vome.io"]


def test_csr_names_only_this_home():
	key = e2e_acme.new_key()
	csr = x509.load_der_x509_csr(e2e_acme.make_csr("h.e2e.vome.io", key))
	assert csr.is_signature_valid
	sans = csr.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
	assert sans.get_values_for_type(x509.DNSName) == ["h.e2e.vome.io"]


def test_keys_round_trip_through_pem():
	key = e2e_acme.new_key()
	assert e2e_acme.thumbprint(e2e_acme.key_from_pem(e2e_acme.key_to_pem(key))) == e2e_acme.thumbprint(key)
