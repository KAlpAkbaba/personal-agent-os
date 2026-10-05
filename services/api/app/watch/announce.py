"""watch-voice: the runner's announcer - a met condition spoken into the live session.

The runner (``app.watch.runner``) writes every change as a notification, a ledger row and a
reading the morning briefing reads (``changes_since``). Speaking is the nicety on top, and it
is narrow on purpose: only a ``condition_met`` (the owner set a line and it was crossed - "20
bin liranın altına indi"), only when the owner is present (``True``, never an unknown), a
greeting is allowed (the presence/greeting policy every unprompted sentence obeys) and it is
not quiet hours. Anything else is not spoken and the change waits for the briefing - which
reads the same rows, so nothing is lost by staying silent.

"Delivered" is what the speaker returned (``RealtimeSayBriefingSpeaker``: the one live
session, the same path the alarm's briefing uses); no live session is ``False``. A change is
spoken at most once per process (its watch and reading time are remembered).
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, time
from typing import Final, Protocol
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.logging import get_logger
from app.watch import compare
from app.watch.service import WatchChange

logger = get_logger("app.watch.announce")

#: The quiet hours when the owner set none (Europe/Istanbul): no unprompted speech at night.
DEFAULT_QUIET_START: Final = time(23, 0)
DEFAULT_QUIET_END: Final = time(7, 0)
DEFAULT_ZONE: Final = ZoneInfo("Europe/Istanbul")
#: How many spoken changes are remembered (a watch reads at most hourly; 20 watches).
_REMEMBERED: Final = 512


class Speaker(Protocol):
    def say(self, text: str, briefing_ids: Sequence[object] = ()) -> bool: ...


def default_quiet_hours(_db: Session, now: datetime) -> bool:
    """23:00-07:00 in Istanbul: the window used when the owner configured none."""
    aware = now if now.tzinfo is not None else now.replace(tzinfo=UTC)
    local = aware.astimezone(DEFAULT_ZONE).time()
    return local >= DEFAULT_QUIET_START or local < DEFAULT_QUIET_END


def sentence_for(change: WatchChange) -> str:
    line = change.line_tr.rstrip()
    return f"Nöbetten haber: {line if line.endswith(('.', '!', '?')) else line + '.'}"


class WatchAnnouncer:
    def __init__(
        self,
        speaker: Speaker,
        *,
        owner_present: Callable[[], bool | None],
        greeting_allowed: Callable[[Session], bool],
        quiet_hours: Callable[[Session, datetime], bool] = default_quiet_hours,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._speaker = speaker
        self._owner_present = owner_present
        self._greeting_allowed = greeting_allowed
        self._quiet_hours = quiet_hours
        self._clock = clock
        self._spoken: OrderedDict[tuple[str, str], None] = OrderedDict()

    def _key(self, change: WatchChange) -> tuple[str, str]:
        return change.watch_id, change.at.isoformat()

    def announce(self, db: Session, change: WatchChange) -> bool:
        """``True`` iff the change was spoken now; ``False`` leaves it to the briefing."""
        if change.outcome != compare.OUTCOME_CONDITION_MET:
            return False
        key = self._key(change)
        if key in self._spoken:
            return False
        if self._owner_present() is not True:
            return False
        if self._quiet_hours(db, self._clock()):
            return False
        if not self._greeting_allowed(db):
            return False
        if not self._speaker.say(sentence_for(change), ()):
            logger.info("watch_announce_not_delivered", watch_id=change.watch_id)
            return False
        self._spoken[key] = None
        while len(self._spoken) > _REMEMBERED:
            self._spoken.popitem(last=False)
        return True


__all__ = ["WatchAnnouncer", "default_quiet_hours", "sentence_for"]
