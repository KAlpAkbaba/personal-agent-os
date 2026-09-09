"""``CreativeService`` (docs/M27_CREATIVE_TOOLS_SPEC.md §2-§7, ADR-0093): the owner's
rule in one line — "a creative edit is planned as data, executed through the most
structured interface the installed application really offers, exported, reopened by an
independent reader and compared with what was asked — and an application that is not
installed or not licensed is named as such, never imitated."

Mirrors ``app.creative3d.service.SceneService``'s own shape (a provider/device call, an
``ActionReceipt`` per call, a ledger row) — the same "write -> read-back -> speak"
discipline every mutating capability in this codebase follows
(docs/M18_ACTION_CONTRACT.md §5.5).

Scope note for this Cloud Core half (recorded plainly rather than overclaimed): Paint is
the one provider this service actually EXECUTES, because it is the one REAL provider on
this machine today (ADR-0093 decision 1 — the document model IS the bitmap, driven
entirely by Pillow, :mod:`app.creative.execute`). Photoshop/Illustrator/Figma ship
complete DETECTION and an honest ``dependency_unavailable`` refusal carrying the
detection facts (:mod:`app.creative.providers`); wiring their own fixed, pinned drivers
is real work for the day the owner's licence/token makes them installed — "the same lab
runs the real application the day they exist, with no code change" (spec §6) describes
that day, not this one. Reading/writing the owner's OWN LIVE device filesystem (a Paint
edit on a real file already sitting on their machine) is windows-engineer-track work,
also not yet wired here — a plan whose ``source`` names an object-store key (an
already-stored artifact/generation) works end to end today; a bare device path answers
``dependency_unavailable`` honestly rather than being imitated.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.actions.receipt import (
    EXECUTION_EXECUTED,
    EXECUTION_REFUSED,
    TERMINAL_FAILED,
    TERMINAL_UNVERIFIED,
    TERMINAL_VERIFIED,
    ActionReceipt,
    record_receipt,
)
from app.creative.compare import NO_CONSTRAINTS as COMPARE_NO_CONSTRAINTS
from app.creative.compare import CompareResult, compare
from app.creative.execute import ExecutionError, ExecutionResult, execute
from app.creative.models import (
    MAX_SELF_CORRECTION_ROUNDS,
    STATE_APPLIED,
    STATE_DEPENDENCY_UNAVAILABLE,
    STATE_FAILED,
    STATE_MISMATCH,
    STATE_PLANNED,
    STATE_UNVERIFIED,
    STATE_VERIFIED,
    CreativeRunRow,
    wire_step,
)
from app.creative.providers import CreativeProvider, ProviderResult, default_providers
from app.creative.spec import TOOL_PAINT, CreativePlan
from app.ledger import service as ledger_service
from app.ledger.vocabulary import (
    EVENT_TYPE_CREATIVE_APPLIED,
    EVENT_TYPE_CREATIVE_CREATED,
    EVENT_TYPE_CREATIVE_DEPENDENCY_UNAVAILABLE,
    EVENT_TYPE_CREATIVE_FAILED,
    EVENT_TYPE_CREATIVE_LISTED,
    EVENT_TYPE_CREATIVE_MISMATCH,
    EVENT_TYPE_CREATIVE_VERIFIED,
    SUBSYSTEM_CREATIVE,
)
from app.logging import get_logger
from app.object_store import ObjectStore, validate_object_key
from app.operator import focus as focus_module
from app.operator.models import FOCUS_KIND_CREATIVE
from app.uistate import UiState
from app.uistate import publish as publish_ui_state
from app.uistate.contract import (
    CREATIVE_ACTIVITY_STEPS,
    CREATIVE_STEP_COMPARING,
    CREATIVE_STEP_CORRECTING,
    CREATIVE_STEP_EXECUTING,
    CREATIVE_STEP_FAILED,
)

logger = get_logger("app.creative.service")

ERROR_CAPABILITY_MISSING = "capability_missing"
ERROR_VALIDATION = "validation_error"
ERROR_DEPENDENCY_UNAVAILABLE = "dependency_unavailable"

SPEECH_NO_RUN = "Hangi çalışma efendim?"
SPEECH_INVALID_PLAN = "Bu düzenleme isteğini işleyemedim efendim."
#: A plan that asks for nothing checkable is not verified and is not a disagreement.
SPEECH_NOTHING_TO_CHECK = "Doğrulanacak bir şey yoktu efendim."

#: Owner-facing tool names, for the honest refusal sentence (spec §5's own example:
#: "Photoshop bu bilgisayarda kurulu değil; Paint'te açayım mı?").
_TOOL_TR: dict[str, str] = {
    "paint": "Paint",
    "photoshop": "Photoshop",
    "illustrator": "Illustrator",
    "figma": "Figma",
}

#: Two creative runs at once is this service's own bound — the same "a small, named
#: ceiling on concurrent work" discipline ``app.creative3d.service.MAX_ACTIVE_SCENES``
#: already applies for its own family.
MAX_ACTIVE_RUNS = 2

_ACTIVE_STATES: tuple[str, ...] = (STATE_PLANNED, STATE_APPLIED, STATE_VERIFIED, STATE_UNVERIFIED)


def _op_capability(op_name: str) -> str:
    """Every operation name in the closed vocabulary IS its own capability name
    (``app.creative.spec.OPERATIONS`` and ``app.creative.providers.ALL_CAPABILITIES``
    are the same tuple) — spelled here as its own function so a future divergence
    between the two has exactly one place to fix."""
    return op_name


class CreativeService:
    def __init__(
        self,
        object_store: ObjectStore | None = None,
        providers: dict[str, CreativeProvider] | None = None,
    ) -> None:
        self._object_store = object_store
        self._providers = providers if providers is not None else default_providers()

    # ------------------------------------------------------------- receipts / ledger

    def _receipt(
        self,
        *,
        capability: str,
        requested_state: str,
        execution: str,
        terminal: str,
        server: dict[str, Any],
        speech: str,
        db: Session | None = None,
        error_class: str | None = None,
        session_id: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        now = datetime.now(UTC)
        receipt = ActionReceipt(
            action_id=str(uuid.uuid4()),
            capability=capability,
            requested_state=requested_state,
            execution_status=execution,
            terminal_status=terminal,
            observed_after={"server": server, "local": {}},
            evidence_refs=[],
            error_class=error_class,
            speech=speech,
            started_at=now,
            completed_at=now,
            session_id=session_id,
            observed_at=now,
        )
        if db is not None:
            record_receipt(db, receipt, SUBSYSTEM_CREATIVE)
        out = receipt.as_dict()
        if extra:
            out.update(extra)
        return out

    def _ledger(
        self,
        db: Session | None,
        *,
        event_type: str,
        action: str,
        summary: str,
        detail: dict[str, Any],
    ) -> None:
        if db is None:
            return
        try:
            ledger_service.record(
                db,
                ledger_service.ActivityEvent(
                    event_type=event_type,
                    subsystem=SUBSYSTEM_CREATIVE,
                    action=action,
                    factual_summary=summary,
                    occurred_at=datetime.now(UTC),
                    detail_json=detail,
                    source="live",
                    source_ref=f"{action}:{uuid.uuid4()}",
                ),
            )
        except Exception:  # noqa: BLE001 - evidence, never a dependency of the action
            logger.warning("creative_ledger_failed", action=action)

    def clarification(self, speech: str) -> dict[str, Any]:
        return {"status": "needs_clarification", "speech": speech, "candidates": []}

    def _invalid_argument(
        self,
        *,
        capability: str,
        requested_state: str,
        speech: str,
        db: Session,
        session_id: str | None,
        error_class: str = ERROR_VALIDATION,
    ) -> dict[str, Any]:
        return self._receipt(
            capability=capability,
            requested_state=requested_state,
            execution=EXECUTION_REFUSED,
            terminal=TERMINAL_FAILED,
            server={"reason": error_class},
            speech=speech,
            db=db,
            error_class=error_class,
            session_id=session_id,
        )

    # ----------------------------------------------------------------- resolution

    def resolve_run(self, db: Session, target: str | None) -> CreativeRunRow | None:
        """ "current"/None -> the durable ``creative`` focus; a literal row id -> that
        row directly; anything else -> the most recently created row (the same
        "owner's words win, else best effort" fallback
        ``app.creative3d.service.SceneService.resolve_scene`` already documents)."""
        if target and target not in ("current", "previous"):
            try:
                row = db.get(CreativeRunRow, uuid.UUID(target))
                if row is not None:
                    return row
            except (ValueError, TypeError):
                pass
        entry = (
            focus_module.previous(db, FOCUS_KIND_CREATIVE)
            if target == "previous"
            else focus_module.current(db, FOCUS_KIND_CREATIVE)
        )
        if entry is not None:
            try:
                row = db.get(CreativeRunRow, uuid.UUID(entry.object_id))
                if row is not None:
                    return row
            except (ValueError, TypeError):
                pass
        return db.execute(
            select(CreativeRunRow).order_by(CreativeRunRow.created_at.desc()).limit(1)
        ).scalar_one_or_none()

    # --------------------------------------------------------------- provider gating

    def _capability_result(self, tool: str) -> ProviderResult:
        provider = self._providers.get(tool)
        if provider is None:
            return ProviderResult(ok=False, error_class=ERROR_DEPENDENCY_UNAVAILABLE, detail={})
        return provider.capabilities()

    def _missing_capability(self, plan: CreativePlan, supported: tuple[str, ...]) -> str | None:
        for op in plan.operations:
            capability = _op_capability(op.op)
            if capability not in supported:
                return capability
        return None

    def _dependency_unavailable_speech(self, tool: str, detail: dict[str, Any]) -> str:
        tool_tr = _TOOL_TR.get(tool, tool)
        alt = ""
        paint_result = self._capability_result(TOOL_PAINT)
        if tool != TOOL_PAINT and paint_result.ok:
            alt = " Paint'te açayım mı?"
        return f"{tool_tr} bu bilgisayarda kurulu değil.{alt}"

    # ------------------------------------------------------------------------ store

    def _store_output(self, row: CreativeRunRow, result: ExecutionResult) -> None:
        if self._object_store is None:
            return
        name = result.output_name or f"{row.name}.{result.format}"
        key = validate_object_key(f"creative/{row.id}/{name}")
        content_type = {
            "png": "image/png",
            "jpg": "image/jpeg",
            "pdf": "application/pdf",
            "svg": "image/svg+xml",
        }.get(result.format, "application/octet-stream")
        self._object_store.put(key, result.image_bytes, content_type=content_type)
        row.output_object_key = key
        row.output_name = name
        row.output_sha256 = hashlib.sha256(result.image_bytes).hexdigest()
        row.output_bytes = len(result.image_bytes)

    def fetch_source_bytes(self, key: str | None) -> bytes | None:
        """The bytes behind a stored object key, for a CALLER that names a source.

        Public because `create()` deliberately does not dereference `plan.source` itself
        (ADR-0094 decision 1) - which means every caller that names one must fetch it, and
        the one that forgot produced a blank canvas instead of a redraw. Naming it in the
        service rather than leaving each tool to reach for the object store keeps that a
        one-line obligation with one implementation.
        """
        return self._fetch_bytes(key)

    def _fetch_bytes(self, key: str | None) -> bytes | None:
        if key is None or self._object_store is None:
            return None
        try:
            return self._object_store.get(key)
        except KeyError:
            return None

    # --------------------------------------------------------------------- describe

    def _describe(self, plan: CreativePlan, result: ExecutionResult) -> str:
        shapes = len(result.drawn_shapes)
        texts = len(result.drawn_texts)
        if result.errors:
            pass
        if plan.source is None and shapes == 0 and texts == 0:
            return (
                f"{plan.name}: yeni bir tuval oluşturdum efendim ({result.width}x{result.height})."
            )
        parts: list[str] = []
        if shapes:
            parts.append(f"{shapes} şekil")
        if texts:
            parts.append(f"{texts} metin")
        if result.background_removed:
            parts.append("arka plan kaldırıldı")
        detail = ", ".join(parts) if parts else "değişiklik uygulandı"
        return f"{plan.name}: {detail} efendim ({result.width}x{result.height})."

    def _mismatch_speech(self, cmp_result: CompareResult) -> str:
        first = cmp_result.mismatches[0] if cmp_result.mismatches else None
        if first is None:
            return "İstediğim gibi olmadı efendim."
        detail = f"{first.object_name} · {first.field}: {first.detail or first.expected}"
        more = len(cmp_result.mismatches) - 1
        tail = f"; {more} uyuşmazlık daha" if more > 0 else ""
        return f"Uyuşmazlık efendim — {detail}{tail}."

    # --------------------------------------------------------------- self-correction

    def _correction_plan(
        self, plan: CreativePlan, cmp_result: CompareResult
    ) -> CreativePlan | None:
        """A follow-up plan for the ONE class of defect a bounded, deterministic
        retry can actually fix: a planned shape/text whose colour the comparison did
        not find (spec §3: "a discrepancy -> correction (a follow-up plan, <= 3
        rounds)"). Re-appends each failing operation so it lands as the LAST thing
        painted — never undone by whatever ran after it the first time. Returns
        ``None`` for any other defect class (wrong size, an empty/invalid output, or
        a reference-similarity miss) — a bounded retry cannot fix those by re-drawing
        anything, and ADR-0093 decision 7 is explicit: a run that still disagrees
        says so and shows the numbers, it does not keep looping to no effect."""
        fixable_indices: set[int] = set()
        for mismatch in cmp_result.mismatches:
            if mismatch.field != "presence":
                return None
            name = mismatch.object_name
            if "[" not in name or not name.endswith("]"):
                return None
            try:
                index = int(name.split("[", 1)[1][:-1])
            except ValueError:
                return None
            fixable_indices.add(index)
        if not fixable_indices:
            return None
        extra_ops = [
            plan.operations[i] for i in sorted(fixable_indices) if i < len(plan.operations)
        ]
        if not extra_ops:
            return None
        new_ops = (list(plan.operations) + extra_ops)[-64:]
        return plan.model_copy(update={"operations": new_ops})

    # ------------------------------------------------------------------------ core

    def _publish(self, *, tool: str, name: str, step: str, **extra: object) -> None:
        """One ``creative.activity`` event. ``metadata.state`` is the STEP of the loop from
        the closed wire vocabulary both halves share - never the database row's own word,
        which is exactly what let M25's two halves drift until its Cockpit panel could not
        read a single successful run.

        Never fails the owner's run over a UI concern: an unknown step is logged and
        dropped, because a word the build cannot read would draw a finished run as one
        still going, and saying nothing is the smaller lie.
        """
        if step not in CREATIVE_ACTIVITY_STEPS:
            logger.error("creative_unknown_activity_step", step=step)
            return
        metadata: dict[str, object] = {"tool": tool, "state": step}
        for key, value in extra.items():
            if value is not None:
                metadata[key] = value
        publish_ui_state(
            UiState.CREATIVE_ACTIVITY,
            subsystem=SUBSYSTEM_CREATIVE,
            label=name[:64],
            metadata=metadata,
        )

    def _run_rounds(
        self,
        db: Session,
        row: CreativeRunRow,
        plan: CreativePlan,
        *,
        capability: str,
        source_bytes: bytes | None,
        reference_bytes: bytes | None,
        session_id: str | None,
    ) -> dict[str, Any]:
        rounds: list[dict[str, Any]] = list(row.rounds_json or [])
        current_plan = plan
        result: ExecutionResult | None = None
        cmp_result: CompareResult | None = None

        while True:
            self._publish(
                tool=plan.tool,
                name=row.name,
                step=CREATIVE_STEP_CORRECTING if rounds else CREATIVE_STEP_EXECUTING,
                round=len(rounds) + 1,
            )
            try:
                result = execute(current_plan, source_bytes)
            except ExecutionError as exc:
                row.state = STATE_FAILED
                row.error_class = ERROR_VALIDATION
                row.error_message = str(exc)
                self._publish(tool=plan.tool, name=row.name, step=CREATIVE_STEP_FAILED)
                row.rounds_json = rounds
                row.updated_at = datetime.now(UTC)
                db.commit()
                self._ledger(
                    db,
                    event_type=EVENT_TYPE_CREATIVE_FAILED,
                    action=capability,
                    summary=f"{capability} -> failed (source could not be decoded)",
                    detail={"run_id": str(row.id)},
                )
                return self._receipt(
                    capability=capability,
                    requested_state="applied",
                    execution=EXECUTION_REFUSED,
                    terminal=TERMINAL_FAILED,
                    server={"reason": ERROR_VALIDATION},
                    speech="Kaynak görsel açılamadı efendim.",
                    db=db,
                    error_class=ERROR_VALIDATION,
                    session_id=session_id,
                    extra={"run_id": str(row.id)},
                )
            self._store_output(row, result)
            self._publish(tool=plan.tool, name=row.name, step=CREATIVE_STEP_COMPARING)
            cmp_result = compare(
                current_plan,
                result.inspection(),
                image_bytes=result.image_bytes,
                reference_bytes=reference_bytes,
            )
            rounds.append(
                {
                    "round": len(rounds) + 1,
                    "plan_json": current_plan.plan_json(),
                    "compare": cmp_result.as_dict(),
                }
            )
            nothing_to_check = cmp_result.reason == COMPARE_NO_CONSTRAINTS
            if cmp_result.ok or nothing_to_check or len(rounds) >= MAX_SELF_CORRECTION_ROUNDS:
                break
            follow_up = self._correction_plan(current_plan, cmp_result)
            if follow_up is None:
                break
            current_plan = follow_up

        assert result is not None and cmp_result is not None
        row.plan_json = current_plan.as_dict()
        row.inspection_json = result.inspection()
        row.compare_json = cmp_result.as_dict()
        row.round_count = len(rounds)
        row.rounds_json = rounds
        nothing_to_check = cmp_result.reason == COMPARE_NO_CONSTRAINTS
        if cmp_result.ok:
            row.state = STATE_VERIFIED
        elif nothing_to_check:
            row.state = STATE_UNVERIFIED
        else:
            row.state = STATE_MISMATCH
        row.updated_at = datetime.now(UTC)
        db.commit()
        # The settled word, from the row's own state through the ONE mapping - so the
        # channel and the row can never disagree about how a run ended.
        self._publish(
            tool=plan.tool,
            name=row.name,
            step=wire_step(row.state),
            similarity=cmp_result.similarity,
            defect=(cmp_result.mismatches[0].wire_defect() if cmp_result.mismatches else None),
        )
        focus_module.set_focus(
            db, FOCUS_KIND_CREATIVE, str(row.id), label=row.name, source="creative_dispatch"
        )

        if cmp_result.ok:
            speech = self._describe(current_plan, result)
        elif nothing_to_check:
            speech = self._describe(current_plan, result) + " " + SPEECH_NOTHING_TO_CHECK
        else:
            speech = self._mismatch_speech(cmp_result)

        if row.state == STATE_MISMATCH:
            event_type = EVENT_TYPE_CREATIVE_MISMATCH
        elif row.state == STATE_VERIFIED:
            event_type = EVENT_TYPE_CREATIVE_VERIFIED
        else:
            event_type = EVENT_TYPE_CREATIVE_APPLIED
        self._ledger(
            db,
            event_type=event_type,
            action=capability,
            summary=f"{capability} -> {row.tool}:{row.name} ({len(rounds)} round(s))",
            detail={
                "run_id": str(row.id),
                "rounds": len(rounds),
                "compare_ok": cmp_result.ok,
                "mismatches": [m.as_dict() for m in cmp_result.mismatches[:8]],
            },
        )
        return self._receipt(
            capability=capability,
            requested_state=row.state,
            execution=EXECUTION_EXECUTED,
            terminal=TERMINAL_VERIFIED if cmp_result.ok else TERMINAL_UNVERIFIED,
            server={
                "rounds": len(rounds),
                "compare_ok": cmp_result.ok,
                "compare_reason": cmp_result.reason,
            },
            speech=speech,
            db=db,
            session_id=session_id,
            extra={
                "run_id": str(row.id),
                "state": row.state,
                "inspection": result.inspection(),
                "compare": cmp_result.as_dict(),
                "rounds": rounds,
                "output_name": row.output_name,
            },
        )

    def create(
        self,
        db: Session,
        *,
        plan: dict[str, Any],
        source_bytes: bytes | None = None,
        reference_bytes: bytes | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        try:
            creative_plan = CreativePlan.model_validate(plan)
        except ValidationError as exc:
            return self._receipt(
                capability="creative.create",
                requested_state="applied",
                execution=EXECUTION_REFUSED,
                terminal=TERMINAL_FAILED,
                server={"reason": "invalid_plan"},
                speech=SPEECH_INVALID_PLAN,
                db=db,
                error_class=ERROR_VALIDATION,
                session_id=session_id,
                extra={"error_class": ERROR_VALIDATION, "detail": str(exc)[:500]},
            )

        active = (
            db.execute(select(CreativeRunRow).where(CreativeRunRow.state.in_(_ACTIVE_STATES)))
            .scalars()
            .all()
        )
        if len(active) >= MAX_ACTIVE_RUNS:
            return self._invalid_argument(
                capability="creative.create",
                requested_state="applied",
                speech="Aynı anda en fazla iki düzenleme üzerinde çalışabilirim efendim.",
                db=db,
                session_id=session_id,
            )

        now = datetime.now(UTC)
        row = CreativeRunRow(
            id=uuid.uuid4(),
            tool=creative_plan.tool,
            name=creative_plan.name,
            label=creative_plan.label,
            state=STATE_PLANNED,
            plan_json=creative_plan.as_dict(),
            created_at=now,
            updated_at=now,
        )
        db.add(row)
        db.commit()
        self._ledger(
            db,
            event_type=EVENT_TYPE_CREATIVE_CREATED,
            action="creative.create",
            summary=f"creative.create -> {creative_plan.tool}:{creative_plan.name}",
            detail={"run_id": str(row.id), "tool": creative_plan.tool},
        )

        cap_result = self._capability_result(creative_plan.tool)
        if not cap_result.ok:
            row.state = STATE_DEPENDENCY_UNAVAILABLE
            row.error_class = ERROR_DEPENDENCY_UNAVAILABLE
            row.error_message = str(cap_result.detail)
            row.updated_at = datetime.now(UTC)
            db.commit()
            self._ledger(
                db,
                event_type=EVENT_TYPE_CREATIVE_DEPENDENCY_UNAVAILABLE,
                action="creative.create",
                summary=f"creative.create -> {creative_plan.tool} unavailable",
                detail={"run_id": str(row.id), "detail": cap_result.detail},
            )
            return self._receipt(
                capability="creative.create",
                requested_state="applied",
                execution=EXECUTION_REFUSED,
                terminal=TERMINAL_FAILED,
                server={"detail": cap_result.detail},
                speech=self._dependency_unavailable_speech(creative_plan.tool, cap_result.detail),
                db=db,
                error_class=ERROR_DEPENDENCY_UNAVAILABLE,
                session_id=session_id,
                extra={"run_id": str(row.id), "state": STATE_DEPENDENCY_UNAVAILABLE},
            )

        missing = self._missing_capability(creative_plan, cap_result.capabilities)
        if missing is not None:
            row.state = STATE_FAILED
            row.error_class = ERROR_CAPABILITY_MISSING
            row.error_message = missing
            row.updated_at = datetime.now(UTC)
            db.commit()
            tool_tr = _TOOL_TR.get(creative_plan.tool, creative_plan.tool)
            return self._receipt(
                capability="creative.create",
                requested_state="applied",
                execution=EXECUTION_REFUSED,
                terminal=TERMINAL_FAILED,
                server={"capability": missing},
                speech=f"{missing} {tool_tr} tarafından desteklenmiyor efendim.",
                db=db,
                error_class=ERROR_CAPABILITY_MISSING,
                session_id=session_id,
                extra={"run_id": str(row.id)},
            )

        return self._run_rounds(
            db,
            row,
            creative_plan,
            capability="creative.create",
            source_bytes=source_bytes,
            reference_bytes=reference_bytes,
            session_id=session_id,
        )

    def apply(
        self,
        db: Session,
        *,
        target: str | None,
        operations: list[dict[str, Any]],
        capability: str = "creative.apply",
        session_id: str | None = None,
        source_bytes: bytes | None = None,
        reference_bytes: bytes | None = None,
    ) -> dict[str, Any]:
        row = self.resolve_run(db, target)
        if row is None:
            return self.clarification(SPEECH_NO_RUN)
        if row.state == STATE_DEPENDENCY_UNAVAILABLE:
            return self._invalid_argument(
                capability=capability,
                requested_state="applied",
                speech=self._dependency_unavailable_speech(row.tool, {}),
                db=db,
                session_id=session_id,
                error_class=ERROR_DEPENDENCY_UNAVAILABLE,
            )
        try:
            # A follow-up edit continues from the run's own LAST OUTPUT — never a
            # blank canvas — so ``open`` is prepended here, unconditionally, and
            # ``source_bytes`` (below) is fetched from the object store when the
            # caller did not already supply fresher bytes of its own. ``source`` is
            # a placeholder reference (this run's own id, which already passes the
            # schema's object-key shape check): the ACTUAL bytes are supplied
            # directly to :func:`app.creative.execute.execute`, never re-fetched by
            # this string, the same "the plan names it, the port supplies it"
            # separation ``app.creative3d.service`` keeps between a plan and the
            # device call that actually reads/writes bytes.
            creative_plan = CreativePlan.model_validate(
                {
                    "tool": row.tool,
                    "name": row.name,
                    "source": str(row.id),
                    "operations": [{"op": "open"}, *operations],
                    "label": row.label,
                }
            )
        except ValidationError as exc:
            return self._receipt(
                capability=capability,
                requested_state="applied",
                execution=EXECUTION_REFUSED,
                terminal=TERMINAL_FAILED,
                server={"reason": "invalid_plan"},
                speech=SPEECH_INVALID_PLAN,
                db=db,
                error_class=ERROR_VALIDATION,
                session_id=session_id,
                extra={
                    "error_class": ERROR_VALIDATION,
                    "detail": str(exc)[:500],
                    "run_id": str(row.id),
                },
            )
        cap_result = self._capability_result(row.tool)
        if not cap_result.ok:
            return self._invalid_argument(
                capability=capability,
                requested_state="applied",
                speech=self._dependency_unavailable_speech(row.tool, cap_result.detail),
                db=db,
                session_id=session_id,
                error_class=ERROR_DEPENDENCY_UNAVAILABLE,
            )
        missing = self._missing_capability(creative_plan, cap_result.capabilities)
        if missing is not None:
            tool_tr = _TOOL_TR.get(row.tool, row.tool)
            return self._invalid_argument(
                capability=capability,
                requested_state="applied",
                speech=f"{missing} {tool_tr} tarafından desteklenmiyor efendim.",
                db=db,
                session_id=session_id,
                error_class=ERROR_CAPABILITY_MISSING,
            )
        if source_bytes is None:
            source_bytes = self._fetch_bytes(row.output_object_key)
        row.round_count = 0
        row.rounds_json = []
        return self._run_rounds(
            db,
            row,
            creative_plan,
            capability=capability,
            source_bytes=source_bytes,
            reference_bytes=reference_bytes,
            session_id=session_id,
        )

    # ----------------------------------------------------------------------- status

    def status(
        self, db: Session, *, target: str | None, session_id: str | None = None
    ) -> dict[str, Any]:
        row = self.resolve_run(db, target)
        if row is None:
            return self.clarification(SPEECH_NO_RUN)
        speech = f"{row.name}: {row.state} efendim."
        return self._receipt(
            capability="creative.status",
            requested_state=row.state,
            execution=EXECUTION_EXECUTED,
            terminal=TERMINAL_VERIFIED,
            server={"state": row.state},
            speech=speech,
            db=db,
            session_id=session_id,
            extra={"run_id": str(row.id), "state": row.state},
        )

    # ------------------------------------------------------------------------- list

    def list(self, db: Session, *, session_id: str | None = None) -> dict[str, Any]:
        rows = list(
            db.execute(select(CreativeRunRow).order_by(CreativeRunRow.created_at.desc()).limit(20))
            .scalars()
            .all()
        )
        self._ledger(
            db,
            event_type=EVENT_TYPE_CREATIVE_LISTED,
            action="creative.list",
            summary=f"creative.list -> {len(rows)} çalışma",
            detail={"count": len(rows)},
        )
        if not rows:
            speech = "Henüz bir düzenleme yapmadım efendim."
        else:
            names = ", ".join(f"{r.tool}:{r.name} ({r.state})" for r in rows)
            speech = f"Şu çalışmalar var efendim: {names}."
        runs = [
            {"run_id": str(r.id), "tool": r.tool, "name": r.name, "state": r.state} for r in rows
        ]
        return self._receipt(
            capability="creative.list",
            requested_state="listed",
            execution=EXECUTION_EXECUTED,
            terminal=TERMINAL_VERIFIED,
            server={"count": len(rows)},
            speech=speech,
            db=db,
            session_id=session_id,
            extra={"runs": runs},
        )


__all__ = ["MAX_ACTIVE_RUNS", "CreativeService"]
