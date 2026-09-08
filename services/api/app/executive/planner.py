"""The planner (spec §2): deterministic reference, model seam inert.

``RuleBasedExecutivePlanner`` recognises the directive's THREE shapes and their variants
(spec §5's own example utterances) from the owner's normalized words — through
``app.voice.intents.normalize_transcript``, the SAME tr-TR normalisation/casefold/filler-
strip every other family's router matching uses, so there is no second Turkish table for
Executive Autonomy to drift from the rest of the router (the ``_explain_kind`` lesson,
``app.voice.intents`` module docstring). It never calls a service and never touches the
database: given a directive string it either returns a validated ``TaskGraph`` or raises
:class:`PlanningClarificationNeeded` — the caller (``app.executive.service.start_run``)
turns that into the honest Turkish clarification, never a guess.

``ClaudeExecutivePlanner`` is the INERT model seam spec §2 asks for: a model MAY propose a
graph, but it goes through the identical :func:`app.executive.graph.validate_graph` this
module's own output goes through — never around it (ADR-0089 decision 2). No model client
is wired in M26; the class exists so the seam's shape is fixed now, and a later milestone
wires a real proposer behind the same ``ExecutivePlanner`` protocol without touching
``workflow.py`` or ``service.py`` at all.
"""

from __future__ import annotations

from typing import Protocol

from app.executive.graph import validate_graph
from app.executive.spec import (
    COMPENSATION_DELETE_RENDER,
    COMPENSATION_DISCARD_DRAFT,
    COMPENSATION_NONE,
    EVIDENCE_ARTIFACT_ID,
    EVIDENCE_DOCUMENT_REFS,
    EVIDENCE_DRAFT_ID,
    EVIDENCE_RESEARCH_REPORT,
    EVIDENCE_TEXT,
    MAX_GOAL_CHARS,
    PRECONDITION_STEP_DONE,
    RISK_MUTATE_EXTERNAL,
    RISK_MUTATE_LOCAL,
    RISK_READ,
    STEP_KIND_ARTIFACTS_CREATE,
    STEP_KIND_DOCUMENTS_COMPARE,
    STEP_KIND_DOCUMENTS_FIND,
    STEP_KIND_MAIL_ANALYZE_THREAD,
    STEP_KIND_MAIL_DRAFT,
    STEP_KIND_RESEARCH_RUN,
    STEP_KIND_RESEARCH_SYNTHESIZE,
    STEP_KIND_SYNTHESIS,
    Postcondition,
    Precondition,
    Retry,
    Step,
    TaskGraph,
)
from app.voice.intents import normalize_transcript

#: The three shapes (spec §2), as a machine token — carried on the run for the receipt/
#: explain sentence and for tests; never itself part of the wire vocabulary.
SHAPE_RESEARCH = "research_report"
SHAPE_FOLDER_COMPARE = "folder_compare"
SHAPE_MAIL_THREAD = "mail_thread"

_RESEARCH_STEMS: tuple[str, ...] = ("araştır", "arastir", "research")
_COMPARE_STEMS: tuple[str, ...] = ("karşılaştır", "karsilastir", "kıyasla", "kiyasla")
_FOLDER_STEMS: tuple[str, ...] = ("klasör", "klasor", "teklif", "folder")
_MAIL_STEMS: tuple[str, ...] = ("mail", "posta", "e-posta", "eposta")
_THREAD_STEMS: tuple[str, ...] = ("zincir", "konuşma", "konusma", "thread")
_DRAFT_STEMS: tuple[str, ...] = ("taslak", "cevap", "yanıt", "yanit", "reply")
_PRESENTATION_STEMS: tuple[str, ...] = ("sunum", "slayt", "powerpoint", "presentation")
_SPREADSHEET_STEMS: tuple[str, ...] = ("excel", "tablo", "spreadsheet")
_SUMMARY_STEMS: tuple[str, ...] = ("özet", "ozet", "summary")


class PlanningClarificationNeeded(Exception):
    """The directive matched none of the three shapes (or a required detail — a folder,
    a mail focus — was not resolvable). ``speech`` is the honest Turkish clarification;
    never a guess at what the owner might have meant (constitution: routine questions are
    answered autonomously, but a fundamentally ambiguous directive is not routine)."""

    def __init__(self, speech: str) -> None:
        self.speech = speech
        super().__init__(speech)


def _has_any(tokens: tuple[str, ...], stems: tuple[str, ...]) -> bool:
    return any(tok == stem or tok.startswith(stem) for tok in tokens for stem in stems)


