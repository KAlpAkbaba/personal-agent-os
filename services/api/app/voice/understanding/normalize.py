"""ADR-0224 layer 1: Turkish suffix stripping (closed, table-driven) and the STT confusion list.

Layer 1 has no opinion about intent; it makes the rule tables and the vectors see the word the
owner said: "açın", "açınız", "açar mısın", "açabilir misin" -> "aç"; "bilgisayarımdan" ->
"bilgisayar" + (poss1sg, abl); "Ofisü" (a form the STT produced) -> "ofis".

The stripper is CLOSED. A suffix chain is dropped only when (a) the ending is one this module
generates from its own suffix grammar, (b) re-attaching the chain to the candidate stem gives the
token back (vowel harmony and buffers agree), and (c) the stem is KNOWN - the verb table, the
application and device vocabularies, the small noun list below. So "istediğim" stays whole (the
ADR-0205 failure: a stem match on "iş" read every conjugation of "istemek" as "at work") and
"unutma" stays whole (negative imperative; "unut" is the delete/remember word). Negative forms
are never stripped: they change the meaning.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from functools import lru_cache
from pathlib import Path
from typing import Final, NamedTuple

from app.devices import aliases as _device_aliases
from app.operator import allowlists as _allowlists
from app.voice.intents import normalize_transcript

VERB, NOUN, OTHER = "verb", "noun", "other"

_VOWELS: Final[str] = "aeıioöuü"
_VOICELESS: Final[str] = "fstkçşhp"
_CASES: Final[tuple[str, ...]] = ("acc", "dat", "loc", "abl", "gen")


class Lemma(NamedTuple):
    surface: str
    stem: str
    suffixes: tuple[str, ...]
    kind: str


class Normalized(NamedTuple):
    text: str
    tokens: tuple[str, ...]
    lemmas: tuple[Lemma, ...]
    applied_confusions: tuple[tuple[str, str], ...]


# ------------------------------------------------------------------ the closed vocabularies

#: Imperative stem -> aorist ending. Explicit, because the aorist vowel is lexical (bul-ur, aç-ar).
#: Verbs whose stem mutates (kaydet -> kaydeder, git -> gider) and ones whose polite form is a
#: common noun ("basın", "alın") are left out on purpose: an unknown verb stays whole.
_VERBS: Final[dict[str, str]] = {
    "aç": "ar",
    "kapat": "ır",
    "oku": "r",
    "yaz": "ar",
    "bul": "ur",
    "ara": "r",
    "araştır": "ır",
    "göster": "ir",
    "sil": "er",
    "getir": "ir",
    "gönder": "ir",
    "başlat": "ır",
    "durdur": "ur",
    "çalıştır": "ır",
    "hatırlat": "ır",
    "unut": "ur",
    "hatırla": "r",
    "söyle": "r",
    "anlat": "ır",
    "özetle": "r",
    "kur": "ar",
    "çevir": "ir",
    "ver": "ir",
    "yap": "ar",
    "bak": "ar",
    "ekle": "r",
    "listele": "r",
    "dinle": "r",
    "oynat": "ır",
    "uyandır": "ır",
    "güldür": "ür",
    "düzelt": "ir",
    "oluştur": "ur",
    "hazırla": "r",
    "dene": "r",
    "duraklat": "ır",
    "aktar": "ır",
    "ayarla": "r",
    "kaydır": "ır",
    "artır": "ır",
    "kaldır": "ır",
    "karşılaştır": "ır",
    "değiştir": "ir",
    "büyüt": "ür",
    "küçült": "ür",
    "ertele": "r",
    "indir": "ir",
    "yükle": "r",
    "paylaş": "ır",
}

#: Nouns drawn from the Owner Utterance Suite's own vocabulary; everything else stays whole.
_NOUNS: Final[tuple[str, ...]] = (
    "bilgisayar",
    "ofis",
    "ev",
    "iş",
    "laptop",
    "hesap",
    "makine",
    "not",
    "defter",
    "dosya",
    "gezgin",
    "rapor",
    "alarm",
    "mail",
    "takvim",
    "hatırlatıcı",
    "toplantı",
    "video",
    "ses",
    "müzik",
    "ekran",
    "tarayıcı",
    "klasör",
    "belge",
    "mesaj",
    "uygulama",
    "pencere",
    "araştırma",
    "haber",
    "durum",
    "şarkı",
    "kamera",
    "sunum",
    "sayaç",
    "kutu",
    "görev",
    "masaüstü",
    "sayfa",
    "hata",
    "sürüm",
    "slayt",
    "komut",
    "rutin",
    "kaynak",
    "sahne",
    "saat",
    "paint",
    "boya",
)
#: Stems whose vowel harmony is lexical, not the last vowel's: saati, maili, rutini.
_FRONT: Final[frozenset[str]] = frozenset({"saat", "mail", "rutin"})

_MI_PARTICLES: Final[frozenset[str]] = frozenset(
    f"{m}{s}"
    for m in ("mı", "mi", "mu", "mü")
    for s in ("sın", "sin", "sun", "sün", "sınız", "siniz", "sunuz", "sünüz")
)


# ------------------------------------------------------------------ phonology


def _last_vowel(s: str) -> str:
    return next((c for c in reversed(s) if c in _VOWELS), "a")


def _high(s: str, front: bool = False) -> str:
    v = _last_vowel(s)
    if front and v in "aı":
        v = "e" if v == "a" else "i"
    return {"a": "ı", "ı": "ı", "e": "i", "i": "i", "o": "u", "u": "u", "ö": "ü", "ü": "ü"}[v]


def _low(s: str, front: bool = False) -> str:
    return "e" if (front or _last_vowel(s) in "eiöü") else "a"


def _vowel_final(s: str) -> bool:
    return s[-1] in _VOWELS


# ------------------------------------------------------------------ suffix grammar

_NOUN_CHAINS: Final[tuple[tuple[str, ...], ...]] = tuple(
    tuple(x for x in (pl, poss, case) if x)
    for pl in ("", "pl")
    for poss in ("", "poss1sg", "poss3sg", "poss2sg", "poss1pl", "poss2pl")
    for case in ("", *_CASES)
)
_VERB_CHAINS: Final[tuple[tuple[str, ...], ...]] = (
    (),
    ("pol",),
    ("pol", "pl"),
    ("sana",),
    ("aor",),
    ("pot", "aor"),
)


def _attach_noun(stem: str, chain: tuple[str, ...]) -> str:
    front = stem in _FRONT
    w = stem
    for sfx in chain:
        h, lo, vf = _high(w, front), _low(w, front), _vowel_final(w)
        n = "n" if (sfx in _CASES and "poss3sg" in chain) else ""  # pronominal n after -(s)i
        d = "t" if (not n and w[-1] in _VOICELESS) else "d"
        if sfx == "pl":
            w += "l" + lo + "r"
        elif sfx == "poss1sg":
            w += "m" if vf else h + "m"
        elif sfx == "poss2sg":
            w += "n" if vf else h + "n"
        elif sfx == "poss3sg":
            w += "s" + h if vf else h
        elif sfx == "poss1pl":
            w += ("m" if vf else h + "m") + h + "z"
        elif sfx == "poss2pl":
            w += ("n" if vf else h + "n") + h + "z"
        elif sfx == "acc":
            w += (n or ("y" if vf else "")) + h
        elif sfx == "dat":
            w += (n or ("y" if vf else "")) + lo
        elif sfx == "loc":
            w += n + d + lo
        elif sfx == "abl":
            w += n + d + lo + "n"
        elif sfx == "gen":
            w += ("n" if (vf or n) else "") + h + "n"
    return w


def _attach_verb(stem: str, chain: tuple[str, ...]) -> str:
    y = "y" if _vowel_final(stem) else ""
    if chain == ():
        return stem
    if chain == ("pol",):
        return stem + y + _high(stem) + "n"
    if chain == ("pol", "pl"):
        w = stem + y + _high(stem) + "n"
        return w + _high(w) + "z"
    if chain == ("sana",):
        return stem + ("sene" if _low(stem) == "e" else "sana")
    if chain == ("aor",):
        return stem + _VERBS[stem]
    if chain == ("pot", "aor"):
        return stem + y + _low(stem) + "bilir"
    raise ValueError(chain)


# ------------------------------------------------------------------ the tables, built once


def _alias_words() -> frozenset[str]:
    words = {w for phrase, _ in _allowlists.APP_ALIAS_PHRASES for w in phrase.split()}
    words |= {
        _device_aliases.ALIAS_EV,
        _device_aliases.ALIAS_IS,
        _device_aliases.ALIAS_OFIS,
        _device_aliases.ALIAS_LAPTOP,
    }
    return frozenset(words)


def _endings(
    stems: Iterable[str],
    attach: Callable[[str, tuple[str, ...]], str],
    chains: tuple[tuple[str, ...], ...],
) -> dict[str, list[tuple[str, ...]]]:
    table: dict[str, list[tuple[str, ...]]] = {}
    for stem in stems:
        for chain in chains:
            if chain:
                found = table.setdefault(attach(stem, chain)[len(stem) :], [])
                if chain not in found:
                    found.append(chain)
    return table


_NOUN_STEMS: set[str] = set(_NOUNS)
_VERB_ENDINGS = _endings(_VERBS, _attach_verb, _VERB_CHAINS)
_NOUN_ENDINGS = _endings(_NOUN_STEMS, _attach_noun, _NOUN_CHAINS)


def _is_known_stem(stem: str, kind: str) -> bool:
    """THE guard: a suffix is dropped only when what is left is a stem this module knows."""
    return stem in (_VERBS if kind == VERB else _NOUN_STEMS)


def _strip(token: str, kind: str) -> tuple[str, tuple[str, ...]] | None:
    endings, attach = (
        (_VERB_ENDINGS, _attach_verb) if kind == VERB else (_NOUN_ENDINGS, _attach_noun)
    )
    for cut in range(len(token) - 1, 0, -1):  # longest remaining stem first
        chains = endings.get(token[cut:])
        stem = token[:cut]
        if chains and _is_known_stem(stem, kind):
            for chain in chains:
                if attach(stem, chain) == token:
                    return stem, chain
    return None


# Alias-vocabulary words (application names, device aliases) join the noun stems only when no
# known stem already explains them: "makinesi" is makine + poss3sg, not a stem of its own.
for _word in sorted(_alias_words()):
    if _word not in _VERBS and _strip(_word, NOUN) is None:
        _NOUN_STEMS.add(_word)
_NOUN_ENDINGS = _endings(_NOUN_STEMS, _attach_noun, _NOUN_CHAINS)


def _lemma(token: str) -> Lemma:
    probe = token.replace("'", "")  # "paint'te"
    for kind, stems in ((VERB, _VERBS), (NOUN, _NOUN_STEMS)):
        if probe in stems:
            return Lemma(token, probe, (), kind)
    for kind in (VERB, NOUN):
        found = _strip(probe, kind)
        if found:
            return Lemma(token, found[0], found[1], kind)
    return Lemma(token, token, (), OTHER)


# ------------------------------------------------------------------ the STT confusion list

CONFUSIONS_FILE: Final[str] = "stt-confusions.json"


def load_confusions(path: Path | None = None) -> dict[str, str]:
    """heard -> meant, from the list beside this module unless a path is given.

    The list has ONE reader - this layer - so it lives with it and ships in the image as
    package data. It moves to ``packages/protocol`` the day a second component reads it:
    a shared file with one reader is a file, not a contract (the falsification registry's
    rule; decided by the lead at integration, 2026-10-01, ADR-0224 addendum).
    """
    if path is None:
        path = Path(__file__).with_name(CONFUSIONS_FILE)
    data = json.loads(path.read_text(encoding="utf-8"))
    return {e["heard"]: e["meant"] for e in data["entries"]}


@lru_cache(maxsize=1)
def _default_confusions() -> dict[str, str]:
    return load_confusions()


# ------------------------------------------------------------------ the public functions


def normalize(text: str, *, confusions: dict[str, str] | None = None) -> Normalized:
    table = _default_confusions() if confusions is None else confusions
    _, raw, _ = normalize_transcript(text)
    tokens: list[str] = []
    applied: list[tuple[str, str]] = []
    for tok in raw:
        if tok in table:
            applied.append((tok, table[tok]))
            tok = table[tok]
        tokens.append(tok)
    lemmas: list[Lemma] = []
    i = 0
    while i < len(tokens):
        lem = _lemma(tokens[i])
        nxt = tokens[i + 1] if i + 1 < len(tokens) else ""
        if lem.kind == VERB and lem.suffixes[-1:] == ("aor",) and nxt in _MI_PARTICLES:
            lem = Lemma(f"{lem.surface} {nxt}", lem.stem, (*lem.suffixes, "q"), VERB)
            i += 1
        lemmas.append(lem)
        i += 1
    return Normalized(" ".join(tokens), tuple(tokens), tuple(lemmas), tuple(applied))


def lemma_tokens(text: str, *, confusions: dict[str, str] | None = None) -> tuple[str, ...]:
    """The tokens with verbs and nouns replaced by their stems (the router's later input)."""
    return tuple(
        lem.stem if lem.kind in (VERB, NOUN) else lem.surface
        for lem in normalize(text, confusions=confusions).lemmas
    )


__all__ = ["Lemma", "Normalized", "lemma_tokens", "load_confusions", "normalize"]
