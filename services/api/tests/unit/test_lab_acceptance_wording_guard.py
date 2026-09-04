"""Unit tests for the SHADOW candidate `acceptance_wording_guard`.

The candidate lives OUTSIDE the application package, at
``services/api/lab/candidates/acceptance_wording_guard/``, and is loaded here by
path rather than imported as ``app.*``. That is not a convenience: it is the
property under test. ``test_no_production_module_imports_the_lab`` walks every
module under ``services/api/app`` and fails if any of them reaches the lab tree.

Covered (SPEC.md "Acceptance criteria"):

1. the two real historical shapes — the PowerShell ``-like "Efendim, son ara*"``
   from ADR-0051 addendum 4 and a Python ``assert "<prose>" in speech``;
2. machine-token equality is never a finding;
3. the allow-list comment, including a reasonless allow;
4. bounded runtime on a large synthetic tree, and on a pathological line;
5. no crash on unreadable/oversized/binary files;
6. the no-production-import guard.
"""

from __future__ import annotations

import ast
import importlib.util
import sys
import time
from pathlib import Path
from types import ModuleType

import pytest

from app.evolution.authority import scan_module_for_forbidden_imports
from app.evolution.sandbox import SandboxPolicy, protected_trees

API_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = API_ROOT.parents[1]
LAB_ROOT = API_ROOT / "lab"
CANDIDATE_ROOT = LAB_ROOT / "candidates" / "acceptance_wording_guard"
CANDIDATE_SRC = CANDIDATE_ROOT / "src" / "acceptance_wording_guard.py"

#: Bound asserted by `test_a_large_tree_scans_within_the_benchmark_bound`. The
#: real repository (231 files) scans in ~0.35s on the owner's machine; this is
#: an order of magnitude of headroom for a tree five times larger, so it fails
#: on a genuine complexity regression and not on a slow CI runner.
BENCHMARK_FILES = 1200
BENCHMARK_MAX_SECONDS = 20.0


def _load_candidate() -> ModuleType:
    """Load the candidate from its path, without putting it on ``app.*``."""
    spec = importlib.util.spec_from_file_location("lab_acceptance_wording_guard", CANDIDATE_SRC)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


guard = _load_candidate()


# --------------------------------------------------------------- the two shapes


PS_HISTORICAL = """
Test-Case "briefing derived from the real research run" {
    $speech_head = $record.speech.Substring(0, 40)
    Assert-True ($speech_head -like "Efendim, son ara*") "briefing head"
}
""".lstrip()

PY_HISTORICAL = """
def test_briefing_reads_the_research_run(session):
    speech = briefing.speech_for(event)
    assert "Efendim, son arastirma gorevi tamamlandi" in speech
""".lstrip()


def test_the_powershell_shape_that_failed_a_working_system_is_found() -> None:
    """ADR-0051 addendum 4: `speech_head -like "Efendim, son ara*"`."""
    findings, allowed = guard.scan_text(
        "scripts/tests/owner-explain.tests.ps1", PS_HISTORICAL, "powershell"
    )
    assert allowed == []
    assert len(findings) == 1
    finding = findings[0]
    assert finding.rule == guard.RULE_GENERATED_WORDING
    assert finding.severity == guard.SEVERITY_HIGH
    assert finding.context == guard.CONTEXT_QUALIFICATION
    assert finding.operator == "-like"
    assert "speech_head" in finding.subject
    assert finding.shape["kind"] == guard.LITERAL_PROSE
    assert finding.shape["wildcard"] is True
    assert finding.shape["words"] == 3
    assert finding.suggestion


def test_the_python_prose_in_speech_shape_is_found() -> None:
    findings, _ = guard.scan_text(
        "services/api/tests/unit/test_ledger_briefing.py", PY_HISTORICAL, "python"
    )
    assert len(findings) == 1
    finding = findings[0]
    assert finding.rule == guard.RULE_GENERATED_WORDING
    assert finding.operator == "in"
    assert finding.subject == "speech"
    # A tests/ tree is demoted one step: a renderer's own unit test pinning its
    # output is not the same claim as a qualification gate (SPEC.md).
    assert finding.context == guard.CONTEXT_TEST
    assert finding.severity == guard.SEVERITY_MEDIUM


def test_a_finding_never_carries_the_matched_string() -> None:
    findings, _ = guard.scan_text("scripts/x.ps1", PS_HISTORICAL, "powershell")
    payload = repr(findings[0].to_dict())
    assert "Efendim" not in payload
    assert "son ara" not in payload
    assert findings[0].shape["digest"].startswith("sha256:")


