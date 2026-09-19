"""A research asked for aloud starts a research when no model is there to decide it.

Production 2026-09-19 (free local mode, ADR-0173): "yapay zeka ile ilgili son haberleri
araştır" produced no tool call at all. On the paid path the MODEL calls ``research.start`` and
writes the topic; the router only classified the sentence (``research_class: new_research``)
and named no tool, so the router-only client had nothing to call and said "Anlayamadım".
"""

from __future__ import annotations

import pytest

from app.voice.intents import research_topic_of
from tests.voice_corpus.harness import build_harness


@pytest.mark.parametrize(
    ("said", "topic"),
    [
        ("yapay zeka ile ilgili son haberleri araştır", "yapay zeka ile ilgili son haberleri"),
        ("Yapay zeka hakkında araştırma yap", "Yapay zeka"),
        ("Bana Hetzner fiyatlarını araştır lütfen", "Hetzner fiyatlarını"),
        # Not a NEW research: a question about the one that finished, a control word.
        ("araştırmayı özetle", None),
        ("araştırmayı tekrar anlat", None),
        ("araştırmayı iptal et", None),
        ("Saat kaç?", None),
    ],
)
def test_the_topic_is_the_owners_sentence_without_the_request(said: str, topic: str | None) -> None:
    assert research_topic_of(said) == topic


def test_the_router_names_research_start_and_the_tool_reads_the_spoken_topic() -> None:
    h = build_harness()
    sid = h.new_session()
    said = h.say(sid, "yapay zeka ile ilgili son haberleri araştır")
    assert said["resolved_intents"][-1]["tool"] == "research.start", said["resolved_intents"]

    # The router-only client sends NO arguments; the topic comes from this turn's record.
    call = h.tool(sid, "c-1", "research.start", {})
    body = call.get("result") or {}
    assert call["status"] == "running", call
    assert body["topic"] == "yapay zeka ile ilgili son haberleri", body
    assert body["status"] == "running" and body["task_id"], body


def test_a_question_about_a_finished_research_names_no_crawl() -> None:
    h = build_harness()
    sid = h.new_session()
    said = h.say(sid, "araştırmayı özetle")
    assert said["resolved_intents"][-1].get("tool") != "research.start"
