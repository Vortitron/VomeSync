const crypto = require('crypto');

const CROCKFORD_BASE32_ALPHABET = '0123456789abcdefghjkmnpqrstvwxyz';

// RFC 8410 SubjectPublicKeyInfo prefix for Ed25519 (OID 1.3.101.112)
// 302a300506032b6570032100 + 32 bytes public key
const ED25519_SPKI_PREFIX = Buffer.from('302a300506032b6570032100', 'hex');

const SWITCH_UID_PREFIX = 'vs_';
const SWITCH_UID_HASH_PREFIX = Buffer.from('vomesync:switch_uid:v1:', 'utf8');
const OWNER_ID_HASH_PREFIX = Buffer.from('vomesync:owner_id:v1:', 'utf8');

function sha256(data) {
	return crypto.createHash('sha256').update(data).digest();
}

function base32CrockfordEncode(bytes) {
	if (!Buffer.isBuffer(bytes)) {
		throw new TypeError('bytes must be a Buffer');
	}

	let bits = 0;
	let bitsLength = 0;
	let output = '';

	for (const b of bytes) {
		bits = (bits << 8) | b;
		bitsLength += 8;

		while (bitsLength >= 5) {
			bitsLength -= 5;
			const index = (bits >> bitsLength) & 31;
			output += CROCKFORD_BASE32_ALPHABET[index];
		}
	}

	if (bitsLength > 0) {
		const index = (bits << (5 - bitsLength)) & 31;
		output += CROCKFORD_BASE32_ALPHABET[index];
	}

	return output;
}

function stableJsonSort(value) {
	if (Array.isArray(value)) {
		return value.map(stableJsonSort);
	}

	if (value && typeof value === 'object' && value.constructor === Object) {
		const sorted = {};
		for (const key of Object.keys(value).sort()) {
			sorted[key] = stableJsonSort(value[key]);
		}
		return sorted;
	}

	return value;
}

function stableJsonStringify(obj) {
	return JSON.stringify(stableJsonSort(obj));
}

function decodeBase64Url(value, label) {
	if (typeof value !== 'string' || value.length === 0) {
		throw new TypeError(`${label} must be a non-empty string`);
	}
	if (!/^[A-Za-z0-9_-]+$/.test(value)) {
		throw new Error(`${label} must be base64url`);
	}

	return Buffer.from(value, 'base64url');
}

function deriveOwnerIdFromOwnerPubKeyB64Url(ownerPubKeyB64Url) {
	const ownerPubKeyRaw = decodeBase64Url(ownerPubKeyB64Url, 'ownerPubKey');
	if (ownerPubKeyRaw.length !== 32) {
		throw new Error('ownerPubKey must be 32 bytes (Ed25519 raw public key)');
	}

	const digest = sha256(Buffer.concat([OWNER_ID_HASH_PREFIX, ownerPubKeyRaw]));
	// 16 bytes is plenty for a stable identifier; hex keeps Redis keys compact
	return digest.subarray(0, 16).toString('hex');
}

function deriveSwitchUidFromSwitchPubKeyB64Url(switchPubKeyB64Url) {
	const switchPubKeyRaw = decodeBase64Url(switchPubKeyB64Url, 'switchPubKey');
	if (switchPubKeyRaw.length !== 32) {
		throw new Error('switchPubKey must be 32 bytes (Ed25519 raw public key)');
	}

	const digest = sha256(Buffer.concat([SWITCH_UID_HASH_PREFIX, switchPubKeyRaw]));
	const short = digest.subarray(0, 16);
	return `${SWITCH_UID_PREFIX}${base32CrockfordEncode(short)}`;
}

function createEd25519PublicKeyFromRaw(pubKeyRaw) {
	if (!Buffer.isBuffer(pubKeyRaw) || pubKeyRaw.length !== 32) {
		throw new Error('pubKeyRaw must be a 32-byte Buffer');
	}

	const spkiDer = Buffer.concat([ED25519_SPKI_PREFIX, pubKeyRaw]);
	return crypto.createPublicKey({ key: spkiDer, format: 'der', type: 'spki' });
}

function verifyEd25519SignatureB64Url(pubKeyB64Url, message, sigB64Url) {
	const pubKeyRaw = decodeBase64Url(pubKeyB64Url, 'pubKey');
	const sigRaw = decodeBase64Url(sigB64Url, 'signature');

	if (pubKeyRaw.length !== 32) {
		throw new Error('pubKey must be 32 bytes (Ed25519 raw public key)');
	}
	if (sigRaw.length !== 64) {
		throw new Error('signature must be 64 bytes (Ed25519 signature)');
	}
	if (typeof message !== 'string') {
		throw new TypeError('message must be a string');
	}

	const keyObject = createEd25519PublicKeyFromRaw(pubKeyRaw);
	return crypto.verify(null, Buffer.from(message, 'utf8'), keyObject, sigRaw);
}

module.exports = {
	stableJsonStringify,
	deriveOwnerIdFromOwnerPubKeyB64Url,
	deriveSwitchUidFromSwitchPubKeyB64Url,
	verifyEd25519SignatureB64Url
};


