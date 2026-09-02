"""M12 unit tests: Turkish realtime intents + the narration bridge.

Every intent in M12 spec §5 has a positive case; the negatives prove matching
is on tokens and meaning, not substrings ("durum" is not "dur").
"""

from __future__ import annotations

import pytest

from app.narration.commands import NarrationState, State
from app.narration.engine import build_plan
from app.voice.intents import (
    SCOPE_CONVERSATION,
    SCOPE_NARRATION,
    Intent,
    apply_to_narration,
    current_item_index,
    normalize_transcript,
    ordered_paragraph_ids,
    resolve_intent,
    turkish_casefold,
)
from app.voice.realtime import STOP_WORDS, RealtimeState

DOC = """# Rapor

Birinci madde: maliyetler yüzde on arttı. Bu beklenen bir sonuçtu.

İkinci madde: gecikme düştü. Ölçüm 240 ms.

Üçüncü madde: kullanıcı memnuniyeti yükseldi.

## Sonuç

Genel olarak olumlu.
"""


@pytest.fixture()
def plan():
    return build_plan(DOC, artifact_id="a1", version=1)


# ------------------------------------------------------------- normalisation


def test_turkish_casefold_keeps_dotless_i() -> None:
    assert turkish_casefold("ISI İKİNCİ") == "ısı ikinci"


def test_fillers_and_numerals_are_normalised() -> None:
    text, tokens, dropped = normalize_transcript("Şey, yani... 2. maddeyi tekrar oku ııı")
    assert text == "ikinci maddeyi tekrar oku"
    assert tokens == ("ikinci", "maddeyi", "tekrar", "oku")
    assert dropped == 3


def test_empty_transcript_is_none() -> None:
    assert resolve_intent("").intent == Intent.NONE
    assert resolve_intent("   ").intent == Intent.NONE
    assert resolve_intent("şey yani ııı").intent == Intent.NONE


# ---------------------------------------------------------------- positives


@pytest.mark.parametrize(
    ("text", "intent", "target"),
    [
        ("dur", Intent.STOP, None),
        ("Dur!", Intent.STOP, None),
        ("tamam dur", Intent.STOP, None),
        ("kes", Intent.STOP, None),
        ("sus", Intent.STOP, None),
        ("yeter", Intent.STOP, None),
        ("durdur şunu", Intent.STOP, None),
        ("bir dakika bekle", Intent.STOP, None),
        ("devam", Intent.RESUME, None),
        ("devam et", Intent.RESUME, None),
        ("ıııı devam et", Intent.RESUME, None),
        ("kaldığın yerden devam", Intent.RESUME, None),
        ("tekrar oku", Intent.REPEAT, None),
        ("bir daha oku", Intent.REPEAT, None),
        ("yeniden oku", Intent.REPEAT, None),
        ("ikinci maddeyi tekrar oku", Intent.REPEAT_ITEM, 2),
        ("İkinci maddeyi tekrar oku", Intent.REPEAT_ITEM, 2),
        ("2. maddeyi tekrar oku", Intent.REPEAT_ITEM, 2),
        ("üçüncü maddeye geç", Intent.REPEAT_ITEM, 3),
        ("şey, üçüncü maddeyi bir daha", Intent.REPEAT_ITEM, 3),
        ("sonraki madde", Intent.NEXT_ITEM, None),
        ("sonraki maddeyi oku", Intent.NEXT_ITEM, None),
        ("önceki madde", Intent.PREVIOUS_ITEM, None),
        ("önceki maddeyi tekrar oku", Intent.PREVIOUS_ITEM, None),
        ("ilk madde", Intent.FIRST_ITEM, 1),
        ("ilk maddeyi oku", Intent.FIRST_ITEM, 1),
        ("son maddeye geç", Intent.LAST_ITEM, None),
        ("sonraki bölüm", Intent.NEXT_SECTION, None),
        ("bir sonraki başlığa geç", Intent.NEXT_SECTION, None),
        ("biraz daha yavaş", Intent.SLOWER, None),
        ("şey, yani biraz daha yavaş", Intent.SLOWER, None),
        ("yavaşla", Intent.SLOWER, None),
        ("biraz daha hızlı", Intent.FASTER, None),
        ("hızlan", Intent.FASTER, None),
        ("özet geç", Intent.SUMMARIZE, None),
        ("özetle", Intent.SUMMARIZE, None),
        ("kısaca anlat", Intent.SUMMARIZE, None),
        ("detaya gir", Intent.DETAIL, None),
        ("ayrıntıya gir", Intent.DETAIL, None),
        ("detaylandır", Intent.DETAIL, None),
        ("burayı atla", Intent.SKIP, None),
        ("bunu atla", Intent.SKIP, None),
        ("bunu geç", Intent.SKIP, None),
    ],
)
def test_every_m12_intent_resolves(text: str, intent: Intent, target: int | None) -> None:
    resolved = resolve_intent(text)
    assert resolved.intent == intent, resolved
    assert resolved.target_index == target


