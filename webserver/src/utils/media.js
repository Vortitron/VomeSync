const crypto = require('crypto');
const fs = require('fs');
const path = require('path');
const dns = require('dns').promises;
const net = require('net');

const sharp = require('sharp');
const logger = require('./logger');

const DEFAULT_MEDIA_DIR = 'media';
const MEDIA_URL_PREFIX = '/api/media';

const MAX_REDIRECTS = 3;
const DEFAULT_FETCH_TIMEOUT_MS = 10_000;

// Reasonable defaults; can be overridden via env if needed.
const MAX_ICON_BYTES = Number.parseInt(process.env.MEDIA_MAX_ICON_BYTES || '', 10) || 2_000_000; // 2MB
const MAX_BANNER_BYTES = Number.parseInt(process.env.MEDIA_MAX_BANNER_BYTES || '', 10) || 8_000_000; // 8MB
const MAX_INPUT_PIXELS = Number.parseInt(process.env.MEDIA_MAX_INPUT_PIXELS || '', 10) || 20_000_000; // 20MP

function getMediaRootDir() {
	const dir = process.env.MEDIA_DIR || DEFAULT_MEDIA_DIR;
	return path.isAbsolute(dir) ? dir : path.join(process.cwd(), dir);
}

function getSwitchMediaDir(uid) {
	return path.join(getMediaRootDir(), 'switch', uid);
}

function isLocalMediaUrl(url) {
	if (typeof url !== 'string' || !url) return false;
	const trimmed = url.trim();
	if (trimmed.startsWith(MEDIA_URL_PREFIX + '/')) return true;
	// Same-origin absolute URLs that include /api/media
	try {
		const u = new URL(trimmed);
		return u.pathname.startsWith(MEDIA_URL_PREFIX + '/');
	} catch {
		return false;
	}
}

function isPrivateIp(ip) {
	const kind = net.isIP(ip);
	if (!kind) return true; // treat unknown as unsafe

	if (kind === 4) {
		const parts = ip.split('.').map((n) => Number.parseInt(n, 10));
		if (parts.length !== 4 || parts.some((n) => Number.isNaN(n))) return true;
		const [a, b] = parts;

		// 0.0.0.0/8, 10.0.0.0/8, 127.0.0.0/8
		if (a === 0 || a === 10 || a === 127) return true;
		// 169.254.0.0/16
		if (a === 169 && b === 254) return true;
		// 172.16.0.0/12
		if (a === 172 && b >= 16 && b <= 31) return true;
		// 192.168.0.0/16
		if (a === 192 && b === 168) return true;
		// 100.64.0.0/10 (CGNAT)
		if (a === 100 && b >= 64 && b <= 127) return true;
		return false;
	}

	// IPv6 checks (simple prefix checks)
	const normalized = ip.toLowerCase();
	if (normalized === '::' || normalized === '::1') return true;
	// Link-local fe80::/10
	if (normalized.startsWith('fe8') || normalized.startsWith('fe9') || normalized.startsWith('fea') || normalized.startsWith('feb')) return true;
	// Unique local fc00::/7
	if (normalized.startsWith('fc') || normalized.startsWith('fd')) return true;
	return false;
}

async function assertSafeRemoteUrl(urlString) {
	let u;
	try {
		u = new URL(urlString);
	} catch {
		throw new Error('Invalid URL');
	}

	if (u.protocol !== 'http:' && u.protocol !== 'https:') {
		throw new Error('Only http/https URLs are allowed');
	}

	// Block obvious local hostnames
	const host = u.hostname.toLowerCase();
	if (host === 'localhost' || host.endsWith('.localhost') || host === '0.0.0.0') {
		throw new Error('URL host is not allowed');
	}

	// If hostname is an IP literal, validate directly
	if (net.isIP(host)) {
		if (isPrivateIp(host)) {
			throw new Error('URL host is not allowed');
		}
		return;
	}

	// Resolve DNS and ensure all returned IPs are public (defence-in-depth)
	let records;
	try {
		records = await dns.lookup(host, { all: true });
	} catch (_err) {
		throw new Error('Failed to resolve image host');
	}
	if (!records || records.length === 0) {
		throw new Error('Failed to resolve image host');
	}
	for (const r of records) {
		if (!r || !r.address || isPrivateIp(r.address)) {
			throw new Error('URL host is not allowed');
		}
	}
}

