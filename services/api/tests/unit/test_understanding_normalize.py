"""ADR-0224 layer 1: the closed Turkish suffix stripper and the STT confusion list."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.voice.understanding import normalize as norm
from app.voice.understanding.normalize import lemma_tokens, load_confusions, normalize

SHARED = Path(norm.__file__).with_name("stt-confusions.json")
TRIAL = "Ofisü bilgisayarında hesap makinesini açın."


@pytest.fixture(autouse=True)
def _bundled_confusions(monkeypatch):
    """The list is package data beside the module (one reader, so not a protocol file)."""
    del monkeypatch
    norm._default_confusions.cache_clear()
    yield
    norm._default_confusions.cache_clear()


def test_trial_sentence_normalises_to_stems_with_the_confusion_recorded():
    n = normalize(TRIAL)
    assert n.applied_confusions == (("ofisü", "ofis"),)
    assert n.text.startswith("ofis bilgisayarında")
    assert lemma_tokens(TRIAL) == ("ofis", "bilgisayar", "hesap", "makine", "aç")


@pytest.mark.parametrize(
    "said",
    [
        "açın",
        "açınız",
        "açsana",
        "açar mısın",
        "açabilir misin",
        "açar mısınız",
        "açabilir misiniz",
        "aç",
    ],
)
def test_open_verb_forms_lemmatise_to_ac(said):
    assert lemma_tokens(said) == ("aç",)


def test_aorist_question_is_one_lemma_with_its_suffixes():
    (lem,) = normalize("açar mısın").lemmas
    assert (lem.stem, lem.suffixes, lem.kind) == ("aç", ("aor", "q"), "verb")
    assert lem.surface == "açar mısın"


def test_istedigim_keeps_its_surface_not_is():
    (lem,) = normalize("istediğim").lemmas
    assert (lem.stem, lem.suffixes, lem.kind) == ("istediğim", (), "other")
    assert lemma_tokens("istediğim videoyu aç")[0] == "istediğim"


def test_unutma_keeps_its_surface():
    assert lemma_tokens("unutma") == ("unutma",)
    assert lemma_tokens("unut") == ("unut",)


def test_bilgisayarimdan_is_bilgisayar_poss1sg_abl():
    (lem,) = normalize("bilgisayarımdan").lemmas
    assert (lem.stem, lem.suffixes, lem.kind) == ("bilgisayar", ("poss1sg", "abl"), "noun")


def test_raporlarini_is_rapor_plural_poss3sg_accusative():
    (lem,) = normalize("raporlarını").lemmas
    assert (lem.stem, lem.suffixes) == ("rapor", ("pl", "poss3sg", "acc"))


@pytest.mark.parametrize("word", ["zxcvbnm", "kaydedin", "istedim", "ofisüm", "bilgisayarz"])
def test_a_word_with_no_known_stem_is_left_whole(word):
    (lem,) = normalize(word).lemmas
    assert (lem.stem, lem.suffixes, lem.kind) == (word, (), "other")


# surface -> stem; drawn from the Owner Utterance Suite's own words (most frequent first)
STEM_CASES = [
    ("uygulamayı", "uygulama"),
    ("uygulaması", "uygulama"),
    ("uygulamanın", "uygulama"),
    ("pencereyi", "pencere"),
    ("pencereye", "pencere"),
    ("araştırmayı", "araştırma"),
    ("araştırmanın", "araştırma"),
    ("haberleri", "haber"),
    ("ekranları", "ekran"),
    ("ekranı", "ekran"),
    ("durumu", "durum"),
    ("dosyayı", "dosya"),
    ("dosyasını", "dosya"),
    ("dosyaları", "dosya"),
    ("dosyanın", "dosya"),
    ("belgeyi", "belge"),
    ("belgeye", "belge"),
    ("belgesi", "belge"),
    ("şarkıyı", "şarkı"),
    ("paint'te", "paint"),
    ("defteri'ni", "defter"),
    ("alarmı", "alarm"),
    ("kamerayı", "kamera"),
    ("sunumda", "sunum"),
    ("sunumu", "sunum"),
    ("makinesini", "makine"),
    ("makinesi", "makine"),
    ("saati", "saat"),
    ("maili", "mail"),
    ("rutinini", "rutin"),
    ("bilgisayarımda", "bilgisayar"),
    ("bilgisayarda", "bilgisayar"),
    ("ofiste", "ofis"),
    ("evde", "ev"),
    ("işi", "iş"),
    ("laptopta", "laptop"),
    ("masaüstünde", "masaüstü"),
    ("hatayı", "hata"),
    ("sürümünü", "sürüm"),
    ("açsana", "aç"),
    ("kapatsana", "kapat"),
    ("yapsana", "yap"),
    ("kapatır", "kapat"),
    ("okur", "oku"),
    ("bulur", "bul"),
    ("durdurur", "durdur"),
    ("verir", "ver"),
    ("özetler", "özetle"),
    ("ekler", "ekle"),
    ("okuyun", "oku"),
    ("gösterin", "göster"),
    ("kapatabilir misin", "kapat"),
    ("okuyabilir misiniz", "oku"),
]


@pytest.mark.parametrize(("surface", "stem"), STEM_CASES)
def test_surface_to_stem(surface, stem):
    assert lemma_tokens(surface) == (stem,)


def test_the_corpus_cases_are_at_least_forty():
    assert len(STEM_CASES) >= 40


def test_normalisation_is_idempotent_on_stems():
    once = lemma_tokens(TRIAL)
    assert lemma_tokens(" ".join(once)) == once


# ------------------------------------------------------------------ the fused word


@pytest.mark.parametrize(
    ("fused", "left", "right"),
    [
        ("hesapmakinesini", "hesap", "makinesini"),
        ("alarmkur", "alarm", "kur"),
        ("ekranlarıkapat", "ekranları", "kapat"),
        ("notdefteri'ni", "not", "defteri'ni"),
        ("maillerimebak", "maillerime", "bak"),
        ("birrutin", "bir", "rutin"),
        ("yedibuçukta", "yedi", "buçukta"),
        ("ofisbilgisayarımda", "ofis", "bilgisayarımda"),
    ],
)
def test_a_fused_token_is_split_into_two_known_words_and_recorded(fused, left, right):
    n = normalize(f"{fused} aç")
    assert n.tokens == (left, right, "aç")
    assert n.applied_splits == ((fused, f"{left} {right}"),)
    assert n.text == f"{left} {right} aç"
    assert [lem.surface for lem in n.lemmas] == [left, right, "aç"]


def test_alarmkur_lemmatises_as_alarm_kur_does():
    assert lemma_tokens("alarmkur") == lemma_tokens("alarm kur") == ("alarm", "kur")
    assert lemma_tokens("hesapmakinesini aç") == ("hesap", "makine", "aç")


def test_a_sentence_without_a_fused_token_records_no_split():
    assert normalize(TRIAL).applied_splits == ()
    assert normalize("hesap makinesini aç").applied_splits == ()


@pytest.mark.parametrize(
    "word",
    [
        "bugün",  # a word of its own, though "bu" and "gün" are both known
        "bugünün",  # ... and so is every form of it ("Bugünün Show Ana Haber videosunu aç.")
        "masaüstünde",
        "bilgisayarımdan",
        "açsana",
        "kapatabilir",
        "hatırlatıcı",
    ],
)
def test_a_token_that_is_a_known_word_is_never_split(word):
    n = normalize(word)
    assert n.tokens == (word,) and n.applied_splits == ()


def test_bugun_would_split_if_it_were_not_a_known_word():
    """The rule above is load-bearing: both halves of "bugün" are words layer 1 knows."""
    assert norm._known("bu") is not None and norm._known("gün") is not None
    assert norm._known("bugün") is not None
    assert norm._split("bugün") is None


@pytest.mark.parametrize(
    "word",
    [
        "silver",  # sil + ver: a verb is never the first half
        "arabul",
        "zxcvalarm",  # one half unknown
        "alarmzxcv",
        "evo",  # a half shorter than two letters
        "kapatma",  # a negative form is never a split
        "ekranıkapatma",
        "hesapmakinesinialarm",  # three words: at most one split, and both halves known
    ],
)
def test_what_is_never_split(word):
    n = normalize(word)
    assert n.tokens == (word,) and n.applied_splits == ()


def test_every_word_of_the_vocabulary_survives_normalisation_whole():
    for word in (*norm._VERBS, *norm._TABLE_VERBS, *norm._NOUN_STEMS, *norm._WORDS):
        assert normalize(word).applied_splits == (), word


# ------------------------------------------------------------------ the negative forms


@pytest.mark.parametrize(
    "word", ["kapatma", "kapatmayın", "kapatmayınız", "unutma", "açmasana", "silme", "silmez"]
)
def test_a_negative_form_is_known_as_one_and_keeps_its_surface(word):
    assert norm.is_negative(word)
    (lem,) = normalize(word).lemmas
    assert (lem.stem, lem.suffixes, lem.kind) == (word, (), "other")


@pytest.mark.parametrize("word", ["kapat", "kapatın", "araştırma", "araştırmayı", "makine", "mama"])
def test_what_is_not_a_negative_form(word):
    assert not norm.is_negative(word)


# ------------------------------------------------------------------ the reading the rules get


def test_lemma_reading_rewrites_only_the_polite_verb_and_the_fused_word():
    reading = norm.lemma_reading("Ofis bilgisayarımda Hesapmakinesini açar mısınız?")
    assert reading is not None
    assert reading.text == "Ofis bilgisayarımda Hesap makinesini aç?"
    assert reading.dropped == (("açar mısınız", "aç"),)
    assert reading.splits == (("hesapmakinesini", "hesap makinesini"),)


def test_lemma_reading_is_none_when_layer_one_changes_nothing():
    assert norm.lemma_reading("Hesap makinesini aç.") is None
    assert norm.lemma_reading("Ekranı kapatır.") is None  # an aorist with no question: a statement
    assert norm.lemma_reading("") is None


def test_lemma_reading_keeps_the_forms_the_caller_names():
    assert norm.lemma_reading("Bunu yapabilir misin?", keep=frozenset({"yapabilir"})) is None
    assert norm.lemma_reading("Bunu yapabilir misin?") is not None


def test_lemma_reading_of_a_sentence_that_says_dont_is_none():
    assert norm.lemma_reading("Ekranları kapatma, sesi açın.") is None
    assert norm.lemma_reading("Ekranları kapatmayın ama sesi açın.") is None
    assert norm.lemma_reading("Sesi açın, ekranları kapatma") is None
    # "indirme" before a noun is the verbal noun (the downloads folder), not "don't download".
    reading = norm.lemma_reading("İndirme klasörünü gösterin.")
    assert reading is not None and reading.dropped == (("gösterin", "göster"),)


# ------------------------------------------------------------------ the protocol file


def test_confusions_file_validates_against_its_schema():
    data = json.loads(SHARED.read_text(encoding="utf-8"))
    assert isinstance(data["entries"], list) and data["entries"]
    heard = []
    for e in data["entries"]:
        assert set(e) == {"heard", "meant", "seen_at", "sentence"}
        assert all(isinstance(v, str) and v.strip() for v in e.values())
        assert e["heard"] == e["heard"].casefold() and " " not in e["heard"]
        assert e["heard"] != e["meant"]
        heard.append(e["heard"])
    assert len(heard) == len(set(heard)), "duplicate heard entries"


def test_seed_entry_is_the_trial_confusion():
    assert load_confusions(SHARED)["ofisü"] == "ofis"


def test_default_reads_the_list_beside_the_module():
    assert load_confusions() == load_confusions(SHARED)
    assert normalize(TRIAL).applied_confusions == (("ofisü", "ofis"),)


def test_explicit_empty_table_applies_nothing():
    n = normalize(TRIAL, confusions={})
    assert n.applied_confusions == ()
    assert lemma_tokens(TRIAL, confusions={})[0] == "ofisü"
