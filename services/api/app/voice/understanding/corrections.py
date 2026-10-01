"""Corrections become vocabulary (ADR-0224): the owner's correction is written to memory as a
synonym and the next match uses it - the second time is right without a release.

* A correction turn follows a MEDIUM read-back or a LOW question (``correctable`` is what the
  relay keeps of that turn: the intent, the slots and the ONE word the device slot was read
  from - never the sentence): "hayır, ofis bilgisayarında", "onu değil, Not Defteri". The pair
  form "ona X deme, Y de" states both sides and needs no turn before it. The rule tables read
  the sentence FIRST; only a sentence they left unrouted is looked at here.
* The pair (heard -> meant) goes through the memory write policy as an explicit ``vocabulary``
  observation ("ofüs = ofis (cihaz)", source the session). The class has no candidate stage.
* ``vocabulary`` reads the rows back behind a version check (ids and versions, one small
  query a sentence); the rows are parsed again only when they changed, and the layer-2
  ``EntityIndex`` is rebuilt only then too (``SemanticEngine`` caches it by content).
* ``read_turn`` is ``policy.read_turn`` with the vocabulary in it: the exact word the owner
  taught binds the device as the owner's own word (1.0); a NEAR form of it is layer 2's, read
  back. The rule table for applications asks ``app_for`` (``intents._app_open_match``).
* Every new synonym is PROPOSED as an STT-confusion candidate, one file under
  ``team/proposals/`` per synonym. ``stt-confusions.json`` is a release artefact: this module
  never writes it.
* Forgetting the memory row ("ofüs'ü unut" -> ``memory.forget``) is all it takes to remove a
  synonym: the next version check no longer finds it.

What is never learned: a word the system already reads as something else ("ofis" must not
come to mean the home PC because the owner changed their mind), a pointing word ("diğer",
"yandaki"), and - when the heard word had to be GUESSED from its place in the sentence - a
word that does not resemble the alias the owner answered with ("hemen bilgisayarımda ... aç"
answered "ev" must not teach "hemen = ev"). The pair form states both sides, so nothing is
guessed there. A wrong synonym would be a wrong-device launch at HIGH, the very thing the
ADR forbids.
"""

from __future__ import annotations

import hashlib
import os
import re
import uuid
from collections.abc import Iterable, Sequence
from contextvars import ContextVar, Token
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.devices import aliases as device_aliases
from app.logging import get_logger
from app.memory import service as memory_service
from app.memory.embedding import Embedder
from app.memory.errors import MemoryErrorClass, MemorySubsystemError
from app.memory.models import Memory
from app.memory.policy import VOCABULARY, Observation
from app.memory.types import MemoryStatus
from app.operator import allowlists
from app.voice.intents import normalize_transcript
from app.voice.understanding import fuzzy, policy
from app.voice.understanding.combine import RuleResult
from app.voice.understanding.normalize import NOUN, normalize
from app.voice.understanding.policy import Decision, DeviceBinding, Thresholds
from app.voice.understanding.semantic import SemanticEngine

logger = get_logger("app.voice.understanding.corrections")

KIND_DEVICE: Final = "device"
KIND_APP: Final = "app"
_KIND_TR: Final[dict[str, str]] = {KIND_DEVICE: "cihaz", KIND_APP: "uygulama"}
#: ``Decision.layer`` when a word the owner taught bound the device.
LAYER_VOCABULARY: Final = "vocabulary"
#: The session-context key the relay keeps ``correctable`` under.
CORRECTABLE_KEY: Final = "understanding_correctable"
#: The intents whose application slot a correction may name.
APP_SLOT_INTENTS: Final[frozenset[str]] = frozenset({"app_open"})
#: Where the proposals go when the caller names no directory.
PROPOSALS_DIR_ENV: Final = "PAGENTOS_TEAM_PROPOSALS_DIR"
CONFUSIONS_PATH: Final = "services/api/app/voice/understanding/stt-confusions.json"

REASON_NOTHING_HEARD: Final = "nothing_heard"
REASON_NOT_A_NAME: Final = "not_a_name"
REASON_KNOWN_WORD: Final = "known_word"
REASON_ALREADY_KNOWN: Final = "already_known"
REASON_NOT_SIMILAR: Final = "not_similar"
#: A heard word taken by POSITION is the mishearing only when it is at least this near the
#: alias the owner answered with ("ofüs" ~ "ofis" 0.75, "evü" ~ "ev" 0.67; "hemen" ~ "ev" 0).
GUESSED_WORD_MIN: Final = 0.6

