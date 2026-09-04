"""Unit tests: the Self Model indexer (PHASE 6).

Everything here runs against a miniature repository written into ``tmp_path``
(``tests/selfmodel_support.py``) except the last test, which indexes THIS
checkout for real -- a fixture can prove the algorithm, only the real tree can
prove the algorithm matches the repository it was written for.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.ledger.models import ActivityEventRow
from app.selfhealing.models import Release
from app.selfmodel import indexer
from app.selfmodel.indexer import IndexConfig, build_index, targets_for_test_module
from app.selfmodel.models import (
    EDGE_CALLS,
    EDGE_DOCUMENTED_BY,
    EDGE_IMPORTS,
    EDGE_TESTS,
    MODULE_KIND_CLIENT,
    MODULE_KIND_MODULE,
    MODULE_KIND_PACKAGE,
    MODULE_KIND_SCRIPT,
    MODULE_KIND_SERVICE,
    PRODUCTION_STATE_INSTALLED,
    PRODUCTION_STATE_RUNNING,
    PRODUCTION_STATE_SOURCE_ONLY,
    SYMBOL_KIND_CLASS,
    SYMBOL_KIND_CONSTANT,
    SYMBOL_KIND_FUNCTION,
    SYMBOL_KIND_METHOD,
    SYMBOL_KIND_ROUTE,
    SYMBOL_KIND_TABLE,
    TRUTH_INSTALLED,
    TRUTH_RUNTIME,
    TRUTH_SOURCE,
    CodeEdge,
    CodeModule,
    CodeSymbol,
    ModuleProvenance,
)
from app.selfmodel.progress import PHASES, SUBSYSTEM_SELF_MODEL, IndexProgress
from tests.selfmodel_support import (
    FIXTURE_MODULE,
    FIXTURE_PACKAGE,
    FIXTURE_TEST_MODULE,
    make_engine,
    write_fixture_tree,
)


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    return write_fixture_tree(tmp_path / "repo")


@pytest.fixture()
def sessions():
    engine = make_engine()
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    yield factory
    engine.dispose()


def _index(factory, repo: Path, **kwargs):
    config = IndexConfig(repo_root=repo, **kwargs)
    with factory() as session:
        report = build_index(session, config=config)
        session.commit()
    return report


def _modules(session: Session) -> dict[str, CodeModule]:
    return {row.module_id: row for row in session.scalars(select(CodeModule))}


def _symbols(session: Session, module_id: str) -> dict[tuple[str, str], CodeSymbol]:
    rows = session.scalars(select(CodeSymbol).where(CodeSymbol.module_id == module_id))
    return {(row.kind, row.name): row for row in rows}


# --------------------------------------------------------------- extraction


def test_fixture_tree_is_indexed_with_the_right_kinds(sessions, repo: Path) -> None:
    report = _index(sessions, repo)
    assert report.modules_discovered > 0

    with sessions() as session:
        modules = _modules(session)

    assert modules[FIXTURE_PACKAGE].kind == MODULE_KIND_PACKAGE
    assert modules[FIXTURE_MODULE].kind == MODULE_KIND_MODULE
    assert modules[FIXTURE_MODULE].path == "services/api/app/observer/diagnostic_observer.py"
    assert modules[FIXTURE_MODULE].language == "python"
    assert modules[FIXTURE_MODULE].owner_area == "observer"

    # Only the FIRST docstring line becomes the purpose -- the rest of the
    # docstring is body, and bodies never enter the index.
    assert modules[FIXTURE_MODULE].purpose == "Watches subsystem health and records what it saw."
    assert "must never reach the index" not in (modules[FIXTURE_MODULE].purpose or "")

    # The display name comes from the LEAF, so a foreign path does not turn
    # into its file extension.
    assert modules[FIXTURE_MODULE].detail_json["display_name"] == "diagnostic observer"
    assert (
        modules["services/browser/browser_agent/worker.py"].detail_json["display_name"] == "worker"
    )
    assert modules["scripts/Deploy-Observer.ps1"].detail_json["display_name"] == "Deploy Observer"

    assert modules["services/browser/browser_agent/worker.py"].kind == MODULE_KIND_SERVICE
    assert modules["apps/web/app/lib/observer.ts"].kind == MODULE_KIND_CLIENT
    assert modules["devices/windows-agent/Agent.cs"].kind == MODULE_KIND_SERVICE
    assert modules["scripts/Deploy-Observer.ps1"].kind == MODULE_KIND_SCRIPT
    assert modules["tests.unit." + FIXTURE_TEST_MODULE.split(".")[-1]].owner_area == "tests"


def test_excluded_directories_are_never_walked(sessions, repo: Path) -> None:
    _index(sessions, repo)
    with sessions() as session:
        ids = set(session.scalars(select(CodeModule.module_id)))
    assert not any("node_modules" in module_id for module_id in ids)


def test_python_symbols_are_extracted(sessions, repo: Path) -> None:
    _index(sessions, repo)
    with sessions() as session:
        symbols = _symbols(session, FIXTURE_MODULE)

    assert (SYMBOL_KIND_CLASS, "ObservationRow") in symbols
    assert (SYMBOL_KIND_TABLE, "observations") in symbols
    assert (SYMBOL_KIND_METHOD, "ObservationRow.summarize") in symbols
    assert (SYMBOL_KIND_FUNCTION, "observe") in symbols
    assert (SYMBOL_KIND_CONSTANT, "OBSERVER_VERSION") in symbols

    # The route carries the router prefix, so it reads as the path an owner
    # would actually call rather than the decorator's fragment.
    assert (SYMBOL_KIND_ROUTE, "GET /v1/observer/status") in symbols

    observe = symbols[(SYMBOL_KIND_FUNCTION, "observe")]
    assert observe.signature.startswith("def observe(target: str, *, deep: bool=False)")
    assert observe.docstring_summary == "Look at one target and return what was seen."
    assert observe.lineno > 0


def test_secret_shaped_string_defaults_never_enter_a_signature(sessions, repo: Path) -> None:
    """A stored signature is unparsed WITH its defaults, so a literal in a
    secret-shaped parameter would be copied out of the source into the database
    and back out over the API. It is redacted on every signature path."""
    _index(sessions, repo)
    with sessions() as session:
        symbols = _symbols(session, FIXTURE_MODULE)
        every_signature = " ".join(row.signature for row in session.scalars(select(CodeSymbol)))

    connect = symbols[(SYMBOL_KIND_FUNCTION, "connect")]
    assert "NOT-A-REAL-SECRET" not in connect.signature
    assert "api_key: str='<redacted>'" in connect.signature
    # a non-secret default is still useful information and is kept
    assert "retries: int=2" in connect.signature

    # ...including the route-handler path, which renders its own signature.
    route = symbols[(SYMBOL_KIND_ROUTE, "GET /v1/observer/status")]
    assert "NOT-A-REAL-SECRET" not in route.signature
    assert "NOT-A-REAL-SECRET" not in every_signature


def test_import_and_call_edges_are_recorded(sessions, repo: Path) -> None:
    _index(sessions, repo)
    with sessions() as session:
        edges = {
            (row.from_module, row.to_module, row.kind) for row in session.scalars(select(CodeEdge))
        }
    assert (FIXTURE_MODULE, "app.observer.helpers", EDGE_IMPORTS) in edges
    assert (FIXTURE_MODULE, "app.observer.helpers", EDGE_CALLS) in edges
    # fastapi/sqlalchemy are not modules of this system, so they produce no edge.
    assert not any(to.startswith("fastapi") for _, to, _ in edges)


def test_tests_and_docs_are_linked_to_the_module(sessions, repo: Path) -> None:
    _index(sessions, repo)
    with sessions() as session:
        edges = {
            (row.from_module, row.to_module, row.kind) for row in session.scalars(select(CodeEdge))
        }
        modules = _modules(session)

    assert (FIXTURE_TEST_MODULE, FIXTURE_MODULE, EDGE_TESTS) in edges
    assert (FIXTURE_MODULE, "docs/DECISIONS.md#ADR-0099", EDGE_DOCUMENTED_BY) in edges
    assert modules[FIXTURE_MODULE].adr_refs == ["ADR-0099"]
    # ADR-0098 mentions no module, so it links to nothing.
    assert "ADR-0098" not in modules[FIXTURE_MODULE].adr_refs
    assert modules[FIXTURE_PACKAGE].spec_refs == ["docs/M99_OBSERVER_SPEC.md"]


@pytest.mark.parametrize(
    ("test_module", "expected"),
    [
        ("tests.unit.test_observer_diagnostic_observer", ["app.observer.diagnostic_observer"]),
        ("tests.unit.test_observer", ["app.observer"]),
        ("tests.unit.test_observer_helpers", ["app.observer.helpers"]),
        # No module answers to this name; an unlinked test beats a wrong link.
        ("tests.unit.test_multi_device_invariant", []),
        ("tests.unit.conftest", []),
    ],
)
def test_test_target_resolution(test_module: str, expected: list[str]) -> None:
    known = {"app", "app.observer", "app.observer.diagnostic_observer", "app.observer.helpers"}
    assert targets_for_test_module(test_module, known) == expected


def test_oversized_files_are_recorded_but_never_parsed(sessions, repo: Path) -> None:
    big = repo / "services/api/app/observer/huge.py"
    big.write_text('"""Huge."""\n' + ("X = 1\n" * 5000), encoding="utf-8")

    report = _index(sessions, repo, max_file_bytes=1024)
    assert report.modules_too_large >= 1

    with sessions() as session:
        row = session.get(CodeModule, "app.observer.huge")
        assert row is not None
        assert row.detail_json["note"] == "too_large"
        assert (
            session.scalar(
                select(func.count())
                .select_from(CodeSymbol)
                .where(CodeSymbol.module_id == row.module_id)
            )
            == 0
        )


def test_foreign_trees_contribute_no_symbols(sessions, repo: Path) -> None:
    _index(sessions, repo)
    with sessions() as session:
        for module_id in (
            "apps/web/app/lib/observer.ts",
            "devices/windows-agent/Agent.cs",
            "scripts/Deploy-Observer.ps1",
            "services/browser/browser_agent/worker.py",
        ):
            count = session.scalar(
                select(func.count())
                .select_from(CodeSymbol)
                .where(CodeSymbol.module_id == module_id)
            )
            assert count == 0, module_id
            assert session.get(CodeModule, module_id).purpose is None


def test_unparsable_python_degrades_to_one_row(sessions, repo: Path) -> None:
    (repo / "services/api/app/observer/broken.py").write_text("def (:\n", encoding="utf-8")
    report = _index(sessions, repo)
    assert report.modules_unparsed == 1
    with sessions() as session:
        row = session.get(CodeModule, "app.observer.broken")
        assert row is not None and row.detail_json["note"].startswith("unparsed:")


# ------------------------------------------------------------- incremental


def test_reindex_of_an_unchanged_checkout_does_no_work(sessions, repo: Path) -> None:
    first = _index(sessions, repo)
    assert first.modules_reparsed > 0
    assert first.writes > 0

    second = _index(sessions, repo)
    assert second.modules_discovered == first.modules_discovered
    assert second.modules_reparsed == 0
    assert second.modules_skipped_unchanged == second.modules_discovered
    assert second.files_read == 0
    assert second.symbols_written == 0
    assert second.edges_written == 0
    assert second.edges_removed == 0
    assert second.provenance_written == 0
    assert second.modules_updated == 0
    assert second.writes == 0


def test_only_the_changed_module_is_reparsed(sessions, repo: Path) -> None:
    _index(sessions, repo)
    target = repo / "services/api/app/observer/helpers.py"
    # A same-second rewrite would be invisible to a seconds-resolution stat, so
    # nudge mtime explicitly rather than sleeping.
    target.write_text('"""Changed helpers."""\n\n\ndef normalize(v):\n    return v\n', "utf-8")
    future = time.time() + 5
    import os

    os.utime(target, (future, future))

    report = _index(sessions, repo)
    assert report.modules_reparsed == 1
    assert report.files_read == 1
    with sessions() as session:
        assert session.get(CodeModule, "app.observer.helpers").purpose == "Changed helpers."


def test_a_deleted_file_drops_its_module_and_rows(sessions, repo: Path) -> None:
    _index(sessions, repo)
    (repo / "services/api/app/observer/helpers.py").unlink()

    report = _index(sessions, repo)
    assert report.modules_removed == 1
    with sessions() as session:
        assert session.get(CodeModule, "app.observer.helpers") is None
        assert (
            session.scalar(
                select(func.count())
                .select_from(ModuleProvenance)
                .where(ModuleProvenance.module_id == "app.observer.helpers")
            )
            == 0
        )


def test_content_fingerprint_mode_detects_an_mtime_preserving_edit(sessions, repo: Path) -> None:
    target = repo / "services/api/app/observer/helpers.py"
    original = target.stat()
    _index(sessions, repo, fingerprint_mode="content")

    same_size = '"""Changed hlprs."""\n\n\ndef normalize(value: str) -> str:\n    """Lowercase and strip."""\n    return value.strip().lower()\n'  # noqa: E501
    target.write_text(same_size, encoding="utf-8")
    import os

    os.utime(target, (original.st_atime, original.st_mtime))

    report = _index(sessions, repo, fingerprint_mode="content")
    assert report.modules_reparsed == 1


# --------------------------------------------------------------- provenance


def test_a_source_only_module_never_reports_installed_or_runtime_truth(
    sessions, repo: Path
) -> None:
    """The whole point of four truths: a checkout walk proves the source exists
    and NOTHING else."""
    _index(sessions, repo)
    with sessions() as session:
        truths = {
            row.truth_kind
            for row in session.scalars(
                select(ModuleProvenance).where(ModuleProvenance.module_id == FIXTURE_MODULE)
            )
        }
        row = session.get(CodeModule, FIXTURE_MODULE)

    assert truths == {TRUTH_SOURCE}
    assert TRUTH_RUNTIME not in truths
    assert TRUTH_INSTALLED not in truths
    assert row.production_state == PRODUCTION_STATE_SOURCE_ONLY


def test_source_provenance_carries_a_real_digest_and_checkout_ref(sessions, repo: Path) -> None:
    _index(sessions, repo)
    with sessions() as session:
        row = session.scalars(
            select(ModuleProvenance).where(
                ModuleProvenance.module_id == FIXTURE_MODULE,
                ModuleProvenance.truth_kind == TRUTH_SOURCE,
            )
        ).one()
    assert row.confidence == 1.0
    assert row.digest and len(row.digest) == 64
    assert row.evidence_refs == [
        {"kind": "checkout", "ref": "services/api/app/observer/diagnostic_observer.py"}
    ]


def test_a_promoted_release_is_installed_truth_not_runtime_truth(
    sessions, repo: Path, monkeypatch
) -> None:
    """A release row proves an intent to deploy. It never proves a live process
    -- the browser-worker defect this table exists for.

    The component is mapped EXPLICITLY: after the 2026-09-05 review, a component that is
    neither mapped nor an exact module id attributes nothing, because a name that merely
    looks like a module's last segment is a guess, not evidence."""
    monkeypatch.setitem(indexer.COMPONENT_MODULE_HINTS, "diagnostic_observer", (FIXTURE_MODULE,))
    _index(sessions, repo)
    with sessions() as session:
        session.add(
            Release(
                component="diagnostic_observer",
                version="1.4.0",
                manifest_digest="d" * 64,
                status="active",
                promoted_at=datetime.now(UTC),
            )
        )
        session.commit()

    _index(sessions, repo)
    with sessions() as session:
        truths = {
            row.truth_kind: row
            for row in session.scalars(
                select(ModuleProvenance).where(ModuleProvenance.module_id == FIXTURE_MODULE)
            )
        }
        state = session.get(CodeModule, FIXTURE_MODULE).production_state

    assert TRUTH_INSTALLED in truths
    assert truths[TRUTH_INSTALLED].version == "1.4.0"
    assert truths[TRUTH_INSTALLED].evidence_refs[0]["kind"] == "release"
    assert TRUTH_RUNTIME not in truths
    assert state == PRODUCTION_STATE_INSTALLED


