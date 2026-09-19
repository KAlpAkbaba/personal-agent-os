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
from dataclasses import dataclass, field, replace
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
#: B14 req 289/290/291: one armed routine the owner can name. The control tools take an id,
#: and a corpus case that invented one would be testing the 404 path.
CTX_ROUTINE_EXISTS: Final = "routine_exists"
#: B16 req 36-38/61: one memory the owner can name, written through the real service.
#: `memory.forget` HARD-deletes and `memory.correct` versions, so a corpus case that
#: proves either has to act on a row the rest of the system agrees exists.
CTX_MEMORY_EXISTS: Final = "memory_exists"
#: B27 req 732: one research genuinely in flight (a task RUNNING with its run row in
#: ``discovering``), so "Araştırmayı iptal et." has something real to end - and the
#: same sentence with nothing running is a refusal, not a success over nothing.
CTX_RESEARCH_RUNNING: Final = "research_running"
#: B31 req 204: the same running research, paused by the owner (the flag, never a stage).
CTX_RESEARCH_PAUSED: Final = "research_paused"
#: B27 req 733: a playback THIS service opened, still live, so "Sesini kıs." has a
#: session to send ``browser.media_volume`` to.
CTX_MEDIA_PLAYING: Final = "media_playing"
#: 2026-09-08 wake-song defect fix: the owner has already approved a wake song
#: (``alarms_service.set_wake_song``) — the one precondition a plain "Yarın 07:30'da beni
#: uyandır." (no media named) needs to resolve to something real rather than the tone, the
#: same "genuine fixture, not a sentinel" discipline every other CTX_*_FOCUSED context
#: already uses.
CTX_ALARM_WAKE_SONG_SET: Final = "alarm_wake_song_set"
#: The harmless test URL the harness approves under ``CTX_ALARM_WAKE_SONG_SET`` (directive
#: item H: never the owner's real song). A corpus-only fixture id, never a real video.
CTX_ALARM_WAKE_SONG_URL: Final = "https://www.youtube.com/watch?v=CorpusApprovedWakeSong"
CTX_EYE_DISABLED: Final = "eye_disabled"
#: M19 (docs/M19_DIGITAL_OPERATOR_SPEC.md §5): a window (and an older one) is
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
#: B32: the OCR fixture (metin.png) already read and focused; the archive fixture focused
#: as a FILE (never extracted); the duplicate proposal already heard (dedup may act).
CTX_IMAGE_FOCUSED: Final = "image_focused"
CTX_ARCHIVE_FOCUSED: Final = "archive_focused"
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

#: M21 (docs/M21_MAIL_CALENDAR_SPEC.md §5): the fixture contexts Mail & Calendar needs, all
#: pre-seeded straight from the oracle (tests/mail_calendar_support.py) — no fake-provider
#: call at all for the focus itself, only for what the tool subsequently reads.
#: The current message is the latest from Ali (uid 104, "Re: Proje planı").
CTX_MESSAGE_FOCUSED: Final = "message_focused"
#: A mail_drafts row exists, PREPARED and already read back this session — the one
#: precondition "Gönder."/"Gönderme."/"Vazgeç." need to resolve to something real.
CTX_DRAFT_READ_BACK: Final = "draft_read_back"
#: The current event is the fixture's own "Diş hekimi" (ev-dis@fixture.example) —
#: for "Bunu bir saat ertele." (spec §3's own reschedule example).
CTX_EVENT_FOCUSED: Final = "event_focused"
#: A calendar_proposals row exists, PREPARED and already read back this session — the one
#: precondition "Onayla."/"Vazgeç." need to resolve to something real.
CTX_PROPOSAL_READ_BACK: Final = "proposal_read_back"

#: M22 (docs/M22_ARTIFACT_FACTORY_SPEC.md §5): a real ``artifacts`` row already exists —
#: a budget spreadsheet, current object focus (kind ``artifact``) — so "bunu aç" /
#: "bu dosya doğru mu?" / "bunu PDF yap" resolve to something real. No CTX for a SECOND,
#: older artifact + "önceki": the harness seeds one current row and one older row, the
#: same two-timestamp discipline CTX_WINDOW_FOCUSED already uses for "önceki pencereye
#: dön" (see tests/voice_corpus/harness.py's own ``seed``).
CTX_ARTIFACT_FOCUSED: Final = "artifact_focused"
#: The current artifact is a DOCUMENT (an older spreadsheet behind it): the kinds decide the
#: formats (spec §1), so "Bunu PDF yap" needs a document in focus — a spreadsheet answers
#: "Bu dosya PDF olamaz" honestly, which the strengthened harness now refuses to count as done.
CTX_DOCUMENT_ARTIFACT_FOCUSED: Final = "document_artifact_focused"

#: M23 (docs/M23_APP_FACTORY_SPEC.md §5): a REAL ``app_projects`` row, scaffolded through
#: the real ``AppFactoryService.create`` against the fake device (the same "genuine
#: fixture, not a sentinel" discipline CTX_ARTIFACT_FOCUSED already uses) — a task-tracker,
#: current object focus (kind ``project``), so "testleri çalıştır" / "uygulamayı çalıştır"
#: resolve to something real.
CTX_APP_SCAFFOLDED: Final = "app_scaffolded"
#: The same project, additionally RUN (state ``running``, a ``run_port``) — for
#: "uygulamayı durdur" / "uygulama çalışıyor mu?" / "uygulamayı aç".
CTX_APP_RUNNING: Final = "app_running"

#: M25 (docs/M25_CREATIVE_3D_SPEC.md §5): a REAL ``scenes`` row, scaffolded through
#: the real ``SceneService.create`` against the fake device (the same "genuine
#: fixture, not a sentinel" discipline CTX_APP_SCAFFOLDED already uses) — a Blender
#: scene, current object focus (kind ``scene``), so "Bir küp ekle." / "Render al." /
#: "Sahnede ne var?" resolve to something real.
CTX_SCENE_BLENDER: Final = "scene_blender"

#: M27 (docs/M27_CREATIVE_TOOLS_SPEC.md §5, ADR-0093): a REAL ``creative_runs`` row,
#: made through the real ``CreativeService.create`` against a fixture Paint provider
#: that always answers "installed" (never launching ``mspaint.exe``, the same
#: "genuine fixture, not a sentinel" discipline ``CTX_SCENE_BLENDER`` already uses) —
#: sets the current ``creative`` focus as a side effect
#: (``CreativeService._run_rounds``'s own ``focus_module.set_focus`` call), so "Arka
#: planını kaldır." / "Renkleri biraz düzelt." / "Bunu PNG olarak dışa aktar." resolve
#: to something real.
CTX_CREATIVE_PAINT: Final = "creative_paint"
#: B43 (req 511): the same real Paint run with one more step - so "Geri al." has a step
#: to undo - and with that step already undone, so "Yinele." has one to redo. A single
#: seeded output has neither, and the honest answer to both would be a refusal.
CTX_CREATIVE_UNDOABLE: Final = "creative_undoable"
CTX_CREATIVE_REDOABLE: Final = "creative_redoable"

#: M28 (docs/M28_NATIVE_APP_FACTORY_SPEC.md §4, §6, ADR-0095): REAL ``native_builds``
#: rows, opened through the real ``app.nativefactory.service.plan_build`` against a
#: FIXTURE ``ToolchainFacts`` that mirrors this machine as spec §1 measured it on
#: 2026-09-09 - .NET 10 and the Windows Kits present, the Android SDK present, and NO
#: Java. Fixed rather than measured on purpose: a corpus case must mean the same thing
#: on the owner's machine and on a CI runner with no .NET at all, and the honest
#: "Android needs a JDK (owner item 33)" refusal is a CONTRACT here, not an accident of
#: what happened to be installed.
#: ``native_planned`` is one ``windows_exe`` row in ``planned``; ``native_android`` is
#: one ``android_apk`` row, which plan_build opens as ``unavailable`` WITH its reason
#: (spec §4: the owner asked, so the answer has to exist somewhere they can see);
#: ``native_built`` is a ``windows_exe`` row driven all the way through the REAL
#: lifecycle - generate, build, test, publish - by a scripted runner standing in for
#: the device's compiler, whose output the REAL independent reader then refuses to
#: read. That last one is the milestone's own character as a fixture: the row lands
#: ``unverified`` and the receipt says "Hazır demiyorum", which is exactly what must
#: happen when nothing has actually verified the file.
CTX_NATIVE_PLANNED: Final = "native_planned"
CTX_NATIVE_ANDROID: Final = "native_android"
CTX_NATIVE_BUILT: Final = "native_built"

#: M24 (docs/M24_CAPABILITY_GENESIS_SPEC.md §6, §7): the REAL counter-box/lamp-box
#: fixture application, started by the harness on a free port and registered into
#: app.genesis.catalogue for the duration of ONE case — never a mock; the same
#: "genuine fixture, not a sentinel" discipline CTX_ARTIFACT_FOCUSED/CTX_APP_SCAFFOLDED
#: already use, one level further (a real HTTP server, not just a real DB row).
CTX_COUNTERBOX_RUNNING: Final = "counterbox_running"
CTX_LAMPBOX_RUNNING: Final = "lampbox_running"

#: M26 addendum (docs/M26_LATEST_NEWS_MODE_SPEC.md §1, §3): a REAL, identity-resolved news
#: source ("Show Ana Haber", a fixture channel id — never a real external claim), the
#: default (lowest priority, enabled) so a bare "Haberleri aç." resolves to it, with a
#: deterministic fixture provider behind it (an older full bulletin, a newer promo) —
#: the same "genuine fixture, not a sentinel" discipline CTX_ARTIFACT_FOCUSED already
#: uses, and the SAME fixture shape tests/unit/test_news_resolver.py's own promo
#: scenario uses, so the corpus and the resolver's unit tests agree.
CTX_NEWS_SOURCE_CONFIGURED: Final = "news_source_configured"

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
#: ``window.list`` appears in every plan that acts on a REMEMBERED window (ADR-0101): the
#: focus stack records what the operator once observed, and the owner closes windows, so
#: "current"/"previous" are held against the desktop before anything acts on them. It is a
#: read and it changes nothing — but it IS a device call this policy did not allow, and the
#: policy is the reason it had to be declared here rather than appearing unannounced.
SIDE_EFFECTS_OPERATOR_WINDOW_CLOSE: Final[frozenset[str]] = frozenset(
    {"window.close", "window.list"}
)
SIDE_EFFECTS_OPERATOR_WINDOW_MAXIMIZE: Final[frozenset[str]] = frozenset(
    {"window.maximize", "window.list"}
)
SIDE_EFFECTS_OPERATOR_WINDOW_MINIMIZE: Final[frozenset[str]] = frozenset(
    {"window.minimize", "window.list"}
)
SIDE_EFFECTS_OPERATOR_WINDOW_RESTORE: Final[frozenset[str]] = frozenset(
    {"window.restore", "window.list"}
)
SIDE_EFFECTS_OPERATOR_WINDOW_PREVIOUS: Final[frozenset[str]] = frozenset(
    {"window.activate", "window.list"}
)
SIDE_EFFECTS_OPERATOR_TYPE: Final[frozenset[str]] = frozenset(
    {"window.activate", "keyboard.type", "ui.inspect", "window.list"}
)
SIDE_EFFECTS_OPERATOR_SHELL: Final[frozenset[str]] = frozenset({"terminal.execute"})
#: B28 req 92/93/98: activate, then ONE input, re-observed.
SIDE_EFFECTS_OPERATOR_KEY: Final[frozenset[str]] = frozenset(
    {"window.activate", "keyboard.key", "window.list"}
)
SIDE_EFFECTS_OPERATOR_SHORTCUT: Final[frozenset[str]] = frozenset(
    {"window.activate", "keyboard.shortcut", "window.list"}
)
SIDE_EFFECTS_OPERATOR_SCROLL: Final[frozenset[str]] = frozenset(
    {"window.activate", "pointer.scroll", "window.list"}
)
#: B29 req 100/102/105: an invoke (activate, invoke - the device's own before/after read
#: is the verification), a read (ONE ui.inspect), a description (ONE screen.capture).
SIDE_EFFECTS_OPERATOR_UI_INVOKE: Final[frozenset[str]] = frozenset(
    {"window.activate", "ui.invoke", "window.list"}
)
SIDE_EFFECTS_OPERATOR_UI_READ: Final[frozenset[str]] = frozenset({"ui.inspect", "window.list"})
SIDE_EFFECTS_OPERATOR_SEE: Final[frozenset[str]] = frozenset({"screen.capture"})
# B30 req 82/84/85/118-122: geometry re-observed from the device's own rect, an
# application closed by name (its window found by image in the device's list), the third
# shell query, and the process/service family.
SIDE_EFFECTS_OPERATOR_APP_CLOSE: Final[frozenset[str]] = frozenset({"window.list", "app.close"})
SIDE_EFFECTS_OPERATOR_PROCESS_LIST: Final[frozenset[str]] = frozenset({"process.list"})
SIDE_EFFECTS_OPERATOR_PROCESS_STOP: Final[frozenset[str]] = frozenset(
    {"process.stop", "process.list"}
)
SIDE_EFFECTS_OPERATOR_SERVICE_STATUS: Final[frozenset[str]] = frozenset({"service.status"})
SIDE_EFFECTS_OPERATOR_SERVICE_RESTART: Final[frozenset[str]] = frozenset(
    {"service.restart", "service.status"}
)

#: M20 (docs/M20_FILE_DOCUMENT_INTELLIGENCE_SPEC.md §4): exactly the device capabilities
#: each documents tool may reach on the fake device (tests/documents_support.py) — the
#: same policy discipline every other family above already uses. A case whose document is
#: already indexed (a CTX_*_FOCUSED fixture) reaches NO capability at all: the index IS
#: the read, and "no background crawling" means a cached answer never re-reads.
SIDE_EFFECTS_DOCUMENTS_SEARCH: Final[frozenset[str]] = frozenset({"file.search"})
SIDE_EFFECTS_DOCUMENTS_READ: Final[frozenset[str]] = frozenset({"document.extract"})
SIDE_EFFECTS_DOCUMENTS_COMPARE: Final[frozenset[str]] = frozenset({"file.compare"})
# B32 req 139/142/150/151: headers only, a scan that hashes, and the Recycle Bin move.
SIDE_EFFECTS_DOCUMENTS_INSPECT: Final[frozenset[str]] = frozenset({"file.inspect"})
SIDE_EFFECTS_DOCUMENTS_DUPLICATES: Final[frozenset[str]] = frozenset({"file.search", "file.locate"})
SIDE_EFFECTS_DOCUMENTS_TRASH: Final[frozenset[str]] = frozenset({"file.trash"})
#: B34 req 153-165: what a managed mutation may reach on the fake desktop - the locate and
#: the read a proposal needs, the six mutations, the Recycle Bin with a backup, the restore.
SIDE_EFFECTS_DOCUMENTS_MUTATION: Final[frozenset[str]] = frozenset(
    {
        "file.search",
        "file.locate",
        "file.read",
        "file.write",
        "file.append",
        "file.rename",
        "file.move",
        "file.copy",
        "file.trash",
        "file.restore",
    }
)

#: M21 (docs/M21_MAIL_CALENDAR_SPEC.md §5): NOT a fake-DEVICE capability like every set
#: above — mail/calendar never touch the device at all. This is the harness's OWN token
#: for "this case's tool call is allowed to reach the fake sender/writer exactly once";
#: the harness checks ``FakeMailSender.sent`` / ``FakeCalendarWriter.created+updated``
#: directly rather than ``FakeDeviceAction.capabilities_called()``. Forbidden in every
#: mail_calendar case except the ONE confirmation case that names its own — never both on
#: the same case, since no single utterance legitimately sends a mail AND commits a
#: calendar change.
SIDE_EFFECTS_MAIL_SEND: Final[frozenset[str]] = frozenset({"mail.send"})
SIDE_EFFECTS_CALENDAR_COMMIT: Final[frozenset[str]] = frozenset({"calendar.commit"})

#: M22 (docs/M22_ARTIFACT_FACTORY_SPEC.md §5): ``artifact.open`` reaches the fake device
#: through ``file.fetch`` only — the same policy discipline every family above uses.
#: create/render/validate/list touch no device at all (the factory renders in-process
#: against the in-memory object store).
SIDE_EFFECTS_ARTIFACT_OPEN: Final[frozenset[str]] = frozenset({"file.fetch"})

#: M23 (docs/M23_APP_FACTORY_SPEC.md §5): exactly the device capabilities each app-
#: factory tool may reach on the fake device — the same policy discipline every family
#: above uses. ``app.status``/``app.list`` reach no device at all when the seeded
#: project is not running / for a listing (``AppFactoryService`` reads the row).
SIDE_EFFECTS_APP_CREATE: Final[frozenset[str]] = frozenset({"project.scaffold"})
SIDE_EFFECTS_APP_RUN: Final[frozenset[str]] = frozenset({"project.run"})
SIDE_EFFECTS_APP_TEST: Final[frozenset[str]] = frozenset({"project.test"})
SIDE_EFFECTS_APP_STOP: Final[frozenset[str]] = frozenset({"project.stop"})
SIDE_EFFECTS_APP_STATUS: Final[frozenset[str]] = frozenset({"project.status"})
SIDE_EFFECTS_APP_OPEN: Final[frozenset[str]] = frozenset({"file.reveal"})

#: M24 (docs/M24_CAPABILITY_GENESIS_SPEC.md §6, §7): NOT a fake-DEVICE capability like
#: every set above — a genesis dispatch reaches the REAL fixture over real loopback
#: HTTP, never the fake device. This is the harness's OWN token (the same shape
#: SIDE_EFFECTS_MAIL_SEND/SIDE_EFFECTS_CALENDAR_COMMIT already are for their own
#: non-device sinks): "this case's tool call is allowed to change the fixture's real
#: state". A case naming it must actually change that state; every other genesis case
#: must not.
SIDE_EFFECTS_CAPABILITY_MUTATE: Final[frozenset[str]] = frozenset({"capability.mutate"})

#: M25 (docs/M25_CREATIVE_3D_SPEC.md §5, §7): the device capabilities the 3D-creation
#: family may reach — the SAME ``project.scaffold``/``project.run`` capability names
#: the App Factory already uses (App Factory device family, extended by the windows-
#: engineer track with the two 3D runtimes) plus the new ``scene.inspect``. Every
#: mutating scene tool (create/add/transform/material/light/camera/render) scaffolds,
#: runs and inspects in one call; the query tool (scene.inspect) reaches only the
#: last one.
SIDE_EFFECTS_SCENE_MUTATE: Final[frozenset[str]] = frozenset(
    {"project.scaffold", "project.run", "scene.inspect"}
)
SIDE_EFFECTS_SCENE_INSPECT: Final[frozenset[str]] = frozenset({"scene.inspect"})

#: M26 addendum (docs/M26_LATEST_NEWS_MODE_SPEC.md §5, §6): governed playback opens the
#: browser worker's OWN ``news`` profile/context — ``browser.session_open`` then
#: ``browser.media_play``, never the alarm's ``desktop.alarm_*``/``browser.media_stop``
#: family and never a research-shaped ``browser.search``/``browser.fetch_evidence``.
#: news.summarize/news.query_latest reach no device capability at all (summarize
#: delegates to research's OWN device selection, invisible to this fake; query_latest
#: only reads the fixture provider).
SIDE_EFFECTS_NEWS_OPEN: Final[frozenset[str]] = frozenset(
    {"browser.session_open", "browser.media_play"}
)

#: ADR-0112: the owner's OWN media. Three calls, in this order, on one isolated media
#: session: open, SEARCH (the news surface never searches - it resolves from a channel
#: feed - so this is the one thing that tells the two families apart in the device
#: trail), then play. Never ``desktop.alarm_*``: a song the owner asked for must not
#: touch the wake alarm's tone or its dedicated browser profile.
SIDE_EFFECTS_MEDIA_PLAY: Final[frozenset[str]] = frozenset(
    {
        "browser.session_open",
        # ADR-0113: a NEW TAB before anything navigates. Attaching to the owner's own
        # Chrome hands the session their FIRST EXISTING tab, so without this the search
        # would drive whatever they had open there to a search engine. Declared HERE
        # because this policy is the list of device calls one utterance may make, and an
        # undeclared one is exactly the kind of silent extra reach it exists to catch --
        # it caught this one.
        "browser.tab_new",
        "browser.search",
        "browser.media_play",
    }
)
#: "Şarkıyı durdur." stops the session this family opened -- nothing else.
SIDE_EFFECTS_MEDIA_STOP: Final[frozenset[str]] = frozenset({"browser.media_stop"})
#: B27 req 733: the level is READ from the page first (``media_status.volume``), then moved.
SIDE_EFFECTS_MEDIA_VOLUME: Final[frozenset[str]] = frozenset(
    {"browser.media_status", "browser.media_volume"}
)
#: B27 req 735: one ``screen.capture`` and nothing else touches the device.
SIDE_EFFECTS_SCREENSHOT: Final[frozenset[str]] = frozenset({"screen.capture"})

#: M27 (docs/M27_CREATIVE_TOOLS_SPEC.md §5, §7, ADR-0093): the Creative Tools Operator
#: reaches the FAKE DEVICE never at all in this Cloud Core half — Paint's own edit runs
#: entirely in-process with Pillow, against the object store, never a device call
#: (``app.creative.service`` module docstring's own scope note). Every creative case
#: therefore uses the SAME empty policy ``SIDE_EFFECTS_NONE`` already is, spelled here
#: under its own name so a family that DOES later gain a device call (a real Paint
#: round trip through the M19 operator) is a visible, deliberate change to this
#: constant rather than a silent widening of an already-shared one.
SIDE_EFFECTS_CREATIVE: Final[frozenset[str]] = frozenset()
#: B43 (req 509): a delivery reaches the owner's disk through the artifact open path.
SIDE_EFFECTS_CREATIVE_DELIVER: Final[frozenset[str]] = frozenset({"file.fetch"})

#: M28 (docs/M28_NATIVE_APP_FACTORY_SPEC.md §5, §6, §9): the Native App Factory reaches
#: the fake DEVICE never at all in this Cloud Core half. The compiler is the device's
#: (spec §5: bounded Job Object children under the authorised ``native`` root), and the
#: corpus hands the tools a scripted runner directly through ``ctx.live`` rather than
#: through a device capability - so a native case that touched ANY device capability
#: would be reaching a path this milestone deliberately does not have. Spelled under its
#: own name, like ``SIDE_EFFECTS_CREATIVE``, so the day the runner does become a device
#: call it is a visible change to this constant.
SIDE_EFFECTS_NATIVE: Final[frozenset[str]] = frozenset()

