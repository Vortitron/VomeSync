# flake8: noqa
"""Tests for the config-entry options redactor.

Regression guard: the relay secret (and optional local HA token) must never be
logged verbatim from ``async_setup_entry`` — they used to be emitted at INFO in
the full ``entry.options`` dict, leaking a live credential into
``home-assistant.log``.
"""
from custom_components.vomesync import _redacted_options


def test_redacts_relay_secret_and_local_token():
	opts = {
		"relay": {
			"server_id": "rly-abc",
			"secret": "SUPER-SECRET-VALUE",
			"ws_url": "wss://sync.vome.io/ws/relay",
			"local_token": "llt_deadbeef",
		},
		"some_flag": True,
	}
	red = _redacted_options(opts)
	# Secrets masked...
	assert red["relay"]["secret"] == "***"
	assert red["relay"]["local_token"] == "***"
	# ...but structure/non-secrets preserved for diagnostics.
	assert red["relay"]["server_id"] == "rly-abc"
	assert red["relay"]["ws_url"] == "wss://sync.vome.io/ws/relay"
	assert red["some_flag"] is True
	# The original object is untouched (shallow-copy semantics).
	assert opts["relay"]["secret"] == "SUPER-SECRET-VALUE"


def test_empty_secret_is_not_masked_to_a_fake_value():
	# An empty/absent secret should stay falsy, not become "***".
	red = _redacted_options({"relay": {"server_id": "x", "secret": ""}})
	assert red["relay"]["secret"] == ""


def test_handles_none_and_empty():
	assert _redacted_options(None) == {}
	assert _redacted_options({}) == {}


def test_top_level_token_key_is_masked():
	red = _redacted_options({"token": "abc123", "password": "hunter2"})
	assert red["token"] == "***"
	assert red["password"] == "***"
