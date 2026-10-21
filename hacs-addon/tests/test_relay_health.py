# flake8: noqa
"""Repairs: a linked home off Vome for too long, and not a moment sooner.

Short drops are normal (router reboots, Vome deploys, the relay's own
backoff); they must never reach Settings → Repairs.  A deliberate stop is
not an outage either.  What must reach it is the only case a person can
do something about: the tunnel has been closed for the whole grace period.
"""
from unittest.mock import MagicMock, patch

import pytest

import custom_components.vomesync.relay_health as rh
from custom_components.vomesync.const import DOMAIN


class _FakeLoop:
	"""call_later that remembers, and fires only when told."""

	def __init__(self):
		self.scheduled = []

	def call_later(self, delay, fn, *args):
		handle = MagicMock()
		handle.delay = delay
		handle.fire = lambda: fn(*args)
		self.scheduled.append(handle)
		return handle

	def fire_all(self):
		for handle in list(self.scheduled):
			if not handle.cancel.called:
				handle.fire()


@pytest.fixture
def hass():
	h = MagicMock()
	h.data = {}
	h.loop = _FakeLoop()
	return h


@pytest.fixture
def issues():
	with patch("homeassistant.helpers.issue_registry.async_create_issue") as create, \
		patch("homeassistant.helpers.issue_registry.async_delete_issue") as delete:
		yield create, delete


def _client(connected):
	client = MagicMock()
	client.connected = connected
	client.server_id = "srv-1"
	client.rejected = False
	return client


def test_drop_starts_the_clock_and_waits(hass, issues):
	create, _ = issues
	rh.note_relay_state(hass, "e1", False)
	assert len(hass.loop.scheduled) == 1
	assert hass.loop.scheduled[0].delay == rh.RELAY_DOWN_GRACE_SECONDS
	create.assert_not_called()


def test_still_down_when_the_clock_runs_out_raises(hass, issues):
	create, _ = issues
	with patch("custom_components.vomesync.relay_client.get_relay_client",
			return_value=_client(False)):
		rh.note_relay_state(hass, "e1", False)
		hass.loop.fire_all()
	create.assert_called_once()
	args, kwargs = create.call_args
	assert args[1] == DOMAIN
	assert args[2] == rh.issue_id("e1")
	assert kwargs["is_fixable"] is False
	assert kwargs["translation_key"] == rh.ISSUE_RELAY_DOWN
	assert kwargs["translation_placeholders"]["server_id"] == "srv-1"
	assert kwargs["translation_placeholders"]["minutes"] == "10"


def test_back_before_the_clock_runs_out_is_silent(hass, issues):
	create, delete = issues
	rh.note_relay_state(hass, "e1", False)
	rh.note_relay_state(hass, "e1", True)
	assert hass.loop.scheduled[0].cancel.called
	hass.loop.fire_all()
	create.assert_not_called()
	# A reconnect also clears an issue that may have been raised earlier.
	assert rh.issue_id("e1") in [c.args[2] for c in delete.call_args_list]


def test_reconnected_by_the_time_the_timer_fires_is_silent(hass, issues):
	"""The timer cannot always be cancelled in time; check the client too."""
	create, _ = issues
	with patch("custom_components.vomesync.relay_client.get_relay_client",
			return_value=_client(True)):
		rh.note_relay_state(hass, "e1", False)
		hass.loop.scheduled[0].fire()
	create.assert_not_called()


def test_relay_gone_by_the_time_the_timer_fires_is_silent(hass, issues):
	create, _ = issues
	with patch("custom_components.vomesync.relay_client.get_relay_client", return_value=None):
		rh.note_relay_state(hass, "e1", False)
		hass.loop.scheduled[0].fire()
	create.assert_not_called()


def test_deliberate_stop_cancels_and_clears(hass, issues):
	create, delete = issues
	rh.note_relay_state(hass, "e1", False)
	rh.forget_relay(hass, "e1")
	assert hass.loop.scheduled[0].cancel.called
	hass.loop.fire_all()
	create.assert_not_called()
	deleted = [c.args[2] for c in delete.call_args_list]
	assert rh.issue_id("e1") in deleted and rh.rejected_issue_id("e1") in deleted
	assert "e1" not in hass.data[DOMAIN][rh._KEY]


