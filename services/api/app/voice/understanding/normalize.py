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

Two things are built on the stripper for the rule tables (ADR-0224 addendum 4, the gap):

* a FUSED token is split when, and only when, it is two words this module knows written as
  one ("hesapmakinesini" -> hesap + makinesini; "alarmkur" -> alarm + kur). A token that is
  itself a known word is never split, a verb is never the first half, a negative form is
  never a half, there is at most one split per token, and a token that could be divided two
  ways is left whole. The split is recorded like a confusion (``Normalized.applied_splits``);
* :func:`lemma_reading` hands the router the sentence as layer 1 reads it: the polite forms
  of a known verb written as its bare imperative, the fused tokens split, everything else
  letter for letter. A sentence that carries a negative imperative has no such reading.

The test team's findings of 2026-10-06 (card understanding-plural-context-typos) add three
WORD repairs, asked for with ``lemma_reading(..., repair_words=True)`` and kept apart in
``LemmaReading.repaired``, each from the grammar or one edit, never from a list of forms: a known
noun said in the PLURAL is its singular ("alarmlarımı" -> "alarmı", "nöbetlerimden birini" ->
"nöbeti"); ONE slip of the finger is undone (a doubled letter, a dropped vowel: "alarmmı",
"alrmı", "sütt"); and a household sentence written as one word is split ("sütbitti").
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Collection, Iterable
from functools import lru_cache
from pathlib import Path
from typing import Final, NamedTuple

from app.devices import aliases as _device_aliases
from app.household import parse as _household
from app.operator import allowlists as _allowlists
from app.voice.intents import normalize_transcript, turkish_casefold

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
    #: fused token -> the two words it was read as: ("hesapmakinesini", "hesap makinesini").
    applied_splits: tuple[tuple[str, str], ...] = ()


class LemmaReading(NamedTuple):
    """The sentence as layer 1 reads it, for the rule tables (:func:`lemma_reading`)."""

    text: str
    #: (the polite form as said, casefolded; the imperative it was read as).
    dropped: tuple[tuple[str, str], ...]
    #: (the fused token, casefolded; the two words).
    splits: tuple[tuple[str, str], ...]
    #: (the word with an ending nobody said, casefolded; the word): ("notü", "not").
    invented: tuple[tuple[str, str], ...] = ()
    #: Only with ``repair_words``: (the words as said, casefolded; the word they are read as) -
    #: a plural ("alarmları", "alarmı"; "nöbetlerimden birini", "nöbeti") or one typo
    #: ("alrmı", "alarmı"; "sütt", "süt"). A fused household sentence is in ``splits``.
    repaired: tuple[tuple[str, str], ...] = ()


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

#: The verbs the router's exact-form tables hold beyond the list above, so that a polite form
#: is read for EVERY verb table and not only for the verbs layer 1 began with. Kept apart on
#: purpose: ``_VERBS`` also feeds layer 3's negation cap (``policy._negative_forms``), which
#: this list must not widen. Left out, as above: stems that mutate ("et", "kaydet", "git")
#: and verbs whose polite form is a common word ("alın", "basın", "kesin", "koyun", "sayın").
_TABLE_VERBS: Final[dict[str, str]] = {
    "çal": "ar",
    "çek": "er",
    "çiz": "er",
    "çıkar": "ır",
    "azalt": "ır",
    "söndür": "ür",
    "sustur": "ur",
    "sus": "ar",
    "tut": "ar",
    "geç": "er",
    "dön": "er",
    "tıkla": "r",
    "tuşla": "r",
    "yakala": "r",
    "kopyala": "r",
    "taşı": "r",
    "üret": "ir",
    "tasarla": "r",
    "derle": "r",
    "paketle": "r",
    "dinlet": "ir",
    "onar": "ır",
    "yarat": "ır",
    "adlandır": "ır",
    "betimle": "r",
    "yetkilendir": "ir",
    "yönlendir": "ir",
    "açıkla": "r",
}
_ALL_VERBS: Final[dict[str, str]] = {**_VERBS, **_TABLE_VERBS}

