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
	except Exception as err:  # noqa: BLE001 - never let a transport error 500 the panel
		return 502, {"error": f"Panel could not reach Home Assistant: {err}"}
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


def _manifest_version(path: str) -> str:
	"""Read a vomesync manifest.json version, '' if unreadable."""
	try:
		with open(path, encoding="utf-8") as fh:
			return str(json.load(fh).get("version", ""))
	except (OSError, ValueError):
		return ""


def _config_root() -> str:
	"""HA config mount inside this container ('' if not mounted).

	``homeassistant_config`` mounts at /homeassistant; the legacy ``config``
	mapping mounts at /config.  If neither exists the add-on cannot install
	the integration at all — the diagnostics surface that loudly.
	"""
	for root in ("/homeassistant", "/config"):
		if os.path.isdir(root):
			return root
	return ""


def installed_versions() -> dict:
	"""Versions the panel can see on disk.

	``installed_version`` is what sits in HA config (what Core WILL run after
	a restart); the integration reports what it IS running via
	``integration_version`` in get_remote_status.  A mismatch means "restart
	Home Assistant to finish the update" — the UI turns that into a banner.
	"""
	root = _config_root()
	return {
		"installed_version": _manifest_version(
			f"{root}/custom_components/vomesync/manifest.json"
		) if root else "",
		"bundled_version": _manifest_version(
			"/usr/share/vome/custom_components/vomesync/manifest.json"
		),
	}


def run_diagnostics() -> dict:
	"""Self-diagnosis for the panel's About view / error state.

	Answers, in order of likelihood: is HA config even mounted, did the
	integration copy land, what can Core see, and which vomesync services
	are actually registered (distinguishes "integration not loaded" from
	"old integration loaded" from "schema rejected the data").
	"""
	root = _config_root()
	diag: dict = {
		"config_mounted": bool(root),
		"config_root": root or "none — the app cannot reach Home Assistant's config",
		"supervisor_token": bool(SUPERVISOR_TOKEN),
	}
	diag.update(installed_versions())
	if root:
		diag["integration_on_disk"] = os.path.isfile(
			f"{root}/custom_components/vomesync/manifest.json"
		)
		diag["addon_marker"] = os.path.isfile(f"{root}/vome/addon.marker")
	status, cfg = _ha_request("GET", "/config")
	diag["core_api"] = "ok" if status == 200 else f"unreachable (HTTP {status})"
	diag["ha_version"] = cfg.get("version", "") if isinstance(cfg, dict) else ""
	status, services = _ha_request("GET", "/services")
	names: list = []
	if status == 200 and isinstance(services, list):
		for domain in services:
			if isinstance(domain, dict) and domain.get("domain") == "vomesync":
				svc = domain.get("services") or {}
				names = sorted(svc) if isinstance(svc, dict) else list(svc)
	diag["vomesync_services"] = names
	# The version the integration ACTUALLY running reports (vs what's on disk).
	# Old code predates this field and reports nothing — which is itself the
	# tell that Home Assistant is still running a stale copy and needs a restart.
	st_status, st_payload = call_service("get_remote_status", {})
	st_body = _unwrap(st_payload)
	diag["running_version"] = (
		st_body.get("integration_version", "") if isinstance(st_body, dict) else ""
	)
	# Live probe of the actual failing call path, so the verdict reflects what
	# Home Assistant does right now rather than a guess. A bad slug is used so
	# nothing is created even on the (mis-)chance it validates. Capture the raw
	# body too: a bare "400: Bad Request" means HA rejected it pre-handler
	# (schema/return_response), whereas a JSON {"error": ...} means our handler
	# ran and rejected the bad input — i.e. real writes work.
	probe_status, probe_raw = call_service(
		"add_lan_route",
		{"slug": "", "host": "", "port": 3389, "scheme": "tcp"},
	)
	probe_body = _unwrap(probe_raw)
	diag["write_probe_status"] = probe_status
	if isinstance(probe_body, dict):
		diag["write_probe_body"] = (
			probe_body.get("error") or probe_body.get("message") or "(ok / no error)"
		)
	else:
		diag["write_probe_body"] = str(probe_body)[:200]
	return diag


def _unwrap(payload: Any) -> Any:
	"""Normalise a Core response into the flat dict the panel UI expects.

	Modern cores wrap service results as ``{"service_response": {...}}``; unwrap
	that.  When Core returns an error instead (``{"message": ...}``) or a
	non-JSON body (``{"raw": ...}``), surface it as ``{"error": ...}`` so the UI
	shows the actual reason rather than a generic "invalid response".
	"""
	if not isinstance(payload, dict):
		return payload
	if "service_response" in payload:
		return payload["service_response"]
	if "error" in payload:
		return payload
	if "message" in payload:
		return {"error": payload["message"]}
	if "raw" in payload:
		text = str(payload["raw"]).strip()
		first = text.splitlines()[0] if text else "empty response from Home Assistant"
		return {"error": first}
	return payload


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

	def _read_body(self) -> bytes:
		"""Read the request body, honouring chunked transfer-encoding.

		Home Assistant's ingress proxy forwards POST bodies with
		``Transfer-Encoding: chunked`` and no ``Content-Length``. A naive
		Content-Length read then sees length 0 and drops the whole payload —
		which silently emptied every write (add route, forward UI, …) and
		surfaced as an opaque "400: Bad Request" from Core's schema check.
		Parse the chunks when that header is present.
		"""
		te = (self.headers.get("Transfer-Encoding") or "").lower()
		if "chunked" in te:
			chunks = []
			while True:
				size_line = self.rfile.readline()
				if not size_line:
					break
				try:
					size = int(size_line.split(b";", 1)[0].strip(), 16)
				except ValueError:
					break
				if size == 0:
					self.rfile.readline()  # consume trailing CRLF
					break
				chunks.append(self.rfile.read(size))
				self.rfile.readline()  # consume CRLF after chunk data
			return b"".join(chunks)
		length = int(self.headers.get("Content-Length") or 0)
		return self.rfile.read(length) if length > 0 else b""

	def _read_json(self) -> dict:
		raw = self._read_body()
		if not raw:
			return {}
		try:
			data = json.loads(raw.decode("utf-8"))
		except (ValueError, UnicodeDecodeError):
			return {}
		return data if isinstance(data, dict) else {}

	def do_GET(self) -> None:  # noqa: N802
		try:
			self._route_get()
		except Exception as err:  # noqa: BLE001 - a handler crash must still return JSON
			LOG.exception("panel GET %s failed", self.path)
			self._send_json(500, {"error": f"Panel error: {err}"})

	def do_POST(self) -> None:  # noqa: N802
		try:
			self._route_post()
		except Exception as err:  # noqa: BLE001 - a handler crash must still return JSON
			LOG.exception("panel POST %s failed", self.path)
			self._send_json(500, {"error": f"Panel error: {err}"})

	def _route_get(self) -> None:
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
			body = _unwrap(payload)
			if isinstance(body, dict):
				body = {**body, **installed_versions()}
				# A service that returned {"error": ...} succeeded at the HTTP
				# layer (200) but failed logically — flag it so the panel shows
				# the message instead of silently rendering an error object.
				if body.get("error") and status < 400:
					status = 400
			self._send_json(status, body)
			return
		if path == "/api/diag":
			self._send_json(200, run_diagnostics())
			return
		self._send_json(404, {"error": "not found"})

	def _route_post(self) -> None:
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
		body = _unwrap(payload)
		if isinstance(body, dict) and body.get("error") and status < 400:
			status = 400
		self._send_json(status, body)

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
