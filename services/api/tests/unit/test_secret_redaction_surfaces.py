"""B04 req 6/7/8/668/683: the thirteen patterns, applied where output actually leaves.

The patterns have existed since M8 and the security agent's own write paths used them. What
did not exist was the PIPELINE: three surfaces built their output without ever asking the
redactor, and the most visible consequence was measurable on 2026-09-12 - the production
database password came back in clear text from `/v1/world/facts`.

Each surface here is driven the way production drives it (the real snapshot assembler, the
real structlog processor chain, the real health check runner), and the assertion is the same
in each case: the secret is not in the output, and the shape of the fact that carried it is.

The sentinel below is deliberately not shaped like any real vendor key, so the repository's
own secret-hygiene scan has nothing to flag in this file.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime

import pytest
import structlog
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import Settings
from app.health import _run_check
from app.logging import REDACTED_BY_KEY, redact_secrets
from app.security.redaction import contains_secret, strip_uri_credentials
from app.worldmodel.state import assemble_snapshot

#: Not a real credential and not shaped like one: what matters is that it is the value the
#: `credential_assignment` / `connection_uri_credential` patterns are supposed to catch.
SECRET = "unit-test-not-a-real-password-8842"
DSN = f"postgresql+asyncpg://pagentos:{SECRET}@10.0.0.5:5432/pagentos"

NOW = datetime(2026, 9, 12, 12, 0, tzinfo=UTC)


# ---------------------------------------------------------------- the world model


@pytest.fixture()
def world_session():
    from app.artifacts.models import Artifact, ArtifactRender, ArtifactVersion, Task, TaskRun
    from app.broker.models import AuditEvent, Device, DeviceCommand, DeviceSession
    from app.identity.models import OwnerSession
    from app.ledger.models import ActivityEventRow
    from app.selfhealing.models import Incident, Release

    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    for table in (
        OwnerSession.__table__,
        Device.__table__,
        DeviceSession.__table__,
        DeviceCommand.__table__,
        AuditEvent.__table__,
        Task.__table__,
        TaskRun.__table__,
        Artifact.__table__,
        ArtifactVersion.__table__,
        ArtifactRender.__table__,
        ActivityEventRow.__table__,
        Incident.__table__,
        Release.__table__,
    ):
        table.create(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        yield session
    engine.dispose()


def _settings_with_secret() -> Settings:
    return Settings(
        _env_file=None,
        database_url=DSN,
        redis_url=f"redis://:{SECRET}@127.0.0.1:6379/0",
    )


def test_the_database_password_is_not_in_the_snapshot(world_session) -> None:
    """The defect itself: `/v1/world/facts` returned the production DSN, password included.
    Owner-gated, which is not the same as safe - the value goes on to the Cockpit, into
    whatever quotes the snapshot, and into any screenshot of it."""
    snapshot = assemble_snapshot(world_session, settings=_settings_with_secret(), now=NOW)

    body = snapshot.as_dict()
    assert SECRET not in repr(body)
    assert contains_secret(body) is None


def test_the_snapshot_still_says_which_database(world_session) -> None:
    """Redaction that destroys the fact is not a fix. The host, port and database name are
    the reason this fact exists; only the userinfo is the owner's secret."""
    snapshot = assemble_snapshot(world_session, settings=_settings_with_secret(), now=NOW)

    fact = next(f for f in snapshot.facts if f.key == "dependencies.database_url")
    assert fact.value == "postgresql+asyncpg://***@10.0.0.5:5432/pagentos"
    assert "10.0.0.5" in fact.value and "pagentos" in fact.value


def test_a_secret_reaching_any_fact_at_all_is_redacted(world_session) -> None:
    """The fix is on the door every collector writes through, not on the one collector that
    leaked. A section added later gets it for free - which is the whole point."""
    from app.worldmodel.state import TruthKind, _Collector

    c = _Collector(NOW)
    c.fact("some.future.fact", "future", f"api_key={SECRET}", TruthKind.SOURCE)
    c.fact("nested.fact", "future", {"config": [f"password: {SECRET}"]}, TruthKind.SOURCE)

    assert SECRET not in repr([f.as_dict() for f in c.facts])