_MAX_HEARD_WORDS: Final = 3
_MIN_HEARD_CHARS: Final = 3
#: Words that point at a machine without naming it; so does any "...ki" ("yandaki").
_NOT_A_NAME: Final[frozenset[str]] = frozenset(
    fuzzy.fold(word)
    for word in (
        "diğer öbür öteki başka bu şu o onun bunun şunun benim senin bizim sizin kendi bir "
        "her hangi aynı yeni eski ve ile de da ya hayır yok değil evet tamam"
    ).split()
)
#: What a correction sentence opens with: the refusal and the thing refused.
_NO_WORDS: Final[frozenset[str]] = frozenset({"hayır", "hayir", "yok", "yo", "değil", "degil"})
_THAT_WORDS: Final[frozenset[str]] = frozenset({"onu", "o", "bunu", "bu", "şunu", "sunu", "öyle"})
_PAIR: Final = re.compile(
    r"^(?:(?:ona|buna|şuna|suna|onu|bunu)\s+)?(?P<said>.+?)\s+deme(?:yin|yiniz)?\s+"
    r"(?P<meant>.+?)\s+de(?:yin|yiniz)?$"
)
#: The case endings a taught word may carry in a sentence ("hesaplayıcıyı aç", "ofüste aç").
#: Closed, like every suffix this router reads (ADR-0205): accusative, possessive+accusative,
#: locative (and -ki), ablative. No dative and no open tail: "not" is not "nota", "notebook".
_CASE_ENDINGS: Final[frozenset[str]] = frozenset(
    fuzzy.fold(ending)
    for ending in (
        "ı i u ü yı yi yu yü nı ni nu nü ını ini unu ünü sını sini sunu sünü "
        "da de ta te nda nde daki deki taki teki ndaki ndeki dan den tan ten ndan nden"
    ).split()
)
_COMPUTER_STEM: Final = "bilgisayar"
_OFFICE_WORD: Final = re.compile(r"^ofis\w+$")


# ------------------------------------------------------------------ the vocabulary


@dataclass(frozen=True, slots=True)
class Synonym:
    """One row of the owner's vocabulary: ``heard`` means the entity ``meant`` (a canonical
    device alias word or an allow-listed application id)."""

    kind: str
    heard: str
    meant: str
    memory_id: uuid.UUID | None = None

    @property
    def label(self) -> str:
        return synonym_text(self.kind, self.heard, self.meant)


def synonym_text(kind: str, heard: str, meant: str) -> str:
    """The memory's text, as the owner would read it: "ofüs = ofis (cihaz)"."""
    name = allowlists.APP_NAMES_TR.get(meant, meant) if kind == KIND_APP else meant
    return f"{heard} = {name} ({_KIND_TR.get(kind, kind)})"


_active: ContextVar[tuple[Synonym, ...]] = ContextVar(
    "pagentos_understanding_vocabulary", default=()
)
#: (the rows' ids and versions, what they parsed to) - the last vocabulary this process read.
_cache: tuple[tuple[tuple[str, int], ...], tuple[Synonym, ...]] | None = None
_loads = 0


def active() -> tuple[Synonym, ...]:
    """The vocabulary of the turn being read (``vocabulary`` sets it); () outside one."""
    return _active.get()


def activate(synonyms: Iterable[Synonym]) -> Token[tuple[Synonym, ...]]:
    return _active.set(tuple(synonyms))


def deactivate(token: Token[tuple[Synonym, ...]]) -> None:
    _active.reset(token)


def reset() -> None:
    """Forget what this process read (tests; a process restart does the same)."""
    global _cache
    _cache = None
    _active.set(())


def loads() -> int:
    """How many times the rows were read and parsed - not how many times they were checked."""
    return _loads


def _synonym_of(row: Memory) -> Synonym | None:
    value = row.value_json if isinstance(row.value_json, dict) else {}
    kind, heard, meant = value.get("kind"), value.get("heard"), value.get("meant")
    if not isinstance(heard, str) or not isinstance(meant, str) or not heard.split():
        return None
    if kind == KIND_DEVICE and meant in device_aliases.CANONICAL_ALIASES:
        return Synonym(KIND_DEVICE, " ".join(heard.split()), meant, row.id)
    if kind == KIND_APP and meant in allowlists.APP_IDS:
        return Synonym(KIND_APP, " ".join(heard.split()), meant, row.id)
    return None  # a row that names no entity the system has binds nothing


