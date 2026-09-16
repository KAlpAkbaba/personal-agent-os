"""B27 req 726-735: the ten everyday sentences route, measured as a SET.

The audit's number was one line — *103 makul cümlenin 59'u hiçbir yeteneğe yönlenmiyor* —
and this file is the other half of that line. ``DAILY_SET`` is the ten requirement
sentences and at least three Turkish synonyms of each, measured against the live router in
one run; the uncovered ratio is asserted, not described. Measured on this machine before
anything was changed: 49 sentences, 32 unrouted, 65.3 %. After: 0.

Two disciplines from the batches before this one are kept on purpose:

* **A narrowing's neighbours are asserted in the same run** (B26's SHIELD): every new
  matcher is one word away from a sentence another family already owned, and
  ``NEIGHBOURS`` is the list of those sentences with the family each must keep.
* **Both halves read each other**: the family keys the router extracts for req 734 are
  checked against ``app.voice.capabilities.FAMILY_TR``; the acting-intent list the corpus
  uses is checked against the one the misroute detector uses; every new intent is checked
  against the tool registry the model is actually handed.
"""

from __future__ import annotations

import pytest

from app.voice import route_telemetry
from app.voice.intents import (
    _CAPABILITY_FAMILY_STEMS,
    CAPABILITY_BY_INTENT,
    QUERY_TOOL_BY_INTENT,
    Intent,
    klass_for,
    resolve_intent,
)
from tests.voice_corpus import routing

# ------------------------------------------------------------------ the measured set

#: The audit's measured ratio for its own 103 sentences, and this set's own baseline —
#: measured on 2026-09-14 against the router as B26 left it, before this batch changed
#: anything. The set may only grow; the ratio may only fall.
AUDIT_UNCOVERED_RATIO = 0.57
BASELINE_UNCOVERED = (49, 32)  # (sentences, unrouted)
#: The bar this batch has to clear: every sentence in the set reaches something.
TARGET_UNCOVERED_RATIO = 0.0

#: requirement -> (expected intent, the sentence the requirement names, synonyms)
DAILY_SET: dict[int, tuple[Intent, str, tuple[str, ...]]] = {
    726: (
        Intent.CLOCK_QUERY,
        "Saat kaç?",
        ("Saat kaç oldu?", "Şu an saat kaç?", "Bugün günlerden ne?"),
    ),
    727: (
        Intent.MEMORY_REMEMBER,
        "Bunu hatırla.",
        ("Bunu aklında tut.", "Şunu unutma: Ali'nin doğum günü mayıs.", "Bunu kaydet."),
    ),
    728: (
        Intent.MEMORY_FORGET,
        "Bunu unut.",
        ("Son söylediğimi unut.", "Bunu unutabilirsin.", "Hafızandan sil."),
    ),
    729: (
        Intent.MAIL_INBOX,
        "Maillerime bak.",
        (
            "Mail var mı?",
            "Gelen kutuma bak.",
            "Yeni mail var mı?",
            "Postalarımı kontrol et.",
            "Maillerimi göster.",
        ),
    ),
    730: (
        Intent.CALENDAR_AGENDA,
        "Bu hafta ne var?",
        (
            "Yarın ne var?",
            "Bugün programım ne?",
            "Haftalık programımı söyle.",
            "Bu hafta takvimimde ne var?",
        ),
    ),
    731: (
        Intent.CALENDAR_CANCEL,
        "Toplantıyı iptal et.",
        (
            "Perşembeki toplantıyı iptal et.",
            "Yarınki randevuyu iptal et.",
            "Toplantıyı takvimden sil.",
        ),
    ),
    732: (
        Intent.RESEARCH_CANCEL,
        "Araştırmayı iptal et.",
        ("Araştırmayı durdur.", "Araştırmayı bırak.", "Araştırmadan vazgeç."),
    ),
    733: (
        Intent.MEDIA_VOLUME,
        "Sesini kıs.",
        (
            "Sesi kıs.",
            "Sesi biraz alçalt.",
            "Sesini azalt.",
            "Sesi aç.",
            "Sesi biraz aç.",
            "Sesini yükselt.",
            "Sessize al.",
        ),
    ),
    734: (
        Intent.CAPABILITIES_QUERY,
        "Neler yapabilirsin?",
        (
            "Ne yapabiliyorsun?",
            "Neler yapabiliyorsun?",
            "Yeteneklerin neler?",
            "Hangi konularda yardımcı olabilirsin?",
            "Mail konusunda neler yapabilirsin?",
        ),
    ),
    735: (
        Intent.SCREENSHOT_CAPTURE,
        "Ekran görüntüsü al.",
        ("Ekranın görüntüsünü al.", "Screenshot al.", "Ekranı yakala."),
    ),
}


