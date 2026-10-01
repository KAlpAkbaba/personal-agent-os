"""The STT harness: one rendering through the canonical path, and the judge of ADR-0224.

    rendering -> POST /events (the boundary right after transcription)
              -> the ONE router, then the threshold policy (band, question, device)
              -> the tool the owner MEANT through POST /tool-calls - the harness plays the
                 model, which calls it whether or not the layers understood the sentence:
                 that call landing on the wrong machine is exactly what 2026-09-30 was
              -> which machine received a command, and what the turn record says

Two worlds, one path. A rendering derived from a ``corpus.py`` case runs through that
corpus's own ``run_case`` (its context, its contract arguments, its side-effect policy), with
the rendering in place of the sentence. A rendering that NAMES a machine, and the three real
ones, run where a wrong machine can be SEEN: two enrolled devices behind the real
``BrokerDeviceAction``, the session bound to the one the sentence does not name. The single
fake device of ``harness.py`` cannot tell an office launch from a home one.

The judge is a pure function over what was observed, so its rules are tested on their own:

* an action on any machine but the meant one is ``wrong_device`` - in every band;
* the meant intent and entities at HIGH or MEDIUM, executed, is ``correct``;
* a LOW turn that asks its one question and runs nothing is ``question`` (correct);
* a sentence the layers left to the model is ``not_understood`` - even when the harness's
  model then guesses right, because a guess is what the layers exist to replace.

Layer 2 runs as production runs it today: no engine configured (ADR-0224 addendum 3).
"""

from __future__ import annotations

import json
import os
import tempfile
import uuid
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final
from unittest.mock import patch

import pytest

from app.voice.realtime_sessions.models import RealtimeSessionRow
from app.voice.understanding import combine
from tests.unit.test_operator_open_application_fallback import (
    HOME_CAPABILITIES,
    OFFICE_CAPABILITIES,
    _bound_session,
    _say,
    _tool,
    _wired,
)
from tests.voice_corpus.harness import build_harness, run_case
from tests.voice_corpus.stt_corpus import (
    BAND_HIGH,
    BAND_LOW,
    BAND_MEDIUM,
    DISTORTIONS,
    NON_ACTING_INTENTS,
    TARGET_CORRECT_RATE,
    SttCase,
    canonical_bases,
)

#: The machine the owner sits at (production: the home PC) and the one he names from afar.
SESSION_DEVICE: Final = "MAIL"
OFFICE_DEVICE: Final = "GMKADIRAKBABA"

#: The enrolled machines of the device world, as production has them (ADR-0205 inventory).
_MACHINES: Final[dict[str, dict[str, Any]]] = {
    SESSION_DEVICE: {"capabilities": HOME_CAPABILITIES, "aliases": ["ev"]},
    OFFICE_DEVICE: {"capabilities": OFFICE_CAPABILITIES, "aliases": ["ofis", "iş"]},
}
DEVICE_OF_ALIAS: Final[dict[str, str]] = {
    alias: name for name, spec in _MACHINES.items() for alias in spec["aliases"]
}

#: The application as the model heard the owner say it: the tool's ``application`` argument.
_SPOKEN_APPLICATION: Final[dict[str, str]] = {"calc": "Hesap Makinesi", "notepad": "Not Defteri"}

#: What the model did with the language preference on 2026-09-30: it must stay refused.
_PREFERENCE_GUESS: Final[tuple[str, dict[str, Any]]] = ("research.answer_mode", {"level": "detail"})

VERDICT_CORRECT: Final = "correct"
VERDICT_QUESTION: Final = "question"
VERDICT_NOT_UNDERSTOOD: Final = "not_understood"
VERDICT_WRONG_READING: Final = "wrong_reading"
VERDICT_WRONG_BAND: Final = "wrong_band"
VERDICT_WRONG_ACTION: Final = "wrong_action"
VERDICT_WRONG_DEVICE: Final = "wrong_device"
VERDICT_ERROR: Final = "error"
_CORRECT: Final[frozenset[str]] = frozenset({VERDICT_CORRECT, VERDICT_QUESTION})