#: Words that take no suffix here and are known for ONE purpose: saying where a fused token
#: divides ("birrutin" -> bir + rutin, "beniuyandır" -> beni + uyandır). Closed classes only -
#: determiners, pronouns, the small numbers. A word those would cut in two is a noun below
#: ("bugün" is not bu + gün, and "bugünün" is not bu + günün).
_WORDS: Final[frozenset[str]] = frozenset(
    {
        "bu",
        "şu",
        "bunu",
        "şunu",
        "onu",
        "beni",
        "bana",
        "seni",
        "kendi",
        "kendin",
        "bir",
        "her",
        "tüm",
        "bütün",
        "yeni",
        "son",
        "ilk",
        "iki",
        "üç",
        "dört",
        "beş",
        "altı",
        "yedi",
        "sekiz",
        "dokuz",
        "on",
        "yarım",
        "yarın",
        "şimdi",
    }
)

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
    "göz",
    "hareket",
    "teknik",
    "gün",
    "bugün",
    "buçuk",
    "bug",
    "resim",
    "nöbet",
)
#: Stems whose vowel harmony is lexical, not the last vowel's: saati, maili, rutini.
_FRONT: Final[frozenset[str]] = frozenset({"saat", "mail", "rutin"})
#: ... and the loanword said with an "a" it is not written with: bug'ı ("bag").
_FLAT: Final[frozenset[str]] = frozenset({"bug"})
#: Stems that lose their last vowel before a vowel-initial suffix: resim -> resmi.
_ELIDED: Final[dict[str, str]] = {"resim": "resm"}
_UNELIDED: Final[dict[str, str]] = {short: stem for stem, short in _ELIDED.items()}
#: The suffixes that begin with a vowel after a consonant-final stem.
_VOWEL_INITIAL: Final[frozenset[str]] = frozenset(
    {"poss1sg", "poss2sg", "poss3sg", "poss1pl", "poss2pl", "acc", "dat", "gen"}
)

_MI_PARTICLES: Final[frozenset[str]] = frozenset(
    f"{m}{s}"
    for m in ("mı", "mi", "mu", "mü")
    for s in ("sın", "sin", "sun", "sün", "sınız", "siniz", "sunuz", "sünüz")
)


# ------------------------------------------------------------------ phonology


def _last_vowel(s: str) -> str:
    return next((c for c in reversed(s) if c in _VOWELS), "a")


def _high(s: str, front: bool = False, flat: bool = False) -> str:
    v = _last_vowel(s)
    if front and v in "aı":
        v = "e" if v == "a" else "i"
    if flat and v in "ou":
        v = "a"
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
    front, flat = stem in _FRONT, stem in _FLAT
    w = _ELIDED[stem] if stem in _ELIDED and chain[:1] and chain[0] in _VOWEL_INITIAL else stem
    for sfx in chain:
        h, lo, vf = _high(w, front, flat), _low(w, front), _vowel_final(w)
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
        return stem + _ALL_VERBS[stem]
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
                form = attach(stem, chain)
                base = stem if form.startswith(stem) else _ELIDED.get(stem, stem)
                found = table.setdefault(form[len(base) :], [])
                if chain not in found:
                    found.append(chain)
    return table


_NOUN_STEMS: set[str] = set(_NOUNS)
_VERB_ENDINGS = _endings(_ALL_VERBS, _attach_verb, _VERB_CHAINS)
_NOUN_ENDINGS = _endings(_NOUN_STEMS, _attach_noun, _NOUN_CHAINS)


def _is_known_stem(stem: str, kind: str) -> bool:
    """THE guard: a suffix is dropped only when what is left is a stem this module knows."""
    return stem in (_ALL_VERBS if kind == VERB else _NOUN_STEMS)


def _stems_at(head: str, kind: str) -> tuple[str, ...]:
    """The known stems ``head`` can be the front of: itself, or the stem it is with its last
    vowel elided ("resm" -> "resim")."""
    stems = [head] if _is_known_stem(head, kind) else []
    if kind == NOUN and head in _UNELIDED:
        stems.append(_UNELIDED[head])
    return tuple(stems)


def _strip(token: str, kind: str) -> tuple[str, tuple[str, ...]] | None:
    endings, attach = (
        (_VERB_ENDINGS, _attach_verb) if kind == VERB else (_NOUN_ENDINGS, _attach_noun)
    )
    for cut in range(len(token) - 1, 0, -1):  # longest remaining stem first
        chains = endings.get(token[cut:])
        if not chains:
            continue
        for stem in _stems_at(token[:cut], kind):
            for chain in chains:
                if attach(stem, chain) == token:
                    return stem, chain
    return None


