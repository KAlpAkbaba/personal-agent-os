"""B05 req 245/247/248/664/666: what a voice utterance is allowed to DO.

The verdict was advisory in the most literal sense - computed by one route, returned to the
caller, read by nobody. The threshold was calculated and never consumed. So the rule the
product states ("voice identity alone is never root authentication") held only because
nothing had wired voice to anything privileged yet, which is a very different guarantee from
holding it on purpose.

This module is the decision path. It answers one question:

    may THIS action be taken, on the strength of what we currently know about who is
    speaking and where they are speaking from?

**The invariant, first, because everything else here is subordinate to it.** A voice match is
never sufficient by itself. On an untrusted device the classifier already caps the best
outcome at UNCERTAIN, and nothing in this module can lift that: there is no path here where
a score, however high, turns an untrusted device into an allowed sensitive action. Voice is
a factor. It is not a key.

**Three tiers, because "sensitive" is not one thing.**

* ``OPEN`` - reading and status. A question is not an action; no step-up.
* ``SENSITIVE`` - it changes something outside this system, or reads the owner's private
  content. Requires a trusted device AND a fresh OWNER verdict.
* ``CRITICAL`` - it is hard to undo or it is the authority boundary itself. Same, plus a
  higher score than the ordinary accept band and a shorter freshness window (req 248).

**Freshness is part of the answer.** A verdict from an hour ago says who was speaking an hour
ago. Every step-up decision carries the age it was willing to accept, and a verdict older
than that is treated as no verdict at all - the same discipline the world model's RUNTIME
truth follows, for the same reason.
"""

from __future__ import annotations

import dataclasses
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Final

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.logging import get_logger

logger = get_logger("app.security.step_up")

TIER_OPEN: Final[str] = "open"
TIER_SENSITIVE: Final[str] = "sensitive"
TIER_CRITICAL: Final[str] = "critical"

#: How recently the speaker must have been verified for the verdict to count. Shorter for
#: CRITICAL: the more the action costs to undo, the less old evidence is worth.
FRESH_FOR_SENSITIVE: Final[timedelta] = timedelta(minutes=15)
FRESH_FOR_CRITICAL: Final[timedelta] = timedelta(minutes=3)

#: req 248: the accept band a CRITICAL action needs, above the ordinary one. Not a second
#: opinion about the classifier - the same score, judged against a stricter bar.
CRITICAL_MIN_SCORE: Final[float] = 0.88

