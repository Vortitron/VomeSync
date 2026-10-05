"""Options flow mixin: what an AI chat may do in this house.

The chat window for an install from HACS is Home Assistant's own Assist:
the owner gives their assistant (OpenRouter, Anthropic, OpenAI, Ollama
...) the "Vome" API under "Control Home Assistant", and it can use
:mod:`.chat_tools`.  This step is where they choose which of those tools
it gets.  The add-on panel's Chat page reads and writes the same choice.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

import voluptuous as vol
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import config_validation as cv

from .chat_tools import (
	ALL_SCOPES,
	CONF_CHAT_SCOPES,
	SCOPE_CONFIGURE,
	SCOPE_CONTROL,
	SCOPE_READ,
	configured_scopes,
)

SCOPE_LABELS = {
	SCOPE_READ: "See states, history, automations and the error log",
	SCOPE_CONTROL: "Control devices (not locks, alarms, covers, valves or cameras)",
	SCOPE_CONFIGURE: "Create, change and delete automations",
}


def has_llm_apis() -> bool:
	try:
		from homeassistant.helpers import llm  # noqa: F401
	except ImportError:
		return False
	return True


def chat_info(llm_apis: bool) -> str:
	if not llm_apis:
		return (
			"This Home Assistant is too old for assistants to use tools "
			"(they arrived in 2024.6). Update it, then come back."
		)
	return (
		"To chat: Settings → Voice assistants → your assistant → the "
		"conversation agent's options → **Control Home Assistant** → tick "
		"**Vome**. No assistant yet? Add the OpenRouter integration with your "
		"key first. Only administrators get more than the read tools."
	)


class VomeSyncOptionsFlowChatMixin:
	"""The ``ai_chat`` item on the Configure menu."""

	async def async_step_ai_chat(
		self, user_input: Optional[Dict[str, Any]] = None
	) -> FlowResult:
		if user_input is not None:
			chosen = [s for s in ALL_SCOPES if s in (user_input.get("scopes") or [])]
			await self._async_update_entry_options({
				**(self._config_entry.options or {}), CONF_CHAT_SCOPES: chosen,
			})
			return await self.async_step_init()
		return self.async_show_form(
			step_id="ai_chat",
			data_schema=vol.Schema({
				vol.Required("scopes", default=configured_scopes(self.hass)):
					cv.multi_select(SCOPE_LABELS),
			}),
			description_placeholders={"info": chat_info(has_llm_apis())},
		)