def vocabulary(session: Session | None) -> tuple[Synonym, ...]:
    """The owner's synonyms as the memory rows hold them NOW, and the vocabulary of this turn.

    The check is one query for ids and versions; the rows are read and parsed only when
    that changed (a correction, a forget, an edit - by this process or another). A store
    that cannot be read is an empty vocabulary, never a dead turn."""
    global _cache, _loads
    if session is None:
        _active.set(())
        return ()
    wanted = (
        Memory.memory_class == VOCABULARY.value,
        Memory.status == MemoryStatus.ACTIVE.value,
    )
    try:
        # A savepoint: a failed read (no memory tables in this deployment) must not leave
        # the caller's transaction aborted.
        with session.begin_nested():
            stamp = tuple(
                (str(memory_id), int(version))
                for memory_id, version in session.execute(
                    select(Memory.id, Memory.version).where(*wanted).order_by(Memory.id)
                )
            )
            if _cache is None or _cache[0] != stamp:
                rows = session.execute(
                    select(Memory).where(*wanted).order_by(Memory.created_at, Memory.id)
                ).scalars()
                parsed = tuple(s for s in (_synonym_of(row) for row in rows) if s is not None)
                _cache = (stamp, parsed)
                _loads += 1
    except Exception:  # noqa: BLE001 - see the docstring
        logger.warning("understanding_vocabulary_unreadable", exc_info=True)
        _active.set(())
        return ()
    _active.set(_cache[1])
    return _cache[1]


def entity_rows(synonyms: Iterable[Synonym]) -> tuple[tuple[str, str, str], ...]:
    """The synonyms as ``EntityIndex`` entries: (kind, value, surface)."""
    return tuple((s.kind, s.meant, s.heard) for s in synonyms)


# ------------------------------------------------------------------ matching a taught word


def _bare(token: str) -> str:
    return fuzzy.fold(token.split("'", 1)[0])


def _same_word(token: str, word: str) -> bool:
    """``token`` is ``word``, bare or with one closed case ending - never a prefix match."""
    said, taught = _bare(token), fuzzy.fold(word)
    if said == taught:
        return True
    if "'" in token:
        return False  # "ofüs'ü": the apostrophe already cut the ending
    return said.startswith(taught) and said[len(taught) :] in _CASE_ENDINGS


def _span(tokens: Sequence[str], heard: str) -> tuple[int, int] | None:
    words = heard.split()
    for start in range(len(tokens) - len(words) + 1):
        window = tokens[start : start + len(words)]
        if all(_bare(t) == fuzzy.fold(w) for t, w in zip(window[:-1], words[:-1], strict=True)):
            if _same_word(window[-1], words[-1]):
                return start, start + len(words)
    return None


def _find(synonyms: Iterable[Synonym], tokens: Sequence[str], kind: str | None) -> Synonym | None:
    """The synonym (the longest heard form first) whose word(s) the sentence says."""
    wanted = [s for s in synonyms if kind is None or s.kind == kind]
    for synonym in sorted(wanted, key=lambda s: -len(s.heard)):
        if _span(tokens, synonym.heard) is not None:
            return synonym
    return None


def app_for(tokens: Sequence[str]) -> str | None:
    """The application a word the owner taught names in this sentence, or None. Asked by the
    rule table (``intents._app_open_match``) after the allow-list's own names found none."""
    synonyms = active()
    if not synonyms:
        return None
    found = _find(synonyms, tokens, KIND_APP)
    return found.meant if found is not None else None


def named_synonym(synonyms: Iterable[Synonym], text: str) -> Synonym | None:
    """The synonym a sentence names by its heard word ("ofüs'ü unut"), or None."""
    return _find(synonyms, normalize_transcript(text)[1], None)


# ------------------------------------------------------------------ reading a turn


