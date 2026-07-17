#!/usr/bin/env python3
"""Vome add-on control panel — tree UI over the shared integration services.

Talks to Home Assistant Core through the Supervisor proxy
(``http://supervisor/core/api`` + ``SUPERVISOR_TOKEN``).  Serves a static
tree-view UI on the ingress port so owners get a clearer layout than the
HACS options menu, while still mutating the same ``options.relay`` data.
"""
from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

PORT = int(os.environ.get("VOME_PANEL_PORT", "8099"))
STATIC_DIR = Path(os.environ.get("VOME_PANEL_STATIC", "/usr/share/vome/panel/static"))
SUPERVISOR_TOKEN = os.environ.get("SUPERVISOR_TOKEN", "")
CORE_API = os.environ.get("VOME_CORE_API", "http://supervisor/core/api")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
LOG = logging.getLogger("vome-panel")


def _ha_request(method: str, path: str, body: Optional[dict] = None) -> tuple[int, Any]:
	"""Call Home Assistant Core via the Supervisor. Returns (status, json_or_text)."""
	if not SUPERVISOR_TOKEN:
		return 503, {"error": "SUPERVISOR_TOKEN missing — is this running as an add-on?"}
	url = CORE_API.rstrip("/") + path
	data = None
	headers = {
		"Authorization": f"Bearer {SUPERVISOR_TOKEN}",
		"Content-Type": "application/json",
	}
	if body is not None:
		data = json.dumps(body).encode("utf-8")
	req = urllib.request.Request(url, data=data, headers=headers, method=method)
	try:
		with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 - fixed supervisor URL
			raw = resp.read()
			status = resp.status
	except urllib.error.HTTPError as err:
		raw = err.read()
		status = err.code
	except urllib.error.URLError as err:
		return 502, {"error": f"Home Assistant unreachable: {err}"}
	if not raw:
		return status, {}
	try:
		return status, json.loads(raw.decode("utf-8"))
	except (ValueError, UnicodeDecodeError):
		return status, {"raw": raw.decode("utf-8", errors="replace")}


def call_service(service: str, data: Optional[dict] = None) -> tuple[int, Any]:
	"""Invoke a vomesync service with ``return_response``."""
	path = f"/services/vomesync/{service}?return_response"
	return _ha_request("POST", path, data or {})


class PanelHandler(BaseHTTPRequestHandler):
	server_version = "VomePanel/0.2"

	def log_message(self, fmt: str, *args) -> None:
		LOG.info("%s - " + fmt, self.address_string(), *args)

	def _send(self, status: int, body: bytes, content_type: str) -> None:
		self.send_response(status)
		self.send_header("Content-Type", content_type)
		self.send_header("Content-Length", str(len(body)))
		self.send_header("Cache-Control", "no-store")
		self.end_headers()
		self.wfile.write(body)

	def _send_json(self, status: int, payload: Any) -> None:
		raw = json.dumps(payload).encode("utf-8")
		self._send(status, raw, "application/json; charset=utf-8")

	def _read_json(self) -> dict:
		length = int(self.headers.get("Content-Length") or 0)
		if length <= 0:
			return {}
		raw = self.rfile.read(length)
		try:
			data = json.loads(raw.decode("utf-8"))
		except (ValueError, UnicodeDecodeError):
			return {}
		return data if isinstance(data, dict) else {}

	def do_GET(self) -> None:  # noqa: N802
		parsed = urlparse(self.path)
		path = parsed.path or "/"
		if path in ("/", "/index.html"):
			self._serve_static("index.html", "text/html; charset=utf-8")
			return
		if path.startswith("/static/"):
			name = path[len("/static/"):]
			ctype = "application/javascript" if name.endswith(".js") else (
				"text/css" if name.endswith(".css") else "application/octet-stream"
			)
			self._serve_static(name, ctype)
			return
		if path == "/api/status":
			status, payload = call_service("get_remote_status", {})
			# HA wraps service responses as {"service_response": {...}} on modern cores.
			if isinstance(payload, dict) and "service_response" in payload:
				payload = payload["service_response"]
			self._send_json(status if status < 500 else status, payload)
			return
		self._send_json(404, {"error": "not found"})

	def do_POST(self) -> None:  # noqa: N802
		parsed = urlparse(self.path)
		path = parsed.path or "/"
		body = self._read_json()
		mapping = {
			"/api/forward_ui": ("set_forward_ui", body),
			"/api/lan_routes": ("set_lan_routes", body),
			"/api/lan_routes/add": ("add_lan_route", body),
			"/api/lan_routes/remove": ("remove_lan_route", body),
			"/api/lan_routes/token": ("mint_lan_tcp_token", body),
		}
		if path not in mapping:
			self._send_json(404, {"error": "not found"})
			return
		service, data = mapping[path]
		status, payload = call_service(service, data)
		if isinstance(payload, dict) and "service_response" in payload:
			payload = payload["service_response"]
		self._send_json(status, payload)

	def _serve_static(self, name: str, content_type: str) -> None:
		# Prevent path escape.
		safe = Path(name).name
		target = STATIC_DIR / safe
		if not target.is_file():
			self._send_json(404, {"error": f"missing static file: {safe}"})
			return
		self._send(200, target.read_bytes(), content_type)


def main() -> None:
	STATIC_DIR.mkdir(parents=True, exist_ok=True)
	server = ThreadingHTTPServer(("0.0.0.0", PORT), PanelHandler)
	LOG.info("Vome panel listening on :%s (static=%s)", PORT, STATIC_DIR)
	server.serve_forever()


if __name__ == "__main__":
	main()