def test_runtime_truth_appears_only_when_something_reported_back(sessions, repo: Path) -> None:
    _index(sessions, repo)
    now = datetime.now(UTC)

    with sessions() as session:
        # A completed deployment WITHOUT a module block proves nothing about a
        # running process, so it must not create runtime truth.
        session.add(
            ActivityEventRow(
                occurred_at=now - timedelta(minutes=5),
                event_type="deployment.observer.released",
                subsystem="deployment",
                status="completed",
                severity="info",
                action="release",
                related_module_id=FIXTURE_MODULE,
                evidence_refs=[],
                factual_summary="Observer surumu yayinlandi.",
                detail_json={},
                source="live",
                source_ref="test:no-runtime-block",
            )
        )
        session.commit()

    _index(sessions, repo)
    with sessions() as session:
        kinds = {
            row.truth_kind
            for row in session.scalars(
                select(ModuleProvenance).where(ModuleProvenance.module_id == FIXTURE_MODULE)
            )
        }
    assert TRUTH_RUNTIME not in kinds

    with sessions() as session:
        session.add(
            ActivityEventRow(
                occurred_at=now,
                event_type="deployment.observer.released",
                subsystem="deployment",
                status="completed",
                severity="info",
                action="release",
                version="1.4.0",
                related_module_id=FIXTURE_MODULE,
                evidence_refs=[],
                factual_summary="Observer calisiyor.",
                detail_json={"module": {"package_sha256": "a" * 64, "file": "worker.py"}},
                source="live",
                source_ref="test:with-runtime-block",
            )
        )
        session.commit()

    _index(sessions, repo)
    with sessions() as session:
        truths = {
            row.truth_kind: row
            for row in session.scalars(
                select(ModuleProvenance).where(ModuleProvenance.module_id == FIXTURE_MODULE)
            )
        }
        state = session.get(CodeModule, FIXTURE_MODULE).production_state

    assert TRUTH_RUNTIME in truths
    assert truths[TRUTH_RUNTIME].digest == "a" * 64
    assert truths[TRUTH_RUNTIME].version == "1.4.0"
    assert truths[TRUTH_RUNTIME].evidence_refs[0]["kind"] == "activity_event"
    assert state == PRODUCTION_STATE_RUNNING


