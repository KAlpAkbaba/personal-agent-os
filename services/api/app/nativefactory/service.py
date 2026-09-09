"""The build lifecycle, as rows (docs/M28_NATIVE_APP_FACTORY_SPEC.md §4).

    request -> spec -> generate -> policy -> build -> test -> publish -> package
            -> an INDEPENDENT read of the produced file -> a verdict on the row

Two rules run through every function here, and they are the same two the milestone is
about:

**Nothing is written from hope.** ``artifact_json`` comes from the reader and nowhere else.
A build that exited zero but produced no readable file leaves the row ``unverified`` with
the reason, never ``verified`` with an assumption. The row can say "I do not know"; it
cannot say "it worked" without something having read it.

**Nothing runs outside its root.** Every build happens under the authorised ``native``
root, through :mod:`app.appfactory` policy, with the toolchain measured rather than
assumed. The commands are FIXED - a project cannot name its own compiler invocation, which
is the whole reason M23's manifest carries a key rather than a command line.

The runner is injected. On the Cloud Core it is the device's bounded Job Object child
(spec §5); in a test it is a fake that returns scripted output; in the lab it is a direct
subprocess on this machine. The lifecycle does not know or care, which is what lets the
same code path be proven three ways.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final, Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.appfactory.validation import AppValidationError, validate_files
from app.logging import get_logger
from app.nativefactory.artifacts import (
    ArtifactUnreadable,
    read_artifact,
    validate_against_spec,
)
from app.nativefactory.generator import render
from app.nativefactory.models import (
    MAX_LOG_TAIL_CHARS,
    STATE_BUILDING,
    STATE_FAILED,
    STATE_GENERATING,
    STATE_MISMATCH,
    STATE_PLANNED,
    STATE_TESTING,
    STATE_UNAVAILABLE,
    STATE_UNVERIFIED,
    STATE_VALIDATING,
    STATE_VERIFIED,
    NativeBuildRow,
)
from app.nativefactory.models_wire import wire_step
from app.nativefactory.roots import check_extensions, native_root, resolve_within
from app.nativefactory.spec import NativeAppSpec, NativeFactoryError, parse_spec
from app.nativefactory.stacks import ToolchainFacts, choose, detect
from app.uistate import publish as publish_ui_state
from app.uistate.contract import NATIVE_BUILD_STEPS, UiState

logger = get_logger("app.nativefactory.service")

#: Bounds a build inherits from M25's own runs. A compile that has not finished in this
#: long has not failed in a way another minute would fix.
BUILD_TIMEOUT_S: Final = 20 * 60


@dataclass(frozen=True, slots=True)
class RunResult:
    """What a runner reports. Deliberately small: an exit code and text, nothing the
    lifecycle could mistake for a verdict."""

    exit_code: int
    output: str

    @property
    def ok(self) -> bool:
        return self.exit_code == 0


class BuildRunner(Protocol):
    """Runs one FIXED toolchain command in a project directory.

    A Protocol, so the Cloud Core's device-dispatched runner, the lab's subprocess and a
    test's fake are the same thing to this module. It takes an argv the lifecycle built -
    never a command line a project or a model composed.
    """

    def run(self, argv: list[str], cwd: Path, *, timeout_s: int) -> RunResult: ...


def _now() -> datetime:
    return datetime.now(UTC)


#: The Living Core subsystem this factory publishes under. Deliberately not M23's
#: `appfactory`: a web app scaffolded and run and a signed EXE read back from its PE header
#: are different claims, and one name for both would hide which was made.
SUBSYSTEM_NATIVE: Final = "nativefactory"


def _touch(db: Session, row: NativeBuildRow, state: str, **fields: Any) -> NativeBuildRow:
    row.state = state
    for key, value in fields.items():
        setattr(row, key, value)
    row.updated_at = _now()
    db.commit()
    logger.info("native_build_state", build_id=str(row.id), state=state, slug=row.slug)
    _publish(row)
    return row


def _publish(row: NativeBuildRow) -> None:
    """One `native.build` event for the row as it now stands.

    Published from `_touch`, which every transition already funnels through, so the channel
    cannot drift from the row: the event's `state` is `wire_step(row.state)` computed from
    the row that was just committed, not from what a caller intended to write.

    Never fails the owner's build over a UI concern (`app.creative.service._publish`'s own
    rule): an unknown step is logged and dropped, because a word the web build cannot read
    draws a finished build as one still being made, and saying nothing is the smaller lie.
    """
    try:
        step = wire_step(row.state)
    except Exception:  # noqa: BLE001 - an unmapped state is a bug to log, not to raise here
        logger.error("native_build_unknown_wire_state", state=row.state, build_id=str(row.id))
        return
    if step not in NATIVE_BUILD_STEPS:  # pragma: no cover - wire_step is total over them
        logger.error("native_build_unknown_activity_step", step=step)
        return

    artifact = row.artifact_json or {}
    metadata: dict[str, object] = {
        "state": step,
        "target": row.target,
        "stack": row.stack,
        "version": row.version,
    }
    # Only what the row HOLDS. `verdict_ok` comes from the verdict the reader produced,
    # never from `state == "verified"` - the web half reads it the same way, on purpose.
    if row.verdict_json is not None:
        metadata["verdict_ok"] = bool(row.verdict_json.get("ok"))
    if artifact:
        metadata["artifact_name"] = Path(str(row.artifact_path or "")).name or None
        metadata["artifact_size_bytes"] = artifact.get("size_bytes")
        metadata["artifact_sha256"] = str(artifact.get("sha256") or "")[:16] or None
    if row.error_class:
        metadata["error_class"] = row.error_class

    publish_ui_state(
        UiState.NATIVE_BUILD,
        subsystem=SUBSYSTEM_NATIVE,
        label=row.display_name[:64],
        metadata={k: v for k, v in metadata.items() if v is not None},
    )


def plan_build(
    db: Session,
    payload: dict[str, Any],
    *,
    facts: ToolchainFacts | None = None,
) -> list[NativeBuildRow]:
    """Validate the request and open one row per target, or refuse before opening any.

    A spec asking for three targets where two are reachable opens rows for all three: the
    one that cannot be built is an `unavailable` ROW with its reason, not a silence. The
    owner asked for it, so the answer about it has to exist somewhere they can see.
    """
    spec = parse_spec(payload)
    measured = facts or detect()
    rows: list[NativeBuildRow] = []
    for target in spec.targets:
        single = spec.model_copy(update={"targets": [target]})
        choice = choose(single, measured)
        now = _now()
        row = NativeBuildRow(
            id=uuid.uuid4(),
            slug=spec.slug,
            display_name=spec.display_title,
            stack=spec.resolved_stack,
            template=spec.template,
            target=target,
            version=spec.version,
            state=STATE_PLANNED if choice.available else STATE_UNAVAILABLE,
            spec_json=single.model_dump(mode="json"),
            error_class=None if choice.available else choice.error_class,
            error_message=None if choice.available else choice.reason[:1000],
            created_at=now,
            updated_at=now,
        )
        db.add(row)
        rows.append(row)
    db.commit()
    return rows


def generate(
    db: Session,
    row: NativeBuildRow,
    workdir: Path,
    *,
    allow_outside_root: bool = False,
) -> NativeBuildRow:
    """Render the template and write it, after the policy has accepted every file."""
    _touch(db, row, STATE_GENERATING)
    spec = NativeAppSpec.model_validate(row.spec_json)
    try:
        project = render(spec)
    except NativeFactoryError as exc:
        return _touch(
            db, row, STATE_UNAVAILABLE, error_class=exc.error_class, error_message=exc.speech
        )

    try:
        # The native addition to M23's policy: a build EXECUTES what it is given, so a
        # rendered tree must contain source and nothing a build step could be pointed at
        # and told to run (roots.py).
        check_extensions(project)
    except NativeFactoryError as exc:
        return _touch(
            db, row, STATE_FAILED, error_class=exc.error_class, error_message=exc.speech
        )

    try:
        report = validate_files(project)
    except AppValidationError as exc:
        return _touch(
            db, row, STATE_FAILED, error_class="policy_refused", error_message=str(exc)[:1000]
        )
    if not report.ok:
        return _touch(
            db,
            row,
            STATE_FAILED,
            error_class="policy_refused",
            error_message="; ".join(report.errors)[:1000],
        )

    # Containment BEFORE anything is written: resolve-then-contain, so `..`, a symlink
    # or an absolute path handed in by a caller cannot put a compiler's output somewhere
    # the authorised root does not cover. `allow_outside_root` exists for the tests and
    # the lab, which build in a temp directory on purpose and say so.
    if not allow_outside_root:
        try:
            resolve_within(native_root(), workdir)
        except NativeFactoryError as exc:
            return _touch(
                db, row, STATE_FAILED, error_class=exc.error_class, error_message=exc.speech
            )

    workdir.mkdir(parents=True, exist_ok=True)
    for file in project.files:
        target = workdir / file.path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(file.text, encoding="utf-8")
    return _touch(db, row, STATE_PLANNED, project_path=str(workdir))


def build_and_test(
    db: Session,
    row: NativeBuildRow,
    runner: BuildRunner,
    *,
    dotnet: str,
) -> NativeBuildRow:
    """Compile, then run the generated project's own tests. FIXED commands only."""
    workdir = Path(row.project_path or "")
    manifest = json.loads((workdir / "manifest.json").read_text(encoding="utf-8"))

    _touch(db, row, STATE_BUILDING)
    built = runner.run(
        [dotnet, "build", manifest["entry"], "-c", "Release", "--nologo"],
        workdir,
        timeout_s=BUILD_TIMEOUT_S,
    )
    if not built.ok:
        return _touch(
            db,
            row,
            STATE_FAILED,
            error_class="build_failed",
            error_message=_tail(built.output, 1000),
            log_tail=_tail(built.output, MAX_LOG_TAIL_CHARS),
        )

    _touch(db, row, STATE_TESTING)
    tested = runner.run(
        [dotnet, "test", manifest["tests"], "-c", "Release", "--nologo"],
        workdir,
        timeout_s=BUILD_TIMEOUT_S,
    )
    summary = _test_summary(tested.output)
    if not tested.ok:
        return _touch(
            db,
            row,
            STATE_FAILED,
            error_class="tests_failed",
            error_message=summary or _tail(tested.output, 1000),
            tests_json={"passed": False, "summary": summary},
            log_tail=_tail(tested.output, MAX_LOG_TAIL_CHARS),
        )
    return _touch(db, row, STATE_TESTING, tests_json={"passed": True, "summary": summary})


