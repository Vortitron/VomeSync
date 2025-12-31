"""Pytest configuration for VomeSync tests."""
import asyncio
import sys
from pathlib import Path
from unittest.mock import MagicMock
import pytest

from homeassistant.config_entries import ConfigEntry

# Ensure custom_components is importable when running tests directly
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from custom_components.vomesync.const import DOMAIN


@pytest.fixture
def event_loop():
	"""Create an instance of the default event loop for the test session."""
	loop = asyncio.get_event_loop_policy().new_event_loop()
	yield loop
	loop.close()


@pytest.fixture
def hass():
	"""Mock Home Assistant instance."""
	hass_mock = MagicMock()
	hass_mock.data = {}
	hass_mock.loop = asyncio.get_event_loop()
	hass_mock.config_entries = MagicMock()
	return hass_mock


@pytest.fixture
def config_entry():
	"""Mock config entry."""
	entry = MagicMock(spec=ConfigEntry)
	entry.domain = DOMAIN
	entry.data = {
		"personal_key": "test-personal-key-uuid",
		"server_url": "https://test-server.com",
		"websocket_url": "wss://test-server.com"
	}
	entry.options = {
		"imported_switches": {},
		"linked_entities": {}
	}
	entry.entry_id = "test-entry-id"
	return entry


@pytest.fixture
def mock_switch_data():
	"""Mock switch data."""
	return {
		"uid": "test-switch-uid",
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
