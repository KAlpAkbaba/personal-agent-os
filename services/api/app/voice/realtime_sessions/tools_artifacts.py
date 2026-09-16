"""The Artifact Factory's voice tools (docs/M22_ARTIFACT_FACTORY_SPEC.md §5, ADR-0085).

Five tools, registered from ``tools.default_registry()`` by ONE added line
(:func:`register_artifacts_tools`), the same discipline ``tools_documents``/
``tools_mail`` already establish for their own families: ``artifact.create``,
``artifact.render``, ``artifact.validate``, ``artifact.open`` and ``artifact.list``.

The ONE router (``app.voice.intents``) extracts the deterministic part of an
ARTIFACT_CREATE utterance — the kind word, the title, and every number the owner said
(``spoken_numbers``) — onto ``ctx.context["last_utterance"]``; this module PREFERS those
over the model's own arguments (the same "owner's words win" rule ``document_ref`` /
``application`` / ``text_to_type`` already follow), and hands ``spoken_numbers`` to
``ArtifactSpec`` itself, which refuses to construct a spec containing a number outside
that set (the "never invented" rule, ``app.artifacts.spec``'s own docstring). There is no
delete tool here (M22 spec §5 names five tools, none of them a mutation of an existing
render's bytes outside re-rendering/re-validating): "Bunu sil" reaches none of these.

``ctx.live["artifacts_runtime"]`` (an ``ArtifactRuntime``: ``.store`` and ``.settings``,
the SAME object ``research.start`` already reads) and ``ctx.live["device_action"]`` come
from live sources injected by the realtime runtime — never imported as singletons here
(docs/M18_ACTION_CONTRACT.md §4); a session with neither is an honest
``dependency_unavailable``, never a guess.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Final

from pydantic import ValidationError

from app.actions.receipt import (
    EXECUTION_EXECUTED,
    EXECUTION_NOOP,
    EXECUTION_REFUSED,
    TERMINAL_ALREADY,
    TERMINAL_FAILED,
    TERMINAL_UNVERIFIED,
    TERMINAL_VERIFIED,
    ActionReceipt,
    record_receipt,
)
from app.artifacts import factory, open_service, service
from app.artifacts import lifecycle as artifact_lifecycle
from app.artifacts.provenance import ACTOR_OWNER_VOICE, Actor
from app.artifacts.spec import ArtifactSpec
from app.ledger import service as ledger_service
from app.ledger.vocabulary import (
    EVENT_TYPE_ARTIFACT_CLONED,
    EVENT_TYPE_ARTIFACT_CREATED,
    EVENT_TYPE_ARTIFACT_DELETED,
    EVENT_TYPE_ARTIFACT_EDITED,
    EVENT_TYPE_ARTIFACT_LISTED,
    EVENT_TYPE_ARTIFACT_OPENED,
    EVENT_TYPE_ARTIFACT_RENDERED,
    EVENT_TYPE_ARTIFACT_VALIDATED,
    SUBSYSTEM_ARTIFACTS,
)
from app.logging import get_logger
from app.operator import focus as focus_module
from app.operator.models import FOCUS_KIND_ARTIFACT
from app.voice.errors import VoiceError, VoiceErrorClass
from app.voice.intents import contains_secret_reference

if TYPE_CHECKING:
    from app.voice.realtime_sessions.tools import ToolContext, ToolRegistry

logger = get_logger("app.voice.realtime_sessions.tools_artifacts")

TOOL_ARTIFACT_CREATE: Final = "artifact.create"
TOOL_ARTIFACT_RENDER: Final = "artifact.render"
TOOL_ARTIFACT_VALIDATE: Final = "artifact.validate"
TOOL_ARTIFACT_OPEN: Final = "artifact.open"
TOOL_ARTIFACT_LIST: Final = "artifact.list"

ARTIFACT_TOOL_NAMES: Final[tuple[str, ...]] = (
    TOOL_ARTIFACT_CREATE,
    TOOL_ARTIFACT_RENDER,
    TOOL_ARTIFACT_VALIDATE,
    TOOL_ARTIFACT_OPEN,
    TOOL_ARTIFACT_LIST,
)

SPEECH_NO_ARTIFACT: Final = "Hangi dosya efendim?"
SPEECH_NO_PREVIOUS_ARTIFACT: Final = "Dönebileceğim önceki bir dosya yok efendim."
SPEECH_UNKNOWN_KIND: Final = (
    "Ne tür bir dosya yapmamı istersiniz — belge, tablo, sunum, liste ya da sayfa?"
)

ERROR_VALIDATION: Final = "validation_error"
ERROR_NOT_FOUND: Final = "not_found"
#: Security-review findings on M22's Cloud Core half (ADR-0085 addendum 6).
ERROR_INVENTED_NUMBER: Final = "invented_number"
ERROR_FORMULA_INJECTION: Final = "formula_injection"
ERROR_SECRET_REFUSED: Final = "secret_refused"

SPEECH_INVENTED_NUMBER: Final = "Bunu dosyaya dökemedim efendim; söylediğiniz rakamlarla uyuşmuyor."
SPEECH_FORMULA_INJECTION: Final = (
    "Bunu dosyaya dökemedim efendim; bu içerik bir formül gibi yorumlanabilir."
)
SPEECH_INVALID_SPEC: Final = "Bunu dosyaya dökemedim efendim; yapı geçersiz."
SPEECH_SECRET_REFUSED: Final = "Şifreleri dosyaya yazamam efendim."

#: ``app.artifacts.spec``'s own model validators name the failing ref between a fixed,
#: greppable ``<marker>[<ref>]:`` prefix (never the refused CONTENT — see spec.py's own
#: docstrings) specifically so this tool can build a clean refusal receipt without ever
#: embedding ``str(exc)`` — pydantic's ``ValidationError`` formatting echoes the raw
#: failing input alongside any custom message, which this tool must never repeat back
#: in a receipt, a ledger row or (worse) something read aloud by TTS.
_INVENTED_NUMBER_REF_RE: Final = re.compile(r"invented_number\[(.*?)\]:")
_FORMULA_INJECTION_REF_RE: Final = re.compile(r"formula_injection\[(.*?)\]:")

_KIND_LABELS: Final[dict[str, str]] = {
    "document": "belge",
    "spreadsheet": "tablo",
    "presentation": "sunum",
    "dataset": "veri listesi",
    "page": "sayfa",
}

_FORMAT_LABELS: Final[dict[str, str]] = {
    "docx": "Word",
    "pdf": "PDF",
    "html": "HTML",
    "md": "Markdown",
    "txt": "metin",
    "xlsx": "Excel",
    "csv": "CSV",
    "pptx": "PowerPoint",
    "json": "JSON",
}


def _format_label(fmt: str) -> str:
    return _FORMAT_LABELS.get(fmt, fmt.upper())


# --------------------------------------------------------------------- plumbing


def _turn_record(ctx: ToolContext) -> dict[str, Any]:
    """The ONE router's record of this turn — the owner's WORDS, preferred over the
    model's own argument, the same rule every other M19+ tool family follows."""
    return dict(ctx.context.get("last_utterance") or {})


