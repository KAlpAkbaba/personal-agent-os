"""The Native App Factory's voice tools (docs/M28_NATIVE_APP_FACTORY_SPEC.md §6).

Eight tools - ``native.create | build | package | install | launch | check | fix |
rebuild`` - registered from ``tools.default_registry()`` by ONE added line
(:func:`register_native_tools`), the same discipline ``tools_apps``/``tools_creative``
already establish for their own families.

Thin adapters, and deliberately so: every decision that could be wrong lives in
``app.nativefactory`` (the spec's one door ``parse_spec``, the stack rule ``choose``,
the lifecycle ``plan_build``/``generate``/``build_and_test``/``publish_and_validate``,
and the independent reader behind them). What these functions do is resolve the
owner's own words into arguments, call that lifecycle, and read the ROW back as
speech via ``receipt_for`` - never compose a sentence from what the call was hoping
for. The milestone's rule, restated where a voice tool could most easily break it:
**a receipt is a read-back.** ``native.build`` says "hazır" only when the row says
``verified``, which only an independent reader can make it say.

The TARGET the owner named ("EXE" -> ``windows_exe``, "kurulum" -> ``windows_msix``,
"APK" -> ``android_apk``) comes from the ONE router
(``ctx.context["last_utterance"]["native_target"]``, ``app.voice.intents``) and is
preferred over the model's own argument - the same "owner's words win" rule
``creative_format``/``scene_kind`` already follow.

**What is NOT here, and why.** The build itself runs on the DEVICE (spec §5: bounded
Job Object children under the authorised ``native`` root), so the Cloud Core holds no
compiler of its own. The runner, the authorised root and the measured toolchain are
therefore INJECTED through ``ctx.live`` (``native_runner``, ``native_root``,
``native_toolchain``) exactly as ``creative_service``/``device_action`` are. With none
registered - today's Cloud Core - every tool that would need one answers with an
honest ``dependency_unavailable`` naming the reason, and NOT with a hopeful sentence.
That refusal is the feature: this milestone exists to stop "I built it" from being
said by anything that did not.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

from sqlalchemy import select

from app.actions.receipt import (
    EXECUTION_EXECUTED,
    EXECUTION_FAILED,
    EXECUTION_NOOP,
    EXECUTION_REFUSED,
    TERMINAL_ALREADY,
    TERMINAL_FAILED,
    TERMINAL_UNVERIFIED,
    TERMINAL_VERIFIED,
    ActionReceipt,
    record_receipt,
)
from app.ledger.vocabulary import SUBSYSTEM_NATIVEFACTORY
from app.logging import get_logger
from app.nativefactory.device_build import build_on_device
from app.nativefactory.models import (
    STATE_FAILED,
    STATE_MISMATCH,
    STATE_UNAVAILABLE,
    STATE_UNVERIFIED,
    STATE_VERIFIED,
    NativeBuildRow,
)
from app.nativefactory.packaging import PackagingError, make_portable_zip
from app.nativefactory.service import (
    build_and_test,
    generate,
    plan_build,
    publish_and_validate,
    receipt_for,
)
from app.nativefactory.spec import (
    ANDROID_TARGETS,
    NATIVE_TARGETS,
    NATIVE_TEMPLATES,
    TARGET_WINDOWS_EXE,
    TARGET_WINDOWS_MSIX,
    TARGET_WINDOWS_PORTABLE,
    TEMPLATE_COUNTER_MOBILE,
    TEMPLATE_NOTES_DESKTOP,
    NativeFactoryError,
)
from app.nativefactory.stacks import (
    ToolchainFacts,
    choose,
    choose_on_device,
    detect,
    refuse_ios,
)
from app.voice.errors import VoiceError, VoiceErrorClass

if TYPE_CHECKING:
    from app.voice.realtime_sessions.tools import ToolContext, ToolRegistry

logger = get_logger("app.voice.realtime_sessions.tools_native")

TOOL_NATIVE_CREATE: Final = "native.create"
TOOL_NATIVE_BUILD: Final = "native.build"
TOOL_NATIVE_PACKAGE: Final = "native.package"
TOOL_NATIVE_INSTALL: Final = "native.install"
TOOL_NATIVE_LAUNCH: Final = "native.launch"
TOOL_NATIVE_CHECK: Final = "native.check"
TOOL_NATIVE_FIX: Final = "native.fix"
TOOL_NATIVE_REBUILD: Final = "native.rebuild"

NATIVE_TOOL_NAMES: Final[tuple[str, ...]] = (
    TOOL_NATIVE_CREATE,
    TOOL_NATIVE_BUILD,
    TOOL_NATIVE_PACKAGE,
    TOOL_NATIVE_INSTALL,
    TOOL_NATIVE_LAUNCH,
    TOOL_NATIVE_CHECK,
    TOOL_NATIVE_FIX,
    TOOL_NATIVE_REBUILD,
)

#: Error classes, in the project taxonomy's own spelling (app.voice.errors). Named here
#: so tests assert the exact value rather than a substring.
ERROR_DEPENDENCY_UNAVAILABLE: Final = str(VoiceErrorClass.DEPENDENCY_UNAVAILABLE)
ERROR_VALIDATION: Final = str(VoiceErrorClass.VALIDATION_ERROR)
#: Not in the voice taxonomy on purpose: "there is no macOS on this machine" is not a
#: dependency that could become available, and calling it one would imply an owner
#: action exists. ``app.nativefactory.stacks.refuse_ios`` uses the same word.
ERROR_PLATFORM_UNREACHABLE: Final = "platform_unreachable"

#: The sentences the owner hears when a step cannot happen. Spelled here rather than
#: inline so the unit tests assert the exact wording (the same discipline
#: ``tools.RESEARCH_START_NO_DEVICE_TR`` already keeps).
SPEECH_NO_BUILD_YET = "Henüz derlenmiş bir uygulama yok efendim; önce yapmam gerekiyor."
SPEECH_NO_RUNNER = (
    "Derleme bu makinede değil, cihazda çalışıyor efendim; şu an bağlı bir derleyici yok, "
    "o yüzden derledim diyemem."
)
SPEECH_ANDROID_NEEDS_JDK = (
    "Android SDK burada ama Java yok efendim: Gradle da apksigner da Java üstünde çalışır. "
    "Bir JDK kurulunca aynı hat çalışır (madde 33)."
)
SPEECH_INSTALL_NEEDS_DEVICE = (
    "Kurulumu buradan yapamam efendim: paket cihazda, sizin oturumunuzda kurulur. "
    "Bu tarafta kurdum diyemem."
)
SPEECH_LAUNCH_NEEDS_DEVICE = (
    "Uygulamayı buradan açamam efendim: pencereyi açan ve süren taraf cihaz. "
    "Açtım diyemem."
)
SPEECH_NOTHING_TO_FIX = "Düzeltilecek bir hata görünmüyor efendim"
SPEECH_MSIX_IS_THE_DEVICES = (
    "İmzalı MSIX kurulumu cihazda üretilir efendim: makeappx ve imzalama oradaki "
    "çalışma kökünde koşar, buradan imzalanmış paket ürettim diyemem."
)
SPEECH_FIX_NEEDS_WORKER = (
    "Hatayı görüyorum efendim ama düzeltmeyi kendi başıma yazamıyorum: kodlayıcı ucu "
    "henüz bağlı değil. Hatanın kendisini okuyabilirim."
)


# --------------------------------------------------------------------- primitives


def _require_db(ctx: ToolContext, tool: str) -> Any:
    if ctx.db is None:
        raise VoiceError(
            VoiceErrorClass.DEPENDENCY_UNAVAILABLE,
            f"{tool} needs the durable state; no database on this session",
        )
    return ctx.db


def _facts(ctx: ToolContext) -> ToolchainFacts:
    """What is really on the machine that will build. Injected in a test and in the
    lab; MEASURED otherwise - never read from a file written yesterday (spec §1's own
    rule, and the M27 lesson about a registry key without an executable)."""
    injected = ctx.live.get("native_toolchain")
    return injected if isinstance(injected, ToolchainFacts) else detect()


def _local_ready(ctx: ToolContext, facts: ToolchainFacts) -> bool:
    """A runner, a root and a compiler on THIS machine: the lab, never production."""
    return (
        ctx.live.get("native_runner") is not None
        and ctx.live.get("native_root") is not None
        and facts.dotnet is not None
    )


def _builds_on_device(ctx: ToolContext, facts: ToolchainFacts) -> bool:
    """Whether the build will run on the enrolled device rather than here (ADR-0119).

    The SAME decision ``_run_lifecycle`` makes when it dispatches, taken here too so that
    PLANNING asks the machine that will build. Asking this one instead is what opened every
    production Windows row as `unavailable` (M28 row 26.16).
    """
    return not _local_ready(ctx, facts) and ctx.live.get("device_action") is not None


def _turn_record(ctx: ToolContext) -> dict[str, Any]:
    """The ONE router's record of this turn (``native_target``/``native_ref``)."""
    return dict(ctx.context.get("last_utterance") or {})


