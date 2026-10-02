"""ADR-0224, the gap of addendum 4: the rule tables read layer 1's reading.

Measured 2026-10-01: 73 of 106 sentences as the STT renders them (68.9 %). The cause was one
thing - the tables matched the surface tokens, so a polite imperative no table listed
("Ekranları kapatın.") and a rendering that fused two words ("hesapmakinesini aç") reached
nothing, while layer 1 already knew both. These tests hold the router to layer 1's reading:
one mechanism for every table, the surface form first, a negative never its positive.
"""

from __future__ import annotations

import pytest

from app.voice import intents as intents_module
from app.voice.intents import Intent, polite_imperative_readings, resolve_intent
from app.voice.understanding import normalize as layer_one
from app.voice.understanding import policy
from app.voice.understanding.combine import (
    MATCH_CONFUSION,
    MATCH_EXACT,
    MATCH_SUFFIX_DROPPED,
    RULE_CONFIDENCE,
    rule_candidate,
)

SUFFIX_DROPPED = RULE_CONFIDENCE[MATCH_SUFFIX_DROPPED]


def _rule_confidence(resolved) -> float:
    """What the relay's own adapter makes of the reading (candidate #1 of layer 2)."""
    reading = policy.rule_reading(
        resolved.intent.value,
        application=resolved.application,
        route_repair=resolved.route_repair,
    )
    candidate = rule_candidate(reading)
    assert candidate is not None
    return candidate.confidence


# --- the polite forms: every table, one mechanism --------------------------------------------


@pytest.mark.parametrize(
    ("said", "bare"),
    [
        ("Ekranları kapatın.", "Ekranları kapat."),
        ("Alarmı kurar mısınız?", "Alarmı kur."),
        ("Araştırmayı durdurabilir misin?", "Araştırmayı durdur."),
    ],
)
def test_a_polite_form_no_table_lists_is_its_bare_imperative_at_the_suffix_dropped_confidence(
    said, bare
):
    plain = resolve_intent(bare)
    assert plain.intent is not Intent.NONE
    assert (plain.confidence, plain.route_repair) == (1.0, None)
    assert _rule_confidence(plain) == RULE_CONFIDENCE[MATCH_EXACT]

    resolved = resolve_intent(said)
    assert resolved.intent is plain.intent, said
    assert resolved.confidence == SUFFIX_DROPPED, said
    assert _rule_confidence(resolved) == SUFFIX_DROPPED, said


def test_a_polite_form_a_table_lists_is_an_exact_closed_form():
    """ "okuyun" is in the research-read table (ADR-0184) and "açın" in the open table
    (ADR-0233): the surface form wins, at the confidence an exact form earns."""
    for said, bare in (
        ("Raporu okuyun.", "Raporu oku."),
        ("Hesap makinesini açın.", "Hesap makinesini aç."),
    ):
        resolved = resolve_intent(said)
        assert resolved.intent is resolve_intent(bare).intent, said
        assert (resolved.confidence, resolved.route_repair) == (1.0, None), said
        assert _rule_confidence(resolved) == RULE_CONFIDENCE[MATCH_EXACT], said


#: One sentence per verb table family, in its bare imperative; every polite form layer 1
#: generates for its verb must reach the same intent. No table lists these forms.
POLITE_FAMILIES = [
    ("Ekranları {v}.", "kapat"),  # display
    ("Gözünü {v}.", "kapat"),  # eye
    ("Haberleri {v}.", "aç"),  # news
    ("Maillerime {v}.", "bak"),  # mail, read-only
    ("Fatura maillerini {v}.", "bul"),  # mail search, read-only
    ("Bu klasördeki PDF'leri {v}.", "bul"),  # documents
    ("Toplantı notlarını Word belgesi {v}.", "yap"),  # artifacts
    ("Bana bir görev takip uygulaması {v}.", "yap"),  # app factory
    ("Bana Windows için masaüstü uygulaması {v}.", "yap"),  # native apps
    ("Blender'da yeni sahne {v}.", "aç"),  # scenes
    ("Varsayılan hava durumu konumumu İstanbul {v}.", "yap"),  # location
    ("Kendi kendini geliştirmeyi {v}.", "duraklat"),  # evolution
    ("Araştırmayı {v}.", "durdur"),  # research
    ("Alarmı {v}.", "kur"),  # alarm
    ("Yeni hareket {v}.", "oluştur"),  # macros
]


