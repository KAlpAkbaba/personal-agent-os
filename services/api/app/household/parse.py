"""What the owner said about the house's stock: one command, or nothing.

Four shapes, each anchored on EXACT words (the memory rule "Turkish suffixes break prefix
matching": a stem match on ``bit`` would take "bitir", and on ``al`` every "alarm"):

* level - "<item> azaldı / bitmek üzere / bitti / kalmadı / aldım" (the verb last);
* add - "listeye <item> ekle / yaz", "<item>'i alışveriş listesine ekler misin";
* remove - "listeden <item> çıkar / sil";
* read - "ne almam lazım", "listeyi oku", "listede ne var", "markete gidiyorum".

A level sentence names a thing a house runs out of: the item must be in the household
vocabulary below, or the sentence must say where ("evde", "mutfakta"). Without that gate
"Toplantı bitti" and "Mesajını aldım" would become stock. A question ("Süt bitti mi?") is
never an update.

Pure: no database, no import from the voice package (the router imports THIS module).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

ACTION_LEVEL: Final = "level"
ACTION_ADD: Final = "add"
ACTION_REMOVE: Final = "remove"
ACTION_READ: Final = "read"

LEVEL_FULL: Final = "var"
LEVEL_LOW: Final = "azaldı"
LEVEL_OUT: Final = "bitti"
LEVELS: Final[tuple[str, ...]] = (LEVEL_FULL, LEVEL_LOW, LEVEL_OUT)

MAX_ITEM_WORDS: Final = 4

_FOLD = str.maketrans("çğıöşüâîû", "cgiosuaiu")


def turkish_lower(text: str) -> str:
    return text.replace("I", "ı").replace("İ", "i").lower()


def fold(word: str) -> str:
    """Lower-case and without Turkish letters: what an ASR transcript may have lost."""
    return turkish_lower(word).translate(_FOLD)


# --------------------------------------------------------------------------- vocabulary

#: folded key word -> (nominative, the form it takes as the head of a compound). The compound
#: form is what "tuvalet KAĞIDI", "kedi MAMASI" say; a lone item is said in the nominative.
_WORDS: Final[dict[str, tuple[str, str]]] = {
    "kagit": ("kağıt", "kağıdı"),
    "havlu": ("havlu", "havlusu"),
    "pecete": ("peçete", "peçetesi"),
    "mendil": ("mendil", "mendili"),
    "deterjan": ("deterjan", "deterjanı"),
    "su": ("su", "suyu"),
    "yumusatici": ("yumuşatıcı", "yumuşatıcısı"),
    "tablet": ("tablet", "tableti"),
    "sabun": ("sabun", "sabunu"),
    "sampuan": ("şampuan", "şampuanı"),
    "macun": ("macun", "macunu"),
    "firca": ("fırça", "fırçası"),
    "poset": ("poşet", "poşeti"),
    "torba": ("torba", "torbası"),
    "sut": ("süt", "sütü"),
    "ekmek": ("ekmek", "ekmeği"),
    "yumurta": ("yumurta", "yumurtası"),
    "peynir": ("peynir", "peyniri"),
    "yogurt": ("yoğurt", "yoğurdu"),
    "tereyag": ("tereyağı", "tereyağı"),
    "yag": ("yağ", "yağı"),
    "zeytinyag": ("zeytinyağı", "zeytinyağı"),
    "cay": ("çay", "çayı"),
    "kahve": ("kahve", "kahvesi"),
    "seker": ("şeker", "şekeri"),
    "tuz": ("tuz", "tuzu"),
    "un": ("un", "unu"),
    "makarna": ("makarna", "makarnası"),
    "pirinc": ("pirinç", "pirinci"),
    "bulgur": ("bulgur", "bulguru"),
    "mercimek": ("mercimek", "mercimeği"),
    "domates": ("domates", "domatesi"),
    "patates": ("patates", "patatesi"),
    "sogan": ("soğan", "soğanı"),
    "sarimsak": ("sarımsak", "sarımsağı"),
    "salca": ("salça", "salçası"),
    "meyve": ("meyve", "meyvesi"),
    "sebze": ("sebze", "sebzesi"),
    "et": ("et", "eti"),
    "tavuk": ("tavuk", "tavuğu"),
    "kiyma": ("kıyma", "kıyması"),
    "bal": ("bal", "balı"),
    "recel": ("reçel", "reçeli"),
    "zeytin": ("zeytin", "zeytini"),
    "mama": ("mama", "maması"),
    "kum": ("kum", "kumu"),
    "bez": ("bez", "bezi"),
    "ampul": ("ampul", "ampulü"),
    "deodorant": ("deodorant", "deodorantı"),
    "cips": ("cips", "cipsi"),
    "biskuvi": ("bisküvi", "bisküvisi"),
}

#: Whole items said as one name (their own nominative, compound included).
_ITEMS: Final[tuple[str, ...]] = (
    "tuvalet kağıdı",
    "kağıt havlu",
    "ıslak mendil",
    "bulaşık deterjanı",
    "çamaşır deterjanı",
    "çamaşır suyu",
    "bulaşık tableti",
    "sıvı sabun",
    "diş macunu",
    "diş fırçası",
    "çöp poşeti",
    "çöp torbası",
    "maden suyu",
    "meyve suyu",
    "kedi maması",
    "köpek maması",
    "kedi kumu",
    "bebek bezi",
)

#: Accusative, possessive and plural endings, longest first (folded).
_SUFFIXES: Final[tuple[str, ...]] = (
    "larimizi",
    "lerimizi",
    "larimiz",
    "lerimiz",
    "larini",
    "lerini",
    "larin",
    "lerin",
    "imizi",
    "umuzu",
    "sini",
    "sunu",
    "lari",
    "leri",
    "imiz",
    "umuz",
    "lar",
    "ler",
    "ini",
    "unu",
    "nin",
    "nun",
    "si",
    "su",
    "ni",
    "nu",
    "yi",
    "yu",
    "in",
    "un",
    "i",
    "u",
)
_SOFTENED: Final[dict[str, str]] = {"d": "t", "b": "p", "g": "k"}


def _soften(word: str) -> str:
    return word[:-1] + _SOFTENED[word[-1]] if word and word[-1] in _SOFTENED else word


def _stem(word: str) -> str:
    """One folded word without its case/possessive/plural ending: a known word when one of
    its readings is known ("cayi" -> "cay", not "ca"; "suyu" -> "su"), else the longest
    ending that leaves three letters."""
    if word in _WORDS:
        return word
    for suffix in _SUFFIXES:
        if word.endswith(suffix) and len(word) - len(suffix) >= 2:
            rest = word[: -len(suffix)]
            if rest in _WORDS:
                return rest
            if _soften(rest) in _WORDS:
                return _soften(rest)
    for suffix in _SUFFIXES:
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            return _soften(word[: -len(suffix)])
    return word


def _clean_words(text: str) -> list[str]:
    """Lower-cased words as said, an apostrophe's suffix cut ("süt'ü" -> "süt")."""
    out: list[str] = []
    for raw in turkish_lower(text).split():
        word = re.split(r"['’`]", raw, maxsplit=1)[0]
        word = re.sub(r"[^\wçğıöşüâîû]", "", word)
        if word:
            out.append(word)
    return out


