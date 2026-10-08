"""The chat tools as a Home Assistant LLM API called "Vome".

Home Assistant's conversation agents (OpenRouter, Anthropic, OpenAI,
Ollama, Google ...) each have a "Control Home Assistant" choice of LLM
APIs.  Registering ours puts Vome in that list, so the owner's existing
assistant — the Assist dialog, the phone app, voice — can use
:mod:`.chat_tools` with no chat window of ours.

Home Assistant only grew LLM APIs in 2024.6, and this integration still
loads on 2024.1, so the import is tried and a Core without it simply has
no Vome entry in that list.

From 2026.10 Home Assistant's own MCP server offers every registered LLM
API to AI apps by default, so these tools are also read by programs that
take a tool's self-description at its word.  A tool that declares nothing
is taken to write, destroy and reach outside the house, so each one says
what it does (:func:`tool_annotations`).  ``ToolResult``, ``ToolAnnotations``
and ``Tool.integration`` arrived in that release; an older Core gets the
plain dict it has always had.
"""
from __future__ import annotations

import logging

from homeassistant.core import HomeAssistant

from .chat_tools import (
	PROMPT,
	SCOPE_CONFIGURE,
	SCOPE_READ,
	ChatTool,
	ChatToolError,
	async_call_tool,
	async_effective_scopes,
	async_tools_for_user,
)
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

API_ID = DOMAIN
API_NAME = "Vome"


def tool_annotations(spec: ChatTool) -> dict:
	"""What a tool does, in Home Assistant's ``ToolAnnotations`` terms.

	Looking changes nothing and stays in the house.  Changing automations
	stays in the house but can replace or delete what the owner made.
	Controlling devices keeps Home Assistant's least-safe default: a device
	action can reach a cloud and cannot be undone by calling it again.
	"""
	if spec.scope == SCOPE_READ:
		return {"read_only": True, "destructive": False,
		        "idempotent": True, "open_world": False}
	if spec.scope == SCOPE_CONFIGURE:
		return {"read_only": False, "destructive": True,
		        "idempotent": True, "open_world": False}
	return {}


def tool_title(spec: ChatTool) -> str:
	"""``find_entities`` → ``Find entities``, for an app's tool list."""
	return spec.name.replace("_", " ").capitalize()


def async_register_llm_api(hass: HomeAssistant) -> bool:
	"""Register once per Home Assistant; False on a Core without LLM APIs."""
	data = hass.data.setdefault(DOMAIN, {})
	if data.get("_llm_api"):
		return True
	try:
		from homeassistant.helpers import llm
	except ImportError:
		return False

	tool_result = getattr(llm, "ToolResult", None)
	annotations_cls = getattr(llm, "ToolAnnotations", None)

	class VomeTool(llm.Tool):
		integration = DOMAIN

		def __init__(self, spec):
			self.spec = spec
			self.name = spec.name
			self.title = tool_title(spec)
			self.description = spec.description
			self.parameters = spec.vol_schema()
			if annotations_cls is not None:
				self.annotations = annotations_cls(**tool_annotations(spec))

		async def async_call(self, hass, tool_input, llm_context):
			user_id = llm_context.context.user_id if llm_context.context else None
			try:
				data, error = {"result": await async_call_tool(
					hass, self.name, tool_input.tool_args, user_id)}, False
			except ChatToolError as err:
				data, error = {"error": str(err)}, True
			if tool_result is None:
				return data
			return tool_result(data=data, error=error)

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
