"""The negative-imperative guard: "do not delete" never reaches the tool that deletes.

Turkish makes the negative imperative with one syllable: ``sil`` -> ``silme``, ``unut`` ->
``unutma``, ``kaldır`` -> ``kaldırma``, ``iptal et`` -> ``iptal etme``. The router's ``_has``
is a PREFIX match, so a rule written with ``_has(tokens, "sil")`` reads "silme" (don't
delete) as "sil" (delete). The same defect came back three times (B16 memory 2026-09-13,
B27 research cancel 2026-09-14, watch-voice 2026-10-04: "Nöbetleri silme." ->
``watch_forget_all``), each time fixed one rule at a time.

This guard closes the class instead of the instance:

* :data:`DESTRUCTIVE_TOOLS` is a CLOSED list: every intent that deletes, forgets, removes or
  cancels, with the corpus sentences (``tests/voice_corpus``, read, never written) that
  reach it today.
* :func:`negate` is a pure generator: the sentence's last imperative takes the negative
  ending by vowel harmony (``-ma/-me``, ``-mayın/-meyin``, ``-mayınız/-meyiniz``), the
  auxiliaries ``et``/``yap`` included ("iptal etme"). Imperatives only; the question and
  wish moods ("silmesem mi?") are out of scope (ADR).
* Every generated sentence goes through :func:`app.voice.intents.resolve_intent` - the one
  router the voice uses - under the state that made the positive sentence reach the tool,
  and must reach NO destructive intent (an answer or ``none`` is fine).
* The ablative ("Nöbetlerimden birini sil.") never reaches a ``*_forget_all``.
* The list watcher: a new intent whose name or tool says forget/remove/delete/cancel/clear/
  uninstall/discard and that is in neither :data:`DESTRUCTIVE_TOOLS` nor
  :data:`NOT_DESTRUCTIVE` is red.
* What is red on main today is in :data:`KNOWN_OPEN` (``xfail(strict=True,
  raises=AssertionError)``): fixing the router makes it XPASS, which is red, and the entry
  is then removed.

``destructive_negation_cases.md`` beside this file is the generated table, held equal to
the generator's output so a wrong Turkish form is seen line by line. Regenerate it with
``uv run python -m tests.unit.test_destructive_negation_guard`` (from ``services/api``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any, Final

import pytest

from app.voice import intents
from app.voice.intents import Intent, resolve_intent

CASES_MD: Final = Path(__file__).with_name("destructive_negation_cases.md")


@dataclass(frozen=True, slots=True)
class Destructive:
    """One destructive intent: its corpus positives and the router state they need."""

    #: (corpus case id, the utterance as the corpus has it). The text is held here too so
    #: the generated table does not depend on which intents this branch has.
    positives: tuple[tuple[str, str], ...]
    state: tuple[tuple[str, Any], ...] = ()
    #: Why no positive is listed (no corpus sentence ends in an imperative to negate).
    no_positive_reason: str = ""


_OPERATOR: Final = (("operator_running", True),)
_EXEC: Final = (("executive_run_state", "running"),)

#: The closed list (intent value -> corpus positives). An intent absent on this branch
#: (``watch_*`` before watch-voice merges) is listed so the guard is ready, and skipped.
DESTRUCTIVE_TOOLS: Final[dict[str, Destructive]] = {
    "memory_forget": Destructive(
        (("m.forget.1", "Bunu unut."), ("b51.memory_forget.1", "Bunu hafızandan sil."))
    ),
    "research_cancel": Destructive(
        (
            ("d.research_cancel.1", "Araştırmayı iptal et."),
            ("d.research_cancel.2", "Araştırmayı durdur."),
            ("d.research_cancel.3", "Araştırmayı bırak."),
            ("d.research_cancel.4", "Araştırmadan vazgeç."),
        )
    ),
    "routine_cancel": Destructive((("r.cancel.1", "Sabah rutinini iptal et."),)),
    "alarm_cancel": Destructive(
        (
            ("r.collision.alarm_cancel", "Sabah alarmımı iptal et."),
            ("a.cancel.1", "Alarmı iptal et."),
            ("a.cancel.2", "Sabah alarmını iptal et."),
            ("a.cancel.3", "Alarmı kaldır."),
        )
    ),
    "calendar_cancel": Destructive(
        (
            ("d.cancel_event.2", "Perşembeki toplantıyı iptal et."),
            ("d.cancel_event.3", "Yarınki randevuyu iptal et."),
            ("d.cancel_event.4", "Toplantıyı takvimden sil."),
        )
    ),
    "evolution_cancel": Destructive(
        (("ev.cancel.1", "Bu geliştirmeyi iptal et."), ("ev.cancel.2", "Geliştirmeden vazgeç."))
    ),
    "macro_record_cancel": Destructive((("macro.cancel.1", "Hareketi iptal et."),)),
    "macro_delete": Destructive(
        (("macro.delete.1", "Yeni mail sekmesi hareketini sil."),),
        state=(("macro_names", ("yeni mail sekmesi",)),),
    ),
    "operator_cancel": Destructive(
        (("op.cancel.1", "İptal et."), ("op.cancel.2", "Dur.")), state=_OPERATOR
    ),
    "document_delete": Destructive(
        (("doc.delete.first_word", "Bu dosyayı sil."),), state=(("document_focused", True),)
    ),
    "artifact_delete": Destructive(
        (("art.delete.asks_first", "Bunu sil."),), state=(("artifact_focused", True),)
    ),
    "capability_cancel": Destructive(
        (),
        state=(("genesis_awaiting_approval", True),),
        no_positive_reason=(
            "korpusun cümleleri ('Vazgeç, yapma.', 'Vazgeçtim.') emirle bitmiyor: "
            "son kelime zaten olumsuz ya da geçmiş zaman"
        ),
    ),
    "exec_cancel": Destructive(
        (
            ("exec.cancel.1", "Bunu iptal et."),
            ("exec.cancel.2", "Vazgeç."),
            ("b51.exec_cancel.1", "Bu işi iptal et."),
        ),
        state=_EXEC,
    ),
    "watch_remove": Destructive(
        (("w.remove.1", "Fiyat nöbetini kaldır."), ("w.remove.2", "Nöbeti kaldır."))
    ),
    "watch_forget_all": Destructive(
        (("w.forget.1", "Nöbetleri unut."), ("w.forget.2", "Bütün nöbetleri sil."))
    ),
    "native_uninstall": Destructive(
        (
            ("nativeapps.uninstall.canonical", "Kurulumu kaldır."),
            ("nativeapps.uninstall.app", "Uygulamayı kaldır."),
        ),
        state=(("native_build_focused", True),),
    ),
    "discard": Destructive((("mc.discard.mail.2", "Vazgeç."),), state=(("draft_pending", True),)),
    # Not matched by the watcher's pattern; listed by decision (ADR): ending a process loses
    # unsaved work, and a rollback swaps the live release.
    "process_stop": Destructive(
        (
            ("op.process.stop.1", "Chrome'u sonlandır."),
            ("op.process.stop.2", "Not Defteri'ni sonlandır."),
        )
    ),
    "release_rollback": Destructive(
        (
            ("ev.rollback.1", "Önceki sürüme dön."),
            ("ev.rollback.2", "Eski sürüme geri al."),
            ("ev.rollback.3", "Bir önceki sürüme geri dön."),
        )
    ),
}

#: Names the watcher's pattern matches that do NOT delete anything, each with its reason.
NOT_DESTRUCTIVE: Final[dict[str, str]] = {}

#: The sentences real runs got wrong, beside the generated ones (intent, sentence, source).
INCIDENT_CASES: Final[tuple[tuple[str, str, str], ...]] = (
    ("memory_forget", "Bunu unutma.", "B16 2026-09-13"),
    ("memory_forget", "Hafızadan bunu unutma.", "öneri 2026-10-05 gerçek cihaz cümlesi"),
    ("research_cancel", "Araştırmayı iptal etme.", "B27 2026-09-14"),
    ("watch_forget_all", "Nöbetleri silme.", "watch-voice-inspector-2 bulgu 1"),
)

#: The ablative takes ONE of the things, never all of them (intent, sentence, source).
ABLATIVE_CASES: Final[tuple[tuple[str, str, str], ...]] = (
    ("watch_forget_all", "Nöbetlerimden birini sil.", "watch-voice-inspector-2 bulgu 2"),
    ("watch_forget_all", "Nöbetlerden fiyatı kaldır.", "pano 2026-10-05 06:33 denetleyici"),
)


def _open(
    intent: str, sentences: tuple[str, ...], why: str, card: str
) -> dict[tuple[str, str], tuple[str, str]]:
    return {(intent, sentence): (why, card) for sentence in sentences}


_CANCEL_STEMS_WHY: Final = "_CANCEL_VERB_STEMS ('iptal','sil','kaldır') _has ile önek eşleşiyor"

#: Red on main 2026-10-05 (72 cases): (intent, sentence) -> (why, the fixing card's name).
#: Each is an ``xfail(strict=True)``; the ADR carries every fixing card's full text.
KNOWN_OPEN: Final[dict[tuple[str, str], tuple[str, str]]] = {
    **_open(
        "memory_forget",
        ("Bunu hafızandan silme.", "Bunu hafızandan silmeyin.", "Bunu hafızandan silmeyiniz."),
        f"intents.py:2087 {_CANCEL_STEMS_WHY}",
        "negation-fix-cancel-verb-stems",
    ),
    **_open(
        "routine_cancel",
        (
            "Sabah rutinini iptal etme.",
            "Sabah rutinini iptal etmeyin.",
            "Sabah rutinini iptal etmeyiniz.",
        ),
        f"intents.py:1974 {_CANCEL_STEMS_WHY}",
        "negation-fix-cancel-verb-stems",
    ),
    **_open(
        "alarm_cancel",
        (
            "Sabah alarmımı iptal etme.",
            "Sabah alarmımı iptal etmeyin.",
            "Sabah alarmımı iptal etmeyiniz.",
            "Alarmı iptal etme.",
            "Alarmı iptal etmeyin.",
            "Alarmı iptal etmeyiniz.",
            "Sabah alarmını iptal etme.",
            "Sabah alarmını iptal etmeyin.",
            "Sabah alarmını iptal etmeyiniz.",
            "Alarmı kaldırma.",
            "Alarmı kaldırmayın.",
            "Alarmı kaldırmayınız.",
        ),
        f"intents.py:2151 {_CANCEL_STEMS_WHY}",
        "negation-fix-cancel-verb-stems",
    ),
    **_open(
        "research_cancel",
        (
            "Araştırmayı iptal etmeyiniz.",
            "Araştırmadan vazgeçme.",
            "Araştırmadan vazgeçmeyin.",
            "Araştırmadan vazgeçmeyiniz.",
        ),
        "intents.py:4851 olumsuz listede 'etmeyiniz' yok; 4878 _DISCARD_STEMS _has ile önek",
        "negation-fix-research-cancel",
    ),
    **_open(
        "calendar_cancel",
        ("Perşembeki toplantıyı iptal etmeyiniz.", "Yarınki randevuyu iptal etmeyiniz."),
        "intents.py:4714 olumsuz listede 'etmeyiniz' yok",
        "negation-fix-calendar-cancel",
    ),
    **_open(
        "evolution_cancel",
        (
            "Bu geliştirmeyi iptal etme.",
            "Bu geliştirmeyi iptal etmeyin.",
            "Bu geliştirmeyi iptal etmeyiniz.",
            "Geliştirmeden vazgeçme.",
            "Geliştirmeden vazgeçmeyin.",
            "Geliştirmeden vazgeçmeyiniz.",
        ),
        "intents.py:1629 _EVOLUTION_CANCEL_STEMS _has ile önek, olumsuz denetimi yok",
        "negation-fix-evolution-cancel",
    ),
    **_open(
        "macro_record_cancel",
        ("Hareketi iptal etme.", "Hareketi iptal etmeyin.", "Hareketi iptal etmeyiniz."),
        "intents.py:2853 'iptal' tam eşleşiyor, 'etme' denetimi yok",
        "negation-fix-macro",
    ),
    **_open(
        "macro_delete",
        (
            "Yeni mail sekmesi hareketini silme.",
            "Yeni mail sekmesi hareketini silmeyin.",
            "Yeni mail sekmesi hareketini silmeyiniz.",
        ),
        "intents.py:2850 _MACRO_DELETE_VERB_STEMS _has ile önek",
        "negation-fix-macro",
    ),
    **_open(
        "operator_cancel",
        ("İptal etme.", "İptal etmeyin.", "İptal etmeyiniz."),
        "intents.py:2438 _has(tokens, 'iptal'), olumsuz denetimi yok",
        "negation-fix-operator-exec-cancel",
    ),
    **_open(
        "exec_cancel",
        (
            "Bunu iptal etme.",
            "Bunu iptal etmeyin.",
            "Bunu iptal etmeyiniz.",
            "Bu işi iptal etme.",
            "Bu işi iptal etmeyin.",
            "Bu işi iptal etmeyiniz.",
        ),
        "intents.py:7140 'iptal' tam eşleşiyor, olumsuz denetimi yok",
        "negation-fix-operator-exec-cancel",
    ),
    **_open(
        "exec_cancel",
        ("Vazgeçme.", "Vazgeçmeyin.", "Vazgeçmeyiniz."),
        "intents.py:4312 _DISCARD_STEMS _has ile önek: iş sürerken 'Vazgeçme.' -> discard",
        "negation-fix-discard",
    ),
    **_open(
        "discard",
        ("Vazgeçme.", "Vazgeçmeyin.", "Vazgeçmeyiniz."),
        "intents.py:4312 _DISCARD_STEMS ('vazgeç') _has ile önek, olumsuz denetimi yok",
        "negation-fix-discard",
    ),
    **_open(
        "native_uninstall",
        (
            "Kurulumu kaldırma.",
            "Kurulumu kaldırmayın.",
            "Kurulumu kaldırmayınız.",
            "Uygulamayı kaldırma.",
            "Uygulamayı kaldırmayın.",
            "Uygulamayı kaldırmayınız.",
        ),
        "intents.py:6839 _NATIVE_UNINSTALL_VERB_STEMS ('kaldır') _has ile önek",
        "negation-fix-native-uninstall",
    ),
    **_open(
        "process_stop",
        (
            "Chrome'u sonlandırma.",
            "Chrome'u sonlandırmayın.",
            "Chrome'u sonlandırmayınız.",
            "Not Defteri'ni sonlandırma.",
            "Not Defteri'ni sonlandırmayın.",
            "Not Defteri'ni sonlandırmayınız.",
        ),
        "intents.py:3049 _STOP_PROCESS_STEMS ('sonlandır') _has ile önek",
        "negation-fix-process-stop",
    ),
    **_open(
        "release_rollback",
        (
            "Önceki sürüme dönme.",
            "Önceki sürüme dönmeyin.",
            "Önceki sürüme dönmeyiniz.",
            "Eski sürüme geri alma.",
            "Eski sürüme geri almayın.",
            "Eski sürüme geri almayınız.",
            "Bir önceki sürüme geri dönme.",
            "Bir önceki sürüme geri dönmeyin.",
            "Bir önceki sürüme geri dönmeyiniz.",
        ),
        "intents.py:1624 _RETURN_VERB_STEMS ('dön','geri') _has ile önek, olumsuz denetimi yok",
        "negation-fix-release-rollback",
    ),
}

#: The watcher's pattern over intent values and their tool names.
_DESTRUCTIVE_NAME: Final = re.compile(
    r"forget|remove|delete|cancel|clear|unut|sil|kaldir|uninstall|discard"
)

# --- the generator ----------------------------------------------------------------------

#: The imperatives the generator negates (a closed list: anything else is left alone).
IMPERATIVES: Final[frozenset[str]] = frozenset(
    {
        "sil",
        "unut",
        "kaldır",
        "kaldir",
        "temizle",
        "durdur",
        "bırak",
        "birak",
        "vazgeç",
        "vazgec",
        "dur",
        "sonlandır",
        "sonlandir",
        "dön",
        "don",
        "al",
        "et",
        "yap",
    }
)
_BACK_VOWELS: Final = frozenset("aıou")
_FRONT_VOWELS: Final = frozenset("eiöü")
_TRAILING: Final = re.compile(r"[.!?…]+$")


def _is_back(word: str) -> bool:
    """Vowel harmony: the last vowel of the word decides -ma (back) or -me (front)."""
    for ch in reversed(word.lower()):
        if ch in _BACK_VOWELS:
            return True
        if ch in _FRONT_VOWELS:
            return False
    raise ValueError(f"ünlüsüz fiil: {word!r}")


def negative_forms(verb: str) -> tuple[str, str, str]:
    """``sil`` -> (silme, silmeyin, silmeyiniz); ``kaldır`` -> (kaldırma, kaldırmayın, ...)."""
    if _is_back(verb):
        return f"{verb}ma", f"{verb}mayın", f"{verb}mayınız"
    return f"{verb}me", f"{verb}meyin", f"{verb}meyiniz"


def negate(sentence: str) -> tuple[str, ...]:
    """The three negative imperatives of ``sentence``, or ``()`` when it does not END in one
    of :data:`IMPERATIVES` ("iptal et" negates its auxiliary: "iptal etme")."""
    body = sentence.rstrip()
    match = _TRAILING.search(body)
    tail = match.group(0) if match else ""
    body = body[: len(body) - len(tail)]
    head, _, last = body.rpartition(" ")
    if intents.turkish_casefold(last) not in IMPERATIVES:
        return ()
    prefix = f"{head} " if head else ""
    return tuple(f"{prefix}{form}{tail}" for form in negative_forms(last))


# --- the cases --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Case:
    intent: str
    positive_id: str
    positive: str
    negative: str
    kind: str  # generated | incident | ablative

    @property
    def key(self) -> tuple[str, str]:
        return (self.intent, self.negative)


def all_negation_cases() -> list[Case]:
    """Every case, for every listed intent (present on this branch or not)."""
    cases: list[Case] = []
    for intent, entry in DESTRUCTIVE_TOOLS.items():
        for case_id, text in entry.positives:
            for negative in negate(text):
                cases.append(Case(intent, case_id, text, negative, "generated"))
    for intent, sentence, source in INCIDENT_CASES:
        cases.append(Case(intent, source, "", sentence, "incident"))
    for intent, sentence, source in ABLATIVE_CASES:
        cases.append(Case(intent, source, "", sentence, "ablative"))
    return cases


def _present() -> set[str]:
    return {i.value for i in Intent}


def destructive_present() -> set[str]:
    return set(DESTRUCTIVE_TOOLS) & _present()


def _state(intent: str) -> dict[str, Any]:
    return dict(DESTRUCTIVE_TOOLS[intent].state)


def render_cases_md() -> str:
    lines = [
        "# Olumsuz emir koruyucusu: üretilen vakalar",
        "",
        "Bu dosya `test_destructive_negation_guard.py` üreticisinin çıktısıdır; test birebir "
        "aynı olduğunu sınar. Yeniden üretmek için (`services/api` içinde): "
        "`uv run python -m tests.unit.test_destructive_negation_guard`.",
        "",
        "Beklenen her satırda aynıdır: cümle hiçbir silen/iptal eden niyete gitmez. "
        "`AÇIK` satırları bugün kırmızıdır (`KNOWN_OPEN`, xfail strict) ve düzeltme kartını adlar.",
        "",
        "| araç | tür | olumlu (kimlik) | olumlu cümle | olumsuz cümle | beklenen |",
        "|---|---|---|---|---|---|",
    ]
    for case in all_negation_cases():
        known = KNOWN_OPEN.get(case.key)
        expected = f"AÇIK: {known[1]}" if known else "silen araca gitmez"
        lines.append(
            f"| {case.intent} | {case.kind} | {case.positive_id} | {case.positive} "
            f"| {case.negative} | {expected} |"
        )
    for intent, entry in DESTRUCTIVE_TOOLS.items():
        if entry.no_positive_reason:
            lines.append(f"| {intent} | olumlu yok | - | - | - | {entry.no_positive_reason} |")
    return "\n".join(lines) + "\n"


# --- the generator's own table ----------------------------------------------------------


@pytest.mark.parametrize(
    ("positive", "expected"),
    [
        ("sil", ("silme", "silmeyin", "silmeyiniz")),
        ("unut", ("unutma", "unutmayın", "unutmayınız")),
        ("kaldır", ("kaldırma", "kaldırmayın", "kaldırmayınız")),
        ("iptal et", ("iptal etme", "iptal etmeyin", "iptal etmeyiniz")),
        ("temizle", ("temizleme", "temizlemeyin", "temizlemeyiniz")),
        ("Bunu unut.", ("Bunu unutma.", "Bunu unutmayın.", "Bunu unutmayınız.")),
        ("Alarmı kaldır.", ("Alarmı kaldırma.", "Alarmı kaldırmayın.", "Alarmı kaldırmayınız.")),
        ("İptal et.", ("İptal etme.", "İptal etmeyin.", "İptal etmeyiniz.")),
        ("Dur.", ("Durma.", "Durmayın.", "Durmayınız.")),
    ],
)
def test_the_generator_negates_by_vowel_harmony(positive: str, expected: tuple[str, ...]) -> None:
    assert negate(positive) == expected


@pytest.mark.parametrize(
    "sentence",
    ["Bunu unutma.", "Sabah rutinini iptal eder misin?", "Vazgeç, yapma.", "Silmesem mi?"],
)
def test_the_generator_leaves_what_does_not_end_in_an_imperative(sentence: str) -> None:
    assert negate(sentence) == ()


def test_the_cases_table_is_the_generators_output() -> None:
    assert CASES_MD.read_text(encoding="utf-8") == render_cases_md(), (
        f"{CASES_MD.name} üreticiyle aynı değil; yeniden üret: "
        "uv run python -m tests.unit.test_destructive_negation_guard"
    )


# --- the list watcher -------------------------------------------------------------------


def _watched_names() -> dict[str, str]:
    """Intent value -> the name that matched (the value itself or its tool)."""
    watched: dict[str, str] = {}
    for intent in Intent:
        tool = intents.CAPABILITY_BY_INTENT.get(intent) or intents.QUERY_TOOL_BY_INTENT.get(intent)
        for name in (intent.value, tool or ""):
            if _DESTRUCTIVE_NAME.search(name):
                watched[intent.value] = name
                break
    return watched


def test_every_destructive_intent_is_listed() -> None:
    missing = [
        value
        for value in sorted(_watched_names())
        if value not in DESTRUCTIVE_TOOLS and value not in NOT_DESTRUCTIVE
    ]
    assert not missing, "\n".join(
        f"yeni silen araç olumsuz emir korumasına yazılmadı: {value}" for value in missing
    )


def test_every_listed_entry_says_how_it_is_proven() -> None:
    for intent, entry in DESTRUCTIVE_TOOLS.items():
        assert entry.positives or entry.no_positive_reason, intent
    for intent, reason in NOT_DESTRUCTIVE.items():
        assert reason.strip(), f"NOT_DESTRUCTIVE gerekçesiz: {intent}"


def test_every_forget_all_has_its_ablative() -> None:
    covered = {intent for intent, _, _ in ABLATIVE_CASES}
    missing = sorted(v for v in _present() if v.endswith("_forget_all") and v not in covered)
    assert not missing, f"ayrılma hâli vakası yok: {missing}"


def test_known_open_names_only_real_cases() -> None:
    keys = {case.key for case in all_negation_cases()}
    stale = [key for key in KNOWN_OPEN if key not in keys]
    assert not stale, f"KNOWN_OPEN üretilmeyen vakayı adlıyor: {stale}"


# --- the corpus positives still reach their tool ----------------------------------------


@cache
def _corpus() -> dict[str, Any]:
    from tests.voice_corpus.corpus import all_cases

    return {case.case_id: case for case in all_cases()}


def _positive_params() -> list[Any]:
    params = []
    for intent in sorted(destructive_present()):
        for case_id, text in DESTRUCTIVE_TOOLS[intent].positives:
            params.append(pytest.param(intent, case_id, text, id=f"{intent}:{case_id}"))
    return params


@pytest.mark.parametrize(("intent", "case_id", "text"), _positive_params())
def test_the_positive_reaches_its_tool(intent: str, case_id: str, text: str) -> None:
    """Without this the negative half proves nothing: the state must make the positive act."""
    case = _corpus().get(case_id)
    assert case is not None, f"korpusta yok: {case_id}"
    assert (case.utterance, case.expected_intent) == (text, intent)
    assert resolve_intent(text, **_state(intent)).intent.value == intent


# --- the guard itself -------------------------------------------------------------------


def _negative_params() -> list[Any]:
    present = destructive_present()
    params = []
    for case in all_negation_cases():
        if case.intent not in present:
            continue
        marks = []
        if (known := KNOWN_OPEN.get(case.key)) is not None:
            marks.append(
                pytest.mark.xfail(
                    strict=True, raises=AssertionError, reason=f"{known[0]} -> {known[1]}"
                )
            )
        params.append(
            pytest.param(case, marks=marks, id=f"{case.kind}:{case.intent}:{case.negative}")
        )
    return params


@pytest.mark.parametrize("case", _negative_params())
def test_the_negative_imperative_reaches_no_destructive_tool(case: Case) -> None:
    resolved = resolve_intent(case.negative, **_state(case.intent)).intent.value
    assert resolved not in destructive_present(), (
        f"olumsuz emir silen araca gidiyor: {case.negative!r} -> {resolved} "
        f"(olumlusu {case.positive_id}: {case.positive!r})"
    )


def test_the_bugs_already_fixed_stay_green() -> None:
    """B16 ('unutma') and B27 ('iptal etme') were fixed on their own; never KNOWN_OPEN."""
    for key in (("memory_forget", "Bunu unutma."), ("research_cancel", "Araştırmayı iptal etme.")):
        assert key not in KNOWN_OPEN
        assert resolve_intent(key[1]).intent.value not in destructive_present(), key


if __name__ == "__main__":
    CASES_MD.write_text(render_cases_md(), encoding="utf-8", newline="\n")
    print(f"{CASES_MD} yazıldı: {len(all_negation_cases())} vaka")