def test_an_uncertainty_detail_is_redacted_too(world_session) -> None:
    """`uncertain(**detail)` is free-form kwargs from any section: exactly where a driver's
    exception string - the one that quotes the DSN - arrives."""
    from app.worldmodel.state import _Collector

    c = _Collector(NOW)
    c.uncertain("dependencies", "dependencies.runtime", "probe_failed", error=f"cannot dial {DSN}")

    assert SECRET not in repr([u.as_dict() for u in c.uncertainties])


# ---------------------------------------------------------------- the log pipeline


def test_a_dsn_in_a_log_line_never_reaches_stdout() -> None:
    out = redact_secrets(None, "info", {"event": "db_connect_failed", "dsn": DSN})

    assert SECRET not in repr(out)
    assert out["event"] == "db_connect_failed", "the message itself is untouched"


def test_a_credential_shaped_key_is_replaced_by_name() -> None:
    """No pattern can recognise `hunter2`; only the key says what it is."""
    out = redact_secrets(None, "info", {"event": "auth", "password": SECRET})

    assert out["password"] == REDACTED_BY_KEY


def test_a_nested_credential_key_is_replaced_too() -> None:
    """A log event is almost always the nested shape, and scanning only the top level is the
    difference between catching this and printing it."""
    out = redact_secrets(
        None,
        "info",
        {"event": "request", "body": {"headers": {"authorization": f"Bearer {SECRET}"}}},
    )

    assert out["body"]["headers"]["authorization"] == REDACTED_BY_KEY


def test_a_count_named_tokens_survives_as_a_number() -> None:
    """`tokens: 1430` from an LLM call matches the key vocabulary and is not a secret.
    Redaction that eats the observability it protects has cost more than it saved."""
    out = redact_secrets(None, "info", {"event": "llm_call", "tokens": 1430, "cost_usd": 0.02})

    assert out["tokens"] == 1430
    assert out["cost_usd"] == 0.02


def test_a_traceback_carrying_the_dsn_is_redacted() -> None:
    """The processor runs AFTER format_exc_info, when the traceback is a plain string - and
    a traceback is the likeliest place a connection string appears."""
    out = redact_secrets(
        None, "error", {"event": "boom", "exception": f'OperationalError: connecting to "{DSN}"'}
    )

    assert SECRET not in out["exception"]
    assert "OperationalError" in out["exception"], "the failure still says what it was"


def test_the_processor_is_actually_in_the_configured_chain() -> None:
    """A redactor nothing calls is the shape of this whole batch's defect: the patterns were
    already there. This asserts the WIRING, by configuring logging the way the app does and
    reading back the chain structlog is holding."""
    from app.logging import configure_logging

    configure_logging(json_output=True)
    processors = structlog.get_config()["processors"]

    assert redact_secrets in processors
    names = [getattr(p, "__name__", type(p).__name__) for p in processors]
    assert names.index("redact_secrets") == len(processors) - 2, (
        "it must sit immediately before the renderer, so nothing an earlier processor adds "
        f"escapes the scan; chain is {names}"
    )


def test_the_standard_library_pipeline_is_covered_too() -> None:
    """structlog is not this process's only writer. uvicorn's access lines, SQLAlchemy's
    engine logging and every third-party library go through `logging` directly, past the
    processor chain entirely - and requirement 683 says no secret is logged, not no secret is
    logged by our own code."""
    import io
    import logging as stdlib_logging

    from app.logging import configure_logging

    configure_logging(json_output=True)
    buffer = io.StringIO()
    root = stdlib_logging.getLogger()
    streams = [(h, getattr(h, "stream", None)) for h in root.handlers]
    for handler, _ in streams:
        handler.stream = buffer
    try:
        # `%s` arguments, not an f-string: a secret in `args` is invisible until `msg % args`
        # has run, which is why the filter formats before it scans.
        stdlib_logging.getLogger("uvicorn.access").warning("connecting to %s", DSN)
    finally:
        for handler, stream in streams:
            if stream is not None:
                handler.stream = stream

    assert SECRET not in buffer.getvalue()
    assert "connecting to" in buffer.getvalue()


