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
    # B40 (req 435-437): a fix scaffolds a new version and runs it on the device.
    "app.fix": TIER_SENSITIVE,
    # B41 (req 440-452): verify drives a browser, launch/modify scaffold and run; log,
    # history and resume read.
    "app.verify": TIER_SENSITIVE,
    "app.log": TIER_OPEN,
    "app.package": TIER_SENSITIVE,
    "app.launch": TIER_SENSITIVE,
    "app.history": TIER_OPEN,
    "app.resume": TIER_OPEN,
    "app.modify": TIER_SENSITIVE,
    "artifact.create": TIER_SENSITIVE,
    "artifact.open": TIER_SENSITIVE,
    "artifact.render": TIER_SENSITIVE,
    "calendar.commit": TIER_SENSITIVE,
    "calendar.discard": TIER_SENSITIVE,
    # B27 req 731: asks for a deletion - refused by policy today, and still the tier a
    # deletion request deserves, because the day B46 permits it the tier must already be
    # right.
    "calendar.cancel": TIER_SENSITIVE,
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
    # B43: generation and the photo fix make files; undo/redo move the pointer;
    # delivery writes the owner's disk; driving presses keys in a real application.
    "creative.generate": TIER_SENSITIVE,
    "creative.enhance": TIER_SENSITIVE,
    "creative.undo": TIER_SENSITIVE,
    "creative.redo": TIER_SENSITIVE,
    "creative.deliver": TIER_SENSITIVE,
    "creative.drive": TIER_SENSITIVE,
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
    # B32: reads of the owner's files are SENSITIVE like the rest of the family; sending
    # copies to the Recycle Bin changes the machine and is CRITICAL.
    "document.preview": TIER_SENSITIVE,
    "document.find_text": TIER_SENSITIVE,
    "document.duplicates": TIER_SENSITIVE,
    "document.dedup": TIER_CRITICAL,
    "document.summarize": TIER_SENSITIVE,
    # B34 req 166/674: a proposal reads the owner's files (SENSITIVE); what changes the
    # machine - applying a proposal, a direct write/append, undoing, deleting to the Recycle
    # Bin - is CRITICAL. Reading the journal and discarding a proposal change nothing.
    "document.write": TIER_CRITICAL,
    "document.append": TIER_CRITICAL,
    "document.edit": TIER_SENSITIVE,
    "document.rename": TIER_SENSITIVE,
    "document.move": TIER_SENSITIVE,
    "document.copy": TIER_SENSITIVE,
    "document.delete": TIER_SENSITIVE,
    "document.apply": TIER_CRITICAL,
    "document.discard": TIER_SENSITIVE,
    "document.undo": TIER_CRITICAL,
    "document.versions": TIER_SENSITIVE,
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
    # B45 (req 347, 348): listing reads the owner's mail; saving writes the owner's disk.
    "mail.attachments": TIER_SENSITIVE,
    "mail.save_attachment": TIER_SENSITIVE,
    # B16 req 31-38. Five of the six change what the system believes about its owner,
    # so five are SENSITIVE. `memory.forget` is the sharpest tool in this product's
    # voice surface: `app.memory` hard-deletes the row, its versions, its evidence and
    # its embeddings, and says so on purpose - there is no undo and no tombstone. It
    # takes an id read back from `memory.search` for the same reason.
    "memory.correct": TIER_SENSITIVE,
    "memory.forget": TIER_SENSITIVE,
    "memory.pin": TIER_SENSITIVE,
    "memory.remember": TIER_SENSITIVE,
    # B21 req 228. Teaching and forgetting a pronunciation CHANGE what the assistant will
    # say from now on - the table is part of the persona instruction (req 229) - so they
    # are governed like any other write. They are the mildest writes in this map: the cost
    # of a wrong one is a word said oddly until the owner corrects it, and there is no
    # deletion of anything the owner cannot re-teach in one sentence.
    "pronunciation.forget": TIER_SENSITIVE,
    "pronunciation.teach": TIER_SENSITIVE,
    # B21 req 414: starting a narration creates a durable session and points the
    # assistant's voice at a document. It reads what the owner already owns and changes
    # nothing in it, but it is a write (a session row) and it decides what gets said next.
    "narration.start": TIER_SENSITIVE,
    "media.play": TIER_SENSITIVE,
    "media.stop": TIER_SENSITIVE,
    # B27 req 733: moves a level on the owner's device - a state change, like stop.
    "media.volume": TIER_SENSITIVE,
    "native.build": TIER_SENSITIVE,
    "native.create": TIER_SENSITIVE,
    "native.fix": TIER_SENSITIVE,
    "native.install": TIER_SENSITIVE,
    "native.launch": TIER_SENSITIVE,
    "native.package": TIER_SENSITIVE,
    "native.rebuild": TIER_SENSITIVE,
    # B33 req 462-471: the lifecycle after the build - launch, verify, log, install,
    # update on the owner's device (SENSITIVE), and removing what was installed (CRITICAL).
    "native.verify": TIER_SENSITIVE,
    "native.log": TIER_SENSITIVE,
    "native.update": TIER_SENSITIVE,
    "native.uninstall": TIER_CRITICAL,
    # B35 (req 622/623): assigning the system work on itself is sensitive - a queued
    # defect spends the model budget and produces a branch; nothing is promoted.
    "selfdev.defect": TIER_SENSITIVE,
    "selfdev.feature": TIER_SENSITIVE,
    "selfdev.status": TIER_OPEN,
    "news.close": TIER_SENSITIVE,
    "news.open": TIER_SENSITIVE,
    "operator.app_open": TIER_SENSITIVE,
    "operator.cancel": TIER_SENSITIVE,
    "operator.type": TIER_SENSITIVE,
    "operator.window_control": TIER_SENSITIVE,
    # B27 req 735: reads whatever is on the owner's screen - the other half of the rule
    # that puts mail.read here. A voice that can capture the screen can see the screen.
    "operator.screenshot": TIER_SENSITIVE,
    # B28 req 92-98: keys and the pointer change the owner's desktop.
    "operator.key": TIER_SENSITIVE,
    "operator.pointer": TIER_SENSITIVE,
    # B29 req 99-105: UI Automation acts on the desktop; reading a tree or a screen reads
    # what is private (the other half of the mail.read rule).
    "operator.ui": TIER_SENSITIVE,
    "operator.inspect": TIER_SENSITIVE,
    "operator.see": TIER_SENSITIVE,
    # B30 req 82/119-122: closing an application and stopping a process change state;
    # a service restart reaches the machine level and is CRITICAL, like the shell.
    "operator.app_close": TIER_SENSITIVE,
    "operator.process": TIER_SENSITIVE,
    "operator.service": TIER_CRITICAL,
    # B39 req 127-130: a mission acts on the desktop across several steps.
    "operator.mission": TIER_SENSITIVE,
    "research.start": TIER_SENSITIVE,
    # B27 req 732: ends a running workflow; a cancelled research is not resumed.
    "research.cancel": TIER_SENSITIVE,
    # B31 req 203/204: pausing and resuming change what the machine is doing.
    "research.pause": TIER_SENSITIVE,
    "research.resume": TIER_SENSITIVE,
    # B14 req 287-291. Four of the five change state, so four are SENSITIVE. `routine.create`
    # in particular deserves it more than most tools here: it is the one that installs
    # something which will act on the owner's behalf UNATTENDED, every morning, until they
    # say otherwise - a mis-heard routine is not a mis-heard command, it is a mis-heard
    # command that repeats.
    "routine.cancel": TIER_SENSITIVE,
    "routine.create": TIER_SENSITIVE,
    "routine.pause": TIER_SENSITIVE,
    "routine.resume": TIER_SENSITIVE,
    # ADR-0196. Starting/ending/naming/cancelling a recording changes only what the
    # session keeps and a row the owner may delete again; the calls a recording keeps
    # each met this gate when they were made. RUN replays desktop actions and is
    # SENSITIVE like them - and every replayed step meets the gate AGAIN on its own
    # name (tools_macros.macro_run), so a macro is never a way around it. DELETE is
    # SENSITIVE the way routine.cancel is: it removes something the owner set up.
    "macro.record_start": TIER_SENSITIVE,
    "macro.record_end": TIER_SENSITIVE,
    "macro.name": TIER_SENSITIVE,
    "macro.cancel": TIER_SENSITIVE,
    "macro.run": TIER_SENSITIVE,
    "macro.delete": TIER_SENSITIVE,
    # ADR-0197: a tab in the owner's own browser, like media.play.
    "godseye.open": TIER_SENSITIVE,
    "scene.add": TIER_SENSITIVE,
    "scene.camera": TIER_SENSITIVE,
    "scene.create": TIER_SENSITIVE,
    "scene.light": TIER_SENSITIVE,
    "scene.material": TIER_SENSITIVE,
    "scene.render": TIER_SENSITIVE,
    # B44 (req 526, 527): motion and an export both run the editor on the owner's disk.
    "scene.animate": TIER_SENSITIVE,
    "scene.export": TIER_SENSITIVE,
    "scene.transform": TIER_SENSITIVE,
    # ---- OPEN: a question, a status, an explanation. Listed rather than defaulted.
    "activity.explain": TIER_OPEN,
    # B25 req 701: "Neler yapabilirsin?". It reads the tool registry — the manifest the
    # provider is already sent on every session — and touches no owner data, no device and
    # no provider. Gating the question "what can you do" behind a step-up would be gating
    # the one answer an owner needs before they can ask for anything else.
    "assistant.capabilities": TIER_OPEN,
    # ADR-0173 addendum: free conversation in the local mode. It answers and does NOTHING
    # else - no tools, no device, no durable write - so it sits with the other answer-only
    # tools. It shipped on 2026-09-19 without a tier at all, which
    # `test_voice_step_up::test_every_registered_tool_has_a_tier` caught here.
    "assistant.chat": TIER_OPEN,
    "alarm.status": TIER_OPEN,
    "ambient.explain": TIER_OPEN,
    "app.list": TIER_OPEN,
    "app.status": TIER_OPEN,
    "artifact.list": TIER_OPEN,
    "artifact.validate": TIER_OPEN,
    # B42 (req 410-416): edit/clone make files, delete removes them; compare reads.
    "artifact.edit": TIER_SENSITIVE,
    "artifact.clone": TIER_SENSITIVE,
    "artifact.delete": TIER_CRITICAL,
    "artifact.compare": TIER_OPEN,
    "briefing.morning": TIER_OPEN,
    # B16 req 32/61: a question about what is already remembered. Reading a memory back
    # changes nothing - but it is a USE, and both of these write a `memory.used` receipt.
    "memory.search": TIER_OPEN,
    "memory.why": TIER_OPEN,
    # Reading the table back changes nothing, and it is the step `pronunciation.forget`
    # depends on: an id read out loud, then named.
    "pronunciation.list": TIER_OPEN,
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
    # B31 req 201/209: opening a report by reference and setting the answer register.
    "research.open": TIER_OPEN,
    "research.answer_mode": TIER_OPEN,
    # Reading back what the owner already set up. OPEN for the same reason `alarm.status`
    # is: refusing to say what is scheduled protects nothing and teaches the owner that the
    # gate is noise.
    "routine.list": TIER_OPEN,
    "macro.list": TIER_OPEN,
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
    REASON_SCORE_BELOW_CRITICAL: ("Bu işlem için ses doğrulaması yeterince kesin değil efendim."),
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