def item_key(name: str) -> str:
    """The one key an item has under all its spoken forms ("Tuvalet kağıdını" ==
    "tuvalet kağıdı" == "tuvalet kagidi"). Only the last word inflects in Turkish."""
    words = [fold(w) for w in _clean_words(name)]
    words = [w for w in words if w]
    if not words:
        return ""
    words[-1] = _stem(words[-1])
    return " ".join(words)


_ITEM_BY_KEY: Final[dict[str, str]] = {item_key(name): name for name in _ITEMS}


def known_item(key: str) -> bool:
    """A thing a house runs out of: a vocabulary item, or a name whose head word is one."""
    if not key:
        return False
    return key in _ITEM_BY_KEY or key.split()[-1] in _WORDS


#: Words (folded) that describe the head noun after them: "TAZE süt", "ESMER şeker", "tam
#: YAĞLI süt" - an adjective, so the head stays nominative ("taze sütü" is its accusative).
#: Nouns that also describe ("KÖY ekmeği", "TOZ bezi") are left out: they make a compound.
_ADJECTIVES: Final = frozenset(
    {
        "taze",
        "esmer",
        "beyaz",
        "kirmizi",
        "yesil",
        "siyah",
        "sari",
        "yagli",
        "yagsiz",
        "organik",
        "dogal",
        "light",
        "laktozsuz",
        "sekersiz",
        "tuzlu",
        "tuzsuz",
        "kepekli",
        "kup",
        "kuru",
        "sade",
        "buyuk",
        "kucuk",
    }
)


