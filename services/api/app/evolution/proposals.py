"""What to do instead of saying "bunu yapamıyorum".

Owner directive, 2026-09-09: "bir şey sorduğumda 'bunu yapamıyorum' değil ... 'bunu feature
olarak ekleyeyim mi' olarak dönüp ... hayır yok gibi cevapları artık kabul etmeyeceğim."

Everything needed to honour that already existed and nothing reached it. ``GapDetector``
walks the owner's own resolution order — existing capability, composition, configuration,
extension, a vetted component, a new skill, and finally a product change it refuses to start
on its own — and ``GapRecorder`` writes the whole decision trail to ``capability_gaps``. The
Evolution Supervisor reads that table on every tick. But the only callers were
``POST /v1/evolution/gaps`` and the M24 genesis service, so a request the assistant could
not serve produced a sentence and nothing else: no row, no trail, no work item, and no way
for the owner to see it later except by saying it again.

This module is the missing caller. It turns one unmet request into the decision the tree
actually reached, a durable gap row carrying that decision, and a Turkish sentence that says
which of those two things happened — because "I have added it to the list" and "I cannot,
and here is why" are different answers and the owner is owed the true one.

What it deliberately does NOT do: decide anything itself. The resolution comes from the
detector, the row from the recorder, the sentence from the resolution. And it never claims a
feature will ship: a gap is a work item, the Evolution Engine's authority stops at
``shadow_ready`` (``backlog.LAB_FORBIDDEN_STATUSES``), and the owner authorises the release.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Final

from app.evolution.gaps import CapabilityRequest
from app.logging import get_logger

logger = get_logger("app.evolution.proposals")

#: The tree's own vocabulary for "something already here covers this". The assistant asked
#: because it believed it could not; when the tree disagrees, the owner hears that instead.
ALREADY_POSSIBLE: Final[frozenset[str]] = frozenset(
    {"existing_capability", "composition", "configuration", "extension"}
)

#: "A new skill can be built for this." The engine may start on it.
BUILDABLE: Final = "generation"

#: "This needs a change to the product itself." EVOLUTION_ENGINE_SPEC §12 / constitution §6:
#: the engine refuses to start, and the row is recorded ``abandoned``. It is still the most
#: valuable thing the owner can be told, because it is the queue THEY control.
PRODUCT_CHANGE: Final = "product_change_required"

SPEECH_ALREADY_POSSIBLE: Final = (
    "Bunu aslında yapabiliyorum efendim; bir daha deneyeyim, olmazsa yolunu düzelteceğim."
)
SPEECH_QUEUED: Final = (
    "Bunu şu an yapamıyorum efendim, ama geliştirme listeme aldım ve üzerinde çalışacağım."
)
SPEECH_PRODUCT_CHANGE: Final = (
    "Bunu yapabilmem için kendi çekirdeğimde bir değişiklik gerekiyor efendim; "
    "kendi başıma başlamam, iş kalemi olarak açtım ve onayınıza getireceğim."
)
SPEECH_NOT_RECORDED: Final = (
    "Bunu şu an yapamıyorum efendim ve listeye de alamadım; bunu not edin lütfen."
)

#: The capability id the rest of the engine will see. ``tokens.CAPABILITY_ID_RE`` is the
#: contract — ``^[a-z][a-z0-9]{0,31}(\.[a-z][a-z0-9_]{0,31}){1,4}$`` — a DOTTED id whose
#: first segment carries no underscore, and ``CapabilityRequest.parse`` refuses anything
#: else outright ("refusing to derive code"). An owner's spoken sentence is none of those
#: things, so it is folded into one: a fixed ``owner`` namespace and one identifier made
#: from their words. A test reads that regex from its own module and holds every id this
#: function can produce against it.
NAMESPACE: Final = "owner"
_NON_IDENT: Final = re.compile(r"[^a-z0-9]+")
_LEADING_NON_ALPHA: Final = re.compile(r"^[^a-z]+")
MAX_IDENT: Final = 32
MAX_REQUEST_TEXT: Final = 2000
FALLBACK_IDENT: Final = "request"

#: Turkish letters, which the id alphabet cannot hold. Only for the ID; the owner's own
#: sentence is stored verbatim in ``request_text``.
_TR_FOLD: Final = str.maketrans("çğıöşüÇĞİÖŞÜ", "cgiosuCGIOSU")


def capability_id(request_text: str) -> str:
    """A legal capability id for an owner sentence: "Ekranı böl" -> "owner.ekrani_bol"."""
    folded = request_text.translate(_TR_FOLD).lower()
    ident = _NON_IDENT.sub("_", folded).strip("_")
    ident = _LEADING_NON_ALPHA.sub("", ident)[:MAX_IDENT].rstrip("_")
    return f"{NAMESPACE}.{ident or FALLBACK_IDENT}"


@dataclass(frozen=True, slots=True)
class Proposal:
    """One unmet request, after the decision tree has had its say."""

    gap_id: str
    request_text: str
    resolution: str
    status: str
    speech: str

    @property
    def already_possible(self) -> bool:
        return self.resolution in ALREADY_POSSIBLE

    def as_dict(self) -> dict[str, Any]:
        return {
            "gap_id": self.gap_id,
            "request_text": self.request_text,
            "resolution": self.resolution,
            "status": self.status,
            "speech": self.speech,
        }


def speech_for(resolution: str) -> str:
    if resolution in ALREADY_POSSIBLE:
        return SPEECH_ALREADY_POSSIBLE
    if resolution == PRODUCT_CHANGE:
        return SPEECH_PRODUCT_CHANGE
    return SPEECH_QUEUED


def propose(
    detector: Any, recorder: Any, *, request_text: str, trace_id: str | None = None
) -> Proposal:
    """Record what the owner asked for and could not get. Returns what to say about it.

    ``detector``/``recorder`` are ``EvolutionRuntime.detector`` / ``.gaps`` — passed in
    rather than imported so a test drives the REAL ones against a temporary database and
    nothing here reaches for a global.
    """
    text = (request_text or "").strip()[:MAX_REQUEST_TEXT]
    if not text:
        raise ValueError("a proposal needs the owner's own words")
    request = CapabilityRequest.parse(
        {"requested_capability": capability_id(text), "request_text": text}
    )
    decision = detector.detect(request)
    gap = recorder.record(request, decision, trace_id=trace_id)
    logger.info(
        "owner_request_recorded_as_gap",
        gap_id=str(gap.get("gap_id") or gap.get("id") or ""),
        resolution=str(gap.get("resolution") or decision.resolution),
    )
    return Proposal(
        gap_id=str(gap.get("gap_id") or gap.get("id") or ""),
        request_text=text,
        resolution=str(gap.get("resolution") or decision.resolution),
        status=str(gap.get("status") or ""),
        speech=speech_for(str(gap.get("resolution") or decision.resolution)),
    )


__all__ = [
    "ALREADY_POSSIBLE",
    "BUILDABLE",
    "MAX_REQUEST_TEXT",
    "PRODUCT_CHANGE",
    "SPEECH_ALREADY_POSSIBLE",
    "SPEECH_NOT_RECORDED",
    "SPEECH_PRODUCT_CHANGE",
    "SPEECH_QUEUED",
    "Proposal",
    "NAMESPACE",
    "capability_id",
    "propose",
    "speech_for",
]
