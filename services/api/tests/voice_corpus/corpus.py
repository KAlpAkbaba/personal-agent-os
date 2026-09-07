"""The Owner Utterance Corpus: versioned cases with their route contracts.

Each :class:`UtteranceCase` says what MUST happen and what MUST NOT. Sources:

* ``canonical``   - spec §6 phrases and the owner's own directives;
* ``paraphrase``  - realistic Turkish variation (polite, short, long, colloquial);
* ``asr_noise``   - transcription imperfections a real ASR produces (no diacritics, split
                    numbers, spaced clock times, dropped punctuation);
* ``regression``  - a phrase that once misrouted in a real owner run (kept forever);
* ``generated``   - deterministic template expansion (seeded; never an LLM's opinion).

Expected fields are deterministic semantic labels: an intent, the tool the contract maps
that intent to, the target the reference must resolve to (``current`` / ``previous`` / a
fixture id), the response class, the tools that must be refused or never reached, and the
side-effect policy the fake device is checked against.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from typing import Final

CORPUS_VERSION: Final = 1

#: Response classes.
RESPONSE_OK: Final = "ok"  # succeeded with speech (and a target where one is expected)
RESPONSE_REFUSED: Final = "refused"  # a truthful refusal receipt (expected)
RESPONSE_CLARIFY: Final = "needs_clarification"
RESPONSE_RUNNING: Final = "running"  # a long-running tool accepted the work
RESPONSE_CONTROL: Final = "control"  # a control intent; no tool is dispatched
RESPONSE_NONE: Final = "none"  # a conversational turn; no tool is dispatched

#: Context fixtures the harness knows how to build.
CTX_NONE: Final = "none"
CTX_RESEARCH_FOCUS_B: Final = "research_focus_b"  # A (older) and B (newer, focused), one title
CTX_ALARM_RINGING: Final = "alarm_ringing"
CTX_ALARM_SCHEDULED: Final = "alarm_scheduled"
CTX_EYE_DISABLED: Final = "eye_disabled"
#: M19 (docs/M19_DIGITAL_OPERATOR_SPEC.md §5): a window ("w-1", and an older "w-0") is
#: already the durable object focus - the window-control and type-text families resolve
#: their target through it, never through a window id the model guessed.
CTX_WINDOW_FOCUSED: Final = "window_focused"
#: An operator task is genuinely mid-flight (the exact slot ``OperatorService.start_task``
#: fills), so a bare "Dur."/"İptal et." routes to OPERATOR_CANCEL and "Ne yapıyorsun?" to
#: OPERATOR_STATUS - the same ringing-aware pattern CTX_ALARM_RINGING already gives.
CTX_OPERATOR_RUNNING: Final = "operator_running"

#: M20 (docs/M20_FILE_DOCUMENT_INTELLIGENCE_SPEC.md §5): the fixture contexts File &
#: Document Intelligence needs, all pre-indexed straight from the oracle
#: (tests/documents_support.py) — no device call, so a case using one of these starts with
#: the document already known and only calls the device when the tool genuinely needs to
#: (an extract that has not happened yet, a compare across two files).
#: The current document is rapor.pdf (5 pages), the previous is sunum-q3.pptx (7 slides) —
#: exactly the pair the task brief names.
CTX_DOCUMENT_FOCUSED: Final = "document_focused"
#: A payment contract (sozlesmeler/2026/sozlesme.docx) is the current document — for the
#: content questions the pdf/pptx pair cannot answer ("Ödeme süresi kaç gün?").
CTX_DOCX_FOCUSED: Final = "docx_focused"
#: The budget spreadsheet is the current document — for "Bu Excel'de ne var?".
CTX_XLSX_FOCUSED: Final = "xlsx_focused"
#: The Q3 presentation is the current document — for "Bu sunumda kaç slayt var?".
CTX_PPTX_FOCUSED: Final = "pptx_focused"
#: A FILE is focused (found by an earlier search) but not yet read: exercises the actual
#: ``document.extract`` device call, spec §3's "current file not yet extracted -> extract
#: it first" branch.
CTX_FILE_FOCUSED: Final = "file_focused"
#: Three of the fixture's files are already indexed and recently used — exactly
#: ``truth.json``'s own ``common_points`` fixture (butce-2026.xlsx, kod.py, notlar.md;
#: common term "bütçe") — for ``document.common_points``'s default "recent" targets.
CTX_COMMON_POINTS_FOCUSED: Final = "common_points_focused"
#: A FILE focus naming a secret-bearing path the device refuses to read at all (spec §2:
#: ``.env*`` etc -> ``permission_denied``) — never a real secret, never a real device;
#: tests/documents_support.py's fake recognises the sentinel id and refuses honestly.
CTX_SECRET_FILE_FOCUSED: Final = "secret_file_focused"

#: Side-effect policies: the device capabilities a case MAY reach on the fake device.
#: Anything else the fake device saw is a forbidden side effect.
SIDE_EFFECTS_NONE: Final[frozenset[str]] = frozenset()
SIDE_EFFECTS_DISPLAY_OFF: Final[frozenset[str]] = frozenset({"desktop.display_off"})
SIDE_EFFECTS_DISPLAY_WAKE: Final[frozenset[str]] = frozenset({"desktop.display_wake"})
SIDE_EFFECTS_ALARM_STOP: Final[frozenset[str]] = frozenset(
    {"browser.media_stop", "desktop.alarm_stop", "desktop.alarm_disarm"}
)
SIDE_EFFECTS_ALARM_SNOOZE: Final[frozenset[str]] = frozenset(
    {"browser.media_stop", "desktop.alarm_stop", "desktop.alarm_disarm", "desktop.alarm_arm"}
)
SIDE_EFFECTS_ALARM_CANCEL: Final[frozenset[str]] = frozenset({"desktop.alarm_disarm"})

#: M19 (docs/M19_DIGITAL_OPERATOR_SPEC.md §4): exactly the device capabilities each plan
#: may reach on the fake device — anything else the harness sees is a forbidden side
#: effect (the same policy the alarm/display families already use above).
SIDE_EFFECTS_OPERATOR_APP_OPEN: Final[frozenset[str]] = frozenset({"app.launch", "window.current"})
SIDE_EFFECTS_OPERATOR_WINDOW_CLOSE: Final[frozenset[str]] = frozenset(
    {"window.close", "window.list"}
)
SIDE_EFFECTS_OPERATOR_WINDOW_MAXIMIZE: Final[frozenset[str]] = frozenset({"window.maximize"})
SIDE_EFFECTS_OPERATOR_WINDOW_MINIMIZE: Final[frozenset[str]] = frozenset({"window.minimize"})
SIDE_EFFECTS_OPERATOR_WINDOW_RESTORE: Final[frozenset[str]] = frozenset({"window.restore"})
SIDE_EFFECTS_OPERATOR_WINDOW_PREVIOUS: Final[frozenset[str]] = frozenset({"window.activate"})
SIDE_EFFECTS_OPERATOR_TYPE: Final[frozenset[str]] = frozenset(
    {"window.activate", "keyboard.type", "ui.inspect"}
)
SIDE_EFFECTS_OPERATOR_SHELL: Final[frozenset[str]] = frozenset({"terminal.execute"})

#: M20 (docs/M20_FILE_DOCUMENT_INTELLIGENCE_SPEC.md §4): exactly the device capabilities
#: each documents tool may reach on the fake device (tests/documents_support.py) — the
#: same policy discipline every other family above already uses. A case whose document is
#: already indexed (a CTX_*_FOCUSED fixture) reaches NO capability at all: the index IS
#: the read, and "no background crawling" means a cached answer never re-reads.
SIDE_EFFECTS_DOCUMENTS_SEARCH: Final[frozenset[str]] = frozenset({"file.search"})
SIDE_EFFECTS_DOCUMENTS_READ: Final[frozenset[str]] = frozenset({"document.extract"})
SIDE_EFFECTS_DOCUMENTS_COMPARE: Final[frozenset[str]] = frozenset({"file.compare"})


@dataclass(frozen=True, slots=True)
class UtteranceCase:
    case_id: str
    utterance: str
    expected_intent: str
    expected_tool: str | None
    expected_response: str = RESPONSE_OK
    #: current | previous | none, or a literal fixture key ("research:B") the harness resolves.
    expected_target: str | None = None
    #: Deterministic extras the harness checks on the tool result (e.g. a parsed clock time).
    expected: dict[str, object] = field(default_factory=dict)
    forbidden_tools: tuple[str, ...] = ()
    side_effects: frozenset[str] = SIDE_EFFECTS_NONE
    context: str = CTX_NONE
    category: str = "misc"
    source: str = "canonical"
    locale: str = "tr-TR"
    regression_issue_id: str | None = None
    #: Arguments the model would pass beside the contract-derived ones (e.g. snooze minutes).
    tool_arguments: dict[str, object] = field(default_factory=dict)
    #: A turn number > 1 lets a case run after another case in the same session (conversation).
    notes: str = ""


def _strip_diacritics(text: str) -> str:
    table = str.maketrans("çğıöşüÇĞİÖŞÜâîû", "cgiosuCGIOSUaiu")
    return text.translate(table)


def _variants(text: str) -> list[tuple[str, str]]:
    """Deterministic ASR-shaped variants of one phrase: no punctuation, lower case, no
    diacritics. Returned with their source label; duplicates removed, order kept."""
    out: list[tuple[str, str]] = []
    seen = {text}
    for label, variant in (
        ("asr_noise", text.rstrip(".?!")),
        ("asr_noise", text.rstrip(".?!").lower()),
        ("asr_noise", _strip_diacritics(text.rstrip(".?!").lower())),
    ):
        if variant not in seen:
            seen.add(variant)
            out.append((label, variant))
    return out


def _with_variants(base: UtteranceCase) -> list[UtteranceCase]:
    cases = [base]
    for n, (label, variant) in enumerate(_variants(base.utterance), start=1):
        cases.append(
            UtteranceCase(
                **{
                    **{f: getattr(base, f) for f in base.__dataclass_fields__},
                    "case_id": f"{base.case_id}.v{n}",
                    "utterance": variant,
                    "source": label,
                }
            )
        )
    return cases


# ------------------------------------------------------------------ research


def _research_cases() -> list[UtteranceCase]:
    cases: list[UtteranceCase] = []
    technical = [
        ("r.tech.1", "Bunu teknik anlat.", "canonical"),
        ("r.tech.2", "Bunun teknik tarafını anlat.", "paraphrase"),
        ("r.tech.3", "Teknik detayını ver.", "paraphrase"),
        ("r.tech.4", "Az önceki araştırmanın teknik detayını açıkla.", "paraphrase"),
        ("r.tech.5", "Seçtiğim araştırmayı teknik anlat.", "paraphrase"),
        ("r.tech.6", "Bunun arka planda nasıl çalıştığını anlat.", "paraphrase"),
        ("r.tech.7", "Bunu teknik anlatır mısın?", "paraphrase"),
        ("r.tech.8", "Bunu teknik olarak anlat.", "paraphrase"),
        ("r.tech.9", "Teknik anlat.", "regression"),
        ("r.tech.10", "Hangi sayfalar elendi?", "canonical"),
        ("r.tech.11", "Araştırma sırasında ne sorun oldu?", "canonical"),
    ]
    # The diagnostic questions ("hangi sayfalar elendi", "ne sorun oldu") and "detayını
    # açıkla" resolve as EXPLAIN questions whose research class is technical; the class
    # and the level are the contract, the intent name is how the router got there.
    explain_shaped = {"r.tech.4", "r.tech.10", "r.tech.11"}
    for case_id, text, source in technical:
        cases.extend(
            _with_variants(
                UtteranceCase(
                    case_id=case_id,
                    utterance=text,
                    expected_intent="explain" if case_id in explain_shaped else "technical",
                    expected_tool="research.explain",
                    expected_target="current",
                    expected={
                        "level": "technical",
                        "research_class": "research_technical_explanation",
                    },
                    forbidden_tools=("research.start",),
                    context=CTX_RESEARCH_FOCUS_B,
                    category="research",
                    source=source,
                    regression_issue_id="M18.2 owner runs 2026-09-06"
                    if source == "regression"
                    else None,
                )
            )
        )
    current = [
        ("r.cur.1", "Bunu anlat.", "canonical"),
        ("r.cur.2", "Bu araştırmayı anlat.", "canonical"),
        ("r.cur.3", "Az önceki araştırmayı anlat.", "canonical"),
        ("r.cur.4", "Son araştırmayı anlat.", "paraphrase"),
        ("r.cur.5", "Bunu özetle.", "paraphrase"),
        ("r.cur.6", "Sonuçları anlat.", "paraphrase"),
        ("r.cur.7", "Bu araştırmanın sonuçlarını anlatır mısın?", "paraphrase"),
    ]
    for case_id, text, source in current:
        cases.extend(
            _with_variants(
                UtteranceCase(
                    case_id=case_id,
                    utterance=text,
                    # The intent varies (none/summarize/...); the class is what binds.
                    expected_intent=None,
                    expected_tool="research.explain",
                    expected_target="current",
                    forbidden_tools=("research.start",),
                    context=CTX_RESEARCH_FOCUS_B,
                    category="research",
                    source=source,
                )
            )
        )
    previous = [
        ("r.prev.1", "Bir önceki araştırmayı anlat.", "canonical"),
        ("r.prev.2", "Önceki araştırmaya dön.", "paraphrase"),
        ("r.prev.3", "Bir öncekinin teknik detayını ver.", "paraphrase"),
        ("r.prev.4", "Bir öncekini anlat.", "canonical"),
        ("r.prev.5", "Bundan önceki araştırmayı anlat.", "paraphrase"),
    ]
    for case_id, text, source in previous:
        cases.extend(
            _with_variants(
                UtteranceCase(
                    case_id=case_id,
                    utterance=text,
                    expected_intent=None,
                    expected_tool="research.explain",
                    expected_target="previous",
                    forbidden_tools=("research.start",),
                    context=CTX_RESEARCH_FOCUS_B,
                    category="research",
                    source=source,
                )
            )
        )
    sources = [
        ("r.src.1", "Kaynakları söyle.", "canonical"),
        ("r.src.2", "Bunun kaynakları neydi?", "canonical"),
        ("r.src.3", "Hangi kaynaklara baktın?", "canonical"),
    ]
    for case_id, text, source in sources:
        cases.extend(
            _with_variants(
                UtteranceCase(
                    case_id=case_id,
                    utterance=text,
                    expected_intent=None,
                    expected_tool="research.sources",
                    expected_target="current",
                    forbidden_tools=("research.start",),
                    context=CTX_RESEARCH_FOCUS_B,
                    category="research",
                    source=source,
                )
            )
        )
    cases.extend(
        _with_variants(
            UtteranceCase(
                case_id="r.find.1",
                utterance="Birinci bulguyu detaylandır.",
                expected_intent=None,
                expected_tool="research.finding_detail",
                expected_target="current",
                forbidden_tools=("research.start",),
                tool_arguments={"index": 1},
                context=CTX_RESEARCH_FOCUS_B,
                category="research",
            )
        )
    )
    # A NEW research and an explicit re-run may crawl: research.start is the expected tool
    # and must be ACCEPTED (running), never refused.
    for case_id, text, source in (
        ("r.new.1", "Yerel modellerin son durumunu araştır.", "canonical"),
        ("r.new.2", "Yapay zeka ajanları hakkında bir araştırma yap.", "paraphrase"),
        ("r.retry.1", "Araştırmayı yeniden yap.", "canonical"),
        ("r.retry.2", "Tekrar araştır.", "paraphrase"),
    ):
        cases.append(
            UtteranceCase(
                case_id=case_id,
                utterance=text,
                expected_intent=None,
                expected_tool="research.start",
                expected_response=RESPONSE_RUNNING,
                expected={
                    "research_class": "new_research" if "new" in case_id else "research_retry"
                },
                context=CTX_RESEARCH_FOCUS_B,
                category="research",
                source=source,
                tool_arguments={"topic": text},
            )
        )
    # A question about the system itself stays with the ledger even with a research in focus.
    cases.append(
        UtteranceCase(
            case_id="r.neighbour.ledger",
            utterance="Son yaptıklarını anlat.",
            expected_intent="explain",
            expected_tool="activity.explain",
            expected={"routed_not": "research_report"},
            context=CTX_RESEARCH_FOCUS_B,
            category="research",
            source="canonical",
        )
    )
    return cases


# --------------------------------------------------------------------- alarms


def _alarm_create_cases() -> list[UtteranceCase]:
    cases: list[UtteranceCase] = []
    explicit = [
        ("a.create.1", "Yarın 7:30'da beni uyandır.", "07:30", "canonical"),
        ("a.create.2", "Sabah yedi buçukta alarm kur.", "07:30", "paraphrase"),
        ("a.create.3", "Yarın sabah 07:30 için uyandırma alarmı ayarla.", "07:30", "paraphrase"),
        ("a.create.4", "yarın yedi buçukta alarm kur", "07:30", "asr_noise"),
        ("a.create.5", "yarin 7 30 da alarm kur", "07:30", "asr_noise"),
        ("a.create.6", "sabah 07:30 uyandır", "07:30", "asr_noise"),
        ("a.create.7", "beni yarın sabah yedi otuzda uyandır", "07:30", "asr_noise"),
        ("a.create.8", "Saat 08:00'e alarm kur.", "08:00", "canonical"),
        ("a.create.9", "yarın 07.30'da uyandır", "07:30", "asr_noise"),
        (
            "a.create.10",
            "Lütfen beni yarın sabah yedi buçukta uyandırır mısın?",
            "07:30",
            "paraphrase",
        ),
        ("a.create.11", "Yarın sabah 07:30'da beni uyandır.", "07:30", "canonical"),
        ("a.create.12", "Akşam yedide uyandır.", "19:00", "canonical"),
    ]
    for case_id, text, local_time, source in explicit:
        cases.append(
            UtteranceCase(
                case_id=case_id,
                utterance=text,
                expected_intent="alarm_create",
                expected_tool="alarm.create",
                expected={"local_time": local_time},
                context=CTX_NONE,
                category="alarm",
                source=source,
            )
        )
    cases.append(
        UtteranceCase(
            case_id="a.create.weekly",
            utterance="Her hafta içi 07:15'te beni uyandır.",
            expected_intent="alarm_create",
            expected_tool="alarm.create",
            expected={"local_time": "07:15", "weekdays": [0, 1, 2, 3, 4]},
            category="alarm",
        )
    )
    # Deterministic template expansion (generated, seeded by order): every combination
    # of these parts must parse to 07:30.
    prefixes = ["", "Lütfen ", "Beni "]
    days = ["yarın sabah ", "yarın ", "sabah "]
    times = ["7:30'da ", "07:30'da ", "yedi buçukta ", "7 30 da ", "07.30'da ", "yedi otuzda "]
    verbs = ["uyandır.", "alarm kur.", "beni uyandır."]
    n = 0
    for prefix, day, when, verb in itertools.product(prefixes, days, times, verbs):
        n += 1
        if n % 3:  # a bounded, deterministic third of the product: 54 of 162
            continue
        text = f"{prefix}{day}{when}{verb}".replace("Beni beni", "Beni").replace("  ", " ")
        cases.append(
            UtteranceCase(
                case_id=f"a.gen.{n}",
                utterance=text,
                expected_intent="alarm_create",
                expected_tool="alarm.create",
                expected={"local_time": "07:30"},
                category="alarm",
                source="generated",
            )
        )
    # Unparseable times are REFUSED, never guessed.
    for case_id, text in (
        ("a.unparsed.1", "Beni bir ara uyandır."),
        ("a.unparsed.2", "Yarın alarm kur."),
    ):
        cases.append(
            UtteranceCase(
                case_id=case_id,
                utterance=text,
                expected_intent="alarm_create",
                expected_tool="alarm.create",
                expected_response=RESPONSE_REFUSED,
                expected={"error_class": "when_unparsed"},
                category="alarm",
            )
        )
    # Test alarms.
    for case_id, text, source in (
        ("a.test.1", "90 saniye sonra test alarmı kur.", "canonical"),
        ("a.test.2", "doksan saniye sonra test alarmı kur", "asr_noise"),
        ("a.test.3", "Doksan saniye sonra YouTube'dan Time ile test alarmı kur.", "canonical"),
    ):
        cases.append(
            UtteranceCase(
                case_id=case_id,
                utterance=text,
                expected_intent="alarm_test_create",
                expected_tool="alarm.create",
                expected={"is_test": True, "relative_seconds": 90},
                tool_arguments={"test": True},
                category="alarm",
                source=source,
            )
        )
    return cases


def _alarm_control_cases() -> list[UtteranceCase]:
    cases: list[UtteranceCase] = []
    stop = [
        ("a.stop.1", "Alarmı kapat.", "canonical"),
        ("a.stop.2", "Alarmı durdur.", "canonical"),
        ("a.stop.3", "Alarmı sustur.", "canonical"),
        ("a.stop.4", "Sustur.", "paraphrase"),
        ("a.stop.5", "Tamam, kapat.", "paraphrase"),
        ("a.stop.6", "Kes şunu.", "paraphrase"),
        ("a.stop.7", "alarmı sustur", "asr_noise"),
    ]
    for case_id, text, source in stop:
        cases.append(
            UtteranceCase(
                case_id=case_id,
                utterance=text,
                expected_intent="alarm_stop",
                expected_tool="alarm.stop",
                expected={"alarm_state": "STOPPED"},
                side_effects=SIDE_EFFECTS_ALARM_STOP,
                context=CTX_ALARM_RINGING,
                category="alarm",
                source=source,
            )
        )
    snooze = [
        ("a.snooze.1", "10 dakika ertele.", 10, "canonical"),
        ("a.snooze.2", "On dakika sonra tekrar çal.", 10, "paraphrase"),
        ("a.snooze.3", "Biraz ertele.", 5, "paraphrase"),
        ("a.snooze.4", "Beş dakika ertele.", 5, "canonical"),
        ("a.snooze.5", "beş dakika ertele", 5, "asr_noise"),
    ]
    for case_id, text, minutes, source in snooze:
        cases.append(
            UtteranceCase(
                case_id=case_id,
                utterance=text,
                expected_intent="alarm_snooze",
                expected_tool="alarm.snooze",
                # A snooze re-arms the alarm at once (SNOOZED is the transition, ARMED
                # the state it rests in): the proof is the count and the minutes SAID.
                expected={"alarm_state": "ARMED", "snooze_count": 1, "snooze_minutes": minutes},
                side_effects=SIDE_EFFECTS_ALARM_SNOOZE,
                context=CTX_ALARM_RINGING,
                category="alarm",
                source=source,
            )
        )
    for case_id, text, source in (
        ("a.cancel.1", "Alarmı iptal et.", "canonical"),
        ("a.cancel.2", "Sabah alarmını iptal et.", "paraphrase"),
        ("a.cancel.3", "Alarmı kaldır.", "paraphrase"),
    ):
        cases.append(
            UtteranceCase(
                case_id=case_id,
                utterance=text,
                expected_intent="alarm_cancel",
                expected_tool="alarm.cancel",
                expected={"alarm_state": "CANCELLED"},
                side_effects=SIDE_EFFECTS_ALARM_CANCEL,
                context=CTX_ALARM_SCHEDULED,
                category="alarm",
                source=source,
            )
        )
    for case_id, text, source in (
        ("a.query.1", "Sabah alarmım kaçta?", "canonical"),
        ("a.query.2", "Alarm var mı?", "paraphrase"),
        ("a.query.3", "Alarmım ne zaman?", "paraphrase"),
    ):
        cases.append(
            UtteranceCase(
                case_id=case_id,
                utterance=text,
                expected_intent="alarm_query",
                expected_tool="alarm.status",
                context=CTX_ALARM_SCHEDULED,
                category="alarm",
                source=source,
            )
        )
    return cases


# -------------------------------------------------------------------- display


def _display_cases() -> list[UtteranceCase]:
    cases: list[UtteranceCase] = []
    for case_id, text, source in (
        ("d.off.1", "Ekranları kapat.", "canonical"),
        ("d.off.2", "Monitörleri kapat.", "paraphrase"),
        ("d.off.3", "Ekranı kapat.", "canonical"),
        ("d.off.4", "Görüntüyü kapat.", "paraphrase"),
        ("d.off.5", "ekranları kapatsana", "asr_noise"),
    ):
        cases.extend(
            _with_variants(
                UtteranceCase(
                    case_id=case_id,
                    utterance=text,
                    expected_intent="display_off",
                    expected_tool="display.off",
                    forbidden_tools=("display.wake",),
                    side_effects=SIDE_EFFECTS_DISPLAY_OFF,
                    category="display",
                    source=source,
                )
            )
        )
    for case_id, text, source in (
        ("d.wake.1", "Ekranları aç.", "canonical"),
        ("d.wake.2", "Monitörleri aç.", "paraphrase"),
        ("d.wake.3", "Ekranı uyandır.", "paraphrase"),
    ):
        cases.extend(
            _with_variants(
                UtteranceCase(
                    case_id=case_id,
                    utterance=text,
                    expected_intent="display_wake",
                    expected_tool="display.wake",
                    forbidden_tools=("display.off",),
                    side_effects=SIDE_EFFECTS_DISPLAY_WAKE,
                    category="display",
                    source=source,
                )
            )
        )
    for case_id, text, source in (
        ("d.status.1", "Ekranlar açık mı?", "canonical"),
        ("d.status.2", "Ekran durumu ne?", "paraphrase"),
        ("d.status.3", "Monitörler kapalı mı?", "paraphrase"),
    ):
        cases.extend(
            _with_variants(
                UtteranceCase(
                    case_id=case_id,
                    utterance=text,
                    expected_intent="display_query",
                    expected_tool="display.status",
                    forbidden_tools=("display.off", "display.wake"),
                    category="display",
                    source=source,
                )
            )
        )
    policy = [
        ("am.1", "Uyurken ekranları kapat.", {"off_when_asleep": True}),
        ("am.2", "Uyuduğumda ekranları kapatma.", {"off_when_asleep": False}),
        ("am.3", "Ben yokken ekranları kapat.", {"off_when_away": True}),
        ("am.4", "Ben yokken ekranları kapatma.", {"off_when_away": False}),
        ("am.5", "Otomatik ekran yönetimini aç.", {"auto_off_enabled": True}),
        ("am.6", "Otomatik ekran yönetimini kapat.", {"auto_off_enabled": False}),
        ("am.7", "Otomatik ekran kapatmayı aç.", {"auto_off_enabled": True}),
        ("am.8", "Ekranı açık tut.", {"keep_on": True}),
        ("am.9", "Ben geri geldiğimde ekranı aç.", {"wake_on_return": True}),
    ]
    for case_id, text, changes in policy:
        cases.extend(
            _with_variants(
                UtteranceCase(
                    case_id=case_id,
                    utterance=text,
                    expected_intent="ambient_policy_set",
                    expected_tool="ambient.set_policy",
                    expected={"policy_changes_include": changes},
                    forbidden_tools=("display.off", "display.wake"),
                    category="ambient",
                )
            )
        )
    for case_id, text in (
        ("am.explain.1", "Ekranları neden kapattın?"),
        ("am.explain.2", "Neden açık bıraktın?"),
        ("am.explain.3", "Şu an ekran politikası ne?"),
    ):
        cases.extend(
            _with_variants(
                UtteranceCase(
                    case_id=case_id,
                    utterance=text,
                    expected_intent="ambient_explain",
                    expected_tool="ambient.explain",
                    forbidden_tools=("display.off", "display.wake"),
                    category="ambient",
                )
            )
        )
    cases.append(
        UtteranceCase(
            case_id="am.test.1",
            utterance="Ekran uyku otomasyonunu test et.",
            expected_intent="ambient_test_display",
            expected_tool="ambient.test_display",
            category="ambient",
        )
    )
    return cases


# ------------------------------------------------------------- eye / presence


def _eye_cases() -> list[UtteranceCase]:
    cases: list[UtteranceCase] = []
    for case_id, text, source in (
        ("e.off.1", "Gözünü kapat.", "canonical"),
        ("e.off.2", "Kamerayı kapat.", "canonical"),
        ("e.off.3", "Beni izleme.", "canonical"),
        ("e.off.4", "gözünü kapat", "asr_noise"),
    ):
        cases.extend(
            _with_variants(
                UtteranceCase(
                    case_id=case_id,
                    utterance=text,
                    expected_intent="eye_disable",
                    expected_tool="eye.disable",
                    expected={"eye_enabled_after": False},
                    forbidden_tools=("eye.enable", "display.off"),
                    category="eye",
                    source=source,
                )
            )
        )
    for case_id, text, source in (
        ("e.on.1", "Gözünü aç.", "canonical"),
        ("e.on.2", "Kamerayı aç.", "canonical"),
        ("e.on.3", "Beni izle.", "canonical"),
        ("e.on.4", "Beni tekrar izle.", "canonical"),
    ):
        cases.extend(
            _with_variants(
                UtteranceCase(
                    case_id=case_id,
                    utterance=text,
                    expected_intent="eye_enable",
                    expected_tool="eye.enable",
                    expected={"eye_enabled_after": True},
                    forbidden_tools=("eye.disable", "display.wake"),
                    context=CTX_EYE_DISABLED,
                    category="eye",
                    source=source,
                )
            )
        )
    for case_id, text, kind in (
        ("p.q.1", "Beni görüyor musun?", "eye_state"),
        ("p.q.2", "Kamera açık mı?", "eye_state"),
        ("p.q.3", "Göz açık mı?", "eye_state"),
        ("p.q.4", "Şu an burada mıyım?", "world_state"),
        ("p.q.5", "Kendi sisteminde şu anda ne görüyorsun?", "world_state"),
    ):
        cases.extend(
            _with_variants(
                UtteranceCase(
                    case_id=case_id,
                    utterance=text,
                    expected_intent="explain",
                    expected_tool="state.now",
                    expected={"query_kind": kind},
                    forbidden_tools=("eye.enable", "eye.disable"),
                    category="presence",
                )
            )
        )
    return cases


# ------------------------------------------------------------------ controls


def _control_cases() -> list[UtteranceCase]:
    cases: list[UtteranceCase] = []
    for case_id, text, intent in (
        ("c.stop.1", "Dur.", "stop"),
        ("c.stop.2", "Kes.", "stop"),
        ("c.resume.1", "Devam et.", "resume"),
        ("c.repeat.1", "Tekrar oku.", "repeat"),
        ("c.slower.1", "Biraz daha yavaş.", "slower"),
        ("c.faster.1", "Biraz daha hızlı.", "faster"),
        ("c.summ.1", "Özetle.", "summarize"),
        ("c.full.1", "Hepsini oku.", "full"),
    ):
        cases.append(
            UtteranceCase(
                case_id=case_id,
                utterance=text,
                expected_intent=intent,
                expected_tool=None,
                expected_response=RESPONSE_CONTROL,
                category="control",
            )
        )
    for case_id, text in (("dep.1", "Bunu canlıya al."), ("dep.2", "Yayına al.")):
        cases.append(
            UtteranceCase(
                case_id=case_id,
                utterance=text,
                expected_intent="deploy",
                expected_tool="release.promote",
                expected_response=RESPONSE_REFUSED,
                category="authority",
            )
        )
    return cases


# ----------------------------------------------------------- self-evolution (M18.4)


def _evolution_cases() -> list[UtteranceCase]:
    """The owner's voice over self-evolution (docs/M18_4_SELF_EVOLUTION_SPEC.md §4). With no
    candidate in the lab, cancel and hold are truthful refusals; the pause switch is a
    ledger row read back; the rollback is always refused; the four questions are answered
    from the supervisor's status through activity.explain."""
    cases: list[UtteranceCase] = []
    for case_id, text, paused, source in (
        ("ev.pause.1", "Kendi kendini geliştirmeyi duraklat.", True, "canonical"),
        ("ev.pause.2", "Kendini geliştirmeyi durdur.", True, "paraphrase"),
        ("ev.pause.3", "Kendi kendini geliştirmeyi kapat.", True, "paraphrase"),
        ("ev.resume.1", "Kendi kendini geliştirmeyi aç.", False, "canonical"),
        ("ev.resume.2", "Kendini geliştirmeye devam et.", False, "paraphrase"),
        ("ev.resume.3", "Kendi kendini geliştirmeyi başlat.", False, "paraphrase"),
    ):
        cases.extend(
            _with_variants(
                UtteranceCase(
                    case_id=case_id,
                    utterance=text,
                    expected_intent="evolution_pause" if paused else "evolution_resume",
                    expected_tool="evolution.control",
                    expected={"evolution_paused_after": paused},
                    forbidden_tools=("release.promote",),
                    category="evolution",
                    source=source,
                )
            )
        )
    for case_id, text, intent, error_class, source in (
        (
            "ev.cancel.1",
            "Bu geliştirmeyi iptal et.",
            "evolution_cancel",
            "no_candidate",
            "canonical",
        ),
        ("ev.cancel.2", "Geliştirmeden vazgeç.", "evolution_cancel", "no_candidate", "paraphrase"),
        ("ev.hold.1", "Bunu canlıya alma.", "evolution_hold", "no_candidate", "canonical"),
        ("ev.hold.2", "Bunu yayına alma.", "evolution_hold", "no_candidate", "paraphrase"),
    ):
        cases.extend(
            _with_variants(
                UtteranceCase(
                    case_id=case_id,
                    utterance=text,
                    expected_intent=intent,
                    expected_tool="evolution.control",
                    expected_response=RESPONSE_REFUSED,
                    expected={"error_class": error_class},
                    forbidden_tools=("release.promote",),
                    category="evolution",
                    source=source,
                )
            )
        )
    for case_id, text, source in (
        ("ev.rollback.1", "Önceki sürüme dön.", "canonical"),
        ("ev.rollback.2", "Eski sürüme geri al.", "paraphrase"),
        ("ev.rollback.3", "Bir önceki sürüme geri dön.", "paraphrase"),
    ):
        cases.extend(
            _with_variants(
                UtteranceCase(
                    case_id=case_id,
                    utterance=text,
                    expected_intent="release_rollback",
                    expected_tool="release.rollback",
                    expected_response=RESPONSE_REFUSED,
                    expected={"error_class": "owner_authorization_required"},
                    forbidden_tools=("release.promote",),
                    category="evolution",
                    source=source,
                )
            )
        )
    for case_id, text, kind in (
        ("ev.q.now", "Şu an ne geliştiriyorsun?", "evolution_now"),
        ("ev.q.fix", "Son hangi hatayı düzelttin?", "last_fix"),
        ("ev.q.version", "Hangi sürüm çalışıyor?", "running_version"),
        ("ev.q.pending", "Bekleyen aday sürüm var mı?", "pending_candidates"),
    ):
        cases.extend(
            _with_variants(
                UtteranceCase(
                    case_id=case_id,
                    utterance=text,
                    expected_intent="explain",
                    expected_tool="activity.explain",
                    expected={"query_kind": kind, "routed": "evolution.status"},
                    forbidden_tools=("release.promote", "evolution.control"),
                    category="evolution",
                )
            )
        )
    return cases


