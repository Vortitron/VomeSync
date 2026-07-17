# flake8: noqa
"""Tests for LAN path routing helpers and relay LAN HTTP proxying."""
import base64
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest

from custom_components.vomesync.lan_routes import (
	LAN_PATH_PREFIX,
	find_route,
	normalise_routes,
	parse_lan_path,
	rewrite_location,
	rewrite_response_headers,
	rewrite_set_cookie_path,
	route_base_url,
	validate_route,
)
from custom_components.vomesync.relay_client import RelayClient


def _mock_session_with_response(status=200, body=b"ok", headers=None):
	mock_response = AsyncMock()
	mock_response.status = status
	mock_response.read = AsyncMock(return_value=body)
	mock_response.headers = headers or {"Content-Type": "text/plain"}
	session = AsyncMock(spec=aiohttp.ClientSession)
	session.request.return_value.__aenter__.return_value = mock_response
	return session, mock_response


def _client(session, **kwargs):
	return RelayClient(None, server_id="rly-1", secret="sek", session=session, **kwargs)


class TestParseLanPath:
	def test_splits_slug_and_remainder(self):
		assert parse_lan_path("/t/nas/") == ("nas", "/")
		assert parse_lan_path("/t/nas/ui/login?x=1") == ("nas", "/ui/login?x=1")
		assert parse_lan_path("/t/rdp") == ("rdp", "/")

	def test_rejects_non_lan_or_bad_slug(self):
		assert parse_lan_path("/lovelace") is None
		assert parse_lan_path("/t/") is None
		assert parse_lan_path("/t/-bad-/") is None
		assert parse_lan_path("/t/../etc/") is None
		assert parse_lan_path("/t/nas/../secret") is None
		assert parse_lan_path(None) is None

	def test_normalises_underscore_slug(self):
		# Underscores become hyphens so "my_nas" matches route slug "my-nas".
		assert parse_lan_path("/t/my_nas/app") == ("my-nas", "/app")


class TestValidateRoute:
	def test_accepts_valid(self):
		route, err = validate_route({
			"slug": "nas", "host": "192.168.1.5", "port": 5000,
		})
		assert err is None
		assert route["slug"] == "nas"
		assert route_base_url(route) == "http://192.168.1.5:5000"

	def test_rejects_bad_host_and_duplicate(self):
		assert validate_route({"slug": "x", "host": "http://evil", "port": 80})[1] == "lan_host_invalid"
		assert validate_route({"slug": "nas", "host": "1.2.3.4", "port": 80}, existing_slugs={"nas"})[1] == "lan_slug_duplicate"

	def test_normalise_routes_drops_invalid(self):
		out = normalise_routes([
			{"slug": "ok", "host": "192.168.0.1", "port": 80},
			{"slug": "bad", "host": "http://x", "port": 80},
			{"slug": "ok", "host": "192.168.0.2", "port": 81},  # duplicate slug dropped
		])
		assert len(out) == 1 and out[0]["host"] == "192.168.0.1"

	def test_accepts_tcp_scheme(self):
		route, err = validate_route({
			"slug": "rdp", "host": "192.168.1.10", "port": 3389, "scheme": "tcp",
		})
		assert err is None
		assert route["scheme"] == "tcp"
		assert route_base_url(route) == "tcp://192.168.1.10:3389"

	def test_rejects_unknown_scheme(self):
		assert validate_route({
			"slug": "x", "host": "1.2.3.4", "port": 80, "scheme": "ftp",
		})[1] == "lan_scheme_invalid"


class TestHeaderRewrite:
	def test_location_absolute_path(self):
		route = {"slug": "nas", "host": "192.168.1.5", "port": 5000, "scheme": "http"}
		assert rewrite_location("/login", slug="nas", route=route) == "/t/nas/login"

	def test_location_absolute_url_same_host(self):
		route = {"slug": "nas", "host": "192.168.1.5", "port": 5000, "scheme": "http"}
		assert rewrite_location(
			"http://192.168.1.5:5000/dash", slug="nas", route=route
		) == "/t/nas/dash"

	def test_location_external_unchanged(self):
		route = {"slug": "nas", "host": "192.168.1.5", "port": 5000, "scheme": "http"}
		assert rewrite_location("https://example.com/x", slug="nas", route=route) == "https://example.com/x"

	def test_set_cookie_path(self):
		assert "Path=/t/nas/" in rewrite_set_cookie_path("sid=1; Path=/", slug="nas")
		assert "Path=/t/nas/app" in rewrite_set_cookie_path("sid=1; Path=/app", slug="nas")

	def test_rewrite_response_headers(self):
		route = {"slug": "nas", "host": "10.0.0.2", "port": 80, "scheme": "http"}
		out = rewrite_response_headers(
			[["Location", "/a"], ["Set-Cookie", "a=b; Path=/"], ["X-Ok", "1"]],
			slug="nas", route=route,
		)
		assert out[0] == ["Location", "/t/nas/a"]
		assert "Path=/t/nas/" in out[1][1]
		assert out[2] == ["X-Ok", "1"]


class TestFindRoute:
	def test_enabled_and_disabled(self):
		routes = [
			{"slug": "a", "host": "1.1.1.1", "port": 80, "scheme": "http", "enabled": True},
			{"slug": "b", "host": "1.1.1.2", "port": 80, "scheme": "http", "enabled": False},
		]
		assert find_route(routes, "a")["host"] == "1.1.1.1"
		assert find_route(routes, "b") is None
		assert find_route(routes, "missing") is None


class TestLanHttpProxy:
	@pytest.mark.asyncio
	async def test_lan_path_proxies_without_forward_ui(self):
		session, resp = _mock_session_with_response(
			status=200, body=b"nas-ok", headers={"Content-Type": "text/html", "Location": "/home"}
		)
		# CIMultiDict-like .items()
		resp.headers = MagicMock()
		resp.headers.items.return_value = [("Content-Type", "text/html"), ("Location", "/home")]
		client = _client(session, lan_routes=[{
			"slug": "nas", "host": "192.168.1.5", "port": 5000, "scheme": "http", "enabled": True,
		}])
		status, headers, body_b64, error = await client._execute_http_proxy({
			"method": "GET", "path": "/t/nas/ui",
		})
		assert error is None and status == 200
		assert base64.b64decode(body_b64) == b"nas-ok"
		assert ["Location", "/t/nas/home"] in headers
		called_url = session.request.call_args[0][1]
		assert called_url == "http://192.168.1.5:5000/ui"

	@pytest.mark.asyncio
	async def test_unknown_lan_slug_refused(self):
		session, _ = _mock_session_with_response()
		client = _client(session, lan_routes=[])
		status, _h, _b, error = await client._execute_http_proxy({"path": "/t/ghost/"})
		assert status == 0 and "No LAN route" in error
		session.request.assert_not_called()

	@pytest.mark.asyncio
	async def test_ha_path_still_needs_forward_ui(self):
		session, _ = _mock_session_with_response()
		client = _client(session, lan_routes=[{
			"slug": "nas", "host": "192.168.1.5", "port": 80, "enabled": True,
		}])
		status, _h, _b, error = await client._execute_http_proxy({"path": "/lovelace"})
		assert status == 0 and "Full-UI forwarding is disabled" in error

	@pytest.mark.asyncio
	async def test_prefix_constant(self):
		assert LAN_PATH_PREFIX == "/t/"
