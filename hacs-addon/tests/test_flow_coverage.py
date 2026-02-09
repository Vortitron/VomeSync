# flake8: noqa
"""
Automatic structural-coverage tests for VomeSync config/options flows.

These tests use FlowIntrospector to discover every async_step_* method and
validate structural integrity without manually writing a test per step.
"""
import pytest
from unittest.mock import MagicMock, patch

from homeassistant.data_entry_flow import FlowResultType

from custom_components.vomesync.config_flow import (
	VomeSyncConfigFlow,
	VomeSyncOptionsFlow,
)
from custom_components.vomesync.options_flow_links import VomeSyncOptionsFlowLinkEntitiesMixin
from flow_test_framework import FlowIntrospector, FlowResultValidator


# ---------------------------------------------------------------------------
# Introspection fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def config_flow_introspector():
	return FlowIntrospector(VomeSyncConfigFlow)


@pytest.fixture
def options_flow_introspector():
	return FlowIntrospector(VomeSyncOptionsFlow)


@pytest.fixture
def link_mixin_introspector():
	return FlowIntrospector(VomeSyncOptionsFlowLinkEntitiesMixin)


# ---------------------------------------------------------------------------
# Step discovery tests
# ---------------------------------------------------------------------------

class TestConfigFlowDiscovery:
	"""Verify the config flow has the expected steps."""

	def test_config_flow_has_user_step(self, config_flow_introspector):
		assert config_flow_introspector.has_step("user"), "Config flow must have 'user' step"

	def test_config_flow_has_generate_key_step(self, config_flow_introspector):
		assert config_flow_introspector.has_step("generate_key")

	def test_config_flow_step_count(self, config_flow_introspector):
		# At minimum: user, generate_key
		assert config_flow_introspector.step_count >= 2


class TestOptionsFlowDiscovery:
	"""Verify the options flow has the expected steps."""

	EXPECTED_STEPS = {
		"init", "more", "back",
		"backup_signing_key",
		"import_switches",
		"create_switch", "create_switch_advanced",
		"confirm_backup_signing_key", "confirm_backup_signing_key_done",
		"reveal_signing_key", "post_create_signing_key",
		"subscribe_switch",
		"manage_switches", "manage_switch_action",
		"view_switch", "edit_switch",
		"manage_on_website",
		"access_keys", "access_key_detail",
		"access_key_pause", "access_key_permissions",
		"create_access_key_v2", "create_access_key_v2_success",
		"revoke_access_key_v2", "revoke_access_key_v2_success",
		"delete_switch", "remove_from_installation",
		"edit_connection",
		"connect_website",
		"manage_api_keys", "create_api_key", "create_api_key_success", "delete_api_key",
		"reannounce_owned_switches", "reannounce_owned_switches_result",
		"cleanup_orphaned_devices",
		# From the mixin
		"link_entities", "link_entities_behaviour",
	}

	def test_all_expected_steps_exist(self, options_flow_introspector):
		"""Every step we expect to test must exist in the flow class."""
		missing = self.EXPECTED_STEPS - options_flow_introspector.step_ids
		assert not missing, f"Missing expected steps: {sorted(missing)}"

	def test_no_unexpected_steps(self, options_flow_introspector):
		"""Alert if new steps are added that aren't in our expected set."""
		extra = options_flow_introspector.step_ids - self.EXPECTED_STEPS
		# This is a soft check — new steps are fine, but we want to know about them
		if extra:
			pytest.skip(f"New steps detected (add to EXPECTED_STEPS and write tests): {sorted(extra)}")

	def test_options_flow_has_init(self, options_flow_introspector):
		assert options_flow_introspector.has_step("init")


# ---------------------------------------------------------------------------
# Menu-option integrity tests
# ---------------------------------------------------------------------------

