"""The question-shaped read API over the self-model index.

This is the layer ``app.explain`` will call to answer the owner's Turkish
questions. It returns FACTS, never wording:

======================================  ================================
"Diagnostic Observer ne durumda?"       :func:`module_status`
"Diagnostic Observer'da sorun ne?"      :func:`module_problems`
"Bu modul neden boyle yazildi?"         :func:`why_written`
"Hangi surum gercekten calisiyor?"      :func:`what_is_running`
"Son test neden basarisiz oldu?"        :func:`last_test_failure`
"Bu kod canlida mi?"                    :func:`module_status` -> ``is_live``
"Canliya alinmaya hazir mi?"            :func:`ready_for_production`
======================================  ================================

Two invariants hold across every function here.

**Nothing is ever guessed.** Each answer carries ``evidence_refs`` (what it is
based on), ``confidence`` (how much that is worth) and ``unknown`` (what it
could not establish). ``is_live`` is a tri-state: ``True`` from a runtime
provenance row, ``False`` when the module is known and no such row exists, and
the answer says which. There is no code path that infers "running" from the
fact that the source is present.

**A name the owner speaks is resolved, not assumed.** ``"Diagnostic Observer"``
normalizes to ``diagnostic_observer`` and matches a module leaf; when nothing
matches, the answer is an explicit not-found carrying candidates, never the
nearest module.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Final

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.selfmodel.indexer import COMPONENT_MODULE_HINTS, has_table
from app.selfmodel.models import (
    EDGE_DOCUMENTED_BY,
    EDGE_RELEASED_AS,
    EDGE_TESTS,
    PRODUCTION_STATE_RUNNING,
    TRUTH_EVIDENCE,
    TRUTH_INSTALLED,
    TRUTH_RUNTIME,
    TRUTH_SOURCE,
    CodeEdge,
    CodeModule,
    CodeSymbol,
    ModuleProvenance,
)

#: Incident statuses that still count as a live problem.
OPEN_INCIDENT_STATUSES: Final[tuple[str, ...]] = ("open", "fix_in_progress")
#: Ledger event types that mean "a test run failed".
TEST_FAILURE_EVENT_TYPES: Final[tuple[str, ...]] = ("evolution.tests_failed",)
#: Gates ``ready_for_production`` insists on seeing evidence for.
REQUIRED_GATES: Final[tuple[str, ...]] = (
    "tests_passed",
    "security_review_passed",
    "shadow_ready",
)
MAX_CANDIDATES: Final[int] = 10
MAX_ROWS: Final[int] = 200

_NON_KEY_RE: Final[re.Pattern[str]] = re.compile(r"[^a-z0-9_./]+")
_COLLAPSE_RE: Final[re.Pattern[str]] = re.compile(r"_{2,}")
#: Turkish letters folded to the ASCII a developer would have typed in an identifier.
#: Applied BEFORE lowercasing, so "İzleyici" folds to "Izleyici" -> "izleyici" rather than
#: an i plus a combining dot that the character filter would then delete.
_TURKISH_FOLD: Final[dict[int, str]] = str.maketrans(
    {
        "ç": "c",
        "Ç": "C",
        "ğ": "g",
        "Ğ": "G",
        "ı": "i",
        "İ": "I",
        "ö": "o",
        "Ö": "O",
        "ş": "s",
        "Ş": "S",
        "ü": "u",
        "Ü": "U",
        "â": "a",
        "Â": "A",
        "î": "i",
        "Î": "I",
        "û": "u",
        "Û": "U",
    }
)


# ------------------------------------------------------------- resolution


def normalize_key(raw: str) -> str:
    """``"Diagnostic Observer"`` -> ``"diagnostic_observer"``.

    Spoken Turkish arrives with spaces, capitals and the odd possessive suffix
    ("Diagnostic Observer'da"); paths arrive with slashes and a ``.py``. Both
    collapse to the same shape a module id uses.
    """
    text = (raw or "").strip()
    # Turkish is a first-class product language and the owner says module names the
    # Turkish way ("Günlük Servisi"). Fold the diacritics to the ASCII a developer must
    # already have typed in the identifier; DELETING them produced "gnlk_servisi", which
    # matched nothing and offered no candidates either (test review, 2026-09-05).
    text = text.translate(_TURKISH_FOLD).lower()
    text = text.split("'")[0] if "'" in text else text
    text = text.replace("\\", "/").replace("-", "_").replace(" ", "_")
    text = _NON_KEY_RE.sub("", text)
    text = _COLLAPSE_RE.sub("_", text)
    if text.endswith(".py"):
        text = text[:-3]
    return text.strip("._/")


@dataclass(frozen=True, slots=True)
class Resolution:
    """The outcome of turning an owner-spoken name into a module id."""

    query: str
    normalized: str
    module_id: str | None = None
    matched_by: str = "not_found"
    confidence: float = 0.0
    candidates: tuple[str, ...] = ()

    @property
    def found(self) -> bool:
        return self.module_id is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "normalized": self.normalized,
            "module_id": self.module_id,
            "matched_by": self.matched_by,
            "confidence": self.confidence,
            "candidates": list(self.candidates),
            "found": self.found,
        }


def _leaf(module_id: str) -> str:
    return module_id.replace("/", ".").rsplit(".", 1)[-1]


def resolve_module(session: Session, key: str) -> Resolution:
    """Exact id, then normalized id, then leaf name, then suffix, then substring.

    Each tier is strictly weaker than the last and says so through
    ``matched_by``/``confidence``. Ambiguity at a tier is NOT broken by picking
    the first hit -- it returns the candidates instead, because "I found four
    things called observer" is a true answer and "app.a.observer" might not be.
    """
    normalized = normalize_key(key)
    ids = list(session.scalars(select(CodeModule.module_id)))
    result = Resolution(query=key, normalized=normalized)
    if not normalized:
        return result

    if key in ids:
        return Resolution(key, normalized, key, "exact", 1.0)
    dotted = normalized.replace("/", ".")
    for candidate in (normalized, dotted):
        if candidate in ids:
            return Resolution(key, normalized, candidate, "normalized", 0.95)

    # A spoken two-word name is as likely to be a dotted path ("Ledger Service"
    # -> app.ledger.service) as a single underscored leaf ("Diagnostic Observer"
    # -> diagnostic_observer), so both readings are tried, leaf first.
    spoken_path = dotted.replace("_", ".")
    tiers = (
        ("leaf", [m for m in ids if _leaf(m) == dotted]),
        ("suffix", [m for m in ids if m.replace("/", ".").endswith(f".{dotted}")]),
        ("spoken_path", [m for m in ids if m.replace("/", ".").endswith(f".{spoken_path}")]),
        ("substring", [m for m in ids if dotted in m.replace("/", ".")]),
    )
    confidences = {"leaf": 0.85, "suffix": 0.75, "spoken_path": 0.7, "substring": 0.5}
    for matched_by, hits in tiers:
        if len(hits) == 1:
            return Resolution(key, normalized, hits[0], matched_by, confidences[matched_by])
        if len(hits) > 1:
            return Resolution(
                key,
                normalized,
                None,
                "ambiguous",
                0.0,
                tuple(sorted(hits)[:MAX_CANDIDATES]),
            )
    return Resolution(key, normalized, None, "not_found", 0.0, _candidates(ids, dotted))


def _candidates(ids: list[str], needle: str) -> tuple[str, ...]:
    """Best-effort suggestions for a not-found query -- shared leading token
    first, then anything sharing a word, so the owner gets something to pick
    from rather than a bare failure."""
    if not needle:
        return ()
    tokens = {t for t in needle.replace("/", ".").split(".") if t}
    scored: list[tuple[int, str]] = []
    for module_id in ids:
        parts = set(module_id.replace("/", ".").split("."))
        overlap = len(tokens & parts)
        if overlap:
            scored.append((-overlap, module_id))
    scored.sort()
    return tuple(module_id for _, module_id in scored[:MAX_CANDIDATES])


# ---------------------------------------------------------------- helpers


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _truths(session: Session, module_id: str) -> dict[str, dict[str, Any]]:
    """The four truths as four separate entries. Absent means absent -- a
    missing ``runtime`` key is the answer to "is this live?", not a gap to fill
    from the ``source`` row."""
    out: dict[str, dict[str, Any]] = {}
    rows = session.scalars(
        select(ModuleProvenance).where(ModuleProvenance.module_id == module_id)
    ).all()
    for row in rows:
        out[row.truth_kind] = {
            "truth_kind": row.truth_kind,
            "version": row.version,
            "digest": row.digest,
            "observed_at": _iso(row.observed_at),
            "evidence_refs": list(row.evidence_refs or []),
            "confidence": row.confidence,
            "stale": bool(row.stale),
        }
    return out


def _components_for(session: Session, module_id: str) -> list[str]:
    """Release/incident component names that could refer to this module."""
    row = session.get(CodeModule, module_id)
    names = {module_id, _leaf(module_id)}
    if row is not None and row.owner_area:
        names.add(row.owner_area)
    for component, hints in COMPONENT_MODULE_HINTS.items():
        if module_id in hints:
            names.add(component)
    return sorted(n for n in names if n)


def _incidents(session: Session, module_id: str, *, open_only: bool) -> list[dict[str, Any]]:
    from app.selfhealing.models import Incident

    if not has_table(session, Incident.__tablename__):
        return []
    components = _components_for(session, module_id)
    if not components:
        return []
    stmt = select(Incident).where(Incident.component.in_(components))
    if open_only:
        stmt = stmt.where(Incident.status.in_(OPEN_INCIDENT_STATUSES))
    rows = session.scalars(stmt.order_by(Incident.last_seen_at.desc()).limit(MAX_ROWS)).all()
    return [
        {
            "incident_id": str(row.id),
            "component": row.component,
            "severity": row.severity,
            "status": row.status,
            "fingerprint": row.fingerprint,
            "occurrence_count": row.occurrence_count,
            "first_seen_at": _iso(row.first_seen_at),
            "last_seen_at": _iso(row.last_seen_at),
            "evidence_refs": [{"kind": "incident", "ref": str(row.id)}],
        }
        for row in rows
    ]


def _test_modules(session: Session, module_id: str) -> list[str]:
    rows = session.scalars(
        select(CodeEdge.from_module).where(
            CodeEdge.to_module == module_id, CodeEdge.kind == EDGE_TESTS
        )
    ).all()
    return sorted(rows)


def _ledger_events(
    session: Session,
    *,
    module_ids: list[str],
    event_types: tuple[str, ...] | None = None,
    statuses: tuple[str, ...] | None = None,
    limit: int = 20,
) -> list[Any]:
    from app.ledger.models import ActivityEventRow

    if not has_table(session, ActivityEventRow.__tablename__) or not module_ids:
        return []
    stmt = select(ActivityEventRow).where(
        (ActivityEventRow.related_module_id.in_(module_ids))
        | (ActivityEventRow.module.in_(module_ids))
    )
    if event_types:
        stmt = stmt.where(ActivityEventRow.event_type.in_(event_types))
    if statuses:
        stmt = stmt.where(ActivityEventRow.status.in_(statuses))
    return list(
        session.scalars(stmt.order_by(ActivityEventRow.occurred_at.desc()).limit(limit)).all()
    )


def _event_dict(row: Any) -> dict[str, Any]:
    return {
        "event_id": str(row.event_id),
        "occurred_at": _iso(row.occurred_at),
        "event_type": row.event_type,
        "subsystem": row.subsystem,
        "status": row.status,
        "severity": row.severity,
        "module": row.module,
        "related_module_id": row.related_module_id,
        "version": row.version,
        "result": row.result,
        "factual_summary": row.factual_summary,
        "evidence_refs": [
            {"kind": "activity_event", "ref": str(row.event_id)},
            *[r for r in (row.evidence_refs or []) if isinstance(r, dict)],
        ],
    }


def _passed_gates(truths: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    evidence = truths.get(TRUTH_EVIDENCE)
    if not evidence:
        return {}
    gates: dict[str, dict[str, Any]] = {}
    for ref in evidence.get("evidence_refs") or []:
        if isinstance(ref, dict) and ref.get("gate"):
            gates[str(ref["gate"])] = ref
    return gates


# ----------------------------------------------------------------- answers


@dataclass(slots=True)
class Answer:
    """Base shape every self-model answer shares."""

    question: str
    resolution: Resolution
    confidence: float = 0.0
    unknown: list[str] = field(default_factory=list)
    evidence_refs: list[dict[str, Any]] = field(default_factory=list)
    facts: dict[str, Any] = field(default_factory=dict)

    @property
    def found(self) -> bool:
        return self.resolution.found

    def to_dict(self) -> dict[str, Any]:
        return {
            "question": self.question,
            "found": self.found,
            "resolution": self.resolution.to_dict(),
            "confidence": self.confidence,
            "unknown": list(self.unknown),
            "evidence_refs": list(self.evidence_refs),
            **self.facts,
        }


def _not_found(question: str, resolution: Resolution) -> Answer:
    """The honest failure: what was asked, what it normalized to, and what the
    index does have that is close."""
    reason = (
        "module_id_ambiguous" if resolution.matched_by == "ambiguous" else "module_id_not_found"
    )
    return Answer(
        question=question,
        resolution=resolution,
        confidence=0.0,
        unknown=[reason],
        facts={"candidates": list(resolution.candidates), "reason": reason},
    )


def module_status(session: Session, module_key: str) -> Answer:
    """What this module is, its four truths, whether it is live, and what
    surrounds it (tests, incidents, release)."""
    resolution = resolve_module(session, module_key)
    if not resolution.found:
        return _not_found("module_status", resolution)
    module_id = resolution.module_id or ""
    row = session.get(CodeModule, module_id)
    if row is None:  # pragma: no cover - resolution read the same table
        return _not_found("module_status", resolution)

    truths = _truths(session, module_id)
    runtime = truths.get(TRUTH_RUNTIME)
    installed = truths.get(TRUTH_INSTALLED)
    unknown: list[str] = []

    # Tri-state, never inferred from source presence.
    if runtime is not None:
        is_live: bool | None = True
    elif installed is not None:
        is_live = False
        unknown.append("installed_but_no_runtime_report")
    else:
        is_live = False
        unknown.append("no_runtime_evidence")
    # Runtime is already spoken for by the tri-state above; repeating it as
    # "no_runtime_truth" would give a consumer two entries meaning one thing.
    for missing in (TRUTH_INSTALLED, TRUTH_EVIDENCE):
        if missing not in truths:
            unknown.append(f"no_{missing}_truth")

    symbols = session.scalars(
        select(CodeSymbol).where(CodeSymbol.module_id == module_id).order_by(CodeSymbol.lineno)
    ).all()
    kind_counts: dict[str, int] = {}
    for symbol in symbols:
        kind_counts[symbol.kind] = kind_counts.get(symbol.kind, 0) + 1

    releases = session.scalars(
        select(CodeEdge).where(CodeEdge.from_module == module_id, CodeEdge.kind == EDGE_RELEASED_AS)
    ).all()
    incidents = _incidents(session, module_id, open_only=False)
    tests = _test_modules(session, module_id)

    evidence_refs: list[dict[str, Any]] = [{"kind": "code_module", "ref": module_id}]
    for truth in truths.values():
        evidence_refs.extend(r for r in truth["evidence_refs"] if isinstance(r, dict))

    # Confidence is the resolution's, capped by how much truth actually exists:
    # a source-only module can never be a high-confidence status answer.
    truth_weight = 1.0 if runtime else (0.8 if installed else 0.6)
    return Answer(
        question="module_status",
        resolution=resolution,
        confidence=round(resolution.confidence * truth_weight, 3),
        unknown=unknown,
        evidence_refs=evidence_refs,
        facts={
            "module_id": module_id,
            "display_name": (row.detail_json or {}).get("display_name") or _leaf(module_id),
            "kind": row.kind,
            "path": row.path,
            "language": row.language,
            "purpose": row.purpose,
            "owner_area": row.owner_area,
            "production_state": row.production_state,
            "is_live": is_live,
            "truths": truths,
            "symbol_counts": kind_counts,
            "symbols": [
                {
                    "name": s.name,
                    "kind": s.kind,
                    "signature": s.signature,
                    "lineno": s.lineno,
                    "docstring_summary": s.docstring_summary,
                }
                for s in symbols[:50]
            ],
            "tests": tests,
            "incidents": incidents,
            "releases": [{"ref": edge.to_module, **(edge.detail_json or {})} for edge in releases],
            "adr_refs": list(row.adr_refs or []),
            "spec_refs": list(row.spec_refs or []),
        },
    )


def module_problems(session: Session, module_key: str) -> Answer:
    """Open incidents, failed test runs, and the index's own known limitations
    for this module."""
    resolution = resolve_module(session, module_key)
    if not resolution.found:
        return _not_found("module_problems", resolution)
    module_id = resolution.module_id or ""
    row = session.get(CodeModule, module_id)

    open_incidents = _incidents(session, module_id, open_only=True)
    scope = [module_id, *_test_modules(session, module_id)]
    failures = [
        _event_dict(e)
        for e in _ledger_events(
            session, module_ids=scope, event_types=TEST_FAILURE_EVENT_TYPES, limit=20
        )
    ]
    failures.extend(
        _event_dict(e)
        for e in _ledger_events(session, module_ids=scope, statuses=("failed",), limit=20)
        if e.event_type not in TEST_FAILURE_EVENT_TYPES
    )

    truths = _truths(session, module_id)
    limitations: list[str] = []
    detail = (row.detail_json or {}) if row is not None else {}
    if detail.get("note") == "too_large":
        limitations.append("file_exceeds_index_size_cap")
    elif detail.get("note"):
        limitations.append(str(detail["note"]))
    if row is not None and row.language != "python":
        limitations.append("foreign_language_indexed_by_path_only")
    if not _test_modules(session, module_id):
        limitations.append("no_linked_tests")
    if TRUTH_RUNTIME not in truths:
        limitations.append("no_runtime_evidence")

    evidence_refs: list[dict[str, Any]] = []
    for incident in open_incidents:
        evidence_refs.extend(incident["evidence_refs"])
    for failure in failures:
        evidence_refs.extend(failure["evidence_refs"])

    unknown: list[str] = []
    if not open_incidents and not failures:
        unknown.append("no_recorded_problem_rows")

    return Answer(
        question="module_problems",
        resolution=resolution,
        confidence=round(resolution.confidence * (1.0 if evidence_refs else 0.5), 3),
        unknown=unknown,
        evidence_refs=evidence_refs,
        facts={
            "module_id": module_id,
            "open_incidents": open_incidents,
            "failed_tests": failures,
            "known_limitations": limitations,
            "has_problems": bool(open_incidents or failures),
        },
    )


def why_written(session: Session, module_key: str) -> Answer:
    """The recorded rationale: ADR refs, spec refs and the module's own first
    docstring line. Never a reconstruction -- a module nobody documented gets
    ``no_recorded_rationale``, not a plausible story."""
    resolution = resolve_module(session, module_key)
    if not resolution.found:
        return _not_found("why_written", resolution)
    module_id = resolution.module_id or ""
    row = session.get(CodeModule, module_id)
    if row is None:  # pragma: no cover
        return _not_found("why_written", resolution)

    doc_edges = session.scalars(
        select(CodeEdge).where(
            CodeEdge.from_module == module_id, CodeEdge.kind == EDGE_DOCUMENTED_BY
        )
    ).all()
    adr_refs = list(row.adr_refs or [])
    spec_refs = list(row.spec_refs or [])
    evidence_refs = [{"kind": "document", "ref": edge.to_module} for edge in doc_edges]
    if row.purpose:
        evidence_refs.append({"kind": "docstring", "ref": row.path})

    unknown: list[str] = []
    if not adr_refs and not spec_refs:
        unknown.append("no_recorded_rationale")
    if not row.purpose:
        unknown.append("no_module_docstring")

    confidence = 0.9 if adr_refs else (0.7 if spec_refs else (0.4 if row.purpose else 0.0))
    return Answer(
        question="why_written",
        resolution=resolution,
        confidence=round(resolution.confidence * confidence, 3),
        unknown=unknown,
        evidence_refs=evidence_refs,
        facts={
            "module_id": module_id,
            "purpose": row.purpose,
            "adr_refs": adr_refs,
            "spec_refs": spec_refs,
            "documents": [edge.to_module for edge in doc_edges],
        },
    )


def what_is_running(session: Session, component: str) -> Answer:
    """Runtime truth with its evidence -- or an honest "nothing reported back".

    ``component`` may be a release component ("cloud_core") or a module key.
    A promoted release is reported as INSTALLED, and it is never upgraded to
    "running" here: that distinction is the entire point of the four truths.
    """
    resolution = resolve_module(session, component)
    module_ids: list[str] = []
    if resolution.found and resolution.module_id:
        module_ids = [resolution.module_id]
    else:
        for hint in COMPONENT_MODULE_HINTS.get(normalize_key(component), ()):
            if session.get(CodeModule, hint) is not None:
                module_ids.append(hint)
        if module_ids:
            resolution = Resolution(
                component, normalize_key(component), module_ids[0], "component_hint", 0.8
            )
    if not module_ids:
        return _not_found("what_is_running", resolution)

    entries: list[dict[str, Any]] = []
    evidence_refs: list[dict[str, Any]] = []
    unknown: list[str] = []
    any_runtime = False
    for module_id in module_ids:
        truths = _truths(session, module_id)
        runtime = truths.get(TRUTH_RUNTIME)
        installed = truths.get(TRUTH_INSTALLED)
        if runtime:
            any_runtime = True
            evidence_refs.extend(r for r in runtime["evidence_refs"] if isinstance(r, dict))
        else:
            unknown.append(f"no_runtime_evidence:{module_id}")
        if installed:
            evidence_refs.extend(r for r in installed["evidence_refs"] if isinstance(r, dict))
        entries.append(
            {
                "module_id": module_id,
                "runtime": runtime,
                "installed": installed,
                "source": truths.get(TRUTH_SOURCE),
                "is_live": runtime is not None,
            }
        )

    return Answer(
        question="what_is_running",
        resolution=resolution,
        confidence=round(resolution.confidence * (0.95 if any_runtime else 0.3), 3),
        unknown=unknown,
        evidence_refs=evidence_refs,
        facts={
            "component": component,
            "modules": entries,
            "runtime_known": any_runtime,
            "answer": "running" if any_runtime else "no_runtime_evidence",
        },
    )


def last_test_failure(session: Session, module_key: str) -> Answer:
    """The most recent recorded failing test run for this module or its tests."""
    resolution = resolve_module(session, module_key)
    if not resolution.found:
        return _not_found("last_test_failure", resolution)
    module_id = resolution.module_id or ""
    scope = [module_id, *_test_modules(session, module_id)]

    events = _ledger_events(
        session, module_ids=scope, event_types=TEST_FAILURE_EVENT_TYPES, limit=1
    )
    if not events:
        events = _ledger_events(session, module_ids=scope, statuses=("failed",), limit=1)
    if not events:
        return Answer(
            question="last_test_failure",
            resolution=resolution,
            confidence=round(resolution.confidence * 0.5, 3),
            unknown=["no_recorded_test_failure"],
            facts={"module_id": module_id, "failure": None, "tests": scope[1:]},
        )

    failure = _event_dict(events[0])
    return Answer(
        question="last_test_failure",
        resolution=resolution,
        confidence=round(resolution.confidence * 0.95, 3),
        unknown=[],
        evidence_refs=failure["evidence_refs"],
        facts={"module_id": module_id, "failure": failure, "tests": scope[1:]},
    )


def ready_for_production(session: Session, module_key: str) -> Answer:
    """Whether the recorded gates say this module may be promoted.

    ``ready`` is True only when every gate in :data:`REQUIRED_GATES` has an
    evidence row AND no incident is open. With no evidence row at all the
    answer is False with ``no_gate_evidence`` -- silence is never consent.
    """
    resolution = resolve_module(session, module_key)
    if not resolution.found:
        return _not_found("ready_for_production", resolution)
    module_id = resolution.module_id or ""
    row = session.get(CodeModule, module_id)

    truths = _truths(session, module_id)
    gates = _passed_gates(truths)
    open_incidents = _incidents(session, module_id, open_only=True)

    missing = [gate for gate in REQUIRED_GATES if gate not in gates]
    blockers: list[str] = list(missing)
    if open_incidents:
        blockers.append("open_incidents")

    unknown: list[str] = []
    if TRUTH_EVIDENCE not in truths:
        unknown.append("no_gate_evidence")

    ready = not blockers
    evidence_refs: list[dict[str, Any]] = [dict(ref) for ref in gates.values()]
    for incident in open_incidents:
        evidence_refs.extend(incident["evidence_refs"])

    # A "yes" is only ever as strong as the evidence behind it; a "no" backed by
    # a concrete missing gate is a confident answer in its own right.
    confidence = 0.9 if (ready and gates) else (0.8 if blockers and gates else 0.5)
    return Answer(
        question="ready_for_production",
        resolution=resolution,
        confidence=round(resolution.confidence * confidence, 3),
        unknown=unknown,
        evidence_refs=evidence_refs,
        facts={
            "module_id": module_id,
            "ready": ready,
            "production_state": row.production_state if row is not None else None,
            "in_shadow_or_lab": (
                row.production_state in ("shadow", "lab") if row is not None else False
            ),
            "gates_passed": sorted(gates),
            "gates_required": list(REQUIRED_GATES),
            "missing": blockers,
            "open_incident_count": len(open_incidents),
            "already_running": (
                row.production_state == PRODUCTION_STATE_RUNNING if row is not None else False
            ),
        },
    )


def search(session: Session, q: str, *, limit: int = 25) -> dict[str, Any]:
    """Substring search across module ids, purposes and symbol names."""
    needle = normalize_key(q)
    if not needle:
        return {"query": q, "normalized": needle, "modules": [], "symbols": []}
    like = f"%{needle.replace('.', '%')}%"
    modules = session.scalars(
        select(CodeModule).where(CodeModule.module_id.ilike(like)).limit(limit)
    ).all()
    symbols = session.scalars(
        select(CodeSymbol).where(CodeSymbol.name.ilike(f"%{needle}%")).limit(limit)
    ).all()
    return {
        "query": q,
        "normalized": needle,
        "modules": [
            {
                "module_id": m.module_id,
                "kind": m.kind,
                "path": m.path,
                "purpose": m.purpose,
                "production_state": m.production_state,
            }
            for m in modules
        ],
        "symbols": [
            {
                "symbol_id": s.symbol_id,
                "module_id": s.module_id,
                "name": s.name,
                "kind": s.kind,
                "signature": s.signature,
                "lineno": s.lineno,
            }
            for s in symbols
        ],
    }


def list_modules(
    session: Session, *, kind: str | None = None, limit: int = 100
) -> list[dict[str, Any]]:
    stmt = select(CodeModule).order_by(CodeModule.module_id)
    if kind:
        stmt = stmt.where(CodeModule.kind == kind)
    rows = session.scalars(stmt.limit(limit)).all()
    return [
        {
            "module_id": row.module_id,
            "kind": row.kind,
            "path": row.path,
            "language": row.language,
            "owner_area": row.owner_area,
            "production_state": row.production_state,
            "purpose": row.purpose,
            "adr_refs": list(row.adr_refs or []),
            "spec_refs": list(row.spec_refs or []),
        }
        for row in rows
    ]


__all__ = [
    "MAX_CANDIDATES",
    "OPEN_INCIDENT_STATUSES",
    "REQUIRED_GATES",
    "TEST_FAILURE_EVENT_TYPES",
    "Answer",
    "Resolution",
    "last_test_failure",
    "list_modules",
    "module_problems",
    "module_status",
    "normalize_key",
    "ready_for_production",
    "resolve_module",
    "search",
    "what_is_running",
    "why_written",
]
