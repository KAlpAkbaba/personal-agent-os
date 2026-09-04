"""M16 track A Activity Ledger REST surface (M16_ACTIVITY_LEDGER_SPEC.md §1, §4).

- GET  /v1/ledger/policy             vocabulary + version this Cloud Core understands
- GET  /v1/ledger/events             newest-first, filterable
- GET  /v1/ledger/latest             most recent completed/failed event
- GET  /v1/ledger/summary            counts by status/subsystem + latest, since a window
- POST /v1/ledger/events             owner scripts append a live event (deployment evidence)
- POST /v1/ledger/backfill           re-derive events from canonical tables on demand
- GET  /v1/ledger/briefings/pending  undelivered, unexpired proactive briefings

Owner-gated like every other surface (research, artifacts, memory, devices).
All DB work runs in a thread (sync SQLAlchemy), reusing ``app.state.artifacts``
as the generic DB session source the way ``app.research.routes`` and
``app.mobile.routes`` already do.
"""

from __future__ import annotations

import asyncio
import json
import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.artifacts.runtime import ArtifactRuntime
from app.identity.dependencies import require_owner_session
from app.ledger import briefing as briefing_service
from app.ledger import service as ledger_service
from app.ledger.models import ActivityEventRow, PendingBriefingRow
from app.ledger.vocabulary import (
    EVENT_TYPES,
    EVOLUTION_EVENT_TYPES,
    PRODUCTION_STATE_NA,
    SEVERITIES,
    STATUSES,
    SUBSYSTEMS,
    InvalidVocabulary,
    validate_event_type,
    validate_production_state,
    validate_severity,
    validate_status,
    validate_subsystem,
)
from app.logging import get_logger
from app.research.injection import is_assistant_directed, is_injection_suspected
from app.voice.realtime_sessions.service import is_forbidden_key

logger = get_logger("app.ledger.routes")

router = APIRouter(prefix="/v1/ledger", dependencies=[Depends(require_owner_session)])

MAX_LIMIT = 200
#: Serialized bound for a posted ``detail_json``: evidence counts and identifiers, not
#: documents.
MAX_DETAIL_BYTES = 16 * 1024
#: Only these subsystems may post a ``critical`` event through the API. ``critical`` maps
#: to the ``immediate`` briefing policy - spoken to the owner unasked - so an arbitrary
#: owner-session caller must not be able to force it (security review, 2026-09-04).
CRITICAL_POSTING_SUBSYSTEMS = frozenset({"deployment", "security", "cloud_core", "device_service"})


#: Phrasings the shared browser marker set (packages/protocol, frozen with the installed
#: worker) does not cover but a spoken briefing must still refuse.
_SPOKEN_TEXT_MARKERS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"ignore (?:all |any |the )?(?:previous|prior|earlier|above) (?:instructions|rules)",
        r"disregard (?:all |any |the )?(?:previous|prior|earlier|above) (?:instructions|rules)",
        r"(?:önceki|onceki|yukarıdaki|yukaridaki) talimat",
        r"talimatlar[ıi] (?:yok say|unut|görmezden gel|gormezden gel)",
        r"you are now",
        r"sahibine (?:söyle|soyle) ki",
    )
)


def _screened_text(value: str, *, where: str) -> str:
    """Text that will be SPOKEN to the owner verbatim must not carry instructions.

    The research pipeline already refuses assistant-directed content at its boundary; a
    ledger event posted through the API is the same class of untrusted text once the
    Self Explanation engine reads it aloud, so it meets the same screen.
    """
    if (
        is_injection_suspected(value)
        or is_assistant_directed(value)
        or any(marker.search(value) for marker in _SPOKEN_TEXT_MARKERS)
    ):
        raise ValueError(f"{where} carries instruction-shaped text and was refused")
    return value


def _screen_strings(value: Any, *, where: str, depth: int = 0) -> None:
    if depth > 8:
        raise ValueError(f"{where} nests too deeply")
    if isinstance(value, str):
        _screened_text(value, where=where)
    elif isinstance(value, dict):
        for inner in value.values():
            _screen_strings(inner, where=where, depth=depth + 1)
    elif isinstance(value, list | tuple):
        for inner in value:
            _screen_strings(inner, where=where, depth=depth + 1)


#: GET /summary default window when the caller does not pass ``since``.
DEFAULT_SUMMARY_WINDOW = timedelta(days=1)


def _artifacts(request: Request) -> ArtifactRuntime:
    return request.app.state.artifacts


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _event_dict(row: ActivityEventRow) -> dict[str, Any]:
    return {
        "event_id": str(row.event_id),
        "occurred_at": _iso(row.occurred_at),
        "recorded_at": _iso(row.recorded_at),
        "event_type": row.event_type,
        "subsystem": row.subsystem,
        "module": row.module,
        "version": row.version,
        "status": row.status,
        "severity": row.severity,
        "action": row.action,
        "result": row.result,
        "production_state": row.production_state,
        "command_id": str(row.command_id) if row.command_id else None,
        "trace_id": row.trace_id,
        "research_job_id": str(row.research_job_id) if row.research_job_id else None,
        "browser_session_id": row.browser_session_id,
        "related_goal_id": str(row.related_goal_id) if row.related_goal_id else None,
        "related_module_id": row.related_module_id,
        "evidence_refs": row.evidence_refs,
        "factual_summary": row.factual_summary,
        "detail_json": row.detail_json,
        "source": row.source,
        "source_ref": row.source_ref,
    }