OWNER_REPORT_ENV: Final = "PAGENTOS_VOICE_CORPUS_REPORT"
STT_REPORT_ENV: Final = "PAGENTOS_STT_CORPUS_REPORT"
_OWNER_SUITE: Final = "OwnerUtteranceSuite"
STT_SUITE: Final = "SttUtteranceSuite"


@dataclass(frozen=True, slots=True)
class Observation:
    """What one rendering did, read off the relay's answers and the device port."""

    intent: str | None
    application: str | None
    band: str | None
    confidence: float | None
    #: The question of a LOW turn, from the turn record; None when nothing was asked.
    question: str | None
    #: Every machine that received a command, in order, each once.
    acted_on: tuple[str, ...]
    tool_status: str | None
    #: ``run_case``'s own verdict for a canonical-world case; None in the device world.
    canonical_verdict: str | None = None
    #: A standing answer mode / policy the turn recorded (a preference must record none).
    mode_changed: bool = False
    #: The model's own guess, dispatched by the harness, was executed.
    guess_ran: bool = False
    speech: str = ""
    problems: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Verdict:
    verdict: str
    problems: tuple[str, ...] = ()

    @property
    def correct(self) -> bool:
        return self.verdict in _CORRECT


def judge(case: SttCase, seen: Observation, *, session_device: str) -> Verdict:
    """The rule of the measurement (ADR-0224), over what was observed."""
    intended = DEVICE_OF_ALIAS[case.device] if case.device else session_device
    strangers = [machine for machine in seen.acted_on if machine != intended]
    if strangers:
        return Verdict(
            VERDICT_WRONG_DEVICE,
            (f"ran on {strangers} - the owner meant {intended} (band {seen.band})",),
        )
    if seen.canonical_verdict == VERDICT_ERROR:
        return Verdict(VERDICT_ERROR, seen.problems)
    if not case.acts:
        if seen.acted_on or seen.mode_changed or seen.guess_ran:
            return Verdict(VERDICT_WRONG_ACTION, ("a preference changed or ran something",))
        if seen.intent not in NON_ACTING_INTENTS:
            return Verdict(VERDICT_WRONG_READING, (f"read as {seen.intent!r}",))
        return Verdict(VERDICT_CORRECT)
    if seen.band not in (BAND_HIGH, BAND_MEDIUM):
        if not seen.question:
            return Verdict(
                VERDICT_NOT_UNDERSTOOD,
                (f"left to the model: intent {seen.intent!r}, band {seen.band}, no question",),
            )
        if seen.acted_on:
            return Verdict(VERDICT_WRONG_ACTION, ("asked a question and acted anyway",))
        if seen.question.count("?") != 1:
            return Verdict(VERDICT_WRONG_READING, (f"not ONE question: {seen.question!r}",))
        if BAND_LOW not in case.bands:
            return Verdict(VERDICT_WRONG_BAND, (f"asked; expected {case.bands}",))
        return Verdict(VERDICT_QUESTION)
    if seen.intent != case.intent:
        return Verdict(VERDICT_WRONG_READING, (f"intent {seen.intent!r} != {case.intent!r}",))
    if case.application is not None and seen.application != case.application:
        return Verdict(
            VERDICT_WRONG_READING,
            (f"application {seen.application!r} != {case.application!r}",),
        )
    if seen.canonical_verdict == "forbidden_side_effect":
        return Verdict(VERDICT_WRONG_ACTION, seen.problems)
    if seen.canonical_verdict is None:
        if seen.tool_status != "succeeded" or seen.acted_on != (intended,):
            return Verdict(
                VERDICT_WRONG_READING,
                (f"not done on {intended}: {seen.tool_status}, ran on {list(seen.acted_on)}",),
            )
    elif seen.canonical_verdict != VERDICT_CORRECT:
        return Verdict(VERDICT_WRONG_READING, seen.problems or (seen.canonical_verdict,))
    if seen.band not in case.bands:
        return Verdict(VERDICT_WRONG_BAND, (f"band {seen.band}; expected {case.bands}",))
    return Verdict(VERDICT_CORRECT)


# ------------------------------------------------------------------- the two worlds