def _sentences() -> list[tuple[int, str, Intent]]:
    out: list[tuple[int, str, Intent]] = []
    for req, (intent, canonical, synonyms) in DAILY_SET.items():
        out.append((req, canonical, intent))
        out.extend((req, text, intent) for text in synonyms)
    return out


def test_the_set_is_the_size_it_was_measured_at() -> None:
    """The baseline is a fact about THIS set; a smaller set would make the ratio a lie."""
    assert len(_sentences()) >= BASELINE_UNCOVERED[0]
    for req, (_intent, _canonical, synonyms) in DAILY_SET.items():
        assert len(synonyms) >= 3, f"req {req}: the test plan asks for at least three synonyms"


def test_the_uncovered_ratio_falls_from_the_baseline_to_the_target() -> None:
    """REAL_PROOF: *103 cümlelik sette kapsanmayan oran %57'den hedefe iner* — asserted
    over the whole set in one run, and reported together so the tenth is not missed."""
    sentences = _sentences()
    unrouted = [
        (req, text) for req, text, _ in sentences if resolve_intent(text).intent is Intent.NONE
    ]
    ratio = len(unrouted) / len(sentences)
    baseline_ratio = BASELINE_UNCOVERED[1] / BASELINE_UNCOVERED[0]
    assert baseline_ratio > AUDIT_UNCOVERED_RATIO, "this set was harder than the audit's"
    assert ratio <= TARGET_UNCOVERED_RATIO, "\n".join(
        f"  req {req}: {text!r} reaches nothing" for req, text in unrouted
    )


@pytest.mark.parametrize(("req", "text", "intent"), _sentences(), ids=lambda v: str(v))
def test_each_sentence_reaches_its_requirement(req: int, text: str, intent: Intent) -> None:
    resolved = resolve_intent(text)
    assert resolved.intent is intent, f"req {req}: {text!r} -> {resolved.intent}"


# ----------------------------------------------------------- the neighbours (shield)

#: Sentences one word away from a new matcher, with the family each must KEEP. Every
#: narrowing in B26 was paired with the sentences it must not touch; every widening here
#: is paired the same way, because a widening is the change most likely to steal one.
NEIGHBOURS: tuple[tuple[str, Intent, dict[str, object], str], ...] = (
    ("Günaydın, bugün ne var?", Intent.MORNING_BRIEFING, {}, "the greeting decides"),
    ("Yetenek durumu ne?", Intent.CAPABILITY_STATUS, {}, "M24's own noun"),
    ("Bunu yapabilir misin?", Intent.NONE, {}, "asks for a thing, not for a list"),
    (
        "Ne yapıyorsun?",
        Intent.OPERATOR_STATUS,
        {"operator_running": True},
        "the operator's status question keeps its stem",
    ),
    ("Kahve hakkında ne biliyorsun?", Intent.MEMORY_SEARCH, {}, "the memory's recall verb"),
    ("Toplantı notlarını sil.", Intent.NONE, {}, "notes are not a meeting (accusative only)"),
    ("Alarmı iptal et.", Intent.ALARM_CANCEL, {}, "the alarm takes its noun first"),
    (
        "Araştırmayı yeniden yap.",
        Intent.REPEAT,
        {},
        "a retry (research_class=research_retry on REPEAT) is not a cancel",
    ),
    ("Araştırmayı iptal etme.", Intent.NONE, {}, "Turkish negation: do NOT"),
    ("Dur.", Intent.STOP, {}, "the bare stop keeps its priority"),
    ("Belgeyi seslendir.", Intent.NONE, {}, "'ses' is a prefix of 'seslendir'"),
    ("Ekranı kapat.", Intent.DISPLAY_OFF, {}, "the display family is first"),
    ("Kamerayı kapat.", Intent.EYE_DISABLE, {}, "the eye family is first"),
    ("Görüntüyü kapat.", Intent.DISPLAY_OFF, {}, "the owner's word for what the screen shows"),
    ("Gelen kutumda ne var?", Intent.MAIL_INBOX, {}, "M21's own inbox sentence"),
    ("Ali'ye mail gönder.", Intent.MAIL_DRAFT_NEW, {}, "a compose is still a compose"),
    ("Bugün takvimimde ne var?", Intent.CALENDAR_AGENDA, {}, "M21's own agenda sentence"),
    ("Perşembe 15'e diş hekimi ekle.", Intent.CALENDAR_PROPOSE, {}, "an addition stays one"),
    ("Sabah alarmım kaçta?", Intent.ALARM_QUERY, {}, "the clock's collision, still the alarm's"),
)


@pytest.mark.parametrize(
    ("text", "intent", "state", "why"), NEIGHBOURS, ids=[n[0] for n in NEIGHBOURS]
)
def test_the_neighbours_keep_their_families(
    text: str, intent: Intent, state: dict[str, object], why: str
) -> None:
    resolved = resolve_intent(text, **state)
    assert resolved.intent is intent, f"{text!r} -> {resolved.intent} — {why}"


