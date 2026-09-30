"""ADR-0224 layer 1: the closed Turkish suffix stripper and the STT confusion list."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app import protocol_files
from app.voice.understanding import normalize as norm
from app.voice.understanding.normalize import lemma_tokens, load_confusions, normalize

SHARED = Path(__file__).resolve().parents[4] / "packages" / "protocol" / "stt-confusions.json"
TRIAL = "Ofisü bilgisayarında hesap makinesini açın."


@pytest.fixture(autouse=True)
def _bundled_confusions(monkeypatch):
    """The lead registers the file in BUNDLED at merge; until then read the shared copy."""
    monkeypatch.setattr(protocol_files, "BUNDLED", (*protocol_files.BUNDLED, norm.CONFUSIONS_FILE))
    monkeypatch.setattr(protocol_files, "BUNDLE_DIR", SHARED.parent)
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


def test_default_reads_through_protocol_file():
    assert load_confusions() == load_confusions(SHARED)
    assert normalize(TRIAL).applied_confusions == (("ofisü", "ofis"),)


def test_explicit_empty_table_applies_nothing():
    n = normalize(TRIAL, confusions={})
    assert n.applied_confusions == ()
    assert lemma_tokens(TRIAL, confusions={})[0] == "ofisü"
