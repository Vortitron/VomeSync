"""The tools an AI chat may use on this Home Assistant.

One list, two ways in:

* **Assist.**  :mod:`.llm_api` registers these as the "Vome" LLM API, so
  any conversation agent the owner already has (OpenRouter, Anthropic,
  OpenAI, Ollama ...) can be given them under "Control Home Assistant".
  That is the chat window for an install from HACS: the Assist dialog,
  the phone app and voice, with nothing of ours to draw.
* **The add-on panel's Chat page.**  The panel runs its own conversation
  with the owner's OpenRouter key and calls each tool through the
  ``vomesync.chat_tool_call`` action.  It can stop and ask before a tool
  that rewrites configuration (``confirm``), which Assist cannot.

**Why not the MCP key's tools.**  Those live on Vome's server and reach
the house over the relay.  A chat that is already *in* the house should
not leave it to read its own state, and should work for a house that is
not linked to Vome at all.

**What a tool may do is the owner's choice** (``CONF_CHAT_SCOPES``: read,
control, configure), ticked in the integration's Configure menu or the
panel.  On top of that, anyone who is not a Home Assistant administrator
— a household member's account, a voice satellite with no user — gets
the read tools only.  Assist lets ordinary users talk to the house, and
"rewrite the automations" must not be something they can talk it into.

**The same domains are off-limits as through Vome's broker**: locks,
alarms, covers, valves and cameras are physical security, and a model
that misheard should not open the garage.  Those stay with the owner's
own buttons.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Awaitable, Callable, Optional

import voluptuous as vol

from homeassistant.core import Context, HomeAssistant
from homeassistant.util import dt as dt_util

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

CONF_CHAT_SCOPES = "chat_scopes"

SCOPE_READ = "read"
SCOPE_CONTROL = "control"
SCOPE_CONFIGURE = "configure"
ALL_SCOPES = (SCOPE_READ, SCOPE_CONTROL, SCOPE_CONFIGURE)
DEFAULT_SCOPES = ALL_SCOPES

# Physical security, as Vome's broker (portal ha_operations._DEFAULT_DENY),
# plus the domains that run arbitrary code or reach the Supervisor, and our
# own: a chat must not issue agent keys or unlink the house.
DENY_DOMAINS = frozenset({
	"lock", "alarm_control_panel", "cover", "valve", "camera",
	"hassio", "shell_command", "python_script", "pyscript", "rest_command",
	"command_line", DOMAIN,
})
DENY_SERVICES = frozenset({
	("homeassistant", "stop"), ("homeassistant", "restart"),
	("homeassistant", "set_location"), ("recorder", "purge"),
	("recorder", "purge_entities"), ("recorder", "disable"),
})

AUTOMATIONS_FILE = "automations.yaml"
AUTOMATION_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
ENTITY_ID_RE = re.compile(r"^[a-z0-9_]+\.[a-z0-9_]+$")
SLUG_RE = re.compile(r"^[a-z0-9_]+$")

# A tool's answer goes back into the model's context, and every byte of it
# is paid for on every later turn.  Past this it is cut, and says so.
MAX_RESULT_CHARS = 12_000
MAX_TEMPLATE_CHARS = 20_000
MAX_HISTORY_HOURS = 24 * 7
MAX_HISTORY_POINTS = 200
MAX_FIND = 100
SERVICE_WAIT = 10

# Serialises our writes to automations.yaml.  Home Assistant's own editor
# has its own lock; the window between the two is a read-modify-write of a
# small file, and the editor re-reads before every save.
_AUTOMATION_LOCK = asyncio.Lock()


class ChatToolError(Exception):
	"""A reason the model (and the person) should be told, as-is."""


@dataclass(frozen=True)
class Param:
	name: str
	type: str  # string | integer | boolean | object | string_list
	description: str
	required: bool = False


@dataclass(frozen=True)
class ChatTool:
	name: str
	description: str
	scope: str
	handler: Callable[..., Awaitable[Any]]
	params: tuple = field(default_factory=tuple)
	# The panel asks before running it.  Assist cannot, so the prompt asks
	# the model to get a yes first; that is a request, not a guard.
	confirm: bool = False
	# Home Assistant itself keeps this to administrators (its template,
	# system log and automation-config APIs all require admin), so a
	# household member does not get it through a chat either.
	admin_only: bool = False

	def json_schema(self) -> dict:
		"""The parameters as JSON Schema, for an OpenAI-style tools list."""
		props: dict = {}
		for p in self.params:
			if p.type == "string_list":
				spec: dict = {"type": "array", "items": {"type": "string"}}
			else:
				spec = {"type": p.type}
			spec["description"] = p.description
			props[p.name] = spec
		return {
			"type": "object",
			"properties": props,
			"required": [p.name for p in self.params if p.required],
		}

	def vol_schema(self) -> vol.Schema:
		"""The parameters as voluptuous, for Home Assistant's LLM API."""
		types = {"string": str, "integer": int, "boolean": bool,
		         "object": dict, "string_list": [str]}
		schema = {}
		for p in self.params:
			marker = vol.Required if p.required else vol.Optional
			schema[marker(p.name, description=p.description)] = types[p.type]
		return vol.Schema(schema)

	def describe(self) -> dict:
		return {
			"name": self.name,
			"description": self.description,
			"parameters": self.json_schema(),
			"scope": self.scope,
			"confirm": self.confirm,
		}