def read_turn(
    text: str,
    *,
    rule: RuleResult | None,
    vocabulary: Sequence[Synonym] | None = None,
    bound_devices: Sequence[str] = (),
    answered_device: str | None = None,
    names_machine: bool = False,
    aliases: Sequence[str] = (),
    engine: SemanticEngine | None = None,
    thresholds: Thresholds | None = None,
) -> Decision:
    """``policy.read_turn`` with the owner's vocabulary in it (the arguments are its own).

    A device word the owner taught, said as taught, is the owner's own closed form: it binds
    at 1.0 - before the confusion list, which is only a release artefact's opinion - unless
    the sentence already names a machine in a form the rule parser reads. Any other form
    goes to the layers with the vocabulary in the entity index. The evidence names the
    synonym that was used."""
    synonyms = active() if vocabulary is None else tuple(vocabulary)
    tokens = normalize_transcript(text)[1]
    taught: Synonym | None = None
    if (
        rule is not None
        and rule.intent in policy.DEVICE_SLOT_INTENTS
        and not bound_devices
        and not answered_device
    ):
        taught = _find(synonyms, tokens, KIND_DEVICE)
    decision = policy.read_turn(
        text,
        rule=rule,
        bound_devices=(taught.meant,) if taught is not None else bound_devices,
        answered_device=answered_device,
        names_machine=False if taught is not None else names_machine,
        aliases=aliases,
        engine=engine,
        vocabulary=entity_rows(synonyms),
        thresholds=thresholds,
    )
    used = [taught] if taught is not None else []
    application = rule.entities.get("app") if rule is not None else None
    if application:
        named = _find(synonyms, tokens, KIND_APP)
        if named is not None and named.meant == application:
            used.append(named)
    if not used or decision.candidate is None:
        return decision
    candidate = replace(
        decision.candidate,
        evidence=(*decision.candidate.evidence, *(f"vocabulary: {s.label}" for s in used)),
    )
    device, layer = decision.device, decision.layer
    if taught is not None and device is not None and device.alias == taught.meant:
        device = DeviceBinding(device.alias, device.confidence, LAYER_VOCABULARY)
        layer = LAYER_VOCABULARY
    return replace(
        decision,
        candidate=candidate,
        ranked=tuple(candidate if c is decision.candidate else c for c in decision.ranked),
        device=device,
        layer=layer,
    )


# ------------------------------------------------------------------ the correction turn


@dataclass(frozen=True, slots=True)
class Correction:
    """What a correction sentence said. ``heard -> meant`` is the pair to learn (when
    ``learnable``); ``intent``/``application``/``device`` re-issue the corrected turn (when
    ``intent`` is set - the pair form corrects a word, not a turn)."""

    kind: str
    heard: str | None
    meant: str
    intent: str | None = None
    application: str | None = None
    device: str | None = None
    #: Why the pair is not written, or None.
    reason: str | None = None

    @property
    def learnable(self) -> bool:
        return self.reason is None and bool(self.heard) and bool(self.meant)


def heard_device_word(text: str) -> str | None:
    """The ONE word a sentence put where the machine's name goes: the word before the
    computer word ("ofüs bilgisayarında"), or an "ofis..." place word no closed form reads."""
    tokens = normalize_transcript(text)[1]
    for index, token in enumerate(tokens):
        if token.startswith(_COMPUTER_STEM) and index > 0:
            return tokens[index - 1].split("'", 1)[0] or None
    for token in tokens:
        if _OFFICE_WORD.match(token):
            return token.split("'", 1)[0]
    return None


def correctable(
    text: str,
    *,
    intent: str,
    application: str | None,
    decision: Decision,
    now: datetime,
) -> dict[str, Any] | None:
    """What the relay keeps (under ``CORRECTABLE_KEY``) of a turn the owner may correct: a
    MEDIUM read-back, or a LOW question asking for the machine. None for any other turn -
    a HIGH turn is the owner's own closed form and a later "hayır" is a new sentence."""
    asks_device = decision.band == policy.BAND_LOW and decision.missing == policy.SLOT_DEVICE
    if not asks_device and not decision.read_back:
        return None
    return {
        "intent": intent,
        "application": application,
        "band": decision.band,
        "device": decision.device.alias if decision.device is not None else None,
        "heard_device": heard_device_word(text),
        "at": now.isoformat().replace("+00:00", "Z"),
    }


