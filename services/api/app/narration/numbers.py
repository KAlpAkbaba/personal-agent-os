"""Deterministic Turkish cardinal / ordinal number-to-words engine.

Pure functions, no randomness, no locale library. Turkish number formation has
two irregularities the code below encodes explicitly:

- the hundreds digit ``1`` is spoken "yüz", never "bir yüz";
- exactly one thousand is spoken "bin", never "bir bin" (but 1.000.000 IS
  "bir milyon" — the ``bir`` is only dropped for "yüz" and "bin").

Ordinals reuse the cardinal reading and make only the LAST word ordinal, with a
vowel-harmony suffix plus the handful of consonant-softening irregulars
(dört -> dördüncü, and the exact number words).
"""

from __future__ import annotations

_ONES = ("", "bir", "iki", "üç", "dört", "beş", "altı", "yedi", "sekiz", "dokuz")
_TENS = ("", "on", "yirmi", "otuz", "kırk", "elli", "altmış", "yetmiş", "seksen", "doksan")
# index == number of 3-digit groups from the right (0 -> units, 1 -> bin, ...)
_SCALES = ("", "bin", "milyon", "milyar", "trilyon", "katrilyon", "kentilyon")

_DIGIT_WORD = {
    "0": "sıfır",
    "1": "bir",
    "2": "iki",
    "3": "üç",
    "4": "dört",
    "5": "beş",
    "6": "altı",
    "7": "yedi",
    "8": "sekiz",
    "9": "dokuz",
}

# Explicit ordinal forms for the atomic number words (covers consonant softening
# such as dört -> dördüncü that a pure suffix rule would get wrong).
_ORDINAL_LAST_WORD = {
    "bir": "birinci",
    "iki": "ikinci",
    "üç": "üçüncü",
    "dört": "dördüncü",
    "beş": "beşinci",
    "altı": "altıncı",
    "yedi": "yedinci",
    "sekiz": "sekizinci",
    "dokuz": "dokuzuncu",
    "on": "onuncu",
    "yirmi": "yirminci",
    "otuz": "otuzuncu",
    "kırk": "kırkıncı",
    "elli": "ellinci",
    "altmış": "altmışıncı",
    "yetmiş": "yetmişinci",
    "seksen": "sekseninci",
    "doksan": "doksanıncı",
    "yüz": "yüzüncü",
    "bin": "bininci",
    "milyon": "milyonuncu",
    "milyar": "milyarıncı",
    "trilyon": "trilyonuncu",
}

_BACK_UNROUNDED = set("aı")
_BACK_ROUNDED = set("ou")
_FRONT_UNROUNDED = set("ei")
_FRONT_ROUNDED = set("öü")
_VOWELS = _BACK_UNROUNDED | _BACK_ROUNDED | _FRONT_UNROUNDED | _FRONT_ROUNDED


def _three_digits(n: int) -> str:
    """Words for 0..999 (empty string for 0)."""
    parts: list[str] = []
    hundreds, rem = divmod(n, 100)
    tens, ones = divmod(rem, 10)
    if hundreds:
        parts.append("yüz" if hundreds == 1 else f"{_ONES[hundreds]} yüz")
    if tens:
        parts.append(_TENS[tens])
    if ones:
        parts.append(_ONES[ones])
    return " ".join(parts)


def cardinal(n: int) -> str:
    """Turkish cardinal reading of an integer (negatives -> 'eksi ...')."""
    if n == 0:
        return "sıfır"
    negative = n < 0
    magnitude = abs(n)
    remaining = magnitude
    groups: list[int] = []
    while remaining > 0:
        remaining, rem = divmod(remaining, 1000)
        groups.append(rem)
    if len(groups) > len(_SCALES):
        # Beyond the named scales: fall back to digit-by-digit rather than lie.
        spoken = digit_by_digit(str(magnitude))
        return f"eksi {spoken}" if negative else spoken
    parts: list[str] = []
    for i in range(len(groups) - 1, -1, -1):
        g = groups[i]
        if g == 0:
            continue
        if i == 1 and g == 1:
            parts.append("bin")  # "bin", never "bir bin"
        elif i == 0:
            parts.append(_three_digits(g))
        else:
            parts.append(f"{_three_digits(g)} {_SCALES[i]}")
    reading = " ".join(parts)
    return f"eksi {reading}" if negative else reading


def _ordinal_suffix(word: str) -> str:
    """Vowel-harmony ordinal suffix fallback for a word not in the irregular map."""
    last_vowel = ""
    for ch in reversed(word):
        if ch in _VOWELS:
            last_vowel = ch
            break
    if last_vowel in _BACK_UNROUNDED:
        vowel = "ı"
    elif last_vowel in _BACK_ROUNDED:
        vowel = "u"
    elif last_vowel in _FRONT_ROUNDED:
        vowel = "ü"
    else:
        vowel = "i"
    ends_with_vowel = bool(word) and word[-1] in _VOWELS
    if ends_with_vowel:
        return f"{word}nc{vowel}"
    return f"{word}{vowel}nc{vowel}"