# ── Who may use what ────────────────────────────────────────────────────


def _entry(hass: HomeAssistant):
	entries = hass.config_entries.async_entries(DOMAIN)
	return entries[0] if entries else None


def configured_scopes(hass: HomeAssistant) -> list:
	"""What the owner has allowed, in a fixed order."""
	entry = _entry(hass)
	raw = (entry.options or {}).get(CONF_CHAT_SCOPES) if entry else None
	chosen = DEFAULT_SCOPES if raw is None else raw
	return [s for s in ALL_SCOPES if s in chosen]


async def async_is_admin(hass: HomeAssistant, user_id: Optional[str]) -> bool:
	if not user_id:
		return False
	user = await hass.auth.async_get_user(user_id)
	return bool(user and user.is_admin)


async def async_effective_scopes(hass: HomeAssistant, user_id: Optional[str]) -> list:
	"""The owner's choice, cut to read-only for anyone not an administrator."""
	scopes = configured_scopes(hass)
	if await async_is_admin(hass, user_id):
		return scopes
	return [s for s in scopes if s == SCOPE_READ]


def tools_for(scopes, admin: bool = True) -> list:
	return [t for t in TOOLS if t.scope in scopes and (admin or not t.admin_only)]


async def async_tools_for_user(hass: HomeAssistant, user_id: Optional[str]) -> list:
	admin = await async_is_admin(hass, user_id)
	return tools_for(await async_effective_scopes(hass, user_id), admin)


def tool_named(name: str) -> Optional[ChatTool]:
	return next((t for t in TOOLS if t.name == name), None)


async def async_call_tool(hass: HomeAssistant, name: str, args: Optional[dict],
                          user_id: Optional[str]) -> Any:
	"""Run one tool as ``user_id``, within what that user may do.

	Returns something JSON-serialisable, already cut to size.  Raises
	:class:`ChatToolError` with a message meant for the model.
	"""
	tool = tool_named(name)
	if tool is None:
		raise ChatToolError(f"There is no tool called {name!r}.")
	if tool not in await async_tools_for_user(hass, user_id):
		raise ChatToolError(
			f"{name} is switched off here. The owner can allow it under "
			"Vome → Configure → AI chat."
		)
	args = dict(args or {})
	try:
		args = tool.vol_schema()(args)
	except vol.Invalid as err:
		raise ChatToolError(f"Bad arguments for {name}: {err}") from err
	context = Context(user_id=user_id)
	try:
		result = await tool.handler(hass, context, **args)
	except ChatToolError:
		raise
	except Exception as err:  # noqa: BLE001 - the model needs words, not a traceback
		_LOGGER.debug("Chat tool %s failed", name, exc_info=True)
		raise ChatToolError(f"{name} failed: {err or type(err).__name__}") from err
	return _fit(result)


def _fit(result: Any) -> Any:
	text = json.dumps(result, default=str)
	if len(text) <= MAX_RESULT_CHARS:
		return json.loads(text)
	return {
		"truncated": True,
		"note": "The answer was too long and has been cut; ask for less.",
		"partial": text[:MAX_RESULT_CHARS],
	}


# ── Reading ─────────────────────────────────────────────────────────────


def _registries(hass: HomeAssistant):
	from homeassistant.helpers import area_registry as ar
	from homeassistant.helpers import device_registry as dr
	from homeassistant.helpers import entity_registry as er
	return er.async_get(hass), dr.async_get(hass), ar.async_get(hass)


