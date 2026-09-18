# flake8: noqa
"""A remote address from inside Home Assistant, before there is an account.

The portal mints the hostname. These tests pin the house-side: the button
works while unlinked, a linked house can still mint, the URL is stored,
forwarding comes on, and an external house that cannot have one is told so.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import custom_components.vomesync.guest_remote as gr
import custom_components.vomesync.health_score as hs
from custom_components.vomesync.const import (
	CONF_BACKUP,
	CONF_BACKUP_SECRET,
	CONF_RELAY,
	CONF_RELAY_FORWARD_UI,
	CONF_RELAY_GUEST,
	CONF_RELAY_GUEST_CLAIM_URL,
	CONF_RELAY_GUEST_EXPIRES,
	CONF_RELAY_REMOTE_URL,
	CONF_RELAY_SECRET,
	CONF_RELAY_SERVER_ID,
)

HOSTED_BACKUP_KEY = (
	"vbk_3d80386f-279a-4388-accb-5d8dd9d1ac71.RmFrZVRva2VuVmFsdWU"
)


class _Entry:
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
	hass.async_create_task = MagicMock()
	return hass


def _opened(**overrides):
	payload = {
		"server_id": "rly-1",
		"relay_secret": "rly_rly-1.s",
		"relay_ws_url": "wss://sync.vome.io/ws/relay",
		"claim_url": "https://vome.io/score/try?k=tok",
		"expires_at": 4_100_000_000,
		"report_id": "r1",
		"remote_url": "https://k7m2xq9p.home.vome.io",
	}
	payload.update(overrides)
	return payload


class TestUnlinkedHouse:
	@pytest.mark.asyncio
	async def test_it_opens_a_guest_run_and_stores_the_url(self):
		entry = _Entry()
		hass = _hass(entry)
		opened = _opened()
		with patch.object(hs, "async_get_clientsession", return_value=MagicMock()), \
				patch.object(hs, "async_request_guest_run", AsyncMock(return_value=opened)), \
				patch.object(hs, "async_instance_id", AsyncMock(return_value="uuid-1")), \
				patch.object(hs, "async_start_relay", AsyncMock()) as start_relay, \
				patch.object(hs, "async_watch_for_report", MagicMock(return_value=None)), \
				patch.object(gr.persistent_notification, "async_create") as notify:
			result = await gr.async_ensure_address(hass, entry)

		assert result["status"] == "opened"
		assert result["remote_url"] == "https://k7m2xq9p.home.vome.io"
		assert result["guest"] is True
		relay = entry.options[CONF_RELAY]
		assert relay[CONF_RELAY_REMOTE_URL] == "https://k7m2xq9p.home.vome.io"
		assert relay[CONF_RELAY_FORWARD_UI] is True
		assert relay[CONF_RELAY_GUEST] is True
		start_relay.assert_awaited_once()
		assert "k7m2xq9p.home.vome.io" in notify.call_args[0][1]
		assert "score/try?k=tok" in notify.call_args[0][1]
		hass.async_create_task.assert_called_once()

	@pytest.mark.asyncio
	async def test_a_second_press_does_not_open_another_run(self):
		entry = _Entry({CONF_RELAY: {
			CONF_RELAY_SERVER_ID: "rly-1", CONF_RELAY_SECRET: "s",
			CONF_RELAY_GUEST: True,
			CONF_RELAY_GUEST_CLAIM_URL: "https://vome.io/score/try?k=tok",
			CONF_RELAY_GUEST_EXPIRES: 4_100_000_000,
			CONF_RELAY_REMOTE_URL: "https://k7m2xq9p.home.vome.io",
			CONF_RELAY_FORWARD_UI: True,
		}})
		hass = _hass(entry)
		with patch.object(hs, "async_request_guest_run", AsyncMock()) as guest, \
				patch.object(gr, "async_request_remote_address", AsyncMock()) as mint, \
				patch.object(gr.persistent_notification, "async_create"):
			result = await gr.async_ensure_address(hass, entry)
		guest.assert_not_awaited()
		mint.assert_not_awaited()
		assert result["status"] == "ready"
		assert result["remote_url"].endswith("k7m2xq9p.home.vome.io")


class TestLinkedHouse:
	@pytest.mark.asyncio
	async def test_it_mints_via_the_agent_endpoint(self):
		"""A device-code link never ran the guest call, but it still gets a URL."""
		entry = _Entry({CONF_RELAY: {
			CONF_RELAY_SERVER_ID: "rly-9", CONF_RELAY_SECRET: "s",
		}})
		hass = _hass(entry)
		with patch.object(hs, "async_get_clientsession", return_value=MagicMock()), \
				patch.object(gr, "async_request_remote_address", AsyncMock(return_value={
					"remote_url": "https://k7m2xq9p.home.vome.io",
					"hosted": False,
				})) as mint, \
				patch.object(gr, "async_start_relay", AsyncMock()) as start_relay, \
				patch.object(gr.persistent_notification, "async_create") as notify:
			result = await gr.async_ensure_address(hass, entry)

		mint.assert_awaited_once()
		assert result["status"] == "ready"
		assert result["remote_url"] == "https://k7m2xq9p.home.vome.io"
		assert entry.options[CONF_RELAY][CONF_RELAY_FORWARD_UI] is True
		start_relay.assert_awaited_once()
		assert "k7m2xq9p.home.vome.io" in notify.call_args[0][1]

	@pytest.mark.asyncio
	async def test_hosted_stores_the_url_without_turning_the_relay_on(self):
		entry = _Entry({CONF_BACKUP: {CONF_BACKUP_SECRET: HOSTED_BACKUP_KEY}})
		hass = _hass(entry)
		with patch.object(hs, "async_get_clientsession", return_value=MagicMock()), \
				patch.object(gr, "async_request_remote_address", AsyncMock(return_value={
					"remote_url": "https://srv-hosted.home.vome.io",
					"hosted": True,
				})), \
				patch.object(gr, "async_start_relay", AsyncMock()) as start_relay, \
				patch.object(gr.persistent_notification, "async_create"):
			result = await gr.async_ensure_address(hass, entry)

		assert result["status"] == "ready"
		assert result["remote_url"] == "https://srv-hosted.home.vome.io"
		assert result["server_id"] == "3d80386f-279a-4388-accb-5d8dd9d1ac71"
		start_relay.assert_not_awaited()
		assert CONF_RELAY_FORWARD_UI not in (entry.options.get(CONF_RELAY) or {})

	@pytest.mark.asyncio
	async def test_an_empty_portal_answer_is_no_address(self):
		entry = _Entry({CONF_RELAY: {
			CONF_RELAY_SERVER_ID: "rly-9", CONF_RELAY_SECRET: "s",
		}})
		with patch.object(hs, "async_get_clientsession", return_value=MagicMock()), \
				patch.object(gr, "async_request_remote_address", AsyncMock(return_value={
					"remote_url": "",
				})):
			result = await gr.async_ensure_address(_hass(entry), entry)
		assert result["status"] == "no_address"
		assert result["remote_url"] == ""


class TestTheFormCopy:
	def test_unlinked_explains_the_submit(self):
		info = gr.step_info(_Entry())
		assert "Submit" in info
		assert "no domain" in info.lower() or "No domain" in info

	def test_a_stored_url_is_shown_in_backticks(self):
		entry = _Entry({CONF_RELAY: {
			CONF_RELAY_SERVER_ID: "rly-1", CONF_RELAY_SECRET: "s",
			CONF_RELAY_GUEST: True,
			CONF_RELAY_REMOTE_URL: "https://k7m2xq9p.home.vome.io",
		}})
		info = gr.step_info(entry)
		assert "`https://k7m2xq9p.home.vome.io`" in info

	def test_linked_without_a_url_still_offers_submit(self):
		entry = _Entry({CONF_RELAY: {
			CONF_RELAY_SERVER_ID: "rly-9", CONF_RELAY_SECRET: "s",
		}})
		info = gr.step_info(entry)
		assert "Submit" in info
		assert "memorable" in info.lower()
