# flake8: noqa
"""Tests for VomeSync integration services."""
import pytest
from unittest.mock import AsyncMock, MagicMock

from custom_components.vomesync.const import DOMAIN
from custom_components.vomesync import _register_services, _get_coordinator_for_service

# Switch services (_register_services) + remote-access services
# (async_register_remote_services). Keep in sync with services.yaml.
EXPECTED_SERVICES = frozenset({
	"create_switch",
	"subscribe_switch",
	"delete_switch",
	"list_switches",
	"forget_switch",
	"get_remote_status",
	"set_forward_ui",
	"set_lan_routes",
	"set_relay_server",
	"add_lan_route",
	"remove_lan_route",
	"mint_lan_tcp_token",
	"set_local_url",
	"set_external_url",
	"set_webhooks",
	"add_webhook",
	"remove_webhook",
	"link_start",
	"link_poll",
	"unlink",
})


def test_get_coordinator_for_service_single_real_entry_no_entry_id(hass, config_entry):
	"""A single real config entry resolves without entry_id.

	hass.data[DOMAIN] also carries several non-coordinator entries —
	"_services_registered" / "_remote_services_registered" markers,
	relay_client's "_relays" registry, services_remote's "_pending_link"
	map — all present on any running instance, so a naive
	len(domain_data) == 1 check always sees 5+ and every single-install
	user calling a service without entry_id would hit "Multiple VomeSync
	entries found". Reproduced live against a real HA instance: an
	earlier fix here that excluded marker keys by name caught only
	"_services_registered" and still failed live, because "_relays" and
	"_remote_services_registered" are set too — hence filtering by type
	(isinstance VomeSyncCoordinator) instead of by name.
	"""
	from custom_components.vomesync.coordinator import VomeSyncCoordinator
	mock_coordinator = MagicMock(spec=VomeSyncCoordinator)
	hass.data = {DOMAIN: {
		config_entry.entry_id: mock_coordinator,
		"_services_registered": True,
		"_remote_services_registered": True,
		"_relays": {config_entry.entry_id: MagicMock()},
		"_pending_link": {},
	}}
	assert _get_coordinator_for_service(hass, None) is mock_coordinator


def test_get_coordinator_for_service_multiple_entries_requires_entry_id():
	from custom_components.vomesync.coordinator import VomeSyncCoordinator
	hass = MagicMock()
	c1 = MagicMock(spec=VomeSyncCoordinator)
	c2 = MagicMock(spec=VomeSyncCoordinator)
	hass.data = {DOMAIN: {
		"entry-1": c1, "entry-2": c2, "_services_registered": True,
	}}
	with pytest.raises(ValueError, match="Multiple VomeSync entries"):
		_get_coordinator_for_service(hass, None)
	assert _get_coordinator_for_service(hass, "entry-2") is c2


@pytest.mark.asyncio
async def test_services_call_coordinator_methods(hass, config_entry):
	"""Services should be registered with expected schemas."""
	mock_coordinator = MagicMock()
	mock_coordinator.create_switch = AsyncMock(return_value="uid-1")
	mock_coordinator.subscribe_to_switch = AsyncMock(return_value=True)
	mock_coordinator.is_switch_owner = MagicMock(return_value=True)
	mock_coordinator.delete_switch = AsyncMock(return_value=True)

	hass.data = {DOMAIN: {config_entry.entry_id: mock_coordinator}}
	hass.services = MagicMock()
	hass.services.async_register = MagicMock()

	_register_services(hass)

	calls = hass.services.async_register.call_args_list
	registered = {c[0][1] for c in calls}
	assert registered == EXPECTED_SERVICES
	assert hass.services.async_register.call_count == len(EXPECTED_SERVICES)

	# Ensure the subscribe service schema only requires uid (no name)
	subscribe = [c for c in calls if c[0][1] == "subscribe_switch"][0]
	schema = subscribe[1]["schema"]
	assert "uid" in schema.schema
	assert "name" not in schema.schema

	# Create switch should support optional theming fields
	create = [c for c in calls if c[0][1] == "create_switch"][0]
	create_schema = create[1]["schema"]
	assert "link" in create_schema.schema
	assert "icon_url" in create_schema.schema
	assert "banner_url" in create_schema.schema
	assert "captcha_token" in create_schema.schema

	# set_relay_server accepts optional ws_url (blank resets to default)
	relay_server = [c for c in calls if c[0][1] == "set_relay_server"][0]
	assert "ws_url" in relay_server[1]["schema"].schema

	link_start = [c for c in calls if c[0][1] == "link_start"][0]
	assert "portal_url" in link_start[1]["schema"].schema
	assert "device_code" in link_start[1]["schema"].schema

	# The panel calls these switch services over REST with ?return_response,
	# so (like get_remote_status/set_forward_ui) they must be ONLY, not
	# OPTIONAL or NONE, or the REST call is rejected with a bare 400.
	from homeassistant.core import SupportsResponse
	for name in ("create_switch", "subscribe_switch", "delete_switch", "list_switches", "forget_switch"):
		reg = [c for c in calls if c[0][1] == name][0]
		assert reg[1]["supports_response"] == SupportsResponse.ONLY