# ---------------------------------------------------------------- negatives


@pytest.mark.parametrize(
    "text",
    [
        "Durum raporunu oku",  # "dur" inside "durum"
        "durumu anlat",
        "yeterli değil",  # "yeter" inside "yeterli"
        "kesin bilgi ver",  # "kes" inside "kesin"
        "susuz kaldım",  # "sus" inside "susuz"
        "Kestane fiyatları arttı",
        "bugün hava güzel",
        "toplantı saat üçte",
        "hızlı bir özet değil, tam metin istiyorum",  # ambiguous by design -> not STOP
    ],
)
def test_substrings_and_unrelated_sentences_do_not_stop(text: str) -> None:
    resolved = resolve_intent(text)
    assert resolved.intent != Intent.STOP, resolved
    for word in ("Durum raporunu oku", "durumu anlat", "yeterli değil", "kesin bilgi ver",
                 "susuz kaldım", "Kestane fiyatları arttı", "bugün hava güzel",
                 "toplantı saat üçte"):
        if text == word:
            assert resolved.intent == Intent.NONE, resolved


def test_stop_words_of_the_m4_fsm_are_all_stop_intents() -> None:
    for word in STOP_WORDS:
        assert resolve_intent(word).intent == Intent.STOP


def test_stop_has_top_priority_over_everything_else() -> None:
    assert resolve_intent("dur, ikinci maddeyi tekrar oku").intent == Intent.STOP
    assert resolve_intent("biraz daha yavaş dur").intent == Intent.STOP
    for state in RealtimeState:
        if state == RealtimeState.CLOSED:
            continue
        assert resolve_intent("dur", session_state=state).intent == Intent.STOP


# -------------------------------------------------------------------- scope


def test_resume_scope_follows_narration_state() -> None:
    paused = NarrationState(state=State.PAUSED)
    assert resolve_intent("devam", narration=paused).scope == SCOPE_NARRATION
    assert resolve_intent("devam", narration=None).scope == SCOPE_CONVERSATION
    idle = NarrationState(state=State.IDLE)
    assert resolve_intent("devam", narration=idle).scope == SCOPE_CONVERSATION
    # item navigation always targets the narration when one is attached
    assert resolve_intent("ikinci madde", narration=idle).scope == SCOPE_NARRATION


def test_confidence_drops_slightly_when_fillers_were_removed() -> None:
    assert resolve_intent("devam").confidence == 1.0
    assert resolve_intent("şey devam").confidence == 0.9
    assert resolve_intent("bugün").confidence == 0.0


# -------------------------------------------------------------------- bridge


def _reading(plan, item: int = 1) -> NarrationState:
    from app.voice.intents import ResolvedIntent

    return apply_to_narration(
        ResolvedIntent(Intent.REPEAT_ITEM, target_index=item), NarrationState(), plan
    ).state


