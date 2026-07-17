"""LAN path routing for friendly-domain HTTP/WebSocket forwarding.

Maps ``/t/<slug>/…`` on the friendly domain to a configured LAN host:port.
Pure helpers (no Home Assistant imports) so HACS and the Supervisor add-on
share one implementation.

Security model:
  * Only explicitly configured, enabled routes are reachable.
  * Slugs are strict ``[a-z0-9-]`` tokens — no path traversal via the slug.
  * Dot segments in the remainder are rejected (same rule as HA forwarding).
  * Absolute ``Location`` / ``Set-Cookie`` Path headers are rewritten under
	the ``/t/<slug>/`` prefix so the browser stays on the tunnel.
"""
from __future__ import annotations

import re
from typing import Any, Optional
from urllib.parse import unquote, urlparse, urlunparse

# Public URL prefix on the friendly domain.  Keep short: it appears in every
# LAN path and in rewritten redirects/cookies.
LAN_PATH_PREFIX = "/t/"

# Route dict keys (stored under CONF_RELAY_LAN_ROUTES in entry options).
ROUTE_SLUG = "slug"
ROUTE_NAME = "name"
ROUTE_HOST = "host"
ROUTE_PORT = "port"
ROUTE_SCHEME = "scheme"
ROUTE_ENABLED = "enabled"
ROUTE_WEBSOCKET = "websocket"

LAN_ROUTE_SCHEMES = ("http", "https", "tcp")
LAN_MAX_ROUTES = 32
LAN_SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,30}[a-z0-9])?$")
# Host: IPv4, IPv6 in brackets, or a simple DNS label sequence (no scheme/path).
LAN_HOST_RE = re.compile(
	r"^(?:"
	r"(?:(?:25[0-5]|2[0-4]\d|[01]?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|[01]?\d?\d)"
	r"|\[?[0-9a-fA-F:]+\]?"
	r"|(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)*[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?"
	r")$"
)


def normalise_slug(raw: Any) -> str:
	"""Lower-case, strip, collapse underscores to hyphens."""
	return str(raw or "").strip().lower().replace("_", "-")


def parse_lan_path(path: Any) -> Optional[tuple[str, str]]:
	"""Split a request path into ``(slug, remainder_with_query)``.

	``remainder`` always starts with ``/`` (or ``/?…``).  Returns ``None`` when
	the path is not a LAN tunnel URL.
	"""
	if not isinstance(path, str) or not path.startswith(LAN_PATH_PREFIX):
		return None
	rest = path[len(LAN_PATH_PREFIX):]
	if not rest:
		return None
	query = ""
	if "?" in rest:
		rest, _, q = rest.partition("?")
		query = "?" + q
	# First segment is the slug; the rest is the upstream path.
	parts = rest.split("/", 1)
	slug = normalise_slug(parts[0])
	if not slug or not LAN_SLUG_RE.match(slug):
		return None
	remainder = "/" + parts[1] if len(parts) > 1 else "/"
	# Reject dot segments in the remainder (same escape concern as HA paths).
	portion = remainder.split("?", 1)[0]
	for segment in portion.split("/"):
		if segment in (".", "..") or unquote(segment) in (".", ".."):
			return None
	return slug, remainder + query


def find_route(routes: Any, slug: str) -> Optional[dict]:
	"""Return the first enabled route matching ``slug``, or ``None``."""
	if not isinstance(routes, list):
		return None
	want = normalise_slug(slug)
	for raw in routes:
		if not isinstance(raw, dict):
			continue
		if normalise_slug(raw.get(ROUTE_SLUG)) != want:
			continue
		if raw.get(ROUTE_ENABLED) is False:
			return None
		return raw
	return None


def route_base_url(route: dict) -> Optional[str]:
	"""Build ``http(s)://host:port`` for a validated route dict."""
	host = str(route.get(ROUTE_HOST) or "").strip()
	if not host or not LAN_HOST_RE.match(host):
		return None
	try:
		port = int(route.get(ROUTE_PORT) or 0)
	except (TypeError, ValueError):
		return None
	if port < 1 or port > 65535:
		return None
	scheme = str(route.get(ROUTE_SCHEME) or "http").lower()
	if scheme not in LAN_ROUTE_SCHEMES:
		return None
	# IPv6 literals without brackets: wrap for the URL authority.
	authority = host
	if ":" in host and not host.startswith("["):
		authority = f"[{host}]"
	return f"{scheme}://{authority}:{port}"


def route_allows_websocket(route: dict) -> bool:
	"""WebSocket bridging is on by default (many LAN UIs need it)."""
	return route.get(ROUTE_WEBSOCKET) is not False