def _step(
    step_id: str,
    kind: str,
    *,
    inputs: dict[str, str] | None = None,
    precondition_step: str | None = None,
    evidence: str,
    risk_class: str,
    compensation: str = COMPENSATION_NONE,
    timeout_s: int = 120,
    retry_only_on: tuple[str, ...] = (),
    retry_max_attempts: int = 1,
    retry_backoff_s: float = 5.0,
    evidence_min: int | None = None,
) -> Step:
    precondition = (
        Precondition(check=PRECONDITION_STEP_DONE, arg=precondition_step)
        if precondition_step is not None
        else Precondition()
    )
    return Step(
        id=step_id,
        kind=kind,
        inputs=inputs or {},
        precondition=precondition,
        postcondition=Postcondition(evidence=evidence, min=evidence_min),
        timeout_s=timeout_s,
        retry=Retry(
            max_attempts=retry_max_attempts, backoff_s=retry_backoff_s, only_on=list(retry_only_on)
        ),
        risk_class=risk_class,
        compensation=compensation,
    )


def _synthesis_step(step_id: str, refs: dict[str, str], precondition_step: str) -> Step:
    return _step(
        step_id,
        STEP_KIND_SYNTHESIS,
        inputs=refs,
        precondition_step=precondition_step,
        evidence=EVIDENCE_TEXT,
        risk_class=RISK_READ,
        timeout_s=60,
    )


def _shape_research(directive: str, *, want_presentation: bool) -> TaskGraph:
    steps = [
        _step(
            "s1",
            STEP_KIND_RESEARCH_RUN,
            inputs={"topic": directive},
            evidence=EVIDENCE_RESEARCH_REPORT,
            risk_class=RISK_MUTATE_LOCAL,
            timeout_s=900,
            retry_only_on=("dependency_unavailable", "timeout"),
            retry_max_attempts=3,
            retry_backoff_s=30.0,
        ),
        _step(
            "s2",
            STEP_KIND_RESEARCH_SYNTHESIZE,
            inputs={"report": "s1.research_report"},
            precondition_step="s1",
            evidence=EVIDENCE_TEXT,
            risk_class=RISK_READ,
            timeout_s=120,
            retry_only_on=("timeout",),
            retry_max_attempts=2,
            retry_backoff_s=10.0,
        ),
        _step(
            "s3",
            STEP_KIND_ARTIFACTS_CREATE,
            inputs={"kind": "document", "source": "s2.text"},
            precondition_step="s2",
            evidence=EVIDENCE_ARTIFACT_ID,
            risk_class=RISK_MUTATE_LOCAL,
            compensation=COMPENSATION_DELETE_RENDER,
            timeout_s=180,
            retry_only_on=("timeout",),
            retry_max_attempts=2,
            retry_backoff_s=10.0,
        ),
    ]
    refs = {"document": "s3.artifact_id"}
    last = "s3"
    if want_presentation:
        steps.append(
            _step(
                "s4",
                STEP_KIND_ARTIFACTS_CREATE,
                inputs={"kind": "presentation", "source": "s2.text"},
                precondition_step="s2",
                evidence=EVIDENCE_ARTIFACT_ID,
                risk_class=RISK_MUTATE_LOCAL,
                compensation=COMPENSATION_DELETE_RENDER,
                timeout_s=180,
                retry_only_on=("timeout",),
                retry_max_attempts=2,
                retry_backoff_s=10.0,
            )
        )
        refs["presentation"] = "s4.artifact_id"
        last = "s4"
    steps.append(_synthesis_step("s5" if want_presentation else "s4", refs, last))
    return TaskGraph(goal=directive[:MAX_GOAL_CHARS], steps=steps)


def _shape_folder_compare(directive: str, *, folder: str | None) -> TaskGraph:
    steps = [
        _step(
            "s1",
            STEP_KIND_DOCUMENTS_FIND,
            inputs={"folder": folder or "current"},
            evidence=EVIDENCE_DOCUMENT_REFS,
            risk_class=RISK_READ,
            timeout_s=60,
        ),
        _step(
            "s2",
            STEP_KIND_DOCUMENTS_COMPARE,
            inputs={"targets": "s1.document_refs"},
            precondition_step="s1",
            evidence=EVIDENCE_DOCUMENT_REFS,
            risk_class=RISK_READ,
            timeout_s=90,
        ),
        _step(
            "s3",
            STEP_KIND_ARTIFACTS_CREATE,
            inputs={"kind": "spreadsheet", "source": "s2.document_refs"},
            precondition_step="s2",
            evidence=EVIDENCE_ARTIFACT_ID,
            risk_class=RISK_MUTATE_LOCAL,
            compensation=COMPENSATION_DELETE_RENDER,
            timeout_s=180,
            retry_only_on=("timeout",),
            retry_max_attempts=2,
            retry_backoff_s=10.0,
        ),
        _step(
            "s4",
            STEP_KIND_ARTIFACTS_CREATE,
            inputs={"kind": "document", "source": "s2.document_refs"},
            precondition_step="s2",
            evidence=EVIDENCE_ARTIFACT_ID,
            risk_class=RISK_MUTATE_LOCAL,
            compensation=COMPENSATION_DELETE_RENDER,
            timeout_s=180,
            retry_only_on=("timeout",),
            retry_max_attempts=2,
            retry_backoff_s=10.0,
        ),
    ]
    steps.append(
        _synthesis_step("s5", {"spreadsheet": "s3.artifact_id", "summary": "s4.artifact_id"}, "s4")
    )
    return TaskGraph(goal=directive[:MAX_GOAL_CHARS], steps=steps)


