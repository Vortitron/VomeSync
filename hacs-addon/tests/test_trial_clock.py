# flake8: noqa
"""Repairs warns before a temporary link ends, and only then."""
from unittest.mock import MagicMock, patch

import pytest

import custom_components.vomesync.trial_clock as tc
from custom_components.vomesync.const import DOMAIN

NOW = 1_800_000_000


def _entry(**relay):
	entry = MagicMock()
	entry.entry_id = "e1"
	entry.options = {"relay": {"server_id": "srv", "secret": "s", **relay}}
	return entry


@pytest.mark.parametrize("secs,words", [
	(12 * 3600, "12 hours"), (2 * 3600 + 1700, "2 hours"), (5400, "2 hours"),
	(5399, "an hour"), (3000, "an hour"), (2999, "49 minutes"), (61, "a minute"), (0, "a minute"),
])
def test_left_phrase(secs, words):
	assert tc.left_phrase(secs) == words


def test_trial_far_from_its_end_is_quiet():
	entry = _entry(agent_trial=True, agent_trial_expires_at=NOW + 2 * 86400)
	assert tc.ending(entry, now=NOW) is None


def test_trial_in_its_last_twelve_hours_warns():
	entry = _entry(agent_trial=True, agent_trial_expires_at=NOW + 11 * 3600)
	assert tc.ending(entry, now=NOW) == (tc.ENDING_TRIAL, 11 * 3600)


def test_trial_already_over_is_left_to_the_refused_link_issue():
	entry = _entry(agent_trial=True, agent_trial_expires_at=NOW - 10)
	assert tc.ending(entry, now=NOW) is None


def test_guest_run_warns_in_its_last_four_hours_only():
	assert tc.ending(_entry(guest=True, guest_expires_at=NOW + 5 * 3600), now=NOW) is None
	assert tc.ending(_entry(guest=True, guest_expires_at=NOW + 3600), now=NOW) == (tc.ENDING_GUEST, 3600)


def test_a_kept_link_has_no_clock():
	assert tc.ending(_entry(), now=NOW) is None


def test_evaluate_raises_without_the_claim_url():
	entry = _entry(guest=True, guest_expires_at=NOW + 3600,
		guest_claim_url="https://vome.io/claim/ONE-TIME-CODE")
	hass = MagicMock()
	with patch("homeassistant.helpers.issue_registry.async_create_issue") as create, \
		patch("homeassistant.helpers.issue_registry.async_delete_issue") as delete:
		assert tc.evaluate(hass, entry, now=NOW) == tc.ENDING_GUEST
	delete.assert_not_called()
	args, kwargs = create.call_args
	assert args[1:3] == (DOMAIN, tc.issue_id("e1"))
	assert kwargs["translation_placeholders"] == {"left": "an hour"}
	assert "ONE-TIME-CODE" not in repr(create.call_args)


def test_evaluate_withdraws_when_nothing_is_due():
	hass = MagicMock()
	with patch("homeassistant.helpers.issue_registry.async_create_issue") as create, \
		patch("homeassistant.helpers.issue_registry.async_delete_issue") as delete:
		assert tc.evaluate(hass, _entry(), now=NOW) is None
	create.assert_not_called()
	delete.assert_called_once_with(hass, DOMAIN, tc.issue_id("e1"))


def test_evaluate_never_raises():
	hass = MagicMock()
	entry = _entry(agent_trial=True, agent_trial_expires_at=NOW + 60)
	with patch("homeassistant.helpers.issue_registry.async_create_issue", side_effect=RuntimeError("x")):
		assert tc.evaluate(hass, entry, now=NOW) is None


def test_issue_texts_exist_for_every_key():
	import json
	from pathlib import Path

	strings = json.loads((Path(tc.__file__).parent / "translations" / "en.json").read_text())
	for key in (tc.ENDING_TRIAL, tc.ENDING_GUEST):
		assert "{left}" in strings["issues"][key]["title"]
		assert "{left}" in strings["issues"][key]["description"]
