"""The owner's M16 acceptance script, headless (docs/M16_ACTIVITY_LEDGER_SPEC.md §5).

Over a simulated realtime session and the real control plane: "Son yaptıklarını anlat"
→ the briefing is spoken from evidence; "Araştırmayı detaylandır" → findings;
"Teknik anlat" → technical evidence; the owner interrupts ("dur" = barge-in with the
transcript spoken so far) → the cursor lands on the first unspoken sentence; "Devam et"
→ speech resumes exactly there. Also the item commands: ikinci madde, önceki maddeyi
açıkla (explain-then-return), bunu atla, özetle.

The evidence is the real 2026-09-04 research run (see test_explain_engine); the voice
path is the real one (tool relay, events, narration rows). Nothing is seeded that did not
happen, and the transcript is never written to any audit row.
"""

# ruff: noqa: F811 - the shared `wired` fixture is imported and then named as a parameter
from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.explain import service as explain_service
from app.narration.models import NarrationSession
from app.voice.realtime_sessions import service
from app.voice.realtime_sessions.models import RealtimeSessionRow
from tests.unit.test_explain_engine import (
    NOW,
    REAL_REPORT,
    MemorySource,
    _qualified,
    _research_completed,
)
from tests.unit.test_voice_realtime_sessions import _audit_rows, _create, wired  # noqa: F401

OWNER_SENTENCE = (
    "Efendim, Research Engine gerçek ortam doğrulamasını başarıyla geçti. "
    "Beş farklı kaynaktan beş sonuç üretti ve yirmi sekiz uygun olmayan sayfayı eledi. "
    "Tarayıcı temiz kapandı; şu anda müdahalenizi gerektiren bir sorun yok."
)


def _use_real_run_evidence(monkeypatch) -> None:
    source = MemorySource([_research_completed(), _qualified()], REAL_REPORT)
    monkeypatch.setattr(explain_service, "evidence_source_factory", lambda db: source)