# --------------------------------------------------------------- the owner's words

def test_the_family_the_owner_named_travels_with_the_intent() -> None:
    """req 734: "mail konusunda neler yapabilirsin?" -> family 'mail', on the resolved
    intent, so the tool prefers it over the model's own argument."""
    assert resolve_intent("Mail konusunda neler yapabilirsin?").capability_family == "mail"
    assert resolve_intent("Takvimle ilgili ne yapabiliyorsun?").capability_family == "calendar"
    assert resolve_intent("Neler yapabilirsin?").capability_family is None


def test_every_family_key_the_router_knows_is_a_family_the_registry_groups_by() -> None:
    """Both halves read each other: a key here that ``capabilities.FAMILY_TR`` does not
    carry would make the tool answer "böyle bir alanım yok" to a sentence the router
    deliberately narrowed."""
    from app.voice.capabilities import FAMILY_TR

    keys = {family for _stems, family in _CAPABILITY_FAMILY_STEMS}
    assert keys, "the family table is empty"
    assert keys <= set(FAMILY_TR), sorted(keys - set(FAMILY_TR))


def test_the_volume_direction_is_read_off_the_verb() -> None:
    """req 733: which way, from the owner's word, never from the model."""
    assert resolve_intent("Sesini kıs.").media_volume_direction == "down"
    assert resolve_intent("Sesi biraz aç.").media_volume_direction == "up"
    assert resolve_intent("Sessize al.").media_volume_direction == "mute"
    assert resolve_intent("Sesi kapat.").media_volume_direction == "mute"
    assert resolve_intent("Saat kaç?").media_volume_direction is None


def test_the_resolved_intent_serialises_the_new_fields() -> None:
    """The service persists ``to_dict()`` on the session row; a field that is not in it
    never reaches the tool."""
    payload = resolve_intent("Mail konusunda neler yapabilirsin?").to_dict()
    assert payload["capability_family"] == "mail"
    assert "media_volume_direction" in payload


# ------------------------------------------------------------- the contract halves

NEW_INTENTS = (
    Intent.CAPABILITIES_QUERY,
    Intent.RESEARCH_CANCEL,
    Intent.MEDIA_VOLUME,
    Intent.SCREENSHOT_CAPTURE,
    Intent.CALENDAR_CANCEL,
)


@pytest.mark.parametrize("intent", NEW_INTENTS, ids=lambda i: i.value)
def test_every_new_intent_names_a_tool_the_model_is_handed(intent: Intent) -> None:
    """An intent with no tool routes to nothing the owner can hear (B25's lesson: complete
    code with no caller)."""
    from app.voice.realtime_sessions.tools import default_registry

    tool = CAPABILITY_BY_INTENT.get(intent) or QUERY_TOOL_BY_INTENT.get(intent)
    assert tool, f"{intent} names no tool"
    assert tool in set(default_registry().names()), f"{tool} is not registered"


def test_the_query_is_a_query_and_the_four_actions_are_actions() -> None:
    assert klass_for(Intent.CAPABILITIES_QUERY) == "query"
    assert Intent.CAPABILITIES_QUERY not in CAPABILITY_BY_INTENT
    for intent in (
        Intent.RESEARCH_CANCEL,
        Intent.MEDIA_VOLUME,
        Intent.SCREENSHOT_CAPTURE,
        Intent.CALENDAR_CANCEL,
    ):
        assert klass_for(intent) == "action", intent


def test_the_corpus_and_the_detector_agree_on_what_acts() -> None:
    """Two lists of "intents that act" — the routing corpus's and the misroute
    detector's — and a sentence that one calls harmless and the other calls an action
    is exactly the drift both were written to catch."""
    corpus = set(routing.ACTING_INTENTS)
    detector = set(route_telemetry.ACTING_INTENTS)
    assert corpus <= detector, sorted(i.value for i in corpus - detector)
    for intent in (Intent.RESEARCH_CANCEL, Intent.CALENDAR_CANCEL, Intent.SCREENSHOT_CAPTURE):
        assert intent in corpus and intent in detector, intent


def test_b26s_unrouted_seven_now_carry_the_intent_each_reaches() -> None:
    """B26 wrote them with ``expected=None``, for their forbidden sets alone. Six of the
    seven now name a target, and every forbidden set is still asserted by the same
    ``misroutes`` run — routing "Sesini kıs." to a screen would still fail."""
    named = [case for case in routing.UNROUTED if case.expected is not None]
    assert len(named) >= 6
    assert routing.misroutes(resolve_intent) == []
    for case in routing.UNROUTED:
        assert case.forbidden, case.utterance