def validate_route(raw: Any, *, existing_slugs: Optional[set[str]] = None) -> tuple[Optional[dict], Optional[str]]:
	"""Validate and normalise one route; return ``(route, error_key)``."""
	if not isinstance(raw, dict):
		return None, "lan_route_invalid"
	slug = normalise_slug(raw.get(ROUTE_SLUG))
	if not slug or not LAN_SLUG_RE.match(slug):
		return None, "lan_slug_invalid"
	if existing_slugs is not None and slug in existing_slugs:
		return None, "lan_slug_duplicate"
	host = str(raw.get(ROUTE_HOST) or "").strip()
	if not host or not LAN_HOST_RE.match(host) or "/" in host or "://" in host:
		return None, "lan_host_invalid"
	try:
		port = int(raw.get(ROUTE_PORT) or 0)
	except (TypeError, ValueError):
		return None, "lan_port_invalid"
	if port < 1 or port > 65535:
		return None, "lan_port_invalid"
	scheme = str(raw.get(ROUTE_SCHEME) or "http").lower()
	if scheme not in LAN_ROUTE_SCHEMES:
		return None, "lan_scheme_invalid"
	name = str(raw.get(ROUTE_NAME) or slug).strip()[:80] or slug
	route = {
		ROUTE_SLUG: slug,
		ROUTE_NAME: name,
		ROUTE_HOST: host,
		ROUTE_PORT: port,
		ROUTE_SCHEME: scheme,
		ROUTE_ENABLED: bool(raw.get(ROUTE_ENABLED, True)),
		ROUTE_WEBSOCKET: bool(raw.get(ROUTE_WEBSOCKET, True)),
	}
	if route_base_url(route) is None:
		return None, "lan_route_invalid"
	return route, None


def normalise_routes(raw: Any) -> list[dict]:
	"""Return a cleaned list of valid routes (drops invalid entries)."""
	if not isinstance(raw, list):
		return []
	out: list[dict] = []
	seen: set[str] = set()
	for item in raw:
		route, err = validate_route(item, existing_slugs=seen)
		if err or route is None:
			continue
		seen.add(route[ROUTE_SLUG])
		out.append(route)
		if len(out) >= LAN_MAX_ROUTES:
			break
	return out


def rewrite_location(value: str, *, slug: str, route: dict) -> str:
	"""Rewrite an upstream ``Location`` so the browser stays under ``/t/<slug>/``."""
	prefix = f"{LAN_PATH_PREFIX}{slug}"
	raw = str(value or "").strip()
	if not raw:
		return raw
	# Absolute path on the LAN device → prefix it.
	if raw.startswith("/") and not raw.startswith("//"):
		return prefix + raw
	parsed = urlparse(raw)
	if not parsed.scheme:
		# Relative URL — browser resolves against the current /t/slug/… URL.
		return raw
	base = route_base_url(route)
	if not base:
		return raw
	base_p = urlparse(base)
	# Same host:port as the LAN target → map path under the tunnel prefix.
	same_host = parsed.hostname and base_p.hostname and (
		parsed.hostname.lower() == base_p.hostname.lower()
	)
	same_port = (parsed.port or _default_port(parsed.scheme)) == (
		base_p.port or _default_port(base_p.scheme)
	)
	if same_host and same_port:
		path = parsed.path or "/"
		return urlunparse(("", "", prefix + path, "", parsed.query, parsed.fragment))
	return raw


def rewrite_set_cookie_path(value: str, *, slug: str) -> str:
	"""If Set-Cookie has ``Path=/…``, nest it under ``/t/<slug>``."""
	prefix = f"{LAN_PATH_PREFIX}{slug}"
	parts = [p.strip() for p in str(value or "").split(";")]
	if not parts:
		return value
	out = [parts[0]]
	for attr in parts[1:]:
		lower = attr.lower()
		if lower.startswith("path="):
			path = attr.split("=", 1)[1].strip()
			if not path.startswith(prefix):
				if path == "/" or not path:
					path = prefix + "/"
				elif path.startswith("/"):
					path = prefix + path
			out.append(f"Path={path}")
		else:
			out.append(attr)
	return "; ".join(out)


def rewrite_response_headers(
	pairs: Any, *, slug: str, route: dict
) -> list[list[str]]:
	"""Apply Location + Set-Cookie Path rewrites to response header pairs."""
	out: list[list[str]] = []
	for item in pairs or []:
		if not isinstance(item, (list, tuple)) or len(item) < 2:
			continue
		name, value = str(item[0]), str(item[1])
		lower = name.lower()
		if lower == "location":
			value = rewrite_location(value, slug=slug, route=route)
		elif lower == "set-cookie":
			value = rewrite_set_cookie_path(value, slug=slug)
		out.append([name, value])
	return out


def _default_port(scheme: Optional[str]) -> int:
	return 443 if (scheme or "").lower() == "https" else 80
