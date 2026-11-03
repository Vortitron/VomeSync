"""``ai_task.vome``: Home Assistant's AI tasks, answered by Vome.

Built for pictures: ``ai_task.generate_data`` with a camera attachment
(``media-source://camera/camera.door``) and instructions such as "is there a
person at the door?" goes to Vome, which asks Claude and answers in words or,
with a ``structure``, as data an automation can test. Paid from the home's
brainpower (:mod:`.home_ai`). Loaded only when the owner has turned the AI
services on, and only on Core 2025.8+, where AI tasks exist.
"""
from __future__ import annotations

import logging

from homeassistant.components import ai_task, conversation
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import home_ai
from .const import DEVICE_MANUFACTURER, DOMAIN

_LOGGER = logging.getLogger(__name__)

# Words in the instructions that ask for the slower, stronger model.
CAREFUL_WORDS = ("look carefully", "careful look", "read the number", "number plate")


async def async_setup_entry(
	hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback,
) -> None:
	async_add_entities([VomeAITaskEntity(entry)])


def structure_schema(structure) -> dict:
	"""A task's ``structure`` (a voluptuous schema) as JSON schema for Vome."""
	from homeassistant.helpers import llm
	from voluptuous_openapi import convert

	return convert(structure, custom_serializer=llm.selector_serializer)


class VomeAITaskEntity(ai_task.AITaskEntity):
	"""Looks at pictures for the home's automations."""

	_attr_has_entity_name = True
	_attr_icon = "mdi:cctv"
	_attr_supported_features = (
		ai_task.AITaskEntityFeature.GENERATE_DATA
		| ai_task.AITaskEntityFeature.SUPPORT_ATTACHMENTS
	)

	def __init__(self, entry: ConfigEntry) -> None:
		self._entry = entry
		self._attr_unique_id = f"vomesync_ai_task_{entry.entry_id}"
		self._attr_name = "AI"
		self._attr_device_info = {
			"identifiers": {(DOMAIN, f"vome_account_{entry.entry_id}")},
			"name": "Vome",
			"manufacturer": DEVICE_MANUFACTURER,
		}

	async def _async_generate_data(
		self, task: ai_task.GenDataTask, chat_log: conversation.ChatLog,
	) -> ai_task.GenDataTaskResult:
		images = []
		for attachment in task.attachments or []:
			if not str(attachment.mime_type or "").startswith("image/"):
				raise HomeAssistantError(
					f"Vome can only look at pictures, not {attachment.mime_type}.")
			images.append(await self.hass.async_add_executor_job(attachment.path.read_bytes))
		if not images:
			raise HomeAssistantError(
				"Vome AI looks at pictures: attach one, for example "
				"media-source://camera/camera.front_door.")

		structure = structure_schema(task.structure) if task.structure else None
		careful = any(word in task.instructions.lower() for word in CAREFUL_WORDS)
		try:
			answer = await home_ai.async_look(
				self.hass, self._entry, instructions=task.instructions, images=images,
				structure=structure, careful=careful,
			)
		except home_ai.VomeAIError as err:
			raise HomeAssistantError(str(err)) from err

		data = answer.get("data") if structure else answer.get("text", "")
		text = answer.get("text") or (str(data) if data is not None else "")
		chat_log.async_add_assistant_content_without_tools(
			conversation.AssistantContent(agent_id=self.entity_id, content=text)
		)
		_LOGGER.debug("Vome AI answered %s for %s", task.name, answer.get("charged"))
		return ai_task.GenDataTaskResult(conversation_id=chat_log.conversation_id, data=data)