def test_second_drop_replaces_the_first_clock(hass, issues):
	rh.note_relay_state(hass, "e1", False)
	rh.note_relay_state(hass, "e1", False)
	assert hass.loop.scheduled[0].cancel.called
	assert not hass.loop.scheduled[1].cancel.called


# ── The relay side ──────────────────────────────────────────────────────────

def test_relay_client_reports_state_and_timestamps():
	from custom_components.vomesync.relay_client import RelayClient, SIGNAL_RELAY_STATE

	hass = MagicMock()
	hass.data = {}
	client = RelayClient(hass, server_id="srv-1", secret="s", entry_id="e1")
	assert client.connected is False
	assert client.connected_since is None

	with patch("homeassistant.helpers.dispatcher.async_dispatcher_send") as send, \
		patch("custom_components.vomesync.relay_health.note_relay_state") as note:
		client._note_state(True)
		send.assert_called_once_with(hass, SIGNAL_RELAY_STATE, "e1", True)
		note.assert_called_once_with(hass, "e1", True, rejected=False)
		assert client.connected_since is not None
		client._note_state(False)
		assert client.disconnected_since is not None
		assert note.call_args[0][2] is False


def test_bare_relay_client_keeps_quiet():
	"""No hass, no entry: timestamps only, nothing dispatched, nothing raised."""
	from custom_components.vomesync.relay_client import RelayClient

	client = RelayClient(None, server_id="srv-1", secret="s")
	with patch("homeassistant.helpers.dispatcher.async_dispatcher_send") as send:
		client._note_state(True)
		client._note_state(False)
	send.assert_not_called()
	assert client.connected_since is not None and client.disconnected_since is not None


@pytest.mark.asyncio
async def test_start_relay_passes_entry_and_starts_the_clock(config_entry):
	from custom_components.vomesync import relay_client as rc
	from custom_components.vomesync.const import CONF_RELAY, CONF_RELAY_SECRET, CONF_RELAY_SERVER_ID

	hass = MagicMock()
	hass.data = {}
	hass.loop = _FakeLoop()
	config_entry.options = {CONF_RELAY: {CONF_RELAY_SERVER_ID: "srv-1", CONF_RELAY_SECRET: "sec"}}
	with patch.object(rc.RelayClient, "start"), \
		patch.object(rc, "async_ensure_local_access_token", return_value="tok"), \
		patch("homeassistant.helpers.dispatcher.async_dispatcher_send") as send:
		await rc.async_start_relay(hass, config_entry)
	client = rc.get_relay_client(hass, config_entry.entry_id)
	assert client is not None and client._entry_id == config_entry.entry_id
	send.assert_called_once_with(hass, rc.SIGNAL_RELAY_STATE, config_entry.entry_id, False)
	assert len(hass.loop.scheduled) == 1

	with patch("homeassistant.helpers.issue_registry.async_delete_issue") as delete:
		await rc.async_stop_relay(hass, config_entry)
	assert hass.loop.scheduled[0].cancel.called
	assert delete.call_count == 2
	assert rc.get_relay_client(hass, config_entry.entry_id) is None


# ── A refused link is not an outage ─────────────────────────────────────────

def _entry_with(relay):
	entry = MagicMock()
	entry.options = {"relay": relay}
	return entry


@pytest.mark.parametrize("relay,kind", [
	({"server_id": "s", "agent_trial": True}, rh.REJECTED_TRIAL),
	({"server_id": "s", "guest": True}, rh.REJECTED_GUEST),
	({"server_id": "s"}, rh.REJECTED_LINK),
])
def test_refusal_raises_at_once_with_the_right_words(hass, issues, relay, kind):
	create, delete = issues
	hass.config_entries.async_get_entry.return_value = _entry_with(relay)
	with patch("custom_components.vomesync.relay_client.get_relay_client",
			return_value=_client(False)):
		rh.note_relay_state(hass, "e1", False, rejected=True)
	# No outage clock: waiting will not fix it.
	assert hass.loop.scheduled == []
	create.assert_called_once()
	args, kwargs = create.call_args
	assert args[2] == rh.rejected_issue_id("e1")
	assert kwargs["translation_key"] == kind
	assert kwargs["translation_placeholders"] == {"server_id": "srv-1"}
	# The outage issue is withdrawn, the refusal one is not.
	deleted = [c.args[2] for c in delete.call_args_list]
	assert rh.issue_id("e1") in deleted
	assert rh.rejected_issue_id("e1") not in deleted


