"""How a macro's name is kept and how a sentence is recognised as naming one (ADR-0196).

Pure: no database, no session. Two questions and nothing else:

* ``name_key`` - the one canonical spelling a name is stored and looked up under, so
  "Yeni Mail Sekmesi", "yeni mail sekmesi." and the ASR's "yeni mail sekmesi" (no
  diacritics, no casing) are one macro and never three.
* ``match_stored_name`` - whether a sentence IS one of the stored names plus, at most,
  a run word ("aç", "çalıştır", "hareketini yap"). The whole sentence has to be accounted
  for: a stored name buried in a longer sentence is not a request to run it, or the
  macro named "sağ tuş" would steal "sağ tuşuna bas" from the key press. Turkish
  suffixes are matched only on the name's LAST word and only from the closed list below
  ("sekmesi" said as "sekmesini"), never by prefix (see the repo's "unut"/"unutma"
  lesson).
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Final

_DIACRITICS: Final = str.maketrans("çğıöşüâîû", "cgiosuaiu")
_NON_WORD_RE: Final = re.compile(r"[^a-z0-9 ]+")
_EDGE_PUNCT_RE: Final = re.compile(r"^[\s\.\,\!\?\:\;\-\"'«»]+|[\s\.\,\!\?\:\;\-\"'«»]+$")
#: A name is a label the owner will SAY: eight words is already a sentence.
MAX_NAME_TOKENS: Final = 8
MAX_NAME_CHARS: Final = 80

#: Words a naming sentence carries AROUND the name ("adı yeni mail sekmesi olsun",
#: "yeni mail sekmesi hareketi") - dropped from the edges only, so a name that starts
#: with "yeni" keeps it.
NAME_FRAME_WORDS: Final[frozenset[str]] = frozenset(
    {
        "adı",
        "adi",
        "ismi",
        "isim",
        "ad",
        "olsun",
        "olarak",
        "kaydet",
        "diye",
        "hareketi",
        "hareket",
        "hareketini",
        "bu",
        "şu",
        "su",
        "o",
        "efendim",
        "lütfen",
        "lutfen",
        "koy",
        "ver",
        "de",
        "da",
    }
)
#: Words a RUN sentence may carry beside the stored name and nothing else.
RUN_FILLER_WORDS: Final[frozenset[str]] = frozenset(
    {
        "aç",
        "ac",
        "yap",
        "çalıştır",
        "calistir",
        "başlat",
        "baslat",
        "oynat",
        "tekrarla",
        "uygula",
        "yürüt",
        "yurut",
        "et",
        "lütfen",
        "lutfen",
        "hareketini",
        "hareketi",
        "hareket",
        "bir",
        "daha",
        "şimdi",
        "simdi",
        "hadi",
        "efendim",
        "hemen",
        "yine",
        "tekrar",
    }
)
#: Case/possessive endings the name's LAST word may carry inside a sentence.
LAST_WORD_SUFFIXES: Final[tuple[str, ...]] = (
    "ni",
    "nı",
    "nu",
    "nü",
    "yi",
    "yı",
    "yu",
    "yü",
    "ne",
    "na",
    "ye",
    "ya",
    "de",
    "da",
    "den",
    "dan",
    "nde",
    "nda",
    "nden",
    "ndan",
    "i",
    "ı",
    "u",
    "ü",
    "e",
    "a",
)


#: Names a macro can never have (security review, 2026-09-21): the M4 stop words and the
#: conversation's own control words. A recording ended and answered with "dur" is the
#: owner STOPPING something, not naming it - and a stored macro called "dur" would run in
#: place of every later emergency stop, for ever. ``spoken_name`` returns nothing for
#: these (the router then reads the word as what it is), and ``match_stored_name`` never
#: matches one even if a row somehow carries it.
RESERVED_NAME_KEYS: Final[frozenset[str]] = frozenset(
    {
        "dur",
        "kes",
        "sus",
        "yeter",
        "tamam dur",
        "sustur",
        "durdur",
        "tamam",
        "devam",
        "iptal",
        "vazgeç",
        "vazgec",
        "evet",
        "hayır",
        "hayir",
        "tekrar",
        "yeniden",
        "bir daha",
        "peki",
    }
)


def fold(word: str) -> str:
    """Casefolded and diacritic-free, the same way for a stored name and a heard one."""
    lowered = word.replace("I", "ı").replace("İ", "i").casefold()
    # "İ".casefold() leaves a combining dot behind; a stored "İstanbul" must meet "istanbul".
    return lowered.replace("\N{COMBINING DOT ABOVE}", "").translate(_DIACRITICS)


_NAME_FRAME_FOLDED: Final[frozenset[str]] = frozenset(fold(w) for w in NAME_FRAME_WORDS)
_RESERVED_FOLDED: Final[frozenset[str]] = frozenset(fold(w) for w in RESERVED_NAME_KEYS)


def is_reserved(key: str) -> bool:
    """Whether a name key is one the owner's control words own (``RESERVED_NAME_KEYS``)."""
    return fold(key).strip() in _RESERVED_FOLDED


