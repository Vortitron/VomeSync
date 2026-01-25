"""Logging utilities with throttling support."""
import time
from typing import Callable, Dict


def should_log(throttle_state: Dict[str, float], key: str, min_interval_seconds: float) -> bool:
	"""Return True if enough time has passed to log this key again."""
	now = time.monotonic()
	last = throttle_state.get(key, 0.0)
	if now - last >= min_interval_seconds:
		throttle_state[key] = now
		return True
	return False


def log_throttled(
	log_func: Callable[..., None],
	throttle_state: Dict[str, float],
	key: str,
	min_interval_seconds: float,
	message: str,
	*args,
) -> None:
	"""Log only if the throttling interval has elapsed."""
	if should_log(throttle_state, key, min_interval_seconds):
		log_func(message, *args)

