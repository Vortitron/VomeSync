# flake8: noqa
"""The panel-facing services must surface real errors, not opaque 400s.

Regression cover for the bug where a linked-entry ambiguity (or any handler
exception) reached the add-on panel as a bare "400: Bad Request" with the
real reason stripped by Home Assistant's REST layer.
"""
import asyncio

from custom_components.vomesync.services_remote import _guard


def test_guard_converts_exception_to_error_dict():
	async def boom(_call):
		raise ValueError("Multiple linked entries; pass entry_id")

	result = asyncio.run(_guard(boom)(None))
	assert result == {"error": "Multiple linked entries; pass entry_id"}


def test_guard_uses_class_name_when_message_empty():
	async def boom(_call):
		raise RuntimeError()

	result = asyncio.run(_guard(boom)(None))
	assert result == {"error": "RuntimeError"}


def test_guard_passes_success_through_untouched():
	async def ok(_call):
		return {"linked": True, "entry_id": "abc"}

	result = asyncio.run(_guard(ok)(None))
	assert result == {"linked": True, "entry_id": "abc"}