def _polite_forms(verb: str) -> list[str]:
    forms = [
        layer_one._attach_verb(verb, ("pol",)),
        layer_one._attach_verb(verb, ("pol", "pl")),
        layer_one._attach_verb(verb, ("sana",)),
    ]
    aorist = layer_one._attach_verb(verb, ("aor",))
    able = layer_one._attach_verb(verb, ("pot", "aor"))
    high = layer_one._high(aorist)  # the particle harmonises with the aorist: kapatır mısın
    forms += [f"{aorist} m{high}s{high}n", f"{able} misin", f"{able} misiniz"]
    # "Bunu yapabilir misin?" asks what I can do: the router's own, older exception stands.
    return [f for f in forms if f.split()[0] not in intents_module._POLITE_NOT_A_REQUEST]


@pytest.mark.parametrize(("frame", "verb"), POLITE_FAMILIES)
def test_every_polite_form_of_every_family_reaches_the_bare_imperatives_intent(frame, verb):
    plain = resolve_intent(frame.format(v=verb))
    assert plain.intent is not Intent.NONE, frame
    for form in _polite_forms(verb):
        said = frame.format(v=form)
        resolved = resolve_intent(said)
        assert resolved.intent is plain.intent, f"{said!r} -> {resolved.intent.value}"
        assert resolved.confidence <= 1.0
        if resolved.route_repair is not None:
            assert resolved.confidence == SUFFIX_DROPPED, said


def test_the_mechanism_is_one_and_no_table_gained_a_list_of_forms():
    """Adding "kapatın" by hand to a table is the fix that was refused: the forms are layer
    1's, generated from its grammar, and no rule table of the router lists them."""
    forms, _stems = intents_module.rule_verb_words()
    for unlisted in ("kapatın", "kapatınız", "bakın", "bulun", "yapın", "duraklatın", "kurun"):
        assert unlisted not in forms, unlisted


def test_a_polite_reading_supplies_the_route_and_the_slots_where_the_surface_reaches_nothing():
    resolved = resolve_intent("Bu dosyanın sonuna toplantı notu ekler misin?")
    assert resolved.intent is Intent.DOCUMENT_APPEND
    assert resolved.text_to_type == "toplantı notu"


def test_a_polite_reading_keeps_the_owners_own_words_where_the_surface_routes():
    """The surface reading decides the slots; layer 1 only says how sure the route is. The
    write table reads "yazar" by its stem, so the words as heard are owned - and the text to
    type is what the owner dictated, not layer 1's rewrite of it ("ışıkları söndür")."""
    said = "Şuraya ışıkları söndürün yazar mısın?"
    surface = intents_module._resolve_intent_rules(said)
    assert surface.intent is Intent.TYPE_TEXT and surface.text_to_type == "ışıkları söndürün"
    resolved = resolve_intent(said)
    assert resolved.intent is Intent.TYPE_TEXT
    assert resolved.text_to_type == "ışıkları söndürün"
    assert resolved.normalized_text == surface.normalized_text
    assert (resolved.confidence, resolved.route_repair) == (SUFFIX_DROPPED, "polite")


# --- a sentence a table owns: its content is not a command ------------------------------------

