# flake8: noqa
"""Tests for the device-registry compatibility helpers.

Core 2026.8 restricted devices to a single config entry and deprecated the
identifier lookups that assumed identifiers were globally unique.  We still
support HA back to 2024.1, so the helpers must pick the scoped API when it
exists and the global one when it does not — and must scope correctly, because
the whole point of the change is that another integration may now legitimately
own a device with the same identifier.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from custom_components.vomesync.device_compat import (
	async_get_device_by_identifier,
	async_remove_device_by_identifier,
)


def _registry(*, scoped: bool, device=None):
	"""A fake registry either with (2026.8+) or without (older) the scoped API."""
	registry = MagicMock()
	registry.async_get_device.return_value = device
	if scoped:
		registry.async_get_device_by_identifier = MagicMock(return_value=device)
	else:
		# MagicMock invents attributes on access, so the absence has to be explicit.
		del registry.async_get_device_by_identifier
	return registry


class TestGetDeviceByIdentifier:
	def test_uses_scoped_lookup_when_available(self):
		device = SimpleNamespace(id="dev-1")
		registry = _registry(scoped=True, device=device)
		with patch("custom_components.vomesync.device_compat.dr.async_get", return_value=registry):
			found = async_get_device_by_identifier(MagicMock(), ("vomesync", "uid-1"), "entry-1")
		assert found is device
		registry.async_get_device_by_identifier.assert_called_once_with(
			("vomesync", "uid-1"), "entry-1"
		)
		registry.async_get_device.assert_not_called()

	def test_falls_back_to_global_lookup_on_older_ha(self):
		device = SimpleNamespace(id="dev-1")
		registry = _registry(scoped=False, device=device)
		with patch("custom_components.vomesync.device_compat.dr.async_get", return_value=registry):
			found = async_get_device_by_identifier(MagicMock(), ("vomesync", "uid-1"), "entry-1")
		assert found is device
		registry.async_get_device.assert_called_once_with(identifiers={("vomesync", "uid-1")})

	def test_without_an_entry_id_uses_the_global_lookup(self):
		# Nothing to scope by — the unscoped call is the only correct option.
		device = SimpleNamespace(id="dev-1")
		registry = _registry(scoped=True, device=device)
		with patch("custom_components.vomesync.device_compat.dr.async_get", return_value=registry):
			found = async_get_device_by_identifier(MagicMock(), ("vomesync", "uid-1"), None)
		assert found is device
		registry.async_get_device_by_identifier.assert_not_called()
		registry.async_get_device.assert_called_once()

	def test_returns_none_when_absent(self):
		registry = _registry(scoped=True, device=None)
		with patch("custom_components.vomesync.device_compat.dr.async_get", return_value=registry):
			assert async_get_device_by_identifier(MagicMock(), ("vomesync", "x"), "entry-1") is None


class TestRemoveDeviceByIdentifier:
	def test_removes_and_reports_true(self):
		device = SimpleNamespace(id="dev-1")
		registry = _registry(scoped=True, device=device)
		with patch("custom_components.vomesync.device_compat.dr.async_get", return_value=registry):
			assert async_remove_device_by_identifier(MagicMock(), ("vomesync", "uid-1"), "entry-1") is True
		registry.async_remove_device.assert_called_once_with("dev-1")

	def test_missing_device_is_not_an_error(self):
		# Forgetting a switch whose device is already gone must stay a no-op.
		registry = _registry(scoped=True, device=None)
		with patch("custom_components.vomesync.device_compat.dr.async_get", return_value=registry):
			assert async_remove_device_by_identifier(MagicMock(), ("vomesync", "gone"), "entry-1") is False
		registry.async_remove_device.assert_not_called()
