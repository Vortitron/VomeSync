"""Tests for VomeSync coordinator."""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.vomesync.coordinator import VomeSyncCoordinator
from custom_components.vomesync.api_client import VomeSyncAPIError
from custom_components.vomesync.const import (
    CONF_PERSONAL_KEY,
    CONF_SERVER_URL,
    CONF_WEBSOCKET_URL,
    DOMAIN
)


class TestVomeSyncCoordinator:
    """Test VomeSync coordinator functionality."""

    @pytest.fixture
    def hass(self):
        """Mock Home Assistant instance."""
        return MagicMock(spec=HomeAssistant)

    @pytest.fixture
    def config_entry(self):
        """Mock config entry."""
        entry = MagicMock(spec=ConfigEntry)
        entry.data = {
            CONF_PERSONAL_KEY: "test-personal-key",
            CONF_SERVER_URL: "https://test-server.com",
            CONF_WEBSOCKET_URL: "wss://test-server.com"
        }
        entry.options = {
            "switches": {},
            "subscriptions": {}
        }
        return entry

    @pytest.fixture
    def coordinator(self, hass, config_entry):
        """Create coordinator for testing."""
        return VomeSyncCoordinator(hass, config_entry)

    @pytest.fixture
    def mock_api_client(self):
        """Mock API client."""
        return AsyncMock()

    @pytest.fixture
    def mock_websocket_client(self):
        """Mock WebSocket client."""
        return AsyncMock()

    @pytest.mark.asyncio
    async def test_coordinator_initialization(self, coordinator, hass, config_entry):
        """Test coordinator initialization."""
        assert coordinator.hass is hass
        assert coordinator.config_entry is config_entry
        assert coordinator.personal_key == "test-personal-key"
        assert coordinator.server_url == "https://test-server.com"
        assert coordinator.switches == {}
        assert coordinator.subscriptions == {}

    @pytest.mark.asyncio
    async def test_async_update_data_success(self, coordinator):
        """Test successful data update."""
        # Mock API responses
        my_switches = [
            {
                "uid": "switch-1",
                "description": "Switch 1",
                "state": False,
                "lastToggled": 1640995200000
            }
        ]
        
        with patch.object(coordinator.api_client, 'get_my_switches', return_value=my_switches), \
             patch.object(coordinator.api_client, 'get_switch_status', return_value=None), \
             patch.object(coordinator, '_ensure_websocket_connection'):
            
            result = await coordinator._async_update_data()
            
        assert result["switches"]["switch-1"]["description"] == "Switch 1"
        assert "last_update" in result

    @pytest.mark.asyncio
    async def test_async_update_data_with_subscriptions(self, coordinator):
        """Test data update with subscriptions."""
        # Add subscription to options
        coordinator.config_entry.options = {
            "subscriptions": {
                "Remote Switch": {
                    "uid": "sub-switch-1",
                    "is_owner": False
                }
            }
        }
        
        my_switches = []
        switch_status = {
            "uid": "sub-switch-1",
            "description": "Remote Switch",
            "state": True,
            "lastToggled": 1640995200000
        }
        
        with patch.object(coordinator.api_client, 'get_my_switches', return_value=my_switches), \
             patch.object(coordinator.api_client, 'get_switch_status', return_value=switch_status), \
             patch.object(coordinator, '_ensure_websocket_connection'):
            
            result = await coordinator._async_update_data()
            
        assert "sub-switch-1" in result["subscriptions"]
        assert result["subscriptions"]["sub-switch-1"]["name"] == "Remote Switch"
        assert result["subscriptions"]["sub-switch-1"]["is_owner"] is False

    @pytest.mark.asyncio
    async def test_async_update_data_api_error(self, coordinator):
        """Test data update with API error."""
        with patch.object(coordinator.api_client, 'get_my_switches', 
                         side_effect=VomeSyncAPIError("API Error")):
            
            with pytest.raises(UpdateFailed, match="Error communicating with VomeSync API"):
                await coordinator._async_update_data()

    @pytest.mark.asyncio
    async def test_toggle_switch_success(self, coordinator):
        """Test successful switch toggle."""
        # Setup initial data
        coordinator.switches = {
            "switch-1": {
                "uid": "switch-1",
                "state": False,
                "lastToggled": 1640995200000
            }
        }
        
        toggle_result = {
            "uid": "switch-1",
            "state": True,
            "timestamp": 1640995300000
        }
        
        with patch.object(coordinator.api_client, 'toggle_switch', return_value=toggle_result):
            result = await coordinator.toggle_switch("switch-1")
            
        assert result is True
        assert coordinator.switches["switch-1"]["state"] is True
        assert coordinator.switches["switch-1"]["lastToggled"] == 1640995300000

    @pytest.mark.asyncio
    async def test_toggle_switch_api_error(self, coordinator):
        """Test switch toggle with API error."""
        with patch.object(coordinator.api_client, 'toggle_switch', 
                         side_effect=VomeSyncAPIError("Toggle failed")):
            
            result = await coordinator.toggle_switch("switch-1")
            
        assert result is False

    @pytest.mark.asyncio
    async def test_create_switch_success(self, coordinator):
        """Test successful switch creation."""
        create_result = {
            "uid": "new-switch-uid",
            "description": "New Switch",
            "location": "Test City",
            "category": "Test",
            "state": False,
            "publicize": False
        }
        
        with patch.object(coordinator.api_client, 'create_switch', return_value=create_result), \
             patch.object(coordinator, '_ensure_websocket_connection'), \
             patch.object(coordinator.hass.config_entries, 'async_update_entry'):
            
            uid = await coordinator.create_switch(
                name="New Switch",
                description="New Switch",
                location="Test City",
                category="Test",
                publicize=False
            )
            
        assert uid == "new-switch-uid"
        assert "new-switch-uid" in coordinator.switches
        assert coordinator.switches["new-switch-uid"]["name"] == "New Switch"

    @pytest.mark.asyncio
    async def test_create_switch_api_error(self, coordinator):
        """Test switch creation with API error."""
        with patch.object(coordinator.api_client, 'create_switch',
                         side_effect=VomeSyncAPIError("Creation failed")):
            
            uid = await coordinator.create_switch(
                name="New Switch",
                description="New Switch"
            )
            
        assert uid is None

    @pytest.mark.asyncio
    async def test_subscribe_to_switch_success(self, coordinator):
        """Test successful switch subscription."""
        switch_status = {
            "uid": "remote-switch-uid",
            "description": "Remote Switch",
            "state": True,
            "lastToggled": 1640995200000
        }
        
        with patch.object(coordinator.api_client, 'get_switch_status', return_value=switch_status), \
             patch.object(coordinator, '_ensure_websocket_connection'), \
             patch.object(coordinator.hass.config_entries, 'async_update_entry'):
            
            result = await coordinator.subscribe_to_switch("Remote Switch", "remote-switch-uid")
            
        assert result is True
        assert "remote-switch-uid" in coordinator.subscriptions
        assert coordinator.subscriptions["remote-switch-uid"]["name"] == "Remote Switch"

    @pytest.mark.asyncio
    async def test_subscribe_to_switch_not_found(self, coordinator):
        """Test subscription to non-existent switch."""
        with patch.object(coordinator.api_client, 'get_switch_status', return_value=None):
            
            result = await coordinator.subscribe_to_switch("Non-existent", "bad-uid")
            
        assert result is False

    @pytest.mark.asyncio
    async def test_delete_switch_success(self, coordinator):
        """Test successful switch deletion."""
        # Setup initial data
        coordinator.switches = {
            "switch-1": {"uid": "switch-1", "description": "Switch 1"}
        }
        coordinator._websocket_connections = {"switch-1": True}
        
        with patch.object(coordinator.api_client, 'delete_switch', return_value=True), \
             patch.object(coordinator.websocket_client, 'unsubscribe'), \
             patch.object(coordinator.hass.config_entries, 'async_update_entry'):
            
            result = await coordinator.delete_switch("switch-1")
            
        assert result is True
        assert "switch-1" not in coordinator.switches

    @pytest.mark.asyncio
    async def test_delete_switch_api_error(self, coordinator):
        """Test switch deletion with API error."""
        with patch.object(coordinator.api_client, 'delete_switch',
                         side_effect=VomeSyncAPIError("Deletion failed")):
            
            result = await coordinator.delete_switch("switch-1")
            
        assert result is False

    @pytest.mark.asyncio
    async def test_handle_websocket_message_state_update(self, coordinator):
        """Test WebSocket state update handling."""
        # Setup initial data
        coordinator.switches = {
            "switch-1": {"uid": "switch-1", "state": False, "lastToggled": 0}
        }
        coordinator.subscriptions = {
            "sub-1": {"uid": "sub-1", "state": False, "lastToggled": 0}
        }
        
        message = {
            "type": "state_update",
            "state": True,
            "timestamp": 1640995200000
        }
        
        # Mock async_update_listeners
        coordinator.async_update_listeners = MagicMock()
        
        await coordinator._handle_websocket_message("switch-1", message)
        
        assert coordinator.switches["switch-1"]["state"] is True
        assert coordinator.switches["switch-1"]["lastToggled"] == 1640995200000
        coordinator.async_update_listeners.assert_called_once()

    @pytest.mark.asyncio
    async def test_handle_websocket_message_error(self, coordinator):
        """Test WebSocket error message handling."""
        message = {
            "type": "error",
            "message": "Switch not found"
        }
        
        # Should not raise exception
        await coordinator._handle_websocket_message("switch-1", message)

    @pytest.mark.asyncio
    async def test_ensure_websocket_connection_success(self, coordinator):
        """Test WebSocket connection establishment."""
        with patch.object(coordinator.websocket_client, 'subscribe') as mock_subscribe:
            await coordinator._ensure_websocket_connection("switch-1")
            
        mock_subscribe.assert_called_once_with("switch-1")
        assert coordinator._websocket_connections["switch-1"] is True

    @pytest.mark.asyncio
    async def test_ensure_websocket_connection_failure(self, coordinator):
        """Test WebSocket connection failure handling."""
        with patch.object(coordinator.websocket_client, 'subscribe',
                         side_effect=Exception("Connection failed")):
            
            await coordinator._ensure_websocket_connection("switch-1")
            
        assert coordinator._websocket_connections["switch-1"] is False

    @pytest.mark.asyncio
    async def test_get_switch_data(self, coordinator):
        """Test switch data retrieval."""
        coordinator.switches = {
            "switch-1": {"uid": "switch-1", "description": "Switch 1"}
        }
        coordinator.subscriptions = {
            "sub-1": {"uid": "sub-1", "description": "Subscription 1"}
        }
        
        # Test owned switch
        data = coordinator.get_switch_data("switch-1")
        assert data["description"] == "Switch 1"
        
        # Test subscribed switch
        data = coordinator.get_switch_data("sub-1")
        assert data["description"] == "Subscription 1"
        
        # Test non-existent switch
        data = coordinator.get_switch_data("non-existent")
        assert data is None

    @pytest.mark.asyncio
    async def test_is_switch_owner(self, coordinator):
        """Test switch ownership check."""
        coordinator.switches = {
            "owned-switch": {"uid": "owned-switch", "is_owner": True}
        }
        coordinator.subscriptions = {
            "subscribed-switch": {"uid": "subscribed-switch", "is_owner": False}
        }
        
        assert coordinator.is_switch_owner("owned-switch") is True
        assert coordinator.is_switch_owner("subscribed-switch") is False
        assert coordinator.is_switch_owner("non-existent") is False

    @pytest.mark.asyncio
    async def test_async_shutdown(self, coordinator):
        """Test coordinator shutdown."""
        with patch.object(coordinator.websocket_client, 'disconnect') as mock_ws_disconnect, \
             patch.object(coordinator.api_client, 'close') as mock_api_close:
            
            await coordinator.async_shutdown()
            
        mock_ws_disconnect.assert_called_once()
        mock_api_close.assert_called_once()