def test_a_stdlib_traceback_is_redacted_before_the_formatter_sees_it() -> None:
    """`exc_info` is rendered by the formatter, long after any filter could run. The filter
    formats it into `exc_text` itself for exactly that reason."""
    import io
    import logging as stdlib_logging

    from app.logging import configure_logging

    configure_logging(json_output=True)
    buffer = io.StringIO()
    root = stdlib_logging.getLogger()
    streams = [(h, getattr(h, "stream", None)) for h in root.handlers]
    for handler, _ in streams:
        handler.stream = buffer
    try:
        try:
            raise RuntimeError(f"dsn {DSN}")
        except RuntimeError:
            stdlib_logging.getLogger("sqlalchemy.engine").error("connect failed", exc_info=True)
    finally:
        for handler, stream in streams:
            if stream is not None:
                handler.stream = stream

    written = buffer.getvalue()
    assert SECRET not in written
    assert "RuntimeError" in written, "the traceback still says what failed"


# ---------------------------------------------------------------- health


def test_a_failing_health_check_does_not_quote_the_password() -> None:
    """The only surface that answers with NO owner session. A driver's connection error
    quotes the DSN it was handed, so before this the exposure was: point anything at
    /v1/system/health while the database is down and read the credential off the response."""

    async def explode() -> None:
        raise RuntimeError(f'could not connect to "{DSN}"')

    result = asyncio.run(_run_check("db", explode, timeout_s=1))

    assert result["status"] == "fail"
    assert SECRET not in result["error"]
    assert "RuntimeError" in result["error"], "the class of failure is still reported"


def test_a_health_failure_that_holds_no_secret_is_reported_verbatim() -> None:
    """Redaction must not blur ordinary failures: most health errors are the operator's only
    clue and contain nothing sensitive."""

    async def explode() -> None:
        raise TimeoutError("connection attempt timed out after 2s")

    result = asyncio.run(_run_check("db", explode, timeout_s=1))

    assert result["error"] == "TimeoutError: connection attempt timed out after 2s"


# ---------------------------------------------------------------- the vocabulary itself


def test_all_thirteen_patterns_are_the_ones_being_applied() -> None:
    """One vocabulary, not three. The world model, the log pipeline and health all reach the
    same SECRET_PATTERNS; a pattern added to app.memory.policy shows up on all three."""
    from app.memory.policy import SECRET_PATTERNS as MEMORY
    from app.security.redaction import SECRET_PATTERNS as APPLIED

    assert len(APPLIED) == 13, [name for name, _ in APPLIED]
    memory_names = {name for name, _ in MEMORY}
    applied_names = {name for name, _ in APPLIED}
    assert memory_names <= applied_names, sorted(memory_names - applied_names)


def test_stripping_credentials_leaves_a_url_without_userinfo_alone() -> None:
    plain = "http://minio:9000"
    assert strip_uri_credentials(plain) == plain
    assert strip_uri_credentials("") == ""


def test_a_bare_host_and_port_is_not_a_uri_and_is_left_alone() -> None:
    """`temporal_address` is `host:port`, not a URL. Nothing to strip, and mangling it would
    break the fact it belongs to."""
    assert strip_uri_credentials("localhost:7233") == "localhost:7233"


def test_the_redaction_names_the_pattern_that_fired() -> None:
    """The shape of the exposure survives even though the value does not - that is what makes
    a redacted log line still worth reading."""
    out = redact_secrets(None, "info", {"event": "x", "cfg": f"db: {DSN}"})

    assert "[REDACTED:connection_uri_credential]" in out["cfg"]


def test_an_id_that_merely_looks_long_is_not_redacted() -> None:
    """A detector that fires on everything protects nothing: trace ids, digests and commit
    shas pass through every one of these surfaces constantly."""
    trace = uuid.uuid4().hex
    digest = "sha256:" + "a" * 64
    out = redact_secrets(None, "info", {"event": "x", "trace_id": trace, "digest": digest})

    assert out["trace_id"] == trace
    assert out["digest"] == digest
