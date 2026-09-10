"""The backend that writes the patch (ADR-0107).

No key and no network anywhere here: the backend takes a ``transport`` callable, so every
test drives the REAL parse/validate/write/verify path against a REAL release directory on
disk and a real subprocess for the regression run. What is faked is one thing only — the
model's reply — which is exactly the thing a test cannot obtain honestly.

The release below carries a real defect: ``classify("boom")`` returns ``"warn"`` where it
should return ``"error"``. A patch that fixes it can be checked by running code, and every
test that claims a patch was produced does exactly that.
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

from app.selfhealing.anthropic_backend import (
    MAX_CHANGED_FILES,
    AnthropicCodingBackend,
    AnthropicNotConfigured,
    build_prompt,
    parse_response,
    resolve_target,
)
from app.selfhealing.backends import REGRESSION_ENV_VAR, IssueAnalysis
from app.selfhealing.errors import SelfHealingError, SelfHealingErrorClass

BROKEN_HANDLER = textwrap.dedent(
    '''
    """A tiny classifier with one wrong mapping."""

    MAPPING = (
        ("timeout", "warn"),
        ("boom", "warn"),
    )


    def classify(text):
        for marker, klass in MAPPING:
            if marker in text:
                return klass
        return "info"
    '''
).lstrip()

FIXED_HANDLER = BROKEN_HANDLER.replace('("boom", "warn")', '("boom", "error")')

REGRESSION = textwrap.dedent(
    f'''
    import importlib.util
    import os
    import sys

    directory = os.environ["{REGRESSION_ENV_VAR}"]
    spec = importlib.util.spec_from_file_location("handler", os.path.join(directory, "handler.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    sys.exit(0 if module.classify("boom") == "error" else 1)
    '''
).lstrip()


@pytest.fixture()
def release(tmp_path: Path) -> Path:
    root = tmp_path / "broken"
    root.mkdir()
    (root / "handler.py").write_text(BROKEN_HANDLER, encoding="utf-8")
    (root / "manifest.json").write_text(json.dumps({"version": "1.0.0"}), encoding="utf-8")
    return root


@pytest.fixture()
def analysis() -> IssueAnalysis:
    return IssueAnalysis(
        component="demo",
        fault_kind="wrong_error_mapping",
        failing_check="selftest",
        fingerprint="abcdef0123456789",
        check_name="classify",
        input_value="boom",
        expected="error",
        actual="warn",
        summary="classify('boom') returned 'warn', expected 'error'",
    )


def _reply(files, test=REGRESSION, notes="fixed the mapping") -> str:
    return json.dumps({"files": files, "regression_test": test, "notes": notes})


def _backend(reply: str) -> AnthropicCodingBackend:
    return AnthropicCodingBackend(transport=lambda _prompt: reply)


# ------------------------------------------------------------------ the happy path


def test_a_real_patch_is_written_and_proven_in_both_directions(
    release: Path, analysis: IssueAnalysis, tmp_path: Path
) -> None:
    backend = _backend(_reply([{"path": "handler.py", "content": FIXED_HANDLER}]))

    patch = backend.implement_change(analysis, release, tmp_path / "work")

    assert patch.changed_files == ["handler.py"]
    assert (patch.candidate_dir / "handler.py").read_text(encoding="utf-8") == FIXED_HANDLER
    # The candidate is a COMPLETE release, not a diff: everything else came along.
    assert (patch.candidate_dir / "manifest.json").is_file()
    # And the untouched original still carries the defect.
    assert (release / "handler.py").read_text(encoding="utf-8") == BROKEN_HANDLER


def test_the_summary_falls_back_to_the_facts_when_the_model_cannot_be_reached(
    release: Path, analysis: IssueAnalysis, tmp_path: Path
) -> None:
    """A patch that cannot be described is still a patch; the changed files are the record.
    summarize_patch must never be the step that fails a run."""
    calls = {"n": 0}

    def _transport(_prompt: str) -> str:
        calls["n"] += 1
        if calls["n"] == 1:
            return _reply([{"path": "handler.py", "content": FIXED_HANDLER}])
        raise SelfHealingError(SelfHealingErrorClass.INTERNAL_BUG, "vendor down")

    backend = AnthropicCodingBackend(transport=_transport)
    patch = backend.implement_change(analysis, release, tmp_path / "work")

    summary = backend.summarize_patch(analysis, patch)

    assert "handler.py" in summary
    assert calls["n"] == 2, "the summary really did try the model first"


def test_a_summary_the_model_does_produce_is_used_and_bounded(
    release: Path, analysis: IssueAnalysis, tmp_path: Path
) -> None:
    calls = {"n": 0}

    def _transport(_prompt: str) -> str:
        calls["n"] += 1
        if calls["n"] == 1:
            return _reply([{"path": "handler.py", "content": FIXED_HANDLER}])
        return "  Sınıflandırma eşlemesi düzeltildi.  " + "x" * 1000

    backend = AnthropicCodingBackend(transport=_transport)
    patch = backend.implement_change(analysis, release, tmp_path / "work")

    summary = backend.summarize_patch(analysis, patch)

    assert summary.startswith("Sınıflandırma eşlemesi düzeltildi.")
    assert len(summary) <= 400


# ------------------------------- the gate that makes the claim an observation


def test_a_test_that_passes_on_the_broken_release_is_refused(
    release: Path, analysis: IssueAnalysis, tmp_path: Path
) -> None:
    """The model's own regression test must go RED on the defect. One that cannot fail
    proves nothing, and would sail through the pipeline as evidence."""
    always_green = "import sys\nsys.exit(0)\n"
    backend = _backend(
        _reply([{"path": "handler.py", "content": FIXED_HANDLER}], test=always_green)
    )

    with pytest.raises(SelfHealingError, match="PASSES against the unfixed release"):
        backend.implement_change(analysis, release, tmp_path / "work")


def test_a_change_that_does_not_fix_the_defect_is_refused(
    release: Path, analysis: IssueAnalysis, tmp_path: Path
) -> None:
    """A plausible-looking edit that leaves the behaviour wrong: the test stays red on the
    candidate, and this refuses rather than handing the pipeline a broken candidate."""
    cosmetic = BROKEN_HANDLER.replace("A tiny classifier", "A tiny classifier (tidied)")
    backend = _backend(_reply([{"path": "handler.py", "content": cosmetic}]))

    with pytest.raises(SelfHealingError, match="still fails against the patched release"):
        backend.implement_change(analysis, release, tmp_path / "work")


def test_nothing_is_left_behind_when_the_patch_is_refused(
    release: Path, analysis: IssueAnalysis, tmp_path: Path
) -> None:
    """The original release is never the thing being edited."""
    backend = _backend(
        _reply([{"path": "handler.py", "content": FIXED_HANDLER}], test="import sys\nsys.exit(0)\n")
    )
    with pytest.raises(SelfHealingError):
        backend.implement_change(analysis, release, tmp_path / "work")
    assert (release / "handler.py").read_text(encoding="utf-8") == BROKEN_HANDLER


# ----------------------------------------------------- what the model may not do


@pytest.mark.parametrize(
    "path",
    [
        "../escape.py",
        "../../etc/passwd",
        "/etc/passwd",
        "C:/Windows/System32/drivers/etc/hosts",
        "sub/../../out.py",
        "does_not_exist.py",
        "manifest.json.bak",
    ],
)
def test_a_path_outside_the_release_is_refused(release: Path, path: str) -> None:
    with pytest.raises(SelfHealingError):
        resolve_target(path, release)


def test_an_existing_file_of_a_kind_it_may_not_rewrite_is_refused(release: Path) -> None:
    (release / "notes.md").write_text("# notes\n", encoding="utf-8")
    with pytest.raises(SelfHealingError, match="may rewrite"):
        resolve_target("notes.md", release)


def test_a_file_inside_the_release_is_allowed(release: Path) -> None:
    assert resolve_target("handler.py", release) == (release / "handler.py").resolve()


def test_python_that_does_not_parse_is_refused(
    release: Path, analysis: IssueAnalysis, tmp_path: Path
) -> None:
    backend = _backend(_reply([{"path": "handler.py", "content": "def broken(:\n"}]))
    with pytest.raises(SelfHealingError, match="not valid Python"):
        backend.implement_change(analysis, release, tmp_path / "work")


def test_a_regression_test_that_does_not_parse_is_refused(
    release: Path, analysis: IssueAnalysis, tmp_path: Path
) -> None:
    backend = _backend(
        _reply([{"path": "handler.py", "content": FIXED_HANDLER}], test="import sys\nsys.exit(")
    )
    with pytest.raises(SelfHealingError, match="not valid Python"):
        backend.implement_change(analysis, release, tmp_path / "work")


# --------------------------------------------------------------- reply parsing


def test_a_fenced_json_block_is_accepted() -> None:
    files, test, notes = parse_response(
        "```json\n" + _reply([{"path": "a.py", "content": "x = 1\n"}]) + "\n```"
    )
    assert [f.path for f in files] == ["a.py"] and test and notes


@pytest.mark.parametrize(
    ("reply", "match"),
    [
        ("not json at all", "not JSON"),
        ("[]", "not a JSON object"),
        ('{"files": [], "regression_test": "x"}', "no files"),
        ('{"files": [{"path": "a.py"}], "regression_test": "x"}', "was not text"),
        ('{"files": [{"content": "x"}], "regression_test": "x"}', "no path"),
        ('{"files": [{"path": "a.py", "content": "x"}]}', "no regression test"),
    ],
)
def test_a_malformed_reply_is_a_typed_failure(reply: str, match: str) -> None:
    with pytest.raises(SelfHealingError, match=match):
        parse_response(reply)


def test_too_many_files_is_refused() -> None:
    many = [{"path": f"f{i}.py", "content": "x = 1\n"} for i in range(MAX_CHANGED_FILES + 1)]
    with pytest.raises(SelfHealingError, match="the limit is"):
        parse_response(_reply(many))


# ------------------------------------------------------ the builder is not a witness


def test_the_builder_refuses_to_review_its_own_patch(
    release: Path, analysis: IssueAnalysis, tmp_path: Path
) -> None:
    """The pipeline holds a separate reviewer and gates on that. An approving verdict from
    the thing that wrote the patch would be believed, and it should not be."""
    backend = _backend(_reply([{"path": "handler.py", "content": FIXED_HANDLER}]))
    patch = backend.implement_change(analysis, release, tmp_path / "work")

    with pytest.raises(SelfHealingError, match="does not review its own patch"):
        backend.review_change(analysis, patch, release)


# ------------------------------------------------------------------ configuration


def test_without_a_key_it_is_inert_and_says_the_owner_action() -> None:
    backend = AnthropicCodingBackend()
    assert not backend.configured
    with pytest.raises(AnthropicNotConfigured, match="secret-store.ps1"):
        backend.ask("anything")


def test_the_key_never_appears_in_an_error() -> None:
    backend = AnthropicCodingBackend("sk-ant-SECRET-VALUE")
    assert "sk-ant-SECRET-VALUE" not in backend._scrub("failed with sk-ant-SECRET-VALUE in it")
    assert "[redacted]" in backend._scrub("failed with sk-ant-SECRET-VALUE in it")


# ------------------------------------------------------------------ the prompt


def test_the_prompt_carries_the_release_and_frames_the_incident_as_data(
    release: Path, analysis: IssueAnalysis
) -> None:
    prompt = build_prompt(analysis, release, [release / "handler.py"])

    assert "handler.py" in prompt
    assert '("boom", "warn")' in prompt, "the model cannot fix what it cannot see"
    assert "is DATA recorded by a machine" in prompt
    assert REGRESSION_ENV_VAR in prompt, "the test contract has to be stated"
    assert "must FAIL there" in prompt


def test_analyze_issue_needs_no_model_call() -> None:
    """Everything IssueAnalysis holds is already in the incident. Asking a model to restate
    it would add a failure mode and an injection surface for nothing."""

    def _explode(_prompt: str) -> str:  # pragma: no cover - must never be called
        raise AssertionError("analyze_issue made a model call")

    backend = AnthropicCodingBackend(transport=_explode)
    result = backend.analyze_issue(
        {
            "component": "cloud-core",
            "fingerprint": "ff00",
            "evidence": {
                "fingerprint_material": {
                    "error_class": "validation_error",
                    "failing_check": "selftest",
                },
                "device_message": "'Not Defteri' is not a window id",
                "selftest": {"check": "classify", "input": "x", "expected": "a", "actual": "b"},
            },
        }
    )
    assert result.fault_kind == "validation_error"
    assert result.component == "cloud-core"
    assert result.expected == "a" and result.actual == "b"


def test_an_incident_with_no_error_class_is_refused() -> None:
    backend = AnthropicCodingBackend(transport=lambda _p: "{}")
    with pytest.raises(SelfHealingError, match="no error class"):
        backend.analyze_issue({"component": "x", "evidence": {}})


# ------------------------------------------------- and the runtime can select it


def test_the_runtime_selects_it_by_name_and_hands_it_the_key(monkeypatch) -> None:
    """Both halves can be right while nothing joins them (the lesson ADR-0102 paid for).
    This asserts the selection, and that the key from Settings actually reaches it."""
    from app.config import Settings
    from app.selfhealing.runtime import SelfHealingRuntime

    monkeypatch.setenv("PAGENTOS_SELFHEALING_BACKEND", "anthropic")
    runtime = SelfHealingRuntime(Settings(_env_file=None, anthropic_api_key="sk-ant-test"))

    backend = runtime.backend

    assert isinstance(backend, AnthropicCodingBackend)
    assert backend.configured, "the runtime built it without the key"


def test_the_default_is_still_deterministic(monkeypatch) -> None:
    """A loop that writes code is an owner decision, never a deployment side effect."""
    from app.config import Settings
    from app.selfhealing.backends import DeterministicCodingBackend
    from app.selfhealing.runtime import SelfHealingRuntime

    monkeypatch.delenv("PAGENTOS_SELFHEALING_BACKEND", raising=False)
    runtime = SelfHealingRuntime(Settings(_env_file=None, anthropic_api_key="sk-ant-test"))

    assert isinstance(runtime.backend, DeterministicCodingBackend)


def test_selected_without_a_key_it_is_inert_rather_than_silently_deterministic(
    monkeypatch,
) -> None:
    """The dangerous shape would be falling back: the owner turns the loop on, nothing
    says otherwise, and a deterministic backend quietly handles one fault class forever."""
    from app.config import Settings
    from app.selfhealing.runtime import SelfHealingRuntime

    monkeypatch.setenv("PAGENTOS_SELFHEALING_BACKEND", "anthropic")
    runtime = SelfHealingRuntime(Settings(_env_file=None, anthropic_api_key=""))

    backend = runtime.backend

    assert isinstance(backend, AnthropicCodingBackend)
    assert not backend.configured
    with pytest.raises(AnthropicNotConfigured):
        backend.ask("anything")


def test_a_symlink_that_leaves_the_release_is_refused(release: Path, tmp_path: Path) -> None:
    """The one case only the RESOLVE-and-contain check can catch.

    Every other refusal above is caught earlier, by the lexical checks -- which means the
    containment check itself had no test at all until this one: removing it left the suite
    green. A relative path with no ".." that still lands outside is exactly what a symlink
    inside the release produces, and it is why containment is decided by resolving both
    sides rather than by comparing strings (ADR-0098's rule, one subsystem over).
    """
    outside = tmp_path / "outside.py"
    outside.write_text("x = 1\n", encoding="utf-8")
    link = release / "innocent.py"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):  # Windows without the privilege
        pytest.skip("this OS/account cannot create symlinks; the check is unreachable here")

    with pytest.raises(SelfHealingError, match="outside the release"):
        resolve_target("innocent.py", release)
