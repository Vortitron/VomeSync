"""Pytest configuration for VomeSync tests."""
import asyncio
from unittest.mock import MagicMock
import pytest

from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry

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
    hass_mock = MagicMock(spec=HomeAssistant)
    hass_mock.data = {}
    hass_mock.loop = asyncio.get_event_loop()
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
        "switches": {},
        "subscriptions": {}
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
