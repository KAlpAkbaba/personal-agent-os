"""ADR-0224 layer 2 in production: the shipped exemplars and the start-up that configures
the semantic engine.

The exemplars are the corpus (`tests/voice_corpus`), exported to package data because
production cannot import ``tests/``. When the byte-equality test below is RED, re-run:

    uv run python scripts/export_understanding_exemplars.py
"""

from __future__ import annotations

import ast
import importlib.util
import json
import threading
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest
from structlog.testing import capture_logs

import app
from app.memory.embedding import DeterministicEmbedder
from app.memory.providers import EmbedderReport
from app.voice.intents import Intent
from app.voice.understanding import combine, exemplars, policy, startup
from app.voice.understanding.semantic import exemplars_from_cases
from tests.voice_corpus.corpus import UtteranceCase, all_cases

API_ROOT = Path(app.__file__).resolve().parents[1]
COMMITTED = Path(exemplars.__file__).with_name(exemplars.EXEMPLARS_FILE)
RERUN = "uv run python scripts/export_understanding_exemplars.py"


def _export_script() -> ModuleType:
    path = API_ROOT / "scripts" / "export_understanding_exemplars.py"
    spec = importlib.util.spec_from_file_location("export_understanding_exemplars", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def export() -> ModuleType:
    return _export_script()


@pytest.fixture(autouse=True)
def _no_default_engine() -> Any:
    combine.reset_default_engine()
    yield
    combine.reset_default_engine()


# --- the shipped file and the corpus cannot drift --------------------------------------


def test_committed_exemplars_are_byte_equal_to_the_export_of_todays_corpus(
    export: ModuleType,
) -> None:
    assert COMMITTED.exists(), f"missing {COMMITTED}; run: {RERUN}"
    expected = export.render(exemplars_from_cases(all_cases())).encode("utf-8")
    assert COMMITTED.read_bytes() == expected, (
        f"{COMMITTED.name} is not today's corpus (a case was added, removed or reworded "
        f"without a re-export); run: {RERUN}"
    )
    assert export.main(["--check"]) == 0


def test_a_corpus_case_added_without_reexport_is_drift(export: ModuleType) -> None:
    added = UtteranceCase(
        case_id="drift.1",
        utterance="Bu cümle korpusta hiç yoktu efendim.",
        expected_intent="weather_query",
        expected_tool=None,
    )
    grown = export.render(exemplars_from_cases([*all_cases(), added]))
    assert COMMITTED.read_bytes() != grown.encode("utf-8")


def test_check_mode_fails_on_a_drifted_file_and_names_the_command(
    export: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    drifted = tmp_path / "exemplars.json"
    drifted.write_bytes(COMMITTED.read_bytes().replace(b"\n ]", b"\n ] ", 1))
    assert export.main(["--check"], target=drifted) == 1
    assert "export_understanding_exemplars.py" in capsys.readouterr().err
    missing = tmp_path / "absent.json"
    assert export.main(["--check"], target=missing) == 1


def test_export_writes_what_it_renders_and_is_stable(export: ModuleType, tmp_path: Path) -> None:
    target = tmp_path / "exemplars.json"
    assert export.main([], target=target) == 0
    first = target.read_bytes()
    assert first == COMMITTED.read_bytes()
    assert b"\r" not in first and first.endswith(b"\n")
    assert export.main([], target=target) == 0
    assert target.read_bytes() == first


def test_the_file_is_sorted_and_carries_no_test_only_field() -> None:
    data = json.loads(COMMITTED.read_text(encoding="utf-8"))
    assert set(data) == {"version", "exemplars"}
    rows = data["exemplars"]
    assert all(set(row) == {"intent", "sentence"} for row in rows)
    keys = [(row["intent"], row["sentence"]) for row in rows]
    assert keys == sorted(keys)
    assert len(set(keys)) == len(keys)
    # Turkish letters are stored as themselves, not as \u escapes.
    assert "ş" in COMMITTED.read_text(encoding="utf-8")


# --- the loader ------------------------------------------------------------------------


def test_load_returns_the_same_exemplars_the_tests_build_from_the_corpus() -> None:
    loaded = exemplars.load_exemplars()
    assert sorted(loaded) == sorted(exemplars_from_cases(all_cases()))
    assert all(intent in {i.value for i in Intent} for intent, _ in loaded)


def _file(tmp_path: Path, payload: Any) -> Path:
    path = tmp_path / "exemplars.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def test_a_file_naming_an_unknown_intent_is_refused(tmp_path: Path) -> None:
    path = _file(
        tmp_path,
        {
            "version": 1,
            "exemplars": [
                {"intent": "alarm_stop", "sentence": "Alarmı kapat."},
                {"intent": "alarm_explode", "sentence": "Alarmı patlat."},
            ],
        },
    )
    with pytest.raises(ValueError, match="alarm_explode"):
        exemplars.load_exemplars(path)


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {"version": 1},
        {"version": 1, "exemplars": []},
        {"version": 2, "exemplars": [{"intent": "alarm_stop", "sentence": "Alarmı kapat."}]},
        {"version": 1, "exemplars": [{"intent": "alarm_stop"}]},
        {"version": 1, "exemplars": [{"intent": "alarm_stop", "sentence": "  "}]},
        {"version": 1, "exemplars": [{"intent": "alarm_stop", "sentence": 7}]},
        {
            "version": 1,
            "exemplars": [{"intent": "alarm_stop", "sentence": "Alarmı kapat.", "case_id": "a"}],
        },
    ],
)
def test_a_malformed_file_is_refused(tmp_path: Path, payload: Any) -> None:
    with pytest.raises(ValueError):
        exemplars.load_exemplars(_file(tmp_path, payload))