@pytest.mark.asyncio
async def test_switch_services_return_payload_and_guard_errors(hass, config_entry):
	"""Switch service handlers return JSON-safe payloads and _guard errors."""
	from custom_components.vomesync import _register_services

	mock_coordinator = MagicMock()
	mock_coordinator.create_switch = AsyncMock(return_value="uid-1")
	mock_coordinator.subscribe_to_switch = AsyncMock(return_value=True)
	mock_coordinator.is_switch_owner = MagicMock(return_value=True)
	mock_coordinator.delete_switch = AsyncMock(return_value=True)
	mock_coordinator.forget_switch = AsyncMock(return_value=True)
	mock_coordinator.switches = {"uid-1": {"name": "Test", "state": True, "is_owner": True}}
	mock_coordinator.subscriptions = {"uid-2": {"name": "Their switch", "state": False, "is_owner": False}}

	hass.data = {DOMAIN: {config_entry.entry_id: mock_coordinator}}
	hass.services = MagicMock()
	handlers = {}
	def _capture_register(domain, name, handler, **kwargs):
		handlers[name] = handler
	hass.services.async_register = MagicMock(side_effect=_capture_register)

	_register_services(hass)

	eid = config_entry.entry_id

	call = MagicMock(data={"name": "Test", "entry_id": eid})
	result = await handlers["create_switch"](call)
	assert result == {"uid": "uid-1"}

	call = MagicMock(data={"uid": "uid-1", "entry_id": eid})
	result = await handlers["list_switches"](call)
	# Merges owned (coordinator.switches) + subscribed (coordinator.subscriptions).
	assert result == {"switches": {
		"uid-1": {"name": "Test", "state": True, "is_owner": True},
		"uid-2": {"name": "Their switch", "state": False, "is_owner": False},
	}}

	# forget_switch never checks ownership — that's the point (it's the safe,
	# always-available "stop tracking this" action for a subscribed switch).
	call = MagicMock(data={"uid": "uid-2", "entry_id": eid})
	result = await handlers["forget_switch"](call)
	assert result == {"uid": "uid-2"}
	mock_coordinator.forget_switch.assert_awaited_with("uid-2")

	mock_coordinator.forget_switch = AsyncMock(return_value=False)
	call = MagicMock(data={"uid": "unknown-uid", "entry_id": eid})
	result = await handlers["forget_switch"](call)
	assert "error" in result and "Unknown switch" in result["error"]

	# Failure path: create_switch returning falsy uid raises, _guard converts
	# it to {"error": ...} instead of an unhandled exception.
	mock_coordinator.create_switch = AsyncMock(return_value=None)
	call = MagicMock(data={"name": "Bad", "entry_id": eid})
	result = await handlers["create_switch"](call)
	assert "error" in result and "Failed to create switch" in result["error"]

	# is_switch_owner False -> delete_switch guarded error, not a raw raise.
	mock_coordinator.is_switch_owner = MagicMock(return_value=False)
	call = MagicMock(data={"uid": "uid-1", "entry_id": eid})
	result = await handlers["delete_switch"](call)
	assert "error" in result and "owners" in result["error"]


@pytest.mark.asyncio
async def test_remove_config_entry_device_forgets_switch_locally(hass, config_entry):
	"""HA's standard device-delete UI forgets the switch (never the server-side delete)."""
	from custom_components.vomesync import async_remove_config_entry_device
	from custom_components.vomesync.const import DOMAIN as VS_DOMAIN

	mock_coordinator = MagicMock()
	mock_coordinator.forget_switch = AsyncMock(return_value=True)
	hass.data = {DOMAIN: {config_entry.entry_id: mock_coordinator}}

	device_entry = MagicMock()
	device_entry.identifiers = {(VS_DOMAIN, "uid-sub")}

	result = await async_remove_config_entry_device(hass, config_entry, device_entry)

	assert result is True
	mock_coordinator.forget_switch.assert_awaited_with("uid-sub")


@pytest.mark.asyncio
async def test_remove_config_entry_device_ignores_foreign_devices(hass, config_entry):
	"""A device from another integration's identifiers never reaches forget_switch."""
	from custom_components.vomesync import async_remove_config_entry_device

	mock_coordinator = MagicMock()
	mock_coordinator.forget_switch = AsyncMock(return_value=True)
	hass.data = {DOMAIN: {config_entry.entry_id: mock_coordinator}}

	device_entry = MagicMock()
	device_entry.identifiers = {("other_domain", "some-id")}

	result = await async_remove_config_entry_device(hass, config_entry, device_entry)

	assert result is True
	mock_coordinator.forget_switch.assert_not_called()

