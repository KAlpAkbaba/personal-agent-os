"""The STT corpus: sentences as the speech-to-text wrote them, through the canonical path.

ADR-0224's second measurement. The cases live in ``tests/voice_corpus/stt_corpus.py``, the
run and the judge in ``tests/voice_corpus/stt_harness.py``; nothing here knows a phrase of
its own. Four things are held: the corpus is what it says it is (three real renderings, the
rest derived by a stated rule and marked so), the judge is the rule of the measurement, the
run meets the numbers the ADR names, and the nightly report carries both corpora's numbers.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from tests.voice_corpus import stt_corpus
from tests.voice_corpus.corpus import CORPUS_VERSION, _strip_diacritics, all_cases
from tests.voice_corpus.harness import build_report, run_case
from tests.voice_corpus.stt_corpus import (
    BAND_HIGH,
    BAND_LOW,
    BAND_MEDIUM,
    DISTORTION_DIACRITICS,
    DISTORTION_FUSED,
    DISTORTION_INVENTED_SUFFIX,
    DISTORTION_POLITE,
    DISTORTIONS,
    IMPERATIVES,
    INVENTED_SUFFIX,
    KNOWN_GAPS,
    ORIGIN_DERIVED,
    ORIGIN_REAL,
    STT_CORPUS_VERSION,
    TARGET_CORRECT_RATE,
    TRIAL_FAMILY_BASES,
    SttCase,
    all_stt_cases,
    canonical_bases,
    rule_bases,
)
from tests.voice_corpus.stt_harness import (
    OFFICE_DEVICE,
    SESSION_DEVICE,
    VERDICT_CORRECT,
    VERDICT_NOT_UNDERSTOOD,
    VERDICT_QUESTION,
    VERDICT_WRONG_ACTION,
    VERDICT_WRONG_BAND,
    VERDICT_WRONG_DEVICE,
    VERDICT_WRONG_READING,
    Observation,
    SttResult,
    build_stt_report,
    judge,
    merge_into_owner_report,
    run_stt_case,
    write_reports,
)

CASES = all_stt_cases()
REAL = [c for c in CASES if c.origin == ORIGIN_REAL]
DERIVED = [c for c in CASES if c.origin == ORIGIN_DERIVED]
_RESULTS: dict[str, SttResult] = {}

REPO = Path(__file__).resolve().parents[4]
COLLECT_SCRIPT = REPO / "scripts" / "voice" / "collect-stt-corpus.ps1"
CORPUS_FILE = Path(stt_corpus.__file__)


def _result_for(case_id: str) -> SttResult:
    if case_id not in _RESULTS:
        case = next(c for c in CASES if c.case_id == case_id)
        _RESULTS[case_id] = run_stt_case(case)
    return _RESULTS[case_id]


def _all_results() -> list[SttResult]:
    return [_result_for(c.case_id) for c in CASES]


# --- the corpus is what it says it is -------------------------------------------------------


def test_the_real_cases_are_the_three_trial_sentences_as_the_stt_wrote_them():
    assert [c.rendering for c in REAL] == [
        "Ofisü bilgisayarında hesap makinesini açın.",
        "Bundan sonra araştırma raporlarını her zaman Türkçe oku",
        "Hesap makinesini aç",
    ]
    office, preference, plain = REAL
    assert (office.intent, office.application, office.device) == ("app_open", "calc", "ofis")
    assert office.bands == (BAND_MEDIUM, BAND_LOW)  # read back or asked, never HIGH
    assert preference.tool is None and not preference.acts
    assert (plain.intent, plain.application, plain.device) == ("app_open", "calc", None)
    assert plain.bands == (BAND_HIGH,)
    for case in REAL:
        assert case.heard_at == "2026-09-30" and case.distortion is None, case.case_id


def test_at_least_sixty_renderings_are_derived_and_every_one_says_so():
    assert len(DERIVED) >= 60
    assert len({c.case_id for c in CASES}) == len(CASES)
    assert len({c.rendering for c in CASES}) == len(CASES)
    for case in DERIVED:
        # A derived sentence is never presented as one production heard.
        assert case.heard_at is None, case.case_id
        assert case.distortion in DISTORTIONS, case.case_id
        assert case.case_id.startswith("stt.derived."), case.case_id
        assert case.base_case_id is not None, case.case_id
    assert {c.distortion for c in DERIVED} == set(DISTORTIONS)


def test_a_derived_case_means_exactly_what_its_canonical_case_says():
    canonical = {c.case_id: c for c in all_cases()}
    for case in DERIVED:
        base = canonical[str(case.base_case_id)]
        assert base.source == "canonical", case.case_id
        assert case.meant == base.utterance, case.case_id
        assert case.intent == base.expected_intent, case.case_id
        assert case.tool == base.expected_tool, case.case_id
        assert case.rendering != base.utterance, case.case_id


def test_the_bases_are_the_ones_the_stated_rule_picks_and_the_trial_family():
    """The corpus cannot be picked to pass: the rule is recomputed from ``corpus.py``."""
    assert list(canonical_bases()) != []
    assert set(canonical_bases()) == set(rule_bases()) | set(TRIAL_FAMILY_BASES)
    assert len(rule_bases()) >= 20  # one per category that has an imperative at all


def _core(word: str) -> tuple[str, str]:
    stem = word.rstrip(".,!?'")
    return stem, word[len(stem) :]


@pytest.mark.parametrize("case", DERIVED, ids=[c.case_id for c in DERIVED])
def test_a_derived_rendering_is_its_one_named_distortion_and_nothing_more(case: SttCase):
    meant, heard = case.meant.split(), case.rendering.split()
    if case.distortion == DISTORTION_POLITE:
        assert heard[:-1] == meant[:-1]
        verb, tail = _core(meant[-1])
        assert heard[-1] == IMPERATIVES[verb.casefold()] + tail
    elif case.distortion == DISTORTION_DIACRITICS:
        assert case.rendering == _strip_diacritics(case.meant)
    elif case.distortion == DISTORTION_FUSED:
        assert len(heard) == len(meant) - 1
        assert "".join(heard) == "".join(meant)
    else:
        assert case.distortion == DISTORTION_INVENTED_SUFFIX
        assert len(heard) == len(meant)
        changed = [i for i, (a, b) in enumerate(zip(meant, heard, strict=True)) if a != b]
        assert len(changed) == 1 and changed[0] != len(meant) - 1  # one word, never the verb
        stem, tail = _core(meant[changed[0]])
        assert heard[changed[0]] == stem + INVENTED_SUFFIX + tail
        assert stem[-1].casefold() not in "aeıioöuü"  # the STT hung it on a consonant


# --- the judge is the rule of the measurement -----------------------------------------------

_OFFICE_CALC = REAL[0]
_PREFERENCE = REAL[1]
_PLAIN_CALC = REAL[2]


def _seen(**fields) -> Observation:
    base = Observation(
        intent="app_open",
        application="calc",
        band=BAND_MEDIUM,
        confidence=0.75,
        question=None,
        acted_on=(OFFICE_DEVICE,),
        tool_status="succeeded",
    )
    return replace(base, **fields)


def test_judge_the_meant_reading_at_medium_on_the_named_machine_is_correct():
    assert judge(_OFFICE_CALC, _seen(), session_device=SESSION_DEVICE).verdict == VERDICT_CORRECT


@pytest.mark.parametrize("band", [BAND_HIGH, BAND_MEDIUM, BAND_LOW])
def test_judge_an_action_on_another_machine_fails_in_every_band(band):
    """The evening of 2026-09-30: the office sentence ran on MAIL, with full confidence."""
    seen = _seen(band=band, confidence=1.0, acted_on=(SESSION_DEVICE,))
    verdict = judge(_OFFICE_CALC, seen, session_device=SESSION_DEVICE)
    assert verdict.verdict == VERDICT_WRONG_DEVICE
    assert not verdict.correct


def test_judge_a_second_machine_beside_the_right_one_is_still_a_wrong_device():
    seen = _seen(acted_on=(OFFICE_DEVICE, SESSION_DEVICE))
    assert judge(_OFFICE_CALC, seen, session_device=SESSION_DEVICE).verdict == VERDICT_WRONG_DEVICE


def test_judge_the_one_question_at_low_is_correct_and_runs_nothing():
    asked = _seen(
        band=BAND_LOW,
        confidence=0.0,
        question="Hangi bilgisayarda: ev mi, ofis mi, iş mi?",
        acted_on=(),
        tool_status="succeeded",
    )
    verdict = judge(_OFFICE_CALC, asked, session_device=SESSION_DEVICE)
    assert verdict.verdict == VERDICT_QUESTION and verdict.correct
    # ... asked AND done is not a question.
    did_it_anyway = replace(asked, acted_on=(OFFICE_DEVICE,))
    assert (
        judge(_OFFICE_CALC, did_it_anyway, session_device=SESSION_DEVICE).verdict
        == VERDICT_WRONG_ACTION
    )
    # ... and a case that allows no question does not pass by being asked one.
    assert judge(_PLAIN_CALC, asked, session_device=SESSION_DEVICE).verdict == VERDICT_WRONG_BAND


def test_judge_a_sentence_left_to_the_model_is_not_understood_even_when_the_guess_lands():
    fell_through = _seen(
        intent="none", application=None, band=BAND_LOW, confidence=0.0, acted_on=(SESSION_DEVICE,)
    )
    verdict = judge(_PLAIN_CALC, fell_through, session_device=SESSION_DEVICE)
    assert verdict.verdict == VERDICT_NOT_UNDERSTOOD and not verdict.correct


def test_judge_the_wrong_intent_or_entity_at_high_is_a_wrong_reading():
    on_session = {"band": BAND_HIGH, "confidence": 1.0, "acted_on": (SESSION_DEVICE,)}
    for seen in (
        _seen(intent="display_off", **on_session),
        _seen(application="notepad", **on_session),
        _seen(canonical_verdict="wrong_route", **on_session),
        _seen(tool_status="failed", **on_session),
    ):
        verdict = judge(_PLAIN_CALC, seen, session_device=SESSION_DEVICE)
        assert verdict.verdict == VERDICT_WRONG_READING, seen
    side_effect = _seen(canonical_verdict="forbidden_side_effect", **on_session)
    assert (
        judge(_PLAIN_CALC, side_effect, session_device=SESSION_DEVICE).verdict
        == VERDICT_WRONG_ACTION
    )


def test_judge_an_invented_word_read_at_high_is_the_wrong_band():
    overconfident = _seen(band=BAND_HIGH, confidence=1.0)
    verdict = judge(_OFFICE_CALC, overconfident, session_device=SESSION_DEVICE)
    assert verdict.verdict == VERDICT_WRONG_BAND


def test_judge_a_preference_is_correct_only_when_nothing_at_all_happened():
    quiet = _seen(
        intent="none",
        application=None,
        band=BAND_LOW,
        confidence=0.0,
        acted_on=(),
        tool_status=None,
    )
    assert judge(_PREFERENCE, quiet, session_device=SESSION_DEVICE).verdict == VERDICT_CORRECT
    for seen, want in (
        (replace(quiet, mode_changed=True), VERDICT_WRONG_ACTION),
        (replace(quiet, guess_ran=True), VERDICT_WRONG_ACTION),
        (replace(quiet, intent="research_open"), VERDICT_WRONG_READING),
        (replace(quiet, acted_on=(SESSION_DEVICE,)), VERDICT_WRONG_ACTION),
    ):
        assert judge(_PREFERENCE, seen, session_device=SESSION_DEVICE).verdict == want, seen


# --- the run ---------------------------------------------------------------------------------


@pytest.mark.parametrize("case", REAL, ids=[c.case_id for c in REAL])
def test_a_real_rendering_is_understood_as_the_owner_meant_it(case: SttCase):
    """The owner's own three sentences: each is its own gate, by name."""
    result = _result_for(case.case_id)
    assert result.correct, (
        f"{case.case_id} {case.rendering!r} -> {result.verdict}: {'; '.join(result.problems)} "
        f"(intent={result.intent} band={result.band} acted_on={result.acted_on})"
    )


