"""Temporal activities for BrowserResearchWorkflow (M13 spec §5).

Each activity is idempotent on ``(task_id, step_key, attempt)`` so the
durable workflow can be replayed/resumed after a worker restart without
duplicating device commands, evidence rows, the artifact or the memory
write. DB + device-command + object-store access happens only here (never in
the workflow), built from settings so the code runs identically embedded in
the API process or in the standalone ``python -m app.worker``.
"""

from __future__ import annotations

import dataclasses
import uuid
from datetime import UTC, datetime
from typing import Any

from temporalio import activity
from temporalio.exceptions import ApplicationError

from app.artifacts import render_store
from app.artifacts import service as artifact_service
from app.artifacts.models import (
    ARTIFACT_KIND_RESEARCH_REPORT,
    ARTIFACT_STATE_CANONICAL_READY,
    ARTIFACT_STATE_READY,
    ARTIFACT_STATE_RENDERS_PENDING,
    TASK_STATUS_FAILED_TERMINAL,
    TASK_STATUS_PLANNED,
    TASK_STATUS_READY,
    TASK_STATUS_RENDERING,
    TASK_STATUS_RUNNING,
)
from app.artifacts.renderers import DEFAULT_RENDER_FORMATS, content_hash
from app.artifacts.runtime import build_artifact_context
from app.artifacts.state import IllegalTransition
from app.config import get_settings
from app.db import build_engine, build_session_factory
from app.devices import service as devices_service
from app.devices.commands import DeviceCommandClient, get_broker_runtime
from app.devices.selection import NoCapableDeviceError, select_device
from app.ledger import briefing as ledger_briefing
from app.ledger import service as ledger_service
from app.ledger.vocabulary import (
    EVENT_TYPE_RESEARCH_QUALITY_GATE,
    STATUS_INFO,
    SUBSYSTEM_RESEARCH,
)
from app.logging import get_logger, task_id_var
from app.memory.embedding import DeterministicEmbedder
from app.memory.service import remember_explicit
from app.memory.types import MemoryClass
from app.research import discovery, eligibility, runs_service, sources
from app.research.browser_gateway import BrowserDispatchError, DeviceBrowserGateway
from app.research.contracts import (
    ERROR_INSUFFICIENT_VALID_EVIDENCE,
    ERROR_INSUFFICIENT_VALID_FINDINGS,
    MIN_REPORT_FINDINGS,
    MIN_VALID_EVIDENCE,
    ContractViolation,
    InsufficientValidEvidence,
    InsufficientValidFindings,
    QuarantineLedger,
)
from app.research.destination import DestinationPolicyError, validate_fetch_target
from app.research.evidence import EvidenceRecord, dedup_and_rank
from app.research.injection import count_markers, is_injection_suspected
from app.research.models import (
    STAGE_DISCOVERING,
    STAGE_FAILED,
    STAGE_FETCHING,
    STAGE_PERSISTING,
    STAGE_PLANNED,
    STAGE_RANKING,
    STAGE_READY,
    STAGE_SELECTING_DEVICE,
    STAGE_SYNTHESIZING,
    STAGE_WAITING_FOR_OWNER_VERIFICATION,
)
from app.research.plan import build_plan
from app.research.report import (
    DetailSection,
    Finding,
    ReportStats,
    ReportWindow,
    ResearchReport,
    SourceItem,
    Statement,
    assign_evidence_ids,
    render_research_markdown,
    run_provenance_gate,
)
from app.research.synthesis import resolve_synthesis_provider
from app.uistate import UiState
from app.uistate import publish as publish_ui

logger = get_logger("app.research.browser_activities")

RETRYABLE_ERROR_CLASSES = frozenset(
    {"dependency_unavailable", "timeout", "ui_state_changed", "provider_rate_limited"}
)


def _session_factory():
    settings = get_settings()
    engine = build_engine(settings.database_url)
    return build_session_factory(engine)


def _command_client() -> DeviceCommandClient:
    return DeviceCommandClient(_session_factory())


def _current_attempt() -> int:
    return activity.info().attempt if activity.in_activity() else 1


def _non_retryable(error_type: str, message: str) -> ApplicationError:
    return ApplicationError(message, type=error_type, non_retryable=True)


def _retryable(error_type: str, message: str) -> ApplicationError:
    return ApplicationError(message, type=error_type, non_retryable=False)


def _transition_task(session, tid: uuid.UUID, status: str, **kwargs: Any) -> None:
    """Best-effort Task.status transition — a replayed activity may re-request
    a transition already applied; the state machine's own idempotent
    re-assert (``new == current``) covers most of that, and any genuinely
    illegal edge (a race between two activities) must not fail the activity."""
    try:
        artifact_service.transition_task(session, tid, status, **kwargs)
    except IllegalTransition as exc:
        logger.debug("browser_research_task_transition_skipped", task_id=str(tid), error=str(exc))


def _stored(payload: Any, key: str, *, entity: str, entity_id: str = "") -> Any:
    """Read a key from a document THIS Cloud Core wrote (a stored report, a plan window).

    Not a model's output, but the same class of boundary: a document written by an older
    release can lack a key, and a bare ``KeyError`` at that point says nothing about which
    entity or field was missing (2026-09-04: KeyError('label') from a model statement). This
    reports the same structured violation, with the producer named as this Cloud Core.
    """
    from app.research.contracts import PRODUCER_CLOUD_CORE, ContractViolation

    if not isinstance(payload, dict) or key not in payload:
        raise ContractViolation(
            entity=entity,
            field_name=key,
            expected="a stored " + entity + " carrying " + key,
            observed=payload if not isinstance(payload, dict) else _MISSING_KEY,
            entity_id=entity_id or entity,
            stage="composing",
            reason="missing_required_field",
            producer=PRODUCER_CLOUD_CORE,
            schema_version=1,
        )
    return payload[key]


_MISSING_KEY = object()