# Alias-vocabulary words (application names, device aliases) join the noun stems only when no
# known stem already explains them: "makinesi" is makine + poss3sg, not a stem of its own.
for _word in sorted(_alias_words()):
    if _word not in _ALL_VERBS and _strip(_word, NOUN) is None:
        _NOUN_STEMS.add(_word)
_NOUN_ENDINGS = _endings(_NOUN_STEMS, _attach_noun, _NOUN_CHAINS)


def _lemma(token: str) -> Lemma:
    probe = token.replace("'", "")  # "paint'te"
    for kind, stems in ((VERB, _ALL_VERBS), (NOUN, _NOUN_STEMS)):
        if probe in stems:
            return Lemma(token, probe, (), kind)
    for kind in (VERB, NOUN):
        found = _strip(probe, kind)
        if found:
            return Lemma(token, found[0], found[1], kind)
    return Lemma(token, token, (), OTHER)


# ------------------------------------------------------------------ the negative forms


def _negative_forms() -> frozenset[str]:
    """Every "don't" of a known verb: kapatma, kapatmayın, kapatmayınız, kapatmasana, kapatmaz.
    A known noun spelled like one ("araştırma") is the noun."""
    forms: set[str] = set()
    for stem in _ALL_VERBS:
        base = stem + "m" + _low(stem)
        polite = base + "y" + _high(base) + "n"
        forms |= {base, polite, polite + _high(polite) + "z", base + "z"}
        forms.add(base + ("sene" if _low(base) == "e" else "sana"))
    return frozenset(forms - _NOUN_STEMS)


_NEGATIVE_FORMS: Final[frozenset[str]] = _negative_forms()
#: The bare negative imperative is also the verbal noun ("indirme klasörü", "arama geçmişi").
_BARE_NEGATIVES: Final[frozenset[str]] = frozenset(stem + "m" + _low(stem) for stem in _ALL_VERBS)


def is_negative(token: str) -> bool:
    """The token is a negative form of a known verb. Never stripped, never split, never the
    half of a split: "kapatma" is not "kapat"."""
    return token in _NEGATIVE_FORMS


def is_verb(token: str) -> bool:
    """The token is a form of a verb this module knows ("kapat", "kapatın", "kapatır"). A
    negative form is not one here ("kapatma"): it is never stripped."""
    return _lemma(token).kind == VERB


# ------------------------------------------------------------------ the fused word

#: A half shorter than this is not a word a split may rest on.
_MIN_HALF: Final = 2


def _known(word: str) -> str | None:
    """The kind of a word this module knows - a stem, a stem with its suffixes, or one of
    ``_WORDS`` - else None. A negative form is not a known word."""
    if is_negative(word):
        return None
    if word in _WORDS:
        return OTHER
    kind = _lemma(word).kind
    return None if kind == OTHER else kind


def _split(token: str) -> tuple[str, str] | None:
    """The two known words a fused token is, or None. At most one split, and only one way."""
    if is_negative(token) or _known(token) is not None:
        return None  # a token that is itself a known word is never split
    found: list[tuple[str, str]] = []
    for cut in range(_MIN_HALF, len(token) - _MIN_HALF + 1):
        left, right = token[:cut], token[cut:]
        if left.endswith("'") or right.startswith("'"):
            continue
        first = _known(left)
        if first is None or first == VERB or _known(right) is None:
            continue  # Turkish ends on its verb: "silver" is not sil + ver
        found.append((left, right))
    return found[0] if len(found) == 1 else None


# ------------------------------------------------------------------ the invented ending


def _invented(token: str) -> str | None:
    """The word a token is once the ending the STT invented is dropped ("notü" -> "not",
    "Ofisü" on 2026-09-30), or None. Only ONE vowel after a consonant-final word this module
    knows that is not a verb, and only a vowel that word's harmony cannot take: "notu" is
    the accusative, "notü" is no Turkish word. A known token is never one."""
    if len(token) < 3 or token[-1] not in _VOWELS or "'" in token:
        return None
    word = token[:-1]
    if word[-1] in _VOWELS or _known(token) is not None or _known(word) in (None, VERB):
        return None
    lemma = _lemma(word)
    front, flat = lemma.stem in _FRONT, lemma.stem in _FLAT
    harmonic = _high(word, front, flat) + _low(word, front)
    if token[-1] in harmonic + harmonic.translate(_LETTER_SWAPS):
        # "ekranlari", "haberlerı": the harmony's own vowel with its dot lost or misplaced -
        # a letter confusion, not an ending nobody said.
        return None
    return word