def publish_and_validate(
    db: Session,
    row: NativeBuildRow,
    runner: BuildRunner,
    *,
    dotnet: str,
    out_dir: Path,
) -> NativeBuildRow:
    """Produce the artefact, then hand it to a reader that did not build it.

    This is the only place a row may reach `verified`, and it may only do so with facts a
    reader returned. Every other outcome is named: nothing produced is `failed`, produced
    but unreadable is `unverified`, produced and disagreeing is `mismatch`.
    """
    workdir = Path(row.project_path or "")
    manifest = json.loads((workdir / "manifest.json").read_text(encoding="utf-8"))
    spec = NativeAppSpec.model_validate(row.spec_json)

    _touch(db, row, STATE_BUILDING)
    published = runner.run(
        [
            dotnet, "publish", manifest["entry"], "-c", "Release",
            "-r", "win-x64", "--self-contained", "true", "--nologo", "-o", str(out_dir),
        ],
        workdir,
        timeout_s=BUILD_TIMEOUT_S,
    )
    if not published.ok:
        return _touch(
            db,
            row,
            STATE_FAILED,
            error_class="publish_failed",
            error_message=_tail(published.output, 1000),
            log_tail=_tail(published.output, MAX_LOG_TAIL_CHARS),
        )

    artifact = out_dir / manifest["artifact"]
    if not artifact.exists():
        return _touch(
            db,
            row,
            STATE_FAILED,
            error_class="artifact_missing",
            error_message=f"publish exited 0 and produced no {manifest['artifact']}",
        )

    _touch(db, row, STATE_VALIDATING, artifact_path=str(artifact))
    try:
        facts = read_artifact(artifact)
    except ArtifactUnreadable as exc:
        # Produced, and nobody could read it. Not a success and not a build failure - the
        # honest middle, with the reason, so nobody later reads silence as agreement.
        return _touch(
            db,
            row,
            STATE_UNVERIFIED,
            error_class="artifact_unreadable",
            error_message=str(exc)[:1000],
        )

    verdict = validate_against_spec(facts, spec)
    return _touch(
        db,
        row,
        STATE_VERIFIED if verdict.ok else STATE_MISMATCH,
        artifact_json=facts.as_dict(),
        verdict_json=verdict.as_dict(),
        error_class=None if verdict.ok else "artifact_mismatch",
        error_message=None if verdict.ok else "; ".join(verdict.mismatches)[:1000],
    )