# ----------------------------------------------------------- M19: the Digital Operator


def _operator_app_open_cases() -> list[UtteranceCase]:
    cases: list[UtteranceCase] = []
    for case_id, text, source in (
        ("op.app.1", "Not Defteri'ni aç.", "canonical"),
        ("op.app.2", "Not defterini aç.", "paraphrase"),
        ("op.app.3", "Chrome'u aç.", "canonical"),
        ("op.app.4", "Chrome aç.", "paraphrase"),
        ("op.app.5", "Tarayıcıyı aç.", "canonical"),
        ("op.app.6", "Google Chrome'u açar mısın?", "paraphrase"),
        ("op.app.7", "Tarayıcıyı bi aç.", "paraphrase"),
        ("op.app.8", "Hesap makinesini aç.", "canonical"),
        ("op.app.9", "PowerShell aç.", "canonical"),
        ("op.app.10", "PowerShell'i aç.", "paraphrase"),
        ("op.app.11", "Dosya gezginini aç.", "paraphrase"),
        ("op.app.12", "Microsoft Edge'i aç.", "paraphrase"),
        ("op.app.13", "Hesap makinesi aç.", "paraphrase"),
    ):
        cases.extend(
            _with_variants(
                UtteranceCase(
                    case_id=case_id,
                    utterance=text,
                    expected_intent="app_open",
                    expected_tool="operator.app_open",
                    side_effects=SIDE_EFFECTS_OPERATOR_APP_OPEN,
                    category="operator",
                    source=source,
                )
            )
        )
    # A name outside the allowlist is refused, naming it — never a guess at a path.
    cases.append(
        UtteranceCase(
            case_id="op.app.unknown",
            utterance="Winamp'ı aç.",
            # The router only classifies APP_OPEN on a recognised alias (module docstring
            # of app.operator.plans's allowlist) - an unknown name is left to the model to
            # route by its own understanding, exactly like every other free-form tool
            # choice this persona makes; the refusal this case proves is the TOOL's own.
            expected_intent=None,
            expected_tool="operator.app_open",
            expected_response=RESPONSE_REFUSED,
            expected={"error_class": "unknown_application"},
            side_effects=SIDE_EFFECTS_NONE,
            category="operator",
            source="canonical",
        )
    )
    return cases


