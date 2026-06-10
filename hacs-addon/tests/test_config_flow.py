# flake8: noqa
"""Tests for VomeSync config and options flows."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.data_entry_flow import FlowResultType

from custom_components.vomesync.config_flow import (
	VomeSyncConfigFlow,
	VomeSyncOptionsFlow,
)
from custom_components.vomesync.const import (
	CONF_SERVER_URL,
	CONF_WEBSOCKET_URL,
	CONF_GENERATE_NEW_KEY,
	CONF_USE_DEFAULT_URLS,
	CONF_SWITCH_NAME,
	CONF_SWITCH_DESCRIPTION,
	CONF_SWITCH_LOCATION,
	CONF_SWITCH_CATEGORY,
	CONF_SWITCH_PUBLICIZE,
	CONF_SWITCH_LINK,
	CONF_SWITCH_ICON_URL,
	CONF_SWITCH_BANNER_URL,
	CONF_CAPTCHA_TOKEN,
	CONF_SWITCH_UID,
	CONF_ACCESS_KEY,
	CONF_SWITCH_ADVANCED,
	CONF_SHOW_SIGNING_KEY_AFTER,
	CONF_PERSONAL_KEY,
	DOMAIN,
	FREE_TIER_MAX_SUBSCRIPTIONS,
)
from flow_test_framework import MockHASSFactory


@pytest.mark.asyncio
async def test_config_flow_derives_websocket_url(hass):
	"""Test WebSocket URL auto-derives from server URL."""
	flow = VomeSyncConfigFlow()
	flow.hass = hass

	# Test HTTP -> WS
	result = await flow.async_step_user({
		CONF_USE_DEFAULT_URLS: False,
		CONF_GENERATE_NEW_KEY: True,
		CONF_SERVER_URL: "http://example.com:3000",
	})

	assert result["type"] == FlowResultType.CREATE_ENTRY
	assert flow._websocket_url == "ws://example.com:3000/ws"

	# Test HTTPS -> WSS
	flow2 = VomeSyncConfigFlow()
	flow2.hass = hass
	result2 = await flow2.async_step_user({
		CONF_USE_DEFAULT_URLS: False,
		CONF_GENERATE_NEW_KEY: True,
		CONF_SERVER_URL: "https://secure.example.com",
	})

	assert result2["type"] == FlowResultType.CREATE_ENTRY
	assert flow2._websocket_url == "wss://secure.example.com/ws"


@pytest.mark.asyncio
async def test_config_flow_with_existing_personal_key(hass):
	"""Test config flow creates a keypair entry."""
	flow = VomeSyncConfigFlow()
	flow.hass = hass

	result = await flow.async_step_user({
		CONF_USE_DEFAULT_URLS: False,
		CONF_GENERATE_NEW_KEY: True,
		CONF_SERVER_URL: "https://test.com",
	})

	assert result["type"] == FlowResultType.CREATE_ENTRY
	assert result["data"][CONF_SERVER_URL] == "https://test.com"


@pytest.mark.asyncio
async def test_config_flow_accepts_initial_switch_uid(hass):
	"""Test optional switch UID on initial setup."""
	flow = VomeSyncConfigFlow()
	flow.hass = hass

	with patch(
		"custom_components.vomesync.config_flow.VomeSyncAPIClient.get_switch_status",
		new=AsyncMock(return_value={"uid": "vs_test123"}),
	), patch(
		"custom_components.vomesync.config_flow.VomeSyncAPIClient.close",
		new=AsyncMock(),
	):
		result = await flow.async_step_user({
			CONF_USE_DEFAULT_URLS: False,
			CONF_GENERATE_NEW_KEY: True,
			CONF_SERVER_URL: "https://test.com",
			CONF_SWITCH_UID: "vs_test123",
		})

	assert result["type"] == FlowResultType.CREATE_ENTRY
	assert result["data"][CONF_SWITCH_UID] == "vs_test123"


@pytest.mark.asyncio
async def test_options_flow_import_switches(hass, config_entry):
	"""Test import switches flow."""
	# Mock coordinator with switches
	mock_coordinator = MagicMock()
	mock_coordinator.switches = {
		"uid-1": {"name": "Switch 1", "description": "Test 1", "is_owner": True},
		"uid-2": {"name": "Switch 2", "description": "Test 2", "is_owner": True},
	}
	mock_coordinator.subscriptions = {
		"uid-3": {"name": "Sub 1", "description": "Test sub", "is_owner": False},
	}

	hass.data = {DOMAIN: {config_entry.entry_id: mock_coordinator}}
	hass.config_entries = MagicMock()

	flow = VomeSyncOptionsFlow(config_entry)
	flow.hass = hass

	# Show import form
	result = await flow.async_step_import_switches(None)
	assert result["type"] == FlowResultType.FORM
	assert "switches" in result["data_schema"].schema

	# Select switches to import
	with patch.object(hass.config_entries, "async_reload", new=AsyncMock()):
		result = await flow.async_step_import_switches({
			"switches": ["uid-1", "uid-3"]
		})

	assert result["type"] == FlowResultType.CREATE_ENTRY
	options = result["data"]
	assert "imported_switches" in options
	assert len(options["imported_switches"]) == 2
	assert "uid-1" in options["imported_switches"]
	assert "uid-3" in options["imported_switches"]


@pytest.mark.asyncio
async def test_options_flow_create_switch_auto_imports(hass, config_entry):
	"""Test creating a switch automatically imports it."""
	config_entry.options = {**(config_entry.options or {}), "signing_key_backup_confirmed": True}
	mock_coordinator = MagicMock()
	mock_coordinator.create_switch = AsyncMock(return_value="new-uid-123")
	mock_coordinator.switches = {}

	hass.data = {DOMAIN: {config_entry.entry_id: mock_coordinator}}
	hass.config_entries = MagicMock()

	flow = VomeSyncOptionsFlow(config_entry)
	flow.hass = hass

	result = await flow.async_step_create_switch({
		CONF_SWITCH_NAME: "My New Switch",
		CONF_SWITCH_DESCRIPTION: "Test switch",
		CONF_SWITCH_LOCATION: "Test City",
		CONF_SWITCH_CATEGORY: "Home",
		CONF_SWITCH_PUBLICIZE: False,
		CONF_SWITCH_ADVANCED: False,
	})

	assert result["type"] == FlowResultType.MENU
	assert result["step_id"] == "manage_switch_action"
	mock_coordinator.create_switch.assert_called_once()


@pytest.mark.asyncio
async def test_options_flow_create_switch_show_signing_key_after(hass, config_entry):
	"""Create-switch should show signing key after submit when requested."""
	config_entry.options = {**(config_entry.options or {}), "signing_key_backup_confirmed": True}
	config_entry.data = {
		**(config_entry.data or {}),
		"auth_mode": "crypto",
		"crypto_seed": "test-seed",
	}

	mock_coordinator = MagicMock()
	mock_coordinator.create_switch = AsyncMock(return_value="new-uid-123")
	mock_coordinator.switches = {}

	hass.data = {DOMAIN: {config_entry.entry_id: mock_coordinator}}
	hass.config_entries = MagicMock()

	flow = VomeSyncOptionsFlow(config_entry)
	flow.hass = hass

	result = await flow.async_step_create_switch({
		CONF_SWITCH_NAME: "My New Switch",
		CONF_SWITCH_DESCRIPTION: "Test switch",
		CONF_SWITCH_LOCATION: "Test City",
		CONF_SWITCH_CATEGORY: "Home",
		CONF_SWITCH_PUBLICIZE: False,
		CONF_SWITCH_ADVANCED: False,
		CONF_SHOW_SIGNING_KEY_AFTER: True,
	})

	assert result["type"] == FlowResultType.FORM
	assert result["step_id"] == "post_create_signing_key"
	mock_coordinator.create_switch.assert_called_once()

	result = await flow.async_step_post_create_signing_key({})
	assert result["type"] == FlowResultType.MENU
	assert result["step_id"] == "manage_switch_action"


@pytest.mark.asyncio
async def test_options_flow_create_switch_schema_has_advanced_toggle(hass, config_entry):
	"""Create-switch form should expose advanced toggle only."""
	config_entry.options = {**(config_entry.options or {}), "signing_key_backup_confirmed": True}
	flow = VomeSyncOptionsFlow(config_entry)
	flow.hass = hass

	result = await flow.async_step_create_switch(None)
	assert result["type"] == FlowResultType.FORM
	schema = result["data_schema"].schema
	assert CONF_SWITCH_ADVANCED in schema
	assert CONF_SWITCH_LINK not in schema
	assert CONF_SWITCH_ICON_URL not in schema
	assert CONF_SWITCH_BANNER_URL not in schema
	assert CONF_CAPTCHA_TOKEN not in schema


@pytest.mark.asyncio
async def test_options_flow_create_switch_fetches_name_from_server(hass, config_entry):
	"""Create-switch form should fetch a globally unique name from the server."""
	config_entry.options = {**(config_entry.options or {}), "signing_key_backup_confirmed": True}
	mock_api = MagicMock()
	mock_api.get_next_switch_name = AsyncMock(return_value="VomeSync Čuovga")

	mock_coordinator = MagicMock()
	mock_coordinator.api_client = mock_api

	hass.data = {DOMAIN: {config_entry.entry_id: mock_coordinator}}

	flow = VomeSyncOptionsFlow(config_entry)
	flow.hass = hass

	result = await flow.async_step_create_switch(None)
	assert result["type"] == FlowResultType.FORM
	schema = result["data_schema"].schema

	# Find the default value for the name field
	name_key = None
	for key in schema:
		if hasattr(key, "schema") and key.schema == CONF_SWITCH_NAME:
			name_key = key
			break

	assert name_key is not None
	assert name_key.default is not None
	default_name = name_key.default() if callable(name_key.default) else name_key.default
	assert default_name == "VomeSync Čuovga"
	mock_api.get_next_switch_name.assert_called_once()


@pytest.mark.asyncio
async def test_options_flow_create_switch_falls_back_to_numbered_name(hass, config_entry):
	"""Create-switch form should fall back to numbered name if server fails."""
	config_entry.options = {**(config_entry.options or {}), "signing_key_backup_confirmed": True}
	flow = VomeSyncOptionsFlow(config_entry)
	flow.hass = hass
	# No coordinator in hass.data means server call fails

	result = await flow.async_step_create_switch(None)
	assert result["type"] == FlowResultType.FORM
	schema = result["data_schema"].schema

	# Find the default value for the name field
	name_key = None
	for key in schema:
		if hasattr(key, "schema") and key.schema == CONF_SWITCH_NAME:
			name_key = key
			break

	assert name_key is not None
	assert name_key.default is not None
	default_name = name_key.default() if callable(name_key.default) else name_key.default
	# Should be a numbered fallback like "Vome Switch 1"
	assert default_name.startswith("Vome Switch ")


@pytest.mark.asyncio
async def test_options_flow_create_switch_advanced_schema_includes_theming_fields(hass, config_entry):
	"""Advanced create-switch step should expose link/icon/banner fields."""
	config_entry.options = {**(config_entry.options or {}), "signing_key_backup_confirmed": True}
	flow = VomeSyncOptionsFlow(config_entry)
	flow.hass = hass

	result = await flow.async_step_create_switch({
		CONF_SWITCH_NAME: "My New Switch",
		CONF_SWITCH_DESCRIPTION: "",
		CONF_SWITCH_LOCATION: "",
		CONF_SWITCH_CATEGORY: "Other",
		CONF_SWITCH_PUBLICIZE: False,
		CONF_SWITCH_ADVANCED: True,
	})
	assert result["type"] == FlowResultType.FORM
	assert result["step_id"] == "create_switch_advanced"
	schema = result["data_schema"].schema
	assert CONF_SWITCH_LINK in schema
	assert CONF_SWITCH_ICON_URL in schema
	assert CONF_SWITCH_BANNER_URL in schema
	assert CONF_CAPTCHA_TOKEN in schema


@pytest.mark.asyncio
async def test_options_flow_create_switch_requires_backup_confirmation(hass, config_entry):
	"""Create-switch should require signing key backup confirmation once."""
	config_entry.data = {
		**(config_entry.data or {}),
		"auth_mode": "crypto",
		"crypto_seed": "test-seed",
	}
	hass.config_entries = MagicMock()
	def _update_entry(entry, options=None):
		entry.options = options or {}
	hass.config_entries.async_update_entry = MagicMock(side_effect=_update_entry)

	flow = VomeSyncOptionsFlow(config_entry)
	flow.hass = hass

	result = await flow.async_step_create_switch(None)
	assert result["type"] == FlowResultType.MENU
	assert result["step_id"] == "confirm_backup_signing_key"

	result = await flow.async_step_reveal_signing_key(None)
	assert result["type"] == FlowResultType.FORM
	assert result["step_id"] == "reveal_signing_key"

	result = await flow.async_step_reveal_signing_key({"confirmed": True})
	assert result["type"] == FlowResultType.FORM
	assert result["step_id"] == "create_switch"


@pytest.mark.asyncio
async def test_options_flow_subscribe_switch_auto_imports(hass, config_entry):
	"""Test subscribing to a switch automatically imports it."""
	mock_coordinator = MagicMock()
	mock_coordinator.subscribe_to_switch = AsyncMock(return_value=True)
	mock_coordinator.subscriptions = {}

	hass.data = {DOMAIN: {config_entry.entry_id: mock_coordinator}}
	hass.config_entries = MagicMock()

	flow = VomeSyncOptionsFlow(config_entry)
	flow.hass = hass

	result = await flow.async_step_subscribe_switch({
		CONF_SWITCH_UID: "remote-uid-456",
		CONF_ACCESS_KEY: "access-123",
	})

	assert result["type"] == FlowResultType.CREATE_ENTRY
	mock_coordinator.subscribe_to_switch.assert_called_once_with("remote-uid-456", access_key="access-123")


@pytest.mark.asyncio
async def test_options_flow_subscribe_switch_limit_reached(hass, config_entry):
	"""Test subscription limit blocks additional subscriptions."""
	imported_switches = {}
	for idx in range(FREE_TIER_MAX_SUBSCRIPTIONS):
		imported_switches[f"uid-{idx}"] = {
			"name": f"Switch {idx}",
			"is_owner": False,
			"cached_data": {}
		}
	config_entry.options = {
		"imported_switches": imported_switches
	}
	mock_coordinator = MagicMock()
	hass.data = {DOMAIN: {config_entry.entry_id: mock_coordinator}}

	flow = VomeSyncOptionsFlow(config_entry)
	flow.hass = hass

	result = await flow.async_step_subscribe_switch({
		CONF_SWITCH_UID: "remote-uid-999",
		CONF_ACCESS_KEY: ""
	})

	assert result["type"] == FlowResultType.FORM
	assert result["errors"]["base"] == "subscription_limit_reached"


@pytest.mark.asyncio
async def test_options_flow_remove_from_installation(hass, config_entry):
	"""Test removing switch from installation (not deleting from server)."""
	config_entry.options = {
		"imported_switches": {
			"uid-to-remove": {
				"name": "Switch to Remove",
				"is_owner": False,
				"cached_data": {}
			}
		}
	}

	hass.config_entries = MagicMock()
	hass.data = {DOMAIN: {config_entry.entry_id: MagicMock()}}

	flow = VomeSyncOptionsFlow(config_entry)
	flow.hass = hass
	flow._step_data = {
		"selected_uid": "uid-to-remove",
		"selected_name": "Switch to Remove",
	}

	# Show confirmation form
	result = await flow.async_step_remove_from_installation(None)
	assert result["type"] == FlowResultType.FORM
	assert "confirm" in result["data_schema"].schema

	# Confirm removal
	with patch.object(hass.config_entries, "async_reload", new=AsyncMock()):
		result = await flow.async_step_remove_from_installation({"confirm": True})

	assert result["type"] == FlowResultType.CREATE_ENTRY
	options = result["data"]
	assert "uid-to-remove" not in options["imported_switches"]


@pytest.mark.asyncio
async def test_options_flow_manage_switch_action_shows_entity_id(hass, config_entry):
	"""Manage switch actions should show resolved entity_id in description placeholders."""
	config_entry.options = {
		"imported_switches": {
			"uid-abc": {
				"name": "My Switch",
				"is_owner": True,
				"cached_data": {}
			}
		}
	}

	mock_entity_reg = MagicMock()
	mock_entity_reg.async_get_entity_id.return_value = "switch.vomesync_my_switch"

	hass.data = {DOMAIN: {config_entry.entry_id: MagicMock()}}
	hass.config_entries = MagicMock()

	flow = VomeSyncOptionsFlow(config_entry)
	flow.hass = hass

	with patch("custom_components.vomesync.config_flow.er.async_get", return_value=mock_entity_reg):
		result = await flow.async_step_manage_switches({"switch": "uid-abc"})

	assert result["type"] == FlowResultType.MENU
	assert result["step_id"] == "manage_switch_action"
	assert result["description_placeholders"]["name"] == "My Switch"
	assert result["description_placeholders"]["entity_id"] == "switch.vomesync_my_switch"


@pytest.mark.asyncio
async def test_options_flow_manage_switch_action_includes_v2_access_keys(hass, config_entry):
	"""Owned v2 switches in crypto mode should show the access-keys menu option."""
	config_entry.data = {
		**(config_entry.data or {}),
		"auth_mode": "crypto",
		"crypto_seed": "test-seed",
	}
	config_entry.options = {
		"imported_switches": {
			"vs_test_uid": {
				"name": "My v2 Switch",
				"is_owner": True,
				"cached_data": {}
			}
		}
	}

	mock_entity_reg = MagicMock()
	mock_entity_reg.async_get_entity_id.return_value = "switch.vomesync_my_switch"

	hass.data = {DOMAIN: {config_entry.entry_id: MagicMock()}}
	hass.config_entries = MagicMock()

	flow = VomeSyncOptionsFlow(config_entry)
	flow.hass = hass

	with patch("custom_components.vomesync.config_flow.er.async_get", return_value=mock_entity_reg):
		result = await flow.async_step_manage_switches({"switch": "vs_test_uid"})

	assert result["type"] == FlowResultType.MENU
	assert "access_keys" in result["menu_options"]
	assert "manage_on_website" in result["menu_options"]


@pytest.mark.asyncio
async def test_options_flow_access_keys_menu(hass, config_entry):
	"""Access-keys step should list existing keys and offer create option."""
	config_entry.data = {
		**(config_entry.data or {}),
		"auth_mode": "crypto",
		"crypto_seed": "test-seed",
	}
	mock_coordinator = MagicMock()
	mock_coordinator.list_v2_access_keys = AsyncMock(return_value={
		"keys": [{"keyId": "a" * 64, "name": "Friend", "permissions": ["toggle"], "paused": False}],
		"count": 1
	})
	hass.data = {DOMAIN: {config_entry.entry_id: mock_coordinator}}

	flow = VomeSyncOptionsFlow(config_entry)
	flow.hass = hass
	flow._step_data = {
		"selected_uid": "vs_test_uid",
		"is_owner": True,
	}

	result = await flow.async_step_access_keys(None)
	assert result["type"] == FlowResultType.FORM
	assert result["step_id"] == "access_keys"
	# The dropdown should contain the key and a create option
	schema_keys = list(result["data_schema"].schema.keys())
	assert any("selected_key" in str(k) for k in schema_keys)


@pytest.mark.asyncio
async def test_options_flow_manage_on_website_creates_link(hass, config_entry):
	"""Manage-on-website should create a metadata key and return a fragment-based URL."""
	config_entry.data = {
		**(config_entry.data or {}),
		"auth_mode": "crypto",
		"crypto_seed": "test-seed",
		"server_url": "https://test-server.com",
	}

	mock_coordinator = MagicMock()
	mock_coordinator.create_v2_access_key = AsyncMock(return_value={"apiKey": "key-123"})
	hass.data = {DOMAIN: {config_entry.entry_id: mock_coordinator}}

	flow = VomeSyncOptionsFlow(config_entry)
	flow.hass = hass
	flow._step_data = {
		"selected_uid": "vs_test_uid",
		"selected_name": "My v2 Switch",
		"is_owner": True,
	}

	result = await flow.async_step_manage_on_website(None)
	assert result["type"] == FlowResultType.FORM
	assert result["step_id"] == "manage_on_website"
	assert "regenerate" in result["data_schema"].schema
	assert "stay_logged_in" in result["data_schema"].schema
	info = result["description_placeholders"]["info"]
	assert "https://test-server.com/switch/vs_test_uid" in info
	assert "#accessKey=" in info
	mock_coordinator.create_v2_access_key.assert_called_once()
	_, kwargs = mock_coordinator.create_v2_access_key.call_args
	assert kwargs.get("ttl_seconds") == 4 * 60 * 60


@pytest.mark.asyncio
async def test_options_flow_manage_on_website_submit_returns_menu(hass, config_entry):
	"""Submitting manage-on-website should return to the switch action menu."""
	config_entry.data = {
		**(config_entry.data or {}),
		"auth_mode": "crypto",
		"crypto_seed": "test-seed",
		"server_url": "https://test-server.com",
	}

	mock_coordinator = MagicMock()
	mock_coordinator.create_v2_access_key = AsyncMock(return_value={"apiKey": "key-123"})
	hass.data = {DOMAIN: {config_entry.entry_id: mock_coordinator}}

	flow = VomeSyncOptionsFlow(config_entry)
	flow.hass = hass
	flow._step_data = {
		"selected_uid": "vs_test_uid",
		"selected_name": "My v2 Switch",
		"is_owner": True,
	}

	# Show step (creates key)
	await flow.async_step_manage_on_website(None)

	# Submit step (revokes key + returns to menu)
	result = await flow.async_step_manage_on_website({"regenerate": False})
	assert result["type"] == FlowResultType.MENU
	assert result["step_id"] == "manage_switch_action"


@pytest.mark.asyncio
async def test_options_flow_manage_on_website_stay_logged_in_uses_long_ttl(hass, config_entry):
	"""Stay logged in should create a longer-lived key and add remember params."""
	config_entry.data = {
		**(config_entry.data or {}),
		"auth_mode": "crypto",
		"crypto_seed": "test-seed",
		"server_url": "https://test-server.com",
	}

	mock_coordinator = MagicMock()
	mock_coordinator.create_v2_access_key = AsyncMock(return_value={"apiKey": "key-123"})
	hass.data = {DOMAIN: {config_entry.entry_id: mock_coordinator}}

	flow = VomeSyncOptionsFlow(config_entry)
	flow.hass = hass
	flow._step_data = {
		"selected_uid": "vs_test_uid",
		"selected_name": "My v2 Switch",
		"is_owner": True,
	}

	# Initial key (default ttl)
	await flow.async_step_manage_on_website(None)

	# Regenerate with stay logged in
	result = await flow.async_step_manage_on_website({
		"regenerate": True,
		"stay_logged_in": True,
	})
	assert result["type"] == FlowResultType.FORM
	info = result["description_placeholders"]["info"]
	assert "remember=1" in info
	assert "ttlSeconds=" in info
	_, kwargs = mock_coordinator.create_v2_access_key.call_args
	assert kwargs.get("ttl_seconds") == 30 * 24 * 60 * 60


@pytest.mark.asyncio
async def test_options_flow_create_access_key_v2(hass, config_entry):
	"""Creating a v2 access key should call the coordinator and show the created key."""
	config_entry.data = {
		**(config_entry.data or {}),
		"auth_mode": "crypto",
		"crypto_seed": "test-seed",
	}
	mock_coordinator = MagicMock()
	mock_coordinator.create_v2_access_key = AsyncMock(return_value={"apiKey": "key-123", "permissions": ["toggle"]})
	hass.data = {DOMAIN: {config_entry.entry_id: mock_coordinator}}

	flow = VomeSyncOptionsFlow(config_entry)
	flow.hass = hass
	flow._step_data = {
		"selected_uid": "vs_test_uid",
		"is_owner": True,
	}

	result = await flow.async_step_create_access_key_v2({"name": "Friend", "permissions": ["toggle", "comment"]})
	assert result["type"] == FlowResultType.FORM
	assert result["step_id"] == "create_access_key_v2_success"
	mock_coordinator.create_v2_access_key.assert_called_once_with("vs_test_uid", name="Friend", permissions=["toggle", "comment"])


@pytest.mark.asyncio
async def test_options_flow_access_key_detail(hass, config_entry):
	"""Selecting a key from the list should show its detail page with actions."""
	config_entry.data = {
		**(config_entry.data or {}),
		"auth_mode": "crypto",
		"crypto_seed": "test-seed",
	}
	mock_coordinator = MagicMock()
	mock_coordinator.list_v2_access_keys = AsyncMock(return_value={
		"keys": [{"keyId": "a" * 64, "name": "Friend", "permissions": ["toggle"], "paused": False, "created": 1700000000000, "lastUsed": 1700001000000}],
		"count": 1
	})
	hass.data = {DOMAIN: {config_entry.entry_id: mock_coordinator}}

	flow = VomeSyncOptionsFlow(config_entry)
	flow.hass = hass
	flow._step_data = {
		"selected_uid": "vs_test_uid",
		"is_owner": True,
		"selected_key_id": "a" * 64,
	}

	result = await flow.async_step_access_key_detail(None)
	assert result["type"] == FlowResultType.FORM
	assert result["step_id"] == "access_key_detail"
	assert "Friend" in result["description_placeholders"]["info"]
	assert "Active" in result["description_placeholders"]["info"]


@pytest.mark.asyncio
async def test_options_flow_revoke_access_key_v2(hass, config_entry):
	"""Revoking a v2 access key should call the coordinator and show success."""
	config_entry.data = {
		**(config_entry.data or {}),
		"auth_mode": "crypto",
		"crypto_seed": "test-seed",
	}
	key_id = "a" * 64
	mock_coordinator = MagicMock()
	mock_coordinator.list_v2_access_keys = AsyncMock(return_value={
		"keys": [{"keyId": key_id, "name": "Friend", "permissions": ["toggle"], "paused": False}],
		"count": 1
	})
	mock_coordinator.revoke_v2_access_key = AsyncMock(return_value=True)
	hass.data = {DOMAIN: {config_entry.entry_id: mock_coordinator}}

	flow = VomeSyncOptionsFlow(config_entry)
	flow.hass = hass
	flow._step_data = {
		"selected_uid": "vs_test_uid",
		"is_owner": True,
		"selected_key_id": key_id,
	}

	result = await flow.async_step_revoke_access_key_v2({"confirm": True})
	assert result["type"] == FlowResultType.FORM
	assert result["step_id"] == "revoke_access_key_v2_success"
	mock_coordinator.revoke_v2_access_key.assert_called_once_with("vs_test_uid", key_id)


@pytest.mark.asyncio
async def test_options_flow_subscribe_composite_uid_key(hass, config_entry):
	"""Subscribe with a uid/key composite should parse and pass both."""
	mock_coordinator = MagicMock()
	mock_coordinator.subscribe_to_switch = AsyncMock(return_value=True)
	mock_coordinator.subscriptions = {}

	hass.data = {DOMAIN: {config_entry.entry_id: mock_coordinator}}
	hass.config_entries = MagicMock()

	flow = VomeSyncOptionsFlow(config_entry)
	flow.hass = hass

	result = await flow.async_step_subscribe_switch({
		CONF_SWITCH_UID: "vs_abc123/secret-key-456",
		CONF_ACCESS_KEY: "",
	})

	assert result["type"] == FlowResultType.CREATE_ENTRY
	mock_coordinator.subscribe_to_switch.assert_called_once_with(
		"vs_abc123", access_key="secret-key-456"
	)


@pytest.mark.asyncio
async def test_options_flow_subscribe_composite_explicit_key_wins(hass, config_entry):
	"""When explicit access_key field is filled, it should override composite key."""
	mock_coordinator = MagicMock()
	mock_coordinator.subscribe_to_switch = AsyncMock(return_value=True)
	mock_coordinator.subscriptions = {}

	hass.data = {DOMAIN: {config_entry.entry_id: mock_coordinator}}
	hass.config_entries = MagicMock()

	flow = VomeSyncOptionsFlow(config_entry)
	flow.hass = hass

	# Composite has a key, but explicit field also has one – composite wins
	# because the code does: composite_key or explicit_key
	result = await flow.async_step_subscribe_switch({
		CONF_SWITCH_UID: "vs_abc123/composite-key",
		CONF_ACCESS_KEY: "explicit-key",
	})

	assert result["type"] == FlowResultType.CREATE_ENTRY
	mock_coordinator.subscribe_to_switch.assert_called_once_with(
		"vs_abc123", access_key="composite-key"
	)


@pytest.mark.asyncio
async def test_config_flow_composite_uid_key_initial_setup(hass):
	"""Test initial setup with a uid/key composite stores both uid and access key."""
	flow = VomeSyncConfigFlow()
	flow.hass = hass

	with patch(
		"custom_components.vomesync.config_flow.VomeSyncAPIClient.get_switch_status",
		new=AsyncMock(return_value={"uid": "vs_test123"}),
	), patch(
		"custom_components.vomesync.config_flow.VomeSyncAPIClient.close",
		new=AsyncMock(),
	):
		result = await flow.async_step_user({
			CONF_USE_DEFAULT_URLS: False,
			CONF_GENERATE_NEW_KEY: True,
			CONF_SERVER_URL: "https://test.com",
			CONF_SWITCH_UID: "vs_test123/my-access-key",
		})

	assert result["type"] == FlowResultType.CREATE_ENTRY
	assert result["data"][CONF_SWITCH_UID] == "vs_test123"
	assert result["data"]["initial_access_key"] == "my-access-key"


@pytest.mark.asyncio
async def test_config_flow_plain_uid_no_access_key(hass):
	"""Test initial setup with plain UID does not set access key."""
	flow = VomeSyncConfigFlow()
	flow.hass = hass

	with patch(
		"custom_components.vomesync.config_flow.VomeSyncAPIClient.get_switch_status",
		new=AsyncMock(return_value={"uid": "vs_test123"}),
	), patch(
		"custom_components.vomesync.config_flow.VomeSyncAPIClient.close",
		new=AsyncMock(),
	):
		result = await flow.async_step_user({
			CONF_USE_DEFAULT_URLS: False,
			CONF_GENERATE_NEW_KEY: True,
			CONF_SERVER_URL: "https://test.com",
			CONF_SWITCH_UID: "vs_test123",
		})

	assert result["type"] == FlowResultType.CREATE_ENTRY
	assert result["data"][CONF_SWITCH_UID] == "vs_test123"
	assert "initial_access_key" not in result["data"]


@pytest.mark.asyncio
async def test_options_flow_pause_access_key(hass, config_entry):
	"""Pausing an access key should call coordinator.pause_v2_access_key."""
	config_entry.data = {
		**(config_entry.data or {}),
		"auth_mode": "crypto",
		"crypto_seed": "test-seed",
	}
	key_id = "b" * 64
	mock_coordinator = MagicMock()
	mock_coordinator.list_v2_access_keys = AsyncMock(return_value={
		"keys": [{"keyId": key_id, "name": "PauseMe", "permissions": ["toggle"], "paused": False, "created": 1700000000000, "lastUsed": None}],
		"count": 1
	})
	mock_coordinator.pause_v2_access_key = AsyncMock(return_value=True)
	hass.data = {DOMAIN: {config_entry.entry_id: mock_coordinator}}

	flow = VomeSyncOptionsFlow(config_entry)
	flow.hass = hass
	flow._step_data = {
		"selected_uid": "vs_test_uid",
		"is_owner": True,
		"selected_key_id": key_id,
	}

	# Show form first (should show Pause since paused=False)
	result = await flow.async_step_access_key_pause(None)
	assert result["type"] == FlowResultType.FORM
	assert result["step_id"] == "access_key_pause"
	assert result["description_placeholders"]["action"].lower() == "pause"

	# Confirm pause
	result = await flow.async_step_access_key_pause({"confirm": True})
	# Should return to detail view on success
	assert result["type"] == FlowResultType.FORM
	assert result["step_id"] == "access_key_detail"
	mock_coordinator.pause_v2_access_key.assert_called_once_with("vs_test_uid", key_id, True)


@pytest.mark.asyncio
async def test_options_flow_unpause_access_key(hass, config_entry):
	"""Unpausing a paused access key should call coordinator with paused=False."""
	config_entry.data = {
		**(config_entry.data or {}),
		"auth_mode": "crypto",
		"crypto_seed": "test-seed",
	}
	key_id = "c" * 64
	mock_coordinator = MagicMock()
	mock_coordinator.list_v2_access_keys = AsyncMock(return_value={
		"keys": [{"keyId": key_id, "name": "UnpauseMe", "permissions": ["toggle"], "paused": True, "created": 1700000000000, "lastUsed": None}],
		"count": 1
	})
	mock_coordinator.pause_v2_access_key = AsyncMock(return_value=True)
	hass.data = {DOMAIN: {config_entry.entry_id: mock_coordinator}}

	flow = VomeSyncOptionsFlow(config_entry)
	flow.hass = hass
	flow._step_data = {
		"selected_uid": "vs_test_uid",
		"is_owner": True,
		"selected_key_id": key_id,
	}

	# Show form first (should show Unpause since paused=True)
	result = await flow.async_step_access_key_pause(None)
	assert result["type"] == FlowResultType.FORM
	assert result["description_placeholders"]["action"].lower() == "unpause"

	# Confirm unpause
	result = await flow.async_step_access_key_pause({"confirm": True})
	assert result["type"] == FlowResultType.FORM
	assert result["step_id"] == "access_key_detail"
	mock_coordinator.pause_v2_access_key.assert_called_once_with("vs_test_uid", key_id, False)


@pytest.mark.asyncio
async def test_options_flow_update_access_key_permissions(hass, config_entry):
	"""Updating access key permissions should call coordinator."""
	config_entry.data = {
		**(config_entry.data or {}),
		"auth_mode": "crypto",
		"crypto_seed": "test-seed",
	}
	key_id = "d" * 64
	mock_coordinator = MagicMock()
	mock_coordinator.list_v2_access_keys = AsyncMock(return_value={
		"keys": [{"keyId": key_id, "name": "PermKey", "permissions": ["toggle"], "paused": False, "created": 1700000000000, "lastUsed": None}],
		"count": 1
	})
	mock_coordinator.update_v2_access_key_permissions = AsyncMock(return_value=True)
	hass.data = {DOMAIN: {config_entry.entry_id: mock_coordinator}}

	flow = VomeSyncOptionsFlow(config_entry)
	flow.hass = hass
	flow._step_data = {
		"selected_uid": "vs_test_uid",
		"is_owner": True,
		"selected_key_id": key_id,
	}

	# Show form first (should show current permissions)
	result = await flow.async_step_access_key_permissions(None)
	assert result["type"] == FlowResultType.FORM
	assert result["step_id"] == "access_key_permissions"

	# Submit new permissions
	result = await flow.async_step_access_key_permissions({"permissions": ["toggle", "comment"]})
	# Should return to detail view on success
	assert result["type"] == FlowResultType.FORM
	assert result["step_id"] == "access_key_detail"
	mock_coordinator.update_v2_access_key_permissions.assert_called_once_with(
		"vs_test_uid", key_id, ["toggle", "comment"]
	)


@pytest.mark.asyncio
async def test_options_flow_update_permissions_empty_fails(hass, config_entry):
	"""Submitting empty permissions should show an error."""
	config_entry.data = {
		**(config_entry.data or {}),
		"auth_mode": "crypto",
		"crypto_seed": "test-seed",
	}
	key_id = "e" * 64
	mock_coordinator = MagicMock()
	mock_coordinator.list_v2_access_keys = AsyncMock(return_value={
		"keys": [{"keyId": key_id, "name": "PermKey", "permissions": ["toggle"], "paused": False, "created": 1700000000000, "lastUsed": None}],
		"count": 1
	})
	hass.data = {DOMAIN: {config_entry.entry_id: mock_coordinator}}

	flow = VomeSyncOptionsFlow(config_entry)
	flow.hass = hass
	flow._step_data = {
		"selected_uid": "vs_test_uid",
		"is_owner": True,
		"selected_key_id": key_id,
	}

	# Submit empty permissions
	result = await flow.async_step_access_key_permissions({"permissions": []})
	assert result["type"] == FlowResultType.FORM
	assert result["step_id"] == "access_key_permissions"
	assert result["errors"]["base"] == "access_key_permissions_empty"


@pytest.mark.asyncio
async def test_options_flow_link_entities(hass, config_entry):
	"""Test linking local entities to a VomeSync switch."""
	# Mock entity registry
	mock_entity_reg = MagicMock()
	mock_entity_reg.entities.values.return_value = [
		MagicMock(
			domain="light",
			entity_id="light.living_room",
			config_entry_id="other-entry",
			original_name="Living Room Light"
		),
		MagicMock(
			domain="switch",
			entity_id="switch.bedroom",
			config_entry_id="other-entry",
			original_name="Bedroom Switch"
		),
	]

	mock_coordinator = MagicMock()
	hass.data = {DOMAIN: {config_entry.entry_id: mock_coordinator}}
	hass.config_entries = MagicMock()

	flow = VomeSyncOptionsFlow(config_entry)
	flow.hass = hass
	flow._step_data = {
		"selected_uid": "switch-uid",
		"selected_name": "Test Switch",
		"is_owner": True,
	}

	with patch("custom_components.vomesync.config_flow.er.async_get", return_value=mock_entity_reg):
		# Show form
		result = await flow.async_step_link_entities(None)
		assert result["type"] == FlowResultType.FORM

		# Link entities
		result = await flow.async_step_link_entities({
			"entities": ["light.living_room", "switch.bedroom"]
		})

	assert result["type"] == FlowResultType.FORM
	assert result["step_id"] == "link_entities_behaviour"

	# Choose behaviour (default/master)
	result2 = await flow.async_step_link_entities_behaviour({
		"mode": "master",
		"master": "light.living_room",
		"direction": "both",
	})

	assert result2["type"] == FlowResultType.CREATE_ENTRY
	options = result2["data"]
	assert "linked_entities" in options
	cfg = options["linked_entities"]["switch-uid"]
	assert cfg["entities"] == ["light.living_room", "switch.bedroom"]
	assert cfg["mode"] == "master"
	assert cfg["master"] == "light.living_room"
	assert cfg["direction"] == "both"


@pytest.mark.asyncio
async def test_options_flow_link_entities_listen_only_hides_direction(hass, config_entry):
	"""Listen-only switches should not show direction options."""
	mock_entity_reg = MagicMock()
	mock_entity_reg.entities.values.return_value = [
		MagicMock(
			domain="light",
			entity_id="light.kitchen",
			config_entry_id="other-entry",
			original_name="Kitchen Light"
		),
	]

	mock_coordinator = MagicMock()
	hass.data = {DOMAIN: {config_entry.entry_id: mock_coordinator}}
	hass.config_entries = MagicMock()

	flow = VomeSyncOptionsFlow(config_entry)
	flow.hass = hass
	flow._step_data = {
		"selected_uid": "switch-uid",
		"selected_name": "Test Switch",
		"is_owner": False,
		"has_access_key": False,
	}

	with patch("custom_components.vomesync.config_flow.er.async_get", return_value=mock_entity_reg):
		result = await flow.async_step_link_entities(None)
		assert result["type"] == FlowResultType.FORM
		schema = result["data_schema"].schema
		assert "direction" not in [field.schema for field in schema]

		result = await flow.async_step_link_entities({
			"entities": ["light.kitchen"],
		})

	assert result["type"] == FlowResultType.CREATE_ENTRY
	options = result["data"]
	cfg = options["linked_entities"]["switch-uid"]
	assert cfg["direction"] == "switch_to_entities"


@pytest.mark.asyncio
async def test_options_flow_delete_switch_removes_from_cache(hass, config_entry):
	"""Test deleting a switch also removes it from imported cache."""
	config_entry.options = {
		"imported_switches": {
			"uid-to-delete": {
				"name": "Switch to Delete",
				"is_owner": True,
				"cached_data": {}
			}
		}
	}

	mock_coordinator = MagicMock()
	mock_coordinator.delete_switch = AsyncMock(return_value=True)
	hass.data = {DOMAIN: {config_entry.entry_id: mock_coordinator}}
	hass.config_entries = MagicMock()

	flow = VomeSyncOptionsFlow(config_entry)
	flow.hass = hass
	flow._step_data = {
		"selected_uid": "uid-to-delete",
		"selected_name": "Switch to Delete",
		"is_owner": True,
	}

	# Confirm deletion
	result = await flow.async_step_delete_switch({"confirm": True})

	assert result["type"] == FlowResultType.CREATE_ENTRY
	mock_coordinator.delete_switch.assert_called_once_with("uid-to-delete")


@pytest.mark.asyncio
async def test_options_flow_edit_connection_urls(hass, config_entry):
	"""Test editing connection URLs."""
	hass.config_entries = MagicMock()
	hass.data = {DOMAIN: {config_entry.entry_id: MagicMock()}}

	flow = VomeSyncOptionsFlow(config_entry)
	flow.hass = hass

	# Show form
	result = await flow.async_step_edit_connection(None)
	assert result["type"] == FlowResultType.FORM
	assert CONF_SERVER_URL in result["data_schema"].schema
	assert CONF_WEBSOCKET_URL in result["data_schema"].schema
	assert "crypto_seed" not in result["data_schema"].schema
	assert "owner_pubkey" not in result["data_schema"].schema

	# Update URLs
	with patch.object(hass.config_entries, "async_reload", new=AsyncMock()):
		result = await flow.async_step_edit_connection({
			CONF_SERVER_URL: "https://new-server.com",
			CONF_WEBSOCKET_URL: "wss://new-server.com/ws",
		})

	assert result["type"] == FlowResultType.CREATE_ENTRY
	hass.config_entries.async_update_entry.assert_called_once()


# ============================================================================
# Tests for previously untested steps
# ============================================================================


@pytest.mark.asyncio
async def test_config_flow_generate_key_aborts(hass):
	"""generate_key step is deprecated and should abort."""
	flow = VomeSyncConfigFlow()
	flow.hass = hass

	result = await flow.async_step_generate_key(None)
	assert result["type"] == FlowResultType.ABORT
	assert result["reason"] == "not_supported"


@pytest.mark.asyncio
async def test_options_flow_init_shows_menu(hass, config_entry):
	"""Init step should show the main menu."""
	flow = VomeSyncOptionsFlow(config_entry)
	flow.hass = hass

	result = await flow.async_step_init(None)
	assert result["type"] == FlowResultType.MENU
	assert result["step_id"] == "init"
	assert "create_switch" in result["menu_options"]
	assert "subscribe_switch" in result["menu_options"]
	assert "manage_switches" in result["menu_options"]
	assert "more" in result["menu_options"]
	# Connect to Vome Home is a headline option, top-level (not under More…).
	assert "link_vome" in result["menu_options"]


@pytest.mark.asyncio
async def test_options_flow_init_offers_unlink_when_linked(hass, config_entry):
	"""A linked entry shows the disconnect option instead."""
	config_entry.options = {
		"relay": {"server_id": "rly-1", "secret": "rly_rly-1.x"},
	}
	flow = VomeSyncOptionsFlow(config_entry)
	flow.hass = hass

	result = await flow.async_step_init(None)
	assert "unlink_vome" in result["menu_options"]
	assert "link_vome" not in result["menu_options"]


@pytest.mark.asyncio
async def test_options_flow_more_shows_submenu(hass, config_entry):
	"""More step should show secondary menu options."""
	flow = VomeSyncOptionsFlow(config_entry)
	flow.hass = hass

	result = await flow.async_step_more(None)
	assert result["type"] == FlowResultType.MENU
	assert result["step_id"] == "more"
	assert "backup_signing_key" in result["menu_options"]
	assert "import_switches" in result["menu_options"]
	assert "edit_connection" in result["menu_options"]
	assert "back" in result["menu_options"]
	# The Vome Home link moved to the top-level menu.
	assert "link_vome" not in result["menu_options"]
	assert "unlink_vome" not in result["menu_options"]


@pytest.mark.asyncio
async def test_options_flow_back_returns_to_init(hass, config_entry):
	"""Back step should return to the init menu."""
	flow = VomeSyncOptionsFlow(config_entry)
	flow.hass = hass

	result = await flow.async_step_back(None)
	assert result["type"] == FlowResultType.MENU
	assert result["step_id"] == "init"


@pytest.mark.asyncio
async def test_options_flow_backup_signing_key_shows_key(hass):
	"""Backup signing key step should display the key in description."""
	config_entry = MockHASSFactory.create_crypto_config_entry(crypto_seed="my-secret-seed")
	flow = VomeSyncOptionsFlow(config_entry)
	flow.hass = hass

	result = await flow.async_step_backup_signing_key(None)
	assert result["type"] == FlowResultType.FORM
	assert result["step_id"] == "backup_signing_key"
	assert "my-secret-seed" in result["description_placeholders"]["info"]


@pytest.mark.asyncio
async def test_options_flow_backup_signing_key_submit_creates_entry(hass):
	"""Submitting backup signing key step should create entry."""
	config_entry = MockHASSFactory.create_crypto_config_entry()
	flow = VomeSyncOptionsFlow(config_entry)
	flow.hass = hass

	result = await flow.async_step_backup_signing_key({})
	assert result["type"] == FlowResultType.CREATE_ENTRY


@pytest.mark.asyncio
async def test_options_flow_backup_signing_key_aborts_without_crypto(hass, config_entry):
	"""Backup signing key should abort if not in crypto mode."""
	flow = VomeSyncOptionsFlow(config_entry)
	flow.hass = hass

	result = await flow.async_step_backup_signing_key(None)
	assert result["type"] == FlowResultType.ABORT
	assert result["reason"] == "crypto_required"


@pytest.mark.asyncio
async def test_options_flow_confirm_backup_signing_key_shows_menu(hass):
	"""Confirm backup step should show a menu."""
	config_entry = MockHASSFactory.create_crypto_config_entry()
	flow = VomeSyncOptionsFlow(config_entry)
	flow.hass = hass

	result = await flow.async_step_confirm_backup_signing_key(None)
	assert result["type"] == FlowResultType.MENU
	assert result["step_id"] == "confirm_backup_signing_key"
	assert "reveal_signing_key" in result["menu_options"]
	assert "confirm_backup_signing_key_done" in result["menu_options"]


@pytest.mark.asyncio
async def test_options_flow_confirm_backup_done_sets_flag(hass):
	"""Confirm backup done should set the flag and proceed to create switch."""
	config_entry = MockHASSFactory.create_crypto_config_entry()
	hass.config_entries = MagicMock()
	def _update_entry(entry, options=None):
		entry.options = options or {}
	hass.config_entries.async_update_entry = MagicMock(side_effect=_update_entry)

	flow = VomeSyncOptionsFlow(config_entry)
	flow.hass = hass

	result = await flow.async_step_confirm_backup_signing_key_done(None)
	# Should proceed to create_switch form after setting flag
	assert result["type"] == FlowResultType.FORM
	assert result["step_id"] == "create_switch"
	assert config_entry.options.get("signing_key_backup_confirmed") is True


@pytest.mark.asyncio
async def test_options_flow_connect_website_shows_login_link(hass, config_entry):
	"""Connect website should create a session token and show login URL."""
	with patch(
		"custom_components.vomesync.config_flow.VomeSyncAPIClient.create_session_token",
		new=AsyncMock(return_value={"token": "abc123"}),
	), patch(
		"custom_components.vomesync.config_flow.VomeSyncAPIClient.close",
		new=AsyncMock(),
	):
		flow = VomeSyncOptionsFlow(config_entry)
		flow.hass = hass

		result = await flow.async_step_connect_website(None)
		assert result["type"] == FlowResultType.FORM
		assert result["step_id"] == "connect_website"
		assert "abc123" in result["description_placeholders"]["session_token"]
		assert "login" in result["description_placeholders"]["login_url"]


@pytest.mark.asyncio
async def test_options_flow_connect_website_submit_creates_entry(hass, config_entry):
	"""Submitting connect website should create entry."""
	with patch(
		"custom_components.vomesync.config_flow.VomeSyncAPIClient.create_session_token",
		new=AsyncMock(return_value={"token": "abc123"}),
	), patch(
		"custom_components.vomesync.config_flow.VomeSyncAPIClient.close",
		new=AsyncMock(),
	):
		flow = VomeSyncOptionsFlow(config_entry)
		flow.hass = hass

		result = await flow.async_step_connect_website({"dummy": True})
		assert result["type"] == FlowResultType.CREATE_ENTRY


@pytest.mark.asyncio
async def test_options_flow_manage_api_keys_shows_form(hass, config_entry):
	"""Manage API keys should show a form with create option."""
	with patch(
		"custom_components.vomesync.config_flow.VomeSyncAPIClient.get_api_keys",
		new=AsyncMock(return_value=[]),
	), patch(
		"custom_components.vomesync.config_flow.VomeSyncAPIClient.close",
		new=AsyncMock(),
	):
		flow = VomeSyncOptionsFlow(config_entry)
		flow.hass = hass

		result = await flow.async_step_manage_api_keys(None)
		assert result["type"] == FlowResultType.FORM
		assert result["step_id"] == "manage_api_keys"
		assert result["description_placeholders"]["api_key_count"] == "0"


@pytest.mark.asyncio
async def test_options_flow_create_api_key_shows_form(hass, config_entry):
	"""Create API key should show a label form."""
	flow = VomeSyncOptionsFlow(config_entry)
	flow.hass = hass

	result = await flow.async_step_create_api_key(None)
	assert result["type"] == FlowResultType.FORM
	assert result["step_id"] == "create_api_key"
	assert "label" in result["data_schema"].schema


@pytest.mark.asyncio
async def test_options_flow_create_api_key_submit_shows_success(hass, config_entry):
	"""Submitting create API key should show success form with new key."""
	with patch(
		"custom_components.vomesync.config_flow.VomeSyncAPIClient.create_api_key",
		new=AsyncMock(return_value={"apiKey": "new-key-abc"}),
	), patch(
		"custom_components.vomesync.config_flow.VomeSyncAPIClient.close",
		new=AsyncMock(),
	):
		flow = VomeSyncOptionsFlow(config_entry)
		flow.hass = hass

		result = await flow.async_step_create_api_key({"label": "Test Key"})
		assert result["type"] == FlowResultType.FORM
		assert result["step_id"] == "create_api_key_success"


@pytest.mark.asyncio
async def test_options_flow_create_api_key_success_returns_to_manage(hass, config_entry):
	"""Create API key success step should return to manage API keys on submit."""
	with patch(
		"custom_components.vomesync.config_flow.VomeSyncAPIClient.get_api_keys",
		new=AsyncMock(return_value=[]),
	), patch(
		"custom_components.vomesync.config_flow.VomeSyncAPIClient.close",
		new=AsyncMock(),
	):
		flow = VomeSyncOptionsFlow(config_entry)
		flow.hass = hass
		flow._step_data = {"new_api_key": "key-123"}

		result = await flow.async_step_create_api_key_success({"api_key": "key-123"})
		assert result["type"] == FlowResultType.FORM
		assert result["step_id"] == "manage_api_keys"


@pytest.mark.asyncio
async def test_options_flow_delete_api_key_shows_confirm(hass, config_entry):
	"""Delete API key should show confirmation form."""
	flow = VomeSyncOptionsFlow(config_entry)
	flow.hass = hass
	flow._step_data = {"delete_key": "key-to-delete"}

	result = await flow.async_step_delete_api_key(None)
	assert result["type"] == FlowResultType.FORM
	assert result["step_id"] == "delete_api_key"
	assert "confirm" in result["data_schema"].schema


@pytest.mark.asyncio
async def test_options_flow_delete_api_key_confirm(hass, config_entry):
	"""Confirming delete API key should call the API and return to manage."""
	with patch(
		"custom_components.vomesync.config_flow.VomeSyncAPIClient.delete_api_key",
		new=AsyncMock(return_value=True),
	), patch(
		"custom_components.vomesync.config_flow.VomeSyncAPIClient.get_api_keys",
		new=AsyncMock(return_value=[]),
	), patch(
		"custom_components.vomesync.config_flow.VomeSyncAPIClient.close",
		new=AsyncMock(),
	):
		flow = VomeSyncOptionsFlow(config_entry)
		flow.hass = hass
		flow._step_data = {"delete_key": "key-to-delete"}

		result = await flow.async_step_delete_api_key({"confirm": True})
		assert result["type"] == FlowResultType.FORM
		assert result["step_id"] == "manage_api_keys"


@pytest.mark.asyncio
async def test_options_flow_view_switch_owner(hass, config_entry):
	"""View switch step for an owner should show webhook URL and details."""
	config_entry.options = {
		"imported_switches": {
			"uid-view": {"name": "My Switch", "is_owner": True, "cached_data": {}}
		}
	}

	mock_coordinator = MagicMock()
	mock_coordinator.switches = {
		"uid-view": {
			"name": "My Switch",
			"description": "A test switch",
			"location": "Test City",
			"category": "Test",
			"lastToggled": 1640995200000,
			"createdAt": 1640995100000,
			"publicize": False,
		}
	}
	mock_coordinator.subscriptions = {}
	hass.data = {DOMAIN: {config_entry.entry_id: mock_coordinator}}

	mock_entity_reg = MagicMock()
	mock_entity_reg.entities = MagicMock()
	mock_entity_reg.entities.values.return_value = []

	flow = VomeSyncOptionsFlow(config_entry)
	flow.hass = hass
	flow._step_data = {
		"selected_uid": "uid-view",
		"selected_name": "My Switch",
		"is_owner": True,
	}

	with patch("custom_components.vomesync.config_flow.er.async_get", return_value=mock_entity_reg):
		result = await flow.async_step_view_switch(None)

	assert result["type"] == FlowResultType.FORM
	assert result["step_id"] == "view_switch"
	assert "My Switch" in result["description_placeholders"]["info"]
	assert "(Owner)" in result["description_placeholders"]["info"]


@pytest.mark.asyncio
async def test_options_flow_view_switch_subscriber(hass, config_entry):
	"""View switch step for a subscriber should show access key field."""
	config_entry.options = {
		"imported_switches": {
			"uid-sub": {"name": "Sub Switch", "is_owner": False, "cached_data": {}}
		}
	}

	mock_coordinator = MagicMock()
	mock_coordinator.switches = {}
	mock_coordinator.subscriptions = {
		"uid-sub": {"name": "Sub Switch", "lastToggled": None, "createdAt": None}
	}
	hass.data = {DOMAIN: {config_entry.entry_id: mock_coordinator}}

	mock_entity_reg = MagicMock()
	mock_entity_reg.entities = MagicMock()
	mock_entity_reg.entities.values.return_value = []

	flow = VomeSyncOptionsFlow(config_entry)
	flow.hass = hass
	flow._step_data = {
		"selected_uid": "uid-sub",
		"selected_name": "Sub Switch",
		"is_owner": False,
	}

	with patch("custom_components.vomesync.config_flow.er.async_get", return_value=mock_entity_reg):
		result = await flow.async_step_view_switch(None)

	assert result["type"] == FlowResultType.FORM
	assert result["step_id"] == "view_switch"
	assert "(Subscribed)" in result["description_placeholders"]["info"]
	# Should have access_key field in schema
	schema_keys = [str(k) for k in result["data_schema"].schema.keys()]
	assert any("access_key" in k for k in schema_keys)


@pytest.mark.asyncio
async def test_options_flow_view_switch_submit_creates_entry(hass, config_entry):
	"""Submitting view switch as owner should create entry."""
	config_entry.options = {
		"imported_switches": {
			"uid-view": {"name": "My Switch", "is_owner": True, "cached_data": {}}
		}
	}
	mock_coordinator = MagicMock()
	mock_coordinator.switches = {"uid-view": {"name": "My Switch"}}
	hass.data = {DOMAIN: {config_entry.entry_id: mock_coordinator}}

	mock_entity_reg = MagicMock()
	mock_entity_reg.entities = MagicMock()
	mock_entity_reg.entities.values.return_value = []

	flow = VomeSyncOptionsFlow(config_entry)
	flow.hass = hass
	flow._step_data = {
		"selected_uid": "uid-view",
		"selected_name": "My Switch",
		"is_owner": True,
	}

	with patch("custom_components.vomesync.config_flow.er.async_get", return_value=mock_entity_reg):
		result = await flow.async_step_view_switch({"uid": "uid-view"})

	assert result["type"] == FlowResultType.CREATE_ENTRY


@pytest.mark.asyncio
async def test_options_flow_edit_switch_shows_form(hass, config_entry):
	"""Edit switch step should show a form with current values."""
	mock_coordinator = MagicMock()
	mock_coordinator.switches = {
		"uid-edit": {
			"name": "Editable Switch",
			"description": "Some desc",
			"location": "City",
			"category": "Other",
			"publicize": False,
			"link": "",
			"iconUrl": "",
			"bannerUrl": "",
		}
	}
	hass.data = {DOMAIN: {config_entry.entry_id: mock_coordinator}}

	flow = VomeSyncOptionsFlow(config_entry)
	flow.hass = hass
	flow._step_data = {
		"selected_uid": "uid-edit",
		"selected_name": "Editable Switch",
		"is_owner": True,
	}

	result = await flow.async_step_edit_switch(None)
	assert result["type"] == FlowResultType.FORM
	assert result["step_id"] == "edit_switch"
	assert CONF_SWITCH_NAME in result["data_schema"].schema


@pytest.mark.asyncio
async def test_options_flow_edit_switch_not_owner_aborts(hass, config_entry):
	"""Edit switch step should abort if not owner."""
	flow = VomeSyncOptionsFlow(config_entry)
	flow.hass = hass
	flow._step_data = {
		"selected_uid": "uid-edit",
		"selected_name": "Not My Switch",
		"is_owner": False,
	}

	result = await flow.async_step_edit_switch(None)
	assert result["type"] == FlowResultType.ABORT
	assert result["reason"] == "not_owner"


@pytest.mark.asyncio
async def test_options_flow_edit_switch_submit_updates(hass, config_entry):
	"""Submitting edit switch should call update_switch_metadata."""
	mock_coordinator = MagicMock()
	mock_coordinator.switches = {
		"uid-edit": {
			"name": "Old Name",
			"description": "",
			"location": "",
			"category": "Other",
			"publicize": False,
			"link": "",
			"iconUrl": "",
			"bannerUrl": "",
		}
	}
	mock_coordinator.update_switch_metadata = AsyncMock(return_value=True)
	hass.data = {DOMAIN: {config_entry.entry_id: mock_coordinator}}
	hass.config_entries = MagicMock()

	flow = VomeSyncOptionsFlow(config_entry)
	flow.hass = hass
	flow._step_data = {
		"selected_uid": "uid-edit",
		"selected_name": "Old Name",
		"is_owner": True,
	}

	result = await flow.async_step_edit_switch({
		CONF_SWITCH_NAME: "New Name",
		"description": "",
		"location": "",
		"category": "Other",
		"publicize": False,
		CONF_SWITCH_LINK: "",
		CONF_SWITCH_ICON_URL: "",
		CONF_SWITCH_BANNER_URL: "",
		CONF_CAPTCHA_TOKEN: "",
	})

	assert result["type"] == FlowResultType.CREATE_ENTRY
	mock_coordinator.update_switch_metadata.assert_called_once()


@pytest.mark.asyncio
async def test_options_flow_reannounce_shows_form(hass):
	"""Reannounce step should show confirmation form."""
	config_entry = MockHASSFactory.create_crypto_config_entry(
		options={
			"imported_switches": {
				"vs_test1": {"name": "Switch 1", "is_owner": True, "crypto_index": 0, "cached_data": {}}
			}
		}
	)
	flow = VomeSyncOptionsFlow(config_entry)
	flow.hass = hass

	result = await flow.async_step_reannounce_owned_switches(None)
	assert result["type"] == FlowResultType.FORM
	assert result["step_id"] == "reannounce_owned_switches"
	assert "confirm" in result["data_schema"].schema
	assert "1" in result["description_placeholders"]["info"]  # owned count


@pytest.mark.asyncio
async def test_options_flow_reannounce_confirm_calls_coordinator(hass):
	"""Confirming reannounce should call coordinator."""
	config_entry = MockHASSFactory.create_crypto_config_entry(
		options={
			"imported_switches": {
				"vs_test1": {"name": "Switch 1", "is_owner": True, "crypto_index": 0, "cached_data": {}}
			}
		}
	)
	mock_coordinator = MockHASSFactory.create_coordinator()
	hass.data = {DOMAIN: {config_entry.entry_id: mock_coordinator}}

	flow = VomeSyncOptionsFlow(config_entry)
	flow.hass = hass

	result = await flow.async_step_reannounce_owned_switches({"confirm": True})
	assert result["type"] == FlowResultType.FORM
	assert result["step_id"] == "reannounce_owned_switches_result"
	mock_coordinator.reannounce_owned_switches.assert_called_once()


@pytest.mark.asyncio
async def test_options_flow_reannounce_decline_creates_entry(hass):
	"""Declining reannounce should create entry without action."""
	config_entry = MockHASSFactory.create_crypto_config_entry()
	flow = VomeSyncOptionsFlow(config_entry)
	flow.hass = hass

	result = await flow.async_step_reannounce_owned_switches({"confirm": False})
	assert result["type"] == FlowResultType.CREATE_ENTRY


@pytest.mark.asyncio
async def test_options_flow_reannounce_result_creates_entry(hass):
	"""Reannounce result step should create entry."""
	config_entry = MockHASSFactory.create_crypto_config_entry()
	flow = VomeSyncOptionsFlow(config_entry)
	flow.hass = hass

	result = await flow.async_step_reannounce_owned_switches_result({})
	assert result["type"] == FlowResultType.CREATE_ENTRY


@pytest.mark.asyncio
async def test_options_flow_cleanup_orphaned_devices_no_orphans(hass, config_entry):
	"""Cleanup should abort if no orphaned devices."""
	mock_device_reg = MagicMock()
	mock_entity_reg = MagicMock()

	with patch("homeassistant.helpers.device_registry.async_get", return_value=mock_device_reg), \
		 patch("homeassistant.helpers.entity_registry.async_get", return_value=mock_entity_reg), \
		 patch("homeassistant.helpers.device_registry.async_entries_for_config_entry", return_value=[]):
		flow = VomeSyncOptionsFlow(config_entry)
		flow.hass = hass

		result = await flow.async_step_cleanup_orphaned_devices(None)

	assert result["type"] == FlowResultType.ABORT
	assert result["reason"] == "no_orphaned_devices"


@pytest.mark.asyncio
async def test_options_flow_cleanup_orphaned_devices_shows_form(hass, config_entry):
	"""Cleanup should show form with orphaned devices."""
	mock_device_reg = MagicMock()
	mock_entity_reg = MagicMock()

	# Create an orphan device (no entities)
	orphan_dev = MagicMock()
	orphan_dev.id = "dev-orphan-1"
	orphan_dev.name = "Orphan Device"
	orphan_dev.name_by_user = None
	orphan_dev.identifiers = {(DOMAIN, "vs_orphan_uid")}

	with patch("homeassistant.helpers.device_registry.async_get", return_value=mock_device_reg), \
		 patch("homeassistant.helpers.entity_registry.async_get", return_value=mock_entity_reg), \
		 patch("homeassistant.helpers.device_registry.async_entries_for_config_entry", return_value=[orphan_dev]), \
		 patch("homeassistant.helpers.entity_registry.async_entries_for_device", return_value=[]):
		flow = VomeSyncOptionsFlow(config_entry)
		flow.hass = hass

		result = await flow.async_step_cleanup_orphaned_devices(None)

	assert result["type"] == FlowResultType.FORM
	assert result["step_id"] == "cleanup_orphaned_devices"
	assert "devices" in result["data_schema"].schema


@pytest.mark.asyncio
async def test_options_flow_cleanup_orphaned_devices_removes_selected(hass, config_entry):
	"""Selecting and confirming should remove the orphaned device."""
	config_entry.options = {
		"imported_switches": {
			"vs_orphan_uid": {"name": "Orphan", "is_owner": True, "cached_data": {}}
		}
	}
	hass.config_entries = MagicMock()

	mock_device_reg = MagicMock()
	mock_entity_reg = MagicMock()

	orphan_dev = MagicMock()
	orphan_dev.id = "dev-orphan-1"
	orphan_dev.name = "Orphan Device"
	orphan_dev.name_by_user = None
	orphan_dev.identifiers = {(DOMAIN, "vs_orphan_uid")}

	with patch("homeassistant.helpers.device_registry.async_get", return_value=mock_device_reg), \
		 patch("homeassistant.helpers.entity_registry.async_get", return_value=mock_entity_reg), \
		 patch("homeassistant.helpers.device_registry.async_entries_for_config_entry", return_value=[orphan_dev]), \
		 patch("homeassistant.helpers.entity_registry.async_entries_for_device", return_value=[]):
		flow = VomeSyncOptionsFlow(config_entry)
		flow.hass = hass

		result = await flow.async_step_cleanup_orphaned_devices({"devices": ["dev-orphan-1"]})

	assert result["type"] == FlowResultType.CREATE_ENTRY
	mock_device_reg.async_remove_device.assert_called_once_with("dev-orphan-1")


# ============================================================================
# Connect to Vome Home (relay link) flow
# ============================================================================

_RELAY_FLOW_MOD = "custom_components.vomesync.options_flow_relay"

_CODE_RESPONSE = {
	"device_code": "dev-123",
	"user_code": "BCDF-GHJK",
	"verification_uri": "https://vome.io/account/link-ha",
	"expires_in": 600,
	"interval": 5,
}


def _relay_flow(hass, config_entry):
	flow = VomeSyncOptionsFlow(config_entry)
	flow.hass = hass
	hass.config_entries = MagicMock()
	return flow


@pytest.mark.asyncio
async def test_link_vome_shows_menu_with_code(hass, config_entry, monkeypatch):
	"""Entry step fetches a code and offers connect vs alternative."""
	monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
	flow = _relay_flow(hass, config_entry)

	with patch(f"{_RELAY_FLOW_MOD}.async_get_clientsession", return_value=MagicMock()), \
		 patch(f"{_RELAY_FLOW_MOD}.async_request_device_code", new=AsyncMock(return_value=_CODE_RESPONSE)):
		result = await flow.async_step_link_vome(None)

	assert result["type"] == FlowResultType.MENU
	assert result["step_id"] == "link_vome"
	assert result["menu_options"] == ["link_vome_confirm", "link_vome_alt"]
	assert result["description_placeholders"]["user_code"] == "BCDF-GHJK"


@pytest.mark.asyncio
async def test_link_vome_code_failure_shows_retry_form(hass, config_entry, monkeypatch):
	"""If the portal can't be reached there is a retry form with the error."""
	monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
	flow = _relay_flow(hass, config_entry)

	with patch(f"{_RELAY_FLOW_MOD}.async_get_clientsession", return_value=MagicMock()), \
		 patch(f"{_RELAY_FLOW_MOD}.async_request_device_code", new=AsyncMock(side_effect=RuntimeError("HTTP 400"))):
		result = await flow.async_step_link_vome(None)

	assert result["type"] == FlowResultType.FORM
	assert result["step_id"] == "link_vome_retry"
	assert result["errors"]["base"] == "relay_code_failed"