#: EVERY registered tool, classified. Not "anything absent is OPEN": a default is how a tool
#: added later ends up ungoverned without anyone deciding that it should be, which is the
#: exact shape of the defect this batch exists to close. `test_every_tool_has_a_tier` reads
#: the real registry and fails if this map and it disagree in either direction, so adding a
#: tool forces a decision about what it is allowed to do on a voice alone.
#:
#: The line between OPEN and SENSITIVE is: does it change something outside this system, or
#: read the owner's private content? Answering a question about state is neither.
_TIERS: Final[dict[str, str]] = {
    # ---- CRITICAL: hard to undo, reaches the outside world, or IS the authority boundary
    "eye.enable": TIER_CRITICAL,
    "mail.send": TIER_CRITICAL,
    "operator.shell": TIER_CRITICAL,
    "release.promote": TIER_CRITICAL,
    "release.rollback": TIER_CRITICAL,
    "capability.approve": TIER_CRITICAL,
    "evolution.control": TIER_CRITICAL,
    # ---- SENSITIVE: changes state, or reads what is private
    "alarm.cancel": TIER_SENSITIVE,
    "alarm.create": TIER_SENSITIVE,
    "alarm.snooze": TIER_SENSITIVE,
    "alarm.stop": TIER_SENSITIVE,
    "ambient.set_policy": TIER_SENSITIVE,
    "ambient.test_display": TIER_SENSITIVE,
    "app.create": TIER_SENSITIVE,
    "app.open": TIER_SENSITIVE,
    "app.run": TIER_SENSITIVE,
    "app.stop": TIER_SENSITIVE,
    "app.test": TIER_SENSITIVE,
    "artifact.create": TIER_SENSITIVE,
    "artifact.open": TIER_SENSITIVE,
    "artifact.render": TIER_SENSITIVE,
    "calendar.commit": TIER_SENSITIVE,
    "calendar.discard": TIER_SENSITIVE,
    "capability.cancel": TIER_SENSITIVE,
    "capability.propose": TIER_SENSITIVE,
    "capability.request": TIER_SENSITIVE,
    "creative.adjust": TIER_SENSITIVE,
    "creative.background": TIER_SENSITIVE,
    "creative.cleanup": TIER_SENSITIVE,
    "creative.design": TIER_SENSITIVE,
    "creative.export": TIER_SENSITIVE,
    "creative.open": TIER_SENSITIVE,
    "creative.redraw": TIER_SENSITIVE,
    "display.off": TIER_SENSITIVE,
    "display.wake": TIER_SENSITIVE,
    # Reading the owner's documents and mailbox is not a state change; it is the other half
    # of the rule. A voice that can read the owner's mail out loud is a voice with access to
    # the owner's mail.
    "document.answer": TIER_SENSITIVE,
    "document.common_points": TIER_SENSITIVE,
    "document.compare": TIER_SENSITIVE,
    "document.inspect": TIER_SENSITIVE,
    "document.previous": TIER_SENSITIVE,
    "document.read": TIER_SENSITIVE,
    "document.summarize": TIER_SENSITIVE,
    "executive.amend": TIER_SENSITIVE,
    "executive.cancel": TIER_SENSITIVE,
    "executive.pause": TIER_SENSITIVE,
    "executive.resume": TIER_SENSITIVE,
    "executive.retry": TIER_SENSITIVE,
    "executive.start": TIER_SENSITIVE,
    "eye.disable": TIER_SENSITIVE,
    "file.search": TIER_SENSITIVE,
    "location.set_default": TIER_SENSITIVE,
    "mail.discard": TIER_SENSITIVE,
    "mail.draft": TIER_SENSITIVE,
    "mail.edit_draft": TIER_SENSITIVE,
    "mail.inbox": TIER_SENSITIVE,
    "mail.read": TIER_SENSITIVE,
    "mail.read_draft": TIER_SENSITIVE,
    "mail.search": TIER_SENSITIVE,
    "mail.thread": TIER_SENSITIVE,
    "media.play": TIER_SENSITIVE,
    "media.stop": TIER_SENSITIVE,
    "native.build": TIER_SENSITIVE,
    "native.create": TIER_SENSITIVE,
    "native.fix": TIER_SENSITIVE,
    "native.install": TIER_SENSITIVE,
    "native.launch": TIER_SENSITIVE,
    "native.package": TIER_SENSITIVE,
    "native.rebuild": TIER_SENSITIVE,
    "news.close": TIER_SENSITIVE,
    "news.open": TIER_SENSITIVE,
    "operator.app_open": TIER_SENSITIVE,
    "operator.cancel": TIER_SENSITIVE,
    "operator.type": TIER_SENSITIVE,
    "operator.window_control": TIER_SENSITIVE,
    "research.start": TIER_SENSITIVE,
    # B14 req 287-291. Four of the five change state, so four are SENSITIVE. `routine.create`
    # in particular deserves it more than most tools here: it is the one that installs
    # something which will act on the owner's behalf UNATTENDED, every morning, until they
    # say otherwise - a mis-heard routine is not a mis-heard command, it is a mis-heard
    # command that repeats.
    "routine.cancel": TIER_SENSITIVE,
    "routine.create": TIER_SENSITIVE,
    "routine.pause": TIER_SENSITIVE,
    "routine.resume": TIER_SENSITIVE,
    "scene.add": TIER_SENSITIVE,
    "scene.camera": TIER_SENSITIVE,
    "scene.create": TIER_SENSITIVE,
    "scene.light": TIER_SENSITIVE,
    "scene.material": TIER_SENSITIVE,
    "scene.render": TIER_SENSITIVE,
    "scene.transform": TIER_SENSITIVE,
    # ---- OPEN: a question, a status, an explanation. Listed rather than defaulted.
    "activity.explain": TIER_OPEN,
    "alarm.status": TIER_OPEN,
    "ambient.explain": TIER_OPEN,
    "app.list": TIER_OPEN,
    "app.status": TIER_OPEN,
    "artifact.list": TIER_OPEN,
    "artifact.validate": TIER_OPEN,
    "briefing.morning": TIER_OPEN,
    "briefing.overnight_work": TIER_OPEN,
    "briefing.system_status": TIER_OPEN,
    "calendar.agenda": TIER_OPEN,
    "calendar.find_slot": TIER_OPEN,
    "calendar.propose": TIER_OPEN,
    "calendar.read_proposal": TIER_OPEN,
    "capability.status": TIER_OPEN,
    "clock.now": TIER_OPEN,
    "display.status": TIER_OPEN,
    "evolution.status": TIER_OPEN,
    "executive.explain": TIER_OPEN,
    "executive.status": TIER_OPEN,
    "location.get_default": TIER_OPEN,
    "narration.control": TIER_OPEN,
    "native.check": TIER_OPEN,
    "news.query_latest": TIER_OPEN,
    "news.summarize": TIER_OPEN,
    "operator.status": TIER_OPEN,
    "plan.redirect": TIER_OPEN,
    "research.explain": TIER_OPEN,
    # Reading back what the owner already set up. OPEN for the same reason `alarm.status`
    # is: refusing to say what is scheduled protects nothing and teaches the owner that the
    # gate is noise.
    "routine.list": TIER_OPEN,
    "research.finding_detail": TIER_OPEN,
    "research.sources": TIER_OPEN,
    "scene.inspect": TIER_OPEN,
    "state.now": TIER_OPEN,
    "voice.intent": TIER_OPEN,
    "weather.current": TIER_OPEN,
    "weather.last_evidence": TIER_OPEN,
}