def display_name(words: list[str]) -> str:
    """The name to keep and say: the vocabulary's own nominative when the key is known
    ("sütü" -> "süt", "kedi mamasını" -> "kedi maması"), else the words as said. A head said
    in the nominative, or after an adjective, stays nominative: "taze süt" is not "taze sütü"
    (test team round t-r10070152)."""
    said = " ".join(words)
    key = item_key(said)
    if key in _ITEM_BY_KEY:
        return _ITEM_BY_KEY[key]
    head = key.split()[-1] if key else ""
    if head in _WORDS:
        nominative, compound = _WORDS[head]
        if len(words) == 1 or fold(words[-1]) == fold(nominative) or fold(words[-2]) in _ADJECTIVES:
            return " ".join([*words[:-1], nominative])
        return " ".join([*words[:-1], compound])
    return said


# --------------------------------------------------------------------------- commands


@dataclass(frozen=True)
class HouseholdCommand:
    action: str
    item: str | None = None
    level: str | None = None
    quantity: str | None = None
    matched: str = ""


_QUESTION: Final = frozenset(
    {"mi", "mu", "miydi", "muydu", "ne", "neden", "nasil", "kim", "hangi", "zaman", "nerede"}
)
_PLACE: Final = frozenset(
    {
        "evde",
        "evdeki",
        "evimizde",
        "mutfakta",
        "mutfaktaki",
        "dolapta",
        "dolaptaki",
        "stokta",
        "stoktaki",
        "banyoda",
        "banyodaki",
    }
)
_FILLER: Final = frozenset(
    {
        "bizim",
        "benim",
        "yine",
        "artik",
        "de",
        "da",
        "bugun",
        "dun",
        "bu",
        "biraz",
        "cok",
        "hep",
        "galiba",
        "sanirim",
        "neredeyse",
        "tamamen",
        "hic",
        "hicbir",
        "ben",
        "biz",
        "efendim",
        "lutfen",
        "bana",
        "hey",
        "jarvis",
        "tamam",
    }
)
#: Folded stems of things a house does not stock: "Evde kimse kalmadı", "Evde elektrik bitti",
#: "Listeye not ekle", "Görevi listeden sil" are about people, utilities, notes and tasks. A
#: place word ("evde") or a bare "listeye" lets an unknown item in; these never are - as a
#: ONE-word name only: "streç film", "yer fıstığı", "enerji içeceği" are goods.
_NOT_GOODS: Final = frozenset(
    {
        "sey",
        "hicbirsey",
        "bunu",
        "onu",
        "bunlari",
        "onlari",
        "kimse",
        "kimsecik",
        "insan",
        "misafir",
        "para",
        "nakit",
        "elektrik",
        "dogalgaz",
        "gaz",
        "internet",
        "wifi",
        "sinyal",
        "sarj",
        "isik",
        "enerji",
        "zaman",
        "vakit",
        "yer",
        "sabir",
        "huzur",
        "keyif",
        "umut",
        "ses",
        "not",
        "gorev",
        "is",
        "toplanti",
        "randevu",
        "etkinlik",
        "hatirlatma",
        "sarki",
        "video",
        "film",
        "dizi",
        "mesaj",
        "mail",
        "eposta",
        "link",
        "kisi",
        "numara",
    }
)
#: The words that may qualify "X listesine / listesinden" as the shopping list; any other
#: qualifier ("çalma listesine", "yapılacaklar listesinden") names another list.
_SHOP_QUALIFIERS: Final = frozenset({"alisveris", "market", "bakkal", "pazar", "ev", "mutfak"})
#: The words that name another list before ANY list word, the first-person forms included:
#: "Çalma listeme bunu ekle" has no compound ending to read, so the qualifier is named here.
_OTHER_LISTS: Final = frozenset(
    {
        "calma",
        "oynatma",
        "izleme",
        "okuma",
        "yapilacaklar",
        "yapilacak",
        "dilek",
        "istek",
        "favori",
        "muzik",
        "sarki",
        "video",
    }
)
#: Words that may follow the verb without changing what was said.
_TAIL: Final = frozenset({"efendim", "ya", "galiba", "sanirim", "artik", "bile"})

