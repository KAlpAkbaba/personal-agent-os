"""B26 req 739/748: the routing measurement set, and the negatives that make it a test.

The audit measured 103 plausible Turkish sentences against the deterministic router and
found **59 that reached nothing** and **7 that reached the WRONG capability**. The second
number is the dangerous one and it is what this batch closes: an utterance that reaches
nothing leaves the owner to say it differently, while an utterance that reaches the wrong
capability *acts*. "Otomatik güncellemeleri kapat." turned the screen automation off.

So every case here carries two fields, and they are not the same claim:

``expected``
    The intent this sentence MUST resolve to, when the product has a capability for it.
    ``None`` means "this batch does not require it to route at all" — the ten sentences
    requirements 726-735 are about belong to B27, and pretending otherwise here would
    either fail for the wrong reason or quietly claim B27's work.

``forbidden``
    The intents that would be WRONG for this sentence. This is the half that makes the set
    a safety net rather than a wish list: a sentence with ``expected=None`` still fails if
    it reaches something destructive, which is exactly the class of defect the audit found.

A misroute, counted by ``test_intent_misroutes.py``, is a resolution that lands in
``forbidden`` — or, where ``expected`` is set, anything other than ``expected``.

The critical verbs the test plan names (*dur, gönder, işle, dağıt, kapat*) each appear here
in BOTH shapes: the sentence where the verb means what it usually means, and the sentence
where the same letters mean something else. That pairing is the point. Turkish builds words
by suffix, so `yaz` (write) is the first three letters of `yazdır` (print) and a prefix
match on the verb turns "Bunu yazdır." into a request to type into whatever window happens
to be focused.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Final

from app.voice.intents import Intent

#: Where a case came from. ``measured`` are the seven the audit actually observed on the
#: owner's own system; they may never be removed.
SOURCE_MEASURED: Final = "measured"
SOURCE_SHIELD: Final = "shield"  # must keep working — the deterministic router's job (741)
SOURCE_CRITICAL: Final = "critical_verb"  # dur / gönder / işle / dağıt / kapat
SOURCE_UNROUTED: Final = "unrouted"  # B27's ten, here only for their forbidden set
SOURCE_PLAUSIBLE: Final = "plausible"  # ordinary owner sentences, for coverage


@dataclass(frozen=True, slots=True)
class RouteCase:
    utterance: str
    #: What it must resolve to, or None when this batch does not require a route.
    expected: Intent | None
    #: What it must NOT resolve to. Never empty: a case with nothing forbidden asserts
    #: nothing about safety and would pass for any router at all.
    forbidden: tuple[Intent, ...]
    why: str
    source: str = SOURCE_PLAUSIBLE
    #: Extra `resolve_intent` state, for the sentences whose meaning depends on it.
    state: dict[str, Any] = field(default_factory=dict)


#: Intents that ACT on the world outside the conversation. A sentence that reaches one of
#: these by accident does something the owner did not ask for, which is the difference
#: between "reached nothing" and "misrouted".
ACTING_INTENTS: Final[tuple[Intent, ...]] = (
    Intent.MAIL_SEND,
    Intent.CALENDAR_COMMIT,
    Intent.CALENDAR_PROPOSE,
    Intent.TYPE_TEXT,
    Intent.AMBIENT_POLICY_SET,
    Intent.DISPLAY_OFF,
    Intent.EYE_ENABLE,
    Intent.DEPLOY,
    Intent.RELEASE_ROLLBACK,
    Intent.MEMORY_FORGET,
    Intent.ALARM_CANCEL,
    Intent.ROUTINE_CANCEL,
    Intent.WINDOW_CLOSE,
    Intent.EXEC_CANCEL,
    # B27 (726-735): the three of the new intents that act - the same three
    # ``app.voice.route_telemetry.ACTING_INTENTS`` added, and test_intent_daily_coverage
    # reads that side to keep the two lists agreeing.
    Intent.RESEARCH_CANCEL,
    Intent.CALENDAR_CANCEL,
    Intent.SCREENSHOT_CAPTURE,
    # B30 (82, 120, 122): the three that change the machine.
    Intent.APP_CLOSE,
    Intent.PROCESS_STOP,
    Intent.SERVICE_RESTART,
    # B31 (203, 204).
    Intent.RESEARCH_PAUSE,
    Intent.RESEARCH_RESUME,
    # B32 (150).
    Intent.DOCUMENT_DEDUP,
    # B33 (469).
    Intent.NATIVE_UNINSTALL,
    # B39 (127-130).
    Intent.MISSION_START,
    # B34 (153-160).
    Intent.DOCUMENT_WRITE,
    Intent.DOCUMENT_APPEND,
    Intent.DOCUMENT_APPLY,
    Intent.DOCUMENT_UNDO,
    Intent.DOCUMENT_MOVE,
    Intent.DOCUMENT_DELETE,
)


# ---------------------------------------------------------------- the seven measured

#: The audit's own words, reproduced on this machine before anything was changed. Each one
#: was observed resolving to the intent in ``forbidden``. This tuple may only grow.
MEASURED_MISROUTES: Final[tuple[RouteCase, ...]] = (
    RouteCase(
        "Otomatik güncellemeleri kapat.",
        expected=None,
        forbidden=(Intent.AMBIENT_POLICY_SET, Intent.DISPLAY_OFF),
        why="The most dangerous of the seven: the owner asked about software updates and "
        "the screen automation was switched off. The router admitted the sentence on the "
        "bare word 'otomatik', with no screen anywhere in it.",
        source=SOURCE_MEASURED,
    ),
    RouteCase(
        "Otomatik yedeklemeyi kapat.",
        expected=None,
        forbidden=(Intent.AMBIENT_POLICY_SET, Intent.DISPLAY_OFF),
        why="Same root: backups are not screens.",
        source=SOURCE_MEASURED,
    ),
    RouteCase(
        "Otomatik kaydetmeyi kapat.",
        expected=None,
        forbidden=(Intent.AMBIENT_POLICY_SET, Intent.DISPLAY_OFF),
        why="Same root: autosave is not a screen either.",
        source=SOURCE_MEASURED,
    ),
    RouteCase(
        "Dosyayı gönder.",
        expected=None,
        forbidden=(Intent.MAIL_SEND,),
        why="'Gönder.' alone is the confirmation of a draft that was just read back. With "
        "a FILE named, the sentence is about the file — and sending mail is the one "
        "action in this family that cannot be taken back.",
        source=SOURCE_MEASURED,
    ),
    RouteCase(
        "Bu dosyayı bana gönder.",
        expected=None,
        forbidden=(Intent.MAIL_SEND,),
        why="The same defect in the shape an owner is most likely to say.",
        source=SOURCE_MEASURED,
    ),
    RouteCase(
        "Bunu yazdır.",
        expected=None,
        forbidden=(Intent.TYPE_TEXT,),
        why="Turkish morphology: `yazdır` (print) begins with `yaz` (write), and a prefix "
        "match on the verb turned a request to PRINT into typing into whatever window "
        "happened to be focused.",
        source=SOURCE_MEASURED,
    ),
    RouteCase(
        "Bir hedef ekle: bu ay kitabı bitir.",
        expected=None,
        forbidden=(Intent.CALENDAR_PROPOSE, Intent.CALENDAR_COMMIT),
        why="'ekle' was enough on its own to mean a calendar event. A goal is not an "
        "appointment, and the owner's month-long intention became a dated entry.",
        source=SOURCE_MEASURED,
    ),
    RouteCase(
        "Yanlış yere tıkladım.",
        expected=None,
        forbidden=(Intent.MISSION_START,),
        why="Owner, 2026-09-19: a remark about the last click became a new mission that "
        "searched the screen for 'Yanlış yere'. The planner matched the stem `tıkla` inside "
        "the past tense `tıkladım`.",
        source=SOURCE_MEASURED,
    ),
)


# ------------------------------------------------ what must keep working (req 741)

#: The deterministic router stays the safety shield. Every fix above is a narrowing, and a
#: narrowing is exactly the change that can take a working sentence with it — so each one
#: is paired here with the sentences it must not have touched.
SHIELD: Final[tuple[RouteCase, ...]] = (
    RouteCase(
        "Uyurken ekranları kapat.",
        Intent.AMBIENT_POLICY_SET,
        (Intent.DISPLAY_OFF,),
        "The standing preference, one word away from the command.",
        SOURCE_SHIELD,
    ),
    RouteCase(
        "Ben yokken ekranları kapat.",
        Intent.AMBIENT_POLICY_SET,
        (Intent.DISPLAY_OFF,),
        "The other half of the same preference.",
        SOURCE_SHIELD,
    ),
    RouteCase(
        "Ekran kapanma süresini 5 dakika yap.",
        Intent.AMBIENT_POLICY_SET,
        (Intent.DISPLAY_OFF,),
        "The owner's own sentence from 2026-09-10 (ADR-0108).",
        SOURCE_SHIELD,
    ),
    RouteCase(
        "Ekranları otomatik kapatmayı aç.",
        Intent.AMBIENT_POLICY_SET,
        (Intent.DISPLAY_OFF,),
        "'otomatik' with a screen in the sentence is this family's own phrase, and the "
        "narrowing must not have cost it.",
        SOURCE_SHIELD,
    ),
    RouteCase(
        "Ekranları kapat.",
        Intent.DISPLAY_OFF,
        (Intent.AMBIENT_POLICY_SET,),
        "The command for right now, not the preference.",
        SOURCE_SHIELD,
    ),
    RouteCase(
        "Gönder.",
        Intent.MAIL_SEND,
        (Intent.DISCARD,),
        "The bare confirmation of a draft just read back — the shape the narrowing keeps.",
        SOURCE_SHIELD,
    ),
    RouteCase(
        "Gönder lütfen.",
        Intent.MAIL_SEND,
        (Intent.DISCARD,),
        "Politeness is not an object; the sentence is still bare.",
        SOURCE_SHIELD,
    ),
    RouteCase(
        "Bunu gönder.",
        Intent.MAIL_SEND,
        (Intent.DISCARD,),
        "A pointer at the thing just read back is not a foreign object.",
        SOURCE_SHIELD,
    ),
    RouteCase(
        "Gönderme.",
        Intent.DISCARD,
        (Intent.MAIL_SEND,),
        "The negation still outranks the verb — the one direction that must never flip.",
        SOURCE_SHIELD,
    ),
    RouteCase(
        "Buraya merhaba yaz.",
        Intent.TYPE_TEXT,
        (),
        "The operator's own sentence, with the target named.",
        SOURCE_SHIELD,
    ),
    RouteCase(
        "Bu kutuya adımı yaz.",
        Intent.TYPE_TEXT,
        (),
        "The same with the other target phrase.",
        SOURCE_SHIELD,
    ),
    RouteCase(
        "Perşembe 15'e diş hekimi ekle.",
        Intent.CALENDAR_PROPOSE,
        (),
        "The calendar's own sentence: a day and a time anchor it.",
        SOURCE_SHIELD,
    ),
    RouteCase(
        "Yarın 10'a toplantı ekle.",
        Intent.CALENDAR_PROPOSE,
        (),
        "A relative day and a calendar noun.",
        SOURCE_SHIELD,
    ),
    RouteCase(
        "Dur.",
        Intent.STOP,
        (),
        "The word that wins from any state (spec §5). Nothing in this batch may touch it.",
        SOURCE_SHIELD,
    ),
    RouteCase(
        "Gözünü kapat.",
        Intent.EYE_DISABLE,
        (Intent.DISPLAY_OFF, Intent.AMBIENT_POLICY_SET),
        "The privacy-critical stop phrase, checked before everything else.",
        SOURCE_SHIELD,
    ),
    RouteCase(
        "Bunu hatırla.",
        Intent.MEMORY_REMEMBER,
        (Intent.MEMORY_FORGET,),
        "B16's family: the one direction that must never invert.",
        SOURCE_SHIELD,
    ),
    RouteCase(
        "Bunu unut.",
        Intent.MEMORY_FORGET,
        (Intent.MEMORY_REMEMBER,),
        "The hard delete, and the sentence that must reach it rather than its opposite.",
        SOURCE_SHIELD,
    ),
    RouteCase(
        "Sabah rutinini durdur.",
        Intent.ROUTINE_PAUSE,
        (Intent.ALARM_STOP, Intent.ALARM_CANCEL, Intent.STOP),
        "B14: the noun decides. Without it, tomorrow's alarm is silenced instead.",
        SOURCE_SHIELD,
    ),
    RouteCase(
        "Saat kaç?",
        Intent.CLOCK_QUERY,
        (Intent.ALARM_QUERY,),
        "B15 req 271 closed this; the measurement set keeps it closed.",
        SOURCE_SHIELD,
    ),
    RouteCase(
        "Sabah alarmım kaçta?",
        Intent.ALARM_QUERY,
        (Intent.CLOCK_QUERY,),
        "The question about an alarm, not about the clock — the pair that keeps both honest.",
        SOURCE_SHIELD,
    ),
)


# ------------------------------------------- the critical verbs, in both meanings

#: *dur, gönder, işle, dağıt, kapat* — each in the shape where it means what it usually
#: means, and in the shape where the same letters mean something else. A router that reads
#: verbs by prefix gets the second shape wrong, and it gets it wrong ACTING.
CRITICAL_VERBS: Final[tuple[RouteCase, ...]] = (
    RouteCase(
        "Durum raporunu oku.",
        expected=None,
        forbidden=(Intent.STOP,),
        why="`durum` (situation) starts with `dur` (stop). Reading a status report must "
        "not stop the narration.",
        source=SOURCE_CRITICAL,
    ),
    RouteCase(
        "Duruşmayı takvime ekle.",
        expected=Intent.CALENDAR_PROPOSE,
        forbidden=(Intent.STOP,),
        why="`duruşma` (hearing) starts with `dur` too, and this sentence genuinely is a "
        "calendar one.",
        source=SOURCE_CRITICAL,
    ),
    RouteCase(
        "Gönderileri oku.",
        expected=None,
        forbidden=(Intent.MAIL_SEND,),
        why="`gönderi` (post/item) starts with `gönder`. Reading posts must not send mail.",
        source=SOURCE_CRITICAL,
    ),
    RouteCase(
        "Bu belgeyi gönder.",
        expected=None,
        forbidden=(Intent.MAIL_SEND,),
        why="A document named is a foreign object: this is not the draft confirmation.",
        source=SOURCE_CRITICAL,
    ),
    RouteCase(
        "Bunu işle.",
        expected=None,
        forbidden=ACTING_INTENTS,
        why="`işle` (process) is a verb with no capability behind it. Reaching ANY acting "
        "intent here would be acting on a sentence nobody implemented.",
        source=SOURCE_CRITICAL,
    ),
    RouteCase(
        "İşlemi iptal et.",
        expected=None,
        forbidden=(Intent.MEMORY_FORGET, Intent.ALARM_CANCEL, Intent.ROUTINE_CANCEL),
        why="`işlem` (operation) — an ambiguous cancel must not pick a family by accident.",
        source=SOURCE_CRITICAL,
    ),
    RouteCase(
        "Dosyaları dağıt.",
        expected=None,
        forbidden=ACTING_INTENTS,
        why="`dağıt` (distribute) has no capability. Nothing may act on it.",
        source=SOURCE_CRITICAL,
    ),
    RouteCase(
        "Uygulamayı kapat.",
        expected=None,
        forbidden=(Intent.DISPLAY_OFF, Intent.AMBIENT_POLICY_SET, Intent.EYE_DISABLE),
        why="`kapat` is the most overloaded verb in the product: windows, screens, the "
        "camera and a policy all use it, and `uygulama` means both a desktop window and a "
        "project the App Factory made. This batch does not decide which of the two the "
        "owner meant — it decides that the answer is never a screen or the camera.",
        source=SOURCE_CRITICAL,
    ),
    RouteCase(
        "Bildirimleri kapat.",
        expected=None,
        forbidden=(Intent.DISPLAY_OFF, Intent.AMBIENT_POLICY_SET, Intent.EYE_DISABLE),
        why="Notifications are not screens, not the camera, and not a window.",
        source=SOURCE_CRITICAL,
    ),
    RouteCase(
        "Sesi kapat.",
        expected=Intent.MEDIA_VOLUME,
        forbidden=(Intent.DISPLAY_OFF, Intent.AMBIENT_POLICY_SET, Intent.EYE_DISABLE),
        why="Muting is req 733 (B27): the media's volume, never a screen or the eye.",
        source=SOURCE_CRITICAL,
    ),
)


# ----------------------------------------- the ten that do not route yet (B27's)

#: Requirements 726-735. They are here for their FORBIDDEN sets only: this batch does not
#: make them route, and a case that claimed otherwise would be claiming B27's work.
#: B26 wrote these with ``expected=None`` - present only for their FORBIDDEN sets. B27
#: (req 726-735) routes them, and the same seven cases now carry the intent each must
#: reach, with every forbidden set kept exactly as it was: routing "Sesini kıs." to the
#: volume is the fix; routing it to a screen would still be the defect.
UNROUTED: Final[tuple[RouteCase, ...]] = (
    RouteCase(
        "Bana hatırlat: yarın süt al.",
        None,
        (Intent.MEMORY_FORGET,),
        "req 727 family — a reminder is not a memory write (B27 does not claim it)",
        SOURCE_UNROUTED,
    ),
    RouteCase(
        "Maillerime bak.", Intent.MAIL_INBOX, (Intent.MAIL_SEND,), "req 729", SOURCE_UNROUTED
    ),
    RouteCase(
        "Bu hafta ne var?",
        Intent.CALENDAR_AGENDA,
        (Intent.CALENDAR_PROPOSE,),
        "req 730",
        SOURCE_UNROUTED,
    ),
    RouteCase(
        "Perşembeki toplantıyı iptal et.",
        Intent.CALENDAR_CANCEL,
        (Intent.CALENDAR_PROPOSE, Intent.ALARM_CANCEL, Intent.ROUTINE_CANCEL),
        "req 731 — and above all it must not CREATE anything",
        SOURCE_UNROUTED,
    ),
    RouteCase(
        "Sesini kıs.",
        Intent.MEDIA_VOLUME,
        tuple(intent for intent in ACTING_INTENTS if intent is not Intent.MEDIA_VOLUME),
        "req 733",
        SOURCE_UNROUTED,
    ),
    RouteCase(
        "Neler yapabilirsin?",
        Intent.CAPABILITIES_QUERY,
        ACTING_INTENTS,
        "req 734 (B25 built the tool)",
        SOURCE_UNROUTED,
    ),
    RouteCase(
        "Ekran görüntüsü al.",
        Intent.SCREENSHOT_CAPTURE,
        (Intent.DISPLAY_OFF, Intent.AMBIENT_POLICY_SET),
        "req 735",
        SOURCE_UNROUTED,
    ),
)


# --------------------------------------------------- ordinary owner sentences


def _plain(
    utterance: str,
    expected: Intent | None,
    why: str,
    *,
    forbidden: tuple[Intent, ...] = (),
    state: dict[str, Any] | None = None,
) -> RouteCase:
    """An ordinary owner sentence, with everything DESTRUCTIVE forbidden by default.

    The default forbidden set is every acting intent except the one this sentence is
    supposed to reach — a case that forbade its own expectation would fail for every
    router, including a correct one, which is how a corpus stops being read.
    """
    default = tuple(intent for intent in ACTING_INTENTS if intent is not expected)
    return RouteCase(utterance, expected, forbidden or default, why, SOURCE_PLAUSIBLE, state or {})


#: Ordinary sentences across every family the product has, each one verified against the
#: router before it was written down. They are the bulk of the 103 and they do two jobs:
#: they are the regression surface for every narrowing this batch makes, and they are what
#: makes "misroute count 0" a statement about the product rather than about seven strings.
PLAUSIBLE: Final[tuple[RouteCase, ...]] = (
    # --- narration control, the family the whole resolver began as
    _plain("Biraz daha yavaş oku.", Intent.SLOWER, "narration speed"),
    _plain("Daha hızlı.", Intent.FASTER, "narration speed, bare"),
    _plain("Tekrar oku.", Intent.REPEAT, "narration repeat"),
    _plain("Sonraki maddeye geç.", Intent.NEXT_ITEM, "narration item"),
    _plain("Önceki maddeye dön.", Intent.PREVIOUS_ITEM, "narration item"),
    _plain("Özetle.", Intent.SUMMARIZE, "narration summarise"),
    _plain("Teknik anlat.", Intent.TECHNICAL, "narration register"),
    _plain("İlk maddeye dön.", Intent.FIRST_ITEM, "narration position"),
    _plain("Son maddeye geç.", Intent.LAST_ITEM, "the other end of the same list"),
    _plain("Bunu atla.", Intent.SKIP, "narration skip"),
    # --- alarms and the morning
    _plain("Yarın sabah 07:30'da beni uyandır.", Intent.ALARM_CREATE, "the wake alarm"),
    _plain("Her hafta içi 07:15'te beni uyandır.", Intent.ALARM_CREATE, "a recurring wake"),
    _plain("Alarmım var mı?", Intent.ALARM_QUERY, "alarm query"),
    _plain(
        "Beş dakika ertele.",
        Intent.ALARM_SNOOZE,
        "snooze, with an alarm actually ringing",
        state={"alarm_ringing": True},
    ),
    _plain("Günaydın, bugün ne var?", Intent.MORNING_BRIEFING, "the morning briefing"),
    _plain("Gece boyunca ne yaptın?", Intent.OVERNIGHT_WORK_QUERY, "what happened overnight"),
    # --- screens and the camera
    _plain("Ekranları aç.", Intent.DISPLAY_WAKE, "the other direction of the display command"),
    _plain("Ekran durumu ne?", Intent.DISPLAY_QUERY, "a query, not a command"),
    _plain("Gözünü aç.", Intent.EYE_ENABLE, "the camera, by the owner's own permission"),
    _plain("Beni izleme.", Intent.EYE_DISABLE, "the privacy stop phrase in its other shape"),
    # --- memory
    _plain("Kahveyi sade severim, bunu hatırla.", Intent.MEMORY_REMEMBER, "an explicit write"),
    _plain("Bunu neden hatırlıyorsun?", Intent.MEMORY_WHY, "provenance"),
    _plain("Kahve hakkında ne biliyorsun?", Intent.MEMORY_SEARCH, "retrieval"),
    _plain("Bunu sabitle.", Intent.MEMORY_PIN, "pinning"),
    _plain("Şunu düzelt.", Intent.MEMORY_CORRECT, "correction, not deletion"),
    # --- routines
    _plain("Hangi rutinlerim var?", Intent.ROUTINE_LIST, "routine list"),
    _plain("Sabah rutinini geri aç.", Intent.ROUTINE_RESUME, "routine resume"),
    # --- mail and calendar
    _plain("Ali'ye mail gönder.", Intent.MAIL_DRAFT_NEW, "a fresh compose names the mail noun"),
    _plain("Mailleri oku.", Intent.MAIL_READ, "reading mail"),
    _plain("Öneriyi oku.", Intent.CALENDAR_READ_PROPOSAL, "read back before confirming"),
    _plain("Takvimimde bugün ne var?", Intent.CALENDAR_AGENDA, "agenda"),
    _plain("Onayla.", Intent.CALENDAR_COMMIT, "the confirmation of a proposal read back"),
    # --- documents and files
    _plain("Masaüstündeki sözleşmeyi bul.", Intent.FILE_SEARCH, "file search"),
    _plain("Bu belgeyi özetle.", Intent.DOCUMENT_SUMMARIZE, "document summary"),
    _plain("Belgeyi oku.", Intent.DOCUMENT_READ, "reading a document"),
    _plain("Belgeleri karşılaştır.", Intent.DOCUMENT_COMPARE, "comparison"),
    _plain("Ortak noktalar ne?", Intent.DOCUMENT_COMMON_POINTS, "the shared points"),
    # --- the operator
    _plain("Not Defteri'ni aç.", Intent.APP_OPEN, "opening a desktop application"),
    _plain("Öndeki pencereyi kapat.", Intent.WINDOW_CLOSE, "window control"),
    _plain("IP adresim ne?", Intent.SHELL_QUERY, "the bounded shell question"),
    _plain("Bilgisayarın adı ne?", Intent.SHELL_QUERY, "the other bounded shell question"),
    # --- weather, location, news
    _plain("Hava nasıl?", Intent.WEATHER_QUERY, "weather"),
    _plain(
        "Hangi konumu kullanıyorsun?",
        Intent.LOCATION_SOURCE_QUERY,
        "where the weather came from",
    ),
    _plain("Haberleri özetle.", Intent.NEWS_SUMMARIZE, "news"),
    _plain("Haberleri aç.", Intent.NEWS_OPEN, "opening the news"),
    _plain("Sistem durumu ne?", Intent.EXPLAIN, "a question about the system itself"),
    # --- the factories
    _plain("Excel oluştur.", Intent.ARTIFACT_CREATE, "artifact factory"),
    _plain("Yeni bir uygulama yap.", Intent.APP_FACTORY_CREATE, "app factory"),
    _plain("Uygulamayı çalıştır.", Intent.APP_FACTORY_RUN, "running a generated project"),
    _plain("Testleri çalıştır.", Intent.APP_FACTORY_TEST, "its tests"),
    _plain("Windows uygulaması yap.", Intent.NATIVE_CREATE_WINDOWS, "the native factory"),
    _plain("EXE derle.", Intent.NATIVE_BUILD_EXE, "compiling it"),
    _plain("Sahneyi oluştur.", Intent.SCENE_CREATE, "the 3D family"),
    _plain("Bir küre ekle.", Intent.SCENE_ADD, "'ekle' in the family that DOES own it"),
    _plain("Sahneyi render al.", Intent.SCENE_RENDER, "rendering it"),
    _plain("Resmi yeniden çiz.", Intent.CREATIVE_REDRAW, "the creative family"),
    _plain("Görseli dışa aktar.", Intent.CREATIVE_EXPORT, "exporting it"),
    # --- media
    _plain("Müziği çal.", Intent.MEDIA_PLAY, "media"),
    _plain("Müziği durdur.", Intent.MEDIA_STOP, "stopping media, not the narration"),
    # --- capability, evolution and release: the ones with authority behind them
    _plain("Yetenek durumu ne?", Intent.CAPABILITY_STATUS, "capability genesis"),
    _plain("Kendi kendini geliştirmeyi duraklat.", Intent.EVOLUTION_PAUSE, "owner authority"),
    _plain("Önceki sürüme dön.", Intent.RELEASE_ROLLBACK, "rollback"),
    _plain("Canlıya al.", Intent.DEPLOY, "the imperative that policy always refuses"),
)


#: The whole measurement set, in one tuple. The count is asserted by the test: the audit
#: measured 103 sentences and the set may only grow.
ROUTING_SET: Final[tuple[RouteCase, ...]] = (
    MEASURED_MISROUTES + SHIELD + CRITICAL_VERBS + UNROUTED + PLAUSIBLE
)


def misroutes(resolve: Any) -> list[tuple[RouteCase, Intent]]:
    """Every case whose resolution is wrong, with what it actually resolved to.

    Returned rather than asserted so the test can report all of them at once: fixing a
    router one failure at a time, re-running between each, is how the seventh gets missed.
    """
    out: list[tuple[RouteCase, Intent]] = []
    for case in ROUTING_SET:
        resolved = resolve(case.utterance, **case.state).intent
        if resolved in case.forbidden or (case.expected is not None and resolved != case.expected):
            out.append((case, resolved))
    return out


__all__ = [
    "ACTING_INTENTS",
    "CRITICAL_VERBS",
    "MEASURED_MISROUTES",
    "ROUTING_SET",
    "RouteCase",
    "SHIELD",
    "SOURCE_CRITICAL",
    "SOURCE_MEASURED",
    "SOURCE_SHIELD",
    "SOURCE_UNROUTED",
    "UNROUTED",
    "misroutes",
]