def _require_db(ctx: ToolContext, tool: str) -> Any:
    if ctx.db is None:
        raise VoiceError(
            VoiceErrorClass.DEPENDENCY_UNAVAILABLE,
            f"{tool} needs the durable state; no database on this session",
        )
    return ctx.db


def _artifacts_runtime(ctx: ToolContext, tool: str) -> Any:
    runtime = ctx.live.get("artifacts_runtime")
    if runtime is None:
        raise VoiceError(
            VoiceErrorClass.DEPENDENCY_UNAVAILABLE, f"{tool} needs the artifact runtime"
        )
    return runtime


def _clarification(speech: str) -> dict[str, Any]:
    return {"status": "needs_clarification", "speech": speech, "candidates": []}


def _resolve_artifact_id(
    ctx: ToolContext, arguments: dict[str, Any], *, default: str = "current"
) -> uuid.UUID | None:
    """The artifact ``target`` points at: the router's own ``artifact_ref``
    ("current"/"previous", set from the owner's deictic words) preferred over the
    model's ``target`` argument, resolved through the SAME durable focus stack
    (``app.operator.focus``, kind ``artifact``) every other M19+ family uses."""
    turn = _turn_record(ctx)
    ref = turn.get("artifact_ref")
    if not (isinstance(ref, str) and ref):
        raw = arguments.get("target")
        ref = raw if isinstance(raw, str) and raw else default
    db = ctx.db
    assert db is not None
    entry = (
        focus_module.previous(db, FOCUS_KIND_ARTIFACT)
        if ref == "previous"
        else focus_module.current(db, FOCUS_KIND_ARTIFACT)
    )
    if entry is None:
        return None
    try:
        return uuid.UUID(entry.object_id)
    except ValueError:
        return None


def _no_target_speech(ctx: ToolContext) -> str:
    turn = _turn_record(ctx)
    if turn.get("artifact_ref") == "previous":
        return SPEECH_NO_PREVIOUS_ARTIFACT
    return SPEECH_NO_ARTIFACT


def _ledger(db: Any, *, event_type: str, action: str, summary: str, detail: dict[str, Any]) -> None:
    if db is None:
        return
    try:
        ledger_service.record(
            db,
            ledger_service.ActivityEvent(
                event_type=event_type,
                subsystem=SUBSYSTEM_ARTIFACTS,
                action=action,
                factual_summary=summary,
                occurred_at=datetime.now(UTC),
                detail_json=detail,
                source="live",
                source_ref=f"{action}:{uuid.uuid4()}",
            ),
        )
    except Exception:  # noqa: BLE001 - evidence, never a dependency of the action
        logger.warning("artifact_ledger_failed", action=action)