def _area_name(entity_id: str, ents, devs, areas) -> str:
	ent = ents.async_get(entity_id)
	area_id = getattr(ent, "area_id", None) if ent else None
	if not area_id and ent and getattr(ent, "device_id", None):
		dev = devs.async_get(ent.device_id)
		area_id = getattr(dev, "area_id", None) if dev else None
	area = areas.async_get_area(area_id) if area_id else None
	return area.name if area else ""


def _brief(state, area: str) -> dict:
	out = {
		"entity_id": state.entity_id,
		"name": state.attributes.get("friendly_name") or state.entity_id,
		"state": state.state,
	}
	unit = state.attributes.get("unit_of_measurement")
	if unit:
		out["unit"] = unit
	if area:
		out["area"] = area
	return out


async def _find_entities(hass, context, query: str = "", domain: str = "",
                         area: str = "", limit: int = 40) -> dict:
	ents, devs, areas = _registries(hass)
	query, area = query.strip().lower(), area.strip().lower()
	limit = max(1, min(int(limit), MAX_FIND))
	states = hass.states.async_all(domain.strip().lower()) if domain.strip() \
		else hass.states.async_all()
	found = []
	for state in sorted(states, key=lambda s: s.entity_id):
		name = str(state.attributes.get("friendly_name") or "")
		if query and query not in state.entity_id.lower() and query not in name.lower():
			continue
		where = _area_name(state.entity_id, ents, devs, areas)
		if area and area not in where.lower():
			continue
		found.append(_brief(state, where))
	return {"count": len(found), "entities": found[:limit],
	        **({"more": len(found) - limit} if len(found) > limit else {})}


async def _get_state(hass, context, entity_ids: list) -> dict:
	out = {}
	for entity_id in entity_ids[:20]:
		state = hass.states.get(entity_id)
		if state is None:
			out[entity_id] = {"error": "no such entity"}
			continue
		out[entity_id] = {
			"state": state.state,
			"attributes": dict(state.attributes),
			"last_changed": state.last_changed.isoformat(),
			"last_updated": state.last_updated.isoformat(),
		}
	return out


async def _get_history(hass, context, entity_id: str, hours: int = 24) -> dict:
	if not ENTITY_ID_RE.match(entity_id):
		raise ChatToolError("entity_id must look like light.kitchen")
	from homeassistant.components.recorder import get_instance, history

	hours = max(1, min(int(hours), MAX_HISTORY_HOURS))
	end = dt_util.utcnow()
	start = end - timedelta(hours=hours)
	changes = await get_instance(hass).async_add_executor_job(
		lambda: history.state_changes_during_period(
			hass, start, end, entity_id, no_attributes=True,
			include_start_time_state=True,
		)
	)
	points = [
		{"state": s.state, "at": s.last_changed.isoformat()}
		for s in (changes or {}).get(entity_id, [])
	]
	return {
		"entity_id": entity_id, "hours": hours, "changes": len(points),
		"points": points[-MAX_HISTORY_POINTS:],
		**({"note": f"only the last {MAX_HISTORY_POINTS} are shown"}
		   if len(points) > MAX_HISTORY_POINTS else {}),
	}


async def _list_areas(hass, context) -> dict:
	_ents, _devs, areas = _registries(hass)
	return {"areas": sorted(
		({"area_id": a.id, "name": a.name} for a in areas.async_list_areas()),
		key=lambda a: a["name"].lower(),
	)}


async def _list_automations(hass, context) -> dict:
	rows = []
	for state in sorted(hass.states.async_all("automation"), key=lambda s: s.entity_id):
		row = {
			"entity_id": state.entity_id,
			"name": state.attributes.get("friendly_name") or state.entity_id,
			"enabled": state.state == "on",
		}
		if state.attributes.get("id"):
			row["id"] = state.attributes["id"]
		if state.attributes.get("last_triggered"):
			row["last_triggered"] = str(state.attributes["last_triggered"])
		rows.append(row)
	return {"count": len(rows), "automations": rows,
	        "note": "Only automations with an id can be read or changed here."}


def _read_automations(path: str) -> list:
	import os

	from homeassistant.util.yaml import load_yaml

	if not os.path.isfile(path):
		return []
	data = load_yaml(path)
	return data if isinstance(data, list) else []


def _write_automations(path: str, data: list) -> None:
	from homeassistant.util.file import write_utf8_file_atomic
	from homeassistant.util.yaml import dump

	contents = dump(data)  # before opening, so a dump error cannot truncate
	write_utf8_file_atomic(path, contents)


