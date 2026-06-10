# flake8: noqa
"""
Reusable Flow Test Framework for Home Assistant Integrations.

This module provides generic utilities for testing any HA integration's
config flow and options flow without a running Home Assistant instance.
It works entirely with pytest + unittest.mock.

Classes:
    FlowIntrospector  - discovers all async_step_* methods in a flow class
    FlowResultValidator - validates FlowResult dicts returned by steps
    MockHASSFactory   - builder helpers for mock hass / config entries / coordinators
    FlowStepRunner    - convenience wrapper to run + validate a step in one call
"""
import asyncio
import inspect
from typing import Any, Dict, List, Optional, Set, Type
from unittest.mock import AsyncMock, MagicMock

from homeassistant.config_entries import ConfigEntry
from homeassistant.data_entry_flow import FlowResultType


# ---------------------------------------------------------------------------
# FlowIntrospector
# ---------------------------------------------------------------------------

class FlowIntrospector:
    """Discover and catalogue all async_step_* methods in a flow class."""

    def __init__(self, flow_class: Type) -> None:
        self.flow_class = flow_class
        self._steps: Dict[str, Any] = {}
        self._discover()

    def _discover(self) -> None:
        for name, method in inspect.getmembers(self.flow_class, predicate=inspect.isfunction):
            if name.startswith("async_step_"):
                step_id = name[len("async_step_"):]
                self._steps[step_id] = {
                    "method_name": name,
                    "method": method,
                    "signature": inspect.signature(method),
                }

    @property
    def step_ids(self) -> Set[str]:
        """Return all discovered step IDs."""
        return set(self._steps.keys())

    @property
    def step_count(self) -> int:
        return len(self._steps)

    def has_step(self, step_id: str) -> bool:
        return step_id in self._steps

    def get_step_method_name(self, step_id: str) -> Optional[str]:
        info = self._steps.get(step_id)
        return info["method_name"] if info else None

    def list_steps(self) -> List[str]:
        """Return sorted list of step IDs."""
        return sorted(self._steps.keys())


# ---------------------------------------------------------------------------
# FlowResultValidator
# ---------------------------------------------------------------------------

_VALID_RESULT_TYPES = {
    FlowResultType.FORM,
    FlowResultType.CREATE_ENTRY,
    FlowResultType.ABORT,
    FlowResultType.MENU,
    FlowResultType.EXTERNAL_STEP,
    FlowResultType.EXTERNAL_STEP_DONE,
    FlowResultType.SHOW_PROGRESS,
    FlowResultType.SHOW_PROGRESS_DONE,
}


class FlowResultValidator:
    """Validate a FlowResult dict against structural rules."""

    def __init__(self, known_steps: Optional[Set[str]] = None) -> None:
        """
        Args:
            known_steps: set of step IDs that exist on the flow class.
                         Used to validate menu_options and step_id references.
        """
        self.known_steps = known_steps or set()
        self.errors: List[str] = []

    def validate(self, result: Dict[str, Any], *, context: str = "") -> bool:
        """Validate a single FlowResult. Returns True if valid."""
        self.errors = []
        prefix = f"[{context}] " if context else ""

        if not isinstance(result, dict):
            self.errors.append(f"{prefix}Result is not a dict: {type(result)}")
            return False

        result_type = result.get("type")
        if result_type is None:
            self.errors.append(f"{prefix}Result missing 'type' key")
            return False

        if result_type not in _VALID_RESULT_TYPES:
            self.errors.append(f"{prefix}Invalid result type: {result_type}")

        # Validate step_id references
        step_id = result.get("step_id")
        if result_type in (FlowResultType.FORM, FlowResultType.MENU):
            if not step_id:
                self.errors.append(f"{prefix}FORM/MENU result missing 'step_id'")

        # Validate menu_options reference real steps
        if result_type == FlowResultType.MENU and self.known_steps:
            menu_options = result.get("menu_options", [])
            for opt in menu_options:
                if opt not in self.known_steps:
                    self.errors.append(
                        f"{prefix}Menu option '{opt}' does not map to a known async_step_{opt} method"
                    )

        # Validate data_schema is a voluptuous schema
        if result_type == FlowResultType.FORM:
            schema = result.get("data_schema")
            if schema is not None:
                # voluptuous Schema objects have a .schema attribute
                if not hasattr(schema, "schema"):
                    self.errors.append(f"{prefix}data_schema is not a voluptuous Schema object")

        return len(self.errors) == 0

    def assert_valid(self, result: Dict[str, Any], *, context: str = "") -> None:
        """Validate and raise AssertionError if invalid."""
        if not self.validate(result, context=context):
            raise AssertionError(
                f"FlowResult validation failed:\n" + "\n".join(f"  - {e}" for e in self.errors)
            )


# ---------------------------------------------------------------------------
# MockHASSFactory
# ---------------------------------------------------------------------------

