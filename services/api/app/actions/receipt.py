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
ACTION_CONTRACT_VERSION: Final = 2

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


def contains_fake_completion(text: str) -> bool:
    """True when ``text`` carries one of the banned phrases (Turkish-casefolded)."""
    folded = text.replace("İ", "i").replace("I", "ı").lower()
    return any(phrase in folded for phrase in FAKE_COMPLETION_PHRASES)


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
    "contains_fake_completion",
    "eye_speech",
    "record_receipt",
]
