# flake8: noqa
"""An MCP key for a coding agent, issued from inside the house.

The flow being pinned here is the one a developer walks:

1. Open the panel in a Home Assistant that has never heard of Vome.
2. Tick what the key may do, press one button, paste the JSON.
3. Change your mind about the permissions without re-pasting anything.
4. Revoke, or connect an account and keep the same key working.

Two things would be unforgivable to get wrong, and both are here: a
temporary link must never read as a permanent one, and a key must never
be quietly wider than the boxes that were ticked.
"""
import json
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest

import custom_components.vomesync.agent_key as ak
from custom_components.vomesync.const import (
	CONF_RELAY,
	CONF_RELAY_AGENT_EXPIRES,
	CONF_RELAY_AGENT_TRIAL,
	CONF_RELAY_FORWARD_UI,
	CONF_RELAY_GUEST,
	CONF_RELAY_SECRET,
	CONF_RELAY_SERVER_ID,
)
from custom_components.vomesync.relay_client import (
	async_agent_key,
	async_request_agent_key,
)


ISSUED = {
	"trial": True,
	"trial_id": "agt-1",
	"server_id": "rly-1",
	"relay_secret": "rly_rly-1.s",
	"relay_ws_url": "wss://sync.vome.io/ws/relay",
	"token": "vh_thekey",
	"scopes": ["ha:read", "ha:write", "ha:config"],
	"expires_at": 4_100_000_000,
	"mcp": {
		"hosted": True,
		"url": "https://vome.io/mcp",
		"json": '{\n  "mcpServers": {}\n}',
	},
}


# ── The HTTP calls ──────────────────────────────────────────────────────────

def _session_returning(payload, status=200):
	response = AsyncMock()
	response.status = status
	response.json.return_value = payload
	response.text.return_value = json.dumps(payload)
	response.raise_for_status = MagicMock()
	session = AsyncMock(spec=aiohttp.ClientSession)
	session.post.return_value.__aenter__.return_value = response
	session.request.return_value.__aenter__.return_value = response
	return session


class TestTheCalls:
	@pytest.mark.asyncio
	async def test_asking_for_a_key_is_one_post_with_no_credential(self):
		session = _session_returning(ISSUED)

		result = await async_request_agent_key(
			session, "https://vome.io", "My HA",
			instance_id="uuid-1", scopes=["ha:read"],
		)

		assert result["token"] == "vh_thekey"
		args, kwargs = session.post.call_args
		assert args[0] == "https://vome.io/api/v1/relay/agent"
		assert kwargs["json"] == {
			"name": "My HA", "instance_id": "uuid-1", "scopes": ["ha:read"],
		}
		assert "headers" not in kwargs, "this is the one call with no credential"

	@pytest.mark.asyncio
	async def test_a_refusal_is_reported_in_vomes_own_words(self):
		"""Both refusals that matter are things to tell a person."""
		session = _session_returning(
			{"error": "This Home Assistant already has an agent key.",
			 "code": "trial_exists"},
			status=409,
		)

		with pytest.raises(RuntimeError, match="already has an agent key"):
			await async_request_agent_key(session, "https://vome.io", "My HA")

	@pytest.mark.asyncio
	async def test_managing_it_carries_the_relay_secret(self):
		session = _session_returning({"active": True, "scopes": ["ha:read"]})

		await async_agent_key(session, "PATCH", "https://vome.io", "rly_x.y",
		                      {"scopes": ["ha:read"]})

		args, kwargs = session.request.call_args
		assert args[0] == "PATCH"
		assert args[1] == "https://vome.io/api/sync/agent/mcp-key"
		assert kwargs["headers"]["Authorization"] == "Bearer rly_x.y"


# ── The flow ────────────────────────────────────────────────────────────────

class _Entry:
	"""A config entry that records what the flow writes back to it."""

	def __init__(self, options=None):
		self.entry_id = "entry-1"
		self.data = {}
		self.options = dict(options or {})


def _hass(entry):
	hass = MagicMock()
	hass.data = {}
	hass.config.location_name = "My House"

	def _update_entry(target, options=None, **_kwargs):
		if options is not None:
			target.options = dict(options)

	hass.config_entries.async_update_entry = MagicMock(side_effect=_update_entry)
	return hass


def _issuing(entry, payload=None):
	"""Patch everything the issue path reaches out to."""
	return (
		patch.object(ak, "async_get_clientsession", return_value=MagicMock()),
		patch.object(ak, "async_request_agent_key",
		             AsyncMock(return_value=dict(payload or ISSUED))),
		patch.object(ak, "async_instance_id", AsyncMock(return_value="uuid-1")),
		patch.object(ak, "async_start_relay", AsyncMock()),
	)


