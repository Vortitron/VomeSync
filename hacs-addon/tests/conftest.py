# flake8: noqa
"""Pytest configuration for VomeSync tests.

Note: no custom ``event_loop`` fixture — pytest-asyncio >= 1.0 removed that
override mechanism, and mock loops are handled in MockHASSFactory instead.
"""
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
import pytest

from homeassistant.config_entries import ConfigEntry

# Ensure custom_components is importable when running tests directly
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from custom_components.vomesync.const import DOMAIN
from flow_test_framework import (
	FlowIntrospector,
	FlowResultValidator,
	FlowStepRunner,
	MockHASSFactory,
)


ADMIN_SERVICES: set = set()


@pytest.fixture(autouse=True)
def admin_services(monkeypatch):
	"""Stand in for Core's ``async_register_admin_service``.

	The pinned Core 2023.7 has no ``supports_response`` on it (2025.6 added
	that), and its wrapper would hide the handler.  This one registers the
	handler as-is, so the tests that collect handlers keep working, and
	records the name: ``ADMIN_SERVICES`` is what was registered admin-only.
	The real check of a household member being refused runs on a current
	Core in tests_core.
	"""
	ADMIN_SERVICES.clear()

	def _register(hass, domain, service, func, schema=None, supports_response=None):
		ADMIN_SERVICES.add(service)
		hass.services.async_register(
			domain, service, func, schema=schema, supports_response=supports_response,
		)

	monkeypatch.setattr(
		"custom_components.vomesync.services_remote.async_register_admin_service", _register,
	)
	return ADMIN_SERVICES


@pytest.fixture
def vome_app(monkeypatch):
	"""The Vome app is installed: YAML changes reach its real file handler.

	Only the HTTP hop is skipped.  The integration's request goes to
	``vome/panel/integration_channel.handle_files`` as the app would get
	it, and that applies the integration's own file_ops rules.  Returns the
	requests, ``[(path, payload), ...]``.
	"""
	sys.path.insert(0, str(ROOT / "vome" / "panel"))
	import integration_channel

	monkeypatch.setattr(
		integration_channel, "INTEGRATION_DIR", ROOT / "custom_components" / "vomesync",
	)
	requests = []

	async def _request(hass, path, payload):
		requests.append((path, payload))
		status, answer = integration_channel.handle_files(payload, hass.config.path())
		return (answer, None) if status == 200 else (None, answer["error"])

	monkeypatch.setattr("custom_components.vomesync.app_channel.async_request", _request)
	return requests


@pytest.fixture
def hass():
	"""Mock Home Assistant instance."""
	return MockHASSFactory.create_hass()


@pytest.fixture
def config_entry():
	"""Mock config entry."""
	return MockHASSFactory.create_config_entry()


@pytest.fixture
def crypto_config_entry():
	"""Mock config entry configured for v2 crypto auth."""
	return MockHASSFactory.create_crypto_config_entry()


@pytest.fixture
def mock_coordinator():
	"""Mock VomeSyncCoordinator with all common methods."""
	return MockHASSFactory.create_coordinator()


@pytest.fixture
def mock_entity_registry():
	"""Mock entity registry with no entities."""
	return MockHASSFactory.create_entity_registry()


@pytest.fixture
def flow_validator():
	"""FlowResultValidator with no known steps (generic)."""
	return FlowResultValidator()


@pytest.fixture
def mock_switch_data():
	"""Mock switch data."""
	return {
		"uid": "test-switch-uid",
		"name": "Test Switch",
		"description": "Test Switch",
		"location": "Test City",
		"category": "Test",
		"state": False,
		"lastToggled": 1640995200000,
		"createdAt": 1640995100000,
		"toggleCount": 0,
		"publicize": False
	}


@pytest.fixture
def mock_api_response():
	"""Mock API response."""
	return {
		"success": True,
		"data": {
			"uid": "test-switch-uid",
			"description": "Test Switch",
			"state": False
		}
	}


@pytest.fixture
def mock_websocket_message():
	"""Mock WebSocket message."""
	return {
		"type": "state_update",
		"uid": "test-switch-uid",
		"state": True,
		"timestamp": 1640995200000
	}