def test_the_office_sentence_runs_at_the_office_and_is_read_back():
    result = _result_for(_OFFICE_CALC.case_id)
    assert result.band == BAND_MEDIUM
    assert result.acted_on == (OFFICE_DEVICE,)
    assert result.speech.startswith("Ofis cihazında Hesap Makinesi açıyorum efendim.")


def test_stt_corpus_has_no_wrong_device_action():
    """ADR-0224: 0 wrong-device actions at any confidence."""
    offenders = [
        (r.case_id, r.rendering, r.acted_on)
        for r in _all_results()
        if r.verdict == VERDICT_WRONG_DEVICE
    ]
    assert offenders == []


def test_the_cases_that_are_not_correct_are_exactly_the_known_gaps():
    """The ratchet under the target. A case that starts failing names itself here; a case the
    layers learn to read must be taken out of ``KNOWN_GAPS`` - so the table only shrinks."""
    failing = {r.case_id: r.verdict for r in _all_results() if not r.correct}
    newly_failing = {k: v for k, v in failing.items() if KNOWN_GAPS.get(k) != v}
    now_understood = sorted(set(KNOWN_GAPS) - set(failing))
    assert newly_failing == {}, "a rendering that was understood (or failed otherwise) regressed"
    assert now_understood == [], "understood now: take these out of stt_corpus.KNOWN_GAPS"
    assert not any(case_id.startswith("stt.real.") for case_id in KNOWN_GAPS)
    assert VERDICT_WRONG_DEVICE not in KNOWN_GAPS.values()  # never a tolerated gap