#: The words as heard are a command or a question a table already owns, with a verb of their
#: own; the polite form or the fused word is inside what the owner dictated. The base intents
#: are the ones main e1543a97 gave (inspector, 2026-10-02: 23 of 176 probes flipped to a device
#: action at 0.9).
OWNED_WITH_CONTENT = [
    ("Şunu hatırla: ışıkları söndürün.", Intent.MEMORY_REMEMBER),
    ("Şunu yaz: sabah alarmı kurun.", Intent.TYPE_TEXT),
    ("Yarın bana hatırlat: müziği durdurun.", Intent.MEMORY_REMEMBER),
    ("Şunu kaydet: maillerime bakın", Intent.MEMORY_REMEMBER),
    ("Aklında tut: araştırmayı durdurabilir misiniz", Intent.MEMORY_REMEMBER),
    ("Buraya ekranları kapatın yaz.", Intent.TYPE_TEXT),
    ("Müziği durdur ve ekranları kapatın.", Intent.MEDIA_STOP),
    ("Şunu açıkla: gözünü kapatınız", Intent.EXPLAIN),
    # ... and the fused word: the same sentence, the same owner.
    ("Şunu hatırla: alarmkur", Intent.MEMORY_REMEMBER),
    ("Şunu yaz: ekranlarıkapat", Intent.TYPE_TEXT),
    ("Müziği durdur ve ekranlarıkapat", Intent.MEDIA_STOP),
]


@pytest.mark.parametrize(("said", "owner"), OWNED_WITH_CONTENT)
def test_a_sentence_a_table_owns_is_not_turned_into_another_action_by_its_content(said, owner):
    resolved = resolve_intent(said)
    assert resolved.intent is owner, f"{said!r} -> {resolved.intent.value}"
    assert resolved == intents_module._resolve_intent_rules(said), said
    assert (resolved.confidence, resolved.route_repair) == (1.0, None), said


def test_a_question_with_no_verb_of_its_own_is_the_command_its_polite_form_asks_for():
    """The one case the imperative reading takes a sentence a table owns: the words as heard
    were read as a question ABOUT self-development, by the noun alone - the only verb the
    owner said is the polite one."""
    for said in (
        "Kendi kendini geliştirmeyi duraklatın.",
        "Kendi kendini geliştirmeyi duraklatır mısın?",
        "Duraklatın kendi kendini geliştirmeyi.",
    ):
        assert intents_module._resolve_intent_rules(said).intent is Intent.EXPLAIN, said
        resolved = resolve_intent(said)
        assert resolved.intent is Intent.EVOLUTION_PAUSE, said
        assert resolved.evolution_action == "pause"
        assert (resolved.confidence, resolved.route_repair) == (SUFFIX_DROPPED, "polite")


@pytest.mark.parametrize(
    ("said", "owner", "guard"),
    [
        # Each sentence passes the two other guards, so each guard is held by its own case.
        ("Bunu unutma ışıkları söndürün", Intent.MEMORY_REMEMBER, "a command, not a question"),
        ("Bana anlat ekranları kapatın", Intent.SCREEN_DESCRIBE, "a verb of its own"),
        ("Şunu açıkla gözünü kapatınız", Intent.EXPLAIN, "a verb of its own"),
        # "tarif" is a verb form only the router's own table lists; layer 1 does not know it.
        ("Ekranı tarif et ekranları kapatın", Intent.SCREEN_DESCRIBE, "a verb a table lists"),
        ("Kendi kendini geliştirme nedir, duraklatın.", Intent.EXPLAIN, "a second clause"),
    ],
)
def test_the_three_guards_of_an_owned_sentence_each_hold_alone(said, owner, guard):
    resolved = resolve_intent(said)
    assert resolved.intent is owner, f"{guard}: {said!r} -> {resolved.intent.value}"
    assert resolved == intents_module._resolve_intent_rules(said), guard