def session_device_for(case: SttCase) -> str:
    """The session sits on the machine the sentence does NOT name, so a silent default to the
    session's own device is always a different machine from the meant one."""
    if case.device and DEVICE_OF_ALIAS[case.device] == SESSION_DEVICE:
        return OFFICE_DEVICE
    return SESSION_DEVICE


def _uses_device_world(case: SttCase) -> bool:
    return case.base_case_id is None or case.device is not None


def _turn_record(factory: Any, sid: str) -> dict[str, Any]:
    with factory() as db:
        row = db.get(RealtimeSessionRow, uuid.UUID(sid))
        return dict((row.context_json or {}).get("last_utterance") or {})


def _question_of(record: dict[str, Any]) -> str | None:
    return (record.get("understanding") or {}).get("question") or None


def _observe_in_device_world(case: SttCase) -> Observation:
    patcher = pytest.MonkeyPatch()
    with tempfile.TemporaryDirectory(prefix="stt-corpus-", ignore_cleanup_errors=True) as tmp:
        world = _wired(patcher, Path(tmp), _MACHINES)
        try:
            names = {device_id: name for name, device_id in world.ids.items()}
            sid = _bound_session(world, session_device_for(case))
            resolved = _say(world.client, sid, case.rendering)["resolved_intents"][0]
            tool_status: str | None = None
            speech = ""
            guess_ran = False
            if case.tool is not None:
                spoken = _SPOKEN_APPLICATION[str(case.application)]
                call = _tool(world.client, sid, case.tool, {"application": spoken})
                tool_status = call["status"]
                speech = str((call.get("result") or call.get("error") or {}).get("speech") or "")
            else:
                guess_tool, guess_arguments = _PREFERENCE_GUESS
                guess = _tool(world.client, sid, guess_tool, dict(guess_arguments))
                guess_ran = guess["status"] == "succeeded"
            record = _turn_record(world.factory, sid)
            acted_on = tuple(dict.fromkeys(names[c["device_id"]] for c in world.commands.calls))
            return Observation(
                intent=resolved.get("intent"),
                application=resolved.get("application"),
                band=resolved.get("band"),
                confidence=resolved.get("confidence"),
                question=_question_of(record),
                acted_on=acted_on,
                tool_status=tool_status,
                mode_changed=bool(record.get("answer_level") or record.get("policy_changes")),
                guess_ran=guess_ran,
                speech=speech,
            )
        finally:
            world.client.close()
            world.factory.kw["bind"].dispose()
            patcher.undo()


def _observe_in_canonical_world(case: SttCase) -> Observation:
    base = canonical_bases()[str(case.base_case_id)]
    rendered = replace(base, case_id=case.case_id, utterance=case.rendering, source="stt_derived")
    harness = build_harness()
    heard: dict[str, Any] = {}
    say = harness.say

    def recording_say(sid: str, text: str, **kwargs: Any) -> dict:
        response = say(sid, text, **kwargs)
        if text == case.rendering:
            heard["sid"] = sid
            heard["resolved"] = response["resolved_intents"][0]
        return response

    harness.say = recording_say  # type: ignore[method-assign]
    result = run_case(rendered, harness=harness)
    resolved = heard.get("resolved") or {}
    record = _turn_record(harness.factory, heard["sid"]) if "sid" in heard else {}
    return Observation(
        intent=result.resolved_intent,
        application=resolved.get("application"),
        band=resolved.get("band"),
        confidence=resolved.get("confidence"),
        question=_question_of(record),
        acted_on=(SESSION_DEVICE,) if harness.device.calls else (),
        tool_status=result.tool_status,
        canonical_verdict=result.verdict,
        speech=result.speech,
        problems=tuple(result.problems),
    )


