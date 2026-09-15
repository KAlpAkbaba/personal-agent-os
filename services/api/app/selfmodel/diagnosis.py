"""B19 req 74/76/77/78/79: four questions the owner asks, answered from runtime.

The self-model can already answer nine questions about a MODULE — its status, its
problems, why it was written, what is running for a component, its last test failure,
whether it is ready. Every one of them needs the owner to name the module first. The four
questions this file answers are the ones asked about the SYSTEM, with nothing named:

* *Nerede takıldın?* — what is stuck right now, which is not the same question as "is
  anything wrong". `app.explain`'s `problems_now` answers from open incidents and critical
  events; a stuck task raises neither. It is a run that started and never finished, and it
  is invisible to every existing answer.
* *Son bug neydi?* — the last defect, with the evidence that says so.
* *Hangi özelliklerin çalışmıyor?* — from RUNTIME, and the requirement says so in as many
  words. Reading it out of the feature matrix would be answering a question about the
  running system from a document a person maintains by hand.
* *Sistem nasıl?* — health as a sentence rather than as eighteen JSON blocks.

**Nothing here is a second source of truth.** Every answer is assembled from a surface that
already owns its fact: the world model's stuck-task count, the self-healing incident store,
the health checks, and the self-model's own provenance. Where a fact is absent this says so
rather than inferring one — "I do not know" is an answer, and this repository has paid
several times for the version of a system that produces a confident sentence instead.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from app.logging import get_logger
from app.narration.numbers import cardinal

logger = get_logger("app.selfmodel.diagnosis")

#: A check whose `status` is anything but this is not working, whatever else it says.
STATUS_OK = "ok"

#: Health checks the product itself marks advisory: not working is not an outage, and
#: saying "iki özelliğim çalışmıyor" about two advisory checks would be crying wolf.
#: Read from `app.health` rather than restated, so the two cannot drift.


def utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass(slots=True)
class Finding:
    """One thing that is true right now, and where it was read."""

    what: str
    detail: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"what": self.what, "detail": self.detail, "evidence": dict(self.evidence)}


@dataclass(slots=True)
class Diagnosis:
    """The system's answer about itself. `speech` is what the owner hears."""

    speech: str = ""
    findings: list[Finding] = field(default_factory=list)
    #: True when the question was answered from something read, False when the answer is
    #: "nothing is recorded" - which is an answer and is not the same as "all is well".
    grounded: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {
            "speech": self.speech,
            "findings": [f.as_dict() for f in self.findings],
            "grounded": self.grounded,
        }


# ------------------------------------------------------------------ req 76: what is stuck


def stuck_now(world: dict[str, Any] | None, incidents: list[dict[str, Any]] | None) -> Diagnosis:
    """req 76. "Nerede takıldın?"

    A stuck task is one that started and never reached a terminal state - the world model
    has counted them since B06 (`STUCK_TASK_AFTER`) and nothing ever asked. It raises no
    incident and logs no error, which is exactly why the question needs its own answer: a
    system that is stuck looks, to every other surface, like a system that is idle.
    """
    world = world or {}
    incidents = incidents or []
    findings: list[Finding] = []

    stuck, known = _metric(world, "tasks.stuck_count")
    if stuck:
        findings.append(
            Finding(
                what=f"{cardinal(stuck)} görev takılı",
                detail="başladı ve bitmedi",
                evidence={"metric": "tasks.stuck_count", "value": stuck},
            )
        )
    blocked = [i for i in incidents if str(i.get("status") or "").lower() == "open"]
    if blocked:
        findings.append(
            Finding(
                what=f"{cardinal(len(blocked))} açık olay var",
                detail=str(blocked[0].get("title") or "")[:120],
                evidence={"incidents": [str(i.get("id") or "") for i in blocked[:5]]},
            )
        )

    if not findings:
        if not known:
            # No fresh observation of the one thing this answer is about. "Nothing is stuck"
            # would be a claim made from data nobody refreshed, which is the failure this
            # whole subsystem's staleness rules exist to prevent.
            return Diagnosis(
                speech=(
                    "Takılı bir şey var mı bilmiyorum efendim; görev gözlemim güncel değil."
                ),
                grounded=False,
            )
        return Diagnosis(speech="Şu anda takıldığım bir şey yok efendim.")
    return Diagnosis(
        speech="Efendim, " + "; ".join(f.what for f in findings) + ".",
        findings=findings,
    )


# --------------------------------------------------------------- req 77: the last defect


def last_defect(defects: list[dict[str, Any]] | None) -> Diagnosis:
    """req 77. "Son bug neydi?"

    Answered from the self-development engine's own records, and ONLY from them. There is
    no inference here from a failed test or an error log: "the last bug" is a claim about
    something that was diagnosed, and a system that answered it from the most recent
    exception it happened to see would be telling the owner about noise.
    """
    rows = sorted(
        defects or [],
        key=lambda d: str(d.get("created_at") or d.get("opened_at") or ""),
        reverse=True,
    )
    if not rows:
        return Diagnosis(
            speech="Kayıtlı bir hata çalışmam yok efendim.",
            grounded=False,
        )
    newest = rows[0]
    title = str(newest.get("title") or newest.get("summary") or "").strip()
    status = str(newest.get("status") or "").strip()
    tail = f" Durumu: {status}." if status else ""
    return Diagnosis(
        speech=f"Son ele aldığım hata: {title}.{tail}",
        findings=[
            Finding(
                what=title,
                detail=status,
                evidence={"defect_id": str(newest.get("id") or newest.get("defect_id") or "")},
            )
        ],
    )