class MockHASSFactory:
    """Builder-pattern factory for creating mock Home Assistant objects."""

    @staticmethod
    def _loop_stand_in() -> Any:
        """A stand-in for ``hass.loop`` that never touches the global loop.

        ``asyncio.get_event_loop()`` here broke under pytest-asyncio >= 1.4,
        which unsets the main-thread loop between tests ("There is no current
        event loop in thread 'MainThread'").  Inside an async test the running
        loop is returned; in sync fixtures a mock is used whose ``create_task``
        closes the coroutine (no "never awaited" warnings) — no current test
        needs a hass.loop task to actually execute.
        """
        try:
            return asyncio.get_running_loop()
        except RuntimeError:
            loop = MagicMock()
            loop.time.return_value = 0.0

            def _consume(coro):
                coro.close()
                return MagicMock()

            loop.create_task.side_effect = _consume
            return loop

    @staticmethod
    def create_hass(**overrides) -> MagicMock:
        """Create a mock hass instance with sensible defaults."""
        hass = MagicMock()
        hass.data = overrides.get("data", {})
        hass.loop = MockHASSFactory._loop_stand_in()
        hass.config_entries = MagicMock()
        hass.config_entries.async_reload = AsyncMock()
        hass.states = MagicMock()
        hass.states.get = MagicMock(return_value=None)
        # Allow config to be accessed for location_name etc.
        hass.config = MagicMock()
        hass.config.location_name = "Home"
        return hass

    @staticmethod
    def create_config_entry(
        *,
        domain: str = "vomesync",
        entry_id: str = "test-entry-id",
        data: Optional[Dict[str, Any]] = None,
        options: Optional[Dict[str, Any]] = None,
    ) -> MagicMock:
        """Create a mock ConfigEntry."""
        entry = MagicMock(spec=ConfigEntry)
        entry.domain = domain
        entry.entry_id = entry_id
        entry.data = data or {
            "personal_key": "test-personal-key-uuid",
            "server_url": "https://test-server.com",
            "websocket_url": "wss://test-server.com",
        }
        entry.options = options or {
            "imported_switches": {},
            "linked_entities": {},
        }
        return entry

    @staticmethod
    def create_crypto_config_entry(
        *,
        domain: str = "vomesync",
        entry_id: str = "test-entry-id",
        crypto_seed: str = "test-seed",
        options: Optional[Dict[str, Any]] = None,
    ) -> MagicMock:
        """Create a mock ConfigEntry configured for v2 crypto auth."""
        entry = MockHASSFactory.create_config_entry(
            domain=domain,
            entry_id=entry_id,
            data={
                "personal_key": "",
                "server_url": "https://test-server.com",
                "websocket_url": "wss://test-server.com",
                "auth_mode": "crypto",
                "crypto_seed": crypto_seed,
            },
            options=options,
        )
        return entry

    @staticmethod
    def create_coordinator(
        *,
        switches: Optional[Dict[str, Any]] = None,
        subscriptions: Optional[Dict[str, Any]] = None,
    ) -> MagicMock:
        """Create a mock VomeSyncCoordinator."""
        coordinator = MagicMock()
        coordinator.switches = switches or {}
        coordinator.subscriptions = subscriptions or {}
        coordinator.create_switch = AsyncMock(return_value="new-uid-123")
        coordinator.delete_switch = AsyncMock(return_value=True)
        coordinator.subscribe_to_switch = AsyncMock(return_value=True)
        coordinator.update_switch_metadata = AsyncMock(return_value=True)
        coordinator.list_v2_access_keys = AsyncMock(return_value={"keys": [], "count": 0})
        coordinator.create_v2_access_key = AsyncMock(return_value={"apiKey": "test-key-123"})
        coordinator.revoke_v2_access_key = AsyncMock(return_value=True)
        coordinator.pause_v2_access_key = AsyncMock(return_value=True)
        coordinator.update_v2_access_key_permissions = AsyncMock(return_value=True)
        coordinator.reannounce_owned_switches = AsyncMock(return_value={
            "eligible": 0, "attempted": 0, "succeeded": 0, "skipped": 0, "errors": []
        })
        coordinator.async_add_imported_entities = AsyncMock()
        coordinator.async_setup_entity_links = AsyncMock()
        coordinator.api_client = MagicMock()
        coordinator.api_client.get_next_switch_name = AsyncMock(return_value="VomeSync Test")
        coordinator.api_client.create_session_token = AsyncMock(return_value={"token": "test-token"})
        coordinator.api_client.get_api_keys = AsyncMock(return_value=[])
        coordinator.api_client.create_api_key = AsyncMock(return_value={"apiKey": "new-api-key"})
        coordinator.api_client.delete_api_key = AsyncMock(return_value=True)
        coordinator.api_client.close = AsyncMock()
        return coordinator

    @staticmethod
    def create_entity_registry(
        entities: Optional[List[Dict[str, Any]]] = None,
    ) -> MagicMock:
        """Create a mock entity registry with optional entity list."""
        reg = MagicMock()
        mock_entities = []
        for ent_data in (entities or []):
            ent = MagicMock()
            ent.domain = ent_data.get("domain", "switch")
            ent.entity_id = ent_data.get("entity_id", "switch.test")
            ent.config_entry_id = ent_data.get("config_entry_id", "other-entry")
            ent.original_name = ent_data.get("original_name", "Test Entity")
            ent.unique_id = ent_data.get("unique_id", "")
            ent.device_id = ent_data.get("device_id", None)
            mock_entities.append(ent)
        reg.entities = MagicMock()
        reg.entities.values.return_value = mock_entities
        reg.async_get_entity_id = MagicMock(return_value=None)
        return reg

    @staticmethod
    def create_device_registry(
        devices: Optional[List[Dict[str, Any]]] = None,
    ) -> MagicMock:
        """Create a mock device registry."""
        reg = MagicMock()
        mock_devices = []
        for dev_data in (devices or []):
            dev = MagicMock()
            dev.id = dev_data.get("id", "dev-1")
            dev.name = dev_data.get("name", "Test Device")
            dev.name_by_user = dev_data.get("name_by_user", None)
            dev.identifiers = dev_data.get("identifiers", set())
            mock_devices.append(dev)
        reg.async_remove_device = MagicMock()
        return reg, mock_devices

    @staticmethod
    def wire_hass(
        hass: MagicMock,
        config_entry: MagicMock,
        coordinator: MagicMock,
        domain: str = "vomesync",
    ) -> None:
        """Wire a coordinator into hass.data for a config entry."""
        hass.data[domain] = {config_entry.entry_id: coordinator}


