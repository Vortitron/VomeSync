# flake8: noqa
"""Forwarding must not hang on a response that never ends.

Full-UI forwarding buffers each response whole before replying. A Home
Assistant endpoint whose body never ends -- /api/hassio/supervisor/logs/follow,
Server-Sent Events, a camera feed -- therefore burned the entire 60s forward
timeout and came back as a bare 502, which is exactly what the browser shows
when the house is offline. The two are not the same problem and should not
look the same.

These responses genuinely cannot be carried by a buffering proxy. Carrying
them needs a chunked relay protocol on both sides; until then the least a
forward can do is fail quickly and say what happened.
"""
import asyncio
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from custom_components.vomesync import relay_client  # noqa: E402
from custom_components.vomesync.const import RELAY_FORWARD_MAX_BODY  # noqa: E402


class FakeResponse:
	"""Just the surface ``_read_forwardable_body`` touches."""

	def __init__(self, headers=None, body=b"", never_ends=False):
		self.headers = headers or {}
		self._body = body
		self._never_ends = never_ends
		self.read_started = False

	async def read(self):
		self.read_started = True
		if self._never_ends:
			await asyncio.Event().wait()  # a follow/SSE body: no EOF, ever
		return self._body


@pytest.mark.asyncio
async def test_an_ordinary_body_is_forwarded_unchanged():
	resp = FakeResponse(headers={"Content-Type": "text/html"}, body=b"<html></html>")
	body, refusal = await relay_client._read_forwardable_body(resp)
	assert refusal is None
	assert body == b"<html></html>"


@pytest.mark.asyncio
async def test_server_sent_events_are_refused_without_reading():
	"""SSE says so in its content type, so there is no need to find out the slow way."""
	resp = FakeResponse(headers={"Content-Type": "text/event-stream; charset=utf-8"})
	body, refusal = await relay_client._read_forwardable_body(resp)
	assert body is None
	assert "stream" in refusal.lower()
	assert resp.read_started is False, 'an SSE body must not be buffered at all'


@pytest.mark.asyncio
async def test_a_body_that_never_ends_fails_fast_and_explains_itself(monkeypatch):
	"""The /logs/follow case: the whole reason a blank 502 appeared a minute later."""
	monkeypatch.setattr(relay_client, "RELAY_FORWARD_BODY_TIMEOUT", 0.05)
	resp = FakeResponse(headers={"Content-Type": "text/plain"}, never_ends=True)

	started = time.monotonic()
	body, refusal = await relay_client._read_forwardable_body(resp)
	elapsed = time.monotonic() - started

	assert body is None
	assert refusal, 'a stream must be refused, not returned as an empty body'
	assert "stream" in refusal.lower(), \
		'the message has to distinguish this from the house being offline'
	assert elapsed < 5, 'the point is to answer quickly rather than burn the timeout'


@pytest.mark.asyncio
async def test_a_declared_oversize_body_is_refused_before_it_is_buffered():
	"""Reading 25 MiB only to throw it away is the slowest possible way to say no."""
	resp = FakeResponse(headers={
		"Content-Type": "application/octet-stream",
		"Content-Length": str(RELAY_FORWARD_MAX_BODY + 1),
	})
	body, refusal = await relay_client._read_forwardable_body(resp)
	assert body is None
	assert "too large" in refusal.lower()
	assert resp.read_started is False


@pytest.mark.asyncio
async def test_an_undeclared_oversize_body_is_still_refused():
	"""A chunked response has no Content-Length to check, so the cap still applies."""
	resp = FakeResponse(
		headers={"Content-Type": "application/octet-stream"},
		body=b"x" * (RELAY_FORWARD_MAX_BODY + 1),
	)
	body, refusal = await relay_client._read_forwardable_body(resp)
	assert body is None
	assert "too large" in refusal.lower()


@pytest.mark.asyncio
async def test_a_bad_content_length_does_not_break_the_forward():
	"""Header values are attacker-adjacent input; a junk one must not raise."""
	resp = FakeResponse(
		headers={"Content-Type": "text/html", "Content-Length": "not-a-number"},
		body=b"ok",
	)
	body, refusal = await relay_client._read_forwardable_body(resp)
	assert refusal is None
	assert body == b"ok"
