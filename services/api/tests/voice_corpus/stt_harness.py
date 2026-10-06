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

Layer 2 runs with no engine configured unless ``run_stt_case(engine=...)`` is given one (the
measurement of ADR-0224 addendum 4). ``production_engine()`` builds the engine production has
since ADR-0245 - the local embedder, the shipped exemplars - so the corpus can be measured
with it too, and ``compare_runs`` / ``failure_classes`` read the two runs side by side.
"""

from __future__ import annotations

import json
import os
import tempfile
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from typing import Any, Final
from unittest.mock import patch

import pytest

from app.config import Settings
from app.memory.embedding import Embedder
from app.memory.providers import (
    DEFAULT_LOCAL_MODEL,
    PROVIDER_LOCAL,
    EmbedderReport,
    ModelFactory,
    _fastembed_factory,
    build_embedder,
)
from app.voice.realtime_sessions.models import RealtimeSessionRow
from app.voice.understanding import combine
from app.voice.understanding.semantic import SemanticEngine
from app.voice.understanding.startup import configure_understanding
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
    #: The layer that decided (``policy.LAYER_*``), from the turn record; None when it has none.
    layer: str | None = None
    #: The decision's top candidates as ``(intent, confidence)``, from the turn record.
    candidates: tuple[tuple[str, float], ...] = ()


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


def _layer_of(record: dict[str, Any]) -> str | None:
    return (record.get("understanding") or {}).get("layer") or None


def _candidates_of(record: dict[str, Any]) -> tuple[tuple[str, float], ...]:
    return tuple((str(pair[0]), float(pair[1])) for pair in record.get("candidates") or ())


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
                layer=_layer_of(record),
                candidates=_candidates_of(record),
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
        layer=_layer_of(record),
        candidates=_candidates_of(record),
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
            "layer": seen.layer if seen else None,
            "candidates": [list(pair) for pair in seen.candidates] if seen else [],
            "confidence": seen.confidence if seen else None,
            "question": seen.question if seen else None,
            "acted_on": list(seen.acted_on) if seen else [],
            "tool_status": seen.tool_status if seen else None,
            "verdict": self.verdict,
            "problems": list(self.problems),
        }


def run_stt_case(case: SttCase, *, engine: SemanticEngine | None = None) -> SttResult:
    """One rendering. ``engine`` None: no layer-2 engine (ADR-0224 addendum 4's run); a built
    engine (``production_engine()``) is put where the policy reads it for this case only."""
    device_world = _uses_device_world(case)
    world = "devices" if device_world else "canonical"
    try:
        with patch.object(combine, "_default_engine", engine):
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


# --------------------------------------------------------- the engine production builds


class ProductionEngineUnavailable(RuntimeError):
    """The local semantic engine could not be built on this machine. Never answered with the
    deterministic embedder: a lexical hash would give a number that means nothing."""


@dataclass(slots=True)
class _CountingEmbedder:
    """The real embedder, unchanged, with every text it is asked for recorded - so a run can
    show the engine was really consulted. Vectors pass through untouched."""

    inner: Embedder
    asked: list[str] = field(default_factory=list)

    @property
    def model_id(self) -> str:
        return self.inner.model_id

    @property
    def model_version(self) -> str:
        return self.inner.model_version

    @property
    def dim(self) -> int:
        return self.inner.dim

    def embed(self, text: str) -> list[float]:
        self.asked.append(text)
        return self.inner.embed(text)


@dataclass(frozen=True, slots=True)
class BuiltEngine:
    engine: SemanticEngine
    report: EmbedderReport
    exemplars: int
    build_ms: float | None
    #: Every text the engine's embedder was asked for AFTER the index was built.
    asked: list[str]


def production_engine(
    *,
    model_factory: ModelFactory | None = None,
    model_name: str = DEFAULT_LOCAL_MODEL,
    threads: int | None = None,
) -> BuiltEngine:
    """The engine ``create_app`` configures (ADR-0245): ``build_embedder`` with the provider
    'local' and the default local model, then ``configure_understanding`` with the SHIPPED
    exemplars, built inline. The default engine the process had before is restored, so the
    engine reaches a case only through ``run_stt_case(engine=...)``.

    ``model_factory`` is the model loader's seam (a test makes it raise); a refusal raises
    ``ProductionEngineUnavailable`` with the reason. ``model_name`` / ``threads`` measure
    another local model with the same engine (memory-embedding-granite-measure); the
    default is production's model.
    """
    settings = Settings(
        memory_embedding_provider=PROVIDER_LOCAL,
        memory_local_embedding_model=model_name,
        understanding_semantic_enabled=True,
    )
    factory = model_factory
    if factory is None and threads is not None:
        factory = partial(_fastembed_factory, threads=threads)
    embedder, report = build_embedder(settings, model_factory=factory)
    if report.active != PROVIDER_LOCAL or not report.semantic:
        raise ProductionEngineUnavailable(
            f"the local embedder could not be built (the deterministic one would serve): "
            f"{report.fallback_reason}"
        )
    counting = _CountingEmbedder(embedder)
    previous = combine._default_engine
    try:
        state = configure_understanding(
            settings, counting, report=report, spawn=lambda build: build()
        )
        if not state.configured:
            raise ProductionEngineUnavailable(f"layer 2 was not configured: {state.reason}")
        engine = combine.default_engine()
    finally:
        combine.reset_default_engine()
        if previous is not None:
            combine._default_engine = previous
    counting.asked.clear()  # the exemplars' own embeddings are the build, not a case
    return BuiltEngine(engine, report, state.exemplars, state.build_ms, counting.asked)


# ------------------------------------------------------------------------ the report


def _tally(results: list[SttResult]) -> dict[str, int]:
    correct = sum(1 for r in results if r.correct)
    return {"total": len(results), "correct": correct}


#: ``by_layer``'s key for a case whose turn record names no layer (an error, a lost session).
LAYER_UNRECORDED: Final = "unrecorded"
_NO_ENGINE_TEXT: Final = "none (production as of ADR-0224 addendum 3)"


def _layer_key(result: SttResult) -> str:
    return (result.seen.layer if result.seen else None) or LAYER_UNRECORDED


def build_stt_report(
    results: list[SttResult], *, corpus_version: int, layer_two_engine: str | None = None
) -> dict[str, Any]:
    by_verdict: dict[str, int] = {}
    by_band: dict[str, int] = {}
    by_layer: dict[str, dict[str, Any]] = {}
    for r in results:
        by_verdict[r.verdict] = by_verdict.get(r.verdict, 0) + 1
        band = r.band or "none"
        by_band[band] = by_band.get(band, 0) + 1
        row = by_layer.setdefault(_layer_key(r), {"total": 0, "correct": 0, "by_verdict": {}})
        row["total"] += 1
        row["correct"] += 1 if r.correct else 0
        row["by_verdict"][r.verdict] = row["by_verdict"].get(r.verdict, 0) + 1
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
        # The denominator of that count: the cases run over the two enrolled devices, where a
        # wrong machine can be SEEN. The rest run on the canonical world's single fake device,
        # so "0 wrong-device" says nothing about them.
        "wrong_device_observable_cases": sum(1 for r in results if r.world == "devices"),
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
        "layer_two_engine": layer_two_engine or _NO_ENGINE_TEXT,
        "by_verdict": by_verdict,
        "by_band": by_band,
        "by_layer": {layer: by_layer[layer] for layer in sorted(by_layer)},
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


# ------------------------------------------------------- two runs, side by side (layer 2)

_Row = Mapping[str, Any]


def _rows(results: Sequence[SttResult | _Row]) -> list[_Row]:
    return [r.as_dict() if isinstance(r, SttResult) else r for r in results]


def compare_runs(
    before: Sequence[SttResult | _Row], after: Sequence[SttResult | _Row]
) -> dict[str, Any]:
    """Every case whose verdict differs between two runs, in ``before``'s order, with the
    layer that decided each time; the counts by ``"from -> to"``; and the cases a run made
    worse (correct before, not after) and better, apart."""
    later = {row["case_id"]: row for row in _rows(after)}
    moved: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    worse: list[str] = []
    better: list[str] = []
    for row in _rows(before):
        other = later.get(row["case_id"])
        if other is None or other["verdict"] == row["verdict"]:
            continue
        moved.append(
            {
                "case_id": row["case_id"],
                "from": row["verdict"],
                "to": other["verdict"],
                "layer_before": row.get("layer"),
                "layer_after": other.get("layer"),
            }
        )
        key = f"{row['verdict']} -> {other['verdict']}"
        counts[key] = counts.get(key, 0) + 1
        was, now = row["verdict"] in _CORRECT, other["verdict"] in _CORRECT
        if was and not now:
            worse.append(row["case_id"])
        elif now and not was:
            better.append(row["case_id"])
    return {
        "moved": moved,
        "counts": {key: counts[key] for key in sorted(counts)},
        "made_worse": worse,
        "made_better": better,
    }


def failure_classes(results: Sequence[SttResult | _Row]) -> list[dict[str, Any]]:
    """The failing cases grouped by (verdict, distortion, layer): count, case ids and the
    ``expected -> resolved`` intent pairs inside each. Largest first, then by key."""
    groups: dict[tuple[str, str | None, str | None], list[_Row]] = {}
    for row in _rows(results):
        if row["verdict"] in _CORRECT:
            continue
        key = (row["verdict"], row.get("distortion"), row.get("layer"))
        groups.setdefault(key, []).append(row)
    out: list[dict[str, Any]] = []
    for (verdict, distortion, layer), rows in groups.items():
        pairs: dict[str, int] = {}
        for row in rows:
            pair = f"{row.get('expected_intent')} -> {row.get('resolved_intent')}"
            pairs[pair] = pairs.get(pair, 0) + 1
        out.append(
            {
                "verdict": verdict,
                "distortion": distortion,
                "layer": layer,
                "count": len(rows),
                "case_ids": [row["case_id"] for row in rows],
                "pairs": {pair: pairs[pair] for pair in sorted(pairs)},
            }
        )
    out.sort(key=lambda c: (-c["count"], c["verdict"], c["distortion"] or "", c["layer"] or ""))
    return out


#: The keys of the layer-2 measurement's report - and no others.
LAYER2_REPORT_KEYS: Final[tuple[str, ...]] = (
    "suite",
    "generated_at",
    "source_sha",
    "no_engine",
    "production_engine",
    "moved",
    "failure_classes",
    "failure_classes_total",
    "engine",
    "repeat_run",
)
LAYER2_SUITE: Final = "SttLayer2Remeasure"
_TOP_CLASSES: Final = 10


def build_layer2_report(
    no_engine: list[SttResult],
    with_engine: list[SttResult],
    *,
    engine: BuiltEngine,
    corpus_version: int,
    repeat_differing: list[str] | None = None,
    layer2_seconds: float | None = None,
    source_sha: str | None = None,
) -> dict[str, Any]:
    """The two runs of the corpus, the cases that moved, the largest failure classes of the
    production-engine run, and how the engine was built. ``source_sha``: the commit the run's
    code is (the caller reads it; None when it cannot)."""
    classes = failure_classes(with_engine)
    return {
        "suite": LAYER2_SUITE,
        "generated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "source_sha": source_sha,
        "no_engine": build_stt_report(no_engine, corpus_version=corpus_version),
        "production_engine": build_stt_report(
            with_engine, corpus_version=corpus_version, layer_two_engine=engine.report.model_id
        ),
        "moved": compare_runs(no_engine, with_engine),
        "failure_classes": classes[:_TOP_CLASSES],
        "failure_classes_total": len(classes),
        "engine": {
            "provider": engine.report.active,
            "semantic": engine.report.semantic,
            "model_id": engine.report.model_id,
            "exemplars": engine.exemplars,
            "build_ms": engine.build_ms,
            "corpus_seconds": layer2_seconds,
        },
        # The determinism check's second run: NOT_RUN unless it was made in this process.
        "repeat_run": (
            {"status": "NOT_RUN"}
            if repeat_differing is None
            else {"status": "RUN", "differing_case_ids": list(repeat_differing)}
        ),
    }


def _pct(report: Mapping[str, Any]) -> str:
    return f"{report['correct']} / {report['total_cases']} = {report['correct_rate'] * 100:.1f} %"


def _top(row: Mapping[str, Any]) -> str:
    candidates = row.get("candidates") or []
    return f"{candidates[0][0]} {candidates[0][1]}" if candidates else "-"


def layer2_markdown(report: Mapping[str, Any]) -> str:
    """The report in prose: the headline first, whichever way it falls."""
    before, after = report["no_engine"], report["production_engine"]
    moved = report["moved"]
    rows = {row["case_id"]: row for row in after["results"]}
    target = after["target_correct_rate"] * 100
    lines = [
        "# STT corpus, layer 2 as production configures it",
        "",
        f"Generated {report['generated_at']} at {report['source_sha'] or 'an unknown commit'}. "
        f"Engine: {report['engine']['model_id']} "
        f"({report['engine']['provider']}, {report['engine']['exemplars']} exemplars, "
        f"index built in {report['engine']['build_ms']} ms).",
        "",
        f"- without the engine: **{_pct(before)}**",
        f"- with the engine:    **{_pct(after)}**",
        f"- target {target:.0f} %: **{'met' if after['target_met'] else 'NOT met'}**",
        f"- layer 2 made {len(moved['made_worse'])} case(s) worse and "
        f"{len(moved['made_better'])} better",
        f"- wrong-device actions with the engine: {after['wrong_device_actions']} over "
        f"{after['wrong_device_observable_cases']} observable cases",
        f"- confident wrong readings: {before['confident_wrong_readings']} -> "
        f"{after['confident_wrong_readings']}",
        "",
        "## Made worse",
        "",
        *([f"- {c}" for c in moved["made_worse"]] or ["- none"]),
        "",
        "## Made better",
        "",
        *([f"- {c}" for c in moved["made_better"]] or ["- none"]),
        "",
        "## Every moved case",
        "",
        "| case | from | to | layer before | layer after | meant | top candidate after |",
        "|---|---|---|---|---|---|---|",
        *(
            f"| {m['case_id']} | {m['from']} | {m['to']} | {m['layer_before']} | "
            f"{m['layer_after']} | {rows[m['case_id']]['expected_intent']} | "
            f"{_top(rows[m['case_id']])} |"
            for m in moved["moved"]
        ),
        "",
        "## By layer (with the engine / without)",
        "",
        "| layer | with: correct / total | without: correct / total |",
        "|---|---|---|",
    ]
    for layer in sorted(set(after["by_layer"]) | set(before["by_layer"])):
        a = after["by_layer"].get(layer, {"correct": 0, "total": 0})
        b = before["by_layer"].get(layer, {"correct": 0, "total": 0})
        lines.append(f"| {layer} | {a['correct']} / {a['total']} | {b['correct']} / {b['total']} |")
    lines += [
        "",
        "## By distortion (with the engine / without)",
        "",
        "| distortion | with | without |",
        "|---|---|---|",
    ]
    for distortion, a in after["by_distortion"].items():
        b = before["by_distortion"][distortion]
        lines.append(
            f"| {distortion} | {a['correct']} / {a['total']} | {b['correct']} / {b['total']} |"
        )
    lines += [
        "",
        f"## The {len(report['failure_classes'])} largest failure classes "
        f"(of {report['failure_classes_total']}), with the engine",
        "",
        "| verdict | distortion | layer | count | expected -> resolved | cases |",
        "|---|---|---|---|---|---|",
        *(
            f"| {c['verdict']} | {c['distortion'] or 'real'} | {c['layer']} | {c['count']} | "
            + ", ".join(f"{p} x{n}" for p, n in c["pairs"].items())
            + f" | {', '.join(c['case_ids'])} |"
            for c in report["failure_classes"]
        ),
        "",
        "## Every failure with the engine",
        "",
        "| case | verdict | layer | band | meant | resolved | top candidate | rendering |",
        "|---|---|---|---|---|---|---|---|",
        *(
            f"| {r['case_id']} | {r['verdict']} | {r['layer']} | {r['band']} | "
            f"{r['expected_intent']} | {r['resolved_intent']} | {_top(r)} | {r['rendering']} |"
            for r in after["failures"]
        ),
        "",
        f"Repeat run: {report['repeat_run']['status']}"
        + (
            f", differing cases: {report['repeat_run']['differing_case_ids'] or 'none'}"
            if report["repeat_run"]["status"] == "RUN"
            else ""
        ),
        "",
    ]
    return "\n".join(lines)


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
                "wrong_device_observable_cases",
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
    "LAYER2_REPORT_KEYS",
    "LAYER_UNRECORDED",
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
    "BuiltEngine",
    "Observation",
    "ProductionEngineUnavailable",
    "SttResult",
    "Verdict",
    "build_layer2_report",
    "build_stt_report",
    "compare_runs",
    "failure_classes",
    "judge",
    "layer2_markdown",
    "merge_into_owner_report",
    "production_engine",
    "run_stt_case",
    "session_device_for",
    "write_reports",
]