#: B33 (456-471): the day the comment above foresaw. The lifecycle AFTER the build is the
#: device's - the build itself (project.scaffold / run / test, file.inspect read back), the
#: package, the shortcut install and its removal, the launch and the UI Automation reads
#: of the running window, the application's own log. A lifecycle case may reach exactly
#: these and nothing else (no file.trash, no process.stop, no browser).
SIDE_EFFECTS_NATIVE_LIFECYCLE: Final[frozenset[str]] = frozenset(
    {
        "project.scaffold",
        "project.run",
        "project.test",
        "file.inspect",
        "project.package",
        "project.install",
        "project.uninstall",
        "project.artifact",
        "app.launch",
        "window.list",
        "window.close",
        "ui.inspect",
        "ui.set_value",
        "ui.invoke",
        "file.read",
    }
)


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
    #: M21 (ADR-0084 addendum 2) / M26 (docs/M26_EXECUTIVE_AUTONOMY_SPEC.md §5, ADR-0089):
    #: a SEQUENCE of (utterance, tool_name) pairs the harness runs FIRST, in the SAME
    #: session, at increasing turns starting from 1, before this case's own utterance
    #: (which then runs at the next turn) — for a case whose contract is genuinely a
    #: MULTI-TURN conversation: a read-back then a confirmation (M21's own two-turn
    #: shape, still exactly supported as a length-1 sequence), or a whole executive
    #: conversation ("Bu işi durdur" -> "Devam et" -> "İkinci adımı tekrar dene" ->
    #: "Sunumu da ekle" -> "Bunu iptal et", each building on the durable state the REAL
    #: tool call before it left behind). ``()`` for every single-turn case (the
    #: overwhelming majority) — never satisfied by a context fixture alone (see
    #: tests/voice_corpus/harness.py's ``seed`` docstring).
    preceding_turns: tuple[tuple[str, str], ...] = ()


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
    # B31 req 203/204: pause and resume, each against the state it needs - and each
    # refused, as a receipt, against the state it does not (never a success over nothing).
    for case_id, text, source, context in (
        ("r.hold.1", "Araştırmayı duraklat.", "canonical", CTX_RESEARCH_RUNNING),
        ("r.hold.2", "Araştırmayı beklet.", "paraphrase", CTX_RESEARCH_RUNNING),
    ):
        cases.append(
            UtteranceCase(
                case_id=case_id,
                utterance=text,
                expected_intent="research_pause",
                expected_tool="research.pause",
                forbidden_tools=("research.cancel",),
                side_effects=SIDE_EFFECTS_NONE,
                context=context,
                category="research",
                source=source,
            )
        )
    for case_id, text, source in (
        ("r.continue.1", "Araştırmaya devam et.", "canonical"),
        ("r.continue.2", "Araştırmayı sürdür.", "paraphrase"),
    ):
        cases.append(
            UtteranceCase(
                case_id=case_id,
                utterance=text,
                expected_intent="research_resume",
                expected_tool="research.resume",
                forbidden_tools=("research.start",),
                side_effects=SIDE_EFFECTS_NONE,
                context=CTX_RESEARCH_PAUSED,
                category="research",
                source=source,
            )
        )
    cases.append(
        UtteranceCase(
            case_id="r.hold.already",
            utterance="Araştırmayı duraklat.",
            expected_intent="research_pause",
            expected_tool="research.pause",
            expected_response=RESPONSE_REFUSED,
            side_effects=SIDE_EFFECTS_NONE,
            context=CTX_RESEARCH_PAUSED,
            category="research",
            source="regression",
        )
    )
    cases.append(
        UtteranceCase(
            case_id="r.continue.not_paused",
            utterance="Araştırmaya devam et.",
            expected_intent="research_resume",
            expected_tool="research.resume",
            expected_response=RESPONSE_REFUSED,
            side_effects=SIDE_EFFECTS_NONE,
            context=CTX_RESEARCH_RUNNING,
            category="research",
            source="regression",
        )
    )
    # B31 req 201: "Bir önceki araştırmayı aç." names a RESEARCH, resolved by the same
    # reference the follow-ups use, never the artifact family's "bunu aç".
    cases.append(
        UtteranceCase(
            case_id="r.open.prev",
            utterance="Bir önceki araştırmayı aç.",
            expected_intent="research_open",
            expected_tool="research.open",
            expected_target="previous",
            forbidden_tools=("artifact.open", "research.start"),
            side_effects=SIDE_EFFECTS_ARTIFACT_OPEN,
            context=CTX_RESEARCH_FOCUS_B,
            category="research",
            source="canonical",
        )
    )
    # B31 req 209: the standing register - a marker ("bundan sonra", "mod") makes it one;
    # the one-off "teknik anlat" above (r.tech.*) stays the follow-up it was.
    for case_id, text, source in (
        ("r.mode.1", "Bundan sonra teknik anlat.", "canonical"),
        ("r.mode.2", "Teknik modu kapat.", "paraphrase"),
        ("r.mode.3", "Artık kısa anlat.", "paraphrase"),
    ):
        cases.append(
            UtteranceCase(
                case_id=case_id,
                utterance=text,
                expected_intent="research_answer_mode",
                expected_tool="research.answer_mode",
                forbidden_tools=("research.explain", "research.start"),
                side_effects=SIDE_EFFECTS_NONE,
                context=CTX_RESEARCH_FOCUS_B,
                category="research",
                source=source,
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


def _alarm_wake_song_cases() -> list[UtteranceCase]:
    """The 2026-09-08 wake-song defect fix, as corpus cases — its own function (directive
    item G) so a parallel track's alarm work does not collide with this one in the same
    function body.

    Every fact below was MEASURED against ``app/voice/intents.py::resolve_intent`` and
    ``app/alarms/tr_time.py::parse_when_text`` on this checkout with a small probe script,
    not assumed from the phrase's wording — the directive's own rule ("no test asserts
    something the code cannot fail") applies to what a case EXPECTS, not only to what it
    asserts:

    1. "Yarın 07:30'da beni uyandır." (spec §6's own canonical phrase, already a
       registered case elsewhere under ``CTX_NONE`` — reused here under a NEW case id,
       under ``CTX_ALARM_WAKE_SONG_SET``) is the one that actually exercises the fix
       itself: no media named on the utterance, an approved wake song in context,
       ``resolved_media_identity`` must be the song, never the tone.
    2. "Yarın bununla uyandır." names no clock time at all — ``parse_when_text`` genuinely
       raises ``no clock time or offset found`` for it, so the REAL, honest outcome is the
       SAME refusal "Yarın alarm kur." already gets (``a.unparsed.2``): ``when_unparsed``,
       nothing created. Kept under ``CTX_ALARM_WAKE_SONG_SET`` on purpose — an approved
       wake song existing must not make the system start GUESSING a time it was never
       given (the fix must never trade one invented value for another).
    3. Every phrase about the wake song ITSELF — setting it ("Bu şarkıyı alarm müziğim
       yap.", "Alarm müziğim bu olsun.", "Alarm müziğimi değiştir.") or asking what it is
       ("Alarm müziğim ne?") — resolves to NO intent at all today: there is no voice tool
       for ``PUT/GET /v1/alarms/wake-song`` (only the owner-session REST route exists,
       ``app/alarms/routes.py``). Same for "Sabah yedi buçukta bu şarkıyı çal.": "çal"
       ("play") is not one of the alarm family's recognised verbs
       (``_SET_VERB_FORMS``/``_WAKE_VERB_STEMS``), so an otherwise perfectly natural way to
       ask for a musical alarm falls through too. These are real gaps (recorded in the
       task's completion report, not silently "fixed" here by inventing a tool nobody
       asked to build) — the cases pin down that they fail SAFELY: no phantom alarm, no
       research route, no unrelated browser action, exactly the ``doc.neg.delete`` pattern
       (``tests/voice_corpus/corpus.py``'s own precedent for "the router resolves nothing,
       on purpose").
    """
    cases: list[UtteranceCase] = []
    explicit_url = "https://www.youtube.com/watch?v=CorpusWakeSongA"

    # 1. The fix itself: no media named, an approved wake song in context.
    cases.extend(
        _with_variants(
            UtteranceCase(
                case_id="a.wakesong.default",
                utterance="Yarın 07:30'da beni uyandır.",
                expected_intent="alarm_create",
                expected_tool="alarm.create",
                expected={"local_time": "07:30", "resolved_media_url": CTX_ALARM_WAKE_SONG_URL},
                forbidden_tools=("research.start",),
                context=CTX_ALARM_WAKE_SONG_SET,
                category="alarm",
                source="canonical",
                regression_issue_id="owner report 2026-09-08: tone instead of the approved song",
            )
        )
    )
    # 1b. The same context must not turn an unparseable time into a guess — the fix only
    #     ever fills in MEDIA, never invents a "when" the owner did not say.
    cases.extend(
        _with_variants(
            UtteranceCase(
                case_id="a.wakesong.deictic_no_time",
                utterance="Yarın bununla uyandır.",
                expected_intent="alarm_create",
                expected_tool="alarm.create",
                expected_response=RESPONSE_REFUSED,
                expected={"error_class": "when_unparsed"},
                context=CTX_ALARM_WAKE_SONG_SET,
                category="alarm",
                source="canonical",
                regression_issue_id=(
                    "no clock time in the utterance; must stay refused, never guessed"
                ),
            )
        )
    )

    # 2. An explicit URL still wins (spec's own rule, unchanged by the fix) — across the
    #    time forms the directive names: 07:30 / 7.30 / yedi buçuk / yedi otuz.
    time_forms = [
        ("a.wakesong.url.1", "Yarın 7:30'da bu YouTube linkiyle beni uyandır.", "07:30"),
        ("a.wakesong.url.2", "Yarın 7.30'da bu YouTube linkiyle beni uyandır.", "07:30"),
        ("a.wakesong.url.3", "Yarın yedi buçukta bu YouTube linkiyle beni uyandır.", "07:30"),
        ("a.wakesong.url.4", "Yarın yedi otuzda bu YouTube linkiyle beni uyandır.", "07:30"),
    ]
    for case_id, text, local_time in time_forms:
        cases.extend(
            _with_variants(
                UtteranceCase(
                    case_id=case_id,
                    utterance=text,
                    expected_intent="alarm_create",
                    expected_tool="alarm.create",
                    expected={"local_time": local_time, "resolved_media_url": explicit_url},
                    forbidden_tools=("research.start",),
                    tool_arguments={"media": {"url": explicit_url}},
                    context=CTX_NONE,
                    category="alarm",
                    source="canonical",
                )
            )
        )

    # 3. No voice path exists for the wake song itself — every one of these must resolve
    #    to NOTHING, never a phantom alarm, a research call or a stray media action.
    no_tool_phrases = [
        ("a.wakesong.set.1", "Bu şarkıyı alarm müziğim yap."),
        ("a.wakesong.set.2", "Alarm müziğim bu olsun."),
        ("a.wakesong.set.3", "Alarm müziğimi değiştir."),
        ("a.wakesong.query.1", "Alarm müziğim ne?"),
    ]
    for case_id, text in no_tool_phrases:
        cases.extend(
            _with_variants(
                UtteranceCase(
                    case_id=case_id,
                    utterance=text,
                    expected_intent="none",
                    expected_tool=None,
                    expected_response=RESPONSE_NONE,
                    side_effects=SIDE_EFFECTS_NONE,
                    context=CTX_NONE,
                    category="alarm",
                    source="canonical",
                    regression_issue_id="no voice tool for the wake song, only the REST route",
                )
            )
        )

    # "çal" ("play") names music but is not a recognised alarm verb — every time form the
    # directive names, so the gap is pinned down consistently rather than by accident.
    play_verb_times = [
        ("a.wakesong.playverb.1", "Sabah 07:30'da bu şarkıyı çal."),
        ("a.wakesong.playverb.2", "Sabah 7.30'da bu şarkıyı çal."),
        ("a.wakesong.playverb.3", "Sabah yedi buçukta bu şarkıyı çal."),
        ("a.wakesong.playverb.4", "Sabah yedi otuzda bu şarkıyı çal."),
    ]
    for case_id, text in play_verb_times:
        cases.extend(
            _with_variants(
                UtteranceCase(
                    case_id=case_id,
                    utterance=text,
                    expected_intent="none",
                    expected_tool=None,
                    expected_response=RESPONSE_NONE,
                    side_effects=SIDE_EFFECTS_NONE,
                    context=CTX_NONE,
                    category="alarm",
                    source="canonical",
                    regression_issue_id="'çal' is not a recognised alarm-create verb",
                )
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
    # The owner's ambient directive (2026-09-08) names one invariant above the others:
    #
    #     display off != Windows sleep
    #
    # A screen the owner asked to darken must never become a suspended machine, because
    # everything that makes this an agent — the alarms, the durable runs, the Evolution
    # Supervisor, the device connection — stops when Windows sleeps and does not when a
    # panel goes dark. The display cases above forbid the opposite DISPLAY tool, which is a
    # different question and does not answer this one.
    #
    # Measured through this router before these cases were written: both utterances resolve
    # to no intent whatsoever. They are here so that stays true, and the structural half —
    # that there is no suspend capability anywhere to route TO — is
    # `tests/unit/test_no_machine_suspend_path.py`.
    for case_id, text, source in (
        ("d.neg.suspend.1", "Bilgisayarı uyut.", "canonical"),
        ("d.neg.suspend.2", "Bilgisayarı kapat.", "canonical"),
        ("d.neg.suspend.3", "Sistemi uyku moduna al.", "paraphrase"),
        ("d.neg.suspend.4", "bilgisayari uyut", "asr_noise"),
    ):
        cases.extend(
            _with_variants(
                UtteranceCase(
                    case_id=case_id,
                    utterance=text,
                    expected_intent="none",
                    expected_tool=None,
                    expected_response=RESPONSE_NONE,
                    # And not by way of the display path either: darkening a screen is not
                    # the nearest safe reading of "suspend the machine", it is a different
                    # act, and answering one with the other would be the assistant deciding
                    # what the owner meant.
                    forbidden_tools=("display.off", "display.wake"),
                    side_effects=SIDE_EFFECTS_NONE,
                    category="display",
                    source=source,
                    regression_issue_id="ambient qualification 2026-09-08",
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


def _operator_input_cases() -> list[UtteranceCase]:
    """B28 req 92/93/98: a key, a chord and a scroll the owner can SAY, each running the
    real plan against the fake device: activate, one guarded input, re-observed."""
    cases: list[UtteranceCase] = []
    for case_id, text, source, effects in (
        ("op.key.1", "Enter'a bas.", "canonical", SIDE_EFFECTS_OPERATOR_KEY),
        ("op.key.2", "Escape'e bas.", "paraphrase", SIDE_EFFECTS_OPERATOR_KEY),
        ("op.key.3", "Tab tuşuna bas.", "paraphrase", SIDE_EFFECTS_OPERATOR_KEY),
        ("op.key.4", "Yukarı ok tuşuna bas.", "paraphrase", SIDE_EFFECTS_OPERATOR_KEY),
        ("op.shortcut.1", "Ctrl S'ye bas.", "canonical", SIDE_EFFECTS_OPERATOR_SHORTCUT),
        ("op.shortcut.2", "Kontrol Z'ye bas.", "paraphrase", SIDE_EFFECTS_OPERATOR_SHORTCUT),
        ("op.shortcut.3", "Alt F4'e bas.", "paraphrase", SIDE_EFFECTS_OPERATOR_SHORTCUT),
    ):
        cases.extend(
            _with_variants(
                UtteranceCase(
                    case_id=case_id,
                    utterance=text,
                    expected_intent="operator_key",
                    expected_tool="operator.key",
                    forbidden_tools=("operator.type",),
                    side_effects=effects,
                    context=CTX_WINDOW_FOCUSED,
                    category="operator",
                    source=source,
                )
            )
        )
    for case_id, text, source in (
        ("op.scroll.1", "Aşağı kaydır.", "canonical"),
        ("op.scroll.2", "Yukarı kaydır.", "paraphrase"),
        ("op.scroll.3", "Biraz aşağıya kaydır.", "paraphrase"),
    ):
        cases.extend(
            _with_variants(
                UtteranceCase(
                    case_id=case_id,
                    utterance=text,
                    expected_intent="operator_scroll",
                    expected_tool="operator.pointer",
                    side_effects=SIDE_EFFECTS_OPERATOR_SCROLL,
                    context=CTX_WINDOW_FOCUSED,
                    category="operator",
                    source=source,
                )
            )
        )
    # B29 req 100: a NAMED button through UI Automation - never a coordinate.
    for case_id, text, source in (
        ("op.ui.invoke.1", "Tamam düğmesine tıkla.", "canonical"),
        ("op.ui.invoke.2", "Kaydet düğmesine bas.", "paraphrase"),
        ("op.ui.invoke.3", "İptal butonuna tıkla.", "paraphrase"),
    ):
        cases.extend(
            _with_variants(
                UtteranceCase(
                    case_id=case_id,
                    utterance=text,
                    expected_intent="ui_invoke",
                    expected_tool="operator.ui",
                    forbidden_tools=("operator.pointer", "operator.key"),
                    side_effects=SIDE_EFFECTS_OPERATOR_UI_INVOKE,
                    context=CTX_WINDOW_FOCUSED,
                    category="operator",
                    source=source,
                )
            )
        )
    # B29 req 102: the text a control holds, read through the tree.
    for case_id, text, source in (
        ("op.ui.read.1", "Ekrandaki metni oku.", "canonical"),
        ("op.ui.read.2", "Ne yazıyor?", "paraphrase"),
        ("op.ui.read.3", "Ekrandaki yazıyı oku.", "paraphrase"),
    ):
        cases.extend(
            _with_variants(
                UtteranceCase(
                    case_id=case_id,
                    utterance=text,
                    expected_intent="ui_read",
                    expected_tool="operator.inspect",
                    forbidden_tools=("operator.see",),
                    side_effects=SIDE_EFFECTS_OPERATOR_UI_READ,
                    context=CTX_WINDOW_FOCUSED,
                    category="operator",
                    source=source,
                )
            )
        )
    # B29 req 105: the visual rung, answered by the harness's scripted provider from a
    # genuine (one-pixel) capture.
    for case_id, text, source in (
        ("op.see.1", "Ekranda ne var?", "canonical"),
        ("op.see.2", "Ekranı anlat.", "paraphrase"),
        ("op.see.3", "Ekranımı tarif et.", "paraphrase"),
    ):
        cases.extend(
            _with_variants(
                UtteranceCase(
                    case_id=case_id,
                    utterance=text,
                    expected_intent="screen_describe",
                    expected_tool="operator.see",
                    forbidden_tools=("display.off", "display.wake", "operator.inspect"),
                    side_effects=SIDE_EFFECTS_OPERATOR_SEE,
                    context=CTX_WINDOW_FOCUSED,
                    category="operator",
                    source=source,
                )
            )
        )
    # "Düğmeye bas." names no key: a question, never a guess (and no device call).
    cases.append(
        UtteranceCase(
            case_id="op.key.no_key",
            utterance="Düğmeye bas.",
            expected_intent="none",
            expected_tool=None,
            expected_response=RESPONSE_NONE,
            forbidden_tools=("operator.key",),
            side_effects=SIDE_EFFECTS_NONE,
            context=CTX_WINDOW_FOCUSED,
            category="operator",
            source="canonical",
        )
    )
    # No window focused: a clarification before any device call.
    cases.append(
        UtteranceCase(
            case_id="op.key.no_window",
            utterance="Enter'a bas.",
            expected_intent="operator_key",
            expected_tool="operator.key",
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
        # B30 req 118: the third bounded shell question.
        ("op.shell.who.1", "Kullanıcı adım ne?", "canonical"),
        ("op.shell.who.2", "Hangi kullanıcıyla oturum açtım?", "paraphrase"),
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


def _operator_process_service_cases() -> list[UtteranceCase]:
    """B30 req 82, 119-122: an application closed by NAME, processes and services asked
    about by name, and the two policy-gated actions. The harness's desktop lists a Notepad
    and a Calculator (CTX_WINDOW_FOCUSED), runs a Notepad and a Chrome, and answers every
    service as Running; the Cloud Core's own policy refuses what the contract does not
    name BEFORE any device call (the ``op.process.stop.policy`` / ``op.service.policy``
    cases: no side effect, a refusal receipt)."""
    cases: list[UtteranceCase] = []
    for case_id, text, source in (
        ("op.app_close.1", "Not Defteri'ni kapat.", "canonical"),
        ("op.app_close.2", "Hesap makinesini kapat.", "paraphrase"),
    ):
        cases.extend(
            _with_variants(
                UtteranceCase(
                    case_id=case_id,
                    utterance=text,
                    expected_intent="app_close",
                    expected_tool="operator.app_close",
                    forbidden_tools=("operator.window_control", "operator.process"),
                    side_effects=SIDE_EFFECTS_OPERATOR_APP_CLOSE,
                    context=CTX_WINDOW_FOCUSED,
                    category="operator",
                    source=source,
                )
            )
        )
    # An application that is not open: the truthful "already closed", no device close.
    cases.append(
        UtteranceCase(
            case_id="op.app_close.not_running",
            utterance="Chrome'u kapat.",
            expected_intent="app_close",
            expected_tool="operator.app_close",
            side_effects=frozenset({"window.list"}),
            context=CTX_WINDOW_FOCUSED,
            category="operator",
            source="regression",
        )
    )
    for case_id, text, source in (
        ("op.process.query.1", "Chrome çalışıyor mu?", "canonical"),
        ("op.process.query.2", "Hangi uygulamalar açık?", "paraphrase"),
        ("op.process.query.3", "Not Defteri açık mı?", "paraphrase"),
    ):
        cases.extend(
            _with_variants(
                UtteranceCase(
                    case_id=case_id,
                    utterance=text,
                    expected_intent="process_query",
                    expected_tool="operator.process",
                    forbidden_tools=("operator.app_close", "operator.app_open"),
                    side_effects=SIDE_EFFECTS_OPERATOR_PROCESS_LIST,
                    category="operator",
                    source=source,
                )
            )
        )
    for case_id, text, source in (
        ("op.process.stop.1", "Chrome'u sonlandır.", "canonical"),
        ("op.process.stop.2", "Not Defteri'ni sonlandır.", "paraphrase"),
    ):
        cases.extend(
            _with_variants(
                UtteranceCase(
                    case_id=case_id,
                    utterance=text,
                    expected_intent="process_stop",
                    expected_tool="operator.process",
                    forbidden_tools=("operator.app_close",),
                    side_effects=SIDE_EFFECTS_OPERATOR_PROCESS_STOP,
                    category="operator",
                    source=source,
                )
            )
        )
    # PowerShell is an application the owner may OPEN but not one the stop policy names:
    # refused by the Cloud Core's own reading of the contract, before any device call.
    cases.append(
        UtteranceCase(
            case_id="op.process.stop.policy",
            utterance="PowerShell'i sonlandır.",
            expected_intent="process_stop",
            expected_tool="operator.process",
            expected_response=RESPONSE_REFUSED,
            side_effects=SIDE_EFFECTS_NONE,
            category="operator",
            source="regression",
        )
    )
    for case_id, text, source in (
        ("op.service.query.1", "Yazdırma servisi çalışıyor mu?", "canonical"),
        ("op.service.query.2", "Spooler servisinin durumu ne?", "paraphrase"),
    ):
        cases.extend(
            _with_variants(
                UtteranceCase(
                    case_id=case_id,
                    utterance=text,
                    expected_intent="service_query",
                    expected_tool="operator.service",
                    side_effects=SIDE_EFFECTS_OPERATOR_SERVICE_STATUS,
                    category="operator",
                    source=source,
                )
            )
        )
    for case_id, text, source in (
        ("op.service.restart.1", "Yazdırma servisini yeniden başlat.", "canonical"),
        ("op.service.restart.2", "Spooler servisini yeniden başlat.", "paraphrase"),
    ):
        cases.extend(
            _with_variants(
                UtteranceCase(
                    case_id=case_id,
                    utterance=text,
                    expected_intent="service_restart",
                    expected_tool="operator.service",
                    forbidden_tools=("operator.shell",),
                    side_effects=SIDE_EFFECTS_OPERATOR_SERVICE_RESTART,
                    category="operator",
                    source=source,
                )
            )
        )
    cases.append(
        UtteranceCase(
            case_id="op.service.policy",
            utterance="Bluetooth servisini yeniden başlat.",
            expected_intent="service_restart",
            expected_tool="operator.service",
            expected_response=RESPONSE_REFUSED,
            side_effects=SIDE_EFFECTS_NONE,
            category="operator",
            source="regression",
        )
    )
    # The generic repeat keeps "yeniden" when no service is named.
    cases.append(
        UtteranceCase(
            case_id="op.service.not_repeat",
            utterance="Yeniden başlat.",
            expected_intent="repeat",
            expected_tool=None,
            expected_response=RESPONSE_CONTROL,
            forbidden_tools=("operator.service",),
            side_effects=SIDE_EFFECTS_NONE,
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
        *_operator_input_cases(),
        *_operator_shell_cases(),
        *_operator_control_cases(),
        *_operator_process_service_cases(),
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


def _document_b32_cases() -> list[UtteranceCase]:
    """B32 req 139/141/142/148/150/151/152/169 through the real relay."""
    cases: list[UtteranceCase] = []
    # 152: the preview of the focused (already read) document - no device call.
    for case_id, text, source in (
        ("doc.preview.1", "Bu belgeyi önizle.", "canonical"),
        ("doc.preview.2", "Bu dosyanın önizlemesini göster.", "paraphrase"),
    ):
        cases.extend(
            _with_variants(
                UtteranceCase(
                    case_id=case_id,
                    utterance=text,
                    expected_intent="document_preview",
                    expected_tool="document.preview",
                    side_effects=SIDE_EFFECTS_NONE,
                    context=CTX_DOCUMENT_FOCUSED,
                    category="documents",
                    source=source,
                )
            )
        )
    # 148: full text over the index (rapor.pdf names Hetzner) - no device call, no crawl.
    for case_id, text, source in (
        ("doc.text.1", "İçinde Hetzner geçen belgeyi bul.", "canonical"),
        ("doc.text.2", "Hetzner yazan dosya hangisi?", "paraphrase"),
    ):
        cases.extend(
            _with_variants(
                UtteranceCase(
                    case_id=case_id,
                    utterance=text,
                    expected_intent="document_find_text",
                    expected_tool="document.find_text",
                    forbidden_tools=("file.search",),
                    side_effects=SIDE_EFFECTS_NONE,
                    context=CTX_DOCUMENT_FOCUSED,
                    category="documents",
                    source=source,
                )
            )
        )
    # 151: the duplicate scan lists then hashes; 150: the move needs the proposal first.
    for case_id, text, source in (
        ("doc.dupes.1", "Yinelenen dosyaları bul.", "canonical"),
        ("doc.dupes.2", "Kopya dosyaları bul.", "paraphrase"),
    ):
        cases.extend(
            _with_variants(
                UtteranceCase(
                    case_id=case_id,
                    utterance=text,
                    expected_intent="document_duplicates",
                    expected_tool="document.duplicates",
                    forbidden_tools=("document.dedup",),
                    side_effects=SIDE_EFFECTS_DOCUMENTS_DUPLICATES,
                    category="documents",
                    source=source,
                )
            )
        )
    cases.append(
        UtteranceCase(
            case_id="doc.dedup.no_plan",
            utterance="Kopyaları çöp kutusuna gönder.",
            expected_intent="document_dedup",
            expected_tool="document.dedup",
            expected_response=RESPONSE_CLARIFY,
            forbidden_tools=("document.duplicates",),
            side_effects=SIDE_EFFECTS_NONE,
            category="documents",
            source="regression",
        )
    )
    cases.append(
        UtteranceCase(
            case_id="doc.dedup.after_plan",
            utterance="Kopyaları çöp kutusuna gönder.",
            expected_intent="document_dedup",
            expected_tool="document.dedup",
            preceding_turns=(("Yinelenen dosyaları bul.", "document.duplicates"),),
            side_effects=SIDE_EFFECTS_DOCUMENTS_DUPLICATES | SIDE_EFFECTS_DOCUMENTS_TRASH,
            category="documents",
            source="canonical",
        )
    )
    # 141/139: the picture's text (already read) and its headers.
    cases.append(
        UtteranceCase(
            case_id="doc.image.text",
            utterance="Görseldeki metni oku.",
            expected_intent="image_text",
            expected_tool="document.read",
            forbidden_tools=("operator.inspect", "operator.see"),
            side_effects=SIDE_EFFECTS_NONE,
            context=CTX_IMAGE_FOCUSED,
            category="documents",
            source="canonical",
        )
    )
    cases.append(
        UtteranceCase(
            case_id="doc.image.meta",
            utterance="Fotoğrafın bilgilerini oku.",
            expected_intent="image_metadata",
            expected_tool="document.inspect",
            side_effects=SIDE_EFFECTS_NONE,
            context=CTX_IMAGE_FOCUSED,
            category="documents",
            source="canonical",
        )
    )
    # 142: an archive focused as a file - inspected, never extracted.
    cases.append(
        UtteranceCase(
            case_id="doc.archive.inspect",
            utterance="Arşivin içinde ne var?",
            expected_intent="document_inspect",
            expected_tool="document.inspect",
            forbidden_tools=("document.read",),
            side_effects=SIDE_EFFECTS_DOCUMENTS_INSPECT,
            context=CTX_ARCHIVE_FOCUSED,
            category="documents",
            source="canonical",
        )
    )
    # 169: the two-documents phrasing.
    cases.append(
        UtteranceCase(
            case_id="doc.cmp.two",
            utterance="Bu iki dokümanı karşılaştır.",
            expected_intent="document_compare",
            expected_tool="document.compare",
            side_effects=SIDE_EFFECTS_DOCUMENTS_COMPARE,
            context=CTX_DOCUMENT_FOCUSED,
            category="documents",
            source="canonical",
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


def _document_b34_cases() -> list[UtteranceCase]:
    """B34 req 153-167, 170 through the real relay: every mutation journaled, the risky
    ones proposals until "Uygula.", every applied one undoable. The fake desktop's
    overlay (tests.documents_support.MUTATED) is what the receipts are read back from."""
    cases: list[UtteranceCase] = []
    focus_notes = (("notlar dosyasını bul.", "file.search"),)
    # 154: a new text file - low risk, applied at once, journaled, undoable.
    for case_id, text, source in (
        ("doc.write.new", "gunluk.md adında bir dosya oluştur.", "canonical"),
        ("doc.write.asr", "gunluk.md adinda bir dosya olustur", "asr_noise"),
    ):
        cases.append(
            UtteranceCase(
                case_id=case_id,
                utterance=text,
                expected_intent="document_write",
                expected_tool="document.write",
                tool_arguments={"content": "# Günlük\n"},
                expected={"speech_contains": "oluşturdum"},
                side_effects=SIDE_EFFECTS_DOCUMENTS_MUTATION,
                context=CTX_NONE,
                category="documents",
                source=source,
            )
        )
    # 155: append - low risk, applied at once.
    cases.append(
        UtteranceCase(
            case_id="doc.append.canonical",
            utterance="Bu dosyanın sonuna toplantı notu ekle.",
            expected_intent="document_append",
            expected_tool="document.append",
            preceding_turns=focus_notes,
            expected={"speech_contains": "sonuna ekledim"},
            side_effects=SIDE_EFFECTS_DOCUMENTS_MUTATION,
            context=CTX_NONE,
            category="documents",
            source="canonical",
        )
    )
    # 153/167: an edit was a proposal until the owner's decision of 2026-09-19 ("Tüm 2.
    # ses onaylarını kaldır", mail excepted): it is applied on the first sentence now, with
    # the backup and the undo that the second word used to stand in front of.
    for case_id, text, source in (
        ("doc.edit.canonical", "Bu dosyada Bütçe yerine Tahmin yaz.", "canonical"),
        ("doc.edit.asr", "bu dosyada butce yerine tahmin yaz", "asr_noise"),
    ):
        cases.append(
            UtteranceCase(
                case_id=case_id,
                utterance=text,
                expected_intent="document_edit",
                expected_tool="document.edit",
                preceding_turns=focus_notes,
                expected={"speech_contains": "değiştirdim"},
                side_effects=SIDE_EFFECTS_DOCUMENTS_MUTATION,
                context=CTX_NONE,
                category="documents",
                source=source,
            )
        )
    # 170: "güncelle ve kaydet" is the same shape with the model's new content - applied.
    cases.append(
        UtteranceCase(
            case_id="doc.edit.update_save",
            utterance="Bu belgeyi güncelle ve kaydet.",
            expected_intent="document_edit",
            expected_tool="document.edit",
            tool_arguments={"content": "# Yeni\n"},
            preceding_turns=focus_notes,
            expected={"speech_contains": "değiştirdim"},
            side_effects=SIDE_EFFECTS_DOCUMENTS_MUTATION,
            context=CTX_NONE,
            category="documents",
            source="paraphrase",
        )
    )
    # 166: "Uygula." applied the proposal this session heard. Since 2026-09-19 nothing is
    # pending after an edit (it was applied on the first word), so the bare "uygula" is the
    # refusal below in every case - and "Vazgeç." has nothing of the document's to discard.
    cases.append(
        UtteranceCase(
            case_id="doc.apply.nothing_pending",
            utterance="Uygula.",
            expected_intent="none",
            expected_tool=None,
            expected_response=RESPONSE_NONE,
            forbidden_tools=("document.apply",),
            side_effects=SIDE_EFFECTS_NONE,
            context=CTX_NONE,
            category="documents",
            source="regression",
            regression_issue_id=(
                "B34: a bare 'uygula' with nothing pending is nobody's confirmation"
            ),
        )
    )
    # 156-158: rename / move / copy were proposals; since 2026-09-19 they are done on the
    # owner's word, each read back as done and each undoable.
    for case_id, text, intent, tool, done in (
        (
            "doc.rename.canonical",
            "Bu dosyanın adını gunluk-notlari.md yap.",
            "document_rename",
            "document.rename",
            "adını",
        ),
        (
            "doc.move.canonical",
            "Bu dosyayı Masaüstüne taşı.",
            "document_move",
            "document.move",
            "taşıdım",
        ),
        (
            "doc.copy.canonical",
            "Bu dosyayı kopyala.",
            "document_copy",
            "document.copy",
            "kopyaladım",
        ),
        (
            "doc.copy.named",
            "Bu dosyayı yedek.md adıyla kopyala.",
            "document_copy",
            "document.copy",
            "kopyaladım",
        ),
    ):
        cases.append(
            UtteranceCase(
                case_id=case_id,
                utterance=text,
                expected_intent=intent,
                expected_tool=tool,
                preceding_turns=focus_notes,
                expected={"speech_contains": done},
                side_effects=SIDE_EFFECTS_DOCUMENTS_MUTATION,
                context=CTX_NONE,
                category="documents",
                source="canonical",
            )
        )
    # 160: undo of the last applied change (the write above), read back.
    cases.append(
        UtteranceCase(
            case_id="doc.undo.after_append",
            utterance="Son değişikliği geri al.",
            expected_intent="document_undo",
            expected_tool="document.undo",
            preceding_turns=(
                *focus_notes,
                ("Bu dosyanın sonuna toplantı notu ekle.", "document.append"),
            ),
            expected={"speech_contains": "Geri aldım"},
            side_effects=SIDE_EFFECTS_DOCUMENTS_MUTATION,
            context=CTX_NONE,
            category="documents",
            source="canonical",
        )
    )
    cases.append(
        UtteranceCase(
            case_id="doc.undo.nothing",
            utterance="Son değişikliği geri al.",
            expected_intent="document_undo",
            expected_tool="document.undo",
            expected_response=RESPONSE_REFUSED,
            expected={"error_class": "nothing_to_undo"},
            side_effects=SIDE_EFFECTS_NONE,
            context=CTX_NONE,
            category="documents",
            source="canonical",
        )
    )
    # 164: the version history of the focused file.
    cases.append(
        UtteranceCase(
            case_id="doc.versions.canonical",
            utterance="Bu dosyanın sürüm geçmişini göster.",
            expected_intent="document_versions",
            expected_tool="document.versions",
            preceding_turns=focus_notes,
            expected={"speech_contains": "kayıtlı bir değişiklik yok"},
            side_effects=frozenset({"file.search", "file.locate"}),
            context=CTX_NONE,
            category="documents",
            source="canonical",
        )
    )
    # An Office document is never edited byte by byte: refused by name, nothing touched.
    cases.append(
        UtteranceCase(
            case_id="doc.edit.office_refused",
            utterance="Bu belgeyi güncelle ve kaydet.",
            expected_intent="document_edit",
            expected_tool="document.edit",
            tool_arguments={"content": "x"},
            expected_response=RESPONSE_REFUSED,
            expected={"error_class": "not_text"},
            side_effects=frozenset({"file.locate"}),
            context=CTX_PPTX_FOCUSED,
            category="documents",
            source="regression",
        )
    )
    # The neighbours keep their owners (measured 2026-09-15 before the matchers were written).
    for case_id, text, intent, tool in (
        ("doc.neg.mail_delete_is_not_a_file", "Tüm mailleri sil.", "none", None),
        ("doc.neg.type_text_stays_operator", "Buraya merhaba yaz.", "type_text", "operator.type"),
    ):
        cases.append(
            UtteranceCase(
                case_id=case_id,
                utterance=text,
                expected_intent=intent,
                expected_tool=tool,
                expected_response=RESPONSE_NONE if tool is None else RESPONSE_OK,
                forbidden_tools=("document.delete", "document.edit", "document.write"),
                side_effects=SIDE_EFFECTS_NONE if tool is None else SIDE_EFFECTS_OPERATOR_TYPE,
                context=CTX_NONE if tool is None else CTX_WINDOW_FOCUSED,
                category="documents",
                source="regression",
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
    # "Bu dosyayı sil." reached no tool at all until B34 (ADR-0083 decision 7), was a
    # PROPOSAL from B34 (ADR-0141), and since the owner's decision of 2026-09-19 is the
    # Recycle Bin on the first word - with the backup, and "geri al" bringing it back. Never
    # the dedup tool, and never a permanent delete.
    cases.extend(
        _with_variants(
            UtteranceCase(
                case_id="doc.delete.first_word",
                utterance="Bu dosyayı sil.",
                expected_intent="document_delete",
                expected_tool="document.delete",
                expected={"speech_contains": "çöp kutusuna gönderdim"},
                forbidden_tools=("document.dedup",),
                side_effects=SIDE_EFFECTS_DOCUMENTS_MUTATION,
                context=CTX_FILE_FOCUSED,
                category="documents",
                source="canonical",
                regression_issue_id=(
                    "ADR-0083 decision 7 -> B34 (ADR-0141) -> owner 2026-09-19: the bin, "
                    "with a backup, on the first word"
                ),
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
        *_document_b32_cases(),
        *_document_b34_cases(),
        *_document_inspect_cases(),
        *_document_answer_cases(),
        *_document_common_points_cases(),
        *_document_previous_cases(),
        *_document_negative_cases(),
    ]


def _mail_calendar_cases() -> list[UtteranceCase]:
    cases: list[UtteranceCase] = []

    # ---------------------------------------------------------------------- mail: READ
    inbox = [
        ("mc.inbox.1", "Gelen kutumda ne var?", "canonical"),
        ("mc.inbox.2", "Okunmamış maillerim var mı?", "canonical"),
        ("mc.inbox.3", "Gelen kutumu kontrol eder misin?", "paraphrase"),
        ("mc.inbox.4", "Hiç okunmamış mailim var mı acaba?", "paraphrase"),
    ]
    for case_id, text, source in inbox:
        cases.extend(
            _with_variants(
                UtteranceCase(
                    case_id=case_id,
                    utterance=text,
                    expected_intent="mail_inbox",
                    expected_tool="mail.inbox",
                    category="mail_calendar",
                    source=source,
                )
            )
        )
    search = [
        ("mc.search.1", "Fatura maillerini bul.", "canonical", "fatura"),
        ("mc.search.2", "Bütçeyle ilgili maili bulur musun?", "paraphrase", "bütçe"),
    ]
    for case_id, text, source, query in search:
        cases.extend(
            _with_variants(
                UtteranceCase(
                    case_id=case_id,
                    utterance=text,
                    expected_intent="mail_search",
                    expected_tool="mail.search",
                    category="mail_calendar",
                    source=source,
                    tool_arguments={"query": query},
                )
            )
        )
    read = [
        ("mc.read.1", "Ali'den gelen son maili oku.", "canonical"),
        ("mc.read.2", "Ali'nin son mailini okur musun?", "paraphrase"),
    ]
    for case_id, text, source in read:
        cases.extend(
            _with_variants(
                UtteranceCase(
                    case_id=case_id,
                    utterance=text,
                    expected_intent="mail_read",
                    expected_tool="mail.read",
                    category="mail_calendar",
                    source=source,
                    tool_arguments={"target": "Ali"},
                )
            )
        )
    thread = [
        ("mc.thread.1", "Bu konuşmanın tamamını oku.", "canonical"),
        ("mc.thread.2", "Bütün yazışmayı okur musun?", "paraphrase"),
    ]
    for case_id, text, source in thread:
        cases.extend(
            _with_variants(
                UtteranceCase(
                    case_id=case_id,
                    utterance=text,
                    expected_intent="mail_thread",
                    expected_tool="mail.thread",
                    context=CTX_MESSAGE_FOCUSED,
                    category="mail_calendar",
                    source=source,
                )
            )
        )

    # B45 (req 347, 348): a focused message's attachments, listed and saved.
    attachments = [
        ("mc.attachments.1", "Bu mailin eklerini göster.", "canonical"),
        ("mc.attachments.2", "Ekte ne var?", "paraphrase"),
    ]
    for case_id, text, source in attachments:
        cases.extend(
            _with_variants(
                UtteranceCase(
                    case_id=case_id,
                    utterance=text,
                    expected_intent="mail_attachments",
                    expected_tool="mail.attachments",
                    context=CTX_MESSAGE_FOCUSED,
                    category="mail_calendar",
                    source=source,
                )
            )
        )
    save_attachment = [
        ("mc.save_attachment.1", "Eki bilgisayarıma kaydet.", "canonical"),
        ("mc.save_attachment.2", "Bu maildeki eki indir.", "paraphrase"),
    ]
    for case_id, text, source in save_attachment:
        cases.extend(
            _with_variants(
                UtteranceCase(
                    case_id=case_id,
                    utterance=text,
                    expected_intent="mail_save_attachment",
                    expected_tool="mail.save_attachment",
                    context=CTX_MESSAGE_FOCUSED,
                    side_effects=SIDE_EFFECTS_ARTIFACT_OPEN,
                    category="mail_calendar",
                    source=source,
                )
            )
        )

    # ------------------------------------------------------------------- mail: PREPARE
    draft_reply = [
        ("mc.draft_reply.1", "Buna cevap yaz: yarın 10'da uygunum.", "canonical"),
        ("mc.draft_reply.2", "Şuna cevap yazar mısın: yarın 10'da uygunum.", "paraphrase"),
    ]
    for case_id, text, source in draft_reply:
        cases.extend(
            _with_variants(
                UtteranceCase(
                    case_id=case_id,
                    utterance=text,
                    expected_intent="mail_draft_reply",
                    expected_tool="mail.draft",
                    context=CTX_MESSAGE_FOCUSED,
                    category="mail_calendar",
                    source=source,
                    tool_arguments={"body": "Yarın 10'da uygunum."},
                )
            )
        )
    cases.extend(
        _with_variants(
            UtteranceCase(
                case_id="mc.draft_new.1",
                utterance="Yeni mail: Ayşe'ye, konu toplantı, yarın gelemiyorum.",
                expected_intent="mail_draft_new",
                expected_tool="mail.draft",
                category="mail_calendar",
                source="canonical",
                tool_arguments={
                    "to": "ayse.kaya@example.com",
                    "subject": "Toplantı",
                    "body": "Yarın gelemiyorum.",
                },
            )
        )
    )
    cases.extend(
        _with_variants(
            UtteranceCase(
                case_id="mc.draft_new.2",
                utterance="Ali'ye mail gönder.",
                expected_intent="mail_draft_new",
                expected_tool="mail.draft",
                expected={"routed_not": "sent"},
                category="mail_calendar",
                source="canonical",
                tool_arguments={
                    "to": "ali.yilmaz@example.com",
                    "subject": "Merhaba",
                    "body": "Merhaba Ali,",
                },
            )
        )
    )
    cases.extend(
        _with_variants(
            UtteranceCase(
                case_id="mc.read_draft.1",
                utterance="Cevabı oku.",
                expected_intent="mail_read_draft",
                expected_tool="mail.read_draft",
                context=CTX_DRAFT_READ_BACK,
                category="mail_calendar",
                source="canonical",
            )
        )
    )
    cases.extend(
        _with_variants(
            UtteranceCase(
                case_id="mc.edit_draft.1",
                utterance="Konuyu 'Plan onayı' yap.",
                expected_intent="mail_edit_draft",
                expected_tool="mail.edit_draft",
                context=CTX_DRAFT_READ_BACK,
                category="mail_calendar",
                source="canonical",
                tool_arguments={"subject": "Plan onayı"},
            )
        )
    )

    # ------------------------------------------------------- mail: EXTERNAL MUTATION
    cases.append(
        UtteranceCase(
            case_id="mc.send.confirmed",
            utterance="Gönder.",
            expected_intent="mail_send",
            expected_tool="mail.send",
            context=CTX_DRAFT_READ_BACK,
            side_effects=SIDE_EFFECTS_MAIL_SEND,
            category="mail_calendar",
            source="canonical",
            # ADR-0084 addendum 2: the REAL read-back-then-confirm sequence, through the
            # real tool, in this session, at an EARLIER turn than the confirmation below.
            preceding_turns=(("Cevabı oku.", "mail.read_draft"),),
        )
    )
    cases.append(
        UtteranceCase(
            case_id="mc.send.no_owner_turn",
            utterance="Cevabı oku.",
            expected_intent="mail_read_draft",
            expected_tool="mail.read_draft",
            context=CTX_DRAFT_READ_BACK,
            forbidden_tools=("mail.send",),
            category="mail_calendar",
            source="canonical",
            regression_issue_id=(
                "M21 security review H1: a model-issued mail.send with no owner "
                "MAIL_SEND turn behind it is refused confirmation_not_owner, nothing sent"
            ),
        )
    )
    cases.append(
        UtteranceCase(
            case_id="mc.send.no_readback",
            utterance="Gönder.",
            expected_intent="mail_send",
            expected_tool="mail.send",
            expected_response=RESPONSE_CLARIFY,
            context=CTX_NONE,
            category="mail_calendar",
            source="canonical",
            regression_issue_id="M21 spec §1: no read-back, no send",
        )
    )
    cases.extend(
        _with_variants(
            UtteranceCase(
                case_id="mc.discard.mail.1",
                utterance="Gönderme.",
                expected_intent="discard",
                expected_tool="mail.discard",
                context=CTX_DRAFT_READ_BACK,
                category="mail_calendar",
                source="canonical",
            )
        )
    )
    cases.extend(
        _with_variants(
            UtteranceCase(
                case_id="mc.discard.mail.2",
                utterance="Vazgeç.",
                expected_intent="discard",
                expected_tool="mail.discard",
                context=CTX_DRAFT_READ_BACK,
                category="mail_calendar",
                source="canonical",
            )
        )
    )

    # ---------------------------------------------------------------- calendar: READ
    agenda = [
        ("mc.agenda.1", "Bugün takvimimde ne var?", "canonical"),
        ("mc.agenda.2", "Bugün ajandamda ne var acaba?", "paraphrase"),
    ]
    for case_id, text, source in agenda:
        cases.extend(
            _with_variants(
                UtteranceCase(
                    case_id=case_id,
                    utterance=text,
                    expected_intent="calendar_agenda",
                    expected_tool="calendar.agenda",
                    category="mail_calendar",
                    source=source,
                    tool_arguments={"when_spoken": "bugün"},
                )
            )
        )
    find_slot = [
        ("mc.slot.1", "Yarın öğleden sonra boş muyum?", "canonical"),
        ("mc.slot.2", "Cuma 60 dakikalık boşluk bul.", "canonical"),
        ("mc.slot.3", "Cuma günü bir saatlik boş vaktim var mı?", "paraphrase"),
    ]
    for case_id, text, source in find_slot:
        cases.extend(
            _with_variants(
                UtteranceCase(
                    case_id=case_id,
                    utterance=text,
                    expected_intent="calendar_find_slot",
                    expected_tool="calendar.find_slot",
                    category="mail_calendar",
                    source=source,
                    tool_arguments={"when_spoken": text},
                )
            )
        )

    # ------------------------------------------------------------- calendar: PREPARE
    cases.extend(
        _with_variants(
            UtteranceCase(
                case_id="mc.propose.new.1",
                utterance="Perşembe 15'e diş hekimi ekle.",
                expected_intent="calendar_propose",
                expected_tool="calendar.propose",
                category="mail_calendar",
                source="canonical",
                tool_arguments={"when_spoken": "Perşembe 15'e", "summary": "Diş hekimi"},
            )
        )
    )
    # B46 (req 356, 357): recurrence and reminder words ride on the same PREPARE sentence.
    for case_id, text, when, summary, source in (
        (
            "mc.propose.recurring.1",
            "Her pazartesi 10'da ekip toplantısı ekle.",
            "Her pazartesi 10'da",
            "Ekip toplantısı",
            "canonical",
        ),
        (
            "mc.propose.weekdays.1",
            "Hafta içi her gün 9'da stand-up ekle.",
            "Hafta içi her gün 9'da",
            "Stand-up",
            "paraphrase",
        ),
        (
            "mc.propose.reminder.1",
            "Yarın 15'e diş hekimi ekle, bir saat önce hatırlat.",
            "Yarın 15'e, bir saat önce hatırlat",
            "Diş hekimi",
            "canonical",
        ),
    ):
        cases.extend(
            _with_variants(
                UtteranceCase(
                    case_id=case_id,
                    utterance=text,
                    expected_intent="calendar_propose",
                    expected_tool="calendar.propose",
                    category="mail_calendar",
                    source=source,
                    tool_arguments={"when_spoken": when, "summary": summary},
                )
            )
        )
    cases.extend(
        _with_variants(
            UtteranceCase(
                case_id="mc.propose.reschedule.1",
                utterance="Bunu bir saat ertele.",
                expected_intent="calendar_propose",
                expected_tool="calendar.propose",
                context=CTX_EVENT_FOCUSED,
                category="mail_calendar",
                source="canonical",
                tool_arguments={"when_spoken": "bir saat"},
            )
        )
    )
    cases.extend(
        _with_variants(
            UtteranceCase(
                case_id="mc.read_proposal.1",
                utterance="Öneriyi oku.",
                expected_intent="calendar_read_proposal",
                expected_tool="calendar.read_proposal",
                context=CTX_PROPOSAL_READ_BACK,
                category="mail_calendar",
                source="canonical",
            )
        )
    )

    # ----------------------------------------------------- calendar: EXTERNAL MUTATION
    cases.append(
        UtteranceCase(
            case_id="mc.commit.confirmed",
            utterance="Onayla.",
            expected_intent="calendar_commit",
            expected_tool="calendar.commit",
            context=CTX_PROPOSAL_READ_BACK,
            side_effects=SIDE_EFFECTS_CALENDAR_COMMIT,
            category="mail_calendar",
            source="canonical",
            # ADR-0084 addendum 2: see mc.send.confirmed's identical comment.
            preceding_turns=(("Öneriyi oku.", "calendar.read_proposal"),),
        )
    )
    cases.append(
        UtteranceCase(
            case_id="mc.commit.no_owner_turn",
            utterance="Öneriyi oku.",
            expected_intent="calendar_read_proposal",
            expected_tool="calendar.read_proposal",
            context=CTX_PROPOSAL_READ_BACK,
            forbidden_tools=("calendar.commit",),
            category="mail_calendar",
            source="canonical",
            regression_issue_id=(
                "M21 security review H1: a model-issued calendar.commit with no owner "
                "CALENDAR_COMMIT turn behind it is refused confirmation_not_owner, "
                "nothing committed"
            ),
        )
    )
    cases.append(
        UtteranceCase(
            case_id="mc.commit.tamam_ekle",
            utterance="Tamam, ekle.",
            expected_intent="calendar_commit",
            expected_tool="calendar.commit",
            context=CTX_PROPOSAL_READ_BACK,
            side_effects=SIDE_EFFECTS_CALENDAR_COMMIT,
            category="mail_calendar",
            source="paraphrase",
            preceding_turns=(("Öneriyi oku.", "calendar.read_proposal"),),
        )
    )
    cases.append(
        UtteranceCase(
            case_id="mc.commit.no_readback",
            utterance="Onayla.",
            expected_intent="calendar_commit",
            expected_tool="calendar.commit",
            expected_response=RESPONSE_CLARIFY,
            context=CTX_NONE,
            category="mail_calendar",
            source="canonical",
            regression_issue_id="M21 spec §1: no read-back, no commit",
        )
    )
    cases.extend(
        _with_variants(
            UtteranceCase(
                case_id="mc.discard.calendar.1",
                utterance="Vazgeç.",
                expected_intent="discard",
                expected_tool="calendar.discard",
                context=CTX_PROPOSAL_READ_BACK,
                category="mail_calendar",
                source="canonical",
            )
        )
    )

    # --------------------------------------------------------------------- negatives
    cases.extend(
        _with_variants(
            UtteranceCase(
                case_id="mc.neg.delete_all",
                utterance="Tüm mailleri sil.",
                expected_intent="none",
                expected_tool=None,
                expected_response=RESPONSE_CONTROL,
                category="mail_calendar",
                source="canonical",
                regression_issue_id="ADR-0084 decision 2: no delete/move/mass action",
            )
        )
    )
    cases.extend(
        _with_variants(
            UtteranceCase(
                case_id="mc.neg.secret",
                utterance="Şifremi Ali'ye maille.",
                expected_intent="none",
                expected_tool=None,
                expected_response=RESPONSE_CONTROL,
                category="mail_calendar",
                source="canonical",
                regression_issue_id="ADR-0084: never draft a secret",
            )
        )
    )
    cases.extend(
        _with_variants(
            UtteranceCase(
                case_id="mc.neg.technical_unchanged",
                utterance="Bunu teknik anlat.",
                expected_intent="technical",
                expected_tool="research.explain",
                expected_target="current",
                expected={"level": "technical"},
                forbidden_tools=("research.start",),
                context=CTX_RESEARCH_FOCUS_B,
                category="mail_calendar",
                source="regression",
                regression_issue_id="M21 must not touch the M18.2 technical-explain path",
            )
        )
    )

    return cases


# ------------------------------------------------------- M22: the Artifact Factory


def _artifact_create_cases() -> list[UtteranceCase]:
    cases: list[UtteranceCase] = []
    for case_id, text, source in (
        (
            "art.create.spreadsheet",
            "Bana bir bütçe tablosu yap: kira 12000, maaş 45000, yazılım 8000.",
            "canonical",
        ),
        ("art.create.document", "Toplantı notlarını Word belgesi yap.", "canonical"),
        (
            "art.create.presentation",
            "Üç slaytlık bir sunum hazırla: giriş, bulgular, sonuç.",
            "canonical",
        ),
        (
            "art.create.dataset",
            "Bana harcamalarımın bir listesini hazırla: kira 12000, market 3000.",
            "canonical",
        ),
        ("art.create.page", "Bana boş bir sayfa hazırla.", "canonical"),
        (
            "art.create.spreadsheet.para",
            "Bir bütçe tablosu yapar mısın? Kira 12000, maaş 45000.",
            "paraphrase",
        ),
        (
            "art.create.document.para",
            "Toplantı notlarını bir Word belgesi hazırlar mısın?",
            "paraphrase",
        ),
        (
            "art.create.presentation.para",
            "Üç slaytlık bir sunum yapsana: giriş, bulgular, sonuç.",
            "paraphrase",
        ),
        ("art.create.page.para", "Bana bir sayfa oluştur.", "paraphrase"),
        (
            "art.create.presentation.new",
            "Yeni bir sunum yap: açılış, demo, kapanış.",
            "canonical",
        ),
        ("art.create.spreadsheet.single", "Bir gider tablosu yap: kira 12000.", "canonical"),
        (
            "art.create.dataset.para",
            "Harcama listemi hazırlar mısın? Market 3000, ulaşım 500.",
            "paraphrase",
        ),
    ):
        cases.extend(
            _with_variants(
                UtteranceCase(
                    case_id=case_id,
                    utterance=text,
                    expected_intent="artifact_create",
                    expected_tool="artifact.create",
                    side_effects=SIDE_EFFECTS_NONE,
                    context=CTX_NONE,
                    category="artifacts",
                    source=source,
                )
            )
        )
    # The "never invented" rule, proven end to end: the model's OWN spec argument
    # carries a number the owner never said (99999) alongside the one they did
    # (12000) — ArtifactSpec refuses to construct it, and the tool answers with a
    # truthful refusal rather than a file with an invented figure in it.
    cases.append(
        UtteranceCase(
            case_id="art.create.never_invented",
            utterance="Bana bir bütçe tablosu yap: kira 12000.",
            expected_intent="artifact_create",
            expected_tool="artifact.create",
            expected_response=RESPONSE_REFUSED,
            expected={"error_class": "validation_error"},
            tool_arguments={
                "spec": {
                    "kind": "spreadsheet",
                    "title": "Bütçe",
                    "sheets": [
                        {
                            "name": "Özet",
                            "columns": ["Kalem", "Tutar"],
                            "rows": [["Kira", 12000], ["Bilinmeyen", 99999]],
                        }
                    ],
                }
            },
            side_effects=SIDE_EFFECTS_NONE,
            context=CTX_NONE,
            category="artifacts",
            source="regression",
            regression_issue_id="M22 spec §1: never invented",
        )
    )
    return cases


def _artifact_render_cases() -> list[UtteranceCase]:
    cases: list[UtteranceCase] = []
    for case_id, text, context, source in (
        ("art.render.pdf", "Bunu PDF yap.", CTX_DOCUMENT_ARTIFACT_FOCUSED, "canonical"),
        ("art.render.excel", "Bunu Excel yap.", CTX_ARTIFACT_FOCUSED, "canonical"),
        (
            "art.render.para",
            "Bunu PDF olarak da hazırlar mısın?",
            CTX_DOCUMENT_ARTIFACT_FOCUSED,
            "paraphrase",
        ),
    ):
        cases.extend(
            _with_variants(
                UtteranceCase(
                    case_id=case_id,
                    utterance=text,
                    expected_intent="artifact_create",
                    expected_tool="artifact.render",
                    side_effects=SIDE_EFFECTS_NONE,
                    context=context,
                    category="artifacts",
                    source=source,
                )
            )
        )
    return cases


def _artifact_open_cases() -> list[UtteranceCase]:
    cases: list[UtteranceCase] = []
    for case_id, text, source in (
        ("art.open.this", "Bunu aç.", "canonical"),
        ("art.open.last", "Son ürettiğin dosyayı aç.", "canonical"),
        ("art.open.para", "Bunu açar mısın?", "paraphrase"),
        ("art.open.asr", "bunu ac", "asr_noise"),
    ):
        cases.extend(
            _with_variants(
                UtteranceCase(
                    case_id=case_id,
                    utterance=text,
                    expected_intent="artifact_open",
                    expected_tool="artifact.open",
                    side_effects=SIDE_EFFECTS_ARTIFACT_OPEN,
                    context=CTX_ARTIFACT_FOCUSED,
                    category="artifacts",
                    source=source,
                )
            )
        )
    cases.extend(
        _with_variants(
            UtteranceCase(
                case_id="art.open.previous",
                utterance="Önceki dosyayı aç.",
                expected_intent="artifact_open",
                expected_tool="artifact.open",
                side_effects=SIDE_EFFECTS_ARTIFACT_OPEN,
                context=CTX_ARTIFACT_FOCUSED,
                category="artifacts",
                source="canonical",
            )
        )
    )
    # Negative (task brief): "Bunu aç." with nothing EVER produced -> an honest
    # clarification, never a guess and never a crash.
    cases.extend(
        _with_variants(
            UtteranceCase(
                case_id="art.neg.open_nothing_produced",
                utterance="Bunu aç.",
                expected_intent="artifact_open",
                expected_tool="artifact.open",
                expected_response=RESPONSE_CLARIFY,
                side_effects=SIDE_EFFECTS_NONE,
                context=CTX_NONE,
                category="artifacts",
                source="regression",
                regression_issue_id="M22 spec §5: nothing produced -> clarification",
            )
        )
    )
    return cases


def _artifact_list_cases() -> list[UtteranceCase]:
    cases: list[UtteranceCase] = []
    for case_id, text, context, source in (
        ("art.list.nothing", "Neler ürettin?", CTX_NONE, "canonical"),
        ("art.list.something", "Ne oluşturdun bugüne kadar?", CTX_ARTIFACT_FOCUSED, "paraphrase"),
        ("art.list.which", "Hangi dosyaları yaptın?", CTX_ARTIFACT_FOCUSED, "paraphrase"),
    ):
        cases.extend(
            _with_variants(
                UtteranceCase(
                    case_id=case_id,
                    utterance=text,
                    expected_intent="artifact_list",
                    expected_tool="artifact.list",
                    side_effects=SIDE_EFFECTS_NONE,
                    context=context,
                    category="artifacts",
                    source=source,
                )
            )
        )
    return cases


def _artifact_validate_cases() -> list[UtteranceCase]:
    cases: list[UtteranceCase] = []
    for case_id, text, source in (
        ("art.validate.this", "Bu dosya doğru mu?", "canonical"),
        ("art.validate.bare", "Doğru mu?", "paraphrase"),
        ("art.validate.numbers", "Rakamlar doğru mu?", "paraphrase"),
        ("art.validate.asr", "bu dosya dogru mu", "asr_noise"),
    ):
        cases.extend(
            _with_variants(
                UtteranceCase(
                    case_id=case_id,
                    utterance=text,
                    expected_intent="artifact_validate",
                    expected_tool="artifact.validate",
                    side_effects=SIDE_EFFECTS_NONE,
                    context=CTX_ARTIFACT_FOCUSED,
                    category="artifacts",
                    source=source,
                )
            )
        )
    return cases


def _artifact_negative_cases() -> list[UtteranceCase]:
    cases: list[UtteranceCase] = []
    # "Bunu sil." was M22's deliberate miss: its five tools had no delete. B42 (req 412)
    # gives the artifact in focus a delete under the owner's policy, and under the default
    # policy (confirm) the first call only ASKS - so the same sentence now reaches
    # artifact.delete and deletes nothing, touching no device. Kept as the regression that
    # proves the change was meant, not drifted into.
    cases.extend(
        _with_variants(
            UtteranceCase(
                case_id="art.delete.asks_first",
                utterance="Bunu sil.",
                expected_intent="artifact_delete",
                expected_tool="artifact.delete",
                side_effects=SIDE_EFFECTS_NONE,
                context=CTX_ARTIFACT_FOCUSED,
                category="artifacts",
                source="regression",
                regression_issue_id="B42 req 412: delete under the owner's policy, asking first",
            )
        )
    )
    # "Bunu teknik anlat." stays exactly what M18.2/M21 already made it — the SAME
    # assertion mc.neg.technical_unchanged makes, kept here too so the artifacts
    # category proves it on its own (task brief: "'Bunu teknik anlat.' unchanged").
    cases.extend(
        _with_variants(
            UtteranceCase(
                case_id="art.neg.technical_unchanged",
                utterance="Bunu teknik anlat.",
                expected_intent="technical",
                expected_tool="research.explain",
                expected_target="current",
                expected={"level": "technical"},
                forbidden_tools=("research.start",),
                context=CTX_RESEARCH_FOCUS_B,
                category="artifacts",
                source="regression",
                regression_issue_id="M22 must not touch the M18.2 technical-explain path",
            )
        )
    )
    # Independent security review of M22's Cloud Core half (ADR-0085 addendum 6).
    #
    # MEDIUM — the "never invented" rule used to be OPT-IN: a numberless utterance
    # left ``spoken_numbers`` unset entirely (the tool only force-set it when the
    # router found numbers at all), so a model-side spec carrying an invented number
    # sailed straight through. "Bir tablo yap." says no number at all; the harness
    # makes the model-side spec carry one (12000) anyway — refused, nothing rendered.
    cases.append(
        UtteranceCase(
            case_id="art.neg.invented",
            utterance="Bir tablo yap.",
            expected_intent="artifact_create",
            expected_tool="artifact.create",
            expected_response=RESPONSE_REFUSED,
            expected={"error_class": "invented_number"},
            tool_arguments={
                "spec": {
                    "kind": "spreadsheet",
                    "title": "Tablo",
                    "sheets": [
                        {
                            "name": "Özet",
                            "columns": ["Kalem", "Tutar"],
                            "rows": [["Kira", 12000]],
                        }
                    ],
                }
            },
            side_effects=SIDE_EFFECTS_NONE,
            context=CTX_NONE,
            category="artifacts",
            source="regression",
            regression_issue_id="ADR-0085 addendum 6 (MEDIUM): a numberless utterance "
            "must still enforce 'no numbers allowed', never skip the rule",
        )
    )
    # LOW — artifact.create had no secret-reference gate at all (mail/typing already
    # refuse "şifre"/"parola"/... , M19 spec §1 invariant 2 / M21). "Şifremi belge
    # yap." asks for a document whose own title (router-extracted: "Şifremi") already
    # names a secret — refused before anything is rendered.
    cases.append(
        UtteranceCase(
            case_id="art.neg.secret",
            utterance="Şifremi belge yap.",
            expected_intent="artifact_create",
            expected_tool="artifact.create",
            expected_response=RESPONSE_REFUSED,
            expected={"error_class": "secret_refused"},
            side_effects=SIDE_EFFECTS_NONE,
            context=CTX_NONE,
            category="artifacts",
            source="regression",
            regression_issue_id="ADR-0085 addendum 6 (LOW): never write a secret into an artifact",
        )
    )
    return cases


def _artifact_cases() -> list[UtteranceCase]:
    return [
        *_artifact_create_cases(),
        *_artifact_render_cases(),
        *_artifact_open_cases(),
        *_artifact_list_cases(),
        *_artifact_validate_cases(),
        *_artifact_negative_cases(),
    ]


def _app_create_cases() -> list[UtteranceCase]:
    cases: list[UtteranceCase] = []
    for case_id, text, source in (
        ("app.create.tracker", "Bana bir görev takip uygulaması yap.", "canonical"),
        (
            "app.create.page",
            "Küçük bir web sayfası uygulaması oluştur: adı Notlarım.",
            "canonical",
        ),
        (
            "app.create.cli",
            "Komut satırı aracı yap: selamla ve say komutları.",
            "canonical",
        ),
        ("app.create.tracker.para", "Bana bir görev takip uygulaması yapar mısın?", "paraphrase"),
        ("app.create.tracker.para2", "Görev takip uygulaması hazırla.", "paraphrase"),
        (
            "app.create.page.para",
            "Küçük bir web sayfası uygulaması yapsana: adı Not Panom.",
            "paraphrase",
        ),
        ("app.create.cli.para", "Bir komut satırı aracı oluşturur musun?", "paraphrase"),
        (
            "app.create.tracker.named",
            "Bana adı Yapılacaklar olan bir görev takip uygulaması yap.",
            "canonical",
        ),
        ("app.create.tracker.asr", "bana bir gorev takip uygulamasi yap", "asr_noise"),
        (
            "app.create.page.asr",
            "kucuk bir web sayfasi uygulamasi olustur adi notlarim",
            "asr_noise",
        ),
    ):
        cases.extend(
            _with_variants(
                UtteranceCase(
                    case_id=case_id,
                    utterance=text,
                    expected_intent="app_factory_create",
                    expected_tool="app.create",
                    side_effects=SIDE_EFFECTS_APP_CREATE,
                    context=CTX_NONE,
                    category="apps",
                    source=source,
                )
            )
        )
    return cases


def _app_run_cases() -> list[UtteranceCase]:
    cases: list[UtteranceCase] = []
    for case_id, text, source in (
        ("app.run.canonical", "Uygulamayı çalıştır.", "canonical"),
        ("app.run.para", "Uygulamayı başlatır mısın?", "paraphrase"),
        ("app.run.para2", "Uygulamayı başlatsana.", "paraphrase"),
        ("app.run.asr", "uygulamayi calistir", "asr_noise"),
    ):
        cases.extend(
            _with_variants(
                UtteranceCase(
                    case_id=case_id,
                    utterance=text,
                    expected_intent="app_factory_run",
                    expected_tool="app.run",
                    side_effects=SIDE_EFFECTS_APP_RUN,
                    context=CTX_APP_SCAFFOLDED,
                    category="apps",
                    source=source,
                )
            )
        )
    return cases


def _app_test_cases() -> list[UtteranceCase]:
    cases: list[UtteranceCase] = []
    for case_id, text, source in (
        ("app.test.canonical", "Testleri çalıştır.", "canonical"),
        ("app.test.para", "Uygulamanın testlerini çalıştırır mısın?", "paraphrase"),
        ("app.test.para2", "Testleri çalıştırsana.", "paraphrase"),
        ("app.test.asr", "testleri calistir", "asr_noise"),
    ):
        cases.extend(
            _with_variants(
                UtteranceCase(
                    case_id=case_id,
                    utterance=text,
                    expected_intent="app_factory_test",
                    expected_tool="app.test",
                    side_effects=SIDE_EFFECTS_APP_TEST,
                    context=CTX_APP_SCAFFOLDED,
                    category="apps",
                    source=source,
                )
            )
        )
    return cases


def _app_stop_cases() -> list[UtteranceCase]:
    cases: list[UtteranceCase] = []
    for case_id, text, source in (
        ("app.stop.canonical", "Uygulamayı durdur.", "canonical"),
        ("app.stop.para", "Uygulamayı durdurur musun?", "paraphrase"),
        ("app.stop.para2", "Uygulamayı kapatsana.", "paraphrase"),
        ("app.stop.asr", "uygulamayi durdur", "asr_noise"),
    ):
        cases.extend(
            _with_variants(
                UtteranceCase(
                    case_id=case_id,
                    utterance=text,
                    expected_intent="app_factory_stop",
                    expected_tool="app.stop",
                    side_effects=SIDE_EFFECTS_APP_STOP,
                    context=CTX_APP_RUNNING,
                    category="apps",
                    source=source,
                )
            )
        )
    return cases


def _app_status_cases() -> list[UtteranceCase]:
    cases: list[UtteranceCase] = []
    for case_id, text, source in (
        ("app.status.canonical", "Uygulama çalışıyor mu?", "canonical"),
        ("app.status.para", "Uygulama hâlâ çalışıyor mu acaba?", "paraphrase"),
        ("app.status.asr", "uygulama calisiyor mu", "asr_noise"),
    ):
        cases.extend(
            _with_variants(
                UtteranceCase(
                    case_id=case_id,
                    utterance=text,
                    expected_intent="app_factory_status",
                    expected_tool="app.status",
                    side_effects=SIDE_EFFECTS_APP_STATUS,
                    context=CTX_APP_RUNNING,
                    category="apps",
                    source=source,
                )
            )
        )
    return cases


def _app_open_cases() -> list[UtteranceCase]:
    cases: list[UtteranceCase] = []
    for case_id, text, source in (
        ("app.open.canonical", "Uygulamayı aç.", "canonical"),
        ("app.open.para", "Uygulamayı açar mısın?", "paraphrase"),
        ("app.open.asr", "uygulamayi ac", "asr_noise"),
    ):
        cases.extend(
            _with_variants(
                UtteranceCase(
                    case_id=case_id,
                    utterance=text,
                    expected_intent="app_factory_open",
                    expected_tool="app.open",
                    side_effects=SIDE_EFFECTS_APP_OPEN,
                    context=CTX_APP_RUNNING,
                    category="apps",
                    source=source,
                )
            )
        )
    return cases


def _app_list_cases() -> list[UtteranceCase]:
    cases: list[UtteranceCase] = []
    for case_id, text, context, source in (
        ("app.list.nothing", "Hangi uygulamaları yaptın?", CTX_NONE, "canonical"),
        (
            "app.list.something",
            "Bugüne kadar hangi uygulamaları yaptın?",
            CTX_APP_SCAFFOLDED,
            "paraphrase",
        ),
        ("app.list.which", "Hangi uygulamaları oluşturdun?", CTX_APP_SCAFFOLDED, "paraphrase"),
        ("app.list.asr", "hangi uygulamalari yaptin", CTX_NONE, "asr_noise"),
    ):
        cases.extend(
            _with_variants(
                UtteranceCase(
                    case_id=case_id,
                    utterance=text,
                    expected_intent="app_factory_list",
                    expected_tool="app.list",
                    side_effects=SIDE_EFFECTS_NONE,
                    context=context,
                    category="apps",
                    source=source,
                )
            )
        )
    return cases


def _app_negative_cases() -> list[UtteranceCase]:
    cases: list[UtteranceCase] = []
    # "Projeyi sil." reaches no tool at all (ADR-0086 decision 5 names no delete tool) -
    # even with a real project focused, the router resolves nothing.
    cases.extend(
        _with_variants(
            UtteranceCase(
                case_id="app.neg.delete",
                utterance="Projeyi sil.",
                expected_intent="none",
                expected_tool=None,
                expected_response=RESPONSE_NONE,
                side_effects=SIDE_EFFECTS_NONE,
                context=CTX_APP_SCAFFOLDED,
                category="apps",
                source="canonical",
                regression_issue_id="M23 spec §5: no delete tool",
            )
        )
    )
    # A name that is already a path is refused BEFORE the device is ever asked (spec's
    # own negative: "a name with a path (..\\x, C:\\x) -> refused").
    cases.append(
        UtteranceCase(
            case_id="app.neg.path_in_name_dotdot",
            utterance="Bana bir görev takip uygulaması yap: adı ..\\x.",
            expected_intent="app_factory_create",
            expected_tool="app.create",
            expected_response=RESPONSE_REFUSED,
            expected={"error_class": "validation_error"},
            tool_arguments={"template": "task-tracker", "name": "..\\x"},
            side_effects=SIDE_EFFECTS_NONE,
            context=CTX_NONE,
            category="apps",
            source="regression",
            regression_issue_id="M23 spec: a project name naming a path is refused "
            "before the device",
        )
    )
    cases.append(
        UtteranceCase(
            case_id="app.neg.path_in_name_drive",
            utterance="Bana bir görev takip uygulaması yap: adı C:\\x.",
            expected_intent="app_factory_create",
            expected_tool="app.create",
            expected_response=RESPONSE_REFUSED,
            expected={"error_class": "validation_error"},
            tool_arguments={"template": "task-tracker", "name": "C:\\x"},
            side_effects=SIDE_EFFECTS_NONE,
            context=CTX_NONE,
            category="apps",
            source="regression",
            regression_issue_id="M23 spec: a project name naming a path is refused "
            "before the device",
        )
    )
    # "Uygulamayı çalıştır." with nothing EVER scaffolded -> an honest clarification,
    # never a guess and never a crash (spec's own negative).
    cases.extend(
        _with_variants(
            UtteranceCase(
                case_id="app.neg.run_nothing_scaffolded",
                utterance="Uygulamayı çalıştır.",
                expected_intent="app_factory_run",
                expected_tool="app.run",
                expected_response=RESPONSE_CLARIFY,
                side_effects=SIDE_EFFECTS_NONE,
                context=CTX_NONE,
                category="apps",
                source="regression",
                regression_issue_id="M23 spec §5: nothing scaffolded -> clarification",
            )
        )
    )
    # "Bunu teknik anlat." stays exactly what M18.2/M21/M22 already made it - the SAME
    # assertion mc.neg.technical_unchanged / art.neg.technical_unchanged make, kept here
    # too so the apps category proves it on its own (task brief: "unchanged").
    cases.extend(
        _with_variants(
            UtteranceCase(
                case_id="app.neg.technical_unchanged",
                utterance="Bunu teknik anlat.",
                expected_intent="technical",
                expected_tool="research.explain",
                expected_target="current",
                expected={"level": "technical"},
                forbidden_tools=("research.start",),
                context=CTX_RESEARCH_FOCUS_B,
                category="apps",
                source="regression",
                regression_issue_id="M23 must not touch the M18.2 technical-explain path",
            )
        )
    )
    return cases


def _app_cases() -> list[UtteranceCase]:
    return [
        *_app_create_cases(),
        *_app_run_cases(),
        *_app_test_cases(),
        *_app_stop_cases(),
        *_app_status_cases(),
        *_app_open_cases(),
        *_app_list_cases(),
        *_app_negative_cases(),
    ]


def _capability_request_cases() -> list[UtteranceCase]:
    cases: list[UtteranceCase] = []
    # A MUTATING operation with no owner authorization on record parks at
    # awaiting_approval BEFORE registering (spec §5/§9's deny-by-default) — but
    # the "testing" stage's OWN evaluation (app.genesis.service._test) already
    # ran the generated tests/evals against the REAL fixture before that park,
    # never a mock (app.genesis.adapter's own module docstring), so a
    # NON-idempotent operation's state has ALREADY moved by the time a bare
    # request (no pre_turn approval) returns — "increment" (by=1, twice: the
    # generated test's own primary-input check + the one eval case) ends up
    # +2, never merely parked-and-untouched. "reset" is idempotent (0 -> 0
    # twice is still 0) and "toggle" flips an EVEN number of times (twice) —
    # both net to NO observable change, which is what makes them a fair
    # "awaiting_approval, untouched" proof instead.
    for case_id, text, source, context, args in (
        (
            "genesis.request.increment",
            "Sayaç kutusunu bir artır.",
            "canonical",
            CTX_COUNTERBOX_RUNNING,
            {"by": 1},
        ),
        (
            "genesis.request.increment.para",
            "Sayacı bir artırır mısın?",
            "paraphrase",
            CTX_COUNTERBOX_RUNNING,
            {"by": 1},
        ),
        # The number the OWNER said, carried literally (spec §6's "numbers spoken are
        # numbers read back"): three and two are different literals reaching the same
        # operation, so a tool that quietly normalised every increment to one would
        # fail here rather than in production.
        (
            "genesis.request.increment.three",
            "Sayacı üç artır.",
            "canonical",
            CTX_COUNTERBOX_RUNNING,
            {"by": 3},
        ),
        (
            "genesis.request.increment.arttir",
            "Sayaç kutusunu iki arttır.",
            "canonical",
            CTX_COUNTERBOX_RUNNING,
            {"by": 2},
        ),
    ):
        cases.extend(
            _with_variants(
                UtteranceCase(
                    case_id=case_id,
                    utterance=text,
                    expected_intent="capability_request",
                    expected_tool="capability.request",
                    side_effects=SIDE_EFFECTS_CAPABILITY_MUTATE,
                    context=context,
                    category="genesis",
                    source=source,
                    tool_arguments={"arguments": args},
                )
            )
        )
    for case_id, text, source, context, args in (
        (
            "genesis.request.reset",
            "Sayaç kutusunu sıfırla.",
            "canonical",
            CTX_COUNTERBOX_RUNNING,
            {},
        ),
        (
            "genesis.request.toggle",
            "Test lambasını aç.",
            "canonical",
            CTX_LAMPBOX_RUNNING,
            {},
        ),
        (
            "genesis.request.toggle.para",
            "Lambayı kapatsana.",
            "paraphrase",
            CTX_LAMPBOX_RUNNING,
            {},
        ),
        (
            "genesis.request.reset.sifirlasana",
            "Sayaç kutusunu sıfırlasana.",
            "paraphrase",
            CTX_COUNTERBOX_RUNNING,
            {},
        ),
        (
            "genesis.request.toggle.degistir",
            "Lambayı değiştir.",
            "canonical",
            CTX_LAMPBOX_RUNNING,
            {},
        ),
    ):
        cases.extend(
            _with_variants(
                UtteranceCase(
                    case_id=case_id,
                    utterance=text,
                    expected_intent="capability_request",
                    expected_tool="capability.request",
                    side_effects=SIDE_EFFECTS_NONE,
                    context=context,
                    category="genesis",
                    source=source,
                    tool_arguments={"arguments": args},
                )
            )
        )
    for case_id, text, source, context in (
        ("genesis.request.read", "Sayaç kaç?", "canonical", CTX_COUNTERBOX_RUNNING),
        (
            "genesis.request.state",
            "Test lambasının durumu ne?",
            "canonical",
            CTX_LAMPBOX_RUNNING,
        ),
        # The target's full name with the same read verb, and the lamp's state asked
        # with the word ("durumda") the STATUS matcher also uses — the status family
        # needs its own "yetenek" noun, so this must stay a read of the lamp itself.
        (
            "genesis.request.read.full",
            "Sayaç kutusu kaç?",
            "paraphrase",
            CTX_COUNTERBOX_RUNNING,
        ),
        (
            "genesis.request.state.durumda",
            "Lamba ne durumda?",
            "paraphrase",
            CTX_LAMPBOX_RUNNING,
        ),
    ):
        cases.extend(
            _with_variants(
                UtteranceCase(
                    case_id=case_id,
                    utterance=text,
                    expected_intent="capability_request",
                    expected_tool="capability.request",
                    side_effects=SIDE_EFFECTS_NONE,
                    context=context,
                    category="genesis",
                    source=source,
                )
            )
        )
    return cases


def _capability_status_cases() -> list[UtteranceCase]:
    cases: list[UtteranceCase] = []
    for case_id, text, source in (
        ("genesis.status.general", "Yeni yetenek ne durumda?", "canonical"),
        ("genesis.status.deictic", "Onu yapabiliyor musun artık?", "canonical"),
        ("genesis.status.para", "Yeni yeteneğin durumu nedir?", "paraphrase"),
        ("genesis.status.yapabildin", "Yeteneği yapabildin mi?", "paraphrase"),
    ):
        cases.extend(
            _with_variants(
                UtteranceCase(
                    case_id=case_id,
                    utterance=text,
                    expected_intent="capability_status",
                    expected_tool="capability.status",
                    side_effects=SIDE_EFFECTS_NONE,
                    context=CTX_NONE,
                    category="genesis",
                    source=source,
                )
            )
        )
    return cases


def _capability_approve_cancel_cases() -> list[UtteranceCase]:
    cases: list[UtteranceCase] = []
    # ADR-0084 addendum 2's own pre_turn shape (M21), reused here for the M24
    # confirmation gate: the mutating request runs first at turn 1 (parking at
    # awaiting_approval — nothing is authorized in the harness), the owner's
    # approval/cancellation then runs at turn 2, strictly after it.
    for case_id, text, source in (
        ("genesis.approve.canonical", "Onaylıyorum.", "canonical"),
        ("genesis.approve.authorize", "Bu uygulamayı yetkilendir.", "canonical"),
        ("genesis.approve.para", "Onaylıyorum, devam et.", "paraphrase"),
        ("genesis.approve.yetkilendiriyorum", "Yetkilendiriyorum.", "paraphrase"),
    ):
        cases.extend(
            _with_variants(
                UtteranceCase(
                    case_id=case_id,
                    utterance=text,
                    expected_intent="capability_approve",
                    expected_tool="capability.approve",
                    side_effects=SIDE_EFFECTS_CAPABILITY_MUTATE,
                    context=CTX_COUNTERBOX_RUNNING,
                    category="genesis",
                    source=source,
                    preceding_turns=(("Sayaç kutusunu bir artır.", "capability.request"),),
                )
            )
        )
    for case_id, text, source in (
        ("genesis.cancel.canonical", "Vazgeç, yapma.", "canonical"),
        ("genesis.cancel.para", "Boş ver, vazgeçtim.", "paraphrase"),
        ("genesis.cancel.vazgectim", "Vazgeçtim.", "paraphrase"),
    ):
        cases.extend(
            _with_variants(
                UtteranceCase(
                    case_id=case_id,
                    utterance=text,
                    expected_intent="capability_cancel",
                    expected_tool="capability.cancel",
                    side_effects=SIDE_EFFECTS_NONE,
                    context=CTX_COUNTERBOX_RUNNING,
                    category="genesis",
                    source=source,
                    preceding_turns=(("Sayaç kutusunu sıfırla.", "capability.request"),),
                )
            )
        )
    return cases


def _capability_negative_cases() -> list[UtteranceCase]:
    return list(
        itertools.chain.from_iterable(
            _with_variants(c)
            for c in (
                UtteranceCase(
                    case_id="genesis.neg.delete",
                    utterance="Sayaç kutusunu sil.",
                    expected_intent="none",
                    expected_tool=None,
                    expected_response=RESPONSE_NONE,
                    context=CTX_COUNTERBOX_RUNNING,
                    category="genesis",
                    source="canonical",
                    regression_issue_id="M24 spec §7: no delete tool for a genesis target",
                ),
                UtteranceCase(
                    case_id="genesis.neg.delete_lamp",
                    utterance="Test lambasını sil.",
                    expected_intent="none",
                    expected_tool=None,
                    expected_response=RESPONSE_NONE,
                    context=CTX_LAMPBOX_RUNNING,
                    category="genesis",
                    source="canonical",
                    notes=(
                        "The same refusal on the OTHER fixture: a KNOWN target with an "
                        "unrecognised verb falls through to NONE, so the guard is a "
                        "property of the matcher and not a counterbox-shaped special case."
                    ),
                    regression_issue_id="M24 spec §7: no delete tool for a genesis target",
                ),
                UtteranceCase(
                    case_id="genesis.neg.not_local_application",
                    utterance="Google'ı bir artır.",
                    expected_intent="none",
                    expected_tool=None,
                    expected_response=RESPONSE_NONE,
                    context=CTX_NONE,
                    category="genesis",
                    source="canonical",
                    notes=(
                        "spec §7: a target outside the catalogue is refused before any "
                        "research — the catalogue miss means the router never even "
                        "resolves a capability intent, let alone dispatches a tool."
                    ),
                    regression_issue_id="M24 spec §7: refused before any research",
                ),
                UtteranceCase(
                    case_id="genesis.neg.approve_nothing_pending",
                    utterance="Onaylıyorum.",
                    expected_intent="calendar_commit",
                    expected_tool="calendar.commit",
                    expected_response=RESPONSE_CLARIFY,
                    side_effects=SIDE_EFFECTS_NONE,
                    context=CTX_NONE,
                    category="genesis",
                    source="canonical",
                    notes=(
                        "spec §7: 'Onaylıyorum' with nothing awaiting -> clarification, "
                        "no side effect. With no genesis run parked (genesis_awaiting_"
                        "approval false) the word falls through UNCHANGED to its "
                        "existing target (CALENDAR_COMMIT), whose own service layer "
                        "answers an honest clarification when nothing is prepared "
                        "there either — never a guessed genesis approval, and never a "
                        "side effect anywhere."
                    ),
                    regression_issue_id="M24 spec §7: no confirmation without a pending run",
                ),
            )
        )
    )


def _capability_cases() -> list[UtteranceCase]:
    return [
        *_capability_request_cases(),
        *_capability_status_cases(),
        *_capability_approve_cancel_cases(),
        *_capability_negative_cases(),
    ]


# ------------------------------------------------------------ M25: 3D Creation
#
# docs/M25_CREATIVE_3D_SPEC.md §5. Every case names ``category="creative3d"``. The
# tool word (blender/unity) and, for ADD, the primitive kind are the router's own
# job (app.voice.intents' SCENE_* matchers) — everything else a case needs
# (an object name, a colour, an energy value) is a plausible MODEL argument,
# supplied by ``contract_arguments``'s own scene.* defaults unless a case overrides
# it via ``tool_arguments``. CTX_SCENE_BLENDER seeds one real, focused Blender scene
# so ADD/TRANSFORM/MATERIAL/LIGHT/CAMERA/RENDER/INSPECT resolve to something real;
# CREATE cases start from CTX_NONE (a fresh scene is exactly what they ask for).


def _scene_create_cases() -> list[UtteranceCase]:
    cases: list[UtteranceCase] = []
    for case_id, text, source in (
        ("scene.create.blender.canonical", "Blender'da yeni sahne aç.", "canonical"),
        ("scene.create.unity.canonical", "Unity'de boş bir sahne oluştur.", "canonical"),
        ("scene.create.blender.para", "Blender'da yeni bir sahne oluşturur musun?", "paraphrase"),
        ("scene.create.blender.para2", "Blender'da boş bir sahne aç.", "paraphrase"),
        ("scene.create.blender.asr", "blenderda yeni sahne ac", "asr_noise"),
    ):
        is_unity = "unity" in case_id
        cases.extend(
            _with_variants(
                UtteranceCase(
                    case_id=case_id,
                    utterance=text,
                    expected_intent="scene_create",
                    expected_tool="scene.create",
                    expected_response=RESPONSE_REFUSED if is_unity else RESPONSE_OK,
                    expected={"error_class": "dependency_unavailable"} if is_unity else {},
                    side_effects=SIDE_EFFECTS_SCENE_MUTATE,
                    context=CTX_NONE,
                    category="creative3d",
                    source=source,
                )
            )
        )
    return cases


def _scene_add_cases() -> list[UtteranceCase]:
    cases: list[UtteranceCase] = []
    for case_id, text, source in (
        ("scene.add.cube.canonical", "Bir küp ekle.", "canonical"),
        ("scene.add.sphere.canonical", "Blender'da küre oluştur.", "canonical"),
        ("scene.add.light.canonical", "Bir ışık ekle.", "canonical"),
        ("scene.add.cylinder.para", "Bir silindir ekler misin?", "paraphrase"),
        ("scene.add.plane.para", "Bir düzlem ekle.", "paraphrase"),
        ("scene.add.camera.para", "Sahneye bir kamera ekle.", "paraphrase"),
        ("scene.add.sun.para", "Bir güneş ışığı ekle.", "paraphrase"),
        ("scene.add.cube.asr", "bir kup ekle", "asr_noise"),
        ("scene.add.sphere.asr", "kure eklesene", "asr_noise"),
    ):
        cases.extend(
            _with_variants(
                UtteranceCase(
                    case_id=case_id,
                    utterance=text,
                    expected_intent="scene_add",
                    expected_tool="scene.add",
                    side_effects=SIDE_EFFECTS_SCENE_MUTATE,
                    context=CTX_SCENE_BLENDER,
                    category="creative3d",
                    source=source,
                )
            )
        )
    return cases


def _scene_transform_cases() -> list[UtteranceCase]:
    cases: list[UtteranceCase] = []
    for case_id, text, source in (
        ("scene.transform.move.canonical", "Küpü sağa taşı.", "canonical"),
        ("scene.transform.scale.canonical", "Küreyi iki kat büyüt.", "canonical"),
        ("scene.transform.shrink.para", "Küpü küçült.", "paraphrase"),
        ("scene.transform.rotate.para", "Küreyi döndür.", "paraphrase"),
        ("scene.transform.deictic.para", "Bunu sağa taşı.", "paraphrase"),
        ("scene.transform.move.asr", "kupu saga tasi", "asr_noise"),
    ):
        cases.extend(
            _with_variants(
                UtteranceCase(
                    case_id=case_id,
                    utterance=text,
                    expected_intent="scene_transform",
                    expected_tool="scene.transform",
                    side_effects=SIDE_EFFECTS_SCENE_MUTATE,
                    context=CTX_SCENE_BLENDER,
                    category="creative3d",
                    source=source,
                )
            )
        )
    return cases


def _scene_material_cases() -> list[UtteranceCase]:
    cases: list[UtteranceCase] = []
    for case_id, text, source in (
        ("scene.material.red.canonical", "Küpü kırmızı yap.", "canonical"),
        ("scene.material.blue.canonical", "Rengini maviye boya.", "canonical"),
        ("scene.material.green.para", "Küreyi yeşil yapar mısın?", "paraphrase"),
        ("scene.material.black.para", "Bunu siyaha boya.", "paraphrase"),
        ("scene.material.red.asr", "kupu kirmizi yap", "asr_noise"),
    ):
        cases.extend(
            _with_variants(
                UtteranceCase(
                    case_id=case_id,
                    utterance=text,
                    expected_intent="scene_material",
                    expected_tool="scene.material",
                    side_effects=SIDE_EFFECTS_SCENE_MUTATE,
                    context=CTX_SCENE_BLENDER,
                    category="creative3d",
                    source=source,
                )
            )
        )
    return cases


def _scene_light_cases() -> list[UtteranceCase]:
    cases: list[UtteranceCase] = []
    for case_id, text, source in (
        ("scene.light.adjust.canonical", "Işığı ayarla.", "canonical"),
        ("scene.light.increase.canonical", "Işığı artır.", "canonical"),
        ("scene.light.decrease.para", "Işığı biraz azalt.", "paraphrase"),
        ("scene.light.adjust.asr", "isigi ayarla", "asr_noise"),
    ):
        cases.extend(
            _with_variants(
                UtteranceCase(
                    case_id=case_id,
                    utterance=text,
                    expected_intent="scene_light",
                    expected_tool="scene.light",
                    side_effects=SIDE_EFFECTS_SCENE_MUTATE,
                    context=CTX_SCENE_BLENDER,
                    category="creative3d",
                    source=source,
                )
            )
        )
    return cases


def _scene_camera_cases() -> list[UtteranceCase]:
    cases: list[UtteranceCase] = []
    for case_id, text, source in (
        ("scene.camera.aim.canonical", "Kamerayı nesneye çevir.", "canonical"),
        ("scene.camera.aim.para", "Kamerayı çevirir misin?", "paraphrase"),
        ("scene.camera.aim.para2", "Kamerayı yönlendir.", "paraphrase"),
        ("scene.camera.aim.asr", "kamerayi nesneye cevir", "asr_noise"),
    ):
        cases.extend(
            _with_variants(
                UtteranceCase(
                    case_id=case_id,
                    utterance=text,
                    expected_intent="scene_camera",
                    expected_tool="scene.camera",
                    side_effects=SIDE_EFFECTS_SCENE_MUTATE,
                    context=CTX_SCENE_BLENDER,
                    category="creative3d",
                    source=source,
                )
            )
        )
    return cases


def _scene_render_cases() -> list[UtteranceCase]:
    cases: list[UtteranceCase] = []
    for case_id, text, source in (
        ("scene.render.canonical", "Render al.", "canonical"),
        ("scene.render.para", "Bir render alır mısın?", "paraphrase"),
        ("scene.render.para2", "Render alsana.", "paraphrase"),
        ("scene.render.asr", "render al", "asr_noise"),
    ):
        cases.extend(
            _with_variants(
                UtteranceCase(
                    case_id=case_id,
                    utterance=text,
                    expected_intent="scene_render",
                    expected_tool="scene.render",
                    side_effects=SIDE_EFFECTS_SCENE_MUTATE,
                    context=CTX_SCENE_BLENDER,
                    category="creative3d",
                    source=source,
                )
            )
        )
    return cases


def _scene_inspect_cases() -> list[UtteranceCase]:
    cases: list[UtteranceCase] = []
    for case_id, text, source in (
        ("scene.inspect.canonical", "Sahnede ne var?", "canonical"),
        ("scene.inspect.para", "Sahnede neler var acaba?", "paraphrase"),
        ("scene.inspect.asr", "sahnede ne var", "asr_noise"),
    ):
        cases.extend(
            _with_variants(
                UtteranceCase(
                    case_id=case_id,
                    utterance=text,
                    expected_intent="scene_inspect",
                    expected_tool="scene.inspect",
                    side_effects=SIDE_EFFECTS_SCENE_INSPECT,
                    context=CTX_SCENE_BLENDER,
                    category="creative3d",
                    source=source,
                )
            )
        )
    return cases


def _scene_export_cases() -> list[UtteranceCase]:
    """B44 (req 527): the scene in focus exported; the file stays on the owner's disk."""
    cases: list[UtteranceCase] = []
    for case_id, text, source in (
        ("scene.export.fbx", "Sahneyi FBX olarak dışa aktar.", "canonical"),
        ("scene.export.glb", "Sahneyi GLB olarak dışa aktarır mısın?", "paraphrase"),
        ("scene.export.asr", "sahneyi glb olarak disa aktar", "asr_noise"),
    ):
        cases.extend(
            _with_variants(
                UtteranceCase(
                    case_id=case_id,
                    utterance=text,
                    expected_intent="scene_export",
                    expected_tool="scene.export",
                    side_effects=SIDE_EFFECTS_SCENE_MUTATE,
                    context=CTX_SCENE_BLENDER,
                    category="creative3d",
                    source=source,
                )
            )
        )
    return cases


def _scene_animate_cases() -> list[UtteranceCase]:
    """B44 (req 526): the motion words reach scene.animate. The corpus scene is empty, so
    the tool asks which object - an honest clarification before any device call."""
    cases: list[UtteranceCase] = []
    for case_id, text, source in (
        ("scene.animate.canonical", "Küreye bir animasyon ekle.", "canonical"),
        ("scene.animate.para", "Küpü canlandır.", "paraphrase"),
        ("scene.animate.asr", "kupu canlandir", "asr_noise"),
    ):
        cases.extend(
            _with_variants(
                UtteranceCase(
                    case_id=case_id,
                    utterance=text,
                    expected_intent="scene_animate",
                    expected_tool="scene.animate",
                    expected_response=RESPONSE_CLARIFY,
                    side_effects=SIDE_EFFECTS_NONE,
                    context=CTX_SCENE_BLENDER,
                    category="creative3d",
                    source=source,
                )
            )
        )
    return cases


def _scene_negative_cases() -> list[UtteranceCase]:
    cases: list[UtteranceCase] = []
    # "Sahneyi sil." reaches no tool at all (spec §5's own negative case) - the closed
    # operation vocabulary names no delete anywhere, so nothing here can ever match it.
    cases.extend(
        _with_variants(
            UtteranceCase(
                case_id="scene.neg.delete",
                utterance="Sahneyi sil.",
                expected_intent="none",
                expected_tool=None,
                expected_response=RESPONSE_NONE,
                side_effects=SIDE_EFFECTS_NONE,
                context=CTX_SCENE_BLENDER,
                category="creative3d",
                source="canonical",
                regression_issue_id="M25 spec §5: the closed vocabulary names no delete",
            )
        )
    )
    cases.extend(
        _with_variants(
            UtteranceCase(
                case_id="scene.neg.delete_project",
                utterance="Projeyi sil.",
                expected_intent="none",
                expected_tool=None,
                expected_response=RESPONSE_NONE,
                side_effects=SIDE_EFFECTS_NONE,
                context=CTX_SCENE_BLENDER,
                category="creative3d",
                source="canonical",
                regression_issue_id="M25 spec §5: the closed vocabulary names no delete",
            )
        )
    )
    # "Sahneyi dışa aktar." was M25's deliberate miss: its vocabulary had no export. B44
    # (req 527) gives the scene one, so the same sentence now reaches scene.export - kept as
    # the regression that proves the change was meant, not drifted into.
    cases.append(
        UtteranceCase(
            case_id="scene.export.regression",
            utterance="Sahneyi dışa aktar.",
            expected_intent="scene_export",
            expected_tool="scene.export",
            side_effects=SIDE_EFFECTS_SCENE_MUTATE,
            context=CTX_SCENE_BLENDER,
            category="creative3d",
            source="regression",
            regression_issue_id="B44 req 527: the export M25 did not have",
        )
    )
    # "Bunu teknik anlat." stays exactly what M18.2/M21/M22/M23 already made it - the
    # SAME assertion those families' own "*.neg.technical_unchanged" cases make, kept
    # here too so the creative3d category proves it on its own.
    cases.extend(
        _with_variants(
            UtteranceCase(
                case_id="scene.neg.technical_unchanged",
                utterance="Bunu teknik anlat.",
                expected_intent="technical",
                expected_tool="research.explain",
                expected_target="current",
                expected={"level": "technical"},
                forbidden_tools=("research.start",),
                context=CTX_RESEARCH_FOCUS_B,
                category="creative3d",
                source="regression",
                regression_issue_id="M25 must not touch the M18.2 technical-explain path",
            )
        )
    )
    # A bare "Küpü sil." with no tool for it either - the router names deterministic
    # vocabulary only, and no SCENE_* matcher accepts "sil" (module comment: no
    # matcher shares a verb with anything unrelated, including its own deletion).
    cases.append(
        UtteranceCase(
            case_id="scene.neg.delete_object",
            utterance="Küpü sil.",
            expected_intent="none",
            expected_tool=None,
            expected_response=RESPONSE_NONE,
            side_effects=SIDE_EFFECTS_NONE,
            context=CTX_SCENE_BLENDER,
            category="creative3d",
            source="regression",
            regression_issue_id="M25 spec §5: no delete tool for any object either",
        )
    )
    return cases


def _scene_cases() -> list[UtteranceCase]:
    return [
        *_scene_create_cases(),
        *_scene_add_cases(),
        *_scene_transform_cases(),
        *_scene_material_cases(),
        *_scene_light_cases(),
        *_scene_camera_cases(),
        *_scene_render_cases(),
        *_scene_inspect_cases(),
        *_scene_export_cases(),
        *_scene_animate_cases(),
        *_scene_negative_cases(),
    ]


#: M26 (docs/M26_EXECUTIVE_AUTONOMY_SPEC.md §5, ADR-0089): Executive Autonomy. Every
#: directive here is the SAME text that starts a real run through
#: ``app.executive.planner.RuleBasedExecutivePlanner`` — the router and the planner
#: read the same words, never a fixture standing in for either.
_EXEC_RESEARCH_DIRECTIVE = (
    "Son üç gündeki AI gelişmelerini araştır, bana etkisini çıkar, Word raporu ve sunum hazırla."
)
_EXEC_RESEARCH_DIRECTIVE_PARA = (
    "Yapay zekadaki son gelişmeleri araştırıp bana bir rapor ve sunum hazırlar mısın?"
)
_EXEC_FOLDER_DIRECTIVE = (
    "Bu klasördeki teklifleri karşılaştır, Excel oluştur ve yönetici özeti hazırla."
)
_EXEC_FOLDER_DIRECTIVE_PARA = (
    "Masaüstündeki teklif dosyalarını karşılaştırıp bir Excel tablosu ve yönetici "
    "özeti çıkarır mısın?"
)
_EXEC_MAIL_DIRECTIVE = "Bu mail zincirini analiz et, ilgili dosyaları bul ve cevap taslağı hazırla."
_EXEC_MAIL_DIRECTIVE_PARA = (
    # NOT "yazışma" (correspondence): that word starts with the SAME "yaz" stem
    # TYPE_TEXT's own write-verb match uses, and TYPE_TEXT is checked earlier in the
    # router's priority chain than EXEC_START — found via the corpus (this exact
    # phrase used to resolve as TYPE_TEXT, "buraya X yaz").
    "Bu mail dizisini inceleyip ilgili belgeleri bulup bir cevap taslağı hazırlar mısın?"
)

#: A directive-shaped preceding turn every active-run case starts with — the run is
#: RUNNING by the time the case's own utterance runs (spec §5's own multi-turn shape;
#: app.executive.service.start_run_db transitions straight to "running", module
#: docstring's own note on why "planned" is not separately durable-visible).
_START_TURN: tuple[str, str] = (_EXEC_RESEARCH_DIRECTIVE, "executive.start")
_START_TURN_MAIL: tuple[str, str] = (_EXEC_MAIL_DIRECTIVE, "executive.start")
_PAUSE_TURN: tuple[str, str] = ("Bu işi durdur.", "executive.pause")


def _executive_start_cases() -> list[UtteranceCase]:
    cases: list[UtteranceCase] = []
    for case_id, utterance, source in (
        ("exec.start.research", _EXEC_RESEARCH_DIRECTIVE, "canonical"),
        ("exec.start.research.para", _EXEC_RESEARCH_DIRECTIVE_PARA, "paraphrase"),
        ("exec.start.folder", _EXEC_FOLDER_DIRECTIVE, "canonical"),
        ("exec.start.folder.para", _EXEC_FOLDER_DIRECTIVE_PARA, "paraphrase"),
        ("exec.start.mail", _EXEC_MAIL_DIRECTIVE, "canonical"),
        ("exec.start.mail.para", _EXEC_MAIL_DIRECTIVE_PARA, "paraphrase"),
    ):
        cases.extend(
            _with_variants(
                UtteranceCase(
                    case_id=case_id,
                    utterance=utterance,
                    expected_intent="exec_start",
                    expected_tool="executive.start",
                    expected_response=RESPONSE_OK,
                    side_effects=SIDE_EFFECTS_NONE,
                    context=CTX_NONE,
                    category="executive",
                    source=source,
                )
            )
        )
    # A directive with no recognised shape and no deliverable word reaches no tool at
    # all (spec §2: the planner's three shapes, never a guess at a fourth) — a plain
    # miss, exactly what "Onu araştır mısın?" (no output word) or an unrelated request
    # must stay.
    cases.extend(
        _with_variants(
            UtteranceCase(
                case_id="exec.start.neg.no_shape",
                utterance="Bana yardım eder misin?",
                expected_intent="none",
                expected_tool=None,
                expected_response=RESPONSE_NONE,
                side_effects=SIDE_EFFECTS_NONE,
                context=CTX_NONE,
                category="executive",
                source="regression",
                regression_issue_id=(
                    "M26 spec §2: an utterance naming none of the three shapes is a "
                    "plain miss, never a guessed executive run"
                ),
            )
        )
    )
    cases.extend(
        _with_variants(
            UtteranceCase(
                case_id="exec.start.neg.plain_research",
                utterance="Yapay zeka haberlerini araştır.",
                expected_intent="none",
                expected_tool=None,
                expected_response=RESPONSE_NONE,
                side_effects=SIDE_EFFECTS_NONE,
                context=CTX_NONE,
                category="executive",
                source="regression",
                regression_issue_id=(
                    "M26 spec §2: a plain research request with no deliverable word "
                    "stays the EXISTING single-shot research.start path, never "
                    "promoted to a multi-step executive run"
                ),
            )
        )
    )
    return cases


def _executive_status_explain_cases() -> list[UtteranceCase]:
    cases: list[UtteranceCase] = []
    for case_id, utterance, intent in (
        ("exec.status.1", "Ne yapıyorsun?", "exec_status"),
        ("exec.status.2", "Ne durumda?", "exec_status"),
        ("exec.status.3", "Şu an ne durumdasın?", "exec_status"),
        ("exec.explain.1", "Şu an tam olarak ne yapıyorsun?", "exec_explain"),
        ("exec.explain.2", "Tam olarak şu anda ne yapıyorsun?", "exec_explain"),
    ):
        tool = "executive.status" if intent == "exec_status" else "executive.explain"
        cases.extend(
            _with_variants(
                UtteranceCase(
                    case_id=case_id,
                    utterance=utterance,
                    expected_intent=intent,
                    expected_tool=tool,
                    expected_response=RESPONSE_OK,
                    side_effects=SIDE_EFFECTS_NONE,
                    context=CTX_NONE,
                    category="executive",
                    source="canonical",
                    preceding_turns=(_START_TURN,),
                )
            )
        )
    # The SAME status question, over a mail-thread-shaped run (spec §5's shape (c)) —
    # proves the active-run gate is not accidentally coupled to one shape's own steps.
    cases.extend(
        _with_variants(
            UtteranceCase(
                case_id="exec.status.mail_shape",
                utterance="Ne durumda?",
                expected_intent="exec_status",
                expected_tool="executive.status",
                expected_response=RESPONSE_OK,
                side_effects=SIDE_EFFECTS_NONE,
                context=CTX_NONE,
                category="executive",
                source="canonical",
                preceding_turns=(_START_TURN_MAIL,),
            )
        )
    )
    # Spec §5's own negative shape mirrored from OPERATOR_STATUS/ALARM_STOP: with NO
    # run at all, "Ne yapıyorsun?" is not executive business — it stays whatever this
    # router already resolves an unclaimed generic question to.
    cases.append(
        UtteranceCase(
            case_id="exec.status.neg.no_run",
            utterance="Ne yapıyorsun?",
            expected_intent="none",
            expected_tool=None,
            expected_response=RESPONSE_NONE,
            side_effects=SIDE_EFFECTS_NONE,
            context=CTX_NONE,
            category="executive",
            source="regression",
            regression_issue_id="M26 spec §5: with no run, this is not executive business",
        )
    )
    return cases


def _executive_pause_resume_cases() -> list[UtteranceCase]:
    cases: list[UtteranceCase] = []
    for case_id, utterance in (
        ("exec.pause.1", "Bu işi durdur."),
        ("exec.pause.2", "Bekle."),
        ("exec.pause.3", "Duraklat."),
        ("exec.pause.4", "Bir dur."),
    ):
        cases.extend(
            _with_variants(
                UtteranceCase(
                    case_id=case_id,
                    utterance=utterance,
                    expected_intent="exec_pause",
                    expected_tool="executive.pause",
                    expected_response=RESPONSE_OK,
                    side_effects=SIDE_EFFECTS_NONE,
                    context=CTX_NONE,
                    category="executive",
                    source="canonical",
                    preceding_turns=(_START_TURN,),
                )
            )
        )
    cases.extend(
        _with_variants(
            UtteranceCase(
                case_id="exec.resume.para",
                utterance="Kaldığın yerden devam et.",
                expected_intent="exec_resume",
                expected_tool="executive.resume",
                expected_response=RESPONSE_OK,
                side_effects=SIDE_EFFECTS_NONE,
                context=CTX_NONE,
                category="executive",
                source="paraphrase",
                preceding_turns=(_START_TURN, _PAUSE_TURN),
            )
        )
    )
    cases.extend(
        _with_variants(
            UtteranceCase(
                case_id="exec.resume.1",
                utterance="Devam et.",
                expected_intent="exec_resume",
                expected_tool="executive.resume",
                expected_response=RESPONSE_OK,
                side_effects=SIDE_EFFECTS_NONE,
                context=CTX_NONE,
                category="executive",
                source="canonical",
                # spec §5's own conversation: durdur -> devam et, in the SAME session.
                preceding_turns=(_START_TURN, _PAUSE_TURN),
            )
        )
    )
    # spec §5's mandatory negative: "Devam et" with nothing paused (a run exists and is
    # RUNNING, never having been paused) -> the tool's own honest clarification, never
    # a silent success and never a narration no-op (app.voice.intents._executive_
    # active_match's own docstring explains why this is a ROUTER-level EXEC_RESUME
    # match that the TOOL then refuses, not a router-level miss).
    cases.extend(
        _with_variants(
            UtteranceCase(
                case_id="exec.resume.neg.nothing_paused",
                utterance="Devam et.",
                expected_intent="exec_resume",
                expected_tool="executive.resume",
                expected_response=RESPONSE_CLARIFY,
                side_effects=SIDE_EFFECTS_NONE,
                context=CTX_NONE,
                category="executive",
                source="canonical",
                preceding_turns=(_START_TURN,),
                regression_issue_id="M26 spec §5: 'Devam et' with nothing paused -> clarification",
            )
        )
    )
    # With NO run at all, "Devam et" is not executive business either — the generic
    # RESUME control intent (narration/conversation) stays the honest fallback.
    cases.append(
        UtteranceCase(
            case_id="exec.resume.neg.no_run",
            utterance="Devam et.",
            expected_intent="resume",
            expected_tool=None,
            expected_response=RESPONSE_CONTROL,
            side_effects=SIDE_EFFECTS_NONE,
            context=CTX_NONE,
            category="executive",
            source="regression",
            regression_issue_id="M26 spec §5: with no run, 'Devam et' is not executive business",
        )
    )
    return cases


def _executive_retry_amend_cases() -> list[UtteranceCase]:
    cases: list[UtteranceCase] = []
    # Neither step has actually FAILED yet (the corpus never runs the durable workflow
    # itself — that is app.executive.workflow's own Temporal test suite's job), so both
    # retry shapes are proven at the ROUTER (the right intent/tool) and the TOOL
    # honestly clarifies rather than pretending to retry something that never failed.
    for case_id, utterance, expected in (
        ("exec.retry.kind", "Araştırmayı tekrar dene.", {}),
        ("exec.retry.kind.mail", "Mail taslağını tekrar dene.", {}),
        # Lowercase "ikinci" deliberately (not "İkinci"): tests.voice_corpus.corpus.
        # _variants' plain str.lower() turns a capital İ into "i" + a combining dot
        # above (U+0307) rather than turkish_casefold's clean "i", which then fails
        # app.voice.intents._ORDINALS' own lookup — found via the full executive
        # corpus run. A real transcript is lowercase at least as often as not, so this
        # loses no realistic coverage.
        ("exec.retry.ordinal", "ikinci adımı tekrar dene.", {}),
        ("exec.retry.ordinal.para", "ikinci adımı yeniden dene.", {}),
    ):
        cases.extend(
            _with_variants(
                UtteranceCase(
                    case_id=case_id,
                    utterance=utterance,
                    expected_intent="exec_retry",
                    expected_tool="executive.retry",
                    expected_response=RESPONSE_CLARIFY,
                    expected=expected,
                    side_effects=SIDE_EFFECTS_NONE,
                    context=CTX_NONE,
                    category="executive",
                    source="canonical",
                    preceding_turns=(_START_TURN,),
                )
            )
        )
    for case_id, utterance in (
        ("exec.amend.presentation", "Sunumu da ekle."),
        ("exec.amend.spreadsheet", "Excel'i de hazırla."),
        ("exec.amend.document", "Rapor da hazırla."),
    ):
        cases.extend(
            _with_variants(
                UtteranceCase(
                    case_id=case_id,
                    utterance=utterance,
                    expected_intent="exec_amend",
                    expected_tool="executive.amend",
                    expected_response=RESPONSE_OK,
                    side_effects=SIDE_EFFECTS_NONE,
                    context=CTX_NONE,
                    category="executive",
                    source="canonical",
                    preceding_turns=(_START_TURN,),
                )
            )
        )
    # With no run at all, neither vocabulary means anything executive.
    cases.append(
        UtteranceCase(
            case_id="exec.retry.neg.no_run",
            utterance="Araştırmayı tekrar dene.",
            # With no run, "tekrar dene" is not executive business — "tekrar" alone
            # already means something to this router (the generic REPEAT control
            # intent, "tekrar oku" etc.), and that is the honest fallback here, never
            # a guessed executive retry.
            expected_intent="repeat",
            expected_tool=None,
            expected_response=RESPONSE_CONTROL,
            side_effects=SIDE_EFFECTS_NONE,
            context=CTX_NONE,
            category="executive",
            source="regression",
            regression_issue_id="M26 spec §5: with no run, a retry phrase is not executive "
            "business",
        )
    )
    return cases


def _executive_cancel_cases() -> list[UtteranceCase]:
    cases: list[UtteranceCase] = []
    for case_id, utterance, start_turn in (
        ("exec.cancel.1", "Bunu iptal et.", _START_TURN),
        ("exec.cancel.2", "Vazgeç.", _START_TURN),
        # Lowercase for the same reason exec.retry.ordinal's own comment explains
        # (capital İ + str.lower() + _ORDINALS/_has_exact — here "iptal" needs no
        # ordinal lookup, but the SAME combining-dot mangling still makes the token
        # not-equal to the plain "iptal" this router matches).
        ("exec.cancel.3", "iptal ediyorum.", _START_TURN),
        ("exec.cancel.mail_shape", "Vazgeç.", _START_TURN_MAIL),
    ):
        cases.extend(
            _with_variants(
                UtteranceCase(
                    case_id=case_id,
                    utterance=utterance,
                    expected_intent="exec_cancel",
                    expected_tool="executive.cancel",
                    expected_response=RESPONSE_OK,
                    side_effects=SIDE_EFFECTS_NONE,
                    context=CTX_NONE,
                    category="executive",
                    source="canonical",
                    preceding_turns=(start_turn,),
                )
            )
        )
    # spec §5's mandatory negative: "Bunu iptal et" with NO run at all -> clarification
    # (the tool itself: app.executive.service._get_run's own "not_found" refusal) —
    # the router still recognises "iptal et"/"vazgeç" as EXEC_CANCEL only once a run
    # exists (app.voice.intents._executive_active_match: ``if run_state is None:
    # return None``), so with NO run this is a plain miss instead, and THAT is the
    # regression this pair of cases pins down together.
    cases.append(
        UtteranceCase(
            case_id="exec.cancel.neg.no_run",
            utterance="Bunu iptal et.",
            expected_intent="none",
            expected_tool=None,
            expected_response=RESPONSE_NONE,
            side_effects=SIDE_EFFECTS_NONE,
            context=CTX_NONE,
            category="executive",
            source="regression",
            regression_issue_id="M26 spec §5: 'Bunu iptal et' with no run -> refused/clarified, "
            "never a guess",
        )
    )
    return cases


def _executive_negative_cross_family_cases() -> list[UtteranceCase]:
    """Spec §5's structural-refusal negative: "Bu maili gönder" inside a run must
    reach the SAME M21 MAIL_SEND path it always does — there is no executive.send, so
    this is unreachable BY CONSTRUCTION (app.executive.spec.STEP_KINDS names no send
    kind, module docstring) rather than merely asserted. Proven the SAME shape
    ``mc.send.no_readback`` already proves with no run at all (no read-back, no send,
    RESPONSE_CLARIFY) — what THIS case adds is that an ACTIVE executive run changes
    nothing about it; ``mc.send.confirmed`` already proves the full successful-send
    shape, which is not this case's job to repeat."""
    return [
        UtteranceCase(
            case_id="exec.neg.mail_send_unchanged",
            utterance="Gönder.",
            expected_intent="mail_send",
            expected_tool="mail.send",
            expected_response=RESPONSE_CLARIFY,
            side_effects=SIDE_EFFECTS_NONE,
            context=CTX_NONE,
            category="executive",
            source="regression",
            preceding_turns=(_START_TURN,),
            regression_issue_id=(
                "M26 spec §5: 'Bu maili gönder' inside a run stays the M21 gate; "
                "no executive.send exists to intercept it"
            ),
        )
    ]


#: M26 addendum (docs/M26_LATEST_NEWS_MODE_SPEC.md §1, §3): the fixture channel identity and
#: the bulletin video ``tests/voice_corpus/harness.py``'s own CTX_NEWS_SOURCE_CONFIGURED
#: seeds — literal strings agreed between the two files (the same "genuine fixture"
#: discipline every other CTX_* uses), never guessed or re-derived here.
_NEWS_FIXTURE_CHANNEL_ID = "UCnewsfixturechannel0000"
_NEWS_FIXTURE_BULLETIN_VIDEO_ID = "bulletin-1"
_NEWS_FIXTURE_SOURCE_ID = "show-ana-haber"


def _news_open_cases() -> list[UtteranceCase]:
    canonical = [
        ("n.open.1", "Haberleri aç.", "canonical"),
        ("n.open.2", "Son haberleri aç.", "canonical"),
        ("n.open.3", "Show Haber'i aç.", "canonical"),
        ("n.open.4", "Show'un son haberini aç.", "canonical"),
        ("n.open.5", "Bugünün Show Ana Haber videosunu aç.", "canonical"),
        ("n.open.6", "En son yüklenen ana haberi aç.", "canonical"),
        ("n.open.7", "Haberleri YouTube'dan aç.", "canonical"),
        ("n.open.8", "Show haberi aç", "canonical"),
        ("n.open.9", "Showun son haberini aç", "canonical"),
        ("n.open.10", "En güncel haber videosunu aç.", "canonical"),
    ]
    cases: list[UtteranceCase] = []
    for case_id, text, source in canonical:
        cases.extend(
            _with_variants(
                UtteranceCase(
                    case_id=case_id,
                    utterance=text,
                    expected_intent="news_open",
                    expected_tool="news.open",
                    expected_response=RESPONSE_OK,
                    expected={
                        "news_channel_id": _NEWS_FIXTURE_CHANNEL_ID,
                        "news_video_id": _NEWS_FIXTURE_BULLETIN_VIDEO_ID,
                        "news_source_id": _NEWS_FIXTURE_SOURCE_ID,
                    },
                    # Negative assertions (task brief §6): opening the news must never
                    # start a bare research crawl and must never touch the alarm's own
                    # media/tone capabilities - the alarm profile is a structurally
                    # separate browser (packages/protocol/BROWSER_CAPABILITIES.md §2
                    # v1.3) from the ``news`` profile this tool actually opens.
                    forbidden_tools=("research.start",),
                    side_effects=SIDE_EFFECTS_NEWS_OPEN,
                    context=CTX_NEWS_SOURCE_CONFIGURED,
                    category="news",
                    source=source,
                )
            )
        )
    return cases


def _media_cases() -> list[UtteranceCase]:
    """ADR-0112. The first two are the owner's OWN words, from 2026-09-10, when the
    system twice answered "geliştirme listeme aldım" and did nothing."""
    canonical = [
        ("m.play.1", "YouTube'dan 'Doğum günün kutlu olsun Kadir' aç."),
        ("m.play.3", "YouTube'dan Sezen Aksu Gülümse aç."),
        ("m.play.4", "Bana bir Tarkan şarkısı aç."),
        ("m.play.5", "Şu şarkıyı çal: Müslüm Gürses Nilüfer."),
    ]
    cases: list[UtteranceCase] = []
    for case_id, text in canonical:
        cases.extend(
            _with_variants(
                UtteranceCase(
                    case_id=case_id,
                    utterance=text,
                    expected_intent="media_play",
                    expected_tool="media.play",
                    expected_response=RESPONSE_OK,
                    # Negative assertions: the owner's song must never start a research
                    # crawl, never open the news surface and never touch the alarm.
                    forbidden_tools=("research.start", "news.open", "alarm.create"),
                    side_effects=SIDE_EFFECTS_MEDIA_PLAY,
                    category="media",
                )
            )
        )
    cases.extend(
        _with_variants(
            UtteranceCase(
                case_id="m.play.no_title.1",
                utterance="Müzik aç.",
                # A medium with no title. Searching for the word "müzik" and playing
                # whatever came back would be the machine choosing for the owner; the
                # honest answer is the question "Neyi açayım efendim?".
                expected_intent="media_play",
                expected_tool="media.play",
                expected_response=RESPONSE_REFUSED,
                side_effects=SIDE_EFFECTS_NONE,
                category="media",
            )
        )
    )
    cases.extend(
        _with_variants(
            UtteranceCase(
                case_id="m.stop.1",
                utterance="Şarkıyı durdur.",
                expected_intent="media_stop",
                expected_tool="media.stop",
                expected_response=RESPONSE_OK,
                forbidden_tools=("alarm.stop",),
                # Stopping opens nothing; the play in the preceding turn does, and its
                # calls are the ones this policy has to allow alongside the stop.
                side_effects=SIDE_EFFECTS_MEDIA_PLAY | SIDE_EFFECTS_MEDIA_STOP,
                category="media",
                # A stop needs something to stop. Run a real play first, in the SAME
                # session -- the durable row it leaves behind is what "durdur" names.
                preceding_turns=(("YouTube'dan Sezen Aksu Gülümse aç.", "media.play"),),
            )
        )
    )
    cases.extend(
        _with_variants(
            UtteranceCase(
                case_id="m.play.compound.1",
                utterance="Chrome aç, YouTube'a gir, bir şey aç.",
                # A KNOWN LIMITATION, recorded rather than hidden. Three commands in
                # one breath: the router takes the first verb it understands and opens
                # Chrome, which is a real and useful half -- before ADR-0112 the whole
                # sentence became "geliştirme listeme aldım" and nothing happened at
                # all. Splitting a compound utterance into a plan is its own feature
                # and does not exist; when it does, this case's expectation changes
                # and the change will be visible here.
                expected_intent="app_open",
                expected_tool="operator.app_open",
                expected_response=RESPONSE_OK,
                side_effects=SIDE_EFFECTS_OPERATOR_APP_OPEN,
                category="media",
                regression_issue_id="compound utterance: only the first command runs",
                notes="ADR-0112 known limitation",
            )
        )
    )
    # --------------------------------------------- owner queue item 2 (2026-09-11)
    # "Güldür Güldür aç." -- the owner's own words, with no media word in the sentence
    # at all. ADR-0112 required an explicit marker on purpose; these are the two ways
    # that requirement was widened, and the cases that hold each of them open.
    for case_id, text in [
        # (a) the marker list was music-only. A show, a series, a film and an episode
        #     are the same request in the owner's mouth.
        ("m.play.show.1", "Güldür Güldür şovunu aç."),
        ("m.play.series.1", "Şu diziyi aç: Leyla ile Mecnun."),
        ("m.play.film.1", "Esaretin Bedeli filmini aç."),
        ("m.play.episode.1", "Son bölümü aç: Güldür Güldür."),
        # (b) no marker at all: a play verb and a name nothing else in the resolver
        #     wanted. Decided at the very bottom of the ladder, which is the guard.
        ("m.play.bare.1", "Güldür Güldür aç."),
        ("m.play.bare.2", "Leyla ile Mecnun aç."),
        ("m.play.bare.3", "Kurtlar Vadisi çal."),
    ]:
        cases.extend(
            _with_variants(
                UtteranceCase(
                    case_id=case_id,
                    utterance=text,
                    expected_intent="media_play",
                    expected_tool="media.play",
                    expected_response=RESPONSE_OK,
                    forbidden_tools=("research.start", "news.open", "alarm.create"),
                    side_effects=SIDE_EFFECTS_MEDIA_PLAY,
                    category="media",
                )
            )
        )

    # The bare-title rule's own boundary, stated as cases rather than trusted.
    cases.extend(
        _with_variants(
            UtteranceCase(
                case_id="m.play.bare.neg.one_word",
                utterance="Winamp'ı aç.",
                # ONE unknown word with a play verb is far more likely a thing than a
                # work, and this is the case that says so: Winamp is a media player the
                # allowlist does not carry, and the truthful answer is an application
                # refusal -- not a YouTube search for the word "Winamp". The cost is
                # real and stated: "Gülümse aç." needs "Gülümse şarkısını aç.".
                expected_intent=None,
                expected_tool="operator.app_open",
                expected_response=RESPONSE_REFUSED,
                forbidden_tools=("media.play",),
                side_effects=SIDE_EFFECTS_NONE,
                category="media",
                notes="bare-title rule: one word is not a title",
            )
        )
    )
    cases.extend(
        _with_variants(
            UtteranceCase(
                case_id="m.play.bare.neg.not_a_title",
                utterance="Arka kapıyı aç.",
                # Two words, no branch above claims them, and still not a title: this
                # system does not open doors, and sending "arka kapı" to YouTube would
                # be a worse answer than silence.
                expected_intent=None,
                expected_tool=None,
                expected_response=RESPONSE_REFUSED,
                forbidden_tools=("media.play",),
                side_effects=SIDE_EFFECTS_NONE,
                category="media",
                notes="bare-title rule: a physical object is not a title",
            )
        )
    )
    cases.extend(
        _with_variants(
            UtteranceCase(
                case_id="m.play.bare.neg.explain_verb",
                utterance="Az önceki araştırmanın teknik detayını açıkla.",
                # "açıkla" STARTS WITH "aç". The first version of the bare-title rule
                # matched play verbs by stem, the way the marker path safely does, and
                # turned twelve corpus cases into YouTube searches -- this one, the
                # "çalıştığını"/"çalışıyor" pair, and their ASR variants. Exact word
                # forms are the fix and this is the case that keeps them exact.
                expected_intent="explain",
                expected_tool="research.explain",
                # No research to explain in this fixture, so the honest answer is a
                # question. What this case pins is the ROUTE, not the outcome: it must
                # reach the research family, and media.play must never see it.
                expected_response=RESPONSE_CLARIFY,
                forbidden_tools=("media.play",),
                side_effects=SIDE_EFFECTS_NONE,
                category="media",
                notes="bare-title rule: a verb prefix is not a verb",
            )
        )
    )
    for case_id, text in [
        # A guard against SCHEDULING was deciding which titles could be played. "kur"
        # (as in "alarm kur") is three letters and was matched as a prefix, so "kurtlar"
        # tripped it and "Kurtlar Vadisi şarkısını çal." -- with an explicit media
        # marker, on the path that shipped in ADR-0112 -- resolved to nothing at all.
        # Same for "dakika" and "Dakikalar". Found 2026-09-11 while widening the matcher.
        ("m.play.schedule_prefix.1", "Kurtlar Vadisi şarkısını çal."),
        ("m.play.schedule_prefix.2", "Dakikalar filmini aç."),
    ]:
        cases.extend(
            _with_variants(
                UtteranceCase(
                    case_id=case_id,
                    utterance=text,
                    expected_intent="media_play",
                    expected_tool="media.play",
                    expected_response=RESPONSE_OK,
                    forbidden_tools=("alarm.create", "research.start", "news.open"),
                    side_effects=SIDE_EFFECTS_MEDIA_PLAY,
                    category="media",
                    regression_issue_id="schedule stems were prefixes and swallowed titles",
                )
            )
        )
    return cases


def _news_summarize_cases() -> list[UtteranceCase]:
    canonical = [
        ("n.sum.1", "Haberleri özetle.", "canonical"),
        ("n.sum.2", "Haberleri anlat.", "canonical"),
        ("n.sum.3", "Bugünkü haberleri özetle.", "canonical"),
    ]
    cases: list[UtteranceCase] = []
    for case_id, text, source in canonical:
        cases.extend(
            _with_variants(
                UtteranceCase(
                    case_id=case_id,
                    utterance=text,
                    expected_intent="news_summarize",
                    expected_tool="news.summarize",
                    expected_response=RESPONSE_RUNNING,
                    # Negative assertion (task brief §6): a summary must NEVER open a
                    # video or touch the browser at all - it only creates a research
                    # task, exactly as research.start would (never a second engine).
                    forbidden_tools=("news.open",),
                    side_effects=SIDE_EFFECTS_NONE,
                    context=CTX_NEWS_SOURCE_CONFIGURED,
                    category="news",
                    source=source,
                )
            )
        )
    return cases


def _news_query_cases() -> list[UtteranceCase]:
    canonical = [
        ("n.query.1", "Son haber ne zaman yüklenmiş?", "canonical"),
        ("n.query.2", "Şu an hangi haber videosunu açacaksın?", "canonical"),
    ]
    cases: list[UtteranceCase] = []
    for case_id, text, source in canonical:
        cases.extend(
            _with_variants(
                UtteranceCase(
                    case_id=case_id,
                    utterance=text,
                    expected_intent="news_query_latest",
                    expected_tool="news.query_latest",
                    expected_response=RESPONSE_OK,
                    expected={
                        "news_channel_id": _NEWS_FIXTURE_CHANNEL_ID,
                        "news_video_id": _NEWS_FIXTURE_BULLETIN_VIDEO_ID,
                    },
                    # Negative assertion (task brief §6): a query must never play
                    # anything and must never start a research/summary run.
                    forbidden_tools=("news.open", "research.start"),
                    side_effects=SIDE_EFFECTS_NONE,
                    context=CTX_NEWS_SOURCE_CONFIGURED,
                    category="news",
                    source=source,
                )
            )
        )
    return cases


def _news_asr_noise_regression_cases() -> list[UtteranceCase]:
    """The exact ASR-noise phrasing the task brief names verbatim (§7), pinned
    forever regardless of what ``_with_variants`` happens to generate from the
    canonical cases above."""
    return [
        UtteranceCase(
            case_id="n.asr.1",
            utterance="haberlerı aç",
            expected_intent="news_open",
            expected_tool="news.open",
            expected_response=RESPONSE_OK,
            forbidden_tools=("research.start",),
            side_effects=SIDE_EFFECTS_NEWS_OPEN,
            context=CTX_NEWS_SOURCE_CONFIGURED,
            category="news",
            source="asr_noise",
        ),
        UtteranceCase(
            case_id="n.asr.2",
            utterance="haberleri ac",
            expected_intent="news_open",
            expected_tool="news.open",
            expected_response=RESPONSE_OK,
            forbidden_tools=("research.start",),
            side_effects=SIDE_EFFECTS_NEWS_OPEN,
            context=CTX_NEWS_SOURCE_CONFIGURED,
            category="news",
            source="asr_noise",
        ),
        UtteranceCase(
            case_id="n.asr.3",
            utterance="bugünün haberlerini aç",
            expected_intent="news_open",
            expected_tool="news.open",
            expected_response=RESPONSE_OK,
            forbidden_tools=("research.start",),
            side_effects=SIDE_EFFECTS_NEWS_OPEN,
            context=CTX_NEWS_SOURCE_CONFIGURED,
            category="news",
            source="asr_noise",
        ),
    ]


def _news_negative_cases() -> list[UtteranceCase]:
    """Task brief §6's mandatory negatives, as their OWN cases rather than only a
    ``forbidden_tools`` entry on the positive ones: a plain display/eye "aç" (no
    "haber" noun at all) must resolve to ITS OWN family, never NEWS_OPEN - proven
    the same way ``test_a_policy_phrase_is_not_a_display_command`` proves the
    ambient/display split."""
    return [
        UtteranceCase(
            case_id="n.neg.display_wake_untouched",
            utterance="Ekranları aç.",
            expected_intent="display_wake",
            expected_tool="display.wake",
            expected_response=RESPONSE_OK,
            side_effects=SIDE_EFFECTS_DISPLAY_WAKE,
            category="news",
            source="regression",
            regression_issue_id="news spec §6: 'haber' noun required, never a bare 'aç'",
        ),
        UtteranceCase(
            case_id="n.neg.eye_enable_untouched",
            utterance="Gözünü aç.",
            expected_intent="eye_enable",
            expected_tool="eye.enable",
            expected_response=RESPONSE_OK,
            category="news",
            source="regression",
            regression_issue_id="news spec §6: 'haber' noun required, never a bare 'aç'",
        ),
    ]


def _news_refusal_cases() -> list[UtteranceCase]:
    """Two honest refusals (never a guess, never an invented channel): no news source
    configured at all, and a channel name the owner's WORDS named that matches none of
    the configured sources — the fixture's only source is "Show Ana Haber", so naming
    a different channel must refuse rather than silently opening Show's video."""
    return [
        UtteranceCase(
            case_id="n.refuse.no_source_configured",
            utterance="Haberleri aç.",
            expected_intent="news_open",
            expected_tool="news.open",
            expected_response=RESPONSE_REFUSED,
            expected={"error_class": "no_news_source"},
            side_effects=SIDE_EFFECTS_NONE,
            context=CTX_NONE,
            category="news",
            source="canonical",
        ),
        UtteranceCase(
            case_id="n.refuse.named_source_not_found",
            utterance="CNN haberini aç.",
            expected_intent="news_open",
            expected_tool="news.open",
            expected_response=RESPONSE_REFUSED,
            expected={"error_class": "news_source_not_found"},
            side_effects=SIDE_EFFECTS_NONE,
            context=CTX_NEWS_SOURCE_CONFIGURED,
            category="news",
            source="canonical",
        ),
    ]


def _news_cases() -> list[UtteranceCase]:
    return [
        *_news_open_cases(),
        *_media_cases(),
        *_news_summarize_cases(),
        *_news_query_cases(),
        *_news_asr_noise_regression_cases(),
        *_news_negative_cases(),
        *_news_refusal_cases(),
    ]


#: M27 (docs/M27_CREATIVE_TOOLS_SPEC.md §5, ADR-0093): the Creative Tools Operator.
#: Every case names ``category="creative"``. Never touches the fake device (module
#: comment above ``SIDE_EFFECTS_CREATIVE``): the SAME empty policy every case here uses
#: is itself the "forbidden side effects" measure spec §5 asks for — any incidental
#: device capability call would be flagged, and this Cloud Core half never makes one.
#: CTX_CREATIVE_PAINT seeds one real, focused Paint run so BACKGROUND/ADJUST/CLEANUP/
#: EXPORT/REDRAW resolve to something real; OPEN/DESIGN start from CTX_NONE (each
#: names its own tool explicitly, and OPEN's blank-canvas path needs no prior run).


def _creative_redraw_cases() -> list[UtteranceCase]:
    cases: list[UtteranceCase] = []
    for case_id, text, source in (
        ("creative.redraw.canonical", "Bu resmi Paint'te yeniden çiz.", "canonical"),
        ("creative.redraw.para", "Bu resmi Paint'te yeniden çizer misin?", "paraphrase"),
        ("creative.redraw.para2", "Bunu Paint'te yeniden çiz.", "paraphrase"),
        ("creative.redraw.asr", "bu resmi paintte yeniden ciz", "asr_noise"),
    ):
        cases.extend(
            _with_variants(
                UtteranceCase(
                    case_id=case_id,
                    utterance=text,
                    expected_intent="creative_redraw",
                    expected_tool="creative.redraw",
                    side_effects=SIDE_EFFECTS_CREATIVE,
                    context=CTX_CREATIVE_PAINT,
                    category="creative",
                    source=source,
                )
            )
        )
    return cases


def _creative_open_cases() -> list[UtteranceCase]:
    """Route distinctions spec §5 demands: Paint (real, succeeds), Photoshop/
    Illustrator/Figma (honest ``dependency_unavailable``), and a BARE "Bunu aç."
    (no tool word) which must reach M19/ARTIFACT_OPEN's generic file-open instead —
    ZERO wrong-app routings."""
    cases: list[UtteranceCase] = []
    cases.extend(
        _with_variants(
            UtteranceCase(
                case_id="creative.open.paint.canonical",
                utterance="Paint'te yeni bir şey aç.",
                expected_intent="creative_open",
                expected_tool="creative.open",
                side_effects=SIDE_EFFECTS_CREATIVE,
                context=CTX_NONE,
                category="creative",
                source="canonical",
            )
        )
    )
    for case_id, text in (
        ("creative.open.photoshop.canonical", "Bunu Photoshop'ta aç."),
        ("creative.open.illustrator.canonical", "Bunu Illustrator'da aç."),
        ("creative.open.figma.canonical", "Bunu Figma'da aç."),
    ):
        cases.extend(
            _with_variants(
                UtteranceCase(
                    case_id=case_id,
                    utterance=text,
                    expected_intent="creative_open",
                    expected_tool="creative.open",
                    expected_response=RESPONSE_REFUSED,
                    expected={"error_class": "dependency_unavailable"},
                    side_effects=SIDE_EFFECTS_CREATIVE,
                    context=CTX_NONE,
                    category="creative",
                    source="canonical",
                )
            )
        )
    # The route distinction itself: no tool word at all reaches ARTIFACT_OPEN, never
    # a creative tool (spec §5: "a bare 'Bunu aç.' on an image focus is M19's
    # file.open, never a creative tool").
    cases.append(
        UtteranceCase(
            case_id="creative.open.neg.bare_open_is_artifact_open",
            utterance="Bunu aç.",
            expected_intent="artifact_open",
            expected_tool="artifact.open",
            expected_target="current",
            side_effects=SIDE_EFFECTS_ARTIFACT_OPEN,
            context=CTX_ARTIFACT_FOCUSED,
            category="creative",
            source="regression",
            regression_issue_id=(
                "M27 spec §5: a bare open with no tool word is never a creative tool"
            ),
        )
    )
    return cases


def _creative_background_cases() -> list[UtteranceCase]:
    cases: list[UtteranceCase] = []
    for case_id, text, source in (
        ("creative.background.canonical", "Arka planını kaldır.", "canonical"),
        ("creative.background.para", "Arka planını kaldırır mısın?", "paraphrase"),
        ("creative.background.asr", "arka planini kaldir", "asr_noise"),
    ):
        cases.extend(
            _with_variants(
                UtteranceCase(
                    case_id=case_id,
                    utterance=text,
                    expected_intent="creative_background",
                    expected_tool="creative.background",
                    side_effects=SIDE_EFFECTS_CREATIVE,
                    context=CTX_CREATIVE_PAINT,
                    category="creative",
                    source=source,
                )
            )
        )
    return cases


def _creative_adjust_cases() -> list[UtteranceCase]:
    cases: list[UtteranceCase] = []
    for case_id, text, source in (
        ("creative.adjust.canonical", "Renkleri biraz düzelt.", "canonical"),
        ("creative.adjust.para", "Renkleri düzeltir misin?", "paraphrase"),
        ("creative.adjust.asr", "renkleri biraz duzelt", "asr_noise"),
    ):
        cases.extend(
            _with_variants(
                UtteranceCase(
                    case_id=case_id,
                    utterance=text,
                    expected_intent="creative_adjust",
                    expected_tool="creative.adjust",
                    side_effects=SIDE_EFFECTS_CREATIVE,
                    context=CTX_CREATIVE_PAINT,
                    category="creative",
                    source=source,
                )
            )
        )
    return cases


def _creative_cleanup_cases() -> list[UtteranceCase]:
    cases: list[UtteranceCase] = []
    for case_id, text, source in (
        ("creative.cleanup.canonical", "Logoyu daha temiz hale getir.", "canonical"),
        ("creative.cleanup.para", "Bunu daha temiz hale getirir misin?", "paraphrase"),
        ("creative.cleanup.asr", "logoyu daha temiz hale getir", "asr_noise"),
    ):
        cases.extend(
            _with_variants(
                UtteranceCase(
                    case_id=case_id,
                    utterance=text,
                    expected_intent="creative_cleanup",
                    expected_tool="creative.cleanup",
                    side_effects=SIDE_EFFECTS_CREATIVE,
                    context=CTX_CREATIVE_PAINT,
                    category="creative",
                    source=source,
                )
            )
        )
    return cases


def _creative_design_cases() -> list[UtteranceCase]:
    cases: list[UtteranceCase] = []
    for case_id, text, source in (
        (
            "creative.design.canonical",
            "Figma'da buna benzeyen bir arayüz tasarla.",
            "canonical",
        ),
        ("creative.design.para", "Figma'da bir arayüz tasarlar mısın?", "paraphrase"),
        ("creative.design.asr", "figmada buna benzeyen bir arayuz tasarla", "asr_noise"),
    ):
        cases.extend(
            _with_variants(
                UtteranceCase(
                    case_id=case_id,
                    utterance=text,
                    expected_intent="creative_design",
                    expected_tool="creative.design",
                    expected_response=RESPONSE_REFUSED,
                    expected={"error_class": "dependency_unavailable"},
                    side_effects=SIDE_EFFECTS_CREATIVE,
                    context=CTX_NONE,
                    category="creative",
                    source=source,
                )
            )
        )
    return cases


def _creative_export_cases() -> list[UtteranceCase]:
    cases: list[UtteranceCase] = []
    for case_id, text, source in (
        ("creative.export.png.canonical", "Bunu PNG olarak dışa aktar.", "canonical"),
        ("creative.export.jpg.para", "Bunu JPG olarak dışa aktarır mısın?", "paraphrase"),
        ("creative.export.pdf.para", "Bunu PDF olarak dışa aktar.", "paraphrase"),
        ("creative.export.png.asr", "bunu png olarak disa aktar", "asr_noise"),
    ):
        cases.extend(
            _with_variants(
                UtteranceCase(
                    case_id=case_id,
                    utterance=text,
                    expected_intent="creative_export",
                    expected_tool="creative.export",
                    side_effects=SIDE_EFFECTS_CREATIVE,
                    context=CTX_CREATIVE_PAINT,
                    category="creative",
                    source=source,
                )
            )
        )
    return cases


def _creative_lifecycle_cases() -> list[UtteranceCase]:
    """B43 (req 492, 509, 511, 512): generation and the photo fix from any room; undo /
    redo / delivery only with a creative run in focus."""
    cases: list[UtteranceCase] = []
    for case_id, text, intent, tool, context, source in (
        (
            "creative.generate.canonical",
            "Bana bir logo üret: mavi bir dalga.",
            "creative_generate",
            "creative.generate",
            CTX_NONE,
            "canonical",
        ),
        (
            "creative.generate.para",
            "Bir afiş oluştur: yaz konseri, kırmızı fon.",
            "creative_generate",
            "creative.generate",
            CTX_NONE,
            "paraphrase",
        ),
        (
            "creative.generate.asr",
            "bana bir gorsel uret mavi dalga",
            "creative_generate",
            "creative.generate",
            CTX_NONE,
            "asr_noise",
        ),
        (
            "creative.enhance.canonical",
            "Bu fotoğrafı düzelt.",
            "creative_enhance",
            "creative.enhance",
            CTX_CREATIVE_PAINT,
            "canonical",
        ),
        (
            "creative.enhance.para",
            "Resmi netleştirir misin?",
            "creative_enhance",
            "creative.enhance",
            CTX_CREATIVE_PAINT,
            "paraphrase",
        ),
        (
            "creative.undo.canonical",
            "Geri al.",
            "creative_undo",
            "creative.undo",
            CTX_CREATIVE_UNDOABLE,
            "canonical",
        ),
        (
            "creative.redo.canonical",
            "Yinele.",
            "creative_redo",
            "creative.redo",
            CTX_CREATIVE_REDOABLE,
            "canonical",
        ),
        (
            "creative.deliver.canonical",
            "Bunu bilgisayarıma indir.",
            "creative_deliver",
            "creative.deliver",
            CTX_CREATIVE_PAINT,
            "canonical",
        ),
        (
            "creative.deliver.paint",
            "Paint'te göster.",
            "creative_deliver",
            "creative.deliver",
            CTX_CREATIVE_PAINT,
            "paraphrase",
        ),
    ):
        cases.extend(
            _with_variants(
                UtteranceCase(
                    case_id=case_id,
                    utterance=text,
                    expected_intent=intent,
                    expected_tool=tool,
                    side_effects=SIDE_EFFECTS_CREATIVE_DELIVER
                    if tool == "creative.deliver"
                    else SIDE_EFFECTS_CREATIVE,
                    context=context,
                    category="creative",
                    source=source,
                )
            )
        )
    return cases


def _creative_negative_cases() -> list[UtteranceCase]:
    cases: list[UtteranceCase] = []
    # "Orijinali sil." names no creative tool at all (spec §5's own negative case) -
    # the closed operation vocabulary names no delete anywhere, so nothing here can
    # ever match it.
    cases.append(
        UtteranceCase(
            case_id="creative.neg.delete_original",
            utterance="Orijinali sil.",
            expected_intent="none",
            expected_tool=None,
            expected_response=RESPONSE_NONE,
            side_effects=SIDE_EFFECTS_NONE,
            context=CTX_CREATIVE_PAINT,
            category="creative",
            source="canonical",
            regression_issue_id="M27 spec §5: the closed vocabulary names no delete",
        )
    )
    # "Bunu teknik anlat." stays exactly what M18.2/M21/M22/M23/M25 already made it -
    # the SAME assertion those families' own "*.neg.technical_unchanged" cases make,
    # kept here too so the creative category proves it on its own (the exact class of
    # collision this family's own priority position was written to avoid — module
    # comment above app.voice.intents._creative_redraw_match).
    cases.extend(
        _with_variants(
            UtteranceCase(
                case_id="creative.neg.technical_unchanged",
                utterance="Bunu teknik anlat.",
                expected_intent="technical",
                expected_tool="research.explain",
                expected_target="current",
                expected={"level": "technical"},
                forbidden_tools=("research.start", "creative.redraw", "creative.open"),
                context=CTX_RESEARCH_FOCUS_B,
                category="creative",
                source="regression",
                regression_issue_id="M27 must not touch the M18.2 technical-explain path",
            )
        )
    )
    # The regression this whole family's priority placement exists for: a bare
    # "kaldır" (no "arka"+"plan") stays exactly the alarm resolver's own bare-wake
    # reading, never CREATIVE_BACKGROUND.
    cases.append(
        UtteranceCase(
            case_id="creative.neg.bare_kaldir_unaffected",
            utterance="Beni kaldır.",
            expected_intent="alarm_create",
            expected_tool="alarm.create",
            # A bare "Beni kaldır." names no parseable time, so the alarm tool's own
            # answer is an honest refusal — the point of this case is the ROUTING
            # (never CREATIVE_BACKGROUND), not that an alarm actually gets created.
            expected_response=RESPONSE_REFUSED,
            expected={"error_class": "when_unparsed"},
            forbidden_tools=("creative.background",),
            side_effects=SIDE_EFFECTS_NONE,
            context=CTX_NONE,
            category="creative",
            source="regression",
            regression_issue_id=(
                "M27: 'kaldır' alone must stay the alarm resolver's own bare-wake reading"
            ),
        )
    )
    # "getir" alone (no "temiz") stays WINDOW_RESTORE's own verb, never
    # CREATIVE_CLEANUP.
    cases.append(
        UtteranceCase(
            case_id="creative.neg.bare_getir_is_window_restore",
            utterance="Pencereyi eski haline getir.",
            expected_intent="window_restore",
            expected_tool="operator.window_control",
            forbidden_tools=("creative.cleanup",),
            side_effects=SIDE_EFFECTS_OPERATOR_WINDOW_RESTORE,
            context=CTX_WINDOW_FOCUSED,
            category="creative",
            source="regression",
            regression_issue_id=(
                "M27: 'getir' alone must stay WINDOW_RESTORE, never CREATIVE_CLEANUP"
            ),
        )
    )
    return cases


def _creative_cases() -> list[UtteranceCase]:
    return [
        *_creative_redraw_cases(),
        *_creative_open_cases(),
        *_creative_background_cases(),
        *_creative_adjust_cases(),
        *_creative_cleanup_cases(),
        *_creative_design_cases(),
        *_creative_export_cases(),
        *_creative_lifecycle_cases(),
        *_creative_negative_cases(),
    ]


def _executive_cases() -> list[UtteranceCase]:
    return [
        *_executive_start_cases(),
        *_executive_status_explain_cases(),
        *_executive_pause_resume_cases(),
        *_executive_retry_amend_cases(),
        *_executive_cancel_cases(),
        *_executive_negative_cross_family_cases(),
    ]


#: docs/DECISIONS.md ADR-0091: Owner Location Context, Live Weather, Morning Briefing.
#: This whole category is ONE function (``_weather_briefing_cases``), kept apart from
#: every other track's own cases in this shared file (module docstring: "two other
#: tracks are adding cases to that file too") so a merge never has to reconcile edits
#: inside a shared function body — only ``all_cases()``'s own one added line.
def _weather_query_cases() -> list[UtteranceCase]:
    cases: list[UtteranceCase] = []
    for case_id, utterance, source in (
        ("weather.bare.1", "Hava nasıl?", "canonical"),
        ("weather.bare.2", "Bugün hava nasıl olacak?", "paraphrase"),
        ("weather.bare.3", "Hava şu an nasıl?", "paraphrase"),
        ("weather.bare.4", "Burada hava nasıl?", "canonical"),
        ("weather.bare.5", "Bulunduğum yerde hava nasıl?", "canonical"),
        ("weather.bare.6", "burda hava nasıl", "asr_noise"),
    ):
        cases.extend(
            _with_variants(
                UtteranceCase(
                    case_id=case_id,
                    utterance=utterance,
                    expected_intent="weather_query",
                    expected_tool="weather.current",
                    expected_response=RESPONSE_OK,
                    side_effects=SIDE_EFFECTS_NONE,
                    context=CTX_NONE,
                    category="weather",
                    source=source,
                )
            )
        )
    for case_id, utterance, source in (
        ("weather.place.istanbul.1", "İstanbul'da hava nasıl?", "canonical"),
        ("weather.place.istanbul.2", "istanbulda hava nasil", "asr_noise"),
        ("weather.place.istanbul.3", "İstanbul'da hava durumu nasıl acaba?", "paraphrase"),
        ("weather.place.ankara.1", "Ankara'da yarın yağmur var mı?", "canonical"),
        ("weather.place.ankara.2", "ankarada yarin hava nasil", "asr_noise"),
    ):
        cases.append(
            UtteranceCase(
                case_id=case_id,
                utterance=utterance,
                expected_intent="weather_query",
                expected_tool="weather.current",
                expected_response=RESPONSE_OK,
                side_effects=SIDE_EFFECTS_NONE,
                context=CTX_NONE,
                category="weather",
                source=source,
            )
        )
    for case_id, utterance, source in (
        ("weather.temp.1", "Şu an bulunduğum yerde kaç derece?", "canonical"),
        ("weather.temp.2", "Kaç derece var dışarıda?", "paraphrase"),
        ("weather.precip.1", "bulundugum yerde yagmur var mi", "asr_noise"),
        ("weather.precip.2", "Yağmur yağacak mı?", "paraphrase"),
    ):
        cases.extend(
            _with_variants(
                UtteranceCase(
                    case_id=case_id,
                    utterance=utterance,
                    expected_intent="weather_query",
                    expected_tool="weather.current",
                    expected_response=RESPONSE_OK,
                    side_effects=SIDE_EFFECTS_NONE,
                    context=CTX_NONE,
                    category="weather",
                    source=source,
                )
            )
        )
    return cases


def _location_default_cases() -> list[UtteranceCase]:
    cases: list[UtteranceCase] = []
    for case_id, utterance, source in (
        ("location.default.set.1", "Varsayılan hava durumu konumumu İstanbul yap.", "canonical"),
        ("location.default.set.2", "Varsayılan konumumu Ankara olarak ayarla.", "paraphrase"),
        (
            "location.default.set.3",
            "varsayilan hava durumu konumumu istanbul yap",
            "asr_noise",
        ),
    ):
        cases.append(
            UtteranceCase(
                case_id=case_id,
                utterance=utterance,
                expected_intent="location_default_set",
                expected_tool="location.set_default",
                expected_response=RESPONSE_OK,
                side_effects=SIDE_EFFECTS_NONE,
                context=CTX_NONE,
                category="weather",
                source=source,
            )
        )
    # No city named at all — the tool asks, never invents one (task brief §1: "Do NOT
    # invent one").
    cases.append(
        UtteranceCase(
            case_id="location.default.set.no_city",
            utterance="Varsayılan konumu ayarla.",
            expected_intent="location_default_set",
            expected_tool="location.set_default",
            expected_response=RESPONSE_CLARIFY,
            side_effects=SIDE_EFFECTS_NONE,
            context=CTX_NONE,
            category="weather",
            source="canonical",
        )
    )
    for case_id, utterance, source in (
        ("location.default.query.1", "Varsayılan konumum ne?", "canonical"),
        ("location.default.query.2", "Varsayılan hava durumu konumum hangisi?", "paraphrase"),
    ):
        cases.extend(
            _with_variants(
                UtteranceCase(
                    case_id=case_id,
                    utterance=utterance,
                    expected_intent="location_default_query",
                    expected_tool="location.get_default",
                    expected_response=RESPONSE_OK,
                    side_effects=SIDE_EFFECTS_NONE,
                    context=CTX_NONE,
                    category="weather",
                    source=source,
                )
            )
        )
    return cases


def _location_source_cases() -> list[UtteranceCase]:
    cases: list[UtteranceCase] = []
    for case_id, utterance, source in (
        ("location.source.1", "Şu an konumumu nereden biliyorsun?", "canonical"),
        ("location.source.2", "Hangi konumu kullanıyorsun?", "canonical"),
        ("location.source.3", "Konumum güncel mi?", "canonical"),
        ("location.source.4", "Hangi konumun havasını söyledin?", "paraphrase"),
    ):
        cases.extend(
            _with_variants(
                UtteranceCase(
                    case_id=case_id,
                    utterance=utterance,
                    expected_intent="location_source_query",
                    expected_tool="weather.last_evidence",
                    expected_response=RESPONSE_OK,
                    side_effects=SIDE_EFFECTS_NONE,
                    context=CTX_NONE,
                    category="weather",
                    source=source,
                    # weather.last_evidence answers truthfully from THE RECORD (task
                    # brief §2) - a fresh session has no prior query to report, so one
                    # real weather.current call comes first, in the SAME session.
                    preceding_turns=(("Hava nasıl?", "weather.current"),),
                )
            )
        )
    return cases


def _briefing_cases() -> list[UtteranceCase]:
    cases: list[UtteranceCase] = []
    for case_id, utterance, source in (
        ("briefing.morning.1", "Günaydın.", "canonical"),
        ("briefing.morning.2", "Sabah özetimi ver.", "canonical"),
        ("briefing.morning.3", "Bugün beni neler bekliyor?", "canonical"),
        ("briefing.morning.4", "Sabah durumunu anlat.", "canonical"),
        ("briefing.morning.5", "Günaydın, bana özet verir misin?", "paraphrase"),
    ):
        cases.extend(
            _with_variants(
                UtteranceCase(
                    case_id=case_id,
                    utterance=utterance,
                    expected_intent="morning_briefing",
                    expected_tool="briefing.morning",
                    expected_response=RESPONSE_OK,
                    side_effects=SIDE_EFFECTS_NONE,
                    context=CTX_NONE,
                    category="weather",
                    source=source,
                )
            )
        )
    for case_id, utterance, source in (
        ("briefing.system_status.1", "Sistem durumu nasıl?", "canonical"),
        ("briefing.system_status.2", "Sistemin durumu nedir?", "paraphrase"),
    ):
        cases.extend(
            _with_variants(
                UtteranceCase(
                    case_id=case_id,
                    utterance=utterance,
                    expected_intent="system_status_query",
                    expected_tool="briefing.system_status",
                    expected_response=RESPONSE_OK,
                    side_effects=SIDE_EFFECTS_NONE,
                    context=CTX_NONE,
                    category="weather",
                    source=source,
                )
            )
        )
    for case_id, utterance, source in (
        ("briefing.overnight.1", "Gece neler yaptın?", "canonical"),
        ("briefing.overnight.2", "Gece boyunca ne yaptın?", "paraphrase"),
    ):
        cases.extend(
            _with_variants(
                UtteranceCase(
                    case_id=case_id,
                    utterance=utterance,
                    expected_intent="overnight_work_query",
                    expected_tool="briefing.overnight_work",
                    expected_response=RESPONSE_OK,
                    side_effects=SIDE_EFFECTS_NONE,
                    context=CTX_NONE,
                    category="weather",
                    source=source,
                )
            )
        )
    return cases


def _weather_briefing_negative_cases() -> list[UtteranceCase]:
    """Task brief §4/§6: "Negative assertions are mandatory" — a bare mention of the
    domain word ("hava", "konum", "durum", "gece", "derece") without the question/action
    shape must resolve to nothing this family owns, so the routing stays deterministic
    and distinct (task brief §5)."""
    cases: list[UtteranceCase] = []
    for case_id, utterance in (
        ("weather.negative.statement", "Bugün hava güzel."),
        ("weather.negative.derece_statement", "On derece soğudu."),
        ("location.negative.statement", "Konumum İstanbul."),
        ("briefing.negative.durum_alone", "Durumu anlat."),
        ("briefing.negative.gece_alone", "İyi geceler."),
    ):
        cases.append(
            UtteranceCase(
                case_id=case_id,
                utterance=utterance,
                expected_intent="none",
                expected_tool=None,
                expected_response=RESPONSE_NONE,
                forbidden_tools=(
                    "weather.current",
                    "location.get_default",
                    "location.set_default",
                    "weather.last_evidence",
                    "briefing.morning",
                    "briefing.system_status",
                    "briefing.overnight_work",
                ),
                side_effects=SIDE_EFFECTS_NONE,
                context=CTX_NONE,
                category="weather",
                source="regression",
            )
        )
    return cases


def _weather_briefing_cases() -> list[UtteranceCase]:
    return [
        *_weather_query_cases(),
        *_location_default_cases(),
        *_location_source_cases(),
        *_briefing_cases(),
        *_weather_briefing_negative_cases(),
    ]


#: M28 (docs/M28_NATIVE_APP_FACTORY_SPEC.md §6, ADR-0095): the Native App Factory.
#: Every case names ``category="nativeapps"``. Never touches the fake device (module
#: comment above ``SIDE_EFFECTS_NATIVE``).
#:
#: This category is written around one measured fact and one milestone rule.
#:
#: The FACT: this family's verbs ("yap", "çıkar", "oluştur", "aç", "kontrol et",
#: "düzelt", "build et") are shared with M19-M27, and FOUR real collisions were found
#: against the live router before a line of it was written - the module comment above
#: ``app.voice.intents._native_create_windows_match`` names each one. Every one of
#: them has a NEGATIVE case here, asserting the OTHER family still wins, because a
#: narrowing gate that nobody tests is a gate that quietly reopens.
#:
#: The RULE: a receipt is a read-back. The build cases run the REAL lifecycle
#: (generate -> build -> test -> publish) against a scripted runner standing in for the
#: device's own compiler, and the REAL independent reader then refuses to read what it
#: produced. So the sentence the owner hears in this corpus is "üretildi ama
#: doğrulayamadım efendim ... Hazır demiyorum" - not because the assertion was weakened
#: but because that IS the truthful answer when nothing has verified the file. The
#: VERIFIED path, where a reader really reads it, is proven in
#: tests/unit/test_voice_native_tools.py against the same real lifecycle.


def _native_create_windows_cases() -> list[UtteranceCase]:
    """Spec §6's own Windows phrase and its variants. Every one of these resolved to
    M23's APP_FACTORY_CREATE before M28 existed (collision 1) - the platform noun is
    what tells the two apart, and ``nativeapps.neg.web_app_is_m23`` holds the other
    side of that line."""
    cases: list[UtteranceCase] = []
    for case_id, text, source in (
        (
            "nativeapps.create.win.canonical",
            "Bana Windows için masaüstü uygulaması yap.",
            "canonical",
        ),
        (
            "nativeapps.create.win.para",
            "Windows için bir masaüstü uygulaması yapar mısın?",
            "paraphrase",
        ),
        ("nativeapps.create.win.short", "Masaüstü uygulaması yap.", "paraphrase"),
        ("nativeapps.create.win.asr", "windows icin masaustu uygulamasi yap", "asr_noise"),
    ):
        cases.extend(
            _with_variants(
                UtteranceCase(
                    case_id=case_id,
                    utterance=text,
                    expected_intent="native_create_windows",
                    expected_tool="native.create",
                    expected={"native_target": "windows_exe", "native_state": "planned"},
                    side_effects=SIDE_EFFECTS_NATIVE,
                    context=CTX_NONE,
                    category="nativeapps",
                    source=source,
                )
            )
        )
    return cases


def _native_create_android_cases() -> list[UtteranceCase]:
    """ADR-0161: with an enrolled device (the corpus has one) the Android head is PLANNED
    for it - the device builds it with the three Gradle shapes - and the owner hears who
    will build it. (Without a device the row opens `unavailable`; test_voice_native_tools
    holds that half.)"""
    cases: list[UtteranceCase] = []
    for case_id, text, source in (
        ("nativeapps.create.android.canonical", "Android sürümünü yap.", "canonical"),
        ("nativeapps.create.android.deictic", "Bunun Android sürümünü yap.", "canonical"),
        ("nativeapps.create.android.asr", "android surumunu yap", "asr_noise"),
    ):
        cases.extend(
            _with_variants(
                UtteranceCase(
                    case_id=case_id,
                    utterance=text,
                    expected_intent="native_create_android",
                    expected_tool="native.create",
                    expected_response=RESPONSE_OK,
                    expected={
                        "native_target": "android_apk",
                        "native_state": "planned",
                        "speech_contains": "cihazınız",
                    },
                    side_effects=SIDE_EFFECTS_NATIVE,
                    context=CTX_NONE,
                    category="nativeapps",
                    source=source,
                )
            )
        )
    return cases


def _native_build_exe_cases() -> list[UtteranceCase]:
    """ "Bunu EXE olarak çıkar." - the whole lifecycle, and the honest verdict at the
    end of it. ``native_state`` is asserted as ``unverified`` deliberately: the scripted
    runner produces a file the REAL reader cannot read, and a row that said ``verified``
    there would be the exact dishonesty the independent reader exists to prevent."""
    cases: list[UtteranceCase] = []
    for case_id, text, source in (
        ("nativeapps.build.exe.canonical", "Bunu EXE olarak çıkar.", "canonical"),
        ("nativeapps.build.exe.para", "Bunu EXE olarak çıkarır mısın?", "paraphrase"),
        ("nativeapps.build.exe.bare", "EXE olarak çıkar.", "paraphrase"),
        ("nativeapps.build.exe.asr", "bunu exe olarak cikar", "asr_noise"),
    ):
        cases.extend(
            _with_variants(
                UtteranceCase(
                    case_id=case_id,
                    utterance=text,
                    expected_intent="native_build_exe",
                    expected_tool="native.build",
                    expected={
                        "native_target": "windows_exe",
                        "native_state": "unverified",
                        "native_verified": False,
                        "speech_contains": "Hazır demiyorum",
                    },
                    side_effects=SIDE_EFFECTS_NATIVE,
                    context=CTX_NATIVE_PLANNED,
                    category="nativeapps",
                    source=source,
                )
            )
        )
    return cases


def _native_build_apk_cases() -> list[UtteranceCase]:
    """ "APK üret." for a row planned against THIS machine: refused, saying the enrolled
    device is what builds Android (ADR-0161) - this machine's build step is dotnet."""
    cases: list[UtteranceCase] = []
    for case_id, text, source in (
        ("nativeapps.build.apk.canonical", "APK üret.", "canonical"),
        ("nativeapps.build.apk.para", "Bana bir APK üretir misin?", "paraphrase"),
        ("nativeapps.build.apk.asr", "apk uret", "asr_noise"),
    ):
        cases.extend(
            _with_variants(
                UtteranceCase(
                    case_id=case_id,
                    utterance=text,
                    expected_intent="native_build_apk",
                    expected_tool="native.build",
                    expected_response=RESPONSE_REFUSED,
                    expected={
                        "error_class": "dependency_unavailable",
                        "native_target": "android_apk",
                        "speech_contains": "cihazınız",
                    },
                    side_effects=SIDE_EFFECTS_NATIVE,
                    context=CTX_NATIVE_ANDROID,
                    category="nativeapps",
                    source=source,
                )
            )
        )
    return cases


def _native_package_cases() -> list[UtteranceCase]:
    """ "Kurulum dosyasını oluştur." - the portable package is made HERE, for real, with
    the standard library. The MSIX is not, and the receipt says so rather than letting
    "kurulum dosyası" quietly mean "a zip" (spec §1: makeappx and signing run in the
    device's own authorised root)."""
    cases: list[UtteranceCase] = []
    for case_id, text, source in (
        ("nativeapps.package.canonical", "Kurulum dosyasını oluştur.", "canonical"),
        ("nativeapps.package.para", "Kurulum dosyası oluşturur musun?", "paraphrase"),
        ("nativeapps.package.asr", "kurulum dosyasini olustur", "asr_noise"),
    ):
        cases.extend(
            _with_variants(
                UtteranceCase(
                    case_id=case_id,
                    utterance=text,
                    expected_intent="native_build_installer",
                    expected_tool="native.package",
                    expected={
                        "native_target": "windows_msix",
                        "speech_contains": "MSIX kurulumu cihazda üretilir",
                    },
                    side_effects=SIDE_EFFECTS_NATIVE,
                    context=CTX_NATIVE_BUILT,
                    category="nativeapps",
                    source=source,
                )
            )
        )
    return cases


def _native_emulator_cases() -> list[UtteranceCase]:
    """ "Uygulamayı emülatörde aç." resolved to M23's APP_FACTORY_OPEN before M28
    existed (collision 3); ``nativeapps.neg.bare_app_open_is_m23`` holds the other side.
    The answer is a refusal naming item 33: the emulator binary and the API 33 image are
    both here, and there is still no APK to install without a JDK."""
    cases: list[UtteranceCase] = []
    for case_id, text, source in (
        ("nativeapps.launch.canonical", "Uygulamayı emülatörde aç.", "canonical"),
        ("nativeapps.launch.short", "Emülatörde aç.", "paraphrase"),
        ("nativeapps.launch.asr", "uygulamayi emulatorde ac", "asr_noise"),
    ):
        cases.extend(
            _with_variants(
                UtteranceCase(
                    case_id=case_id,
                    utterance=text,
                    expected_intent="native_emulator_open",
                    expected_tool="native.launch",
                    expected_response=RESPONSE_REFUSED,
                    expected={
                        "error_class": "dependency_unavailable",
                        # ADR-0161: opening an APK is not wired; the refusal says so by name.
                        "speech_contains": "emülatörde açmak bu hatta yok",
                    },
                    side_effects=SIDE_EFFECTS_NATIVE,
                    context=CTX_NATIVE_ANDROID,
                    category="nativeapps",
                    source=source,
                )
            )
        )
    return cases


def _native_check_fix_rebuild_cases() -> list[UtteranceCase]:
    """The three utterances spec §6 spells with NO native noun at all. Each is gated on
    ``native_build_focused`` (the caller's live "there is a build to be asked about"
    fact) rather than on vocabulary - so each has a twin negative below proving it stays
    with its previous owner when there is nothing built."""
    cases: list[UtteranceCase] = []
    for case_id, text, source in (
        ("nativeapps.check.canonical", "Çalışıyor mu kontrol et.", "canonical"),
        ("nativeapps.check.asr", "calisiyor mu kontrol et", "asr_noise"),
    ):
        cases.extend(
            _with_variants(
                UtteranceCase(
                    case_id=case_id,
                    utterance=text,
                    expected_intent="native_check",
                    expected_tool="native.check",
                    expected={"native_state": "unverified", "native_verified": False},
                    side_effects=SIDE_EFFECTS_NATIVE,
                    context=CTX_NATIVE_BUILT,
                    category="nativeapps",
                    source=source,
                )
            )
        )
    # "varsa" is the whole sentence: with nothing broken the truthful answer is that
    # nothing is broken - a NOOP receipt, never a pretended repair.
    for case_id, text, source in (
        ("nativeapps.fix.nothing.canonical", "Hata varsa düzelt.", "canonical"),
        ("nativeapps.fix.nothing.asr", "hata varsa duzelt", "asr_noise"),
    ):
        cases.extend(
            _with_variants(
                UtteranceCase(
                    case_id=case_id,
                    utterance=text,
                    expected_intent="native_fix",
                    expected_tool="native.fix",
                    expected={"speech_contains": "Düzeltilecek bir hata görünmüyor"},
                    side_effects=SIDE_EFFECTS_NATIVE,
                    context=CTX_NATIVE_PLANNED,
                    category="nativeapps",
                    source=source,
                )
            )
        )
    # ... and with something genuinely wrong and a device connected (B33 req 470), the
    # one repair this factory can honestly perform: the source re-rendered from the spec
    # and the whole build run again ON THE DEVICE, whose verdict becomes the row's. (With
    # no device the answer is still the error itself and "not written" -
    # test_voice_native_tools has that twin.)
    cases.append(
        UtteranceCase(
            case_id="nativeapps.fix.rebuilt_on_device",
            utterance="Hata varsa düzelt.",
            expected_intent="native_fix",
            expected_tool="native.fix",
            expected={
                "native_state": "verified",
                "speech_contains": "yeniden derledim",
            },
            side_effects=SIDE_EFFECTS_NATIVE_LIFECYCLE,
            context=CTX_NATIVE_BUILT,
            category="nativeapps",
            source="canonical",
        )
    )
    for case_id, text, source in (
        ("nativeapps.rebuild.canonical", "Yeni sürümü build et.", "canonical"),
        ("nativeapps.rebuild.asr", "yeni surumu build et", "asr_noise"),
    ):
        cases.extend(
            _with_variants(
                UtteranceCase(
                    case_id=case_id,
                    utterance=text,
                    expected_intent="native_rebuild",
                    expected_tool="native.rebuild",
                    expected={
                        "native_version": "0.1.1",
                        "native_state": "unverified",
                        "speech_contains": "Hazır demiyorum",
                    },
                    side_effects=SIDE_EFFECTS_NATIVE,
                    context=CTX_NATIVE_PLANNED,
                    category="nativeapps",
                    source=source,
                )
            )
        )
    return cases


# ------------------------------------------- B35: the owner assigns the system work on itself


def _selfdev_cases() -> list[UtteranceCase]:
    """B35 (req 622/623): "Şu bug'ı kendin düzelt." / "Şu özelliği kendine ekle." are QUEUED
    rows and a receipt saying so - no device call, no branch in this process, nothing
    promoted. The speech says both that it was queued and that the owner decides."""
    cases: list[UtteranceCase] = []
    for case_id, text, intent, tool, source in (
        (
            "selfdev.fix.canonical",
            "Şu bug'ı kendin düzelt.",
            "selfdev_fix",
            "selfdev.defect",
            "canonical",
        ),
        (
            "selfdev.fix.hata",
            "Bu hatayı kendin düzelt.",
            "selfdev_fix",
            "selfdev.defect",
            "paraphrase",
        ),
        ("selfdev.fix.sen", "Şu hatayı sen düzelt.", "selfdev_fix", "selfdev.defect", "paraphrase"),
        (
            "selfdev.fix.coz",
            "Kendindeki bu hatayı çöz.",
            "selfdev_fix",
            "selfdev.defect",
            "paraphrase",
        ),
        (
            "selfdev.feature.canonical",
            "Şu özelliği kendine ekle.",
            "selfdev_feature",
            "selfdev.feature",
            "canonical",
        ),
        (
            "selfdev.feature.front",
            "Kendine şu özelliği ekle.",
            "selfdev_feature",
            "selfdev.feature",
            "paraphrase",
        ),
        (
            "selfdev.feature.yetenek",
            "Bu yeteneği kendine kazandır.",
            "selfdev_feature",
            "selfdev.feature",
            "paraphrase",
        ),
    ):
        cases.extend(
            _with_variants(
                UtteranceCase(
                    case_id=case_id,
                    utterance=text,
                    expected_intent=intent,
                    expected_tool=tool,
                    expected={"speech_contains": "onayınızı"},
                    forbidden_tools=("release.promote", "evolution.control"),
                    category="selfdev",
                    source=source,
                )
            )
        )
    cases.extend(
        _with_variants(
            UtteranceCase(
                case_id="selfdev.status.canonical",
                utterance="Kendinde ne düzeltiyorsun?",
                expected_intent="selfdev_status",
                expected_tool="selfdev.status",
                expected={"speech_contains": "kuyruk"},
                category="selfdev",
                source="canonical",
            )
        )
    )
    # The neighbours the new block must NOT steal: the evolution switch shares the
    # self-reference, and a bare "düzelt" is still the memory correction.
    cases.append(
        UtteranceCase(
            case_id="selfdev.neg.evolution_pause_keeps_its_switch",
            utterance="Kendi kendini geliştirmeyi duraklat.",
            expected_intent="evolution_pause",
            expected_tool="evolution.control",
            expected={"evolution_paused_after": True},
            forbidden_tools=("selfdev.defect", "selfdev.feature"),
            category="selfdev",
            source="canonical",
        )
    )
    return cases


def _native_lifecycle_cases() -> list[UtteranceCase]:
    """B33 (462-471): the lifecycle after the build, each sentence gated on
    ``native_build_focused`` like check/fix/rebuild, each answered by the DEVICE through
    the harness's fake (which shows a Notepad window and an empty log - so the receipts
    below are the honest "opened, UI not read" / "could not verify" / "log empty" ones,
    measured, not the happy sentences)."""
    cases: list[UtteranceCase] = []
    # 462: "Masaüstü uygulamasını aç." - the NATIVE word is what reaches the built
    # application's launch; the bare "Uygulamayı aç." stays M23's app_factory_open even
    # with a build (test_voice_native_intents), and the same qualified sentence is M23's
    # without one (``nativeapps.neg.app_open_without_a_build_is_m23``).
    for case_id, text, source in (
        ("nativeapps.win.launch.canonical", "Masaüstü uygulamasını aç.", "canonical"),
        ("nativeapps.win.launch.run", "Windows uygulamasını çalıştır.", "paraphrase"),
        ("nativeapps.win.launch.exe", "EXE'yi çalıştır.", "paraphrase"),
        ("nativeapps.win.launch.program", "Programı başlat.", "paraphrase"),
        ("nativeapps.win.launch.asr", "masaustu uygulamasini ac", "asr_noise"),
    ):
        cases.extend(
            _with_variants(
                UtteranceCase(
                    case_id=case_id,
                    utterance=text,
                    expected_intent="native_launch",
                    expected_tool="native.launch",
                    expected={"speech_contains": "açıldı"},
                    side_effects=SIDE_EFFECTS_NATIVE_LIFECYCLE,
                    context=CTX_NATIVE_BUILT,
                    category="nativeapps",
                    source=source,
                )
            )
        )
    # 463-466: the verification is the 26.15 flow; against the fake's Notepad tree it
    # stops at the first read-back and names it.
    for case_id, text, source in (
        ("nativeapps.verify.canonical", "Uygulamayı doğrula.", "canonical"),
        ("nativeapps.verify.ui", "Arayüzünü test et.", "paraphrase"),
        ("nativeapps.verify.asr", "uygulamayi dogrula", "asr_noise"),
    ):
        cases.extend(
            _with_variants(
                UtteranceCase(
                    case_id=case_id,
                    utterance=text,
                    expected_intent="native_verify",
                    expected_tool="native.verify",
                    expected={"speech_contains": "doğrulanamadı"},
                    side_effects=SIDE_EFFECTS_NATIVE_LIFECYCLE,
                    context=CTX_NATIVE_BUILT,
                    category="nativeapps",
                    source=source,
                )
            )
        )
    # 466: the application's own log, read by the device (empty in the fake).
    for case_id, text, source in (
        ("nativeapps.log.canonical", "Uygulamanın günlüğünü oku.", "canonical"),
        ("nativeapps.log.para", "Uygulamanın logunu göster.", "paraphrase"),
        ("nativeapps.log.asr", "uygulamanin gunlugunu oku", "asr_noise"),
    ):
        cases.extend(
            _with_variants(
                UtteranceCase(
                    case_id=case_id,
                    utterance=text,
                    expected_intent="native_log",
                    expected_tool="native.log",
                    expected={"speech_contains": "günlüğü"},
                    side_effects=SIDE_EFFECTS_NATIVE_LIFECYCLE,
                    context=CTX_NATIVE_BUILT,
                    category="nativeapps",
                    source=source,
                )
            )
        )
    # 469: spec §6's own negative - removing what this system never installed is refused
    # BY NAME (the fake device's not_found), never reported as done.
    for case_id, text, source in (
        ("nativeapps.uninstall.canonical", "Kurulumu kaldır.", "canonical"),
        ("nativeapps.uninstall.app", "Uygulamayı kaldır.", "paraphrase"),
        ("nativeapps.uninstall.asr", "kurulumu kaldir", "asr_noise"),
    ):
        cases.extend(
            _with_variants(
                UtteranceCase(
                    case_id=case_id,
                    utterance=text,
                    expected_intent="native_uninstall",
                    expected_tool="native.uninstall",
                    expected_response=RESPONSE_REFUSED,
                    expected={"error_class": "not_found", "speech_contains": "kurulmamış"},
                    side_effects=SIDE_EFFECTS_NATIVE_LIFECYCLE,
                    context=CTX_NATIVE_BUILT,
                    category="nativeapps",
                    source=source,
                )
            )
        )
    # 471: the update is a NEW row at the next version, built on the device, installed.
    for case_id, text, source in (
        ("nativeapps.update.canonical", "Uygulamayı güncelle.", "canonical"),
        ("nativeapps.update.asr", "uygulamayi guncelle", "asr_noise"),
    ):
        cases.extend(
            _with_variants(
                UtteranceCase(
                    case_id=case_id,
                    utterance=text,
                    expected_intent="native_update",
                    expected_tool="native.update",
                    expected={"native_version": "0.1.1", "speech_contains": "sürümüne güncellendi"},
                    side_effects=SIDE_EFFECTS_NATIVE_LIFECYCLE,
                    context=CTX_NATIVE_BUILT,
                    category="nativeapps",
                    source=source,
                )
            )
        )
    # The twins: with NO native build the same words stay where they were (measured
    # against the live router on 2026-09-15 before the B33 matchers were gated).
    cases.append(
        UtteranceCase(
            case_id="nativeapps.neg.app_open_without_a_build_is_m23",
            utterance="Masaüstü uygulamasını aç.",
            expected_intent="app_factory_open",
            expected_tool="app.open",
            forbidden_tools=("native.launch",),
            side_effects=SIDE_EFFECTS_APP_OPEN,
            context=CTX_APP_SCAFFOLDED,
            category="nativeapps",
            source="regression",
            regression_issue_id=(
                "B33: 'masaüstü uygulamasını aç' is the built native application's launch "
                "ONLY while a native build is focused; M23's app.open keeps the sentence "
                "otherwise (and keeps the bare 'uygulamayı aç' always)"
            ),
        )
    )
    for case_id, text in (
        ("nativeapps.neg.verify_without_a_build_is_nothing", "Uygulamayı doğrula."),
        ("nativeapps.neg.update_without_a_build_is_nothing", "Uygulamayı güncelle."),
        ("nativeapps.neg.log_without_a_build_is_nothing", "Uygulamanın günlüğünü oku."),
    ):
        cases.append(
            UtteranceCase(
                case_id=case_id,
                utterance=text,
                expected_intent="none",
                expected_tool=None,
                expected_response=RESPONSE_NONE,
                forbidden_tools=("native.verify", "native.update", "native.log"),
                side_effects=SIDE_EFFECTS_NONE,
                context=CTX_NONE,
                category="nativeapps",
                source="regression",
                regression_issue_id=(
                    "B33: the lifecycle verbs need a native build to be about; with none "
                    "they resolve to nothing rather than to a guess"
                ),
            )
        )
    return cases


def _native_negative_cases() -> list[UtteranceCase]:
    """The mandatory negatives. Each one is a family this router already served, and each
    was checked against the LIVE router before M28's matchers were written - so the
    expected value here is the behaviour that must not change, not a guess at it."""
    cases: list[UtteranceCase] = []
    # iOS: refused BY NAME, and by refusing to claim it at all. There is no macOS and no
    # Xcode here and no MAUI head to share, so nothing in this family may answer - the
    # router resolves it to NOTHING and no native tool is reached (spec §1, §6).
    for case_id, text in (
        ("nativeapps.neg.ios_version", "iOS sürümünü yap."),
        ("nativeapps.neg.ipad_exe", "iPad uygulamasını EXE olarak çıkar."),
    ):
        cases.append(
            UtteranceCase(
                case_id=case_id,
                utterance=text,
                expected_intent="none",
                expected_tool=None,
                expected_response=RESPONSE_NONE,
                forbidden_tools=("native.create", "native.build"),
                side_effects=SIDE_EFFECTS_NONE,
                context=CTX_NATIVE_PLANNED,
                category="nativeapps",
                source="canonical",
                regression_issue_id=(
                    "M28 spec §1/§6: an iOS request routes to nothing in this family"
                ),
            )
        )
    # The same, with M28's own strongest noun present: "masaüstü uygulaması yap" would
    # otherwise be NATIVE_CREATE_WINDOWS. The iOS gate runs first, so this stays exactly
    # what it was before M28 - M23's own app factory.
    cases.append(
        UtteranceCase(
            case_id="nativeapps.neg.iphone_desktop_stays_m23",
            utterance="iPhone için masaüstü uygulaması yap.",
            expected_intent="app_factory_create",
            expected_tool="app.create",
            forbidden_tools=("native.create",),
            side_effects=SIDE_EFFECTS_APP_CREATE,
            context=CTX_NONE,
            category="nativeapps",
            source="regression",
            regression_issue_id=(
                "M28: the iOS gate runs before the platform noun, so an iPhone request "
                "never becomes a Windows build"
            ),
        )
    )
    # Collision 1's other side: an app-factory request that names no platform.
    for case_id, text in (
        ("nativeapps.neg.web_app_is_m23", "Web uygulaması yap."),
        ("nativeapps.neg.task_tracker_is_m23", "Bana bir görev takip uygulaması yap."),
    ):
        cases.append(
            UtteranceCase(
                case_id=case_id,
                utterance=text,
                expected_intent="app_factory_create",
                expected_tool="app.create",
                forbidden_tools=("native.create",),
                side_effects=SIDE_EFFECTS_APP_CREATE,
                context=CTX_NONE,
                category="nativeapps",
                source="regression",
                regression_issue_id=(
                    "M28: an app-factory request that names no native platform stays M23"
                ),
            )
        )
    # M22 keeps "yap" for its own kinds: a presentation is not a desktop application.
    cases.append(
        UtteranceCase(
            case_id="nativeapps.neg.sunum_is_m22",
            utterance="Sunum yap.",
            expected_intent="artifact_create",
            expected_tool="artifact.create",
            forbidden_tools=("native.create",),
            side_effects=SIDE_EFFECTS_NONE,
            context=CTX_NONE,
            category="nativeapps",
            source="regression",
            regression_issue_id="M28: 'Sunum yap.' stays M22's artifact factory",
        )
    )
    # Collision 3's other side: an open with no "emülatör" noun.
    cases.append(
        UtteranceCase(
            case_id="nativeapps.neg.bare_app_open_is_m23",
            utterance="Bir uygulama aç.",
            expected_intent="app_factory_open",
            expected_tool="app.open",
            forbidden_tools=("native.launch",),
            side_effects=SIDE_EFFECTS_APP_OPEN,
            context=CTX_APP_SCAFFOLDED,
            category="nativeapps",
            source="regression",
            regression_issue_id=(
                "M28: a bare application open with no emulator noun stays M23's app.open "
                "- measured against the live router on 2026-09-09 before M28's matchers "
                "were written, so this asserts the UNCHANGED route, not a hoped-for one"
            ),
        )
    )
    # Collision 2's other side: the corpus's own EXEC_START directive carries "masaüstü"
    # and a create verb, and must stay M26.
    cases.append(
        UtteranceCase(
            case_id="nativeapps.neg.desktop_folder_directive_is_m26",
            utterance=(
                "Masaüstündeki teklif dosyalarını karşılaştırıp bir Excel tablosu ve "
                "yönetici özeti hazırla."
            ),
            expected_intent="exec_start",
            expected_tool="executive.start",
            forbidden_tools=("native.create",),
            side_effects=SIDE_EFFECTS_NONE,
            context=CTX_NONE,
            category="nativeapps",
            source="regression",
            regression_issue_id=(
                "M28: 'masaüstü' + a create verb is not a desktop application unless an "
                "application noun is there too"
            ),
        )
    )
    # ...and M20's own search verb keeps "masaüstü" too.
    cases.append(
        UtteranceCase(
            case_id="nativeapps.neg.desktop_search_is_m20",
            utterance="Masaüstündeki sözleşme dosyasını bul.",
            expected_intent="file_search",
            expected_tool="file.search",
            forbidden_tools=("native.create",),
            side_effects=SIDE_EFFECTS_DOCUMENTS_SEARCH,
            context=CTX_NONE,
            category="nativeapps",
            source="regression",
            regression_issue_id="M28: the Desktop FOLDER word is not a desktop application",
        )
    )
    # The three noun-less matchers, each proved to stay with its previous owner when
    # there is no build in the system (module comment, collision 4).
    cases.append(
        UtteranceCase(
            case_id="nativeapps.neg.fix_without_a_build_is_explain",
            utterance="Hata varsa düzelt.",
            expected_intent="explain",
            expected_tool="research.explain",
            expected_target="current",
            forbidden_tools=("native.fix",),
            side_effects=SIDE_EFFECTS_NONE,
            context=CTX_RESEARCH_FOCUS_B,
            category="nativeapps",
            source="regression",
            regression_issue_id=(
                "M28 collision 4: 'hata' is _RESEARCH_PROBLEM_WORDS' own word; with no "
                "build in the system NATIVE_FIX must not fire"
            ),
        )
    )
    cases.append(
        UtteranceCase(
            case_id="nativeapps.neg.check_without_a_build_is_nothing",
            utterance="Çalışıyor mu kontrol et.",
            expected_intent="none",
            expected_tool=None,
            expected_response=RESPONSE_NONE,
            forbidden_tools=("native.check",),
            side_effects=SIDE_EFFECTS_NONE,
            context=CTX_NONE,
            category="nativeapps",
            source="regression",
            regression_issue_id=(
                "M28 collision 4: with no build in the system this is what it always was"
            ),
        )
    )
    # The bug this family's own probe found before it landed: with a build in the system,
    # NATIVE_CHECK's "kontrol" stem claimed M21's inbox check. Fixed by refusing the mail
    # family's own noun - narrowed, not reordered.
    cases.append(
        UtteranceCase(
            case_id="nativeapps.neg.inbox_check_stays_m21",
            utterance="Gelen kutumu kontrol eder misin?",
            expected_intent="mail_inbox",
            expected_tool="mail.inbox",
            forbidden_tools=("native.check",),
            side_effects=SIDE_EFFECTS_NONE,
            context=CTX_NATIVE_BUILT,
            category="nativeapps",
            source="regression",
            regression_issue_id=(
                "M28: NATIVE_CHECK's 'kontrol' stole 'Gelen kutumu kontrol eder misin?' "
                "from MAIL_INBOX whenever a native build existed"
            ),
        )
    )
    # M23's own status question keeps "çalışıyor mu" even with a native build present:
    # the app noun is what decides, and app.status then honestly asks WHICH project,
    # there being no app project in this fixture.
    cases.append(
        UtteranceCase(
            case_id="nativeapps.neg.app_status_stays_m23",
            utterance="Uygulama çalışıyor mu?",
            expected_intent="app_factory_status",
            expected_tool="app.status",
            expected_response=RESPONSE_CLARIFY,
            forbidden_tools=("native.check",),
            side_effects=SIDE_EFFECTS_NONE,
            context=CTX_NATIVE_BUILT,
            category="nativeapps",
            source="regression",
            regression_issue_id=(
                "M28: 'Uygulama çalışıyor mu?' stays APP_FACTORY_STATUS even with a "
                "native build in the system"
            ),
        )
    )
    # "sürüm" without a compile verb is still M18.4's rollback, with a build present.
    cases.append(
        UtteranceCase(
            case_id="nativeapps.neg.previous_version_is_rollback",
            utterance="Önceki sürüme dön.",
            expected_intent="release_rollback",
            expected_tool="release.rollback",
            # The rollback itself is always the owner's (the same refusal the evolution
            # category's own ``ev.rollback.*`` cases assert); what THIS case is about is
            # the route surviving a native build's presence.
            expected_response=RESPONSE_REFUSED,
            expected={"error_class": "owner_authorization_required"},
            forbidden_tools=("native.rebuild",),
            side_effects=SIDE_EFFECTS_NONE,
            context=CTX_NATIVE_BUILT,
            category="nativeapps",
            source="regression",
            regression_issue_id=(
                "M28: NATIVE_REBUILD requires the compile verb; the version word alone "
                "never fires it"
            ),
        )
    )
    # M27 keeps "düzelt" for its own colour noun.
    cases.append(
        UtteranceCase(
            case_id="nativeapps.neg.colour_adjust_stays_m27",
            utterance="Renkleri biraz düzelt.",
            expected_intent="creative_adjust",
            expected_tool="creative.adjust",
            forbidden_tools=("native.fix",),
            side_effects=SIDE_EFFECTS_CREATIVE,
            context=CTX_CREATIVE_PAINT,
            category="nativeapps",
            source="regression",
            regression_issue_id="M28: 'Renkleri biraz düzelt.' stays CREATIVE_ADJUST",
        )
    )
    # M23's own test verb, with a native build present.
    cases.append(
        UtteranceCase(
            case_id="nativeapps.neg.app_tests_stay_m23",
            utterance="Testleri çalıştır.",
            expected_intent="app_factory_test",
            expected_tool="app.test",
            forbidden_tools=("native.build",),
            side_effects=SIDE_EFFECTS_APP_TEST,
            context=CTX_APP_SCAFFOLDED,
            category="nativeapps",
            source="regression",
            regression_issue_id="M28: 'Testleri çalıştır.' stays M23's app.test",
        )
    )
    return cases


def _nativeapps_cases() -> list[UtteranceCase]:
    return [
        *_native_create_windows_cases(),
        *_native_create_android_cases(),
        *_native_build_exe_cases(),
        *_native_build_apk_cases(),
        *_native_package_cases(),
        *_native_emulator_cases(),
        *_native_check_fix_rebuild_cases(),
        *_native_lifecycle_cases(),
        *_native_negative_cases(),
    ]


def _routine_cases() -> list[UtteranceCase]:
    """B14 req 296-299: the owner's own routines, in their own words.

    The three sentences the roadmap names by hand — "her sabah 08:00", "evden çıkınca",
    "bilgisayar boşta kalınca" — plus the control verbs, plus the collisions that make this
    family worth pinning at all. "Sabah rutinini durdur" and "Sabah alarmımı iptal et"
    differ by ONE noun and route to different subsystems; the pair below is what keeps
    that true.
    """
    cases: list[UtteranceCase] = []

    # req 296/297: setting one up, recurring, in one sentence.
    create = [
        ("r.create.1", "Her sabah 08:00'de bana haberleri okuyan bir rutin kur.", "canonical"),
        ("r.create.2", "Hafta içi 07:00'de bir rutin ayarla.", "canonical"),
        ("r.create.3", "her sabah sekizde rutin kur", "asr_noise"),
        # req 298: a presence trigger, in the owner's words.
        ("r.create.4", "Evden çıkınca çalışacak bir rutin oluştur.", "canonical"),
        # req 299: a condition trigger, in the owner's words.
        ("r.create.5", "Bilgisayar boşta kalınca çalışacak bir rutin kur.", "canonical"),
    ]
    for case_id, text, source in create:
        cases.append(
            UtteranceCase(
                case_id=case_id,
                utterance=text,
                expected_intent="routine_create",
                expected_tool="routine.create",
                category="routine",
                source=source,
            )
        )

    # req 288: asking. Four shapes, because "hangi ... var", "neler", "kaç" and the plain
    # imperative are all how this actually gets said and none of them carries a "mı".
    for case_id, text, source in [
        ("r.list.1", "Hangi rutinlerim var?", "canonical"),
        ("r.list.2", "Rutinlerim neler?", "paraphrase"),
        ("r.list.3", "Kaç rutinim var?", "paraphrase"),
        ("r.list.4", "Rutinlerimi listele.", "canonical"),
    ]:
        cases.append(
            UtteranceCase(
                case_id=case_id,
                utterance=text,
                expected_intent="routine_list",
                expected_tool="routine.list",
                category="routine",
                source=source,
            )
        )

    for case_id, text, intent, tool, source in [
        ("r.cancel.1", "Sabah rutinini iptal et.", "routine_cancel", "routine.cancel", "canonical"),
        ("r.pause.1", "Sabah rutinini durdur.", "routine_pause", "routine.pause", "canonical"),
        (
            "r.pause.2",
            "Bu rutini bu hafta duraklat.",
            "routine_pause",
            "routine.pause",
            "paraphrase",
        ),
        ("r.resume.1", "Sabah rutinini geri aç.", "routine_resume", "routine.resume", "canonical"),
        (
            "r.resume.2",
            "Sabah rutinini tekrar başlat.",
            "routine_resume",
            "routine.resume",
            "paraphrase",
        ),
    ]:
        cases.append(
            UtteranceCase(
                case_id=case_id,
                utterance=text,
                expected_intent=intent,
                expected_tool=tool,
                context=CTX_ROUTINE_EXISTS,
                category="routine",
                source=source,
            )
        )

    # The collisions. These are the cases this family exists to not break: one noun apart,
    # two subsystems, and getting them the wrong way round means an alarm the owner turned
    # off by accident - discovered by oversleeping.
    cases.append(
        UtteranceCase(
            case_id="r.collision.alarm_cancel",
            utterance="Sabah alarmımı iptal et.",
            expected_intent="alarm_cancel",
            expected_tool="alarm.cancel",
            forbidden_tools=("routine.cancel",),
            category="routine",
            source="canonical",
            notes="One noun apart from r.cancel.1. The alarm must win when it is the noun.",
        )
    )
    cases.append(
        UtteranceCase(
            case_id="r.collision.alarm_stop",
            utterance="Alarmı durdur.",
            expected_intent="alarm_stop",
            expected_tool="alarm.stop",
            expected={"alarm_state": "STOPPED"},
            side_effects=SIDE_EFFECTS_ALARM_STOP,
            context=CTX_ALARM_RINGING,
            forbidden_tools=("routine.pause",),
            category="routine",
            source="canonical",
            notes="'durdur' is the routine family's pause verb AND the alarm's stop verb.",
        )
    )
    return cases


def _clock_cases() -> list[UtteranceCase]:
    """B15 req 271: "Saat kaç?", asked directly.

    The sentence has existed in the briefing since it was written and no intent reached it,
    so the only way to be told the time was to ask for a whole morning briefing. The three
    collisions below are the reason this family is resolved AFTER the alarm's: every one of
    them carries the clock's own noun.
    """
    cases = [
        UtteranceCase(
            case_id=f"c.now.{i}",
            utterance=text,
            expected_intent="clock_query",
            expected_tool="clock.now",
            category="clock",
            source=source,
        )
        for i, (text, source) in enumerate(
            [
                ("Saat kaç?", "canonical"),
                ("Saat kaç oldu?", "paraphrase"),
                ("Bugün günlerden ne?", "canonical"),
                ("saat kac", "asr_noise"),
            ],
            start=1,
        )
    ]
    cases.append(
        UtteranceCase(
            case_id="c.collision.alarm_query",
            utterance="Sabah alarmım kaçta?",
            expected_intent="alarm_query",
            expected_tool="alarm.status",
            forbidden_tools=("clock.now",),
            category="clock",
            source="canonical",
            notes="Carries the clock's noun AND its question word; the alarm noun decides.",
        )
    )
    cases.append(
        UtteranceCase(
            case_id="c.collision.alarm_create",
            utterance="Saat yedi buçukta beni uyandır.",
            expected_intent="alarm_create",
            expected_tool="alarm.create",
            expected={"local_time": "07:30"},
            forbidden_tools=("clock.now",),
            category="clock",
            source="canonical",
            notes="'saat' names the hour of an alarm, not a question about the clock.",
        )
    )
    return cases


def _memory_cases() -> list[UtteranceCase]:
    """B16 req 35-38, 61: the owner's own memory, by voice.

    Two families of collision, and both were found by running this corpus rather than by
    anybody reasoning about them beforehand.

    The first is INSIDE the family and is the sharpest thing in it: Turkish negates an
    imperative with a suffix, so "unut" is forget and "unutMA" is the strongest remember
    phrase `app.memory.policy` knows. `_has` is a prefix match. A stem match on "unut"
    reads "bunu unutma" as a HARD delete - the row, its versions, its evidence and its
    embeddings, with no undo. `m.negation.*` pins both readings.

    The second is with every other family, because this one's verbs belong to everybody:
    "düzelt" is the native app factory's fix verb and the evolution engine's, and "nereden
    biliyorsun" is the location family's own question. Placed with the alarm and routine
    families this took nine corpus cases away from their owners in a single run, which is
    why it is resolved after all of them.
    """
    cases = [
        UtteranceCase(
            case_id=f"m.remember.{i}",
            utterance=text,
            expected_intent="memory_remember",
            expected_tool="memory.remember",
            category="memory",
            source=source,
        )
        for i, (text, source) in enumerate(
            [
                ("Kahveyi sade severim, bunu hatırla.", "canonical"),
                ("Bunu aklında tut.", "canonical"),
                ("Sunu kaydet.", "paraphrase"),
                ("bunu hatirla", "asr_noise"),
            ],
            start=1,
        )
    ]
    cases.extend(
        UtteranceCase(
            case_id=f"m.recall.{i}",
            utterance=text,
            expected_intent="memory_search",
            expected_tool="memory.search",
            forbidden_tools=("memory.forget",),
            category="memory",
            source=source,
        )
        for i, (text, source) in enumerate(
            [
                ("Kahve hakkında ne biliyorsun?", "canonical"),
                ("Bunu hatırlıyor musun?", "canonical"),
            ],
            start=1,
        )
    )
    cases.append(
        UtteranceCase(
            case_id="m.forget.1",
            utterance="Bunu unut.",
            expected_intent="memory_forget",
            expected_tool="memory.forget",
            context=CTX_MEMORY_EXISTS,
            category="memory",
            source="canonical",
        )
    )
    cases.append(
        UtteranceCase(
            case_id="m.negation.unutma_is_remember",
            utterance="Bunu unutma.",
            expected_intent="memory_remember",
            expected_tool="memory.remember",
            forbidden_tools=("memory.forget",),
            category="memory",
            source="canonical",
            notes=(
                "Turkish negates an imperative with a suffix. A PREFIX match on 'unut' "
                "reads the owner's strongest remember phrase as an irreversible delete."
            ),
        )
    )
    cases.append(
        UtteranceCase(
            case_id="m.pin.1",
            utterance="Bunu sabitle.",
            expected_intent="memory_pin",
            expected_tool="memory.pin",
            context=CTX_MEMORY_EXISTS,
            category="memory",
            source="canonical",
        )
    )
    cases.append(
        UtteranceCase(
            case_id="m.why.1",
            utterance="Bunu neden hatırlıyorsun?",
            expected_intent="memory_why",
            expected_tool="memory.why",
            forbidden_tools=("memory.forget", "memory.remember"),
            context=CTX_MEMORY_EXISTS,
            category="memory",
            source="canonical",
            notes="Carries the recall verb and is a question ABOUT the memory, not for it.",
        )
    )
    cases.append(
        UtteranceCase(
            case_id="m.collision.location_source",
            utterance="Şu an konumumu nereden biliyorsun?",
            expected_intent="location_source_query",
            expected_tool="weather.last_evidence",
            expected_response=RESPONSE_OK,
            forbidden_tools=("memory.search", "memory.why"),
            category="memory",
            source="regression",
            notes="'nereden biliyorsun' is the LOCATION family's own question (2026-09-13).",
            # `weather.last_evidence` answers from THE RECORD, so a fresh session has no
            # prior query to report - the same preceding turn `location.source.1` uses.
            preceding_turns=(("Hava nasıl?", "weather.current"),),
        )
    )
    cases.append(
        UtteranceCase(
            case_id="m.collision.evolution_last_fix",
            utterance="Son hangi hatayı düzelttin?",
            expected_intent="explain",
            expected_tool="activity.explain",
            expected={"query_kind": "last_fix", "routed": "evolution.status"},
            forbidden_tools=("memory.correct",),
            category="memory",
            source="regression",
            notes="'düzelt' is the whole product's fix verb, not a memory correction.",
        )
    )
    return cases


def _daily_cases() -> list[UtteranceCase]:
    """B27 req 729-735: the everyday sentences the audit measured as reaching nothing.

    Every case here runs through the relay to a REAL tool, the same way every other family
    does - so "routes" means the receipt came back, not that an enum value was produced.
    Requirements 726-728 are covered by ``_clock_cases`` and ``_memory_cases`` already
    (they routed before this batch; the audit's "yönlenmiyor" for them was measured
    against a router that has since gained both families).
    """
    cases: list[UtteranceCase] = []

    # ------------------------------------------------------------- 729: "Maillerime bak."
    for case_id, text, source in (
        ("d.inbox.1", "Maillerime bak.", "canonical"),
        ("d.inbox.2", "Mail var mı?", "paraphrase"),
        ("d.inbox.3", "Gelen kutuma bak.", "paraphrase"),
        ("d.inbox.4", "Postalarımı kontrol et.", "paraphrase"),
        ("d.inbox.5", "Yeni mail var mı?", "regression"),
    ):
        cases.extend(
            _with_variants(
                UtteranceCase(
                    case_id=case_id,
                    utterance=text,
                    expected_intent="mail_inbox",
                    expected_tool="mail.inbox",
                    forbidden_tools=("mail.draft",),
                    category="daily",
                    source=source,
                    notes=(
                        "'Yeni mail var mı?' was read as a request to COMPOSE (found "
                        "measuring req 729); a question never composes."
                        if case_id == "d.inbox.5"
                        else ""
                    ),
                )
            )
        )

    # ----------------------------------------------------------- 730: "Bu hafta ne var?"
    for case_id, text, source in (
        ("d.agenda.1", "Bu hafta ne var?", "canonical"),
        ("d.agenda.2", "Yarın ne var?", "paraphrase"),
        ("d.agenda.3", "Bugün programım ne?", "paraphrase"),
        ("d.agenda.4", "Haftalık programımı söyle.", "paraphrase"),
    ):
        cases.extend(
            _with_variants(
                UtteranceCase(
                    case_id=case_id,
                    utterance=text,
                    expected_intent="calendar_agenda",
                    expected_tool="calendar.agenda",
                    forbidden_tools=("calendar.propose", "briefing.morning"),
                    category="daily",
                    source=source,
                    tool_arguments={"when_spoken": text},
                )
            )
        )
    cases.append(
        UtteranceCase(
            case_id="d.agenda.collision.greeting",
            utterance="Günaydın, bugün ne var?",
            expected_intent="morning_briefing",
            expected_tool="briefing.morning",
            forbidden_tools=("calendar.agenda",),
            category="daily",
            source="regression",
            notes="The greeting makes it the briefing (ADR-0091); the agenda steps aside.",
        )
    )

    # ------------------------------------------------------ 731: "Toplantıyı iptal et."
    cases.extend(
        _with_variants(
            UtteranceCase(
                case_id="d.cancel_event.1",
                utterance="Toplantıyı iptal et.",
                expected_intent="calendar_cancel",
                expected_tool="calendar.cancel",
                expected_response=RESPONSE_REFUSED,
                expected={"error_class": "deletion_not_permitted"},
                forbidden_tools=("calendar.propose", "alarm.cancel", "routine.cancel"),
                context=CTX_EVENT_FOCUSED,
                category="daily",
                source="canonical",
                notes="Spec §1: no delete. The refusal is a receipt naming the event.",
            )
        )
    )
    for case_id, text, source in (
        ("d.cancel_event.2", "Perşembeki toplantıyı iptal et.", "canonical"),
        ("d.cancel_event.3", "Yarınki randevuyu iptal et.", "paraphrase"),
        ("d.cancel_event.4", "Toplantıyı takvimden sil.", "paraphrase"),
    ):
        cases.append(
            UtteranceCase(
                case_id=case_id,
                utterance=text,
                expected_intent="calendar_cancel",
                expected_tool="calendar.cancel",
                expected_response=RESPONSE_CLARIFY,
                forbidden_tools=("calendar.propose", "alarm.cancel", "routine.cancel"),
                category="daily",
                source=source,
                notes="No event focused: 'Hangi etkinlik?' - never a guess, never a creation.",
            )
        )

    # ----------------------------------------------------- 732: "Araştırmayı iptal et."
    for case_id, text, source in (
        ("d.research_cancel.1", "Araştırmayı iptal et.", "canonical"),
        ("d.research_cancel.2", "Araştırmayı durdur.", "paraphrase"),
        ("d.research_cancel.3", "Araştırmayı bırak.", "paraphrase"),
        ("d.research_cancel.4", "Araştırmadan vazgeç.", "paraphrase"),
    ):
        cases.append(
            UtteranceCase(
                case_id=case_id,
                utterance=text,
                expected_intent="research_cancel",
                expected_tool="research.cancel",
                forbidden_tools=("research.start", "executive.cancel", "operator.cancel"),
                context=CTX_RESEARCH_RUNNING,
                category="daily",
                source=source,
            )
        )
    cases.append(
        UtteranceCase(
            case_id="d.research_cancel.nothing",
            utterance="Araştırmayı iptal et.",
            expected_intent="research_cancel",
            expected_tool="research.cancel",
            expected_response=RESPONSE_REFUSED,
            expected={"error_class": "nothing_running"},
            forbidden_tools=("research.start",),
            category="daily",
            source="canonical",
            notes="Nothing in flight: a refused receipt, never a success over nothing.",
        )
    )
    cases.append(
        UtteranceCase(
            case_id="d.research_cancel.negation",
            utterance="Araştırmayı iptal etme.",
            expected_intent="none",
            expected_tool=None,
            expected_response=RESPONSE_NONE,
            forbidden_tools=("research.cancel",),
            context=CTX_RESEARCH_RUNNING,
            category="daily",
            source="regression",
            notes="Turkish negation: 'etme' is 'do NOT'. The running research stays running.",
        )
    )

    # ------------------------------------------------------------- 733: "Sesini kıs."
    for case_id, text, source in (
        ("d.volume.1", "Sesini kıs.", "canonical"),
        ("d.volume.2", "Sesi biraz aç.", "paraphrase"),
        ("d.volume.3", "Sesini yükselt.", "paraphrase"),
        ("d.volume.4", "Sessize al.", "paraphrase"),
        ("d.volume.5", "Sesi kapat.", "paraphrase"),
    ):
        cases.extend(
            _with_variants(
                UtteranceCase(
                    case_id=case_id,
                    utterance=text,
                    expected_intent="media_volume",
                    expected_tool="media.volume",
                    forbidden_tools=("media.play", "display.off", "display.wake", "alarm.stop"),
                    side_effects=SIDE_EFFECTS_MEDIA_VOLUME,
                    context=CTX_MEDIA_PLAYING,
                    category="daily",
                    source=source,
                )
            )
        )
    cases.append(
        UtteranceCase(
            case_id="d.volume.nothing",
            utterance="Sesini kıs.",
            expected_intent="media_volume",
            expected_tool="media.volume",
            expected_response=RESPONSE_REFUSED,
            expected={"error_class": "nothing_playing"},
            forbidden_tools=("media.play",),
            category="daily",
            source="canonical",
            notes="Nothing playing: the honest refusal, and no device call for a level.",
        )
    )

    # ------------------------------------------------------ 734: "Neler yapabilirsin?"
    for case_id, text, source in (
        ("d.caps.1", "Neler yapabilirsin?", "canonical"),
        ("d.caps.2", "Ne yapabiliyorsun?", "paraphrase"),
        ("d.caps.3", "Yeteneklerin neler?", "paraphrase"),
        ("d.caps.4", "Hangi konularda yardımcı olabilirsin?", "paraphrase"),
        ("d.caps.5", "Mail konusunda neler yapabilirsin?", "paraphrase"),
    ):
        cases.extend(
            _with_variants(
                UtteranceCase(
                    case_id=case_id,
                    utterance=text,
                    expected_intent="capabilities_query",
                    expected_tool="assistant.capabilities",
                    forbidden_tools=("capability.request", "capability.status", "operator.status"),
                    category="daily",
                    source=source,
                )
            )
        )
    cases.append(
        UtteranceCase(
            case_id="d.caps.collision.genesis_status",
            utterance="Yetenek durumu ne?",
            expected_intent="capability_status",
            expected_tool="capability.status",
            forbidden_tools=("assistant.capabilities",),
            category="daily",
            source="regression",
            notes="M24's own noun: the bare stem 'yetenek' stays Capability Genesis's.",
        )
    )

    # ------------------------------------------------------ 735: "Ekran görüntüsü al."
    for case_id, text, source in (
        ("d.shot.1", "Ekran görüntüsü al.", "canonical"),
        ("d.shot.2", "Ekranın görüntüsünü al.", "paraphrase"),
        ("d.shot.3", "Screenshot al.", "paraphrase"),
        ("d.shot.4", "Ekranı yakala.", "paraphrase"),
    ):
        cases.extend(
            _with_variants(
                UtteranceCase(
                    case_id=case_id,
                    utterance=text,
                    expected_intent="screenshot_capture",
                    expected_tool="operator.screenshot",
                    forbidden_tools=("display.off", "display.wake", "eye.enable", "eye.disable"),
                    side_effects=SIDE_EFFECTS_SCREENSHOT,
                    category="daily",
                    source=source,
                )
            )
        )
    return cases


# ------------------------------------------------------------ B51 paraphrases

#: B51 (req 746/748): realistic Turkish re-phrasings of an existing case, each inheriting
#: that case's WHOLE contract - context, tool, response class, target, extras, forbidden
#: tools, side-effect policy and preceding turns - so a paraphrase is held to exactly what
#: the sentence it re-says is held to. (case_id, template case_id, utterance). Every one
#: runs with its ASR variants (``_with_variants``). The polite question form ("...-ır
#: mısın?"), the "-sana" imperative, a different noun for the same object and a reordered
#: sentence are the shapes owners actually use; the deterministic router routes all of
#: them - no model is involved (the model router stays behind its own flag, row 740).
_B51_PARAPHRASES: Final[tuple[tuple[str, str, str], ...]] = (
    # --- clock / weather / daily
    ("b51.clock.1", "c.now.1", "Şu an saat kaç?"),
    ("b51.clock.2", "c.now.1", "Saat kaç acaba?"),
    ("b51.weather.1", "weather.bare.1", "Dışarıda hava nasıl?"),
    ("b51.weather.2", "weather.bare.1", "Hava durumu nasıl?"),
    ("b51.caps.1", "d.caps.1", "Neler yapabiliyorsun?"),
    ("b51.briefing.1", "briefing.morning.2", "Bana sabah özetini verir misin?"),
    ("b51.routine_list.1", "r.list.1", "Rutinlerimi söyler misin?"),
    ("b51.routine_cancel.1", "r.cancel.1", "Sabah rutinini iptal eder misin?"),
    (
        "b51.routine_create.1",
        "r.create.1",
        "Her sabah 08:00'de bana haberleri okuyan bir rutin oluştur.",
    ),
    # --- alarms
    ("b51.alarm_create.1", "a.create.1", "Yarın 7:30'da uyandırır mısın beni?"),
    ("b51.alarm_create.2", "a.create.1", "Yarın sabah 7:30'a bir alarm kurar mısın?"),
    ("b51.alarm_test.1", "a.test.1", "90 saniye sonra bir test alarmı ayarla."),
    ("b51.alarm_test.2", "a.test.1", "90 saniye sonra test alarmı kurar mısın?"),
    ("b51.alarm_query.1", "a.query.1", "Sabah alarmım saat kaçta?"),
    ("b51.alarm_cancel.1", "a.cancel.1", "Alarmı iptal eder misin?"),
    ("b51.alarm_stop.1", "a.stop.1", "Alarmı kapatır mısın?"),
    ("b51.alarm_snooze.1", "a.snooze.1", "10 dakika erteler misin?"),
    # --- display / ambient / eye
    ("b51.display_off.1", "d.off.1", "Ekranları kapatır mısın?"),
    ("b51.display_off.2", "d.off.1", "Monitörleri kapatsana."),
    ("b51.display_wake.1", "d.wake.1", "Ekranları açar mısın?"),
    ("b51.ambient_explain.1", "am.explain.1", "Monitörleri neden kapattın?"),
    ("b51.ambient_explain.2", "am.explain.1", "Ekranları niye kapattın?"),
    ("b51.ambient_policy.1", "am.1", "Ben uyurken ekranları kapat."),
    ("b51.ambient_test.1", "am.test.1", "Ekran uyku otomasyonunu test eder misin?"),
    ("b51.eye_off.1", "e.off.1", "Kamerayı kapatır mısın?"),
    ("b51.eye_off.2", "e.off.1", "Gözlerini kapat."),
    ("b51.eye_on.1", "e.on.1", "Kamerayı açar mısın?"),
    ("b51.eye_on.2", "e.on.1", "Gözlerini aç."),
    ("b51.screenshot.1", "d.shot.1", "Ekran görüntüsü alır mısın?"),
    # --- research
    ("b51.research_open.1", "r.open.prev", "Önceki araştırmayı aç."),
    ("b51.research_sources.1", "r.src.1", "Kaynakları söyler misin?"),
    ("b51.research_cancel.1", "d.research_cancel.1", "Araştırmayı iptal eder misin?"),
    # --- operator
    ("b51.app_open.1", "op.app.1", "Not Defteri'ni açar mısın?"),
    ("b51.op_status.1", "op.status.1", "Şu an ne yapıyorsun?"),
    ("b51.win_max.1", "op.win.max.1", "Pencereyi büyütür müsün?"),
    ("b51.win_max.2", "op.win.max.1", "Bu pencereyi büyüt."),
    ("b51.win_min.1", "op.win.min.1", "Pencereyi küçültür müsün?"),
    ("b51.win_min.2", "op.win.min.1", "Pencereyi küçült."),
    ("b51.process_query.1", "op.process.query.1", "Chrome açık mı?"),
    ("b51.shell_ip.1", "op.shell.ip.1", "IP adresimi söyler misin?"),
    # --- documents
    ("b51.file_search.1", "doc.search.2", "Masaüstünde sözleşme dosyasını ara."),
    ("b51.doc_read.1", "doc.read.1", "Bu dosyayı okur musun?"),
    ("b51.doc_summ.1", "doc.summ.1", "Bunu özetler misin?"),
    ("b51.doc_insp.1", "doc.insp.1", "Bu Excel dosyasında ne var?"),
    ("b51.doc_preview.1", "doc.preview.1", "Bu belgeyi önizler misin?"),
    ("b51.image_meta.1", "doc.image.meta", "Fotoğrafın bilgilerini göster."),
    ("b51.image_text.1", "doc.image.text", "Görseldeki yazıyı oku."),
    ("b51.doc_write.1", "doc.write.new", "gunluk.md adında bir dosya oluşturur musun?"),
    ("b51.doc_append.1", "doc.append.canonical", "Bu dosyanın sonuna toplantı notu ekler misin?"),
    ("b51.doc_apply.1", "doc.apply.nothing_pending", "Tamam, uygula."),
    ("b51.doc_copy.1", "doc.copy.canonical", "Bu dosyayı kopyalar mısın?"),
    ("b51.doc_move.1", "doc.move.canonical", "Bu dosyayı Masaüstüne taşır mısın?"),
    (
        "b51.doc_rename.1",
        "doc.rename.canonical",
        "Bu dosyanın adını gunluk-notlari.md olarak değiştir.",
    ),
    ("b51.doc_delete.1", "doc.delete.first_word", "Bu dosyayı siler misin?"),
    ("b51.doc_undo.1", "doc.undo.after_append", "Son değişikliği geri alır mısın?"),
    ("b51.doc_versions.1", "doc.versions.canonical", "Bu dosyanın sürümlerini göster."),
    # --- memory
    ("b51.memory_search.1", "m.recall.1", "Kahve hakkında neler biliyorsun?"),
    ("b51.memory_pin.1", "m.pin.1", "Bunu sabitler misin?"),
    ("b51.memory_why.1", "m.why.1", "Bunu niye hatırlıyorsun?"),
    ("b51.memory_forget.1", "m.forget.1", "Bunu hafızandan sil."),
    # --- artifacts / creative / media / news
    ("b51.artifact_open.1", "art.open.this", "Bunu açsana."),
    ("b51.creative_open.1", "creative.open.paint.canonical", "Paint'te yeni bir tuval aç."),
    ("b51.creative_undo.1", "creative.undo.canonical", "Geri alır mısın?"),
    ("b51.creative_redo.1", "creative.redo.canonical", "Yineler misin?"),
    ("b51.media_play.1", "m.play.4", "Bana bir Tarkan şarkısı çal."),
    ("b51.media_stop.1", "m.stop.1", "Şarkıyı durdurur musun?"),
    ("b51.news_open.1", "n.open.1", "Haberleri açar mısın?"),
    ("b51.news_query.1", "n.query.1", "Son haber ne zaman yüklendi?"),
    ("b51.news_sum.1", "n.sum.1", "Haberleri özetler misin?"),
    # --- executive, native, release, self-development
    ("b51.exec_status.1", "exec.status.1", "Ne durumdasın?"),
    ("b51.exec_explain.1", "exec.explain.1", "Şu anda tam olarak ne yapıyorsun?"),
    ("b51.exec_pause.1", "exec.pause.1", "Bu işi duraklat."),
    ("b51.exec_cancel.1", "exec.cancel.1", "Bu işi iptal et."),
    ("b51.exec_retry.1", "exec.retry.ordinal", "İkinci adımı bir daha dene."),
    ("b51.exec_amend.1", "exec.amend.presentation", "Sunumu da ekler misin?"),
    ("b51.native_check.1", "nativeapps.check.canonical", "Çalışıyor mu bir kontrol et."),
    (
        "b51.native_android.1",
        "nativeapps.create.android.deictic",
        "Bunun Android sürümünü yapar mısın?",
    ),
    ("b51.native_fix.1", "nativeapps.fix.rebuilt_on_device", "Hata varsa düzeltir misin?"),
    ("b51.native_rebuild.1", "nativeapps.rebuild.canonical", "Yeni sürümü derle."),
    ("b51.native_update.1", "nativeapps.update.canonical", "Uygulamayı günceller misin?"),
    ("b51.deploy.1", "dep.2", "Bunu yayına al."),
    ("b51.selfdev_status.1", "selfdev.status.canonical", "Kendinde neyi düzeltiyorsun?"),
)


def _b51_paraphrase_cases(existing: list[UtteranceCase]) -> list[UtteranceCase]:
    templates = {c.case_id: c for c in existing}
    out: list[UtteranceCase] = []
    for case_id, template_id, utterance in _B51_PARAPHRASES:
        out.extend(
            _with_variants(
                replace(
                    templates[template_id],
                    case_id=case_id,
                    utterance=utterance,
                    source="paraphrase",
                    regression_issue_id=None,
                    notes=f"B51 paraphrase of {template_id}",
                )
            )
        )
    return out


def all_cases() -> list[UtteranceCase]:
    cases = [
        *_clock_cases(),
        *_routine_cases(),
        *_memory_cases(),
        *_daily_cases(),
        *_research_cases(),
        *_alarm_create_cases(),
        *_alarm_control_cases(),
        *_alarm_wake_song_cases(),
        *_display_cases(),
        *_eye_cases(),
        *_control_cases(),
        *_evolution_cases(),
        *_selfdev_cases(),
        *_operator_cases(),
        *_documents_cases(),
        *_mail_calendar_cases(),
        *_artifact_cases(),
        *_app_cases(),
        *_capability_cases(),
        *_scene_cases(),
        *_executive_cases(),
        *_weather_briefing_cases(),
        *_news_cases(),
        *_creative_cases(),
        *_nativeapps_cases(),
    ]
    cases.extend(_b51_paraphrase_cases(cases))
    ids = [c.case_id for c in cases]
    assert len(ids) == len(set(ids)), "duplicate case ids"
    return cases


__all__ = [
    "CORPUS_VERSION",
    "CTX_ALARM_RINGING",
    "CTX_ALARM_SCHEDULED",
    "CTX_ALARM_WAKE_SONG_SET",
    "CTX_ALARM_WAKE_SONG_URL",
    "CTX_COUNTERBOX_RUNNING",
    "CTX_EYE_DISABLED",
    "CTX_LAMPBOX_RUNNING",
    "CTX_MEDIA_PLAYING",
    "CTX_NEWS_SOURCE_CONFIGURED",
    "CTX_RESEARCH_RUNNING",
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
