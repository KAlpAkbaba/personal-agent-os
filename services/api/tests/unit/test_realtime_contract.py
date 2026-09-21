"""The realtime session wire contract: one canonical document, versioned, committed.

The first real owner qualification failed at session creation with a bare
``HTTP 422``: the page (HEAD) sent ``voice`` to a one-release-older API whose
request model is ``extra="forbid"``. These tests make that class of drift
visible in CI instead of in the owner's browser.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.voice.realtime_sessions.contract import (
    CONTRACT_VERSION,
    LEGACY_V1_CREATE_FIELDS,
    create_fields,
    realtime_contract,
)

COMMITTED = (Path(__file__).resolve().parents[4] / "packages" / "protocol"
             / "realtime-session-contract.json")


def test_committed_contract_matches_the_live_request_models() -> None:
    assert COMMITTED.exists(), f"missing {COMMITTED}; run scripts/export_realtime_contract.py"
    committed = json.loads(COMMITTED.read_text(encoding="utf-8"))
    assert committed == realtime_contract(), (
        "packages/protocol/realtime-session-contract.json drifted from the Pydantic models; "
        "bump CONTRACT_VERSION if fields changed and re-run scripts/export_realtime_contract.py"
    )


def test_contract_version_and_create_fields() -> None:
    doc = realtime_contract()
    assert doc["contract_version"] == CONTRACT_VERSION == 3
    v2 = set(create_fields())
    assert set(LEGACY_V1_CREATE_FIELDS) < v2
    assert v2 - set(LEGACY_V1_CREATE_FIELDS) == {"voice"}  # the v2 addition, and only that
    assert doc["legacy"]["1"]["create_session"] == list(LEGACY_V1_CREATE_FIELDS)
    props = doc["requests"]["create_session"]["properties"]
    assert set(props) == v2
    assert doc["requests"]["create_session"].get("additionalProperties") is False, (
        "extra=forbid must be visible in the schema so a client can drop unknown fields itself"
    )


def test_every_request_body_is_in_the_contract() -> None:
    doc = realtime_contract()
    assert set(doc["requests"]) == {"create_session", "attach", "tool_call", "tool_complete",
                                    "events"}
    for name, schema in doc["requests"].items():
        assert schema.get("type") == "object", name
        assert "properties" in schema, name
