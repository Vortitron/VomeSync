"""Tests for VomeSync API client."""
import asyncio
import json
from unittest.mock import AsyncMock, patch, MagicMock
import pytest
import aiohttp

from custom_components.vomesync.api_client import VomeSyncAPIClient, VomeSyncAPIError


class TestVomeSyncAPIClient:
    """Test VomeSync API client functionality."""

    @pytest.fixture
    def api_client(self):
        """Create API client for testing."""
        return VomeSyncAPIClient("https://test-server.com", "test-personal-key")

    @pytest.fixture
    def mock_session(self):
        """Mock aiohttp session."""
        return AsyncMock(spec=aiohttp.ClientSession)

    @pytest.mark.asyncio
    async def test_generate_personal_key_success(self, api_client, mock_session):
        """Test successful personal key generation."""
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json.return_value = {
            "success": True,
            "data": {
                "personalKey": "test-uuid-key",
                "jwt": "test-jwt-token",
                "expiresIn": "1 year"
            }
        }
        mock_response.text.return_value = json.dumps(mock_response.json.return_value)
        mock_response.content_type = "application/json"
        
        mock_session.request.return_value.__aenter__.return_value = mock_response
        
        with patch.object(api_client, '_get_session', return_value=mock_session):
            result = await api_client.generate_personal_key()
            
        assert result["personalKey"] == "test-uuid-key"
        assert result["jwt"] == "test-jwt-token"
        mock_session.request.assert_called_once_with(
            "POST",
            "https://test-server.com/api/generate-key",
            json={"consent": True},
            headers={}
        )

    @pytest.mark.asyncio
    async def test_generate_personal_key_failure(self, api_client, mock_session):
        """Test personal key generation failure."""
        mock_response = AsyncMock()
        mock_response.status = 400
        mock_response.json.return_value = {
            "success": False,
            "error": "Consent required"
        }
        mock_response.text.return_value = json.dumps(mock_response.json.return_value)
        mock_response.content_type = "application/json"
        
        mock_session.request.return_value.__aenter__.return_value = mock_response
        
        with patch.object(api_client, '_get_session', return_value=mock_session):
            with pytest.raises(VomeSyncAPIError, match="API request failed"):
                await api_client.generate_personal_key()

    @pytest.mark.asyncio
    async def test_validate_personal_key_valid(self, api_client, mock_session):
        """Test valid personal key validation."""
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json.return_value = {
            "success": True,
            "data": {"switches": []}
        }
        mock_response.text.return_value = json.dumps(mock_response.json.return_value)
        mock_response.content_type = "application/json"
        
        mock_session.request.return_value.__aenter__.return_value = mock_response
        
        with patch.object(api_client, '_get_session', return_value=mock_session):
            result = await api_client.validate_personal_key("valid-key")
            
        assert result is True

    @pytest.mark.asyncio
    async def test_validate_personal_key_invalid(self, api_client, mock_session):
        """Test invalid personal key validation."""
        mock_response = AsyncMock()
        mock_response.status = 401
        mock_response.json.return_value = {
            "success": False,
            "error": "Invalid personal key"
        }
        mock_response.text.return_value = json.dumps(mock_response.json.return_value)
        mock_response.content_type = "application/json"
        
        mock_session.request.return_value.__aenter__.return_value = mock_response
        
        with patch.object(api_client, '_get_session', return_value=mock_session):
            result = await api_client.validate_personal_key("invalid-key")
            
        assert result is False

    @pytest.mark.asyncio
    async def test_create_switch_success(self, api_client, mock_session):
        """Test successful switch creation."""
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json.return_value = {
            "success": True,
            "data": {
                "uid": "test-switch-uid",
                "description": "Test Switch",
                "location": "Test City",
                "category": "Test",
                "state": False,
                "publicize": True
            }
        }
        mock_response.text.return_value = json.dumps(mock_response.json.return_value)
        mock_response.content_type = "application/json"
        
        mock_session.request.return_value.__aenter__.return_value = mock_response
        
        with patch.object(api_client, '_get_session', return_value=mock_session):
            result = await api_client.create_switch(
                description="Test Switch",
                location="Test City",
                category="Test",
                publicize=True
            )
            
        assert result["uid"] == "test-switch-uid"
        assert result["description"] == "Test Switch"
        assert result["publicize"] is True

    @pytest.mark.asyncio
    async def test_toggle_switch_success(self, api_client, mock_session):
        """Test successful switch toggle."""
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json.return_value = {
            "success": True,
            "data": {
                "uid": "test-switch-uid",
                "state": True,
                "timestamp": 1640995200000
            }
        }
        mock_response.text.return_value = json.dumps(mock_response.json.return_value)
        mock_response.content_type = "application/json"
        
        mock_session.request.return_value.__aenter__.return_value = mock_response
        
        with patch.object(api_client, '_get_session', return_value=mock_session):
            result = await api_client.toggle_switch("test-switch-uid")
            
        assert result["uid"] == "test-switch-uid"
        assert result["state"] is True
        assert result["timestamp"] == 1640995200000

    @pytest.mark.asyncio
    async def test_get_switch_status_success(self, api_client, mock_session):
        """Test successful switch status retrieval."""
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json.return_value = {
            "success": True,
            "data": {
                "uid": "test-switch-uid",
                "description": "Test Switch",
                "state": True,
                "lastToggled": 1640995200000
            }
        }
        mock_response.text.return_value = json.dumps(mock_response.json.return_value)
        mock_response.content_type = "application/json"
        
        mock_session.request.return_value.__aenter__.return_value = mock_response
        
        with patch.object(api_client, '_get_session', return_value=mock_session):
            result = await api_client.get_switch_status("test-switch-uid")
            
        assert result["uid"] == "test-switch-uid"
        assert result["state"] is True

    @pytest.mark.asyncio
    async def test_get_switch_status_not_found(self, api_client, mock_session):
        """Test switch status for non-existent switch."""
        mock_response = AsyncMock()
        mock_response.status = 404
        mock_response.json.return_value = {
            "success": False,
            "error": "Switch not found"
        }
        mock_response.text.return_value = json.dumps(mock_response.json.return_value)
        mock_response.content_type = "application/json"
        
        mock_session.request.return_value.__aenter__.return_value = mock_response
        
        with patch.object(api_client, '_get_session', return_value=mock_session):
            result = await api_client.get_switch_status("non-existent-uid")
            
        assert result is None

    @pytest.mark.asyncio
    async def test_get_my_switches_success(self, api_client, mock_session):
        """Test successful retrieval of user switches."""
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json.return_value = {
            "success": True,
            "data": {
                "switches": [
                    {
                        "uid": "switch-1",
                        "description": "Switch 1",
                        "state": False
                    },
                    {
                        "uid": "switch-2",
                        "description": "Switch 2",
                        "state": True
                    }
                ]
            }
        }
        mock_response.text.return_value = json.dumps(mock_response.json.return_value)
        mock_response.content_type = "application/json"
        
        mock_session.request.return_value.__aenter__.return_value = mock_response
        
        with patch.object(api_client, '_get_session', return_value=mock_session):
            result = await api_client.get_my_switches()
            
        assert len(result) == 2
        assert result[0]["uid"] == "switch-1"
        assert result[1]["uid"] == "switch-2"

    @pytest.mark.asyncio
    async def test_get_public_switches_success(self, api_client, mock_session):
        """Test successful retrieval of public switches."""
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json.return_value = {
            "success": True,
            "data": {
                "switches": [
                    {
                        "uid": "public-switch-1",
                        "description": "Public Switch 1",
                        "state": True,
                        "category": "Community"
                    }
                ]
            }
        }
        mock_response.text.return_value = json.dumps(mock_response.json.return_value)
        mock_response.content_type = "application/json"
        
        mock_session.request.return_value.__aenter__.return_value = mock_response
        
        with patch.object(api_client, '_get_session', return_value=mock_session):
            result = await api_client.get_public_switches()
            
        assert len(result) == 1
        assert result[0]["uid"] == "public-switch-1"
        assert result[0]["category"] == "Community"

    @pytest.mark.asyncio
    async def test_delete_switch_success(self, api_client, mock_session):
        """Test successful switch deletion."""
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json.return_value = {
            "success": True,
            "data": {
                "message": "Switch deleted successfully",
                "uid": "test-switch-uid"
            }
        }
        mock_response.text.return_value = json.dumps(mock_response.json.return_value)
        mock_response.content_type = "application/json"
        
        mock_session.request.return_value.__aenter__.return_value = mock_response
        
        with patch.object(api_client, '_get_session', return_value=mock_session):
            result = await api_client.delete_switch("test-switch-uid")
            
        assert result is True

    @pytest.mark.asyncio
    async def test_health_check_success(self, api_client, mock_session):
        """Test successful health check."""
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json.return_value = {
            "status": "healthy",
            "timestamp": 1640995200000,
            "redis": True
        }
        mock_response.text.return_value = json.dumps(mock_response.json.return_value)
        mock_response.content_type = "application/json"
        
        mock_session.request.return_value.__aenter__.return_value = mock_response
        
        with patch.object(api_client, '_get_session', return_value=mock_session):
            result = await api_client.health_check()
            
        assert result is True

    @pytest.mark.asyncio
    async def test_health_check_failure(self, api_client, mock_session):
        """Test health check failure."""
        mock_response = AsyncMock()
        mock_response.status = 500
        mock_response.json.return_value = {
            "status": "unhealthy"
        }
        mock_response.text.return_value = json.dumps(mock_response.json.return_value)
        mock_response.content_type = "application/json"
        
        mock_session.request.return_value.__aenter__.return_value = mock_response
        
        with patch.object(api_client, '_get_session', return_value=mock_session):
            result = await api_client.health_check()
            
        assert result is False

    @pytest.mark.asyncio
    async def test_network_error_handling(self, api_client, mock_session):
        """Test network error handling."""
        mock_session.request.side_effect = aiohttp.ClientError("Network error")
        
        with patch.object(api_client, '_get_session', return_value=mock_session):
            with pytest.raises(VomeSyncAPIError, match="Network error"):
                await api_client.generate_personal_key()

    @pytest.mark.asyncio
    async def test_timeout_error_handling(self, api_client, mock_session):
        """Test timeout error handling."""
        mock_session.request.side_effect = asyncio.TimeoutError()
        
        with patch.object(api_client, '_get_session', return_value=mock_session):
            with pytest.raises(VomeSyncAPIError, match="Request timeout"):
                await api_client.generate_personal_key()

    @pytest.mark.asyncio
    async def test_session_management(self, api_client):
        """Test session creation and management."""
        # Test session creation
        session = await api_client._get_session()
        assert session is not None
        
        # Test session reuse
        session2 = await api_client._get_session()
        assert session is session2
        
        # Test session close
        await api_client.close()

    @pytest.mark.asyncio
    async def test_authentication_headers(self, api_client, mock_session):
        """Test authentication headers are properly set."""
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json.return_value = {"success": True, "data": {"switches": []}}
        mock_response.text.return_value = json.dumps(mock_response.json.return_value)
        mock_response.content_type = "application/json"
        
        mock_session.request.return_value.__aenter__.return_value = mock_response
        
        with patch.object(api_client, '_get_session', return_value=mock_session):
            await api_client.get_my_switches()
            
        # Verify authentication header was set
        call_args = mock_session.request.call_args
        headers = call_args[1]['headers']
        assert 'X-Personal-Key' in headers
        assert headers['X-Personal-Key'] == 'test-personal-key'

    @pytest.mark.asyncio
    async def test_request_data_with_auth(self, api_client, mock_session):
        """Test request data includes personal key when authentication required."""
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json.return_value = {
            "success": True,
            "data": {"uid": "test-uid", "state": True}
        }
        mock_response.text.return_value = json.dumps(mock_response.json.return_value)
        mock_response.content_type = "application/json"
        
        mock_session.request.return_value.__aenter__.return_value = mock_response
        
        with patch.object(api_client, '_get_session', return_value=mock_session):
            await api_client.toggle_switch("test-uid")
            
        # Verify personal key was included in request data
        call_args = mock_session.request.call_args
        request_data = call_args[1]['json']
        assert 'personalKey' in request_data
        assert request_data['personalKey'] == 'test-personal-key'