def test_evidence_only_truths_refuse_to_be_written_without_evidence(sessions) -> None:
    with sessions() as session:
        with pytest.raises(ValueError, match="requires evidence_refs"):
            indexer._upsert_provenance(
                session,
                module_id="app.observer",
                truth_kind=TRUTH_RUNTIME,
                version="9.9.9",
                digest=None,
                observed_at=datetime.now(UTC),
                evidence_refs=[],
                confidence=1.0,
            )


def test_a_database_without_evidence_tables_still_indexes(repo: Path) -> None:
    engine = make_engine(with_evidence_tables=False)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    try:
        report = _index(factory, repo)
        assert report.modules_reparsed > 0
        with factory() as session:
            assert session.get(CodeModule, FIXTURE_MODULE) is not None
    finally:
        engine.dispose()


# ------------------------------------------------------------------ ui state


def test_indexing_publishes_thinking_with_counters_only(sessions, repo: Path) -> None:
    frames: list[tuple[str, str, dict]] = []

    def capture(state: str, *, subsystem: str, metadata: dict) -> None:
        frames.append((state, subsystem, metadata))

    config = IndexConfig(repo_root=repo)
    with sessions() as session:
        build_index(session, config=config, progress=IndexProgress(publisher=capture))
        session.commit()

    assert frames, "an index run must publish UI state"
    assert all(subsystem == SUBSYSTEM_SELF_MODEL for _, subsystem, _ in frames)
    thinking = [f for f in frames if f[0] == "thinking"]
    assert thinking
    assert frames[-1][0] == "idle"

    for _, _, metadata in frames:
        assert set(metadata) <= {"phase", "done", "total", "percent"}
        assert metadata.get("phase") in PHASES
        for key in ("done", "total", "percent"):
            if key in metadata:
                assert isinstance(metadata[key], int)
        # Nothing content-bearing: no path, no module id, no docstring.
        assert not any(isinstance(v, str) and "/" in v for v in metadata.values())