@pytest.mark.asyncio
@pytest.mark.parametrize("supervisor", [True, False])
async def test_link_vome_confirm_never_shows_fields(hass, config_entry, monkeypatch, supervisor):
	"""The plain connect has no text boxes on any install type.

	The component mints its own local access token via hass.auth, so neither
	HAOS/Supervised nor Container/Core installs need manual input here.
	"""
	if supervisor:
		monkeypatch.setenv("SUPERVISOR_TOKEN", "supertoken")
	else:
		monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
	flow = _relay_flow(hass, config_entry)
	flow._step_data.update({
		"relay_device_code": "dev-123",
		"relay_user_code": "BCDF-GHJK",
	})

	result = await flow.async_step_link_vome_confirm(None)

	assert result["type"] == FlowResultType.FORM
	assert result["step_id"] == "link_vome_confirm"
	assert len(result["data_schema"].schema) == 0


@pytest.mark.asyncio
async def test_link_vome_alt_always_shows_both_fields(hass, config_entry, monkeypatch):
	"""Alternative connection exposes the manual token + ESPHome URL."""
	monkeypatch.setenv("SUPERVISOR_TOKEN", "supertoken")
	flow = _relay_flow(hass, config_entry)
	flow._step_data.update({"relay_device_code": "dev-123"})

	result = await flow.async_step_link_vome_alt(None)

	field_names = [str(key) for key in result["data_schema"].schema]
	assert field_names == ["local_token", "esphome_url"]