_LOW_WORDS: Final = frozenset({"azaldi", "azalmis", "azaliyor", "bitiyor", "bitmekte"})
_LOW_PAIRS: Final = frozenset(
    {("bitmek", "uzere"), ("az", "kaldi"), ("azalmak", "uzere"), ("tukenmek", "uzere")}
)
_OUT_WORDS: Final = frozenset({"bitti", "bitmis", "tukendi", "tukenmis", "kalmadi", "kalmamis"})
_BOUGHT_WORDS: Final = frozenset({"aldim", "aldik", "alindi"})

_LIST_TO: Final = frozenset({"listeye", "listesine", "listeme", "listemize"})
_LIST_FROM: Final = frozenset({"listeden", "listesinden", "listemden", "listemizden"})
_LIST_NOUN: Final = frozenset(
    {
        "liste",
        "listesi",
        "listesini",
        "listeyi",
        "listem",
        "listemi",
        "listemizi",
        "listede",
        "listemde",
        "listesinde",
        "listemizde",
    }
)
_ADD_VERBS: Final = frozenset(
    {
        "ekle",
        "ekler",
        "eklesene",
        "ekleyin",
        "ekleyiver",
        "yaz",
        "yazar",
        "yazsana",
        "yazin",
        "koy",
        "koyar",
        "koysana",
    }
)
_REMOVE_VERBS: Final = frozenset(
    {
        "cikar",
        "cikart",
        "cikarir",
        "cikarsana",
        "cikarin",
        "sil",
        "siler",
        "silsene",
        "silin",
        "kaldir",
        "kaldirir",
        "kaldirsana",
    }
)
_POLITE: Final = frozenset({"misin", "misiniz", "musun", "musunuz"})
_SHOP_WORDS: Final = frozenset({"alisveris", "market", "bir", "sunu", "sunlari"})
_READ_VERBS: Final = frozenset(
    {"oku", "okur", "okusana", "okuyun", "soyle", "soyler", "say", "sayar"}
)
_READ_EXTRA: Final = frozenset(
    {"ne", "neler", "var", "bana", "lutfen", "efendim", "simdi", "alisveris", "market"}
)
_NEED_BUY: Final = frozenset({"almam", "almamiz", "alinacak", "almaliyim", "almaliyiz"})
_NEED: Final = frozenset({"lazim", "gerek", "gerekiyor", "gerekli"})
_SHOP_FROM: Final = frozenset({"marketten", "markette", "bakkaldan", "pazardan", "manavdan"})
_SHALL_BUY: Final = frozenset({"alayim", "alalim"})
_GO_PLACE: Final = frozenset({"markete", "alisverise", "pazara", "bakkala", "manava"})
_GO_VERB: Final = frozenset(
    {
        "gidiyorum",
        "gidiyoruz",
        "gidecegim",
        "gidecegiz",
        "gidicem",
        "cikiyorum",
        "cikiyoruz",
        "ugruyorum",
        "ugrayacagim",
    }
)
_MISSING: Final = frozenset({"eksik", "eksikler", "eksikleri", "eksiklerimiz"})

