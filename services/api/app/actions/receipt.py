"""The action receipt (docs/M18_ACTION_CONTRACT.md §5.5, ADR-0063).

On 2026-09-06 the owner said "Gözünü kapat." The deterministic safety net really
disabled the eye, and the assistant - which had no tool for it - answered "öyle olmuş
gibi düşün": a spoken claim about a physical mutation, grounded in nothing. The rule this
module enforces is

    OWNER COMMAND -> normalise -> authorise -> execute -> wait for the terminal ACK
                  -> read back the resulting runtime state -> only then narrate

WRITE -> READ-BACK -> SPEAK, never UNDERSTAND -> ASSUME -> SPEAK. A handler builds an
:class:`ActionReceipt` from what it READ BACK after acting, the tool result IS the
receipt, and the model reads ``speech`` verbatim. No speech template here claims a
mutation unless ``terminal_status`` is ``verified`` (or ``already``); a test asserts none
of them contains a phrase from :data:`FAKE_COMPLETION_PHRASES`, and the persona forbids
those phrases by name.

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
FAKE_COMPLETION_PHRASES: Final[tuple[str, ...]] = (
    "yapmış gibi düşün",
    "olmuş gibi düşün",
    "gibi düşün",
    "sayabiliriz",
    "varsayalım",
    "oldu varsay",
)


def contains_fake_completion(text: str) -> bool:
    """True when ``text`` carries one of the banned phrases (Turkish-casefolded)."""
    folded = text.replace("İ", "i").replace("I", "ı").lower()
    return any(phrase in folded for phrase in FAKE_COMPLETION_PHRASES)


# ------------------------------------------------------------------ eye speech

#: Exact strings (contract §5.2). Keyed by (capability, terminal_status[, error_class]).
EYE_SPEECH_DISABLE_VERIFIED: Final = "Gözümü kapattım efendim."
EYE_SPEECH_DISABLE_ALREADY: Final = "Gözüm zaten kapalı efendim."
EYE_SPEECH_DISABLE_UNVERIFIED: Final = "Kamerayı kapatamadım; işlem doğrulanmadı."
EYE_SPEECH_ENABLE_VERIFIED: Final = "Gözümü açtım efendim."
EYE_SPEECH_ENABLE_ALREADY: Final = "Gözüm zaten açık efendim."
EYE_SPEECH_ENABLE_PERMISSION_DENIED: Final = "Kamerayı açamadım; tarayıcı kamera izni vermedi."
EYE_SPEECH_ENABLE_DEVICE_UNAVAILABLE: Final = "Kamerayı açamadım; kamera bulunamadı ya da meşgul."
EYE_SPEECH_ENABLE_UNVERIFIED: Final = "Kamerayı açamadım; işlem doğrulanmadı."

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
    EYE_SPEECH_ENABLE_VERIFIED,
    EYE_SPEECH_ENABLE_ALREADY,
    EYE_SPEECH_ENABLE_PERMISSION_DENIED,
    EYE_SPEECH_ENABLE_DEVICE_UNAVAILABLE,
    EYE_SPEECH_ENABLE_UNVERIFIED,
    RELEASE_PROMOTE_REFUSED_SPEECH,
)


def eye_speech(*, enable: bool, terminal_status: str, error_class: str | None) -> str:
    """The exact sentence for an eye action's outcome (contract §5.2).

    Everything that is not ``verified``/``already`` says the camera could NOT be
    changed - including ``unverified``, where the write may have happened but the
    read-back did not confirm it. Saying "I closed it" on an unverified write is the
    defect this module exists to prevent.
    """
    if enable:
        if terminal_status == TERMINAL_VERIFIED:
            return EYE_SPEECH_ENABLE_VERIFIED
        if terminal_status == TERMINAL_ALREADY:
            return EYE_SPEECH_ENABLE_ALREADY
        if terminal_status == TERMINAL_FAILED and error_class == "permission_denied":
            return EYE_SPEECH_ENABLE_PERMISSION_DENIED
        if terminal_status == TERMINAL_FAILED and error_class == "device_unavailable":
            return EYE_SPEECH_ENABLE_DEVICE_UNAVAILABLE
        return EYE_SPEECH_ENABLE_UNVERIFIED
    if terminal_status == TERMINAL_VERIFIED:
        return EYE_SPEECH_DISABLE_VERIFIED
    if terminal_status == TERMINAL_ALREADY:
        return EYE_SPEECH_DISABLE_ALREADY
    return EYE_SPEECH_DISABLE_UNVERIFIED


# ------------------------------------------------------------------ the receipt


def _iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class ActionReceipt:
    """What actually happened, read back after acting (contract §5.5).

    ``observed_after`` holds the server's read-back and the client's local report side
    by side, so a disagreement between them is visible in the record rather than
    resolved silently in favour of the one that flatters the assistant.
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

    def __post_init__(self) -> None:
        if self.execution_status not in EXECUTION_STATUSES:
            raise ValueError(f"unknown execution_status: {self.execution_status!r}")
        if self.terminal_status not in TERMINAL_STATUSES:
            raise ValueError(f"unknown terminal_status: {self.terminal_status!r}")
        if contains_fake_completion(self.speech):
            raise ValueError("receipt speech contains a banned fake-completion phrase")

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
    the receipt WITHOUT its speech. Idempotent on the action id.

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
    "EXECUTION_EXECUTED",
    "EXECUTION_FAILED",
    "EXECUTION_NOOP",
    "EXECUTION_REFUSED",
    "EXECUTION_STATUSES",
    "EYE_SPEECH_DISABLE_ALREADY",
    "EYE_SPEECH_DISABLE_UNVERIFIED",
    "EYE_SPEECH_DISABLE_VERIFIED",
    "EYE_SPEECH_ENABLE_ALREADY",
    "EYE_SPEECH_ENABLE_DEVICE_UNAVAILABLE",
    "EYE_SPEECH_ENABLE_PERMISSION_DENIED",
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
    "contains_fake_completion",
    "eye_speech",
    "record_receipt",
]
