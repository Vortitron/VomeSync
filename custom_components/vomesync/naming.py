"""Naming helpers for VomeSync."""
from urllib.parse import urlparse

from .const import DEVICE_MODEL_OWNED, DEVICE_MODEL_REMOTE


def _normalise_server_label(server_url: str) -> str:
	"""Return a concise host label for display."""
	if not server_url:
		return ""
	try:
		parsed = urlparse(server_url)
		host = parsed.netloc or parsed.path
	except Exception:  # noqa: BLE001
		host = server_url
	return str(host).strip().strip("/")


def build_entry_title(server_url: str) -> str:
	"""Build a default integration title using the server host."""
	host = _normalise_server_label(server_url)
	if host:
		return f"VomeSync ({host})"
	return "VomeSync"


def is_default_entry_title(title: str) -> bool:
	"""Return True when the title looks like an auto-generated default."""
	if not title:
		return True
	value = title.strip()
	if value == "VomeSync":
		return True
	if value.startswith("VomeSync (keypair "):
		return True
	return False


def format_device_name(name: str) -> str:
	"""Return a friendly device name without ownership markers."""
	switch_name = (name or "").strip()
	return switch_name if switch_name else "VomeSync Switch"


def format_device_model(is_owner: bool) -> str:
	"""Return the device model with ownership context."""
	return DEVICE_MODEL_OWNED if is_owner else DEVICE_MODEL_REMOTE

