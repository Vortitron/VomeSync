"""Vome's AI services for this home: looking at pictures and hearing commands.

Two Home Assistant entities, offered only when the owner turns them on
(``ai_services`` in the options, the "AI for cameras and voice" item on
Configure):

* ``ai_task.vome`` -- ``ai_task.generate_data`` with camera attachments goes
  to Vome, which asks Claude about the pictures (:mod:`.ai_task`);
* ``stt.vome`` -- a speech-to-text engine for the Assist pipeline, which Vome
  hears with Deepgram (:mod:`.stt`).

Both are paid from the home's **Brainpower** at Vome: a few kronor a month
free between them, then whatever the owner has topped up, never more than the
daily limit they set. The portal's half and its rules are in the portal repo,
``portal/home_wallet.py`` and ``docs/home_ai_services_plan.md``.

**Pictures are shrunk here**, in the owner's own Home Assistant, before they
leave: 1024 pixels on the long edge is enough to tell a fox from a cat and a
fraction of the price of a full frame. Vome itself never decodes a picture.
"""
from __future__ import annotations

import io
import logging
from typing import Any, Optional

import aiohttp

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

_LOGGER = logging.getLogger(__name__)

CONF_AI_SERVICES = "ai_services"
WALLET_PATH = "/api/sync/agent/ai/wallet"
VISION_PATH = "/api/sync/agent/ai/vision"
HEARING_PATH = "/api/sync/agent/ai/hearing"
IMAGE_EDGE = 1024
_TIMEOUT = aiohttp.ClientTimeout(total=90)


class VomeAIError(Exception):
	"""Vome's answer, in words a person can act on."""


def enabled(entry: ConfigEntry) -> bool:
	return bool((entry.options or {}).get(CONF_AI_SERVICES))


def platforms(entry: ConfigEntry) -> list:
	"""The extra platforms to load: none unless switched on and Core has them.

	``ai_task`` arrived in Core 2025.8; on an older one only hearing is
	offered.
	"""
	if not enabled(entry):
		return []
	from homeassistant.const import Platform
	found = [Platform.STT]
	if hasattr(Platform, "AI_TASK"):
		found.insert(0, Platform.AI_TASK)
	return found


def _credentials(entry: ConfigEntry) -> tuple[str, str]:
	from .agent_key import _portal_url
	from .backup_client import credentials_for_entry

	_server_id, secret = credentials_for_entry(entry)
	if not secret:
		raise VomeAIError(
			"This Home Assistant isn't linked to a Vome account yet. Link it "
			"from the Vome integration's Configure menu first."
		)
	return _portal_url(entry), str(secret)


async def _call(hass: HomeAssistant, entry: ConfigEntry, method: str, path: str, *,
                json: Any = None, data: Optional[bytes] = None,
                params: Optional[list] = None) -> dict:
	base, secret = _credentials(entry)
	session = async_get_clientsession(hass)
	headers = {"Authorization": f"Bearer {secret}"}
	if data is not None:
		headers["Content-Type"] = "application/octet-stream"
	try:
		async with session.request(method, base + path, headers=headers, json=json,
		                           data=data, params=params, timeout=_TIMEOUT) as resp:
			try:
				body = await resp.json(content_type=None)
			except ValueError:
				body = {}
			body = body if isinstance(body, dict) else {}
			if resp.status >= 400:
				raise VomeAIError(body.get("error") or f"Vome answered HTTP {resp.status}.")
			return body
	except aiohttp.ClientError as err:
		raise VomeAIError(f"Could not reach Vome: {err}") from err


async def async_wallet(hass: HomeAssistant, entry: ConfigEntry) -> dict:
	"""The home's brainpower, as Vome counts it."""
	return await _call(hass, entry, "GET", WALLET_PATH)


def shrink(raw: bytes) -> bytes:
	"""A JPEG no larger than :data:`IMAGE_EDGE`. Blocking: run in the executor."""
	try:
		from PIL import Image, ImageOps
	except ImportError:  # Core ships Pillow; send as it is if not
		return raw
	with Image.open(io.BytesIO(raw)) as image:
		image = ImageOps.exif_transpose(image).convert("RGB")
		image.thumbnail((IMAGE_EDGE, IMAGE_EDGE))
		out = io.BytesIO()
		image.save(out, format="JPEG", quality=80)
		return out.getvalue()


async def async_look(hass: HomeAssistant, entry: ConfigEntry, *, instructions: str,
                     images: list[bytes], structure: Optional[dict] = None,
                     careful: bool = False) -> dict:
	"""Ask Vome about ``images``. Returns its answer (``text`` or ``data``)."""
	import base64

	shrunk = [await hass.async_add_executor_job(shrink, raw) for raw in images]
	payload = {
		"instructions": instructions,
		"images": [base64.b64encode(raw).decode("ascii") for raw in shrunk],
		"careful": careful,
	}
	if structure:
		payload["structure"] = structure
	return await _call(hass, entry, "POST", VISION_PATH, json=payload)


async def async_hear(hass: HomeAssistant, entry: ConfigEntry, audio: bytes, *,
                     language: str, sample_rate: int, channels: int,
                     keyterms: Optional[list] = None) -> str:
	"""The words in a voice command, 16-bit PCM."""
	params = [("language", language), ("sample_rate", str(sample_rate)),
	          ("channels", str(channels))]
	params += [("keyterm", term) for term in (keyterms or [])]
	body = await _call(hass, entry, "POST", HEARING_PATH, data=audio, params=params)
	return str(body.get("text") or "")
