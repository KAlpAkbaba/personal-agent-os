"""B31 req 192, 199, 201, 207, 209 through the real relay and the real tools.

- 192: a DEEP research asked for aloud is started with the DEEP policy's own source
  budget, not the QUICK default the voice path used to send for every mode.
- 199: an earlier completed research on the same topic is cited in the answer.
- 201: "Bir önceki araştırmayı aç" reaches a research tool, never the artifact family.
- 207: a synthesis substitution is spoken as one at the technical level.
- 209: "Bundan sonra teknik anlat" is a standing register the next answers read.
"""

# ruff: noqa: F811 - the fixtures are imported from the suite that owns them
from __future__ import annotations

from app.research import focus as focus_module
from app.research.answers import technical_speech
from app.research.policy import MODE_DEEP, MODE_QUICK, resolve_policy
from app.voice.intents import Intent, resolve_intent
from app.voice.realtime_sessions.tools import _voice_max_sources
from tests.unit.test_voice_research_followup import (
    REPORT_JSON,
    _complete_a_research,
    _create,
    _enroll_online_device,
    _patched_temporal_client,
    _say,
    _tool,
    wired,  # noqa: F401 - the fixture
)

# ------------------------------------------------------------------------- 192


def test_a_deep_research_asked_aloud_gets_the_deep_source_budget(wired) -> None:
    client, runtime, _sideband, _broker, _artifacts = wired
    _enroll_online_device(_broker)
    sid = _create(client)
    _say(client, sid, "Yapay zekâ düzenlemelerini kapsamlı araştır.")
    with _patched_temporal_client() as connect:
        started = _tool(
            client, sid, "start-deep", "research.start", {"topic": "yapay zekâ düzenlemeleri"}
        )
    assert started["status"] == "running", started
    assert started["result"]["mode"] == MODE_DEEP
    deep_max = resolve_policy(MODE_DEEP).max_sources
    assert started["result"]["max_sources"] == deep_max
    assert deep_max > 12, "the DEEP policy's budget is what the 12-source cap was hiding"
    request = connect.return_value.start_workflow.await_args.args[1]
    assert request.mode == MODE_DEEP
    assert request.max_sources == deep_max


def test_the_model_may_narrow_the_mode_but_never_widen_it(wired) -> None:
    client, _runtime, _sideband, _broker, _artifacts = wired
    _enroll_online_device(_broker)
    sid = _create(client)
    with _patched_temporal_client():
        widened = _tool(
            client, sid, "start-w", "research.start", {"topic": "hava durumu", "mode": "deep"}
        )
    assert widened["result"]["mode"] == MODE_QUICK, "a wider mode is the owner's word only"
    assert widened["result"]["max_sources"] == _voice_max_sources(MODE_QUICK, 12)
    sid2 = _create(client)
    with _patched_temporal_client():
        narrowed = _tool(
            client,
            sid2,
            "start-n",
            "research.start",
            {"topic": "yapay zekâ düzenlemeleri kapsamlı", "mode": "quick"},
        )
    assert narrowed["result"]["mode"] == MODE_QUICK


def test_voice_max_sources_is_the_policys_for_every_mode() -> None:
    assert _voice_max_sources(MODE_DEEP, 12) == resolve_policy(MODE_DEEP).max_sources
    assert _voice_max_sources("standard", 12) == resolve_policy("standard").max_sources
    assert _voice_max_sources(MODE_QUICK, 12) == min(12, resolve_policy(MODE_QUICK).max_sources)
    assert _voice_max_sources(MODE_QUICK, 3) == 3


# ------------------------------------------------------------------------- 201


def test_bir_onceki_arastirmayi_ac_opens_the_previous_research_not_an_artifact(wired) -> None:
    client, runtime, _sideband, _broker, _artifacts = wired
    _enroll_online_device(_broker)
    older, older_artifact = _complete_a_research(client, runtime, _artifacts, topic="Eski konu")
    newer, _ = _complete_a_research(client, runtime, _artifacts, topic="Yeni konu")
    assert resolve_intent("Bir önceki araştırmayı aç.").intent is Intent.RESEARCH_OPEN
    sid = _create(client)
    said = _say(client, sid, "Bir önceki araştırmayı aç.")
    assert said["resolved_intents"][0]["intent"] == "research_open"
    call = _tool(client, sid, "open-1", "research.open", {})
    assert call["status"] == "succeeded", call
    body = call["result"]
    assert body["research_job_id"] == older
    assert body["research_artifact_id"] == older_artifact
    # No device on this session: the research is focused and the summary spoken; the
    # speech says the report was NOT opened anywhere.
    assert body["opened"] is False
    assert body["topic"] and body["topic"] in body["speech"]
    assert "açacak bir cihaz yok" in body["speech"]
    with runtime.session() as db:
        assert focus_module.current_focus(db).research_job_id == older
    assert newer != older