def test_a_sentence_a_mail_or_calendar_table_owns_is_never_re_read():
    """B45/B46: their routes stay exactly as they are - not even the confidence moves. Each
    sentence holds a polite form no table lists, and its imperative reading reaches the very
    same intent; the reading is still not taken."""
    for said, state, owner in (
        ("Bunu bir saat erteleyin.", {"event_focused": True}, Intent.CALENDAR_PROPOSE),
        ("Bugün takvimimde ne var söyler misin?", {}, Intent.CALENDAR_AGENDA),
        ("Son maili okur musun?", {}, Intent.MAIL_READ),
    ):
        reading = layer_one.lemma_reading(said)
        assert reading is not None and reading.dropped, said
        assert intents_module._resolve_intent_rules(reading.text, **state).intent is owner, said
        resolved = resolve_intent(said, **state)
        assert resolved == intents_module._resolve_intent_rules(said, **state), said
        assert resolved.intent is owner
        assert (resolved.confidence, resolved.route_repair) == (1.0, None), said


def test_a_quoted_title_is_never_rewritten():
    said = "YouTube'dan 'Bana Bakın' şarkısını açın."
    plain = resolve_intent("YouTube'dan 'Bana Bakın' şarkısını aç.")
    resolved = resolve_intent(said)
    assert resolved.intent is plain.intent is Intent.MEDIA_PLAY
    assert resolved.media_query == plain.media_query
    reading = layer_one.lemma_reading("'Bana Bakın' şarkısını kapatın.")
    assert reading is not None and "'Bana Bakın'" in reading.text
    assert reading.dropped == (("kapatın", "kapat"),)


# --- what the mechanism must never do ---------------------------------------------------------


@pytest.mark.parametrize(
    ("said", "never"),
    [
        ("Ekranı kapatma", Intent.DISPLAY_OFF),
        ("Ekranları kapatmayın.", Intent.DISPLAY_OFF),
        ("Ekranlarıkapatma.", Intent.DISPLAY_OFF),
        ("Bunu unutma", Intent.MEMORY_FORGET),
        ("Bunu unutmayın.", Intent.MEMORY_FORGET),
        ("Bunuunutma.", Intent.MEMORY_FORGET),
    ],
)
def test_a_negative_form_never_becomes_its_positive(said, never):
    assert resolve_intent(said).intent is not never, said


def test_bunu_unutma_is_still_remember():
    assert resolve_intent("Bunu unutma").intent is Intent.MEMORY_REMEMBER
    assert resolve_intent("Bunu unut").intent is Intent.MEMORY_FORGET


def test_a_sentence_that_says_dont_is_read_as_heard():
    """The negative-form guard: layer 1 offers no reading of a sentence that carries a
    negative imperative, so the polite clause beside it cannot become the action the first
    clause forbade. Red when the guard is removed."""
    said = "Ekranları kapatmayın, kapatın demedim."
    assert layer_one.lemma_reading(said) is None
    assert layer_one.lemma_reading("Ekranları kapatın demedim.") is not None
    resolved = resolve_intent(said)
    assert resolved.intent is not Intent.DISPLAY_OFF
    assert resolved == intents_module._resolve_intent_rules(said)


def test_a_verbal_noun_is_not_a_negative():
    """ "Araştırma" the noun is spelled like "don't research"; it is a known noun."""
    reading = layer_one.lemma_reading("Araştırma raporunu gösterin.")
    assert reading is not None and reading.dropped == (("gösterin", "göster"),)


def test_a_question_that_only_looks_polite_is_still_not_a_request():
    for said in ("Bunu yapabilir misin?", "Bunu hatırlar mısın?"):
        assert resolve_intent(said) == intents_module._resolve_intent_rules(said), said
        assert resolve_intent(said).route_repair is None


def test_the_reading_never_routes_into_a_mail_or_calendar_action():
    """B45/B46: "Gönderir misin?" after a read-back is not newly a send. Reading mail changes
    nothing the owner can see (the query class), so "Maillerime bakın." is read."""
    for said, state in (
        ("Gönderir misin?", {"draft_pending": True}),
        ("Gönderin.", {"draft_pending": True}),
        ("Mailimi gönderin.", {"draft_pending": True}),
    ):
        assert resolve_intent(said, **state) == intents_module._resolve_intent_rules(
            said, **state
        ), said
        assert resolve_intent(said, **state).intent is not Intent.MAIL_SEND, said
    assert polite_imperative_readings("Gönderir misin?")  # ... though its imperative is one
    assert resolve_intent("Gönder.", draft_pending=True).intent is Intent.MAIL_SEND
    inbox = resolve_intent("Maillerime bakın.")
    assert inbox.intent is Intent.MAIL_INBOX and inbox.klass == intents_module.KLASS_QUERY