async function fetchWithLimits(urlString, maxBytes, timeoutMs = DEFAULT_FETCH_TIMEOUT_MS) {
	let current = urlString;
	for (let i = 0; i <= MAX_REDIRECTS; i += 1) {
		await assertSafeRemoteUrl(current);

		const ac = new AbortController();
		const timeout = setTimeout(() => ac.abort(), timeoutMs);
		let res;
		try {
			res = await fetch(current, { redirect: 'manual', signal: ac.signal });
		} finally {
			clearTimeout(timeout);
		}

		if (res.status >= 300 && res.status < 400 && res.headers && res.headers.get('location')) {
			const nextUrl = new URL(res.headers.get('location'), current).toString();
			current = nextUrl;
			continue;
		}

		if (!res.ok) {
			throw new Error(`Image fetch failed (HTTP ${res.status})`);
		}

		// Stream into a Buffer with a hard cap
		const chunks = [];
		let total = 0;

		if (!res.body) {
			throw new Error('Image fetch failed (no body)');
		}

		const reader = res.body.getReader();
		while (true) {
			// eslint-disable-next-line no-await-in-loop
			const { done, value } = await reader.read();
			if (done) break;
			if (value) {
				total += value.byteLength || value.length || 0;
				if (total > maxBytes) {
					try { reader.cancel(); } catch (_err) { /* ignore */ }
					throw new Error('Image too large');
				}
				chunks.push(Buffer.from(value));
			}
		}
		return Buffer.concat(chunks);
	}

	throw new Error('Too many redirects');
}

async function ensureDir(dirPath) {
	await fs.promises.mkdir(dirPath, { recursive: true });
}

function sha256Hex(buffer) {
	return crypto.createHash('sha256').update(buffer).digest('hex');
}

async function convertToWebp(buffer, kind) {
	const base = sharp(buffer, { limitInputPixels: MAX_INPUT_PIXELS, failOnError: true });
	if (kind === 'icon') {
		return await base
			.resize(256, 256, { fit: 'cover', withoutEnlargement: false })
			.webp({ quality: 82 })
			.toBuffer();
	}
	// banner
	return await base
		.resize(1600, 900, { fit: 'inside', withoutEnlargement: true })
		.webp({ quality: 82 })
		.toBuffer();
}

async function storeWebp(uid, kind, webpBuffer) {
	const digest = sha256Hex(webpBuffer).slice(0, 16);
	const filename = `${kind}_${digest}.webp`;
	const dir = getSwitchMediaDir(uid);
	await ensureDir(dir);
	const absPath = path.join(dir, filename);
	await fs.promises.writeFile(absPath, webpBuffer);
	return `${MEDIA_URL_PREFIX}/switch/${encodeURIComponent(uid)}/${filename}`;
}

async function ingestImageBuffer(uid, kind, buffer) {
	if (!uid || !kind || !buffer) {
		throw new Error('Invalid image input');
	}
	const maxBytes = kind === 'icon' ? MAX_ICON_BYTES : MAX_BANNER_BYTES;
	if (Buffer.isBuffer(buffer) && buffer.length > maxBytes) {
		throw new Error('Image too large');
	}
	const webp = await convertToWebp(buffer, kind);
	return await storeWebp(uid, kind, webp);
}

async function ingestImageFromUrl(uid, kind, urlString) {
	if (!urlString || typeof urlString !== 'string') {
		throw new Error('Invalid URL');
	}
	if (isLocalMediaUrl(urlString)) {
		// Normalise to a local path (strip origin if provided)
		try {
			const u = new URL(urlString);
			return u.pathname;
		} catch {
			return urlString.trim();
		}
	}

	const maxBytes = kind === 'icon' ? MAX_ICON_BYTES : MAX_BANNER_BYTES;
	const downloaded = await fetchWithLimits(urlString.trim(), maxBytes);
	return await ingestImageBuffer(uid, kind, downloaded);
}

async function deleteSwitchMedia(uid) {
	if (!uid) return;
	const dir = getSwitchMediaDir(uid);
	try {
		await fs.promises.rm(dir, { recursive: true, force: true });
	} catch (err) {
		logger.warn('Failed to delete media dir for %s: %s', uid, err && err.message ? err.message : String(err));
	}
}

module.exports = {
	getMediaRootDir,
	isLocalMediaUrl,
	ingestImageFromUrl,
	ingestImageBuffer,
	deleteSwitchMedia
};


