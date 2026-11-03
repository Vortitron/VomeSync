"""``stt.vome``: speech-to-text for the Assist pipeline, heard by Vome.

Choose it as a voice assistant's speech-to-text engine, and a command from
an ESP32 satellite, Voice PE or the phone app goes to Vome, which hears it
with Deepgram. The names of the home's areas and of the entities exposed to
Assist go along as hints, so "Vardagsrum" comes back spelled right. Paid from
the home's pocket money (:mod:`.home_ai`). Loaded only when the owner has
turned the AI services on.
"""
from __future__ import annotations

import logging
from collections.abc import AsyncIterable

from homeassistant.components import stt
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import home_ai
from .const import DEVICE_MANUFACTURER, DOMAIN

_LOGGER = logging.getLogger(__name__)

# What Deepgram's Nova-3 hears well. Swedish has its own model.
LANGUAGES = ["en", "en-GB", "en-US", "sv", "da", "nb", "no", "fi", "de", "nl", "fr",
             "es", "it", "pt"]
# Vome refuses a command longer than this, so stop reading there.
MAX_SECONDS = 30
MAX_KEYTERMS = 100


async def async_setup_entry(
	hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback,
) -> None:
	async_add_entities([VomeSpeechToTextEntity(entry)])


def keyterms(hass: HomeAssistant) -> list[str]:
	"""Area names, then names of entities exposed to Assist: what people say."""
	from homeassistant.helpers import area_registry as ar

	names = [area.name for area in ar.async_get(hass).async_list_areas()]
	try:
		from homeassistant.components.homeassistant.exposed_entities import async_should_expose
		for state in hass.states.async_all():
			if len(names) >= MAX_KEYTERMS:
				break
			if async_should_expose(hass, "conversation", state.entity_id):
				name = state.attributes.get("friendly_name")
				if name:
					names.append(str(name))
	except ImportError:
		pass
	seen, out = set(), []
	for name in names:
		key = name.casefold()
		if key not in seen:
			seen.add(key)
			out.append(name)
	return out[:MAX_KEYTERMS]


class VomeSpeechToTextEntity(stt.SpeechToTextEntity):
	"""Hears voice commands through Vome."""

	_attr_has_entity_name = True
	_attr_icon = "mdi:microphone-message"

	def __init__(self, entry: ConfigEntry) -> None:
		self._entry = entry
		self._attr_unique_id = f"vomesync_stt_{entry.entry_id}"
		self._attr_name = "Hearing"
		self._attr_device_info = {
			"identifiers": {(DOMAIN, f"vome_account_{entry.entry_id}")},
			"name": "Vome",
			"manufacturer": DEVICE_MANUFACTURER,
		}

	@property
	def supported_languages(self) -> list[str]:
		return LANGUAGES

	@property
	def supported_formats(self) -> list[stt.AudioFormats]:
		return [stt.AudioFormats.WAV]

	@property
	def supported_codecs(self) -> list[stt.AudioCodecs]:
		return [stt.AudioCodecs.PCM]

	@property
	def supported_bit_rates(self) -> list[stt.AudioBitRates]:
		return [stt.AudioBitRates.BITRATE_16]

	@property
	def supported_sample_rates(self) -> list[stt.AudioSampleRates]:
		return [stt.AudioSampleRates.SAMPLERATE_16000]

	@property
	def supported_channels(self) -> list[stt.AudioChannels]:
		return [stt.AudioChannels.CHANNEL_MONO]

	async def async_process_audio_stream(
		self, metadata: stt.SpeechMetadata, stream: AsyncIterable[bytes],
	) -> stt.SpeechResult:
		limit = int(metadata.sample_rate) * 2 * int(metadata.channel) * MAX_SECONDS
		audio = bytearray()
		async for chunk in stream:
			audio.extend(chunk)
			if len(audio) >= limit:
				del audio[limit:]
				break
		if not audio:
			return stt.SpeechResult(None, stt.SpeechResultState.ERROR)
		try:
			text = await home_ai.async_hear(
				self.hass, self._entry, bytes(audio), language=metadata.language,
				sample_rate=int(metadata.sample_rate), channels=int(metadata.channel),
				keyterms=keyterms(self.hass),
			)
		except home_ai.VomeAIError as err:
			_LOGGER.warning("Vome could not hear that: %s", err)
			return stt.SpeechResult(None, stt.SpeechResultState.ERROR)
		return stt.SpeechResult(text, stt.SpeechResultState.SUCCESS)