def _report_from_json(report_json: dict[str, Any]) -> ResearchReport:
    def statement(d: dict[str, Any]) -> Statement:
        return Statement(
            text=_stored(d, "text", entity="statement"),
            label=_stored(d, "label", entity="statement"),
            evidence_ids=tuple(d.get("evidence_ids", ())),
            provenance_note=d.get("provenance_note"),
        )

    def finding(d: dict[str, Any]) -> Finding:
        return Finding(
            id=_stored(d, "id", entity="finding"),
            title=_stored(d, "title", entity="finding"),
            summary=_stored(d, "summary", entity="finding"),
            why_it_matters=_stored(d, "why_it_matters", entity="finding"),
            importance=_stored(d, "importance", entity="finding"),
            label=_stored(d, "label", entity="finding", entity_id=str(d.get("id", ""))),
            evidence_ids=tuple(d.get("evidence_ids", ())),
            first_seen=d.get("first_seen"),
            provenance_note=d.get("provenance_note"),
        )

    return ResearchReport(
        task_id=_stored(report_json, "task_id", entity="report"),
        topic=_stored(report_json, "topic", entity="report"),
        window=ReportWindow(**_stored(report_json, "window", entity="report")),
        generated_at=_stored(report_json, "generated_at", entity="report"),
        synthesis_provider=_stored(report_json, "synthesis_provider", entity="report"),
        executive_summary=_stored(report_json, "executive_summary", entity="report"),
        findings=tuple(finding(f) for f in _stored(report_json, "findings", entity="report")),
        why_it_matters=tuple(
            statement(s) for s in _stored(report_json, "why_it_matters", entity="report")
        ),
        watch_next=tuple(statement(s) for s in _stored(report_json, "watch_next", entity="report")),
        details=tuple(
            DetailSection(
                heading=d["heading"], statements=tuple(statement(s) for s in d["statements"])
            )
            for d in _stored(report_json, "details", entity="report")
        ),
        uncertainty=tuple(
            statement(s) for s in _stored(report_json, "uncertainty", entity="report")
        ),
        sources=tuple(SourceItem(**s) for s in _stored(report_json, "sources", entity="report")),
        stats=ReportStats(**_stored(report_json, "stats", entity="report")),
    )


# --------------------------------------------------------------------- plan


