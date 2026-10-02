"""A database change that only SQLite has seen does not pass the gate (owner rule, 2026-10-01).

ADR-0214 addendum 4. The team's lock row was written with a 42-character version into a
VARCHAR(32): every unit test ran on SQLite, which does not enforce the length, the worker
wrote NOT_RUN beside "DbStore on Postgres", and the first cycle in database mode died in
production. The rule since: a table is exercised on the dev stack's PostgreSQL by a test
under ``tests/integration`` (the gate runs those against real Postgres, after the real
migrations), or the change does not merge.

What this file can hold mechanically is the floor of that rule: every mapped table is NAMED
by an integration test, by its table name or its model class. Naming is not exercising - the
inspector's run on the dev stack is what proves a claim (``.claude/agents/inspector.md``) -
but a table nothing under ``tests/integration`` even mentions has certainly never met
Postgres in the gate. The tables that were in that state when the rule was made are frozen
below; the list may only shrink.
"""

from __future__ import annotations

import importlib
import pkgutil
import re
from pathlib import Path

from app.models import Base

API = Path(__file__).resolve().parents[2]
INTEGRATION = API / "tests" / "integration"

#: The 51 of 87 mapped tables no integration test named on 2026-10-01 (some ARE reached
#: through a route an integration test calls - the name check cannot see that; they leave
#: the list when a test names them). Debt, not permission: a NEW table is never
#: added here - it gets a Postgres test - and a table that gains one is removed from the list.
#: Paid since: the memory side tables and routines/alarms (eight tables, 2026-10-01,
#: ``test_memory_tables_postgres.py`` and ``test_routines_alarms_postgres.py``); 43 remain.
UNEXERCISED_BASELINE = frozenset(
    {
        "app_projects",
        "authorization_events",
        "briefing_preferences",
        "calendar_index",
        "calendar_proposals",
        "code_edges",
        "code_modules",
        "code_symbols",
        "creative_runs",
        "document_index",
        "evolution_opportunities",
        "executive_runs",
        "executive_steps",
        "experience_lessons",
        "file_mutations",
        "genesis_catalogue",
        "genesis_runs",
        "goal_tasks",
        "goals",
        "location_context",
        "mail_drafts",
        "mail_index",
        "module_provenance",
        "native_builds",
        "news_playback_contexts",
        "news_resolutions",
        "news_sources",
        "object_focus",
        "operator_missions",
        "owner_media_playbacks",
        "pending_briefings",
        "pronunciation_entries",
        "research_candidates",
        "research_focus",
        "research_owner_state",
        "research_reports",
        "scenes",
        "selfdev_defects",
        "speaker_verdicts",
        "voice_macros",
        "voice_profiles",
        "weather_query_evidence",
    }
)


def _mapped_tables() -> dict[str, str]:
    """table -> model class, for EVERY model module of the application.

    Every ``models`` / ``*_models`` module is imported here, so the answer does not depend on
    which other tests happened to import what before this one ran."""
    import app

    for module in pkgutil.walk_packages(app.__path__, "app."):
        leaf = module.name.rsplit(".", 1)[-1]
        if leaf == "models" or leaf.endswith("_models"):
            importlib.import_module(module.name)
    return {mapper.local_table.name: mapper.class_.__name__ for mapper in Base.registry.mappers}


def _named_by_integration_tests(tables: dict[str, str]) -> set[str]:
    text = "\n".join(
        path.read_text("utf-8", errors="ignore") for path in sorted(INTEGRATION.glob("*.py"))
    )
    return {
        table
        for table, model in tables.items()
        if re.search(rf"\b{re.escape(table)}\b", text)
        or re.search(rf"\b{re.escape(model)}\b", text)
    }


def test_every_table_is_named_by_a_test_that_runs_on_postgres() -> None:
    tables = _mapped_tables()
    unnamed = set(tables) - _named_by_integration_tests(tables)
    new = sorted(unnamed - UNEXERCISED_BASELINE)
    assert not new, (
        "these tables are exercised by no test under tests/integration, so only SQLite has "
        "seen them (it does not enforce VARCHAR lengths, JSONB, or server defaults): "
        f"{new}. Write a test that takes the real calls to the dev stack's PostgreSQL - "
        "tests/integration/test_team_state_postgres.py is the model - and do NOT add the "
        "table to UNEXERCISED_BASELINE."
    )


def test_the_baseline_only_shrinks() -> None:
    tables = _mapped_tables()
    unnamed = set(tables) - _named_by_integration_tests(tables)
    paid = sorted(UNEXERCISED_BASELINE & set(tables) - unnamed)
    assert not paid, (
        f"these now have a Postgres test: remove them from UNEXERCISED_BASELINE: {paid}"
    )
    gone = sorted(UNEXERCISED_BASELINE - set(tables))
    assert not gone, f"these tables no longer exist: remove them from UNEXERCISED_BASELINE: {gone}"


def test_the_table_that_made_the_rule_is_covered() -> None:
    tables = _mapped_tables()
    assert "team_state" in tables
    assert "team_state" in _named_by_integration_tests(tables)
    assert "team_state" not in UNEXERCISED_BASELINE