# --------------------------------------------------- the real repository


def test_indexes_this_repository_for_real() -> None:
    """Index the checkout this test is running from and assert on a package
    that really exists.

    PHASE 6 named ``app/uistate`` for this test; that package is owned by a
    parallel track and is not in this checkout, so the assertions run against
    ``app.ledger`` -- a package that carries everything the real test was meant
    to prove: symbols, a table, routes, a linked unit test, an ADR reference and
    a spec reference. ``app.selfmodel`` (this package) is asserted alongside it
    so the index is shown to contain itself.
    """
    repo_root = indexer.default_repo_root()
    if not (repo_root / "services/api/app/ledger/models.py").is_file():
        pytest.skip("not running from a full checkout")

    engine = make_engine()
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    try:
        with factory() as session:
            report = build_index(session, repo_root=repo_root)
            session.commit()
            assert report.modules_discovered > 100

            # the module and its package
            package = session.get(CodeModule, "app.ledger")
            models = session.get(CodeModule, "app.ledger.models")
            routes = session.get(CodeModule, "app.ledger.routes")
            assert package is not None and package.kind == MODULE_KIND_PACKAGE
            assert routes is not None and routes.kind == MODULE_KIND_MODULE
            assert models is not None and models.path == "services/api/app/ledger/models.py"
            assert models.purpose and models.purpose.startswith("Activity Ledger ORM rows")

            # its symbols, including the ORM table and a real route path
            names = {(s.kind, s.name) for s in session.scalars(select(CodeSymbol))}
            assert (SYMBOL_KIND_CLASS, "ActivityEventRow") in names
            assert (SYMBOL_KIND_TABLE, "activity_events") in names
            assert (SYMBOL_KIND_ROUTE, "GET /v1/ledger/policy") in names

            # its test link and its documentation links
            edges = {
                (e.from_module, e.to_module, e.kind) for e in session.scalars(select(CodeEdge))
            }
            assert ("tests.unit.test_ledger_service", "app.ledger.service", EDGE_TESTS) in edges
            assert any(
                frm == "app.ledger" and kind == EDGE_DOCUMENTED_BY and to.startswith("docs/")
                for frm, to, kind in edges
            )
            assert package.adr_refs, "app.ledger is referenced by at least one ADR"

            # the index contains itself, and says only what it can prove
            selfmodel = session.get(CodeModule, "app.selfmodel.indexer")
            assert selfmodel is not None
            assert selfmodel.production_state == PRODUCTION_STATE_SOURCE_ONLY
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(ModuleProvenance)
                    .where(
                        ModuleProvenance.module_id == "app.selfmodel.indexer",
                        ModuleProvenance.truth_kind == TRUTH_RUNTIME,
                    )
                )
                == 0
            )
    finally:
        engine.dispose()