_LETTER_SWAPS: Final = str.maketrans("ıiöü", "iıou")


# ------------------------------------------------------------------ the plural, the typo

#: "nöbetlerimden BİRİNİ": the partitive pronoun, built by the noun grammar (bir + 3sg + acc).
_PARTITIVE: Final = _attach_noun("bir", ("poss3sg", "acc"))
#: A typo is repaired by inserting a vowel only into a word whose stem is at least this long:
#: "hata" is one letter from "hafta" and must never be what "hafta" is read as.
_TYPO_MIN_STEM: Final = 5


def _singular(token: str) -> tuple[str, tuple[str, ...]] | None:
    """(the stem, the case) of a known noun said with a plural ending: "alarmları" -> ("alarm",
    ("acc",)), "alarmlarımızı" -> ("alarm", ("acc",)), "nöbetlerimden" -> ("nöbet", ("abl",)).
    The suffix grammar reads the token; the noun keeps its case and loses its number and its
    possessor - the tables read the singular, and "which of them" is the tool's question, not
    the router's. None for anything else."""
    if "'" in token or is_negative(token):
        return None
    lemma = _lemma(token)
    if lemma.kind != NOUN or "pl" not in lemma.suffixes:
        return None
    return lemma.stem, (lemma.suffixes[-1:] if lemma.suffixes[-1] in _CASES else ())


def _household_item(word: str) -> bool:
    """The word is a household item as its own bare name ("süt"), not an inflected form."""
    key = _household.item_key(word)
    return (
        bool(key) and " " not in key and key == _household.fold(word) and _household.known_item(key)
    )


def _typo(token: str) -> str | None:
    """The word a token is once ONE slip of the finger is undone, or None. Two slips only,
    each checked against what this layer knows - never against a list of misspellings:

    * a doubled letter collapsed ("alarmmı" -> "alarmı", "sütt" -> "süt") into a known noun
      form or a household item's bare name;
    * a dropped vowel put back ("alrmı" -> "alarmı") into a known noun form whose stem has
      at least ``_TYPO_MIN_STEM`` letters - only when no doubled letter explains the token.

    A deletion is never a repair (it would read "hafta" as "hata"), a consonant is never
    inserted ("takim" stays "takım", not "takvim"), a known word or a negative form is never
    touched, and a token two words are one edit from is left whole."""
    if len(token) < 4 or "'" in token or is_negative(token) or _known(token) is not None:
        return None
    if _household_item(token):
        return None
    found: set[str] = set()
    for i in range(1, len(token)):
        if token[i] == token[i - 1]:
            candidate = token[:i] + token[i + 1 :]
            if _known(candidate) == NOUN or _household_item(candidate):
                found.add(candidate)
    if found:  # a doubled letter is the slip: "alarmmı" is not "alarmımı" with a vowel lost
        return found.pop() if len(found) == 1 else None
    for i in range(len(token) + 1):
        for vowel in _VOWELS:
            candidate = token[:i] + vowel + token[i:]
            if _known(candidate) == NOUN and len(_lemma(candidate).stem) >= _TYPO_MIN_STEM:
                found.add(candidate)
    return found.pop() if len(found) == 1 else None


def _repaired(token: str, following: str) -> tuple[str, bool] | None:
    """(the word, whether the next word went with it) the reading writes for a token said in
    the plural (:func:`_singular`; "nöbetlerimden birini" is the one accusative "nöbeti") or
    with a typo (:func:`_typo`), else None."""
    single = _singular(token)
    if single is not None:
        stem, case = single
        if case == ("abl",) and following == _PARTITIVE:
            return _attach_noun(stem, ("acc",)), True
        return _attach_noun(stem, case), False
    typo = _typo(token)
    return (typo, False) if typo is not None else None


def _household_split(token: str) -> tuple[str, str] | None:
    """The two words a fused household sentence is ("sütbitti" -> süt + bitti): a household
    item's bare name, then words the household table itself reads with it as a command. One
    way only."""
    found = [
        (token[:cut], token[cut:])
        for cut in range(_MIN_HALF, len(token) - _MIN_HALF + 1)
        if _household_item(token[:cut])
        and _household.parse_words([token[:cut], token[cut:]]) is not None
    ]
    return found[0] if len(found) == 1 else None


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
    splits: list[tuple[str, str]] = []
    for tok in raw:
        if tok in table:
            applied.append((tok, table[tok]))
            tok = table[tok]
        halves = _split(tok)
        if halves is None:
            tokens.append(tok)
        else:
            splits.append((tok, " ".join(halves)))
            tokens.extend(halves)
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
    return Normalized(" ".join(tokens), tuple(tokens), tuple(lemmas), tuple(applied), tuple(splits))