def test_a_subject_is_an_identifier_path_never_source_text() -> None:
    """`ast.unparse` reproduced string arguments; `_subject_path` must not."""
    source = 'def t():\n    assert "bir iki uc dort" in normalize("1.234,56 TL")\n'
    findings, _ = guard.scan_text("scripts/x.py", source, "python")
    assert [f.subject for f in findings] == [] or all("1.234" not in f.subject for f in findings)
    other = 'def t():\n    assert speech_of("1.234,56 TL") == "bir iki uc dort bes"\n'
    findings, _ = guard.scan_text("scripts/x.py", other, "python")
    assert len(findings) == 1
    assert findings[0].subject == "speech_of()"


# ------------------------------------------------------------- non-detection


MACHINE_TOKENS = """
Assert-True ($result.Verdict -eq "PASS") "verdict"
Assert-True ($report.error_class -eq "insufficient_valid_findings") "typed error"
Assert-True ($state.Status -eq "evolution.shadow_ready") "lifecycle status"
Assert-True ($agent.Version -eq "0.4.0") "version"
""".lstrip()

PY_MACHINE_TOKENS = """
def test_tokens(result):
    assert result.speech == "PASS"
    assert result.summary == "insufficient_valid_findings"
    assert result.narration == "evolution.shadow_ready"
    assert result.speech_head == "cloud_core"
""".lstrip()


def test_machine_token_equality_is_never_a_finding() -> None:
    findings, _ = guard.scan_text("scripts/q.ps1", MACHINE_TOKENS, "powershell")
    assert findings == []


def test_machine_token_equality_is_not_a_finding_even_on_a_speech_subject() -> None:
    """The subject gate must not be the *only* gate; the literal matters too."""
    findings, _ = guard.scan_text("scripts/q.py", PY_MACHINE_TOKENS, "python")
    assert findings == []


@pytest.mark.parametrize(
    ("literal", "kind"),
    [
        ("PASS", "machine_token"),
        ("insufficient_valid_findings", "machine_token"),
        ("evolution.shadow_ready", "machine_token"),
        ("0.4.0", "machine_token"),
        ("openai-realtime", "machine_token"),
        ("", "empty"),
        ("^cat '/opt/pagentos/app/RELEASE'", "pattern"),
        ("[0-9a-f]{12}", "pattern"),
        ("Efendim, son ara*", "prose"),
        ("must be replaced", "prose"),
        ("POST /v1/devices/enroll", "short_text"),
        ("gorev tamam", "short_text"),
        ("Gorev tamamlandi", "short_text"),
    ],
)
def test_classify_literal_is_a_total_pure_function(literal: str, kind: str) -> None:
    assert guard.classify_literal(literal) == kind


def test_deterministic_error_messages_are_not_findings() -> None:
    """The shape that would drown the report if detection were literal-driven."""
    source = (
        'Assert-True ($result.StdOut -match "must be replaced") "plan"\n'
        'Assert-True ($_.Exception.Message -match "did not become healthy") "why"\n'
        'Assert-True ($h.Output -match "does not list openai-realtime") "provider"\n'
    )
    findings, _ = guard.scan_text("scripts/tests/x.tests.ps1", source, "powershell")
    assert findings == []


def test_a_subject_holding_file_contents_is_suppressed_not_reported() -> None:
    """`owner-explain.tests.ps1` greps its own script; that is not speech."""
    source = (
        '$text = [IO.File]::ReadAllText((Join-Path $repoRoot "scripts\\voice\\x.ps1"))\n'
        'Assert-True ($text -match "briefing cites ledger events and a job") "ok"\n'
    )
    suppressed: list[dict[str, object]] = []
    findings, _ = guard.scan_text(
        "scripts/tests/owner-explain.tests.ps1",
        source,
        "powershell",
        suppressed=suppressed,
    )
    assert findings == []
    assert len(suppressed) == 1
    assert suppressed[0]["reason"] == "subject_holds_file_contents"


# ---------------------------------------------------------------- allow-list


def test_an_inline_allow_with_a_reason_keeps_the_assertion() -> None:
    source = (
        'Assert-True ($speech_head -like "Efendim, son ara*") "x"  '
        "# wording-guard: allow the fixed greeting is contractual\n"
    )
    findings, allowed = guard.scan_text("scripts/q.ps1", source, "powershell")
    assert findings == []
    assert len(allowed) == 1
    assert allowed[0].reason == "the fixed greeting is contractual"
    assert allowed[0].to_dict()["allow_without_reason"] is False


def test_an_allow_on_the_line_above_also_applies() -> None:
    source = (
        "# wording-guard: allow pinned by the owner on 2026-09-05\n"
        'assert "Efendim son arastirma gorevi" in speech\n'
    )
    findings, allowed = guard.scan_text("scripts/q.py", source, "python")
    assert findings == []
    assert allowed[0].reason == "pinned by the owner on 2026-09-05"


