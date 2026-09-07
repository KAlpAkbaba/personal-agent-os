"""The Owner Utterance Suite: every corpus case through the canonical path (2026-09-07).

One parametrised test per case, so a regression names the utterance that broke; two
aggregate tests that hold the whole suite to its contract (zero forbidden side effects,
zero wrong routes); and a report writer for the nightly run. The cases live in
``tests/voice_corpus/corpus.py``; nothing here knows a phrase of its own.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from tests.voice_corpus.corpus import CORPUS_VERSION, all_cases
from tests.voice_corpus.harness import CaseResult, build_report, run_case

CASES = all_cases()
_RESULTS: dict[str, CaseResult] = {}


def _result_for(case_id: str) -> CaseResult:
    if case_id not in _RESULTS:
        case = next(c for c in CASES if c.case_id == case_id)
        _RESULTS[case_id] = run_case(case)
    return _RESULTS[case_id]


@pytest.mark.parametrize("case", CASES, ids=[c.case_id for c in CASES])
def test_owner_utterance(case):
    result = _result_for(case.case_id)
    assert result.verdict == "correct", (
        f"{case.case_id} {case.utterance!r} [{case.context}] -> {result.verdict}: "
        f"{'; '.join(result.problems)} (intent={result.resolved_intent} "
        f"class={result.research_class} ref={result.research_reference} "
        f"tool_status={result.tool_status})"
    )


def test_corpus_has_no_forbidden_side_effect_anywhere():
    results = [_result_for(c.case_id) for c in CASES]
    offenders = [r.case_id for r in results if r.verdict == "forbidden_side_effect"]
    assert offenders == []


def test_corpus_report_is_written_when_asked():
    """The nightly suite's evidence (directive §21): counts by category and source, the
    confusion rows, and the HEALTHY / REGRESSION_FOUND summary, from real results."""
    results = [_result_for(c.case_id) for c in CASES]
    report = build_report(results, corpus_version=CORPUS_VERSION)
    assert report["total_cases"] == len(CASES)
    assert (
        report["passed"] + report["clarification"] + report["failed_routing"]
        == len(CASES) - report["forbidden_side_effects"]
    )
    target = os.environ.get("PAGENTOS_VOICE_CORPUS_REPORT")
    if target:
        path = Path(target)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    assert report["summary"] in ("HEALTHY", "REGRESSION_FOUND")
