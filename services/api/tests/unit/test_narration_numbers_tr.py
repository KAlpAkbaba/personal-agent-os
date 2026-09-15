"""B21 req 230: Turkish number pronunciation, measured rather than assumed.

The matrix says "kısmi" and it was right, but not about the number engine — `numbers.py`
reads cardinals, ordinals, decimals and digit runs correctly, and the normaliser's twenty-
one stages cover money, dates, clocks, percentages, IPs, versions and phone numbers. What
was partial is what the pipeline did with the characters AROUND a number, and each gap was
found by reading a corpus of ordinary Turkish out loud rather than by reading the code:

* **the apostrophe.** "1.000'den fazla" became "bin'den fazla". Turkish writes a suffix on
  a numeral with an apostrophe and speaks it joined, and the joining is not concatenation:
  the author harmonised the suffix to the DIGITS, and after conversion the preceding word
  is different ("20:00'de" → "yirmi sıfır sıfır" + "de" wants "sıfırda").
* **the minus sign.** "-3°C" became "-üç". `cardinal()` has said "eksi" for a negative
  since M4 and no rule ever handed it one, because a sign is not part of `\\d+`. Below zero
  is exactly where a temperature has to be right.
* **the degree symbol.** "21°C" became "yirmi bir" — the word that makes it a temperature
  fell silent, in a system that reads a weather line every morning.
* **the solidus.** "3/4" became "üç/dört".

Every case below is a sentence this system actually produces: a weather line, a briefing
figure, an alarm time, a report ratio.
"""

from __future__ import annotations

import pytest

from app.narration.normalizer import normalize
from app.narration.numbers import attach_suffix, cardinal, ordinal

# ------------------------------------------------------------- the apostrophe


@pytest.mark.parametrize(
    ("written", "spoken"),
    [
        ("1.000'den fazla", "binden fazla"),
        ("3'ü tamamlandı", "üçü tamamlandı"),
        ("5'lik paket", "beşlik paket"),
        ("2'şer tane", "ikişer tane"),
        ("10'ar dakika", "onar dakika"),
        ("Saat 08:45'te başlıyor", "Saat sekiz kırk beşte başlıyor"),
        # The one that concatenation alone gets wrong: the suffix was harmonised to the
        # digits, and the word in front of it is now "sıfır".
        ("20:00'de", "yirmi sıfır sıfırda"),
        ("6'ta", "altıda"),
    ],
)
def test_a_suffix_written_on_a_numeral_is_spoken_joined_and_in_harmony(written, spoken):
    assert normalize(written) == spoken


def test_ordinary_turkish_apostrophes_are_left_alone():
    # The rule removes an apostrophe, and Turkish uses it everywhere. Only the closed set
    # of number words is touched.
    assert normalize("Ahmet'in evi") == "Ahmet'in evi"
    assert normalize("PagentOS'un sesi") == "PagentOS'un sesi"
    assert normalize("İstanbul'da yağmur var") == "İstanbul'da yağmur var"


def test_the_harmoniser_answers_for_itself():
    assert attach_suffix("bin", "den") == "binden"
    assert attach_suffix("üç", "ü") == "üçü"
    assert attach_suffix("dokuz", "da") == "dokuzda"
    # d hardens to t after a voiceless consonant, and softens back after a vowel.
    assert attach_suffix("beş", "de") == "beşte"
    assert attach_suffix("altı", "ta") == "altıda"
    # A word with no vowel cannot be harmonised to; it is joined as written rather than
    # guessed at.
    assert attach_suffix("", "de") == "de"


# ----------------------------------------------------------------- the sign


@pytest.mark.parametrize(
    ("written", "spoken"),
    [
        ("-5 derece", "eksi beş derece"),
        ("−7 derece", "eksi yedi derece"),  # U+2212, what a copy-paste from a forecast has
        ("-12,5 derece", "eksi on iki virgül beş derece"),
        ("bugün -3°C olacak", "bugün eksi üç derece olacak"),
    ],
)
def test_a_negative_number_is_read_as_negative(written, spoken):
    assert normalize(written) == spoken


def test_a_hyphen_between_numbers_is_still_a_range(written="5-10 arası"):
    # The sign rule must not eat a range. "beş eksi on" would be a different claim.
    assert normalize(written) == "beş-on arası"


def test_an_iso_date_is_still_a_date():
    assert normalize("2026-09-13 tarihinde") == "on üç Eylül iki bin yirmi altı tarihinde"


# --------------------------------------------------------------- the degree


@pytest.mark.parametrize(
    ("written", "spoken"),
    [
        ("Sıcaklık 21°C.", "Sıcaklık yirmi bir derece."),
        ("25 °C", "yirmi beş derece"),
        ("0°", "sıfır derece"),
        ("68°F", "altmış sekiz fahrenhayt derece"),
    ],
)
def test_a_temperature_says_the_word_that_makes_it_one(written, spoken):
    assert normalize(written) == spoken


# -------------------------------------------------------------- the solidus


@pytest.mark.parametrize(
    ("written", "spoken"),
    [
        ("3/4 tamamlandı", "dörtte üç tamamlandı"),
        ("1/4 oranında", "dörtte bir oranında"),
        ("2/3 çoğunluk", "üçte iki çoğunluk"),
        # Idioms, both of which the mechanical reading gets wrong — and "ikide bir" is not
        # merely stiff, it is a different Turkish expression.
        ("7/24 açık", "yedi yirmi dört açık"),
        ("1/2 litre süt", "yarım litre süt"),
    ],
)
def test_a_fraction_is_read_denominator_first(written, spoken):
    assert normalize(written) == spoken


@pytest.mark.parametrize(
    "written",
    ["A/B testi", "km/s hızla", "13/09/2026 tarihinde", "192.168.1.20/24"],
)
def test_a_slash_that_is_not_a_fraction_is_not_read_as_one(written):
    spoken = normalize(written)
    assert "de " not in spoken.replace("tarihinde", "") or "/" in written
    # The specific claims, so a future change to the guards fails here loudly:
    if written.startswith("A/B"):
        assert spoken == "A/B testi"
    if written.startswith("km/s"):
        assert spoken == "km/s hızla"
    if written.startswith("13/09"):
        assert "Eylül" not in spoken and "dokuzda" not in spoken
    if written.startswith("192."):
        assert spoken.endswith("bölü yirmi dört")


# ------------------------------------------------- nothing that worked is broken


@pytest.mark.parametrize(
    ("written", "spoken"),
    [
        ("1.250.000 lira", "bir milyon iki yüz elli bin lira"),
        ("yüzde 3,42", "yüzde üç virgül kırk iki"),
        ("%20 indirim", "yüzde yirmi indirim"),
        ("1.500 TL ödedim", "bin beş yüz Türk lirası ödedim"),
        ("v1.2.3 sürümü", "sürüm bir nokta iki nokta üç sürümü"),
        ("15 Eylül 2026", "on beş Eylül iki bin yirmi altı"),
        ("3. madde önemli", "üçüncü madde önemli"),
        ("18'inci kat", "on sekizinci kat"),
    ],
)
def test_the_stages_that_already_worked_still_do(written, spoken):
    assert normalize(written) == spoken


def test_the_number_engine_itself_is_unchanged():
    assert cardinal(-5) == "eksi beş"
    assert cardinal(1_250_000) == "bir milyon iki yüz elli bin"
    assert ordinal(4) == "dördüncü"
