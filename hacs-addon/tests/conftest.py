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
