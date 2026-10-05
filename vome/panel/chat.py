"""The panel's Chat page: the owner's OpenRouter key, talking to this house.

The conversation runs here, in the add-on, and goes straight from this
house to OpenRouter.  Nothing passes through Vome.  The model reaches Home
Assistant only through the integration's chat tools
(``vomesync.chat_tool_call``), so it gets exactly what Assist's agents
get, within the same owner-chosen limits.

**The key** lives in this add-on's own ``/data/chat.json``, readable by
nobody else, and is left out of Home Assistant backups (``backup_exclude``
in config.yaml): backups end up in cloud storage, and a spending key has no
business being in one.

**Changes wait for a click.**  A tool marked ``confirm`` (writing or
deleting an automation) is not run when the model asks: the turn stops,
the page shows what it would do, and the owner approves or declines.  That
is the trust model the portal's assistant works to, too — the owner runs
changes to their house, an AI only proposes them.

**The browser holds the conversation.**  Each request carries it, so the
add-on keeps no transcript of anyone's house.  It is bounded
(``MAX_MESSAGES``) because every message is paid for again on every turn.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, Optional

OPENROUTER_API = "https://openrouter.ai/api/v1"
DEFAULT_MODEL = "anthropic/claude-sonnet-5.5"
SETTINGS_FILE = Path(os.environ.get("VOME_CHAT_SETTINGS", "/data/chat.json"))

# A turn is the model calling tools until it has an answer.  Past this it
# stops and says so, rather than spending the owner's credit in a loop.
MAX_ROUNDS = 12
MAX_MESSAGES = 80
MAX_TOKENS = 4096
MODEL_TIMEOUT = 120
MODELS_TTL = 3600
# A 429 is usually momentary (free models above all); two short waits
# before giving up, inside the panel request, so the owner need not resend.
RATE_LIMIT_BACKOFF = (4, 10)
sleep = time.sleep

# Only providers that neither store nor train on prompts.  A house's
# states and automations are exactly the data that should not end up in a
# training set.  https://openrouter.ai/docs/features/provider-routing
PROVIDER_POLICY = {"data_collection": "deny"}

PANEL_NOTE = (
	" The owner is chatting in the Vome app inside Home Assistant. When you "
	"call save_automation or delete_automation, the app shows the owner what "
	"you are about to do with an Approve button, so call the tool directly "
	"with a one-line explanation instead of asking first in text."
)

Post = Callable[[str, dict, dict, int], tuple]
Get = Callable[[str, dict, int], tuple]
CallTool = Callable[[str, dict], dict]


class ChatError(Exception):
	"""Something to tell the owner, in words they can act on."""


# ── Settings ────────────────────────────────────────────────────────────


def load_settings(path: Optional[Path] = None) -> dict:
	target = path or SETTINGS_FILE
	try:
		data = json.loads(target.read_text(encoding="utf-8"))
	except (OSError, ValueError):
		return {}
	return data if isinstance(data, dict) else {}


def save_settings(values: dict, path: Optional[Path] = None) -> dict:
	target = path or SETTINGS_FILE
	current = load_settings(target)
	current.update({k: v for k, v in values.items() if v is not None})
	current = {k: v for k, v in current.items() if v != ""}
	target.parent.mkdir(parents=True, exist_ok=True)
	tmp = target.with_suffix(".tmp")
	# Created 0600, not chmod-ed afterwards: no moment where the key is
	# readable by anyone else.
	fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
	with os.fdopen(fd, "w", encoding="utf-8") as fh:
		json.dump(current, fh)
	os.replace(tmp, target)
	return current


def key_hint(key: str) -> str:
	return f"{key[:6]}…{key[-4:]}" if len(key) > 12 else "set"


def public_settings(settings: dict) -> dict:
	"""What the page may see: never the key itself."""
	key = str(settings.get("api_key") or "")
	return {
		"has_key": bool(key),
		"key_hint": key_hint(key) if key else "",
		"model": settings.get("model") or DEFAULT_MODEL,
		"default_model": DEFAULT_MODEL,
	}


# ── OpenRouter ──────────────────────────────────────────────────────────


def http_post(url: str, headers: dict, body: dict, timeout: int) -> tuple:
	data = json.dumps(body).encode("utf-8")
	req = urllib.request.Request(url, data=data, headers=headers, method="POST")
	return _send(req, timeout)


def http_get(url: str, headers: dict, timeout: int) -> tuple:
	return _send(urllib.request.Request(url, headers=headers, method="GET"), timeout)


def _send(req, timeout: int) -> tuple:
	try:
		with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - fixed host
			raw, status = resp.read(), resp.status
	except urllib.error.HTTPError as err:
		raw, status = err.read(), err.code
	except (urllib.error.URLError, TimeoutError, OSError) as err:
		raise ChatError(f"Could not reach OpenRouter: {getattr(err, 'reason', err)}") from err
	try:
		return status, json.loads(raw.decode("utf-8")) if raw else {}
	except (ValueError, UnicodeDecodeError):
		return status, {"error": {"message": raw.decode("utf-8", "replace")[:300]}}


def _headers(api_key: str) -> dict:
	return {
		"Authorization": f"Bearer {api_key}",
		"Content-Type": "application/json",
		# OpenRouter's app attribution: lists Vome as the app making the calls.
		"HTTP-Referer": "https://vome.io",
		"X-Title": "Vome",
	}


def _explain(status: int, body: Any) -> str:
	err = body.get("error") if isinstance(body, dict) else None
	message = (err.get("message") if isinstance(err, dict) else err) or f"HTTP {status}"
	if status == 401:
		return "OpenRouter did not accept the key. Check it, or make a new one at openrouter.ai/keys."
	if status == 402:
		return (
			"Your OpenRouter account is out of credit. Top it up at openrouter.ai/credits, "
			"or pick a free model (slower, and limited to about 50 messages a day)."
		)
	if status == 404 and "data policy" in str(message).lower():
		return (
			"No provider for that model promises not to store or train on your "
			"messages, so it is not used from here. Pick another model."
		)
	if status == 429:
		return "OpenRouter says to slow down. Wait a moment and try again."
	return f"OpenRouter: {message}"


def check_key(api_key: str, get: Get = http_get) -> dict:
	"""Ask OpenRouter about a key before keeping it."""
	status, body = get(f"{OPENROUTER_API}/key", _headers(api_key), 20)
	if status != 200:
		raise ChatError(_explain(status, body))
	data = body.get("data") if isinstance(body, dict) else None
	return data if isinstance(data, dict) else {}


_models_cache: dict = {"at": 0.0, "models": []}


def list_models(get: Get = http_get, now: Optional[float] = None) -> list:
	"""Models that can use tools, cheapest first within each maker."""
	now = time.time() if now is None else now
	if _models_cache["models"] and now - _models_cache["at"] < MODELS_TTL:
		return _models_cache["models"]
	status, body = get(f"{OPENROUTER_API}/models", {"Accept": "application/json"}, 20)
	if status != 200 or not isinstance(body, dict):
		raise ChatError(_explain(status, body))
	models = []
	for m in body.get("data") or []:
		mid = str(m.get("id") or "")
		if not mid or "tools" not in (m.get("supported_parameters") or []):
			continue
		if ":" in mid and not mid.endswith(":free"):
			continue  # ":batch" and the like are not models to chat with; ":free" is
		pricing = m.get("pricing") or {}
		try:
			per_m_in = float(pricing.get("prompt") or 0) * 1e6
			per_m_out = float(pricing.get("completion") or 0) * 1e6
		except (TypeError, ValueError):
			continue
		models.append({"id": mid, "name": m.get("name") or mid,
		               "in": round(per_m_in, 3), "out": round(per_m_out, 3)})
	models.sort(key=lambda x: (x["id"].split("/")[0], x["name"]))
	_models_cache.update(at=now, models=models)
	return models


def _complete(api_key: str, model: str, messages: list, tools: list, post: Post) -> dict:
	body = {
		"model": model,
		"messages": messages,
		"max_tokens": MAX_TOKENS,
		"provider": PROVIDER_POLICY,
	}
	if tools:
		body["tools"] = tools
	for wait in RATE_LIMIT_BACKOFF + (None,):
		status, reply = post(f"{OPENROUTER_API}/chat/completions", _headers(api_key), body,
		                     MODEL_TIMEOUT)
		if status != 429 or wait is None:
			break
		sleep(wait)
	if status != 200 or not isinstance(reply, dict) or reply.get("error"):
		raise ChatError(_explain(status, reply))
	choices = reply.get("choices") or []
	if not choices:
		raise ChatError("OpenRouter sent an empty answer. Try again.")
	return reply


# ── A turn ──────────────────────────────────────────────────────────────


def _clean_history(messages: Any) -> list:
	"""Keep the shapes a chat completion takes, and only the recent ones."""
	if not isinstance(messages, list):
		raise ChatError("Send the conversation as a list.")
	out = []
	for m in messages[-MAX_MESSAGES:]:
		if not isinstance(m, dict) or m.get("role") not in ("user", "assistant", "tool"):
			continue
		clean = {"role": m["role"], "content": m.get("content") or ""}
		if m["role"] == "assistant" and isinstance(m.get("tool_calls"), list):
			clean["tool_calls"] = m["tool_calls"]
		if m["role"] == "tool":
			clean["tool_call_id"] = str(m.get("tool_call_id") or "")
		out.append(clean)
	# A cut can leave tool results whose call was dropped; a provider
	# rejects those, so start at the first person-written message.
	while out and out[0]["role"] != "user":
		out.pop(0)
	return _close_skipped_calls(out)


def _close_skipped_calls(messages: list) -> list:
	"""A call the owner talked past instead of answering counts as a no.

	Every tool call must have its result before the next message, or the
	provider refuses the whole conversation.  The last assistant message
	is left alone: its calls may be the ones waiting for a click now.
	"""
	out = []
	for i, m in enumerate(messages):
		out.append(m)
		calls = m.get("tool_calls") if m["role"] == "assistant" else None
		if not calls:
			continue
		later = messages[i + 1:]
		if not any(x["role"] in ("user", "assistant") for x in later):
			continue
		answered = set()
		for x in later:
			if x["role"] != "tool":
				break
			answered.add(x.get("tool_call_id"))
		for call in calls:
			if call.get("id") not in answered:
				out.append({"role": "tool", "tool_call_id": call.get("id") or "",
				            "content": json.dumps({"ok": False, "declined": "Not run."})})
	return out


def _unanswered(messages: list) -> list:
	"""Tool calls in the last assistant message that have no result yet."""
	for i in range(len(messages) - 1, -1, -1):
		m = messages[i]
		if m["role"] == "assistant":
			answered = {x.get("tool_call_id") for x in messages[i + 1:] if x["role"] == "tool"}
			return [c for c in (m.get("tool_calls") or []) if c.get("id") not in answered]
		if m["role"] == "user":
			return []
	return []


def _args(call: dict) -> dict:
	raw = (call.get("function") or {}).get("arguments") or "{}"
	try:
		args = json.loads(raw) if isinstance(raw, str) else raw
	except ValueError:
		return {"__unparsed__": raw}
	return args if isinstance(args, dict) else {}


def _run(call: dict, call_tool: CallTool) -> dict:
	name = (call.get("function") or {}).get("name") or ""
	args = _args(call)
	if "__unparsed__" in args:
		result = {"ok": False, "error": "The arguments were not valid JSON."}
	else:
		try:
			result = call_tool(name, args)
		except Exception as err:  # noqa: BLE001 - the model gets the reason and carries on
			result = {"ok": False, "error": str(err) or type(err).__name__}
	return {"role": "tool", "tool_call_id": call.get("id") or "",
	        "content": json.dumps(result, default=str)}


def run_turn(messages: Any, settings: dict, toolset: dict, call_tool: CallTool,
             post: Post = http_post, approve: Optional[bool] = None) -> dict:
	"""Carry the conversation on until the model answers or needs a yes.

	``toolset`` is what ``vomesync.chat_tools`` returned.  ``approve``
	answers the confirmation the last turn stopped at: True runs the
	waiting calls, False tells the model the owner said no.

	Returns ``{"messages": [...], "pending": [calls] | [], "cost": float}``.
	"""
	api_key = str(settings.get("api_key") or "")
	if not api_key:
		raise ChatError("Add your OpenRouter key first.")
	model = settings.get("model") or DEFAULT_MODEL
	history = _clean_history(messages)
	if not history:
		raise ChatError("Say something first.")
	described = {t["name"]: t for t in (toolset.get("tools") or [])}
	tools = [{"type": "function", "function": {
		"name": t["name"], "description": t.get("description") or "",
		"parameters": t.get("parameters") or {"type": "object", "properties": {}},
	}} for t in described.values()]
	system = {"role": "system", "content": (toolset.get("prompt") or "") + PANEL_NOTE}
	cost = 0.0

	waiting = _unanswered(history)
	if waiting:
		for call in waiting:
			name = (call.get("function") or {}).get("name")
			needs_yes = bool((described.get(name) or {}).get("confirm"))
			if needs_yes and approve is not True:
				history.append({"role": "tool", "tool_call_id": call.get("id") or "",
				                "content": json.dumps({"ok": False, "declined":
				                                       "The owner said no to this."})})
			else:
				history.append(_run(call, call_tool))

	for _ in range(MAX_ROUNDS):
		reply = _complete(api_key, model, [system] + history, tools, post)
		cost += float((reply.get("usage") or {}).get("cost") or 0)
		message = reply["choices"][0].get("message") or {}
		calls = [c for c in (message.get("tool_calls") or []) if isinstance(c, dict)]
		entry = {"role": "assistant", "content": message.get("content") or ""}
		if calls:
			entry["tool_calls"] = calls
		history.append(entry)
		if not calls:
			return {"messages": history, "pending": [], "cost": cost, "model": model}
		if any((described.get((c.get("function") or {}).get("name")) or {}).get("confirm")
		       for c in calls):
			return {"messages": history, "pending": calls, "cost": cost, "model": model}
		for call in calls:
			history.append(_run(call, call_tool))

	history.append({"role": "assistant", "content":
	                f"(Stopped after {MAX_ROUNDS} steps without an answer. Ask again, more narrowly.)"})
	return {"messages": history, "pending": [], "cost": cost, "model": model}