def _receipt(
    ctx: ToolContext,
    *,
    capability: str,
    requested_state: str,
    execution: str,
    terminal: str,
    server: dict[str, Any],
    speech: str,
    error_class: str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    now = datetime.now(UTC)
    receipt = ActionReceipt(
        action_id=ctx.call_id or str(uuid.uuid4()),
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
        session_id=str(ctx.session_id),
        observed_at=now,
    )
    if ctx.db is not None:
        record_receipt(ctx.db, receipt, SUBSYSTEM_ARTIFACTS)
    out = receipt.as_dict()
    if extra:
        out.update(extra)
    return out


# ------------------------------------------------------------------ artifact.create


def artifact_create(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """ "Bana bir bütçe tablosu yap: kira 12000, maaş 45000, yazılım 8000",
    "Toplantı notlarını Word belgesi yap", "Üç slaytlık bir sunum hazırla: giriş,
    bulgular, sonuç" (spec §5). Also reached by "Bunu PDF yap" when the router found no
    kind word at all (a deictic format request against the focused artifact) — the
    model then names ``format`` and the current artifact's kind decides the rest; a
    caller wanting a genuinely NEW artifact in that shape should call ``artifact.render``
    instead, which this tool refuses to become (no ``target`` argument at all)."""
    db = _require_db(ctx, TOOL_ARTIFACT_CREATE)
    runtime = _artifacts_runtime(ctx, TOOL_ARTIFACT_CREATE)
    turn = _turn_record(ctx)

    kind = turn.get("artifact_kind") if isinstance(turn.get("artifact_kind"), str) else None
    kind = kind or (str(arguments.get("kind")) if arguments.get("kind") else None)
    if not kind:
        return _clarification(SPEECH_UNKNOWN_KIND)

    title = turn.get("artifact_title") if isinstance(turn.get("artifact_title"), str) else None
    title = title or None
    raw_title = arguments.get("title")
    title = title or (str(raw_title) if isinstance(raw_title, str) and raw_title else None)
    title = title or "Adsız"

    raw_spec = arguments.get("spec")
    spec_dict: dict[str, Any] = dict(raw_spec) if isinstance(raw_spec, dict) else {}
    spec_dict.setdefault("kind", kind)
    spec_dict.setdefault("title", title)
    # MEDIUM security-review finding (ADR-0085 addendum 6): ``spoken_numbers`` is now
    # ALWAYS force-set from the router's own extraction — an empty list when the
    # utterance had no numbers at all, never left unset. Previously this only ran
    # `if spoken_numbers` (truthy), so a NUMBERLESS utterance left ``spoken_numbers``
    # unset entirely, ``ArtifactSpec.spoken_numbers`` defaulted to ``None``, and the
    # "never invented" rule was SKIPPED OUTRIGHT rather than enforcing "no numbers
    # allowed" — an invented figure (e.g. a model hallucinating "kira 12000" with
    # nothing spoken) sailed straight into a rendered file. A forced assignment (never
    # ``setdefault``) also means the MODEL's own ``spec`` argument can never widen this
    # set by smuggling its own ``spoken_numbers`` key — only the router's words count.
    turn_numbers = turn.get("spoken_numbers")
    spec_dict["spoken_numbers"] = list(turn_numbers) if isinstance(turn_numbers, list) else []

    try:
        spec = ArtifactSpec.model_validate(spec_dict)
    except (ValidationError, ValueError) as exc:
        detail = str(exc)
        formula_match = _FORMULA_INJECTION_REF_RE.search(detail)
        if formula_match:
            ref = formula_match.group(1)
            # Never `str(exc)` here: pydantic's own ValidationError formatting echoes
            # the raw failing input alongside spec.py's own (already content-free)
            # message — this receipt names the ref and NOTHING else (HIGH finding).
            return _receipt(
                ctx,
                capability=TOOL_ARTIFACT_CREATE,
                requested_state="created",
                execution=EXECUTION_REFUSED,
                terminal=TERMINAL_FAILED,
                server={"reason": ERROR_FORMULA_INJECTION, "ref": ref},
                speech=SPEECH_FORMULA_INJECTION,
                error_class=ERROR_FORMULA_INJECTION,
                extra={"failing_ref": ref},
            )
        invented_match = _INVENTED_NUMBER_REF_RE.search(detail)
        if invented_match:
            ref = invented_match.group(1)
            return _receipt(
                ctx,
                capability=TOOL_ARTIFACT_CREATE,
                requested_state="created",
                execution=EXECUTION_REFUSED,
                terminal=TERMINAL_FAILED,
                server={"reason": ERROR_INVENTED_NUMBER, "ref": ref},
                speech=SPEECH_INVENTED_NUMBER,
                error_class=ERROR_INVENTED_NUMBER,
                extra={"failing_ref": ref},
            )
        return _receipt(
            ctx,
            capability=TOOL_ARTIFACT_CREATE,
            requested_state="created",
            execution=EXECUTION_REFUSED,
            terminal=TERMINAL_FAILED,
            server={"reason": ERROR_VALIDATION},
            speech=SPEECH_INVALID_SPEC,
            error_class=ERROR_VALIDATION,
            extra={"detail": detail[:500]},
        )

    # LOW security-review finding (ADR-0085 addendum 6): the same secret-reference gate
    # M21 already applies to mail (``app.voice.intents.contains_secret_reference``,
    # M19 spec §1 invariant 2) is applied here to the title and every free-text/cell
    # string the structure carries — a password/PIN never gets written to a file, the
    # same way it never gets typed or mailed. Checked AFTER the spec validates (so
    # ``spec._all_text()``/sheet & dataset cells are available in one already-typed
    # traversal) and BEFORE ``factory.create`` — nothing is rendered.
    secret_texts = [title, spec.title, *spec._all_text()]  # noqa: SLF001 - same package
    if any(contains_secret_reference(t) for t in secret_texts if t):
        return _receipt(
            ctx,
            capability=TOOL_ARTIFACT_CREATE,
            requested_state="created",
            execution=EXECUTION_REFUSED,
            terminal=TERMINAL_FAILED,
            server={"reason": ERROR_SECRET_REFUSED},
            speech=SPEECH_SECRET_REFUSED,
            error_class=ERROR_SECRET_REFUSED,
        )

    result = factory.create(
        db,
        runtime.store,
        spec=spec,
        actor=Actor(ACTOR_OWNER_VOICE, ref=ctx.call_id, session_id=str(ctx.session_id)),
    )
    focus_module.set_focus(
        db,
        FOCUS_KIND_ARTIFACT,
        str(result.artifact_id),
        label=result.title,
        source="artifact_create",
    )
    numbers = sorted(spec._structure_numbers())  # noqa: SLF001 - same package, test hook
    formats = [r.format for r in result.renders]
    failing = {r.format: r.failing_refs for r in result.renders if not r.valid}
    kind_label = _KIND_LABELS.get(spec.kind, spec.kind)
    format_labels = ", ".join(_format_label(f) for f in formats)
    if result.all_valid:
        speech = (
            f"{spec.title} adlı {kind_label} dosyasını hazırladım efendim "
            f"({format_labels}); doğrulandı."
        )
    else:
        bad = ", ".join(
            f"{_format_label(f)} ({refs[0] if refs else '?'})" for f, refs in failing.items()
        )
        speech = (
            f"{spec.title} adlı {kind_label} dosyasını hazırladım efendim ({format_labels}); "
            f"ancak şunlar doğrulanamadı: {bad}."
        )
    _ledger(
        db,
        event_type=EVENT_TYPE_ARTIFACT_CREATED,
        action="artifact.create",
        summary=f"artifact.create -> {spec.title} ({spec.kind})",
        detail={"artifact_id": str(result.artifact_id), "kind": spec.kind, "formats": formats},
    )
    return _receipt(
        ctx,
        capability=TOOL_ARTIFACT_CREATE,
        requested_state="created",
        execution=EXECUTION_EXECUTED,
        terminal=TERMINAL_VERIFIED if result.all_valid else TERMINAL_UNVERIFIED,
        server={"artifact_id": str(result.artifact_id), "kind": spec.kind, "formats": formats},
        speech=speech,
        error_class=None if result.all_valid else "invalid_render",
        extra={
            "artifact_id": str(result.artifact_id),
            "kind": spec.kind,
            "title": spec.title,
            "created": result.created,
            "all_valid": result.all_valid,
            "formats": formats,
            "failing_refs": failing,
            "numbers": numbers,
        },
    )


# ------------------------------------------------------------------ artifact.render


def artifact_render(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """ "Bunu PDF yap." after an artifact already exists — render (or re-use) one more
    format of the CURRENT (or previous) artifact (spec §5)."""
    db = _require_db(ctx, TOOL_ARTIFACT_RENDER)
    runtime = _artifacts_runtime(ctx, TOOL_ARTIFACT_RENDER)
    artifact_id = _resolve_artifact_id(ctx, arguments)
    if artifact_id is None:
        return _clarification(_no_target_speech(ctx))
    raw_fmt = arguments.get("format")
    fmt = str(raw_fmt).lower() if isinstance(raw_fmt, str) and raw_fmt else None
    if not fmt:
        return _clarification("Hangi formatta olsun?")
    try:
        result = factory.render_format(db, runtime.store, artifact_id=artifact_id, fmt=fmt)
    except ValueError:
        return _receipt(
            ctx,
            capability=TOOL_ARTIFACT_RENDER,
            requested_state="rendered",
            execution=EXECUTION_REFUSED,
            terminal=TERMINAL_FAILED,
            server={"reason": ERROR_VALIDATION, "format": fmt},
            speech=f"Bu dosya {_format_label(fmt)} olamaz efendim.",
            error_class=ERROR_VALIDATION,
        )
    if result is None:
        return _receipt(
            ctx,
            capability=TOOL_ARTIFACT_RENDER,
            requested_state="rendered",
            execution=EXECUTION_REFUSED,
            terminal=TERMINAL_FAILED,
            server={"reason": ERROR_NOT_FOUND},
            speech="Böyle bir çıktı yok efendim.",
            error_class=ERROR_NOT_FOUND,
        )
    _ledger(
        db,
        event_type=EVENT_TYPE_ARTIFACT_RENDERED,
        action="artifact.render",
        summary=f"artifact.render -> {fmt} ({result.state})",
        detail={"artifact_id": str(artifact_id), "format": fmt, "state": result.state},
    )
    if result.valid:
        speech = f"{_format_label(fmt)} olarak hazırladım efendim; doğrulandı."
    else:
        bad_ref = result.failing_refs[0] if result.failing_refs else "?"
        speech = f"{_format_label(fmt)} olarak hazırladım efendim; ancak doğrulanamadı ({bad_ref})."
    return _receipt(
        ctx,
        capability=TOOL_ARTIFACT_RENDER,
        requested_state="rendered",
        execution=EXECUTION_EXECUTED,
        terminal=TERMINAL_VERIFIED if result.valid else TERMINAL_UNVERIFIED,
        server={"artifact_id": str(artifact_id), "format": fmt, "state": result.state},
        speech=speech,
        error_class=None if result.valid else "invalid_render",
        extra={
            "artifact_id": str(artifact_id),
            "format": fmt,
            "state": result.state,
            "failing_refs": result.failing_refs,
        },
    )


# ---------------------------------------------------------------- artifact.validate


def artifact_validate(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """ "Bu dosya doğru mu?" — re-validate on demand, every render this artifact
    currently has (spec §5)."""
    db = _require_db(ctx, TOOL_ARTIFACT_VALIDATE)
    runtime = _artifacts_runtime(ctx, TOOL_ARTIFACT_VALIDATE)
    artifact_id = _resolve_artifact_id(ctx, arguments)
    if artifact_id is None:
        return _clarification(_no_target_speech(ctx))
    results = factory.revalidate_all(db, runtime.store, artifact_id=artifact_id)
    if results is None:
        return _receipt(
            ctx,
            capability=TOOL_ARTIFACT_VALIDATE,
            requested_state="validated",
            execution=EXECUTION_REFUSED,
            terminal=TERMINAL_FAILED,
            server={"reason": ERROR_NOT_FOUND},
            speech="Böyle bir çıktı yok efendim.",
            error_class=ERROR_NOT_FOUND,
        )
    invalid = [r for r in results if r.get("state") != "valid"]
    all_valid = not invalid
    failing_ref = None
    if invalid:
        validation = invalid[0].get("validation")
        if isinstance(validation, dict):
            refs = validation.get("failing_refs") or []
            failing_ref = refs[0] if refs else None
    speech = (
        "Evet, doğru efendim."
        if all_valid
        else f"Hayır efendim, doğrulanamadı ({failing_ref or '?'})."
    )
    _ledger(
        db,
        event_type=EVENT_TYPE_ARTIFACT_VALIDATED,
        action="artifact.validate",
        summary=f"artifact.validate -> {'valid' if all_valid else 'invalid'}",
        detail={"artifact_id": str(artifact_id), "all_valid": all_valid},
    )
    return _receipt(
        ctx,
        capability=TOOL_ARTIFACT_VALIDATE,
        requested_state="validated",
        execution=EXECUTION_NOOP,
        terminal=TERMINAL_VERIFIED if all_valid else TERMINAL_UNVERIFIED,
        server={"artifact_id": str(artifact_id), "all_valid": all_valid},
        speech=speech,
        error_class=None if all_valid else "invalid_render",
        extra={
            "artifact_id": str(artifact_id),
            "all_valid": all_valid,
            "failing_ref": failing_ref,
            "formats": [r.get("format") for r in results],
        },
    )


# -------------------------------------------------------------------- artifact.open


def artifact_open(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """ "Bunu aç.", "Son ürettiğin dosyayı aç." (spec §5): fetch + open the current (or
    previous) artifact's render on the owner's machine, through the device's
    ``file.fetch`` (DEVICE_PROTOCOL.md §6k). ``capability_missing`` when no device
    advertises it (the 0.1.0/0.3.x agent) — never a guess, never a claim of an open that
    did not happen."""
    db = _require_db(ctx, TOOL_ARTIFACT_OPEN)
    runtime = _artifacts_runtime(ctx, TOOL_ARTIFACT_OPEN)
    artifact_id = _resolve_artifact_id(ctx, arguments)
    if artifact_id is None:
        return _clarification(_no_target_speech(ctx))
    device_action = ctx.live.get("device_action")
    raw_fmt = arguments.get("format")
    fmt = str(raw_fmt).lower() if isinstance(raw_fmt, str) and raw_fmt else None
    base_url = getattr(runtime.settings, "artifact_download_origin", "") or ""
    outcome = open_service.open_artifact(
        db,
        device_action,
        artifact_id=artifact_id,
        fmt=fmt,
        base_url=base_url,
        idempotency_key=f"artifact-open:{ctx.call_id or uuid.uuid4()}",
    )
    if outcome.ok and outcome.error_class is None:
        execution, terminal = (
            EXECUTION_EXECUTED,
            (TERMINAL_VERIFIED if outcome.state == "opened" else TERMINAL_UNVERIFIED),
        )
    elif outcome.ok:
        # Fetched but the device could not open it (DEVICE_PROTOCOL.md §6k step 10):
        # the fetch DID happen — never presented as a plain failure.
        execution, terminal = EXECUTION_EXECUTED, TERMINAL_UNVERIFIED
    else:
        execution, terminal = EXECUTION_REFUSED, TERMINAL_FAILED
    _ledger(
        db,
        event_type=EVENT_TYPE_ARTIFACT_OPENED,
        action="artifact.open",
        summary=f"artifact.open -> {outcome.state or outcome.error_class}",
        detail={"artifact_id": str(artifact_id), "format": outcome.format, "state": outcome.state},
    )
    return _receipt(
        ctx,
        capability=TOOL_ARTIFACT_OPEN,
        requested_state="opened",
        execution=execution,
        terminal=terminal,
        server={
            "artifact_id": outcome.artifact_id,
            "format": outcome.format,
            "state": outcome.state,
        },
        speech=outcome.speech,
        error_class=outcome.error_class,
        extra={
            "artifact_id": outcome.artifact_id,
            "format": outcome.format,
            "state": outcome.state,
            "window_title": outcome.window_title,
        },
    )


# -------------------------------------------------------------------- artifact.list


def artifact_list(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """ "Neler ürettin?" (spec §5): the owner's most recently made artifacts."""
    del arguments
    db = _require_db(ctx, TOOL_ARTIFACT_LIST)
    items = service.list_artifacts(db, limit=10)
    _ledger(
        db,
        event_type=EVENT_TYPE_ARTIFACT_LISTED,
        action="artifact.list",
        summary=f"artifact.list -> {len(items)} sonuç",
        detail={"count": len(items)},
    )
    if not items:
        speech = "Henüz bir şey üretmedim efendim."
    else:
        names = ", ".join(f"{a.title} ({_KIND_LABELS.get(a.kind, a.kind)})" for a in items[:5])
        speech = f"Şunları ürettim efendim: {names}."
    return _receipt(
        ctx,
        capability=TOOL_ARTIFACT_LIST,
        requested_state="listed",
        execution=EXECUTION_NOOP,
        terminal=TERMINAL_ALREADY,
        server={"count": len(items)},
        speech=speech,
        extra={
            "artifacts": [
                {"artifact_id": str(a.id), "title": a.title, "kind": a.kind, "state": a.state}
                for a in items
            ]
        },
    )


# ------------------------------------------------------------------ registration


# --------------------------------------------------------- B42: the lifecycle tools

TOOL_ARTIFACT_EDIT: Final = "artifact.edit"
TOOL_ARTIFACT_CLONE: Final = "artifact.clone"
TOOL_ARTIFACT_DELETE: Final = "artifact.delete"
TOOL_ARTIFACT_COMPARE: Final = "artifact.compare"
SPEECH_DELETE_DENIED: Final = "Silme politikanız artefakt silmeye izin vermiyor efendim."


def _lifecycle_refusal(
    ctx: ToolContext,
    tool: str,
    requested_state: str,
    exc: artifact_lifecycle.ArtifactLifecycleError,
) -> dict[str, Any]:
    return _receipt(
        ctx,
        capability=tool,
        requested_state=requested_state,
        execution=EXECUTION_REFUSED,
        terminal=TERMINAL_FAILED,
        server={"reason": exc.code},
        speech=exc.speech,
        error_class=exc.code,
    )


def artifact_edit(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """ "Bu belgeye 'Riskler' bölümünü ekle." (B42 req 410) - the model names the edit
    as structured ops; the owner's own numbers (the router's ``spoken_numbers``) are the
    only numbers the edit may introduce."""
    db = _require_db(ctx, TOOL_ARTIFACT_EDIT)
    runtime = _artifacts_runtime(ctx, TOOL_ARTIFACT_EDIT)
    artifact_id = _resolve_artifact_id(ctx, arguments)
    if artifact_id is None:
        return _clarification(_no_target_speech(ctx))
    turn = _turn_record(ctx)
    raw_ops = arguments.get("ops")
    if not isinstance(raw_ops, list) or not raw_ops:
        return _clarification("Neyi nasıl değiştireyim efendim?")
    numbers = turn.get("spoken_numbers")
    payload = {"ops": raw_ops, "spoken_numbers": list(numbers) if isinstance(numbers, list) else []}
    try:
        edit = artifact_lifecycle.ArtifactEdit.model_validate(payload)
        result = artifact_lifecycle.edit_artifact(
            db,
            runtime.store,
            artifact_id,
            edit,
            actor=Actor(ACTOR_OWNER_VOICE, ref=ctx.call_id, session_id=str(ctx.session_id)),
        )
    except artifact_lifecycle.ArtifactLifecycleError as exc:
        return _lifecycle_refusal(ctx, TOOL_ARTIFACT_EDIT, "edited", exc)
    except (ValidationError, ValueError) as exc:
        # The spec's own rules (never invented, no formula injection) hold on version
        # two exactly as on version one - the SAME refusal shapes create uses, naming
        # the ref and nothing else.
        detail = str(exc)
        for pattern, error_class, speech in (
            (_FORMULA_INJECTION_REF_RE, ERROR_FORMULA_INJECTION, SPEECH_FORMULA_INJECTION),
            (_INVENTED_NUMBER_REF_RE, ERROR_INVENTED_NUMBER, SPEECH_INVENTED_NUMBER),
        ):
            match = pattern.search(detail)
            if match:
                return _receipt(
                    ctx,
                    capability=TOOL_ARTIFACT_EDIT,
                    requested_state="edited",
                    execution=EXECUTION_REFUSED,
                    terminal=TERMINAL_FAILED,
                    server={"reason": error_class, "ref": match.group(1)},
                    speech=speech,
                    error_class=error_class,
                    extra={"failing_ref": match.group(1)},
                )
        return _clarification("Düzenlemeyi anlayamadım efendim; neyi değiştireyim?")
    _ledger(
        db,
        event_type=EVENT_TYPE_ARTIFACT_EDITED,
        action="artifact.edit",
        summary=f"artifact.edit -> {artifact_id} v{result.version} ({len(edit.ops)} op)",
        detail={
            "artifact_id": str(artifact_id),
            "version": result.version,
            "ops": [o.op for o in edit.ops],
        },
    )
    valid = result.all_valid
    return _receipt(
        ctx,
        capability=TOOL_ARTIFACT_EDIT,
        requested_state="edited",
        execution=EXECUTION_EXECUTED,
        terminal=TERMINAL_VERIFIED if valid else TERMINAL_UNVERIFIED,
        server=result.as_dict(),
        speech=(
            f"Düzenledim efendim; sürüm {result.version} hazır."
            if valid
            else f"Düzenledim efendim; sürüm {result.version} hazır ama bir biçim doğrulanamadı."
        ),
        extra={"artifact_id": str(artifact_id), "version": result.version},
    )


def artifact_clone(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """ "Bu belgeyi kopyala." (B42 req 411)."""
    db = _require_db(ctx, TOOL_ARTIFACT_CLONE)
    runtime = _artifacts_runtime(ctx, TOOL_ARTIFACT_CLONE)
    artifact_id = _resolve_artifact_id(ctx, arguments)
    if artifact_id is None:
        return _clarification(_no_target_speech(ctx))
    turn = _turn_record(ctx)
    title = turn.get("artifact_title") if isinstance(turn.get("artifact_title"), str) else None
    title = title or (
        str(arguments.get("title")) if isinstance(arguments.get("title"), str) else None
    )
    try:
        result = artifact_lifecycle.clone_artifact(
            db,
            runtime.store,
            artifact_id,
            title=title,
            actor=Actor(ACTOR_OWNER_VOICE, ref=ctx.call_id, session_id=str(ctx.session_id)),
        )
    except artifact_lifecycle.ArtifactLifecycleError as exc:
        return _lifecycle_refusal(ctx, TOOL_ARTIFACT_CLONE, "cloned", exc)
    focus_module.set_focus(
        db,
        FOCUS_KIND_ARTIFACT,
        str(result.artifact_id),
        label=title or "kopya",
        source="artifact_clone",
    )
    _ledger(
        db,
        event_type=EVENT_TYPE_ARTIFACT_CLONED,
        action="artifact.clone",
        summary=f"artifact.clone -> {result.artifact_id} from {artifact_id}",
        detail={"artifact_id": str(result.artifact_id), "from": str(artifact_id)},
    )
    return _receipt(
        ctx,
        capability=TOOL_ARTIFACT_CLONE,
        requested_state="cloned",
        execution=EXECUTION_EXECUTED,
        terminal=TERMINAL_VERIFIED if result.all_valid else TERMINAL_UNVERIFIED,
        server=result.as_dict(),
        speech="Kopyaladım efendim; kopya artık odakta.",
        extra={"artifact_id": str(result.artifact_id), "from": str(artifact_id)},
    )


def artifact_delete(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """ "Bu belgeyi sil." then "Evet, sil." (B42 req 412) - under the owner's policy."""
    db = _require_db(ctx, TOOL_ARTIFACT_DELETE)
    runtime = _artifacts_runtime(ctx, TOOL_ARTIFACT_DELETE)
    artifact_id = _resolve_artifact_id(ctx, arguments)
    if artifact_id is None:
        return _clarification(_no_target_speech(ctx))
    turn = _turn_record(ctx)
    confirmed = bool(turn.get("artifact_confirm")) or bool(arguments.get("confirm"))
    policy = str(getattr(runtime.settings, "artifact_delete_policy", "confirm") or "confirm")
    try:
        outcome = artifact_lifecycle.delete_artifact(
            db, runtime.store, artifact_id, policy=policy, confirmed=confirmed
        )
    except artifact_lifecycle.ArtifactLifecycleError as exc:
        return _lifecycle_refusal(ctx, TOOL_ARTIFACT_DELETE, "deleted", exc)
    if outcome.status == "needs_confirmation":
        return {
            "status": "needs_confirmation",
            "speech": outcome.speech,
            "artifact_id": str(artifact_id),
            "candidates": [],
        }
    if outcome.status == "denied":
        return _receipt(
            ctx,
            capability=TOOL_ARTIFACT_DELETE,
            requested_state="deleted",
            execution=EXECUTION_REFUSED,
            terminal=TERMINAL_FAILED,
            server={"policy": policy},
            speech=SPEECH_DELETE_DENIED,
            error_class=artifact_lifecycle.ERROR_DELETE_DENIED,
        )
    _ledger(
        db,
        event_type=EVENT_TYPE_ARTIFACT_DELETED,
        action="artifact.delete",
        summary=f"artifact.delete -> {artifact_id} ({outcome.renders_removed} render)",
        detail={
            "artifact_id": str(artifact_id),
            "policy": policy,
            "renders_removed": outcome.renders_removed,
        },
    )
    return _receipt(
        ctx,
        capability=TOOL_ARTIFACT_DELETE,
        requested_state="deleted",
        execution=EXECUTION_EXECUTED,
        terminal=TERMINAL_VERIFIED,
        server=outcome.as_dict(),
        speech=outcome.speech,
        extra={"artifact_id": str(artifact_id), "renders_removed": outcome.renders_removed},
    )


def artifact_compare(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """ "Öncekiyle karşılaştır." / "İkisini karşılaştır." (B42 req 415/416): the current
    artifact against its previous version, or against the previous artifact in focus."""
    db = _require_db(ctx, TOOL_ARTIFACT_COMPARE)
    _artifacts_runtime(ctx, TOOL_ARTIFACT_COMPARE)
    current_id = _resolve_artifact_id(ctx, arguments, default="current")
    if current_id is None:
        return _clarification(_no_target_speech(ctx))
    against = arguments.get("against")
    other_id: uuid.UUID | None = None
    other_version: int | None = None
    if against == "previous_artifact":
        entry = focus_module.previous(db, FOCUS_KIND_ARTIFACT)
        if entry is not None:
            try:
                other_id = uuid.UUID(entry.object_id)
            except ValueError:
                other_id = None
        if other_id is None:
            return _clarification(SPEECH_NO_PREVIOUS_ARTIFACT)
    else:
        artifact = service.get_artifact(db, current_id)
        if artifact is None:
            return _clarification(_no_target_speech(ctx))
        if artifact.current_version < 2:
            return _clarification("Bu artefaktın karşılaştırılacak önceki sürümü yok efendim.")
        other_id = current_id
        other_version = artifact.current_version - 1
    try:
        result = artifact_lifecycle.compare_versions(
            db, other_id, current_id, a_version=other_version
        )
    except artifact_lifecycle.ArtifactLifecycleError as exc:
        return _clarification(exc.speech)
    return {
        "speech": result["speech"],
        "comparison": result["comparison"],
        "diff": result["diff"],
        "a": result["a"],
        "b": result["b"],
    }


def register_artifacts_tools(reg: ToolRegistry) -> ToolRegistry:
    """Register all five tools (module docstring: ONE line in ``default_registry``)."""
    from app.voice.realtime_sessions.tools import ToolSpec

    reg.register(
        ToolSpec(
            name=TOOL_ARTIFACT_CREATE,
            description=(
                "Sahibin sözlerinden YENİ bir dosya (belge/tablo/sunum/veri listesi/"
                "sayfa) üretir: 'bana bir bütçe tablosu yap: kira 12000, maaş 45000', "
                "'toplantı notlarını Word belgesi yap', 'üç slaytlık bir sunum hazırla: "
                "giriş, bulgular, sonuç'. 'spec' alanına yapıyı ver (kind'a göre "
                "sections/sheets/slides/rows); sahibin söylediği HER SAYIYI aynen "
                "aktar, kendinden sayı UYDURMA — sunucu uydurulan sayıyı reddeder. "
                "Dönen 'speech' metnini aynen oku."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "kind": {
                        "type": "string",
                        "enum": ["document", "spreadsheet", "presentation", "dataset", "page"],
                    },
                    "title": {"type": "string", "maxLength": 500},
                    "spec": {"type": "object"},
                },
                "additionalProperties": False,
            },
            handler=artifact_create,
        )
    )
    reg.register(
        ToolSpec(
            name=TOOL_ARTIFACT_EDIT,
            description=(
                "ODAKTAKİ artefaktı DÜZENLER ve yeni bir SÜRÜM yapar: 'bu belgeye Riskler bölümünü "
                "ekle', 'başlığı X yap', 'tabloya şu satırı ekle', 'sunuma bir slayt ekle'. 'ops' "
                "alanına yapısal düzenlemeleri ver "
                "(set_title / append_section / replace_section / remove_section / "
                "append_slide / append_row / append_bullet). Sahibin söylemediği hiçbir sayıyı "
                "ekleme."
                " Dönen 'speech' metnini aynen oku."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "target": {"type": "string", "maxLength": 32},
                    **{"ops": {"type": "array", "items": {"type": "object"}, "maxItems": 20}},
                },
                "additionalProperties": False,
            },
            handler=artifact_edit,
        )
    )
    reg.register(
        ToolSpec(
            name=TOOL_ARTIFACT_CLONE,
            description=(
                "ODAKTAKİ artefaktı KOPYALAR (yeni bir artefakt, kaynağı kayıtlı): 'bunu kopyala', "
                "'belgeyi çoğalt'. 'title' alanına sahibin söylediği yeni adı ver."
                " Dönen 'speech' metnini aynen oku."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "target": {"type": "string", "maxLength": 32},
                    **{"title": {"type": "string", "maxLength": 500}},
                },
                "additionalProperties": False,
            },
            handler=artifact_clone,
        )
    )
    reg.register(
        ToolSpec(
            name=TOOL_ARTIFACT_DELETE,
            description=(
                "ODAKTAKİ artefaktı sahibin silme politikasına göre SİLER: 'bunu sil', 'belgeyi "
                "sil'. Politika onay istiyorsa 'needs_confirmation' döner; sahip 'Evet, sil' derse "
                "'confirm': true ile yeniden çağır."
                " Dönen 'speech' metnini aynen oku."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "target": {"type": "string", "maxLength": 32},
                    **{"confirm": {"type": "boolean"}},
                },
                "additionalProperties": False,
            },
            handler=artifact_delete,
        )
    )
    reg.register(
        ToolSpec(
            name=TOOL_ARTIFACT_COMPARE,
            description=(
                "ODAKTAKİ artefaktı önceki sürümüyle ya da önceki artefaktla KARŞILAŞTIRIR: "
                "'öncekiyle karşılaştır', 'ikisini karşılaştır', 'ne değişti'. 'against' alanı "
                "'previous_version' (varsayılan) ya da 'previous_artifact'."
                " Dönen 'speech' metnini aynen oku."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "target": {"type": "string", "maxLength": 32},
                    **{
                        "against": {
                            "type": "string",
                            "enum": ["previous_version", "previous_artifact"],
                        }
                    },
                },
                "additionalProperties": False,
            },
            handler=artifact_compare,
        )
    )
    reg.register(
        ToolSpec(
            name=TOOL_ARTIFACT_RENDER,
            description=(
                "ODAKTAKİ dosyayı EK bir formatta üretir: 'bunu PDF yap', 'bunu Excel "
                "yap'. Hangi dosya olduğunu SUNUCU çözer; 'format' alanına istenen "
                "biçimi ver (pdf/docx/html/md/txt/xlsx/csv/pptx/json). Dönen 'speech' "
                "metnini aynen oku."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "target": {"type": "string", "maxLength": 200},
                    "format": {"type": "string", "maxLength": 16},
                },
                "additionalProperties": False,
            },
            handler=artifact_render,
        )
    )
    reg.register(
        ToolSpec(
            name=TOOL_ARTIFACT_VALIDATE,
            description=(
                "ODAKTAKİ dosyanın DOĞRU üretilip üretilmediğini bağımsız bir "
                "okuyucuyla yeniden kontrol eder: 'bu dosya doğru mu?'. İçeriği "
                "UYDURMAZ; sunucunun gerçek kontrolünü okur. Dönen 'speech' metnini "
                "aynen oku."
            ),
            parameters={
                "type": "object",
                "properties": {"target": {"type": "string", "maxLength": 200}},
                "additionalProperties": False,
            },
            handler=artifact_validate,
        )
    )
    reg.register(
        ToolSpec(
            name=TOOL_ARTIFACT_OPEN,
            description=(
                "ODAKTAKİ (ya da bir öncekini) dosyayı sahibin bilgisayarında AÇAR: "
                "'bunu aç', 'son ürettiğin dosyayı aç'. Hangi dosya olduğunu SUNUCU "
                "çözer. Dönen 'speech' metnini aynen oku; kendi bilginle 'açtım' deme."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "target": {"type": "string", "maxLength": 200},
                    "format": {"type": "string", "maxLength": 16},
                },
                "additionalProperties": False,
            },
            handler=artifact_open,
        )
    )
    reg.register(
        ToolSpec(
            name=TOOL_ARTIFACT_LIST,
            description=(
                "Sahip için son üretilen dosyaları listeler: 'neler ürettin?'. Dönen "
                "'speech' metnini aynen oku."
            ),
            parameters={"type": "object", "properties": {}, "additionalProperties": False},
            handler=artifact_list,
        )
    )
    return reg


__all__ = [
    "ARTIFACT_TOOL_NAMES",
    "TOOL_ARTIFACT_CREATE",
    "TOOL_ARTIFACT_LIST",
    "TOOL_ARTIFACT_OPEN",
    "TOOL_ARTIFACT_RENDER",
    "TOOL_ARTIFACT_VALIDATE",
    "artifact_create",
    "artifact_list",
    "artifact_open",
    "artifact_render",
    "artifact_validate",
    "register_artifacts_tools",
]
