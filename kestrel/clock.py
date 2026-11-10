"""The one place the agent learns what time it is.

Every time-dependent rule (return window, Night Owl window) asks a Clock, so a
reviewer can pin the time with --now or KESTREL_NOW and nothing else changes.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

EASTERN = ZoneInfo("America/New_York")


def parse_now(text: str) -> datetime:
    """Parse '2026-12-01 21:30' or any ISO 8601 timestamp into Eastern time.

    Text without a UTC offset is read as Eastern wall-clock time.
    """
    moment = datetime.fromisoformat(text.strip())
    if moment.tzinfo is None:
        return moment.replace(tzinfo=EASTERN)
    return moment.astimezone(EASTERN)


class Clock:
    """Real time by default; frozen at `fixed` when one is given."""

    def __init__(self, fixed: datetime | None = None):
        self._fixed = fixed

    def now(self) -> datetime:
        return self._fixed or datetime.now(EASTERN)
