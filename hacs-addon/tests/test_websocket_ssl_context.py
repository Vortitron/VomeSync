# flake8: noqa
"""The switch WebSocket must not build its SSL context inside the event loop.

Letting `websockets.connect` create its own context does blocking file I/O
(load_default_certs / set_default_verify_paths read the CA bundle from disk) on
the event loop; HA 2026.8 / Python 3.14 logs a warning and asks for a bug report
on every reconnect. We pass HA's import-time context instead.
"""
import ssl
from unittest.mock import patch

import custom_components.vomesync.websocket_client as wc


class TestSslSelection:
	def test_wss_gets_a_context(self):
		assert isinstance(wc._ssl_for("wss://sync.vome.io/ws?uid=x"), ssl.SSLContext)

	def test_plain_ws_gets_none(self):
		# Passing a context for a plaintext socket would be wrong, not merely wasteful.
		assert wc._ssl_for("ws://127.0.0.1:3002/ws?uid=x") is None

	def test_missing_ha_helper_degrades_to_none(self):
		# Older/newer HA, or no HA at all: fall back rather than crash the connect.
		with patch.object(wc, "_default_ssl_context", return_value=None):
			assert wc._ssl_for("wss://sync.vome.io/ws") is None

	def test_context_is_reused_not_rebuilt(self):
		# HA caches it at import time; identity proves we are not constructing
		# a fresh context (and re-reading the CA bundle) per connection.
		assert wc._default_ssl_context() is wc._default_ssl_context()


def test_connect_is_called_with_the_context():
	import asyncio
	from unittest.mock import AsyncMock, MagicMock

	client = wc.VomeSyncWebSocketClient.__new__(wc.VomeSyncWebSocketClient)
	client.base_url = "wss://sync.vome.io/ws"
	client.connections = {}
	client.reconnect_attempts = {}

	fake_ws = AsyncMock()
	fake_ws.__aiter__.return_value = iter([])
	cm = MagicMock()
	cm.__aenter__ = AsyncMock(return_value=fake_ws)
	cm.__aexit__ = AsyncMock(return_value=False)

    # Any handler work after connect is irrelevant here; we only care that the
    # context reached websockets.connect.
	with patch.object(wc.websockets, "connect", MagicMock(return_value=cm)) as conn:
		with patch.object(client, "_send_message", AsyncMock()):
			try:
				asyncio.run(client._connect_to_switch("uid-1"))
			except Exception:
				pass
	assert conn.called, "websockets.connect was not called"
	assert isinstance(conn.call_args.kwargs.get("ssl"), ssl.SSLContext)
