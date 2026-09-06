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
    REAL_STATS,
    TASK_ID,
    MemorySource,
    _qualified,
    _research_completed,
)
from tests.unit.test_voice_realtime_sessions import _audit_rows, _create, wired  # noqa: F401

#: M18.2 DEFECT 2 (ADR-0067): kept in sync with test_explain_engine.OWNER_SENTENCE by
#: hand (this file duplicates it rather than importing, matching how it already
#: existed before this change) — the executive briefing speaks the report's findings,
#: never the pipeline's counts.
OWNER_SENTENCE = (
    "Efendim, Research Engine gerçek ortam doğrulamasını başarıyla geçti. "
    "Araştırmayı tamamladım. Birincisi, Yapay zekâ ajanları ve ajan temelli yapay zekâ. "
    "Bu önemli çünkü Ajan kavramının Türkçe kamuoyunda tanımlanması. İkincisi, Kamuda "
    "yapay zeka dönemi. Bu önemli çünkü Kamu kullanımı ajan taleplerini büyütür. "
    "İstersen diğer bulguları veya kaynakları da anlatabilirim. "
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
    # M18.2 DEFECT 2 (ADR-0067): the richer findings-based outcome (OWNER_SENTENCE, 438
    # chars) no longer fits the 420-char executive listening budget
    # (app.voice.intents.SPEECH_BUDGET_CHARS) in one turn — this first turn speaks
    # every sentence except the closing one, which "devam et" reads next, exactly like
    # any other budget-capped chunk (app.voice.intents.speech_from).
    result = _tool(client, sid, "x1", "activity.explain", question="Son yaptıklarını anlat")
    assert result["speech"] == OWNER_SENTENCE.rsplit(" Tarayıcı", 1)[0]
    assert len(result["speech"]) <= 420  # the executive listening budget itself
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

    # 8-9. "Dur." — the client stops playback first, then reports what was spoken so far.
    # A real playback can only ever report having spoken THIS chunk (the 420-char-budgeted
    # text "Özetle" actually returned above) — never text beyond it, since that is all the
    # provider was ever given to say. Cut mid-sentence, as the owner's real interruptions are.
    spoken_so_far = (
        "Efendim, Research Engine gerçek ortam doğrulamasını başarıyla geçti. "
        "Araştırmayı tamamladım. Birincisi, Yapay"
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
    assert resumed["speech"].startswith("Birincisi, Yapay zekâ ajanları")
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

    # M18.2 DEFECT 2 (ADR-0067): REAL_REPORT has two findings and "Elenen sayfalar" no
    # longer rides along as a third Ayrıntı item, so skipping past the second (and now
    # last) item reaches the end of the Ayrıntı section rather than a further item.
    skipped = _control(client, sid, "e5", "Bunu atla")
    assert skipped["narration"]["action"] == "end_of_document"

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
    head = (
        "Efendim, Research Engine gerçek ortam doğrulamasını başarıyla geçti. "
        "Araştırmayı tamamladım. Birincisi, Yapay"
    )
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
    assert explain["level"] == "executive" and 100 < explain["speech_chars"] <= 480
    assert explain["speech_head"].startswith("Efendim, Research Engine gerçek ortam")
    assert explain["facts"] >= 5 and explain["uncertainties"] == 0
    assert detail["intent"] == "detail" and detail["action"] == "jump_level"
    assert technical["intent"] == "technical"
    assert resume["intent"] == "resume" and resume["narration_state"] == "READING"
    assert resume["speech_head"].startswith("Birincisi, Yapay zekâ ajanları")
    kinds = [e["kind"] for e in activity["client_events"]]
    assert kinds == ["spoken", "barge_in_start", "playback_stopped"]
    spoken = activity["client_events"][0]
    assert spoken["aligned"] == 1 and spoken["action"] == "paused" and spoken["spoken_chunks"] == 2
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
    # M18.2 DEFECT 2 (ADR-0067): the executive Özet no longer fits one 420-char turn (see
    # test_owner_acceptance_script_end_to_end), so "the whole section" is now read across
    # two turns before it is actually finished — reporting each one, in order, as spoken.
    first_chunk = result["speech"]
    _events(
        client,
        sid,
        [
            {
                "kind": "spoken",
                "t_ms": 9000,
                "turn": 1,
                "text": first_chunk,
                "payload": {"final": 1, "response_seq": 1, "chars": len(first_chunk)},
            },
        ],
    )
    continued = _control(client, sid, "c1b", "Devam et")
    assert continued["narration"]["narration_state"] == "READING"
    second_chunk = continued["speech"]
    assert second_chunk.startswith("Tarayıcı temiz kapandı")
    _events(
        client,
        sid,
        [
            {
                "kind": "spoken",
                "t_ms": 9500,
                "turn": 2,
                "text": second_chunk,
                "payload": {"final": 1, "response_seq": 2, "chars": len(second_chunk)},
            },
            {"kind": "utterance", "t_ms": 10000, "turn": 3, "text": "dur"},
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
    first_cut = (
        "Efendim, Research Engine gerçek ortam doğrulamasını başarıyla geçti. "
        "Araştırmayı tamamladım. Birincisi, Yapay"
    )
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
    assert resumed["speech"].startswith("Birincisi, Yapay zekâ ajanları")
    second_cut = (
        "Birincisi, Yapay zekâ ajanları ve ajan temelli yapay zekâ. Bu önemli çünkü Ajan "
        "kavramının Türkçe kamuoyunda tanımlanması. İkincisi, Kamuda"
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
    assert again["speech"].startswith("İkincisi, Kamuda yapay zeka dönemi.")


def test_activity_carries_the_interruption_counters_the_client_reports(wired, monkeypatch) -> None:
    """The two-lane interruption policy's own evidence (owner UX result 2026-09-04): the
    client reports cumulative counters in a mic_metrics state event; the session activity
    record surfaces them so the qualification can assert "it kept speaking over the other
    voice" from rows rather than from memory."""
    client, _identity, _runtime, _sideband, _issued, _engine = wired
    _use_real_run_evidence(monkeypatch)
    sid = _create(client)["session_id"]
    _tool(client, sid, "m1", "activity.explain", question="Son yaptıklarını anlat")
    _events(
        client,
        sid,
        [
            {
                "kind": "state",
                "t_ms": 12_000,
                "turn": 3,
                "payload": {
                    "mic_metrics": 1,
                    "speech_detected": 4,
                    "potential_barge_in": 3,
                    "accepted_owner_interruption": 1,
                    "rejected_background_speech": 2,
                    "explicit_stop_command": 1,
                    "false_interruption": 0,
                    "false_starts": 0,
                    "gate_opens": 4,
                },
            }
        ],
    )
    noise = client.get(f"/v1/voice/realtime/sessions/{sid}/activity").json()["noise"]
    assert noise["reported"] is True
    assert noise["speech_detected"] == 4
    assert noise["potential_barge_in"] == 3
    assert noise["accepted_owner_interruption"] == 1
    assert noise["rejected_background_speech"] == 2
    assert noise["explicit_stop_command"] == 1
    assert noise["false_interruption"] == 0
    # the same counters reach the benchmark record the owner already fetches
    bench = client.get(f"/v1/voice/realtime/sessions/{sid}/benchmark").json()
    assert bench["context"]["noise"]["rejected_background_speech"] == 2


def test_the_briefing_carries_structural_provenance_not_wording(wired, monkeypatch) -> None:
    """Acceptance must rest on structure, never on generated Turkish (owner note,
    2026-09-05: the "briefing derived from the real research run" check matched a
    sentence prefix, so a paraphrase failed a working system). The record names the
    ledger events, the research job, the evidence kinds and the numbers the sentences
    were built from; a checker compares those with the source record."""
    client, _identity, _runtime, _sideband, _issued, _engine = wired
    _use_real_run_evidence(monkeypatch)
    sid = _create(client)["session_id"]
    result = _tool(client, sid, "p1", "activity.explain", question="Son yaptıklarını anlat")

    prov = result["provenance"]
    assert prov["research_job_id"] == TASK_ID
    assert prov["event_ids"] == ["ev-research-1", "ev-qualified-1"] or set(prov["event_ids"]) == {
        "ev-research-1",
        "ev-qualified-1",
    }
    assert {"activity_event", "research_report", "file", "artifact"} <= set(prov["evidence_kinds"])
    assert prov["seeded"] is False
    assert prov["facts"]["findings"] == 5 and prov["facts"]["sources"] == 5
    assert prov["facts"]["rejected"] == 28
    assert prov["facts"]["rejected_by_reason"] == REAL_STATS["rejected_by_reason"]
    assert prov["facts"]["verdict"] == "PASS" and prov["facts"]["qualified"] is True
    assert prov["facts"]["installed_release"] == "0.4.0" and prov["facts"]["deployed"] is False
    assert prov["facts"]["source"].startswith("backfill:") or prov["facts"]["source"] == "live"
    assert "known_fact" in prov["statement_labels"]

    # and the same block reaches the durable activity record the qualification reads
    activity = client.get(f"/v1/voice/realtime/sessions/{sid}/activity").json()
    recorded = activity["tool_calls"][0]["provenance"]
    assert recorded["research_job_id"] == TASK_ID
    assert recorded["facts"]["findings"] == 5


def test_provenance_of_a_briefing_with_no_evidence_claims_nothing(wired, monkeypatch) -> None:
    client, _identity, _runtime, _sideband, _issued, _engine = wired
    monkeypatch.setattr(explain_service, "evidence_source_factory", lambda db: MemorySource([]))
    sid = _create(client)["session_id"]
    result = _tool(client, sid, "p2", "activity.explain", question="Son yaptıklarını anlat")
    prov = result["provenance"]
    assert prov["event_ids"] == [] and prov["facts"] == {}
    assert prov["research_job_id"] is None
    assert prov["statement_labels"] == ["uncertainty"]


def test_the_durable_row_records_which_subsystem_answered(wired, monkeypatch) -> None:
    """The defect behind the owner's failed M17 run, pinned.

    Every tool call in that session recorded ``query_kind=""`` because the field was read
    from the NARRATION intent resolver, which resolves controls like "dur" and returns None
    for a question. Five correct cognitive answers were therefore reported by the harness as
    four subsystems "not reached by voice" - a metadata failure wearing the costume of a
    routing failure (2026-09-05).

    The routing record is written by the engine that dispatched the question, so the durable
    row can say which cognitive path served it without anyone reading Turkish prose.
    """
    client, _identity, _runtime, _sideband, _issued, _engine = wired
    _use_real_run_evidence(monkeypatch)
    sid = _create(client)["session_id"]
    _tool(client, sid, "q1", "activity.explain", question="Son yaptıklarını anlat")
    _tool(client, sid, "q2", "activity.explain", question="Şu anda hangi hedeflerin var?")
    _tool(client, sid, "q3", "activity.explain", question="Bunu canlıya alabilir misin?")

    activity = client.get(f"/v1/voice/realtime/sessions/{sid}/activity").json()
    calls = [c for c in activity["tool_calls"] if c["name"] == "activity.explain"]
    kinds = [c.get("query_kind") for c in calls]
    assert "" not in kinds and None not in kinds, f"query_kind was not persisted: {kinds}"
    assert "goals" in kinds, kinds
    assert "can_deploy" in kinds, kinds

    by_kind = {c["query_kind"]: c for c in calls}
    assert by_kind["goals"]["subsystem"] == "goals"
    assert by_kind["can_deploy"]["subsystem"] == "authority"
    # The row carries the routing fields themselves, which is what the harness reads. The
    # evidence behind the authority answer is asserted against PRODUCTION rather than here:
    # this fixture substitutes a stub evidence source with no authority_policy, so there is
    # genuinely nothing for that branch to cite, and an assertion to the contrary would be
    # testing the fixture.
    for call in calls:
        assert "subsystem" in call and "evidence_kinds" in call and "entity_ids" in call