#: A word of the sentence as written: letters, with the apostrophe a suffix hangs on.
_WORD_RE: Final[re.Pattern[str]] = re.compile(r"[^\W\d_]+(?:['’][^\W\d_]+)*")
#: A quoted title or dictated text ('Bana Bakın'): the owner's own words, never rewritten.
_QUOTED_RE: Final[re.Pattern[str]] = re.compile(r"(?<!\w)['\"“‘«][^'\"“”‘’«»]+['\"”’»](?!\w)")
#: The chains that ARE a request for the bare imperative; an aorist is one only before its
#: second-person question particle ("kapatır mısın"), never alone ("kapatır": a statement).
_IMPERATIVE_CHAINS: Final[frozenset[tuple[str, ...]]] = frozenset(
    {("pol",), ("pol", "pl"), ("sana",)}
)
_QUESTION_CHAINS: Final[frozenset[tuple[str, ...]]] = frozenset({("aor",), ("pot", "aor")})


def _readings(token: str, kind: str) -> list[tuple[str, ...]]:
    """Every suffix chain that explains the token on a known stem of the kind (every cut)."""
    endings, attach = (
        (_VERB_ENDINGS, _attach_verb) if kind == VERB else (_NOUN_ENDINGS, _attach_noun)
    )
    found: list[tuple[str, ...]] = []
    for cut in range(len(token) - 1, 0, -1):
        for stem in _stems_at(token[:cut], kind):
            found += [c for c in endings.get(token[cut:], ()) if attach(stem, c) == token]
    return found


def _is_compound_head(word: str) -> bool:
    """The word proves the bare negative before it a verbal noun ("indirme klasörünü"): a known
    noun whose EVERY reading carries a possessive. "ışıkları", "ama", "kurun" prove nothing."""
    probe = word.replace("'", "")
    if probe in _ALL_VERBS or probe in _NOUN_STEMS or _readings(probe, VERB):
        return False
    chains = _readings(probe, NOUN)
    return bool(chains) and all(any(s.startswith("poss") for s in c) for c in chains)


#: The pronoun objects a verb takes ("bunu kapatma"): closed, and not nouns of this module.
_PRONOUN_OBJECTS: Final[frozenset[str]] = frozenset(
    {"bunu", "şunu", "onu", "bunları", "şunları", "onları", "beni", "seni", "bizi", "sizi"}
)


def _is_accusative_object(word: str) -> bool:
    """The word can be the accusative object of the verb after it: "ekranı" (acc, or poss -
    no telling, so it counts), "bunu". A negative after its own object is "don't", whatever
    follows it: "Ekranı kapatma sesini kapatın" (inspector-1, third pass)."""
    probe = word.replace("'", "")
    if probe in _PRONOUN_OBJECTS:
        return True
    return any(c and c[-1] == "acc" for c in _readings(probe, NOUN))


def _says_dont(words: list[re.Match[str]], folded: list[str], text: str) -> bool:
    """THE negative-form guard of the reading: the sentence carries a negative imperative of a
    known verb. The bare form ("kapatma") is also the verbal noun, and is read as one only
    where the next word, with nothing between them, is a compound head (:func:`_is_compound_head`)
    and the word before it, if any in the same clause, is no accusative object
    (:func:`_is_accusative_object`): speech-to-text writes no comma, so "kapatma ışıkları
    söndürün" and "ekranı kapatma sesini kapatın" say "don't"."""
    for index, token in enumerate(folded):
        if not is_negative(token):
            continue
        if token not in _BARE_NEGATIVES or index + 1 == len(words):
            return True
        if text[words[index].end() : words[index + 1].start()].strip():
            return True  # punctuation closes the clause: "kapatma, ..."
        if not _is_compound_head(folded[index + 1]):
            return True
        if (
            index > 0
            and not text[words[index - 1].end() : words[index].start()].strip()
            and _is_accusative_object(folded[index - 1])
        ):
            return True  # its own object before it: "ekranı kapatma" is never a noun phrase
    return False