class TestIssuingFromAnUnlinkedHouse:
	@pytest.mark.asyncio
	async def test_one_press_links_it_and_hands_back_the_paste(self):
		entry = _Entry()
		hass = _hass(entry)
		session, request, instance, start_relay = _issuing(entry)
		with session, request, instance, start_relay as started:
			result = await ak.async_request_key(hass, entry)

		assert result["token"] == "vh_thekey"
		assert result["mcp"]["json"]
		# Nothing can be brokered until the tunnel is up.
		started.assert_awaited_once()

		relay = entry.options[CONF_RELAY]
		assert relay[CONF_RELAY_SERVER_ID] == "rly-1"
		assert relay[CONF_RELAY_SECRET] == "rly_rly-1.s"
		assert relay[CONF_RELAY_AGENT_TRIAL] is True
		assert relay[CONF_RELAY_AGENT_EXPIRES] == 4_100_000_000

	@pytest.mark.asyncio
	async def test_it_does_not_publish_this_house_on_the_web(self):
		"""A guest score run turns the UI forward on because its owner is
		given an address to open. An agent key needs no such thing, and
		publishing a login page is not a side effect anybody asked for."""
		entry = _Entry()
		hass = _hass(entry)
		session, request, instance, start_relay = _issuing(entry)
		with session, request, instance, start_relay:
			await ak.async_request_key(hass, entry)

		assert CONF_RELAY_FORWARD_UI not in entry.options[CONF_RELAY]

	@pytest.mark.asyncio
	async def test_the_ticked_boxes_are_what_is_asked_for(self):
		entry = _Entry()
		hass = _hass(entry)
		session, request, instance, start_relay = _issuing(entry)
		with session, request as asked, instance, start_relay:
			await ak.async_request_key(hass, entry, scopes=["ha:read", "ha:files"])

		assert asked.await_args.kwargs["scopes"] == ["ha:read", "ha:files"]

	@pytest.mark.asyncio
	async def test_files_is_not_asked_for_unless_ticked(self):
		entry = _Entry()
		hass = _hass(entry)
		session, request, instance, start_relay = _issuing(entry)
		with session, request as asked, instance, start_relay:
			await ak.async_request_key(hass, entry)

		assert asked.await_args.kwargs["scopes"] == ["ha:read", "ha:write", "ha:config"]

	@pytest.mark.asyncio
	async def test_a_scope_this_build_does_not_know_is_dropped(self):
		entry = _Entry()
		hass = _hass(entry)
		session, request, instance, start_relay = _issuing(entry)
		with session, request as asked, instance, start_relay:
			await ak.async_request_key(hass, entry, scopes=["ha:read", "instances:write"])

		assert asked.await_args.kwargs["scopes"] == ["ha:read"]

	@pytest.mark.asyncio
	async def test_the_house_records_which_vome_issued_the_key(self):
		"""Credentials only work against the site that granted them;
		without this every later call falls back to production."""
		entry = _Entry({"portal_url": "https://staging.vome.io"})
		hass = _hass(entry)
		session, request, instance, start_relay = _issuing(entry)
		with session, request as asked, instance, start_relay:
			await ak.async_request_key(hass, entry)

		assert asked.await_args.args[1] == "https://staging.vome.io"
		assert entry.options["portal_url"] == "https://staging.vome.io"


class TestWhenItShouldRefuse:
	@pytest.mark.asyncio
	async def test_a_house_that_already_has_a_key_is_told_to_replace_it(self):
		entry = _Entry({CONF_RELAY: {
			CONF_RELAY_SERVER_ID: "rly-1", CONF_RELAY_SECRET: "s",
			CONF_RELAY_AGENT_TRIAL: True,
		}})
		with pytest.raises(ak.AgentKeyError, match="Replace key"):
			await ak.async_request_key(_hass(entry), entry)

	@pytest.mark.asyncio
	async def test_a_guest_score_link_is_not_quietly_thrown_away(self):
		"""Issuing over it would orphan the run at Vome's end."""
		entry = _Entry({CONF_RELAY: {
			CONF_RELAY_SERVER_ID: "rly-1", CONF_RELAY_SECRET: "s",
			CONF_RELAY_GUEST: True,
		}})
		with pytest.raises(ak.AgentKeyError, match="health-score link"):
			await ak.async_request_key(_hass(entry), entry)

	@pytest.mark.asyncio
	async def test_a_linked_house_is_sent_to_its_own_account(self):
		entry = _Entry({CONF_RELAY: {
			CONF_RELAY_SERVER_ID: "rly-1", CONF_RELAY_SECRET: "s",
		}})
		with pytest.raises(ak.AgentKeyError, match="API tokens"):
			await ak.async_request_key(_hass(entry), entry)


