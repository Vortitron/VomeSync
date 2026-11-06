"""Vome on Home Assistant's "Model Context Protocol" page.

Home Assistant's ``mcp`` integration lists the MCP servers that apps announce
to the Supervisor, with an Add button, and gives an added server's tools to
the owner's Assist agents.  ESPHome Device Builder appeared there on its own
in October 2026; an owner who sees an MCP card should see a Vome one too.  So
this app announces itself (``discovery: [mcp]`` in config.yaml) and answers
MCP here, on its own port on Home Assistant's internal network.

**What it serves.**  Vome's own tools from the integration's chat tool list
(``group == "vome"``: is this home reachable, its health score, CHAP, ...),
not the entity tools: Assist already has its own, and the full set is the
"Vome" LLM API, which knows who is asking.  An MCP call does not say who is
asking, so every call here is made as nobody in particular
(``anonymous: true``), which the integration cuts to the read tools that a
household member may use.  Nothing here can change anything.

**Who may call.**  The URL carries a secret (kept in ``/data`` so the entry
the owner added keeps working after a restart).  Only Home Assistant Core
lists the Supervisor's discoveries, so other apps on the network cannot learn
it, and a request without it is refused.

**The protocol.**  Home Assistant's client opens a short Streamable HTTP
session for each refresh and each call: ``initialize``, then ``tools/list``
or ``tools/call``.  JSON replies are allowed by the transport, so there are no
sessions or event streams here; a GET is 405, which the transport expects.
Home Assistant gives a call 10 seconds.
"""
from __future__ import annotations

import hmac
import json
import logging
import os
import secrets
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Optional

LOG = logging.getLogger("vome-panel")

MCP_PORT = int(os.environ.get("VOME_MCP_PORT", "8097"))
STATE_FILE = Path(os.environ.get("VOME_MCP_STATE", "/data/mcp.json"))
SUPERVISOR_URL = os.environ.get("VOME_SUPERVISOR_URL", "http://supervisor")
SERVER_NAME = "Vome"
PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
MAX_BODY_BYTES = 256 * 1024
ANNOUNCE_RETRY_SECONDS = 60
INSTRUCTIONS = (
	"Vome links this Home Assistant to Vome: remote access without port "
	"forwarding, a health score, CHAP standby protection and shared switches. "
	"These tools say how that stands for this home. They only look; the owner "
	"changes Vome settings in the Vome panel."
)

# JSON-RPC error codes
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602


def load_secret(path: Path = STATE_FILE) -> str:
	"""The URL secret, made once and kept."""
	try:
		value = json.loads(path.read_text(encoding="utf-8")).get("secret")
		if isinstance(value, str) and len(value) >= 32:
			return value
	except (OSError, ValueError, AttributeError):
		pass
	value = secrets.token_urlsafe(32)
	try:
		path.parent.mkdir(parents=True, exist_ok=True)
		path.write_text(json.dumps({"secret": value}), encoding="utf-8")
		os.chmod(path, 0o600)
	except OSError as err:
		# Still serve; the owner re-adds the entry after a restart.
		LOG.warning("MCP: could not keep the URL secret in %s: %s", path, err)
	return value


def server_url(secret: str) -> str:
	"""This app's MCP address as Home Assistant reaches it."""
	host = os.environ.get("VOME_APP_HOST") or socket.gethostname()
	return f"http://{host.lower()}:{MCP_PORT}/mcp/{secret}"


def _version() -> str:
	try:
		with open("/usr/share/vome/config.yaml", encoding="utf-8") as fh:
			for line in fh:
				if line.startswith("version:"):
					return line.split(":", 1)[1].strip().strip('"')
	except OSError:
		pass
	return "0"