_NUMBERS: Final[dict[str, str]] = {
    "bir": "bir",
    "iki": "iki",
    "uc": "üç",
    "dort": "dört",
    "bes": "beş",
    "alti": "altı",
    "yedi": "yedi",
    "sekiz": "sekiz",
    "dokuz": "dokuz",
    "on": "on",
    "yarim": "yarım",
}
_UNITS: Final = frozenset(
    {
        "paket",
        "litre",
        "kilo",
        "kilogram",
        "kg",
        "gram",
        "gr",
        "tane",
        "adet",
        "sise",
        "kutu",
        "koli",
        "rulo",
        "duzine",
        "demet",
        "kavanoz",
        "bidon",
    }
)


def _number(folded: str) -> bool:
    return folded.isdigit() or folded in _NUMBERS


def _item_words(words: list[str]) -> list[str] | None:
    """The words that name the item, or None when they cannot be one."""
    kept = [w for w in words if fold(w) not in _FILLER and fold(w) not in _PLACE]
    if not kept or len(kept) > MAX_ITEM_WORDS:
        return None
    if any(fold(w) in _QUESTION or fold(w) in ("ve", "ile") for w in kept):
        return None
    if not all(re.search(r"[^\W\d_]", w) for w in kept):
        return None
    if len(kept) == 1 and (fold(kept[0]) in _NOT_GOODS or _stem(fold(kept[0])) in _NOT_GOODS):
        return None
    return kept


def _level_of(folded: list[str]) -> tuple[str, int] | None:
    """(level, how many words the verb took) when the sentence ends in a level verb."""
    if len(folded) >= 2 and (folded[-2], folded[-1]) in _LOW_PAIRS:
        return LEVEL_LOW, 2
    last = folded[-1]
    if last in _LOW_WORDS:
        return LEVEL_LOW, 1
    if last in _OUT_WORDS:
        return LEVEL_OUT, 1
    if last in _BOUGHT_WORDS:
        return LEVEL_FULL, 1
    return None


def _parse_level(words: list[str], folded: list[str]) -> HouseholdCommand | None:
    if any(f in _QUESTION for f in folded):
        return None
    end = len(folded)
    while end > 0 and folded[end - 1] in _TAIL:
        end -= 1
    if end < 2:
        return None
    found = _level_of(folded[:end])
    if found is None:
        return None
    level, width = found
    # "Bir kahve aldım", "İki paket süt aldım": the count is not the name.
    named, _ = _quantity(words[: end - width])
    item = _item_words(named) if named else None
    if item is None:
        return None
    key = item_key(" ".join(item))
    placed = any(f in _PLACE for f in folded)
    if not (known_item(key) or placed):
        return None
    return HouseholdCommand(
        ACTION_LEVEL,
        item=display_name(item),
        level=level,
        matched=" ".join(words[end - width : end]),
    )


def _quantity(words: list[str]) -> tuple[list[str], str | None]:
    """The words without the quantity, and the quantity ("iki paket", "3 litre")."""
    out: list[str] = []
    quantity: str | None = None
    index = 0
    while index < len(words):
        folded = fold(words[index])
        if _number(folded) and quantity is None:
            spoken = words[index] if folded.isdigit() else _NUMBERS[folded]
            following = fold(words[index + 1]) if index + 1 < len(words) else ""
            if following in _UNITS:
                quantity = f"{spoken} {words[index + 1]}"
                index += 2
                continue
            if folded != "bir":
                quantity = spoken
            index += 1
            continue
        out.append(words[index])
        index += 1
    return out, quantity