def _target_word(ctx: ToolContext, arguments: dict[str, Any]) -> str | None:
    """The target the owner's WORDS named, else the model's own argument, else None."""
    said = _turn_record(ctx).get("native_target")
    if isinstance(said, str) and said in NATIVE_TARGETS:
        return said
    arg = arguments.get("target")
    return arg if isinstance(arg, str) and arg in NATIVE_TARGETS else None


def _receipt(
    ctx: ToolContext,
    *,
    capability: str,
    requested_state: str,
    execution: str,
    terminal: str,
    speech: str,
    server: dict[str, Any] | None = None,
    error_class: str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    now = datetime.now(UTC)
    receipt = ActionReceipt(
        action_id=str(ctx.call_id or uuid.uuid4()),
        capability=capability,
        requested_state=requested_state,
        execution_status=execution,
        terminal_status=terminal,
        observed_after={"server": dict(server or {}), "local": {}},
        evidence_refs=[],
        error_class=error_class,
        speech=speech,
        started_at=now,
        completed_at=now,
        session_id=str(ctx.session_id),
        observed_at=now,
    )
    if ctx.db is not None:
        record_receipt(ctx.db, receipt, SUBSYSTEM_NATIVEFACTORY)
    out = receipt.as_dict()
    if extra:
        out.update(extra)
    return out


def _refused(
    ctx: ToolContext,
    *,
    capability: str,
    requested_state: str,
    speech: str,
    error_class: str,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return _receipt(
        ctx,
        capability=capability,
        requested_state=requested_state,
        execution=EXECUTION_REFUSED,
        terminal=TERMINAL_FAILED,
        speech=speech,
        server={"reason": error_class},
        error_class=error_class,
        extra=extra,
    )


def _row_summary(row: NativeBuildRow) -> dict[str, Any]:
    """What a caller (and the corpus) may assert on, straight off the ROW."""
    return {
        "build_id": str(row.id),
        "slug": row.slug,
        "target": row.target,
        "stack": row.stack,
        "version": row.version,
        "state": row.state,
        "artifact": dict(row.artifact_json or {}) or None,
        "error_class": row.error_class,
    }


#: The row state -> what the receipt may claim. ``verified`` is the ONLY state that
#: may be claimed (docs/M18_ACTION_CONTRACT.md's own TERMINAL_CLAIMABLE rule, applied
#: to this milestone's own verdict): produced-but-unread and produced-but-disagreeing
#: are both ``unverified``, which is exactly what ``receipt_for`` already says out loud.
_TERMINAL_BY_STATE: Final[dict[str, str]] = {
    STATE_VERIFIED: TERMINAL_VERIFIED,
    STATE_MISMATCH: TERMINAL_UNVERIFIED,
    STATE_UNVERIFIED: TERMINAL_UNVERIFIED,
    STATE_FAILED: TERMINAL_FAILED,
    STATE_UNAVAILABLE: TERMINAL_FAILED,
}


def _latest_row(db: Any, *, target: str | None = None) -> NativeBuildRow | None:
    """The most recent build row, optionally of one target. Spec §7: the reference
    resolves through IDS on the stack, never through a fuzzy title - so a target word
    narrows, and nothing here ever matches on a display name."""
    stmt = select(NativeBuildRow).order_by(NativeBuildRow.created_at.desc())
    if target:
        stmt = stmt.where(NativeBuildRow.target == target)
    return db.execute(stmt.limit(1)).scalars().first()


def _resolve_row(ctx: ToolContext, db: Any, arguments: dict[str, Any]) -> NativeBuildRow | None:
    raw = arguments.get("build_id")
    if isinstance(raw, str) and raw:
        try:
            row = db.get(NativeBuildRow, uuid.UUID(raw))
        except ValueError:
            row = None
        if row is not None:
            return row
    target = _target_word(ctx, arguments)
    return _latest_row(db, target=target) or _latest_row(db)


def _template_for(target: str | None, arguments: dict[str, Any]) -> str:
    named = arguments.get("template")
    if isinstance(named, str) and named in NATIVE_TEMPLATES:
        return named
    return TEMPLATE_COUNTER_MOBILE if target in ANDROID_TARGETS else TEMPLATE_NOTES_DESKTOP


# ------------------------------------------------------------------ native.create


def native_create(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """ "Bana Windows için masaüstü uygulaması yap." / "Android sürümünü yap." (spec §6).

    Validates through ``parse_spec`` (the one door), opens one ROW PER TARGET - including
    a row for a target this machine cannot reach, which is ``unavailable`` with its
    reason rather than a silence (spec §4) - and speaks the stack choice with the reason
    the owner can interrogate ("WPF seçtim: Windows masaüstü, form arayüzü, kurulu araç
    .NET 10", spec §3).
    """
    db = _require_db(ctx, TOOL_NATIVE_CREATE)
    # The owner's own sentence, as the model relays it. The ROUTER already refuses an
    # iOS request (it resolves to nothing in this family at all, ``app.voice.intents``
    # module comment, rule 5), so this is defence in depth for the one path that
    # bypasses it: a model calling this tool directly.
    request_text = str(arguments.get("request") or "")
    ios = refuse_ios(request_text) or refuse_ios(str(arguments.get("targets") or ""))
    if ios is not None:
        return _refused(
            ctx,
            capability=TOOL_NATIVE_CREATE,
            requested_state="ios",
            speech=ios.reason,
            error_class=ERROR_PLATFORM_UNREACHABLE,
        )

    said_target = _target_word(ctx, arguments)
    raw_targets = arguments.get("targets")
    targets = (
        [t for t in raw_targets if t in NATIVE_TARGETS] if isinstance(raw_targets, list) else []
    )
    if not targets:
        targets = [said_target or TARGET_WINDOWS_EXE]
    payload: dict[str, Any] = {
        "name": str(arguments.get("name") or "Uygulamam"),
        "template": _template_for(targets[0], arguments),
        "targets": targets,
        "version": str(arguments.get("version") or "0.1.0"),
    }
    if isinstance(arguments.get("title"), str) and arguments["title"]:
        payload["title"] = arguments["title"]

    facts = _facts(ctx)
    on_device = _builds_on_device(ctx, facts)
    try:
        rows = plan_build(db, payload, facts=facts, on_device=on_device)
    except NativeFactoryError as exc:
        return _refused(
            ctx,
            capability=TOOL_NATIVE_CREATE,
            requested_state=",".join(targets),
            speech=exc.speech,
            error_class=ERROR_VALIDATION,
        )

    from app.nativefactory.spec import NativeAppSpec

    first = NativeAppSpec.model_validate(rows[0].spec_json)
    choice = choose_on_device(first) if on_device else choose(first, facts)
    lines = [choice.reason] + [receipt_for(row) for row in rows]
    reachable = [row for row in rows if row.state != STATE_UNAVAILABLE]
    return _receipt(
        ctx,
        capability=TOOL_NATIVE_CREATE,
        requested_state=",".join(targets),
        execution=EXECUTION_EXECUTED if reachable else EXECUTION_REFUSED,
        terminal=TERMINAL_VERIFIED if reachable else TERMINAL_FAILED,
        speech=" ".join(lines),
        server={"builds": [_row_summary(row) for row in rows]},
        error_class=None if reachable else (rows[0].error_class or ERROR_DEPENDENCY_UNAVAILABLE),
        extra={"builds": [_row_summary(row) for row in rows], "stack_choice": choice.as_dict()},
    )


# ------------------------------------------------------- native.build / native.rebuild


def _run_lifecycle(
    ctx: ToolContext,
    row: NativeBuildRow,
    *,
    capability: str,
) -> dict[str, Any]:
    """generate -> build+test -> publish -> an INDEPENDENT read, then the ROW as speech.

    Every early return here is a refusal with the actual reason, because every one of
    them is a case where nothing was built and saying otherwise is the failure mode this
    milestone is named for.
    """
    db = ctx.db
    facts = _facts(ctx)
    if row.state == STATE_UNAVAILABLE:
        return _refused(
            ctx,
            capability=capability,
            requested_state=row.target,
            speech=row.error_message or receipt_for(row),
            error_class=row.error_class or ERROR_DEPENDENCY_UNAVAILABLE,
            extra={"build": _row_summary(row)},
        )
    if row.target in ANDROID_TARGETS and not facts.can_build_android:
        return _refused(
            ctx,
            capability=capability,
            requested_state=row.target,
            speech=SPEECH_ANDROID_NEEDS_JDK,
            error_class=ERROR_DEPENDENCY_UNAVAILABLE,
            extra={"build": _row_summary(row), "owner_action": "33"},
        )
    runner = ctx.live.get("native_runner")
    root = ctx.live.get("native_root")

    if not _local_ready(ctx, facts):
        # M28 row 26.16. Production is a Linux Cloud Core with no .NET SDK, no makeappx and
        # no Windows; the machine that has all three is the owner's enrolled device. So the
        # absence of a LOCAL runner is not the end of the road here -- it is the normal case,
        # and the device path is the real one.
        #
        # `facts` above measures THIS machine, which is why it is not consulted below: a
        # Linux Cloud Core's missing dotnet says nothing about the device's. If the device
        # has no toolchain, its own `project.run` refuses and that refusal reaches the row in
        # the device's own words (`device_build._fail`), which is more useful than a guess
        # made here.
        device = ctx.live.get("device_action")
        if device is not None:
            return _build_on_device(ctx, row, device, capability=capability)
        return _refused(
            ctx,
            capability=capability,
            requested_state=row.target,
            speech=SPEECH_NO_RUNNER,
            error_class=ERROR_DEPENDENCY_UNAVAILABLE,
            extra={"build": _row_summary(row)},
        )

    workdir = Path(root) / f"{row.slug}-{row.target}-{str(row.id)[:8]}"
    row = generate(db, row, workdir / "project", root=Path(root))
    if row.state in (STATE_FAILED, STATE_UNAVAILABLE):
        return _refused(
            ctx,
            capability=capability,
            requested_state=row.target,
            speech=receipt_for(row),
            error_class=row.error_class or ERROR_VALIDATION,
            extra={"build": _row_summary(row)},
        )
    row = build_and_test(db, row, runner, dotnet=facts.dotnet)
    if row.state == STATE_FAILED:
        return _receipt(
            ctx,
            capability=capability,
            requested_state=row.target,
            execution=EXECUTION_FAILED,
            terminal=TERMINAL_FAILED,
            speech=receipt_for(row),
            server=_row_summary(row),
            error_class=row.error_class,
            extra={"build": _row_summary(row)},
        )
    row = publish_and_validate(
        db, row, runner, dotnet=facts.dotnet, out_dir=workdir / "publish"
    )
    return _receipt(
        ctx,
        capability=capability,
        requested_state=row.target,
        execution=EXECUTION_EXECUTED if row.state != STATE_FAILED else EXECUTION_FAILED,
        terminal=_TERMINAL_BY_STATE.get(row.state, TERMINAL_UNVERIFIED),
        speech=receipt_for(row),
        server=_row_summary(row),
        error_class=row.error_class,
        extra={"build": _row_summary(row)},
    )


def _build_on_device(
    ctx: ToolContext,
    row: NativeBuildRow,
    device: Any,
    *,
    capability: str,
) -> dict[str, Any]:
    """The production chain, end to end, on the machine that actually has a compiler.

        NativeAppSpec -> render + the SAME file policy the local path uses
                      -> project.scaffold(root="native") on the enrolled device
                      -> project.run build / project.test / project.run publish
                      -> file.inspect, read back BY THE DEVICE
                      -> validate_against_spec, run HERE on what the device read
                      -> the durable row, and the receipt composed from it

    Nothing is compiled on Cloud Core and nothing is inferred from an exit code. The verdict
    is the same function the lab path uses -- one judge, two sources of facts -- so a row
    cannot say `verified` because a device-shaped path was taken; it says it because the
    version the device read out of the artefact is the version the spec asked for.
    """
    outcome = build_on_device(ctx.db, row, device)
    if not outcome.ok:
        return _receipt(
            ctx,
            capability=capability,
            requested_state=row.target,
            execution=EXECUTION_FAILED,
            terminal=TERMINAL_FAILED,
            speech=receipt_for(row),
            server=_row_summary(row),
            error_class=row.error_class,
            extra={"build": _row_summary(row), "built_on": "device"},
        )
    return _receipt(
        ctx,
        capability=capability,
        requested_state=row.target,
        execution=EXECUTION_EXECUTED,
        terminal=_TERMINAL_BY_STATE.get(row.state, TERMINAL_UNVERIFIED),
        speech=receipt_for(row),
        server=_row_summary(row),
        error_class=row.error_class,
        extra={"build": _row_summary(row), "built_on": "device"},
    )


def native_build(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """ "Bunu EXE olarak çıkar." / "APK üret." (spec §6)."""
    db = _require_db(ctx, TOOL_NATIVE_BUILD)
    row = _resolve_row(ctx, db, arguments)
    if row is None:
        return {"status": "needs_clarification", "speech": SPEECH_NO_BUILD_YET, "candidates": []}
    return _run_lifecycle(ctx, row, capability=TOOL_NATIVE_BUILD)


def native_rebuild(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """ "Yeni sürümü build et." (spec §6) - the SAME spec at the next patch version, as
    a NEW row, so the old artefact's own verdict is never overwritten by the new one's.
    """
    db = _require_db(ctx, TOOL_NATIVE_REBUILD)
    previous = _resolve_row(ctx, db, arguments)
    if previous is None:
        return {"status": "needs_clarification", "speech": SPEECH_NO_BUILD_YET, "candidates": []}
    payload = dict(previous.spec_json or {})
    payload["version"] = _next_version(str(arguments.get("version") or previous.version))
    facts = _facts(ctx)
    try:
        rows = plan_build(
            db, payload, facts=facts, on_device=_builds_on_device(ctx, facts)
        )
    except NativeFactoryError as exc:
        return _refused(
            ctx,
            capability=TOOL_NATIVE_REBUILD,
            requested_state=previous.target,
            speech=exc.speech,
            error_class=ERROR_VALIDATION,
        )
    return _run_lifecycle(ctx, rows[0], capability=TOOL_NATIVE_REBUILD)


def _next_version(version: str) -> str:
    """`0.1.0` -> `0.1.1`. Bounded by the spec's own pattern; a version this cannot
    parse is returned unchanged and ``parse_spec`` refuses it by name."""
    parts = version.split(".")
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        return version
    return f"{parts[0]}.{parts[1]}.{int(parts[2]) + 1}"


# ----------------------------------------------------------------- native.package


def native_package(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """ "Kurulum dosyasını oluştur." (spec §6).

    The portable zip is made HERE, with the standard library, because there is no format
    subtlety to get wrong. The MSIX is not: it is ``makeappx.exe``'s job, it is signed
    with a run-local certificate, and it installs only where that certificate is trusted
    - so when makeappx is not on the machine that will build, this refuses and says so
    rather than producing something that would fail at the owner's double-click.
    """
    db = _require_db(ctx, TOOL_NATIVE_PACKAGE)
    row = _resolve_row(ctx, db, arguments)
    if row is None:
        return {"status": "needs_clarification", "speech": SPEECH_NO_BUILD_YET, "candidates": []}
    target = _target_word(ctx, arguments) or TARGET_WINDOWS_MSIX
    if row.target in ANDROID_TARGETS or target in ANDROID_TARGETS:
        return _refused(
            ctx,
            capability=TOOL_NATIVE_PACKAGE,
            requested_state=target,
            speech=SPEECH_ANDROID_NEEDS_JDK,
            error_class=ERROR_DEPENDENCY_UNAVAILABLE,
            extra={"build": _row_summary(row), "owner_action": "33"},
        )
    publish_dir = Path(row.artifact_path).parent if row.artifact_path else None
    if publish_dir is None or not publish_dir.is_dir():
        return _refused(
            ctx,
            capability=TOOL_NATIVE_PACKAGE,
            requested_state=target,
            speech=(
                f"Önce derlemem gerekiyor efendim: {row.display_name} için "
                f"paketlenecek bir çıktı yok."
            ),
            error_class=ERROR_VALIDATION,
            extra={"build": _row_summary(row)},
        )
    out_path = publish_dir.parent / f"{row.slug}-{row.version}.zip"
    try:
        package = make_portable_zip(publish_dir, out_path)
    except PackagingError as exc:
        return _refused(
            ctx,
            capability=TOOL_NATIVE_PACKAGE,
            requested_state=TARGET_WINDOWS_PORTABLE,
            speech=f"Paketleyemedim efendim: {exc}.",
            error_class=ERROR_VALIDATION,
            extra={"build": _row_summary(row)},
        )
    size_kb = package.path.stat().st_size // 1024
    # What was actually made, and - when an MSIX was what the owner's word meant - what
    # was NOT. A tool that answered "kurulum dosyası" with a zip and left it there would
    # be the same rounding-up this milestone exists to refuse.
    msix_note = f" {SPEECH_MSIX_IS_THE_DEVICES}" if target == TARGET_WINDOWS_MSIX else ""
    return _receipt(
        ctx,
        capability=TOOL_NATIVE_PACKAGE,
        requested_state=TARGET_WINDOWS_PORTABLE,
        execution=EXECUTION_EXECUTED,
        terminal=TERMINAL_VERIFIED,
        speech=(
            f"{row.display_name} paketlendi efendim: {package.path.name}, {size_kb} KB. "
            f"{package.note}{msix_note}"
        ),
        server={"package": str(package.path), "signed": package.signed},
        extra={"build": _row_summary(row), "package_path": str(package.path)},
    )


# ------------------------------------------------- native.install / native.launch


def native_install(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """Install the package this run produced, on the owner's own session (spec §5).

    Cloud Core cannot: the package installs where the owner is, through the device.
    Refused with the reason rather than claimed. Spec §6's own negative - "Kurulumu
    kaldır" of anything this run did not install - is refused by the same rule from the
    other direction: this tool only ever names the row it built.
    """
    db = _require_db(ctx, TOOL_NATIVE_INSTALL)
    row = _resolve_row(ctx, db, arguments)
    installer = ctx.live.get("native_installer")
    if installer is None:
        return _refused(
            ctx,
            capability=TOOL_NATIVE_INSTALL,
            requested_state=(row.target if row is not None else "unknown"),
            speech=SPEECH_INSTALL_NEEDS_DEVICE,
            error_class=ERROR_DEPENDENCY_UNAVAILABLE,
            extra={"build": _row_summary(row) if row is not None else None},
        )
    result = installer.install(row)
    return _receipt(
        ctx,
        capability=TOOL_NATIVE_INSTALL,
        requested_state=row.target if row is not None else "unknown",
        execution=EXECUTION_EXECUTED,
        terminal=TERMINAL_VERIFIED,
        speech=str(result.get("speech") or ""),
        server=dict(result),
    )


def native_launch(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """ "Uygulamayı emülatörde aç." (spec §6).

    Two honest refusals, and they are different: Android cannot be reached at all until
    a JDK exists (owner item 33 - there is no AVD to boot and no APK to install), and
    Windows can be reached only from the DEVICE, which the Cloud Core is not.
    """
    db = _require_db(ctx, TOOL_NATIVE_LAUNCH)
    row = _resolve_row(ctx, db, arguments)
    facts = _facts(ctx)
    target = _target_word(ctx, arguments) or (row.target if row is not None else None)
    if target in ANDROID_TARGETS and not facts.can_build_android:
        return _refused(
            ctx,
            capability=TOOL_NATIVE_LAUNCH,
            requested_state=str(target),
            speech=SPEECH_ANDROID_NEEDS_JDK,
            error_class=ERROR_DEPENDENCY_UNAVAILABLE,
            extra={"build": _row_summary(row) if row is not None else None, "owner_action": "33"},
        )
    launcher = ctx.live.get("native_launcher")
    if launcher is None:
        return _refused(
            ctx,
            capability=TOOL_NATIVE_LAUNCH,
            requested_state=str(target or "unknown"),
            speech=SPEECH_LAUNCH_NEEDS_DEVICE,
            error_class=ERROR_DEPENDENCY_UNAVAILABLE,
            extra={"build": _row_summary(row) if row is not None else None},
        )
    result = launcher.launch(row)
    return _receipt(
        ctx,
        capability=TOOL_NATIVE_LAUNCH,
        requested_state=str(target or "unknown"),
        execution=EXECUTION_EXECUTED,
        terminal=TERMINAL_VERIFIED,
        speech=str(result.get("speech") or ""),
        server=dict(result),
    )


# --------------------------------------------------------- native.check / native.fix


def native_check(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """ "Çalışıyor mu kontrol et." (spec §6) - a QUERY: it reads the row and the facts an
    independent reader already recorded, and drives nothing. What it must never do is
    round ``unverified`` up to "çalışıyor", so the sentence comes from ``receipt_for``,
    which is written not to."""
    db = _require_db(ctx, TOOL_NATIVE_CHECK)
    row = _resolve_row(ctx, db, arguments)
    if row is None:
        return {"status": "needs_clarification", "speech": SPEECH_NO_BUILD_YET, "candidates": []}
    tests = row.tests_json or {}
    extra = ""
    if tests.get("passed") is True and tests.get("summary"):
        extra = f" Testler geçti: {tests['summary']}."
    return {
        "speech": receipt_for(row) + extra,
        "build": _row_summary(row),
        "state": row.state,
        "verified": row.state == STATE_VERIFIED,
    }


def native_fix(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """ "Hata varsa düzelt." (spec §6).

    "varsa" is the whole sentence: with nothing broken, the truthful answer is that
    nothing is broken - a NOOP receipt, never a pretended repair. With something broken
    and no coding worker connected (spec §9: the coding-model seam is inert here), the
    truthful answer is the ERROR ITSELF, read off the row, and an explicit statement
    that the fix was not written.
    """
    db = _require_db(ctx, TOOL_NATIVE_FIX)
    row = _resolve_row(ctx, db, arguments)
    if row is None:
        return {"status": "needs_clarification", "speech": SPEECH_NO_BUILD_YET, "candidates": []}
    broken = row.state in (STATE_FAILED, STATE_MISMATCH, STATE_UNVERIFIED)
    if not broken:
        return _receipt(
            ctx,
            capability=TOOL_NATIVE_FIX,
            requested_state="fix",
            execution=EXECUTION_NOOP,
            terminal=TERMINAL_ALREADY,
            speech=f"{SPEECH_NOTHING_TO_FIX}: {row.display_name} {row.state} durumda.",
            server=_row_summary(row),
            extra={"build": _row_summary(row), "fixed": False},
        )
    worker = ctx.live.get("native_fix_worker")
    detail = row.error_message or row.log_tail or "ayrıntı yok"
    if worker is None:
        return _refused(
            ctx,
            capability=TOOL_NATIVE_FIX,
            requested_state="fix",
            speech=f"{SPEECH_FIX_NEEDS_WORKER} {row.display_name}: {detail[:200]}",
            error_class=ERROR_DEPENDENCY_UNAVAILABLE,
            extra={"build": _row_summary(row), "fixed": False},
        )
    result = worker.fix(row)
    return _receipt(
        ctx,
        capability=TOOL_NATIVE_FIX,
        requested_state="fix",
        execution=EXECUTION_EXECUTED,
        terminal=TERMINAL_VERIFIED,
        speech=str(result.get("speech") or ""),
        server=dict(result),
        extra={"build": _row_summary(row), "fixed": True},
    )


# ------------------------------------------------------------------- registration


def register_native_tools(reg: ToolRegistry) -> ToolRegistry:
    from app.voice.realtime_sessions.tools import ToolSpec

    target_enum = {"type": "string", "enum": list(NATIVE_TARGETS)}
    build_ref = {"type": "string", "maxLength": 64}

    reg.register(
        ToolSpec(
            name=TOOL_NATIVE_CREATE,
            description=(
                "Windows masaüstü ya da Android için YENİ bir yerel uygulama projesi "
                "planlar: 'Bana Windows için masaüstü uygulaması yap' / 'Android "
                "sürümünü yap' denince bu araç çağrılır. Üretilemeyen hedefi sessizce "
                "atlamaz, nedenini söyler. Dönen 'speech' metnini aynen oku."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "maxLength": 60},
                    "title": {"type": "string", "maxLength": 80},
                    "template": {"type": "string", "enum": list(NATIVE_TEMPLATES)},
                    "targets": {"type": "array", "items": target_enum, "maxItems": 5},
                    "target": target_enum,
                    "version": {"type": "string", "maxLength": 16},
                    "request": {"type": "string", "maxLength": 500},
                },
                "additionalProperties": False,
            },
            handler=native_create,
        )
    )
    reg.register(
        ToolSpec(
            name=TOOL_NATIVE_BUILD,
            description=(
                "Planlanmış uygulamayı DERLER, testlerini çalıştırır, çıktıyı üretir ve "
                "üretilen dosyayı BAĞIMSIZ bir okuyucuya doğrulatır: 'Bunu EXE olarak "
                "çıkar' / 'APK üret'. Doğrulanmadıysa 'hazır' demez. Dönen 'speech' "
                "metnini aynen oku."
            ),
            parameters={
                "type": "object",
                "properties": {"target": target_enum, "build_id": build_ref},
                "additionalProperties": False,
            },
            handler=native_build,
        )
    )
    reg.register(
        ToolSpec(
            name=TOOL_NATIVE_PACKAGE,
            description=(
                "Derlenmiş çıktıyı DAĞITILABİLİR PAKETE çevirir (taşınabilir zip, ya da "
                "makeappx varsa MSIX): 'Kurulum dosyasını oluştur'. Dönen 'speech' "
                "metnini aynen oku."
            ),
            parameters={
                "type": "object",
                "properties": {"target": target_enum, "build_id": build_ref},
                "additionalProperties": False,
            },
            handler=native_package,
        )
    )
    reg.register(
        ToolSpec(
            name=TOOL_NATIVE_INSTALL,
            description=(
                "Bu çalışmanın ürettiği paketi sahibin kendi oturumuna KURAR. Cihaz "
                "tarafı bağlı değilse kurmadığını dürüstçe söyler. Dönen 'speech' "
                "metnini aynen oku."
            ),
            parameters={
                "type": "object",
                "properties": {"build_id": build_ref},
                "additionalProperties": False,
            },
            handler=native_install,
        )
    )
    reg.register(
        ToolSpec(
            name=TOOL_NATIVE_LAUNCH,
            description=(
                "Üretilen uygulamayı ÇALIŞTIRIR - Android için emülatörde: 'Uygulamayı "
                "emülatörde aç'. Açamıyorsa nedenini söyler, açtım demez. Dönen "
                "'speech' metnini aynen oku."
            ),
            parameters={
                "type": "object",
                "properties": {"target": target_enum, "build_id": build_ref},
                "additionalProperties": False,
            },
            handler=native_launch,
        )
    )
    reg.register(
        ToolSpec(
            name=TOOL_NATIVE_CHECK,
            description=(
                "Üretilen uygulamanın DURUMUNU okur (derlendi mi, testler geçti mi, "
                "dosyayı bağımsız okuyucu doğruladı mı): 'Çalışıyor mu kontrol et'. "
                "Hiçbir şeyi değiştirmez. Dönen 'speech' metnini aynen oku."
            ),
            parameters={
                "type": "object",
                "properties": {"target": target_enum, "build_id": build_ref},
                "additionalProperties": False,
            },
            handler=native_check,
        )
    )
    reg.register(
        ToolSpec(
            name=TOOL_NATIVE_FIX,
            description=(
                "Derleme/test/doğrulama hatası varsa DÜZELTMEYE çalışır: 'Hata varsa "
                "düzelt'. Hata yoksa yok der; düzeltemiyorsa hatanın kendisini okur ve "
                "düzeltmediğini söyler. Dönen 'speech' metnini aynen oku."
            ),
            parameters={
                "type": "object",
                "properties": {"build_id": build_ref},
                "additionalProperties": False,
            },
            handler=native_fix,
        )
    )
    reg.register(
        ToolSpec(
            name=TOOL_NATIVE_REBUILD,
            description=(
                "Aynı uygulamayı BİR SONRAKİ SÜRÜM olarak yeniden derler ve doğrular: "
                "'Yeni sürümü build et'. Eski çıktının kararını ezmez, yeni bir kayıt "
                "açar. Dönen 'speech' metnini aynen oku."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "target": target_enum,
                    "build_id": build_ref,
                    "version": {"type": "string", "maxLength": 16},
                },
                "additionalProperties": False,
            },
            handler=native_rebuild,
        )
    )
    return reg


__all__ = [
    "NATIVE_TOOL_NAMES",
    "SPEECH_ANDROID_NEEDS_JDK",
    "SPEECH_FIX_NEEDS_WORKER",
    "SPEECH_INSTALL_NEEDS_DEVICE",
    "SPEECH_LAUNCH_NEEDS_DEVICE",
    "SPEECH_MSIX_IS_THE_DEVICES",
    "SPEECH_NOTHING_TO_FIX",
    "SPEECH_NO_BUILD_YET",
    "SPEECH_NO_RUNNER",
    "TOOL_NATIVE_BUILD",
    "TOOL_NATIVE_CHECK",
    "TOOL_NATIVE_CREATE",
    "TOOL_NATIVE_FIX",
    "TOOL_NATIVE_INSTALL",
    "TOOL_NATIVE_LAUNCH",
    "TOOL_NATIVE_PACKAGE",
    "TOOL_NATIVE_REBUILD",
    "native_build",
    "native_check",
    "native_create",
    "native_fix",
    "native_install",
    "native_launch",
    "native_package",
    "native_rebuild",
    "register_native_tools",
]