def _briefing_dict(row: PendingBriefingRow) -> dict[str, Any]:
    return {
        "briefing_id": str(row.briefing_id),
        "created_at": _iso(row.created_at),
        "policy": row.policy,
        "priority": row.priority,
        "speech": row.speech,
        "event_ids": row.event_ids,
        "delivered_at": _iso(row.delivered_at),
        "delivered_via": row.delivered_via,
        "expires_at": _iso(row.expires_at),
    }


def _no_forbidden_keys(value: Any, *, where: str) -> None:
    """Same blocklist as the realtime-voice surface's own scrubber/validator
    (``app.voice.realtime_sessions.service.is_forbidden_key``): a payload key
    that normalizes to text/transcript/audio/secret/token/credential/password
    is refused outright, never stored."""
    if isinstance(value, dict):
        for key, inner in value.items():
            if is_forbidden_key(key):
                raise ValueError(
                    f"{where} must not carry text/transcript/audio/credential-shaped keys ({key!r})"
                )
            _no_forbidden_keys(inner, where=where)
    elif isinstance(value, list):
        for inner in value:
            _no_forbidden_keys(inner, where=where)


class EvidenceRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str = Field(min_length=1, max_length=64)
    ref: str = Field(min_length=1, max_length=256)
    digest: str | None = Field(default=None, max_length=128)


class CreateEventRequest(BaseModel):
    """Body for ``POST /v1/ledger/events``. ``source`` is never accepted from
    the caller — the route forces it to ``"live"`` (spec §1.1)."""

    model_config = ConfigDict(extra="forbid")

    event_type: str
    subsystem: str
    action: str = Field(min_length=1, max_length=128)
    factual_summary: str = Field(min_length=1, max_length=2000)
    source_ref: str = Field(min_length=1, max_length=256)
    status: str = "completed"
    severity: str = "info"
    result: str | None = Field(default=None, max_length=256)
    production_state: str = PRODUCTION_STATE_NA
    module: str | None = Field(default=None, max_length=128)
    version: str | None = Field(default=None, max_length=32)
    occurred_at: datetime | None = None
    trace_id: str | None = Field(default=None, max_length=128)
    research_job_id: uuid.UUID | None = None
    browser_session_id: str | None = Field(default=None, max_length=128)
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    detail_json: dict[str, Any] = Field(default_factory=dict)

    @field_validator("event_type")
    @classmethod
    def _valid_event_type(cls, value: str) -> str:
        try:
            return validate_event_type(value)
        except InvalidVocabulary as exc:
            raise ValueError(str(exc)) from exc

    @field_validator("factual_summary")
    @classmethod
    def _screened_summary(cls, value: str) -> str:
        return _screened_text(value, where="factual_summary")

    @model_validator(mode="after")
    def _critical_only_from_trusted_subsystems(self) -> CreateEventRequest:
        if self.severity == "critical" and self.subsystem not in CRITICAL_POSTING_SUBSYSTEMS:
            raise ValueError(
                "severity 'critical' may be posted only by "
                + ", ".join(sorted(CRITICAL_POSTING_SUBSYSTEMS))
            )
        return self

    @field_validator("subsystem")
    @classmethod
    def _valid_subsystem(cls, value: str) -> str:
        try:
            return validate_subsystem(value)
        except InvalidVocabulary as exc:
            raise ValueError(str(exc)) from exc

    @field_validator("status")
    @classmethod
    def _valid_status(cls, value: str) -> str:
        try:
            return validate_status(value)
        except InvalidVocabulary as exc:
            raise ValueError(str(exc)) from exc

    @field_validator("severity")
    @classmethod
    def _valid_severity(cls, value: str) -> str:
        try:
            return validate_severity(value)
        except InvalidVocabulary as exc:
            raise ValueError(str(exc)) from exc

    @field_validator("production_state")
    @classmethod
    def _valid_production_state(cls, value: str) -> str:
        try:
            return validate_production_state(value)
        except InvalidVocabulary as exc:
            raise ValueError(str(exc)) from exc

    @field_validator("detail_json")
    @classmethod
    def _no_forbidden_detail(cls, value: dict[str, Any]) -> dict[str, Any]:
        _no_forbidden_keys(value, where="detail_json")
        encoded = json.dumps(value, ensure_ascii=False, default=str)
        if len(encoded.encode("utf-8")) > MAX_DETAIL_BYTES:
            raise ValueError(f"detail_json exceeds {MAX_DETAIL_BYTES} bytes")
        _screen_strings(value, where="detail_json")
        return value