def ordinal(n: int) -> str:
    """Turkish ordinal reading, e.g. 2 -> 'ikinci', 31 -> 'otuz birinci'."""
    words = cardinal(n).split()
    if not words:
        return cardinal(n)
    last = words[-1]
    words[-1] = _ORDINAL_LAST_WORD.get(last) or _ordinal_suffix(last)
    return " ".join(words)


#: Consonants after which a suffix's initial d/c hardens to t/ç (Turkish consonant
#: assimilation). "beş" + "de" is "beşte"; "bin" + "de" is "binde".
_VOICELESS = set("fstkçşhp")


def _last_vowel(word: str) -> str:
    for ch in reversed(word.lower()):
        if ch in _VOWELS:
            return ch
    return ""


def _harmonise_vowel(vowel: str, last: str, *, two_way: bool) -> str:
    """The Turkish vowel this suffix vowel becomes after a word ending in ``last``.

    ``two_way`` is the a/e alternation (-de/-da, -den/-dan, -e/-a, -ler/-lar); the
    four-way alternation (-i/-ı/-u/-ü, -lik/-lık/-luk/-lük) is the other one.
    """
    if two_way:
        return "a" if last in _BACK_UNROUNDED or last in _BACK_ROUNDED else "e"
    if last in _BACK_UNROUNDED:
        return "ı"
    if last in _BACK_ROUNDED:
        return "u"
    if last in _FRONT_ROUNDED:
        return "ü"
    return "i"


_TWO_WAY = set("ae")
_FOUR_WAY = set("ıiuü")


def attach_suffix(word: str, suffix: str) -> str:
    """B21 req 230: join a written suffix to a spoken number, in harmony.

    Turkish writes a suffix on a numeral with an apostrophe — "1.000'den", "3'ü",
    "08:45'te", "2'şer" — and the normaliser used to convert the number and leave the
    apostrophe standing: "bin'den", "üç'ü", "sekiz kırk beş'te". A TTS reads that as a
    break, a glottal stop, or the word "kesme"; none of them is Turkish.

    Joining alone is not enough, because the author harmonised the suffix to the DIGITS
    they wrote and the conversion changes what the last word is. "20:00'de" becomes
    "yirmi sıfır sıfır" + "de", and Turkish wants "sıfırda". So the suffix's own vowels
    are re-harmonised to the word that now precedes them, and an initial d/c hardens
    after a voiceless consonant. Nothing is invented: the suffix's CONSONANTS and its
    alternation class come from what the author wrote.
    """
    word = word.rstrip()
    suffix = suffix.strip()
    if not suffix:
        return word
    if not word:
        return suffix
    last = _last_vowel(word.split()[-1] if word.split() else word)
    if not last:
        return f"{word}{suffix}"
    tail = word[-1].lower()
    out: list[str] = []
    for index, ch in enumerate(suffix):
        lower = ch.lower()
        if lower in _TWO_WAY:
            out.append(_harmonise_vowel(lower, last, two_way=True))
        elif lower in _FOUR_WAY:
            out.append(_harmonise_vowel(lower, last, two_way=False))
        elif index == 0 and lower in ("d", "c") and tail in _VOICELESS:
            out.append("t" if lower == "d" else "ç")
        elif index == 0 and lower in ("t", "ç") and tail not in _VOICELESS and tail in _VOWELS:
            # The author wrote the hard form after a digit; after a vowel Turkish softens
            # it back ("6'ta" -> "altıda").
            out.append("d" if lower == "t" else "c")
        else:
            out.append(ch)
    return word + "".join(out)


def digit_by_digit(digits: str) -> str:
    """Read a run of characters digit by digit ('05' -> 'sıfır beş')."""
    out = [_DIGIT_WORD[c] for c in digits if c in _DIGIT_WORD]
    return " ".join(out)


def decimal(int_part: str, frac_part: str) -> str:
    """Read a comma-decimal number: '17', '2' -> 'on yedi virgül iki'."""
    whole = cardinal(int(int_part)) if int_part else "sıfır"
    # A leading zero in the fraction is meaningful ("05" -> "sıfır beş"), so read
    # such fractions digit by digit; otherwise read the fraction as a cardinal.
    if len(frac_part) > 1 and frac_part[0] == "0":
        frac = digit_by_digit(frac_part)
    else:
        frac = cardinal(int(frac_part)) if frac_part else "sıfır"
    return f"{whole} virgül {frac}"


__all__ = ["attach_suffix", "cardinal", "ordinal", "decimal", "digit_by_digit"]