def read_application_log(db: Session, row: NativeBuildRow, log_path: Path) -> NativeBuildRow:
    """The application's own log after a run (spec §5).

    A crash the owner never saw is still evidence, and a log that is not there is recorded
    as not there rather than as an empty one.
    """
    if not log_path.exists():
        return _touch(db, row, row.state, log_tail=None)
    return _touch(db, row, row.state, log_tail=_tail(log_path.read_text(
        encoding="utf-8", errors="replace"), MAX_LOG_TAIL_CHARS))


def list_builds(db: Session, *, limit: int = 50) -> list[NativeBuildRow]:
    return list(
        db.execute(
            select(NativeBuildRow).order_by(NativeBuildRow.created_at.desc()).limit(limit)
        ).scalars()
    )


def get_build(db: Session, build_id: uuid.UUID) -> NativeBuildRow | None:
    return db.get(NativeBuildRow, build_id)


def receipt_for(row: NativeBuildRow) -> str:
    """What the owner hears, composed from the ROW - never from what anything remembered.

    The M26 rule, inherited: a receipt is a read-back. If the row says `unverified`, the
    sentence says so, because a receipt that rounded that up to "hazır" would be the exact
    dishonesty the independent reader exists to prevent.
    """
    facts = row.artifact_json or {}
    if row.state == STATE_VERIFIED:
        size = int(facts.get("size_bytes") or 0)
        digest = str(facts.get("sha256") or "")[:8]
        return (
            f"{row.display_name} hazır efendim: {Path(str(row.artifact_path or '')).name}, "
            f"{size // 1024} KB, sürüm {facts.get('version') or row.version}, "
            f"sha256 {digest}. Bağımsız okuyucu doğruladı."
        )
    if row.state == STATE_MISMATCH:
        return (
            f"{row.display_name} üretildi ama beklenene uymuyor efendim: "
            f"{row.error_message or 'ayrıntı yok'}."
        )
    if row.state == STATE_UNVERIFIED:
        return (
            f"{row.display_name} üretildi ama doğrulayamadım efendim: "
            f"{row.error_message or 'okunamadı'}. Hazır demiyorum."
        )
    if row.state == STATE_UNAVAILABLE:
        return row.error_message or f"{row.target} bu makinede üretilemiyor efendim."
    if row.state == STATE_FAILED:
        return f"{row.display_name} derlenemedi efendim: {row.error_message or 'bilinmeyen hata'}."
    return f"{row.display_name} için {row.target} hazırlanıyor efendim."


