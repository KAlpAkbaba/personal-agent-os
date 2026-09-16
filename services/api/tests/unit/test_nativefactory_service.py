"""The lifecycle, as rows — and the one thing a row may never do.

The rule this suite exists to hold: **a row may not say `verified` unless a reader read the
file.** Every other outcome the lifecycle can reach is named and distinct — nothing
produced is `failed`, produced but unreadable is `unverified`, produced and disagreeing is
`mismatch`, and a target this machine cannot reach is `unavailable`, which is not a
failure at all.

The runner is a fake here because the real one is proven by
`scripts/tests/native-windows-lab.py`, which compiles for real. What a fake CAN prove, and
a real build cannot prove cheaply, is the failure matrix: a compiler that exits non-zero, a
publish that exits zero and produces nothing, an artefact nobody can read. Those are the
paths that decide whether the owner is told the truth.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.nativefactory.models import (
    STATE_FAILED,
    STATE_MISMATCH,
    STATE_PLANNED,
    STATE_UNAVAILABLE,
    STATE_UNVERIFIED,
    STATE_VERIFIED,
    NativeBuildRow,
)
from app.nativefactory.service import (
    RunResult,
    build_and_test,
    generate,
    plan_build,
    publish_and_validate,
    receipt_for,
)
from app.nativefactory.stacks import ToolchainFacts

TABLES = [NativeBuildRow.__table__]

FULL = ToolchainFacts(
    dotnet=r"C:\dotnet.exe",
    dotnet_sdk="10.0.400",
    makeappx=r"C:\makeappx.exe",
    signtool=r"C:\signtool.exe",
    java=r"C:\java.exe",
    java_home=r"C:\Java",
    android_sdk=r"C:\Sdk",
    aapt2=r"C:\aapt2.exe",
    macos=False,
)
NO_JAVA = ToolchainFacts(**{**FULL.as_dict(), "java": None, "java_home": None})

WINDOWS = {
    "name": "Notlarim",
    "title": "Notlarım",
    "template": "notes-desktop",
    "targets": ["windows_exe"],
    "version": "0.1.0",
    "persistence": "local_file",
    "features": ["add_item", "list_items", "delete_item", "persist_local"],
}


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    for table in TABLES:
        table.create(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    yield session
    session.close()


class FakeRunner:
    """Scripted exit codes, in the order the lifecycle asks for them."""

    def __init__(self, *results: RunResult) -> None:
        self._results = list(results)
        self.calls: list[list[str]] = []

    def run(self, argv: list[str], cwd: Path, *, timeout_s: int) -> RunResult:
        self.calls.append(argv)
        return self._results.pop(0) if self._results else RunResult(0, "")


PASSED = RunResult(0, "Passed!  - Failed: 0, Passed: 5, Skipped: 0, Total: 5")


def _planned(db: Session, tmp_path: Path) -> NativeBuildRow:
    row = plan_build(db, WINDOWS, facts=FULL)[0]
    # `allow_outside_root` is the escape hatch the lab and these tests use DELIBERATELY:
    # they build in a temp directory on purpose. Production callers pass nothing, and the
    # test below proves what happens to them if they point somewhere else.
    return generate(db, row, tmp_path / "project", allow_outside_root=True)


# ------------------------------------------------------------------------- planning


def test_a_reachable_target_opens_a_planned_row(db, tmp_path) -> None:
    rows = plan_build(db, WINDOWS, facts=FULL)
    assert len(rows) == 1
    assert rows[0].state == STATE_PLANNED
    assert rows[0].slug == "notlarim"
    assert rows[0].target == "windows_exe"


def test_an_unreachable_target_opens_an_UNAVAILABLE_row_not_a_silence(db) -> None:
    """The owner asked for it, so the answer about it has to exist somewhere they can see -
    and it must not be a `failed`, because nothing failed."""
    rows = plan_build(
        db,
        {"name": "Sayac", "template": "counter-mobile", "targets": ["android_apk"],
         "features": ["counter"]},
        facts=NO_JAVA,
    )
    assert rows[0].state == STATE_UNAVAILABLE
    assert rows[0].error_class == "dependency_unavailable"
    assert "cihazınız" in (rows[0].error_message or "")


def test_one_row_per_target_so_one_can_fail_without_the_other(db) -> None:
    rows = plan_build(
        db, {**WINDOWS, "targets": ["windows_exe", "windows_msix"]}, facts=FULL
    )
    assert {r.target for r in rows} == {"windows_exe", "windows_msix"}
    assert len({r.id for r in rows}) == 2


# ----------------------------------------------------------------------- generation


def test_generation_writes_the_project_the_policy_accepted(db, tmp_path) -> None:
    row = _planned(db, tmp_path)
    assert row.project_path
    written = Path(row.project_path)
    manifest = json.loads((written / "manifest.json").read_text(encoding="utf-8"))
    assert (written / manifest["entry"]).exists()
    assert (written / manifest["tests"]).exists()


# ---------------------------------------------------------------- the failure matrix


def test_a_compiler_that_fails_leaves_the_row_failed_with_its_output(db, tmp_path) -> None:
    row = _planned(db, tmp_path)
    runner = FakeRunner(RunResult(1, "error CS0103: 'Path' does not exist"))
    row = build_and_test(db, row, runner, dotnet="dotnet")
    assert row.state == STATE_FAILED
    assert row.error_class == "build_failed"
    assert "CS0103" in (row.error_message or "")


def test_failing_tests_are_a_failure_with_the_counts_kept(db, tmp_path) -> None:
    row = _planned(db, tmp_path)
    runner = FakeRunner(RunResult(0, "build ok"), RunResult(1, "Failed!  - Failed: 2, Passed: 3"))
    row = build_and_test(db, row, runner, dotnet="dotnet")
    assert row.state == STATE_FAILED
    assert row.error_class == "tests_failed"
    assert row.tests_json == {"passed": False, "summary": "Failed!  - Failed: 2, Passed: 3"}


def test_a_publish_that_produces_nothing_is_a_failure_not_a_success(db, tmp_path) -> None:
    """`exit 0` says a compiler was happy. It does not say a file exists."""
    row = _planned(db, tmp_path)
    row = build_and_test(db, row, FakeRunner(RunResult(0, ""), PASSED), dotnet="dotnet")
    row = publish_and_validate(
        db, row, FakeRunner(RunResult(0, "publish ok")), dotnet="dotnet", out_dir=tmp_path / "out"
    )
    assert row.state == STATE_FAILED
    assert row.error_class == "artifact_missing"
    assert row.artifact_json is None


def test_an_artefact_nobody_can_read_is_UNVERIFIED_never_verified(db, tmp_path) -> None:
    """The honest middle. A row that rounded this up to `verified` would be exactly the
    dishonesty the independent reader exists to prevent."""
    row = _planned(db, tmp_path)
    row = build_and_test(db, row, FakeRunner(RunResult(0, ""), PASSED), dotnet="dotnet")
    out = tmp_path / "out"
    out.mkdir()
    (out / "notlarim.exe").write_bytes(b"this is not a PE image")
    row = publish_and_validate(
        db, row, FakeRunner(RunResult(0, "")), dotnet="dotnet", out_dir=out
    )
    assert row.state == STATE_UNVERIFIED
    assert row.error_class == "artifact_unreadable"
    assert row.artifact_json is None
    assert "Hazır demiyorum" in receipt_for(row)


def test_an_artefact_that_disagrees_with_the_spec_is_a_MISMATCH(db, tmp_path, monkeypatch) -> None:
    from app.nativefactory import service as service_module
    from app.nativefactory.artifacts import ArtifactFacts

    row = _planned(db, tmp_path)
    row = build_and_test(db, row, FakeRunner(RunResult(0, ""), PASSED), dotnet="dotnet")
    out = tmp_path / "out"
    out.mkdir()
    (out / "notlarim.exe").write_bytes(b"MZ")

    # A reader that CAN read it, and finds yesterday's version.
    monkeypatch.setattr(
        service_module,
        "read_artifact",
        lambda path, **kw: ArtifactFacts(
            path=str(path), kind="pe", size_bytes=10, sha256="0" * 64,
            version="0.0.9", architecture="x64", subsystem="windows_gui",
        ),
    )
    row = publish_and_validate(
        db, row, FakeRunner(RunResult(0, "")), dotnet="dotnet", out_dir=out
    )
    assert row.state == STATE_MISMATCH
    assert row.artifact_json is not None  # what was READ is kept, even when it disagrees
    assert "0.0.9" in (row.error_message or "")


def test_only_a_read_file_can_make_a_row_verified(db, tmp_path, monkeypatch) -> None:
    """The rule the whole module is written around, asserted directly."""
    from app.nativefactory import service as service_module
    from app.nativefactory.artifacts import ArtifactFacts

    row = _planned(db, tmp_path)
    row = build_and_test(db, row, FakeRunner(RunResult(0, ""), PASSED), dotnet="dotnet")
    out = tmp_path / "out"
    out.mkdir()
    (out / "notlarim.exe").write_bytes(b"MZ")
    monkeypatch.setattr(
        service_module,
        "read_artifact",
        lambda path, **kw: ArtifactFacts(
            path=str(path), kind="pe", size_bytes=162304, sha256="a" * 64,
            version="0.1.0", architecture="x64", subsystem="windows_gui",
        ),
    )
    row = publish_and_validate(
        db, row, FakeRunner(RunResult(0, "")), dotnet="dotnet", out_dir=out
    )
    assert row.state == STATE_VERIFIED
    assert row.artifact_json["version"] == "0.1.0"
    assert row.verdict_json["ok"] is True


# --------------------------------------------------------------------------- receipts


def test_the_receipt_is_read_back_from_the_row(db, tmp_path) -> None:
    """M26's rule, inherited: a receipt is a read-back, never what something remembered
    saying. Each state gets a sentence that means what the state means."""
    rows = plan_build(db, WINDOWS, facts=FULL)
    row = rows[0]
    assert "hazırlanıyor" in receipt_for(row)

    row.state = STATE_UNAVAILABLE
    row.error_message = "Java yok efendim."
    assert receipt_for(row) == "Java yok efendim."

    row.state = STATE_FAILED
    row.error_message = "error CS0103"
    assert "derlenemedi" in receipt_for(row)

    row.state = STATE_VERIFIED
    row.artifact_path = str(tmp_path / "notlarim.exe")
    row.artifact_json = {"size_bytes": 162304, "sha256": "abcdef1234", "version": "0.1.0"}
    spoken = receipt_for(row)
    assert "hazır" in spoken
    assert "158 KB" in spoken
    assert "0.1.0" in spoken
    assert "Bağımsız okuyucu doğruladı" in spoken


def test_the_runner_is_only_ever_given_a_fixed_argv(db, tmp_path) -> None:
    """A project may not name its own compiler invocation - the reason M23's manifest
    carries a key rather than a command line."""
    row = _planned(db, tmp_path)
    runner = FakeRunner(RunResult(0, ""), PASSED)
    build_and_test(db, row, runner, dotnet="dotnet")
    for argv in runner.calls:
        assert argv[0] == "dotnet"
        assert argv[1] in ("build", "test", "publish")
        assert not any(" " in part and part.endswith((".csproj", ".exe")) for part in argv)


def test_generating_outside_the_authorised_root_is_refused(db, tmp_path) -> None:
    """The guard the tests above opt out of, proven to bite when nobody opts out.

    Containment is applied BEFORE anything is written, so a caller pointing a build at a
    directory the authorised root does not cover gets a refused row and an empty disk -
    not a compiler's output somewhere nobody agreed to.
    """
    row = plan_build(db, WINDOWS, facts=FULL)[0]
    target = tmp_path / "somewhere-nobody-agreed-to"

    row = generate(db, row, target)

    assert row.state == STATE_FAILED
    assert row.error_class == "path_outside_root"
    assert row.project_path is None
    assert not target.exists(), "the guard let files be written before refusing"


# ------------------------------------------------------------- the Living Core channel


def test_every_transition_publishes_native_build(db, tmp_path) -> None:
    """A channel nobody speaks is the failure M25 shipped.

    Both halves can agree on a vocabulary perfectly while nothing ever publishes it - and
    no guard notices, because the two lists are still identical. So this asserts the
    channel is SPOKEN, from the events a real lifecycle actually emitted.
    """
    from app.uistate.contract import NATIVE_BUILD_STEPS, UiState
    from app.uistate.publisher import UiStatePublisher, set_publisher

    bus = UiStatePublisher(tail_size=64)
    set_publisher(bus)
    try:
        row = plan_build(db, WINDOWS, facts=FULL)[0]
        row = generate(db, row, tmp_path / "project", allow_outside_root=True)
        row = build_and_test(db, row, FakeRunner(RunResult(0, ""), PASSED), dotnet="dotnet")
        published = bus.tail(limit=64)
    finally:
        set_publisher(UiStatePublisher())

    native = [e for e in published if e.state == UiState.NATIVE_BUILD]
    assert native, "the lifecycle never published native.build at all"
    for event in native:
        assert event.subsystem == "nativefactory"
        # The word on the wire is always one the web half can read.
        assert event.metadata["state"] in NATIVE_BUILD_STEPS
        assert event.metadata["target"] == "windows_exe"

    # ...and the steps it actually walked are on the wire, in order.
    assert [e.metadata["state"] for e in native][:3] == ["generating", "planned", "building"]


def test_the_channel_says_verified_only_when_the_verdict_did(db, tmp_path, monkeypatch) -> None:
    """`verdict_ok` is read from the reader's verdict, never inferred from the state - the
    web half reads it the same way, on purpose, so neither can imply agreement the reader
    did not give."""
    from app.nativefactory import service as service_module
    from app.nativefactory.artifacts import ArtifactFacts
    from app.uistate.contract import UiState
    from app.uistate.publisher import UiStatePublisher, set_publisher

    row = plan_build(db, WINDOWS, facts=FULL)[0]
    row = generate(db, row, tmp_path / "project", allow_outside_root=True)
    row = build_and_test(db, row, FakeRunner(RunResult(0, ""), PASSED), dotnet="dotnet")
    out = tmp_path / "out"
    out.mkdir()
    (out / "notlarim.exe").write_bytes(b"MZ")
    monkeypatch.setattr(
        service_module,
        "read_artifact",
        lambda path, **kw: ArtifactFacts(
            path=str(path), kind="pe", size_bytes=162304, sha256="c" * 64,
            version="0.0.9", architecture="x64", subsystem="windows_gui",
        ),
    )

    bus = UiStatePublisher(tail_size=64)
    set_publisher(bus)
    try:
        publish_and_validate(
            db, row, FakeRunner(RunResult(0, "")), dotnet="dotnet", out_dir=out
        )
        published = bus.tail(limit=64)
    finally:
        set_publisher(UiStatePublisher())

    final = [e for e in published if e.state == UiState.NATIVE_BUILD][-1]
    assert final.metadata["state"] == "mismatch"
    assert final.metadata["verdict_ok"] is False


# ------------------------------------------------- a Windows path, read on a Linux core


def test_a_windows_artifact_path_yields_its_file_name_on_any_os() -> None:
    r"""CI run 34339398396: ``assert 'C:\builds\...\notlarim.exe' == 'notlarim.exe'``.

    ``pathlib.Path`` is ``PosixPath`` on the Cloud Core's Linux, where a backslash is an
    ordinary character - so ``Path(windows_path).name`` is the WHOLE STRING. The owner's
    Windows machine can never catch this, because there the same call is right, which is
    exactly why these assertions are written against literal strings.
    """
    from app.nativefactory.service import artifact_file_name

    assert artifact_file_name(r"C:\builds\notlarim\out\notlarim.exe") == "notlarim.exe"
    assert artifact_file_name(r"C:\builds\notlarim\out\notlarim.msix") == "notlarim.msix"
    # A POSIX path still resolves, so nothing breaks for a path produced anywhere else.
    assert artifact_file_name("/srv/builds/notlarim/notlarim.exe") == "notlarim.exe"
    assert artifact_file_name(None) is None
    assert artifact_file_name("") is None


def test_the_receipt_speaks_a_file_name_not_a_whole_path(db) -> None:
    """What the owner would have HEARD before the fix: the entire Windows path read out
    where a file name belongs."""
    row = plan_build(db, WINDOWS, facts=FULL)[0]
    row.state = STATE_VERIFIED
    row.artifact_path = r"C:\builds\notlarim\out\notlarim.exe"
    row.artifact_json = {"size_bytes": 162304, "sha256": "b" * 64, "version": "0.1.0"}

    spoken = receipt_for(row)
    assert "notlarim.exe" in spoken
    assert "C:" not in spoken