def _fresh(kept: Any, now: datetime) -> bool:
    if not isinstance(kept, dict) or not isinstance(kept.get("intent"), str):
        return False
    try:
        at = datetime.fromisoformat(str(kept.get("at")).replace("Z", "+00:00"))
    except ValueError:
        return False
    if at.tzinfo is None:
        at = at.replace(tzinfo=now.tzinfo)
    return 0.0 <= (now - at).total_seconds() <= policy.ANSWER_WINDOW_S


def _whole_app(words: Sequence[str], synonyms: Iterable[Synonym]) -> str | None:
    """The application ``words`` name when they name ONLY it ("not defteri", "hesaplayıcı")."""
    if not words:
        return None
    phrase = " ".join(_bare(w) for w in words)
    for alias, app_id in allowlists.APP_ALIAS_PHRASES:
        if fuzzy.fold(alias) == phrase:
            return app_id
    for app_id, name in allowlists.APP_NAMES_TR.items():
        if fuzzy.fold(name) == phrase:
            return app_id
    for synonym in synonyms:
        if synonym.kind == KIND_APP and _span(words, synonym.heard) == (0, len(words)):
            return synonym.meant
    return None


def _entity(words: Sequence[str], synonyms: Iterable[Synonym]) -> tuple[str, str] | None:
    """(kind, value) when ``words`` are exactly an application or a device alias phrase."""
    app = _whole_app(words, synonyms)
    if app is not None:
        return KIND_APP, app
    alias = policy.answered_alias(" ".join(words))
    return (KIND_DEVICE, alias) if alias else None


def _why_not(heard: str | None, kind: str, aliases: Iterable[str]) -> str | None:
    """Why ``heard`` may not become a synonym, or None when it may."""
    if not heard or not heard.split():
        return REASON_NOTHING_HEARD
    words = heard.split()
    folded = [fuzzy.fold(w) for w in words]
    if len(words) > _MAX_HEARD_WORDS or len("".join(folded)) < _MIN_HEARD_CHARS:
        return REASON_NOT_A_NAME
    if any(w in _NOT_A_NAME for w in folded):
        return REASON_NOT_A_NAME
    if kind == KIND_DEVICE and folded[-1].endswith("ki"):
        return REASON_NOT_A_NAME
    # A word the system already reads as something: an alias (bare, suffixed, or one the
    # owner configured on a device) or an application name. The confusion list is left out
    # on purpose - the owner's correction outranks it.
    if _entity(words, ()) is not None:
        return REASON_KNOWN_WORD
    if " ".join(folded) in {fuzzy.fold(str(a)) for a in aliases}:
        return REASON_KNOWN_WORD
    for lemma in normalize(heard, confusions={}).lemmas:
        if lemma.kind == NOUN and lemma.stem in device_aliases.CANONICAL_ALIASES:
            return REASON_KNOWN_WORD
    from app.operator.plans import resolve_app_alias

    if resolve_app_alias(tuple(words)) is not None:
        return REASON_KNOWN_WORD
    return None


def _unlike(heard: str | None, alias: str) -> str | None:
    """``REASON_NOT_SIMILAR`` when a heard word taken by position is not a mishearing of
    ``alias``, else None."""
    if not heard:
        return None
    near = fuzzy.similarity(fuzzy.fold(heard), fuzzy.fold(alias))
    return None if near >= GUESSED_WORD_MIN else REASON_NOT_SIMILAR


def _pair_correction(
    said: Sequence[str], meant: Sequence[str], synonyms: Sequence[Synonym], aliases: Iterable[str]
) -> Correction | None:
    """ "Ona X deme, Y de": the side the system knows is the entity, the other the new word."""
    known_said, known_meant = _entity(said, synonyms), _entity(meant, synonyms)
    if known_said is None and known_meant is None:
        return None  # two words that name nothing here: not a correction this module reads
    if known_said is not None and known_meant is not None:
        same = known_said == known_meant
        return Correction(
            kind=known_meant[0],
            heard=" ".join(said),
            meant=known_meant[1],
            reason=REASON_ALREADY_KNOWN if same else REASON_KNOWN_WORD,
        )
    known, new = (known_meant, said) if known_meant is not None else (known_said, meant)
    assert known is not None
    kind, value = known
    heard = " ".join(w.split("'", 1)[0] for w in new)
    return Correction(kind=kind, heard=heard, meant=value, reason=_why_not(heard, kind, aliases))