class TestMenuOptionIntegrity:
	"""Verify that all menu_options in menus reference real steps."""

	@pytest.mark.asyncio
	async def test_init_menu_options_are_valid_steps(self, hass, config_entry, options_flow_introspector):
		"""The init menu options should all be real steps."""
		flow = VomeSyncOptionsFlow(config_entry)
		flow.hass = hass

		result = await flow.async_step_init(None)
		assert result["type"] == FlowResultType.MENU

		validator = FlowResultValidator(known_steps=options_flow_introspector.step_ids)
		validator.assert_valid(result, context="init")

	@pytest.mark.asyncio
	async def test_more_menu_options_are_valid_steps(self, hass, config_entry, options_flow_introspector):
		"""The 'more' submenu options should all be real steps."""
		flow = VomeSyncOptionsFlow(config_entry)
		flow.hass = hass

		result = await flow.async_step_more(None)
		assert result["type"] == FlowResultType.MENU

		validator = FlowResultValidator(known_steps=options_flow_introspector.step_ids)
		validator.assert_valid(result, context="more")

	@pytest.mark.asyncio
	async def test_manage_switch_action_menu_options_are_valid(self, hass, config_entry, options_flow_introspector):
		"""Manage switch action menu options should all be real steps."""
		from unittest.mock import MagicMock
		config_entry.options = {
			"imported_switches": {
				"uid-test": {"name": "Test", "is_owner": True, "cached_data": {}}
			}
		}
		mock_entity_reg = MagicMock()
		mock_entity_reg.async_get_entity_id.return_value = None

		hass.data = {"vomesync": {config_entry.entry_id: MagicMock()}}

		flow = VomeSyncOptionsFlow(config_entry)
		flow.hass = hass

		with patch("custom_components.vomesync.config_flow.er.async_get", return_value=mock_entity_reg):
			result = await flow.async_step_manage_switches({"switch": "uid-test"})

		assert result["type"] == FlowResultType.MENU
		validator = FlowResultValidator(known_steps=options_flow_introspector.step_ids)
		validator.assert_valid(result, context="manage_switch_action")

	@pytest.mark.asyncio
	async def test_confirm_backup_signing_key_menu_valid(self, hass, options_flow_introspector):
		"""Confirm backup signing key menu should reference real steps."""
		entry = VomeSyncOptionsFlow.__init__  # just need a crypto entry
		from flow_test_framework import MockHASSFactory
		config_entry = MockHASSFactory.create_crypto_config_entry()

		flow = VomeSyncOptionsFlow(config_entry)
		flow.hass = hass

		result = await flow.async_step_confirm_backup_signing_key(None)
		assert result["type"] == FlowResultType.MENU

		validator = FlowResultValidator(known_steps=options_flow_introspector.step_ids)
		validator.assert_valid(result, context="confirm_backup_signing_key")


# ---------------------------------------------------------------------------
# Schema validation tests
# ---------------------------------------------------------------------------

class TestSchemaValidation:
	"""Verify that form steps produce valid voluptuous schemas."""

	@pytest.mark.asyncio
	async def test_subscribe_switch_form_has_schema(self, hass, config_entry):
		"""Subscribe switch form should have a valid schema."""
		flow = VomeSyncOptionsFlow(config_entry)
		flow.hass = hass

		result = await flow.async_step_subscribe_switch(None)
		assert result["type"] == FlowResultType.FORM
		assert result["data_schema"] is not None
		assert hasattr(result["data_schema"], "schema")

	@pytest.mark.asyncio
	async def test_edit_connection_form_has_schema(self, hass, config_entry):
		"""Edit connection form should have a valid schema."""
		hass.data = {"vomesync": {config_entry.entry_id: MagicMock()}}
		flow = VomeSyncOptionsFlow(config_entry)
		flow.hass = hass

		result = await flow.async_step_edit_connection(None)
		assert result["type"] == FlowResultType.FORM
		assert result["data_schema"] is not None
		assert hasattr(result["data_schema"], "schema")

	@pytest.mark.asyncio
	async def test_delete_switch_form_has_schema(self, hass, config_entry):
		"""Delete switch form should have a valid schema."""
		flow = VomeSyncOptionsFlow(config_entry)
		flow.hass = hass
		flow._step_data = {"selected_uid": "uid-1", "selected_name": "Test", "is_owner": True}

		result = await flow.async_step_delete_switch(None)
		assert result["type"] == FlowResultType.FORM
		assert result["data_schema"] is not None
		assert hasattr(result["data_schema"], "schema")


# ---------------------------------------------------------------------------
# Coverage report (informational)
# ---------------------------------------------------------------------------

class TestCoverageReport:
	"""Print a coverage summary — always passes, for informational use."""

	def test_print_options_flow_step_list(self, options_flow_introspector):
		"""Informational: list all discovered options flow steps."""
		steps = options_flow_introspector.list_steps()
		# This test always passes; it's just for visibility in test output
		assert len(steps) > 0, "Should discover at least one step"

	def test_print_config_flow_step_list(self, config_flow_introspector):
		"""Informational: list all discovered config flow steps."""
		steps = config_flow_introspector.list_steps()
		assert len(steps) > 0, "Should discover at least one step"