REASON_NO_VERDICT: Final[str] = "no_speaker_verdict"
REASON_STALE_VERDICT: Final[str] = "speaker_verdict_stale"
REASON_NOT_OWNER: Final[str] = "speaker_not_owner"
REASON_UNTRUSTED_DEVICE: Final[str] = "device_not_trusted"
REASON_SCORE_BELOW_CRITICAL: Final[str] = "score_below_critical_bar"

#: What the owner hears. Turkish, and it says which of the two is missing so the next thing
#: they do is the thing that would fix it (tr-TR first-class, CLAUDE.md).
SPEECH_BY_REASON: Final[dict[str, str]] = {
    REASON_NO_VERDICT: "Bunu yapmadan önce sesinizi doğrulamam gerekiyor efendim.",
    REASON_STALE_VERDICT: "Ses doğrulaması eskidi efendim; tekrar doğrulamam gerekiyor.",
    REASON_NOT_OWNER: "Sesi tanıyamadım efendim; bu işlemi yapamam.",
    REASON_UNTRUSTED_DEVICE: (
        "Bu cihaz kayıtlı değil efendim; sesle tek başına bu işlemi yapamam."
    ),
    REASON_SCORE_BELOW_CRITICAL: (
        "Bu işlem için ses doğrulaması yeterince kesin değil efendim."
    ),
}


#: Mode, exactly as the device gate has one and for the same reason. Enforcing this today
#: would refuse every sensitive voice action, because the only thing that produces a verdict
#: is a REST endpoint nothing calls automatically - the realtime path has no probe flow yet.
#: So the decision path ships built, wired and tested, and OFF. Turning it on needs two
#: things the owner controls: the flow that produces verdicts, and the decision to enforce.
MODE_SHADOW: Final[str] = "shadow"
MODE_ENFORCE: Final[str] = "enforce"
MODES: Final[tuple[str, ...]] = (MODE_SHADOW, MODE_ENFORCE)

_MODE: str = MODE_SHADOW


def set_mode(mode: str) -> str:
    global _MODE
    if mode not in MODES:
        logger.warning("voice_step_up_unknown_mode", requested=mode, using=MODE_SHADOW)
        _MODE = MODE_SHADOW
    else:
        _MODE = mode
    return _MODE


def current_mode() -> str:
    return _MODE


def tier_of(tool_name: str) -> str:
    """The tier of a tool. An unknown name is SENSITIVE, not OPEN.

    Failing closed here costs nothing real - `handle_tool_call` refuses an unregistered tool
    before this is consulted, and the registry test keeps the map complete - but the default
    is the one line that decides what happens the day those two guarantees are both wrong at
    once, and "treat it as harmless" is the wrong answer to that.
    """
    return _TIERS.get(tool_name, TIER_SENSITIVE)


@dataclasses.dataclass(frozen=True, slots=True)
class StepUpDecision:
    #: The outcome AFTER the mode is applied. In shadow, a refused action still runs.
    allowed: bool
    #: The conclusion itself, independent of the mode - what shadow mode exists to record.
    would_refuse: bool
    tier: str
    reason: str | None
    tool: str
    mode: str = MODE_SHADOW
    #: The verdict this decision was reached on, for the audit. Never the probe.
    verdict: dict[str, Any] | None = None

    @property
    def speech(self) -> str | None:
        return SPEECH_BY_REASON.get(self.reason or "")

    def as_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "would_refuse": self.would_refuse,
            "tier": self.tier,
            "reason": self.reason,
            "tool": self.tool,
            "mode": self.mode,
            "verdict": self.verdict,
        }


