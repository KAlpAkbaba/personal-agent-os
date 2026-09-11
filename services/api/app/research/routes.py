"""M13 research REST surface (M13_RESEARCH_SPEC.md §4). Owner-gated like every
other surface (artifacts, memory, devices).

Device selection happens HERE, synchronously, before the workflow starts —
not as the workflow's first activity — so a request that names an
unreachable device (or when nothing is online at all) fails fast with
``409 no_capable_device`` and a Turkish ``detail`` instead of starting a
workflow that is certain to fail later. The selected device is persisted on
the run row immediately, so the workflow's own ``select_device`` activity
(idempotent, spec §5) reuses it rather than re-deciding.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from temporalio.client import Client

from app.artifacts import service as artifact_service
from app.artifacts.runtime import ArtifactRuntime
from app.broker.runtime import BrokerRuntime
from app.identity.dependencies import require_owner_session
from app.logging import get_logger, trace_id_var
from app.research import focus as research_focus
from app.research import runs_service
from app.research import service as research_service
from app.research.browser_gateway import SearchEvidence
from app.research.contracts import (
    ERROR_INSUFFICIENT_VALID_FINDINGS,
    MIN_REPORT_FINDINGS,
    MIN_VALID_EVIDENCE,
    SCHEMAS,
    TARGET_REPORT_FINDINGS,
)
from app.research.eligibility import (
    MIN_TOPIC_RELEVANCE,
    PAGE_VALIDITY_KINDS,
    REJECTION_REASONS,
)
from app.research.models import STAGE_CANCELLED, ResearchRunRow
from app.research.policy import DEFAULT_MODE, POLICIES, RESEARCH_MODES

logger = get_logger("app.research.routes")

router = APIRouter(prefix="/v1/research", dependencies=[Depends(require_owner_session)])

MAX_SOURCES_CEILING = 30


def _broker(request: Request) -> BrokerRuntime:
    return request.app.state.broker


def _artifacts(request: Request) -> ArtifactRuntime:
    return request.app.state.artifacts


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


async def _temporal_client(request: Request) -> Client:
    settings = request.app.state.artifacts.settings
    return await Client.connect(settings.temporal_address, namespace=settings.temporal_namespace)


#: spec §5a: interactive_wait_s bounds (60s = one browser.wait slice; 1800s = 30 minutes).
# 30 s exists for owner QUALIFICATION runs (a 600 s wait is not a test); production
# requests keep DEFAULT_INTERACTIVE_WAIT_S, and the owner smoke has -HandoffTimeoutSec.
# Re-exported from app.research.service (the single definition both callers share) so
# nothing importing them from this module needs to change.
MIN_INTERACTIVE_WAIT_S = research_service.MIN_INTERACTIVE_WAIT_S
MAX_INTERACTIVE_WAIT_S = research_service.MAX_INTERACTIVE_WAIT_S
DEFAULT_INTERACTIVE_WAIT_S = research_service.DEFAULT_INTERACTIVE_WAIT_S


class CreateResearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input: str = Field(min_length=1, max_length=4000)
    target_device: str | None = Field(default=None, max_length=256)
    recency_days: int | None = Field(default=None, ge=1, le=365)
    max_sources: int = Field(default=12, ge=1, le=MAX_SOURCES_CEILING)
    synthesis: str = Field(default="auto", max_length=32)
    #: spec §5a: owner-handoff mode. The web page sends `true`; the CLI
    #: runner (scripts/research_smoke.py) has `--interactive`. False
    #: (default) never waits on a Google interstitial.
    interactive: bool = Field(default=False)
    #: owner-facing spelling of the same choice (contract §3a search modes):
    #: "interactive" = Google -> owner handoff if needed -> fallback only afterwards;
    #: "unattended" = Google -> deterministic fallback if blocked. Wins over `interactive`.
    mode: str | None = Field(default=None, pattern="^(interactive|unattended)$")
    #: interactive runs only: "fallback" (default) or "fail" when the owner does not
    #: complete Google's page within interactive_wait_s (spec §5a).
    on_verification_timeout: str = Field(default="fallback", pattern="^(fallback|fail)$")
    interactive_wait_s: int = Field(
        default=DEFAULT_INTERACTIVE_WAIT_S,
        ge=MIN_INTERACTIVE_WAIT_S,
        le=MAX_INTERACTIVE_WAIT_S,
    )
    #: PRODUCT DECISION (owner, 2026-09-04): DuckDuckGo is the default
    #: production search provider; None means "use the Settings default"
    #: (research_search_provider). "google" stays fully selectable —
    #: including its CAPTCHA/owner-handoff machinery, unchanged.
    search_provider: str | None = Field(default=None, pattern="^(duckduckgo|google|auto)$")
    #: M18.2 (ADR-0068): "quick" (default) | "standard" | "deep" — how much research
    #: this run does (app.research.policy.ResearchPolicy), never how it searches.
    #: Named ``research_mode`` rather than reusing ``mode`` above: that field
    #: already means interactive/unattended (owner-handoff), an existing, tested
    #: contract this change leaves alone (ADR-0067 amendment).
    research_mode: str = Field(default=DEFAULT_MODE, pattern="^(quick|standard|deep)$")


def _effective_interactive(body: CreateResearchRequest) -> bool:
    if body.mode is not None:
        return body.mode == "interactive"
    return body.interactive


CreateResearchRequest.effective_interactive = property(_effective_interactive)  # type: ignore[attr-defined]


@router.post("", status_code=202)
async def create_research(request: Request, body: CreateResearchRequest) -> JSONResponse:
    broker = _broker(request)
    artifacts = _artifacts(request)
    trace_id = trace_id_var.get()

    def create_task_and_select() -> research_service.StartedResearch:
        with artifacts.session() as session:
            return research_service.start_browser_research(
                session,
                broker,
                input=body.input,
                target_device=body.target_device,
                recency_days=body.recency_days,
                max_sources=body.max_sources,
                trace_id=trace_id,
                source=research_service.SOURCE_REST,
            )

    started = await asyncio.to_thread(create_task_and_select)
    if started.error is not None:
        logger.info(
            "research_no_capable_device", task_id=str(started.task_id), detail=started.error
        )
        raise HTTPException(
            status_code=409,
            detail={
                "error_class": "no_capable_device",
                "detail": started.error,
                "task_id": str(started.task_id),
            },
        )

    try:
        client = await _temporal_client(request)
        await research_service.start_browser_research_workflow(
            client,
            artifacts,
            task_id=started.task_id,
            workflow_id=started.workflow_id,
            input=body.input,
            target_device=body.target_device,
            recency_days=body.recency_days,
            max_sources=body.max_sources,
            synthesis=body.synthesis,
            interactive=body.effective_interactive,
            interactive_wait_s=body.interactive_wait_s,
            on_verification_timeout=body.on_verification_timeout,
            search_provider=body.search_provider or artifacts.settings.research_search_provider,
            mode=body.research_mode,
        )
    except Exception as exc:  # noqa: BLE001 - a typed refusal, and the task closed
        # Phase 8: Temporal down used to surface as an untyped 500 with the task left
        # CREATED for ever. It is a dependency the caller can name, and the task says so.
        detail = f"{type(exc).__name__}: {exc}"

        def close_task() -> None:
            with artifacts.session() as session:
                research_service.fail_unstarted_research(session, started.task_id, detail=detail)

        await asyncio.to_thread(close_task)
        logger.warning(
            "research_workflow_start_failed", task_id=str(started.task_id), error=detail[:300]
        )
        raise HTTPException(
            status_code=503,
            detail={
                "error_class": "dependency_unavailable",
                "detail": research_service.WORKFLOW_UNAVAILABLE_TR,
                "task_id": str(started.task_id),
            },
        ) from exc
    logger.info("research_created", task_id=str(started.task_id), workflow_id=started.workflow_id)
    return JSONResponse(
        status_code=202,
        content={
            "task_id": str(started.task_id),
            "workflow_id": started.workflow_id,
            "status": "planned",
            "device": started.device,
        },
    )


#: Bumped whenever the research POLICY or EVIDENCE contract changes shape (not on every code
#: change). A client compares it with what it expects; an older Cloud Core answers 404 or a
#: lower number, which is the signal that one release is needed.
#: 1 - DuckDuckGo default provider, per-request search_provider (2026-09-04)
#: 2 - typed field contracts + per-candidate quarantine (ADR-0050 item 20, 2026-09-04)
#: 3 - named, versioned entity schemas: required/optional/derived fields, per-item quarantine
#:     for statements and detail sections (ADR-0050 item 21, 2026-09-04)
#: 4 - research speed modes (quick/standard/deep), wave-based fetching with early stop, and
#:     the challenge/cooldown policy (ADR-0068, 2026-09-07)
RESEARCH_POLICY_VERSION = 5


@router.get("/policy")
async def get_research_policy(request: Request) -> dict[str, Any]:
    """The effective research policy of THIS Cloud Core (owner decision, 2026-09-04).

    Side-effect free: a client (the owner research command, the web page) reads it to know
    which search provider a default run will use and whether this deployment understands the
    policy at all — an older Cloud Core has no such route and answers 404, which is the
    signal that a Cloud Core release is needed, rather than silently running the older
    Google-first discovery.
    """
    settings = _artifacts(request).settings
    return {
        "policy_version": RESEARCH_POLICY_VERSION,
        "search_provider": settings.research_search_provider,
        "search_providers": ["duckduckgo", "google", "auto"],
        "worker_search_contract": SearchEvidence.REQUIRED_SCHEMA_VERSION,
        "max_sources_ceiling": MAX_SOURCES_CEILING,
        "interactive_wait_s": {
            "default": DEFAULT_INTERACTIVE_WAIT_S,
            "min": MIN_INTERACTIVE_WAIT_S,
            "max": MAX_INTERACTIVE_WAIT_S,
        },
        "evidence_contract": {
            "typed_fields": True,
            "quarantine": "invalid_evidence_contract",
            "min_valid_evidence": MIN_VALID_EVIDENCE,
            "schemas": {name: entity.version for name, entity in sorted(SCHEMAS.items())},
        },
        "quality_gate": {
            # Policy 4 (owner incident, 2026-09-04): fetched pages are judged for topic,
            # recency and page validity BEFORE they can be cited, and a report that cannot
            # reach the findings floor fails instead of publishing an empty answer.
            "rejection_reasons": sorted(REJECTION_REASONS),
            "min_topic_relevance": MIN_TOPIC_RELEVANCE,
            "page_validity_kinds": sorted(PAGE_VALIDITY_KINDS),
        },
        "findings_contract": {
            "min_findings": MIN_REPORT_FINDINGS,
            "target_findings": TARGET_REPORT_FINDINGS,
            "attribution_required": True,
            "failure_error_class": ERROR_INSUFFICIENT_VALID_FINDINGS,
        },
        "verification_timeout_policies": ["fallback", "fail"],
        "modes": ["interactive", "unattended"],
        # M18.2 (ADR-0068): the research SPEED modes — quick/standard/deep — distinct
        # from "modes" above (owner-handoff interactive/unattended). "research_mode"
        # is this route's own request field name for the same reason.
        "research_modes": RESEARCH_MODES,
        "research_mode_default": DEFAULT_MODE,
        "research_policies": {name: p.as_dict() for name, p in POLICIES.items()},
    }


# ---------------------------------------------------------------------- focus
#
# docs/DECISIONS.md ADR-0076. Declared BEFORE ``GET /{task_id}``: "focus" is not a UUID,
# and a path parameter typed as one would answer 422 rather than falling through.


@router.get("/focus")
async def get_research_focus(request: Request) -> dict[str, Any]:
    """Which research the owner is pointing at, and what else is within reach.

    The web track builds against exactly this shape. It is a READ: it never moves the
    focus, never expires the clarification early, and starts nothing.
    """
    artifacts = _artifacts(request)

    def load() -> dict[str, Any]:
        with artifacts.session() as session:
            now = datetime.now(UTC)
            current = research_focus.current_focus(session)
            previous = research_focus.previous_focus(session)
            stack = research_focus.focus_stack(session)
            pending = research_focus.peek_pending_clarification(session, now=now)
            return {
                "current": current.as_dict() if current else None,
                "previous": previous.as_dict() if previous else None,
                "stack": [entry.as_dict() for entry in stack],
                "pending_clarification": pending,
            }

    return await asyncio.to_thread(load)


@router.post("/{task_id}/focus")
async def set_research_focus(request: Request, task_id: uuid.UUID) -> dict[str, Any]:
    """The owner selected a research in the UI. That IS the focus.

    A click is the least ambiguous reference there is, so it outranks every inference —
    and because the focus is durable and owner-level, the next voice session's "bunu
    anlat" means this one even though that session has never heard of it. Refused with
    409 when the run has no READY report: pointing at a research that has not finished
    would make every follow-up a question.
    """
    artifacts = _artifacts(request)

    def apply() -> tuple[int, dict[str, Any]]:
        with artifacts.session() as session:
            if runs_service.get_run(session, task_id) is None:
                return 404, {"detail": "unknown research task"}
            if not research_focus.is_completed(session, task_id):
                return 409, {"error": "not_completed"}
            entry = research_focus.set_focus(
                session, task_id, source=research_focus.FOCUS_OWNER_SELECTED_IN_UI
            )
            if entry is None:  # pragma: no cover - only on a genuine DB fault
                return 409, {"error": "not_completed"}
            session.commit()
            return 200, {"focus": entry.as_dict()}

    status, payload = await asyncio.to_thread(apply)
    if status == 404:
        raise HTTPException(status_code=404, detail=payload.get("detail"))
    if status == 409:
        return JSONResponse(status_code=409, content=payload)
    logger.info("research_focus_set", task_id=str(task_id), source="owner_selected_in_ui")
    return payload


@router.get("")
async def list_research(request: Request) -> dict[str, Any]:
    artifacts = _artifacts(request)

    def load() -> list[dict[str, Any]]:
        with artifacts.session() as session:
            # ADR-0076: which row is the focus, established ONCE for the whole list.
            current = research_focus.current_focus(session)
            focus_job_id = current.research_job_id if current else None
            tasks = artifact_service.list_tasks(session, limit=500)
            out = []
            for task in tasks:
                run = runs_service.get_run(session, task.id)
                if run is None:
                    continue  # not a research task
                report_row = runs_service.get_report(session, task.id)
                report_json = dict(getattr(report_row, "report_json", None) or {})
                stats = report_json.get("stats")
                stats = stats if isinstance(stats, dict) else {}
                plan_json = dict(run.plan_json or {})
                sources = report_json.get("sources") or ()
                out.append(
                    {
                        "task_id": str(task.id),
                        "topic": task.intent,
                        "status": task.status,
                        "stage": run.stage,
                        "device": str(run.device_id) if run.device_id else None,
                        "created_at": _iso(task.created_at),
                        "ready_at": _iso(task.ready_at),
                        # ADR-0076: the three fields a list row needs to be SELECTABLE -
                        # the owner picks a research by what it was and when it finished,
                        # never by a task id - plus which one is currently in focus.
                        "completed_at": _iso(task.ready_at),
                        "mode": str(stats.get("mode") or plan_json.get("mode") or "") or None,
                        "source_count": (
                            len(sources) if isinstance(sources, (list, tuple)) else 0
                        ),
                        "is_focus": str(task.id) == focus_job_id,
                        "artifact_id": str(report_row.artifact_id)
                        if report_row and report_row.artifact_id
                        else None,
                    }
                )
            return out

    tasks = await asyncio.to_thread(load)
    return {"tasks": tasks}


def _load_detail(artifacts: ArtifactRuntime, task_id: uuid.UUID) -> dict[str, Any] | None:
    with artifacts.session() as session:
        task = artifact_service.get_task(session, task_id)
        if task is None:
            return None
        run: ResearchRunRow | None = runs_service.get_run(session, task_id)
        report_row = runs_service.get_report(session, task_id)
        error = None
        if task.error_class:
            error = {"error_class": task.error_class, "detail": task.error_message}
        elif run is not None and run.error:
            error = {"error_class": "research_failed", "detail": run.error}
        return {
            "task_id": str(task.id),
            "topic": task.intent,
            "status": task.status,
            "stage": run.stage if run else None,
            "progress": run.progress_json if run else {},
            "device": str(run.device_id) if run and run.device_id else None,
            "plan": run.plan_json if run else None,
            "report": report_row.report_json if report_row else None,
            "artifact_id": str(report_row.artifact_id)
            if report_row and report_row.artifact_id
            else None,
            "memory_id": str(report_row.memory_id) if report_row and report_row.memory_id else None,
            "error": error,
            "events": (run.events_json or [])[-50:] if run else [],
        }


@router.get("/{task_id}")
async def get_research(request: Request, task_id: uuid.UUID) -> dict[str, Any]:
    artifacts = _artifacts(request)
    payload = await asyncio.to_thread(_load_detail, artifacts, task_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="unknown research task")
    return payload


@router.get("/{task_id}/report")
async def get_research_report(request: Request, task_id: uuid.UUID) -> dict[str, Any]:
    artifacts = _artifacts(request)

    def load() -> dict[str, Any] | None:
        with artifacts.session() as session:
            row = runs_service.get_report(session, task_id)
            return row.report_json if row else None

    report = await asyncio.to_thread(load)
    if report is None:
        raise HTTPException(status_code=404, detail="report not synthesized yet")
    return report


@router.post("/{task_id}/cancel")
async def cancel_research(request: Request, task_id: uuid.UUID) -> dict[str, Any]:
    artifacts = _artifacts(request)

    def load_workflow_id() -> str | None:
        with artifacts.session() as session:
            task = artifact_service.get_task(session, task_id)
            return task.workflow_id if task else None

    workflow_id = await asyncio.to_thread(load_workflow_id)
    if workflow_id is None:
        raise HTTPException(status_code=404, detail="unknown research task")

    client = await _temporal_client(request)
    handle = client.get_workflow_handle(workflow_id)
    try:
        await handle.cancel()
    except Exception as exc:  # noqa: BLE001 - the workflow may already be gone/terminal
        logger.warning("research_cancel_failed", task_id=str(task_id), error=str(exc))

    def mark() -> None:
        with artifacts.session() as session:
            runs_service.update_run(
                session,
                task_id,
                stage=STAGE_CANCELLED,
                event={"stage": STAGE_CANCELLED, "detail": "cancelled by owner"},
            )

    await asyncio.to_thread(mark)
    logger.info("research_cancelled", task_id=str(task_id), workflow_id=workflow_id)
    return {"task_id": str(task_id), "status": "cancel_requested"}


__all__ = ["router"]