# --------------------------------------------------- req 78: what is not working, from runtime


def not_working(
    checks: dict[str, Any] | None, *, advisory: frozenset[str] | None = None
) -> Diagnosis:
    """req 78. "Hangi özelliklerin çalışmıyor?" — from the health checks, not the matrix.

    The requirement says "çalışma zamanından" and it is worth saying why that word is in
    it: the feature matrix is a document a person maintains, and answering a question about
    the running system out of it would mean the answer is right exactly as often as the
    document is. The health checks are what the process can see about itself.

    An ADVISORY check that is down is reported separately rather than counted as a broken
    feature: `app.health` marks those (`required: False`) because the product does not
    depend on them, and "two of my features are not working" about two advisory checks
    would be crying wolf.
    """
    checks = checks or {}
    advisory = advisory or frozenset()
    broken: list[Finding] = []
    degraded: list[Finding] = []

    for name, block in sorted(checks.items()):
        if not isinstance(block, dict):
            continue
        status = str(block.get("status") or "").lower()
        if status == STATUS_OK:
            continue
        required = block.get("required")
        is_advisory = name in advisory or required is False
        finding = Finding(
            what=name,
            detail=str(block.get("error") or block.get("detail") or status)[:160],
            evidence={"check": name, "status": status, "required": bool(required is not False)},
        )
        (degraded if is_advisory else broken).append(finding)

    if not checks:
        return Diagnosis(speech="Sağlık ölçümüm yok efendim.", grounded=False)
    if not broken and not degraded:
        return Diagnosis(
            speech=f"Bütün bileşenler çalışıyor efendim ({cardinal(len(checks))} kontrol).",
            findings=[],
        )

    parts: list[str] = []
    if broken:
        parts.append("çalışmayan: " + ", ".join(f.what for f in broken))
    if degraded:
        parts.append("ikincil ve şu an kapalı: " + ", ".join(f.what for f in degraded))
    return Diagnosis(
        speech="Efendim, " + "; ".join(parts) + ".",
        findings=[*broken, *degraded],
    )


# ------------------------------------------------ req 74/79: health as a sentence, and a summary


def health_sentence(
    checks: dict[str, Any] | None, *, advisory: frozenset[str] | None = None
) -> str:
    """req 74. Eighteen JSON blocks, said in one sentence."""
    return not_working(checks, advisory=advisory).speech


def summary(
    *,
    checks: dict[str, Any] | None = None,
    world: dict[str, Any] | None = None,
    incidents: list[dict[str, Any]] | None = None,
    defects: list[dict[str, Any]] | None = None,
    runtime: dict[str, Any] | None = None,
    advisory: frozenset[str] | None = None,
) -> Diagnosis:
    """req 79. The whole self-diagnostic, in the order somebody listening needs it.

    What is broken first, what is stuck second, what I am running third. A summary that led
    with the build id would be a summary written for the person who built the system rather
    than for the person relying on it.
    """
    health = not_working(checks, advisory=advisory)
    stuck = stuck_now(world, incidents)
    defect = last_defect(defects)

    lines = [health.speech, stuck.speech]
    if runtime:
        version = str(runtime.get("version") or "").strip()
        build = str(runtime.get("build_id") or "").strip()
        if version or build:
            said = version or "sürüm bilinmiyor"
            lines.append(f"Çalışan sürüm {said}" + (f", yapı {build[:12]}." if build else "."))
    if defect.grounded:
        lines.append(defect.speech)

    return Diagnosis(
        speech=" ".join(line for line in lines if line),
        findings=[*health.findings, *stuck.findings, *defect.findings],
        grounded=health.grounded and stuck.grounded,
    )


def _metric(world: dict[str, Any], key: str) -> tuple[int, bool]:
    """One world-model fact as a number, or 0.

    The snapshot is `{"facts": [{"key", "value", "observed_at", "stale", ...}]}` - a list of
    observations each carrying its own evidence and staleness, never a flat dict of numbers.
    Read the way the world model actually publishes it rather than the way it would be
    convenient to; a `.get(key)` against the snapshot returns None for every metric there
    is and would have made this answer permanently "nothing is stuck".

    Returns ``(value, known)``. A STALE fact is `known=False`, and that distinction is the
    whole point: the first draft returned 0 for a stale observation, which makes the answer
    "nothing is stuck" - the reassuring sentence, produced from data nobody had refreshed.
    The reassuring answer is the one that has to be earned.
    """
    for fact in world.get("facts") or []:
        if not isinstance(fact, dict) or fact.get("key") != key:
            continue
        if fact.get("stale"):
            return 0, False
        try:
            return int(fact.get("value") or 0), True
        except (TypeError, ValueError):
            return 0, True
    return 0, False


__all__ = [
    "STATUS_OK",
    "Diagnosis",
    "Finding",
    "health_sentence",
    "last_defect",
    "not_working",
    "stuck_now",
    "summary",
]