def _shape_mail_thread(directive: str) -> TaskGraph:
    steps = [
        _step(
            "s1",
            STEP_KIND_MAIL_ANALYZE_THREAD,
            inputs={"target": "current"},
            evidence=EVIDENCE_TEXT,
            risk_class=RISK_READ,
            timeout_s=60,
        ),
        _step(
            "s2",
            STEP_KIND_DOCUMENTS_FIND,
            inputs={"folder": "current"},
            precondition_step="s1",
            evidence=EVIDENCE_DOCUMENT_REFS,
            risk_class=RISK_READ,
            timeout_s=60,
            # documents.find may legitimately come back with zero files (spec §3's
            # "a document unavailable" case is exactly this shape) — min=0 so an empty
            # find is not itself a graph-level contradiction; the run still ends
            # honestly partial only if mail.draft then cannot proceed without it.
            evidence_min=0,
        ),
        _step(
            "s3",
            STEP_KIND_MAIL_DRAFT,
            inputs={"body_source": "s1.text", "attachments": "s2.document_refs"},
            precondition_step="s2",
            evidence=EVIDENCE_DRAFT_ID,
            risk_class=RISK_MUTATE_EXTERNAL,
            compensation=COMPENSATION_DISCARD_DRAFT,
            timeout_s=60,
        ),
    ]
    steps.append(_synthesis_step("s4", {"draft": "s3.draft_id"}, "s3"))
    return TaskGraph(goal=directive[:MAX_GOAL_CHARS], steps=steps)


class ExecutivePlanner(Protocol):
    def plan(self, directive: str, *, folder: str | None = None) -> TaskGraph: ...


class RuleBasedExecutivePlanner:
    """Deterministic (spec §2): the SAME directive text always produces the SAME graph.
    No randomness, no model call — every branch below is a plain token match."""

    def plan(self, directive: str, *, folder: str | None = None) -> TaskGraph:
        text = (directive or "").strip()
        if not text:
            raise PlanningClarificationNeeded("Ne yapmamı istediğinizi tam anlayamadım efendim.")
        _normalized, tokens, _fillers = normalize_transcript(text)

        if _has_any(tokens, _MAIL_STEMS) and (
            _has_any(tokens, _THREAD_STEMS) or _has_any(tokens, _DRAFT_STEMS)
        ):
            graph = _shape_mail_thread(text)
        elif _has_any(tokens, _COMPARE_STEMS) and _has_any(tokens, _FOLDER_STEMS):
            graph = _shape_folder_compare(text, folder=folder)
        elif _has_any(tokens, _RESEARCH_STEMS):
            graph = _shape_research(text, want_presentation=_has_any(tokens, _PRESENTATION_STEMS))
        else:
            raise PlanningClarificationNeeded(
                "Bunu araştırma, klasör karşılaştırma veya mail taslağı işlerinden "
                "hangisi olarak ele almamı istediğinizi söyler misiniz efendim?"
            )

        # ADR-0089 decision 1: EVERY graph, however it was built, goes through the same
        # gate before it is trusted. A deterministic planner bug is still a bug.
        validate_graph(graph)
        return graph


class ClaudeExecutivePlanner:
    """The model seam (spec §2): INERT in M26. A future milestone wires a real model
    call here; until then this raises rather than silently falling back to the rule-based
    planner (a caller that asked for the model seam specifically must know it got
    nothing, not receive an answer it did not ask for)."""

    def plan(self, directive: str, *, folder: str | None = None) -> TaskGraph:
        raise NotImplementedError(
            "ClaudeExecutivePlanner is an inert seam in M26: no model proposer is wired. "
            "Any graph it eventually proposes MUST still pass app.executive.graph."
            "validate_graph before it may run (ADR-0089 decision 2) — that gate is not "
            "optional for a model-proposed graph any more than for the rule-based one."
        )


__all__ = [
    "ClaudeExecutivePlanner",
    "ExecutivePlanner",
    "PlanningClarificationNeeded",
    "RuleBasedExecutivePlanner",
    "SHAPE_FOLDER_COMPARE",
    "SHAPE_MAIL_THREAD",
    "SHAPE_RESEARCH",
]