def _operator_window_cases() -> list[UtteranceCase]:
    cases: list[UtteranceCase] = []
    for case_id, text, action, side_effects, source in (
        ("op.win.close.1", "Bunu kapat.", "close", SIDE_EFFECTS_OPERATOR_WINDOW_CLOSE, "canonical"),
        (
            "op.win.close.2",
            "Şu pencereyi kapatsana.",
            "close",
            SIDE_EFFECTS_OPERATOR_WINDOW_CLOSE,
            "paraphrase",
        ),
        (
            "op.win.close.3",
            "Öndeki pencereyi kapat.",
            "close",
            SIDE_EFFECTS_OPERATOR_WINDOW_CLOSE,
            "paraphrase",
        ),
        (
            "op.win.max.1",
            "Pencereyi büyüt.",
            "maximize",
            SIDE_EFFECTS_OPERATOR_WINDOW_MAXIMIZE,
            "canonical",
        ),
        (
            "op.win.min.1",
            "Bu pencereyi küçült.",
            "minimize",
            SIDE_EFFECTS_OPERATOR_WINDOW_MINIMIZE,
            "canonical",
        ),
        (
            "op.win.restore.1",
            "Pencereyi eski haline getir.",
            "restore",
            SIDE_EFFECTS_OPERATOR_WINDOW_RESTORE,
            "canonical",
        ),
        (
            "op.win.restore.2",
            "Pencereyi geri yükle.",
            "restore",
            SIDE_EFFECTS_OPERATOR_WINDOW_RESTORE,
            "paraphrase",
        ),
        (
            "op.win.prev.1",
            "Önceki pencereye dön.",
            "previous",
            SIDE_EFFECTS_OPERATOR_WINDOW_PREVIOUS,
            "canonical",
        ),
        (
            "op.win.prev.2",
            "Bir önceki pencereye geç.",
            "previous",
            SIDE_EFFECTS_OPERATOR_WINDOW_PREVIOUS,
            "paraphrase",
        ),
    ):
        intent = {
            "close": "window_close",
            "maximize": "window_maximize",
            "minimize": "window_minimize",
            "restore": "window_restore",
            "previous": "window_previous",
        }[action]
        cases.extend(
            _with_variants(
                UtteranceCase(
                    case_id=case_id,
                    utterance=text,
                    expected_intent=intent,
                    expected_tool="operator.window_control",
                    side_effects=side_effects,
                    context=CTX_WINDOW_FOCUSED,
                    category="operator",
                    source=source,
                )
            )
        )
    # "Bunu kapat" with no window ever focused asks which one, rather than guessing.
    cases.append(
        UtteranceCase(
            case_id="op.win.noclose_context",
            utterance="Bunu kapat.",
            expected_intent="window_close",
            expected_tool="operator.window_control",
            expected_response=RESPONSE_CLARIFY,
            side_effects=SIDE_EFFECTS_NONE,
            context=CTX_NONE,
            category="operator",
            source="regression",
        )
    )
    return cases