@dataclass(frozen=True, slots=True)
class SttResult:
    case: SttCase
    seen: Observation | None
    verdict: str
    problems: tuple[str, ...]
    world: str

    @property
    def correct(self) -> bool:
        return self.verdict in _CORRECT

    @property
    def case_id(self) -> str:
        return self.case.case_id

    @property
    def rendering(self) -> str:
        return self.case.rendering

    @property
    def intent(self) -> str | None:
        return self.seen.intent if self.seen else None

    @property
    def band(self) -> str | None:
        return self.seen.band if self.seen else None

    @property
    def acted_on(self) -> tuple[str, ...]:
        return self.seen.acted_on if self.seen else ()

    @property
    def speech(self) -> str:
        return self.seen.speech if self.seen else ""

    def as_dict(self) -> dict[str, Any]:
        seen = self.seen
        return {
            "case_id": self.case.case_id,
            "origin": self.case.origin,
            "distortion": self.case.distortion,
            "base_case_id": self.case.base_case_id,
            "rendering": self.case.rendering,
            "meant": self.case.meant,
            "expected_intent": self.case.intent,
            "expected_application": self.case.application,
            "expected_device": self.case.device,
            "expected_bands": list(self.case.bands),
            "world": self.world,
            "resolved_intent": seen.intent if seen else None,
            "resolved_application": seen.application if seen else None,
            "band": seen.band if seen else None,
            "confidence": seen.confidence if seen else None,
            "question": seen.question if seen else None,
            "acted_on": list(seen.acted_on) if seen else [],
            "tool_status": seen.tool_status if seen else None,
            "verdict": self.verdict,
            "problems": list(self.problems),
        }


def run_stt_case(case: SttCase) -> SttResult:
    """One rendering, with layer 2 as production has it today: no engine configured."""
    device_world = _uses_device_world(case)
    world = "devices" if device_world else "canonical"
    try:
        with patch.object(combine, "_default_engine", None):
            seen = (
                _observe_in_device_world(case)
                if device_world
                else _observe_in_canonical_world(case)
            )
    except Exception as exc:  # noqa: BLE001 - a crashed case is a finding, not a crash of the suite
        return SttResult(case, None, VERDICT_ERROR, (f"{type(exc).__name__}: {exc}"[:300],), world)
    verdict = judge(
        case, seen, session_device=session_device_for(case) if device_world else SESSION_DEVICE
    )
    return SttResult(case, seen, verdict.verdict, verdict.problems, world)


# ------------------------------------------------------------------------ the report


def _tally(results: list[SttResult]) -> dict[str, int]:
    correct = sum(1 for r in results if r.correct)
    return {"total": len(results), "correct": correct}


def build_stt_report(results: list[SttResult], *, corpus_version: int) -> dict[str, Any]:
    by_verdict: dict[str, int] = {}
    by_band: dict[str, int] = {}
    for r in results:
        by_verdict[r.verdict] = by_verdict.get(r.verdict, 0) + 1
        band = r.band or "none"
        by_band[band] = by_band.get(band, 0) + 1
    total = len(results)
    correct = sum(1 for r in results if r.correct)
    wrong_device = by_verdict.get(VERDICT_WRONG_DEVICE, 0)
    rate = round(correct / total, 4) if total else 0.0
    target_met = rate >= TARGET_CORRECT_RATE and wrong_device == 0
    if wrong_device:
        summary = "WRONG_DEVICE"
    else:
        summary = "TARGET_MET" if target_met else "BELOW_TARGET"
    return {
        "suite": STT_SUITE,
        "corpus_version": corpus_version,
        "generated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "total_cases": total,
        "correct": correct,
        # The meant reading DONE at HIGH or MEDIUM, and the one question at LOW, apart.
        "acted": by_verdict.get(VERDICT_CORRECT, 0),
        "questions": by_verdict.get(VERDICT_QUESTION, 0),
        "not_understood": by_verdict.get(VERDICT_NOT_UNDERSTOOD, 0),
        "wrong_device_actions": wrong_device,
        # A reading that is NOT the meant one, held at HIGH or MEDIUM: the shape of
        # 2026-09-30 - the wrong thing, with full confidence.
        "confident_wrong_readings": sum(
            1
            for r in results
            if r.verdict == VERDICT_WRONG_READING and r.band in (BAND_HIGH, BAND_MEDIUM)
        ),
        "correct_rate": rate,
        "acted_rate": round(by_verdict.get(VERDICT_CORRECT, 0) / total, 4) if total else 0.0,
        "target_correct_rate": TARGET_CORRECT_RATE,
        "target_met": target_met,
        "summary": summary,
        "layer_two_engine": "none (production as of ADR-0224 addendum 3)",
        "by_verdict": by_verdict,
        "by_band": by_band,
        "by_origin": {
            origin: _tally([r for r in results if r.case.origin == origin])
            for origin in sorted({r.case.origin for r in results})
        },
        "by_distortion": {
            distortion: _tally([r for r in results if r.case.distortion == distortion])
            for distortion in DISTORTIONS
        },
        "failures": [r.as_dict() for r in results if not r.correct],
        "results": [r.as_dict() for r in results],
    }