class Tools:
	"""Vome's tools, as the integration lists and runs them."""

	def __init__(self, call_service: Callable, unwrap: Callable) -> None:
		self._call_service = call_service
		self._unwrap = unwrap

	def list(self) -> list[dict]:
		status, payload = self._call_service("chat_tools", {"anonymous": True})
		body = self._unwrap(payload)
		if status != 200 or not isinstance(body, dict) or body.get("error"):
			# An empty list, not an error: Home Assistant would otherwise
			# refuse to add the entry while the integration is still loading.
			LOG.info("MCP: no tools yet (%s)", body.get("error") if isinstance(body, dict) else status)
			return []
		return [
			{
				"name": tool["name"],
				"title": tool.get("title") or tool["name"].replace("_", " ").capitalize(),
				"description": tool.get("description") or "",
				"inputSchema": tool.get("parameters") or {"type": "object", "properties": {}},
				"annotations": {"readOnlyHint": True, "destructiveHint": False,
				                "idempotentHint": True, "openWorldHint": False},
			}
			for tool in body.get("tools") or []
			if isinstance(tool, dict) and tool.get("group") == "vome" and tool.get("name")
		]

	def call(self, name: str, arguments: dict) -> dict:
		if name not in {tool["name"] for tool in self.list()}:
			return _text_result(f"There is no Vome tool called {name!r} here.", error=True)
		status, payload = self._call_service(
			"chat_tool_call", {"name": name, "arguments": arguments, "anonymous": True})
		body = self._unwrap(payload)
		if not isinstance(body, dict):
			return _text_result(f"Home Assistant answered HTTP {status}.", error=True)
		if body.get("ok") is False or ("error" in body and "result" not in body):
			return _text_result(str(body.get("error") or "The tool failed."), error=True)
		LOG.info("MCP: %s answered", name)
		return _text_result(json.dumps(body.get("result"), ensure_ascii=False, default=str))


def _text_result(text: str, error: bool = False) -> dict:
	return {"content": [{"type": "text", "text": text}], "isError": error}


def _reply(message_id: Any, result: Any = None, error: Optional[tuple] = None) -> dict:
	if error is not None:
		return {"jsonrpc": "2.0", "id": message_id, "error": {"code": error[0], "message": error[1]}}
	return {"jsonrpc": "2.0", "id": message_id, "result": result}


def handle_message(message: Any, tools: Tools) -> Optional[dict]:
	"""One JSON-RPC message in, its reply out (None for a notification)."""
	if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
		return _reply(None, error=(INVALID_REQUEST, "Not a JSON-RPC 2.0 message."))
	method = message.get("method")
	if "id" not in message:
		return None  # notifications/initialized and friends need no answer
	message_id = message["id"]
	params = message.get("params") if isinstance(message.get("params"), dict) else {}
	if method == "initialize":
		asked = params.get("protocolVersion")
		return _reply(message_id, {
			"protocolVersion": asked if asked in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0],
			"capabilities": {"tools": {"listChanged": False}},
			"serverInfo": {"name": SERVER_NAME, "version": _version()},
			"instructions": INSTRUCTIONS,
		})
	if method == "ping":
		return _reply(message_id, {})
	if method == "tools/list":
		return _reply(message_id, {"tools": tools.list()})
	if method == "tools/call":
		name = params.get("name")
		arguments = params.get("arguments") or {}
		if not isinstance(name, str) or not isinstance(arguments, dict):
			return _reply(message_id, error=(INVALID_PARAMS, "tools/call takes a name and an arguments object."))
		return _reply(message_id, tools.call(name, arguments))
	return _reply(message_id, error=(METHOD_NOT_FOUND, f"Unknown method: {method}"))