def test_stt_corpus_meets_the_target():
    """ADR-0224: >= 95 % correct - the meant reading at HIGH or MEDIUM, or the one question."""
    report = build_stt_report(_all_results(), corpus_version=STT_CORPUS_VERSION)
    failures = [
        f"{row['case_id']} {row['rendering']!r} -> {row['verdict']}" for row in report["failures"]
    ]
    assert report["correct_rate"] >= TARGET_CORRECT_RATE, (
        f"{report['correct']}/{report['total_cases']} = {report['correct_rate']:.3f} "
        f"< {TARGET_CORRECT_RATE}:\n" + "\n".join(failures)
    )


def test_the_report_counts_add_up_and_name_the_target():
    results = _all_results()
    report = build_stt_report(results, corpus_version=STT_CORPUS_VERSION)
    assert report["suite"] == "SttUtteranceSuite"
    assert report["total_cases"] == len(CASES)
    assert report["correct"] == report["acted"] + report["questions"]
    assert report["correct"] + len(report["failures"]) == len(CASES)
    assert sum(report["by_verdict"].values()) == len(CASES)
    assert report["by_origin"][ORIGIN_REAL]["total"] == 3
    assert report["by_origin"][ORIGIN_DERIVED]["total"] == len(DERIVED)
    assert set(report["by_distortion"]) == set(DISTORTIONS)
    assert report["target_correct_rate"] == TARGET_CORRECT_RATE
    assert report["target_met"] is (
        report["correct_rate"] >= TARGET_CORRECT_RATE and report["wrong_device_actions"] == 0
    )
    assert report["summary"] in ("TARGET_MET", "BELOW_TARGET", "WRONG_DEVICE")
    # "0 wrong-device" is a claim about the cases where a wrong machine can be SEEN: the two
    # enrolled devices. On the canonical world's single fake device it cannot, so the report
    # carries the denominator beside the count (counted here from the corpus, not the run).
    observable = [c for c in CASES if c.origin == ORIGIN_REAL or c.device is not None]
    assert report["wrong_device_observable_cases"] == len(observable) == 11
    assert report["wrong_device_observable_cases"] == sum(
        1 for r in results if r.world == "devices"
    )
    # The trial's own shape, counted apart: another reading, held at HIGH or MEDIUM.
    assert report["confident_wrong_readings"] == sum(
        1
        for r in results
        if r.verdict == VERDICT_WRONG_READING and r.band in (BAND_HIGH, BAND_MEDIUM)
    )