def _parse_list_edit(
    words: list[str], folded: list[str], anchors: frozenset[str], verbs: frozenset[str], action: str
) -> HouseholdCommand | None:
    anchor_at = next((i for i, f in enumerate(folded) if f in anchors), None)
    if anchor_at is None:
        return None
    verb_at = next((i for i, f in enumerate(folded) if f in verbs), None)
    if verb_at is None:
        return None
    if anchor_at > 0 and folded[anchor_at - 1] in _OTHER_LISTS:
        return None  # "çalma listeme", "oynatma listemden": another list
    qualified = folded[anchor_at].startswith("listesi")
    qualifier = anchor_at - 1 if qualified and anchor_at > 0 else None
    if qualifier is not None and folded[qualifier] not in _SHOP_QUALIFIERS:
        return None  # "çalma listesine", "yapılacaklar listesinden": another list
    rest = [
        w
        for i, (w, f) in enumerate(zip(words, folded, strict=True))
        if i not in (verb_at, qualifier)
        and f not in anchors
        and f not in _POLITE
        and f not in _SHOP_WORDS
    ]
    quantity: str | None = None
    if action == ACTION_ADD:
        rest, quantity = _quantity(rest)
    else:
        rest = [w for w in rest if not _number(fold(w))]
    item = _item_words(rest) if rest else None
    if rest and item is None:
        return None
    return HouseholdCommand(
        action,
        item=display_name(item) if item else None,
        quantity=quantity,
        matched=words[verb_at],
    )


def _parse_read(words: list[str], folded: list[str]) -> HouseholdCommand | None:
    has = set(folded)
    if has & {"ne", "neler"}:
        if has & _NEED_BUY and (has & _NEED or has & _SHOP_FROM or has & {"alinacak", "almaliyim"}):
            return HouseholdCommand(ACTION_READ, matched="ne almam lazım")
        if has & _SHALL_BUY and has & _SHOP_FROM:
            return HouseholdCommand(ACTION_READ, matched="ne alayım")
    if has & _GO_PLACE and has & _GO_VERB and not has & _QUESTION:
        return HouseholdCommand(ACTION_READ, matched="markete gidiyorum")
    if has & _MISSING and (has & {"ne", "neler"} or has & _PLACE):
        return HouseholdCommand(ACTION_READ, matched="ne eksik")
    if has & _LIST_NOUN:
        asks = bool(has & _READ_VERBS) or ({"var"} <= has and bool(has & {"ne", "neler"}))
        if asks and has <= (_LIST_NOUN | _READ_VERBS | _READ_EXTRA):
            return HouseholdCommand(ACTION_READ, matched="listeyi oku")
    return None


def parse_words(words: list[str]) -> HouseholdCommand | None:
    """The command these lower-cased words carry, or None (not a household sentence)."""
    words = [w for w in words if w]
    if not words:
        return None
    folded = [fold(w) for w in words]
    for anchors, verbs, action in (
        (_LIST_FROM, _REMOVE_VERBS, ACTION_REMOVE),
        (_LIST_TO, _ADD_VERBS, ACTION_ADD),
    ):
        command = _parse_list_edit(words, folded, anchors, verbs, action)
        if command is not None:
            return command
    return _parse_read(words, folded) or _parse_level(words, folded)


# --------------------------------------------------------------------------- typed input

MAX_NAME_CHARS: Final = 40
NAME_NOT_UNDERSTOOD: Final = (
    "Bunu ürün adı olarak anlayamadım; kısa bir ürün adı yaz (örneğin 'süt' ya da "
    "'tuvalet kağıdı')."
)
QUANTITY_NOT_POSITIVE: Final = (
    "Miktar sıfırdan büyük bir sayı olmalı, istersen birimiyle (örneğin 'iki paket' ya da "
    "'3 litre')."
)

