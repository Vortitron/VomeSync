"""Reporting this home's own failed logins, so its owner can read them.

Home Assistant tells you about a failed login by writing a warning and raising
a persistent notification:

    Login attempt or request with invalid authentication from myhost (10.0.0.5).
    Requested URL: '/auth/token'. (Mozilla/5.0 …)

The address in it is whatever Core saw on the last hop, which for anything
arriving through Vome is a piece of our plumbing — loopback for a relay home,
the container host for a hosted one.  Vome's edge holds the visitor's real
address and reports it (webserver ``utils/accessEvents.js``), so between the
two an owner can tell a stranger from their own phone.

Between the two, and not from the edge alone: a device *inside the house* — an
old tablet with a stale token, a script with yesterday's password, a scanner on
the LAN — never touches Vome's edge at all.  Those attempts exist only in
Core's own log, which is exactly the case that leaves someone staring at an
unexplained ``10.x`` in a notification.  This module reports them so the answer
"that one came from inside your network" can actually be given.

**Why it reads the log.**  ``process_wrong_login`` fires no event
(``homeassistant/components/http/ban.py``) — it logs and notifies, and that is
all.  The one hook available is ``system_log_event``, which the ``system_log``
integration fires for every WARNING and above.  So this listens for that,
keeps only records from Core's own ban logger, and parses the sentence above.
Parsing a log line is a coupling to a message, so
``tests/test_login_watch.py`` pins it against the installed Home Assistant: a
release that rewords it fails the build rather than quietly reporting nothing.

Nothing here is on a request path, and nothing here can fail a login: the
listener catches everything, and a home whose relay is down simply reports
nothing until it comes back.
"""
from __future__ import annotations

import logging
import re
import time
from typing import Any, Optional

from homeassistant.core import Event, HomeAssistant, callback

_LOGGER = logging.getLogger(__name__)

# The event the ``system_log`` integration fires for every WARNING+ record.
EVENT_SYSTEM_LOG = "system_log_event"

# Only Core's own ban/auth logger is of interest; every other warning in a busy
# home is somebody else's business and none of the owner's access log.
BAN_LOGGER = "homeassistant.components.http.ban"

# The sentence built in ``process_wrong_login``.  Written to tolerate the parts
# that vary (a resolved hostname or a bare address, a missing user agent) and
# to fail into "we saw a failure but not its details" rather than into a
# traceback.
_FAILURE_RE = re.compile(
    r"invalid authentication from (?P<host>\S+) \((?P<ip>[^)]+)\)\."
    r"(?:\s*Requested URL: '(?P<url>[^']*)'\.)?"
    r"(?:\s*\((?P<agent>.*)\))?"
)
_BAN_RE = re.compile(r"Banned IP (?P<ip>\S+) for too many login attempts")

# Event names shared with the portal's vocabulary (portal/remote_access_log.py).
EVENT_LOGIN_FAILED = "login_failed"
EVENT_LOGIN_BLOCKED = "login_blocked"

# The home reports in batches on a timer of the relay's choosing; this caps how
# many are held while it is disconnected.  A flood is exactly when this matters
# and exactly when it must not become a memory problem, so the oldest go.
MAX_QUEUED = 200


def _text(message: Any) -> str:
    """A system_log record's message, which may be a list of lines."""
    if isinstance(message, (list, tuple)):
        return " ".join(str(part) for part in message)
    return str(message or "")


def parse_record(name: str, message: Any) -> Optional[dict]:
    """Turn one log record into an access event, or ``None`` if it is not one.

    Kept a plain function so the parsing can be tested against real Home
    Assistant log lines without a running hass.
    """
    if name != BAN_LOGGER:
        return None
    text = _text(message)
    match = _FAILURE_RE.search(text)
    if match:
        agent = match.group("agent")
        return {
            "event": EVENT_LOGIN_FAILED,
            "outcome": "denied",
            # The address as *Home Assistant* saw it.  For a request through
            # Vome that is our own last hop, which is the point: the same
            # failure reported by the edge carries the real one, and a failure
            # only reported here came from somewhere the edge never saw.
            "client_ip": match.group("ip"),
            "path": match.group("url") or None,
            "user_agent": agent or None,
            "detail": "Home Assistant rejected the credentials",
        }
    ban = _BAN_RE.search(text)
    if ban:
        return {
            "event": EVENT_LOGIN_BLOCKED,
            "outcome": "blocked",
            "client_ip": ban.group("ip"),
            "detail": "Home Assistant banned this address itself",
        }
    return None


class LoginWatcher:
    """Listens for Core's login failures and hands them to the relay.

    Owned by the relay client: it exists only while the home is linked, and
    stops with it.  ``send`` is the relay's fire-and-forget batch sender.
    """

    def __init__(self, hass: HomeAssistant, send) -> None:
        self._hass = hass
        self._send = send
        self._unsub = None
        self._queue: list[dict] = []

    def start(self) -> None:
        if self._unsub is not None:
            return
        self._unsub = self._hass.bus.async_listen(EVENT_SYSTEM_LOG, self._handle)
        _LOGGER.debug("Login watcher started")

    def stop(self) -> None:
        if self._unsub is not None:
            self._unsub()
            self._unsub = None
        self._queue.clear()

    @callback
    def _handle(self, event: Event) -> None:
        try:
            data = event.data or {}
            parsed = parse_record(data.get("name"), data.get("message"))
            if not parsed:
                return
            parsed["source"] = "home"
            parsed["at"] = int(data.get("timestamp") or time.time())
            # A repeated record arrives with Core's own occurrence count; carry
            # it so a burst reports as a burst rather than as a single line.
            count = data.get("count")
            parsed["count"] = int(count) if isinstance(count, int) and count > 0 else 1
            self._queue.append(parsed)
            if len(self._queue) > MAX_QUEUED:
                del self._queue[: len(self._queue) - MAX_QUEUED]
            self._hass.async_create_task(self._flush())
        except Exception:  # noqa: BLE001 - a log line must never break the bus
            _LOGGER.debug("Could not handle a system log record", exc_info=True)

    async def _flush(self) -> None:
        if not self._queue:
            return
        batch, self._queue = self._queue, []
        try:
            await self._send(batch)
        except Exception:  # noqa: BLE001 - dropped, never requeued into a wall
            _LOGGER.debug("Could not report login events to Vome", exc_info=True)