def _automation_id(hass, automation_id: str) -> str:
	"""Accept the id or ``automation.<entity>``; return the id."""
	automation_id = automation_id.strip()
	if automation_id.startswith("automation."):
		state = hass.states.get(automation_id)
		found = state.attributes.get("id") if state else None
		if not found:
			raise ChatToolError(
				f"{automation_id} has no id, so it is written in YAML by hand "
				"and cannot be read or changed here."
			)
		automation_id = str(found)
	if not AUTOMATION_ID_RE.match(automation_id):
		raise ChatToolError("That is not an automation id.")
	return automation_id


async def _get_automation(hass, context, automation_id: str) -> dict:
	from homeassistant.util.yaml import dump

	key = _automation_id(hass, automation_id)
	data = await hass.async_add_executor_job(
		_read_automations, hass.config.path(AUTOMATIONS_FILE))
	for item in data:
		if isinstance(item, dict) and str(item.get("id")) == key:
			return {"id": key, "yaml": dump(item)}
	raise ChatToolError(f"No automation with id {key} in {AUTOMATIONS_FILE}.")


async def _get_error_log(hass, context, limit: int = 25) -> dict:
	handler = hass.data.get("system_log")
	records = getattr(handler, "records", None)
	if records is None:
		raise ChatToolError("Home Assistant's system log is not running.")
	rows = []
	for rec in records.to_list()[: max(1, min(int(limit), 50))]:
		message = rec.get("message")
		if isinstance(message, list):
			message = " / ".join(str(m) for m in message[:3])
		rows.append({
			"level": rec.get("level"),
			"source": rec.get("name"),
			"message": str(message)[:600],
			"count": rec.get("count"),
			"last": rec.get("timestamp"),
		})
	return {"entries": rows}


async def _render_template(hass, context, template: str) -> dict:
	if len(template) > MAX_TEMPLATE_CHARS:
		raise ChatToolError("That template is too long.")
	from homeassistant.helpers.template import Template

	rendered = Template(template, hass).async_render(parse_result=False)
	return {"result": str(rendered)}


# ── Changing things ─────────────────────────────────────────────────────


async def _call_service(hass, context, domain: str, service: str,
                        entity_ids: Optional[list] = None,
                        data: Optional[dict] = None) -> dict:
	domain, service = domain.strip().lower(), service.strip().lower()
	if not SLUG_RE.match(domain) or not SLUG_RE.match(service):
		raise ChatToolError("domain and service are lowercase names, e.g. light / turn_on.")
	if domain in DENY_DOMAINS or (domain, service) in DENY_SERVICES:
		raise ChatToolError(
			f"{domain}.{service} is not available to the chat. Locks, alarms, "
			"covers, valves, cameras and system actions stay with the owner."
		)
	if not hass.services.has_service(domain, service):
		raise ChatToolError(f"Home Assistant has no action {domain}.{service}.")
	payload = dict(data or {})
	targets = list(entity_ids or [])
	extra = payload.pop("entity_id", None)
	if isinstance(extra, str):
		targets.append(extra)
	elif isinstance(extra, list):
		targets.extend(str(e) for e in extra)
	for entity_id in targets:
		if not ENTITY_ID_RE.match(entity_id):
			raise ChatToolError(f"{entity_id!r} is not an entity id.")
		if entity_id.split(".", 1)[0] in DENY_DOMAINS:
			raise ChatToolError(f"{entity_id} is not available to the chat.")
	if targets:
		payload["entity_id"] = targets
	for key in ("area_id", "device_id", "floor_id", "label_id"):
		# A target by area could reach a lock the entity check never saw.
		if key in payload:
			raise ChatToolError(
				f"Name the entities instead of {key}; find_entities can list an area's."
			)
	# Wait for it, but not for ever: running a script waits for the whole
	# script, delays and all.  Past the wait it carries on by itself.
	task = hass.async_create_task(hass.services.async_call(
		domain, service, payload, blocking=True, context=context))
	done, _pending = await asyncio.wait({task}, timeout=SERVICE_WAIT)
	if not done:
		return {"started": f"{domain}.{service}",
		        "note": f"Still running after {SERVICE_WAIT} seconds; it carries on."}
	task.result()  # raises what the action raised
	return {
		"done": f"{domain}.{service}",
		"now": {e: (hass.states.get(e).state if hass.states.get(e) else None)
		        for e in targets},
	}