_RUN_FILLER_FOLDED: Final[frozenset[str]] = frozenset(fold(w) for w in RUN_FILLER_WORDS)
_LAST_WORD_SUFFIXES_FOLDED: Final[frozenset[str]] = frozenset(fold(s) for s in LAST_WORD_SUFFIXES)


def _tokens(text: str) -> list[str]:
    return [t for t in _NON_WORD_RE.sub(" ", fold(text)).split() if t]


def name_key(text: str) -> str:
    """The canonical key a name is stored and matched under (module docstring)."""
    tokens = _tokens(text)
    while tokens and tokens[0] in _NAME_FRAME_FOLDED:
        tokens.pop(0)
    while tokens and tokens[-1] in _NAME_FRAME_FOLDED:
        tokens.pop()
    key = " ".join(tokens[:MAX_NAME_TOKENS])
    return "" if key in _RESERVED_FOLDED else key


def spoken_name(text: str) -> str:
    """The name as the owner SAID it, with its casing, minus the frame words at the
    edges and the punctuation the recogniser adds: "Adı yeni mail sekmesi olsun." ->
    "yeni mail sekmesi". Empty when nothing but frame words was said, and empty for a
    reserved control word ("Dur." is a stop, never a name - ``RESERVED_NAME_KEYS``)."""
    words = [w for w in _EDGE_PUNCT_RE.sub("", text.strip()).split() if w]
    cleaned = [_EDGE_PUNCT_RE.sub("", w) for w in words]
    cleaned = [w for w in cleaned if w]
    while cleaned and fold(cleaned[0]) in _NAME_FRAME_FOLDED:
        cleaned.pop(0)
    while cleaned and fold(cleaned[-1]) in _NAME_FRAME_FOLDED:
        cleaned.pop()
    name = " ".join(cleaned[:MAX_NAME_TOKENS])[:MAX_NAME_CHARS]
    return "" if not name_key(name) else name


def _last_word_matches(spoken: str, stored: str) -> bool:
    if spoken == stored:
        return True
    if not spoken.startswith(stored):
        return False
    return spoken[len(stored) :] in _LAST_WORD_SUFFIXES_FOLDED


def match_stored_name(tokens: Sequence[str], keys: Sequence[str]) -> str | None:
    """The stored key this sentence names, or None (module docstring).

    Longest keys first, so a macro called "yeni mail" never wins a sentence that names
    "yeni mail sekmesi". Every token outside the name must be a run filler.
    """
    folded = [fold(t) for t in tokens if t]
    if not folded:
        return None
    for key in sorted({k for k in keys if k}, key=lambda k: (-len(k.split()), k)):
        if key in _RESERVED_FOLDED:
            continue  # defence in depth: a stop word is never a macro, whatever a row says
        parts = key.split()
        n = len(parts)
        if n == 0 or n > len(folded):
            continue
        for start in range(0, len(folded) - n + 1):
            window = folded[start : start + n]
            if window[:-1] != parts[:-1]:
                continue
            if not _last_word_matches(window[-1], parts[-1]):
                continue
            rest = folded[:start] + folded[start + n :]
            if all(w in _RUN_FILLER_FOLDED for w in rest):
                return key
    return None


__all__ = [
    "LAST_WORD_SUFFIXES",
    "MAX_NAME_CHARS",
    "MAX_NAME_TOKENS",
    "NAME_FRAME_WORDS",
    "RESERVED_NAME_KEYS",
    "RUN_FILLER_WORDS",
    "fold",
    "is_reserved",
    "match_stored_name",
    "name_key",
    "spoken_name",
]