#: Number words a typed quantity may be built of ("on iki", "yüz elli"); "yarım" is half.
_COUNT_WORDS: Final = frozenset(
    {*_NUMBERS, "yirmi", "otuz", "kirk", "elli", "altmis", "yetmis", "seksen", "doksan", "yuz"}
)
#: A digit amount with an optional unit glued on ("3", "1,5", "500gr"); no sign.
_DIGIT_AMOUNT: Final = re.compile(r"(\d+(?:[.,]\d+)?)([^\W\d_]*)")
#: Finite-verb endings (folded) that no shop item ends in: the progressive ("gidiyorum"), the
#: first-person past ("unuttum"), future ("alacağım") and necessity ("almalıyım"). The bare
#: participles are left out on purpose: "kuru yemiş", "içecek" are goods.
_VERB_ENDING: Final = re.compile(
    r"(?:[iu]yor(?:um|uz|sun|sunuz|lar|du|dum|duk|mus)?"
    r"|..[dt][iu]m"
    r"|[ae]c[ae]g[iu][mz]"
    r"|m[ae]l[iu]y[iu][mz])$"
)
#: The verbs the voice path reads; a name holding one is a sentence, not a thing.
_VERBS: Final = (
    _LOW_WORDS
    | _OUT_WORDS
    | _BOUGHT_WORDS
    | _ADD_VERBS
    | _REMOVE_VERBS
    | _READ_VERBS
    | _GO_VERB
    | _NEED
    | _NEED_BUY
    | _SHALL_BUY
    | _POLITE
)
#: Verb stems (folded) a shopping sentence ends on: buy, bring, add, write, forget, look.
_VERB_STEMS: Final = ("al", "getir", "ekle", "yaz", "unut", "bak", "iste")
#: What follows such a stem in a finite or verbal-noun form (folded, vowels loosely
#: harmonised): an optional passive, then the imperative (bare, "-sana", "-in"), the
#: conditional ("-sak"), optative ("-alım"), aorist with a person ("-ırız"), necessity
#: ("-malı"), future, past, the verbal nouns ("-mayı", "-mak", "-mam") or the negative ("-ma").
_STEM_ENDING: Final = (
    r"(?:[iu]?n|[iu]l)?"
    r"(?:"
    r"|m[ae]"
    r"|s[ae]n[ae]|s[iu]n|y?[iu]n(?:[iu]z)?"
    r"|s[ae](?:m|n|k|n[iu]z)?"
    r"|y?[ae](?:y[iu]m|l[iu]m)"
    r"|[aeiu]?r(?:[iu]m|[iu]z|s[iu]n|s[iu]n[iu]z|l[ae]r)?"
    r"|m[ae]l[iu](?:y[iu][mz])?"
    r"|y?[ae]c[ae](?:k|g[iu][mz])"
    r"|[dt][iu](?:m|k|n|n[iu]z|l[ae]r)?"
    r"|m[ae]y[iu]|m[ae]k|m[ae]m(?:[iu]z)?"
    r")"
)
_STEM_VERB: Final = re.compile(rf"(?:{'|'.join(_VERB_STEMS)}){_STEM_ENDING}")
#: Words that make a phrase a statement or a question about a thing: "süt var", "süt yok mu".
_PREDICATE: Final = frozenset({"var", "yok", "mi", "mu"})


