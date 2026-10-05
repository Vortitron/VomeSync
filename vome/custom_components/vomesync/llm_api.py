"""The chat tools as a Home Assistant LLM API called "Vome".

Home Assistant's conversation agents (OpenRouter, Anthropic, OpenAI,
Ollama, Google ...) each have a "Control Home Assistant" choice of LLM
APIs.  Registering ours puts Vome in that list, so the owner's existing
assistant — the Assist dialog, the phone app, voice — can use
:mod:`.chat_tools` with no chat window of ours.

Home Assistant only grew LLM APIs in 2024.6, and this integration still
loads on 2024.1, so the import is tried and a Core without it simply has
no Vome entry in that list.
"""
from __future__ import annotations

import logging

from homeassistant.core import HomeAssistant

from .chat_tools import (
	PROMPT,
	ChatToolError,
	async_call_tool,
	async_effective_scopes,
	async_tools_for_user,
)
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

API_ID = DOMAIN
API_NAME = "Vome"


def async_register_llm_api(hass: HomeAssistant) -> bool:
	"""Register once per Home Assistant; False on a Core without LLM APIs."""
	data = hass.data.setdefault(DOMAIN, {})
	if data.get("_llm_api"):
		return True
	try:
		from homeassistant.helpers import llm
	except ImportError:
		return False

	class VomeTool(llm.Tool):
		def __init__(self, spec):
			self.spec = spec
			self.name = spec.name
			self.description = spec.description
			self.parameters = spec.vol_schema()

		async def async_call(self, hass, tool_input, llm_context):
			user_id = llm_context.context.user_id if llm_context.context else None
			try:
				return {"result": await async_call_tool(
					hass, self.name, tool_input.tool_args, user_id)}
			except ChatToolError as err:
				return {"error": str(err)}

	class VomeAPI(llm.API):
		async def async_get_api_instance(self, llm_context):
			user_id = llm_context.context.user_id if llm_context.context else None
			scopes = await async_effective_scopes(self.hass, user_id)
			prompt = PROMPT
			if scopes == ["read"]:
				prompt += " You can look but not change anything here."
			return llm.APIInstance(
				api=self,
				api_prompt=prompt,
				llm_context=llm_context,
				tools=[VomeTool(spec) for spec in
				       await async_tools_for_user(self.hass, user_id)],
			)

	try:
		data["_llm_api"] = llm.async_register_api(
			hass, VomeAPI(hass=hass, id=API_ID, name=API_NAME))
	except Exception:  # noqa: BLE001 - a Core-side change must not stop the integration
		_LOGGER.warning("Could not offer Vome to Home Assistant's assistants", exc_info=True)
		return False
	return True
