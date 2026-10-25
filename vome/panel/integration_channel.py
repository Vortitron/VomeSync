"""Where the Vome integration hands over the YAML it may not write itself.

A Home Assistant integration must not write or patch the owner's YAML
configuration; an app with ``/config`` mapped may, as the File editor does.
So when the relay's file API or the chat changes a YAML file or an
automation, the integration asks this app to do it (its side is
``custom_components/vomesync/app_channel.py``).

The app listens for that on its own port on Home Assistant's internal
network, separate from the ingress panel, and checks in with the integration
every couple of minutes through ``vomesync.app_check_in`` (admin-only; this
app calls as the Supervisor).  The check-in's answer carries the secret every
request from the integration must present.  Nothing here listens until a
check-in has succeeded, and a request without that secret is refused.

Files are changed with the integration's own ``file_ops`` rules, loaded from
the copy bundled in this image, and only YAML is accepted.  Automations are
saved and deleted through Home Assistant's own automation editor API, so Home
Assistant writes automations.yaml exactly as its editor would.
"""
from __future__ import annotations

import hmac
import importlib.util
import json
import logging
import os
import re
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import ModuleType
from typing import Any, Callable, Optional

LOG = logging.getLogger("vome-panel")

CHANNEL_PORT = int(os.environ.get("VOME_CHANNEL_PORT", "8098"))
INTEGRATION_DIR = Path(
	os.environ.get("VOME_INTEGRATION_DIR", "/usr/share/vome/custom_components/vomesync")
)
CHECK_IN_SECONDS = 120
RETRY_SECONDS = 20
MAX_BODY_BYTES = 4 * 1024 * 1024
AUTOMATION_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")

_secret = {"value": ""}
_file_ops: dict[str, ModuleType] = {}


def file_ops() -> ModuleType:
	"""The integration's file_ops module, loaded on its own (no Core needed)."""
	if "module" not in _file_ops:
		spec = importlib.util.spec_from_file_location(
			"vome_file_ops", INTEGRATION_DIR / "file_ops.py",
		)
		if spec is None or spec.loader is None:
			raise ImportError(f"file_ops.py is missing from {INTEGRATION_DIR}")
		module = importlib.util.module_from_spec(spec)
		spec.loader.exec_module(module)
		_file_ops["module"] = module
	return _file_ops["module"]


def channel_url() -> str:
	"""This app's address as Home Assistant reaches it (its hostname)."""
	host = os.environ.get("VOME_APP_HOST") or socket.gethostname()
	return f"http://{host.lower()}:{CHANNEL_PORT}"


def check_in(call_service: Callable, unwrap: Callable) -> bool:
	"""Tell the integration where this app listens; keep the secret it answers."""
	status, payload = call_service("app_check_in", {"url": channel_url()})
	body = unwrap(payload)
	secret = body.get("secret") if isinstance(body, dict) else None
	if status != 200 or not isinstance(secret, str) or len(secret) < 32:
		return False
	_secret["value"] = secret
	return True


def authorised(header: Optional[str]) -> bool:
	secret = _secret["value"]
	if not secret or not header:
		return False
	return hmac.compare_digest(header.encode(), f"Bearer {secret}".encode())


def handle_files(payload: dict, config_root: str) -> tuple[int, dict]:
	"""One YAML ``write``, ``delete`` or ``move`` under Home Assistant's config."""
	if not config_root:
		return 503, {"error": "The Vome app cannot see Home Assistant's config folder."}
	ops = file_ops()
	status, body, error = ops.apply(
		Path(config_root).resolve(), str(payload.get("op") or ""), payload.get("path"),
		to=payload.get("to"), overwrite=bool(payload.get("overwrite")),
		body=payload.get("body"), yaml_only=True,
	)
	if status != 200 or body is None:
		return 400, {"error": error or "The file was not changed."}
	return 200, {"result": json.loads(body)}


