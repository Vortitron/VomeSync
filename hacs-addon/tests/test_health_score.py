# flake8: noqa
"""The health score, run from inside Home Assistant before there is an account.

The flow being pinned here is the one a stranger walks:

1. Press the button in a Home Assistant that has never heard of Vome.
2. It gets a temporary link, a queued check, and one URL to open.
3. The report comes back and lives here — on a sensor, with the findings.
4. Sign in from that URL to keep it, or Vome deletes the lot in two hours.

The two things that would be unforgivable to get wrong are both here: a
temporary link must never be presented as a permanent one (step 4 is a
decision, not a formality), and the report must survive on this side when
Vome deletes its copy.
"""
import json
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest

import custom_components.vomesync.health_score as hs
from custom_components.vomesync.const import (
	CONF_RELAY,
	CONF_RELAY_GUEST,
	CONF_RELAY_GUEST_CLAIM_URL,
	CONF_RELAY_GUEST_EXPIRES,
	CONF_RELAY_SECRET,
	CONF_RELAY_SERVER_ID,
	DOMAIN,
)
from custom_components.vomesync.relay_client import (
	async_fetch_health_report,
	async_request_guest_run,
	async_start_health_check,
)


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
	async def test_a_guest_run_is_one_post(self):
		"""No code to read off one screen and type into another."""
		session = _session_returning({
			"server_id": "rly-1", "relay_secret": "rly_rly-1.s",
			"claim_url": "https://vome.io/score/try?k=tok", "expires_at": 123,
		})
		result = await async_request_guest_run(session, "https://vome.io", "My HA")
		assert result["claim_url"].startswith("https://vome.io/score/try?k=")
		args, kwargs = session.post.call_args
		assert args[0] == "https://vome.io/api/v1/relay/guest"
		assert kwargs["json"] == {"name": "My HA", "use_ai": True}

	@pytest.mark.asyncio
	async def test_the_report_is_fetched_with_the_relay_secret(self):
		"""The credential this house already holds — so it can only ever
		read its own report."""
		session = _session_returning({"status": "done", "report": {"score": 71}})
		result = await async_fetch_health_report(session, "https://vome.io", "rly_x.y")
		assert result["report"]["score"] == 71
		args, kwargs = session.request.call_args
		assert args[0] == "GET"
		assert args[1] == "https://vome.io/api/sync/agent/health-report"
		assert kwargs["headers"]["Authorization"] == "Bearer rly_x.y"

	@pytest.mark.asyncio
	async def test_not_yet_is_not_an_error(self):
		"""202 (running) and 404 (never run) are answers, not failures —
		raising on them would turn "not yet" into "broken"."""
		session = _session_returning({"status": "queued", "report": None}, status=202)
		result = await async_fetch_health_report(session, "https://vome.io", "rly_x.y")
		assert result["_status"] == 202
		assert result["report"] is None

	@pytest.mark.asyncio
	async def test_a_refused_credential_does_raise(self):
		session = _session_returning({"error": "nope"}, status=401)
		with pytest.raises(RuntimeError):
			await async_fetch_health_report(session, "https://vome.io", "stale")

	@pytest.mark.asyncio
	async def test_a_linked_house_asks_for_a_check_itself(self):
		session = _session_returning({"status": "queued", "report_id": "r1"}, status=202)
		result = await async_start_health_check(session, "https://vome.io", "rly_x.y")
		assert result["status"] == "queued"
		args, _ = session.request.call_args
		assert args[0] == "POST"
		assert args[1] == "https://vome.io/api/sync/agent/health-check"


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


@pytest.fixture
def linked_entry():
	return _Entry({CONF_RELAY: {
		CONF_RELAY_SERVER_ID: "rly-9", CONF_RELAY_SECRET: "rly_rly-9.s",
	}})