# --- start-up --------------------------------------------------------------------------

LOCAL = EmbedderReport(
    requested="local",
    active="local",
    model_id="local-fake",
    model_version="dim256",
    dim=256,
    semantic=True,
)
FALLBACK = EmbedderReport(
    requested="local",
    active="deterministic",
    model_id="deterministic",
    model_version="v1",
    dim=256,
    semantic=False,
    fallback_reason="local embedding provider needs the fastembed package",
)
SETTINGS = SimpleNamespace()
FEW = [
    ("alarm_stop", "Alarmı kapat."),
    ("weather_query", "Bugün hava nasıl?"),
    ("research_cancel", "Araştırmayı iptal et."),
]
# A sentence the REAL router answers with no rule (measured: resolve_intent -> none). The first
# constant here, "Dışarıda hava nasıl bugün", is matched by the weather table: the mechanism
# was right and the label was wrong (inspector, 2026-10-02) - and a test below holds the label.
RULELESS = "Bugün nasılsın"


@dataclass
class FakeSemanticEmbedder:
    """The deterministic vectors behind an embedder the report marks semantic; each call
    costs ``cost_s`` on the fake clock (a slow index build)."""

    cost_s: float = 0.0
    now: float = 0.0
    calls: int = 0
    model_id: str = "local-fake"
    model_version: str = "dim256"
    dim: int = 256
    _inner: DeterministicEmbedder = field(default_factory=DeterministicEmbedder)

    def embed(self, text: str) -> list[float]:
        self.calls += 1
        self.now += self.cost_s
        return self._inner.embed(text)

    def clock(self) -> float:
        return self.now


def _inline(build: Any) -> None:
    build()


def test_with_a_semantic_embedder_the_default_engine_answers() -> None:
    embedder = FakeSemanticEmbedder()
    with capture_logs() as logs:
        state = startup.configure_understanding(
            SETTINGS, embedder, report=LOCAL, exemplars=FEW, spawn=_inline
        )
    assert state.configured is True and state.reason is None
    engine = policy.configured_engine()
    assert engine is not None and engine.embedder is embedder
    ranked = combine.understand(RULELESS, device_aliases={}, rule_result=None)
    assert ranked and ranked[0].source == "semantic"
    assert ranked[0].intent == "weather_query"
    decision = policy.read_turn(RULELESS, rule=None, engine=engine)
    assert decision.audit_block()["layer"] == policy.LAYER_SEMANTIC
    done = [e for e in logs if e["event"] == "understanding_engine_configured"]
    assert len(done) == 1 and done[0]["exemplars"] == len(FEW)


def test_the_shipped_exemplars_are_what_start_up_indexes_by_default() -> None:
    embedder = FakeSemanticEmbedder()
    startup.configure_understanding(SETTINGS, embedder, report=LOCAL, spawn=_inline)
    engine = policy.configured_engine()
    assert engine is not None
    hit = engine.intent_hits("Alarmı kapat.")[0]
    assert (hit.intent, round(hit.cosine, 6)) == ("alarm_stop", 1.0)


@pytest.mark.parametrize(
    ("embedder", "report", "reason"),
    [
        (DeterministicEmbedder(), FALLBACK, "needs the fastembed package"),
        # A report that says "semantic" over the lexical embedder is still the lexical one.
        (DeterministicEmbedder(), LOCAL, "deterministic"),
        # A semantic provider that is not on this host is a network call per sentence.
        (
            FakeSemanticEmbedder(),
            EmbedderReport("openai", "openai", "openai-x", "dim256", 256, True),
            "openai",
        ),
    ],
)
def test_without_the_local_semantic_embedder_nothing_is_configured_and_the_reason_is_logged(
    embedder: Any, report: EmbedderReport, reason: str
) -> None:
    spawned: list[Any] = []
    with capture_logs() as logs:
        state = startup.configure_understanding(
            SETTINGS, embedder, report=report, exemplars=FEW, spawn=spawned.append
        )
    assert state.configured is False
    assert spawned == []
    assert policy.configured_engine() is None
    lines = [e for e in logs if e["event"] == "understanding_engine_not_configured"]
    assert len(lines) == 1
    assert reason in lines[0]["reason"]
    assert reason in (state.reason or "")