def _parse_automation(text: str) -> dict:
	# Plain safe_load, not Home Assistant's loader: that one follows
	# !include and !secret, and a model could use them to copy another
	# file's contents into an automation it then reads back.
	import yaml as pyyaml

	try:
		config = pyyaml.safe_load(text)
	except pyyaml.YAMLError as err:
		raise ChatToolError(f"That is not valid YAML (tags like !secret are not allowed): {err}") from err
	if isinstance(config, list) and len(config) == 1:
		config = config[0]
	if not isinstance(config, dict):
		raise ChatToolError("The YAML must be one automation (a mapping).")
	return json.loads(json.dumps(config))  # plain dicts, no YAML node types


async def _save_automation(hass, context, yaml: str, automation_id: str = "") -> dict:
	from homeassistant.components.automation.config import async_validate_config_item

	config = _parse_automation(yaml)
	key = automation_id.strip() or str(config.pop("id", "") or "") \
		or str(int(time.time() * 1000))
	config.pop("id", None)
	key = _automation_id(hass, key)
	if not config.get("alias"):
		raise ChatToolError("Give the automation an alias, so the owner can find it.")
	try:
		await async_validate_config_item(hass, key, config)
	except Exception as err:  # noqa: BLE001 - vol.Invalid or HomeAssistantError
		raise ChatToolError(f"Home Assistant would not accept it: {err}") from err

	path = hass.config.path(AUTOMATIONS_FILE)
	async with _AUTOMATION_LOCK:
		data = await hass.async_add_executor_job(_read_automations, path)
		value = {"id": key, **config}
		for index, item in enumerate(data):
			if isinstance(item, dict) and str(item.get("id")) == key:
				data[index] = value
				created = False
				break
		else:
			data.append(value)
			created = True
		await hass.async_add_executor_job(_write_automations, path, data)
	await hass.services.async_call(
		"automation", "reload", {"id": key}, blocking=True, context=context)
	return {"id": key, "alias": config["alias"],
	        "result": "created" if created else "updated"}


async def _delete_automation(hass, context, automation_id: str) -> dict:
	key = _automation_id(hass, automation_id)
	path = hass.config.path(AUTOMATIONS_FILE)
	async with _AUTOMATION_LOCK:
		data = await hass.async_add_executor_job(_read_automations, path)
		kept = [i for i in data if not (isinstance(i, dict) and str(i.get("id")) == key)]
		if len(kept) == len(data):
			raise ChatToolError(f"No automation with id {key}.")
		await hass.async_add_executor_job(_write_automations, path, kept)
	from homeassistant.helpers import entity_registry as er

	ents = er.async_get(hass)
	entity_id = ents.async_get_entity_id("automation", "automation", key)
	if entity_id:
		ents.async_remove(entity_id)
	await hass.services.async_call("automation", "reload", {}, blocking=True, context=context)
	return {"id": key, "result": "deleted"}