def _operator_type_cases() -> list[UtteranceCase]:
    cases: list[UtteranceCase] = []
    for case_id, text, source in (
        ("op.type.1", "Buraya merhaba yaz.", "canonical"),
        ("op.type.2", "Bu kutuya merhaba yaz.", "paraphrase"),
        ("op.type.3", "Seçili yere merhaba yaz.", "paraphrase"),
    ):
        cases.extend(
            _with_variants(
                UtteranceCase(
                    case_id=case_id,
                    utterance=text,
                    expected_intent="type_text",
                    expected_tool="operator.type",
                    side_effects=SIDE_EFFECTS_OPERATOR_TYPE,
                    context=CTX_WINDOW_FOCUSED,
                    category="operator",
                    source=source,
                )
            )
        )
    # No payload at all: a clarification, never a guess at what to type.
    cases.append(
        UtteranceCase(
            case_id="op.type.no_text",
            utterance="Şuraya yazar mısın?",
            expected_intent="type_text",
            expected_tool="operator.type",
            expected_response=RESPONSE_CLARIFY,
            side_effects=SIDE_EFFECTS_NONE,
            context=CTX_WINDOW_FOCUSED,
            category="operator",
            source="canonical",
        )
    )
    # No window focused at all: also a clarification (asked before any device call).
    cases.append(
        UtteranceCase(
            case_id="op.type.no_window",
            utterance="Buraya merhaba yaz.",
            expected_intent="type_text",
            expected_tool="operator.type",
            expected_response=RESPONSE_CLARIFY,
            side_effects=SIDE_EFFECTS_NONE,
            context=CTX_NONE,
            category="operator",
            source="regression",
        )
    )
    # A password-shaped payload is refused outright — never typed, never a device call.
    cases.append(
        UtteranceCase(
            case_id="op.type.secret",
            utterance="Buraya şifremi yaz.",
            expected_intent="type_text",
            expected_tool="operator.type",
            expected_response=RESPONSE_REFUSED,
            expected={"error_class": "secret_refused"},
            side_effects=SIDE_EFFECTS_NONE,
            context=CTX_WINDOW_FOCUSED,
            category="operator",
            source="canonical",
            regression_issue_id="M19 spec §1 invariant 2",
        )
    )
    return cases