def correction_turn(
    kept: Any,
    text: str,
    *,
    now: datetime,
    vocabulary: Sequence[Synonym] | None = None,
    aliases: Iterable[str] = (),
) -> Correction | None:
    """The correction ``text`` makes, or None. Call it only for a sentence the rule tables
    left unrouted (rule first). ``kept`` is ``correctable`` of the turn before, or None.

    After a LOW question the bare answer ("ofis bilgisayarında") corrects the word that was
    heard; after a MEDIUM read-back a correction says no first ("hayır, ev bilgisayarında").
    """
    synonyms = active() if vocabulary is None else tuple(vocabulary)
    tokens = normalize_transcript(text)[1]
    if not tokens:
        return None
    pair = _PAIR.match(" ".join(tokens))
    if pair is not None:
        return _pair_correction(
            pair.group("said").split(), pair.group("meant").split(), synonyms, aliases
        )
    if not _fresh(kept, now):
        return None
    refused = False
    rest = list(tokens)
    while rest and (rest[0] in _NO_WORDS or rest[0] in _THAT_WORDS):
        refused = refused or rest[0] in _NO_WORDS
        rest.pop(0)
    if not rest:
        return None
    intent = str(kept["intent"])
    after_question = kept.get("band") == policy.BAND_LOW
    if not refused and not after_question:
        return None
    alias = policy.answered_alias(" ".join(rest))
    if alias is not None:
        if intent not in policy.DEVICE_SLOT_INTENTS or alias == kept.get("device"):
            return None
        heard = kept.get("heard_device")
        heard = heard if isinstance(heard, str) else None
        return Correction(
            kind=KIND_DEVICE,
            heard=heard,
            meant=alias,
            intent=intent,
            application=kept.get("application"),
            device=alias,
            reason=_why_not(heard, KIND_DEVICE, aliases) or _unlike(heard, alias),
        )
    app = _whole_app(rest, synonyms)
    if app is None or not refused or intent not in APP_SLOT_INTENTS:
        return None
    if app == kept.get("application"):
        return None
    # The application of the turn before was read from words the allow-list knows, and
    # which of them is not kept: there is a turn to re-issue and no new word to learn.
    return Correction(
        kind=KIND_APP,
        heard=None,
        meant=app,
        intent=intent,
        application=app,
        device=kept.get("device"),
        reason=REASON_NOTHING_HEARD,
    )


# ------------------------------------------------------------------ learning


@dataclass(frozen=True, slots=True)
class Learned:
    written: bool
    #: Why nothing was written, when nothing was.
    reason: str | None = None
    #: The memory service's own word: created, corroborated, superseded_previous.
    action: str | None = None
    memory_id: uuid.UUID | None = None
    proposal: Path | None = None
    proposal_written: bool = False
    proposal_reason: str | None = None


_DEFAULT_DIR: Final[Any] = object()


def default_proposals_dir() -> Path | None:
    """``PAGENTOS_TEAM_PROPOSALS_DIR``, else the checkout's ``team/proposals`` when this
    module runs from one, else None (an image has no checkout: the caller is told)."""
    configured = os.environ.get(PROPOSALS_DIR_ENV, "").strip()
    if configured:
        return Path(configured)
    parents = Path(__file__).resolve().parents
    if len(parents) > 5 and (parents[5] / "team" / "proposals").is_dir():
        return parents[5] / "team" / "proposals"
    return None


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", fuzzy.fold(text)).strip("-") or "x"


