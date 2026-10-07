"""Turkish amounts, in kuruş: "1.234,56 TL", "₺300", "yedi yüz elli lira", "bin buçuk".

Money is kept as an integer number of kuruş (1 TL = 100 kuruş): a float would turn
"0,10 + 0,20" into a spend that never matches a bank mail. Two readers:

* :func:`parse_amount` - the digits a bank mail writes ("1.234,56 TL"; a dot groups
  thousands, a comma starts the kuruş; an English-format "TRY 1,234.56" too);
* :func:`words_amount` - the words the transcript carries (the tr-TR normaliser turns
  "750" into "yedi yüz elli" before the router sees it).

:func:`money_in` finds the amounts a SENTENCE says: a number followed by a currency word
("lira", "TL", "türk lirası", "₺"), or - when the caller says the sentence answers a price
question - a bare number that is not a count of something ("iki tane", "üç kilo").

Pure: no database, no network.
"""

from __future__ import annotations

import re
from typing import Final

_DIGITS: Final = re.compile(r"^[0-9]+$")
_CURRENCY_MARKS: Final = ("tl", "try", "₺")

#: Arabic-Indic (٠-٩) and Persian (۰-۹) digits are read as ASCII; any other non-ASCII digit
#: ("７５０", "७५०") is refused - ``\d`` and ``int()`` would take it silently.
_ARABIC_DIGITS: Final = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "0123456789" * 2)

#: Longer than a bank ever writes an amount: refused before any int() (Python refuses a
#: string of more than 4300 digits with a ValueError - a 500 at the route).
MAX_AMOUNT_CHARS: Final = 64

UNITS: Final[dict[str, int]] = {
    "sıfır": 0,
    "bir": 1,
    "iki": 2,
    "üç": 3,
    "dört": 4,
    "beş": 5,
    "altı": 6,
    "yedi": 7,
    "sekiz": 8,
    "dokuz": 9,
}
TENS: Final[dict[str, int]] = {
    "on": 10,
    "yirmi": 20,
    "otuz": 30,
    "kırk": 40,
    "elli": 50,
    "altmış": 60,
    "yetmiş": 70,
    "seksen": 80,
    "doksan": 90,
}
_SCALES: Final[dict[str, int]] = {"bin": 1000, "milyon": 1_000_000}
_NUMBER_WORDS: Final = frozenset({*UNITS, *TENS, "yüz", *_SCALES, "buçuk"})

#: A word after a number that makes it a COUNT, not a price ("iki kilo", "üç tane").
_COUNT_WORDS: Final = frozenset(
    {
        "tane",
        "adet",
        "kilo",
        "kg",
        "gram",
        "gr",
        "litre",
        "lt",
        "metre",
        "paket",
        "kutu",
        "şişe",
        "saat",
        "dakika",
        "saniye",
        "gün",
        "hafta",
        "ay",
        "yıl",
        "sene",
        "kişi",
        "kere",
        "kez",
        "numara",
        "beden",
        "yaş",
        "yaşında",
        "kat",
        "porsiyon",
        "dilim",
        "parça",
    }
)

#: Currency words as the transcript says them; a suffix after an apostrophe is cut first.
_LIRA_WORDS: Final = frozenset(
    {"lira", "liraya", "liradan", "lirası", "liralık", "lirayı", "lirasına", "tl", "try", "₺"}
)


def parse_amount(raw: str) -> int | None:
    """Digits as a bank writes them -> kuruş; None for anything that is not one amount."""
    text = raw.strip()
    if len(text) > MAX_AMOUNT_CHARS:
        return None
    text = text.translate(_ARABIC_DIGITS)
    for mark in ("TL", "TRY", "₺", "tl", "try"):
        text = text.replace(mark, " ")
    text = text.strip().rstrip("'").strip()
    if not text or not re.fullmatch(r"[0-9.,]+", text) or not re.search(r"[0-9]", text):
        return None
    if "," in text and "." in text:
        decimal = "," if text.rfind(",") > text.rfind(".") else "."
        group = "." if decimal == "," else ","
        whole, _, frac = text.rpartition(decimal)
        if decimal in whole:
            return None
        groups = whole.split(group)
    elif "," in text:
        whole, _, frac = text.rpartition(",")
        if "," in whole:
            return None
        groups = [whole]
    elif "." in text:
        parts = text.split(".")
        if all(len(p) == 3 for p in parts[1:]) and parts[0]:
            groups, frac = parts, ""
        elif len(parts) == 2 and 1 <= len(parts[1]) <= 2:
            groups, frac = [parts[0]], parts[1]
        else:
            return None
    else:
        groups, frac = [text], ""
    if not groups[0] or not all(_DIGITS.match(g) for g in groups):
        return None
    if len(groups) > 1 and (len(groups[0]) > 3 or any(len(g) != 3 for g in groups[1:])):
        return None
    if frac and (not _DIGITS.match(frac) or len(frac) > 2):
        return None
    lira = int("".join(groups))
    kurus = int(frac.ljust(2, "0")) if frac else 0
    return lira * 100 + kurus