def test_a_reasonless_allow_is_honoured_but_reported() -> None:
    source = 'assert "Efendim son arastirma gorevi" in speech  # wording-guard: allow\n'
    findings, allowed = guard.scan_text("scripts/q.py", source, "python")
    assert findings == []
    assert allowed[0].reason == ""
    assert allowed[0].to_dict()["allow_without_reason"] is True


def test_an_unrelated_comment_does_not_allow_anything() -> None:
    source = '# this is fine, honestly\nassert "Efendim son arastirma gorevi" in speech\n'
    findings, allowed = guard.scan_text("scripts/q.py", source, "python")
    assert allowed == []
    assert len(findings) == 1


# ------------------------------------------------------------------- robustness


def test_unreadable_oversized_and_binary_files_are_skipped_not_fatal(tmp_path) -> None:
    surface = tmp_path / "scripts"
    surface.mkdir()
    (surface / "binary.ps1").write_bytes(b'\x00\x01\x02 -eq "a b c"\x00')
    (surface / "huge.ps1").write_bytes(b"# pad\n" * (guard.MAX_FILE_BYTES // 4))
    (surface / "good.ps1").write_text(
        'Assert-True ($speech -like "Efendim son arastirma gorevi") "x"\n',
        encoding="utf-8",
    )
    report = guard.scan_paths(tmp_path, surfaces=("scripts",))
    assert report.files_scanned == 1
    reasons = {entry["reason"] for entry in report.skipped}
    assert reasons == {"binary", "too_large"}
    assert len(report.findings) == 1


def test_a_file_with_a_syntax_error_yields_no_findings_and_no_crash() -> None:
    findings, allowed = guard.scan_text("scripts/broken.py", "def (:\n", "python")
    assert (findings, allowed) == ([], [])


def test_undecodable_bytes_do_not_stop_the_scan(tmp_path) -> None:
    surface = tmp_path / "scripts"
    surface.mkdir()
    (surface / "latin.ps1").write_bytes(
        b'Assert-True ($speech -like "Efendim son ara\xff gorevi") "x"\n'
    )
    report = guard.scan_paths(tmp_path, surfaces=("scripts",))
    assert report.files_scanned == 1
    assert report.skipped == []


def test_a_surface_may_not_escape_the_root(tmp_path) -> None:
    root = tmp_path / "repo"
    (root / "scripts").mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.ps1").write_text(
        'Assert-True ($speech -like "Efendim son arastirma gorevi") "x"\n',
        encoding="utf-8",
    )
    report = guard.scan_paths(root, surfaces=("../outside",))
    assert report.files_scanned == 0
    assert report.findings == []


def test_an_excluded_directory_is_matched_relative_to_the_root(tmp_path) -> None:
    """A repo checked out under `.claude/worktrees/...` must still be scanned.

    The first real run of this guard reported "0 findings" from 0 files for
    exactly this reason, which is the most dangerous output a checker has.
    """
    root = tmp_path / ".claude" / "worktrees" / "wt"
    (root / "scripts").mkdir(parents=True)
    (root / "scripts" / "q.ps1").write_text(
        'Assert-True ($speech -like "Efendim son arastirma gorevi") "x"\n',
        encoding="utf-8",
    )
    report = guard.scan_paths(root, surfaces=("scripts",))
    assert report.files_scanned == 1
    assert len(report.findings) == 1


def test_an_excluded_directory_inside_the_root_is_still_excluded(tmp_path) -> None:
    surface = tmp_path / "scripts"
    (surface / "node_modules").mkdir(parents=True)
    (surface / "node_modules" / "q.ps1").write_text(
        'Assert-True ($speech -like "Efendim son arastirma gorevi") "x"\n',
        encoding="utf-8",
    )
    report = guard.scan_paths(tmp_path, surfaces=("scripts",))
    assert report.files_scanned == 0


# -------------------------------------------------------------------- benchmark


def test_a_pathological_line_does_not_hang() -> None:
    """No regex here may backtrack: a 200k-character line must be linear."""
    line = 'Assert-True ($speech -match "' + ("a " * 100_000) + '") "x"\n'
    started = time.perf_counter()
    guard.scan_text("scripts/q.ps1", line, "powershell")
    assert time.perf_counter() - started < 1.0


def test_a_large_tree_scans_within_the_benchmark_bound(tmp_path) -> None:
    surface = tmp_path / "scripts"
    surface.mkdir()
    body = (
        'Assert-True ($result.StdOut -match "must be replaced") "plan"\n'
        'Assert-True ($report.error_class -eq "insufficient_valid_findings") "typed"\n'
        'Assert-True ($speech_head -like "Efendim, son ara*") "briefing head"\n'
    ) * 20
    for index in range(BENCHMARK_FILES):
        (surface / f"case_{index:05d}.ps1").write_text(body, encoding="utf-8")

    report = guard.scan_paths(tmp_path, surfaces=("scripts",))
    assert report.files_scanned == BENCHMARK_FILES
    assert report.truncated is False
    # One finding per file: the 20 repetitions are the same (path, line-shape)
    # key only when the line number matches, so assert the lower bound instead.
    assert len(report.findings) >= BENCHMARK_FILES
    assert report.duration_s < BENCHMARK_MAX_SECONDS, (
        f"scanned {BENCHMARK_FILES} files in {report.duration_s:.2f}s, "
        f"bound is {BENCHMARK_MAX_SECONDS}s"
    )


def test_max_files_truncates_rather_than_running_forever(tmp_path) -> None:
    surface = tmp_path / "scripts"
    surface.mkdir()
    for index in range(5):
        (surface / f"f{index}.ps1").write_text("# nothing\n", encoding="utf-8")
    report = guard.scan_paths(tmp_path, surfaces=("scripts",), max_files=2)
    assert report.files_scanned == 2
    assert report.truncated is True


# ----------------------------------------------------------- the boundary itself


#: Import roots no module under `services/api/app` may reach.
_FORBIDDEN_IMPORT_ROOTS = ("lab", "acceptance_wording_guard", "lab_acceptance_wording_guard")


def _imported_module_names(source: Path) -> set[str]:
    """Every module name a Python file imports, from the AST (not a grep)."""
    try:
        tree = ast.parse(source.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:  # pragma: no cover - the package parses
        return set()
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_no_production_module_imports_the_lab() -> None:
    """The candidate is reachable from tests and from nowhere in `app/`.

    Checked on the import graph rather than by substring: a grep for
    ``"from lab"`` matches ``from labels import ...`` and would fail the suite
    on innocent code, which is the same false-positive mistake this candidate
    exists to prevent.
    """
    offenders: list[str] = []
    for source in sorted((API_ROOT / "app").rglob("*.py")):
        for name in _imported_module_names(source):
            root = name.split(".", 1)[0]
            if root in _FORBIDDEN_IMPORT_ROOTS:
                offenders.append(f"{source.relative_to(API_ROOT).as_posix()} -> {name}")
    assert offenders == [], offenders


def test_the_import_guard_would_actually_catch_a_violation(tmp_path) -> None:
    """The guard above must fail on a real import, not merely pass vacuously."""
    offender = tmp_path / "offender.py"
    offender.write_text(
        "from lab.candidates.acceptance_wording_guard.src import x\n", encoding="utf-8"
    )
    names = _imported_module_names(offender)
    assert any(n.split(".", 1)[0] in _FORBIDDEN_IMPORT_ROOTS for n in names)


def test_the_lab_is_not_a_package_of_the_application() -> None:
    """No `__init__.py` chain makes the lab importable as a top-level package."""
    assert not (LAB_ROOT / "__init__.py").exists()
    assert not (CANDIDATE_ROOT / "__init__.py").exists()
    assert not (CANDIDATE_ROOT / "src" / "__init__.py").exists()


def test_the_lab_root_is_a_legal_sandbox_root() -> None:
    """It overlaps no protected tree — which `app/evolution/app` would."""
    SandboxPolicy(LAB_ROOT)
    for protected in protected_trees():
        assert not LAB_ROOT.resolve().is_relative_to(protected)
        assert not protected.is_relative_to(LAB_ROOT.resolve())


def test_the_candidate_imports_no_forbidden_module() -> None:
    """The engine's own import guard, applied to the candidate's source."""
    assert scan_module_for_forbidden_imports(CANDIDATE_SRC) == []


def test_the_candidate_ships_its_spec_threat_model_and_notes() -> None:
    for name in ("SPEC.md", "THREAT_MODEL.md", "NOTES.md"):
        path = CANDIDATE_ROOT / name
        assert path.is_file(), name
        assert len(path.read_text(encoding="utf-8")) > 500, name


def test_the_candidate_is_pure_stdlib_and_does_no_io_beyond_reading() -> None:
    source = CANDIDATE_SRC.read_text(encoding="utf-8")
    for forbidden in (
        "import requests",
        "import httpx",
        "import subprocess",
        "urllib.request",
        "socket.",
        "os.system",
        ".write_text(",
        ".write_bytes(",
    ):
        assert forbidden not in source, forbidden


def test_the_repository_itself_can_be_scanned() -> None:
    """The real acceptance surfaces, as recorded in evidence/repo_scan.json."""
    report = guard.scan_paths(REPO_ROOT, surfaces=guard.DEFAULT_SURFACES)
    assert report.files_scanned > 100
    assert report.skipped == []
    # Every finding must name a real file and a plausible line.
    for finding in report.findings:
        assert (REPO_ROOT / finding.path).is_file(), finding.path
        assert finding.line >= 1
        assert finding.context in {
            guard.CONTEXT_QUALIFICATION,
            guard.CONTEXT_TEST,
            guard.CONTEXT_SOURCE,
        }