def _tail(text: str, limit: int) -> str:
    return (text or "")[-limit:]


def _test_summary(output: str) -> str | None:
    """The generated project's own test counts.

    The CLI's language is pinned by every caller (`DOTNET_CLI_UI_LANGUAGE=en`) precisely so
    this parse is not a guess about the machine's locale - the lab learned that the hard
    way when a Turkish CLI made it record `None` where a count belonged.
    """
    for line in (output or "").splitlines():
        if "Passed:" in line or "Failed:" in line:
            return line.strip()[:200]
    return None


#: The lab and any future caller build a runner from this, so the fixed-argv rule has one
#: implementation rather than one per caller.
def subprocess_runner(env: dict[str, str] | None = None) -> BuildRunner:
    import os
    import subprocess

    class _Subprocess:
        def run(self, argv: list[str], cwd: Path, *, timeout_s: int) -> RunResult:
            merged = {**os.environ, "DOTNET_CLI_UI_LANGUAGE": "en", "DOTNET_NOLOGO": "1"}
            merged.update(env or {})
            proc = subprocess.run(
                argv, cwd=cwd, capture_output=True, text=True, timeout=timeout_s, env=merged
            )
            return RunResult(proc.returncode, (proc.stdout or "") + (proc.stderr or ""))

    return _Subprocess()


__all__ = [
    "BUILD_TIMEOUT_S",
    "BuildRunner",
    "RunResult",
    "build_and_test",
    "generate",
    "get_build",
    "list_builds",
    "plan_build",
    "publish_and_validate",
    "read_application_log",
    "receipt_for",
    "subprocess_runner",
]
