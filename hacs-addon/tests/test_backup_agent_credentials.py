# flake8: noqa
"""Which credential the backup agent presents, and when it exists at all.

A **hosted** VomeHome instance has no relay tunnel to itself, so it holds no
relay secret and never gets one — the portal's only minting path always
creates a new ``rly-`` server. The agent used to require exactly that
credential, so on a hosted instance ``async_get_backup_agents`` returned
nothing and Vome never appeared under Settings → System → Backups, whatever
the customer's plan said.

The backup key is the narrower of the two grants: it reaches that server's
backup storage and nothing else, whereas the relay secret also authenticates
the tunnel that brokers calls into someone's home. So where both exist, the
narrow one has to win — that is the point of having it.
"""
from types import SimpleNamespace

import pytest

from custom_components.vomesync.backup_client import credentials_for_entry
from custom_components.vomesync.const import (
	CONF_BACKUP,
	CONF_BACKUP_SECRET,
	CONF_RELAY,
	CONF_RELAY_SECRET,
	CONF_RELAY_SERVER_ID,
	backup_secret_server_id,
)


BACKUP_KEY = 'vbk_3d80386f-279a-4388-accb-5d8dd9d1ac71.RmFrZVRva2VuVmFsdWU'
RELAY_KEY = 'rly_rly-568e6d697864.QW5vdGhlclRva2Vu'


def _entry(options):
	return SimpleNamespace(options=options, title='VomeSync')


def test_a_hosted_instance_with_a_backup_key_gets_an_agent():
	entry = _entry({CONF_BACKUP: {CONF_BACKUP_SECRET: BACKUP_KEY}})

	server_id, secret = credentials_for_entry(entry)

	assert server_id == '3d80386f-279a-4388-accb-5d8dd9d1ac71'
	assert secret == BACKUP_KEY


def test_a_relay_link_still_works_untouched():
	entry = _entry({CONF_RELAY: {CONF_RELAY_SERVER_ID: 'rly-568e6d697864',
								 CONF_RELAY_SECRET: RELAY_KEY}})

	assert credentials_for_entry(entry) == ('rly-568e6d697864', RELAY_KEY)


def test_the_narrower_credential_wins_when_both_are_present():
	"""Never send the tunnel credential when the backup-only one would do."""
	entry = _entry({
		CONF_BACKUP: {CONF_BACKUP_SECRET: BACKUP_KEY},
		CONF_RELAY: {CONF_RELAY_SERVER_ID: 'rly-568e6d697864',
					 CONF_RELAY_SECRET: RELAY_KEY},
	})

	_server_id, secret = credentials_for_entry(entry)

	assert secret == BACKUP_KEY
	assert secret != RELAY_KEY


def test_an_unconfigured_entry_offers_no_agent():
	"""Better no backup location than one that 401s on every upload."""
	assert credentials_for_entry(_entry({})) == (None, None)
	assert credentials_for_entry(_entry({CONF_BACKUP: {}})) == (None, None)
	# A half-filled relay link is not a credential either.
	assert credentials_for_entry(
		_entry({CONF_RELAY: {CONF_RELAY_SERVER_ID: 'rly-1'}})) == (None, None)


@pytest.mark.parametrize('bad', [
	None, '', 'vbk_', 'vbk_srv-1', 'vbk_srv-1.', 'vbk_.token',
	'rly_srv-1.token', 'srv-1.token', 42,
])
def test_a_malformed_key_yields_no_server_id(bad):
	assert backup_secret_server_id(bad) is None


def test_a_malformed_key_does_not_produce_an_agent():
	"""Parsing failure must not fall through to an agent with an empty id."""
	entry = _entry({CONF_BACKUP: {CONF_BACKUP_SECRET: 'vbk_nonsense'}})

	assert credentials_for_entry(entry) == (None, None)


def test_the_agent_module_selects_entries_with_this_helper():
	"""backup.py cannot be imported without homeassistant.components.backup
	(and therefore securetar), which is exactly why the decision lives in
	backup_client. Pin the wiring by source so the split cannot rot."""
	from pathlib import Path

	source = (Path(__file__).resolve().parents[2]
			  / 'custom_components' / 'vomesync' / 'backup.py').read_text()

	assert 'credentials_for_entry' in source
	assert 'if all(credentials_for_entry(entry))' in source