def _propose(
    directory: Path | None, synonym: Synonym, now: datetime
) -> tuple[Path | None, bool, str | None]:
    """One proposal file per synonym, created exclusively: a second correction of the same
    word, a second worker or a second session finds the file and writes nothing."""
    if directory is None:
        return None, False, "no_proposals_dir"
    digest = hashlib.sha256(f"{synonym.kind}|{synonym.heard}|{synonym.meant}".encode()).hexdigest()
    path = directory / (
        f"stt-karisiklik-{synonym.kind}-{_slug(synonym.heard)}-{_slug(synonym.meant)}"
        f"-{digest[:8]}.md"
    )
    single = len(synonym.heard.split()) == 1 and synonym.kind == KIND_DEVICE
    similarity = fuzzy.similarity(fuzzy.fold(synonym.heard), fuzzy.fold(synonym.meant))
    day = now.date().isoformat()
    body = (
        f'# Öneri: STT karışıklık adayı - "{synonym.heard}" -> "{synonym.meant}"'
        f" ({_KIND_TR[synonym.kind]})\n\n"
        f"Tarih: {day} · Kaynak: sahibin sesli düzeltmesi (ADR-0224) · "
        "Durum: gece döngüsü adayı\n\n"
        "## Ne\n"
        f'Sahip bir okumayı düzeltti: duyulan "{synonym.heard}", kastedilen "{synonym.meant}". '
        "Çift, `vocabulary` sınıfı hafıza kaydı olarak yazıldı ve eşleşmede hemen kullanılıyor; "
        "sahip kaydı unutursa eşanlamlı da gider.\n\n"
        "## Gece döngüsü için\n"
        f"- Aday giriş (`{CONFUSIONS_PATH}`): "
        f'`{{"heard": "{synonym.heard}", "meant": "{synonym.meant}", "seen_at": "{day}"}}`\n'
        f"- Bulanık benzerlik: {similarity:.2f}. Düşükse bu bir STT karışıklığı değil, sahibin "
        "kendi adlandırmasıdır: hafızada kalır, dosyaya girmez.\n"
        f"- Tek kelimelik cihaz/kelime çifti: {'evet' if single else 'hayır'} "
        "(dosya yalnız tek kelimelik biçimleri tutar).\n"
        "- `stt-confusions.json` bir sürüm çıktısıdır: relay yazmaz; girişi gece döngüsü, "
        "kanıtıyla önerir. Cümle bu dosyaya yazılmadı (yalnız iki kelime).\n"
    )
    try:
        with path.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(body)
    except FileExistsError:
        return path, False, "already_proposed"
    except OSError as exc:
        logger.warning("understanding_proposal_not_written", error=type(exc).__name__)
        return None, False, "proposal_write_failed"
    return path, True, None


def learn(
    session: Session,
    embedder: Embedder,
    correction: Correction,
    *,
    session_id: Any,
    proposals_dir: Path | None | Any = _DEFAULT_DIR,
    now: datetime | None = None,
) -> Learned:
    """Write the correction's pair through the memory write policy (explicit, class
    ``vocabulary``, source the session) and propose it as an STT-confusion candidate.

    Never raises: a correction that cannot be kept must not cost the owner the turn. The
    memory service commits on the session it is given (as every write through it does)."""
    if not correction.learnable or correction.heard is None:
        return Learned(False, reason=correction.reason or REASON_NOTHING_HEARD)
    heard = " ".join(correction.heard.split())
    synonym = Synonym(correction.kind, heard, correction.meant)
    observation = Observation(
        text=synonym.label,
        memory_class=VOCABULARY,
        key=f"{correction.kind}:{fuzzy.fold(heard)}",
        value={"kind": correction.kind, "heard": heard, "meant": correction.meant},
        explicit=True,
        source={"kind": "voice_correction", "session_id": str(session_id)},
    )
    try:
        result = memory_service.record_observation(session, embedder, observation)
    except MemorySubsystemError as exc:
        refused = exc.error_class == MemoryErrorClass.SECRET_REJECTED
        return Learned(False, reason="secret_rejected" if refused else "write_refused")
    except Exception as exc:  # noqa: BLE001 - see the docstring
        session.rollback()
        logger.warning("understanding_correction_not_written", error=type(exc).__name__)
        return Learned(False, reason="write_failed")
    if result.memory_id is None:
        return Learned(False, reason=result.action)
    directory = default_proposals_dir() if proposals_dir is _DEFAULT_DIR else proposals_dir
    path, written, why = _propose(directory, synonym, now or datetime.now(UTC))
    return Learned(
        True,
        action=result.action,
        memory_id=result.memory_id,
        proposal=path,
        proposal_written=written,
        proposal_reason=why,
    )


__all__ = [
    "APP_SLOT_INTENTS",
    "CORRECTABLE_KEY",
    "KIND_APP",
    "KIND_DEVICE",
    "LAYER_VOCABULARY",
    "PROPOSALS_DIR_ENV",
    "Correction",
    "Learned",
    "Synonym",
    "activate",
    "active",
    "app_for",
    "correctable",
    "correction_turn",
    "deactivate",
    "default_proposals_dir",
    "entity_rows",
    "heard_device_word",
    "learn",
    "loads",
    "named_synonym",
    "read_turn",
    "reset",
    "synonym_text",
    "vocabulary",
]