def name_problem(raw: str) -> str | None:
    """Why a typed item name is not one (a Turkish sentence), or None: a name is a short noun
    phrase - at most MAX_NAME_CHARS letters and MAX_ITEM_WORDS words, and no verb. Turkish
    ends a sentence on its verb, so a verb stem counts on the last word only ("yaz meyvesi"
    is a thing, "listeye süt yaz" is not)."""
    words = _clean_words(raw)
    if len(" ".join(raw.split())) > MAX_NAME_CHARS or len(words) > MAX_ITEM_WORDS:
        return NAME_NOT_UNDERSTOOD
    if parse_words(words) is not None:  # "süt bitti", "listeye süt ekle", "markete gidiyorum"
        return NAME_NOT_UNDERSTOOD
    folded = [fold(w) for w in words]
    if not folded:
        return None
    if any(f in _PREDICATE or _VERB_ENDING.search(f) for f in folded):
        return NAME_NOT_UNDERSTOOD
    last = folded[-1]
    if last in _VERBS or _STEM_VERB.fullmatch(last):
        return NAME_NOT_UNDERSTOOD
    return None


def quantity_problem(raw: str) -> str | None:
    """Why a typed quantity is not a positive amount, or None: a number above zero (digits or
    Turkish number words) and at most one unit word after it - "iki paket", "1,5 litre", "2kg"."""
    words = raw.split()
    if not words:
        return None
    first = _DIGIT_AMOUNT.fullmatch(words[0])
    if first is not None:
        if float(first.group(1).replace(",", ".")) <= 0:
            return QUANTITY_NOT_POSITIVE
        rest = words[1:]
        if first.group(2) and rest:
            return QUANTITY_NOT_POSITIVE
    else:
        count = 0
        while count < len(words) and fold(words[count]) in _COUNT_WORDS:
            count += 1
        if count == 0:
            return QUANTITY_NOT_POSITIVE
        rest = words[count:]
    if len(rest) > 1 or not all(re.fullmatch(r"[^\W\d_]+", w) for w in rest):
        return QUANTITY_NOT_POSITIVE
    return None


def split_amount(name: str) -> tuple[str, str | None]:
    """A typed name that starts with its amount, split: "iki şişe süt" -> ("süt", "iki şişe"),
    "2 litre süt" -> ("süt", "2 litre"). Anything else, or an amount with nothing after it,
    comes back whole with None (test team round t-r10070152). The amount is every number word
    in a row ("on iki yumurta" is twelve eggs, not "iki yumurta" times ten); a number before an
    adjective is part of the name ("yarım yağlı süt" is a kind of milk)."""
    words = name.split()
    folded = [fold(w) for w in words]
    if folded and folded[0].isdigit():
        count = 1
    else:
        count = 0
        while count < len(folded) and folded[count] in _COUNT_WORDS:
            count += 1
    if count == 0:
        return name, None
    end = count + 1 if count < len(folded) and folded[count] in _UNITS else count
    rest = words[end:]
    if not rest or fold(rest[0]) in _ADJECTIVES:
        return name, None
    if folded[:end] == ["bir"]:
        return " ".join(rest), None  # "bir süt" is one süt, said the way one says a thing
    spoken = [_NUMBERS.get(f, w) for w, f in zip(words[:count], folded, strict=False)]
    return " ".join(rest), " ".join([*spoken, *words[count:end]])


def parse_tokens(tokens: tuple[str, ...]) -> HouseholdCommand | None:
    """The router's entry: its normalized tokens (lower-case; an apostrophe kept)."""
    return parse_words(_clean_words(" ".join(tokens)))


def parse_sentence(text: str) -> HouseholdCommand | None:
    return parse_words(_clean_words(text))


__all__ = [
    "ACTION_ADD",
    "ACTION_LEVEL",
    "ACTION_READ",
    "ACTION_REMOVE",
    "LEVELS",
    "LEVEL_FULL",
    "LEVEL_LOW",
    "LEVEL_OUT",
    "HouseholdCommand",
    "display_name",
    "fold",
    "item_key",
    "known_item",
    "name_problem",
    "parse_sentence",
    "parse_tokens",
    "parse_words",
    "quantity_problem",
    "split_amount",
]