def handle_automation(payload: dict, ha_request: Callable) -> tuple[int, dict]:
	"""Save or delete one automation through Home Assistant's editor API."""
	key = str(payload.get("id") or "")
	if not AUTOMATION_ID_RE.match(key):
		return 400, {"error": "That is not an automation id."}
	op = payload.get("op")
	if op == "save":
		config = payload.get("config")
		if not isinstance(config, dict):
			return 400, {"error": "An automation to save is a mapping."}
		status, body = ha_request("POST", f"/config/automation/config/{key}", config)
	elif op == "delete":
		status, body = ha_request("DELETE", f"/config/automation/config/{key}")
	else:
		return 400, {"error": f"Unknown automation operation: {op}"}
	if status == 200:
		return 200, {"result": "saved" if op == "save" else "deleted"}
	detail = body.get("message") or body.get("error") if isinstance(body, dict) else body
	return 400, {"error": f"Home Assistant refused it (HTTP {status}): {detail}"}


def make_handler(config_root: Callable[[], str], ha_request: Callable) -> type:
	class ChannelHandler(BaseHTTPRequestHandler):
		server_version = "VomeAppChannel/1"

		def log_message(self, fmt: str, *args) -> None:
			LOG.debug("channel %s - " + fmt, self.address_string(), *args)

		def _send_json(self, status: int, payload: Any) -> None:
			raw = json.dumps(payload).encode("utf-8")
			self.send_response(status)
			self.send_header("Content-Type", "application/json; charset=utf-8")
			self.send_header("Content-Length", str(len(raw)))
			self.end_headers()
			self.wfile.write(raw)

		def do_GET(self) -> None:  # noqa: N802
			self._send_json(405, {"error": "POST only."})

		def do_POST(self) -> None:  # noqa: N802
			if not authorised(self.headers.get("Authorization")):
				LOG.warning("channel: refused a request from %s without the secret",
				            (self.client_address or ("?",))[0])
				self._send_json(401, {"error": "Not the Vome integration."})
				return
			length = int(self.headers.get("Content-Length") or 0)
			if length <= 0 or length > MAX_BODY_BYTES:
				self._send_json(400, {"error": "Send one JSON body."})
				return
			try:
				payload = json.loads(self.rfile.read(length).decode("utf-8"))
			except (ValueError, UnicodeDecodeError):
				payload = None
			if not isinstance(payload, dict):
				self._send_json(400, {"error": "The body must be a JSON object."})
				return
			try:
				if self.path == "/files":
					self._send_json(*handle_files(payload, config_root()))
				elif self.path == "/automation":
					self._send_json(*handle_automation(payload, ha_request))
				else:
					self._send_json(404, {"error": "not found"})
			except Exception as err:  # noqa: BLE001 - always answer in JSON
				LOG.exception("channel %s failed", self.path)
				self._send_json(500, {"error": f"The Vome app failed: {err}"})

	return ChannelHandler


def _check_in_forever(call_service: Callable, unwrap: Callable, stop: threading.Event) -> None:
	connected = None
	while not stop.is_set():
		try:
			ok = check_in(call_service, unwrap)
		except Exception as err:  # noqa: BLE001 - keep trying; Core may be restarting
			LOG.debug("channel check-in failed: %s", err)
			ok = False
		if ok != connected:
			LOG.info("Vome integration %s", "reached" if ok else "not reachable yet; retrying")
			connected = ok
		stop.wait(CHECK_IN_SECONDS if ok else RETRY_SECONDS)


def start(
	*, call_service: Callable, unwrap: Callable, ha_request: Callable,
	config_root: Callable[[], str],
) -> threading.Event:
	"""Listen for the integration and keep checking in; returns a stop event."""
	server = ThreadingHTTPServer(("0.0.0.0", CHANNEL_PORT), make_handler(config_root, ha_request))
	threading.Thread(target=server.serve_forever, name="vome-channel", daemon=True).start()
	stop = threading.Event()
	threading.Thread(
		target=_check_in_forever, args=(call_service, unwrap, stop),
		name="vome-check-in", daemon=True,
	).start()
	LOG.info("Listening for the Vome integration on :%s", CHANNEL_PORT)
	return stop
