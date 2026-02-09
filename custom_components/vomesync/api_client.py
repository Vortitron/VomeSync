"""API client for VomeSync server communication."""
import asyncio
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
	API_V2_CREATE_SWITCH,
	API_V2_MY_SWITCHES,
	API_V2_SET_STATE,
	API_V2_UPDATE_SWITCH,
	API_V2_ACCESS_KEYS_CREATE,
	API_V2_ACCESS_KEYS_LIST,
	API_V2_ACCESS_KEYS_REVOKE,
	API_V2_ACCESS_KEYS_PAUSE,
	API_V2_ACCESS_KEYS_PERMISSIONS,
	API_V2_TOGGLE,
	AUTH_MODE_CRYPTO,
)

from .crypto import (
	build_v2_create_switch_request,
	build_v2_my_switches_request,
	build_v2_set_state_request,
	build_v2_update_switch_request,
	build_v2_create_access_key_request,
	build_v2_list_access_keys_request,
	build_v2_revoke_access_key_request,
	build_v2_pause_access_key_request,
	build_v2_update_access_key_permissions_request,
)

_LOGGER = logging.getLogger(__name__)


class VomeSyncAPIError(Exception):
	"""VomeSync API error."""


class VomeSyncAPIClient:
	"""Client for VomeSync API communication."""

	def __init__(
		self,
		server_url: str,
		personal_key: Optional[str] = None,
		auth_mode: Optional[str] = None,
		crypto_seed: Optional[str] = None,
	) -> None:
		"""Initialize the API client."""
		self.server_url = server_url.rstrip("/")
		self.personal_key = personal_key
		self.auth_mode = auth_mode
		self.crypto_seed = crypto_seed
		self.session: Optional[aiohttp.ClientSession] = None
		self._timeout = ClientTimeout(total=30, connect=10)

	@property
	def crypto_enabled(self) -> bool:
		return bool(self.auth_mode == AUTH_MODE_CRYPTO and self.crypto_seed)

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
		extra_headers: Optional[Dict[str, str]] = None,
	) -> Dict[str, Any]:
		"""Make an API request."""
		url = f"{self.server_url}{endpoint}"
		
		session = await self._get_session()
		
		headers: Dict[str, str] = {}
		if require_auth and self.personal_key:
			headers["X-Personal-Key"] = self.personal_key
		
		if require_auth and self.personal_key:
			if data is None:
				data = {}
			data["personalKey"] = self.personal_key
		
		if extra_headers:
			headers.update(extra_headers)

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
					details = response_data.get("details")
					if isinstance(details, list) and details:
						detail_messages = []
						for detail in details:
							if isinstance(detail, dict):
								message = detail.get("message") or detail.get("field")
							else:
								message = str(detail)
							if message:
								detail_messages.append(str(message))
						if detail_messages:
							error_msg = f"{error_msg} ({'; '.join(detail_messages)})"
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

	async def get_next_switch_name(self) -> str:
		"""Get a globally unique switch name from the server."""
		try:
			result = await self._make_request("GET", "/api/next-switch-name")
			return result.get("name", "VomeSync Switch")
		except VomeSyncAPIError:
			# Fallback if server doesn't support this endpoint yet
			return "VomeSync Switch"

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
		name: str = "",
		description: str = "",
		location: str = "",
		category: str = "Other",
		publicize: bool = False,
		link: str = "",
		captcha_token: str = "",
	) -> Dict[str, Any]:
		"""Create a new switch."""
		if self.crypto_enabled:
			raise VomeSyncAPIError("Crypto mode enabled; use create_switch_v2()")
		
		data = {
			"name": name,
			"description": description,
			"location": location,
			"category": category,
			"publicize": publicize,
			"link": link,
			"captchaToken": captcha_token,
		}
		return await self._make_request("POST", API_CREATE_SWITCH, data, require_auth=True)

	async def update_switch(self, uid: str, updates: Dict[str, Any]) -> Dict[str, Any]:
		"""Update a legacy (v1) switch metadata (PATCH /api/switch/:uid)."""
		if not isinstance(uid, str) or not uid:
			raise VomeSyncAPIError("UID required")
		if not isinstance(updates, dict) or not updates:
			raise VomeSyncAPIError("At least one update field is required")
		endpoint = f"/api/switch/{uid}"
		return await self._make_request("PATCH", endpoint, updates, require_auth=True)

	async def create_switch_v2(
		self,
		index: int,
		name: str = "",
		description: str = "",
		location: str = "",
		category: str = "Other",
		publicize: bool = False,
		link: str = "",
		icon_url: Optional[str] = None,
		banner_url: Optional[str] = None,
		captcha_token: str = "",
	) -> Dict[str, Any]:
		"""Create a new switch using v2 crypto auth."""
		if not self.crypto_enabled:
			raise VomeSyncAPIError("Crypto mode is not enabled for this client")
		
		req = build_v2_create_switch_request(
			self.crypto_seed,
			index=index,
			name=name,
			description=description,
			location=location,
			category=category,
			publicize=publicize,
			link=link,
			icon_url=icon_url,
			banner_url=banner_url,
			captcha_token=captcha_token,
		)
		data = {
			"ownerPubKey": req.ownerPubKey,
			"switchPubKey": req.switchPubKey,
			"index": req.index,
			"ts": req.ts,
			"nonce": req.nonce,
			"sigOwner": req.sigOwner,
			"sigSwitch": req.sigSwitch,
			**({"name": req.name} if isinstance(req.name, str) and req.name else {}),
			"description": req.description,
			"location": req.location,
			"category": req.category,
			"publicize": req.publicize,
			"link": req.link,
			**({"iconUrl": req.iconUrl} if isinstance(req.iconUrl, str) and req.iconUrl else {}),
			**({"bannerUrl": req.bannerUrl} if isinstance(req.bannerUrl, str) and req.bannerUrl else {}),
			"captchaToken": req.captchaToken,
		}
		return await self._make_request("POST", API_V2_CREATE_SWITCH, data, require_auth=False)

	async def update_switch_v2_metadata(
		self,
		uid: str,
		updates: Dict[str, Any],
		captcha_token: str = "",
	) -> Dict[str, Any]:
		"""Update v2 switch metadata (signed by owner key)."""
		if not self.crypto_enabled:
			raise VomeSyncAPIError("Crypto mode is not enabled for this client")
		if not isinstance(uid, str) or not uid:
			raise VomeSyncAPIError("UID required")
		if not isinstance(updates, dict) or not updates:
			raise VomeSyncAPIError("At least one update field is required")
		
		payload = build_v2_update_switch_request(
			self.crypto_seed,
			uid=uid,
			updates=updates,
			captcha_token=captcha_token,
		)
		endpoint = API_V2_UPDATE_SWITCH.format(uid=uid)
		return await self._make_request("POST", endpoint, payload, require_auth=False)

	async def create_v2_access_key(
		self,
		uid: str,
		name: str = "",
		permissions: Optional[list[str]] = None,
		ttl_seconds: Optional[int] = None,
	) -> Dict[str, Any]:
		"""Create a delegated v2 access key (signed by owner key)."""
		if not self.crypto_enabled:
			raise VomeSyncAPIError("Crypto mode is not enabled for this client")
		if not isinstance(uid, str) or not uid:
			raise VomeSyncAPIError("UID required")
		
		req = build_v2_create_access_key_request(
			self.crypto_seed,
			uid=uid,
			name=name,
			permissions=permissions,
			ttl_seconds=ttl_seconds,
		)
		endpoint = API_V2_ACCESS_KEYS_CREATE.format(uid=uid)
		return await self._make_request("POST", endpoint, req, require_auth=False)

	async def list_v2_access_keys(self, uid: str) -> Dict[str, Any]:
		"""List delegated v2 access keys for a switch (signed by owner key)."""
		if not self.crypto_enabled:
			raise VomeSyncAPIError("Crypto mode is not enabled for this client")
		if not isinstance(uid, str) or not uid:
			raise VomeSyncAPIError("UID required")
		
		req = build_v2_list_access_keys_request(self.crypto_seed, uid=uid)
		endpoint = API_V2_ACCESS_KEYS_LIST.format(uid=uid)
		return await self._make_request("POST", endpoint, req, require_auth=False)

	async def revoke_v2_access_key(self, uid: str, api_key_or_id: str) -> bool:
		"""Revoke a delegated v2 access key (signed by owner key)."""
		if not self.crypto_enabled:
			raise VomeSyncAPIError("Crypto mode is not enabled for this client")
		if not isinstance(uid, str) or not uid:
			raise VomeSyncAPIError("UID required")
		if not isinstance(api_key_or_id, str) or not api_key_or_id:
			raise VomeSyncAPIError("Access key ID required")
		
		key_id = None
		api_key = None
		if len(api_key_or_id) == 64 and all(c in "0123456789abcdefABCDEF" for c in api_key_or_id):
			key_id = api_key_or_id
		else:
			api_key = api_key_or_id

		req = build_v2_revoke_access_key_request(self.crypto_seed, uid=uid, api_key=api_key, key_id=key_id)
		endpoint = API_V2_ACCESS_KEYS_REVOKE.format(uid=uid)
		await self._make_request("POST", endpoint, req, require_auth=False)
		return True

	async def pause_v2_access_key(self, uid: str, key_id: str, paused: bool) -> bool:
		"""Pause or unpause a delegated v2 access key (signed by owner key)."""
		if not self.crypto_enabled:
			raise VomeSyncAPIError("Crypto mode is not enabled for this client")
		if not isinstance(uid, str) or not uid:
			raise VomeSyncAPIError("UID required")
		if not isinstance(key_id, str) or not key_id:
			raise VomeSyncAPIError("Key ID required")

		req = build_v2_pause_access_key_request(
			self.crypto_seed, uid=uid, key_id=key_id, paused=paused
		)
		endpoint = API_V2_ACCESS_KEYS_PAUSE.format(uid=uid)
		await self._make_request("POST", endpoint, req, require_auth=False)
		return True

	async def update_v2_access_key_permissions(
		self, uid: str, key_id: str, permissions: list[str]
	) -> bool:
		"""Update permissions on a delegated v2 access key (signed by owner key)."""
		if not self.crypto_enabled:
			raise VomeSyncAPIError("Crypto mode is not enabled for this client")
		if not isinstance(uid, str) or not uid:
			raise VomeSyncAPIError("UID required")
		if not isinstance(key_id, str) or not key_id:
			raise VomeSyncAPIError("Key ID required")
		if not isinstance(permissions, list) or not permissions:
			raise VomeSyncAPIError("Permissions list required")

		req = build_v2_update_access_key_permissions_request(
			self.crypto_seed, uid=uid, key_id=key_id, permissions=permissions
		)
		endpoint = API_V2_ACCESS_KEYS_PERMISSIONS.format(uid=uid)
		await self._make_request("POST", endpoint, req, require_auth=False)
		return True

	async def toggle_switch(self, uid: str) -> Dict[str, Any]:
		"""Toggle a switch."""
		endpoint = API_TOGGLE_SWITCH.format(uid=uid)
		return await self._make_request("POST", endpoint, {}, require_auth=True)

	async def toggle_switch_with_access_key(self, uid: str, access_key: str) -> Dict[str, Any]:
		"""Toggle a v2 switch using a delegated access key."""
		endpoint = API_V2_TOGGLE.format(uid=uid)
		return await self._make_request(
			"POST",
			endpoint,
			{},
			require_auth=False,
			extra_headers={"X-Api-Key": access_key},
		)

	async def set_switch_state_v2(
		self,
		uid: str,
		index: int,
		state: bool,
		params: Optional[Dict[str, Any]] = None,
	) -> Dict[str, Any]:
		"""Set switch state using v2 crypto auth (supports params)."""
		if not self.crypto_enabled:
			raise VomeSyncAPIError("Crypto mode is not enabled for this client")
		
		payload = build_v2_set_state_request(
			self.crypto_seed,
			uid=uid,
			index=index,
			state=state,
			params=params or {},
		)
		endpoint = API_V2_SET_STATE.format(uid=uid)
		return await self._make_request("POST", endpoint, payload, require_auth=False)

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
			if self.crypto_enabled:
				payload = build_v2_my_switches_request(self.crypto_seed)
				response = await self._make_request("POST", API_V2_MY_SWITCHES, payload, require_auth=False)
				return response.get("switches", [])
			
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

	async def get_api_keys(self) -> list[Dict[str, Any]]:
		"""Alias for list_api_keys (kept for backwards compatibility)."""
		return await self.list_api_keys()

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

	async def test_connection(self) -> Dict[str, Any]:
		"""Test API connectivity and return a structured result for UI/debugging.

		This is intended for config/options flows where the user wants quick feedback.
		It does not raise and should not log sensitive values.
		"""
		result: Dict[str, Any] = {
			"server_url": self.server_url,
			"crypto_enabled": bool(self.crypto_enabled),
			"health_ok": False,
			"health_error": None,
			"my_switches_ok": False,
			"my_switches_error": None,
			"my_switches_count": None,
		}

		# Health
		try:
			await self._make_request("GET", "/api/health")
			result["health_ok"] = True
		except VomeSyncAPIError as ex:
			result["health_error"] = str(ex)

		# My switches (only attempt if server reachable)
		try:
			if self.crypto_enabled:
				payload = build_v2_my_switches_request(self.crypto_seed)
				response = await self._make_request("POST", API_V2_MY_SWITCHES, payload, require_auth=False)
			else:
				response = await self._make_request("GET", API_MY_SWITCHES, require_auth=True)
			switches = response.get("switches", [])
			result["my_switches_ok"] = True
			result["my_switches_count"] = len(switches) if isinstance(switches, list) else 0
		except VomeSyncAPIError as ex:
			result["my_switches_error"] = str(ex)

		return result
