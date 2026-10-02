"""d20261002: the lead's line - ``create_app`` starts layer 2 of ADR-0224 (TEAM_PROTOCOL section 4).

``understanding-engine-startup`` built the start-up inside its own area; nothing called it.
What joins it to the running application is held here: the ONE call in ``create_app``, with the
memory runtime's own embedder and report, the declared off-switch, and the sentence the
start-up's tests call "ruleless" being ruleless for the real router.
"""

from __future__ import annotations

from typing import Any

from structlog.testing import capture_logs

from app import main as app_main
from app.config import Settings
from app.voice.intents import resolve_intent
from app.voice.understanding import startup
from tests.unit.test_understanding_startup import RULELESS


def test_create_app_hands_the_memory_runtimes_embedder_and_its_report_to_the_start_up(
    monkeypatch,
) -> None:
    seen: list[tuple[Any, Any, Any]] = []

    def configure(settings: Any, embedder: Any, *, report: Any) -> None:
        seen.append((settings, embedder, report))

    monkeypatch.setattr(app_main, "configure_understanding", configure)
    settings = Settings()
    app = app_main.create_app(settings)
    assert len(seen) == 1, "one call, in create_app"
    given_settings, embedder, report = seen[0]
    assert given_settings is settings
    # The memory runtime's OWN objects: a second embedder would be a second model in memory,
    # and a report derived again would be a second opinion on "semantic" (B37).
    assert embedder is app.state.memory.embedder
    assert report is app.state.memory.embedder_report


def test_the_call_in_create_app_is_the_start_up_modules_own_function() -> None:
    assert app_main.configure_understanding is startup.configure_understanding


def test_with_the_suites_deterministic_embedder_the_app_configures_nothing_and_says_why() -> None:
    """Every test application runs the deterministic (lexical) embedder: the engine must NOT be
    configured from it - it scored "Bugün nasılsın" as weather_query - and one line says so."""
    with capture_logs() as logs:
        app_main.create_app(Settings(memory_embedding_provider="deterministic"))
    said = [entry for entry in logs if entry["event"] == startup.EVENT_NOT_CONFIGURED]
    assert len(said) == 1, logs
    assert "deterministic" in str(said[0].get("reason")), said[0]
    assert not [entry for entry in logs if entry["event"] == startup.EVENT_CONFIGURED]


def test_the_off_switch_is_a_declared_setting_and_create_app_honours_it() -> None:
    assert Settings().understanding_semantic_enabled is True, "layer 2 is on unless switched off"
    with capture_logs() as logs:
        app_main.create_app(
            Settings(understanding_semantic_enabled=False, memory_embedding_provider="local")
        )
    said = [entry for entry in logs if entry["event"] == startup.EVENT_NOT_CONFIGURED]
    assert len(said) == 1, logs
    assert "understanding_semantic_enabled" in str(said[0].get("reason")), said[0]


def test_the_sentence_the_start_up_tests_call_ruleless_is_ruleless_for_the_real_router() -> None:
    """The first constant was matched by the weather table; the label was wrong and the audit
    row an owner would have seen for it said ``rule`` (inspector, 2026-10-02)."""
    assert resolve_intent(RULELESS).intent == "none"
    assert resolve_intent("Dışarıda hava nasıl bugün").intent != "none", (
        "the sentence this replaced IS a rule's: if that changes, choose again"
    )
