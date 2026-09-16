"""Alembic migration round-trip: upgrade -> downgrade -> upgrade (M0 gate)."""

import uuid
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config as AlembicConfig
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from app.config import Settings

pytestmark = pytest.mark.integration

API_ROOT = Path(__file__).resolve().parents[2]


def alembic_config() -> AlembicConfig:
    cfg = AlembicConfig(str(API_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(API_ROOT / "alembic"))
    return cfg


def table_names(settings: Settings) -> set[str]:
    engine = create_engine(settings.database_url)
    try:
        return set(inspect(engine).get_table_names())
    finally:
        engine.dispose()


def vector_extension_installed(settings: Settings) -> bool:
    engine = create_engine(settings.database_url)
    try:
        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT 1 FROM pg_extension WHERE extname = 'vector'")
            ).fetchone()
            return row is not None
    finally:
        engine.dispose()


BROKER_TABLES = {
    "devices",
    "device_sessions",
    "device_commands",
    "enrollment_tokens",
    "audit_events",
}

ARTIFACT_TABLES = {
    "tasks",
    "task_runs",
    "artifacts",
    "artifact_versions",
    "artifact_renders",
    "research_sources",
}

#: B11 req 372, migration 0060.
WEBPUSH_TABLES = {"webpush_subscriptions"}


def test_migration_round_trip(settings: Settings) -> None:
    cfg = alembic_config()

    command.upgrade(cfg, "head")
    assert "owner" in table_names(settings)
    assert BROKER_TABLES <= table_names(settings)
    assert ARTIFACT_TABLES <= table_names(settings)
    assert WEBPUSH_TABLES <= table_names(settings)
    assert vector_extension_installed(settings) is True

    command.downgrade(cfg, "base")
    assert "owner" not in table_names(settings)
    assert BROKER_TABLES.isdisjoint(table_names(settings))
    assert ARTIFACT_TABLES.isdisjoint(table_names(settings))
    assert WEBPUSH_TABLES.isdisjoint(table_names(settings))
    assert vector_extension_installed(settings) is False

    command.upgrade(cfg, "head")
    assert "owner" in table_names(settings)
    assert BROKER_TABLES <= table_names(settings)
    assert ARTIFACT_TABLES <= table_names(settings)
    assert WEBPUSH_TABLES <= table_names(settings)
    assert vector_extension_installed(settings) is True


def test_downgrade_one_revision_drops_only_m3(settings: Settings) -> None:
    """0003 -> 0002 must remove M3 tables and keep the M0/M1 schema intact."""
    cfg = alembic_config()
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "0002_device_broker")
    names = table_names(settings)
    assert ARTIFACT_TABLES.isdisjoint(names)
    assert BROKER_TABLES <= names
    assert "owner" in names
    command.upgrade(cfg, "head")
    assert ARTIFACT_TABLES <= table_names(settings)


def test_owner_defaults_after_upgrade(settings: Settings) -> None:
    cfg = alembic_config()
    command.upgrade(cfg, "head")
    engine = create_engine(settings.database_url)
    try:
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM owner"))
            conn.execute(text("INSERT INTO owner (display_name) VALUES ('Owner Test')"))
            row = conn.execute(
                text("SELECT locale, timezone, settings_json FROM owner")
            ).one()
            assert row.locale == "tr-TR"
            assert row.timezone == "Europe/Istanbul"
            assert row.settings_json == {}
            conn.execute(text("DELETE FROM owner"))
    finally:
        engine.dispose()


def test_downgrade_one_revision_drops_only_webpush(settings: Settings) -> None:
    """0060 -> 0059 must remove ONLY webpush_subscriptions and keep everything else
    (in particular ambient_policy, 0059's own table) intact - B11's migration test."""
    cfg = alembic_config()
    command.upgrade(cfg, "head")
    assert WEBPUSH_TABLES <= table_names(settings)
    command.downgrade(cfg, "0059_ambient_camera_mode")
    names = table_names(settings)
    assert WEBPUSH_TABLES.isdisjoint(names)
    assert "ambient_policy" in names
    assert "owner" in names
    command.upgrade(cfg, "head")
    assert WEBPUSH_TABLES <= table_names(settings)


def test_webpush_subscriptions_endpoint_is_unique(settings: Settings) -> None:
    """The migration's unique index (ux_webpush_subscriptions_endpoint) is the DB-level
    half of app.webpush.service.subscribe's "re-subscribing the same browser updates the
    existing row" behaviour - proven here against a REAL PostgreSQL constraint, not just
    the ORM-level `unique=True` SQLite exercises in the unit suite."""
    cfg = alembic_config()
    command.upgrade(cfg, "head")
    engine = create_engine(settings.database_url)
    try:
        endpoint = f"https://fcm.googleapis.com/fcm/send/{uuid.uuid4()}"
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO webpush_subscriptions (id, endpoint, p256dh, auth) "
                    "VALUES (:id, :endpoint, 'p256dh-a', 'auth-a')"
                ),
                {"id": str(uuid.uuid4()), "endpoint": endpoint},
            )
        try:
            with engine.begin() as conn:
                conn.execute(
                    text(
                        "INSERT INTO webpush_subscriptions (id, endpoint, p256dh, auth) "
                        "VALUES (:id, :endpoint, 'p256dh-b', 'auth-b')"
                    ),
                    {"id": str(uuid.uuid4()), "endpoint": endpoint},
                )
            raise AssertionError("a second row with the same endpoint must be refused")
        except IntegrityError:
            pass
        finally:
            with engine.begin() as conn:
                conn.execute(
                    text("DELETE FROM webpush_subscriptions WHERE endpoint = :endpoint"),
                    {"endpoint": endpoint},
                )
    finally:
        engine.dispose()