def _operator_shell_cases() -> list[UtteranceCase]:
    cases: list[UtteranceCase] = []
    for case_id, text, source in (
        ("op.shell.ip.1", "IP adresimi göster.", "canonical"),
        ("op.shell.ip.2", "IP adresim ne?", "paraphrase"),
        ("op.shell.host.1", "Bilgisayarın adı ne?", "canonical"),
        ("op.shell.host.2", "Bilgisayarımın adı nedir?", "paraphrase"),
    ):
        cases.extend(
            _with_variants(
                UtteranceCase(
                    case_id=case_id,
                    utterance=text,
                    expected_intent="shell_query",
                    expected_tool="operator.shell",
                    side_effects=SIDE_EFFECTS_OPERATOR_SHELL,
                    category="operator",
                    source=source,
                )
            )
        )
    return cases


def _operator_control_cases() -> list[UtteranceCase]:
    cases: list[UtteranceCase] = []
    # "İptal..." is not wrapped in _with_variants: Python's locale-independent str.lower()
    # maps the Turkish capital dotted İ (U+0130) to "i" + a COMBINING DOT ABOVE (U+0307)
    # rather than a plain "i" (turkish_casefold, used everywhere resolve_intent actually
    # normalizes speech, gets this right) - _variants()'s own lowercasing does not, so its
    # generated ASR-noise variant carries an invisible extra codepoint no token match ever
    # sees. Narrow, case-specific: kept out of the shared helper rather than changing
    # behaviour every existing corpus case already relies on.
    for case_id, text, source in (
        ("op.cancel.1", "İptal et.", "canonical"),
        ("op.cancel.3", "İptal.", "paraphrase"),
    ):
        cases.append(
            UtteranceCase(
                case_id=case_id,
                utterance=text,
                expected_intent="operator_cancel",
                expected_tool="operator.cancel",
                side_effects=SIDE_EFFECTS_NONE,
                context=CTX_OPERATOR_RUNNING,
                category="operator",
                source=source,
            )
        )
    for case_id, text, source in (("op.cancel.2", "Dur.", "canonical"),):
        cases.extend(
            _with_variants(
                UtteranceCase(
                    case_id=case_id,
                    utterance=text,
                    expected_intent="operator_cancel",
                    expected_tool="operator.cancel",
                    side_effects=SIDE_EFFECTS_NONE,
                    context=CTX_OPERATOR_RUNNING,
                    category="operator",
                    source=source,
                )
            )
        )
    cases.append(
        UtteranceCase(
            case_id="op.status.1",
            utterance="Ne yapıyorsun?",
            expected_intent="operator_status",
            expected_tool="operator.status",
            side_effects=SIDE_EFFECTS_NONE,
            context=CTX_OPERATOR_RUNNING,
            category="operator",
            source="canonical",
        )
    )
    # The SAME words with no task running keep their ordinary meaning (control-class
    # STOP) — the gate is on live state, never on vocabulary alone.
    cases.append(
        UtteranceCase(
            case_id="op.cancel.not_running",
            utterance="Dur.",
            expected_intent="stop",
            expected_tool=None,
            expected_response=RESPONSE_CONTROL,
            side_effects=SIDE_EFFECTS_NONE,
            context=CTX_NONE,
            category="operator",
            source="regression",
        )
    )
    return cases


