"""Options flow mixin: linked-entity configuration for VomeSync."""
import logging
from typing import Any, Dict, Optional

import voluptuous as vol
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.selector import selector

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


class VomeSyncOptionsFlowLinkEntitiesMixin:
	"""Mixin providing entity-linking steps for the options flow."""

	def _is_listen_only_switch(self) -> bool:
		"""Return True when the selected switch should be read-only."""
		is_owner = bool(self._step_data.get("is_owner", False))
		has_access_key = bool(self._step_data.get("has_access_key", False))
		return (not is_owner) and (not has_access_key)

	async def async_step_link_entities(
		self, user_input: Optional[Dict[str, Any]] = None
	) -> FlowResult:
		"""Link local entities to sync with this switch."""
		selected_uid = self._step_data.get("selected_uid")
		selected_name = self._step_data.get("selected_name", selected_uid)
		
		listen_only = self._is_listen_only_switch()

		if user_input is not None:
			# Update links for this switch
			selected_entities = user_input.get("entities", [])
			if not isinstance(selected_entities, list):
				selected_entities = list(selected_entities)
			if listen_only:
				direction = "switch_to_entities"
			else:
				direction = self._normalise_link_direction(user_input.get("direction"), default="both")
			
			_LOGGER.info("Linking entities for switch %s: %s", selected_uid, selected_entities)
			
			# No entities selected => remove linkage
			if not selected_entities:
				options = dict(self._config_entry.options or {})
				linked_entities = dict(options.get("linked_entities", {}) or {})
				if selected_uid in linked_entities:
					del linked_entities[selected_uid]
				options["linked_entities"] = linked_entities
				await self._async_update_entry_options(options)
				
				# Rebuild listeners (best effort)
				try:
					coordinator = self.hass.data[DOMAIN][self._config_entry.entry_id]
					await coordinator.async_setup_entity_links()
				except Exception as err:  # noqa: BLE001
					_LOGGER.error("Failed to set up entity links: %s", err)
				
				return self.async_create_entry(title="", data=options)
			
			# One entity => no extra questions; just store as master mode
			if len(selected_entities) == 1:
				options = dict(self._config_entry.options or {})
				linked_entities = dict(options.get("linked_entities", {}) or {})
				linked_entities[selected_uid] = {
					"entities": list(selected_entities),
					"mode": "master",
					"master": selected_entities[0],
					"direction": direction,
				}
				options["linked_entities"] = linked_entities
				await self._async_update_entry_options(options)
				
				try:
					coordinator = self.hass.data[DOMAIN][self._config_entry.entry_id]
					await coordinator.async_setup_entity_links()
				except Exception as err:  # noqa: BLE001
					_LOGGER.error("Failed to set up entity links: %s", err)
				
				return self.async_create_entry(title="", data=options)
			
			# Multiple entities:
			# - If direction is switch->entities only, no behaviour is needed (we just toggle them all).
			# - Otherwise, ask how linked entities should drive the switch.
			if direction == "switch_to_entities":
				options = dict(self._config_entry.options or {})
				linked_entities = dict(options.get("linked_entities", {}) or {})
				linked_entities[selected_uid] = {
					"entities": list(selected_entities),
					"mode": "master",
					"master": selected_entities[0],
					"direction": direction,
				}
				options["linked_entities"] = linked_entities
				await self._async_update_entry_options(options)
				
				try:
					coordinator = self.hass.data[DOMAIN][self._config_entry.entry_id]
					await coordinator.async_setup_entity_links()
				except Exception as err:  # noqa: BLE001
					_LOGGER.error("Failed to set up entity links: %s", err)
				
				return self.async_create_entry(title="", data=options)
			
			# Ask for behaviour (master / OR / AND)
			self._step_data["pending_link_entities"] = list(selected_entities)
			self._step_data["pending_link_direction"] = direction
			return await self.async_step_link_entities_behaviour()
		
		# Get currently linked entities for this switch
		options = self._config_entry.options or {}
		linked_entities = options.get("linked_entities", {}) or {}
		current_cfg = linked_entities.get(selected_uid)
		current_links: list[str]
		current_direction = "both"
		if isinstance(current_cfg, dict):
			raw = current_cfg.get("entities", [])
			current_links = raw if isinstance(raw, list) else []
			current_direction = self._normalise_link_direction(current_cfg.get("direction"), default="both")
		else:
			current_links = []
		if listen_only:
			current_direction = "switch_to_entities"
		
		# Get all switchable entities from HA
		entity_reg = er.async_get(self.hass)
		
		# Get all entities that support turn_on/turn_off
		switchable_domains = ["switch", "light", "fan", "input_boolean", "automation", "script"]
		candidates: list[tuple[str, str, str]] = []
		seen_entities = set()
		
		for entity in entity_reg.entities.values():
			if entity.domain in switchable_domains:
				# Skip our own VomeSync entities
				if entity.config_entry_id == self._config_entry.entry_id:
					continue
				
				# Get friendly name
				state = self.hass.states.get(entity.entity_id)
				name = (state.attributes.get("friendly_name") if state else None) or entity.original_name or entity.entity_id
				label = f"{name} ({entity.entity_id})"
				candidates.append((str(name).casefold(), entity.entity_id, label))
				seen_entities.add(entity.entity_id)
		
		# Ensure existing links are still selectable even if the entity is missing from the registry.
		for entity_id in current_links:
			if entity_id not in seen_entities:
				candidates.append((str(entity_id).casefold(), entity_id, entity_id))
		
		if not candidates:
			return self.async_abort(reason="no_linkable_entities")
		
		available_entities: Dict[str, str] = {}
		include_entities: list[str] = []
		for _name_key, entity_id, label in sorted(candidates):
			if entity_id in available_entities:
				continue
			available_entities[entity_id] = label
			include_entities.append(entity_id)
		
		_LOGGER.debug("Current links for %s: %s", selected_uid, current_links)
		_LOGGER.debug("Available entities: %s", list(available_entities.keys())[:5])
		
		# Keep available entity labels for the next step (behaviour/master selection)
		self._step_data["link_available_entities"] = available_entities
		self._step_data["link_direction_labels"] = self._get_link_direction_labels()
		
		data_fields = {
			vol.Optional("entities", default=current_links): selector({
				"entity": {
					"multiple": True,
					"include_entities": include_entities,
				}
			}),
		}
		if not listen_only:
			direction_labels = self._get_link_direction_labels()
			data_fields[vol.Optional("direction", default=current_direction)] = vol.In(direction_labels)

		return self.async_show_form(
			step_id="link_entities",
			data_schema=vol.Schema(data_fields),
			description_placeholders={
				"switch_name": selected_name,
				"info": (
					f"Link one or more entities to **{selected_name}**.\n\n"
					f"Currently linked: {len(current_links)} entities"
				),
			}
		)

	async def async_step_link_entities_behaviour(
		self, user_input: Optional[Dict[str, Any]] = None
	) -> FlowResult:
		"""Choose how multiple linked entities should drive the switch."""
		selected_uid = self._step_data.get("selected_uid")
		selected_name = self._step_data.get("selected_name", selected_uid)
		selected_entities = self._step_data.get("pending_link_entities") or []
		listen_only = self._is_listen_only_switch()
		direction_default = self._normalise_link_direction(self._step_data.get("pending_link_direction"), default="both")
		available_entities = self._step_data.get("link_available_entities") or {}
		
		if not selected_uid or not isinstance(selected_entities, list) or len(selected_entities) <= 1:
			return self.async_abort(reason="no_linkable_entities")
		
		# Default: master=first entity
		master_default = selected_entities[0]
		
		# If existing config already has a mode/master, prefill
		options = self._config_entry.options or {}
		linked_entities = options.get("linked_entities", {}) or {}
		existing = linked_entities.get(selected_uid)
		mode_default = "master"
		if isinstance(existing, dict):
			mode_default = existing.get("mode") if isinstance(existing.get("mode"), str) else mode_default
			existing_master = existing.get("master")
			if isinstance(existing_master, str) and existing_master in selected_entities:
				master_default = existing_master
			direction_default = self._normalise_link_direction(existing.get("direction"), default=direction_default)
		if listen_only:
			direction_default = "switch_to_entities"
		
		if user_input is not None:
			mode = user_input.get("mode") if isinstance(user_input.get("mode"), str) else "master"
			master = user_input.get("master") if isinstance(user_input.get("master"), str) else master_default
			if master not in selected_entities:
				master = master_default
			if listen_only:
				direction = "switch_to_entities"
			else:
				direction = self._normalise_link_direction(user_input.get("direction"), default=direction_default)
			
			# Persist the config
			new_options = dict(self._config_entry.options or {})
			new_linked = dict(new_options.get("linked_entities", {}) or {})
			new_linked[selected_uid] = {
				"entities": list(selected_entities),
				"mode": mode,
				"master": master,
				"direction": direction,
			}
			new_options["linked_entities"] = new_linked
			await self._async_update_entry_options(new_options)
			
			# Trigger coordinator to (re)configure listeners
			try:
				coordinator = self.hass.data[DOMAIN][self._config_entry.entry_id]
				await coordinator.async_setup_entity_links()
			except Exception as err:  # noqa: BLE001
				_LOGGER.error("Failed to set up entity links: %s", err)
			
			# Clean up temporary step state
			self._step_data.pop("pending_link_entities", None)
			self._step_data.pop("pending_link_direction", None)
			return self.async_create_entry(title="", data=new_options)
		
		master_labels = {
			eid: (available_entities.get(eid) if isinstance(available_entities, dict) else None) or eid
			for eid in selected_entities
		}
		
		mode_labels = {
			"master": "Master (only one linked entity drives the switch)",
			"or": "Any on (OR) — switch turns on if any linked entity is on",
			"and": "All on (AND) — switch turns on only if all linked entities are on",
		}
		data_fields = {
			vol.Required("mode", default=mode_default): vol.In(mode_labels),
			vol.Optional("master", default=master_default): vol.In(master_labels),
		}
		if not listen_only:
			direction_labels = self._get_link_direction_labels()
			data_fields[vol.Required("direction", default=direction_default)] = vol.In(direction_labels)

		return self.async_show_form(
			step_id="link_entities_behaviour",
			data_schema=vol.Schema(data_fields),
			description_placeholders={
				"info": (
					f"Choose how multiple linked entities should update **{selected_name}**.\n\n"
					"Tip: If you want the switch to follow one specific entity, use **Master**.\n"
					"Note: The **Master** selection only applies when mode is **Master**."
				)
			},
		)
