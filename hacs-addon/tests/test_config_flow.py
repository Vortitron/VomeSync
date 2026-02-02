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
	DOMAIN,
	FREE_TIER_MAX_SUBSCRIPTIONS,
)


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
	# Should be a numbered fallback like "VomeSync Switch 1"
	assert default_name.startswith("VomeSync Switch ")


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
	"""Access-keys menu should be available for owned v2 switches in crypto mode."""
	config_entry.data = {
		**(config_entry.data or {}),
		"auth_mode": "crypto",
		"crypto_seed": "test-seed",
	}
	mock_coordinator = MagicMock()
	hass.data = {DOMAIN: {config_entry.entry_id: mock_coordinator}}

	flow = VomeSyncOptionsFlow(config_entry)
	flow.hass = hass
	flow._step_data = {
		"selected_uid": "vs_test_uid",
		"is_owner": True,
	}

	result = await flow.async_step_access_keys(None)
	assert result["type"] == FlowResultType.MENU
	assert result["step_id"] == "access_keys"
	assert "create_access_key_v2" in result["menu_options"]
	assert "list_access_keys_v2" in result["menu_options"]
	assert "revoke_access_key_v2" in result["menu_options"]


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
async def test_options_flow_list_access_keys_v2(hass, config_entry):
	"""Listing v2 access keys should show the keys in the description."""
	config_entry.data = {
		**(config_entry.data or {}),
		"auth_mode": "crypto",
		"crypto_seed": "test-seed",
	}
	mock_coordinator = MagicMock()
	mock_coordinator.list_v2_access_keys = AsyncMock(return_value={
		"keys": [{"keyId": "a" * 64, "name": "Friend", "permissions": ["toggle"]}],
		"count": 1
	})
	hass.data = {DOMAIN: {config_entry.entry_id: mock_coordinator}}

	flow = VomeSyncOptionsFlow(config_entry)
	flow.hass = hass
	flow._step_data = {
		"selected_uid": "vs_test_uid",
		"is_owner": True,
	}

	result = await flow.async_step_list_access_keys_v2(None)
	assert result["type"] == FlowResultType.FORM
	assert result["step_id"] == "list_access_keys_v2"
	assert "aaaaaaaa..." in result["description_placeholders"]["info"]


@pytest.mark.asyncio
async def test_options_flow_revoke_access_key_v2(hass, config_entry):
	"""Revoking a v2 access key should call the coordinator and show success."""
	config_entry.data = {
		**(config_entry.data or {}),
		"auth_mode": "crypto",
		"crypto_seed": "test-seed",
	}
	mock_coordinator = MagicMock()
	mock_coordinator.list_v2_access_keys = AsyncMock(return_value={
		"keys": [{"keyId": "a" * 64, "name": "Friend", "permissions": ["toggle"]}],
		"count": 1
	})
	mock_coordinator.revoke_v2_access_key = AsyncMock(return_value=True)
	hass.data = {DOMAIN: {config_entry.entry_id: mock_coordinator}}

	flow = VomeSyncOptionsFlow(config_entry)
	flow.hass = hass
	flow._step_data = {
		"selected_uid": "vs_test_uid",
		"is_owner": True,
	}

	result = await flow.async_step_revoke_access_key_v2({"api_key": "a" * 64})
	assert result["type"] == FlowResultType.FORM
	assert result["step_id"] == "revoke_access_key_v2_success"
	mock_coordinator.revoke_v2_access_key.assert_called_once_with("vs_test_uid", "a" * 64)


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