def _operator_cases() -> list[UtteranceCase]:
    return [
        *_operator_app_open_cases(),
        *_operator_window_cases(),
        *_operator_type_cases(),
        *_operator_shell_cases(),
        *_operator_control_cases(),
    ]


# --------------------------------------------------- M20: File & Document Intelligence


def _document_search_cases() -> list[UtteranceCase]:
    cases: list[UtteranceCase] = []
    for case_id, text, source in (
        ("doc.search.1", "Bu klasördeki PDF'leri bul.", "canonical"),
        ("doc.search.2", "Masaüstündeki sözleşme dosyasını bul.", "canonical"),
        ("doc.search.3", "İndirilenler'de bütçe dosyasını ara.", "canonical"),
        ("doc.search.4", "Masaüstünde sözleşme dosyasını bulur musun?", "paraphrase"),
        ("doc.search.5", "İndirilenler klasöründe bütçe dosyasını arar mısın?", "paraphrase"),
        ("doc.search.6", "Bu klasördeki PDF dosyalarını bulsana.", "paraphrase"),
        ("doc.search.7", "masaüstündeki sözleşme dosyasını bul", "asr_noise"),
        ("doc.search.8", "indirilenlerde butce dosyasini ara", "asr_noise"),
    ):
        cases.extend(
            _with_variants(
                UtteranceCase(
                    case_id=case_id,
                    utterance=text,
                    expected_intent="file_search",
                    expected_tool="file.search",
                    side_effects=SIDE_EFFECTS_DOCUMENTS_SEARCH,
                    context=CTX_NONE,
                    category="documents",
                    source=source,
                )
            )
        )
    return cases


