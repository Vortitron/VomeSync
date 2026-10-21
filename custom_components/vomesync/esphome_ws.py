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
import re
from typing import Any, Awaitable, Callable, Optional

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


# A PlatformIO / esptool progress redraw: ``Downloading: [====    ]  45%``.
# ESPHome's own splitter breaks on ``\r`` as well as ``\n`` so these surface
# live, which is right for a browser that overwrites a line in place and wrong
# for us: one framework download emits hundreds of near-identical lines and
# crowds the real build output out of the line budget downstream.
_PROGRESS_RE = re.compile(r"^(?P<label>.*?)\[[=\s.\-#>]*\]\s*\d{1,3}(?:\.\d+)?\s*%")


def progress_label(line: str) -> Optional[str]:
	"""The text before the bar, or None when the line is not a progress bar.

	The label is what makes collapsing safe. A build's summary ends with two
	lines that look exactly like progress bars::

	    RAM:   [======    ]  63.7% (used 79324 bytes from 124580 bytes)
	    Flash: [=======   ]  71.2% (used 1307351 bytes from 1835008 bytes)

	Collapsing purely on "looks like a bar" would drop the RAM line and report
	only Flash. Two lines are the same bar being redrawn only if they carry the
	same label, so that is the test.
	"""
	match = _PROGRESS_RE.match(line)
	return match.group("label") if match else None


# How long a followed job may stay silent before the job itself is asked how
# it is doing. A build streams output within a second or two of starting; one
# queued behind another build is silent for longer, which is why the answer is
# a question to the dashboard and not a failure.
FOLLOW_FIRST_FRAME_TIMEOUT = 20.0
_GET_JOB = "firmware/get_job"

# Device Builder's job statuses are ``queued``/``running``/``completed``/
# ``failed``/``cancelled``; the others are spellings a frame might use.
_SUCCEEDED_STATUSES = frozenset({"completed", "succeeded", "success"})
_FAILED_STATUSES = frozenset({"failed", "error", "cancelled", "canceled"})


def job_status_is_terminal(payload: Any) -> bool:
	"""Whether ``payload`` describes a job (or command) that has finished."""
	if not isinstance(payload, dict):
		return False
	status = str(payload.get("status") or "").lower()
	return status in _SUCCEEDED_STATUSES or status in _FAILED_STATUSES


def exit_code_of(payload: Any) -> Optional[int]:
	"""The exit status a terminal payload reports, or None when it says nothing.

	``exit_code`` (a firmware job) or ``code`` (a streamed subprocess) when
	present; otherwise the status decides, and a job that failed with neither
	still failed. A failed status never reads as 0, whatever the code says.
	"""
	if not isinstance(payload, dict):
		return None
	code = payload.get("exit_code")
	if code is None:
		code = payload.get("code")
	if isinstance(code, bool) or not isinstance(code, (int, type(None))):
		code = None
	status = str(payload.get("status") or "").lower()
	if status in _FAILED_STATUSES:
		return code if code else 1
	if code is not None:
		return code
	if status in _SUCCEEDED_STATUSES:
		return 0
	if payload.get("error"):
		return 1
	return None


class _LineSink:
	"""Output lines on their way up, with progress-bar redraws collapsed."""

	def __init__(self, emit: Callable[[dict[str, Any]], Any]) -> None:
		self._emit = emit
		self._pending: Optional[str] = None
		self._label: Optional[str] = None
		self.emitted = False

	async def flush(self) -> None:
		if self._pending is not None:
			pending, self._pending, self._label = self._pending, None, None
			await self.line(pending)

	async def line(self, text: str) -> None:
		self.emitted = True
		# Job output keeps its own newline; adding another doubled every line.
		await self._emit({"event": "line", "data": text if text.endswith("\n") else f"{text}\n"})

	async def lines(self, lines: Any) -> None:
		if not isinstance(lines, list):
			return
		for line in lines:
			if not isinstance(line, str):
				continue
			label = progress_label(line)
			if label is not None:
				# Hold it: if the next line redraws the *same* bar this one
				# never needed sending, and if it does not, the last state of
				# the bar is the one worth keeping.
				if self._pending is not None and label != self._label:
					await self.flush()
				self._pending = line
				self._label = label
				continue
			await self.flush()
			await self.line(line)


async def _emit_exit(
	sink: _LineSink, emit: Callable[[dict[str, Any]], Any], payload: Any
) -> None:
	"""Send the exit for a terminal payload, with its error text if it failed."""
	await sink.flush()
	code = exit_code_of(payload)
	if code and isinstance(payload, dict) and payload.get("error"):
		await sink.line(str(payload["error"]))
	await emit({"event": "exit", "code": code})


class EsphomeWsError(Exception):
	"""The dashboard refused a command, or its ``/ws`` API is unreachable."""


# A job outlives the socket that follows it, so a dropped socket is followed
# again, a few times, after a short pause for a dashboard that is busy compiling.
FOLLOW_REATTACH_ATTEMPTS = 3
FOLLOW_REATTACH_DELAY = 2.0

