"""B25 req 701: "Neler yapabilirsin?" — derived from the tool registry, never written out.

The requirement's test plan says it in four words: *elle liste yasak*. A hand-written list of
what this system can do is a second source of truth about its own abilities, and it starts
drifting the day after it is written — the one thing an assistant must not be wrong about.

So this reads `default_registry()`. And it reads it for more than the names, because the
registry already holds the thing the owner actually needs. Every one of the tool
descriptions is written in Turkish for the model, and most of them carry the owner's OWN
sentences in quotes so the model can recognise them::

    alarm.snooze  →  "Çalan alarmı ERTELER ('beş dakika ertele', 'on dakika ertele'). …"
    eye.disable   →  "Active Eye'ı (kamerayı) KAPATIR: 'gözünü kapat', 'kamerayı kapat', …"

Those quoted fragments are exactly what the batch's goal asks for — *"Sahip sisteme ne
diyebileceğini görebilsin"* — so they are lifted out and listed as things to say, rather
than paraphrased into a second wording that could disagree with the one the model was told.

The only hand-written thing here is `FAMILY_TR`: a Turkish name per family prefix, because
`selfmodel` and `executive` are words for the person building the system, not for the person
using it. A test asserts it covers the registry exactly, so a new family fails the suite
instead of appearing to the owner as a bare English prefix.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - types only
    from app.voice.realtime_sessions.tools import ToolRegistry

#: The owner's word for each tool-name prefix. Checked against the registry by a test:
#: this list may not be shorter than the registry's families, and may not be longer.
FAMILY_TR: dict[str, str] = {
    "activity": "Geçmiş ve açıklama",
    "alarm": "Alarmlar",
    "ambient": "Ekran ve ortam",
    "app": "Uygulama yapımı",
    "artifact": "Belge üretimi",
    "assistant": "Neler yapabilirim",
    "briefing": "Brifingler",
    "calendar": "Takvim",
    "capability": "Yeni yetenek edinme",
    "clock": "Saat",
    "creative": "Görsel çalışma",
    "display": "Ekran gücü",
    "document": "Belgelerim",
    "evolution": "Kendini geliştirme",
    "executive": "Çok adımlı işler",
    "eye": "Kamera",
    "file": "Dosya arama",
    "location": "Konum",
    "mail": "Posta",
    "media": "Müzik ve ses",
    "memory": "Hafıza",
    "narration": "Seslendirme",
    "native": "Yerel uygulama yapımı",
    "news": "Haberler",
    "operator": "Bilgisayarı kullanma",
    "plan": "Planı değiştirme",
    "pronunciation": "Telaffuz",
    "release": "Sürüm",
    "research": "Araştırma",
    # B35 (req 622/623): assigning the system work on its own code, by voice.
    "selfdev": "Kendi kodunu düzeltme",
    "routine": "Rutinler",
    "scene": "3B sahne",
    "state": "Anlık durum",
    "voice": "Ses yönlendirme",
    "weather": "Hava durumu",
}

#: A phrase the owner might say, as the tool description quotes it.
#:
#: The obvious pattern — ``'([^']+)'`` — is wrong in Turkish, and wrong in the way this
#: repository keeps being wrong in Turkish: the apostrophe is a SUFFIX separator. In
#: ``Active Eye'ı (kamerayı) KAPATIR: 'gözünü kapat'`` the naive reader opens a quote at
#: ``Eye'`` and closes it before ``gözünü``, so it extracts ``ı (kamerayı) KAPATIR:`` and
#: silently loses the phrase it was looking for. Three of the six families checked by hand
#: were damaged this way.
#:
#: So an opening quote must follow a boundary, a closing quote must be followed by one, and
#: an apostrophe with a letter on BOTH sides is part of a word rather than a delimiter —
#: which is what lets ``'İstanbul'da hava nasıl?'`` come out whole.
_QUOTED = re.compile(r"(?<![^\s(:,])'((?:[^']|(?<=\w)'(?=\w)){3,80})'(?![^\s).,:;!?])")

#: Never present these as things to say. Each is a tool the model calls on its own behalf
#: as part of another job, so listing it would invite an owner to ask for a step rather
#: than for the thing they want.
INTERNAL_TOOLS: frozenset[str] = frozenset({"plan.redirect", "voice.intent", "state.now"})

#: How many example phrases one capability shows. The descriptions carry up to a dozen;
#: past four the list stops being readable and starts being a transcript.
MAX_PHRASES = 4

#: Longest summary shown. The descriptions are written for a model and can run long; the
#: first sentence is the promise and the rest is instruction to the model.
MAX_SUMMARY_CHARS = 220


@dataclass(frozen=True, slots=True)
class Capability:
    """One tool, as the owner would meet it."""

    name: str
    family: str
    family_tr: str
    summary: str
    phrases: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "family": self.family,
            "family_tr": self.family_tr,
            "summary": self.summary,
            "phrases": list(self.phrases),
        }


def family_of(tool_name: str) -> str:
    return tool_name.split(".", 1)[0]


def summarise(description: str) -> str:
    """The promise, without the instruction to the model.

    A description's first sentence says what the tool does; what follows is usually
    "Dönen 'speech' metnini aynen oku" or a list of synonyms, which is true and is not the
    owner's business. The sentence break is taken on `. ` / `: ` so an abbreviation or a
    time like `07:30` does not cut it in half.
    """
    text = " ".join(description.split())
    # The EARLIEST break, not the first one tried: `alarm.create` reads
    # "Uyandırma alarmı KURAR: 'yarın sabah 07:30'da beni uyandır'. …", and testing ". "
    # before ": " takes the sentence break twenty words later and keeps every example.
    cuts = [text.find(sep) for sep in (". ", ": ")]
    at = min((i for i in cuts if i >= 20), default=-1)
    if at >= 0:
        text = text[:at] + ("." if text[at] == "." else "")
    if len(text) > MAX_SUMMARY_CHARS:
        text = text[: MAX_SUMMARY_CHARS - 1].rstrip() + "…"
    return text


def phrases_in(description: str) -> list[str]:
    """The owner's own sentences, as the description quotes them, in order, deduped."""
    seen: list[str] = []
    for raw in _QUOTED.findall(description):
        phrase = " ".join(raw.split())
        # A quoted FIELD name ('speech', 'alarm_id') is not something anybody says.
        if "_" in phrase or (phrase.islower() and " " not in phrase and len(phrase) < 12):
            continue
        # A fragment that crossed a sentence break, or that is a label rather than an
        # utterance, is not a thing to say either.
        if ". " in phrase or phrase.endswith(":"):
            continue
        if phrase not in seen:
            seen.append(phrase)
        if len(seen) >= MAX_PHRASES:
            break
    return seen