def _document_read_cases() -> list[UtteranceCase]:
    cases: list[UtteranceCase] = []
    for case_id, text, source in (
        ("doc.read.1", "Bu dosyayı oku.", "canonical"),
        ("doc.read.2", "Bu belgeyi okur musun?", "paraphrase"),
        ("doc.read.3", "Dosyayı okusana.", "paraphrase"),
        ("doc.read.4", "bu dosyayı oku", "asr_noise"),
    ):
        cases.extend(
            _with_variants(
                UtteranceCase(
                    case_id=case_id,
                    utterance=text,
                    expected_intent="document_read",
                    expected_tool="document.read",
                    side_effects=SIDE_EFFECTS_DOCUMENTS_READ,
                    context=CTX_FILE_FOCUSED,
                    category="documents",
                    source=source,
                )
            )
        )
    # Already indexed (the current document IS the file): a re-read never re-extracts.
    cases.append(
        UtteranceCase(
            case_id="doc.read.cached",
            utterance="Bu belgeyi tekrar oku.",
            expected_intent="document_read",
            expected_tool="document.read",
            side_effects=SIDE_EFFECTS_NONE,
            context=CTX_DOCUMENT_FOCUSED,
            category="documents",
            source="paraphrase",
        )
    )
    return cases


def _document_summarize_cases() -> list[UtteranceCase]:
    cases: list[UtteranceCase] = []
    for case_id, text, source in (
        ("doc.summ.1", "Bunu özetle.", "canonical"),
        ("doc.summ.2", "Bu belgeyi özetle.", "canonical"),
        ("doc.summ.3", "Bu PDF'i özetle.", "canonical"),
        ("doc.summ.4", "Bu belgeyi özetler misin?", "paraphrase"),
        ("doc.summ.5", "Bu dosyayı kısaca özetle.", "paraphrase"),
        ("doc.summ.6", "bu belgeyi özetle", "asr_noise"),
        ("doc.summ.7", "bu pdfi ozetle", "asr_noise"),
    ):
        cases.extend(
            _with_variants(
                UtteranceCase(
                    case_id=case_id,
                    utterance=text,
                    expected_intent="document_summarize",
                    expected_tool="document.summarize",
                    side_effects=SIDE_EFFECTS_NONE,
                    context=CTX_DOCUMENT_FOCUSED,
                    category="documents",
                    source=source,
                )
            )
        )
    return cases


def _document_compare_cases() -> list[UtteranceCase]:
    cases: list[UtteranceCase] = []
    for case_id, text, source in (
        ("doc.cmp.1", "Bir önceki belgeyle karşılaştır.", "canonical"),
        ("doc.cmp.2", "Önceki dosyayla karşılaştır.", "canonical"),
        ("doc.cmp.3", "Bunu bir önceki belgeyle karşılaştırır mısın?", "paraphrase"),
        ("doc.cmp.4", "bir önceki belgeyle karşılaştır", "asr_noise"),
    ):
        cases.extend(
            _with_variants(
                UtteranceCase(
                    case_id=case_id,
                    utterance=text,
                    expected_intent="document_compare",
                    expected_tool="document.compare",
                    side_effects=SIDE_EFFECTS_DOCUMENTS_COMPARE,
                    context=CTX_DOCUMENT_FOCUSED,
                    category="documents",
                    source=source,
                )
            )
        )
    return cases


def _document_inspect_cases() -> list[UtteranceCase]:
    cases: list[UtteranceCase] = []
    for case_id, text, source, context in (
        ("doc.insp.1", "Bu Excel'de ne var?", "canonical", CTX_XLSX_FOCUSED),
        ("doc.insp.2", "Bu sunumda kaç slayt var?", "canonical", CTX_PPTX_FOCUSED),
        ("doc.insp.3", "Bu tabloda ne var?", "paraphrase", CTX_XLSX_FOCUSED),
        ("doc.insp.4", "Sunumda kaç slayt var?", "paraphrase", CTX_PPTX_FOCUSED),
        ("doc.insp.5", "bu excelde ne var", "asr_noise", CTX_XLSX_FOCUSED),
    ):
        cases.extend(
            _with_variants(
                UtteranceCase(
                    case_id=case_id,
                    utterance=text,
                    expected_intent="document_inspect",
                    expected_tool="document.inspect",
                    side_effects=SIDE_EFFECTS_NONE,
                    context=context,
                    category="documents",
                    source=source,
                )
            )
        )
    return cases