# --- the fused word ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("said", "two_words"),
    [
        ("hesapmakinesini aç", "hesap makinesini aç"),
        ("Hesapmakinesini aç.", "Hesap makinesini aç."),
        ("alarmkur", "alarm kur"),
        ("NotDefteri'ni aç.", "Not Defteri'ni aç."),
        ("Ekranlarıkapat.", "Ekranları kapat."),
        ("Haberleriaç.", "Haberleri aç."),
        ("Maillerimebak.", "Maillerime bak."),
        ("Yenihareket oluştur.", "Yeni hareket oluştur."),
        ("Ekranlarıkapatın.", "Ekranları kapat."),  # fused AND polite
    ],
)
def test_a_fused_word_resolves_as_the_two_word_sentence_does(said, two_words):
    plain = resolve_intent(two_words)
    assert plain.intent is not Intent.NONE, two_words
    resolved = resolve_intent(said)
    assert resolved.intent is plain.intent, f"{said!r} -> {resolved.intent.value}"
    assert resolved.application == plain.application
    assert resolved.route_repair is not None and "fused" in resolved.route_repair
    # A repaired rendering is never an exact closed form: the confidence of a confusion.
    assert resolved.confidence == RULE_CONFIDENCE[MATCH_CONFUSION]
    assert _rule_confidence(resolved) < RULE_CONFIDENCE[MATCH_EXACT]


def test_a_fused_time_is_read_by_the_router_as_the_two_word_sentence():
    """The surface route existed, read off the fused token: the split reading is the one
    returned. (The alarm TOOL still parses the time from the sentence as heard - outside the
    router; that case stays in ``stt_corpus.KNOWN_GAPS``.)"""
    plain = resolve_intent("Saat yedi buçukta beni uyandır.")
    resolved = resolve_intent("Saat yedibuçukta beni uyandır.")
    assert resolved.intent is plain.intent is Intent.ALARM_CREATE
    assert resolved.spoken_numbers == plain.spoken_numbers
    assert resolved.normalized_text == plain.normalized_text


@pytest.mark.parametrize(
    "said",
    [
        "Masaüstünü göster.",  # masaüstü is a known word, not masa + üstü
        "Silver temasını anlat.",  # sil + ver: two verbs are never a split
        "Bulun beni.",
        "İstediğim videoyu aç.",
    ],
)
def test_a_sentence_with_no_fused_word_is_left_alone(said):
    reading = layer_one.lemma_reading(said)
    assert reading is None or reading.splits == ()


def test_no_sentence_of_the_owner_utterance_suite_holds_a_token_layer_one_would_split():
    """The split's precision, held against every sentence the owner corpus knows: they are
    written correctly, so a split in any of them is a false one ("Bugünün Show Ana Haber
    videosunu aç." was read as bu + günün until "bugün" became a word of its own)."""
    from tests.voice_corpus.corpus import all_cases

    cases = all_cases()
    assert len(cases) >= 2754
    split = {
        case.case_id: layer_one.normalize(case.utterance).applied_splits
        for case in cases
        if layer_one.normalize(case.utterance).applied_splits
    }
    assert split == {}
    assert resolve_intent("Bugünün Show Ana Haber videosunu aç.").news_source_ref == "show"


def test_the_routers_two_confidences_are_layer_twos_own():
    assert intents_module._SUFFIX_DROPPED_CONFIDENCE == RULE_CONFIDENCE[MATCH_SUFFIX_DROPPED]
    assert intents_module._REPAIRED_WORD_CONFIDENCE == RULE_CONFIDENCE[MATCH_CONFUSION]
