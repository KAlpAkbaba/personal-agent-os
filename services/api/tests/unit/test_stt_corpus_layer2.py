"""The STT corpus again, with layer 2 as production configures it (ADR-0245 'At merge').

ADR-0224 addendum 4 measured 73 of 106 WITHOUT the semantic engine; since ADR-0245
``create_app`` builds it from the local embedder and the shipped exemplars. This file runs the
106 renderings with that engine - built by ``stt_harness.production_engine()`` the way
production builds it - beside the no-engine run, and writes the two side by side when
``PAGENTOS_STT_LAYER2_REPORT`` names a path. The percentage is NOT asserted: it is what is
measured. What is asserted is that the measurement is what it says it is.

The gate's time: the no-engine run is ``test_stt_utterance_corpus.py``'s own module cache
(imported, never run twice), so this file adds ONE run of the corpus. The repeat run of the
determinism check is made only when ``PAGENTOS_STT_LAYER2_REPEAT`` is set.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any

import pytest

from app.memory.providers import DEFAULT_LOCAL_MODEL
from app.voice.understanding import combine, policy
from tests.unit import test_stt_utterance_corpus as no_engine_suite
from tests.voice_corpus import stt_harness
from tests.voice_corpus.stt_corpus import ORIGIN_REAL, STT_CORPUS_VERSION
from tests.voice_corpus.stt_harness import (
    LAYER2_REPORT_KEYS,
    VERDICT_WRONG_DEVICE,
    ProductionEngineUnavailable,
    SttResult,
    build_layer2_report,
    build_stt_report,
    compare_runs,
    failure_classes,
    layer2_markdown,
    production_engine,
    run_stt_case,
)

CASES = no_engine_suite.CASES
EXEMPLARS_FILE = (
    Path(combine.__file__).resolve().parent / "exemplars.json"
)  # the shipped file, read here on its own
REPORT_ENV = "PAGENTOS_STT_LAYER2_REPORT"
REPEAT_ENV = "PAGENTOS_STT_LAYER2_REPEAT"
#: The local model the engine embeds with (memory-embedding-granite-measure). Unset: the
#: production default, so the gate's run is today's run.
ENGINE_MODEL_ENV = "PAGENTOS_STT_CORPUS_ENGINE_MODEL"
#: A hang guard for one whole run of the corpus, never a performance claim.
RUN_DEADLINE_S = 1800.0

_ENGINE: dict[str, Any] = {}
_RUNS: dict[str, Any] = {}


def _engine_model() -> str:
    return os.environ.get(ENGINE_MODEL_ENV) or DEFAULT_LOCAL_MODEL


def _engine() -> Any:
    if "built" not in _ENGINE:
        _ENGINE["built"] = production_engine(model_name=_engine_model())
    return _ENGINE["built"]


def _run_with_engine(key: str) -> list[SttResult]:
    """One run of the corpus with the production engine, cached per module. Per case: how
    many texts the embedder was asked for while that case ran."""
    if key not in _RUNS:
        built = _engine()
        started = time.monotonic()
        results: list[SttResult] = []
        asked: dict[str, int] = {}
        for case in CASES:
            if time.monotonic() - started > RUN_DEADLINE_S:
                raise TimeoutError(
                    f"{key}: the corpus run passed {RUN_DEADLINE_S} s at {case.case_id}"
                )
            before = len(built.asked)
            results.append(run_stt_case(case, engine=built.engine))
            asked[case.case_id] = len(built.asked) - before
        _RUNS[key] = results
        _RUNS[f"{key}.asked"] = asked
        _RUNS[f"{key}.seconds"] = round(time.monotonic() - started, 1)
    return _RUNS[key]


def _no_engine() -> list[SttResult]:
    return no_engine_suite._all_results()


def _layer2() -> list[SttResult]:
    return _run_with_engine("layer2")


def _verdicts(results: list[SttResult]) -> dict[str, str]:
    return {r.case_id: r.verdict for r in results}


# --- (1) the no-engine run is the existing measurement --------------------------------------


def test_the_no_engine_run_is_the_known_gaps_case_for_case():
    results = _no_engine()
    assert [r.case_id for r in results] == [c.case_id for c in CASES]
    failing = {r.case_id: r.verdict for r in results if not r.correct}
    assert failing == dict(no_engine_suite.KNOWN_GAPS)
    # The new keyword, left at None, is today's path: the same verdict and a recorded layer.
    by_id = {r.case_id: r for r in results}
    sample = [c for c in CASES if c.origin == ORIGIN_REAL]
    seen_distortions: set[str | None] = set()
    for case in CASES:
        if case.origin != ORIGIN_REAL and case.distortion not in seen_distortions:
            seen_distortions.add(case.distortion)
            sample.append(case)
    for case in sample:
        again = run_stt_case(case, engine=None)
        assert again.verdict == by_id[case.case_id].verdict, case.case_id
        assert again.seen is not None and again.seen.layer == by_id[case.case_id].seen.layer


# --- (2) the production-engine run really used the engine -----------------------------------


def test_the_engine_is_the_local_embedders_with_the_shipped_exemplars():
    built = _engine()
    assert built.report.active == "local"
    assert built.report.semantic is True
    assert built.report.model_id.startswith("local-")
    shipped = json.loads(EXEMPLARS_FILE.read_text(encoding="utf-8"))["exemplars"]
    assert built.exemplars == len(shipped)
    assert built.engine is not None


def test_the_embedder_was_asked_for_every_case_no_rule_matched():
    results = _layer2()
    asked = _RUNS["layer2.asked"]
    no_rule = [r.case_id for r in _no_engine() if r.intent in (None, "none")]
    assert no_rule, "no case fell through the rules without the engine - nothing was measured"
    never_asked = [case_id for case_id in no_rule if asked[case_id] < 1]
    assert never_asked == []
    # ... and layer 2 is what decided at least one of them.
    assert any(r.seen is not None and r.seen.layer == policy.LAYER_SEMANTIC for r in results)


# --- (3) the hard invariants ------------------------------------------------------------------


def test_with_the_engine_no_action_lands_on_another_machine():
    offenders = [
        (r.case_id, r.rendering, r.acted_on) for r in _layer2() if r.verdict == VERDICT_WRONG_DEVICE
    ]
    assert offenders == []


def test_with_the_engine_the_three_real_sentences_are_understood():
    real = [r for r in _layer2() if r.case.origin == ORIGIN_REAL]
    assert len(real) == 3
    for r in real:
        assert r.correct, f"{r.case_id} {r.rendering!r} -> {r.verdict}: {r.problems}"


# --- (4) the counts add up ------------------------------------------------------------------


def test_the_layer2_counts_add_up():
    results = _layer2()
    report = build_stt_report(results, corpus_version=STT_CORPUS_VERSION)
    assert report["total_cases"] == len(CASES) == 106
    assert sum(report["by_verdict"].values()) == len(CASES)
    assert sum(row["total"] for row in report["by_layer"].values()) == len(CASES)
    # Each layer's row is the cases that layer decided, counted here from the results.
    counted: dict[str, list[SttResult]] = {}
    for r in results:
        layer = (r.seen.layer if r.seen else None) or "unrecorded"
        counted.setdefault(layer, []).append(r)
    assert set(report["by_layer"]) == set(counted)
    for layer, rows in counted.items():
        assert report["by_layer"][layer]["total"] == len(rows), layer
        assert report["by_layer"][layer]["correct"] == sum(1 for r in rows if r.correct), layer
        assert sum(report["by_layer"][layer]["by_verdict"].values()) == len(rows), layer
    failures = [r.case_id for r in results if not r.correct]
    classes = failure_classes(results)
    members = [case_id for c in classes for case_id in c["case_ids"]]
    assert sorted(members) == sorted(failures)  # each failure in exactly ONE class
    assert sum(c["count"] for c in classes) == len(failures)
    before, after = _verdicts(_no_engine()), _verdicts(results)
    differs = {case_id for case_id in before if before[case_id] != after[case_id]}
    assert {m["case_id"] for m in compare_runs(_no_engine(), results)["moved"]} == differs


# --- (5) compare_runs and failure_classes on hand-built rows ---------------------------------


def _row(case_id, verdict, *, distortion=None, layer=None, expected="app_open", resolved="none"):
    return {
        "case_id": case_id,
        "verdict": verdict,
        "distortion": distortion,
        "layer": layer,
        "expected_intent": expected,
        "resolved_intent": resolved,
    }


def test_compare_runs_names_every_moved_case_in_both_directions():
    before = [
        _row("a", "correct", layer="rule", resolved="app_open"),
        _row("b", "not_understood", layer="none"),
        _row("c", "not_understood", layer="none"),
        _row("d", "wrong_reading", layer="rule", resolved="x"),
    ]
    after = [
        _row("a", "wrong_reading", layer="semantic", resolved="x"),
        _row("b", "correct", layer="semantic", resolved="app_open"),
        _row("c", "not_understood", layer="semantic"),
        _row("d", "wrong_reading", layer="rule", resolved="x"),
    ]
    assert compare_runs(before, after) == {
        "moved": [
            {
                "case_id": "a",
                "from": "correct",
                "to": "wrong_reading",
                "layer_before": "rule",
                "layer_after": "semantic",
            },
            {
                "case_id": "b",
                "from": "not_understood",
                "to": "correct",
                "layer_before": "none",
                "layer_after": "semantic",
            },
        ],
        "counts": {"correct -> wrong_reading": 1, "not_understood -> correct": 1},
        "made_worse": ["a"],
        "made_better": ["b"],
    }
    assert compare_runs([], []) == {"moved": [], "counts": {}, "made_worse": [], "made_better": []}


def test_failure_classes_order_by_count_then_key_and_count_the_pairs():
    rows = [
        _row("ok", "correct", layer="rule", resolved="app_open"),
        _row("q", "question", layer="none"),  # a question is correct: no class
        _row("n1", "not_understood", distortion="polite", layer="none"),
        _row("n2", "not_understood", distortion="polite", layer="none", expected="display_off"),
        _row("w1", "wrong_reading", distortion="fused", layer="semantic", resolved="x"),
        _row("b1", "wrong_band", distortion="fused", layer="rule", resolved="app_open"),
        _row("r1", "not_understood", layer="none"),  # a real case: distortion None
    ]
    assert failure_classes(rows) == [
        {
            "verdict": "not_understood",
            "distortion": "polite",
            "layer": "none",
            "count": 2,
            "case_ids": ["n1", "n2"],
            "pairs": {"app_open -> none": 1, "display_off -> none": 1},
        },
        {
            "verdict": "not_understood",
            "distortion": None,
            "layer": "none",
            "count": 1,
            "case_ids": ["r1"],
            "pairs": {"app_open -> none": 1},
        },
        {
            "verdict": "wrong_band",
            "distortion": "fused",
            "layer": "rule",
            "count": 1,
            "case_ids": ["b1"],
            "pairs": {"app_open -> app_open": 1},
        },
        {
            "verdict": "wrong_reading",
            "distortion": "fused",
            "layer": "semantic",
            "count": 1,
            "case_ids": ["w1"],
            "pairs": {"app_open -> x": 1},
        },
    ]
    assert failure_classes([]) == []
    assert failure_classes([_row("ok", "correct")]) == []


# --- (6) the same engine twice gives the same verdicts ---------------------------------------


@pytest.mark.skipif(
    not os.environ.get(REPEAT_ENV), reason=f"{REPEAT_ENV} not set (the gate's time)"
)
def test_a_second_production_engine_run_gives_the_same_verdicts():
    first, second = _verdicts(_layer2()), _verdicts(_run_with_engine("repeat"))
    differing = sorted(case_id for case_id in first if first[case_id] != second[case_id])
    _RUNS["repeat.differing"] = differing
    assert differing == []


# --- (8) no local model: a named error, never a lexical number -------------------------------


def test_without_the_local_model_production_engine_refuses_and_writes_nothing(
    tmp_path, monkeypatch
):
    target = tmp_path / "layer2.json"
    monkeypatch.setenv(REPORT_ENV, str(target))
    before = policy.configured_engine()

    def no_model(model_name: str, cache_dir: str | None) -> Any:
        raise OSError("the model is not on this machine")

    with pytest.raises(ProductionEngineUnavailable) as raised:
        _write_evidence(lambda: production_engine(model_factory=no_model))
    assert "deterministic" in str(raised.value) or "could not be loaded" in str(raised.value)
    assert not target.exists()
    assert not target.with_suffix(".md").exists()
    assert policy.configured_engine() is before


# --- (8b) another local model: the measurement's seam, never a production change -----------


def test_production_engine_builds_with_the_model_it_is_given(monkeypatch):
    other = "ibm-granite/granite-embedding-311m-multilingual-r2"
    built_with: list[Any] = []
    loaded: list[str] = []
    real_build = stt_harness.build_embedder

    def recording_build(settings, **kwargs):
        built_with.append(settings)
        return real_build(settings, **kwargs)

    def fake_256(model_name: str, cache_dir: str | None) -> Any:
        loaded.append(model_name)

        class _Model:
            def embed(self, documents, **_kwargs):
                for text in documents:
                    seed = sum(ord(ch) for ch in text) or 1
                    yield [float((seed * (i + 1)) % 7 + 1) for i in range(256)]

        return _Model()

    monkeypatch.setattr(stt_harness, "build_embedder", recording_build)
    before = policy.configured_engine()
    built = production_engine(model_factory=fake_256, model_name=other)
    assert [s.memory_local_embedding_model for s in built_with] == [other]
    assert loaded == [other]
    assert built.report.model_id.startswith("local-" + other)
    assert policy.configured_engine() is before
    # ... and with no model named, the default the gate has always measured.
    built_with.clear()
    production_engine(model_factory=fake_256)
    assert [s.memory_local_embedding_model for s in built_with] == [DEFAULT_LOCAL_MODEL]


def test_the_engine_model_variable_unset_is_the_production_default(monkeypatch):
    monkeypatch.delenv(ENGINE_MODEL_ENV, raising=False)
    assert _engine_model() == DEFAULT_LOCAL_MODEL
    monkeypatch.setenv(ENGINE_MODEL_ENV, "ibm-granite/granite-embedding-311m-multilingual-r2")
    assert _engine_model() == "ibm-granite/granite-embedding-311m-multilingual-r2"


# --- (9) the report ---------------------------------------------------------------------------


def _source_sha() -> str | None:
    """The commit this run's code is; ' (dirty)' when the API tree differs from it."""
    api = Path(__file__).resolve().parents[2]
    try:
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=api, capture_output=True, text=True, timeout=30
        )
        dirty = subprocess.run(
            ["git", "status", "--porcelain", "--", "."],
            cwd=api,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if head.returncode != 0:
        return None
    return head.stdout.strip() + (" (dirty)" if dirty.stdout.strip() else "")


def _write_evidence(build_engine) -> list[Path]:
    """Build the engine FIRST (a refusal writes nothing), then write where the env says."""
    built = build_engine()
    target = os.environ.get(REPORT_ENV)
    report = build_layer2_report(
        _no_engine(),
        _layer2(),
        engine=built,
        repeat_differing=_RUNS.get("repeat.differing"),
        layer2_seconds=_RUNS.get("layer2.seconds"),
        corpus_version=STT_CORPUS_VERSION,
        source_sha=_source_sha(),
    )
    if not target:
        return []
    path = Path(target)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    md = path.with_suffix(".md")
    md.write_text(layer2_markdown(report), encoding="utf-8")
    return [path, md]


def test_the_report_holds_both_runs_the_engine_and_the_classes(tmp_path, monkeypatch):
    target = os.environ.get(REPORT_ENV)
    if not target:
        target = str(tmp_path / "layer2.json")
        monkeypatch.setenv(REPORT_ENV, target)
    written = _write_evidence(_engine)
    assert written == [Path(target), Path(target).with_suffix(".md")]
    report = json.loads(Path(target).read_text(encoding="utf-8"))
    assert set(report) == set(LAYER2_REPORT_KEYS)
    assert report["no_engine"]["total_cases"] == report["production_engine"]["total_cases"] == 106
    assert report["no_engine"]["correct"] == 106 - len(no_engine_suite.KNOWN_GAPS)
    assert report["engine"]["provider"] == "local"
    assert report["engine"]["semantic"] is True
    assert report["engine"]["model_id"] == _engine().report.model_id
    assert report["engine"]["exemplars"] == _engine().exemplars
    assert 0 < len(report["failure_classes"]) <= 10
    assert report["failure_classes"] == failure_classes(_layer2())[:10]
    assert report["failure_classes_total"] == len(failure_classes(_layer2()))
    assert report["moved"] == compare_runs(_no_engine(), _layer2())
    assert "by_layer" in report["no_engine"] and "by_layer" in report["production_engine"]
    assert report["production_engine"]["layer_two_engine"].startswith("local-")
    assert report["source_sha"] is None or report["source_sha"][:40] == _source_sha()[:40]
    text = Path(target).with_suffix(".md").read_text(encoding="utf-8")
    assert f"{report['production_engine']['correct']} / 106" in text
    assert str(report["source_sha"] or "an unknown commit") in text


# --- (7) nothing leaks: runs LAST in this file -----------------------------------------------


def test_zz_after_the_runs_policy_sees_no_engine():
    """The default engine is what it was before this module (None in the unit suite)."""
    _engine()
    _layer2()
    assert combine._default_engine is None
    assert policy.configured_engine() is None