@pytest.mark.asyncio
async def test_link_vome_confirm_approved_saves_and_starts_relay(hass, config_entry, monkeypatch):
	"""An approved poll stores the relay credentials and starts the client."""
	monkeypatch.setenv("SUPERVISOR_TOKEN", "supertoken")
	config_entry.options = {}
	flow = _relay_flow(hass, config_entry)
	flow._step_data.update({"relay_device_code": "dev-123"})

	poll = AsyncMock(return_value={
		"status": "approved",
		"server_id": "rly-9",
		"relay_secret": "rly_rly-9.tail",
		"relay_ws_url": "wss://sync.vome.io/ws/relay",
	})
	start = AsyncMock()
	with patch(f"{_RELAY_FLOW_MOD}.async_get_clientsession", return_value=MagicMock()), \
		 patch(f"{_RELAY_FLOW_MOD}.async_poll_device_token", new=poll), \
		 patch(f"{_RELAY_FLOW_MOD}.async_start_relay", new=start):
		result = await flow.async_step_link_vome_confirm({})

	assert result["type"] == FlowResultType.CREATE_ENTRY
	saved = result["data"]["relay"]
	assert saved["server_id"] == "rly-9"
	assert saved["secret"] == "rly_rly-9.tail"
	start.assert_awaited_once()
	# Pending step data is cleared so a future link starts fresh.
	assert "relay_device_code" not in flow._step_data