def _document_answer_cases() -> list[UtteranceCase]:
    cases: list[UtteranceCase] = []
    for case_id, text, source, context in (
        ("doc.ans.1", "Üçüncü sayfada ne yazıyor?", "canonical", CTX_DOCUMENT_FOCUSED),
        ("doc.ans.2", "Ödeme süresi kaç gün?", "canonical", CTX_DOCX_FOCUSED),
        ("doc.ans.3", "Ödeme süresi ne kadar?", "paraphrase", CTX_DOCX_FOCUSED),
        ("doc.ans.4", "Üçüncü sayfa ne diyor?", "paraphrase", CTX_DOCUMENT_FOCUSED),
        ("doc.ans.5", "üçüncü sayfada ne yazıyor", "asr_noise", CTX_DOCUMENT_FOCUSED),
        ("doc.ans.6", "odeme suresi kac gun", "asr_noise", CTX_DOCX_FOCUSED),
    ):
        cases.extend(
            _with_variants(
                UtteranceCase(
                    case_id=case_id,
                    utterance=text,
                    expected_intent="document_answer",
                    expected_tool="document.answer",
                    side_effects=SIDE_EFFECTS_NONE,
                    context=context,
                    category="documents",
                    source=source,
                )
            )
        )
    return cases


def _document_common_points_cases() -> list[UtteranceCase]:
    cases: list[UtteranceCase] = []
    for case_id, text, source in (
        ("doc.common.1", "Bunların ortak noktalarını çıkar.", "canonical"),
        ("doc.common.2", "Ortak noktaları söyler misin?", "paraphrase"),
        ("doc.common.3", "bunların ortak noktalarını çıkar", "asr_noise"),
    ):
        cases.extend(
            _with_variants(
                UtteranceCase(
                    case_id=case_id,
                    utterance=text,
                    expected_intent="document_common_points",
                    expected_tool="document.common_points",
                    side_effects=SIDE_EFFECTS_NONE,
                    context=CTX_COMMON_POINTS_FOCUSED,
                    category="documents",
                    source=source,
                )
            )
        )
    return cases


def _document_previous_cases() -> list[UtteranceCase]:
    cases: list[UtteranceCase] = []
    for case_id, text, source in (
        ("doc.prev.1", "Az önceki sunuma geri dön.", "canonical"),
        ("doc.prev.2", "Bir önceki belgeye dön.", "canonical"),
        ("doc.prev.3", "Önceki belgeye geçer misin?", "paraphrase"),
        ("doc.prev.4", "az önceki sunuma geri dön", "asr_noise"),
    ):
        cases.extend(
            _with_variants(
                UtteranceCase(
                    case_id=case_id,
                    utterance=text,
                    expected_intent="document_previous",
                    expected_tool="document.previous",
                    side_effects=SIDE_EFFECTS_NONE,
                    context=CTX_DOCUMENT_FOCUSED,
                    category="documents",
                    source=source,
                )
            )
        )
    return cases


def _document_negative_cases() -> list[UtteranceCase]:
    cases: list[UtteranceCase] = []
    # No document focused at all: DOCUMENT_SUMMARIZE still resolves (the noun says so),
    # and the SERVICE — not a guess — asks which one, exactly like the operator family's
    # own "no window focused" clarifications.
    cases.extend(
        _with_variants(
            UtteranceCase(
                case_id="doc.neg.no_focus",
                utterance="Bu belgeyi özetle.",
                expected_intent="document_summarize",
                expected_tool="document.summarize",
                expected_response=RESPONSE_CLARIFY,
                side_effects=SIDE_EFFECTS_NONE,
                context=CTX_NONE,
                category="documents",
                source="regression",
            )
        )
    )
    # "Bu dosyayı sil." reaches no tool and no device capability at all — there is no
    # delete/move/write tool in M20 (ADR-0083 decision 7); the router resolves nothing.
    cases.extend(
        _with_variants(
            UtteranceCase(
                case_id="doc.neg.delete",
                utterance="Bu dosyayı sil.",
                expected_intent="none",
                expected_tool=None,
                expected_response=RESPONSE_NONE,
                side_effects=SIDE_EFFECTS_NONE,
                context=CTX_FILE_FOCUSED,
                category="documents",
                source="canonical",
                regression_issue_id="ADR-0083 decision 7",
            )
        )
    )
    # A secret-bearing path: the tool IS called, and the fake device refuses honestly
    # (spec §2's confinement rule) — no content leaked, no guess at what the file holds.
    cases.extend(
        _with_variants(
            UtteranceCase(
                case_id="doc.neg.secret",
                utterance="Şifre dosyamı oku.",
                expected_intent="document_read",
                expected_tool="document.read",
                expected_response=RESPONSE_REFUSED,
                expected={"error_class": "permission_denied"},
                side_effects=SIDE_EFFECTS_DOCUMENTS_READ,
                context=CTX_SECRET_FILE_FOCUSED,
                category="documents",
                source="canonical",
                regression_issue_id="M20 spec §2 confinement",
            )
        )
    )
    return cases


def _documents_cases() -> list[UtteranceCase]:
    return [
        *_document_search_cases(),
        *_document_read_cases(),
        *_document_summarize_cases(),
        *_document_compare_cases(),
        *_document_inspect_cases(),
        *_document_answer_cases(),
        *_document_common_points_cases(),
        *_document_previous_cases(),
        *_document_negative_cases(),
    ]


def all_cases() -> list[UtteranceCase]:
    cases = [
        *_research_cases(),
        *_alarm_create_cases(),
        *_alarm_control_cases(),
        *_display_cases(),
        *_eye_cases(),
        *_control_cases(),
        *_evolution_cases(),
        *_operator_cases(),
        *_documents_cases(),
    ]
    ids = [c.case_id for c in cases]
    assert len(ids) == len(set(ids)), "duplicate case ids"
    return cases


__all__ = [
    "CORPUS_VERSION",
    "CTX_ALARM_RINGING",
    "CTX_ALARM_SCHEDULED",
    "CTX_EYE_DISABLED",
    "CTX_NONE",
    "CTX_OPERATOR_RUNNING",
    "CTX_RESEARCH_FOCUS_B",
    "CTX_WINDOW_FOCUSED",
    "SIDE_EFFECTS_OPERATOR_APP_OPEN",
    "SIDE_EFFECTS_OPERATOR_SHELL",
    "SIDE_EFFECTS_OPERATOR_TYPE",
    "SIDE_EFFECTS_OPERATOR_WINDOW_CLOSE",
    "SIDE_EFFECTS_OPERATOR_WINDOW_MAXIMIZE",
    "SIDE_EFFECTS_OPERATOR_WINDOW_MINIMIZE",
    "SIDE_EFFECTS_OPERATOR_WINDOW_PREVIOUS",
    "SIDE_EFFECTS_OPERATOR_WINDOW_RESTORE",
    "RESPONSE_CLARIFY",
    "RESPONSE_CONTROL",
    "RESPONSE_NONE",
    "RESPONSE_OK",
    "RESPONSE_REFUSED",
    "RESPONSE_RUNNING",
    "UtteranceCase",
    "all_cases",
]
