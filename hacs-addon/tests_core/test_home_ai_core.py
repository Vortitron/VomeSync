# flake8: noqa
"""Vome's AI services on a real Core: ``ai_task.vome_ai`` and ``stt.vome_hearing``.

Nothing reaches Vome: the two calls in ``home_ai`` are stubbed, so what is
checked is the Home Assistant side -- that the entities exist only when the
owner turned them on, that ``ai_task.generate_data`` with a camera picture
reaches Vome shrunk and comes back as words or as data, that a structure is
sent as JSON schema, and that a voice command is heard from the PCM stream.
"""
import io
from contextlib import contextmanager
from unittest.mock import patch

import pytest
import voluptuous as vol
from homeassistant.components import stt
from homeassistant.helpers import selector
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.vomesync import home_ai
from custom_components.vomesync.const import (
	CONF_RELAY,
	CONF_RELAY_SECRET,
	CONF_RELAY_SERVER_ID,
	DOMAIN,
)

from test_platforms_core import _stubbed


@pytest.fixture(autouse=True)
def _custom_integrations(enable_custom_integrations):
	yield


def _entry(hass, on=True):
	entry = MockConfigEntry(
		domain=DOMAIN, title="Vome",
		data={"personal_key": "pk", "server_url": "https://sync.vome.test",
		      "websocket_url": "wss://sync.vome.test"},
		options={CONF_RELAY: {CONF_RELAY_SERVER_ID: "srv-1", CONF_RELAY_SECRET: "s"},
		         home_ai.CONF_AI_SERVICES: on},
	)
	entry.add_to_hass(hass)
	return entry


async def _load(hass, entry):
	assert await async_setup_component(hass, "homeassistant", {})
	with _stubbed():
		assert await hass.config_entries.async_setup(entry.entry_id)
		await hass.async_block_till_done()


def _jpeg(size=(1920, 1080)):
	from PIL import Image
	out = io.BytesIO()
	Image.new("RGB", size, (40, 120, 40)).save(out, format="JPEG")
	return out.getvalue()


async def test_the_entities_exist_only_when_turned_on(hass):
	off = _entry(hass, on=False)
	await _load(hass, off)
	assert hass.states.get("ai_task.vome_ai") is None
	assert hass.states.get("stt.vome_hearing") is None
	assert await hass.config_entries.async_unload(off.entry_id)


async def test_turning_off_unloads_what_was_loaded(hass):
	entry = _entry(hass)
	await _load(hass, entry)
	assert hass.states.get("ai_task.vome_ai") is not None
	assert hass.states.get("stt.vome_hearing") is not None
	hass.config_entries.async_update_entry(entry, options={**entry.options, home_ai.CONF_AI_SERVICES: False})
	with _stubbed():
		assert await hass.config_entries.async_unload(entry.entry_id)
		await hass.async_block_till_done()


async def test_generate_data_sends_a_shrunk_picture_and_returns_words(hass, tmp_path):
	entry = _entry(hass)
	await _load(hass, entry)
	picture = tmp_path / "door.jpg"
	picture.write_bytes(_jpeg())
	sent = {}

	async def look(hass_, entry_, **kwargs):
		sent.update(kwargs)
		return {"text": "A fox on the lawn.", "charged": "0,26 öre"}

	from homeassistant.components import ai_task, conversation
	task = ai_task.GenDataTask(
		name="garden", instructions="What animal is this?",
		attachments=[conversation.Attachment(
			media_content_id="media-source://camera/camera.garden",
			mime_type="image/jpeg", path=picture)],
	)
	entity = hass.data["entity_components"]["ai_task"].get_entity("ai_task.vome_ai")
	with patch.object(home_ai, "async_look", look):
		from homeassistant.helpers import chat_session
		with chat_session.async_get_chat_session(hass) as session:
			result = await entity.internal_async_generate_data(session, task)
	assert result.data == "A fox on the lawn."
	assert sent["careful"] is False and sent["structure"] is None
	# Shrinking happens in home_ai.async_look; the entity hands over the file.
	assert sent["images"] == [picture.read_bytes()]


async def test_a_structure_is_sent_as_json_schema_and_comes_back_as_data(hass, tmp_path):
	entry = _entry(hass)
	await _load(hass, entry)
	picture = tmp_path / "door.jpg"
	picture.write_bytes(_jpeg())
	sent = {}

	async def look(hass_, entry_, **kwargs):
		sent.update(kwargs)
		return {"data": {"seen": True, "what": "fox"}}

	from homeassistant.components import ai_task, conversation
	task = ai_task.GenDataTask(
		name="garden", instructions="Look carefully: anything there?",
		structure=vol.Schema({
			vol.Required("seen"): selector.BooleanSelector(),
			vol.Required("what"): selector.TextSelector(),
		}),
		attachments=[conversation.Attachment(
			media_content_id="media-source://camera/camera.garden",
			mime_type="image/jpeg", path=picture)],
	)
	entity = hass.data["entity_components"]["ai_task"].get_entity("ai_task.vome_ai")
	with patch.object(home_ai, "async_look", look):
		from homeassistant.helpers import chat_session
		with chat_session.async_get_chat_session(hass) as session:
			result = await entity.internal_async_generate_data(session, task)
	assert result.data == {"seen": True, "what": "fox"}
	assert sent["careful"] is True
	schema = sent["structure"]
	assert schema["type"] == "object"
	assert schema["properties"]["seen"]["type"] == "boolean"