def test_opening_with_nothing_completed_is_a_question(wired) -> None:
    client, _runtime, _sideband, _broker, _artifacts = wired
    _enroll_online_device(_broker)
    sid = _create(client)
    _say(client, sid, "Bir önceki araştırmayı aç.")
    call = _tool(client, sid, "open-2", "research.open", {})
    assert call["status"] == "needs_clarification", call


# ------------------------------------------------------------------------- 199


def test_an_earlier_report_on_the_same_topic_is_cited(wired) -> None:
    client, runtime, _sideband, _broker, _artifacts = wired
    _enroll_online_device(_broker)
    first, _ = _complete_a_research(client, runtime, _artifacts, topic="Elektrikli araç pazarı")
    second, _ = _complete_a_research(client, runtime, _artifacts, topic="Elektrikli araç pazarı")
    sid = _create(client)
    _say(client, sid, "Bu araştırmayı anlat.")
    call = _tool(client, sid, "explain-1", "research.explain", {})
    assert call["status"] == "succeeded", call
    body = call["result"]
    assert body["research_job_id"] == second
    assert body["previous_report"]["research_job_id"] == first
    assert "Bu konuda daha önce de bir araştırma var" in body["speech"]


def test_no_earlier_report_means_no_citation(wired) -> None:
    client, runtime, _sideband, _broker, _artifacts = wired
    _enroll_online_device(_broker)
    _complete_a_research(client, runtime, _artifacts, topic="Tek başına bir konu")
    sid = _create(client)
    _say(client, sid, "Bu araştırmayı anlat.")
    body = _tool(client, sid, "explain-2", "research.explain", {})["result"]
    assert "previous_report" not in body
    assert "daha önce de" not in body["speech"]


# ------------------------------------------------------------------------- 209


def test_bundan_sonra_teknik_anlat_is_a_standing_register(wired) -> None:
    client, runtime, _sideband, _broker, _artifacts = wired
    _enroll_online_device(_broker)
    _complete_a_research(client, runtime, _artifacts)
    sid = _create(client)
    _say(client, sid, "Bundan sonra teknik anlat.")
    call = _tool(client, sid, "mode-1", "research.answer_mode", {})
    assert call["status"] == "succeeded", call
    assert call["result"]["level"] == "technical"
    with runtime.session() as db:
        assert focus_module.get_answer_level(db) == "technical"
    # A plain "anlat" now answers technically without the model naming a level.
    _say(client, sid, "Bu araştırmayı anlat.", turn=2, t_ms=2000)
    body = _tool(client, sid, "explain-3", "research.explain", {})["result"]
    assert body["level"] == "technical"
    assert body["speech"].startswith("Bu araştırmada")
    # ... and "Teknik modu kapat." puts the executive register back.
    _say(client, sid, "Teknik modu kapat.", turn=3, t_ms=3000)
    back = _tool(client, sid, "mode-2", "research.answer_mode", {})
    assert back["result"]["level"] == "executive"
    _say(client, sid, "Bu araştırmayı anlat.", turn=4, t_ms=4000)
    body = _tool(client, sid, "explain-4", "research.explain", {})["result"]
    assert body["level"] == "executive"


def test_a_one_off_teknik_anlat_is_still_the_followup_not_the_register() -> None:
    assert resolve_intent("Teknik anlat.").intent is Intent.TECHNICAL
    assert resolve_intent("Bunu teknik anlat.").intent is Intent.TECHNICAL
    assert resolve_intent("Bundan sonra teknik anlat.").intent is Intent.RESEARCH_ANSWER_MODE
    assert resolve_intent("Artık kısa anlat.").answer_level == "executive"
    assert resolve_intent("Her zaman ayrıntılı anlat.").answer_level == "detail"


# ------------------------------------------------------------------------- 207


def test_a_synthesis_substitution_is_spoken_as_one() -> None:
    report = {
        **REPORT_JSON,
        "synthesis_provider": "deterministic",
        "synthesis_fallback": {
            "requested": "anthropic",
            "used": "deterministic",
            "attempts": 2,
            "reason": "contract_violation",
        },
        "search_fallbacks": 1,
    }
    speech = technical_speech(report)
    assert "istenen anthropic iki denemede kabul edilmedi, yedeğe geçildi" in speech
    assert "Sentez sağlayıcısı deterministic" in speech
    assert "bir arama sorgusu yedek sağlayıcıyla cevaplandı" in speech
    plain = technical_speech({**REPORT_JSON, "synthesis_provider": "anthropic"})
    assert "yedeğe" not in plain and "Sentez sağlayıcısı anthropic." in plain