@lru_cache(maxsize=1)
def _default() -> tuple[Capability, ...]:
    """The default registry's capabilities, built once per process.

    The registry is static once the module graph is imported, and building it registers a
    hundred and thirty tools. A route and a voice tool both ask for this list, so it is
    cached — and only for the DEFAULT registry: a caller that passes its own gets a fresh
    derivation, which is what a test needs.
    """
    # Imported here, not at module scope: `tools` imports `tools_assistant`, which asks
    # this module for the list. A module-level import would close that circle at import
    # time; deferring it to the first CALL means the registry is finished before it is read.
    from app.voice.realtime_sessions.tools import default_registry

    return tuple(_derive(default_registry()))


def capabilities(registry: ToolRegistry | None = None) -> list[Capability]:
    """Every tool the assistant has, in the owner's words, derived from the registry."""
    if registry is None:
        return list(_default())
    return _derive(registry)


def _derive(reg: ToolRegistry) -> list[Capability]:
    out: list[Capability] = []
    for entry in reg.manifest():
        name = str(entry["name"])
        if name in INTERNAL_TOOLS:
            continue
        family = family_of(name)
        description = str(entry.get("description") or "")
        out.append(
            Capability(
                name=name,
                family=family,
                family_tr=FAMILY_TR.get(family, family),
                summary=summarise(description),
                phrases=phrases_in(description),
            )
        )
    return out


def families(items: list[Capability]) -> list[dict[str, Any]]:
    """The families, in the owner's alphabet, with how many things each holds."""
    counts: dict[str, int] = {}
    for item in items:
        counts[item.family] = counts.get(item.family, 0) + 1
    return sorted(
        (
            {"family": family, "family_tr": FAMILY_TR.get(family, family), "count": count}
            for family, count in counts.items()
        ),
        key=lambda row: row["family_tr"],
    )


def speech(items: list[Capability]) -> str:
    """What the assistant SAYS when asked, which is not the list.

    Reading a hundred and thirty tool names aloud is not an answer; it is a denial of
    service. The spoken form names how many things and which areas, and points at the
    surface that can show them all — the same rule the constitution applies to a finished
    task: notify briefly and wait.
    """
    groups = families(items)
    names = [row["family_tr"] for row in groups]
    if not names:
        return "Efendim, şu anda kayıtlı bir yeteneğim yok."
    head = ", ".join(names[:5])
    return (
        f"Efendim, {len(items)} şey yapabiliyorum; {len(groups)} alanda. "
        f"Başlıcaları: {head}. Tam listeyi Ses sayfasında görebilirsiniz."
    )


__all__ = [
    "Capability",
    "FAMILY_TR",
    "INTERNAL_TOOLS",
    "MAX_PHRASES",
    "capabilities",
    "families",
    "family_of",
    "phrases_in",
    "speech",
    "summarise",
]