async def test_a_task_without_a_picture_is_refused_before_vome_is_asked(hass):
	entry = _entry(hass)
	await _load(hass, entry)
	from homeassistant.components import ai_task
	from homeassistant.exceptions import HomeAssistantError
	from homeassistant.helpers import chat_session
	entity = hass.data["entity_components"]["ai_task"].get_entity("ai_task.vome_ai")

	async def look(*args, **kwargs):
		raise AssertionError("Vome was called without a picture")

	with patch.object(home_ai, "async_look", look), \
			chat_session.async_get_chat_session(hass) as session, \
			pytest.raises(HomeAssistantError):
		await entity.internal_async_generate_data(
			session, ai_task.GenDataTask(name="x", instructions="Anything?"))


def test_pictures_are_shrunk_before_they_leave():
	from PIL import Image
	with Image.open(io.BytesIO(home_ai.shrink(_jpeg()))) as small:
		assert max(small.size) == home_ai.IMAGE_EDGE


async def test_a_voice_command_is_heard_from_the_pcm_stream(hass):
	entry = _entry(hass)
	await _load(hass, entry)
	from homeassistant.helpers import area_registry as ar
	ar.async_get(hass).async_create("Vardagsrum")
	sent = {}

	async def hear(hass_, entry_, audio, **kwargs):
		sent["audio"] = audio
		sent.update(kwargs)
		return "tänd i vardagsrummet"

	async def chunks():
		for _ in range(3):
			yield b"\0\0" * 1600

	entity = hass.data["entity_components"]["stt"].get_entity("stt.vome_hearing")
	metadata = stt.SpeechMetadata(
		language="sv", format=stt.AudioFormats.WAV, codec=stt.AudioCodecs.PCM,
		bit_rate=stt.AudioBitRates.BITRATE_16, sample_rate=stt.AudioSampleRates.SAMPLERATE_16000,
		channel=stt.AudioChannels.CHANNEL_MONO,
	)
	with patch.object(home_ai, "async_hear", hear):
		result = await entity.async_process_audio_stream(metadata, chunks())
	assert result.result == stt.SpeechResultState.SUCCESS
	assert result.text == "tänd i vardagsrummet"
	assert len(sent["audio"]) == 3 * 3200
	assert sent["language"] == "sv" and sent["sample_rate"] == 16000
	assert "Vardagsrum" in sent["keyterms"]


async def test_a_failed_hearing_is_an_error_result_not_a_crash(hass):
	entry = _entry(hass)
	await _load(hass, entry)

	async def hear(*args, **kwargs):
		raise home_ai.VomeAIError("This home's brainpower has run out.")

	async def chunks():
		yield b"\0\0" * 1600

	entity = hass.data["entity_components"]["stt"].get_entity("stt.vome_hearing")
	metadata = stt.SpeechMetadata(
		language="en", format=stt.AudioFormats.WAV, codec=stt.AudioCodecs.PCM,
		bit_rate=stt.AudioBitRates.BITRATE_16, sample_rate=stt.AudioSampleRates.SAMPLERATE_16000,
		channel=stt.AudioChannels.CHANNEL_MONO,
	)
	with patch.object(home_ai, "async_hear", hear):
		result = await entity.async_process_audio_stream(metadata, chunks())
	assert result.result == stt.SpeechResultState.ERROR


def test_structure_needs_nothing_core_does_not_ship():
	# voluptuous_openapi comes with Core's OpenAI and Google integrations,
	# not with Core: a home without them failed every structured task.
	from pathlib import Path
	from custom_components.vomesync import ai_task
	assert "voluptuous_openapi" not in Path(ai_task.__file__).read_text().split('"""')[-1]


def test_selectors_become_json_schema():
	from custom_components.vomesync.ai_task import structure_schema
	schema = structure_schema(vol.Schema({
		vol.Required("seen", description="Anything there?"): selector.BooleanSelector(),
		vol.Required("count"): selector.NumberSelector({"min": 0, "max": 20}),
		vol.Optional("kind"): selector.SelectSelector({"options": ["fox", "cat", "person"]}),
		vol.Required("tags"): selector.TextSelector({"multiple": True}),
	}))
	assert schema["required"] == ["seen", "count", "tags"]
	assert schema["properties"]["seen"] == {"type": "boolean", "description": "Anything there?"}
	assert schema["properties"]["count"] == {"type": "number"}
	assert schema["properties"]["kind"] == {"type": "string", "enum": ["fox", "cat", "person"]}
	assert schema["properties"]["tags"] == {"type": "array", "items": {"type": "string"}}