def _words_whole(words: list[str]) -> int | None:
    """Turkish number words -> an integer; None when they are not one number."""
    if not words:
        return None
    total = 0
    chunk = 0
    seen = False
    for word in words:
        if word in UNITS:
            chunk += UNITS[word]
        elif word in TENS:
            chunk += TENS[word]
        elif word == "yüz":
            chunk = (chunk or 1) * 100
        elif word in _SCALES:
            scale = _SCALES[word]
            total += (chunk or 1) * scale
            chunk = 0
        else:
            return None
        seen = True
    return total + chunk if seen else None


def words_amount(words: list[str]) -> int | None:
    """ "yedi yüz elli" -> 75000; "bin buçuk" -> 150000; "... virgül elli" -> kuruş."""
    if not words:
        return None
    frac = 0
    if "virgül" in words:
        at = words.index("virgül")
        cents = _words_whole(words[at + 1 :])
        if cents is None or cents > 99:
            return None
        frac = cents
        words = words[:at]
    half = False
    if words and words[-1] == "buçuk":
        half = True
        words = words[:-1]
    whole = _words_whole(words)
    if whole is None:
        return None
    kurus = whole * 100 + frac
    if half:
        if whole >= 1000 and whole % 1000 == 0:
            kurus += 500 * 100
        elif whole >= 100 and whole % 100 == 0:
            kurus += 50 * 100
        else:
            kurus += 50
    return kurus


def _clean(word: str) -> str:
    word = word.replace("İ", "i").replace("I", "ı").lower()
    word = re.split(r"['’`]", word, maxsplit=1)[0]
    return word.strip('.,!?;:()"')


def _tokens(sentence: str) -> list[str]:
    spaced = re.sub(r"₺\s*", " ₺ ", sentence)
    return [w for w in (_clean(raw) for raw in spaced.split()) if w]


def _digit_token(word: str) -> int | None:
    return parse_amount(word) if re.fullmatch(r"[\d.,]+", word) else None


def money_in(sentence: str, *, bare: bool = False) -> list[int]:
    """Every amount the sentence says, in order, in kuruş.

    ``bare`` - the sentence answers a price question - lets a number without a currency
    word count, unless a count word follows it ("iki tane")."""
    words = _tokens(sentence)
    found: list[int] = []
    i = 0
    while i < len(words):
        word = words[i]
        if word == "₺" and i + 1 < len(words) and (value := _digit_token(words[i + 1])):
            found.append(value)
            i += 2
            continue
        if word in ("tl", "try") and i + 1 < len(words) and (value := _digit_token(words[i + 1])):
            found.append(value)
            i += 2
            continue
        start = i
        value: int | None = None
        if (digit := _digit_token(word)) is not None:
            value = digit
            i += 1
        else:
            j = i
            while j < len(words) and (words[j] in _NUMBER_WORDS or words[j] == "virgül"):
                j += 1
            if j > i:
                value = words_amount(words[i:j])
                i = j
        if value is None:
            i = max(i, start + 1)
            continue
        following = words[i] if i < len(words) else ""
        if following in _LIRA_WORDS or following.startswith("lira"):
            found.append(value)
            i += 1
            if i < len(words) and words[i] in ("lirası", "lira"):
                i += 1
        elif following in ("türk",) and i + 1 < len(words) and words[i + 1].startswith("lira"):
            found.append(value)
            i += 2
        elif bare and following not in _COUNT_WORDS and value > 0 and words[start:i] != ["bir"]:
            # A lone "bir" is the article ("bir düşüneyim"), never a price of one lira.
            found.append(value)
    return found


def format_tl(kurus: int) -> str:
    """75000 -> "750 TL"; 123456 -> "1.234,56 TL" (as a bank writes and a voice says)."""
    lira, rest = divmod(abs(int(kurus)), 100)
    grouped = f"{lira:,}".replace(",", ".")
    sign = "-" if kurus < 0 else ""
    return f"{sign}{grouped},{rest:02d} TL" if rest else f"{sign}{grouped} TL"


def format_liralik(kurus: int) -> str:
    """ "750 liralık" / "1.234,56 liralık" - the question's adjective."""
    return format_tl(kurus).removesuffix(" TL") + " liralık"


__all__ = ["format_liralik", "format_tl", "money_in", "parse_amount", "words_amount"]
