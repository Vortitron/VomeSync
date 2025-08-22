"""WebSocket client for real-time VomeSync updates."""
import asyncio
import json
import logging
from typing import Any, Callable, Dict, Optional
import websockets
from websockets.exceptions import ConnectionClosed, InvalidURI

from .const import (
	WS_MSG_STATE_UPDATE,
	WS_MSG_ERROR,
	WS_MSG_PING,
	WS_MSG_PONG,
	WS_MSG_SUBSCRIBE,
	WS_MSG_UNSUBSCRIBE,
	WEBSOCKET_RECONNECT_DELAY,
)

_LOGGER = logging.getLogger(__name__)


class VomeSyncWebSocketClient:
	"""WebSocket client for VomeSync real-time updates."""

	def __init__(
		self,
		base_url: str,
		message_handler: Callable[[str, Dict[str, Any]], None]
	) -> None:
		"""Initialize WebSocket client."""
		self.base_url = base_url.replace("https://", "wss://").replace("http://", "ws://")
		self.message_handler = message_handler
		self.connections: Dict[str, websockets.WebSocketServerProtocol] = {}
		self.connection_tasks: Dict[str, asyncio.Task] = {}
		self._shutdown = False

	async def subscribe(self, uid: str) -> None:
		"""Subscribe to a switch's updates."""
		if uid in self.connections:
			_LOGGER.debug("Already connected to switch %s", uid)
			return

		_LOGGER.info("Subscribing to switch %s", uid)
		
		# Create connection task
		task = asyncio.create_task(self._maintain_connection(uid))
		self.connection_tasks[uid] = task

	async def unsubscribe(self, uid: str) -> None:
		"""Unsubscribe from a switch's updates."""
		_LOGGER.info("Unsubscribing from switch %s", uid)
		
		# Cancel connection task
		if uid in self.connection_tasks:
			task = self.connection_tasks.pop(uid)
			task.cancel()
			try:
				await task
			except asyncio.CancelledError:
				pass

		# Close WebSocket connection
		if uid in self.connections:
			connection = self.connections.pop(uid)
			try:
				await connection.close()
			except Exception as ex:
				_LOGGER.debug("Error closing WebSocket for %s: %s", uid, ex)

	async def _maintain_connection(self, uid: str) -> None:
		"""Maintain WebSocket connection for a switch."""
		while not self._shutdown:
			try:
				await self._connect_to_switch(uid)
			except asyncio.CancelledError:
				break
			except Exception as ex:
				_LOGGER.warning("WebSocket connection failed for %s: %s", uid, ex)
			
			if not self._shutdown:
				_LOGGER.debug("Reconnecting to switch %s in %s seconds", uid, WEBSOCKET_RECONNECT_DELAY)
				await asyncio.sleep(WEBSOCKET_RECONNECT_DELAY)

	async def _connect_to_switch(self, uid: str) -> None:
		"""Connect to a specific switch's WebSocket."""
		url = f"{self.base_url}/ws?uid={uid}"
		
		try:
			_LOGGER.debug("Connecting to WebSocket: %s", url)
			
			async with websockets.connect(
				url,
				ping_interval=30,
				ping_timeout=10,
				close_timeout=10
			) as websocket:
				self.connections[uid] = websocket
				_LOGGER.info("WebSocket connected for switch %s", uid)
				
				# Send subscription message
				await self._send_message(websocket, {
					"type": WS_MSG_SUBSCRIBE,
					"uid": uid
				})
				
				# Listen for messages
				async for message in websocket:
					if self._shutdown:
						break
					
					try:
						data = json.loads(message)
						await self._handle_message(uid, data)
					except json.JSONDecodeError as ex:
						_LOGGER.warning("Invalid JSON from WebSocket %s: %s", uid, ex)
					except Exception as ex:
						_LOGGER.error("Error handling WebSocket message for %s: %s", uid, ex)
				
		except ConnectionClosed as ex:
			_LOGGER.debug("WebSocket connection closed for %s: %s", uid, ex)
			raise
		except InvalidURI as ex:
			_LOGGER.error("Invalid WebSocket URI for %s: %s", uid, ex)
			raise
		except Exception as ex:
			_LOGGER.error("WebSocket error for %s: %s", uid, ex)
			raise
		finally:
			self.connections.pop(uid, None)

	async def _send_message(self, websocket: websockets.WebSocketServerProtocol, message: Dict[str, Any]) -> None:
		"""Send message to WebSocket."""
		try:
			await websocket.send(json.dumps(message))
		except Exception as ex:
			_LOGGER.error("Failed to send WebSocket message: %s", ex)

	async def _handle_message(self, uid: str, message: Dict[str, Any]) -> None:
		"""Handle incoming WebSocket message."""
		message_type = message.get("type")
		
		if message_type == WS_MSG_STATE_UPDATE:
			_LOGGER.debug("State update for %s: %s", uid, message.get("state"))
			await self._call_message_handler(uid, message)
			
		elif message_type == WS_MSG_ERROR:
			_LOGGER.warning("WebSocket error for %s: %s", uid, message.get("message"))
			await self._call_message_handler(uid, message)
			
		elif message_type == WS_MSG_PING:
			# Respond to ping
			websocket = self.connections.get(uid)
			if websocket:
				await self._send_message(websocket, {
					"type": WS_MSG_PONG,
					"timestamp": message.get("timestamp")
				})
				
		elif message_type == WS_MSG_PONG:
			_LOGGER.debug("Received pong from %s", uid)
			
		else:
			_LOGGER.debug("Unknown message type from %s: %s", uid, message_type)

	async def _call_message_handler(self, uid: str, message: Dict[str, Any]) -> None:
		"""Call the message handler safely."""
		try:
			if asyncio.iscoroutinefunction(self.message_handler):
				await self.message_handler(uid, message)
			else:
				self.message_handler(uid, message)
		except Exception as ex:
			_LOGGER.error("Error in message handler for %s: %s", uid, ex)

	async def disconnect(self) -> None:
		"""Disconnect all WebSocket connections."""
		_LOGGER.info("Disconnecting all WebSocket connections")
		self._shutdown = True
		
		# Cancel all connection tasks
		tasks = list(self.connection_tasks.values())
		self.connection_tasks.clear()
		
		if tasks:
			for task in tasks:
				task.cancel()
			
			# Wait for tasks to complete
			try:
				await asyncio.gather(*tasks, return_exceptions=True)
			except Exception as ex:
				_LOGGER.debug("Error waiting for connection tasks: %s", ex)
		
		# Close all connections
		connections = list(self.connections.values())
		self.connections.clear()
		
		if connections:
			close_tasks = [conn.close() for conn in connections]
			try:
				await asyncio.gather(*close_tasks, return_exceptions=True)
			except Exception as ex:
				_LOGGER.debug("Error closing WebSocket connections: %s", ex)

	def is_connected(self, uid: str) -> bool:
		"""Check if connected to a switch."""
		connection = self.connections.get(uid)
		return connection is not None and not connection.closed

	def get_connection_count(self) -> int:
		"""Get number of active connections."""
		return len([conn for conn in self.connections.values() if not conn.closed])