# ------------------------------- defects found by independent review (2026-09-05)


def test_an_unmapped_component_never_attributes_runtime_truth_to_a_module() -> None:
    """A deployment event naming "observer" must not mark every module whose dotted id
    happens to end in `.observer` as running - and certainly not several at once. The four
    truth kinds exist to prevent exactly that confusion."""
    from app.selfmodel.indexer import _modules_for_component

    known = {"app.observer.diagnostic_observer", "app.other.diagnostic_observer", "app.observer"}
    # a bare leaf name is a guess, even when it matches exactly one module
    assert _modules_for_component("observer", known) == []
    assert _modules_for_component("diagnostic_observer", known) == []
    # the module's own id is evidence; so is an explicit hint
    assert _modules_for_component("app.observer", known) == ["app.observer"]
    assert _modules_for_component("APP.OBSERVER", known) == ["app.observer"]
    assert _modules_for_component("", known) == []
    assert _modules_for_component("nothing_like_this", known) == []


def test_the_walk_never_leaves_the_tree_through_a_junction(tmp_path) -> None:
    """A Windows junction is a reparse point, not a symlink: os.walk descends into it and
    the leaf never reports as a link, so containment must be decided by RESOLVING the path
    (the same fix app.security.checks.collect_files carries)."""
    import subprocess
    import sys

    from app.selfmodel.indexer import _contained, _iter_files

    root = tmp_path / "repo"
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "inside.py").write_text("x = 1\n", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret_module.py").write_text("y = 2\n", encoding="utf-8")

    # containment is decided on the resolved path, junction or not
    assert _contained(root / "pkg" / "inside.py", root.resolve()) is not None
    assert _contained(outside / "secret_module.py", root.resolve()) is None

    if sys.platform == "win32":
        link = root / "pkg" / "linked"
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(outside)],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            found = {p.name for p in _iter_files(root, (".py",))}
            assert "inside.py" in found
            assert "secret_module.py" not in found, "the walk left the tree through a junction"
