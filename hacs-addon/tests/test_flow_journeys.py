# flake8: noqa
"""
Multi-step user workflow (journey) tests for VomeSync flows.

Each test simulates a realistic sequence of flow steps a user would
follow, validating transitions, data persistence, and API calls.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.data_entry_flow import FlowResultType

from custom_components.vomesync.config_flow import (
	VomeSyncConfigFlow,
	VomeSyncOptionsFlow,
)
from custom_components.vomesync.const import (
	CONF_ACCESS_KEY,
	CONF_CAPTCHA_TOKEN,
	CONF_GENERATE_NEW_KEY,
	CONF_SERVER_URL,
	CONF_SWITCH_ADVANCED,
	CONF_SWITCH_BANNER_URL,
	CONF_SWITCH_CATEGORY,
	CONF_SWITCH_DESCRIPTION,
	CONF_SWITCH_ICON_URL,
	CONF_SWITCH_LINK,
	CONF_SWITCH_LOCATION,
	CONF_SWITCH_NAME,
	CONF_SWITCH_PUBLICIZE,
	CONF_SWITCH_UID,
	CONF_SHOW_SIGNING_KEY_AFTER,
	CONF_USE_DEFAULT_URLS,
	CONF_WEBSOCKET_URL,
	DOMAIN,
)
from flow_test_framework import FlowStepRunner, MockHASSFactory


# ---------------------------------------------------------------------------
# Journey 1: Full setup → create first switch → view details
# ---------------------------------------------------------------------------


class TestFullSetupJourney:
	"""Initial config → create switch → view switch details."""

	@pytest.mark.asyncio
	async def test_initial_setup_creates_entry_with_crypto(self):
		"""Config flow user step with default settings creates a crypto entry."""
		flow = VomeSyncConfigFlow()
		flow.hass = MockHASSFactory.create_hass()

		with patch(
			"custom_components.vomesync.config_flow.VomeSyncAPIClient.get_switch_status",
			new=AsyncMock(return_value=None),
		), patch(
			"custom_components.vomesync.config_flow.VomeSyncAPIClient.close",
			new=AsyncMock(),
		):
			# Step 1: Show the form
			result = await flow.async_step_user(None)
			assert result["type"] == FlowResultType.FORM
			assert result["step_id"] == "user"

			# Step 2: Submit with defaults
			result = await flow.async_step_user({
				CONF_SWITCH_UID: "",
				CONF_GENERATE_NEW_KEY: True,
				CONF_USE_DEFAULT_URLS: True,
			})

			assert result["type"] == FlowResultType.CREATE_ENTRY
			assert result["data"]["auth_mode"] == "crypto"
			assert result["data"]["crypto_seed"]  # Should have generated a seed

	@pytest.mark.asyncio
	async def test_setup_then_create_switch_journey(self):
		"""After setup, user creates their first switch and views it."""
		# Setup: create a crypto config entry and mock coordinator
		config_entry = MockHASSFactory.create_crypto_config_entry(
			options={"signing_key_backup_confirmed": True}
		)
		hass = MockHASSFactory.create_hass()
		coordinator = MockHASSFactory.create_coordinator()
		coordinator.create_switch = AsyncMock(return_value="vs_new_switch_uid")
		coordinator.switches = {}
		MockHASSFactory.wire_hass(hass, config_entry, coordinator)

		mock_entity_reg = MagicMock()
		mock_entity_reg.async_get_entity_id.return_value = None
		mock_entity_reg.entities = MagicMock()
		mock_entity_reg.entities.values.return_value = []

		flow = VomeSyncOptionsFlow(config_entry)
		flow.hass = hass
		runner = FlowStepRunner(flow, auto_validate=False)

		# Step 1: Init menu
		result = await runner.run_step("init")
		runner.assert_menu("init")
		assert "create_switch" in result["menu_options"]

		# Step 2: Create switch form
		result = await runner.run_step("create_switch", None)
		assert result["type"] == FlowResultType.FORM
		assert result["step_id"] == "create_switch"

		# Step 3: Submit switch creation
		with patch("custom_components.vomesync.config_flow.er.async_get", return_value=mock_entity_reg):
			result = await runner.run_step("create_switch", {
				CONF_SWITCH_NAME: "My First Switch",
				CONF_SWITCH_DESCRIPTION: "Test switch for journey",
				CONF_SWITCH_LOCATION: "London",
				CONF_SWITCH_CATEGORY: "Personal",
				CONF_SWITCH_PUBLICIZE: False,
				CONF_SWITCH_ADVANCED: False,
				CONF_SHOW_SIGNING_KEY_AFTER: False,
			})

		assert result["type"] == FlowResultType.MENU
		assert result["step_id"] == "manage_switch_action"
		coordinator.create_switch.assert_called_once()

		# Step 4: View switch from the action menu
		coordinator.switches = {
			"vs_new_switch_uid": {
				"name": "My First Switch",
				"description": "Test switch for journey",
				"location": "London",
				"category": "Personal",
				"publicize": False,
				"lastToggled": None,
				"createdAt": 1640995100000,
			}
		}

		with patch("custom_components.vomesync.config_flow.er.async_get", return_value=mock_entity_reg):
			result = await runner.run_step("view_switch", None)

		assert result["type"] == FlowResultType.FORM
		assert result["step_id"] == "view_switch"
		assert "My First Switch" in result["description_placeholders"]["info"]
		assert "(Owner)" in result["description_placeholders"]["info"]


# ---------------------------------------------------------------------------
# Journey 2: Subscribe → manage → view → link → unsubscribe
# ---------------------------------------------------------------------------


class TestSubscriptionJourney:
	"""Subscribe to a switch, manage it, link entities, then remove."""

	@pytest.mark.asyncio
	async def test_subscribe_then_manage_journey(self):
		"""User subscribes to a switch, views it, then removes from installation."""
		config_entry = MockHASSFactory.create_crypto_config_entry()
		hass = MockHASSFactory.create_hass()
		coordinator = MockHASSFactory.create_coordinator()
		coordinator.subscribe_to_switch = AsyncMock(return_value=True)
		MockHASSFactory.wire_hass(hass, config_entry, coordinator)

		flow = VomeSyncOptionsFlow(config_entry)
		flow.hass = hass
		runner = FlowStepRunner(flow, auto_validate=False)

		# Step 1: Show subscribe form
		result = await runner.run_step("subscribe_switch", None)
		assert result["type"] == FlowResultType.FORM
		assert result["step_id"] == "subscribe_switch"

		# Step 2: Submit subscription
		result = await runner.run_step("subscribe_switch", {
			CONF_SWITCH_UID: "vs_remote_switch",
			CONF_ACCESS_KEY: "my-access-key",
		})
		assert result["type"] == FlowResultType.CREATE_ENTRY
		coordinator.subscribe_to_switch.assert_called_once_with(
			"vs_remote_switch", access_key="my-access-key"
		)

		# Step 3: Now manage the subscribed switch
		config_entry.options = {
			"imported_switches": {
				"vs_remote_switch": {
					"name": "Remote Switch",
					"is_owner": False,
					"access_key": "my-access-key",
					"cached_data": {},
				}
			}
		}

		# Create a fresh flow for the manage steps
		flow2 = VomeSyncOptionsFlow(config_entry)
		flow2.hass = hass
		runner2 = FlowStepRunner(flow2, auto_validate=False)

		mock_entity_reg = MagicMock()
		mock_entity_reg.async_get_entity_id.return_value = None
		mock_entity_reg.entities = MagicMock()
		mock_entity_reg.entities.values.return_value = []

		# Step 4: Select the switch to manage
		with patch("custom_components.vomesync.config_flow.er.async_get", return_value=mock_entity_reg):
			result = await runner2.run_step("manage_switches", {"switch": "vs_remote_switch"})

		assert result["type"] == FlowResultType.MENU
		assert result["step_id"] == "manage_switch_action"
		assert "view_switch" in result["menu_options"]
		assert "remove_from_installation" in result["menu_options"]
		# Subscriber shouldn't see edit_switch or delete_switch
		assert "edit_switch" not in result["menu_options"]
		assert "delete_switch" not in result["menu_options"]

		# Step 5: Remove from installation
		result = await runner2.run_step("remove_from_installation", None)
		assert result["type"] == FlowResultType.FORM
		assert result["step_id"] == "remove_from_installation"

		result = await runner2.run_step("remove_from_installation", {"confirm": True})
		assert result["type"] == FlowResultType.CREATE_ENTRY
		# Should have removed from imported_switches
		assert "vs_remote_switch" not in config_entry.options.get("imported_switches", {})


# ---------------------------------------------------------------------------
# Journey 3: Access key lifecycle
# ---------------------------------------------------------------------------


class TestAccessKeyLifecycleJourney:
	"""Create access key → view detail → pause → unpause → update permissions → revoke."""

	@pytest.mark.asyncio
	async def test_access_key_full_lifecycle(self):
		"""Walk through the complete access key lifecycle."""
		config_entry = MockHASSFactory.create_crypto_config_entry(
			options={
				"imported_switches": {
					"vs_my_switch": {"name": "My Switch", "is_owner": True, "crypto_index": 0, "cached_data": {}}
				}
			}
		)
		hass = MockHASSFactory.create_hass()
		coordinator = MockHASSFactory.create_coordinator()
		coordinator.create_v2_access_key = AsyncMock(return_value={"apiKey": "ak_test_123"})
		coordinator.list_v2_access_keys = AsyncMock(return_value={
			"keys": [
				{
					"keyId": "kid_001",
					"name": "Test Key",
					"permissions": ["toggle"],
					"paused": False,
					"created": 1640995100000,
					"lastUsed": None,
				}
			],
			"count": 1,
		})
		coordinator.pause_v2_access_key = AsyncMock(return_value=True)
		coordinator.update_v2_access_key_permissions = AsyncMock(return_value=True)
		coordinator.revoke_v2_access_key = AsyncMock(return_value=True)
		MockHASSFactory.wire_hass(hass, config_entry, coordinator)

		flow = VomeSyncOptionsFlow(config_entry)
		flow.hass = hass
		flow._step_data = {
			"selected_uid": "vs_my_switch",
			"selected_name": "My Switch",
			"is_owner": True,
		}
		runner = FlowStepRunner(flow, auto_validate=False)

		# Step 1: Access keys list
		result = await runner.run_step("access_keys", None)
		assert result["type"] == FlowResultType.FORM
		assert result["step_id"] == "access_keys"
		# Verify the key ID appears in the schema's In validator container
		schema_val = list(result["data_schema"].schema.values())[0]
		assert "kid_001" in schema_val.container

		# Step 2: Create new key
		result = await runner.run_step("create_access_key_v2", None)
		assert result["type"] == FlowResultType.FORM
		assert result["step_id"] == "create_access_key_v2"

		result = await runner.run_step("create_access_key_v2", {
			"name": "Automation Key",
			"permissions": ["toggle", "comment"],
		})
		assert result["type"] == FlowResultType.FORM
		assert result["step_id"] == "create_access_key_v2_success"
		coordinator.create_v2_access_key.assert_called_once()

		# Step 3: View key detail
		flow._step_data["selected_key_id"] = "kid_001"
		result = await runner.run_step("access_key_detail", None)
		assert result["type"] == FlowResultType.FORM
		assert result["step_id"] == "access_key_detail"
		assert "Test Key" in result["description_placeholders"]["info"]

		# Step 4: Pause the key
		result = await runner.run_step("access_key_pause", None)
		assert result["type"] == FlowResultType.FORM
		assert result["step_id"] == "access_key_pause"

		result = await runner.run_step("access_key_pause", {"confirm": True})
		coordinator.pause_v2_access_key.assert_called_once_with("vs_my_switch", "kid_001", True)

		# Step 5: Update permissions
		result = await runner.run_step("access_key_permissions", None)
		assert result["type"] == FlowResultType.FORM
		assert result["step_id"] == "access_key_permissions"

		result = await runner.run_step("access_key_permissions", {
			"permissions": ["toggle", "comment", "metadata"],
		})
		coordinator.update_v2_access_key_permissions.assert_called_once()

		# Step 6: Revoke the key
		result = await runner.run_step("revoke_access_key_v2", None)
		assert result["type"] == FlowResultType.FORM
		assert result["step_id"] == "revoke_access_key_v2"

		result = await runner.run_step("revoke_access_key_v2", {"confirm": True})
		assert result["type"] == FlowResultType.FORM
		assert result["step_id"] == "revoke_access_key_v2_success"
		coordinator.revoke_v2_access_key.assert_called_once_with("vs_my_switch", "kid_001")


# ---------------------------------------------------------------------------
# Journey 4: Server migration (edit connection → reannounce)
# ---------------------------------------------------------------------------


class TestServerMigrationJourney:
	"""Edit connection URLs then reannounce switches to the new server."""

	@pytest.mark.asyncio
	async def test_edit_connection_and_reannounce(self):
		"""User changes server URL, then reannounces owned switches."""
		config_entry = MockHASSFactory.create_crypto_config_entry(
			options={
				"imported_switches": {
					"vs_switch_1": {"name": "Switch 1", "is_owner": True, "crypto_index": 0, "cached_data": {}}
				}
			}
		)
		hass = MockHASSFactory.create_hass()
		coordinator = MockHASSFactory.create_coordinator()
		coordinator.reannounce_owned_switches = AsyncMock(return_value={
			"eligible": 1, "attempted": 1, "succeeded": 1, "skipped": 0, "errors": []
		})
		MockHASSFactory.wire_hass(hass, config_entry, coordinator)

		flow = VomeSyncOptionsFlow(config_entry)
		flow.hass = hass
		runner = FlowStepRunner(flow, auto_validate=False)

		# Step 1: Edit connection form
		result = await runner.run_step("edit_connection", None)
		assert result["type"] == FlowResultType.FORM
		assert result["step_id"] == "edit_connection"

		# Step 2: Submit new URLs
		result = await runner.run_step("edit_connection", {
			CONF_SERVER_URL: "https://new-server.vome.io",
			CONF_WEBSOCKET_URL: "wss://new-server.vome.io/ws",
		})
		assert result["type"] == FlowResultType.CREATE_ENTRY

		# Step 3: Navigate to reannounce via more menu
		flow2 = VomeSyncOptionsFlow(config_entry)
		flow2.hass = hass
		runner2 = FlowStepRunner(flow2, auto_validate=False)

		result = await runner2.run_step("more", None)
		runner2.assert_menu("more")
		assert "reannounce_owned_switches" in result["menu_options"]

		# Step 4: Reannounce form
		result = await runner2.run_step("reannounce_owned_switches", None)
		assert result["type"] == FlowResultType.FORM
		assert result["step_id"] == "reannounce_owned_switches"

		# Step 5: Confirm reannounce
		result = await runner2.run_step("reannounce_owned_switches", {"confirm": True})
		assert result["type"] == FlowResultType.FORM
		assert result["step_id"] == "reannounce_owned_switches_result"
		coordinator.reannounce_owned_switches.assert_called_once()
		assert "1" in result["description_placeholders"]["info"]  # succeeded count

		# Step 6: Dismiss result
		result = await runner2.run_step("reannounce_owned_switches_result", {})
		assert result["type"] == FlowResultType.CREATE_ENTRY


# ---------------------------------------------------------------------------
# Journey 5: Cleanup orphaned devices
# ---------------------------------------------------------------------------


class TestCleanupJourney:
	"""Navigate to cleanup, select orphans, verify removal."""

	@pytest.mark.asyncio
	async def test_cleanup_orphaned_devices_journey(self):
		"""User navigates to cleanup and removes an orphaned device."""
		config_entry = MockHASSFactory.create_crypto_config_entry(
			options={
				"imported_switches": {
					"vs_orphan_uid": {"name": "Orphan", "is_owner": True, "cached_data": {}}
				}
			}
		)
		hass = MockHASSFactory.create_hass()
		hass.config_entries = MagicMock()

		mock_device_reg = MagicMock()
		mock_entity_reg = MagicMock()

		orphan_dev = MagicMock()
		orphan_dev.id = "dev-orphan-1"
		orphan_dev.name = "Orphan Device"
		orphan_dev.name_by_user = None
		orphan_dev.identifiers = {(DOMAIN, "vs_orphan_uid")}

		flow = VomeSyncOptionsFlow(config_entry)
		flow.hass = hass
		runner = FlowStepRunner(flow, auto_validate=False)

		# Step 1: More menu
		result = await runner.run_step("more", None)
		runner.assert_menu("more")
		assert "cleanup_orphaned_devices" in result["menu_options"]

		# Step 2: Show orphaned devices
		with patch("homeassistant.helpers.device_registry.async_get", return_value=mock_device_reg), \
			 patch("homeassistant.helpers.entity_registry.async_get", return_value=mock_entity_reg), \
			 patch("homeassistant.helpers.device_registry.async_entries_for_config_entry", return_value=[orphan_dev]), \
			 patch("homeassistant.helpers.entity_registry.async_entries_for_device", return_value=[]):
			result = await runner.run_step("cleanup_orphaned_devices", None)

		assert result["type"] == FlowResultType.FORM
		assert result["step_id"] == "cleanup_orphaned_devices"

		# Step 3: Select and remove the orphan
		with patch("homeassistant.helpers.device_registry.async_get", return_value=mock_device_reg), \
			 patch("homeassistant.helpers.entity_registry.async_get", return_value=mock_entity_reg), \
			 patch("homeassistant.helpers.device_registry.async_entries_for_config_entry", return_value=[orphan_dev]), \
			 patch("homeassistant.helpers.entity_registry.async_entries_for_device", return_value=[]):
			result = await runner.run_step("cleanup_orphaned_devices", {"devices": ["dev-orphan-1"]})

		assert result["type"] == FlowResultType.CREATE_ENTRY
		mock_device_reg.async_remove_device.assert_called_once_with("dev-orphan-1")


# ---------------------------------------------------------------------------
# Journey 6: Backup signing key flow
# ---------------------------------------------------------------------------


class TestBackupSigningKeyJourney:
	"""Confirm backup → reveal key → confirm → create switch."""

	@pytest.mark.asyncio
	async def test_backup_key_before_first_switch(self):
		"""User must confirm backup before creating first switch (crypto mode)."""
		config_entry = MockHASSFactory.create_crypto_config_entry(
			options={"imported_switches": {}}
		)
		hass = MockHASSFactory.create_hass()
		coordinator = MockHASSFactory.create_coordinator()
		MockHASSFactory.wire_hass(hass, config_entry, coordinator)
		hass.config_entries = MagicMock()
		def _update_entry(entry, options=None, data=None):
			if options is not None:
				entry.options = options
			if data is not None:
				entry.data = data
		hass.config_entries.async_update_entry = MagicMock(side_effect=_update_entry)

		flow = VomeSyncOptionsFlow(config_entry)
		flow.hass = hass
		runner = FlowStepRunner(flow, auto_validate=False)

		# Step 1: Try to create switch → redirected to confirm backup
		result = await runner.run_step("create_switch", None)
		assert result["type"] == FlowResultType.MENU
		assert result["step_id"] == "confirm_backup_signing_key"
		assert "reveal_signing_key" in result["menu_options"]
		assert "confirm_backup_signing_key_done" in result["menu_options"]

		# Step 2: Reveal signing key
		result = await runner.run_step("reveal_signing_key", None)
		assert result["type"] == FlowResultType.FORM
		assert result["step_id"] == "reveal_signing_key"
		assert "test-seed" in result["description_placeholders"]["info"]

		# Step 3: Confirm backup
		result = await runner.run_step("reveal_signing_key", {"confirmed": True})
		# Should proceed to create_switch form
		assert result["type"] == FlowResultType.FORM
		assert result["step_id"] == "create_switch"
		assert config_entry.options.get("signing_key_backup_confirmed") is True


# ---------------------------------------------------------------------------
# Journey 7: Edit switch metadata
# ---------------------------------------------------------------------------


class TestEditSwitchJourney:
	"""Select switch → edit metadata → verify API call."""

	@pytest.mark.asyncio
	async def test_edit_switch_metadata_journey(self):
		"""Owner selects a switch and edits its metadata."""
		config_entry = MockHASSFactory.create_crypto_config_entry(
			options={
				"imported_switches": {
					"vs_editable": {"name": "Old Name", "is_owner": True, "cached_data": {}}
				}
			}
		)
		hass = MockHASSFactory.create_hass()
		coordinator = MockHASSFactory.create_coordinator()
		coordinator.switches = {
			"vs_editable": {
				"name": "Old Name",
				"description": "Old description",
				"location": "Old City",
				"category": "Other",
				"publicize": False,
				"link": "",
				"iconUrl": "",
				"bannerUrl": "",
			}
		}
		coordinator.update_switch_metadata = AsyncMock(return_value=True)
		MockHASSFactory.wire_hass(hass, config_entry, coordinator)

		mock_entity_reg = MagicMock()
		mock_entity_reg.async_get_entity_id.return_value = None
		mock_entity_reg.entities = MagicMock()
		mock_entity_reg.entities.values.return_value = []

		flow = VomeSyncOptionsFlow(config_entry)
		flow.hass = hass
		runner = FlowStepRunner(flow, auto_validate=False)

		# Step 1: Select switch from manage
		with patch("custom_components.vomesync.config_flow.er.async_get", return_value=mock_entity_reg):
			result = await runner.run_step("manage_switches", {"switch": "vs_editable"})

		assert result["type"] == FlowResultType.MENU
		assert result["step_id"] == "manage_switch_action"
		assert "edit_switch" in result["menu_options"]

		# Step 2: Show edit form
		result = await runner.run_step("edit_switch", None)
		assert result["type"] == FlowResultType.FORM
		assert result["step_id"] == "edit_switch"

		# Step 3: Submit changes
		result = await runner.run_step("edit_switch", {
			CONF_SWITCH_NAME: "New Name",
			"description": "New description",
			"location": "New City",
			"category": "Personal",
			"publicize": False,
			CONF_SWITCH_LINK: "",
			CONF_SWITCH_ICON_URL: "",
			CONF_SWITCH_BANNER_URL: "",
			CONF_CAPTCHA_TOKEN: "",
		})

		assert result["type"] == FlowResultType.CREATE_ENTRY
		coordinator.update_switch_metadata.assert_called_once()
		call_args = coordinator.update_switch_metadata.call_args
		assert call_args[0][0] == "vs_editable"
		updates = call_args[0][1]
		assert updates["name"] == "New Name"
		assert updates["description"] == "New description"


# ---------------------------------------------------------------------------
# Journey 8: Link entities journey
# ---------------------------------------------------------------------------


class TestLinkEntitiesJourney:
	"""Select switch → link entities → verify options updated."""

	@pytest.mark.asyncio
	async def test_link_single_entity(self):
		"""User links a single entity to a switch (no behaviour step needed)."""
		config_entry = MockHASSFactory.create_crypto_config_entry(
			options={
				"imported_switches": {
					"vs_linkable": {"name": "Linkable", "is_owner": True, "cached_data": {}}
				},
				"linked_entities": {},
			}
		)
		hass = MockHASSFactory.create_hass()
		coordinator = MockHASSFactory.create_coordinator()
		MockHASSFactory.wire_hass(hass, config_entry, coordinator)
		hass.config_entries = MagicMock()
		def _update_entry(entry, options=None, data=None):
			if options is not None:
				entry.options = options
		hass.config_entries.async_update_entry = MagicMock(side_effect=_update_entry)

		# Create a mock entity in the registry
		mock_entity = MagicMock()
		mock_entity.domain = "switch"
		mock_entity.entity_id = "switch.kitchen_light"
		mock_entity.config_entry_id = "other-entry"
		mock_entity.original_name = "Kitchen Light"
		mock_entity.unique_id = "kitchen_light_unique"

		mock_entity_reg = MagicMock()
		mock_entity_reg.entities = MagicMock()
		mock_entity_reg.entities.values.return_value = [mock_entity]

		mock_state = MagicMock()
		mock_state.attributes = {"friendly_name": "Kitchen Light"}
		hass.states.get = MagicMock(return_value=mock_state)

		flow = VomeSyncOptionsFlow(config_entry)
		flow.hass = hass
		flow._step_data = {
			"selected_uid": "vs_linkable",
			"selected_name": "Linkable",
			"is_owner": True,
			"has_access_key": False,
		}
		runner = FlowStepRunner(flow, auto_validate=False)

		# Step 1: Show link entities form
		with patch("custom_components.vomesync.options_flow_links.er.async_get", return_value=mock_entity_reg):
			result = await runner.run_step("link_entities", None)

		assert result["type"] == FlowResultType.FORM
		assert result["step_id"] == "link_entities"

		# Step 2: Select a single entity
		with patch("custom_components.vomesync.options_flow_links.er.async_get", return_value=mock_entity_reg):
			result = await runner.run_step("link_entities", {
				"entities": ["switch.kitchen_light"],
				"direction": "both",
			})

		assert result["type"] == FlowResultType.CREATE_ENTRY
		linked = config_entry.options.get("linked_entities", {})
		assert "vs_linkable" in linked
		assert linked["vs_linkable"]["entities"] == ["switch.kitchen_light"]
		assert linked["vs_linkable"]["mode"] == "master"
		coordinator.async_setup_entity_links.assert_called()

	@pytest.mark.asyncio
	async def test_link_multiple_entities_shows_behaviour_step(self):
		"""Linking multiple entities should show the behaviour step."""
		config_entry = MockHASSFactory.create_crypto_config_entry(
			options={
				"imported_switches": {
					"vs_multi": {"name": "Multi", "is_owner": True, "cached_data": {}}
				},
				"linked_entities": {},
			}
		)
		hass = MockHASSFactory.create_hass()
		coordinator = MockHASSFactory.create_coordinator()
		MockHASSFactory.wire_hass(hass, config_entry, coordinator)
		hass.config_entries = MagicMock()
		def _update_entry(entry, options=None, data=None):
			if options is not None:
				entry.options = options
		hass.config_entries.async_update_entry = MagicMock(side_effect=_update_entry)

		mock_entity1 = MagicMock()
		mock_entity1.domain = "switch"
		mock_entity1.entity_id = "switch.entity_a"
		mock_entity1.config_entry_id = "other"
		mock_entity1.original_name = "Entity A"
		mock_entity1.unique_id = "a"

		mock_entity2 = MagicMock()
		mock_entity2.domain = "light"
		mock_entity2.entity_id = "light.entity_b"
		mock_entity2.config_entry_id = "other"
		mock_entity2.original_name = "Entity B"
		mock_entity2.unique_id = "b"

		mock_entity_reg = MagicMock()
		mock_entity_reg.entities = MagicMock()
		mock_entity_reg.entities.values.return_value = [mock_entity1, mock_entity2]

		hass.states.get = MagicMock(return_value=None)

		flow = VomeSyncOptionsFlow(config_entry)
		flow.hass = hass
		flow._step_data = {
			"selected_uid": "vs_multi",
			"selected_name": "Multi",
			"is_owner": True,
			"has_access_key": False,
		}
		runner = FlowStepRunner(flow, auto_validate=False)

		# Step 1: Link entities form
		with patch("custom_components.vomesync.options_flow_links.er.async_get", return_value=mock_entity_reg):
			result = await runner.run_step("link_entities", None)
		assert result["type"] == FlowResultType.FORM

		# Step 2: Select multiple entities with "both" direction
		with patch("custom_components.vomesync.options_flow_links.er.async_get", return_value=mock_entity_reg):
			result = await runner.run_step("link_entities", {
				"entities": ["switch.entity_a", "light.entity_b"],
				"direction": "both",
			})

		# Should redirect to behaviour step
		assert result["type"] == FlowResultType.FORM
		assert result["step_id"] == "link_entities_behaviour"

		# Step 3: Choose behaviour
		result = await runner.run_step("link_entities_behaviour", {
			"mode": "or",
			"master": "switch.entity_a",
			"direction": "both",
		})

		assert result["type"] == FlowResultType.CREATE_ENTRY
		linked = config_entry.options.get("linked_entities", {})
		assert "vs_multi" in linked
		assert linked["vs_multi"]["mode"] == "or"
		assert len(linked["vs_multi"]["entities"]) == 2


# ---------------------------------------------------------------------------
# Journey 9: API key management (v1 legacy)
# ---------------------------------------------------------------------------


class TestAPIKeyManagementJourney:
	"""Create and delete v1 API keys."""

	@pytest.mark.asyncio
	async def test_create_and_delete_api_key(self):
		"""User creates a new API key, then deletes it."""
		config_entry = MockHASSFactory.create_config_entry()
		hass = MockHASSFactory.create_hass()

		flow = VomeSyncOptionsFlow(config_entry)
		flow.hass = hass
		runner = FlowStepRunner(flow, auto_validate=False)

		# Step 1: Show manage API keys (empty list)
		with patch(
			"custom_components.vomesync.config_flow.VomeSyncAPIClient.get_api_keys",
			new=AsyncMock(return_value=[]),
		), patch(
			"custom_components.vomesync.config_flow.VomeSyncAPIClient.close",
			new=AsyncMock(),
		):
			result = await runner.run_step("manage_api_keys", None)

		assert result["type"] == FlowResultType.FORM
		assert result["step_id"] == "manage_api_keys"
		assert result["description_placeholders"]["api_key_count"] == "0"

		# Step 2: Navigate to create
		result = await runner.run_step("create_api_key", None)
		assert result["type"] == FlowResultType.FORM
		assert result["step_id"] == "create_api_key"

		# Step 3: Create key
		with patch(
			"custom_components.vomesync.config_flow.VomeSyncAPIClient.create_api_key",
			new=AsyncMock(return_value={"apiKey": "new-key-xyz"}),
		), patch(
			"custom_components.vomesync.config_flow.VomeSyncAPIClient.close",
			new=AsyncMock(),
		):
			result = await runner.run_step("create_api_key", {"label": "My Webhook"})

		assert result["type"] == FlowResultType.FORM
		assert result["step_id"] == "create_api_key_success"

		# Step 4: Dismiss success and go to manage with the key now listed
		with patch(
			"custom_components.vomesync.config_flow.VomeSyncAPIClient.get_api_keys",
			new=AsyncMock(return_value=[{"apiKey": "new-key-xyz", "name": "My Webhook"}]),
		), patch(
			"custom_components.vomesync.config_flow.VomeSyncAPIClient.close",
			new=AsyncMock(),
		):
			result = await runner.run_step("create_api_key_success", {"api_key": "new-key-xyz"})

		assert result["type"] == FlowResultType.FORM
		assert result["step_id"] == "manage_api_keys"
		assert result["description_placeholders"]["api_key_count"] == "1"

		# Step 5: Delete the key
		flow._step_data["delete_key"] = "new-key-xyz"
		result = await runner.run_step("delete_api_key", None)
		assert result["type"] == FlowResultType.FORM
		assert result["step_id"] == "delete_api_key"

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
			result = await runner.run_step("delete_api_key", {"confirm": True})

		assert result["type"] == FlowResultType.FORM
		assert result["step_id"] == "manage_api_keys"
		assert result["description_placeholders"]["api_key_count"] == "0"

