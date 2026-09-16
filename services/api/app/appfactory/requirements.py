"""The general requirements parser (B40 req 422): the owner's sentence about an
application -> :class:`Requirements`, with everything it could NOT read named in
``unparsed`` and never dropped in silence.

Rule-based and Turkish-first (docs/M23_APP_FACTORY_SPEC.md §1: a spec is produced from the
owner's own words, never free prose handed to a generator). The three built-in templates
keep their phrases (``app.voice.intents._appfactory_template_from_tokens``); this parser
reads what those templates cannot hold: the records the application keeps and their
fields, whether it needs a login, whether it is an API, what it is called. The planner
(``app.appfactory.planner``) turns the result into an architecture and a file plan; the
composer builds the files.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Final

MAX_ENTITIES: Final = 8
MAX_FIELDS: Final = 12
MAX_SENTENCE_CHARS: Final = 600

FEATURE_API: Final = "api"
FEATURE_DATABASE: Final = "database"
FEATURE_AUTH: Final = "auth"
FEATURE_FRONTEND: Final = "frontend"
FEATURES: Final[tuple[str, ...]] = (FEATURE_API, FEATURE_DATABASE, FEATURE_AUTH, FEATURE_FRONTEND)

#: Field-type words (req 427): the type a field carries is read off its NAME.
_NUMBER_WORDS: Final[tuple[str, ...]] = (
    "tutar",
    "fiyat",
    "adet",
    "sayı",
    "sayi",
    "miktar",
    "ücret",
    "ucret",
    "puan",
    "yaş",
    "yas",
    "stok",
    "toplam",
    "oran",
    "yüzde",
    "yuzde",
    "bedel",
    "maliyet",
    "kilo",
    "gram",
    "metre",
)
_DATE_WORDS: Final[tuple[str, ...]] = ("tarih", "gün", "gun", "zaman", "vade", "doğum", "dogum")
_BOOLEAN_WORDS: Final[tuple[str, ...]] = (
    "tamamlandı",
    "tamamlandi",
    "aktif",
    "bitti",
    "yapıldı",
    "yapildi",
    "onaylı",
    "onayli",
    "ödendi",
    "odendi",
    "açık",
    "acik",
)
_AUTH_WORDS: Final[tuple[str, ...]] = (
    "giriş",
    "giris",
    "şifre",
    "sifre",
    "parola",
    "login",
    "kullanıcı hesabı",
    "kullanici hesabi",
    "oturum",
    "girişli",
    "girisli",
)
_API_WORDS: Final[tuple[str, ...]] = (
    "api",
    "sunucu",
    "servis",
    "backend",
    "arka uç",
    "arka uc",
    "rest",
)
_API_ONLY_WORDS: Final[tuple[str, ...]] = (
    "sadece api",
    "yalnız api",
    "yalnizca api",
    "arayüzsüz",
    "arayuzsuz",
)
_KEEP_VERBS: Final[tuple[str, ...]] = (
    "tutan",
    "tutsun",
    "kaydeden",
    "kaydetsin",
    "yöneten",
    "yoneten",
    "yönetsin",
    "yonetsin",
    "listeleyen",
    "listelesin",
    "takip eden",
    "takip etsin",
    "saklayan",
    "saklasın",
    "saklasin",
    "izleyen",
    "izlesin",
)
_NOISE_WORDS: Final[frozenset[str]] = frozenset(
    {
        # feature words are never record kinds ("sadece api olan ... servis")
        "api",
        "sadece",
        "yalnız",
        "yalniz",
        "yalnızca",
        "yalnizca",
        "servis",
        "sunucu",
        "backend",
        "rest",
        "arayüzsüz",
        "arayuzsuz",
        "bana",
        "bir",
        "ve",
        "ile",
        "için",
        "icin",
        "olan",
        "uygulama",
        "uygulaması",
        "uygulamasi",
        "yap",
        "oluştur",
        "olustur",
        "hazırla",
        "hazirla",
        "küçük",
        "kucuk",
        "basit",
        "web",
        "adı",
        "adi",
        "ismi",
        "girişli",
        "girisli",
        "girişi",
        "girisi",
        "kayıtları",
        "kayitlari",
        "kayıtlarını",
        "kayitlarini",
        "bilgilerini",
        "bilgileri",
    }
)
_PLURAL_SUFFIXES: Final[tuple[str, ...]] = (
    # "kitaplarımı" / "müşterilerimizi": the possessive-accusative forms first, longest first.
    "larımızı",
    "lerimizi",
    "larımız",
    "lerimiz",
    "larınız",
    "leriniz",
    "larımı",
    "lerimi",
    "larım",
    "lerim",
    "lerini",
    "larını",
    "larini",
    "lerinin",
    "larının",
    "larinin",
    "leriyle",
    "larıyla",
    "leri",
    "ları",
    "lari",
    "lere",
    "lara",
    "lerde",
    "larda",
    "lerden",
    "lardan",
    "ler",
    "lar",
)
_NAME_RE: Final = re.compile(
    r"(?:adı|ismi|adi)\s*[:=]?\s*([\"“”']?)([^\"“”'.,;:]{1,60})\1", re.IGNORECASE
)


@dataclass(slots=True)
class FieldSpec:
    name: str
    type: str = "text"

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "type": self.type}


@dataclass(slots=True)
class EntitySpec:
    name: str
    fields: list[FieldSpec] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "fields": [f.as_dict() for f in self.fields]}


@dataclass(slots=True)
class Requirements:
    """What the parser read, and what it could not (``unparsed``, req 422)."""

    sentence: str
    name: str | None = None
    entities: list[EntitySpec] = field(default_factory=list)
    features: list[str] = field(default_factory=list)
    unparsed: list[str] = field(default_factory=list)
    #: The built-in template the sentence names, when it does (the three shapes stay).
    template_hint: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "sentence": self.sentence,
            "name": self.name,
            "entities": [e.as_dict() for e in self.entities],
            "features": list(self.features),
            "unparsed": list(self.unparsed),
            "template_hint": self.template_hint,
        }

    @property
    def wants_auth(self) -> bool:
        return FEATURE_AUTH in self.features

    @property
    def wants_frontend(self) -> bool:
        return FEATURE_FRONTEND in self.features


def _lower(text: str) -> str:
    return text.replace("I", "ı").replace("İ", "i").lower()


def _singular(word: str) -> str:
    w = word.strip(" ,.;:'\"")
    for suffix in _PLURAL_SUFFIXES:
        if w.endswith(suffix) and len(w) - len(suffix) >= 3:
            return w[: -len(suffix)]
    return w


def _field_type(name: str) -> str:
    n = _lower(name)
    if any(w in n for w in _DATE_WORDS):
        return "date"
    if any(w in n for w in _NUMBER_WORDS):
        return "number"
    if any(w in n for w in _BOOLEAN_WORDS) or n.endswith((" mı", " mi", " mu", " mü")):
        return "boolean"
    return "text"


def _clean_field(name: str, *, entity: str = "") -> str:
    """ "müşteri adı" of the kind "müşteri" -> "ad"; "sipariş tutarı" -> "tutar"; the
    possessive suffix a Turkish noun phrase carries is dropped so the field is a name."""
    n = _lower(name).strip(" ,.;:'\"")
    n = re.sub(r"\s+", " ", n)
    if entity and n.startswith(entity + " "):
        n = n[len(entity) + 1 :]
    n = re.sub(r"\s+(mı|mi|mu|mü)$", "", n)
    words = n.split(" ")
    last = words[-1]
    for suffix in ("ları", "leri", "sı", "si", "su", "sü", "ı", "i", "u", "ü"):
        if last.endswith(suffix) and len(last) - len(suffix) >= 2 and last not in _BOOLEAN_WORDS:
            words[-1] = last[: -len(suffix)]
            break
    return " ".join(words)[:40]


def _entity_from_phrase(phrase: str) -> str:
    words = [w for w in _lower(phrase).split() if w and w not in _NOISE_WORDS]
    if not words:
        return ""
    return _singular(words[-1])[:40]


def _split_list(text: str) -> list[str]:
    parts = re.split(r"\s*(?:,|;|\bve\b|\bile\b)\s*", text)
    return [p.strip(" .:'\"") for p in parts if p and p.strip(" .:'\"")]


def parse_requirements(text: str) -> Requirements:
    """The owner's sentence -> :class:`Requirements`. Never raises for a sentence it
    cannot read: an empty entity list and the unread parts in ``unparsed`` are the
    honest answer, and the planner decides what to do with them."""
    sentence = (text or "").strip()[:MAX_SENTENCE_CHARS]
    req = Requirements(sentence=sentence)
    if not sentence:
        return req
    lowered = _lower(sentence)

    # The name: "adı X" / "ismi X".
    match = _NAME_RE.search(sentence)
    if match:
        req.name = match.group(2).strip()[:60] or None

    # Features.
    if any(w in lowered for w in _AUTH_WORDS):
        req.features.append(FEATURE_AUTH)
    if any(w in lowered for w in _API_WORDS):
        req.features.append(FEATURE_API)
    req.features.append(FEATURE_DATABASE)
    if not any(w in lowered for w in _API_ONLY_WORDS):
        req.features.append(FEATURE_FRONTEND)

    # The template hint (the three built-in shapes keep their words).
    if "görev" in lowered and "takip" in lowered:
        req.template_hint = "task-tracker"
    elif "web sayfa" in lowered or "web sayfası" in lowered:
        req.template_hint = "static-page"
    elif "komut satır" in lowered or re.search(r"\bcli\b", lowered):
        req.template_hint = "cli-tool"

    # Entities and fields: "X: a, b; Y: c, d" after the first colon, else the nouns
    # before a keep-verb ("müşterileri ve siparişleri tutan").
    head, _, tail = sentence.partition(":")
    fields_text = tail if tail.strip() else ""
    if not fields_text and req.name and _NAME_RE.search(head):
        # "adı X" may sit in the head; the colon rule is still the field rule.
        pass
    entity_names: list[str] = []
    keep = next((v for v in _KEEP_VERBS if v in _lower(head)), None)
    if keep:
        before = _lower(head).split(keep, 1)[0]
        before = (
            re.sub(r"^.*?\b(?:bana|bir)\b", "", before)
            if "bana" in before or " bir " in before
            else before
        )
        for phrase in _split_list(before):
            name = _entity_from_phrase(phrase)
            if name and name not in entity_names and name not in _NOISE_WORDS:
                entity_names.append(name)
    if fields_text:
        for group in [g for g in re.split(r"\s*;\s*", fields_text) if g.strip()]:
            if ":" in group:
                ent, _, flds = group.partition(":")
                ent_name = _entity_from_phrase(ent) or _singular(_lower(ent).strip())
                field_names = _split_list(flds)
                if ent_name in entity_names:
                    entity_names.remove(ent_name)
            else:
                # The groups follow the kinds named before the colon, in order.
                ent_name = entity_names.pop(0) if entity_names else ""
                field_names = _split_list(group)
            if not ent_name:
                req.unparsed.append(group.strip()[:120])
                continue
            entity = EntitySpec(name=ent_name[:40])
            for raw in field_names[:MAX_FIELDS]:
                cleaned = _clean_field(raw, entity=ent_name)
                if cleaned:
                    entity.fields.append(FieldSpec(name=cleaned, type=_field_type(cleaned)))
            if not entity.fields:
                req.unparsed.append(group.strip()[:120])
                continue
            req.entities.append(entity)
    for name in entity_names:
        if len(req.entities) >= MAX_ENTITIES:
            req.unparsed.append(name)
            continue
        req.entities.append(EntitySpec(name=name, fields=[FieldSpec(name="ad", type="text")]))
    req.entities = req.entities[:MAX_ENTITIES]
    if not req.entities and not req.template_hint:
        req.unparsed.append("hangi kayıtları tutacağı söylenmedi")
    return req


__all__ = [
    "FEATURES",
    "FEATURE_API",
    "FEATURE_AUTH",
    "FEATURE_DATABASE",
    "FEATURE_FRONTEND",
    "MAX_ENTITIES",
    "MAX_FIELDS",
    "EntitySpec",
    "FieldSpec",
    "Requirements",
    "parse_requirements",
]
