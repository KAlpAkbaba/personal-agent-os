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
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Collection, Iterable
from functools import lru_cache
from pathlib import Path
from typing import Final, NamedTuple

from app.devices import aliases as _device_aliases
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
                found = table.setdefault(attach(stem, chain)[len(stem) :], [])
                if chain not in found:
                    found.append(chain)
    return table


_NOUN_STEMS: set[str] = set(_NOUNS)
_VERB_ENDINGS = _endings(_ALL_VERBS, _attach_verb, _VERB_CHAINS)
_NOUN_ENDINGS = _endings(_NOUN_STEMS, _attach_noun, _NOUN_CHAINS)


def _is_known_stem(stem: str, kind: str) -> bool:
    """THE guard: a suffix is dropped only when what is left is a stem this module knows."""
    return stem in (_ALL_VERBS if kind == VERB else _NOUN_STEMS)


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
        stem = token[:cut]
        if _is_known_stem(stem, kind):
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


def lemma_reading(text: str, *, keep: Collection[str] = ()) -> LemmaReading | None:
    """The sentence as layer 1 reads it, for the rule tables - or None when layer 1 changes
    nothing, or must not.

    Only two things are rewritten, in the owner's own text: a polite form of a known verb
    becomes its bare imperative ("kapatın", "kapatsana", "kapatır mısınız", "kapatabilir
    misin" -> "kapat"), and a fused token becomes its two words. A form named in ``keep`` is
    left as said (the caller's "this is a question, not a request" list), and so is everything
    inside quotes. A sentence that says "don't" has no reading at all (:func:`_says_dont`).
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
    cursor = 0
    index = 0
    while index < len(words):
        match, token = words[index], folded[index]
        raw = match.group()
        parts = [(raw, token)]
        halves = _split(token) if len(token) == len(raw) else None
        if halves is not None:
            cut = len(halves[0])
            parts = [(raw[:cut], halves[0]), (raw[cut:], halves[1])]
            splits.append((token, " ".join(halves)))
        end = match.end()
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
    if not dropped and not splits:
        return None
    out.append(text[cursor:])
    return LemmaReading("".join(out), tuple(dropped), tuple(splits))


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