def test_items_exclude_headings(plan) -> None:
    """'ikinci madde' is the second thing the owner HEARS as an item, so the
    '# Rapor' heading does not count."""
    assert len(ordered_paragraph_ids(plan)) == 4
    assert plan.chunk_at(_reading(plan, 1).cursor).text.startswith("Birinci madde")
    assert plan.chunk_at(_reading(plan, 4).cursor).text == "Genel olarak olumlu."
    beyond = apply_to_narration(resolve_intent("beşinci maddeyi oku"), _reading(plan, 1), plan)
    assert not beyond.ok and beyond.action == "jump_failed"


def test_bridge_stop_always_pauses_and_keeps_cursor(plan) -> None:
    st = _reading(plan, 2)
    res = apply_to_narration(resolve_intent("dur"), st, plan)
    assert res.state.state == State.PAUSED
    assert res.state.cursor == st.cursor
    assert res.action == "paused"


def test_bridge_resume_after_stop_reads_from_the_same_cursor(plan) -> None:
    st = _reading(plan, 2)
    paused = apply_to_narration(resolve_intent("dur"), st, plan).state
    res = apply_to_narration(resolve_intent("devam", narration=paused), paused, plan)
    assert res.state.state == State.READING
    assert res.state.cursor == st.cursor


def test_bridge_repeat_item_navigates_semantically(plan) -> None:
    st = _reading(plan, 1)
    res = apply_to_narration(resolve_intent("ikinci maddeyi tekrar oku"), st, plan)
    assert res.ok and res.action == "jump_item"
    assert current_item_index(plan, res.state.cursor) == 2
    assert plan.chunk_at(res.state.cursor).text.startswith("İkinci madde")


def test_bridge_next_previous_first_last_and_skip(plan) -> None:
    n = len(ordered_paragraph_ids(plan))
    st = _reading(plan, 2)
    assert current_item_index(plan, apply_to_narration(
        resolve_intent("sonraki madde"), st, plan).state.cursor) == 3
    assert current_item_index(plan, apply_to_narration(
        resolve_intent("önceki madde"), st, plan).state.cursor) == 1
    assert current_item_index(plan, apply_to_narration(
        resolve_intent("ilk madde"), st, plan).state.cursor) == 1
    assert current_item_index(plan, apply_to_narration(
        resolve_intent("son madde"), st, plan).state.cursor) == n
    skipped = apply_to_narration(resolve_intent("burayı atla"), st, plan)
    assert skipped.action == "skipped"
    assert current_item_index(plan, skipped.state.cursor) == 3
    at_end = apply_to_narration(resolve_intent("burayı atla"), _reading(plan, n), plan)
    assert not at_end.ok and at_end.action == "end_of_document"


def test_bridge_speed_steps_and_clamps(plan) -> None:
    st = _reading(plan, 1)
    slower = apply_to_narration(resolve_intent("biraz daha yavaş"), st, plan)
    assert slower.speed == pytest.approx(0.75) and slower.action == "speed_changed"
    faster = apply_to_narration(resolve_intent("biraz daha hızlı"), st, plan)
    assert faster.speed == pytest.approx(1.25)
    floor = st
    for _ in range(10):
        floor = apply_to_narration(resolve_intent("yavaşla"), floor, plan).state
    assert floor.speed == pytest.approx(0.5)


def test_bridge_presentation_level_does_not_move_the_cursor(plan) -> None:
    st = _reading(plan, 2)
    summary = apply_to_narration(resolve_intent("özet geç"), st, plan)
    assert summary.presentation == "summary" and summary.state.cursor == st.cursor
    detail = apply_to_narration(resolve_intent("detaya gir"), st, plan)
    assert detail.presentation == "detail" and detail.state.cursor == st.cursor


def test_bridge_unknown_intent_is_a_noop(plan) -> None:
    st = _reading(plan, 1)
    res = apply_to_narration(resolve_intent("bugün hava güzel"), st, plan)
    assert not res.ok and res.action == "noop" and res.state == st