class TestLivingWithIt:
	def _trial_entry(self):
		return _Entry({CONF_RELAY: {
			CONF_RELAY_SERVER_ID: "rly-1", CONF_RELAY_SECRET: "rly_rly-1.s",
			CONF_RELAY_AGENT_TRIAL: True, CONF_RELAY_AGENT_EXPIRES: 4_100_000_000,
		}})

	@pytest.mark.asyncio
	async def test_changing_permissions_does_not_touch_the_key(self):
		entry = self._trial_entry()
		answer = {"scopes": ["ha:read", "ha:files"], "active": True, "_status": 200}
		with patch.object(ak, "async_get_clientsession", return_value=MagicMock()), \
				patch.object(ak, "async_agent_key", AsyncMock(return_value=answer)) as call:
			result = await ak.async_set_scopes(hass := _hass(entry), entry,
			                                   ["ha:read", "ha:files"])

		assert result["scopes"] == ["ha:read", "ha:files"]
		assert "_status" not in result
		assert call.await_args.args[1] == "PATCH"
		# The stored credentials are untouched, so the pasted mcp.json lives.
		assert entry.options[CONF_RELAY][CONF_RELAY_SECRET] == "rly_rly-1.s"

	@pytest.mark.asyncio
	async def test_an_empty_permission_set_is_refused_here_too(self):
		entry = self._trial_entry()
		with pytest.raises(ak.AgentKeyError, match="at least one"):
			await ak.async_set_scopes(_hass(entry), entry, [])

	@pytest.mark.asyncio
	async def test_replacing_a_lost_key_keeps_the_clock(self):
		entry = self._trial_entry()
		answer = dict(ISSUED, token="vh_newkey")
		with patch.object(ak, "async_get_clientsession", return_value=MagicMock()), \
				patch.object(ak, "async_agent_key", AsyncMock(return_value=answer)):
			result = await ak.async_reissue(_hass(entry), entry)

		assert result["token"] == "vh_newkey"
		assert entry.options[CONF_RELAY][CONF_RELAY_AGENT_EXPIRES] == 4_100_000_000

	@pytest.mark.asyncio
	async def test_revoking_ends_vomes_side_before_forgetting_ours(self):
		"""The other order leaves a house that looks unlinked while a
		live key still reaches into it."""
		entry = self._trial_entry()
		order = []
		revoke = AsyncMock(side_effect=lambda *a, **k: order.append("vome") or {"revoked": True})
		stop = AsyncMock(side_effect=lambda *a, **k: order.append("local"))
		with patch.object(ak, "async_get_clientsession", return_value=MagicMock()), \
				patch.object(ak, "async_agent_key", revoke), \
				patch.object(ak, "async_stop_relay", stop):
			result = await ak.async_revoke(_hass(entry), entry)

		assert result["revoked"] is True
		assert order == ["vome", "local"]
		assert CONF_RELAY not in entry.options

	@pytest.mark.asyncio
	async def test_revoking_with_nothing_to_revoke_is_not_an_error(self):
		entry = _Entry()
		assert (await ak.async_revoke(_hass(entry), entry))["revoked"] is False


class TestWhatThePanelIsTold:
	@pytest.mark.asyncio
	async def test_an_unlinked_house_is_offered_a_key(self):
		entry = _Entry()
		state = await ak.async_panel_state(_hass(entry), entry)
		assert state["offer"] == "issue"
		assert state["default_scopes"] == ["ha:read", "ha:write", "ha:config"]
		assert "ha:files" in state["offered_scopes"]

	@pytest.mark.asyncio
	async def test_a_trial_house_is_shown_vomes_clock_not_its_own(self):
		"""A panel reporting its own stale copy would show a key as live
		after it had stopped working."""
		entry = _Entry({CONF_RELAY: {
			CONF_RELAY_SERVER_ID: "rly-1", CONF_RELAY_SECRET: "s",
			CONF_RELAY_AGENT_TRIAL: True, CONF_RELAY_AGENT_EXPIRES: 4_100_000_000,
		}})
		asked = {"active": True, "scopes": ["ha:read"], "seconds_left": 42, "_status": 200}
		with patch.object(ak, "async_get_clientsession", return_value=MagicMock()), \
				patch.object(ak, "async_agent_key", AsyncMock(return_value=asked)):
			state = await ak.async_panel_state(_hass(entry), entry)

		assert state["offer"] == "manage"
		assert state["seconds_left"] == 42
		assert state["scopes"] == ["ha:read"]

	@pytest.mark.asyncio
	async def test_the_panel_still_renders_when_vome_is_unreachable(self):
		entry = _Entry({CONF_RELAY: {
			CONF_RELAY_SERVER_ID: "rly-1", CONF_RELAY_SECRET: "s",
			CONF_RELAY_AGENT_TRIAL: True,
		}})
		with patch.object(ak, "async_get_clientsession", return_value=MagicMock()), \
				patch.object(ak, "async_agent_key",
				             AsyncMock(side_effect=RuntimeError("Vome unreachable"))):
			state = await ak.async_panel_state(_hass(entry), entry)

		assert state["offer"] == "manage"
		assert "unreachable" in state["error"]

	@pytest.mark.asyncio
	async def test_a_linked_house_is_pointed_at_its_account(self):
		entry = _Entry({CONF_RELAY: {
			CONF_RELAY_SERVER_ID: "rly-1", CONF_RELAY_SECRET: "s",
		}})
		state = await ak.async_panel_state(_hass(entry), entry)
		assert state["offer"] == "linked_account"

	@pytest.mark.asyncio
	async def test_a_guest_house_is_asked_to_finish_that_first(self):
		entry = _Entry({CONF_RELAY: {
			CONF_RELAY_SERVER_ID: "rly-1", CONF_RELAY_SECRET: "s",
			CONF_RELAY_GUEST: True,
		}})
		state = await ak.async_panel_state(_hass(entry), entry)
		assert state["offer"] == "guest_link_first"