# ---------------------------------------------------------------------------
# FlowStepRunner
# ---------------------------------------------------------------------------

class FlowStepRunner:
    """Convenience wrapper to run flow steps with automatic validation."""

    def __init__(
        self,
        flow,
        *,
        validator: Optional[FlowResultValidator] = None,
        auto_validate: bool = True,
    ) -> None:
        self.flow = flow
        self.validator = validator or FlowResultValidator()
        self.auto_validate = auto_validate
        self._results: List[Dict[str, Any]] = []

    async def run_step(
        self,
        step_id: str,
        user_input: Optional[Dict[str, Any]] = None,
        *,
        validate: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """Run a specific flow step and optionally validate the result."""
        method_name = f"async_step_{step_id}"
        method = getattr(self.flow, method_name, None)
        if method is None:
            raise AttributeError(f"Flow has no method '{method_name}'")

        result = await method(user_input)
        self._results.append(result)

        should_validate = validate if validate is not None else self.auto_validate
        if should_validate and result is not None:
            self.validator.assert_valid(result, context=f"step={step_id}")

        return result

    @property
    def last_result(self) -> Optional[Dict[str, Any]]:
        return self._results[-1] if self._results else None

    @property
    def all_results(self) -> List[Dict[str, Any]]:
        return list(self._results)

    def assert_step_id(self, expected: str) -> None:
        """Assert the last result's step_id matches."""
        assert self.last_result is not None, "No results yet"
        assert self.last_result.get("step_id") == expected, (
            f"Expected step_id '{expected}', got '{self.last_result.get('step_id')}'"
        )

    def assert_type(self, expected: FlowResultType) -> None:
        """Assert the last result's type matches."""
        assert self.last_result is not None, "No results yet"
        assert self.last_result.get("type") == expected, (
            f"Expected type {expected}, got {self.last_result.get('type')}"
        )

    def assert_form(self, step_id: Optional[str] = None) -> Dict[str, Any]:
        """Assert last result is a FORM and optionally check step_id."""
        self.assert_type(FlowResultType.FORM)
        if step_id is not None:
            self.assert_step_id(step_id)
        return self.last_result

    def assert_menu(self, step_id: Optional[str] = None) -> Dict[str, Any]:
        """Assert last result is a MENU and optionally check step_id."""
        self.assert_type(FlowResultType.MENU)
        if step_id is not None:
            self.assert_step_id(step_id)
        return self.last_result

    def assert_create_entry(self) -> Dict[str, Any]:
        """Assert last result is CREATE_ENTRY."""
        self.assert_type(FlowResultType.CREATE_ENTRY)
        return self.last_result

    def assert_abort(self, reason: Optional[str] = None) -> Dict[str, Any]:
        """Assert last result is ABORT and optionally check reason."""
        self.assert_type(FlowResultType.ABORT)
        if reason is not None:
            assert self.last_result.get("reason") == reason, (
                f"Expected abort reason '{reason}', got '{self.last_result.get('reason')}'"
            )
        return self.last_result