def test_unknown_entry_gets_the_plain_wording(hass):
	hass.config_entries.async_get_entry.return_value = None
	assert rh.rejected_kind(hass, "gone") == rh.REJECTED_LINK


def test_outage_timer_stands_down_for_a_refused_link(hass, issues):
	create, _ = issues
	refused = _client(False)
	refused.rejected = True
	with patch("custom_components.vomesync.relay_client.get_relay_client", return_value=refused):
		rh.note_relay_state(hass, "e1", False)
		hass.loop.fire_all()
	create.assert_not_called()


def test_connecting_again_clears_the_refusal(hass, issues):
	_, delete = issues
	rh.note_relay_state(hass, "e1", True)
	deleted = [c.args[2] for c in delete.call_args_list]
	assert rh.rejected_issue_id("e1") in deleted and rh.issue_id("e1") in deleted


def test_relay_loop_backs_off_on_a_401_and_marks_rejected():
	import asyncio
	import aiohttp
	from custom_components.vomesync import relay_client as rc
	from custom_components.vomesync.const import RELAY_REJECTED_RETRY_DELAY

	client = rc.RelayClient(None, server_id="srv-1", secret="dead")
	err = aiohttp.WSServerHandshakeError(MagicMock(), (), status=401, message="Unauthorized")
	sleeps = []

	async def _sleep(delay):
		sleeps.append(delay)
		client._closing = True

	async def _go():
		with patch.object(client, "_connect_once", side_effect=err), \
			patch.object(rc.asyncio, "sleep", _sleep):
			await client._run()

	asyncio.run(_go())
	assert client.rejected is True
	assert sleeps == [RELAY_REJECTED_RETRY_DELAY]


def test_relay_loop_treats_a_500_as_an_ordinary_failure():
	import asyncio
	import aiohttp
	from custom_components.vomesync import relay_client as rc
	from custom_components.vomesync.const import RELAY_RECONNECT_DELAY

	client = rc.RelayClient(None, server_id="srv-1", secret="s")
	err = aiohttp.WSServerHandshakeError(MagicMock(), (), status=500, message="Verification error")
	sleeps = []

	async def _sleep(delay):
		sleeps.append(delay)
		client._closing = True

	async def _go():
		with patch.object(client, "_connect_once", side_effect=err), \
			patch.object(rc.asyncio, "sleep", _sleep):
			await client._run()

	asyncio.run(_go())
	assert client.rejected is False
	assert sleeps == [RELAY_RECONNECT_DELAY]


def test_relay_loop_treats_a_revoked_close_as_a_refusal():
	import asyncio
	from custom_components.vomesync import relay_client as rc
	from custom_components.vomesync.const import RELAY_REJECTED_RETRY_DELAY

	client = rc.RelayClient(None, server_id="srv-1", secret="s")
	sleeps = []

	async def _sleep(delay):
		sleeps.append(delay)
		client._closing = True

	async def _revoked():
		return True

	async def _go():
		with patch.object(client, "_connect_once", side_effect=_revoked), \
			patch.object(rc.asyncio, "sleep", _sleep):
			await client._run()

	asyncio.run(_go())
	assert client.rejected is True
	assert sleeps == [RELAY_REJECTED_RETRY_DELAY]


def test_a_good_connection_clears_rejected():
	from custom_components.vomesync.relay_client import RelayClient

	client = RelayClient(None, server_id="srv-1", secret="s")
	client._note_state(False, rejected=True)
	first_down = client.disconnected_since
	client._note_state(False, rejected=True)
	assert client.disconnected_since == first_down  # the outage began at the first
	client._note_state(True)
	assert client.rejected is False