def test_a_control_sentence_is_an_action_and_is_judged_as_one():
    """Red before: "Devam edin." (resume, no tool) was judged like the preference."""
    resume = next(c for c in DERIVED if c.case_id == "stt.derived.c.resume.1.polite")
    assert resume.tool is None and resume.acts
    assert _result_for(resume.case_id).verdict == VERDICT_CORRECT


def _owner_report() -> dict:
    """A real Owner Utterance Suite report, over a few of its own cases."""
    owner_cases = [c for c in all_cases() if c.case_id in ("c.now.1", "d.off.1", "op.app.8")]
    return build_report([run_case(c) for c in owner_cases], corpus_version=CORPUS_VERSION)


def test_the_nightly_report_holds_both_corpora_numbers(tmp_path, monkeypatch):
    """The owner suite writes its report; this suite adds its own numbers to the same file."""
    owner = _owner_report()
    target = tmp_path / "voice-routing.json"
    target.write_text(json.dumps(owner, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("PAGENTOS_VOICE_CORPUS_REPORT", str(target))
    monkeypatch.delenv("PAGENTOS_STT_CORPUS_REPORT", raising=False)
    stt = build_stt_report(_all_results(), corpus_version=STT_CORPUS_VERSION)

    assert write_reports(stt) == [target]

    merged = json.loads(target.read_text(encoding="utf-8"))
    # What the owner suite wrote is untouched (the qualification script reads these) ...
    for key in ("suite", "total_cases", "passed", "failed_routing", "summary", "results"):
        assert merged[key] == owner[key], key
    # ... and the two numbers of ADR-0224 stand side by side.
    both = merged["understanding"]
    assert both["owner_corpus"] == {
        "status": "RUN",
        "generated_at": owner["generated_at"],
        "total_cases": owner["total_cases"],
        "correct": owner["passed"],
        "correct_rate": 1.0,
        "summary": owner["summary"],
    }
    assert both["stt_corpus"]["total_cases"] == len(CASES)
    assert both["stt_corpus"]["correct_rate"] == stt["correct_rate"]
    assert both["stt_corpus"]["wrong_device_actions"] == stt["wrong_device_actions"]
    assert both["stt_corpus"]["wrong_device_observable_cases"] == 11
    assert both["stt_corpus"]["target_met"] is stt["target_met"]
    assert merged["stt_corpus"]["results"] == stt["results"]


def test_without_the_owner_report_the_owner_number_is_not_run_never_invented(tmp_path, monkeypatch):
    target = tmp_path / "reports" / "stt.json"
    monkeypatch.delenv("PAGENTOS_VOICE_CORPUS_REPORT", raising=False)
    monkeypatch.setenv("PAGENTOS_STT_CORPUS_REPORT", str(target))
    stt = build_stt_report(_all_results(), corpus_version=STT_CORPUS_VERSION)
    assert write_reports(stt) == [target]
    written = json.loads(target.read_text(encoding="utf-8"))
    assert written["understanding"]["owner_corpus"] == {"status": "NOT_RUN"}
    # The owner suite's own file is only ever added to, never created in its place.
    absent = tmp_path / "voice-routing.json"
    monkeypatch.setenv("PAGENTOS_VOICE_CORPUS_REPORT", str(absent))
    assert write_reports(stt) == [target] and not absent.exists()
    assert written["understanding"]["stt_corpus"]["correct"] == stt["correct"]
    assert merge_into_owner_report({"suite": "SomethingElse"}, stt)["understanding"][
        "owner_corpus"
    ] == {"status": "NOT_RUN"}


def test_stt_report_is_written_when_asked():
    """The nightly run's evidence: written where the environment says, from real results."""
    stt = build_stt_report(_all_results(), corpus_version=STT_CORPUS_VERSION)
    for path in write_reports(stt):
        assert json.loads(path.read_text(encoding="utf-8"))["understanding"]["stt_corpus"]


# --- the collector: proposals for the corpus, never the corpus -------------------------------

_DUMP_ROWS = [
    # A local session's sentence the router understood nothing of: the one place production
    # keeps the owner's words (``chat_question``), and exactly the sentences worth collecting.
    {
        "kind": "session",
        "session_id": "5d1c1f5e-0000-4000-8000-000000000001",
        "provider": "local-router",
        "updated_at": "2026-10-01T18:02:11Z",
        "last_utterance": {
            "at": "2026-10-01T18:02:11Z",
            "intent": "none",
            "chat_question": "Ekranları kapatın lütfen.",
            "band": "low",
            "confidence": 0.0,
            "candidates": [],
            "understanding": {"layer": "none", "band": "low", "confidence": 0.0},
        },
    },
    # The same sentence heard again in another session: one proposal, not two.
    {
        "kind": "session",
        "session_id": "5d1c1f5e-0000-4000-8000-000000000002",
        "provider": "local-router",
        "updated_at": "2026-10-01T19:40:00Z",
        "last_utterance": {
            "at": "2026-10-01T19:40:00Z",
            "intent": "none",
            "chat_question": "Ekranları kapatın lütfen.",
        },
    },
    # Already a case of the corpus: nothing to propose.
    {
        "kind": "session",
        "session_id": "5d1c1f5e-0000-4000-8000-000000000003",
        "provider": "local-router",
        "updated_at": "2026-10-01T20:00:00Z",
        "last_utterance": {
            "at": "2026-10-01T20:00:00Z",
            "intent": "none",
            "chat_question": "Ofisü bilgisayarında hesap makinesini açın.",
        },
    },
    # A paid session keeps no sentence at all (KVKK): counted, never guessed at.
    {
        "kind": "session",
        "session_id": "5d1c1f5e-0000-4000-8000-000000000004",
        "provider": "openai-realtime",
        "updated_at": "2026-10-01T20:05:00Z",
        "last_utterance": {"at": "2026-10-01T20:05:00Z", "intent": "app_open", "band": "high"},
    },
    {
        "kind": "audit",
        "at": "2026-10-01T18:02:11Z",
        "metadata": {
            "intent": "none",
            "chars": 25,
            "understanding": {"layer": "none", "band": "low", "confidence": 0.0},
        },
    },
    {
        "kind": "audit",
        "at": "2026-10-01T20:05:00Z",
        "metadata": {
            "intent": "app_open",
            "chars": 19,
            "understanding": {"layer": "rule", "band": "high", "confidence": 1.0},
        },
    },
]


def _powershell() -> str | None:
    if sys.platform != "win32":
        return None
    candidate = (
        Path(os.environ.get("SystemRoot", r"C:\Windows"))
        / "System32"
        / "WindowsPowerShell"
        / "v1.0"
        / "powershell.exe"
    )
    return str(candidate) if candidate.is_file() else None


def _collect(*arguments: str) -> subprocess.CompletedProcess[str]:
    shell = _powershell()
    assert shell is not None
    return subprocess.run(  # noqa: S603 - a fixed interpreter and this repository's own script
        [
            shell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(COLLECT_SCRIPT),
            *arguments,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        check=False,
    )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.skipif(_powershell() is None, reason="Windows PowerShell is not on this machine")
def test_the_collector_proposes_new_real_renderings_from_a_sample_dump(tmp_path):
    dump = tmp_path / "audit-dump.jsonl"
    lines = [json.dumps(row, ensure_ascii=False) for row in _DUMP_ROWS]
    dump.write_text("\n".join([*lines, "this line is not json", ""]), encoding="utf-8")
    out = tmp_path / "stt-proposals.json"
    before = _sha256(CORPUS_FILE)

    ran = _collect("-DumpPath", str(dump), "-OutPath", str(out), "-CorpusPath", str(CORPUS_FILE))

    assert ran.returncode == 0, ran.stdout + ran.stderr
    assert _sha256(CORPUS_FILE) == before  # it read the corpus and wrote nothing to it
    proposals = json.loads(out.read_text(encoding="utf-8-sig"))
    assert [p["rendering"] for p in proposals["proposals"]] == ["Ekranları kapatın lütfen."]
    proposal = proposals["proposals"][0]
    assert proposal["origin"] == ORIGIN_REAL
    assert proposal["heard_at"] == "2026-10-01"
    assert proposal["times_heard"] == 2
    assert proposal["resolved_intent"] == "none" and proposal["band"] == "low"
    assert proposal["candidates"] == []  # a list when empty, never {}
    assert proposal["confirms_derived_case"] is None
    # What the owner MEANT is not in any dump: a person fills it in, the tool never guesses.
    assert proposal["status"] == "needs_owner_meaning"
    for slot in ("meant", "intent", "tool", "application", "device"):
        assert proposal[slot] is None, slot
    assert proposals["skipped"] == {
        "already_in_corpus": 1,
        "no_sentence_kept": 1,
        "unreadable_lines": 1,
    }
    assert proposals["audit"]["turns"] == 2
    assert proposals["audit"]["by_band"] == {"low": 1, "high": 1}


@pytest.mark.skipif(_powershell() is None, reason="Windows PowerShell is not on this machine")
def test_the_collector_refuses_to_write_the_corpus_itself(tmp_path):
    dump = tmp_path / "audit-dump.jsonl"
    dump.write_text(json.dumps(_DUMP_ROWS[0], ensure_ascii=False) + "\n", encoding="utf-8")
    scratch = tmp_path / "stt_corpus.py"
    scratch.write_bytes(CORPUS_FILE.read_bytes())
    before = _sha256(scratch)

    for out in (scratch, tmp_path / "proposals.py"):
        ran = _collect("-DumpPath", str(dump), "-OutPath", str(out), "-CorpusPath", str(scratch))
        assert ran.returncode != 0, ran.stdout
    assert _sha256(scratch) == before
    assert not (tmp_path / "proposals.py").exists()


def _heard(index: int, sentence: str, **turn: object) -> dict:
    """One local-mode miss, as the dump holds it."""
    at = f"2026-10-01T21:{index % 60:02d}:00Z"
    return {
        "kind": "session",
        "session_id": f"5d1c1f5e-0000-4000-8000-{index:012d}",
        "provider": "local-router",
        "updated_at": at,
        "last_utterance": {"at": at, "intent": "none", "chat_question": sentence, **turn},
    }


def _proposals_for(tmp_path: Path, rows: list[dict]) -> dict:
    dump = tmp_path / "audit-dump.jsonl"
    lines = [json.dumps(row, ensure_ascii=False) for row in rows]
    dump.write_text("\n".join([*lines, ""]), encoding="utf-8")
    out = tmp_path / "stt-proposals.json"
    ran = _collect("-DumpPath", str(dump), "-OutPath", str(out))
    assert ran.returncode == 0, ran.stdout + ran.stderr
    return json.loads(out.read_text(encoding="utf-8-sig"))


@pytest.mark.skipif(_powershell() is None, reason="Windows PowerShell is not on this machine")
def test_the_collector_reads_the_corpus_as_this_suite_reads_it(tmp_path):
    """The two halves read each other: every rendering of the corpus goes through the
    collector. A REAL one is already there; a DERIVED one that production really heard is the
    best proposal there is - a derived case confirmed - and names the case it confirms."""
    report = _proposals_for(tmp_path, [_heard(i, c.rendering) for i, c in enumerate(CASES)])

    assert report["corpus_seen"] == {
        "real_renderings": len(REAL),
        "derived_renderings": len(DERIVED),
    }
    assert report["skipped"]["already_in_corpus"] == len(REAL)
    assert [(p["rendering"], p["confirms_derived_case"]) for p in report["proposals"]] == [
        (c.rendering, c.case_id) for c in DERIVED
    ]
    for proposal in report["proposals"]:
        assert proposal["origin"] == ORIGIN_REAL
        assert proposal["status"] == "needs_owner_meaning"


@pytest.mark.skipif(_powershell() is None, reason="Windows PowerShell is not on this machine")
def test_the_collector_matches_whole_renderings_never_a_part_of_the_file(tmp_path):
    """Red before: "already in the corpus" was a substring search over the file's text, so a
    sentence inside a longer line, a docstring word or a ``meant`` was silently dropped."""
    assert "Ofisü" in CORPUS_FILE.read_text(encoding="utf-8")
    report = _proposals_for(
        tmp_path,
        [
            _heard(1, "hesap makinesini açın"),  # the tail of the real office rendering
            _heard(2, "Ofisü"),  # a word of the corpus's docstring
            _heard(3, "Hesap makinesini aç"),  # a real rendering, whole: the one skip
            _heard(4, "Hesap makinesini aç."),  # its ``meant``: no rendering of the corpus
            _heard(5, "hesap makinesini aç.", candidates=[{"intent": "app_open"}]),
            _heard(6, "Hesap makinesini aç."),
        ],
    )
    assert report["skipped"]["already_in_corpus"] == 1
    # A rendering is its letters: two that differ by a capital are two, heard twice and once.
    assert [(p["rendering"], p["times_heard"]) for p in report["proposals"]] == [
        ("hesap makinesini açın", 1),
        ("Ofisü", 1),
        ("Hesap makinesini aç.", 2),
        ("hesap makinesini aç.", 1),
    ]
    assert [p["confirms_derived_case"] for p in report["proposals"]] == [None] * 4
    assert [p["candidates"] for p in report["proposals"]] == [[], [], [], [{"intent": "app_open"}]]


@pytest.mark.skipif(_powershell() is None, reason="Windows PowerShell is not on this machine")
def test_the_collector_writes_an_empty_proposal_list_as_a_list(tmp_path):
    report = _proposals_for(tmp_path, [_heard(1, REAL[2].rendering)])
    assert report["proposals"] == []
    assert report["audit"] == {"turns": 0, "by_band": {}, "by_layer": {}}


@pytest.mark.skipif(_powershell() is None, reason="Windows PowerShell is not on this machine")
def test_the_collector_never_writes_over_what_it_reads(tmp_path):
    """Red before: ``-OutPath`` equal to ``-DumpPath`` replaced the dump and exited 0."""
    dump = tmp_path / "audit-dump.jsonl"
    dump.write_text(json.dumps(_DUMP_ROWS[0], ensure_ascii=False) + "\n", encoding="utf-8")
    before = _sha256(dump)
    for spelling in (str(dump), str(dump).upper(), str(tmp_path / "." / "audit-dump.jsonl")):
        ran = _collect("-DumpPath", str(dump), "-OutPath", spelling)
        assert ran.returncode != 0, ran.stdout
        assert "refusing" in ran.stdout + ran.stderr
        assert _sha256(dump) == before, spelling


@pytest.mark.skipif(_powershell() is None, reason="Windows PowerShell is not on this machine")
def test_the_collector_keeps_the_owners_sentences_out_of_the_tracked_tree(tmp_path):
    """Proposals hold raw sentences (KVKK): inside the repository they go under
    ``state/reports`` - which git ignores - or nowhere."""
    dump = tmp_path / "audit-dump.jsonl"
    dump.write_text(json.dumps(_DUMP_ROWS[0], ensure_ascii=False) + "\n", encoding="utf-8")
    tracked = REPO / "docs" / "stt-corpus-proposals-test.json"
    ran = _collect("-DumpPath", str(dump), "-OutPath", str(tracked))
    try:
        assert ran.returncode != 0, ran.stdout
        assert not tracked.exists()
    finally:
        tracked.unlink(missing_ok=True)
    ignored = subprocess.run(  # noqa: S603, S607 - git, on this repository
        ["git", "-C", str(REPO), "check-ignore", "-q", "state/reports/stt-corpus-proposals.json"],
        check=False,
    )
    assert ignored.returncode == 0  # the default path is one git never sees