class TestRunningItFromAnUnlinkedHouse:
	@pytest.mark.asyncio
	async def test_it_links_itself_and_hands_over_one_url(self):
		entry = _Entry()
		hass = _hass(entry)
		opened = {
			"server_id": "rly-1", "relay_secret": "rly_rly-1.s",
			"relay_ws_url": "wss://sync.vome.io/ws/relay",
			"claim_url": "https://vome.io/score/try?k=tok",
			"expires_at": 4_100_000_000, "report_id": "r1",
		}
		with patch.object(hs, "async_get_clientsession", return_value=MagicMock()), \
				patch.object(hs, "async_request_guest_run", AsyncMock(return_value=opened)), \
				patch.object(hs, "async_start_relay", AsyncMock()) as start_relay, \
				patch.object(hs.persistent_notification, "async_create") as notify:
			result = await hs.async_run_check(hass, entry)

		assert result["guest"] is True
		assert result["claim_url"] == "https://vome.io/score/try?k=tok"
		# The tunnel has to come up or the queued check has nothing to read.
		start_relay.assert_awaited_once()
		# The link is the flow, so it is put in front of the person.
		assert "score/try?k=tok" in notify.call_args[0][1]

		relay = entry.options[CONF_RELAY]
		assert relay[CONF_RELAY_SERVER_ID] == "rly-1"
		assert relay[CONF_RELAY_GUEST] is True
		assert relay[CONF_RELAY_GUEST_CLAIM_URL].endswith("k=tok")

	@pytest.mark.asyncio
	async def test_a_linked_house_does_not_open_a_second_link(self, linked_entry):
		hass = _hass(linked_entry)
		with patch.object(hs, "async_get_clientsession", return_value=MagicMock()), \
				patch.object(hs, "async_request_guest_run", AsyncMock()) as guest, \
				patch.object(hs, "async_start_health_check",
				             AsyncMock(return_value={"status": "queued"})) as check:
			result = await hs.async_run_check(hass, linked_entry)
		guest.assert_not_awaited()
		check.assert_awaited_once()
		assert result["guest"] is False


class TestTheReportComesHome:
	@pytest.mark.asyncio
	async def test_a_finished_report_is_stored_for_the_sensor(self, linked_entry):
		hass = _hass(linked_entry)
		payload = {"status": "done", "guest": False, "report": {
			"score": 84, "summary": "Mostly well.",
			"findings": [{"title": "Backups are stale", "severity": "medium"}],
		}}
		with patch.object(hs, "async_get_clientsession", return_value=MagicMock()), \
				patch.object(hs, "async_fetch_health_report", AsyncMock(return_value=payload)), \
				patch.object(hs, "async_dispatcher_send") as dispatched:
			report = await hs.async_refresh_report(hass, linked_entry)

		assert report["score"] == 84
		assert hs.stored_report(hass, linked_entry.entry_id)["score"] == 84
		# The sensor repaints without polling.
		dispatched.assert_called_once()

	@pytest.mark.asyncio
	async def test_a_running_check_stores_nothing_yet(self, linked_entry):
		hass = _hass(linked_entry)
		payload = {"status": "queued", "guest": False, "report": None}
		with patch.object(hs, "async_get_clientsession", return_value=MagicMock()), \
				patch.object(hs, "async_fetch_health_report", AsyncMock(return_value=payload)):
			assert await hs.async_refresh_report(hass, linked_entry) is None
		assert hs.stored_report(hass, linked_entry.entry_id) is None

	@pytest.mark.asyncio
	async def test_an_unlinked_house_has_nothing_to_ask(self):
		entry = _Entry()
		assert await hs.async_refresh_report(_hass(entry), entry) is None


