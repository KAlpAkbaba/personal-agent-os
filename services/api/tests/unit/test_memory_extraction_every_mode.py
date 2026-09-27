"""ADR-0201: the owner's own sentence feeds the memory write policy in EVERY mode.

Until this change the only text that ever reached `extract_from_summary` was a paid
realtime session's `summary` event (B16). The local mode never sends one - there is no
model to write it - and neither does an operator turn or a research turn. So sixteen
days of the owner talking to the local mode taught the memory nothing he did not
prefix with "bunu hatırla". These tests go through the REAL relay (`/events` utterance
-> `record_client_events` -> the write policy), the way the browser does it, and pin:

* a sentence with a preference signal becomes a CANDIDATE (never durable from one
  sentence, never explicit) - in the local mode, where nothing else could have filed it;
* a plain command is chatty for the policy and writes nothing (a hundred "yukarı bas"
  do not become memories);
* "bunu hatırla ..." is NOT extracted: the tool files it explicitly, and a candidate
  copy beside it would be the ladder corroborating an owner memory with itself;
* a credential in a sentence is refused by the secrets guard and audited WITHOUT content;
* the same sentence twice in one session is one observation, not evidence for itself;
* the source names the channel (local / voice) so a retrieval can say where it heard it;
* the event's audit metadata carries counts only - never the words.
"""

# ruff: noqa: F811 - the shared `wired` fixture is imported and then named as a parameter
from __future__ import annotations

import pytest
from sqlalchemy import select

from app.memory.extraction import SOURCE_KIND_UTTERANCE
from app.memory.models import Memory, MemoryAuditEvent
from app.memory.types import WriteStage
from app.voice.providers_local_router import LocalRouterRealtimeProvider
from tests.unit.test_local_mode_memory_relay import _call, _utter
from tests.unit.test_local_mode_memory_relay import wired as _memory_wired  # noqa: F401
from tests.unit.test_voice_realtime_sessions import _audit_rows, _create
from tests.unit.test_voice_realtime_sessions import wired as _wired  # noqa: F401 - its base


@pytest.fixture()
def wired(_memory_wired):
    """The memory-wired relay, plus the local-router provider (ADR-0173) so a session may
    be created with ``transport="text"`` - the local mode this change is about."""
    client, runtime, *rest = _memory_wired
    local = LocalRouterRealtimeProvider()
    runtime.providers[local.name] = local
    yield (client, runtime, *rest)


def _memories(runtime) -> list[Memory]:
    with runtime.session() as db:
        return list(db.execute(select(Memory).order_by(Memory.created_at)).scalars())


def test_a_preference_said_in_the_local_mode_becomes_a_candidate(wired) -> None:
    client, runtime, *_ = wired
    sid = _create(client, transport="text")["session_id"]

    _utter(client, sid, "Bundan sonra araştırma raporlarını her zaman Türkçe oku.")

    rows = _memories(runtime)
    assert len(rows) == 1, [r.text for r in rows]
    row = rows[0]
    assert "Türkçe oku" in row.text
    assert row.explicit is False, "a sentence is never an explicit owner memory (M5 review #4)"
    assert row.stage == WriteStage.CANDIDATE.value
    assert row.confidence <= 0.4
    source = (row.provenance_json or {}).get("source") or {}
    assert source.get("kind") == SOURCE_KIND_UTTERANCE
    assert source.get("channel") == "local"
    assert source.get("session_id") == sid


def test_a_plain_command_teaches_the_memory_nothing(wired) -> None:
    client, runtime, *_ = wired
    sid = _create(client, transport="text")["session_id"]

    for turn, text in enumerate(("Yukarı tuşuna bas.", "Sekmeyi kapat.", "Tamam."), start=1):
        _utter(client, sid, text, turn=turn)

    assert _memories(runtime) == []


def test_bunu_hatirla_is_filed_once_by_the_tool_and_not_again_by_the_extractor(wired) -> None:
    client, runtime, *_ = wired
    sid = _create(client, transport="text")["session_id"]

    _utter(client, sid, "Bunu hatırla: kahveyi şekersiz içiyorum")
    assert _memories(runtime) == [], "the utterance alone files nothing"
    body = _call(client, sid, "local-mem-x", "memory.remember")
    assert body["status"] == "succeeded", body

    rows = _memories(runtime)
    assert len(rows) == 1, [(r.text, r.explicit) for r in rows]
    assert rows[0].explicit is True


def test_a_secret_in_a_sentence_is_refused_and_counted_without_its_content(wired) -> None:
    client, runtime, *_ = wired
    sid = _create(client, transport="text")["session_id"]
    secret = "sk-" + "a1b2c3d4e5f6g7h8" * 2

    _utter(client, sid, f"Her zaman şu anahtarı kullan: {secret}")

    assert _memories(runtime) == []
    rows = _audit_rows(runtime, sid)
    metas = [
        (r.metadata_json or {}).get("memory_extraction")
        for r in rows
        if (r.metadata_json or {}).get("memory_extraction")
    ]
    assert metas and metas[0].get("refused") == 1, metas
    with runtime.session() as db:
        memory_audits = list(db.execute(select(MemoryAuditEvent)).scalars())
    dumped = " ".join(repr(r.__dict__) for r in rows) + " ".join(
        repr(a.__dict__) for a in memory_audits
    )
    assert secret not in dumped, "the secret never reaches any audit row"


def test_the_same_sentence_twice_is_one_observation(wired) -> None:
    client, runtime, *_ = wired
    sid = _create(client, transport="text")["session_id"]
    sentence = "Sabahları her zaman kahve içerim."

    _utter(client, sid, sentence, turn=1)
    _utter(client, sid, sentence, turn=2)

    rows = _memories(runtime)
    assert len(rows) == 1
    assert rows[0].evidence_count <= 1, "a repeat in one session is not corroboration"


def test_the_paid_session_names_its_channel_as_voice(wired) -> None:
    client, runtime, *_ = wired
    sid = _create(client)["session_id"]

    _utter(client, sid, "Ben her zaman sabah altıda kalkarım.")

    rows = _memories(runtime)
    assert len(rows) == 1, [r.text for r in rows]
    assert ((rows[0].provenance_json or {}).get("source") or {}).get("channel") == "voice"


def test_the_audit_metadata_carries_counts_and_never_the_words(wired) -> None:
    client, runtime, *_ = wired
    sid = _create(client, transport="text")["session_id"]
    sentence = "Bundan sonra toplantı notlarını her zaman Word yap."

    _utter(client, sid, sentence)

    rows = _audit_rows(runtime, sid)
    metas = [
        r.metadata_json or {} for r in rows if (r.metadata_json or {}).get("memory_extraction")
    ]
    assert metas, "the utterance event says extraction ran"
    assert metas[0]["memory_extraction"].get("written") == 1
    assert sentence not in " ".join(repr(m) for m in metas)
