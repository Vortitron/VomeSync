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
	DOMAIN,
)


@pytest.mark.asyncio
async def test_config_flow_derives_websocket_url(hass):
	"""Test WebSocket URL auto-derives from server URL."""
	flow = VomeSyncConfigFlow()
	flow.hass = hass

	# Test HTTP -> WS
	result = await flow.async_step_user({
		CONF_SERVER_URL: "http://example.com:3000",
	})

	assert result["type"] == FlowResultType.CREATE_ENTRY
	assert flow._websocket_url == "ws://example.com:3000/ws"

	# Test HTTPS -> WSS
	flow2 = VomeSyncConfigFlow()
	flow2.hass = hass
	result2 = await flow2.async_step_user({
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
		CONF_SERVER_URL: "https://test.com",
	})

	assert result["type"] == FlowResultType.CREATE_ENTRY
	assert result["data"][CONF_SERVER_URL] == "https://test.com"


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
	})

	assert result["type"] == FlowResultType.CREATE_ENTRY
	mock_coordinator.create_switch.assert_called_once()


@pytest.mark.asyncio
async def test_options_flow_create_switch_schema_includes_theming_fields(hass, config_entry):
	"""Create-switch form should expose link/icon/banner fields."""
	flow = VomeSyncOptionsFlow(config_entry)
	flow.hass = hass

	result = await flow.async_step_create_switch(None)
	assert result["type"] == FlowResultType.FORM
	schema = result["data_schema"].schema
	assert CONF_SWITCH_LINK in schema
	assert CONF_SWITCH_ICON_URL in schema
	assert CONF_SWITCH_BANNER_URL in schema
	assert CONF_CAPTCHA_TOKEN in schema


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
	})

	assert result["type"] == FlowResultType.CREATE_ENTRY
	mock_coordinator.subscribe_to_switch.assert_called_once_with("remote-uid-456")


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
	schema = result["data_schema"].schema
	assert "website_management_url" in schema
	assert "access_key" in schema


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
		"keys": [{"apiKey": "key-123", "name": "Friend", "permissions": ["toggle"]}],
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
	assert "key-123" in result["description_placeholders"]["info"]


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
		"keys": [{"apiKey": "key-123", "name": "Friend", "permissions": ["toggle"]}],
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

	result = await flow.async_step_revoke_access_key_v2({"api_key": "key-123"})
	assert result["type"] == FlowResultType.FORM
	assert result["step_id"] == "revoke_access_key_v2_success"
	mock_coordinator.revoke_v2_access_key.assert_called_once_with("vs_test_uid", "key-123")


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