@pytest.mark.asyncio
async def test_link_vome_alt_approved_saves_manual_fields(hass, config_entry, monkeypatch):
	"""The alternative form persists the manual token and ESPHome URL."""
	monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
	config_entry.options = {}
	flow = _relay_flow(hass, config_entry)
	flow._step_data.update({"relay_device_code": "dev-123"})

	poll = AsyncMock(return_value={
		"status": "approved",
		"server_id": "rly-9",
		"relay_secret": "rly_rly-9.tail",
		"relay_ws_url": "wss://sync.vome.io/ws/relay",
	})
	with patch(f"{_RELAY_FLOW_MOD}.async_get_clientsession", return_value=MagicMock()), \
		 patch(f"{_RELAY_FLOW_MOD}.async_poll_device_token", new=poll), \
		 patch(f"{_RELAY_FLOW_MOD}.async_start_relay", new=AsyncMock()):
		result = await flow.async_step_link_vome_alt({
			"local_token": " llt-abc ",
			"esphome_url": "http://192.168.1.5:6052",
		})

	saved = result["data"]["relay"]
	assert saved["local_token"] == "llt-abc"
	assert saved["esphome_url"] == "http://192.168.1.5:6052"


@pytest.mark.asyncio
async def test_link_vome_confirm_pending_reshows_form(hass, config_entry, monkeypatch):
	"""A not-yet-approved poll keeps the form up with a pending error."""
	monkeypatch.setenv("SUPERVISOR_TOKEN", "supertoken")
	flow = _relay_flow(hass, config_entry)
	flow._step_data.update({"relay_device_code": "dev-123", "relay_user_code": "BCDF-GHJK"})

	with patch(f"{_RELAY_FLOW_MOD}.async_get_clientsession", return_value=MagicMock()), \
		 patch(f"{_RELAY_FLOW_MOD}.async_poll_device_token", new=AsyncMock(return_value={"status": "pending"})):
		result = await flow.async_step_link_vome_confirm({})

	assert result["type"] == FlowResultType.FORM
	assert result["errors"]["base"] == "relay_pending"