def _tool(client, session_id: str, call_id: str, name: str, **arguments):
    r = client.post(
        f"/v1/voice/realtime/sessions/{session_id}/tool-calls",
        json={"call_id": call_id, "name": name, "arguments": arguments},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "succeeded", body
    return body["result"]


def _control(client, session_id: str, call_id: str, utterance: str):
    return _tool(client, session_id, call_id, "narration.control", utterance=utterance)


def _events(client, session_id: str, events: list[dict]):
    r = client.post(f"/v1/voice/realtime/sessions/{session_id}/events", json={"events": events})
    assert r.status_code == 200, r.text
    return r.json()


def _narration_row(engine, narration_id: str) -> NarrationSession:
    with Session(engine) as db:
        row = db.get(NarrationSession, uuid.UUID(narration_id))
        db.expunge(row)
        return row


# ------------------------------------------------------------------ the script


def test_owner_acceptance_script_end_to_end(wired, monkeypatch) -> None:
    client, _identity, runtime, sideband, _issued, engine = wired
    _use_real_run_evidence(monkeypatch)
    created = _create(client)
    sid = created["session_id"]
    assert any(t["name"] == "activity.explain" for t in created["tools"])
    assert "activity.explain" in created["instructions"]

    # 1-3. "Son yaptıklarını anlat" → the briefing, spoken verbatim from evidence
    result = _tool(client, sid, "x1", "activity.explain", question="Son yaptıklarını anlat")
    assert result["speech"] == OWNER_SENTENCE
    assert len(result["speech"]) <= 420  # a listening budget, not a document
    assert result["level"] == "executive" and result["facts"] >= 2
    assert result["uncertainties"] == 0
    assert result["intent"]["intent"] == "explain"
    narration_id = result["narration_session_id"]
    assert narration_id
    assert sideband.events()[-1] == "narration_cursor"
    with runtime.session() as db:
        row = db.get(RealtimeSessionRow, uuid.UUID(sid))
        assert str(row.narration_session_id) == narration_id
        assert row.context_json["presentation"] == "summary"
    state = client.get(f"/v1/voice/realtime/sessions/{sid}").json()
    assert state["narration"]["state"] == "READING"

    # 4-5. "Araştırmayı detaylandır" → the actual findings, from the report
    detail = _control(client, sid, "x2", "Araştırmayı detaylandır")
    assert detail["intent"]["intent"] == "detail"
    assert detail["narration"]["action"] == "jump_level"
    assert detail["speech"].startswith("Yapay zekâ ajanları ve ajan temelli yapay zekâ.")
    assert "Bilim ve Gelecek" in detail["speech"]
    assert len(detail["speech"]) <= 900  # 30-60 s, cut at a sentence boundary

    # 6-7. "Teknik anlat" → technical evidence
    technical = _control(client, sid, "x3", "Teknik anlat")
    assert technical["intent"]["intent"] == "technical"
    assert technical["narration"]["presentation"] == "technical"
    # concise: versions, evidence, architecture; no identifiers unless something failed
    assert "Research policy" in technical["speech"]
    assert "sıfır nokta dört nokta sıfır" in technical["speech"]  # 0.4.0, spoken
    assert "deployment gerekmedi" in technical["speech"]
    assert "Görev kimliği" not in technical["speech"]
    assert len(technical["speech"]) <= 700

    # back to the summary, then the owner interrupts it
    summary = _control(client, sid, "x4", "Özetle")
    assert summary["speech"].startswith("Efendim, Research Engine gerçek ortam")

    # 8-9. "Dur." — the client stops playback first, then reports what was spoken so far
    spoken_so_far = (
        "Efendim, Research Engine gerçek ortam doğrulamasını başarıyla geçti. "
        "Beş farklı kaynaktan beş sonuç üretti ve yirmi sekiz uygun olmayan sayfayı eledi. "
        "Tarayıcı temiz"
    )
    response = _events(
        client,
        sid,
        [
            {
                "kind": "spoken",
                "t_ms": 5000,
                "turn": 1,
                "text": spoken_so_far,
                "payload": {"final": 0, "response_seq": 3, "chars": len(spoken_so_far)},
            },
            {
                "kind": "barge_in_start",
                "t_ms": 5001,
                "turn": 2,
                "payload": {"playback_stopped_ms": 60},
            },
            {"kind": "playback_stopped", "t_ms": 5061, "turn": 2, "payload": {}},
            {"kind": "utterance", "t_ms": 5300, "turn": 2, "text": "dur"},
        ],
    )
    assert response["accepted"] == 4
    assert response["resolved_intents"][-1]["intent"] == "stop"
    # the device-bound session receives the cursor over the sideband (delivered here);
    # a web client without a device socket gets the same frame in pending_sideband
    cursor_frames = [
        frame for _device, frame in sideband.frames if frame["event"] == "narration_cursor"
    ] + [f for f in response["pending_sideband"] if f["event"] == "narration_cursor"]
    paused = _narration_row(engine, narration_id)
    assert paused.state == "PAUSED"
    assert cursor_frames and cursor_frames[-1]["payload"]["action"] == "paused"
    assert cursor_frames[-1]["payload"]["spoken_chunks"] == 2

    # 10-11. "Devam et." — resumes at the first sentence that was not fully spoken
    resumed = _control(client, sid, "x5", "Devam et")
    assert resumed["intent"]["intent"] == "resume"
    assert resumed["narration"]["narration_state"] == "READING"
    assert resumed["speech"].startswith("Tarayıcı temiz kapandı; şu anda müdahalenizi")
    assert resumed["speech"].endswith("bir sorun yok.")


def test_item_commands_explain_previous_returns_and_skip_moves_on(wired, monkeypatch) -> None:
    client, _identity, _runtime, _sideband, _issued, _engine = wired
    _use_real_run_evidence(monkeypatch)
    sid = _create(client)["session_id"]
    _tool(client, sid, "e1", "activity.explain", question="Araştırmayı detaylandır")

    second = _control(client, sid, "e2", "İkinci madde")
    assert second["narration"]["action"] == "jump_item"
    assert second["speech"].startswith("Kamuda yapay zeka dönemi.")

    explained = _control(client, sid, "e3", "Önceki maddeyi açıkla")
    assert explained["intent"]["intent"] == "explain_previous"
    assert explained["narration"]["narration_state"] == "EXPLAINING"
    assert explained["speech"].startswith("Yapay zekâ ajanları ve ajan temelli yapay zekâ.")
    assert "Kamuda" not in explained["speech"]  # only the explained item is read

    back = _control(client, sid, "e4", "Devam et")
    assert back["narration"]["narration_state"] == "READING"
    assert back["speech"].startswith("Kamuda yapay zeka dönemi.")  # the exact saved cursor

    skipped = _control(client, sid, "e5", "Bunu atla")
    assert skipped["narration"]["action"] == "skipped"
    assert skipped["speech"].startswith("Elenen sayfalar.")

    everything = _control(client, sid, "e6b", "Hepsini oku")
    assert everything["intent"]["intent"] == "full"
    assert everything["narration"]["action"] == "read_all"
    assert everything["speech"].startswith("Efendim, Research Engine")
    assert "Elenen sayfalar" in everything["speech"] and "Mimari" in everything["speech"]

    stopped = _control(client, sid, "e6", "Dur")
    assert stopped["narration"]["action"] == "paused" and stopped["speech"] == ""


def test_explain_with_no_evidence_says_so_and_still_attaches(wired, monkeypatch) -> None:
    client, _identity, _runtime, _sideband, _issued, _engine = wired
    monkeypatch.setattr(explain_service, "evidence_source_factory", lambda db: MemorySource([]))
    sid = _create(client)["session_id"]
    result = _tool(client, sid, "n1", "activity.explain", question="Bugün neler yaptın")
    assert result["speech"] == "Bu konuda kayıt bulamadım."
    assert result["uncertainties"] == 1 and result["facts"] == 0
    assert result["evidence_count"] == 0


def test_spoken_transcript_is_never_audited(wired, monkeypatch) -> None:
    client, _identity, runtime, _sideband, _issued, _engine = wired
    _use_real_run_evidence(monkeypatch)
    sid = _create(client)["session_id"]
    _tool(client, sid, "a1", "activity.explain", question="Son yaptıklarını anlat")
    secret_shaped = "Efendim, son araştırma motoru qualification'ı başarıyla tamamlandı."
    _events(
        client,
        sid,
        [
            {
                "kind": "spoken",
                "t_ms": 100,
                "turn": 1,
                "text": secret_shaped,
                "payload": {"final": 1, "response_seq": 1, "chars": len(secret_shaped)},
            },
        ],
    )
    rows = _audit_rows(runtime, sid, service.ACTION_CLIENT_EVENT)
    spoken_rows = [r for r in rows if r.metadata_json.get("kind") == "spoken"]
    assert spoken_rows, "the spoken event must be audited as an event"
    for r in spoken_rows:
        flat = str(r.metadata_json)
        assert "qualification" not in flat and "text" not in r.metadata_json
        assert r.metadata_json["chars"] == len(secret_shaped)
        assert r.metadata_json["aligned"] == 1


def test_explain_query_without_narration_attached_resolves_to_explain(wired) -> None:
    client, _identity, _runtime, _sideband, _issued, _engine = wired
    sid = _create(client)["session_id"]
    r = client.post(
        f"/v1/voice/realtime/sessions/{sid}/events",
        json={
            "events": [
                {"kind": "utterance", "t_ms": 10, "turn": 1, "text": "Son yaptıklarını anlat"},
                {"kind": "utterance", "t_ms": 20, "turn": 2, "text": "Araştırmayı detaylandır"},
            ]
        },
    ).json()
    kinds = [(i["intent"], i.get("query_kind")) for i in r["resolved_intents"]]
    assert kinds == [("explain", "last_activity"), ("explain", "research_detail")]


def test_activity_explain_persists_a_briefing_artifact(wired, monkeypatch) -> None:
    from app.artifacts.models import Artifact, ArtifactVersion

    client, _identity, _runtime, _sideband, _issued, engine = wired
    _use_real_run_evidence(monkeypatch)
    sid = _create(client)["session_id"]
    result = _tool(client, sid, "p1", "activity.explain", question="Son yaptıklarını anlat")
    with Session(engine) as db:
        artifact = db.get(Artifact, uuid.UUID(result["artifact_id"]))
        assert artifact.kind == "activity_briefing" and artifact.state == "READY"
        assert artifact.executive_summary == OWNER_SENTENCE
        version = db.query(ArtifactVersion).filter_by(artifact_id=artifact.id).one()
        assert version.canonical_body.startswith("# Özet\n\nEfendim, Research Engine")
        assert "# Kanıt" in version.canonical_body
        assert version.source_manifest_json["generated_at"].startswith(str(NOW.year))


def test_session_activity_is_the_durable_record_of_the_script(wired, monkeypatch) -> None:
    """What the owner script asserts after the real session: every step from rows."""
    client, _identity, _runtime, _sideband, _issued, _engine = wired
    _use_real_run_evidence(monkeypatch)
    sid = _create(client)["session_id"]
    _tool(client, sid, "s1", "activity.explain", question="Son yaptıklarını anlat")
    _control(client, sid, "s2", "Araştırmayı detaylandır")
    _control(client, sid, "s3", "Teknik anlat")
    _control(client, sid, "s4", "Özetle")
    head = "Efendim, Research Engine gerçek ortam doğrulamasını başarıyla geçti. Beş farklı"
    _events(
        client,
        sid,
        [
            {
                "kind": "spoken",
                "t_ms": 900,
                "turn": 1,
                "text": head,
                "payload": {"final": 0, "response_seq": 4, "chars": len(head)},
            },
            {
                "kind": "barge_in_start",
                "t_ms": 901,
                "turn": 2,
                "payload": {"playback_stopped_ms": 40},
            },
            {"kind": "playback_stopped", "t_ms": 941, "turn": 2, "payload": {}},
            {"kind": "utterance", "t_ms": 1200, "turn": 2, "text": "dur"},
        ],
    )
    _control(client, sid, "s5", "Devam et")

    activity = client.get(f"/v1/voice/realtime/sessions/{sid}/activity").json()
    names = [(c["name"], c["status"]) for c in activity["tool_calls"]]
    assert names == [("activity.explain", "succeeded")] + [("narration.control", "succeeded")] * 4
    explain, detail, technical, _summary, resume = activity["tool_calls"]
    assert explain["level"] == "executive" and 100 < explain["speech_chars"] <= 420
    assert explain["speech_head"].startswith("Efendim, Research Engine gerçek ortam")
    assert explain["facts"] >= 5 and explain["uncertainties"] == 0
    assert detail["intent"] == "detail" and detail["action"] == "jump_level"
    assert technical["intent"] == "technical"
    assert resume["intent"] == "resume" and resume["narration_state"] == "READING"
    assert resume["speech_head"].startswith("Beş farklı kaynaktan beş sonuç üretti")
    kinds = [e["kind"] for e in activity["client_events"]]
    assert kinds == ["spoken", "barge_in_start", "playback_stopped"]
    spoken = activity["client_events"][0]
    assert spoken["aligned"] == 1 and spoken["action"] == "paused" and spoken["spoken_chunks"] == 1
    assert "text" not in spoken and spoken["chars"] == len(head)
    assert [i["intent"] for i in activity["intents"]] == ["stop"]
    assert activity["barge_in_count"] == 1
    assert activity["narration"]["state"] == "READING"

    # and the ledger holds the explanation, the pause and the resume as evidence
    from app.ledger import service as ledger_service

    with Session(_engine) as db:
        types = [e.event_type for e in ledger_service.query(db, subsystems=("voice",), limit=50)]
    assert "voice.explained" in types
    assert "voice.narration.paused" in types
    assert "voice.narration.resumed" in types


# ---------------------------------------- "Dur" is idempotent; "Devam et" resumes


def test_dur_when_nothing_is_being_read_is_a_quiet_state_change(wired, monkeypatch) -> None:
    """Owner observation 2026-09-04: "Dur" after speech had already finished must not become
    an error. Server side, the narration machine's DUR always wins and never errors: a
    second "dur" on a paused narration is paused again, with nothing to say."""
    client, _identity, _runtime, _sideband, _issued, _engine = wired
    _use_real_run_evidence(monkeypatch)
    sid = _create(client)["session_id"]
    _tool(client, sid, "d1", "activity.explain", question="Son yaptıklarını anlat")
    first = _control(client, sid, "d2", "Dur")
    assert first["narration"]["action"] == "paused" and first["speech"] == ""
    second = _control(client, sid, "d3", "Dur")
    assert second["narration"]["action"] == "paused" and second["narration"]["ok"] is True
    assert second["speech"] == ""
    # an intent-only resolution (no narration attached) is just as quiet
    fresh = _create(client)["session_id"]
    r = client.post(
        f"/v1/voice/realtime/sessions/{fresh}/events",
        json={"events": [{"kind": "utterance", "t_ms": 5, "turn": 1, "text": "dur"}]},
    )
    assert r.status_code == 200 and r.json()["resolved_intents"][0]["intent"] == "stop"


def test_dur_after_speech_completed_then_devam_continues_with_the_next_section(
    wired, monkeypatch
) -> None:
    """The response finished (spoken final=1, the whole section read) and only then the
    owner says "dur": the cursor is already past the section; "devam et" goes on to the
    next section rather than repeating what was heard."""
    client, _identity, _runtime, _sideband, _issued, _engine = wired
    _use_real_run_evidence(monkeypatch)
    sid = _create(client)["session_id"]
    result = _tool(client, sid, "c1", "activity.explain", question="Son yaptıklarını anlat")
    whole = result["speech"]
    _events(
        client,
        sid,
        [
            {
                "kind": "spoken",
                "t_ms": 9000,
                "turn": 1,
                "text": whole,
                "payload": {"final": 1, "response_seq": 1, "chars": len(whole)},
            },
            {"kind": "utterance", "t_ms": 9500, "turn": 2, "text": "dur"},
        ],
    )
    stopped = _control(client, sid, "c2", "Dur")
    assert stopped["narration"]["action"] == "paused" and stopped["speech"] == ""
    resumed = _control(client, sid, "c3", "Devam et")
    assert resumed["narration"]["narration_state"] == "READING"
    assert not resumed["speech"].startswith("Efendim")
    assert resumed["speech"].startswith("Yapay zekâ ajanları ve ajan temelli yapay zekâ.")


def test_level_words_routed_to_activity_explain_move_within_the_briefing(
    wired, monkeypatch
) -> None:
    """The owner's evidence showed the provider routing "detaylandır" / "teknik anlat" to
    activity.explain instead of narration.control. With a briefing attached those are
    moves through it: the same cursor jump, and the durable record carries the normalised
    intent regardless of which tool the provider chose."""
    client, _identity, _runtime, _sideband, _issued, _engine = wired
    _use_real_run_evidence(monkeypatch)
    sid = _create(client)["session_id"]
    _tool(client, sid, "r1", "activity.explain", question="Son yaptıklarını anlat")
    for call_id, phrase, intent in (
        ("r2", "detaylandır", "detail"),
        ("r3", "teknik detaya gir", "technical"),
        ("r4", "kod seviyesinde anlat", "technical"),
        ("r5", "özetle", "summarize"),
        ("r6", "daha detaylı anlat", "detail"),
    ):
        moved = _tool(client, sid, call_id, "activity.explain", question=phrase)
        assert moved["routed"] == "narration", phrase
        assert moved["intent"]["intent"] == intent, phrase
        assert moved["narration"]["action"] == "jump_level", phrase
        assert moved["speech"], phrase
    activity = client.get(f"/v1/voice/realtime/sessions/{sid}/activity").json()
    recorded = [(c["name"], c["intent"]) for c in activity["tool_calls"][1:]]
    assert recorded == [
        ("activity.explain", "detail"),
        ("activity.explain", "technical"),
        ("activity.explain", "technical"),
        ("activity.explain", "summarize"),
        ("activity.explain", "detail"),
    ]


def test_two_interruptions_in_a_row_each_resume_from_their_own_point(wired, monkeypatch) -> None:
    client, _identity, _runtime, _sideband, _issued, _engine = wired
    _use_real_run_evidence(monkeypatch)
    sid = _create(client)["session_id"]
    _tool(client, sid, "i1", "activity.explain", question="Son yaptıklarını anlat")
    first_cut = "Efendim, Research Engine gerçek ortam doğrulamasını başarıyla geçti. Beş"
    _events(
        client,
        sid,
        [
            {
                "kind": "spoken",
                "t_ms": 1000,
                "turn": 1,
                "text": first_cut,
                "payload": {"final": 0, "response_seq": 1, "chars": len(first_cut)},
            },
            {
                "kind": "barge_in_start",
                "t_ms": 1001,
                "turn": 2,
                "payload": {"playback_stopped_ms": 50},
            },
            {"kind": "playback_stopped", "t_ms": 1051, "turn": 2, "payload": {}},
        ],
    )
    resumed = _control(client, sid, "i2", "Devam et")
    assert resumed["speech"].startswith("Beş farklı kaynaktan beş sonuç üretti")
    second_cut = (
        "Beş farklı kaynaktan beş sonuç üretti ve yirmi sekiz uygun olmayan sayfayı eledi. "
        "Tarayıcı temiz"
    )
    _events(
        client,
        sid,
        [
            {
                "kind": "spoken",
                "t_ms": 3000,
                "turn": 3,
                "text": second_cut,
                "payload": {"final": 0, "response_seq": 2, "chars": len(second_cut)},
            },
            {
                "kind": "barge_in_start",
                "t_ms": 3001,
                "turn": 4,
                "payload": {"playback_stopped_ms": 50},
            },
            {"kind": "playback_stopped", "t_ms": 3051, "turn": 4, "payload": {}},
        ],
    )
    again = _control(client, sid, "i3", "Devam et")
    assert again["speech"].startswith("Tarayıcı temiz kapandı; şu anda müdahalenizi")
