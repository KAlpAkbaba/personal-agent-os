"""The local mode remembers and recalls through the REAL relay (ADR-0192).

The unit tests in test_memory_voice_tools build the turn record by hand, which is exactly
what `service._record_utterance`'s own comment warns cannot prove anything: a field added
to the tool and never copied onto the turn record passes every such test and still reaches
the tool as nothing. This goes utterance event -> turn record -> an EMPTY-argument tool call,
the way the browser in the local mode actually does it.
"""

# ruff: noqa: F811 - the shared `wired` fixture is imported and then named as a parameter
from __future__ import annotations

import pytest
from sqlalchemy import select

from app.memory.embedding import DeterministicEmbedder
from app.memory.models import (
    Entity,
    EntityEdge,
    Memory,
    MemoryAuditEvent,
    MemoryEmbedding,
    MemoryEvidence,
    MemoryVersion,
)
from app.operator.models import ObjectFocusRow
from tests.unit.test_voice_realtime_sessions import _create
from tests.unit.test_voice_realtime_sessions import wired as _wired  # noqa: F401


class _MemoryRuntime:
    embedder = DeterministicEmbedder()


@pytest.fixture()
def wired(_wired):
    """The shared relay fixture, plus the memory tables and the memory runtime - handed
    over through `register_live`, the SAME path `app.main.create_app` uses in production,
    so the tools read exactly what production reads."""
    client, identity, runtime, sideband, issued, engine = _wired
    for table in (
        Entity.__table__,
        EntityEdge.__table__,
        Memory.__table__,
        MemoryVersion.__table__,
        MemoryEvidence.__table__,
        MemoryEmbedding.__table__,
        MemoryAuditEvent.__table__,
        ObjectFocusRow.__table__,
    ):
        table.create(engine, checkfirst=True)
    runtime.register_live(memory_runtime=_MemoryRuntime())
    yield client, runtime, identity, sideband, issued, engine


def _utter(client, sid: str, text: str, *, turn: int = 1) -> None:
    r = client.post(
        f"/v1/voice/realtime/sessions/{sid}/events",
        json={"events": [{"kind": "utterance", "t_ms": 100, "turn": turn, "text": text}]},
    )
    assert r.status_code == 200, r.text


def _call(client, sid: str, call_id: str, name: str) -> dict:
    r = client.post(
        f"/v1/voice/realtime/sessions/{sid}/tool-calls",
        json={"call_id": call_id, "name": name, "arguments": {}},
    )
    assert r.status_code == 200, r.text
    return r.json()


def test_bunu_hatirla_keeps_the_fact_in_the_local_mode(wired) -> None:
    client, runtime, *_ = wired
    sid = _create(client)["session_id"]
    _utter(client, sid, "Bunu hatırla: kahveyi şekersiz içiyorum")

    body = _call(client, sid, "local-mem-1", "memory.remember")

    assert body["status"] == "succeeded", (
        body.get("error_class"),
        body.get("result"),
        body.get("error"),
    )
    with runtime.session() as db:
        texts = [m.text for m in db.execute(select(Memory)).scalars()]
    assert "kahveyi şekersiz içiyorum" in texts, texts


def test_the_local_mode_recalls_the_subject_it_was_asked_about(wired) -> None:
    client, _runtime, *_ = wired
    sid = _create(client)["session_id"]
    _utter(client, sid, "Bunu hatırla: kahveyi şekersiz içiyorum")
    _call(client, sid, "local-mem-2", "memory.remember")
    _utter(client, sid, "Kahve hakkında ne biliyorsun", turn=2)

    body = _call(client, sid, "local-mem-3", "memory.search")

    assert body["status"] == "succeeded", body
    assert "şekersiz" in str(body["result"].get("speech") or ""), body["result"]
