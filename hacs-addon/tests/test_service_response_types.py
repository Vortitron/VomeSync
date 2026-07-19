# flake8: noqa
"""Panel-facing services must be SupportsResponse.ONLY.

The add-on panel calls every remote service over the REST API with
``?return_response`` and consumes the returned dict.  A service registered as
OPTIONAL (rather than ONLY) invoked that way is rejected by Home Assistant as a
bare "400: Bad Request" with no message — the failure that made every panel
write look broken.  Pin the response type so it can't regress.
"""
from unittest.mock import MagicMock

from homeassistant.core import SupportsResponse

from custom_components.vomesync.services_remote import async_register_remote_services

PANEL_SERVICES = {
	"get_remote_status",
	"set_forward_ui",
	"set_lan_routes",
	"add_lan_route",
	"remove_lan_route",
	"mint_lan_tcp_token",
}


def _registered(hass_calls):
	return {
		call.args[1]: call.kwargs.get("supports_response")
		for call in hass_calls
	}


def test_panel_services_are_response_only():
	hass = MagicMock()
	hass.data = {}
	async_register_remote_services(hass)
	registered = _registered(hass.services.async_register.call_args_list)

	for name in PANEL_SERVICES:
		assert name in registered, f"{name} was not registered"
		assert registered[name] is SupportsResponse.ONLY, (
			f"{name} must be SupportsResponse.ONLY — the panel calls it with "
			"?return_response, and OPTIONAL yields a bare 400 over REST"
		)