def make_handler(secret: str, tools: Tools) -> type:
	path_ok = f"/mcp/{secret}"

	class McpHandler(BaseHTTPRequestHandler):
		server_version = "VomeMCP/1"

		def log_message(self, fmt: str, *args) -> None:
			LOG.debug("mcp %s - " + fmt, self.address_string(), *args)

		def _send(self, status: int, payload: Any = None, headers: Optional[dict] = None) -> None:
			raw = b"" if payload is None else json.dumps(payload).encode("utf-8")
			self.send_response(status)
			if payload is not None:
				self.send_header("Content-Type", "application/json")
			for key, value in (headers or {}).items():
				self.send_header(key, value)
			self.send_header("Content-Length", str(len(raw)))
			self.end_headers()
			self.wfile.write(raw)

		def _allowed(self) -> bool:
			if hmac.compare_digest(self.path.split("?", 1)[0].encode(), path_ok.encode()):
				return True
			LOG.warning("mcp: refused a request from %s without the secret",
			            (self.client_address or ("?",))[0])
			self._send(404, {"error": "not found"})
			return False

		def do_GET(self) -> None:  # noqa: N802
			if self._allowed():
				self._send(405, headers={"Allow": "POST"})

		def do_DELETE(self) -> None:  # noqa: N802
			if self._allowed():
				self._send(405, headers={"Allow": "POST"})

		def do_POST(self) -> None:  # noqa: N802
			if not self._allowed():
				return
			length = int(self.headers.get("Content-Length") or 0)
			if length <= 0 or length > MAX_BODY_BYTES:
				self._send(400, _reply(None, error=(INVALID_REQUEST, "Send one JSON-RPC message.")))
				return
			try:
				message = json.loads(self.rfile.read(length).decode("utf-8"))
			except (ValueError, UnicodeDecodeError):
				self._send(400, _reply(None, error=(PARSE_ERROR, "That is not JSON.")))
				return
			try:
				if isinstance(message, list):
					replies = [r for r in (handle_message(m, tools) for m in message) if r is not None]
					reply: Any = replies or None
				else:
					reply = handle_message(message, tools)
			except Exception as err:  # noqa: BLE001 - always answer in JSON-RPC
				LOG.exception("mcp request failed")
				reply = _reply(message.get("id") if isinstance(message, dict) else None,
				               error=(-32603, f"The Vome app failed: {err}"))
			if reply is None:
				self._send(202)
			else:
				self._send(200, reply)

	return McpHandler


def announce(url: str, token: str) -> bool:
	"""Tell the Supervisor, which tells Home Assistant's MCP integration.

	The Supervisor answers an identical announcement with the one it already
	has, so the discovery (and the entry made from it) stays the same across
	restarts as long as the URL does.
	"""
	import urllib.request

	req = urllib.request.Request(
		SUPERVISOR_URL.rstrip("/") + "/discovery",
		data=json.dumps({"service": "mcp", "config": {"url": url}}).encode("utf-8"),
		headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
		method="POST",
	)
	try:
		with urllib.request.urlopen(req, timeout=15) as resp:  # noqa: S310 - fixed supervisor URL
			return resp.status == 200
	except Exception as err:  # noqa: BLE001 - retried; the panel works without it
		LOG.info("MCP: Supervisor did not take the announcement yet: %s", err)
		return False


def _announce_until_done(url: str, token: str) -> None:
	stop = threading.Event()
	while not announce(url, token):
		stop.wait(ANNOUNCE_RETRY_SECONDS)
	LOG.info("MCP: announced to Home Assistant's Model Context Protocol integration")


def start(*, call_service: Callable, unwrap: Callable, supervisor_token: str) -> None:
	"""Serve MCP and announce it; the panel carries on if either fails."""
	secret = load_secret()
	tools = Tools(call_service, unwrap)
	try:
		server = ThreadingHTTPServer(("0.0.0.0", MCP_PORT), make_handler(secret, tools))
	except OSError as err:
		LOG.warning("MCP: could not listen on :%s: %s", MCP_PORT, err)
		return
	threading.Thread(target=server.serve_forever, name="vome-mcp", daemon=True).start()
	LOG.info("MCP: listening on :%s", MCP_PORT)
	if supervisor_token:
		threading.Thread(
			target=_announce_until_done, args=(server_url(secret), supervisor_token),
			name="vome-mcp-announce", daemon=True,
		).start()
