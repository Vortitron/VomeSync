"""Client for the ESPHome Device Builder's multiplexed ``/ws`` API.

ESPHome split its dashboard out into ``esphome-device-builder``, which replaced
the old per-command WebSockets (``/validate``, ``/logs``, …) and the ``/edit``
REST endpoint with a single multiplexed ``/ws`` socket.  The legacy surface that
remains — ``/devices``, ``/json-config``, ``/compile``, ``/upload`` — is
explicitly documented upstream as deprecated and "will be removed once HA
migrates to the /ws multiplexed API".  Building on it again would just buy the
same breakage a second time.

**This module is the compatibility layer, and it lives here on purpose.**  The
component is the only thing that can see the dashboard at all (the add-on is
host-networked with its web port disabled, behind an ingress nginx admitting
only the Supervisor and localhost), so it is also the right place to absorb the
dashboard's protocol churn.  Everything above it — relay, portal, MCP — keeps
the stable ``{"event": "line"}`` / ``{"event": "exit"}`` contract it already
speaks, and none of it needs to know that ESPHome moved.

Wire protocol
-------------
Connect, and the server opens with a *server info* frame (carrying
``requires_auth``).  Then each request is ``{"command", "message_id", "args"}``
and replies arrive tagged with that ``message_id``:

* ``{"message_id", "result"}``            — a completed non-streaming command
* ``{"message_id", "event": "output", "data"}``   — one line of output
* ``{"message_id", "event": "snapshot", "data"}`` — buffered lines replayed
* ``{"message_id", "event": "result", "data"}``   — terminal; the exit status is
  ``exit_code`` on a firmware job and ``code`` on a streamed subprocess
* ``{"message_id", "error_code", "details"}``     — refused

Builds are *jobs*: ``firmware/compile`` and ``firmware/upload`` queue one and
return its ``job_id``, then ``firmware/follow_job`` streams it.  Doing it that
way (rather than the deprecated ``/compile`` socket) is also what makes an
agent-triggered build show up in the dashboard's own "Firmware tasks" panel.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Callable, Optional

import aiohttp

_LOGGER = logging.getLogger(__name__)

WS_PATH = "/ws"
DEFAULT_OTA_PORT = "OTA"

# Streaming commands: one request, output frames, a terminal frame.
_STREAM_COMMANDS: dict[str, str] = {
	"validate": "devices/validate",
	"logs": "devices/logs",
}
# Job commands: queue, then follow the job id the queue call returns.
_JOB_COMMANDS: dict[str, str] = {
	"compile": "firmware/compile",
	"upload": "firmware/upload",
	"clean": "firmware/clean",
}
SUPPORTED_COMMANDS = tuple(sorted({*_STREAM_COMMANDS, *_JOB_COMMANDS}))

_FOLLOW_JOB = "firmware/follow_job"


class EsphomeWsError(Exception):
	"""The dashboard refused a command, or its ``/ws`` API is unreachable."""


def _args_for(command: str, configuration: str, port: Optional[str]) -> dict[str, Any]:
	"""Arguments for one command, in the shape the dashboard expects."""
	if command == "logs":
		# `--device` is always passed upstream: without one `esphome logs`
		# prompts for a port and dies on the stdin-less subprocess.
		return {
			"configuration": configuration,
			"port": port or DEFAULT_OTA_PORT,
			"no_states": False,
		}
	if command == "upload":
		return {"configuration": configuration, "port": port or DEFAULT_OTA_PORT}
	return {"configuration": configuration}


class EsphomeWsSession:
	"""One connection to the dashboard's ``/ws`` endpoint."""

	def __init__(self, ws: aiohttp.ClientWebSocketResponse) -> None:
		self._ws = ws
		self._counter = 0
		self.server_info: dict[str, Any] = {}

	def _next_id(self) -> str:
		self._counter += 1
		return f"vome-{self._counter}"

	async def _send(self, command: str, args: dict[str, Any]) -> str:
		message_id = self._next_id()
		await self._ws.send_json(
			{"command": command, "message_id": message_id, "args": args}
		)
		return message_id

	async def _next_frame(self, timeout: float) -> dict[str, Any]:
		msg = await asyncio.wait_for(self._ws.receive(), timeout=timeout)
		if msg.type != aiohttp.WSMsgType.TEXT:
			raise EsphomeWsError(f"ESPHome dashboard closed the /ws connection ({msg.type.name}).")
		try:
			frame = json.loads(msg.data)
		except (ValueError, TypeError) as err:
			raise EsphomeWsError(f"Unparseable frame from the ESPHome dashboard: {err}") from err
		if not isinstance(frame, dict):
			raise EsphomeWsError("Unexpected frame shape from the ESPHome dashboard.")
		return frame

	async def handshake(self, timeout: float) -> None:
		"""Read the opening server-info frame and refuse a dashboard we cannot use."""
		info = await self._next_frame(timeout)
		self.server_info = info
		if info.get("requires_auth"):
			raise EsphomeWsError(
				"The ESPHome dashboard requires authentication on its API and Vome holds "
				"no credential for it. Remove the dashboard password, or reach it through "
				"the Home Assistant ingress it trusts."
			)

	async def call(self, command: str, args: dict[str, Any], timeout: float) -> Any:
		"""Run one non-streaming command and return its result."""
		message_id = await self._send(command, args)
		while True:
			frame = await self._next_frame(timeout)
			if frame.get("message_id") != message_id:
				continue
			if "error_code" in frame:
				raise EsphomeWsError(
					f"{command} refused ({frame.get('error_code')}): {frame.get('details') or ''}".strip()
				)
			if "result" in frame:
				return frame["result"]

	async def stream(
		self,
		command: str,
		args: dict[str, Any],
		emit: Callable[[dict[str, Any]], Any],
		timeout: float,
	) -> None:
		"""Run one streaming command, translating its frames to line/exit."""
		message_id = await self._send(command, args)
		await self._pump(message_id, emit, timeout)

	async def _pump(
		self,
		message_id: str,
		emit: Callable[[dict[str, Any]], Any],
		timeout: float,
	) -> None:
		"""Forward frames for ``message_id`` until the terminal one arrives."""
		while True:
			frame = await self._next_frame(timeout)
			if frame.get("message_id") != message_id:
				continue
			if "error_code" in frame:
				detail = f"{frame.get('error_code')}: {frame.get('details') or ''}".strip()
				await emit({"event": "line", "data": f"{detail}\n"})
				await emit({"event": "exit", "code": 1})
				return
			event = frame.get("event")
			if event in ("output", "snapshot"):
				data = frame.get("data")
				# A snapshot may replay several buffered lines at once.
				lines = data if isinstance(data, list) else [data]
				for line in lines:
					if line is None:
						continue
					await emit({"event": "line", "data": f"{line}\n"})
			elif event == "result":
				payload = frame.get("data") or {}
				# The two terminal shapes differ by one key: a firmware job
				# reports ``exit_code``, a streamed subprocess ``code``. Reading
				# only one leaves the other's exit as None, which reads as
				# failure even when the command plainly succeeded.
				code = None
				if isinstance(payload, dict):
					code = payload.get("exit_code")
					if code is None:
						code = payload.get("code")
				if code is None and isinstance(payload, dict) and payload.get("error"):
					# A job that failed without an exit code still failed.
					await emit({"event": "line", "data": f"{payload['error']}\n"})
					code = 1
				await emit({"event": "exit", "code": code})
				return
			elif "result" in frame:
				# A streaming command that answered with a plain result is done.
				await emit({"event": "exit", "code": 0})
				return

	async def run_build_command(
		self,
		command: str,
		configuration: str,
		port: Optional[str],
		emit: Callable[[dict[str, Any]], Any],
		timeout: float,
	) -> None:
		"""Run one of the ESPHome commands Vome exposes, streaming its output."""
		args = _args_for(command, configuration, port)
		if command in _STREAM_COMMANDS:
			await self.stream(_STREAM_COMMANDS[command], args, emit, timeout)
			return
		queued = await self.call(_JOB_COMMANDS[command], args, timeout)
		job_id = (queued or {}).get("job_id") if isinstance(queued, dict) else None
		if not job_id:
			raise EsphomeWsError(f"{command} did not return a job id.")
		# Following the job (rather than the deprecated per-command socket) is
		# what puts an agent-triggered build in the dashboard's own task list.
		message_id = await self._send(_FOLLOW_JOB, {"job_id": job_id})
		await self._pump(message_id, emit, timeout)