def _owner_numbers(owner: dict[str, Any] | None) -> dict[str, Any]:
    """The Owner Utterance Suite's number, read from its own report - or NOT_RUN, never a
    number this suite made up for it."""
    if not owner or owner.get("suite") != _OWNER_SUITE or not owner.get("total_cases"):
        return {"status": "NOT_RUN"}
    return {
        "status": "RUN",
        "generated_at": owner.get("generated_at"),
        "total_cases": owner["total_cases"],
        "correct": owner["passed"],
        "correct_rate": round(owner["passed"] / owner["total_cases"], 4),
        "summary": owner.get("summary"),
    }


def merge_into_owner_report(owner: dict[str, Any] | None, stt: dict[str, Any]) -> dict[str, Any]:
    """The nightly report: what the owner suite wrote, untouched, plus ``understanding`` - the
    two numbers of ADR-0224 side by side - and the STT run in full under ``stt_corpus``."""
    numbers = _owner_numbers(owner)
    merged = dict(owner) if owner and numbers["status"] == "RUN" else {}
    merged["understanding"] = {
        "owner_corpus": numbers,
        "stt_corpus": {
            key: stt[key]
            for key in (
                "generated_at",
                "total_cases",
                "correct",
                "acted",
                "questions",
                "not_understood",
                "wrong_device_actions",
                "confident_wrong_readings",
                "correct_rate",
                "acted_rate",
                "target_correct_rate",
                "target_met",
                "summary",
            )
        },
    }
    merged["stt_corpus"] = stt
    return merged


def _read_report(path: Path) -> dict[str, Any] | None:
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return loaded if isinstance(loaded, dict) else None


def write_reports(stt: dict[str, Any]) -> list[Path]:
    """Write where the environment asks. ``PAGENTOS_VOICE_CORPUS_REPORT`` is the owner suite's
    file: its report is read and written back with this suite's numbers added (run this file
    AFTER ``test_owner_utterance_corpus.py``). ``PAGENTOS_STT_CORPUS_REPORT`` is a file of
    this suite's own, carrying the owner's number only when the owner's report is there."""
    owner_target = os.environ.get(OWNER_REPORT_ENV)
    own_target = os.environ.get(STT_REPORT_ENV)
    owner = _read_report(Path(owner_target)) if owner_target else None
    written: list[Path] = []
    numbers = _owner_numbers(owner)
    # The owner suite's file is only ever ADDED to: when it is not there, it is not created,
    # so the nightly script's own "no report was written" stays true.
    targets = [owner_target if numbers["status"] == "RUN" else None, own_target]
    for target in targets:
        if not target:
            continue
        path = Path(target)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(merge_into_owner_report(owner, stt), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        written.append(path)
    return written


__all__ = [
    "DEVICE_OF_ALIAS",
    "OFFICE_DEVICE",
    "SESSION_DEVICE",
    "VERDICT_CORRECT",
    "VERDICT_ERROR",
    "VERDICT_NOT_UNDERSTOOD",
    "VERDICT_QUESTION",
    "VERDICT_WRONG_ACTION",
    "VERDICT_WRONG_BAND",
    "VERDICT_WRONG_DEVICE",
    "VERDICT_WRONG_READING",
    "Observation",
    "SttResult",
    "Verdict",
    "build_stt_report",
    "judge",
    "merge_into_owner_report",
    "run_stt_case",
    "session_device_for",
    "write_reports",
]