def lemma_reading(
    text: str, *, keep: Collection[str] = (), repair_words: bool = False
) -> LemmaReading | None:
    """The sentence as layer 1 reads it, for the rule tables - or None when layer 1 changes
    nothing, or must not.

    Only two things are rewritten, in the owner's own text: a polite form of a known verb
    becomes its bare imperative ("kapatın", "kapatsana", "kapatır mısınız", "kapatabilir
    misin" -> "kapat"), and a fused token becomes its two words. A form named in ``keep`` is
    left as said (the caller's "this is a question, not a request" list), and so is everything
    inside quotes. A sentence that says "don't" has no reading at all (:func:`_says_dont`).

    ``repair_words`` adds the word repairs (``LemmaReading.repaired``): a plural noun read as
    its singular, one slip of the finger undone, a fused household sentence split. Off by
    default: the tables read most plurals as they are ("Ekranları kapat"), so the repairs are
    a reading for a sentence the words as heard left unrouted, never a rewrite of one routed.
    """
    quoted = [m.span() for m in _QUOTED_RE.finditer(text)]
    words = [
        m
        for m in _WORD_RE.finditer(text)
        if not any(start <= m.start() < end for start, end in quoted)
    ]
    folded = [turkish_casefold(m.group()).replace("’", "'") for m in words]
    if _says_dont(words, folded, text):
        return None
    out: list[str] = []
    dropped: list[tuple[str, str]] = []
    splits: list[tuple[str, str]] = []
    invented: list[tuple[str, str]] = []
    repaired: list[tuple[str, str]] = []
    cursor = 0
    index = 0
    while index < len(words):
        match, token = words[index], folded[index]
        raw = match.group()
        parts = [(raw, token)]
        same_length = len(token) == len(raw)
        halves = _split(token) if same_length else None
        word = _invented(token) if halves is None and same_length else None
        end = match.end()
        if word is not None:
            parts = [(raw[: len(word)], word)]
            invented.append((token, word))
        elif halves is None and same_length and repair_words:
            if _known(token) is None and not _household_item(token):
                halves = _household_split(token)
            following = folded[index + 1] if index + 1 < len(words) else ""
            if following and text[end : words[index + 1].start()].strip():
                following = ""  # punctuation closes the phrase: that word is not its own
            repair = None if halves is not None else _repaired(token, following)
            if repair is not None:
                word, took_next = repair
                said = f"{token} {following}" if took_next else token
                parts = [(word, word)]
                repaired.append((said, word))
                if took_next:
                    end = words[index + 1].end()
                    index += 1
        if halves is not None:
            cut = len(halves[0])
            parts = [(raw[:cut], halves[0]), (raw[cut:], halves[1])]
            splits.append((token, " ".join(halves)))
        written: list[str] = []
        for position, (said, part) in enumerate(parts):
            lemma = _lemma(part)
            if lemma.kind != VERB or part in keep:
                written.append(said)
            elif lemma.suffixes in _IMPERATIVE_CHAINS:
                written.append(lemma.stem)
                dropped.append((part, lemma.stem))
            elif (
                lemma.suffixes in _QUESTION_CHAINS
                and position == len(parts) - 1
                and index + 1 < len(words)
                and folded[index + 1] in _MI_PARTICLES
                and not text[end : words[index + 1].start()].strip()
            ):
                written.append(lemma.stem)
                dropped.append((f"{part} {folded[index + 1]}", lemma.stem))
                end = words[index + 1].end()
                index += 1
            else:
                written.append(said)
        out.append(text[cursor : match.start()])
        out.append(" ".join(written))
        cursor = end
        index += 1
    if not dropped and not splits and not invented and not repaired:
        return None
    out.append(text[cursor:])
    return LemmaReading(
        "".join(out), tuple(dropped), tuple(splits), tuple(invented), tuple(repaired)
    )


def lemma_tokens(text: str, *, confusions: dict[str, str] | None = None) -> tuple[str, ...]:
    """The tokens with verbs and nouns replaced by their stems (the router's later input)."""
    return tuple(
        lem.stem if lem.kind in (VERB, NOUN) else lem.surface
        for lem in normalize(text, confusions=confusions).lemmas
    )


__all__ = [
    "Lemma",
    "LemmaReading",
    "Normalized",
    "is_negative",
    "is_verb",
    "lemma_reading",
    "lemma_tokens",
    "load_confusions",
    "normalize",
]