class TestTheClockIsHonest:
	"""A temporary link must never read as a permanent one."""

	def _guest_entry(self, expires=4_100_000_000):
		return _Entry({CONF_RELAY: {
			CONF_RELAY_SERVER_ID: "rly-1", CONF_RELAY_SECRET: "rly_rly-1.s",
			CONF_RELAY_GUEST: True, CONF_RELAY_GUEST_EXPIRES: expires,
			CONF_RELAY_GUEST_CLAIM_URL: "https://vome.io/score/try?k=tok",
		}})

	def test_a_guest_link_says_it_is_one(self):
		entry = self._guest_entry()
		assert hs.is_linked(entry) is True
		assert hs.is_guest(entry) is True
		assert hs.claim_url(entry).endswith("k=tok")
		assert hs.guest_seconds_left(entry, now=4_100_000_000 - 600) == 600

	def test_an_expired_clock_reads_zero_not_negative(self):
		entry = self._guest_entry(expires=1_000)
		assert hs.guest_seconds_left(entry, now=9_000) == 0

	@pytest.mark.asyncio
	async def test_being_claimed_takes_the_clock_off_the_same_link(self):
		"""Signing in re-points the server at the real account — the
		credentials keep working, so nothing needs re-linking here."""
		entry = self._guest_entry()
		hass = _hass(entry)
		payload = {"status": "done", "guest": False, "report": {"score": 90}}
		with patch.object(hs, "async_get_clientsession", return_value=MagicMock()), \
				patch.object(hs, "async_fetch_health_report", AsyncMock(return_value=payload)), \
				patch.object(hs.persistent_notification, "async_dismiss") as dismiss:
			await hs.async_refresh_report(hass, entry)

		relay = entry.options[CONF_RELAY]
		assert CONF_RELAY_GUEST not in relay
		assert CONF_RELAY_GUEST_CLAIM_URL not in relay
		# The same secret and server survive: no reconnection, no re-link.
		assert relay[CONF_RELAY_SECRET] == "rly_rly-1.s"
		dismiss.assert_called_once()

	@pytest.mark.asyncio
	async def test_a_deleted_run_takes_its_credentials_with_it(self):
		"""Vome swept the run. The link is dead, so it must not sit here
		looking alive — and the person is told once, plainly."""
		entry = self._guest_entry()
		hass = _hass(entry)
		with patch.object(hs, "async_get_clientsession", return_value=MagicMock()), \
				patch.object(hs, "async_fetch_health_report",
				             AsyncMock(side_effect=RuntimeError("HTTP 401"))), \
				patch.object(hs, "async_start_relay", AsyncMock()) as start_relay, \
				patch.object(hs.persistent_notification, "async_create") as notify:
			assert await hs.async_refresh_report(hass, entry) is None

		relay = entry.options[CONF_RELAY]
		assert CONF_RELAY_SECRET not in relay
		assert CONF_RELAY_GUEST not in relay
		start_relay.assert_awaited_once()  # stops the tunnel
		assert "not saved" in notify.call_args[0][1]

	@pytest.mark.asyncio
	async def test_a_real_link_failing_is_raised_not_swallowed(self, linked_entry):
		"""Only a guest link resolves itself on failure. An account's link
		going bad is a fault to surface, not a link to quietly discard."""
		hass = _hass(linked_entry)
		with patch.object(hs, "async_get_clientsession", return_value=MagicMock()), \
				patch.object(hs, "async_fetch_health_report",
				             AsyncMock(side_effect=RuntimeError("HTTP 500"))):
			with pytest.raises(RuntimeError):
				await hs.async_refresh_report(hass, linked_entry)
		assert linked_entry.options[CONF_RELAY][CONF_RELAY_SECRET] == "rly_rly-9.s"


# ── The entity ──────────────────────────────────────────────────────────────

class TestTheSensorInTheHouse:
	"""The report has to be readable here, or "we delete ours" costs the
	person their report."""

	def _sensor(self, entry, report=None):
		from custom_components.vomesync.sensor import VomeHealthScoreSensor

		hass = _hass(entry)
		if report is not None:
			hass.data = {DOMAIN: {hs.DATA_HEALTH: {entry.entry_id: report}}}
		sensor = VomeHealthScoreSensor(hass, entry)
		return sensor

	def test_no_check_yet_is_unavailable_not_zero(self, linked_entry):
		"""A health score of nothing is not a health score of 0."""
		sensor = self._sensor(linked_entry)
		assert sensor.available is False
		assert sensor.native_value is None

	def test_the_findings_are_on_the_entity(self, linked_entry):
		sensor = self._sensor(linked_entry, {
			"score": 77, "summary": "Mostly well.",
			"findings": [{"title": "Backups are stale", "severity": "medium"}],
			"categories": [{"category": "hygiene"}],
			"generated_at": 1_780_000_000,
		})
		assert sensor.available is True
		assert sensor.native_value == 77
		attributes = sensor.extra_state_attributes
		assert attributes["findings"][0]["title"] == "Backups are stale"
		assert attributes["summary"] == "Mostly well."
		assert attributes["saved_to_account"] is True

	def test_a_guest_score_says_it_is_not_saved(self):
		"""Whoever reads this entity — a card, an automation, a person —
		gets told the number is on a clock."""
		entry = _Entry({CONF_RELAY: {
			CONF_RELAY_SERVER_ID: "rly-1", CONF_RELAY_SECRET: "s",
			CONF_RELAY_GUEST: True, CONF_RELAY_GUEST_EXPIRES: 4_100_000_000,
			CONF_RELAY_GUEST_CLAIM_URL: "https://vome.io/score/try?k=tok",
		}})
		attributes = self._sensor(entry, {"score": 61}).extra_state_attributes
		assert attributes["saved_to_account"] is False
		assert attributes["keep_it_url"].endswith("k=tok")
		assert attributes["deleted_in_seconds"] > 0