def record_verdict(
    db: Session,
    *,
    owner_session_id: uuid.UUID,
    decision: str,
    score: float,
    device_trusted: bool,
    effective_accept: float,
    now: datetime | None = None,
) -> None:
    """Replace this session's verdict. One row per session, never a history."""
    from app.voice.models import SpeakerVerdictRow

    moment = now or datetime.now(UTC)
    row = db.execute(
        select(SpeakerVerdictRow).where(SpeakerVerdictRow.owner_session_id == owner_session_id)
    ).scalar_one_or_none()
    if row is None:
        row = SpeakerVerdictRow(owner_session_id=owner_session_id)
        db.add(row)
    row.decision = decision
    row.score = float(score)
    row.device_trusted = bool(device_trusted)
    row.effective_accept = float(effective_accept)
    row.verified_at = moment
    db.commit()


def _aware(value: datetime | None, fallback: datetime) -> datetime:
    if value is None:
        return fallback
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _refuse(tool: str, tier: str, reason: str, mode: str, verdict: dict[str, Any] | None = None):
    return StepUpDecision(
        allowed=(mode != MODE_ENFORCE),
        would_refuse=True,
        tier=tier,
        reason=reason,
        tool=tool,
        mode=mode,
        verdict=verdict,
    )


def evaluate(
    db: Session,
    *,
    tool: str,
    owner_session_id: uuid.UUID,
    device_trusted: bool,
    now: datetime | None = None,
    mode: str | None = None,
) -> StepUpDecision:
    """Decide whether this voice tool call may proceed. Never raises."""
    from app.voice.models import SpeakerVerdictRow
    from app.voice.speaker import SpeakerDecision

    mode = mode if mode is not None else _MODE
    if mode not in MODES:
        mode = MODE_SHADOW
    tier = tier_of(tool)
    if tier == TIER_OPEN:
        return StepUpDecision(
            allowed=True, would_refuse=False, tier=tier, reason=None, tool=tool, mode=mode
        )

    moment = now or datetime.now(UTC)

    # The invariant, checked before anything about the voice: an untrusted device is not
    # something a good score can argue its way past.
    if not device_trusted:
        return _refuse(tool, tier, REASON_UNTRUSTED_DEVICE, mode)

    row = db.execute(
        select(SpeakerVerdictRow).where(SpeakerVerdictRow.owner_session_id == owner_session_id)
    ).scalar_one_or_none()
    if row is None:
        return _refuse(tool, tier, REASON_NO_VERDICT, mode)

    verdict = {
        "decision": row.decision,
        "score": row.score,
        "device_trusted": row.device_trusted,
        "verified_at": _aware(row.verified_at, moment).isoformat(),
    }
    window = FRESH_FOR_CRITICAL if tier == TIER_CRITICAL else FRESH_FOR_SENSITIVE
    if moment - _aware(row.verified_at, moment) > window:
        return _refuse(tool, tier, REASON_STALE_VERDICT, mode, verdict)
    if row.decision != str(SpeakerDecision.OWNER):
        return _refuse(tool, tier, REASON_NOT_OWNER, mode, verdict)
    # A verdict reached on an UNTRUSTED device cannot be reused now that the device is
    # trusted: it was a measurement taken under conditions that capped it at UNCERTAIN.
    if not row.device_trusted:
        return _refuse(tool, tier, REASON_NO_VERDICT, mode, verdict)
    if tier == TIER_CRITICAL and row.score < CRITICAL_MIN_SCORE:
        return _refuse(tool, tier, REASON_SCORE_BELOW_CRITICAL, mode, verdict)
    return StepUpDecision(
        allowed=True,
        would_refuse=False,
        tier=tier,
        reason=None,
        tool=tool,
        mode=mode,
        verdict=verdict,
    )


def record(decision: StepUpDecision, *, owner_session_id: uuid.UUID) -> None:
    """Log every refusal reached, in either mode - the shadow line is the product of shadow
    mode, and without it turning enforcement on would be a guess."""
    if not decision.would_refuse:
        return
    logger.warning(
        "voice_step_up_required",
        tool=decision.tool,
        tier=decision.tier,
        reason=decision.reason,
        mode=decision.mode,
        blocked=not decision.allowed,
        owner_session_id=str(owner_session_id),
    )


__all__ = [
    "CRITICAL_MIN_SCORE",
    "MODES",
    "MODE_ENFORCE",
    "MODE_SHADOW",
    "current_mode",
    "set_mode",
    "FRESH_FOR_CRITICAL",
    "FRESH_FOR_SENSITIVE",
    "REASON_NOT_OWNER",
    "REASON_NO_VERDICT",
    "REASON_SCORE_BELOW_CRITICAL",
    "REASON_STALE_VERDICT",
    "REASON_UNTRUSTED_DEVICE",
    "SPEECH_BY_REASON",
    "TIER_CRITICAL",
    "TIER_OPEN",
    "TIER_SENSITIVE",
    "StepUpDecision",
    "evaluate",
    "record",
    "record_verdict",
    "tier_of",
]