TOOLS = (
	ChatTool(
		"find_entities",
		"Search this home's entities by words in their name or id, by domain "
		"(light, sensor, ...) and/or by area. Returns ids, names, states, areas.",
		SCOPE_READ, _find_entities,
		(Param("query", "string", "Words to look for in the name or entity id."),
		 Param("domain", "string", "Only this domain, e.g. light."),
		 Param("area", "string", "Only entities in an area whose name contains this."),
		 Param("limit", "integer", "At most this many (default 40, max 100).")),
	),
	ChatTool(
		"get_state",
		"The current state and all attributes of up to 20 entities.",
		SCOPE_READ, _get_state,
		(Param("entity_ids", "string_list", "Entity ids, e.g. sensor.hall_temperature.", True),),
	),
	ChatTool(
		"get_history",
		"How one entity's state changed over the last hours (up to a week).",
		SCOPE_READ, _get_history,
		(Param("entity_id", "string", "The entity id.", True),
		 Param("hours", "integer", "How far back, in hours (default 24, max 168).")),
	),
	ChatTool("list_areas", "The areas (rooms) of this home.", SCOPE_READ, _list_areas),
	ChatTool(
		"list_automations",
		"Every automation: entity id, name, whether it is on, its id and when it last ran.",
		SCOPE_READ, _list_automations,
	),
	ChatTool(
		"get_automation",
		"The YAML of one automation made in the editor, by its id or entity id.",
		SCOPE_READ, _get_automation,
		(Param("automation_id", "string", "The id, or automation.<name>.", True),),
		admin_only=True,
	),
	ChatTool(
		"get_error_log",
		"Recent warnings and errors from Home Assistant's system log, newest first.",
		SCOPE_READ, _get_error_log,
		(Param("limit", "integer", "How many (default 25, max 50)."),),
		admin_only=True,
	),
	ChatTool(
		"render_template",
		"Evaluate a Home Assistant (Jinja) template, for questions a single state "
		"cannot answer, e.g. counting lights that are on.",
		SCOPE_READ, _render_template,
		(Param("template", "string", "The template text.", True),),
		admin_only=True,
	),
	ChatTool(
		"call_service",
		"Run a Home Assistant action (service) on named entities, e.g. light.turn_on "
		"with data {\"brightness_pct\": 40}. Locks, alarms, covers, valves and cameras "
		"are refused.",
		SCOPE_CONTROL, _call_service,
		(Param("domain", "string", "e.g. light", True),
		 Param("service", "string", "e.g. turn_on", True),
		 Param("entity_ids", "string_list", "The entities to act on."),
		 Param("data", "object", "Any other fields the action takes.")),
	),
	ChatTool(
		"save_automation",
		"Create or replace an automation, given as Home Assistant automation YAML "
		"(alias, description, triggers, conditions, actions, mode). Pass automation_id "
		"to replace an existing one. Show the owner the YAML and get a yes first.",
		SCOPE_CONFIGURE, _save_automation,
		(Param("yaml", "string", "The automation, as YAML.", True),
		 Param("automation_id", "string", "The id of the automation to replace.")),
		confirm=True,
	),
	ChatTool(
		"delete_automation",
		"Delete an automation made in the editor. Ask the owner first.",
		SCOPE_CONFIGURE, _delete_automation,
		(Param("automation_id", "string", "The id, or automation.<name>.", True),),
		confirm=True,
	),
)


PROMPT = (
	"You are the Vome assistant inside this Home Assistant. Use the tools to look "
	"before you answer; never guess an entity id — find it. Keep answers short "
	"and plain. Before you create, change or delete an automation, show the owner "
	"what you will do and wait for them to agree. Locks, alarms, covers, valves "
	"and cameras are not yours to operate; say so if asked."
)


# ── Actions, for the add-on panel's Chat page ───────────────────────────


def async_register_chat_services(hass: HomeAssistant) -> None:
	"""``chat_tools``, ``chat_tool_call`` and ``chat_set_scopes``.

	The caller's own user decides what runs, exactly as in Assist: the
	panel calls as the Supervisor (an administrator), and a household
	member's account calling these directly gets the read tools only.
	"""
	from homeassistant.core import ServiceCall, SupportsResponse
	from homeassistant.helpers import config_validation as cv

	from .services_remote import _guard

	async def _tools(call: ServiceCall):
		return {
			"scopes": await async_effective_scopes(hass, call.context.user_id),
			"configured": configured_scopes(hass),
			"prompt": PROMPT,
			"tools": [t.describe() for t in
			          await async_tools_for_user(hass, call.context.user_id)],
		}

	async def _call(call: ServiceCall):
		try:
			result = await async_call_tool(
				hass, call.data["name"], call.data.get("arguments"),
				call.context.user_id)
		except ChatToolError as err:
			return {"ok": False, "error": str(err)}
		return {"ok": True, "result": result}

	async def _set_scopes(call: ServiceCall):
		if not await async_is_admin(hass, call.context.user_id):
			raise ValueError("Only an administrator can change what the chat may do.")
		entry = _entry(hass)
		if entry is None:
			raise ValueError("Set up the Vome integration first.")
		chosen = [s for s in ALL_SCOPES if s in (call.data.get("scopes") or [])]
		hass.config_entries.async_update_entry(
			entry, options={**(entry.options or {}), CONF_CHAT_SCOPES: chosen})
		return {"configured": chosen}

	hass.services.async_register(
		DOMAIN, "chat_tools", _guard(_tools), schema=vol.Schema({}),
		supports_response=SupportsResponse.ONLY,
	)
	hass.services.async_register(
		DOMAIN, "chat_tool_call", _guard(_call),
		schema=vol.Schema({
			vol.Required("name"): cv.string,
			vol.Optional("arguments", default=dict): dict,
		}),
		supports_response=SupportsResponse.ONLY,
	)
	hass.services.async_register(
		DOMAIN, "chat_set_scopes", _guard(_set_scopes),
		schema=vol.Schema({vol.Required("scopes"): [vol.In(ALL_SCOPES)]}),
		supports_response=SupportsResponse.ONLY,
	)
