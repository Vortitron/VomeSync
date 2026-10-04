"""Options flow mixin: the coding-agent key, for installs without the add-on.

The add-on panel's Coding agent page is the friendly way to get the free MCP
key: tick what an agent may do, get a key and the commands to paste.  An
install through HACS has no panel, so until now the same thing was only an
action in Developer Tools, which nobody finds.  These steps put it on the
integration's own Configure menu, using the same calls the panel makes
(``agent_key.async_panel_state`` and friends), so the two cannot drift apart.

The key is shown once, on the step after it is issued, and kept nowhere:
Vome stores a hash, and anything here that stored it would be a second place
to steal it from.  Advanced things stay in the panel; this is the key, its
permissions, and the commands for Claude Code (with its side panes),
Cursor and VS Code.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, Optional

import voluptuous as vol
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import config_validation as cv

_LOGGER = logging.getLogger(__name__)

# The same install lines the vome.io key page and the panel show.
CLAUDE_CONNECT = "/plugin install vome-connect --marketplace Vortitron/home-assistant-mcp"
CLAUDE_PANES = "/plugin install vome-panes --marketplace Vortitron/home-assistant-mcp"

SCOPE_LABELS = {
	"ha:read": "See states, history and settings",
	"ha:write": "Control devices (call actions)",
	"ha:config": "Edit automations, scripts and dashboards",
	"ha:files": "Read and write files in /config, including secrets.yaml",
}

_SD_ISSUED = "agent_issued"


def _scopes_text(scopes) -> str:
	labels = [SCOPE_LABELS.get(s, s).lower() for s in (scopes or [])]
	return ", ".join(labels) or "nothing yet"


def _time_left(expires_at, now: Optional[float] = None) -> str:
	try:
		left = float(expires_at) - (time.time() if now is None else now)
	except (TypeError, ValueError):
		return "in about two days"
	if left <= 0:
		return "now"
	hours = int(left // 3600)
	if hours >= 48:
		return f"in {hours // 24} days"
	if hours >= 1:
		return f"in {hours} hour{'s' if hours != 1 else ''}"
	return "within the hour"


def _claude_commands(issued: dict) -> tuple[str, str]:
	"""The connect and panes lines Vome sent, or the standard ones."""
	for client in (issued.get("mcp") or {}).get("clients") or []:
		connect = client.get("connect") if isinstance(client, dict) else None
		if isinstance(connect, dict) and connect.get("command"):
			return connect["command"], connect.get("all_panes") or CLAUDE_PANES
	return CLAUDE_CONNECT, CLAUDE_PANES


def issued_info(issued: dict, now: Optional[float] = None) -> str:
	"""What the step after issuing shows: the key, once, and where to put it."""
	token = issued.get("token") or ""
	connect, panes = _claude_commands(issued)
	mcp_json = (issued.get("mcp") or {}).get("json") or ""
	parts = [
		"**Your key, shown only this once.** Copy it now: Vome keeps a "
		"fingerprint of it and Home Assistant keeps nothing.",
		f"```\n{token}\n```",
		"**Claude Code**, in a terminal. This asks for the key above:",
		f"```\n{connect}\n```",
		"Then the side panes, which show what Claude is doing to your home "
		"as it happens (automations, ESPHome, health score, a working dashboard):",
		f"```\n{panes}\n```",
	]
	if mcp_json:
		parts += [
			"**Cursor, VS Code and other agents**: add this to the agent's MCP settings:",
			f"```json\n{mcp_json}\n```",
		]
	parts.append(
		f"The key reaches this Home Assistant only, and may: "
		f"{_scopes_text(issued.get('scopes'))}. It stops working "
		f"{_time_left(issued.get('expires_at'), now)} unless you sign in to "
		"Vome and connect this Home Assistant, which keeps the same key working."
	)
	return "\n\n".join(parts)


def manage_info(state: dict, now: Optional[float] = None) -> str:
	"""The page for a house that already has a key."""
	if state.get("error"):
		return (
			"This Home Assistant has a coding agent key, but Vome could not be "
			f"asked about it just now ({state['error']}). Try again in a moment."
		)
	if state.get("active") is False:
		return (
			"This Home Assistant's coding agent key has stopped working. "
			"Revoke it to start again with a new one."
		)
	ends = _time_left(state.get("expires_at"), now) if state.get("expires_at") else (
		f"in {int(state.get('seconds_left') or 0) // 3600} hours"
	)
	return (
		f"This Home Assistant has a coding agent key. It may: "
		f"{_scopes_text(state.get('scopes'))}. It stops working {ends} unless "
		"you sign in to Vome and connect this Home Assistant.\n\n"
		"The key itself was shown once, when it was made. If it is lost, "
		"replace it: the new one keeps the same permissions and end date."
	)


class VomeSyncOptionsFlowAgentMixin:
	"""Mixin providing the "Coding agent" steps."""

	async def _agent_state(self) -> dict:
		from . import agent_key

		return await agent_key.async_panel_state(self.hass, self._config_entry)

	async def async_step_coding_agent(
		self, user_input: Optional[Dict[str, Any]] = None
	) -> FlowResult:
		"""Issue a key, or manage the one this house has."""
		from . import agent_key

		state = await self._agent_state()
		offer = state.get("offer")
		if offer == "manage":
			return self.async_show_menu(
				step_id="coding_agent_manage",
				menu_options=[
					"coding_agent_scopes", "coding_agent_reissue",
					"coding_agent_revoke", "back",
				],
				description_placeholders={"info": manage_info(state)},
			)
		if offer in ("linked_account", "guest_link_first"):
			return await self.async_step_coding_agent_elsewhere(offer=offer, state=state)

		errors: Dict[str, str] = {}
		if user_input is not None:
			try:
				issued = await agent_key.async_request_key(
					self.hass, self._config_entry, scopes=user_input.get("scopes"),
				)
			except agent_key.AgentKeyError as err:
				_LOGGER.info("Agent key refused: %s", err)
				errors["base"] = "agent_key_refused"
			except Exception as err:  # noqa: BLE001 - surface it on the form
				_LOGGER.warning("Could not issue an agent key: %s", err)
				errors["base"] = "agent_key_failed"
			else:
				self._step_data[_SD_ISSUED] = issued
				return await self.async_step_coding_agent_issued()

		offered = {s: SCOPE_LABELS.get(s, s) for s in state.get("offered_scopes") or []}
		return self.async_show_form(
			step_id="coding_agent",
			data_schema=vol.Schema({
				vol.Required(
					"scopes", default=list(state.get("default_scopes") or []),
				): cv.multi_select(offered),
			}),
			errors=errors,
		)

	async def async_step_coding_agent_manage(
		self, user_input: Optional[Dict[str, Any]] = None
	) -> FlowResult:
		"""The manage menu's own step id; it is drawn by ``coding_agent``."""
		return await self.async_step_coding_agent()

	async def async_step_coding_agent_issued(
		self, user_input: Optional[Dict[str, Any]] = None
	) -> FlowResult:
		"""The key, once.  Submitting goes back to the menu and forgets it."""
		issued = self._step_data.get(_SD_ISSUED)
		if user_input is not None or not issued:
			self._step_data.pop(_SD_ISSUED, None)
			return await self.async_step_init()
		return self.async_show_form(
			step_id="coding_agent_issued",
			data_schema=vol.Schema({}),
			description_placeholders={"info": issued_info(issued)},
		)

	async def async_step_coding_agent_elsewhere(
		self, user_input: Optional[Dict[str, Any]] = None, *,
		offer: Optional[str] = None, state: Optional[dict] = None,
	) -> FlowResult:
		"""This house's keys come from somewhere else: say where."""
		if user_input is not None:
			return await self.async_step_init()
		state = state or {}
		portal = (state.get("portal_url") or "https://vome.io").rstrip("/")
		if offer == "linked_account":
			info = (
				"This Home Assistant is linked to your Vome account, so its keys "
				f"live there: [{portal}/account/api-tokens]({portal}/account/api-tokens). "
				"The page has the Claude Code commands too, side panes included."
			)
		else:
			info = (
				"This Home Assistant is on a temporary health-score link. Keep it "
				"by signing in from the link in the score, or let it expire, and "
				"then come back here for a key."
			)
		return self.async_show_form(
			step_id="coding_agent_elsewhere",
			data_schema=vol.Schema({}),
			description_placeholders={"info": info},
		)

	async def async_step_coding_agent_scopes(
		self, user_input: Optional[Dict[str, Any]] = None
	) -> FlowResult:
		"""Change what the key may do; its secret stays the same."""
		from . import agent_key

		errors: Dict[str, str] = {}
		state = await self._agent_state()
		if user_input is not None:
			try:
				await agent_key.async_set_scopes(
					self.hass, self._config_entry, user_input.get("scopes"),
				)
			except Exception as err:  # noqa: BLE001 - surface it on the form
				_LOGGER.warning("Could not change the agent key's permissions: %s", err)
				errors["base"] = "agent_key_failed"
			else:
				return await self.async_step_coding_agent()
		offered = {s: SCOPE_LABELS.get(s, s) for s in state.get("offered_scopes") or []}
		current = [s for s in (state.get("scopes") or []) if s in offered]
		return self.async_show_form(
			step_id="coding_agent_scopes",
			data_schema=vol.Schema({
				vol.Required("scopes", default=current): cv.multi_select(offered),
			}),
			errors=errors,
		)

	async def async_step_coding_agent_reissue(
		self, user_input: Optional[Dict[str, Any]] = None
	) -> FlowResult:
		"""A new key for a lost one; the old one stops."""
		from . import agent_key

		errors: Dict[str, str] = {}
		if user_input is not None:
			try:
				issued = await agent_key.async_reissue(self.hass, self._config_entry)
			except Exception as err:  # noqa: BLE001 - surface it on the form
				_LOGGER.warning("Could not replace the agent key: %s", err)
				errors["base"] = "agent_key_failed"
			else:
				self._step_data[_SD_ISSUED] = issued
				return await self.async_step_coding_agent_issued()
		return self.async_show_form(
			step_id="coding_agent_reissue", data_schema=vol.Schema({}), errors=errors,
		)

	async def async_step_coding_agent_revoke(
		self, user_input: Optional[Dict[str, Any]] = None
	) -> FlowResult:
		"""End the key now."""
		from . import agent_key

		errors: Dict[str, str] = {}
		if user_input is not None:
			try:
				await agent_key.async_revoke(self.hass, self._config_entry)
			except Exception as err:  # noqa: BLE001 - surface it on the form
				_LOGGER.warning("Could not revoke the agent key: %s", err)
				errors["base"] = "agent_key_failed"
			else:
				return await self.async_step_init()
		return self.async_show_form(
			step_id="coding_agent_revoke", data_schema=vol.Schema({}), errors=errors,
		)