def test_the_lexical_embedder_never_decides() -> None:
    """What the guard is for: on the lexical embedder these two score as a weather query
    and a research cancel. With it, start-up leaves the rule tables alone."""
    startup.configure_understanding(
        SETTINGS, DeterministicEmbedder(), report=FALLBACK, spawn=_inline
    )
    for sentence in ("Bugün nasılsın", "Araştırmayı iptal etme"):
        decision = policy.read_turn(sentence, rule=None, engine=policy.configured_engine())
        assert decision.ranked == ()
        assert decision.layer == policy.LAYER_NONE


def test_the_owner_can_switch_the_engine_off() -> None:
    with capture_logs() as logs:
        state = startup.configure_understanding(
            SimpleNamespace(understanding_semantic_enabled=False),
            FakeSemanticEmbedder(),
            report=LOCAL,
            exemplars=FEW,
            spawn=_inline,
        )
    assert state.configured is False and policy.configured_engine() is None
    assert [e["reason"] for e in logs if e["event"] == "understanding_engine_not_configured"] == [
        "understanding_semantic_enabled is false"
    ]


def test_a_slow_index_build_does_not_delay_the_return_and_the_rule_decides_meanwhile() -> None:
    embedder = FakeSemanticEmbedder(cost_s=1.0)  # three exemplars + the preference family
    pending: list[Any] = []
    started = embedder.clock()
    state = startup.configure_understanding(
        SETTINGS,
        embedder,
        report=LOCAL,
        exemplars=FEW,
        spawn=pending.append,
        clock=embedder.clock,
    )
    assert embedder.clock() - started <= 0.5, "the index was built before the function returned"
    assert embedder.calls == 0
    assert len(pending) == 1
    assert state.configured is False and state.building is True

    # Meanwhile: the engine is "not ready" and the policy reads the rule candidate only.
    assert policy.configured_engine() is None
    rule = policy.rule_reading("alarm_stop")
    decision = policy.read_turn("Alarmı kapat.", rule=rule, engine=policy.configured_engine())
    assert [(c.source, c.intent) for c in decision.ranked] == [("rule", "alarm_stop")]
    assert policy.read_turn(RULELESS, rule=None, engine=policy.configured_engine()).ranked == ()

    with capture_logs() as logs:
        pending[0]()
    assert state.configured is True and state.building is False
    assert embedder.clock() - started >= 3.0
    assert policy.configured_engine() is not None
    done = [e for e in logs if e["event"] == "understanding_engine_configured"]
    assert len(done) == 1 and done[0]["build_ms"] >= 3000


def test_the_default_spawn_is_a_background_thread() -> None:
    release = threading.Event()
    waited: list[bool] = []

    @dataclass
    class Blocking(FakeSemanticEmbedder):
        def embed(self, text: str) -> list[float]:
            if not waited:
                # Bounded: a build run inline fails the assertion below instead of hanging.
                waited.append(release.wait(5.0))
            return super().embed(text)

    state = startup.configure_understanding(SETTINGS, Blocking(), report=LOCAL, exemplars=FEW)
    returned_before_the_build = policy.configured_engine() is None and state.building
    release.set()
    assert state.thread is not None and state.thread.daemon
    state.thread.join(10.0)
    assert not state.thread.is_alive()
    assert returned_before_the_build
    assert waited == [True]
    assert state.configured is True and policy.configured_engine() is not None


def test_a_failing_build_leaves_the_rule_tables_and_says_so() -> None:
    @dataclass
    class Broken(FakeSemanticEmbedder):
        def embed(self, text: str) -> list[float]:
            raise RuntimeError("model gone")

    with capture_logs() as logs:
        state = startup.configure_understanding(
            SETTINGS, Broken(), report=LOCAL, exemplars=FEW, spawn=_inline
        )
    assert state.configured is False and state.building is False
    assert state.reason == "index build failed: RuntimeError"
    assert policy.configured_engine() is None
    assert [e["reason"] for e in logs if e["event"] == "understanding_engine_not_configured"] == [
        "index build failed: RuntimeError"
    ]


# --- production never imports tests/ ----------------------------------------------------


def test_no_module_under_app_imports_tests() -> None:
    offenders: list[str] = []
    app_dir = Path(app.__file__).resolve().parent
    for path in sorted(app_dir.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                names = [node.module or ""]
            if any(name == "tests" or name.startswith("tests.") for name in names):
                offenders.append(f"{path.relative_to(app_dir)}:{node.lineno}")
    assert offenders == []