@pytest.mark.asyncio
async def test_link_vome_confirm_expired_mints_new_code(hass, config_entry, monkeypatch):
	"""An expired code is replaced so the re-shown form displays a valid one."""
	monkeypatch.setenv("SUPERVISOR_TOKEN", "supertoken")
	flow = _relay_flow(hass, config_entry)
	flow._step_data.update({"relay_device_code": "dev-old", "relay_user_code": "OLDC-ODEX"})

	new_code = dict(_CODE_RESPONSE, device_code="dev-new", user_code="NEWC-ODEZ")
	with patch(f"{_RELAY_FLOW_MOD}.async_get_clientsession", return_value=MagicMock()), \
		 patch(f"{_RELAY_FLOW_MOD}.async_poll_device_token", new=AsyncMock(return_value={"status": "expired"})), \
		 patch(f"{_RELAY_FLOW_MOD}.async_request_device_code", new=AsyncMock(return_value=new_code)):
		result = await flow.async_step_link_vome_confirm({})

	assert result["type"] == FlowResultType.FORM
	assert result["errors"]["base"] == "relay_expired"
	assert result["description_placeholders"]["user_code"] == "NEWC-ODEZ"
	assert flow._step_data["relay_device_code"] == "dev-new"


@pytest.mark.asyncio
async def test_link_vome_confirm_without_pending_code_restarts(hass, config_entry, monkeypatch):
	"""Landing on confirm without a code (e.g. restart) restarts the flow."""
	monkeypatch.setenv("SUPERVISOR_TOKEN", "supertoken")
	flow = _relay_flow(hass, config_entry)

	with patch(f"{_RELAY_FLOW_MOD}.async_get_clientsession", return_value=MagicMock()), \
		 patch(f"{_RELAY_FLOW_MOD}.async_request_device_code", new=AsyncMock(return_value=_CODE_RESPONSE)):
		result = await flow.async_step_link_vome_confirm(None)

	assert result["type"] == FlowResultType.MENU
	assert result["step_id"] == "link_vome"