_DROPPED = "closed the /ws connection"


def _socket_dropped(err: BaseException) -> bool:
	"""Whether ``err`` is the socket going away, not the dashboard saying no."""
	if isinstance(err, (aiohttp.ClientError, ConnectionError)):
		return True
	return _DROPPED in str(err)


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
		*,
		job_id: Optional[str] = None,
	) -> None:
		"""Forward frames for ``message_id`` until the terminal one arrives.

		Consecutive progress-bar redraws are collapsed to the newest, so a
		framework download does not spend the whole line budget on a bar being
		drawn a hundred times.

		With ``job_id`` (a ``firmware/follow_job``), silence is not trusted. A
		config that fails validation ends its job within a second, before the
		follow is even sent; Device Builder 1.x replays such a job and closes it
		with a result frame, but a dashboard that does not would leave this
		waiting the whole build ceiling while the agent's own deadline expires
		and it reports "timeout" instead of the error. So when the follow says
		nothing for :data:`FOLLOW_FIRST_FRAME_TIMEOUT`, or ends without saying
		how the job went, the job itself is asked (``firmware/get_job``).
		"""
		sink = _LineSink(emit)
		first_wait = FOLLOW_FIRST_FRAME_TIMEOUT if job_id else None
		heard = False
		probe_id: Optional[str] = None
		# Set once the follow has ended without a verdict: the probe's answer is
		# then the last word, and waiting on it is bounded too.
		ended_unsure = False

		async def probe() -> str:
			return await self._send(_GET_JOB, {"job_id": job_id})

		loop = asyncio.get_running_loop()
		# Asking the job does not extend the ceiling: a job that stays silent
		# (queued behind another build) is asked again every first_wait, but
		# only until ``timeout`` of silence has passed, as before.
		silent_until = loop.time() + timeout
		while True:
			if ended_unsure:
				wait = FOLLOW_FIRST_FRAME_TIMEOUT
			elif not heard and first_wait is not None:
				wait = max(0.0, min(first_wait, silent_until - loop.time()))
			else:
				wait = timeout
			try:
				frame = await self._next_frame(wait)
			except asyncio.TimeoutError:
				if ended_unsure:
					await sink.flush()
					await sink.line("ESPHome did not say how the job ended.")
					await emit({"event": "exit", "code": None})
					return
				if job_id and not heard and loop.time() < silent_until:
					if probe_id is None:
						probe_id = await probe()
					continue
				raise
			frame_id = frame.get("message_id")
			if probe_id is not None and frame_id == probe_id:
				job = frame.get("result") if "result" in frame else None
				if isinstance(job, dict) and job_status_is_terminal(job):
					await sink.flush()
					if not sink.emitted:
						# Nothing was streamed, so the job's own buffer is the
						# only place the validation error is.
						await sink.lines(job.get("output"))
					await _emit_exit(sink, emit, job)
					return
				probe_id = None
				if ended_unsure:
					await sink.flush()
					await sink.line("ESPHome did not say how the job ended.")
					await emit({"event": "exit", "code": None})
					return
				# Queued behind another build, or a dashboard with no get_job:
				# keep following at the normal ceiling.
				continue
			if frame_id != message_id:
				continue
			heard = True
			if "error_code" in frame:
				await sink.flush()
				detail = f"{frame.get('error_code')}: {frame.get('details') or ''}".strip()
				await sink.line(detail)
				await emit({"event": "exit", "code": 1})
				return
			event = frame.get("event")
			if event in ("output", "snapshot"):
				data = frame.get("data")
				if isinstance(data, dict) and job_status_is_terminal(data):
					# A job-shaped snapshot that is already over.
					await sink.flush()
					await sink.lines(data.get("output"))
					await _emit_exit(sink, emit, data)
					return
				# A snapshot may replay several buffered lines at once.
				await sink.lines(data if isinstance(data, list) else [data])
			elif event == "result":
				await sink.flush()
				# The two terminal shapes differ by one key: a firmware job
				# reports ``exit_code``, a streamed subprocess ``code``. Reading
				# only one leaves the other's exit as None, which reads as
				# failure even when the command plainly succeeded.
				await _emit_exit(sink, emit, frame.get("data") or {})
				return
			elif event is not None and isinstance(frame.get("data"), dict) and (
				job_status_is_terminal(frame["data"])
			):
				# Any other event that carries a finished job (a lifecycle
				# frame such as ``job_failed``) is just as terminal.
				await sink.flush()
				await _emit_exit(sink, emit, frame["data"])
				return
			elif "result" in frame:
				# A streaming command that answered with a plain result is done.
				# It is only a success if it says so, or says nothing at all
				# about a command that is not a job: a plain reply carrying a
				# failed status or a nonzero exit is a failure.
				await sink.flush()
				result = frame.get("result")
				code = exit_code_of(result) if isinstance(result, dict) else None
				if code is None and job_id:
					# Ended without a verdict: ask the job, once, briefly.
					ended_unsure = True
					probe_id = await probe()
					continue
				if isinstance(result, dict) and result.get("error") and code:
					await sink.line(str(result["error"]))
				await emit({"event": "exit", "code": 0 if code is None else code})
				return

	async def pending_migrations(self, configuration: str, timeout: float) -> dict:
		"""Which rename rules ``configuration``'s YAML still needs.

		ESPHome surfaces these as a banner in its own UI; an agent never sees it
		and goes on writing deprecated spellings. The dashboard reports the
		rules, not replacement text, so this reports them too — knowing that
		``homeassistant.service`` became ``homeassistant.action`` is what an
		agent needs to fix the YAML it already has read/write access to.
		"""
		yaml_text = await self.call(
			"devices/get_config", {"configuration": configuration}, timeout
		)
		if not isinstance(yaml_text, str):
			yaml_text = ""
		result = await self.call("editor/migrate_config", {"content": yaml_text}, timeout)
		changes = (result or {}).get("changes") or [] if isinstance(result, dict) else []
		return {
			"configuration": configuration,
			"migrations_pending": bool(changes),
			# `required` means the installed ESPHome already rejects the old
			# spelling — the difference between "tidy this up" and "this will
			# stop compiling".
			"required": any(bool(c.get("required")) for c in changes if isinstance(c, dict)),
			"changes": changes,
		}

	async def pair_remote_build(
		self,
		hostname: str,
		port: int,
		pairing_key: str,
		timeout: float,
	) -> dict[str, Any]:
		"""Pair this dashboard with a headless ``--remote-build-only`` receiver.

		The dashboard (on the house) dials ``hostname:port`` itself. We only
		drive the Device Builder ``remote_build/*`` commands over local ``/ws``.
		"""
		preview = await self.call(
			"remote_build/preview_pair",
			{"hostname": hostname, "port": port},
			timeout,
		)
		if not isinstance(preview, dict) or not preview.get("pin_sha256"):
			raise EsphomeWsError(
				"ESPHome did not return a pairing fingerprint. Device Builder "
				"2026.6 or newer is required for remote compile."
			)
		pin = str(preview["pin_sha256"])
		result = await self.call(
			"remote_build/request_pair",
			{
				"hostname": hostname,
				"port": port,
				"pin_sha256": pin,
				"receiver_label": "Vome compile",
				"offloader_label": "Home Assistant",
				"pairing_key": pairing_key,
				"offloader_label_auto": False,
				"receiver_label_auto": False,
			},
			timeout,
		)
		if isinstance(result, dict):
			out = dict(result)
			out.setdefault("pin_sha256", pin)
			return out
		return {"pin_sha256": pin}

	async def run_build_command(
		self,
		command: str,
		configuration: str,
		port: Optional[str],
		emit: Callable[[dict[str, Any]], Any],
		timeout: float,
		*,
		reconnect: Optional[Callable[[], Awaitable["EsphomeWsSession"]]] = None,
	) -> None:
		"""Run one of the ESPHome commands Vome exposes, streaming its output.

		``reconnect`` opens a fresh session. A build is a job on the dashboard,
		not on this socket: when the socket drops mid-build (GamlaBio, 21 Oct
		2026: eight minutes into an esp-idf rebuild, at ninja step 94/125, while
		the build carried on and the next try found it done), the same job is
		followed again on a new one rather than reported as a failure.
		"""
		args = _args_for(command, configuration, port)
		if command in _STREAM_COMMANDS:
			await self.stream(_STREAM_COMMANDS[command], args, emit, timeout)
			return
		queued = await self.call(_JOB_COMMANDS[command], args, timeout)
		job_id = (queued or {}).get("job_id") if isinstance(queued, dict) else None
		if not job_id:
			raise EsphomeWsError(f"{command} did not return a job id.")
		if job_status_is_terminal(queued):
			# Already over by the time the queue answered (a config that fails
			# validation can be): nothing to follow, the reply says how it went.
			sink = _LineSink(emit)
			await sink.lines(queued.get("output"))
			await _emit_exit(sink, emit, queued)
			return
		# Following the job (rather than the deprecated per-command socket) is
		# what puts an agent-triggered build in the dashboard's own task list.
		session: EsphomeWsSession = self
		reattached = 0
		while True:
			message_id = await session._send(_FOLLOW_JOB, {"job_id": job_id})
			try:
				await session._pump(message_id, emit, timeout, job_id=job_id)
				return
			except (EsphomeWsError, aiohttp.ClientError, ConnectionError) as err:
				if reconnect is None or reattached >= FOLLOW_REATTACH_ATTEMPTS or not _socket_dropped(err):
					raise
				reattached += 1
				_LOGGER.info(
					"ESPHome /ws dropped while following job %s (%s); following it again (%d/%d)",
					job_id, err, reattached, FOLLOW_REATTACH_ATTEMPTS,
				)
				await emit({"event": "line", "data": (
					"[Vome] The ESPHome dashboard dropped the connection; the build "
					"carries on there, so Vome is following the same job again.\n"
				)})
				await asyncio.sleep(FOLLOW_REATTACH_DELAY)
				session = await reconnect()
