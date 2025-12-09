"""API client for VomeSync server communication."""
import asyncio
import json
import logging
from typing import Any, Dict, Optional
import aiohttp
from aiohttp import ClientTimeout, ClientError

from .const import (
	API_GENERATE_KEY,
	API_CREATE_SWITCH,
	API_TOGGLE_SWITCH,
	API_GET_STATUS,
	API_MY_SWITCHES,
	API_PUBLIC_SWITCHES,
)

_LOGGER = logging.getLogger(__name__)


class VomeSyncAPIError(Exception):
	"""VomeSync API error."""


class VomeSyncAPIClient:
	"""Client for VomeSync API communication."""

	def __init__(self, server_url: str, personal_key: Optional[str] = None) -> None:
		"""Initialize the API client."""
		self.server_url = server_url.rstrip("/")
		self.personal_key = personal_key
		self.session: Optional[aiohttp.ClientSession] = None
		self._timeout = ClientTimeout(total=30, connect=10)

	async def _get_session(self) -> aiohttp.ClientSession:
		"""Get or create aiohttp session."""
		if self.session is None or self.session.closed:
			self.session = aiohttp.ClientSession(
				timeout=self._timeout,
				headers={
					"Content-Type": "application/json",
					"User-Agent": "VomeSync-HomeAssistant/1.0",
				}
			)
		return self.session

	async def close(self) -> None:
		"""Close the session."""
		if self.session and not self.session.closed:
			await self.session.close()

	async def _make_request(
		self,
		method: str,
		endpoint: str,
		data: Optional[Dict[str, Any]] = None,
		require_auth: bool = False,
	) -> Dict[str, Any]:
		"""Make an API request."""
		url = f"{self.server_url}{endpoint}"
		
		session = await self._get_session()
		
		headers = {}
		if require_auth and self.personal_key:
			headers["X-Personal-Key"] = self.personal_key
		
		if require_auth and self.personal_key:
			if data is None:
				data = {}
			data["personalKey"] = self.personal_key

		try:
			_LOGGER.debug("Making %s request to %s", method, endpoint)
			
			async with session.request(
				method,
				url,
				json=data,
				headers=headers
			) as response:
				response_text = await response.text()
				
				if response.content_type == "application/json":
					response_data = await response.json()
				else:
					response_data = {"error": "Invalid response format", "text": response_text}

				if response.status >= 400:
					error_msg = response_data.get("error", f"HTTP {response.status}")
					_LOGGER.error("API request failed (%s %s): %s", method, url, error_msg)
					raise VomeSyncAPIError(f"API request failed: {error_msg}")

				if not response_data.get("success", True):
					error_msg = response_data.get("error", "Unknown error")
					_LOGGER.error("API returned error (%s %s): %s", method, url, error_msg)
					raise VomeSyncAPIError(f"API error: {error_msg}")

				return response_data.get("data", response_data)

		except ClientError as ex:
			_LOGGER.error("Network error during API request: %s", ex)
			raise VomeSyncAPIError(f"Network error: {ex}") from ex
		except asyncio.TimeoutError as ex:
			_LOGGER.error("Timeout during API request")
			raise VomeSyncAPIError("Request timeout") from ex

	async def generate_personal_key(self) -> Dict[str, Any]:
		"""Generate a new personal key."""
		data = {"consent": True}
		return await self._make_request("POST", API_GENERATE_KEY, data)

	async def validate_personal_key(self, personal_key: str) -> bool:
		"""Validate a personal key by making an authenticated request."""
		previous_key = self.personal_key
		self.personal_key = personal_key
		try:
			await self._make_request("GET", API_MY_SWITCHES, require_auth=True)
			return True
		except VomeSyncAPIError:
			return False
		finally:
			self.personal_key = previous_key

	async def create_switch(
		self,
		description: str = "",
		location: str = "",
		category: str = "Other",
		publicize: bool = False,
	) -> Dict[str, Any]:
		"""Create a new switch."""
		data = {
			"description": description,
			"location": location,
			"category": category,
			"publicize": publicize,
		}
		return await self._make_request("POST", API_CREATE_SWITCH, data, require_auth=True)

	async def toggle_switch(self, uid: str) -> Dict[str, Any]:
		"""Toggle a switch."""
		endpoint = API_TOGGLE_SWITCH.format(uid=uid)
		return await self._make_request("POST", endpoint, {}, require_auth=True)

	async def get_switch_status(self, uid: str) -> Optional[Dict[str, Any]]:
		"""Get switch status (public endpoint)."""
		try:
			endpoint = API_GET_STATUS.format(uid=uid)
			return await self._make_request("GET", endpoint)
		except VomeSyncAPIError:
			return None

	async def get_my_switches(self) -> list[Dict[str, Any]]:
		"""Get user's switches."""
		try:
			response = await self._make_request("GET", API_MY_SWITCHES, require_auth=True)
			return response.get("switches", [])
		except VomeSyncAPIError:
			return []

	async def get_public_switches(self) -> list[Dict[str, Any]]:
		"""Get public switches."""
		try:
			response = await self._make_request("GET", API_PUBLIC_SWITCHES)
			return response.get("switches", [])
		except VomeSyncAPIError:
			return []

	async def delete_switch(self, uid: str) -> bool:
		"""Delete a switch."""
		try:
			endpoint = f"/api/switch/{uid}"
			await self._make_request("DELETE", endpoint, require_auth=True)
			return True
		except VomeSyncAPIError:
			return False

	# API key management
	async def list_api_keys(self) -> list[Dict[str, Any]]:
		return await self._make_request("GET", "/api/api-keys", require_auth=True)

	async def create_api_key(self, name: str = "") -> Dict[str, Any]:
		return await self._make_request("POST", "/api/api-keys", {"name": name}, require_auth=True)

	async def delete_api_key(self, api_key: str) -> bool:
		try:
			await self._make_request("DELETE", f"/api/api-keys/{api_key}", require_auth=True)
			return True
		except VomeSyncAPIError:
			return False

	# Session tokens
	async def create_session_token(self) -> Dict[str, Any]:
		return await self._make_request("POST", "/api/session-token", {}, require_auth=True)

	async def health_check(self) -> bool:
		"""Check server health."""
		try:
			await self._make_request("GET", "/api/health")
			return True
		except VomeSyncAPIError:
			return False
