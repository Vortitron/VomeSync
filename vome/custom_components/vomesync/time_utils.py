"""Time formatting helpers for VomeSync."""
from datetime import datetime, timezone


def format_timestamp_ms(value: object) -> str:
	"""Format epoch milliseconds to a local datetime string."""
	try:
		ts = int(value)
	except (TypeError, ValueError):
		return ""
	if ts <= 0:
		return ""
	dt = datetime.fromtimestamp(ts / 1000.0, tz=timezone.utc).astimezone()
	return dt.strftime("%Y-%m-%d %H:%M:%S %Z")