#: Bumped whenever the ledger's tables, vocabulary or routes change in a way an owner
#: script must know about; the owner commands compare it with the deployed Cloud Core.
#: 2 (2026-09-04): narration budgets, owner-relevance ranking, normalised level intents,
#: client-owned interruption. The owner command releases the Cloud Core once for it.
LEDGER_VERSION = 2


@router.get("/policy")
async def get_ledger_policy() -> dict[str, Any]:
    """Side-effect free. An owner script (release/deploy tooling) probes this
    to decide whether a Cloud Core release is needed before it can safely
    write ``deployment.*`` evidence — mirrors ``GET /v1/research/policy``."""
    return {
        "ledger_version": LEDGER_VERSION,
        "event_types": sorted(EVENT_TYPES),
        "evolution_event_types": sorted(EVOLUTION_EVENT_TYPES),
        "subsystems": sorted(SUBSYSTEMS),
        "statuses": sorted(STATUSES),
        "severities": sorted(SEVERITIES),
    }


@router.get("/events")
async def list_events(
    request: Request,
    since: datetime | None = None,
    until: datetime | None = None,
    subsystem: str | None = None,
    status: str | None = None,
    event_type: str | None = None,
    research_job_id: uuid.UUID | None = None,
    limit: int = Query(default=50, ge=1, le=MAX_LIMIT),
) -> dict[str, Any]:
    artifacts = _artifacts(request)

    def load() -> list[ActivityEventRow]:
        with artifacts.session() as session:
            return ledger_service.query(
                session,
                since=since,
                until=until,
                subsystems=[subsystem] if subsystem else None,
                statuses=[status] if status else None,
                event_types=[event_type] if event_type else None,
                research_job_id=research_job_id,
                limit=limit,
            )

    rows = await asyncio.to_thread(load)
    return {"events": [_event_dict(row) for row in rows]}


@router.get("/latest")
async def get_latest_event(request: Request, subsystem: str | None = None) -> dict[str, Any]:
    artifacts = _artifacts(request)

    def load() -> ActivityEventRow | None:
        with artifacts.session() as session:
            return ledger_service.latest(session, subsystems=[subsystem] if subsystem else None)

    row = await asyncio.to_thread(load)
    return {"event": _event_dict(row) if row else None}


@router.get("/summary")
async def get_summary(request: Request, since: datetime | None = None) -> dict[str, Any]:
    artifacts = _artifacts(request)
    window_start = since or (datetime.now(UTC) - DEFAULT_SUMMARY_WINDOW)

    def load() -> dict[str, Any]:
        with artifacts.session() as session:
            by_status = ledger_service.count_by_status(session, window_start)
            by_subsystem = ledger_service.count_by_subsystem(session, window_start)
            latest_row = ledger_service.latest(session)
            return {
                "since": _iso(window_start),
                "by_status": by_status,
                "by_subsystem": by_subsystem,
                "latest": _event_dict(latest_row) if latest_row else None,
            }

    return await asyncio.to_thread(load)


@router.post("/events", status_code=201)
async def create_event(request: Request, body: CreateEventRequest) -> dict[str, Any]:
    """Owner scripts (release/deployment evidence) append a live event here.
    ``source`` is always forced to ``"live"``; ``source_ref`` is the caller's
    own idempotency key (spec §1.2 writer table: deployment)."""
    artifacts = _artifacts(request)

    def write() -> ActivityEventRow:
        with artifacts.session() as session:
            return ledger_service.record(
                session,
                ledger_service.ActivityEvent(
                    event_type=body.event_type,
                    subsystem=body.subsystem,
                    action=body.action,
                    factual_summary=body.factual_summary,
                    source="live",
                    source_ref=body.source_ref,
                    status=body.status,
                    severity=body.severity,
                    result=body.result,
                    production_state=body.production_state,
                    module=body.module,
                    version=body.version,
                    occurred_at=body.occurred_at,
                    trace_id=body.trace_id,
                    research_job_id=body.research_job_id,
                    browser_session_id=body.browser_session_id,
                    evidence_refs=[ref.model_dump(exclude_none=True) for ref in body.evidence_refs],
                    detail_json=body.detail_json,
                ),
            )

    row = await asyncio.to_thread(write)
    return _event_dict(row)


@router.post("/backfill")
async def run_backfill(request: Request) -> dict[str, Any]:
    artifacts = _artifacts(request)

    def run() -> dict[str, Any]:
        with artifacts.session() as session:
            return ledger_service.backfill(session).as_dict()

    return await asyncio.to_thread(run)


@router.get("/briefings/pending")
async def list_pending_briefings(request: Request) -> dict[str, Any]:
    artifacts = _artifacts(request)

    def load() -> list[PendingBriefingRow]:
        with artifacts.session() as session:
            return briefing_service.pending(session)

    rows = await asyncio.to_thread(load)
    return {"briefings": [_briefing_dict(row) for row in rows]}
