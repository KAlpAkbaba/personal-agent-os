"""M8 unit tests: SECRET HYGIENE.

Acceptance bullet covered here: **full audit trail has no secret leakage.**

The shape of the guarantee: a fixture target containing a fake credential
yields a finding that NAMES the issue (which file, which line, which credential
pattern) without reproducing the secret anywhere — not in the finding, not in
the assessment result, not in the audit trail, and not in the report.
"""

from __future__ import annotations

import json

import pytest

from app.security.redaction import (
    SECRET_PATTERNS,
    assert_redacted,
    contains_secret,
    find_secret,
    redact_text,
    redact_value,
)
from tests.unit.test_security_support import FIXTURE_ROOT, enroll_lab_host, make_stack

# The exact literals planted in tests/fixtures/security_target/app.env. If any
# of these ever appears in stored state, the redaction pass has failed.
PLANTED_SECRETS = (
    "Fak3-Placeholder-Password-Value",
    "AKIA" + "IOSFODNN7EXAMPLE",
    "placeholder-api-key-0000000000000000",
    "Fak3-Placeholder-Db-Pass",
)


@pytest.fixture()
def assessed():
    stack = make_stack()
    enroll_lab_host(stack, config_root=FIXTURE_ROOT)
    result = stack.assessments.run(target="10.20.30.40")
    return stack, result


def _blob(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False, default=str)


# --------------------------------------------------- the redactor itself


@pytest.mark.parametrize(
    ("text", "pattern"),
    [
        ("APP_PASSWORD=Fak3-Placeholder-Password-Value", "credential_assignment"),
        ("client_secret: some-long-opaque-value", "credential_assignment"),
        ("db_url=postgresql://u:p4ssw0rd@host:5432/db", "connection_uri_credential"),
        ("AWS_ACCESS_KEY_ID=" + "AKIA" + "IOSFODNN7EXAMPLE", "credential_assignment"),
        ("-----BEGIN RSA PRIVATE " + "KEY-----", "private_key_block"),
        ("Authorization: Basic QWxhZGRpbjpvcGVuc2VzYW1l", "basic_auth_header"),
    ],
)
def test_named_patterns_are_detected(text, pattern) -> None:
    assert find_secret(text) == pattern


@pytest.mark.parametrize(
    "text",
    [
        "password_rotation_days=90",
        "min_password_length: 8",
        "token_ttl = 3600",
        "password_required: true",
        "secret_enabled = false",
        "log_level = info",
    ],
)
def test_policy_knobs_are_not_treated_as_secrets(text) -> None:
    """A false positive here would flood the owner with fake criticals."""
    assert find_secret(text) is None


def test_redaction_replaces_the_value_and_names_the_pattern() -> None:
    redacted, hits = redact_text("APP_PASSWORD=Fak3-Placeholder-Password-Value")
    assert "Fak3-Placeholder-Password-Value" not in redacted
    assert "REDACTED" in redacted
    assert hits == ["credential_assignment"]


def test_redaction_output_is_itself_clean_and_stable() -> None:
    """Redacting twice must not re-trigger a pattern on the placeholder."""
    once, _ = redact_text("APP_PASSWORD=Fak3-Placeholder-Password-Value")
    twice, hits = redact_text(once)
    assert twice == once
    assert hits == []
    assert contains_secret(once) is None


def test_redaction_walks_nested_structures_including_keys() -> None:
    payload = {
        "api_key=super-secret-value": ["AKIA" + "IOSFODNN7EXAMPLE"],
        "nested": {"note": "password: hunter2hunter2"},
    }
    cleaned = redact_value(payload)
    assert contains_secret(cleaned) is None
    assert "AKIA" + "IOSFODNN7EXAMPLE" not in _blob(cleaned)


def test_assert_redacted_fails_closed() -> None:
    """If a raw secret ever reaches a write path, it is replaced, not stored."""
    guarded = assert_redacted(
        {"leak": "AWS_SECRET_ACCESS_KEY=zzzzzzzzzzzzzzzzzzzz"}, where="test"
    )
    assert guarded["redaction_failure"] is True
    assert "zzzzzzzzzzzzzzzzzzzz" not in _blob(guarded)


def test_pattern_list_is_shared_with_the_memory_subsystem() -> None:
    """One definition of 'what a secret looks like' across subsystems."""
    from app.memory.policy import SECRET_PATTERNS as MEMORY_PATTERNS

    names = {name for name, _ in SECRET_PATTERNS}
    assert {name for name, _ in MEMORY_PATTERNS} <= names


# ------------------------------- ACCEPTANCE: the finding names, never leaks


def test_fixture_credential_yields_a_finding_that_names_the_issue(assessed) -> None:
    _stack, result = assessed
    secret_findings = [
        f for f in result["findings"] if f["evidence"]["check_id"] == "secret_in_config"
    ]
    assert secret_findings, "the planted credentials must be found"

    for finding in secret_findings:
        # It NAMES the exposure...
        assert finding["severity"] == "critical"
        assert "kimlik bilgisi" in finding["title"].lower()
        assert finding["evidence"]["file"] == "app.env"
        assert finding["evidence"]["line"] > 0
        assert finding["evidence"]["secret_patterns"]
        assert finding["evidence"]["rationale_tr"]
        # ...without reproducing it.
        assert "REDACTED" in finding["evidence"]["redacted_line"]

    detected = {p for f in secret_findings for p in f["evidence"]["secret_patterns"]}
    assert {"credential_assignment", "connection_uri_credential"} <= detected


def test_no_planted_secret_appears_in_any_stored_state(assessed) -> None:
    stack, result = assessed
    surfaces = {
        "assessment": _blob(result),
        "findings": _blob(stack.assessments.list_findings()),
        "assessments": _blob(stack.assessments.list()),
        "assets": _blob(stack.registry.list()),
        "audit": _blob(stack.registry.list_events(limit=500)),
    }
    for name, blob in surfaces.items():
        for secret in PLANTED_SECRETS:
            assert secret not in blob, f"{secret!r} leaked into {name}"
        assert contains_secret(json.loads(blob)) is None, f"secret pattern survived in {name}"


def test_audit_trail_is_complete_and_secret_free(assessed) -> None:
    """ACCEPTANCE: full audit trail, no secret leakage."""
    stack, _result = assessed
    events = stack.registry.list_events(limit=500)
    assert {e["action"] for e in events} == {"enrolled", "assessment_authorized"}
    for event in events:
        assert event["created_at"]
        assert event["reason"]
        assert contains_secret(event) is None


def test_a_refusal_event_never_carries_the_requested_secret(assessed) -> None:
    stack, _result = assessed
    with stack.session() as session:
        stack.guard.evaluate(
            session,
            target="203.0.113.9",
            testing_class="configuration_audit",
        )
    refusal = stack.registry.list_events(action="assessment_refused")[0]
    assert refusal["allowed"] is False
    assert contains_secret(refusal) is None
