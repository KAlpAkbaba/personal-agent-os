"""The action receipt (docs/M18_ACTION_CONTRACT.md §5.5, ADR-0063).

On 2026-09-06 the owner said "Gözünü kapat." A server-side utterance hook really disabled
the eye, and the assistant - which had no tool for it - answered "öyle olmuş gibi düşün":
a spoken claim about a physical mutation, grounded in nothing. Later the same day
(owner session 3eb6fee7) that hook closed the camera 7 ms after the tool call, with no
receipt of its own, while every receipt said ``failed``. The rule this module enforces is

    OWNER COMMAND -> normalise -> authorise -> execute -> wait for the terminal ACK
                  -> read back the resulting runtime state -> only then narrate

WRITE -> READ-BACK -> SPEAK, never UNDERSTAND -> ASSUME -> SPEAK. A handler builds an
:class:`ActionReceipt` from what it READ BACK after acting, the tool result IS the
receipt, and the model reads ``speech`` verbatim. No speech template here claims the
assistant DID a mutation unless ``terminal_status`` is ``verified`` (or ``already``); a
test asserts none of them contains a phrase from :data:`FAKE_COMPLETION_PHRASES`, and the
persona forbids those phrases by name.

Truthfulness cuts both ways. When the browser reports the camera physically closed but the
Cloud Core could not verify its own record, the receipt is ``unverified`` and the sentence
says exactly that ("Kamera kapandı ancak işlem kaydını doğrulayamadım.") - never
"kapatamadım", which would be a second false claim in the opposite direction.

The receipt is common to every future mutating capability (display-off, mail, files,
media, deployments adopt it when they are built; contract §9). Nothing here knows about
cameras: the eye speech table lives here only because the contract fixes its strings.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Final

from sqlalchemy.orm import Session

from app.ledger import service as ledger_service
from app.ledger.vocabulary import (
    EVENT_TYPE_ACTION_RECEIPT,
    SEVERITY_INFO,
    SEVERITY_NOTICE,
    SEVERITY_WARNING,
    STATUS_ALREADY,
    STATUS_FAILED,
    STATUS_UNVERIFIED,
    STATUS_VERIFIED,
)
from app.logging import get_logger

logger = get_logger("app.actions.receipt")

# ------------------------------------------------------------------ vocabulary

EXECUTION_EXECUTED: Final = "executed"  # a write happened in this command
EXECUTION_NOOP: Final = "noop"  # nothing to do: it was already so
EXECUTION_REFUSED: Final = "refused"  # policy said no; recorded all the same
EXECUTION_FAILED: Final = "failed"  # the capability could not do it
EXECUTION_STATUSES: Final[tuple[str, ...]] = (
    EXECUTION_EXECUTED,
    EXECUTION_NOOP,
    EXECUTION_REFUSED,
    EXECUTION_FAILED,
)

TERMINAL_VERIFIED: Final = STATUS_VERIFIED  # read-back matched AND the state changed now
TERMINAL_ALREADY: Final = STATUS_ALREADY  # nothing changed; it was already so before this turn
TERMINAL_UNVERIFIED: Final = STATUS_UNVERIFIED  # server and local disagree / read-back mismatch
TERMINAL_FAILED: Final = STATUS_FAILED  # the local capability reported an error, or refused
TERMINAL_STATUSES: Final[tuple[str, ...]] = (
    TERMINAL_VERIFIED,
    TERMINAL_ALREADY,
    TERMINAL_UNVERIFIED,
    TERMINAL_FAILED,
)

#: The only terminal statuses under which speech may claim the mutation happened.
TERMINAL_CLAIMABLE: Final[frozenset[str]] = frozenset({TERMINAL_VERIFIED, TERMINAL_ALREADY})

#: Banned in ANY assistant speech about a mutation (contract §1). One place; the persona
#: names them, and ``test_actions_receipt.py`` asserts no speech template contains one.
#: The action-contract version this Cloud Core runs, advertised on the health manifest
#: (voice_realtime.action_contract_version) so an owner qualification can tell a deployed
#: v1 (receipts without session_id / observed_at / the track read-back, a server-side
#: safety net, "kapatamadım" for a camera that had closed) from what this checkout needs.
#: Tool NAMES did not change between v1 and v2, so they cannot tell the two apart. Bump it
#: whenever the receipt shape, the terminal logic or the speech table changes.
#: v3 (2026-09-06, owner run 9df439af): the durable eye row carries the action_id and
#: session_id of the voice action that wrote it, so a ledger row correlates to its receipt
#: by identity rather than by a time window.
#: v4 (2026-09-07, M18.2 follow-up to ADR-0067): research.start starts the REAL M13
#: pipeline (task + device selection, synchronously, inside the tool call; the Temporal
#: workflow start is a ToolContext follow-up the route awaits after commit) instead of
#: fabricating a local plan the pipeline never heard about. Its terminal schema is new
#: (spoken_result/executive_summary/findings/source_summary/diagnostics, plus "speech"
#: mirroring spoken_result so the persona's "read speech verbatim" instruction and
#: session_activity's speech_head both work the same way every other tool's result
#: does); a research.start failure (no capable device, or a workflow that could not be
#: started) is an immediate or async-completed FAILED call with error_class and a
#: truthful Turkish speech - never a "running" call the pipeline can never finish, and
#: plan.redirect refuses a running research plan honestly rather than claiming a
#: redirect the workflow has no signal to receive.
#: v5 (2026-09-07, M18.2 owner run a4455670, ADR-0068): research.start's terminal
#: `diagnostics` gained the fast-path fields (`mode`, `budget_s`, `elapsed_s`,
#: `waves`, `challenged_pages`, `cooled_domains`) and the run itself now carries an
#: explicit speed mode (quick default / standard / deep, never chosen silently) —
#: a deployed v4 has no mode concept at all and always ran what v5 calls QUICK's
#: unbounded predecessor (fetch everything discovery found, up to max_sources, no
#: wave/time budget, no challenge/cooldown memory).
#: v6 (2026-09-07, M18.3, ADR-0071): a whole new family of mutating capabilities reaches
#: the owner by voice — alarm.create/cancel/snooze/stop, display.off/wake,
#: ambient.set_policy/test_display — and with it two receipt shapes this contract had not
#: needed before: a DEVICE REFUSAL that is a successful command (the companion answering
#: "no, the owner just touched the keyboard" is execution_status=refused with its own
#: sentence, never a failure), and a receipt whose observed_after.local is the DEVICE's own
#: read-back rather than a browser's. The wake sequence writes one receipt per physical
#: step (app.alarms.sequence.RECEIPT_BY_DEVICE_CALL enumerates them), so a qualification
#: run can prove every physical action from the ledger alone.
#: v7 (2026-09-07, M18.2 follow-up, ADR-0075): a research explanation never becomes a
#: second crawl. `research.start` gains a REFUSED terminal shape
#: ({"status": "refused", "reason": "research_followup_turn", research_job_id,
#: research_artifact_id, speech}) on a follow-up or technical-explanation turn bound to a
#: completed research, and `activity.explain` names the job and artifact it read
#: (`research_job_id` / `research_artifact_id`, also under provenance) - the identity an
#: owner qualification asserts instead of timestamps.
#: v8 (2026-09-07, M18.2 architectural fix, ADR-0076): a research is a thing the owner
#: POINTS AT. A durable, owner-level research focus survives voice reconnects and page
#: reloads; three follow-up tools (`research.explain`, `research.sources`,
#: `research.finding_detail`) execute against an explicitly resolved job id and take no
#: title and no id from the model; every research-bound tool result names
#: `research_job_id`, `research_artifact_id`, `resolution_reason` and `focus_source`; and
#: `research.start` is refused for any turn that POINTS at a run - including when no
#: research exists at all, where the answer is one question rather than a crawl. A
#: deployed v7 has no focus, no resolver and no such tools: it binds a follow-up to
#: whatever ran most recently and asks an unanswerable clarification when it cannot.
#: v9 (2026-09-07, M18.2 final narrow defect, ADR-0077): a tool result is a CONTRACT, not
#: a dict that happened to come back. A research-bound result (`research.explain`,
#: `research.sources`, `research.finding_detail`, and `activity.explain` on a turn the
#: canonical router recorded as being about a finished research) ends in exactly one of
#: three terminal statuses: `succeeded` ONLY with `research_job_id`,
#: `research_artifact_id` and a non-empty `speech`; `needs_clarification` (a new tool
#: status, migration 0023) with the one question to ask and no target; `failed` with
#: `error_class` and a truthful sentence. The relay enforces it: an "ok" with no target
#: or no words is recorded as failed (internal_bug), never as succeeded. `activity.explain`
#: on a research-bound turn answers from THAT research's report, bound by the turn's own
#: reference (never the model's paraphrase), attaches no narration session and names
#: `answered_by: research.explain` - one authoritative answer per turn whichever tool the
#: model chose. A deployed v8 records a clarification as a succeeded call with no target
#: and lets `activity.explain` narrate the ledger's telemetry instead of the report.
#: v10 (2026-09-07, M18.3 wiring defect, ADR-0078): the alarm/display voice tools
#: (`alarm.stop/snooze/cancel`, `display.off/wake/status`) now actually reach the device:
#: the realtime runtime's live sources carry the process's wake sequence and device-status
#: registry (`RealtimeVoiceRuntime.register_live`, called by create_app). A deployed v9
#: changes the alarm row on "Alarmı kapat." while the music keeps playing, and answers
#: every "Ekranları kapat." with "no device runtime" - the tools were built and tested
#: against injected fakes and never wired to the route.
ACTION_CONTRACT_VERSION: Final = 10

FAKE_COMPLETION_PHRASES: Final[tuple[str, ...]] = (
    "yapmış gibi düşün",
    "olmuş gibi düşün",
    "gibi düşün",
    "sayabiliriz",
    "varsayalım",
    "oldu varsay",
)

#: The bound on a client's ``action_trace`` (contract §5.1): how many steps, how long each.
ACTION_TRACE_MAX_STEPS: Final = 12
ACTION_TRACE_STEP_CHARS: Final = 80


#: docs/DECISIONS.md ADR-0076. The OTHER thing an owner must never hear instead of an
#: answer: the assistant narrating its own bookkeeping. On 2026-09-06 the owner asked
#: "Teknik anlat." and got "kayıtlarımı kontrol edeceğim" and "hangi kayda bakmam
#: gerektiğini bulmaya çalışıyorum" — six times, with no answer after any of them. A
#: follow-up about a finished research is answered from that research's own report, at
#: once; where the answer came from is not a sentence.
BOOKKEEPING_PHRASES: Final[tuple[str, ...]] = (
    "kayıtlarımı kontrol",
    "kayıtlara bakıyorum",
    "kayıtlara bakayım",
    "hangi kayda bak",
    "hangi kaydı",
    "bulmaya çalışıyorum",
    "kontrol etmem gerek",
    "biraz bakmam gerek",
)


def _fold(text: str) -> str:
    return text.replace("İ", "i").replace("I", "ı").lower()


def contains_fake_completion(text: str) -> bool:
    """True when ``text`` carries one of the banned phrases (Turkish-casefolded)."""
    return any(phrase in _fold(text) for phrase in FAKE_COMPLETION_PHRASES)


def contains_bookkeeping(text: str) -> bool:
    """True when ``text`` narrates the lookup instead of answering (ADR-0076)."""
    return any(phrase in _fold(text) for phrase in BOOKKEEPING_PHRASES)


# ------------------------------------------------------------------ client error classes

#: What the client's ``observed_after.local.error_class`` may say (contract §5.1). Each has
#: its own sentence below: when the reason is known it is said, never "işlem doğrulanmadı".
ERROR_PERMISSION_DENIED: Final = "permission_denied"
ERROR_DEVICE_NOT_FOUND: Final = "device_not_found"
ERROR_DEVICE_BUSY: Final = "device_busy"
ERROR_DEVICE_UNAVAILABLE: Final = "device_unavailable"  # legacy: not-found-or-busy
ERROR_GET_USER_MEDIA_FAILED: Final = "get_user_media_failed"
ERROR_STREAM_CREATED_BUT_TRACK_ENDED: Final = "stream_created_but_track_ended"
ERROR_PERCEPTION_START_FAILED: Final = "perception_start_failed"
ERROR_STATE_TRANSITION_FAILED: Final = "state_transition_failed"
ERROR_TIMEOUT: Final = "timeout"
ERROR_CAPABILITY_MISSING: Final = "capability_missing"
CLIENT_ERROR_CLASSES: Final[tuple[str, ...]] = (
    ERROR_PERMISSION_DENIED,
    ERROR_DEVICE_NOT_FOUND,
    ERROR_DEVICE_BUSY,
    ERROR_DEVICE_UNAVAILABLE,
    ERROR_GET_USER_MEDIA_FAILED,
    ERROR_STREAM_CREATED_BUT_TRACK_ENDED,
    ERROR_PERCEPTION_START_FAILED,
    ERROR_STATE_TRANSITION_FAILED,
    ERROR_TIMEOUT,
    ERROR_CAPABILITY_MISSING,
)

# ------------------------------------------------------------------ eye speech

#: Exact strings (contract §5.2). Keyed by (capability, terminal_status[, error_class]).
EYE_SPEECH_DISABLE_VERIFIED: Final = "Gözümü kapattım efendim."
EYE_SPEECH_DISABLE_ALREADY: Final = "Gözüm zaten kapalı efendim."
#: The camera is still running by the browser's own account, or nothing local confirmed it.
EYE_SPEECH_DISABLE_UNVERIFIED: Final = "Kamerayı kapatamadım; işlem doğrulanmadı."
#: The camera DID close (the browser says DISABLED); the Cloud Core's record did not confirm.
EYE_SPEECH_DISABLE_RECORD_UNVERIFIED: Final = "Kamera kapandı ancak işlem kaydını doğrulayamadım."
EYE_SPEECH_ENABLE_VERIFIED: Final = "Gözümü açtım efendim."
EYE_SPEECH_ENABLE_ALREADY: Final = "Gözüm zaten açık efendim."
EYE_SPEECH_ENABLE_PERMISSION_DENIED: Final = "Kamerayı açamadım; tarayıcı kamera izni vermedi."
EYE_SPEECH_ENABLE_DEVICE_NOT_FOUND: Final = "Kamerayı açamadım; kamera bulunamadı."
EYE_SPEECH_ENABLE_DEVICE_BUSY: Final = (
    "Kamerayı açamadım; kamera başka bir uygulama tarafından kullanılıyor."
)
EYE_SPEECH_ENABLE_DEVICE_UNAVAILABLE: Final = "Kamerayı açamadım; kamera bulunamadı ya da meşgul."
EYE_SPEECH_ENABLE_GET_USER_MEDIA_FAILED: Final = (
    "Kamerayı açamadım; tarayıcı kamera akışını başlatamadı."
)
EYE_SPEECH_ENABLE_TRACK_ENDED: Final = "Kamera açıldı ama görüntü akışı hemen kesildi."
EYE_SPEECH_ENABLE_PERCEPTION_START_FAILED: Final = (
    "Kamera açıldı ama algılama döngüsü başlatılamadı."
)
EYE_SPEECH_ENABLE_STATE_TRANSITION_FAILED: Final = "Kamerayı açamadım; durum geçişi tamamlanamadı."
#: timeout / capability_missing / the camera is not open by the browser's own account.
EYE_SPEECH_ENABLE_UNVERIFIED: Final = "Kamerayı açamadım; işlem doğrulanmadı."
#: The camera DID open (ACTIVE, track live); the Cloud Core's record did not confirm.
EYE_SPEECH_ENABLE_RECORD_UNVERIFIED: Final = "Kamera açıldı ancak işlem kaydını doğrulayamadım."

#: Enable failures the client can name, each with its own sentence (contract §5.2).
_ENABLE_FAILED_BY_ERROR_CLASS: Final[dict[str, str]] = {
    ERROR_PERMISSION_DENIED: EYE_SPEECH_ENABLE_PERMISSION_DENIED,
    ERROR_DEVICE_NOT_FOUND: EYE_SPEECH_ENABLE_DEVICE_NOT_FOUND,
    ERROR_DEVICE_BUSY: EYE_SPEECH_ENABLE_DEVICE_BUSY,
    ERROR_DEVICE_UNAVAILABLE: EYE_SPEECH_ENABLE_DEVICE_UNAVAILABLE,
    ERROR_GET_USER_MEDIA_FAILED: EYE_SPEECH_ENABLE_GET_USER_MEDIA_FAILED,
    ERROR_STREAM_CREATED_BUT_TRACK_ENDED: EYE_SPEECH_ENABLE_TRACK_ENDED,
    ERROR_PERCEPTION_START_FAILED: EYE_SPEECH_ENABLE_PERCEPTION_START_FAILED,
    ERROR_STATE_TRANSITION_FAILED: EYE_SPEECH_ENABLE_STATE_TRANSITION_FAILED,
}

#: Authority (contract §2): a voiced "canlıya al" is always refused with exactly this.
RELEASE_PROMOTE_REFUSED_SPEECH: Final = (
    "Canlıya alma kararı sizin efendim; onayı Core'daki Onay Merkezi'nden verirsiniz. "
    "Ben kendi başıma canlıya almam."
)

#: Every speech template this module can emit, so one test can sweep them all.
SPEECH_TEMPLATES: Final[tuple[str, ...]] = (
    EYE_SPEECH_DISABLE_VERIFIED,
    EYE_SPEECH_DISABLE_ALREADY,
    EYE_SPEECH_DISABLE_UNVERIFIED,
    EYE_SPEECH_DISABLE_RECORD_UNVERIFIED,
    EYE_SPEECH_ENABLE_VERIFIED,
    EYE_SPEECH_ENABLE_ALREADY,
    EYE_SPEECH_ENABLE_PERMISSION_DENIED,
    EYE_SPEECH_ENABLE_DEVICE_NOT_FOUND,
    EYE_SPEECH_ENABLE_DEVICE_BUSY,
    EYE_SPEECH_ENABLE_DEVICE_UNAVAILABLE,
    EYE_SPEECH_ENABLE_GET_USER_MEDIA_FAILED,
    EYE_SPEECH_ENABLE_TRACK_ENDED,
    EYE_SPEECH_ENABLE_PERCEPTION_START_FAILED,
    EYE_SPEECH_ENABLE_STATE_TRANSITION_FAILED,
    EYE_SPEECH_ENABLE_UNVERIFIED,
    EYE_SPEECH_ENABLE_RECORD_UNVERIFIED,
    RELEASE_PROMOTE_REFUSED_SPEECH,
)


def eye_speech(
    *,
    enable: bool,
    terminal_status: str,
    error_class: str | None,
    physical_ok: bool = False,
) -> str:
    """The exact sentence for an eye action's outcome (contract §5.2).

    ``physical_ok`` is the browser's own report that the camera is in the requested
    state (DISABLED after a disable; ACTIVE with a live track after an enable). It only
    matters below ``verified``: an ``unverified`` receipt with ``physical_ok`` says the
    camera changed and the RECORD could not be confirmed, because "kapatamadım" for a
    camera that is off would be as false as "kapattım" for one that is on.
    """
    if enable:
        if terminal_status == TERMINAL_VERIFIED:
            return EYE_SPEECH_ENABLE_VERIFIED
        if terminal_status == TERMINAL_ALREADY:
            return EYE_SPEECH_ENABLE_ALREADY
        if terminal_status == TERMINAL_FAILED and error_class in _ENABLE_FAILED_BY_ERROR_CLASS:
            return _ENABLE_FAILED_BY_ERROR_CLASS[error_class]
        if terminal_status == TERMINAL_UNVERIFIED and physical_ok:
            return EYE_SPEECH_ENABLE_RECORD_UNVERIFIED
        return EYE_SPEECH_ENABLE_UNVERIFIED
    if terminal_status == TERMINAL_VERIFIED:
        return EYE_SPEECH_DISABLE_VERIFIED
    if terminal_status == TERMINAL_ALREADY:
        return EYE_SPEECH_DISABLE_ALREADY
    if terminal_status == TERMINAL_UNVERIFIED and physical_ok:
        return EYE_SPEECH_DISABLE_RECORD_UNVERIFIED
    return EYE_SPEECH_DISABLE_UNVERIFIED


# ------------------------------------------------------------------ the receipt


def _iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


def bound_action_trace(raw: Any) -> list[str]:
    """The client's ``action_trace`` (contract §5.1), bounded: at most
    :data:`ACTION_TRACE_MAX_STEPS` short strings. Anything that is not a list of strings
    is an empty trace, never an error - the trace is evidence, not an input."""
    if not isinstance(raw, list | tuple):
        return []
    out: list[str] = []
    for step in raw:
        if isinstance(step, str) and step.strip():
            out.append(step.strip()[:ACTION_TRACE_STEP_CHARS])
        if len(out) >= ACTION_TRACE_MAX_STEPS:
            break
    return out


@dataclass(frozen=True)
class ActionReceipt:
    """What actually happened, read back after acting (contract §5.5).

    ``observed_after`` holds the server's read-back and the client's local report side
    by side, so a disagreement between them is visible in the record rather than
    resolved silently in favour of the one that flatters the assistant. ``session_id``
    is the realtime session the command came through and ``observed_at`` is when the
    read-back was taken, so a ledger query can correlate a receipt with a session and a
    harness can place it against the browser's own timeline.
    """

    action_id: str  # the tool call_id, or a uuid for non-voice actors
    capability: str  # "eye.disable"
    requested_state: str  # "disabled"
    execution_status: str  # executed | noop | refused | failed
    terminal_status: str  # verified | already | unverified | failed
    observed_after: dict[str, Any]  # {"server": {...}, "local": {...}}
    evidence_refs: list[dict[str, Any]] = field(default_factory=list)
    error_class: str | None = None
    speech: str = ""
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    session_id: str | None = None  # the realtime session (None for non-voice actors)
    observed_at: datetime | None = None  # when the read-back was taken
    action_trace: list[str] = field(default_factory=list)  # the client's steps, bounded

    def __post_init__(self) -> None:
        if self.execution_status not in EXECUTION_STATUSES:
            raise ValueError(f"unknown execution_status: {self.execution_status!r}")
        if self.terminal_status not in TERMINAL_STATUSES:
            raise ValueError(f"unknown terminal_status: {self.terminal_status!r}")
        if contains_fake_completion(self.speech):
            raise ValueError("receipt speech contains a banned fake-completion phrase")
        object.__setattr__(self, "action_trace", bound_action_trace(self.action_trace))

    @property
    def claimable(self) -> bool:
        """May speech say the mutation happened? Only on verified / already."""
        return self.terminal_status in TERMINAL_CLAIMABLE

    def as_dict(self, *, include_speech: bool = True) -> dict[str, Any]:
        out: dict[str, Any] = {
            "action_id": self.action_id,
            "capability": self.capability,
            "requested_state": self.requested_state,
            "execution_status": self.execution_status,
            "terminal_status": self.terminal_status,
            "observed_after": {k: dict(v) for k, v in self.observed_after.items()},
            "evidence_refs": [dict(r) for r in self.evidence_refs],
            "error_class": self.error_class,
            "started_at": _iso(self.started_at),
            "completed_at": _iso(self.completed_at),
            "session_id": self.session_id,
            "observed_at": _iso(self.observed_at) if self.observed_at is not None else None,
            "action_trace": list(self.action_trace),
        }
        if include_speech:
            out["speech"] = self.speech
        return out


def _severity_for(receipt: ActionReceipt) -> str:
    if receipt.terminal_status not in (TERMINAL_FAILED, TERMINAL_UNVERIFIED):
        return SEVERITY_INFO
    if receipt.execution_status == EXECUTION_REFUSED:
        return SEVERITY_NOTICE
    return SEVERITY_WARNING


def record_receipt(db: Session, receipt: ActionReceipt, subsystem: str) -> Any | None:
    """Write the ``action.receipt`` ledger row (contract §5.5): subsystem = the
    capability's, action = the capability, status = the terminal status, detail =
    the receipt WITHOUT its speech (so ``detail_json.session_id`` and
    ``detail_json.observed_at`` are queryable). Idempotent on the action id.

    Best-effort like every other ledger note: the receipt is evidence of the action,
    never a dependency of it - the owner still hears the grounded answer if the ledger
    is down, and the failure is logged where an operator will see it.
    """
    try:
        return ledger_service.record(
            db,
            ledger_service.ActivityEvent(
                event_type=EVENT_TYPE_ACTION_RECEIPT,
                subsystem=subsystem,
                action=receipt.capability,
                status=receipt.terminal_status,
                severity=_severity_for(receipt),
                factual_summary=(
                    f"{receipt.capability} -> {receipt.requested_state}: "
                    f"{receipt.execution_status}, {receipt.terminal_status}"
                    + (f" ({receipt.error_class})" if receipt.error_class else "")
                ),
                occurred_at=receipt.completed_at,
                evidence_refs=[dict(r) for r in receipt.evidence_refs],
                detail_json=receipt.as_dict(include_speech=False),
                source="live",
                source_ref=f"action_receipt:{receipt.capability}:{receipt.action_id}",
            ),
        )
    except Exception as exc:  # noqa: BLE001 - see docstring
        logger.warning(
            "action_receipt_ledger_failed",
            capability=receipt.capability,
            action_id=receipt.action_id,
            error=f"{type(exc).__name__}: {exc}",
        )
        return None


__all__ = [
    "ACTION_TRACE_MAX_STEPS",
    "ACTION_TRACE_STEP_CHARS",
    "BOOKKEEPING_PHRASES",
    "CLIENT_ERROR_CLASSES",
    "ERROR_CAPABILITY_MISSING",
    "ERROR_DEVICE_BUSY",
    "ERROR_DEVICE_NOT_FOUND",
    "ERROR_DEVICE_UNAVAILABLE",
    "ERROR_GET_USER_MEDIA_FAILED",
    "ERROR_PERCEPTION_START_FAILED",
    "ERROR_PERMISSION_DENIED",
    "ERROR_STATE_TRANSITION_FAILED",
    "ERROR_STREAM_CREATED_BUT_TRACK_ENDED",
    "ERROR_TIMEOUT",
    "EXECUTION_EXECUTED",
    "EXECUTION_FAILED",
    "EXECUTION_NOOP",
    "EXECUTION_REFUSED",
    "EXECUTION_STATUSES",
    "EYE_SPEECH_DISABLE_ALREADY",
    "EYE_SPEECH_DISABLE_RECORD_UNVERIFIED",
    "EYE_SPEECH_DISABLE_UNVERIFIED",
    "EYE_SPEECH_DISABLE_VERIFIED",
    "EYE_SPEECH_ENABLE_ALREADY",
    "EYE_SPEECH_ENABLE_DEVICE_BUSY",
    "EYE_SPEECH_ENABLE_DEVICE_NOT_FOUND",
    "EYE_SPEECH_ENABLE_DEVICE_UNAVAILABLE",
    "EYE_SPEECH_ENABLE_GET_USER_MEDIA_FAILED",
    "EYE_SPEECH_ENABLE_PERCEPTION_START_FAILED",
    "EYE_SPEECH_ENABLE_PERMISSION_DENIED",
    "EYE_SPEECH_ENABLE_RECORD_UNVERIFIED",
    "EYE_SPEECH_ENABLE_STATE_TRANSITION_FAILED",
    "EYE_SPEECH_ENABLE_TRACK_ENDED",
    "EYE_SPEECH_ENABLE_UNVERIFIED",
    "EYE_SPEECH_ENABLE_VERIFIED",
    "FAKE_COMPLETION_PHRASES",
    "RELEASE_PROMOTE_REFUSED_SPEECH",
    "SPEECH_TEMPLATES",
    "TERMINAL_ALREADY",
    "TERMINAL_CLAIMABLE",
    "TERMINAL_FAILED",
    "TERMINAL_STATUSES",
    "TERMINAL_UNVERIFIED",
    "TERMINAL_VERIFIED",
    "ActionReceipt",
    "bound_action_trace",
    "contains_bookkeeping",
    "contains_fake_completion",
    "eye_speech",
    "record_receipt",
]
