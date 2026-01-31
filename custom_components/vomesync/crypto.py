"""Cryptographic helpers for VomeSync (v2 signed API).

This module implements:
- Master key generation (Ed25519) stored locally in Home Assistant
- Deterministic per-switch subkeys derived from the master seed + index
- Deterministic switch UID derivation from the switch public key
- Stable JSON canonicalisation + Ed25519 signatures (base64url)
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass
from typing import Any, Dict, Optional

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat


_CROCKFORD_BASE32_ALPHABET = "0123456789abcdefghjkmnpqrstvwxyz"
_SWITCH_UID_PREFIX = "vs_"
_SWITCH_UID_HASH_PREFIX = b"vomesync:switch_uid:v1:"
_SWITCH_SEED_DERIVE_PREFIX = b"vomesync:switch_seed:v1:"


def _b64url_encode(data: bytes) -> str:
	return base64.urlsafe_b64encode(data).decode("utf-8").rstrip("=")


def _b64url_decode(data: str) -> bytes:
	# Add padding back for Python base64 decoder
	pad_len = (-len(data)) % 4
	return base64.urlsafe_b64decode(data + ("=" * pad_len))


def _base32_crockford_encode(data: bytes) -> str:
	bits = 0
	bits_len = 0
	out = []
	for b in data:
		bits = (bits << 8) | b
		bits_len += 8
		while bits_len >= 5:
			bits_len -= 5
			idx = (bits >> bits_len) & 31
			out.append(_CROCKFORD_BASE32_ALPHABET[idx])
	if bits_len > 0:
		idx = (bits << (5 - bits_len)) & 31
		out.append(_CROCKFORD_BASE32_ALPHABET[idx])
	return "".join(out)


def generate_master_seed_b64url() -> str:
	"""Generate a new 32-byte master seed encoded as base64url."""
	return _b64url_encode(secrets.token_bytes(32))


def owner_pubkey_b64url(master_seed_b64url: str) -> str:
	seed = _b64url_decode(master_seed_b64url)
	if len(seed) != 32:
		raise ValueError("master seed must be 32 bytes")
	priv = Ed25519PrivateKey.from_private_bytes(seed)
	pub = priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
	return _b64url_encode(pub)


def derive_switch_seed(master_seed_b64url: str, index: int) -> bytes:
	"""Derive a deterministic 32-byte switch seed from master seed + index."""
	if index < 0:
		raise ValueError("index must be >= 0")
	master = _b64url_decode(master_seed_b64url)
	if len(master) != 32:
		raise ValueError("master seed must be 32 bytes")
	msg = _SWITCH_SEED_DERIVE_PREFIX + str(index).encode("utf-8")
	return hmac.new(master, msg, hashlib.sha256).digest()


def switch_pubkey_b64url(master_seed_b64url: str, index: int) -> str:
	seed = derive_switch_seed(master_seed_b64url, index)
	priv = Ed25519PrivateKey.from_private_bytes(seed)
	pub = priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
	return _b64url_encode(pub)


def derive_switch_uid_from_switch_pubkey_b64url(switch_pubkey_b64url: str) -> str:
	pub = _b64url_decode(switch_pubkey_b64url)
	if len(pub) != 32:
		raise ValueError("switch public key must be 32 bytes")
	digest = hashlib.sha256(_SWITCH_UID_HASH_PREFIX + pub).digest()
	short = digest[:16]
	return _SWITCH_UID_PREFIX + _base32_crockford_encode(short)


def canonical_json(payload: Dict[str, Any]) -> str:
	"""Deterministic JSON string suitable for signing/verification across languages."""
	return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sign_b64url(private_key: Ed25519PrivateKey, message: str) -> str:
	sig = private_key.sign(message.encode("utf-8"))
	return _b64url_encode(sig)


def owner_private_key(master_seed_b64url: str) -> Ed25519PrivateKey:
	seed = _b64url_decode(master_seed_b64url)
	if len(seed) != 32:
		raise ValueError("master seed must be 32 bytes")
	return Ed25519PrivateKey.from_private_bytes(seed)


def switch_private_key(master_seed_b64url: str, index: int) -> Ed25519PrivateKey:
	seed = derive_switch_seed(master_seed_b64url, index)
	return Ed25519PrivateKey.from_private_bytes(seed)


def new_nonce() -> str:
	"""Create a nonce suitable for v2 requests (URL-safe, length-limited)."""
	# token_urlsafe(18) is typically 24 chars; within our 8..128 requirement.
	return secrets.token_urlsafe(18)


@dataclass(frozen=True)
class V2CreateSwitchRequest:
	ownerPubKey: str
	switchPubKey: str
	index: int
	ts: int
	nonce: str
	sigOwner: str
	sigSwitch: str
	name: str
	description: str
	location: str
	category: str
	publicize: bool
	link: str
	iconUrl: Optional[str] = None
	bannerUrl: Optional[str] = None
	captchaToken: str = ""


def build_v2_create_switch_request(
	master_seed_b64url: str,
	index: int,
	name: str = "",
	description: str = "",
	location: str = "",
	category: str = "Other",
	publicize: bool = False,
	link: str = "",
	icon_url: Optional[str] = None,
	banner_url: Optional[str] = None,
	captcha_token: str = "",
	ts: Optional[int] = None,
	nonce: Optional[str] = None,
) -> V2CreateSwitchRequest:
	owner_pub = owner_pubkey_b64url(master_seed_b64url)
	switch_pub = switch_pubkey_b64url(master_seed_b64url, index)
	uid = derive_switch_uid_from_switch_pubkey_b64url(switch_pub)
	# Use real timestamp; keep deterministic for tests when provided.
	if ts is None:
		import time
		ts_i = int(time.time() * 1000)
	else:
		ts_i = int(ts)
	nonce_s = nonce or new_nonce()

	payload = {
		"v": 2,
		"action": "create_switch",
		"ownerPubKey": owner_pub,
		"switchPubKey": switch_pub,
		"uid": uid,
		"index": int(index),
		"ts": ts_i,
		"nonce": nonce_s,
		"payload": {
			"description": description or "",
			"location": location or "",
			"category": category or "Other",
			"publicize": bool(publicize),
			"link": link or "",
		},
	}

	# IMPORTANT: these must only be included when explicitly set, otherwise older
	# clients (and signatures) will not match server canonicalisation.
	name_clean = str(name).strip() if isinstance(name, str) else ""
	if name_clean:
		payload["payload"]["name"] = name_clean
	icon_clean = str(icon_url).strip() if isinstance(icon_url, str) else ""
	if icon_clean:
		payload["payload"]["iconUrl"] = icon_clean
	banner_clean = str(banner_url).strip() if isinstance(banner_url, str) else ""
	if banner_clean:
		payload["payload"]["bannerUrl"] = banner_clean

	canon = canonical_json(payload)

	owner_sig = sign_b64url(owner_private_key(master_seed_b64url), canon)
	switch_sig = sign_b64url(switch_private_key(master_seed_b64url, index), canon)

	return V2CreateSwitchRequest(
		ownerPubKey=owner_pub,
		switchPubKey=switch_pub,
		index=int(index),
		ts=ts_i,
		nonce=nonce_s,
		sigOwner=owner_sig,
		sigSwitch=switch_sig,
		name=name_clean,
		description=description or "",
		location=location or "",
		category=category or "Other",
		publicize=bool(publicize),
		link=link or "",
		iconUrl=payload["payload"].get("iconUrl"),
		bannerUrl=payload["payload"].get("bannerUrl"),
		captchaToken=captcha_token or "",
	)


def build_v2_update_switch_request(
	master_seed_b64url: str,
	uid: str,
	updates: Dict[str, Any],
	captcha_token: str = "",
	ts: Optional[int] = None,
	nonce: Optional[str] = None,
) -> Dict[str, Any]:
	"""Build a signed v2 update_switch request (signed by owner key)."""
	if not isinstance(uid, str) or not uid:
		raise ValueError("uid must be a non-empty string")
	if not isinstance(updates, dict) or not updates:
		raise ValueError("updates must be a non-empty dict")

	owner_pub = owner_pubkey_b64url(master_seed_b64url)
	if ts is None:
		import time
		ts_i = int(time.time() * 1000)
	else:
		ts_i = int(ts)
	nonce_s = nonce or new_nonce()

	payload = {
		"v": 2,
		"action": "update_switch",
		"uid": uid,
		"ownerPubKey": owner_pub,
		"ts": ts_i,
		"nonce": nonce_s,
		"payload": updates,
	}
	canon = canonical_json(payload)
	sig_owner = sign_b64url(owner_private_key(master_seed_b64url), canon)

	req: Dict[str, Any] = {
		"ownerPubKey": owner_pub,
		"ts": ts_i,
		"nonce": nonce_s,
		"sigOwner": sig_owner,
		**updates,
	}
	if captcha_token is not None:
		req["captchaToken"] = captcha_token or ""
	return req


def build_v2_create_access_key_request(
	master_seed_b64url: str,
	uid: str,
	name: Optional[str] = None,
	permissions: Optional[list[str]] = None,
	ttl_seconds: Optional[int] = None,
	ts: Optional[int] = None,
	nonce: Optional[str] = None,
) -> Dict[str, Any]:
	"""Build a signed v2 create_access_key request (signed by owner key)."""
	if not isinstance(uid, str) or not uid:
		raise ValueError("uid must be a non-empty string")

	owner_pub = owner_pubkey_b64url(master_seed_b64url)
	if ts is None:
		import time
		ts_i = int(time.time() * 1000)
	else:
		ts_i = int(ts)
	nonce_s = nonce or new_nonce()

	payload_obj: Dict[str, Any] = {}
	if name is not None:
		payload_obj["name"] = str(name)
	if permissions is not None:
		payload_obj["permissions"] = list(permissions)
	if ttl_seconds is not None:
		payload_obj["ttlSeconds"] = int(ttl_seconds)

	payload = {
		"v": 2,
		"action": "create_access_key",
		"uid": uid,
		"ownerPubKey": owner_pub,
		"ts": ts_i,
		"nonce": nonce_s,
		"payload": payload_obj,
	}
	canon = canonical_json(payload)
	sig_owner = sign_b64url(owner_private_key(master_seed_b64url), canon)

	req: Dict[str, Any] = {
		"ownerPubKey": owner_pub,
		"ts": ts_i,
		"nonce": nonce_s,
		"sigOwner": sig_owner,
	}
	if name is not None:
		req["name"] = str(name)
	if permissions is not None:
		req["permissions"] = list(permissions)
	if ttl_seconds is not None:
		req["ttlSeconds"] = int(ttl_seconds)
	return req


def build_v2_list_access_keys_request(
	master_seed_b64url: str,
	uid: str,
	ts: Optional[int] = None,
	nonce: Optional[str] = None,
) -> Dict[str, Any]:
	"""Build a signed v2 list_access_keys request (signed by owner key)."""
	if not isinstance(uid, str) or not uid:
		raise ValueError("uid must be a non-empty string")

	owner_pub = owner_pubkey_b64url(master_seed_b64url)
	if ts is None:
		import time
		ts_i = int(time.time() * 1000)
	else:
		ts_i = int(ts)
	nonce_s = nonce or new_nonce()

	payload = {
		"v": 2,
		"action": "list_access_keys",
		"uid": uid,
		"ownerPubKey": owner_pub,
		"ts": ts_i,
		"nonce": nonce_s,
	}
	canon = canonical_json(payload)
	sig_owner = sign_b64url(owner_private_key(master_seed_b64url), canon)

	return {
		"ownerPubKey": owner_pub,
		"ts": ts_i,
		"nonce": nonce_s,
		"sigOwner": sig_owner,
	}


def build_v2_revoke_access_key_request(
	master_seed_b64url: str,
	uid: str,
	api_key: Optional[str] = None,
	key_id: Optional[str] = None,
	ts: Optional[int] = None,
	nonce: Optional[str] = None,
) -> Dict[str, Any]:
	"""Build a signed v2 revoke_access_key request (signed by owner key)."""
	if not isinstance(uid, str) or not uid:
		raise ValueError("uid must be a non-empty string")
	if api_key is None and key_id is None:
		raise ValueError("api_key or key_id must be provided")
	if api_key is not None and (not isinstance(api_key, str) or not api_key):
		raise ValueError("api_key must be a non-empty string")
	if key_id is not None and (not isinstance(key_id, str) or not key_id):
		raise ValueError("key_id must be a non-empty string")

	owner_pub = owner_pubkey_b64url(master_seed_b64url)
	if ts is None:
		import time
		ts_i = int(time.time() * 1000)
	else:
		ts_i = int(ts)
	nonce_s = nonce or new_nonce()

	key_part: Dict[str, Any] = {}
	if key_id is not None:
		key_part["keyId"] = key_id
	else:
		key_part["apiKey"] = api_key

	payload = {
		"v": 2,
		"action": "revoke_access_key",
		"uid": uid,
		"ownerPubKey": owner_pub,
		"ts": ts_i,
		"nonce": nonce_s,
		**key_part,
	}
	canon = canonical_json(payload)
	sig_owner = sign_b64url(owner_private_key(master_seed_b64url), canon)

	req = {
		"ownerPubKey": owner_pub,
		"ts": ts_i,
		"nonce": nonce_s,
		"sigOwner": sig_owner,
		**key_part,
	}
	return req


def build_v2_my_switches_request(master_seed_b64url: str, ts: Optional[int] = None, nonce: Optional[str] = None) -> Dict[str, Any]:
	if ts is None:
		import time
		ts_i = int(time.time() * 1000)
	else:
		ts_i = int(ts)
	nonce_s = nonce or new_nonce()
	owner_pub = owner_pubkey_b64url(master_seed_b64url)

	payload = {
		"v": 2,
		"action": "my_switches",
		"ownerPubKey": owner_pub,
		"ts": ts_i,
		"nonce": nonce_s,
	}
	canon = canonical_json(payload)
	sig_owner = sign_b64url(owner_private_key(master_seed_b64url), canon)
	return {
		"ownerPubKey": owner_pub,
		"ts": ts_i,
		"nonce": nonce_s,
		"sigOwner": sig_owner,
	}


def build_v2_set_state_request(
	master_seed_b64url: str,
	uid: str,
	index: int,
	state: bool,
	params: Optional[Dict[str, Any]] = None,
	ts: Optional[int] = None,
	nonce: Optional[str] = None,
) -> Dict[str, Any]:
	if ts is None:
		import time
		ts_i = int(time.time() * 1000)
	else:
		ts_i = int(ts)
	nonce_s = nonce or new_nonce()
	params_obj = params or {}

	payload = {
		"v": 2,
		"action": "set_state",
		"uid": uid,
		"ts": ts_i,
		"nonce": nonce_s,
		"state": bool(state),
		"params": params_obj,
	}
	canon = canonical_json(payload)
	sig_switch = sign_b64url(switch_private_key(master_seed_b64url, index), canon)
	return {
		"ts": ts_i,
		"nonce": nonce_s,
		"sigSwitch": sig_switch,
		"state": bool(state),
		"params": params_obj,
	}
