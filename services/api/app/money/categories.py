"""Where the money went: a small fixed set of categories, from a merchant or from words.

A bank prints the merchant ("MIGROS ATASEHIR", "SHELL MODA"); the owner says the place
("markete", "benzine"). Both land on one of the keys below, or on None (uncategorised - it is
still counted in "bu ay ne kadar harcadım"). The match is on whole words, folded the way an
ASR without Turkish letters spells them, never on a stem ("market" is not "marketing").
"""

from __future__ import annotations

import re
from typing import Final

MARKET: Final = "market"
FOOD: Final = "yemek"
FUEL: Final = "akaryakıt"
PHARMACY: Final = "eczane"
CLOTHES: Final = "giyim"
TRANSPORT: Final = "ulaşım"
BILLS: Final = "fatura"
CATEGORIES: Final[tuple[str, ...]] = (MARKET, FOOD, FUEL, PHARMACY, CLOTHES, TRANSPORT, BILLS)

#: The dative a sentence says the category in: "bu ay MARKETE ne harcadım".
SPOKEN: Final[dict[str, str]] = {
    MARKET: "markete",
    FOOD: "yemeğe",
    FUEL: "akaryakıta",
    PHARMACY: "eczaneye",
    CLOTHES: "giyime",
    TRANSPORT: "ulaşıma",
    BILLS: "faturalara",
}

_FOLD: Final = str.maketrans("çğıöşüâîû", "cgiosuaiu")

#: folded word -> category. Merchant names (as banks print them) and the owner's words.
_WORDS: Final[dict[str, str]] = {
    **dict.fromkeys(
        (
            "market",
            "markete",
            "marketten",
            "markette",
            "migros",
            "a101",
            "bim",
            "sok",
            "carrefour",
            "carrefoursa",
            "file",
            "macrocenter",
            "metro",
            "manav",
            "manava",
            "bakkal",
            "bakkala",
            "pazar",
            "pazara",
            "pazardan",
            "kasap",
            "kasaba",
            "hakmar",
            "tarim",
        ),
        MARKET,
    ),
    **dict.fromkeys(
        (
            "yemek",
            "yemege",
            "restoran",
            "restorana",
            "lokanta",
            "lokantaya",
            "cafe",
            "kafe",
            "kafeye",
            "kahve",
            "starbucks",
            "yemeksepeti",
            "burger",
            "pizza",
            "doner",
            "kebap",
        ),
        FOOD,
    ),
    **dict.fromkeys(
        (
            "akaryakit",
            "akaryakita",
            "benzin",
            "benzine",
            "mazot",
            "mazota",
            "shell",
            "opet",
            "bp",
            "petrol",
            "aytemiz",
            "total",
            "totalenergies",
        ),
        FUEL,
    ),
    **dict.fromkeys(("eczane", "eczaneye", "ilac", "ilaca"), PHARMACY),
    **dict.fromkeys(
        (
            "giyim",
            "giyime",
            "lcw",
            "waikiki",
            "koton",
            "defacto",
            "zara",
            "mavi",
            "boyner",
            "ceket",
            "ayakkabi",
            "pantolon",
            "gomlek",
        ),
        CLOTHES,
    ),
    **dict.fromkeys(
        ("ulasim", "ulasima", "taksi", "taksiye", "uber", "bitaksi", "istanbulkart", "otopark"),
        TRANSPORT,
    ),
    **dict.fromkeys(
        (
            "fatura",
            "faturalara",
            "faturaya",
            "enerjisa",
            "igdas",
            "iski",
            "turkcell",
            "vodafone",
            "telekom",
            "elektrik",
            "dogalgaz",
        ),
        BILLS,
    ),
}


def fold(word: str) -> str:
    return word.replace("İ", "i").replace("I", "ı").lower().translate(_FOLD)


def categorize(said: str | None) -> str | None:
    """The category a merchant or a sentence names, or None."""
    if not said:
        return None
    for word in re.findall(r"[\wçğıöşüÇĞİÖŞÜ]+", said):
        if category := _WORDS.get(fold(word)):
            return category
    return None


def normalize(category: str | None) -> str | None:
    """A category the model or a form sent ("Market", "akaryakit") -> a key, or None."""
    if not category or not isinstance(category, str):
        return None
    folded = fold(category.strip())
    for key in CATEGORIES:
        if fold(key) == folded:
            return key
    return categorize(category)


__all__ = ["CATEGORIES", "SPOKEN", "categorize", "normalize"]