@activity.defn(name="browser_research_plan")
def plan_activity(
    task_id: str, topic: str, recency_days: int | None, max_sources: int
) -> dict[str, Any]:
    task_id_var.set(task_id)
    tid = uuid.UUID(task_id)
    factory = _session_factory()
    with factory() as session:
        existing = runs_service.get_run(session, tid)
        if existing is not None and existing.plan_json:
            return existing.plan_json
        plan = build_plan(
            topic,
            now=datetime.now(UTC),
            recency_days_override=recency_days,
            max_sources_per_query=max(1, max_sources // 4 or 1),
        )
        plan_dict = plan.as_dict()
        _transition_task(session, tid, TASK_STATUS_PLANNED)
        runs_service.update_run(
            session,
            tid,
            stage=STAGE_PLANNED,
            plan_json=plan_dict,
            event={"stage": STAGE_PLANNED, "detail": "plan built"},
        )
    logger.info("browser_research_planned", task_id=task_id)
    return plan_dict


# ------------------------------------------------------------- select_device


@activity.defn(name="browser_research_select_device")
def select_device_activity(task_id: str, target_device: str | None) -> dict[str, Any]:
    task_id_var.set(task_id)
    tid = uuid.UUID(task_id)
    factory = _session_factory()

    runtime = get_broker_runtime()
    if runtime is None:
        raise _non_retryable(
            "dependency_unavailable", "broker runtime not registered in this process"
        )

    with factory() as session:
        run = runs_service.get_run(session, tid)
        if run is not None and run.device_id is not None:
            view = devices_service.get_device_view(session, runtime, run.device_id)
            if view is not None and view.presence == "online":
                # This is the COMMON path in production: POST /v1/research
                # already selected + persisted the device before starting the
                # workflow (app/research/routes.py), so RUNNING must be
                # entered here too, not only on the (replay-only) re-select
                # path below.
                _transition_task(session, tid, TASK_STATUS_RUNNING)
                return {"device_id": str(run.device_id), "name": view.name, "reused": True}
        views = devices_service.list_device_views(
            session, runtime, stale_after_s=get_settings().device_presence_stale_after_s
        )
        try:
            result = select_device(views, capability="browser.chrome", target=target_device)
        except NoCapableDeviceError as exc:
            _transition_task(
                session,
                tid,
                TASK_STATUS_FAILED_TERMINAL,
                error_class="no_capable_device",
                error_message=exc.detail_tr,
            )
            runs_service.update_run(
                session,
                tid,
                stage=STAGE_FAILED,
                error=exc.detail_tr,
                event={"stage": STAGE_FAILED, "detail": exc.detail_tr},
            )
            raise _non_retryable("no_capable_device", exc.detail_tr) from exc
        _transition_task(session, tid, TASK_STATUS_RUNNING)
        runs_service.update_run(
            session,
            tid,
            stage=STAGE_SELECTING_DEVICE,
            device_id=result.device.id,
            event={"stage": STAGE_SELECTING_DEVICE, "detail": f"selected {result.device.name}"},
        )
    logger.info(
        "browser_research_device_selected", task_id=task_id, device_id=str(result.device.id)
    )
    return {"device_id": str(result.device.id), "name": result.device.name, "reused": False}


# ------------------------------------------------------------------ discover


def _official_candidates(query_text: str, query_id: str) -> list[discovery.DiscoveredCandidate]:
    out: list[discovery.DiscoveredCandidate] = []
    for entry in sources.for_topics(tuple(query_text.split())):
        if not entry.feed_url:
            continue
        try:
            out.extend(
                discovery.fetch_rss(entry.feed_url, publisher=entry.publisher, query_id=query_id)
            )
        except discovery.DiscoveryError as exc:
            # One feed failing must not fail discovery for the whole run.
            logger.warning(
                "browser_research_feed_failed", publisher=entry.publisher, error=str(exc)
            )
    return out


def _retag(candidates: list, query_id: str) -> list:
    """API discovery (Hacker News, arXiv) labels candidates with the query TEXT; the
    workflow's ``<source_class>:<i>`` id is what carries the class into fetch ordering
    and the report (seen live: every HN/arXiv source came out as class ``unknown``)."""
    from dataclasses import replace

    return [replace(c, query_id=query_id) for c in candidates]


@activity.defn(name="browser_research_discover")
def discover_activity(
    task_id: str,
    device_id: str,
    query_id: str,
    query_text: str,
    source_class: str,
    window_start_iso: str,
    interstitial: str = "fallback",
    search_provider: str | None = None,
) -> dict[str, Any]:
    """Returns ``{"status": "done"|"waiting", "candidates": n, "path": …,
    "verification_url": …}`` (spec §5a). ``status=="waiting"`` means the
    device handed a Google interstitial back to the owner
    (``interstitial="handoff"``) instead of solving or falling back; the
    workflow is responsible for looping ``await_verification_activity`` and
    re-calling this activity for the SAME query afterwards. ``search_provider``
    (PRODUCT DECISION 2026-09-04: default DuckDuckGo) comes from the workflow
    request; ``None`` falls back to ``Settings.research_search_provider``
    (e.g. a direct/legacy activity call)."""
    task_id_var.set(task_id)
    tid = uuid.UUID(task_id)
    window_start = datetime.fromisoformat(window_start_iso)

    candidates: list[discovery.DiscoveredCandidate] = []
    path: str | None = None
    verification_url: str | None = None
    try:
        if source_class == "technical":
            candidates = discovery.fetch_hn(query_text, window_start=window_start)
            candidates = _retag(candidates, query_id)
        elif source_class == "academic":
            candidates = discovery.fetch_arxiv(query_text)
            candidates = _retag(candidates, query_id)
        elif source_class == "official":
            candidates = _official_candidates(query_text, query_id)
        else:  # "news" / "community": the device's real Chrome, semantic result links
            factory0 = _session_factory()
            with factory0() as session:
                already_discovered = any(
                    c.query_id == query_id for c in runs_service.list_candidates(session, tid)
                )
            if already_discovered:
                # spec §5a: "identical (query, provider) searches within a job
                # are not re-issued" — this query already produced candidates
                # (or is in flight elsewhere); nothing new to search for.
                return {
                    "status": "done",
                    "candidates": 0,
                    "path": "cached",
                    "verification_url": None,
                }

            gateway = DeviceBrowserGateway(
                _command_client(),
                device_id=uuid.UUID(device_id),
                task_id=task_id,
                search_provider=search_provider or get_settings().research_search_provider,
            )
            try:
                hits = gateway.search(
                    query_text,
                    source_class=source_class,
                    max_results=10,
                    interstitial=interstitial,
                )
            except BrowserDispatchError as exc:
                if exc.retryable:
                    raise _retryable(exc.error_class, exc.message) from exc
                raise _non_retryable(exc.error_class, exc.message) from exc
            evidence = gateway.last_search_evidence
            provider = evidence.provider if evidence else "unknown"
            path = evidence.path if evidence else None
            verification_url = evidence.verification_url if evidence else None
            if evidence is not None and not evidence.contract_ok:
                # An installed worker that predates the provider abstraction answers without
                # evidence fields: say so in the run record instead of inventing a provider.
                logger.warning(
                    "browser_research_search_contract_mismatch",
                    task_id=task_id,
                    schema_version=evidence.schema_version,
                    required=evidence.REQUIRED_SCHEMA_VERSION,
                )
            if evidence is not None:
                # Provider evidence is part of the durable run record (owner requirement):
                # requested vs actual provider, fallback and its reason, query, result count.
                with _session_factory()() as session:
                    runs_service.update_run(
                        session,
                        tid,
                        stage=STAGE_DISCOVERING,
                        event={
                            "stage": STAGE_DISCOVERING,
                            "detail": (
                                f"{query_id}: search requested_provider="
                                f"{evidence.requested_provider} provider={evidence.provider} "
                                f"fallback={str(evidence.fallback).lower()}"
                                + (
                                    f" reason={evidence.fallback_reason}"
                                    if evidence.fallback_reason
                                    else ""
                                )
                                + f" result_count={evidence.result_count}"
                                + (f" path={evidence.path}" if evidence.path else "")
                                + (
                                    f" CONTRACT MISMATCH: worker search schema "
                                    f"{evidence.schema_version} < "
                                    f"{evidence.REQUIRED_SCHEMA_VERSION}"
                                    if not evidence.contract_ok
                                    else ""
                                )
                            ),
                            "search": evidence.as_dict(),
                        },
                    )
                    session.commit()

            if evidence is not None and evidence.waiting_for_owner_verification:
                # spec §5a: the owner-handoff outcome. Record the stage/event
                # (naming the provider, the page kind and verification_url)
                # and surface verification_url on progress_json too, so the
                # web client (GET /v1/research/{id}) can show it without
                # scraping the event's free-text detail.
                with _session_factory()() as session:
                    runs_service.update_run(
                        session,
                        tid,
                        stage=STAGE_WAITING_FOR_OWNER_VERIFICATION,
                        progress={
                            "verification_url": verification_url,
                            "verification_provider": provider,
                        },
                        event={
                            "stage": STAGE_WAITING_FOR_OWNER_VERIFICATION,
                            "detail": (
                                f"{query_id}: sahibin doğrulaması bekleniyor "
                                f"provider={provider} page_kind={evidence.page_kind} "
                                f"verification_url={verification_url}"
                            ),
                        },
                    )
                    session.commit()
                return {
                    "status": "waiting",
                    "candidates": 0,
                    "path": path,
                    "verification_url": verification_url,
                }

            candidates = [
                discovery.DiscoveredCandidate(
                    url=h.url,
                    title=h.title,
                    publisher="",
                    discovered_by=f"browser_search:{provider}",
                    query_id=query_id,
                    published_hint=h.published_hint,
                )
                for h in hits
            ]
    except discovery.DiscoveryError as exc:
        logger.warning("browser_research_discovery_failed", task_id=task_id, error=str(exc))
        candidates = []

    factory = _session_factory()
    with factory() as session:
        inserted = runs_service.insert_candidates(session, tid, candidates)
        total = len(runs_service.list_candidates(session, tid))
        runs_service.update_run(
            session,
            tid,
            stage=STAGE_DISCOVERING,
            progress={"discovered": total, "verification_url": None},
            event={"stage": STAGE_DISCOVERING, "detail": f"{query_id}: +{inserted} candidates"},
        )
    return {"status": "done", "candidates": inserted, "path": path, "verification_url": None}


# URL shapes that are listing/search/tag pages rather than articles. The first live run
# spent its whole budget on such pages (a newspaper's "yapay zeka" search listing is
# discovered first by every engine); they carry no dated claim and mostly links.
_LISTING_PATH_MARKERS = (
    "/haberleri/",
    "/arama",
    "/search",
    "/tag/",
    "/tags/",
    "/etiket/",
    "/konu/",
    "/topics/",
    "/topic/",
    "/kategori/",
    "/category/",
    "/k/",
    "/keyword/",
)
_CLASS_PRIORITY = {"official": 0, "technical": 1, "academic": 2, "news": 3, "community": 4}


def _looks_like_listing(url: str) -> bool:
    from urllib.parse import urlsplit

    parts = urlsplit(url)
    path = (parts.path or "/").lower()
    if not path.endswith("/"):
        path += "/"
    if any(marker in path for marker in _LISTING_PATH_MARKERS):
        return True
    query = (parts.query or "").lower()
    return query.startswith("q=") or "&q=" in query or "search=" in query


def _prefetch_preference(candidate: Any, topic: str) -> tuple[int, float]:
    """How promising a candidate looks BEFORE it is fetched, from what discovery stored.

    Only the URL and the provider's date hint exist at this point (no title, no body), so this
    is a preference, not a verdict - the real gate runs after the fetch. It exists because the
    fetch budget is small: on 2026-09-04 twelve fetches were spent on pages that the gate would
    later reject (a ten-day-old model card, two unrelated arXiv papers), leaving nothing to
    synthesize from. Ordering by URL topicality and a within-window date hint spends the same
    budget on candidates that can actually answer the question.
    """
    hint = (getattr(candidate, "published_hint", "") or "").lower()
    recent_hint = any(
        marker in hint
        for marker in (
            "saat",
            "hour",
            "dakika",
            "minute",
            "gün önce",
            "day ago",
            "days ago",
            "dün",
            "gun once",
            "dun",
            "bugun",
            "yesterday",
            "today",
            "bugün",
        )
    )
    relevance = eligibility.topic_relevance(
        topic=topic, title="", excerpt="", url=str(getattr(candidate, "url", ""))
    )
    return (0 if recent_hint else 1, -relevance)


def select_fetch_order(candidates: list, max_sources: int, topic: str = "") -> list:
    """Order the pending candidates so the fetch budget covers every source class.

    Primary sources first (official > technical > academic > news > community), a
    per-class quota so one engine's early results cannot crowd out feeds, Hacker News
    and arXiv, listing/search pages deferred to the very end, and discovery order kept
    within a class (deterministic; the same rows always yield the same order).
    """
    by_class: dict[str, list] = {}
    deferred: list = []
    for c in candidates:
        if _looks_like_listing(c.url):
            deferred.append(c)
            continue
        by_class.setdefault(_class_for_query(c.query_id), []).append(c)
    if topic:
        for bucket in by_class.values():
            bucket.sort(key=lambda c: _prefetch_preference(c, topic))
    classes = sorted(by_class, key=lambda k: (_CLASS_PRIORITY.get(k, 9), k))
    ordered: list = []
    if classes and max_sources > 0:
        quota = max(1, -(-max_sources // len(classes)))  # ceiling division
        for k in classes:
            ordered.extend(by_class[k][:quota])
        for k in classes:
            ordered.extend(by_class[k][quota:])
    ordered.extend(deferred)
    seen: set = set()
    unique: list = []
    for c in ordered:
        if c.url in seen:
            continue
        seen.add(c.url)
        unique.append(c)
    return unique


@activity.defn(name="browser_research_fetch_targets")
def fetch_targets_activity(task_id: str, max_sources: int, topic: str = "") -> list[dict[str, str]]:
    """Candidate URLs not yet fetched, oldest-discovered first, capped at
    ``max_sources`` (the owner's budget for this run).

    Destination policy (finding HIGH-2) is enforced HERE too, not only in
    ``DeviceBrowserGateway.fetch_url`` — a candidate that fails it never
    becomes a fetch target at all, so it never costs a Temporal activity or a
    device command, and it is never retried (there is nothing to retry: the
    URL itself is refused, not a transient dispatch failure)."""
    task_id_var.set(task_id)
    tid = uuid.UUID(task_id)
    factory = _session_factory()
    with factory() as session:
        candidates = runs_service.list_candidates(session, tid)
        already = {r.url for r in runs_service.list_evidence(session, tid)}
        pending = [c for c in candidates if c.url not in already]
        pending = select_fetch_order(pending, max_sources, topic)
        targets = []
        for c in pending:
            try:
                validate_fetch_target(c.url)
            except DestinationPolicyError as exc:
                logger.warning(
                    "browser_research_fetch_target_rejected",
                    task_id=task_id,
                    url=c.url,
                    error=str(exc),
                )
                runs_service.update_run(
                    session,
                    tid,
                    stage=STAGE_FETCHING,
                    event={
                        "stage": STAGE_FETCHING,
                        "detail": f"target rejected {c.url}: security_scope_error",
                    },
                )
                continue
            targets.append(c)
            if len(targets) >= max_sources:
                break
    return [
        {"url": c.url, "query": c.query_id, "source_class": _class_for_query(c.query_id)}
        for c in targets
    ]


def _class_for_query(query_id: str) -> str:
    # query_id is formatted "<source_class>:<query index>" by the workflow.
    return query_id.split(":", 1)[0] if ":" in query_id else "unknown"


# ----------------------------------------------------------------- fetch(url)


@activity.defn(name="browser_research_fetch")
def fetch_activity(task_id: str, device_id: str, url: str, query: str, source_class: str) -> str:
    """Returns ``"fetched" | "duplicate"``; a website-level failure
    (``page_kind != ok``) is still a successful command and recorded as
    evidence — only a browser/transport error raises."""
    task_id_var.set(task_id)
    tid = uuid.UUID(task_id)
    attempt = _current_attempt()

    gateway = DeviceBrowserGateway(
        _command_client(), device_id=uuid.UUID(device_id), task_id=task_id
    )
    try:
        # tab="new" (spec §5a): fetch in a separate tab so the job's persistent
        # Google results tab stays loaded for the next discover_activity call.
        record = gateway.fetch_url(
            url, query=query, source_class=source_class, attempt=attempt, tab="new"
        )
    except BrowserDispatchError as exc:
        factory = _session_factory()
        with factory() as session:
            runs_service.update_run(
                session,
                tid,
                stage=STAGE_FETCHING,
                event={"stage": STAGE_FETCHING, "detail": f"fetch failed {url}: {exc.error_class}"},
            )
        if exc.error_class in RETRYABLE_ERROR_CLASSES:
            if activity.in_activity():
                activity.heartbeat()
            raise _retryable(exc.error_class, exc.message) from exc
        raise _non_retryable(exc.error_class, exc.message) from exc

    if not record.injection_suspected and is_injection_suspected(record.excerpt):
        record = dataclasses.replace(record, injection_suspected=True)

    factory = _session_factory()
    with factory() as session:
        _row, created = runs_service.upsert_evidence(
            session,
            tid,
            url,
            evidence_json=record.as_dict(),
            device_id=uuid.UUID(device_id),
            command_id=uuid.UUID(record.command_id) if record.command_id else None,
            injection_suspected=record.injection_suspected,
        )
        fetch_done = len(runs_service.list_evidence(session, tid))
        runs_service.update_run(
            session,
            tid,
            stage=STAGE_FETCHING,
            progress={"fetch_done": fetch_done},
            event={"stage": STAGE_FETCHING, "detail": f"fetched {url}"},
        )
    return "fetched" if created else "duplicate"


# --------------------------------------------------------- await_verification


@activity.defn(name="browser_research_await_verification")
def await_verification_activity(
    task_id: str, device_id: str, timeout_s: float, iteration: int
) -> dict[str, Any]:
    """One ``browser.wait for=verification_cleared`` poll, capped at 60s
    (spec §5a). The workflow calls this in a loop, decrementing its own
    ``interactive_wait_s`` budget by ``timeout_s`` each time, until it is
    satisfied or the budget runs out — never raises on a device/transport
    problem, it just reports ``satisfied: false`` so the workflow's loop
    treats it the same as "not cleared yet" and keeps going (or gives up and
    falls back once the budget is spent)."""
    task_id_var.set(task_id)
    heartbeat_fn = activity.heartbeat if activity.in_activity() else None
    if heartbeat_fn is not None:
        heartbeat_fn()
    gateway = DeviceBrowserGateway(
        _command_client(), device_id=uuid.UUID(device_id), task_id=task_id
    )
    try:
        result = gateway.await_verification(
            timeout_s=timeout_s, iteration=iteration, heartbeat=heartbeat_fn
        )
    except BrowserDispatchError as exc:
        logger.warning(
            "browser_research_await_verification_failed",
            task_id=task_id,
            error_class=exc.error_class,
            error=exc.message,
        )
        return {"satisfied": False, "url": None, "elapsed_ms": None}
    return result


# -------------------------------------------------------------------- rank


def _gate_admits(payload: Any) -> bool:
    """Whether the stored quality gate verdict lets this row be used as evidence.

    A row with no verdict is admitted: ranking has simply not judged it yet (or the row
    predates the gate), and refusing those would silently empty older runs. Only an
    explicit refusal keeps a page out.
    """
    gate = payload.get("gate") if isinstance(payload, dict) else None
    return not (isinstance(gate, dict) and gate.get("eligible") is False)


def _gate_rejections(rows: list[Any]) -> dict[str, int]:
    """Per-reason counts of the pages the gate refused, read back from the rows."""
    counts: dict[str, int] = {}
    for row in rows:
        payload = row.evidence_json if isinstance(row.evidence_json, dict) else {}
        gate = payload.get("gate")
        if not isinstance(gate, dict) or gate.get("eligible") is not False:
            continue
        reason = str(gate.get("reason") or "unknown")
        counts[reason] = counts.get(reason, 0) + 1
    return counts


def _load_evidence_with_quarantine(
    rows: list[Any], *, stage: str, require_id: bool = False
) -> tuple[list[EvidenceRecord], QuarantineLedger]:
    """Rebuild stored evidence rows, quarantining any that break their field contract.

    Fault isolation (owner incident, 2026-09-04): one malformed row must not end a research
    job. The offending row is left exactly as it is in the evidence store - nothing is
    rewritten or deleted - and only its identity and the violated field are recorded, so the
    run's event trail can say what was set aside and why.
    """
    records: list[EvidenceRecord] = []
    quarantine = QuarantineLedger(stage=stage)
    for row in rows:
        payload = row.evidence_json if isinstance(row.evidence_json, dict) else {}
        url = str(payload.get("url") or getattr(row, "url", "") or "unknown")
        if not _gate_admits(payload):
            continue
        # After ranking, a row without a citation id is one the latest ranking did not keep;
        # citing it would produce a source nothing can point at.
        if require_id and not str(payload.get("id") or "").strip():
            continue
        try:
            records.append(EvidenceRecord.from_dict(payload, stage=stage))
        except ContractViolation as violation:
            quarantine.record(violation, url=url)
            logger.warning("research_evidence_quarantined", **violation.as_dict())
        except (KeyError, TypeError, ValueError) as exc:
            quarantine.record_reason(
                entity="evidence_item", entity_id=url, reason=type(exc).__name__, url=url
            )
            logger.warning(
                "research_evidence_quarantined", entity_id=url, reason=type(exc).__name__
            )
    return records, quarantine


def _require_enough_evidence(
    records: list[EvidenceRecord], quarantine: QuarantineLedger, *, stage: str
) -> None:
    """Fail the run ONLY when quarantining left too little to answer the request."""
    if len(records) >= MIN_VALID_EVIDENCE or (records and quarantine.empty):
        return
    raise InsufficientValidEvidence(
        stage=stage,
        valid=len(records),
        required=MIN_VALID_EVIDENCE,
        quarantined=quarantine.entries,
    )


def _apply_quality_gate(
    records: list[EvidenceRecord],
    *,
    topic: str,
    window_start: str,
    window_end: str,
    stage: str,
    published_hints: dict[str, str] | None = None,
) -> tuple[list[EvidenceRecord], dict[str, int], list[dict[str, Any]]]:
    """Only evidence that can actually answer the request reaches ranking and synthesis.

    The owner's run of 2026-09-04 ranked a Granite page from ten days earlier, a Turkish
    article from outside the window, two unrelated arXiv papers (Catalan's constant, a halo
    profile) and OpenAI pages whose extracted title was the interstitial "Bir dakika
    lütfen...". Nothing in the pipeline had ever asked whether a fetched page was ON TOPIC,
    INSIDE THE WINDOW or REAL CONTENT - the ranker only scored what it was given.

    Every candidate is now judged by :mod:`app.research.eligibility` before it can become
    evidence, and every rejection is recorded with its reason (off_topic,
    outside_recency_window, date_uncertain, interstitial, duplicate_event,
    insufficient_content) so the counts appear in the run's evidence.
    """
    kept: list[EvidenceRecord] = []
    rejected: dict[str, int] = {}
    details: list[dict[str, Any]] = []
    seen_event_keys: list[str] = []
    for record in records:
        verdict = eligibility.evaluate_candidate(
            title=record.title,
            excerpt=record.excerpt,
            url=record.url,
            topic=topic,
            http_status=record.http_status,
            published_at=record.published_at.isoformat() if record.published_at else None,
            # The search provider's own wording ("2 gun once") is weaker than a machine
            # -readable date but far better than nothing: without it, every page whose
            # HTML omits article:published_time is date_uncertain and gets refused, which
            # would starve the report for a reason that has nothing to do with the page.
            published_hint=(published_hints or {}).get(record.url),
            retrieved_at=(record.retrieved_at or record.fetched_at).isoformat()
            if (record.retrieved_at or record.fetched_at)
            else None,
            window_start=window_start,
            window_end=window_end,
            publisher=record.publisher,
            existing_event_keys=tuple(seen_event_keys),
        )
        if verdict.eligible:
            kept.append(record)
            seen_event_keys.append(
                eligibility.duplicate_event_key(
                    title=record.title, url=record.url, publisher=record.publisher
                )
            )
            continue
        reason = verdict.reason or "off_topic"
        rejected[reason] = rejected.get(reason, 0) + 1
        entry = verdict.as_dict()
        entry.update({"url": record.url, "stage": stage})
        details.append(entry)
        logger.info("research_candidate_rejected", reason=reason, url=record.url)
    return kept, rejected, details


@activity.defn(name="browser_research_rank")
def rank_activity(
    task_id: str, topic: str, window_start_iso: str, window_end_iso: str
) -> dict[str, int]:
    task_id_var.set(task_id)
    tid = uuid.UUID(task_id)
    window_start = datetime.fromisoformat(window_start_iso)
    window_end = datetime.fromisoformat(window_end_iso)

    factory = _session_factory()
    with factory() as session:
        rows = runs_service.list_evidence(session, tid)
        records, quarantine = _load_evidence_with_quarantine(rows, stage=STAGE_RANKING)
        try:
            _require_enough_evidence(records, quarantine, stage=STAGE_RANKING)
        except InsufficientValidEvidence as exc:
            runs_service.update_run(
                session,
                tid,
                event={
                    "stage": STAGE_RANKING,
                    "detail": "insufficient valid evidence",
                    **exc.as_dict(),
                },
            )
            raise _non_retryable(ERROR_INSUFFICIENT_VALID_EVIDENCE, str(exc)) from exc
        published_hints = {
            c.url: c.published_hint
            for c in runs_service.list_candidates(session, tid)
            if getattr(c, "published_hint", None)
        }
        eligible, rejected, rejection_details = _apply_quality_gate(
            records,
            topic=topic,
            window_start=window_start_iso,
            window_end=window_end_iso,
            stage=STAGE_RANKING,
            published_hints=published_hints,
        )
        publish_ui(
            UiState.RESEARCHING,
            subsystem="research",
            intensity=0.7,
            task_id=task_id,
            status=STAGE_RANKING,
            label=topic[:64],
            metadata={"candidates": len(records), "kept": len(eligible)},
        )
        ranked = assign_evidence_ids(
            dedup_and_rank(eligible, topic=topic, window_start=window_start, window_end=window_end)
        )
        runs_service.update_evidence_ranking(session, tid, ranked)
        runs_service.clear_stale_evidence_ids(session, tid, {r.url for r in ranked})
        # The verdict is written onto the rows themselves so the later synthesis activity
        # cannot read a refused page back out of the store and cite it.
        runs_service.record_evidence_gate(
            session,
            tid,
            {
                **{
                    r.url: {"eligible": True, "reason": None, "stage": STAGE_RANKING}
                    for r in eligible
                },
                **{
                    d["url"]: {"eligible": False, "reason": d["reason"], "stage": STAGE_RANKING}
                    for d in rejection_details
                },
            },
        )
        event: dict[str, Any] = {
            "stage": STAGE_RANKING,
            "detail": f"{len(ranked)} evidence ranked, {sum(rejected.values())} rejected",
            "rejected": rejected,
            "rejected_examples": rejection_details[:10],
        }
        if not quarantine.empty:
            event.update(quarantine.summary())
            event["quarantine"] = quarantine.entries[:10]
        runs_service.update_run(
            session,
            tid,
            stage=STAGE_RANKING,
            progress={
                "evidence": len(ranked),
                "quarantined": len(quarantine),
                "rejected": sum(rejected.values()),
                # Persisted so the report can show WHY the web was thin, not just that it was.
                "rejected_by_reason": rejected,
            },
            event=event,
        )
        if rejected:
            # Ledger write is side-effect-safe: it must never fail the ranking
            # step itself (M16 track A).
            try:
                rejected_total = sum(rejected.values())
                ledger_service.record(
                    session,
                    ledger_service.ActivityEvent(
                        event_type=EVENT_TYPE_RESEARCH_QUALITY_GATE,
                        subsystem=SUBSYSTEM_RESEARCH,
                        action="quality_gate_rejected",
                        status=STATUS_INFO,
                        result=f"{rejected_total} rejected",
                        factual_summary=f"Kalite kapısı {rejected_total} sayfayı eledi.",
                        occurred_at=datetime.now(UTC),
                        research_job_id=tid,
                        evidence_refs=[{"kind": "research_run", "ref": str(tid)}],
                        detail_json={
                            "rejected": rejected,
                            "rejected_examples": rejection_details[:10],
                        },
                        source="live",
                        source_ref=f"research_runs:{tid}:quality_gate:attempt:{_current_attempt()}",
                    ),
                )
            except Exception as exc:  # noqa: BLE001 - see comment above
                logger.warning(
                    "ledger_record_failed", task_id=task_id, error=f"{type(exc).__name__}: {exc}"
                )
    return {
        "evidence": len(ranked),
        "deduplicated": len(eligible) - len(ranked),
        "quarantined": len(quarantine),
        "rejected": sum(rejected.values()),
    }


# --------------------------------------------------------------- synthesize


def _require_enough_findings(result: Any, provider_name: str) -> None:
    """The report's floor applies to every provider, including the deterministic one.

    Cardinality is a property of the answer, not of one parser: a model that returns valid
    JSON with two findings and a deterministic pass that can only build one from thin
    evidence are the same failure from the owner's side. Checking here - on the result -
    rather than only inside the response parser is what makes the ladder's last rung real.
    """
    findings = list(getattr(result, "findings", ()) or ())
    if len(findings) >= MIN_REPORT_FINDINGS:
        return
    raise InsufficientValidFindings(
        produced=len(findings),
        required=MIN_REPORT_FINDINGS,
        provider=provider_name,
    )


def _synthesize_with_fallback(
    provider: Any, topic: str, evidence: list[EvidenceRecord], *, recency_label: str, settings: Any
) -> tuple[Any, Any, list[dict[str, Any]]]:
    """Synthesize, and fall back to the deterministic provider when a model's output cannot be
    trusted.

    A model that answers a contract-bound field with prose (2026-09-04: ``importance`` carrying
    a Turkish sentence) has produced unusable output, not a reason to throw away a run whose
    discovery, fetching and ranking all succeeded. The offending output is rejected as a whole
    and the deterministic provider - which builds findings only from validated evidence -
    produces the report instead. The substitution is recorded, never silent.
    """
    attempts: list[dict[str, Any]] = []
    # 1. the configured provider
    for attempt in (1, 2):
        try:
            result = provider.synthesize(topic, evidence, recency_label=recency_label)
            _require_enough_findings(result, provider.name)
            if attempt > 1:
                logger.info("research_synthesis_retry_succeeded", provider=provider.name)
            return result, provider, attempts
        except (ContractViolation, InsufficientValidFindings, InsufficientValidEvidence) as exc:
            detail = exc.as_dict()
            attempts.append({"provider": provider.name, "attempt": attempt, **detail})
            logger.warning("research_synthesis_attempt_rejected", attempt=attempt, **detail)
            # 2. retry ONCE with the same validated evidence: the model is nondeterministic and
            #    the prompt states the schema, so a second pass often answers correctly.
            if attempt == 1 and provider.name != "deterministic":
                continue
            break

    # 3. deterministic, evidence-backed synthesis from the validated evidence only
    fallback_provider = resolve_synthesis_provider("deterministic", settings)
    if fallback_provider.name != provider.name:
        try:
            result = fallback_provider.synthesize(topic, evidence, recency_label=recency_label)
            _require_enough_findings(result, fallback_provider.name)
            logger.warning(
                "research_synthesis_fell_back",
                provider=provider.name,
                fallback=fallback_provider.name,
                attempts=len(attempts),
            )
            return result, fallback_provider, attempts
        except (ContractViolation, InsufficientValidFindings, InsufficientValidEvidence) as exc:
            attempts.append({"provider": fallback_provider.name, "attempt": 3, **exc.as_dict()})

    # 4. nothing defensible: fail honestly rather than invent findings
    raise InsufficientValidFindings(
        produced=0,
        required=MIN_REPORT_FINDINGS,
        provider=provider.name,
        quarantined=attempts,
    )


@activity.defn(name="browser_research_synthesize")
def synthesize_activity(
    task_id: str, topic: str, window_json: dict[str, Any], synthesis_name: str
) -> dict[str, Any]:
    task_id_var.set(task_id)
    tid = uuid.UUID(task_id)
    settings = get_settings()

    factory = _session_factory()
    with factory() as session:
        rows = runs_service.list_evidence(session, tid)
        candidate_count = len(runs_service.list_candidates(session, tid))
        rejected_by_reason = _gate_rejections(rows)
        ranked, quarantine = _load_evidence_with_quarantine(
            rows, stage=STAGE_SYNTHESIZING, require_id=True
        )
        try:
            _require_enough_evidence(ranked, quarantine, stage=STAGE_SYNTHESIZING)
        except InsufficientValidEvidence as exc:
            runs_service.update_run(
                session,
                tid,
                event={
                    "stage": STAGE_SYNTHESIZING,
                    "detail": "insufficient valid evidence",
                    **exc.as_dict(),
                },
            )
            raise _non_retryable(ERROR_INSUFFICIENT_VALID_EVIDENCE, str(exc)) from exc
        ranked.sort(key=lambda r: (r.rank or 9999, r.url))
        evidence_by_id = {e.id: e for e in ranked}
        primary_only = [e for e in ranked if not e.syndicated_of]

        provider = resolve_synthesis_provider(synthesis_name, settings)
        try:
            result, provider, synthesis_attempts = _synthesize_with_fallback(
                provider,
                topic,
                primary_only,
                recency_label=window_json["label"],
                settings=settings,
            )
        except InsufficientValidFindings as exc:
            runs_service.update_run(
                session,
                tid,
                event={
                    "stage": STAGE_SYNTHESIZING,
                    "detail": "no defensible findings could be produced",
                    **exc.as_dict(),
                },
            )
            raise _non_retryable(ERROR_INSUFFICIENT_VALID_FINDINGS, str(exc)) from exc
        synthesis_quarantine = (
            list(quarantine.entries)
            + list(getattr(result, "quarantined", ()))
            + list(synthesis_attempts)
        )

        stats = ReportStats(
            queries=0,
            discovered=candidate_count,
            fetched=len(rows),
            fetch_failed=0,
            deduplicated=len(rows) - len(ranked),
            evidence=len(ranked),
            truncated_fields=result.truncated_fields,
            rejected=sum(rejected_by_reason.values()),
            rejected_by_reason=rejected_by_reason,
        )
        if synthesis_quarantine:
            logger.info(
                "research_quarantine_summary",
                stage=STAGE_SYNTHESIZING,
                quarantined=len(synthesis_quarantine),
            )
        report = ResearchReport(
            task_id=task_id,
            topic=topic,
            # window_json is plan["recency"] (RecencyWindow.as_dict()): start/
            # end/label/amount/unit. ReportWindow only carries start/end/label.
            window=ReportWindow(
                start=window_json["start"], end=window_json["end"], label=window_json["label"]
            ),
            generated_at=datetime.now(UTC).isoformat(),
            synthesis_provider=provider.name,
            executive_summary=result.executive_summary,
            findings=result.findings,
            why_it_matters=result.why_it_matters,
            watch_next=result.watch_next,
            details=result.details,
            uncertainty=result.uncertainty,
            sources=tuple(SourceItem.from_evidence(e.id, e) for e in ranked),
            stats=stats,
        )
        report = run_provenance_gate(report, evidence_by_id)

        runs_service.upsert_report(
            session, tid, report_json=report.as_dict(), synthesis_provider=provider.name
        )
        runs_service.update_run(
            session,
            tid,
            stage=STAGE_SYNTHESIZING,
            event={"stage": STAGE_SYNTHESIZING, "detail": f"synthesized via {provider.name}"},
        )
    logger.info("browser_research_synthesized", task_id=task_id, provider=provider.name)
    return report.as_dict()


# ---------------------------------------------------------------- persist


def _artifact_title(topic: str) -> str:
    return f"Araştırma Raporu: {topic.strip()}"


@activity.defn(name="browser_research_persist_artifact")
def persist_artifact_activity(task_id: str, topic: str) -> dict[str, Any]:
    task_id_var.set(task_id)
    tid = uuid.UUID(task_id)
    settings = get_settings()

    factory = _session_factory()
    with factory() as session:
        report_row = runs_service.get_report(session, tid)
        if report_row is None:
            raise _non_retryable("internal_bug", f"no synthesized report for task {task_id}")
        report_json = dict(report_row.report_json)
        synthesis_provider = report_row.synthesis_provider

    markdown = render_research_markdown(_report_from_json(report_json))
    body_hash = content_hash(markdown.encode("utf-8"))

    af_factory, store = build_artifact_context(settings)
    with af_factory() as session:
        artifact = artifact_service.get_or_create_artifact_for_task(
            session,
            task_id=tid,
            title=_artifact_title(topic),
            kind=ARTIFACT_KIND_RESEARCH_REPORT,
        )
        version = artifact_service.add_artifact_version(
            session,
            artifact_id=artifact.id,
            canonical_body=markdown,
            content_hash=body_hash,
            source_manifest=report_json,
        )
        artifact_service.set_executive_summary(
            session, artifact.id, report_json.get("executive_summary", "")
        )
        if artifact.state != ARTIFACT_STATE_CANONICAL_READY:
            artifact_service.set_artifact_state(
                session, artifact.id, ARTIFACT_STATE_CANONICAL_READY
            )
        rows = render_store.ensure_renders(
            session,
            store,
            version=version,
            title=artifact.title,
            formats=DEFAULT_RENDER_FORMATS,
        )
        if artifact.state != ARTIFACT_STATE_RENDERS_PENDING:
            artifact_service.set_artifact_state(
                session, artifact.id, ARTIFACT_STATE_RENDERS_PENDING
            )
        if artifact.state != ARTIFACT_STATE_READY:
            artifact_service.set_artifact_state(session, artifact.id, ARTIFACT_STATE_READY)
        artifact_id = artifact.id
        version_no = version.version

    with factory() as session:
        runs_service.upsert_report(
            session,
            tid,
            report_json=report_json,
            synthesis_provider=synthesis_provider,
            artifact_id=artifact_id,
        )
        _transition_task(session, tid, TASK_STATUS_RENDERING)
        _transition_task(session, tid, TASK_STATUS_READY)
        runs_service.update_run(
            session,
            tid,
            stage=STAGE_PERSISTING,
            event={"stage": STAGE_PERSISTING, "detail": f"artifact {artifact_id} v{version_no}"},
        )
        # Ledger write is side-effect-safe: a failure here must never fail the
        # (already-persisted) artifact/task transitions above (M16 track A).
        try:
            ledger_event = ledger_service.record(
                session,
                ledger_service.build_research_completed_event(
                    task_id=tid,
                    occurred_at=datetime.now(UTC),
                    report_json=report_json,
                    artifact_id=artifact_id,
                    source="live",
                    source_ref=f"research_runs:{tid}:ready",
                ),
            )
            ledger_briefing.queue_briefing(session, ledger_event)
        except Exception as exc:  # noqa: BLE001 - see comment above
            logger.warning(
                "ledger_record_failed", task_id=task_id, error=f"{type(exc).__name__}: {exc}"
            )
    logger.info(
        "browser_research_artifact_persisted", task_id=task_id, artifact_id=str(artifact_id)
    )
    return {"artifact_id": str(artifact_id), "version": version_no, "render_count": len(rows)}


# ------------------------------------------------------------------ remember


def _finding_memory_entry(
    finding: dict[str, Any], source_by_id: dict[str, Any], *, synthesis_provider: str
) -> dict[str, Any]:
    """Everything a finding may contribute to episodic memory (spec §7,
    ADR-0050 §6/§7, memory-boundary review finding CRITICAL-1). Title/label/
    importance/evidence_urls are always structured, provider-independent
    data — never raw page text. ``summary`` is the one field that started
    life as (or could still carry) untrusted page text, so it is included
    ONLY when every one of these holds:

    - the synthesis provider is not ``deterministic`` (that provider's
      summary is now provenance-only text, but the memory boundary must not
      depend on which provider ran — it is enforced on the TEXT, not on
      trusting a particular provider's output shape);
    - none of the evidence this finding cites is ``injection_suspected``;
    - the summary text itself carries no injection markers
      (``app.research.injection.count_markers``).

    Otherwise the ``summary`` key is omitted entirely — never truncated,
    never replaced with a placeholder, simply not written."""
    evidence_ids = finding.get("evidence_ids", [])
    entry: dict[str, Any] = {
        "title": finding["title"],
        "label": finding["label"],
        "importance": finding.get("importance"),
        "evidence_urls": [source_by_id[eid]["url"] for eid in evidence_ids if eid in source_by_id],
    }
    summary = finding.get("summary", "")
    cited_evidence_suspected = any(
        source_by_id.get(eid, {}).get("injection_suspected") for eid in evidence_ids
    )
    if (
        synthesis_provider != "deterministic"
        and not cited_evidence_suspected
        and count_markers(summary) == 0
    ):
        entry["summary"] = summary
    return entry


@activity.defn(name="browser_research_remember")
def remember_activity(task_id: str, topic: str) -> str | None:
    task_id_var.set(task_id)
    tid = uuid.UUID(task_id)
    settings = get_settings()

    factory = _session_factory()
    with factory() as session:
        report_row = runs_service.get_report(session, tid)
        if report_row is None:
            return None
        report_json = report_row.report_json

    mem_engine = build_engine(settings.database_url)
    mem_factory = build_session_factory(mem_engine)
    embedder = DeterministicEmbedder()
    synthesis_provider = report_json.get("synthesis_provider", "deterministic")
    with mem_factory() as session:
        source_by_id = {s["id"]: s for s in report_json.get("sources", [])}
        value = {
            "question": topic,
            "window": report_json.get("window"),
            "generated_at": report_json.get("generated_at"),
            "findings": [
                _finding_memory_entry(f, source_by_id, synthesis_provider=synthesis_provider)
                for f in report_json.get("findings", [])
            ],
            "sources": [
                {
                    "url": _stored(s, "url", entity="report_source"),
                    "title": _stored(s, "title", entity="report_source"),
                    "publisher": s["publisher"],
                    "published_at": s["published_at"],
                }
                for s in report_json.get("sources", [])
            ],
            "implications": [s["text"] for s in report_json.get("why_it_matters", [])],
            "owner_feedback": None,
            "artifact_id": str(report_row.artifact_id) if report_row.artifact_id else None,
        }
        result = remember_explicit(
            session,
            embedder,
            text=f"Araştırma tamamlandı: {topic}",
            memory_class=MemoryClass.EPISODIC,
            key=f"research:{task_id}",
            value=value,
            source={"kind": "research", "task_id": task_id},
        )
        memory_id = result.memory_id

    if memory_id is not None:
        with factory() as session:
            runs_service.set_report_memory(session, tid, memory_id)
            runs_service.update_run(
                session,
                tid,
                stage=STAGE_READY,
                event={"stage": STAGE_READY, "detail": f"memory {memory_id}"},
            )
    return str(memory_id) if memory_id else None


# --------------------------------------------------------------- close_session


@activity.defn(name="browser_research_close_session")
def close_session_activity(task_id: str, device_id: str) -> bool:
    task_id_var.set(task_id)
    gateway = DeviceBrowserGateway(
        _command_client(), device_id=uuid.UUID(device_id), task_id=task_id
    )
    gateway._session_opened = True  # best-effort close regardless of local tracking
    try:
        gateway.close_session()
    except Exception as exc:  # noqa: BLE001 - close is best-effort (spec §5)
        logger.warning("browser_research_close_session_failed", task_id=task_id, error=str(exc))
        return False
    return True


@activity.defn(name="browser_research_fail_run")
def fail_run_activity(task_id: str, error_class: str, detail: str) -> bool:
    """Record a terminal failure of the run (stage failed, task FAILED, one event).

    Seen live: when synthesis exhausted its retries the workflow failed inside Temporal
    but the run row kept its last stage, so the owner's status endpoint showed
    "ranking" forever. Idempotent: a run already failed/ready is left as it is.
    """
    tid = uuid.UUID(task_id)
    with _session_factory()() as session:
        run = runs_service.get_run(session, tid)
        if run is not None and run.stage in (STAGE_FAILED, STAGE_READY):
            return False
        task = artifact_service.get_task(session, tid)
        if task is not None and task.status not in (TASK_STATUS_FAILED_TERMINAL,):
            try:
                _transition_task(
                    session,
                    tid,
                    TASK_STATUS_FAILED_TERMINAL,
                    error_class=error_class,
                    error_message=detail[:2000],
                )
            except Exception:  # noqa: BLE001 - an illegal transition must not mask the failure
                pass
        runs_service.update_run(
            session,
            tid,
            stage=STAGE_FAILED,
            error=detail[:2000],
            event={"stage": STAGE_FAILED, "detail": f"{error_class}: {detail[:300]}"},
        )
        session.commit()
        # Ledger write is side-effect-safe: a failure here must never mask the
        # research run's own (already-committed) terminal failure (M16 track A).
        try:
            ledger_event = ledger_service.record(
                session,
                ledger_service.build_research_failed_event(
                    task_id=tid,
                    occurred_at=datetime.now(UTC),
                    error_class=error_class,
                    error=detail,
                    source="live",
                    source_ref=f"research_runs:{tid}:failed",
                ),
            )
            ledger_briefing.queue_briefing(session, ledger_event)
        except Exception as exc:  # noqa: BLE001 - see comment above
            logger.warning(
                "ledger_record_failed", task_id=task_id, error=f"{type(exc).__name__}: {exc}"
            )
    return True


BROWSER_RESEARCH_ACTIVITIES = (
    plan_activity,
    select_device_activity,
    discover_activity,
    await_verification_activity,
    fetch_targets_activity,
    fetch_activity,
    rank_activity,
    synthesize_activity,
    persist_artifact_activity,
    remember_activity,
    close_session_activity,
    fail_run_activity,
)

__all__ = [
    "BROWSER_RESEARCH_ACTIVITIES",
    "await_verification_activity",
    "close_session_activity",
    "discover_activity",
    "fetch_activity",
    "fetch_targets_activity",
    "persist_artifact_activity",
    "plan_activity",
    "rank_activity",
    "remember_activity",
    "select_device_activity",
    "synthesize_activity",
]
