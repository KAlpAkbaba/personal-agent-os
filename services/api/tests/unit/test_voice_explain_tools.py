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
    "Efendim, son araştırma motoru qualification'ı başarıyla tamamlandı. "
    "Beş sonuç ve beş farklı kaynak ürettim. "
    "Dokuz konu dışı sayfayı, on bir ara doğrulama sayfasını, üç tarihi doğrulanamayan "
    "sonucu, bir tekrar eden olayı ve dört tarih dışı sonucu eledim. "
    "Tarayıcı temiz şekilde kapandı. "
    "Research Engine artık gerçek ortamda doğrulanmış durumda. Bilginize."
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
    assert result["level"] == "executive" and result["facts"] >= 5
    assert result["uncertainties"] == 0
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
    assert "Elenen sayfalar" in detail["speech"]

    # 6-7. "Teknik anlat" → technical evidence
    technical = _control(client, sid, "x3", "Teknik anlat")
    assert technical["intent"]["intent"] == "technical"
    assert technical["narration"]["presentation"] == "technical"
    assert "Görev kimliği" in technical["speech"]
    # versions are spoken, not spelled: "0.4.0" arrives as words
    assert "sıfır nokta dört nokta sıfır" in technical["speech"]
    assert "dağıtım yapılmadı" in technical["speech"]

    # back to the summary, then the owner interrupts it
    summary = _control(client, sid, "x4", "Özetle")
    assert summary["speech"].startswith("Efendim, son araştırma motoru")

    # 8-9. "Dur." — the client stops playback first, then reports what was spoken so far
    spoken_so_far = (
        "Efendim, son araştırma motoru qualification'ı başarıyla tamamlandı. "
        "Beş sonuç ve beş farklı kaynak ürettim. Dokuz konu dışı"
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
    assert resumed["speech"].startswith("Dokuz konu dışı sayfayı, on bir ara doğrulama")
    assert resumed["speech"].endswith("Bilginize.")


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
        assert version.canonical_body.startswith("# Özet\n\nEfendim, son araştırma motoru")
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
    head = "Efendim, son araştırma motoru qualification'ı başarıyla tamamlandı. Beş sonuç ve"
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
    assert explain["level"] == "executive" and explain["speech_chars"] > 100
    assert explain["speech_head"].startswith("Efendim, son araştırma motoru")
    assert explain["facts"] >= 5 and explain["uncertainties"] == 0
    assert detail["intent"] == "detail" and detail["action"] == "jump_level"
    assert technical["intent"] == "technical"
    assert resume["intent"] == "resume" and resume["narration_state"] == "READING"
    assert resume["speech_head"].startswith("Beş sonuç ve beş farklı kaynak")
    kinds = [e["kind"] for e in activity["client_events"]]
    assert kinds == ["spoken", "barge_in_start", "playback_stopped"]
    spoken = activity["client_events"][0]
    assert spoken["aligned"] == 1 and spoken["action"] == "paused" and spoken["spoken_chunks"] == 1
    assert "text" not in spoken and spoken["chars"] == len(head)
    assert [i["intent"] for i in activity["intents"]] == ["stop"]
    assert activity["barge_in_count"] == 1
    assert activity["narration"]["state"] == "READING"
