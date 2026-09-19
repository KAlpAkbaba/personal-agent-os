"""Turkish realtime intent resolver + narration bridge (M12 spec §5, ADR-0034 §4).

The provider gives us a transcript of what the owner just said; this module
decides what it MEANS for the assistant, against the live session state and
the narration state, and translates it into narration-engine cursor / speed
operations. It is semantic, not keyword-only:

- the text is passed through the existing tr-TR normaliser first ("2. maddeyi"
  -> "ikinci maddeyi"), then Turkish-casefolded (İ -> i, I -> ı) and tokenised;
- hesitation fillers ("şey", "yani", "hani", "ııı", "eee", "hmm", ...) are
  dropped, so "şey, yani biraz daha yavaş" resolves like "biraz daha yavaş";
- matching is on TOKENS, never substrings: "durum raporunu oku" does not stop,
  "durdur" does; the M4 ``STOP_WORDS`` keep their meaning and top priority;
- "devam" / "tekrar" resolve against state: with a narration paused they are
  narration operations, in a plain conversation they mean "continue / repeat
  what you were saying" (``scope``).

Nothing here touches audio, the network or the database. The bridge returns a
new ``NarrationState`` through the M4 command machine (``app.narration.commands``)
so "dur always wins" and the exact-cursor rules are inherited, not duplicated.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Any, Final

from app.calendar import tr_time as calendar_tr_time
from app.narration import commands
from app.narration.commands import Command, NarrationState, ParsedCommand, State
from app.narration.engine import PARAGRAPH_HEADING, PARAGRAPH_LIST, Cursor, NarrationPlan
from app.narration.normalizer import normalize
from app.voice.realtime import STOP_WORDS, RealtimeState


class Intent(StrEnum):
    # M18 (docs/M18_HOLOGRAPHIC_CORE_SPEC.md §2): the privacy-critical Active Eye
    # stop phrases. Listed first because it is checked first — see resolve_intent.
    EYE_DISABLE = "eye_disable"  # gözünü kapat / kamerayı kapat / beni izleme
    # docs/M18_ACTION_CONTRACT.md §2: the enable path that did not exist on 2026-09-06.
    EYE_ENABLE = "eye_enable"  # gözünü aç / kamerayı aç / beni izle / beni tekrar izle
    # "Canlıya al." as an IMPERATIVE is an action (always refused by policy, contract §2);
    # "canlıya alabilir misin?" stays the can_deploy QUERY.
    DEPLOY = "deploy"  # canlıya al / yayına al
    # M18.4 (spec §4): the owner's voice over self-evolution. Actions, every one a receipt.
    EVOLUTION_PAUSE = "evolution_pause"  # kendi kendini geliştirmeyi duraklat
    EVOLUTION_RESUME = "evolution_resume"  # kendi kendini geliştirmeyi aç
    EVOLUTION_CANCEL = "evolution_cancel"  # bu geliştirmeyi iptal et
    EVOLUTION_HOLD = "evolution_hold"  # bunu canlıya alma
    RELEASE_ROLLBACK = "release_rollback"  # önceki sürüme dön
    # B14 req 287-291, 296-299: the owner's own routines, by voice. Resolved BEFORE the
    # alarm family, because "rutini durdur" and "rutini iptal et" carry the alarm's own
    # stop and cancel verbs - the noun is what tells them apart, and the noun has to be
    # looked at first or "sabah rutinini durdur" silences tomorrow's alarm instead.
    ROUTINE_CREATE = "routine_create"  # her sabah 08:00'de haberleri oku
    ROUTINE_LIST = "routine_list"  # hangi rutinlerim var
    ROUTINE_CANCEL = "routine_cancel"  # sabah rutinini iptal et
    ROUTINE_PAUSE = "routine_pause"  # sabah rutinini durdur / bu hafta durdur
    ROUTINE_RESUME = "routine_resume"  # sabah rutinini geri aç
    # B15 req 271: "Saat kaç?" / "Bugün günlerden ne?". The sentence has existed in the
    # briefing since it was written and no intent reached it, so the owner could be told
    # the time only as part of a whole morning briefing. Resolved AFTER the alarm family:
    # "Sabah alarmım kaçta?" is a question about an alarm, not about the clock.
    CLOCK_QUERY = "clock_query"
    # B27 req 726-735: the ten sentences the audit measured as reaching NOTHING that a
    # person actually says every day. Each resolves to a tool that already existed (or,
    # for the two below marked so, to the tool this batch adds as the caller a
    # capability never had). Resolved right after the clock, before the operator's
    # running-gated pair: each carries its own noun, so none can shadow a bare "Dur.".
    CAPABILITIES_QUERY = "capabilities_query"  # neler yapabilirsin / yeteneklerin neler
    RESEARCH_CANCEL = "research_cancel"  # araştırmayı iptal et / araştırmayı durdur
    # B31 req 201/203/204/209: the research paused, resumed, opened by reference, and the
    # owner's standing answer register.
    RESEARCH_PAUSE = "research_pause"  # araştırmayı duraklat
    RESEARCH_RESUME = "research_resume"  # araştırmaya devam et
    RESEARCH_OPEN = "research_open"  # bir önceki araştırmayı aç
    RESEARCH_ANSWER_MODE = "research_answer_mode"  # bundan sonra teknik anlat
    MEDIA_VOLUME = "media_volume"  # sesini kıs / sesi aç / sessize al
    SCREENSHOT_CAPTURE = "screenshot_capture"  # ekran görüntüsü al
    CALENDAR_CANCEL = "calendar_cancel"  # toplantıyı iptal et / randevuyu sil
    # B28 req 92/93/98: the Digital Operator's input the owner can SAY. A key or a chord
    # ("Enter'a bas", "Ctrl S'ye bas") and a scroll ("aşağı kaydır"); clicks are the
    # model's own last-resort argument (req 107) and have no spoken form here.
    OPERATOR_KEY = "operator_key"
    OPERATOR_SCROLL = "operator_scroll"
    # B29 req 100/102/105: UI Automation and the visual rung, spoken. "Tamam düğmesine
    # tıkla" invokes a named button through the tree (never a coordinate); "ekrandaki
    # metni oku" reads a control's text; "ekranda ne var" asks the vision provider.
    UI_INVOKE = "ui_invoke"
    UI_READ = "ui_read"
    SCREEN_DESCRIBE = "screen_describe"
    # B30 req 82/119-122: an application closed by name, processes and services asked
    # about by name, and the two policy-gated actions on them.
    APP_CLOSE = "app_close"  # Not Defteri'ni kapat / Chrome'u kapat
    PROCESS_QUERY = "process_query"  # Chrome çalışıyor mu / hangi uygulamalar açık
    PROCESS_STOP = "process_stop"  # Chrome'u sonlandır
    SERVICE_QUERY = "service_query"  # yazdırma servisi çalışıyor mu
    SERVICE_RESTART = "service_restart"  # Spooler servisini yeniden başlat
    # B16 req 35-38, 61: the owner's own memory, by voice. `app.memory` has been
    # complete since M5 with a REST surface and no sentence reached it. Resolved AFTER
    # the alarm, display, routine and clock families: this family shares verbs with none
    # of them and objects with all of them, so "alarmı unut" must stay an alarm.
    MEMORY_REMEMBER = "memory_remember"  # bunu hatırla / aklında tut / unutma
    MEMORY_SEARCH = "memory_search"  # bunu hatırlıyor musun / ne biliyorsun
    MEMORY_FORGET = "memory_forget"  # bunu unut
    MEMORY_CORRECT = "memory_correct"  # hayır, öyle değil, düzelt
    MEMORY_PIN = "memory_pin"  # bunu sabitle
    MEMORY_WHY = "memory_why"  # bunu neden hatırlıyorsun
    STOP = "stop"  # dur / kes / sus / yeter / durdur / duraklat / bekle
    RESUME = "resume"  # devam / kaldığın yerden / sürdür
    REPEAT = "repeat"  # tekrar (oku) / yeniden oku / bir daha
    REPEAT_ITEM = "repeat_item"  # ikinci maddeyi tekrar oku / üçüncü maddeye geç
    NEXT_ITEM = "next_item"  # sonraki madde
    PREVIOUS_ITEM = "previous_item"  # önceki madde
    FIRST_ITEM = "first_item"  # ilk madde
    LAST_ITEM = "last_item"  # son madde
    NEXT_SECTION = "next_section"  # sonraki bölüm / başlık
    SLOWER = "slower"  # biraz daha yavaş / yavaşla
    FASTER = "faster"  # biraz daha hızlı / hızlan
    SUMMARIZE = "summarize"  # özet geç / özetle / kısaca
    DETAIL = "detail"  # detaya gir / detaylandır / ayrıntı
    SKIP = "skip"  # burayı atla / bunu geç
    # M16 (docs/M16_ACTIVITY_LEDGER_SPEC.md §3.3): self explanation over the ledger
    TECHNICAL = "technical"  # teknik anlat / teknik olarak ne değişti
    EXPLAIN_PREVIOUS = "explain_previous"  # önceki maddeyi açıkla
    EXPLAIN = "explain"  # son yaptıklarını anlat / ne başarısız oldu / kanıtı ne ...
    FULL = "full"  # hepsini oku / tamamını anlat / bütün detayları oku

    # M18.3 (docs/M18_3_LIVING_CORE_WAKE_ALARM_SPEC.md §3.8, §6). The wake alarm and the
    # ambient display, in the SAME router as everything else — there is deliberately no
    # second Turkish table anywhere for these, for the reason `_explain_kind`'s docstring
    # records: two tables that must agree will not.
    ALARM_CREATE = "alarm_create"  # yarın sabah 07:30'da beni uyandır / saat 08:00'e alarm kur
    ALARM_TEST_CREATE = "alarm_test_create"  # 90 saniye sonra test alarmı kur
    ALARM_CANCEL = "alarm_cancel"  # alarmı iptal et
    ALARM_SNOOZE = "alarm_snooze"  # beş dakika ertele / on dakika ertele
    ALARM_STOP = "alarm_stop"  # alarmı kapat / alarmı durdur / alarmı sustur
    ALARM_QUERY = "alarm_query"  # sabah alarmım kaçta?
    DISPLAY_OFF = "display_off"  # ekranları kapat / ekranı kapat
    DISPLAY_WAKE = "display_wake"  # ekranları aç / ekranı aç
    DISPLAY_QUERY = "display_query"  # ekranlar açık mı?
    AMBIENT_POLICY_SET = "ambient_policy_set"  # uyurken ekranları kapat / ben yokken ...
    AMBIENT_TEST_DISPLAY = "ambient_test_display"  # ekran uyku otomasyonunu test et
    AMBIENT_EXPLAIN = "ambient_explain"  # ekranları neden kapattın / ekran politikası ne

    # M19 (docs/M19_DIGITAL_OPERATOR_SPEC.md §2, §3): the Digital Operator. Every one of
    # these targets an interactive-family capability through app.operator/tools_operator -
    # never a second desktop-control path (spec's invariant 1).
    APP_OPEN = "app_open"  # Not Defteri'ni aç / Chrome'u aç / Tarayıcıyı aç
    WINDOW_CLOSE = "window_close"  # bunu kapat / bu pencereyi kapat / öndeki pencereyi kapat
    WINDOW_MAXIMIZE = "window_maximize"  # pencereyi büyüt
    WINDOW_MINIMIZE = "window_minimize"  # bu pencereyi küçült
    WINDOW_RESTORE = "window_restore"  # pencereyi eski haline getir
    WINDOW_PREVIOUS = "window_previous"  # önceki pencereye dön / bir önceki pencereye geç
    TYPE_TEXT = "type_text"  # buraya X yaz / bu kutuya X yaz / seçili yere X yaz
    SHELL_QUERY = "shell_query"  # IP adresimi göster / bilgisayarın adı ne
    OPERATOR_CANCEL = "operator_cancel"  # dur / iptal et, while a task is running
    OPERATOR_STATUS = "operator_status"  # Ne yapıyorsun?, while a task is running
    # B39 (req 127-130): a multi-step mission ("Chrome'u aç ve YouTube'a gir",
    # "Ayarlarda Bluetooth'u aç") and its three words while one is parked or running.
    MISSION_START = "mission_start"
    MISSION_APPROVE = "mission_approve"  # Evet, başla - while the plan waits
    MISSION_PAUSE = "mission_pause"  # Bekle / duraklat - while it runs
    MISSION_RESUME = "mission_resume"  # Devam et - while it is paused

    # M20 (docs/M20_FILE_DOCUMENT_INTELLIGENCE_SPEC.md §3): File & Document Intelligence.
    # Every one of these targets app.documents through tools_documents - never a second
    # file-reading path (ADR-0083 decision 1).
    FILE_SEARCH = "file_search"  # bu klasördeki PDF'leri bul / masaüstündeki X'i bul
    DOCUMENT_READ = "document_read"  # bu dosyayı oku
    DOCUMENT_SUMMARIZE = "document_summarize"  # bunu özetle (bir belge odaktayken)
    DOCUMENT_ANSWER = "document_answer"  # ödeme süresi kaç gün / üçüncü sayfada ne yazıyor
    # B32 req 139/141/148/150/151/152: pictures, archives, full text, duplicates, preview.
    DOCUMENT_PREVIEW = "document_preview"  # bu belgeyi önizle
    DOCUMENT_FIND_TEXT = "document_find_text"  # içinde bütçe geçen belgeyi bul
    DOCUMENT_DUPLICATES = "document_duplicates"  # yinelenen dosyaları bul
    DOCUMENT_DEDUP = "document_dedup"  # kopyaları çöp kutusuna gönder
    # B34 req 153-167, 170: the managed mutations - journaled, reversible, approved by risk.
    DOCUMENT_WRITE = "document_write"  # X adında bir dosya oluştur
    DOCUMENT_APPEND = "document_append"  # bu dosyanın sonuna şunu ekle
    DOCUMENT_EDIT = "document_edit"  # bu dosyada X yerine Y yaz / bu belgeyi güncelle ve kaydet
    DOCUMENT_RENAME = "document_rename"  # bu dosyanın adını X yap
    DOCUMENT_MOVE = "document_move"  # bu dosyayı Masaüstüne taşı
    DOCUMENT_COPY = "document_copy"  # bu dosyayı kopyala
    DOCUMENT_DELETE = "document_delete"  # bu dosyayı sil (çöp kutusu, yedekli)
    DOCUMENT_APPLY = "document_apply"  # uygula / kaydet (bekleyen değişiklik)
    DOCUMENT_UNDO = "document_undo"  # son değişikliği geri al
    DOCUMENT_VERSIONS = "document_versions"  # bu dosyanın sürüm geçmişi
    IMAGE_TEXT = "image_text"  # görseldeki metni oku
    IMAGE_METADATA = "image_metadata"  # fotoğrafın bilgilerini oku
    DOCUMENT_COMPARE = "document_compare"  # bir önceki belgeyle karşılaştır
    DOCUMENT_INSPECT = "document_inspect"  # bu Excel'de ne var / kaç slayt var
    DOCUMENT_COMMON_POINTS = "document_common_points"  # bunların ortak noktalarını çıkar
    DOCUMENT_PREVIOUS = "document_previous"  # az önceki belgeye/sunuma dön

    # M21 (docs/M21_MAIL_CALENDAR_SPEC.md §3): Mail & Calendar. Every one of these targets
    # app.mail/app.calendar through tools_mail/tools_calendar - never a second mail/
    # calendar path (ADR-0084). The three tiers keep their own vocabulary (spec §1): READ
    # (INBOX/SEARCH/READ/THREAD, AGENDA/FIND_SLOT) mutates nothing the owner can see;
    # PREPARE (DRAFT_REPLY/DRAFT_NEW/EDIT_DRAFT/READ_DRAFT, PROPOSE/READ_PROPOSAL) writes a
    # local, reversible row, always read back; EXTERNAL MUTATION (SEND, COMMIT) is the
    # owner's alone, and DISCARD ends either the draft or the proposal, whichever is
    # pending (the tool layer decides which, from the SAME durable focus the read-back
    # gate itself reads — never from vocabulary alone).
    MAIL_INBOX = "mail_inbox"  # Gelen kutumda ne var? / Okunmamış maillerim var mı?
    MAIL_SEARCH = "mail_search"  # Fatura maillerini bul
    MAIL_READ = "mail_read"  # Ali'den gelen son maili oku
    MAIL_THREAD = "mail_thread"  # Bu konuşmanın tamamını oku
    MAIL_DRAFT_REPLY = "mail_draft_reply"  # Buna cevap yaz: ...
    MAIL_DRAFT_NEW = "mail_draft_new"  # Yeni mail: Ayşe'ye, konu ..., ...
    MAIL_EDIT_DRAFT = "mail_edit_draft"  # Konuyu 'Plan onayı' yap
    MAIL_READ_DRAFT = "mail_read_draft"  # Cevabı oku
    MAIL_SEND = "mail_send"  # Gönder. (only with a draft read back)
    # B45 (req 347, 348): a message's attachments.
    MAIL_ATTACHMENTS = "mail_attachments"  # Bu mailin eklerini göster
    MAIL_SAVE_ATTACHMENT = "mail_save_attachment"  # Eki bilgisayarıma kaydet
    CALENDAR_AGENDA = "calendar_agenda"  # Bugün takvimimde ne var?
    CALENDAR_FIND_SLOT = "calendar_find_slot"  # Cuma 60 dakikalık boşluk bul
    CALENDAR_PROPOSE = "calendar_propose"  # Perşembe 15'e diş hekimi ekle / Bunu bir saat ertele
    CALENDAR_READ_PROPOSAL = "calendar_read_proposal"  # Öneriyi oku
    CALENDAR_COMMIT = "calendar_commit"  # Onayla. / Tamam, ekle. (only with a proposal read back)
    DISCARD = "discard"  # Gönderme. / Vazgeç. — whichever of draft/proposal is pending

    # M22 (docs/M22_ARTIFACT_FACTORY_SPEC.md §5): the Artifact Factory. Every one of
    # these targets app.artifacts through tools_artifacts - never a second file-making
    # path. ARTIFACT_CREATE also carries a deictic format request against an artifact
    # that already exists ("Bunu PDF yap") - the router names only kind/title/numbers;
    # the model's own tool choice (artifact.create vs artifact.render) is not this
    # enum's business (contract §2 fixes the INTENT, never which tool answers it).
    ARTIFACT_CREATE = "artifact_create"  # Bana bir bütçe tablosu yap / Bunu PDF yap
    ARTIFACT_OPEN = "artifact_open"  # Bunu aç / Son ürettiğin dosyayı aç
    ARTIFACT_LIST = "artifact_list"  # Neler ürettin?
    ARTIFACT_VALIDATE = "artifact_validate"  # Bu dosya doğru mu?
    # B42 (req 410-416): the artifact in focus owns its lifecycle words.
    ARTIFACT_EDIT = "artifact_edit"  # Bu belgeye Riskler bölümünü ekle
    ARTIFACT_CLONE = "artifact_clone"  # Bunu kopyala
    ARTIFACT_DELETE = "artifact_delete"  # Bunu sil / Evet, sil
    ARTIFACT_COMPARE = "artifact_compare"  # Öncekiyle karşılaştır

    # M23 (docs/M23_APP_FACTORY_SPEC.md §5): the App Factory. Every one of these targets
    # app.appfactory through tools_apps - never a second app-building path (ADR-0086).
    # NAMED ``APP_FACTORY_*`` rather than the spec's own ``APP_*`` (its tool names, e.g.
    # ``app.open``, are kept exactly as named) because ``Intent.APP_OPEN`` already exists
    # (M19: launching a named OS application, "Chrome'u aç") - a genuinely different
    # utterance shape ("Uygulamayı aç" names no real app) that must not share an enum
    # member with a capability meaning something else entirely.
    APP_FACTORY_CREATE = "app_factory_create"  # Bana bir görev takip uygulaması yap
    APP_FACTORY_RUN = "app_factory_run"  # Uygulamayı çalıştır
    APP_FACTORY_TEST = "app_factory_test"  # Testleri çalıştır
    APP_FACTORY_STOP = "app_factory_stop"  # Uygulamayı durdur
    APP_FACTORY_STATUS = "app_factory_status"  # Uygulama çalışıyor mu?
    APP_FACTORY_OPEN = "app_factory_open"  # Uygulamayı aç
    APP_FACTORY_LIST = "app_factory_list"  # Hangi uygulamaları yaptın?
    APP_FACTORY_FIX = "app_factory_fix"  # Testleri düzelt / uygulamadaki hatayı düzelt (B40)
    # B41 (req 440-452): the generated application's lifecycle, gated on a project focus.
    APP_FACTORY_VERIFY = "app_factory_verify"  # Uygulamayı doğrula
    APP_FACTORY_LOG = "app_factory_log"  # Uygulamanın günlüğünü oku
    APP_FACTORY_PACKAGE = "app_factory_package"  # Uygulamayı paketle
    APP_FACTORY_LAUNCH = "app_factory_launch"  # Paketlenmiş sürümü başlat
    APP_FACTORY_HISTORY = "app_factory_history"  # Bu uygulamada neler yaptık?
    APP_FACTORY_RESUME = "app_factory_resume"  # Kitaplık uygulamasına devam edelim
    APP_FACTORY_MODIFY = "app_factory_modify"  # Bu uygulamaya ... ekle

    # M24 (docs/M24_CAPABILITY_GENESIS_SPEC.md §6): Capability Genesis. Every one of
    # these targets app.genesis through tools_genesis - never a second
    # capability-acquisition path (ADR-0087). The TARGET (which local application) and
    # the OPERATION (which of its controls) are both resolved from the owner's own
    # words against app.genesis.catalogue.GenesisInterfaceCatalogue, the same
    # "app.operator.plans.resolve_app_alias" shape APP_OPEN already uses for its own
    # allowlist - never a second name-resolution table.
    CAPABILITY_REQUEST = "capability_request"  # Sayaç kutusunu bir artır / Test lambasını aç
    CAPABILITY_STATUS = "capability_status"  # Yeni yetenek ne durumda? / Onu yapabiliyor musun?
    CAPABILITY_APPROVE = "capability_approve"  # Onaylıyorum / Bu uygulamayı yetkilendir
    CAPABILITY_CANCEL = "capability_cancel"  # Vazgeç, yapma

    # M25 (docs/M25_CREATIVE_3D_SPEC.md §5): 3D Creation. Every one of these targets
    # app.creative3d through tools_scene - never a second Blender/Unity path
    # (ADR-0088). Every matcher is gated on its OWN noun (sahne / küp / küre /
    # silindir / düzlem / ışık / kamera / render, spec §5's own list) so nothing here
    # can ever be reached by an utterance about a window, an alarm, a document or an
    # app - the same "every family requires its own noun" discipline
    # _APP_NOUN_STEMS's own module comment documents. SCENE_MATERIAL has no separate
    # utterance in the spec's own enumeration but the tool (scene.material) does
    # (spec §5's tool list) - added here as the router's own reasonable extension,
    # gated on a colour word plus an object noun, never colliding with anything above.
    SCENE_CREATE = "scene_create"  # Unity'de boş bir sahne oluştur / Blender'da yeni sahne aç
    SCENE_ADD = "scene_add"  # Bir küp ekle / Blender'da küre oluştur / Bir ışık ekle
    SCENE_TRANSFORM = "scene_transform"  # Küpü sağa taşı / Küreyi iki kat büyüt
    SCENE_MATERIAL = "scene_material"  # Küpü kırmızı yap / rengini maviye boya
    SCENE_LIGHT = "scene_light"  # Işığı ayarla / Işığı artır
    SCENE_CAMERA = "scene_camera"  # Kamerayı nesneye çevir
    SCENE_RENDER = "scene_render"  # Render al
    SCENE_INSPECT = "scene_inspect"  # Sahnede ne var?
    # B44 (req 526, 527): the production path's motion and export.
    SCENE_ANIMATE = "scene_animate"  # Küreye bir animasyon ekle
    SCENE_EXPORT = "scene_export"  # Sahneyi GLB olarak dışa aktar

    # M26 (docs/M26_EXECUTIVE_AUTONOMY_SPEC.md §5): Executive Autonomy. Every one of
    # these targets app.executive through tools_executive - never a second multi-step
    # orchestration path (ADR-0089). EXEC_START recognises the directive's three
    # SHAPES (a bounded, deliberately small vocabulary fragment — WHICH shape and the
    # full graph is app.executive.planner.RuleBasedExecutivePlanner's own job, given
    # the SAME directive text verbatim; this resolver only decides "is this a
    # multi-step executive directive at all", the same split app.explain.classify /
    # this file's own EXPLAIN branch already keeps for a different family). The other
    # six are gated on ``executive_run_active`` (the CALLER's one live fact, the same
    # "context, never vocabulary alone" discipline ``operator_running``/
    # ``genesis_awaiting_approval`` already establish) — EXEC_STATUS/EXEC_CANCEL would
    # otherwise silently steal "Ne yapıyorsun?"/"Vazgeç" from OPERATOR_STATUS/DISCARD
    # even with no run to be asked about.
    EXEC_START = (
        "exec_start"  # Son üç gündeki AI gelişmelerini araştır... Word raporu ve sunum hazırla.
    )
    EXEC_STATUS = "exec_status"  # Ne yapıyorsun? / Ne durumda?
    EXEC_EXPLAIN = "exec_explain"  # Şu an tam olarak ne yapıyorsun?
    EXEC_PAUSE = "exec_pause"  # Bu işi durdur / Bekle
    EXEC_RESUME = "exec_resume"  # Devam et
    EXEC_RETRY = "exec_retry"  # İkinci adımı tekrar dene / Araştırmayı tekrar dene
    EXEC_AMEND = "exec_amend"  # Sunumu da ekle / Excel'i de hazırla
    EXEC_CANCEL = "exec_cancel"  # Bunu iptal et / Vazgeç

    # docs/DECISIONS.md ADR-0091: Owner Location Context, Live Weather and the Morning
    # Briefing. WEATHER_QUERY covers both an explicit place ("İstanbul'da hava nasıl?")
    # and the owner's current/default location ("Hava nasıl?") — the router extracts the
    # place from the WORDS (weather_place below) exactly as APP_OPEN/ARTIFACT_CREATE
    # extract theirs; the tool asks app.location for everything else, never a second
    # place parser at the tool layer.
    WEATHER_QUERY = "weather_query"  # Hava nasıl? / İstanbul'da hava nasıl? / Kaç derece?
    LOCATION_DEFAULT_SET = "location_default_set"  # Varsayılan hava durumu konumumu ... yap.
    LOCATION_DEFAULT_QUERY = "location_default_query"  # Varsayılan konumum ne?
    LOCATION_SOURCE_QUERY = "location_source_query"  # Konumumu nereden biliyorsun? / güncel mi?
    MORNING_BRIEFING = "morning_briefing"  # Günaydın. / Sabah özetimi ver. / ... bekliyor?
    SYSTEM_STATUS_QUERY = "system_status_query"  # Sistem durumu nasıl?
    OVERNIGHT_WORK_QUERY = "overnight_work_query"  # Gece neler yaptın?
    # M26 addendum (docs/M26_LATEST_NEWS_MODE_SPEC.md §6): Latest News Mode. Two distinct
    # operations, deterministic — NEWS_OPEN plays the latest eligible video (a real
    # mutation: a browser opens, a video plays), NEWS_SUMMARIZE routes a current-events
    # SUMMARY through the existing research pipeline and never plays anything.
    # NEWS_QUERY_LATEST answers "son haber ne zaman yüklenmiş?" / "hangi haberi
    # açacaksın?" from the resolver alone, without opening anything.
    NEWS_OPEN = "news_open"  # Haberleri aç / Son haberleri aç / Show'un son haberini aç
    NEWS_SUMMARIZE = "news_summarize"  # Haberleri özetle / Bugünkü haberleri özetle
    NEWS_QUERY_LATEST = "news_query_latest"  # Son haber ne zaman yüklenmiş?

    # ADR-0112: media the OWNER named. Deliberately NARROW - it fires only when the
    # words carry an explicit media marker ("youtube", "şarkı", "müzik", "klip") - so
    # that "haberleri aç" stays NEWS_OPEN, "Chrome'u aç" stays APP_OPEN and "perdeleri
    # aç" stays whatever it already was. A play verb alone means nothing here.
    MEDIA_PLAY = "media_play"  # YouTube'dan X aç / X şarkısını aç / X çal
    MEDIA_STOP = "media_stop"  # Durdur (yalnızca bu araçla açılanı)

    # M27 (docs/M27_CREATIVE_TOOLS_SPEC.md §5, ADR-0093): the Creative Tools Operator.
    # Every one of these targets app.creative through tools_creative - never a second
    # image-editing path. Checked EARLY in resolve_intent (before the alarm/ambient
    # block) because this family's own verbs ("kaldır", "arka plan", "aç") are also
    # claimed elsewhere in this router with no gating noun of their own — see the
    # module comment above the match functions for the exact collisions found and why
    # priority position is the fix, not a vocabulary change on either side.
    CREATIVE_REDRAW = "creative_redraw"  # Bu resmi Paint'te yeniden çiz.
    CREATIVE_OPEN = "creative_open"  # Bunu Photoshop'ta aç.
    CREATIVE_BACKGROUND = "creative_background"  # Arka planını kaldır.
    CREATIVE_ADJUST = "creative_adjust"  # Renkleri biraz düzelt.
    CREATIVE_CLEANUP = "creative_cleanup"  # Logoyu daha temiz hale getir.
    CREATIVE_DESIGN = "creative_design"  # Figma'da buna benzeyen bir arayüz tasarla.
    CREATIVE_EXPORT = "creative_export"  # Bunu PNG olarak dışa aktar.
    # B43 (req 492, 509, 511, 512): the creative run's life after its first bytes.
    CREATIVE_GENERATE = "creative_generate"  # Bana bir logo üret: mavi bir dalga.
    CREATIVE_ENHANCE = "creative_enhance"  # Bu fotoğrafı düzelt.
    CREATIVE_UNDO = "creative_undo"  # Geri al.
    CREATIVE_REDO = "creative_redo"  # Yinele.
    CREATIVE_DELIVER = "creative_deliver"  # Bunu bilgisayarıma indir. / Paint'te göster.

    # M28 (docs/M28_NATIVE_APP_FACTORY_SPEC.md §6): the Native Desktop + Mobile
    # Application Factory. Every one of these targets app.nativefactory through
    # tools_native - never a second build path. This family's VERBS are the most
    # heavily shared in the whole router ("yap", "çıkar", "oluştur", "aç", "kontrol
    # et", "düzelt", "build et" are claimed by M19-M27 between them), so every matcher
    # below is gated on a NATIVE NOUN of its own (windows / masaüstü / android / exe /
    # apk / kurulum / emülatör) or, for the three utterances spec §6 spells with no
    # noun at all, on the CALLER's live "a native build exists" fact - and every one of
    # them REFUSES when another family's noun is present. Narrowed, never reordered:
    # the M27 lesson (the module comment above ``_native_create_windows_match`` names
    # the four real collisions this cost).
    NATIVE_CREATE_WINDOWS = "native_create_windows"  # Bana Windows için masaüstü uygulaması yap.
    NATIVE_BUILD_EXE = "native_build_exe"  # Bunu EXE olarak çıkar.
    NATIVE_BUILD_INSTALLER = "native_build_installer"  # Kurulum dosyasını oluştur.
    NATIVE_CREATE_ANDROID = "native_create_android"  # Android sürümünü yap.
    NATIVE_BUILD_APK = "native_build_apk"  # APK üret.
    NATIVE_EMULATOR_OPEN = "native_emulator_open"  # Uygulamayı emülatörde aç.
    NATIVE_CHECK = "native_check"  # Çalışıyor mu kontrol et.
    NATIVE_FIX = "native_fix"  # Hata varsa düzelt.
    NATIVE_REBUILD = "native_rebuild"  # Yeni sürümü build et.
    # B33 req 462-471: the lifecycle after the build, on the device.
    NATIVE_LAUNCH = "native_launch"  # Uygulamayı aç / çalıştır (Windows)
    NATIVE_VERIFY = "native_verify"  # Uygulamayı doğrula / arayüzünü test et
    NATIVE_LOG = "native_log"  # Uygulamanın günlüğünü oku
    NATIVE_UNINSTALL = "native_uninstall"  # Kurulumu kaldır
    NATIVE_UPDATE = "native_update"  # Uygulamayı güncelle
    # B35 (req 622/623): the owner assigns the system work on ITSELF - a queued
    # defect or feature on the self-development queue, never a live edit.
    SELFDEV_FIX = "selfdev_fix"  # Şu bug'ı kendin düzelt
    SELFDEV_FEATURE = "selfdev_feature"  # Şu özelliği kendine ekle
    SELFDEV_STATUS = "selfdev_status"  # Kendinde ne düzeltiyorsun?

    NONE = "none"


#: Scope tells the caller WHICH subsystem the intent targets, resolved from
#: state: a paused narration owns "devam"; a cancelled conversational answer
#: owns "devam" otherwise.
SCOPE_NARRATION = "narration"
SCOPE_CONVERSATION = "conversation"

#: The three classes of owner utterance (docs/M18_ACTION_CONTRACT.md §2). A QUERY is
#: answered from an authoritative source and mutates nothing; an ACTION targets a
#: canonical capability and ends in a receipt; a CONTROL steers the conversation or a
#: narration (stop, resume, item moves, speed). This resolver is the ONLY place the
#: class is decided.
KLASS_QUERY = "query"
KLASS_ACTION = "action"
KLASS_CONTROL = "control"

#: The canonical capability each ACTION intent targets (contract §2's third column).
CAPABILITY_BY_INTENT: dict[Intent, str] = {
    Intent.EYE_DISABLE: "eye.disable",
    Intent.EYE_ENABLE: "eye.enable",
    Intent.DEPLOY: "release.promote",
    Intent.EVOLUTION_PAUSE: "evolution.control",
    Intent.EVOLUTION_RESUME: "evolution.control",
    Intent.EVOLUTION_CANCEL: "evolution.control",
    Intent.EVOLUTION_HOLD: "evolution.control",
    Intent.RELEASE_ROLLBACK: "release.rollback",
    # M18.3 (spec §3.8): every one of these ends in an ActionReceipt. ALARM_TEST_CREATE
    # targets the SAME capability as ALARM_CREATE — a test alarm is a real alarm with
    # `test=true` and a short offset (spec §8.1), not a second code path, so it must not be
    # a second capability either.
    # B14 req 287-291. `routine.list` is a QUERY and lives in the other table.
    Intent.ROUTINE_CREATE: "routine.create",
    Intent.ROUTINE_CANCEL: "routine.cancel",
    Intent.ROUTINE_PAUSE: "routine.pause",
    Intent.ROUTINE_RESUME: "routine.resume",
    # B16 req 35-38. `memory.search` and `memory.why` are QUERIES and live in the other
    # table: they read a memory back and change nothing (they do write a `memory.used`
    # receipt, which is evidence ABOUT the read, not a mutation of the row).
    Intent.MEMORY_REMEMBER: "memory.remember",
    Intent.MEMORY_FORGET: "memory.forget",
    Intent.MEMORY_CORRECT: "memory.correct",
    Intent.MEMORY_PIN: "memory.pin",
    Intent.ALARM_CREATE: "alarm.create",
    Intent.ALARM_TEST_CREATE: "alarm.create",
    Intent.ALARM_CANCEL: "alarm.cancel",
    Intent.ALARM_SNOOZE: "alarm.snooze",
    Intent.ALARM_STOP: "alarm.stop",
    Intent.DISPLAY_OFF: "display.off",
    Intent.DISPLAY_WAKE: "display.wake",
    Intent.AMBIENT_POLICY_SET: "ambient.set_policy",
    Intent.AMBIENT_TEST_DISPLAY: "ambient.test_display",
    # M19 (spec §2, §3): the window-control family shares one capability/tool - the
    # PAYLOAD's ``action`` field is what differs, derived from the intent by the tool.
    Intent.APP_OPEN: "operator.app_open",
    Intent.WINDOW_CLOSE: "operator.window_control",
    Intent.WINDOW_MAXIMIZE: "operator.window_control",
    Intent.WINDOW_MINIMIZE: "operator.window_control",
    Intent.WINDOW_RESTORE: "operator.window_control",
    Intent.WINDOW_PREVIOUS: "operator.window_control",
    Intent.TYPE_TEXT: "operator.type",
    Intent.OPERATOR_CANCEL: "operator.cancel",
    Intent.MISSION_START: "operator.mission",
    Intent.MISSION_APPROVE: "operator.mission",
    Intent.MISSION_PAUSE: "operator.mission",
    Intent.MISSION_RESUME: "operator.mission",
    # M21 (spec §3): the PREPARE and EXTERNAL MUTATION tiers end in a receipt, the same
    # class alarm.create/ambient.set_policy already get. DISCARD is deliberately absent —
    # it targets one of two capabilities depending on which object is pending, decided at
    # the tool layer, and `ResolvedIntent.capability`/`klass` are set explicitly at its own
    # call site in resolve_intent rather than through this static table.
    Intent.MAIL_DRAFT_REPLY: "mail.draft",
    Intent.MAIL_DRAFT_NEW: "mail.draft",
    Intent.MAIL_EDIT_DRAFT: "mail.edit_draft",
    Intent.MAIL_READ_DRAFT: "mail.read_draft",
    Intent.MAIL_SEND: "mail.send",
    Intent.CALENDAR_PROPOSE: "calendar.propose",
    Intent.CALENDAR_READ_PROPOSAL: "calendar.read_proposal",
    Intent.CALENDAR_COMMIT: "calendar.commit",
    # M22 (spec §5): making a file and fetching+opening one on the owner's machine are
    # both real mutations, the same class alarm.create/mail.draft already get.
    Intent.ARTIFACT_CREATE: "artifact.create",
    Intent.ARTIFACT_OPEN: "artifact.open",
    # M23 (spec §5): scaffolding, running, testing, stopping and opening a project on the
    # owner's machine are all real mutations, the same class artifact.create/open already
    # get.
    Intent.APP_FACTORY_CREATE: "app.create",
    Intent.APP_FACTORY_RUN: "app.run",
    Intent.APP_FACTORY_TEST: "app.test",
    Intent.APP_FACTORY_FIX: "app.fix",
    Intent.APP_FACTORY_VERIFY: "app.verify",
    Intent.APP_FACTORY_LOG: "app.log",
    Intent.APP_FACTORY_PACKAGE: "app.package",
    Intent.APP_FACTORY_LAUNCH: "app.launch",
    Intent.APP_FACTORY_HISTORY: "app.history",
    Intent.APP_FACTORY_RESUME: "app.resume",
    Intent.APP_FACTORY_MODIFY: "app.modify",
    Intent.APP_FACTORY_STOP: "app.stop",
    Intent.APP_FACTORY_OPEN: "app.open",
    # M24 (spec §6): a genesis request/approval/cancellation is a real mutation
    # (a candidate is built, tested, rolled out, registered and used, or a
    # mutating operation actually runs against the owner's local application)
    # - the same receipt class alarm.create/mail.send/app.create already get.
    Intent.CAPABILITY_REQUEST: "capability.request",
    Intent.CAPABILITY_APPROVE: "capability.approve",
    Intent.CAPABILITY_CANCEL: "capability.cancel",
    # M25 (spec §5): creating/changing a 3D scene on the owner's machine is a real
    # mutation, the same class app.create/capability.request already get. Inspecting
    # one is not (SCENE_INSPECT is a QUERY_TOOL_BY_INTENT entry instead, below).
    Intent.SCENE_CREATE: "scene.create",
    Intent.SCENE_ADD: "scene.add",
    Intent.SCENE_TRANSFORM: "scene.transform",
    Intent.SCENE_MATERIAL: "scene.material",
    Intent.SCENE_LIGHT: "scene.light",
    Intent.SCENE_CAMERA: "scene.camera",
    Intent.SCENE_RENDER: "scene.render",
    Intent.SCENE_ANIMATE: "scene.animate",
    Intent.SCENE_EXPORT: "scene.export",
    # M26 (spec §5): starting/pausing/resuming/retrying/amending/cancelling a durable
    # multi-step job is a real mutation, the same class every other family above gets.
    # EXEC_STATUS/EXEC_EXPLAIN are QUERY_TOOL_BY_INTENT entries instead (read nothing
    # the owner cannot already see, mutate nothing).
    Intent.EXEC_START: "executive.start",
    Intent.EXEC_PAUSE: "executive.pause",
    Intent.EXEC_RESUME: "executive.resume",
    Intent.EXEC_RETRY: "executive.retry",
    Intent.EXEC_AMEND: "executive.amend",
    Intent.EXEC_CANCEL: "executive.cancel",
    # ADR-0091: setting the owner's durable default weather location is a real (local)
    # mutation — the same receipt class alarm.create/mail.draft already get. Reading it
    # back, asking where a location came from, and asking for the weather/system status/
    # overnight summary/briefing all read something without changing it, so each is a
    # QUERY_TOOL_BY_INTENT entry instead, below.
    Intent.LOCATION_DEFAULT_SET: "location.set_default",
    # M26 addendum (spec §6): opening a video and starting a summary run are both real
    # mutations (a browser opens/plays; a research task is created) - the same class
    # every other family above gets.
    Intent.NEWS_OPEN: "news.open",
    Intent.MEDIA_PLAY: "media.play",
    Intent.MEDIA_STOP: "media.stop",
    Intent.NEWS_SUMMARIZE: "news.summarize",
    # M27 (spec §5): planning and executing a creative-tool edit is a real mutation
    # (a file is produced, compared and stored) - the same class every other family
    # above gets. CREATIVE_OPEN is an ACTION too, even when it ends in an honest
    # refusal (the same "APP_OPEN/ARTIFACT_OPEN are actions regardless of outcome"
    # rule already applies to their own open verbs).
    Intent.CREATIVE_REDRAW: "creative.redraw",
    Intent.CREATIVE_OPEN: "creative.open",
    Intent.CREATIVE_BACKGROUND: "creative.background",
    Intent.CREATIVE_ADJUST: "creative.adjust",
    Intent.CREATIVE_CLEANUP: "creative.cleanup",
    Intent.CREATIVE_DESIGN: "creative.design",
    Intent.CREATIVE_EXPORT: "creative.export",
    Intent.CREATIVE_GENERATE: "creative.generate",
    Intent.CREATIVE_ENHANCE: "creative.enhance",
    Intent.CREATIVE_UNDO: "creative.undo",
    Intent.CREATIVE_REDO: "creative.redo",
    Intent.CREATIVE_DELIVER: "creative.deliver",
    # M28 (spec §6): scaffolding, compiling, packaging, installing, launching, fixing
    # and rebuilding a real distributable application on the owner's machine are all
    # real mutations - the same receipt class app.create/creative.redraw already get.
    # NATIVE_CHECK is a QUERY_TOOL_BY_INTENT entry instead (below): it reads a build
    # row back and drives nothing.
    Intent.NATIVE_CREATE_WINDOWS: "native.create",
    Intent.NATIVE_CREATE_ANDROID: "native.create",
    Intent.NATIVE_BUILD_EXE: "native.build",
    Intent.NATIVE_BUILD_APK: "native.build",
    Intent.NATIVE_BUILD_INSTALLER: "native.package",
    Intent.NATIVE_EMULATOR_OPEN: "native.launch",
    Intent.NATIVE_FIX: "native.fix",
    Intent.NATIVE_REBUILD: "native.rebuild",
    # B33.
    Intent.NATIVE_LAUNCH: "native.launch",
    Intent.NATIVE_VERIFY: "native.verify",
    Intent.NATIVE_LOG: "native.log",
    Intent.NATIVE_UNINSTALL: "native.uninstall",
    Intent.NATIVE_UPDATE: "native.update",
    Intent.SELFDEV_FIX: "selfdev.defect",
    Intent.SELFDEV_FEATURE: "selfdev.feature",
    Intent.SELFDEV_STATUS: "selfdev.status",
    # B27 req 731-733, 735. Each ACTS: a workflow is cancelled, a volume changes on the
    # device, the screen is read, a calendar deletion is asked for - the last one ends in
    # an honest refusal today (the writer has no delete, spec §1's own boundary; B46 owns
    # the policy) and is an ACTION all the same, for the reason DEPLOY already is: the
    # refusal is a receipt, and a receipt is evidence.
    Intent.RESEARCH_CANCEL: "research.cancel",
    # B31 req 201/203/204/209.
    Intent.RESEARCH_PAUSE: "research.pause",
    Intent.RESEARCH_RESUME: "research.resume",
    Intent.RESEARCH_OPEN: "research.open",
    Intent.RESEARCH_ANSWER_MODE: "research.answer_mode",
    Intent.MEDIA_VOLUME: "media.volume",
    Intent.SCREENSHOT_CAPTURE: "operator.screenshot",
    Intent.CALENDAR_CANCEL: "calendar.cancel",
    # B28 req 92/93/98: a key press and a scroll both act on the owner's desktop.
    Intent.OPERATOR_KEY: "operator.key",
    Intent.OPERATOR_SCROLL: "operator.pointer",
    # B29 req 100: invoking a button through UI Automation acts on the desktop.
    Intent.UI_INVOKE: "operator.ui",
    # B30 req 82/120/122: closing an application, stopping a process, restarting a service.
    Intent.APP_CLOSE: "operator.app_close",
    Intent.PROCESS_STOP: "operator.process",
    Intent.SERVICE_RESTART: "operator.service",
}

#: QUERY intents that name a tool rather than being answered conversationally (contract §2:
#: a query is answered from an authoritative source and mutates nothing). Kept separate
#: from CAPABILITY_BY_INTENT because that map is what makes an intent an ACTION.
QUERY_TOOL_BY_INTENT: dict[Intent, str] = {
    Intent.ALARM_QUERY: "alarm.status",
    # B15 req 271. A query: it reads a clock and changes nothing.
    Intent.CLOCK_QUERY: "clock.now",
    # B14 req 288. Reading back what the owner already set up mutates nothing.
    Intent.ROUTINE_LIST: "routine.list",
    # B16 req 32/61. Both read and neither mutates.
    Intent.MEMORY_SEARCH: "memory.search",
    Intent.MEMORY_WHY: "memory.why",
    Intent.DISPLAY_QUERY: "display.status",
    # ADR-0079 §12: "why did / didn't you" and "what is the policy now" are answered from
    # the live decision, the presence assertion, the holdoffs and the ledger - a query.
    Intent.AMBIENT_EXPLAIN: "ambient.explain",
    # M19 (spec §2, §3): neither mutates anything - a shell read and "what are you doing".
    Intent.SHELL_QUERY: "operator.shell",
    Intent.OPERATOR_STATUS: "operator.status",
    # M20 (docs/M20_FILE_DOCUMENT_INTELLIGENCE_SPEC.md §3): reading and answering from a
    # document mutates nothing the owner can see (the index is Cloud Core bookkeeping) -
    # every one of these is a query, the same class operator.shell/status already get.
    Intent.FILE_SEARCH: "file.search",
    Intent.DOCUMENT_READ: "document.read",
    Intent.DOCUMENT_SUMMARIZE: "document.summarize",
    Intent.DOCUMENT_ANSWER: "document.answer",
    Intent.DOCUMENT_COMPARE: "document.compare",
    # B32.
    Intent.DOCUMENT_PREVIEW: "document.preview",
    Intent.DOCUMENT_FIND_TEXT: "document.find_text",
    Intent.DOCUMENT_DUPLICATES: "document.duplicates",
    Intent.DOCUMENT_DEDUP: "document.dedup",
    # B34.
    Intent.DOCUMENT_WRITE: "document.write",
    Intent.DOCUMENT_APPEND: "document.append",
    Intent.DOCUMENT_EDIT: "document.edit",
    Intent.DOCUMENT_RENAME: "document.rename",
    Intent.DOCUMENT_MOVE: "document.move",
    Intent.DOCUMENT_COPY: "document.copy",
    Intent.DOCUMENT_DELETE: "document.delete",
    Intent.DOCUMENT_APPLY: "document.apply",
    Intent.DOCUMENT_UNDO: "document.undo",
    Intent.DOCUMENT_VERSIONS: "document.versions",
    Intent.IMAGE_TEXT: "document.read",
    Intent.IMAGE_METADATA: "document.inspect",
    Intent.DOCUMENT_INSPECT: "document.inspect",
    Intent.DOCUMENT_COMMON_POINTS: "document.common_points",
    Intent.DOCUMENT_PREVIOUS: "document.previous",
    # M21 (spec §3): reading and searching mail/calendar mutates nothing the owner can
    # see (mail_index/calendar_index are Cloud Core bookkeeping) - the same query class
    # the document family already gets for the identical reason.
    Intent.MAIL_INBOX: "mail.inbox",
    Intent.MAIL_SEARCH: "mail.search",
    Intent.MAIL_READ: "mail.read",
    Intent.MAIL_THREAD: "mail.thread",
    Intent.MAIL_ATTACHMENTS: "mail.attachments",
    Intent.MAIL_SAVE_ATTACHMENT: "mail.save_attachment",
    Intent.CALENDAR_AGENDA: "calendar.agenda",
    Intent.CALENDAR_FIND_SLOT: "calendar.find_slot",
    # M22 (spec §5): listing what was made and re-checking it mutate nothing the owner
    # can see (re-validation writes bookkeeping only) - the same query class the
    # document/mail families already get for the identical reason.
    Intent.ARTIFACT_LIST: "artifact.list",
    Intent.ARTIFACT_VALIDATE: "artifact.validate",
    Intent.ARTIFACT_EDIT: "artifact.edit",
    Intent.ARTIFACT_CLONE: "artifact.clone",
    Intent.ARTIFACT_DELETE: "artifact.delete",
    Intent.ARTIFACT_COMPARE: "artifact.compare",
    # M23 (spec §5): a status read-back and listing what was made mutate nothing the
    # owner can see - the same query class artifact.list/validate already get.
    Intent.APP_FACTORY_STATUS: "app.status",
    Intent.APP_FACTORY_LIST: "app.list",
    # M24 (spec §6): a status read-back mutates nothing the owner can see - the same
    # query class artifact.list/app.status already get.
    Intent.CAPABILITY_STATUS: "capability.status",
    # M25 (spec §5): reading a scene back mutates nothing the owner can see (the
    # device call is a read of the tool's own state) - the same query class
    # document.read/app.status already get for the identical reason.
    Intent.SCENE_INSPECT: "scene.inspect",
    # M26 (spec §5): a status read-back and the current step's own one-sentence
    # explanation mutate nothing the owner can see - the same query class every other
    # family's own status/explain entry above already gets.
    Intent.EXEC_STATUS: "executive.status",
    Intent.EXEC_EXPLAIN: "executive.explain",
    # ADR-0091: weather/location/briefing reads — none mutate anything the owner can
    # see, the same query class every family above already gets for the identical
    # reason.
    Intent.WEATHER_QUERY: "weather.current",
    Intent.LOCATION_DEFAULT_QUERY: "location.get_default",
    Intent.LOCATION_SOURCE_QUERY: "weather.last_evidence",
    Intent.MORNING_BRIEFING: "briefing.morning",
    Intent.SYSTEM_STATUS_QUERY: "briefing.system_status",
    Intent.OVERNIGHT_WORK_QUERY: "briefing.overnight_work",
    # M26 addendum (spec §6): the resolver's own decision, read without opening anything -
    # mutates nothing the owner can see, the same query class every other family's
    # own status/explain entry above already gets.
    Intent.NEWS_QUERY_LATEST: "news.query_latest",
    # M28 (spec §6): "Çalışıyor mu kontrol et." reads the build row and the artefact
    # facts an independent reader already recorded - it drives nothing and changes
    # nothing the owner can see, so it is a query, the same class app.status/
    # scene.inspect already get for the identical reason.
    Intent.NATIVE_CHECK: "native.check",
    # B27 req 734: "Neler yapabilirsin?" reads the registry (B25 built the tool) and
    # changes nothing - a query, the same class clock.now already gets.
    Intent.CAPABILITIES_QUERY: "assistant.capabilities",
    # B29 req 102/105: reading a control's text and describing the screen change nothing.
    Intent.UI_READ: "operator.inspect",
    Intent.SCREEN_DESCRIBE: "operator.see",
    # B30 req 119/121: reading what runs changes nothing.
    Intent.PROCESS_QUERY: "operator.process",
    Intent.SERVICE_QUERY: "operator.service",
}


#: The four RESEARCH interaction classes (docs/DECISIONS.md ADR-0075). They are not
#: intents: an owner utterance about a research already carries an intent (TECHNICAL,
#: EXPLAIN, REPEAT, NONE ...), and what the server additionally has to know is whether
#: this turn may START A CRAWL. That question has exactly four answers, and they are
#: decided HERE, in the one router, so the durable audit row says which class was
#: decided and no second Turkish table can disagree with it.
RESEARCH_CLASS_NEW = "new_research"
RESEARCH_CLASS_TECHNICAL_EXPLANATION = "research_technical_explanation"
RESEARCH_CLASS_FOLLOWUP = "research_followup"
RESEARCH_CLASS_RETRY = "research_retry"

RESEARCH_CLASSES: Final[tuple[str, ...]] = (
    RESEARCH_CLASS_NEW,
    RESEARCH_CLASS_TECHNICAL_EXPLANATION,
    RESEARCH_CLASS_FOLLOWUP,
    RESEARCH_CLASS_RETRY,
)

#: The two classes that MAY start a crawl. Everything else about a completed research is
#: answered from that research's own report.
RESEARCH_CLASSES_MAY_CRAWL: Final[tuple[str, ...]] = (
    RESEARCH_CLASS_NEW,
    RESEARCH_CLASS_RETRY,
)

#: The two classes that are ABOUT a research that already finished, and therefore must
#: never become a second crawl (ADR-0075 decision 3).
RESEARCH_CLASSES_BOUND_TO_A_RUN: Final[tuple[str, ...]] = (
    RESEARCH_CLASS_TECHNICAL_EXPLANATION,
    RESEARCH_CLASS_FOLLOWUP,
)


def klass_for(intent: Intent) -> str:
    """query | action | control for an intent (contract §2).

    ``NONE`` is reported as a query: nothing this resolver owns was said, the model
    answers conversationally, and nothing mutates - which is the query class's
    guarantee. It is emphatically not an action."""
    if intent in CAPABILITY_BY_INTENT:
        return KLASS_ACTION
    if intent in (Intent.EXPLAIN, Intent.NONE) or intent in QUERY_TOOL_BY_INTENT:
        return KLASS_QUERY
    return KLASS_CONTROL


PRESENTATION_SUMMARY = "summary"
PRESENTATION_DETAIL = "detail"
PRESENTATION_TECHNICAL = "technical"
PRESENTATION_FULL = "full"

#: Section titles of an activity briefing (app.explain) that the presentation levels map
#: onto. A briefing is an artifact, so "özetle" / "detay ver" / "teknik anlat" are cursor
#: jumps into it, not a different document.
LEVEL_SECTION_TITLES: dict[str, tuple[str, ...]] = {
    PRESENTATION_SUMMARY: ("özet", "ozet"),
    PRESENTATION_DETAIL: ("ayrıntı", "ayrinti", "detay"),
    PRESENTATION_TECHNICAL: ("teknik",),
}

#: Questions about the system's own activity (spec §2). Each entry: the tokens that must
#: ALL be present (as stems), and the query kind they resolve to. Order matters: the
#: first match wins, so the more specific phrasings come first.
#: REMOVED 2026-09-05. This was a second Turkish pattern table, duplicating
#: app/explain/classify.py's. They drifted, and the drift cost an owner qualification run:
#: the classifier knew the M17 question kinds and this list did not. _explain_kind now
#: delegates, so there is one table and it cannot disagree with itself.

SPEED_STEP = 0.25

# Hesitation fillers (spec §5 hesitation guard vocabulary + common Turkish).
FILLERS = frozenset(
    {
        "şey",
        "yani",
        "hani",
        "işte",
        "böyle",
        "ya",
        "yaa",
        "ee",
        "eee",
        "ıı",
        "ııı",
        "ı",
        "hmm",
        "hm",
        "hımm",
        "hım",
        "ehm",
        "aa",
        "aaa",
        "of",
        "e",
        "mm",
        "mmm",
    }
)
_ELONGATED_FILLER = re.compile(r"^(?:ı{2,}|e{2,}|a{2,}|m{2,}|h[ıi]?m+|ee+h?|ya+)$")

# Stop tokens: the M4 STOP_WORDS (single-word members) plus imperative forms
# that M4's narration command parser already treats as "dur".
_SINGLE_STOP_WORDS = frozenset(w for w in STOP_WORDS if " " not in w)
_MULTI_STOP_PHRASES = tuple(w for w in STOP_WORDS if " " in w)
STOP_TOKENS = _SINGLE_STOP_WORDS | frozenset({"durdur", "duraklat", "bekle"})

_ORDINALS: dict[str, int] = {**commands._ORDINAL_WORDS}
# Stems, because Turkish suffixes soften the final consonant (başlık -> başlığa).
_ITEM_NOUNS = ("madde", "paragraf", "nokta", "başlı", "bölüm")
_SECTION_NOUNS = ("bölüm", "başlı")

_PUNCT_RE = re.compile(r"[^\w\s']", re.UNICODE)


@dataclass(frozen=True, slots=True)
class ResolvedIntent:
    intent: Intent
    scope: str = SCOPE_CONVERSATION
    target_index: int | None = None  # 1-based item index for REPEAT_ITEM
    normalized_text: str = ""
    tokens: tuple[str, ...] = ()
    fillers_removed: int = 0
    confidence: float = 1.0
    matched: str = ""  # the token/phrase that decided it (for audit/debug)
    query_kind: str | None = None  # EXPLAIN only: which question about the system
    #: One of :data:`RESEARCH_CLASSES` when this utterance is about research at all
    #: (ADR-0075). ``None`` means "nothing to do with research" - never "safe to crawl".
    research_class: str | None = None
    #: WHICH research the utterance points at (ADR-0076), as words alone can tell:
    #: current | previous | ordinal | topic | none. ``selection`` is never decided here -
    #: it means "an answer to the question the server just asked", and only the resolver
    #: knows whether such a question is open.
    reference: ResearchReference | None = None
    #: query | action | control (contract §2); derived from the intent unless given.
    klass: str = ""
    #: The canonical capability an ACTION targets ("eye.disable"); None for the rest.
    capability: str | None = None
    #: ADR-0079 §7: for an AMBIENT_POLICY_SET utterance, the policy fields the owner's
    #: WORDS set (``ambient_policy_changes``) - derived in the one router, recorded on the
    #: turn, and preferred by the tool over whatever booleans the model passed.
    policy_changes: dict[str, bool | int] | None = None
    #: For an ALARM_SNOOZE utterance, the minutes the owner SAID (``spoken_minutes``);
    #: None when no count was spoken, and the alarm's own default applies.
    alarm_minutes: int | None = None
    #: M18.4: for an EVOLUTION_* utterance, the action the owner's words asked for
    #: (pause | resume | cancel | hold); the tool applies THIS, never the model's argument.
    evolution_action: str | None = None
    #: M19 (spec §3): for APP_OPEN, the allowlisted app id the owner's WORDS named
    #: (app.operator.plans.resolve_app_alias) - the tool prefers THIS over the model's own
    #: ``application`` argument, the same "owner's words win" rule ambient policy and the
    #: snooze minutes already follow.
    application: str | None = None
    #: For TYPE_TEXT, the payload extracted from the phrase itself ("buraya X yaz" -> X),
    #: or None when the owner named no text at all ("Şuraya yazar mısın?") - a clarification
    #: is then the honest answer, not a guess.
    text_to_type: str | None = None
    #: For SHELL_QUERY, which reading was asked for: "ip" | "hostname" | "whoami" (B30).
    shell_query: str | None = None
    #: For the window-control family and TYPE_TEXT, which window the owner's words pointed
    #: at as far as vocabulary alone can say: "current" | "previous" | None. The tool
    #: resolves the actual window id through the durable focus stack either way.
    window_ref: str | None = None
    #: M20 (docs/M20_FILE_DOCUMENT_INTELLIGENCE_SPEC.md §3): for the document family,
    #: which document the owner's WORDS pointed at: "current" | "previous" | None. The
    #: tool resolves the actual document through the durable focus stack (app.operator.focus,
    #: kind "document") either way - the same "owner's words win" rule window_ref follows.
    document_ref: str | None = None
    #: For DOCUMENT_ANSWER, the raw question (the owner's own words, never a paraphrase).
    question: str | None = None
    #: B34: for DOCUMENT_EDIT, the words to find and the words to put in their place ("X
    #: yerine Y yaz"); None for the "güncelle ve kaydet" shape, whose new text is the model's.
    find_text: str | None = None
    replace_text: str | None = None
    #: B34: for DOCUMENT_WRITE / RENAME / COPY, the file name the owner's words named.
    new_name: str | None = None
    #: For FILE_SEARCH, the name fragment the owner's words named ("sözleşme", "bütçe"),
    #: or None when none was said (a bare "bu klasördeki PDF'leri bul").
    pattern: str | None = None
    #: For FILE_SEARCH, the folder the owner's words named ("Masaüstü" -> "Desktop"), or
    #: None when none was said.
    folder: str | None = None
    #: For FILE_SEARCH, the extensions the owner's words named ([".pdf"]), or None.
    extensions: list[str] | None = None
    #: M21 (docs/M21_MAIL_CALENDAR_SPEC.md §3): for the mail family, which message the
    #: owner's WORDS pointed at: "current" | "previous" | None. ``None`` means the words
    #: named neither — the tool falls back to the model's own ``target`` argument (a
    #: spoken name, "Ali'den gelen son maili oku"), the same "owner's words win only when
    #: they actually said something" rule ``document_ref`` already follows.
    mail_ref: str | None = None
    #: For the calendar reschedule shape ("Bunu bir saat ertele"), the event the owner's
    #: WORDS pointed at: "current" | None.
    calendar_ref: str | None = None
    #: B46 (req 356, 357): for CALENDAR_PROPOSE, the recurrence rule and the reminder
    #: (minutes before) the owner's own words asked for - read from the utterance, so
    #: the model can neither drop nor invent them. None when nothing was said.
    calendar_rrule: str | None = None
    calendar_reminder_minutes: int | None = None
    #: M22 (docs/M22_ARTIFACT_FACTORY_SPEC.md §5): for the artifact family, which
    #: artifact the owner's WORDS pointed at: "current" | "previous" | None. ``None``
    #: means the words named neither and the tool falls back to its own default
    #: ("current") - the same "owner's words win only when they actually said
    #: something" rule ``document_ref``/``mail_ref`` already follow.
    artifact_ref: str | None = None
    #: For ARTIFACT_CREATE, the kind word the owner's WORDS carried ("tablo" ->
    #: "spreadsheet", "belge"/"word" -> "document", "sunum"/"slayt" -> "presentation",
    #: "liste"/"csv" -> "dataset", "sayfa" -> "page"), or None when no kind word was
    #: said at all ("Bunu PDF yap" against an artifact that already exists).
    artifact_kind: str | None = None
    #: For ARTIFACT_CREATE, the title the owner's WORDS carried (the text between any
    #: leading filler and the create verb, minus the kind word itself), or None when
    #: nothing recognisable remains - a best-effort convenience the tool prefers only
    #: when non-empty, never a substitute for the model's own title.
    artifact_title: str | None = None
    #: B42 (req 412): the owner's explicit yes to a delete ("Evet, sil").
    artifact_confirm: bool = False
    #: For ARTIFACT_CREATE, every number the owner's WORDS actually said ("kira 12000,
    #: maaş 45000" -> [12000.0, 45000.0]), the closed set ``ArtifactSpec`` validates the
    #: model's own ``spec`` argument against (the "never invented" rule) - None when no
    #: number was said at all.
    spoken_numbers: list[float] | None = None
    #: M23 (docs/M23_APP_FACTORY_SPEC.md §5): for the App Factory family, which project
    #: the owner's WORDS pointed at: "current" | None. ``None`` means the words named
    #: neither and the tool falls back to its own default ("current") - the same
    #: "owner's words win only when they actually said something" rule
    #: ``artifact_ref``/``document_ref`` already follow.
    app_ref: str | None = None
    #: For APP_FACTORY_CREATE, the built-in template word the owner's WORDS carried
    #: ("görev takip" -> "task-tracker", "web sayfası" -> "static-page", "komut satırı"/
    #: "cli" -> "cli-tool"), or None when no template word was said at all - the model
    #: still names its own template, and this is only a best-effort convenience the tool
    #: prefers when non-empty (the same rule ``artifact_kind`` already follows).
    app_template: str | None = None
    #: For APP_FACTORY_CREATE, the name the owner's WORDS carried ("adı Notlarım" ->
    #: "Notlarım"), or None when no name was said - the model still names its own
    #: ``name`` argument, and this is preferred only when non-empty.
    app_name: str | None = None
    #: For APP_FACTORY_CREATE against a "cli-tool" template, every command NAME the
    #: owner's WORDS carried ("selamla ve say komutları" -> ["selamla", "say"]), or None
    #: when none was said - the model still names its own ``commands``, and this is
    #: preferred only when non-empty.
    app_commands: list[str] | None = None
    #: B40 (req 422): the owner's whole sentence when no built-in template fits it.
    app_request: str | None = None
    #: M24 (docs/M24_CAPABILITY_GENESIS_SPEC.md §6): for the Capability Genesis
    #: family, the interface the owner's WORDS named, resolved against
    #: app.genesis.catalogue.GenesisInterfaceCatalogue - the interface's own
    #: name and its researchable /spec url, or both None when the words named
    #: no KNOWN local application at all (a target outside the catalogue is
    #: refused at the tool layer before any research happens, spec §7's own
    #: negative case - never a guess at what "google" might mean).
    capability_target_name: str | None = None
    capability_target_url: str | None = None
    #: For CAPABILITY_REQUEST, the operation id the owner's WORDS named among
    #: the resolved target's own operation aliases ("bir artır" -> "increment"),
    #: or None when no known verb matched - the tool then asks which operation,
    #: never guesses one.
    capability_operation: str | None = None
    #: M25 (docs/M25_CREATIVE_3D_SPEC.md §5): for the 3D creation family, the tool
    #: word the owner's WORDS carried ("blender" / "unity"), or None when no tool
    #: word was said at all - the tool then falls back to the CURRENT scene focus's
    #: own tool, and only asks a clarification when neither is known (spec §5's own
    #: rule, "the tool word resolved from the utterance, else the current focus,
    #: else a clarification").
    scene_tool: str | None = None
    #: For the 3D creation family, which scene the owner's WORDS pointed at:
    #: "current" | None. ``None`` means the words named neither and the tool falls
    #: back to its own default ("current") - the same "owner's words win only when
    #: they actually said something" rule ``app_ref``/``artifact_ref`` already follow.
    scene_ref: str | None = None
    #: For SCENE_ADD, the primitive kind word the owner's WORDS carried ("küp" ->
    #: "cube", "küre" -> "sphere", "silindir" -> "cylinder", "düzlem" -> "plane",
    #: "ışık" -> "light_point", "kamera" -> "camera"), or None when no kind word was
    #: said at all - the model still names its own ``kind`` argument, and this is
    #: only a best-effort convenience the tool prefers when non-empty (the same rule
    #: ``app_template``/``artifact_kind`` already follow).
    scene_kind: str | None = None
    #: B44 (req 527): the export format the owner's WORDS named ("glb" / "fbx"), else
    #: None - the tool falls back to GLB.
    scene_format: str | None = None
    #: M26 (docs/M26_EXECUTIVE_AUTONOMY_SPEC.md §5): for EXEC_START, WHICH of the three
    #: directive shapes the owner's WORDS matched ("research" | "folder_compare" |
    #: "mail_thread") — a diagnostic echo of what this resolver decided, never itself
    #: the planner's input (the planner re-derives the shape from the SAME directive
    #: text, app.executive.planner.RuleBasedExecutivePlanner's own docstring: "no
    #: second Turkish table" holds here too - this field is for the audit row and the
    #: tool's own follow-up, not a second source of truth the planner would trust).
    exec_shape: str | None = None
    #: For EXEC_RETRY, the 1-based ordinal the owner's WORDS named ("ikinci adımı" ->
    #: 2), or None when none was said ("Araştırmayı tekrar dene" names a KIND instead,
    #: exec_kind_hint below). The tool resolves the actual step id from the run's own
    #: rows either way - this is only what the words themselves said.
    exec_step_ordinal: int | None = None
    #: For EXEC_RETRY, a coarse step-kind family the owner's WORDS named
    #: ("araştırmayı" -> "research", "excel'i" -> "artifacts"), or None. The tool
    #: matches this against the run's own step kinds (app.executive.spec.STEP_KINDS
    #: all start with one of these family prefixes) - never a guess at a specific step
    #: id from the word alone.
    exec_kind_hint: str | None = None
    #: For EXEC_AMEND, the deliverable kind the owner's WORDS named ("sunumu da ekle"
    #: -> "presentation", "excel'i de hazırla" -> "spreadsheet"), or None when the
    #: words named neither - the tool then asks which, never guesses.
    exec_amend_kind: str | None = None
    #: ADR-0091: for WEATHER_QUERY, the place the owner's WORDS named ("İstanbul'da hava
    #: nasıl?" -> "İstanbul"), resolved against a small built-in Turkish city gazetteer
    #: (``_CITY_STEMS``) — or None when no place was said ("Hava nasıl?"), in which case
    #: ``app.location.service.LocationService.resolve`` decides the place, never this
    #: resolver. Tier 1 of the resolution order (task brief §1) is exactly "a place named
    #: in the request" — this field IS that place.
    weather_place: str | None = None
    #: For LOCATION_DEFAULT_SET, the place the owner's WORDS named ("Varsayılan hava
    #: durumu konumumu İstanbul yap." -> "İstanbul") against the same gazetteer, or None
    #: when the words named none at all — the tool then asks which, never guesses a
    #: default (task brief §1: "Do NOT invent one").
    location_default_city: str | None = None
    #: M26 addendum (docs/M26_LATEST_NEWS_MODE_SPEC.md §6): for NEWS_OPEN/NEWS_SUMMARIZE/
    #: NEWS_QUERY_LATEST, a channel-name HINT the owner's WORDS carried ("Show'un son
    #: haberini aç" -> "show'un"), matched by the tool against configured sources'
    #: display names - or None when the words named no source at all ("Haberleri
    #: aç."), which is not a refusal: the tool falls back to the default configured
    #: source (never a guess at WHICH channel; that identity was already established
    #: when the source was configured, app.news.identity). The same "owner's words
    #: win only when they actually said something" rule ``app_ref``/``scene_ref``
    #: already follow.
    news_source_ref: str | None = None
    #: ADR-0112: for MEDIA_PLAY, the title the owner's WORDS named ("YouTube'dan
    #: 'Doğum günün kutlu olsun Kadir' aç." -> "doğum günün kutlu olsun kadir"), or
    #: None when they named a medium but no title ("müzik aç") - which the tool turns
    #: into a question, never a search for the word "müzik". The model's own ``query``
    #: argument is the fallback for turns the router did not classify, never the
    #: preference: a title said out loud must not be paraphrased.
    media_query: str | None = None
    #: M27 (docs/M27_CREATIVE_TOOLS_SPEC.md §5): for the Creative Tools family, the
    #: tool word the owner's WORDS carried ("paint" / "photoshop" / "illustrator" /
    #: "figma"), or None when no tool word was said at all - the tool then falls back
    #: to the CURRENT creative focus's own tool, else the installed-provider default,
    #: and only asks a clarification when none of those resolve one (spec §5's own
    #: rule, the same shape ``scene_tool`` already documents for M25).
    creative_tool: str | None = None
    #: For the Creative Tools family, which run the owner's WORDS pointed at:
    #: "current" | "previous" | None - the same "owner's words win only when they
    #: actually said something" rule ``scene_ref`` already follows.
    creative_ref: str | None = None
    #: For CREATIVE_EXPORT, the export format word the owner's WORDS carried ("PNG" ->
    #: "png"), or None when none was said - the tool then falls back to "png", the
    #: same best-effort-convenience rule ``scene_kind`` already follows.
    creative_format: str | None = None
    #: B43: the owner's own generation sentence (492) and the application a delivery
    #: should open the file in ("Paint'te göster" -> mspaint).
    creative_prompt: str | None = None
    creative_application: str | None = None
    #: M28 (docs/M28_NATIVE_APP_FACTORY_SPEC.md §6): for the Native App Factory family,
    #: the TARGET the owner's own WORDS named - one of ``app.nativefactory.spec.
    #: NATIVE_TARGETS`` ("EXE" -> "windows_exe", "kurulum" -> "windows_msix", "APK" ->
    #: "android_apk", "Windows"/"masaüstü" -> "windows_exe", "Android" ->
    #: "android_apk") - or None when the words named no target at all ("Çalışıyor mu
    #: kontrol et."). The tool prefers THIS over the model's own ``target`` argument,
    #: the same "owner's words win" rule ``creative_format``/``scene_kind`` already
    #: follow. Spelled here as literals rather than imported from app.nativefactory
    #: (this module's own "no cross-module import for a string literal" convention);
    #: tests/unit/test_voice_native_intents.py reads the OTHER side's source and fails
    #: if the two ever drift.
    native_target: str | None = None
    #: For the Native App Factory family, which build the owner's WORDS pointed at:
    #: "current" | "previous" | None. ``None`` means the words named neither and the
    #: tool falls back to its own default ("current") - the same "owner's words win
    #: only when they actually said something" rule ``creative_ref``/``app_ref``
    #: already follow. Spec §7: "Bunu EXE yap." / "Bunun Android sürümünü yap."
    #: resolve through ids on the build stack, never through fuzzy titles.
    native_ref: str | None = None
    #: B35 (req 622/623): the owner's OWN sentence assigning the system work on itself,
    #: kept whole so the queue row carries what was said and not the model's paraphrase.
    selfdev_request: str | None = None
    #: B39 (req 127-130): the owner's own sentence for the mission planner, and the
    #: mission word (start/approve/pause/resume) the router heard.
    mission_request: str | None = None
    mission_action: str | None = None
    #: B27 req 734: the ONE area the owner asked about ("mail konusunda neler
    #: yapabilirsin?"), as a capability family key, or None for the whole question. The
    #: tool prefers THIS over the model's own ``family`` argument - the owner's words win,
    #: the rule every field above already follows.
    capability_family: str | None = None
    #: B27 req 733: which way the volume goes - "down" | "up" | "mute" - read off the
    #: owner's own verb, never guessed by the model.
    media_volume_direction: str | None = None
    #: B28 req 92/93: the key the owner named ("enter") or the chord ("ctrl+s"), in the
    #: companion's own vocabulary; the tool prefers THIS over the model's argument.
    key_press: str | None = None
    #: B28 req 98: "down" | "up" for a spoken scroll.
    scroll_direction: str | None = None
    #: B31 req 209: the standing answer register the owner asked for
    #: (executive | detail | technical | full).
    answer_level: str | None = None
    #: B31 req 192: the research mode the OWNER'S OWN WORDS carry ("kapsamlı",
    #: "derinlemesine" -> deep; "geniş", "karşılaştırmalı" -> standard; else quick) -
    #: read by research.start so the model's extracted topic cannot drop the word that
    #: chose the mode (owner rule 1: never silently choose DEEP - and never silently lose it).
    research_mode: str | None = None
    #: B32 req 148: the words the owner wants found in a document's text.
    text_query: str | None = None
    #: B29 req 100/102: the button or control the owner NAMED ("Tamam", "belge"), in the
    #: owner's own casing; the tool turns it into a UI Automation query.
    ui_target: str | None = None
    #: B30 req 119/120: the process (application) the owner NAMED, as the allowlist id
    #: when the alias table knows it, else the owner's word.
    process_name: str | None = None
    #: B30 req 121/122: the service the owner NAMED, in the owner's own word ("yazdırma");
    #: the tool maps it to the Windows service name.
    service_name: str | None = None
    #: B51 req 746/747: how a route that the words as heard did NOT reach was reached -
    #: ``polite`` (a "-ır mısın" request read as its imperative), ``ascii_fold`` (a
    #: transcript that lost its Turkish letters matched with them folded) or both joined
    #: by "+". None for every route the words reached directly.
    route_repair: str | None = None

    def __post_init__(self) -> None:
        if not self.klass:
            object.__setattr__(self, "klass", klass_for(self.intent))
        if self.capability is None and self.intent in CAPABILITY_BY_INTENT:
            object.__setattr__(self, "capability", CAPABILITY_BY_INTENT[self.intent])

    def to_dict(self) -> dict[str, Any]:
        return {
            "intent": self.intent.value,
            "klass": self.klass,
            "capability": self.capability,
            "scope": self.scope,
            "target_index": self.target_index,
            "normalized_text": self.normalized_text,
            "fillers_removed": self.fillers_removed,
            "confidence": self.confidence,
            "matched": self.matched,
            "query_kind": self.query_kind,
            "research_class": self.research_class,
            "research_reference": self.research_reference,
            "policy_changes": dict(self.policy_changes) if self.policy_changes else None,
            "alarm_minutes": self.alarm_minutes,
            "evolution_action": self.evolution_action,
            "application": self.application,
            "text_to_type": self.text_to_type,
            "shell_query": self.shell_query,
            "window_ref": self.window_ref,
            "document_ref": self.document_ref,
            "question": self.question,
            "pattern": self.pattern,
            "folder": self.folder,
            "extensions": list(self.extensions) if self.extensions else None,
            "mail_ref": self.mail_ref,
            "calendar_ref": self.calendar_ref,
            "artifact_ref": self.artifact_ref,
            "artifact_kind": self.artifact_kind,
            "artifact_title": self.artifact_title,
            "spoken_numbers": list(self.spoken_numbers) if self.spoken_numbers else None,
            "news_source_ref": self.news_source_ref,
            "media_query": self.media_query,
            "native_target": self.native_target,
            "native_ref": self.native_ref,
            "selfdev_request": self.selfdev_request,
            "mission_request": self.mission_request,
            "mission_action": self.mission_action,
            "capability_family": self.capability_family,
            "media_volume_direction": self.media_volume_direction,
            "key_press": self.key_press,
            "scroll_direction": self.scroll_direction,
            "answer_level": self.answer_level,
            "research_mode": self.research_mode,
            "text_query": self.text_query,
            "ui_target": self.ui_target,
            "process_name": self.process_name,
            "service_name": self.service_name,
            "route_repair": self.route_repair,
        }

    @property
    def research_reference(self) -> str:
        """The reference KIND, for the durable audit row (ADR-0076)."""
        return self.reference.kind if self.reference is not None else RESEARCH_REFERENCE_NONE


# ------------------------------------------------------------- normalisation


def turkish_casefold(text: str) -> str:
    """Casefold that keeps Turkish dotted/dotless i distinct (str.lower maps
    'I' to 'i', which would turn 'ISI' into 'isi' instead of 'ısı').

    B51 req 747: the text is composed (NFC) first - a transcript delivered decomposed
    ("s" + U+0327 for "ş") was cut in two at the combining mark by the punctuation strip -
    and the combining dot a non-Turkish lowercase leaves on "İ" ("İkinci".lower() is
    "i" + U+0307, and so is JavaScript's toLowerCase) is dropped, since it split the word
    the same way."""
    composed = unicodedata.normalize("NFC", text)
    return composed.replace("İ", "i").replace("I", "ı").lower().replace("i\u0307", "i")


#: B51 req 747: what an ASR that drops Turkish letters does to a word. Used only by the
#: fold repair pass (:func:`resolve_intent`), never by the first, exact reading.
_ASR_FOLD_TABLE: Final = str.maketrans(
    {"ı": "i", "ş": "s", "ğ": "g", "ü": "u", "ö": "o", "ç": "c", "â": "a", "î": "i", "û": "u"}
)
#: Set only inside the fold repair pass: ``_has``/``_has_exact`` then compare both sides
#: folded. A context variable, so a concurrent resolution elsewhere is never affected.
_FOLD_MATCHING: ContextVar[bool] = ContextVar("pagentos_intent_fold_matching", default=False)


def asr_fold(word: str) -> str:
    """The word as a transcriber without Turkish letters would spell it."""
    return word.translate(_ASR_FOLD_TABLE)


def is_filler(token: str) -> bool:
    return token in FILLERS or bool(_ELONGATED_FILLER.match(token))


def normalize_transcript(text: str) -> tuple[str, tuple[str, ...], int]:
    """(normalized text, content tokens, fillers removed).

    Runs the tr-TR normaliser so numerals become words ("2." -> "ikinci"),
    casefolds the Turkish way, strips punctuation and drops hesitation fillers.
    """
    if not text or not text.strip():
        return "", (), 0
    spoken = normalize(text.strip())
    lowered = turkish_casefold(spoken)
    cleaned = _PUNCT_RE.sub(" ", lowered)
    raw_tokens = [t.strip("'") for t in cleaned.split()]
    raw_tokens = [t for t in raw_tokens if t]
    tokens = [t for t in raw_tokens if not is_filler(t)]
    return " ".join(tokens), tuple(tokens), len(raw_tokens) - len(tokens)


# ---------------------------------------------------------------- resolution


def _has(tokens: tuple[str, ...], *stems: str) -> str | None:
    """First token that starts with one of ``stems`` (Turkish suffixes vary:
    maddeyi / maddeye / maddeden), or None."""
    if _FOLD_MATCHING.get():
        folded_stems = tuple(asr_fold(stem) for stem in stems)
        for tok in tokens:
            if asr_fold(tok).startswith(folded_stems):
                return tok
        return None
    for tok in tokens:
        for stem in stems:
            if tok == stem or tok.startswith(stem):
                return tok
    return None


def _has_exact(tokens: tuple[str, ...], *words: str) -> str | None:
    if _FOLD_MATCHING.get():
        folded_words = {asr_fold(word) for word in words}
        for tok in tokens:
            if asr_fold(tok) in folded_words:
                return tok
        return None
    for tok in tokens:
        if tok in words:
            return tok
    return None


#: "Read it all": the only phrasings that lift the narration budget (spec: full/read-all).
_FULL_READ_PHRASES: tuple[tuple[str, ...], ...] = (
    ("hepsini", "oku"),
    ("hepsini", "anlat"),
    ("tamamını", "oku"),
    ("tamamını", "anlat"),
    ("bütün", "detay"),
    ("tüm", "detay"),
    ("tümünü", "oku"),
    ("tümünü", "anlat"),
)


def _full_read(tokens: tuple[str, ...]) -> bool:
    return any(all(_has(tokens, stem) for stem in stems) for stems in _FULL_READ_PHRASES)


#: Kinds the intent resolver refuses on a single common word, and the stems that
#: corroborate them.
#:
#: The classifier and this resolver answer different questions, and that difference is the
#: whole reason this table exists. The classifier is deliberately liberal: by the time it
#: runs, the utterance is already known to BE a question about the system, so matching
#: "teknik" or "bugün" alone is correct there. This resolver decides whether an utterance
#: was a question at all, from raw speech that may be about the weather - so "bugün hava
#: güzel" must not become a briefing request, and a bare "teknik anlat" is a move through
#: an open briefing rather than a new one. The duplicate table this replaced encoded these
#: distinctions accidentally, by being narrower; they are stated deliberately now
#: (2026-09-05).
_INTENT_CORROBORATION: dict[str, tuple[str, ...]] = {
    "today": ("yaptı", "yapti", "neler", "oldu"),
    "technical": ("değiş", "degis"),
    "research_detail": ("araştırma", "arastirma", "bulgu"),
    # "durumu anlat" and "durum raporunu oku" must not become a cognitive query on one
    # common noun; a subsystem has to be named.
    "subsystem_status": (
        "araştırma",
        "arastirma",
        "research",
        "tarayıcı",
        "tarayici",
        "chrome",
        "ses",
        "voice",
        "dağıtım",
        "dagitim",
        "release",
        "hafıza",
        "hafiza",
        "bellek",
        "sistem",
        "cihaz",
        "sunucu",
    ),
}


def _explain_kind(tokens: tuple[str, ...], text: str = "") -> str | None:
    """The kind of question about the system's own activity, or None.

    Delegates to ``app.explain.classify``, which is the ONE Turkish normalisation table.
    This module used to keep a second one, and the two drifted: the classifier learned the
    M17 kinds and this list never did, so the server watched the owner ask "Kendi
    sisteminde şu anda ne görüyorsun?" and recorded intent=none with no query kind at all
    (owner M17 run, 2026-09-05). Two tables that must agree will not.

    The import is local because ``classify`` imports THIS module for
    ``normalize_transcript``; a module-level import would be circular.

    ``matched`` matters: ``classify`` answers every input, defaulting to last-activity.
    That default is a reasonable answer to a question and a bad reason to decide something
    WAS a question, so only a real pattern match counts here.
    """
    from app.explain.classify import classify

    query = classify(text or " ".join(tokens))
    if not query.matched:
        return None
    required = _INTENT_CORROBORATION.get(query.kind)
    if required is not None and not any(_has(tokens, stem) for stem in required):
        return None
    return query.kind


#: Exact inflected forms, not stems — "göz" as a startswith-stem would also match
#: "gözlük" (glasses) and "gözlem" (observation), unrelated words that happen to
#: share the root. A privacy-critical trigger is worth the extra explicit forms
#: rather than a prefix match that fires on the wrong noun.
_EYE_WORD_FORMS: Final[tuple[str, ...]] = (
    "göz",
    "gözü",
    "gözünü",
    "gözler",
    "gözlerini",
    # ...and the same words as an ASR that dropped the diacritics renders them (Owner
    # Utterance Corpus, asr_noise source, 2026-09-07). Exact forms still: "goz" is not a
    # prefix of anything, and "gozunu" is this word or nothing.
    "goz",
    "gozu",
    "gozunu",
    "gozler",
    "gozlerini",
)
_CAMERA_WORD_FORMS: Final[tuple[str, ...]] = (
    "kamera",
    "kamerayı",
    "kameramı",
    "kamerasını",
    "kameraları",
    "kamerayi",
    "kamerami",
    "kamerasini",
    "kameralari",
)


def _eye_disable_match(tokens: tuple[str, ...]) -> str | None:
    """``Gözünü kapat`` / ``Kamerayı kapat`` / ``Beni izleme`` (M18 spec §2).

    Uses the SAME token/stem primitives (``_has_exact``) as every other intent
    in this file — there is deliberately no second Turkish pattern table for
    this. The task brief is explicit about why: a second table already
    drifted from this one twice (see ``_explain_kind``'s docstring), and the
    Active Eye's disable phrases are exactly the kind of privacy-critical
    command that must never live somewhere it could silently fall out of
    sync. Exact forms, not ``_has`` stems, on purpose (see the word-form
    comments above).
    """
    if _has_exact(tokens, *_EYE_WORD_FORMS) and _has_exact(tokens, "kapat"):
        return "gözünü kapat"
    if _has_exact(tokens, *_CAMERA_WORD_FORMS) and _has_exact(tokens, "kapat"):
        return "kamerayı kapat"
    if _has_exact(tokens, "beni") and _has_exact(tokens, "izleme"):
        return "beni izleme"
    return None


#: The imperative "open" forms. Exact, like the eye/camera nouns: "açık" (open, adj.) is
#: the QUERY "kamera açık mı?" and must not become an action; "açar mısın" is a request
#: and is honoured as one.
_OPEN_VERB_FORMS: Final[tuple[str, ...]] = ("aç", "açsana", "açar", "ac", "acsana", "acar")
#: "Active Eye'ı aç" — the product name, as the ASR renders it (the apostrophe survives
#: normalisation, so the stem match on "eye" is the honest way to catch "eye'ı"/"eye'i").
_ACTIVE_EYE_FORMS: Final[tuple[str, ...]] = ("active", "aktif")


def _eye_enable_match(tokens: tuple[str, ...]) -> str | None:
    """``Gözünü aç`` / ``Kamerayı aç`` / ``Beni izle`` / ``Beni tekrar izle`` /
    ``Gözünü tekrar aç`` / ``Active Eye'ı aç`` (docs/M18_ACTION_CONTRACT.md §2).

    Built on the same word forms as :func:`_eye_disable_match`, and evaluated AFTER
    it, so "beni izleme" (the negative imperative: do not watch me) stays a disable and
    "beni izle" (watch me) is an enable — the two differ by one suffix and the privacy
    direction must win a tie.
    """
    if _has_exact(tokens, *_EYE_WORD_FORMS) and _has_exact(tokens, *_OPEN_VERB_FORMS):
        return "gözünü aç"
    if _has_exact(tokens, *_CAMERA_WORD_FORMS) and _has_exact(tokens, *_OPEN_VERB_FORMS):
        return "kamerayı aç"
    if (
        _has_exact(tokens, *_ACTIVE_EYE_FORMS)
        and _has(tokens, "eye")
        and _has_exact(tokens, *_OPEN_VERB_FORMS)
    ):
        return "active eye'ı aç"
    if _has_exact(tokens, "beni") and _has_exact(tokens, "izle"):
        return "beni izle"
    return None


#: "Canlıya al." / "Yayına al." as imperatives (contract §2). Exact verb forms: "alabilir"
#: is the question, and the question stays a can_deploy QUERY answered from policy.
_PROMOTE_TARGETS: Final[tuple[str, ...]] = ("canlıya", "canliya", "yayına", "yayina")
_TAKE_VERB_FORMS: Final[tuple[str, ...]] = ("al", "alsana")


#: M18.4 (spec §4). "geliştirme" as a stem: "geliştirmeyi", "geliştirmeye", "geliştirmeleri"
#: are all the noun; "geliştiriyorsun" (a question about what is being built) is NOT, and
#: stays a question. Exact verb forms, as everywhere in this family.
_EVOLUTION_NOUN_STEMS: Final[tuple[str, ...]] = (
    "geliştirme",
    "gelistirme",
    "evrim",
    "özgelişim",
    "ozgelisim",
)
_EVOLUTION_PAUSE_FORMS: Final[tuple[str, ...]] = (
    "duraklat",
    "durdur",
    "kapat",
    "beklet",
    "dondur",
)
_EVOLUTION_RESUME_FORMS: Final[tuple[str, ...]] = (
    "aç",
    "ac",
    "başlat",
    "baslat",
    "sürdür",
    "surdur",
    "devam",
)
_EVOLUTION_CANCEL_STEMS: Final[tuple[str, ...]] = ("iptal", "vazgeç", "vazgec")
_HOLD_NEGATION_FORMS: Final[tuple[str, ...]] = ("alma", "almayın", "almayin", "almayacaksın")
_VERSION_STEMS: Final[tuple[str, ...]] = ("sürüm", "surum", "versiyon")
_PREVIOUS_VERSION_STEMS: Final[tuple[str, ...]] = ("öncek", "oncek", "eski")
_RETURN_VERB_STEMS: Final[tuple[str, ...]] = ("dön", "don", "geri")

EVOLUTION_ACTION_BY_INTENT: Final[dict[Intent, str]] = {
    Intent.EVOLUTION_PAUSE: "pause",
    Intent.EVOLUTION_RESUME: "resume",
    Intent.EVOLUTION_CANCEL: "cancel",
    Intent.EVOLUTION_HOLD: "hold",
}


def _evolution_match(tokens: tuple[str, ...]) -> tuple[Intent, str] | None:
    """The owner's voice over self-evolution (M18.4 spec §4), in priority order: the hold
    ("canlıya alma" - a negation the deploy matcher must never read as "al"), the rollback,
    then the pause / resume / cancel forms that need the evolution noun."""
    if _has_exact(tokens, *_PROMOTE_TARGETS) and _has_exact(tokens, *_HOLD_NEGATION_FORMS):
        return Intent.EVOLUTION_HOLD, "canlıya alma"
    if (
        _has(tokens, *_VERSION_STEMS)
        and _has(tokens, *_PREVIOUS_VERSION_STEMS)
        and _has(tokens, *_RETURN_VERB_STEMS)
    ):
        return Intent.RELEASE_ROLLBACK, "önceki sürüme dön"
    if _has(tokens, *_EVOLUTION_NOUN_STEMS) is None:
        return None
    if _has(tokens, *_EVOLUTION_CANCEL_STEMS):
        return Intent.EVOLUTION_CANCEL, "geliştirmeyi iptal et"
    if _has_exact(tokens, *_EVOLUTION_PAUSE_FORMS):
        return Intent.EVOLUTION_PAUSE, "geliştirmeyi duraklat"
    if _has_exact(tokens, *_EVOLUTION_RESUME_FORMS):
        return Intent.EVOLUTION_RESUME, "geliştirmeyi aç"
    return None


# ------------------------------------------------- B35: the owner assigns the system work on itself
#
# "Şu bug'ı kendin düzelt." / "Şu özelliği kendine ekle." (req 622/623): a self-reference
# ("kendin", "kendine", "sen") beside a defect noun and a fix verb, or beside a feature noun
# and an add verb. Evaluated AFTER the evolution block, because "kendi kendini geliştirmeyi
# ..." carries the same self-reference and is the pause/resume switch, never an assignment
# (the ORDER is the guard: a noun check here was dead code, proven so by a mutation that
# removed it and turned nothing red). Before the memory-correct and explain matchers,
# which claim "düzelt" and "hatayı" for themselves - and keep them when nothing in the
# sentence points at the system itself (tests/unit/test_selfdev_b35.py).

_SELF_REFERENCE_FORMS: Final[tuple[str, ...]] = (
    "kendin",
    "kendine",
    "kendini",
    "kendinde",
    "kendindeki",
    "kendinden",
    "sen",
)
_SELFDEV_DEFECT_NOUN_STEMS: Final[tuple[str, ...]] = (
    "bug",
    "hata",
    "kusur",
    "sorun",
    "arıza",
    "ariza",
)
_SELFDEV_FIX_VERB_STEMS: Final[tuple[str, ...]] = (
    "düzelt",
    "duzelt",
    "çöz",
    "coz",
    "onar",
    "gider",
)
#: "özelliği" / "yeteneği": the k softens to ğ under the accusative, so the stems stop short.
_SELFDEV_FEATURE_NOUN_STEMS: Final[tuple[str, ...]] = ("özelli", "ozelli", "yetene", "fonksiyon")
_SELFDEV_ADD_VERB_STEMS: Final[tuple[str, ...]] = ("ekle", "kazandır", "kazandir", "getir", "koy")
_SELFDEV_QUESTION_FORMS: Final[tuple[str, ...]] = ("ne", "neyi", "neler", "hangi")


def _selfdev_match(tokens: tuple[str, ...]) -> tuple[Intent, str] | None:
    if _has_exact(tokens, *_SELF_REFERENCE_FORMS) is None:
        return None
    defect = _has(tokens, *_SELFDEV_DEFECT_NOUN_STEMS)
    fix = _has(tokens, *_SELFDEV_FIX_VERB_STEMS)
    if defect and fix and not fix.endswith(("yorsun", "yorsunuz", "iyor")):
        return Intent.SELFDEV_FIX, f"{defect} {fix}"
    feature = _has(tokens, *_SELFDEV_FEATURE_NOUN_STEMS)
    add = _has(tokens, *_SELFDEV_ADD_VERB_STEMS)
    if feature and add:
        return Intent.SELFDEV_FEATURE, f"{feature} {add}"
    if _has_exact(tokens, "kendinde") and fix and _has_exact(tokens, *_SELFDEV_QUESTION_FORMS):
        return Intent.SELFDEV_STATUS, f"kendinde {fix}"
    return None


def _deploy_match(tokens: tuple[str, ...]) -> str | None:
    target = _has_exact(tokens, *_PROMOTE_TARGETS)
    if target and _has_exact(tokens, *_TAKE_VERB_FORMS):
        return "yayına al" if target.startswith("yay") else "canlıya al"
    return None


# ------------------------------------------------- M18.3: alarms and the display
#
# Built on the SAME token/stem primitives as every intent above (`_has`, `_has_exact`) —
# there is deliberately no second Turkish pattern table for the alarm and display phrases,
# for the reason `_explain_kind`'s docstring records: two tables that must agree will not.
# The word forms are exact where a stem would over-match: "alarm" as a stem would also
# match "alarmın" (fine) but "aç" as a stem would match "açık" (the QUERY), so the verb
# forms are enumerated.

_ALARM_WORD_FORMS: Final[tuple[str, ...]] = (
    "alarm",
    "alarmı",
    "alarmi",
    "alarmım",
    "alarmim",
    "alarmımı",
    "alarmimi",
    "alarmını",
    "alarmini",
)
#: "uyandır" (wake me) in the forms an owner says it, plus the polite request form.
_WAKE_VERB_STEMS: Final[tuple[str, ...]] = ("uyandır", "uyandir", "kaldır", "kaldir")
#: "kur" (set), "kurar mısın" — a bare "kur" plus an alarm noun is the create imperative.
_SET_VERB_FORMS: Final[tuple[str, ...]] = ("kur", "kursana", "kurar", "kurabilir")
_CANCEL_VERB_STEMS: Final[tuple[str, ...]] = ("iptal", "sil", "kaldır", "kaldir")
#: "kapat" is shared with the eye and the display, so an alarm noun must be present.
_ALARM_STOP_VERB_FORMS: Final[tuple[str, ...]] = (
    "kapat",
    "durdur",
    "sustur",
    "kes",
    "sus",
)
_SNOOZE_VERB_STEMS: Final[tuple[str, ...]] = ("ertele", "erteler")
#: "On dakika sonra tekrar çal." - a snooze said as "ring again later" (corpus a.snooze.2).
#: Exact ring forms: "çalıştır" (run) belongs to the research re-run, never to an alarm.
_RING_VERB_FORMS: Final[tuple[str, ...]] = ("çal", "cal", "çalsın", "calsin", "çalsana", "calsana")
_TEST_WORD_FORMS: Final[tuple[str, ...]] = ("test", "deneme")
_ALARM_ADJUST_VERB_FORMS: Final[tuple[str, ...]] = ("ayarla", "ayarlasana", "ayarlar")
_ALARM_SONG_STEMS: Final[tuple[str, ...]] = ("müzi", "muzi", "şarkı", "sarki", "melodi", "zil")

#: Spoken minutes for a snooze: the tr-TR normaliser has already turned "10" into "on",
#: so these are the words a minute count arrives as (compounds: "on beş", "yirmi").
_MINUTE_WORDS: Final[dict[str, int]] = {
    "bir": 1,
    "iki": 2,
    "üç": 3,
    "uc": 3,
    "dört": 4,
    "dort": 4,
    "beş": 5,
    "bes": 5,
    "altı": 6,
    "alti": 6,
    "yedi": 7,
    "sekiz": 8,
    "dokuz": 9,
    "on": 10,
    "yirmi": 20,
    "otuz": 30,
    "kırk": 40,
    "kirk": 40,
    "elli": 50,
    "altmış": 60,
    "altmis": 60,
    "yetmiş": 70,
    "yetmis": 70,
    "seksen": 80,
    "doksan": 90,
    # No "yüz". The compounder above only joins a round TEN to a unit, so "iki yüz dakika"
    # would read as the bare "yüz" and become a hundred minutes -- a number the owner never
    # said, applied silently. Refusing to parse it is the honest failure; misreading it is
    # not. Tens to ninety are enough for a wait, and ninety-nine is the ceiling this
    # vocabulary can actually express.
}
_MAX_SPOKEN_MINUTES: Final = 180


def _minute_value(token: str) -> int | None:
    if token.isdigit():
        return int(token)
    return _MINUTE_WORDS.get(token)


def spoken_minutes(tokens: tuple[str, ...]) -> int | None:
    """The minute count the owner SAID ("on dakika", "on beş dakika", "5 dk"), or None.

    Derived in the one router and carried on the turn (``ResolvedIntent.alarm_minutes``)
    so the snooze tool applies what was said rather than what the model chose to pass -
    the same rule ADR-0079 §7 made for the ambient policy fields.
    """
    for n, tok in enumerate(tokens):
        if not (tok.startswith("dakika") or tok == "dk"):
            continue
        if n == 0:
            return None
        last = _minute_value(tokens[n - 1])
        if last is None:
            return None
        value = last
        if last < 10 and n >= 2:
            tens = _minute_value(tokens[n - 2])
            if tens is not None and tens >= 10 and tens % 10 == 0:
                value = tens + last
        return value if 1 <= value <= _MAX_SPOKEN_MINUTES else None
    return None


def _snooze_again_match(tokens: tuple[str, ...]) -> str | None:
    """ "On dakika sonra tekrar çal." / "Biraz sonra yeniden çal.": a snooze without the
    verb "ertele". Needs "again" AND a ring verb AND a "later" (minutes or "sonra"), so
    "şarkıyı tekrar çal" (play the song again) stays a repeat."""
    if (
        _has_exact(tokens, *_RERUN_WORDS)
        and _has_exact(tokens, *_RING_VERB_FORMS)
        and (_has_exact(tokens, "sonra") or spoken_minutes(tokens) is not None)
    ):
        return "tekrar çal"
    return None


_SCREEN_WORD_FORMS: Final[tuple[str, ...]] = (
    "ekran",
    "ekranı",
    "ekrani",
    "ekranlar",
    "ekranları",
    "ekranlari",
    "ekranlarını",
    "ekranlarini",
    "monitör",
    "monitor",
    "monitörü",
    "monitoru",
    "monitörleri",
    "monitorleri",
    "monitörler",
    "monitorler",
    # "Görüntüyü kapat." - the owner's word for what the screen shows (corpus d.off.4).
    # Exact forms: "görüntüsünü" belongs to "kamera görüntüsünü", which the eye owns.
    "görüntü",
    "görüntüyü",
    "goruntu",
    "goruntuyu",
)
_CLOSE_VERB_FORMS: Final[tuple[str, ...]] = ("kapat", "kapatsana", "kapatır", "söndür", "sondur")
#: Deliberately NOT the eye's `_OPEN_VERB_FORMS`: this is a display, and reusing that tuple
#: would couple two privacy-unrelated vocabularies through one edit.
_SCREEN_OPEN_VERB_FORMS: Final[tuple[str, ...]] = (
    "aç",
    "açsana",
    "açar",
    "ac",
    "acsana",
    "acar",
    "uyandır",
    "uyandir",
)


def _alarm_noun(tokens: tuple[str, ...]) -> str | None:
    return _has_exact(tokens, *_ALARM_WORD_FORMS)


def _screen_noun(tokens: tuple[str, ...]) -> str | None:
    return _has_exact(tokens, *_SCREEN_WORD_FORMS)


def _is_question(tokens: tuple[str, ...]) -> bool:
    """ "kaçta", "ne zaman", "var mı", "açık mı" — the shapes that make an alarm/display
    utterance a QUERY rather than a command."""
    return bool(
        _has(tokens, "kaçta", "kacta", "kaçtı", "kacti")
        or _has_exact(tokens, "mı", "mi", "mu", "mü")
        or (_has_exact(tokens, "ne") and _has(tokens, "zaman"))
        # "Ekran durumu ne?" / "Alarm durumu nedir?" (corpus d.status.2).
        or (_has(tokens, "durum") and _has_exact(tokens, "ne", "nedir", "nasıl", "nasil"))
    )


# ---------------------------------------------------------------- B14: the routines
#
# The owner's own routines, by voice (req 287-291, 296-299). Resolved BEFORE the alarm
# family for one concrete reason: "sabah rutinini durdur" and "sabah rutinini iptal et"
# carry the alarm family's own stop and cancel verbs. The NOUN is what tells them apart, so
# the noun has to be looked at first - otherwise the owner turning off a morning routine
# silences tomorrow's alarm instead, and finds out by oversleeping.
#
# Every phrase below requires the routine noun. There is deliberately no bare-verb
# fallback: "durdur" alone is the narration stop, and a routine family that claimed it
# would take a word the owner uses constantly.

#: "rutin" and its suffixed forms. Turkish agglutination means the stem match is the right
#: primitive here (rutini, rutinimi, rutinlerim, rutinini, rutinlerimi ...).
_ROUTINE_NOUN_STEMS: Final[tuple[str, ...]] = ("rutin",)

#: Setting one up. `kur` covers kur/kurar/kursana; `ayarla` the other common phrasing.
_ROUTINE_CREATE_VERB_STEMS: Final[tuple[str, ...]] = ("kur", "ayarla", "oluştur", "olustur")

#: Turning one off for a while. `durdur`/`duraklat` are the owner's words; `beklet` too.
_ROUTINE_PAUSE_VERB_STEMS: Final[tuple[str, ...]] = ("durdur", "duraklat", "beklet")

#: Turning it back on. "geri aç", "tekrar başlat", "devam ettir".
_ROUTINE_RESUME_VERB_STEMS: Final[tuple[str, ...]] = ("başlat", "baslat", "sürdür", "surdur")
_ROUTINE_RESUME_PARTICLES: Final[tuple[str, ...]] = ("geri", "tekrar", "yeniden", "devam")
_ROUTINE_OPEN_VERB_FORMS: Final[tuple[str, ...]] = ("aç", "ac", "açar", "acar", "açsana")


#: What makes a routine sentence a QUESTION. Wider than the shared ``_is_question`` on
#: purpose: "Hangi rutinlerim var?" and "Rutinlerim neler?" carry no interrogative particle
#: at all, and the shared helper is tuned for the alarm/display families, where widening it
#: would change what "var" means for sentences this batch never looked at.
_ROUTINE_QUESTION_MARKERS: Final[tuple[str, ...]] = (
    "hangi",
    "neler",
    "nedir",
    "var",
    "kaç",
    "kac",
)
#: And the imperatives that ask for the same answer.
_ROUTINE_LIST_VERB_STEMS: Final[tuple[str, ...]] = (
    "listele",
    "say",
    "göster",
    "goster",
    # B51 (746): "Rutinlerimi söyle / söyler misin?".
    "söyle",
    "soyle",
)


def _routine_noun(tokens: tuple[str, ...]) -> str | None:
    return _has(tokens, *_ROUTINE_NOUN_STEMS)


def _routine_match(tokens: tuple[str, ...]) -> tuple[Intent, str] | None:
    """The routine family, in priority order: QUERY, then PAUSE/RESUME, then CANCEL, then
    CREATE.

    The order matters the same way the alarm family's does, and for the same reason: the
    words overlap and the consequences are asymmetric. "Hangi rutinlerim var?" must never
    mutate anything, so the question shape is checked first. PAUSE before CANCEL because
    "durdur" and "iptal" are different requests and reading a pause as a cancellation
    throws away the routine's id and its history - a loss the owner cannot undo by saying
    the sentence again.
    """
    noun = _routine_noun(tokens)
    if noun is None:
        return None

    # "Hangi rutinlerim var?" / "Rutinlerim neler?" / "Rutinlerimi say." / "Kaç rutinim var?"
    if (
        _is_question(tokens)
        or _has_exact(tokens, *_ROUTINE_QUESTION_MARKERS)
        or _has(tokens, *_ROUTINE_LIST_VERB_STEMS)
    ):
        return Intent.ROUTINE_LIST, noun

    # RESUME before PAUSE: "geri aç" and "tekrar başlat" carry no pause verb, but
    # "duraklatılmış rutini başlat" carries both, and the imperative is the resume.
    if _has_exact(tokens, *_ROUTINE_RESUME_PARTICLES) and (
        _has(tokens, *_ROUTINE_RESUME_VERB_STEMS) or _has_exact(tokens, *_ROUTINE_OPEN_VERB_FORMS)
    ):
        return Intent.ROUTINE_RESUME, noun
    if _has(tokens, *_ROUTINE_RESUME_VERB_STEMS):
        return Intent.ROUTINE_RESUME, noun

    if _has(tokens, *_ROUTINE_PAUSE_VERB_STEMS):
        return Intent.ROUTINE_PAUSE, noun

    if _has(tokens, *_CANCEL_VERB_STEMS):
        return Intent.ROUTINE_CANCEL, noun

    if _has(tokens, *_ROUTINE_CREATE_VERB_STEMS):
        return Intent.ROUTINE_CREATE, noun

    return None


#: B15 req 271: the words that ask what time or what day it is. Nouns only - the question
#: shape is checked separately, because "saat yedide uyandır" is an alarm and carries the
#: same noun.
_CLOCK_NOUN_FORMS: Final[tuple[str, ...]] = ("saat", "saati", "tarih", "tarihi")
_DAY_QUESTION_FORMS: Final[tuple[str, ...]] = ("günlerden", "gunlerden")
#: The clock family's own question words. Wider than the shared ``_is_question``, which is
#: tuned for the alarm/display families: "Saat kaç?" carries no interrogative particle and
#: no "kaçta", and widening the shared helper would change what those families claim.
_CLOCK_QUESTION_FORMS: Final[tuple[str, ...]] = ("kaç", "kac", "kaçtır", "kactir", "ne", "nedir")


def _clock_match(tokens: tuple[str, ...]) -> tuple[Intent, str] | None:
    """ "Saat kaç?" / "Bugün günlerden ne?" — the clock, asked directly.

    Evaluated AFTER the alarm and display families, which is the whole reason this can be
    as simple as it is: "Sabah alarmım kaçta?" carries the alarm noun and is claimed there,
    "saat yedide uyandır" carries the wake verb and is claimed there. What reaches this
    point is a bare question about the time, and nothing else asks one.
    """
    # B40 (req 422): an application request that lists its fields ("... sipariş tutarı,
    # tarih, ödendi mi") carries a clock noun and a question particle without asking the
    # time; the create shape is the stronger signal and is checked here, not later.
    if _appfactory_create_match(tokens) is not None:
        return None
    if _has_exact(tokens, *_DAY_QUESTION_FORMS):
        return Intent.CLOCK_QUERY, "günlerden"
    if _has_exact(tokens, *_CLOCK_NOUN_FORMS) and (
        _is_question(tokens) or _has_exact(tokens, *_CLOCK_QUESTION_FORMS)
    ):
        return Intent.CLOCK_QUERY, "saat"
    return None


# ----------------------------------------------------------------- B16: the memory
#
# `app.memory` has been complete since M5 and no sentence the owner could say reached it.
# The family is resolved AFTER the alarm, display, routine and clock families and declines
# outright when one of their nouns is present, because it shares verbs with none of them
# but shares OBJECTS with all of them: "alarmı unut" is an alarm the owner wants cancelled,
# not a memory row.

#: FORGET. Exact forms and never `_has`, which is a PREFIX match: "unutma" is Turkish for
#: "don't forget", i.e. the owner's strongest REMEMBER phrase, and `app.memory.policy`
#: already lists it as one. A stem match on "unut" would read "bunu unutma" as a hard
#: delete - and `memory.forget` is a hard delete: the row, its versions, its evidence and
#: its embeddings, with no undo. The one place in this file where the difference between
#: `_has` and `_has_exact` is the difference between remembering and destroying.
_MEMORY_FORGET_FORMS: Final[tuple[str, ...]] = (
    "unut",
    "unutabilirsin",
    "unutalım",
    "unutalim",
    "unutun",
)
#: REMEMBER, including the negative imperative the forget forms deliberately exclude.
_MEMORY_REMEMBER_STEMS: Final[tuple[str, ...]] = ("hatırla", "hatirla", "unutma", "kaydet")
#: "Bunu hatırlıyor musun?" / "Kahve hakkında ne biliyorsun?" - a question about what is
#: already there. Distinct stems from REMEMBER: "hatırlıyor" does not start with "hatırla".
_MEMORY_RECALL_STEMS: Final[tuple[str, ...]] = (
    "hatırlıyor",
    "hatirliyor",
    "biliyor",
    "biliyorsun",
)
_MEMORY_WHY_FORMS: Final[tuple[str, ...]] = ("neden", "niye", "niçin", "nicin", "nereden")
_MEMORY_PIN_STEMS: Final[tuple[str, ...]] = ("sabitle", "sabit")
_MEMORY_CORRECT_STEMS: Final[tuple[str, ...]] = ("düzelt", "duzelt")
#: The noun, for the sentences that name it: "hafızandan sil", "kaydı göster".
_MEMORY_NOUN_STEMS: Final[tuple[str, ...]] = ("hafıza", "hafiza", "bellek", "bellegin")


def _memory_match(tokens: tuple[str, ...]) -> tuple[Intent, str] | None:
    """The memory family, in priority order: WHY, RECALL, FORGET, PIN, CORRECT, REMEMBER.

    REMEMBER is LAST and it is the widest, which is the right way round: a memory written
    by mistake can be forgotten, and a memory forgotten by mistake cannot be recovered.
    Every ambiguity in this family therefore resolves away from the destructive reading.

    WHY before RECALL because "Bunu neden hatırlıyorsun?" carries the recall verb and is a
    question about the memory rather than for it. RECALL before FORGET because "bunu
    hatırlıyor musun" is a question, and a question must never mutate anything.
    """
    # Another family's object. This one declines rather than competing: those families are
    # resolved first anyway, and being explicit here is what keeps a later reordering from
    # silently turning "alarmı unut" into a memory deletion.
    if _alarm_noun(tokens) or _routine_noun(tokens) or _screen_noun(tokens):
        return None

    if (why := _has_exact(tokens, *_MEMORY_WHY_FORMS)) and _has(tokens, *_MEMORY_RECALL_STEMS):
        return Intent.MEMORY_WHY, why
    if recall := _has(tokens, *_MEMORY_RECALL_STEMS):
        return Intent.MEMORY_SEARCH, recall
    if forget := _has_exact(tokens, *_MEMORY_FORGET_FORMS):
        return Intent.MEMORY_FORGET, forget
    if pin := _has(tokens, *_MEMORY_PIN_STEMS):
        return Intent.MEMORY_PIN, pin
    if correct := _has(tokens, *_MEMORY_CORRECT_STEMS):
        return Intent.MEMORY_CORRECT, correct
    if remember := _has(tokens, *_MEMORY_REMEMBER_STEMS):
        return Intent.MEMORY_REMEMBER, remember
    # "Bunu aklında tut." - two tokens, and neither means anything on its own.
    if _has(tokens, "aklında", "aklinda") and _has(tokens, "tut"):
        return Intent.MEMORY_REMEMBER, "aklında tut"
    # "Hafızandan sil." - the noun makes the shared cancel verb unambiguous.
    if (noun := _has(tokens, *_MEMORY_NOUN_STEMS)) and _has(tokens, *_CANCEL_VERB_STEMS):
        return Intent.MEMORY_FORGET, noun
    return None


def _alarm_match(
    tokens: tuple[str, ...], *, alarm_ringing: bool = False, event_focused: bool = False
) -> tuple[Intent, str] | None:
    """The alarm family (spec §6's phrase list), in priority order.

    ``alarm_ringing`` is the one piece of live context this family takes: while an alarm
    is actually ringing, a bare "Sustur." / "Kapat." / "Kes şunu." is about the alarm -
    the owner is talking to the thing that just woke them, and the noun is the loudest
    thing in the room (Owner Utterance Corpus a.stop.4-6, 2026-09-07). With no alarm
    ringing the same words keep every meaning they had.

    STOP before CANCEL before CREATE, because the words overlap and the physical
    consequence of getting it wrong is asymmetric: "alarmı kapat" while it is ringing must
    silence it, and mistaking that for "cancel tomorrow's alarm" would leave the owner
    listening to it. The QUERY check runs first for the same reason in reverse — "sabah
    alarmım kaçta?" must never mutate anything.
    """
    # "Beş dakika ertele." names no alarm at all — the owner is talking to the thing that
    # just woke them, and requiring the noun would leave that sentence unresolved at
    # exactly the moment they are least able to rephrase it. "ertele" means nothing else —
    # EXCEPT the one new meaning M21 gives it (spec §3: "Bunu bir saat ertele" reschedules
    # the focused calendar EVENT), and only when there is no alarm noun, no alarm actually
    # ringing, and a calendar event genuinely focused right now: a real alarm keeps every
    # priority it already had, in every existing case (``event_focused`` is False unless
    # this milestone's own focus kind was set, which no pre-M21 corpus case ever does).
    if _has(tokens, *_SNOOZE_VERB_STEMS):
        if (
            event_focused
            and not alarm_ringing
            and _alarm_noun(tokens) is None
            and _screen_noun(tokens) is None
        ):
            return None
        return Intent.ALARM_SNOOZE, "ertele"
    if again := _snooze_again_match(tokens):
        return Intent.ALARM_SNOOZE, again

    noun = _alarm_noun(tokens)
    wake = _has(tokens, *_WAKE_VERB_STEMS)
    # "Ekranı uyandır." wakes the DISPLAY: a screen noun with the wake verb and no alarm
    # noun is never an alarm (corpus d.wake.3 misrouted here to alarm.create).
    if noun is None and wake and _screen_noun(tokens) is not None:
        return None
    if (
        noun is None
        and alarm_ringing
        and _screen_noun(tokens) is None
        and _has_exact(tokens, *_ALARM_STOP_VERB_FORMS)
    ):
        return Intent.ALARM_STOP, "sustur"
    if noun is None and not wake:
        return None

    if noun is not None and _is_question(tokens) and not _has_exact(tokens, *_SET_VERB_FORMS):
        return Intent.ALARM_QUERY, noun

    if noun is not None and _has_exact(tokens, *_ALARM_STOP_VERB_FORMS):
        return Intent.ALARM_STOP, "alarmı kapat"

    if noun is not None and _has(tokens, *_CANCEL_VERB_STEMS):
        return Intent.ALARM_CANCEL, "alarmı iptal et"

    creating = (
        _has_exact(tokens, *_SET_VERB_FORMS)
        or bool(wake)
        # B51 (746): "Test alarmı ayarla." - "ayarla" is every family's "set", so it
        # counts only beside the alarm noun (checked above) and never beside the song
        # the wake-song sentences name ("Alarm müziğimi ayarla" sets no alarm).
        or (
            _has_exact(tokens, *_ALARM_ADJUST_VERB_FORMS)
            and _has(tokens, *_ALARM_SONG_STEMS) is None
        )
    )
    if creating:
        if _has_exact(tokens, *_TEST_WORD_FORMS):
            return Intent.ALARM_TEST_CREATE, "test alarmı kur"
        return Intent.ALARM_CREATE, noun or wake or "uyandır"
    return None


#: ADR-0079 §7: the negations that turn a standing preference OFF ("... kapatma").
_POLICY_NEGATION_FORMS: Final[tuple[str, ...]] = (
    "kapatma",
    "kapama",
    "kapatmayın",
    "kapatmayin",
    "kapatmasın",
    "kapatmasin",
)
_WAKE_NEGATION_FORMS: Final[tuple[str, ...]] = ("açma", "acma", "açmayın", "acmayin")
_KEEP_VERB_FORMS: Final[tuple[str, ...]] = (
    "tut",
    "tutsana",
    "tutar",
    "tutma",
    "tutmayın",
    "tutmayin",
)
_KEEP_NEGATION_FORMS: Final[tuple[str, ...]] = ("tutma", "tutmayın", "tutmayin")
_ASLEEP_STEMS: Final[tuple[str, ...]] = ("uyurken", "uyuyorken", "uyudu", "uyuyunca")
_AWAY_FORMS: Final[tuple[str, ...]] = ("yokken", "olmadığımda", "olmadigimda", "yokum")
_RETURN_STEMS: Final[tuple[str, ...]] = ("geldiğimde", "geldigimde", "döndüğümde", "dondugumde")
_AUTO_ON_FORMS: Final[tuple[str, ...]] = (
    "aç",
    "açsana",
    "ac",
    "başlat",
    "baslat",
    "etkinleştir",
    "etkinlestir",
)
_AUTO_OFF_FORMS: Final[tuple[str, ...]] = ("kapat", "kapatsana", "kapa", "durdur")


def ambient_policy_changes(tokens: tuple[str, ...]) -> dict[str, bool | int] | None:
    """The policy fields the owner's WORDS set (ADR-0079 §7), or None.

    Deterministic and in the one router, so "Uyuduğumda ekranları kapatma." cannot be
    recorded as a request to darken anything: the negation is read here, not trusted to
    the model's choice of booleans. Enabling a half ("uyurken kapat", "yokken kapat")
    also enables the automation, because that is what the sentence asks for; disabling
    a half leaves the switch alone. "Ekranı açık tut." is its own preference and
    outranks the rest (``keep_on``); "açık tutma" lifts it.
    """
    changes: dict[str, bool | int] = {}
    if (
        _screen_noun(tokens) is not None
        and _has_exact(tokens, *_KEEP_VERB_FORMS)
        and _has_exact(tokens, "açık", "acik")
    ):
        return {"keep_on": not bool(_has_exact(tokens, *_KEEP_NEGATION_FORMS))}
    negated = bool(_has_exact(tokens, *_POLICY_NEGATION_FORMS))
    if _has(tokens, *_ASLEEP_STEMS):
        changes["off_when_asleep"] = not negated
        if not negated:
            changes["auto_off_enabled"] = True
    if _has_exact(tokens, *_AWAY_FORMS):
        changes["off_when_away"] = not negated
        if not negated:
            changes["auto_off_enabled"] = True
    if _has(tokens, *_RETURN_STEMS):
        changes["wake_on_return"] = not bool(_has_exact(tokens, *_WAKE_NEGATION_FORMS))
    # B26 req 738: the same narrowing on the half that WRITES the policy. `_ambient_policy_
    # match` is the only caller today, but a reader that flips `auto_off_enabled` for any
    # sentence containing "otomatik" is one call site away from the defect again.
    if _has(tokens, "otomatik") and _screen_noun(tokens) is not None and not changes:
        if _has_exact(tokens, *_AUTO_ON_FORMS):
            changes["auto_off_enabled"] = True
        elif _has_exact(tokens, *_AUTO_OFF_FORMS) or _has(tokens, "devre"):
            changes["auto_off_enabled"] = False
    # The WAITS, not just the switches. ``away_after_s`` and ``asleep_after_s`` have been
    # editable at the service layer all along (``ambient.service._EDITABLE_FIELDS``); no
    # sentence could reach them, because this function returned booleans and the reader
    # dropped anything that was not one. The owner's example was the plain case: the
    # screens go dark after fifteen minutes away and they want five.
    if not negated:
        # No second ceiling: ``spoken_minutes`` already refuses anything outside
        # 1.._MAX_SPOKEN_MINUTES, and a bound restated here could only ever drift from it.
        minutes = spoken_minutes(tokens)
        if minutes is not None:
            field = "asleep_after_s" if _has(tokens, *_ASLEEP_STEMS) else "away_after_s"
            changes[field] = minutes * 60
    return changes or None


def _ambient_explain_match(tokens: tuple[str, ...]) -> str | None:
    """ "Ekranları neden kapattın?", "Neden açık bıraktın?", "Şu an ekran politikası ne?"
    (ADR-0079 §12). "neden" alone is not enough - "Neden önemli?" is a research
    follow-up - so the question must name the screen, the leaving-on, or the policy."""
    why = _has_exact(tokens, "neden", "niye", "niçin", "nicin")
    if why and (_screen_noun(tokens) or _has(tokens, "bırak", "birak")):
        return why
    if _screen_noun(tokens) and _has(tokens, "politika"):
        return "politika"
    if (
        _screen_noun(tokens)
        and _has(tokens, "otomasyon", "yönetim", "yonetim")
        and _is_question(tokens)
    ):
        return "otomasyon"
    return None


def _ambient_policy_match(tokens: tuple[str, ...]) -> tuple[Intent, str] | None:
    """The policy phrases of spec §6. Checked BEFORE the bare display commands, because
    "uyurken ekranları kapat" is a standing preference and "ekranları kapat" is a command
    for right now — one word apart, and the difference is whether the screens go dark in
    two seconds or in twenty minutes."""
    if explained := _ambient_explain_match(tokens):
        return Intent.AMBIENT_EXPLAIN, explained
    # B26 req 738 — the most dangerous misroute the audit measured. The gate used to admit
    # a sentence on the bare word "otomatik" with no screen anywhere in it, so "Otomatik
    # güncellemeleri kapat." (software updates), "Otomatik yedeklemeyi kapat." (backups)
    # and "Otomatik kaydetmeyi kapat." (autosave) all turned the SCREEN automation off.
    # This family is about screens; the screen has to be in the sentence. Nothing else is
    # lost, because "otomatik" alone was never this family's own phrase — its own phrase
    # is "ekranları otomatik kapatmayı aç", which still has a screen in it.
    if _screen_noun(tokens) is None:
        return None
    if _has(tokens, "test") and _has(tokens, "ekran"):
        return Intent.AMBIENT_TEST_DISPLAY, "ekran testi"
    if _has_exact(tokens, *_KEEP_VERB_FORMS) and _has_exact(tokens, "açık", "acik"):
        return Intent.AMBIENT_POLICY_SET, "açık tut"
    # "Ekran kapanma SÜRESİNİ 5 dakika yap." A duration alone is not enough to come here:
    # "Ekranları 5 dakika sonra kapat." is a command for later, and this function's own
    # docstring is about exactly that distance -- two seconds versus twenty minutes. The
    # word the owner uses for the threshold is what anchors it.
    if _has(tokens, "süre", "sure") and spoken_minutes(tokens) is not None:
        return Intent.AMBIENT_POLICY_SET, "süre"
    if _has(tokens, *_ASLEEP_STEMS):
        return Intent.AMBIENT_POLICY_SET, "uyurken"
    if _has_exact(tokens, *_AWAY_FORMS):
        return Intent.AMBIENT_POLICY_SET, "yokken"
    if _has(tokens, "otomatik"):
        return Intent.AMBIENT_POLICY_SET, "otomatik"
    if _has(tokens, "geldiğimde", "geldigimde", "döndüğümde", "dondugumde"):
        return Intent.AMBIENT_POLICY_SET, "geri geldiğimde"
    return None


def _display_match(tokens: tuple[str, ...]) -> tuple[Intent, str] | None:
    noun = _screen_noun(tokens)
    if noun is None:
        return None
    if _is_question(tokens):
        return Intent.DISPLAY_QUERY, noun
    if _has_exact(tokens, *_CLOSE_VERB_FORMS):
        return Intent.DISPLAY_OFF, "ekranları kapat"
    if _has_exact(tokens, *_SCREEN_OPEN_VERB_FORMS):
        return Intent.DISPLAY_WAKE, "ekranları aç"
    return None


# ------------------------------------------------------- M19: the Digital Operator
#
# Built on the SAME token/stem primitives as every intent above - there is deliberately no
# second Turkish pattern table for these either. The app-name alias table lives in
# app.operator.plans (a domain fact, not a routing table) and is imported lazily, the same
# way _explain_kind imports app.explain.classify: app.operator does not import this module,
# so there is no cycle, but a lazy import keeps this module's own load path free of a
# domain package it does not otherwise need.

_WINDOW_NOUN_STEMS: Final[tuple[str, ...]] = ("pencere",)
_FRONT_WINDOW_STEMS: Final[tuple[str, ...]] = ("önde", "onde")  # "öndeki pencere"
_MAXIMIZE_VERB_STEMS: Final[tuple[str, ...]] = ("büyüt", "buyut")
_MINIMIZE_VERB_STEMS: Final[tuple[str, ...]] = ("küçült", "kuçult", "kucult")
_RESTORE_HAL_STEMS: Final[tuple[str, ...]] = ("eski",)
_RESTORE_YUKLE_STEMS: Final[tuple[str, ...]] = ("yükle", "yukle")


# ------------------------------------------------- B39: operator missions (127-130)
#
# A mission is what the owner asks for in one breath and cannot be one plan: two or
# more operator/browser parts joined by "ve"/"sonra", or one part no fixed plan
# serves (a Settings page, an Explorer folder, an editor's file, an Office document).
# The planner itself (app.operator.mission.plan_mission) decides - the router asks it
# and keeps single simple steps with the tools that already own them, so "Not
# Defteri'ni aç" is APP_OPEN as it always was. The three control words are gated on
# ``mission_state`` (the caller's one live fact), never on vocabulary alone.

_MISSION_SIMPLE_KINDS: Final[tuple[str, ...]] = (
    "app_open",
    "type_text",
    "ui_invoke",
    "window_close",
    # "navigate" left this tuple on 2026-09-18: a page is opened in the OWNER'S OWN Chrome
    # by the mission's keyboard rung (owner decision), which no single tool does - so
    # "YouTube'u aç" is a mission, not the media player's.
)
_MISSION_APPROVE_FORMS: Final[tuple[str, ...]] = (
    "evet",
    "başla",
    "basla",
    "başlayabilirsin",
    "baslayabilirsin",
    "onaylıyorum",
    "onayliyorum",
    "onayla",
    "yap",
    "tamam",
    "olur",
)
_MISSION_PAUSE_FORMS: Final[tuple[str, ...]] = ("bekle", "duraklat", "beklet", "ara ver")
_MISSION_RESUME_FORMS: Final[tuple[str, ...]] = ("devam", "sürdür", "surdur")


def _mission_start_match(tokens: tuple[str, ...], text: str) -> str | None:
    """The planner says whether this sentence is a mission and what its first step is
    called; a single simple step (one the operator's own tools already serve) is not
    a mission, so nothing is stolen from APP_OPEN / TYPE_TEXT / UI_INVOKE / WINDOW_CLOSE."""
    if len(tokens) < 2:
        return None
    # "Haberleri YouTube'dan aç" names a site the planner would open; it is the news
    # tool's (Latest News Mode), checked here so the planner never sees it (2026-09-18).
    if _news_noun(tokens) is not None:
        return None
    from app.operator.mission import MissionClarificationNeeded, plan_mission

    try:
        mission = plan_mission(text)
    except MissionClarificationNeeded as exc:
        # A planner that recognised the request and is missing one detail keeps the route:
        # the mission tool asks its question. Anything else is not this router's sentence.
        return exc.label or None
    # What the OWNER asked for: a step the planner added to prepare another (the browser
    # brought to front before a page) is not a second request.
    asked = [s for s in mission.steps if not s.args.get("implicit")]
    # A search typed into the owner's browser is not something a single operator tool
    # does (browser in front, the words typed, Enter), so it is a mission on its own.
    if (
        len(asked) >= 2
        or any(s.kind not in _MISSION_SIMPLE_KINDS for s in asked)
        or any(s.args.get("search") for s in asked)
    ):
        return mission.steps[0].label_tr
    return None


def _mission_approve_match(tokens: tuple[str, ...]) -> str | None:
    """ "Evet, başla." / "Onaylıyorum." while the plan waits (req 130) - gated."""
    if len(tokens) > 4:
        return None
    return _has_exact(tokens, *_MISSION_APPROVE_FORMS)


def _mission_pause_match(tokens: tuple[str, ...]) -> str | None:
    if len(tokens) > 4:
        return None
    if _has_exact(tokens, "bekle", "beklet", "duraklat"):
        return _has_exact(tokens, "bekle", "beklet", "duraklat")
    if "ara ver" in " ".join(tokens):
        return "ara ver"
    return None


def _mission_resume_match(tokens: tuple[str, ...]) -> str | None:
    if len(tokens) > 4:
        return None
    return _has_exact(tokens, *_MISSION_RESUME_FORMS)


def _operator_cancel_match(tokens: tuple[str, ...]) -> str | None:
    """ "Dur." / "İptal et." while a task is running (spec §3) - gated by the caller on
    ``operator_running``, never on vocabulary alone: these words mean plenty else too."""
    if tok := _stop_match(" ".join(tokens), tokens):
        return tok
    if tok := _has(tokens, "iptal"):
        return tok
    return None


def _operator_status_match(tokens: tuple[str, ...]) -> str | None:
    """ "Ne yapıyorsun?" while a task is running (spec §3)."""
    if _has_exact(tokens, "ne") and _has(tokens, "yapıyor", "yapiyor"):
        return "ne yapıyorsun"
    return None


def _shell_query_match(tokens: tuple[str, ...]) -> tuple[str, str] | None:
    """ "IP adresimi göster" / "IP adresim ne?" -> ("ip", ...); "Bilgisayarın adı ne?" ->
    ("hostname", ...) (spec §2's ``terminal.execute`` ``hostname``/``ipconfig``).

    ``ıp`` alongside ``ip``: Turkish casefolding maps a plain ASCII "I" to the dotless
    "ı" (``turkish_casefold``'s whole reason for existing - "ISI" must not become "isi"),
    so the ASCII acronym "IP" written with a capital Latin I casefolds to "ıp", not "ip".
    """
    if _has_exact(tokens, "ip", "ıp"):
        return "ip", "ip adresi"
    if _has(tokens, "bilgisayar") and _has(tokens, "ad") and _has_exact(tokens, "ne", "nedir"):
        return "hostname", "bilgisayarın adı"
    # B30 req 118: "Kullanıcı adım ne?" / "Hangi kullanıcıyla oturum açtım?" -> whoami.
    if _has(tokens, "kullanıcı", "kullanici") and (
        (_has(tokens, "ad") and _has_exact(tokens, "ne", "nedir"))
        or _has(tokens, "oturum")
        or _has_exact(tokens, "hangi", "kim")
    ):
        return "whoami", "kullanıcı adı"
    return None


def _window_control_match(tokens: tuple[str, ...]) -> tuple[Intent, str, str | None] | None:
    """The window-control family (spec §2's ``window.*``), in priority order: "önceki
    pencereye dön" first (it also carries the window noun the other branches key on), then
    close/maximize/minimize/restore. Fires on the window noun OR a deictic/front-of-screen
    pointer ("bunu", "öndeki") so "Bunu kapat" resolves without naming "pencere" at all -
    but only ever alongside one of this family's own verbs, so it can never shadow the
    eye/alarm/display "kapat" phrases already checked earlier in ``resolve_intent``."""
    noun = _has(tokens, *_WINDOW_NOUN_STEMS)
    pointer = noun or _has_exact(tokens, *_DEICTIC_WORDS) or _has(tokens, *_FRONT_WINDOW_STEMS)
    if pointer is None:
        return None
    if (
        noun is not None
        and _has(tokens, *_PREVIOUS_STEMS)
        and _has_exact(tokens, "dön", "don", "geç", "gec")
    ):
        return Intent.WINDOW_PREVIOUS, "önceki pencereye dön", "previous"
    if _has_exact(tokens, *_CLOSE_VERB_FORMS):
        return Intent.WINDOW_CLOSE, pointer, "current"
    if _has(tokens, *_MAXIMIZE_VERB_STEMS):
        return Intent.WINDOW_MAXIMIZE, pointer, "current"
    if _has(tokens, *_MINIMIZE_VERB_STEMS):
        return Intent.WINDOW_MINIMIZE, pointer, "current"
    if _has(tokens, *_RESTORE_HAL_STEMS) and _has(tokens, "hal"):
        return Intent.WINDOW_RESTORE, pointer, "current"
    if _has(tokens, *_RESTORE_YUKLE_STEMS):
        return Intent.WINDOW_RESTORE, pointer, "current"
    return None


#: "yaz" as a stem also matches "yazı"/"yazılım" - deliberately, the same way "gözlük"
#: sharing "göz" is accepted for eye/camera (module docstring): every Turkish word that
#: starts with "yaz" is at least plausibly about writing, and this only fires alongside a
#: target phrase or a deictic pointer anyway.
_WRITE_VERB_STEMS: Final[tuple[str, ...]] = ("yaz",)
#: B26 req 737. Turkish builds words by suffix, and `yaz` (write) is the first three letters
#: of several words that are not the verb at all. The causative `yazdır` is PRINT — "Bunu
#: yazdır." asks for paper — and a prefix match on the verb turned it into typing whatever
#: was said into whatever window happened to be focused, which the audit measured.
#: A closed list of the non-verbs rather than a cleverer stemmer: the words are few, they
#: are known, and a rule that "guesses" morphology is what produced the defect.
_WRITE_NOT_VERB_STEMS: Final[tuple[str, ...]] = (
    "yazdır",  # yazdır / yazdırt / yazdırır -> PRINT (causative)
    "yazdir",
    "yazıcı",  # printer
    "yazici",
    "yazılım",  # software
    "yazilim",
    "yazım",  # spelling
    "yazim",
)


def _write_verb(tokens: tuple[str, ...]) -> str | None:
    """The token that really is "write", or None (B26 req 737)."""
    for token in tokens:
        if not token.startswith(_WRITE_VERB_STEMS):
            continue
        if token.startswith(_WRITE_NOT_VERB_STEMS):
            continue
        return token
    return None


_WRITE_TARGET_STEMS: Final[tuple[str, ...]] = ("buraya", "şuraya", "suraya", "kutu", "yere", "alan")

#: The target phrase to cut before reading the payload off the raw utterance (longest
#: first, so "bu kutuya" is not shadowed by a shorter overlapping match).
_WRITE_TARGET_PHRASES: Final[tuple[str, ...]] = (
    "seçili yere",
    "secili yere",
    "bu kutuya",
    "bu alana",
    "bu yere",
    "buraya",
    "şuraya",
    "suraya",
)
#: req 737 again, one layer down: the payload reader cuts the utterance AT the verb, so it
#: has to agree with `_write_verb` about which words are the verb. A negative lookahead
#: rather than a second list — two lists of the same words is how the two halves drift.
_WRITE_VERB_RE: Final[re.Pattern[str]] = re.compile(
    r"\byaz(?!dır|dir|ıcı|ici|ılım|ilim|ım\b|im\b)\w*\b"
)


def _type_text_match(tokens: tuple[str, ...]) -> str | None:
    if _write_verb(tokens) is None:
        return None
    if _has(tokens, *_WRITE_TARGET_STEMS) or _has_exact(tokens, *_DEICTIC_WORDS):
        return "yaz"
    return None


# ------------------------------------------------- B28: the operator's spoken input
#
#: The companion's ``keyboard.key`` vocabulary (``InputSynthesizer.KeyMap``), by the
#: words a Turkish speaker uses for each. Apostrophe suffixes ("enter'a", "escape'e")
#: are cut before matching, the same way ``resolve_app_alias`` cuts "chrome'u".
_KEY_BY_WORD: Final[dict[str, str]] = {
    "enter": "enter",
    "escape": "escape",
    "esc": "escape",
    "tab": "tab",
    "backspace": "backspace",
    "delete": "delete",
    "del": "delete",
    "insert": "insert",
    "home": "home",
    "end": "end",
    "pageup": "pageup",
    "pagedown": "pagedown",
    "space": "space",
    "boşluk": "space",
    "bosluk": "space",
    "boşluğa": "space",
    "bosluga": "space",
    **{f"f{n}": f"f{n}" for n in range(1, 13)},
}
#: Arrow words: "yukarı ok tuşuna bas" - the direction plus "ok".
_ARROW_BY_WORD: Final[dict[str, str]] = {
    "yukarı": "up",
    "yukari": "up",
    "aşağı": "down",
    "asagi": "down",
    "sol": "left",
    "sağ": "right",
    "sag": "right",
}
_MODIFIER_BY_WORD: Final[dict[str, str]] = {
    "ctrl": "ctrl",
    "control": "ctrl",
    "kontrol": "ctrl",
    "alt": "alt",
    "shift": "shift",
    "şift": "shift",
}
_PRESS_VERB_FORMS: Final[tuple[str, ...]] = (
    "bas",
    "bassana",
    "basar",
    "basın",
    "basin",
    "tuşla",
    "tusla",
    "tuşlasana",
    "tuslasana",
)
_KEY_NOUN_STEMS: Final[tuple[str, ...]] = ("tuş", "tus")
_SCROLL_VERB_FORMS: Final[tuple[str, ...]] = (
    "kaydır",
    "kaydir",
    "kaydırsana",
    "kaydirsana",
    "kaydırır",
    "kaydirir",
)
_SCROLL_DIRECTION_BY_WORD: Final[dict[str, str]] = {
    "aşağı": "down",
    "asagi": "down",
    "aşağıya": "down",
    "asagiya": "down",
    "yukarı": "up",
    "yukari": "up",
    "yukarıya": "up",
    "yukariya": "up",
}


def _bare(token: str) -> str:
    """The token without its apostrophe suffix: "enter'a" -> "enter", "s'ye" -> "s"."""
    return token.split("'", 1)[0]


#: The digits as the tr-TR normaliser spells them ("0 tuşuna bas" arrives as "sıfır tuşuna bas").
_DIGIT_KEY_BY_WORD: Final[dict[str, str]] = {
    "sıfır": "0",
    "sifir": "0",
    "bir": "1",
    "iki": "2",
    "üç": "3",
    "uc": "3",
    "dört": "4",
    "dort": "4",
    "beş": "5",
    "bes": "5",
    "altı": "6",
    "alti": "6",
    "yedi": "7",
    "sekiz": "8",
    "dokuz": "9",
}


def _key_press_match(tokens: tuple[str, ...]) -> str | None:
    """ "Enter'a bas." -> "enter"; "Ctrl S'ye bas." -> "ctrl+s"; "Yukarı ok tuşuna bas."
    -> "up". ``None`` when there is no press verb or no key the companion knows: "Düğmeye
    bas." names no key, and a clarification is the tool's to ask, not this matcher's to
    guess."""
    if _has_exact(tokens, *_PRESS_VERB_FORMS) is None:
        return None
    modifiers: list[str] = []
    key: str | None = None
    bare = [_bare(tok) for tok in tokens]
    for word in bare:
        if word in _MODIFIER_BY_WORD:
            if _MODIFIER_BY_WORD[word] not in modifiers:
                modifiers.append(_MODIFIER_BY_WORD[word])
        elif key is None and word in _KEY_BY_WORD:
            key = _KEY_BY_WORD[word]
    if key is None and _has_exact(tokens, "ok", "oka", "ok'a"):
        for word in bare:
            if word in _ARROW_BY_WORD:
                key = _ARROW_BY_WORD[word]
                break
    if key is None and not _has(tokens, "fare", "mouse"):
        # "Sağ tuşuna bas" (owner, 2026-09-20): the owner names the DIRECTION and the key
        # noun, not the word "ok" - and the sentence reached no intent at all, so nothing
        # was ever sent. The direction has to stand right before the key noun: "sağ" on its
        # own is a place ("sağdaki pencere"), and the mouse's own right button ("farenin sağ
        # tuşu") is the pointer's, never an arrow key.
        for index, word in enumerate(bare[:-1]):
            if word in _ARROW_BY_WORD and bare[index + 1].startswith(_KEY_NOUN_STEMS):
                key = _ARROW_BY_WORD[word]
                break
    if key is None and modifiers:
        # A chord's own key may be a bare letter or digit: "ctrl s'ye bas".
        for word in bare:
            if len(word) == 1 and word.isalnum():
                key = word
                break
    if key is None:
        # "0 tuşuna bas", "k tuşuna bas" (owner, 2026-09-19): a page's own shortcuts are single
        # characters. Only the word standing right before the key NOUN is read as the key -
        # "bir tuşa bas" names no key, and the normaliser has already spelled "0" as "sıfır".
        for index, word in enumerate(bare[:-1]):
            if not bare[index + 1].startswith(_KEY_NOUN_STEMS):
                continue
            if len(word) == 1 and word.isascii() and word.isalnum():
                key = word
            elif word in _DIGIT_KEY_BY_WORD and word != "bir":
                key = _DIGIT_KEY_BY_WORD[word]
            break
    if key is None:
        return None
    return "+".join([*modifiers, key])


#: B29 req 100. "X düğmesine tıkla/bas" - the button NOUN plus a click/press verb; the
#: name is the words before the noun, read off the raw utterance for its own casing.
_BUTTON_NOUN_FORMS: Final[tuple[str, ...]] = (
    "düğmesine",
    "dugmesine",
    "düğmeye",
    "dugmeye",
    "butonuna",
    "butona",
)
_CLICK_VERB_FORMS: Final[tuple[str, ...]] = (
    "tıkla",
    "tikla",
    "tıklasana",
    "tiklasana",
    "tıklar",
    "tiklar",
    "bas",
    "bassana",
    "basar",
)
_BUTTON_RE: Final = re.compile(
    r"^(?P<name>.+?)\s+(?:düğmesine|dugmesine|düğmeye|dugmeye|butonuna|butona)\b", re.IGNORECASE
)
#: B29 req 102. "Ekrandaki metni oku." / "Ne yazıyor?" / "Belgeyi oku." - a text noun (or
#: the adapter's document word) with a read verb, or the bare "ne yazıyor" question.
_READ_TEXT_NOUN_FORMS: Final[tuple[str, ...]] = (
    "metni",
    "metin",
    "yazıyı",
    "yaziyi",
    "yazı",
    "yazi",
    "alanı",
    "alani",
)
_READ_VERB_FORMS_UI: Final[tuple[str, ...]] = ("oku", "okusana", "okur", "söyle", "soyle")
#: The words a bare "Ne yazıyor?" may carry and still be about the screen in front.
_BARE_READ_WORDS: Final[frozenset[str]] = frozenset(
    {
        "ne",
        "yazıyor",
        "yaziyor",
        "burada",
        "orada",
        "şurada",
        "surada",
        "ekranda",
        "ekrandaki",
        "ekranımda",
        "ekranimda",
    }
)
#: B29 req 105. "Ekranda ne var?" / "Ekranı anlat." / "Ekranı tarif et." ("Ne görüyorsun?"
#: stays the activity explanation's - the eye's own question, ADR-0079.)
_DESCRIBE_VERB_FORMS: Final[tuple[str, ...]] = ("anlat", "anlatsana", "tarif", "betimle")
_SCREEN_LOCATIVE_FORMS: Final[tuple[str, ...]] = (
    "ekranda",
    "ekrandaki",
    "ekranımda",
    "ekranimda",
    "ekranımı",
    "ekranimi",
    "ekranım",
    "ekranim",
)


def _ui_invoke_match(tokens: tuple[str, ...], utterance: str) -> str | None:
    """ "Tamam düğmesine tıkla." -> "Tamam"; None when no button is named."""
    if _has_exact(tokens, *_BUTTON_NOUN_FORMS) is None:
        return None
    if _has_exact(tokens, *_CLICK_VERB_FORMS) is None:
        return None
    match = _BUTTON_RE.search(utterance.strip())
    if match is None:
        return None
    name = match.group("name").strip(" ,.'\"")
    # "Şu düğmeye bas" names nothing a tree can find.
    if not name or turkish_casefold(name) in _DEICTIC_WORDS:
        return None
    return name


def _ui_read_match(tokens: tuple[str, ...]) -> tuple[str, str | None] | None:
    """(matched, spoken target): "Ekrandaki metni oku." -> ("metni oku", "metin");
    "Ne yazıyor?" -> ("ne yazıyor", None)."""
    # The BARE question only: "Ne yazıyor?" / "Burada ne yazıyor?". With a page or a
    # document named ("Üçüncü sayfada ne yazıyor?") the question is the document
    # family's (DOCUMENT_ANSWER), resolved further down the ladder.
    if (
        _has_exact(tokens, "ne")
        and _has(tokens, "yazıyor", "yaziyor")
        and all(tok in _BARE_READ_WORDS for tok in tokens)
    ):
        return "ne yazıyor", None
    noun = _has_exact(tokens, *_READ_TEXT_NOUN_FORMS)
    if noun is None or _has_exact(tokens, *_READ_VERB_FORMS_UI) is None:
        return None
    # The document word the adapter knows ("belge" / "metin" / "yazı"), bare.
    bare_forms = {"metni": "metin", "yazıyı": "yazı", "yaziyi": "yazı"}
    target = bare_forms.get(noun, noun)
    return "metni oku", target


def _screen_describe_match(tokens: tuple[str, ...]) -> str | None:
    """ "Ekranda ne var?" / "Ekranı anlat." / "Ekranımı tarif et."."""
    on_screen = _has_exact(tokens, *_SCREEN_LOCATIVE_FORMS)
    screen = _screen_noun(tokens)
    if on_screen and _has_exact(tokens, "ne", "neler") and _has_exact(tokens, "var"):
        return "ekranda ne var"
    if (screen or on_screen) and _has(tokens, *_DESCRIBE_VERB_FORMS):
        return "ekranı anlat"
    return None


# ------------------------------------------- B30: applications, processes, services

_SERVICE_NOUN_STEMS: Final[tuple[str, ...]] = ("servis", "hizmet")
_RUNNING_QUERY_FORMS: Final[tuple[str, ...]] = ("çalışıyor", "calisiyor", "açık", "acik", "aktif")
_RESTART_STEMS: Final[tuple[str, ...]] = ("başlat", "baslat")
_STOP_PROCESS_STEMS: Final[tuple[str, ...]] = ("sonlandır", "sonlandir", "öldür", "oldur")
_PROCESS_NOUN_STEMS: Final[tuple[str, ...]] = (
    "uygulama",
    "program",
    "süreç",
    "surec",
    "işlem",
    "islem",
)
_WHICH_FORMS: Final[tuple[str, ...]] = ("hangi", "neler", "ne")


def _service_match(tokens: tuple[str, ...]) -> tuple[Intent, str] | None:
    """ "Yazdırma servisi çalışıyor mu?" -> (SERVICE_QUERY, "yazdırma"); "Spooler servisini
    yeniden başlat." -> (SERVICE_RESTART, "spooler"). The service NOUN is required."""
    noun_index = next(
        (i for i, tok in enumerate(tokens) if tok.startswith(_SERVICE_NOUN_STEMS)), None
    )
    if noun_index is None:
        return None
    # The service's own name is the word before the noun ("yazdırma servisi").
    name = _bare(tokens[noun_index - 1]) if noun_index > 0 else ""
    if _has(tokens, "yeniden", "tekrar") and _has(tokens, *_RESTART_STEMS):
        return Intent.SERVICE_RESTART, name
    if _has_exact(tokens, *_RUNNING_QUERY_FORMS) and _is_question(tokens):
        return Intent.SERVICE_QUERY, name
    if _has(tokens, "durum") and _has_exact(tokens, "ne", "nedir", "nasıl", "nasil"):
        return Intent.SERVICE_QUERY, name
    return None


def _process_match(tokens: tuple[str, ...]) -> tuple[Intent, str | None, str] | None:
    """ "Chrome çalışıyor mu?" -> (PROCESS_QUERY, "chrome"); "Hangi uygulamalar açık?" ->
    (PROCESS_QUERY, None); "Chrome'u sonlandır." -> (PROCESS_STOP, "chrome")."""
    from app.operator.plans import resolve_app_alias

    canonical = resolve_app_alias(tokens)
    if _has(tokens, *_STOP_PROCESS_STEMS):
        if canonical is None:
            return None
        return Intent.PROCESS_STOP, canonical, "sonlandır"
    if _has_exact(tokens, *_RUNNING_QUERY_FORMS) and _is_question(tokens):
        if canonical is not None:
            return Intent.PROCESS_QUERY, canonical, "çalışıyor mu"
        if _has(tokens, *_PROCESS_NOUN_STEMS) and _has_exact(tokens, *_WHICH_FORMS):
            return Intent.PROCESS_QUERY, None, "hangi uygulamalar açık"
    if (
        _has(tokens, *_PROCESS_NOUN_STEMS)
        and _has_exact(tokens, *_WHICH_FORMS)
        and _has_exact(tokens, *_RUNNING_QUERY_FORMS)
    ):
        return Intent.PROCESS_QUERY, None, "hangi uygulamalar açık"
    return None


def _app_close_match(tokens: tuple[str, ...]) -> str | None:
    """ "Not Defteri'ni kapat." / "Chrome'u kapat." — an allowlisted application named with
    the close verb. The alarm/display/eye families (which own "kapat" with their own nouns)
    are resolved before this, and a bare "Bunu kapat" names no application and stays the
    window family's."""
    if _has_exact(tokens, *_CLOSE_VERB_FORMS) is None:
        return None
    from app.operator.plans import resolve_app_alias

    return resolve_app_alias(tokens)


# ------------------------------------------ B31: pause, resume, open, answer register

_RESEARCH_PAUSE_FORMS: Final[tuple[str, ...]] = ("duraklat", "beklet", "askıya", "askiya")
_RESEARCH_PAUSE_NEGATION_FORMS: Final[tuple[str, ...]] = (
    "duraklatma",
    "duraklatmayın",
    "duraklatmayin",
    "bekletme",
    "bekletmeyin",
)
_RESEARCH_RESUME_FORMS: Final[tuple[str, ...]] = ("devam", "sürdür", "surdur")
_RESEARCH_OPEN_REFERENCE_WORDS: Final[tuple[str, ...]] = (
    "önceki",
    "onceki",
    "öncekini",
    "oncekini",
    "son",
    "bu",
    "şu",
    "su",
    "o",
    "ilk",
    "birinci",
    "ikinci",
    "üçüncü",
    "ucuncu",
    "dördüncü",
    "dorduncu",
    "beşinci",
    "besinci",
)
_ANSWER_MODE_STANDING_MARKERS: Final[tuple[str, ...]] = ("bundan", "artık", "artik", "hep", "her")
_ANSWER_MODE_LEVEL_WORDS: Final[tuple[tuple[str, ...], str]] = (
    (("teknik",), "technical"),
    (("ayrıntılı", "ayrintili", "detaylı", "detayli", "ayrıntı", "ayrinti"), "detail"),
    (("tam", "tamamını", "tamamini", "uzun"), "full"),
    (("kısa", "kisa", "özet", "ozet", "yönetici", "yonetici", "kısaca", "kisaca"), "executive"),
)


def _research_pause_match(tokens: tuple[str, ...]) -> str | None:
    """ "Araştırmayı duraklat." / "Araştırmayı beklet." (B31 req 203) - the research
    named with a pause verb; "durdur" stays the cancel it always was."""
    if _has(tokens, *_RESEARCH_NOUN_STEMS) is None:
        return None
    if _has_exact(tokens, *_RESEARCH_CANCEL_NEGATION_FORMS, *_RESEARCH_PAUSE_NEGATION_FORMS):
        return None
    if _has(tokens, *_RESEARCH_PAUSE_FORMS):
        return "araştırmayı duraklat"
    return None


def _research_resume_match(tokens: tuple[str, ...]) -> str | None:
    """ "Araştırmaya devam et." / "Araştırmayı sürdür." (B31 req 204). The research noun is
    required: a bare "devam" belongs to the narration and the conversation."""
    if _has(tokens, *_RESEARCH_NOUN_STEMS) is None:
        return None
    if _has_exact(tokens, "etme", "etmeyin"):
        return None
    if _has(tokens, *_RESEARCH_RESUME_FORMS):
        return "araştırmaya devam et"
    return None


def _research_open_match(tokens: tuple[str, ...]) -> str | None:
    """ "Bir önceki araştırmayı aç." / "Son araştırmayı aç." / "İkinci araştırmayı aç."
    (B31 req 201) - a research POINTED AT with the open verb. Without a pointer ("yeni bir
    araştırma aç") this is not an open, and the artifact family's "bunu aç" without the
    research noun is not this either."""
    if _has(tokens, *_RESEARCH_NOUN_STEMS) is None:
        return None
    if _has_exact(tokens, *_ARTIFACT_OPEN_VERB_FORMS) is None:
        return None
    if _has_exact(tokens, "yeni"):
        return None
    if _has_exact(tokens, *_RESEARCH_OPEN_REFERENCE_WORDS) is None:
        return None
    return "araştırmayı aç"


def _answer_mode_match(tokens: tuple[str, ...]) -> tuple[str, str] | None:
    """ "Bundan sonra teknik anlat." -> ("technical", ...); "Teknik modu kapat." ->
    ("executive", ...); "Artık kısa anlat." -> ("executive", ...) (B31 req 209). A
    STANDING register needs a standing marker ("bundan sonra", "artık", "hep", "her
    zaman") or the word "mod"; a one-off "teknik anlat" stays the follow-up it was."""
    has_mode_word = _has(tokens, "mod") is not None
    standing = _has_exact(tokens, *_ANSWER_MODE_STANDING_MARKERS) is not None and (
        _has_exact(tokens, "sonra", "zaman", "hep", "artık", "artik") is not None
    )
    if not (has_mode_word or standing):
        return None
    if has_mode_word and _has(tokens, "teknik") and _has_exact(tokens, *_CLOSE_VERB_FORMS):
        return "executive", "teknik modu kapat"
    for words, level in _ANSWER_MODE_LEVEL_WORDS:
        if _has_exact(tokens, *words) or _has(tokens, *words):
            if has_mode_word or _has(tokens, "anlat", "konuş", "konus", "cevap", "söyle", "soyle"):
                return level, f"bundan sonra {words[0]} anlat"
    return None


def _scroll_match(tokens: tuple[str, ...]) -> str | None:
    """ "Aşağı kaydır." -> "down"; "Yukarı kaydır." -> "up"."""
    if _has_exact(tokens, *_SCROLL_VERB_FORMS) is None:
        return None
    for tok in tokens:
        if _bare(tok) in _SCROLL_DIRECTION_BY_WORD:
            return _SCROLL_DIRECTION_BY_WORD[_bare(tok)]
    return None


def _extract_type_text(utterance: str) -> str | None:
    """The payload of a TYPE_TEXT utterance ("Buraya merhaba yaz." -> "merhaba"), read off
    the raw text (never the filtered/lower-cased token list: the payload's own casing is
    the owner's words). ``None`` when nothing was actually said to type ("Şuraya yazar
    mısın?") - a clarification is then the honest answer, never a guess (spec §4)."""
    if not utterance:
        return None
    lowered = turkish_casefold(utterance).strip()
    match = _WRITE_VERB_RE.search(lowered)
    if match is None:
        return None
    before = lowered[: match.start()].strip(" ,.'\"")
    for phrase in _WRITE_TARGET_PHRASES:
        if before.startswith(phrase):
            before = before[len(phrase) :].strip(" ,.'\"")
            break
    return before or None


#: docs/M19_DIGITAL_OPERATOR_SPEC.md §3: ``operator.type`` refuses a secret-looking
#: request outright ("Buraya şifremi yaz" -> "Şifreleri ben yazmam"). One place names the
#: words, so the tool never has to keep a second copy of this list.
_SECRET_WORD_STEMS: Final[tuple[str, ...]] = ("şifre", "sifre", "parola", "password", "pin")


def contains_secret_reference(text: str) -> bool:
    """Whether ``text`` names a password/PIN-shaped secret (module docstring)."""
    if not text:
        return False
    _, tokens, _ = normalize_transcript(text)
    return _has(tokens, *_SECRET_WORD_STEMS) is not None


def _app_open_match(tokens: tuple[str, ...]) -> tuple[str, str] | None:
    """ "Not Defteri'ni aç" / "Chrome'u aç" / "Tarayıcıyı aç" (spec §2's ``app.launch``
    allowlist, spec §3's APP_OPEN). Requires an open-imperative verb (module: "açık" the
    adjective/query stays a query) AND a name the allowlist alias table actually knows -
    "Kapıyı aç" (open the door) names nothing on the list and resolves to nothing here."""
    if not _has_exact(tokens, *_OPEN_VERB_FORMS):
        return None
    from app.operator.plans import resolve_app_alias

    canonical = resolve_app_alias(tokens)
    if canonical is None:
        return None
    return canonical, canonical


# --------------------------------------------- M20: File & Document Intelligence
#
# Built on the SAME token/stem primitives as every intent above - no second Turkish
# pattern table (module docstring's own rule). Folder/extension name aliases are a
# domain fact of app.documents, not a routing table, and stay right here (small enough
# that a lazy import would only add indirection); a wrong route here reaches no device
# capability either way (ADR-0083 decision 7: there is no write/delete tool to misroute
# into).
#
# "Bu dosyayı sil." names a document noun and a verb this table does not recognise
# ("sil") - it resolves to every branch below returning None and falls through to
# Intent.NONE, which dispatches no tool at all (module docstring's own rule for NONE).

#: Whole-document-kind nouns ("bu EXCEL'de", "bu PDF'i") - deliberately NOT internal
#: place nouns (sayfa/slayt/satır), which name a LOCATION inside a document and belong to
#: DOCUMENT_ANSWER instead (a Turkish suffix is still a prefix match: "dosyayı" starts
#: with "dosya").
_DOCUMENT_NOUN_STEMS: Final[tuple[str, ...]] = (
    "dosya",
    "belge",
    # B32 req 169/142: "Bu iki dokümanı karşılaştır", "Arşivin içinde ne var?"
    "doküman",
    "dokuman",
    "arşiv",
    "arsiv",
    "zip",
    "pdf",
    "sunum",
    "excel",
    "tablo",
    "word",
)
#: Internal LOCATION nouns a content question names ("üçüncü sayfada", "bu satırda").
_DOCUMENT_PLACE_STEMS: Final[tuple[str, ...]] = (
    "sayfa",
    "slayt",
    "slayd",
    "satır",
    "satir",
    "madde",
    "paragraf",
    "bölüm",
    "bolum",
    "anahtar",
)
_FOLDER_ALIASES: Final[dict[str, str]] = {
    "masaüstü": "Desktop",
    "masaustu": "Desktop",
    "belgelerim": "Documents",
    "belgelerimde": "Documents",
    "indirilenler": "Downloads",
}
_EXTENSION_ALIASES: Final[dict[str, str]] = {
    "pdf": ".pdf",
    "excel": ".xlsx",
    "xlsx": ".xlsx",
    "word": ".docx",
    "docx": ".docx",
    "sunum": ".pptx",
    "pptx": ".pptx",
    "powerpoint": ".pptx",
    "csv": ".csv",
}
_FIND_VERB_FORMS: Final[tuple[str, ...]] = ("bul", "bulsana", "bulur")
#: Exact forms only - "ara" as a stem would also match "araştır"/"araştırma" (a totally
#: unrelated word that happens to share a prefix; the eye/camera noun table above states
#: the same rule for the same reason).
_SEARCH_VERB_FORMS: Final[tuple[str, ...]] = ("ara", "arasana", "arar")
_COMPARE_VERB_STEMS: Final[tuple[str, ...]] = ("karşılaştır", "karsilastir")
_DOCUMENT_RETURN_VERB_FORMS: Final[tuple[str, ...]] = ("dön", "don", "geç", "gec")
_READ_VERB_FORMS: Final[tuple[str, ...]] = ("oku", "okusana", "okur", "okuyabilir")
_SUMMARIZE_STEMS: Final[tuple[str, ...]] = ("özet", "ozet")
_COMMON_POINTS_STEM: Final = "ortak"
_CONTENT_QUESTION_VERB_STEMS: Final[tuple[str, ...]] = ("yaz", "diyor", "yazılı", "yazili")


def _document_search_match(tokens: tuple[str, ...]) -> str | None:
    """ "Bu klasördeki PDF'leri bul." / "Masaüstündeki sözleşmeyi bul." / "İndirilenler'de
    bütçe dosyasını ara." (spec §3)."""
    verb = _has_exact(tokens, *_FIND_VERB_FORMS, *_SEARCH_VERB_FORMS)
    if verb is None:
        return None
    if _has(tokens, *_DOCUMENT_NOUN_STEMS) is None and not any(
        tok.startswith(alias) for tok in tokens for alias in _FOLDER_ALIASES
    ):
        return None
    return verb


def _document_previous_word_match(tokens: tuple[str, ...]) -> str | None:
    """Any "öncek..." token, WITHOUT ``_previous_match``'s research-only exception that
    "az önceki" names the most recent (i.e. CURRENT) one rather than the one before it.
    That exception exists because a research can "just finish" — there is no document
    equivalent: spec §3's own example, "Az önceki sunuma geri dön.", means the previous
    document, full stop."""
    for tok in tokens:
        if tok.startswith("öncek") or tok.startswith("oncek"):
            return tok
    return None


def _document_previous_match(tokens: tuple[str, ...]) -> str | None:
    """ "Az önceki sunuma geri dön." / "Bir önceki belgeye dön." (spec §3) - checked BEFORE
    compare, since "karşılaştır" never appears in these phrases and the reverse ordering
    would be just as safe; kept this way to read in the same order as the phrase list."""
    if _has(tokens, *_DOCUMENT_NOUN_STEMS) is None:
        return None
    if _document_previous_word_match(tokens) is None:
        return None
    if _has(tokens, *_DOCUMENT_RETURN_VERB_FORMS) is None:
        return None
    return "önceki belgeye dön"


def _document_compare_match(tokens: tuple[str, ...]) -> str | None:
    """ "Bir önceki belgeyle karşılaştır." / "Önceki dosyayla karşılaştır." (spec §3) -
    always current vs. previous (the only compare phrasing this milestone's corpus asks
    for; a caller passing an explicit ``a``/``b`` argument overrides the default)."""
    if _has(tokens, *_COMPARE_VERB_STEMS) is None:
        return None
    if _has(tokens, *_DOCUMENT_NOUN_STEMS) is None:
        return None
    return "karşılaştır"


# ------------------------------------------------ B32: preview, full text, duplicates

_PREVIEW_STEMS: Final[tuple[str, ...]] = ("önizle", "onizle")
_DUPLICATE_STEMS: Final[tuple[str, ...]] = (
    "yinelenen",
    "kopya",
    "mükerrer",
    "mukerrer",
    "çift",
    "cift",
)
_TRASH_FORMS: Final[tuple[str, ...]] = (
    "çöp",
    "cop",
    "temizle",
    "sil",
    "kaldır",
    "kaldir",
    "gönder",
    "gonder",
)
_IMAGE_NOUN_STEMS: Final[tuple[str, ...]] = (
    "görsel",
    "gorsel",
    "resim",
    "fotoğraf",
    "fotograf",
    "foto",
)
_ARCHIVE_NOUN_STEMS: Final[tuple[str, ...]] = ("arşiv", "arsiv", "zip")
_TEXT_NOUN_STEMS: Final[tuple[str, ...]] = ("metin", "metni", "yazı", "yazi", "yazıyı", "yaziyi")
_CONTAINS_FORMS: Final[tuple[str, ...]] = ("geçen", "gecen", "yazan", "içeren", "iceren", "bulunan")


def _document_preview_match(tokens: tuple[str, ...]) -> str | None:
    """ "Bu belgeyi önizle." / "Bu dosyanın önizlemesini göster." (B32 req 152)."""
    if _has(tokens, *_PREVIEW_STEMS) is None:
        return None
    return "önizle"


def _document_find_text_match(tokens: tuple[str, ...]) -> tuple[str, str] | None:
    """ "İçinde bütçe geçen belgeyi bul." / "Hetzner yazan dosya hangisi?" (B32 req 148):
    the words BEFORE the containing verb are the query; the document noun is required so a
    plain "X geçen" question stays the conversation's."""
    if _has(tokens, *_DOCUMENT_NOUN_STEMS) is None:
        return None
    verb_index = next((i for i, tok in enumerate(tokens) if tok in _CONTAINS_FORMS), None)
    if verb_index is None:
        return None
    start = 0
    for i in range(verb_index - 1, -1, -1):
        if tokens[i] in ("içinde", "icinde", "metninde", "içeriğinde", "iceriginde"):
            start = i + 1
            break
    words = [t for t in tokens[start:verb_index] if t not in ("içinde", "icinde", "bir")]
    if not words:
        return None
    return " ".join(words), "içinde geçen belge"


def _document_duplicates_match(tokens: tuple[str, ...]) -> str | None:
    """ "Yinelenen dosyaları bul." / "Kopya dosyaları bul." (B32 req 151)."""
    if _has(tokens, *_DUPLICATE_STEMS) is None:
        return None
    if _has(tokens, *_DOCUMENT_NOUN_STEMS) is None:
        return None
    if _has_exact(tokens, *_FIND_VERB_FORMS, *_SEARCH_VERB_FORMS, "göster", "goster", "listele"):
        return "yinelenen dosyaları bul"
    if _has_exact(tokens, "var") and _has_exact(tokens, "mı", "mi", "mu", "mü"):
        return "yinelenen dosya var mı"
    return None


# ------------------------------------------------ B34: managed file mutation (153-170)

_MUTATION_DOC_NOUN_STEMS: Final[tuple[str, ...]] = ("dosya", "belge", "doküman", "dokuman", "not")
#: "aç" is NOT here: "dosyayı aç" is the operator's / the artifact family's OPEN, and the
#: first gate run measured both stolen by "yeni dosya aç".
_CREATE_VERB_FORMS: Final[tuple[str, ...]] = ("oluştur", "olustur", "yarat")
_NAME_MARKERS: Final[tuple[str, ...]] = (
    "adında",
    "adinda",
    "adıyla",
    "adiyla",
    "isimli",
    "adlı",
    "adli",
    "ismiyle",
)
_APPEND_MARKERS: Final[tuple[str, ...]] = ("sonuna", "altına", "altina")
_APPEND_VERB_FORMS: Final[tuple[str, ...]] = ("ekle", "eklesene", "yaz", "yazsana")
_REPLACE_MARKER: Final = "yerine"
_EDIT_VERB_FORMS: Final[tuple[str, ...]] = ("yaz", "yazsana", "değiştir", "degistir", "koy")
_UPDATE_SAVE_STEMS: Final[tuple[str, ...]] = ("güncelle", "guncelle")
_RENAME_MARKERS: Final[tuple[str, ...]] = ("adını", "adini", "ismini", "adi", "adı")
_RENAME_VERB_FORMS: Final[tuple[str, ...]] = (
    "yap",
    "değiştir",
    "degistir",
    "koy",
    "adlandır",
    "adlandir",
)
_MOVE_VERB_STEMS: Final[tuple[str, ...]] = ("taşı", "tasi")
_COPY_VERB_STEMS: Final[tuple[str, ...]] = ("kopyala", "kopyasını", "kopyasini")
_DELETE_VERB_FORMS: Final[tuple[str, ...]] = ("sil", "silsene", "siler")
_TRASH_NOUN_STEMS: Final[tuple[str, ...]] = ("çöp", "cop")
_UNDO_NOUN_STEMS: Final[tuple[str, ...]] = ("değişikli", "degisikli", "düzenleme", "duzenleme")
_VERSION_NOUN_STEMS: Final[tuple[str, ...]] = ("sürüm", "surum", "versiyon", "geçmiş", "gecmis")
_APPLY_FORMS: Final[tuple[str, ...]] = (
    "uygula",
    "uygulayabilirsin",
    "kaydet",
    "onaylıyorum",
    "onayliyorum",
)
#: Nouns whose families own their own "sil"/"kaldır"/"taşı": never a file mutation.
_MUTATION_FOREIGN_STEMS: Final[tuple[str, ...]] = (
    "mail",
    "posta",
    "e-posta",
    "eposta",
    "etkinli",
    "toplantı",
    "toplanti",
    "randevu",
    "alarm",
    "hatırlat",
    "hatirlat",
    "kopyalar",  # B32's duplicates keep "kopyaları çöp kutusuna gönder" ("kopyala" is ours)
    "yinelen",
    "uygulam",
    "proje",
    "kurulum",
    "sunum",
    "excel",
    "tablo",
    "pdf",
)


def _has_mutation_noun(tokens: tuple[str, ...]) -> bool:
    return _has(tokens, *_MUTATION_DOC_NOUN_STEMS) is not None


def _mutation_foreign(tokens: tuple[str, ...]) -> bool:
    return _has(tokens, *_MUTATION_FOREIGN_STEMS) is not None


def _words_between(tokens: tuple[str, ...], start: int, stop: int) -> str:
    return " ".join(tokens[start:stop]).strip()


_NAME_BEFORE_MARKER_RE: Final[re.Pattern[str]] = re.compile(
    r"(?P<name>[\w][\w.\-]*)['’]?\s+(?:adında|adinda|adıyla|adiyla|isimli|adlı|adli|ismiyle)\b",
    re.IGNORECASE,
)
_NAME_AFTER_RENAME_RE: Final[re.Pattern[str]] = re.compile(
    r"(?:adını|adini|ismini)\s+(?P<name>[\w][\w.\-]*)\s+(?:olarak\s+)?(?:yap|değiştir|degistir|koy)\b",
    re.IGNORECASE,
)
_NAME_BEFORE_OLARAK_RE: Final[re.Pattern[str]] = re.compile(
    r"(?P<name>[\w][\w.\-]*)\s+olarak\s+(?:yeniden\s+)?adlandır", re.IGNORECASE
)


def _spoken_file_name(text: str, *patterns: re.Pattern[str]) -> str | None:
    """A file name the owner SPELLED in the raw utterance ("notlar-yeni.md adında"): the
    tokenizer cuts a dotted name into pieces, so the name is read off the text itself."""
    lowered = turkish_casefold(text)
    for pattern in patterns:
        match = pattern.search(lowered)
        if match:
            name = match.group("name").strip("'’.")
            if name and name not in ("bir", "bu", "şu", "su", "yeni"):
                return name
    return None


def _mutation_foreign_before(tokens: tuple[str, ...], stop: int) -> bool:
    """Another family's noun in the TARGET part of the sentence (before the marker); the
    payload after it may say anything ("... sonuna toplantı notu ekle")."""
    return _has(tokens[:stop], *_MUTATION_FOREIGN_STEMS) is not None


def _document_write_match(tokens: tuple[str, ...], text: str) -> tuple[str, str | None] | None:
    """ "X adında bir dosya oluştur." / "Yeni bir metin dosyası oluştur." (154): the
    document noun ("dosya" - "sunum oluştur" is M22's) with a create verb; the name is what
    the owner spelled before "adında/adıyla/isimli", read off the raw text."""
    if _has(tokens, "dosya") is None:
        return None
    if _has_exact(tokens, *_CREATE_VERB_FORMS) is None:
        return None
    if _mutation_foreign(tokens):
        return None
    return "dosya oluştur", _spoken_file_name(text, _NAME_BEFORE_MARKER_RE)


def _document_append_match(tokens: tuple[str, ...]) -> tuple[str, str | None] | None:
    """ "Bu dosyanın sonuna şunu ekle." (155): the text is what sits between "sonuna" and
    the verb - or nothing, and the tool asks."""
    if not _has_mutation_noun(tokens):
        return None
    marker = next((i for i, t in enumerate(tokens) if t in _APPEND_MARKERS), None)
    if marker is None or _mutation_foreign_before(tokens, marker):
        return None
    verb = next((i for i, t in enumerate(tokens) if i > marker and t in _APPEND_VERB_FORMS), None)
    if verb is None:
        return None
    text = _words_between(tokens, marker + 1, verb)
    fillers = ("şunu", "sunu", "bunu", "şu", "su", "diye", "şunları", "sunlari")
    for filler in fillers:
        if text.startswith(filler + " "):
            text = text[len(filler) + 1 :]
        if text.endswith(" " + filler):
            text = text[: -(len(filler) + 1)]
    if text in fillers:
        text = ""
    return "sonuna ekle", (text or None)


def _document_edit_match(tokens: tuple[str, ...]) -> tuple[str, str | None, str | None] | None:
    """ "Bu dosyada X yerine Y yaz." (153, 167) -> (matched, find, replace); "Bu belgeyi
    güncelle ve kaydet." (170) -> (matched, None, None): the new content is the model's."""
    if not _has_mutation_noun(tokens):
        return None
    marker = next((i for i, t in enumerate(tokens) if t == _REPLACE_MARKER), None)
    if marker is None and _mutation_foreign(tokens):
        return None
    if marker is not None:
        noun_index = next(
            (i for i, t in enumerate(tokens) if t.startswith(_MUTATION_DOC_NOUN_STEMS)), 0
        )
        if _mutation_foreign_before(tokens, noun_index + 1):
            return None
        verb = next((i for i, t in enumerate(tokens) if i > marker and t in _EDIT_VERB_FORMS), None)
        if verb is None:
            return None
        noun = next(
            (
                i
                for i, t in enumerate(tokens)
                if i < marker and t.startswith(_MUTATION_DOC_NOUN_STEMS)
            ),
            -1,
        )
        find = _words_between(tokens, noun + 1, marker)
        replace = _words_between(tokens, marker + 1, verb)
        if not find:
            return None
        return "yerine yaz", find, replace
    if _has(tokens, *_UPDATE_SAVE_STEMS) is not None:
        return "güncelle ve kaydet", None, None
    return None


def _document_rename_match(tokens: tuple[str, ...], text: str) -> tuple[str, str | None] | None:
    """ "Bu dosyanın adını X yap." / "... X olarak yeniden adlandır." (156)."""
    if not _has_mutation_noun(tokens) or _mutation_foreign(tokens):
        return None
    if _has(tokens, "adlandır", "adlandir") is not None:
        return "yeniden adlandır", _spoken_file_name(text, _NAME_BEFORE_OLARAK_RE)
    marker = next((i for i, t in enumerate(tokens) if t in _RENAME_MARKERS), None)
    if marker is None:
        return None
    verb = next((i for i, t in enumerate(tokens) if i > marker and t in _RENAME_VERB_FORMS), None)
    if verb is None:
        return None
    return "adını değiştir", _spoken_file_name(text, _NAME_AFTER_RENAME_RE)


def _document_move_match(tokens: tuple[str, ...]) -> str | None:
    """ "Bu dosyayı Masaüstüne taşı." (157): a document noun, a spoken folder, the move verb."""
    if not _has_mutation_noun(tokens) or _mutation_foreign(tokens):
        return None
    if _has(tokens, *_MOVE_VERB_STEMS) is None:
        return None
    return "taşı"


def _document_copy_match(tokens: tuple[str, ...], text: str) -> tuple[str, str | None] | None:
    """ "Bu dosyayı kopyala." / "... Masaüstüne kopyala." / "... X adıyla kopyala." (158)."""
    if not _has_mutation_noun(tokens) or _mutation_foreign(tokens):
        return None
    if _has(tokens, *_COPY_VERB_STEMS) is None:
        return None
    return "kopyala", _spoken_file_name(text, _NAME_BEFORE_MARKER_RE)


def _document_delete_match(tokens: tuple[str, ...]) -> str | None:
    """ "Bu dosyayı sil." / "Bu dosyayı çöp kutusuna gönder." (159): the document noun with
    the delete verb or the bin; the duplicate noun stays B32's dedup, a mail/calendar noun
    stays its family's."""
    if not _has_mutation_noun(tokens) or _mutation_foreign(tokens):
        return None
    if _has_exact(tokens, *_DELETE_VERB_FORMS) is not None:
        return "sil"
    if (
        _has(tokens, *_TRASH_NOUN_STEMS) is not None
        and _has(tokens, "gönder", "gonder", "at", "taşı", "tasi") is not None
        # B51 (746): "Kopya dosyaları çöp kutusuna gönder." is B32's duplicate cleanup;
        # without this it proposed trashing the CURRENT file instead.
        and _has(tokens, *_DUPLICATE_STEMS) is None
    ):
        return "çöp kutusuna gönder"
    return None


def _document_undo_match(tokens: tuple[str, ...]) -> str | None:
    """ "Son değişikliği geri al." / "Dosyadaki değişikliği geri al." (160)."""
    if (
        _has_exact(tokens, "geri") is None
        or _has_exact(tokens, "al", "alsana", "alır", "alir") is None
    ):
        return None
    if _has(tokens, *_UNDO_NOUN_STEMS) is None and not _has_mutation_noun(tokens):
        return None
    if _mutation_foreign(tokens):
        return None
    return "geri al"


def _document_versions_match(tokens: tuple[str, ...]) -> str | None:
    """ "Bu dosyanın sürüm geçmişini göster." (164)."""
    if not _has_mutation_noun(tokens) or _mutation_foreign(tokens):
        return None
    if _has(tokens, *_VERSION_NOUN_STEMS) is None:
        return None
    return "sürüm geçmişi"


def _document_apply_match(tokens: tuple[str, ...], *, mutation_pending: bool) -> str | None:
    """ "Uygula." / "Kaydet." / "Onaylıyorum." - BARE, and only while a file change this
    session heard is pending (the same shape "Gönder." has with a draft)."""
    if not mutation_pending:
        return None
    for form in _APPLY_FORMS:
        if _is_bare(tokens, form) or (
            len(tokens) == 2 and tokens[0] in ("evet", "tamam") and tokens[1] == form
        ):
            return form
    return None


def _document_dedup_match(tokens: tuple[str, ...]) -> str | None:
    """ "Kopyaları çöp kutusuna gönder." / "Yinelenenleri temizle." (B32 req 150) - the
    duplicate noun with a trash verb; a bare "sil" without the duplicate noun is never
    this."""
    if _has(tokens, *_DUPLICATE_STEMS) is None:
        return None
    if _has(tokens, *_TRASH_FORMS) is None:
        return None
    if _has_exact(tokens, *_FIND_VERB_FORMS):
        return None
    return "kopyaları çöp kutusuna gönder"


def _image_text_match(tokens: tuple[str, ...]) -> str | None:
    """ "Görseldeki metni oku." / "Resimdeki yazıyı oku." (B32 req 141): the picture, its
    text, the read verb. Before the screen-reading family, which owns a bare "metni oku"."""
    if _has(tokens, *_IMAGE_NOUN_STEMS) is None:
        return None
    if _has(tokens, *_TEXT_NOUN_STEMS) is None:
        return None
    if _has_exact(tokens, *_READ_VERB_FORMS, "söyle", "soyle") is None:
        return None
    return "görseldeki metni oku"


def _image_metadata_match(tokens: tuple[str, ...]) -> str | None:
    """ "Fotoğrafın bilgilerini oku." / "Bu resmin bilgileri ne?" (B32 req 139)."""
    if _has(tokens, *_IMAGE_NOUN_STEMS) is None:
        return None
    if _has(tokens, "bilgi", "özellik", "ozellik", "boyut", "çekim", "cekim") is None:
        return None
    return "fotoğrafın bilgileri"


def _document_read_match(tokens: tuple[str, ...]) -> str | None:
    """ "Bu dosyayı oku." / "Bu belgeyi okur musun?" (spec §3)."""
    if _has_exact(tokens, *_READ_VERB_FORMS) is None:
        return None
    if _has(tokens, *_DOCUMENT_NOUN_STEMS) is None:
        return None
    return "oku"


def _document_summarize_match(tokens: tuple[str, ...], *, document_focused: bool) -> str | None:
    """ "Bunu özetle." (a document already focused) / "Bu belgeyi özetle." / "Bu PDF'i
    özetle." (spec §3). A bare deictic ("bunu") needs a document actually focused - with
    none (and no document noun either), this is not this tool's business at all, and
    falls through to the EXISTING research/narration SUMMARIZE behaviour lower down
    (module docstring's own priority-order discipline: never guess, and never shadow a
    behaviour this milestone was not asked to touch)."""
    if _has(tokens, *_SUMMARIZE_STEMS) is None:
        return None
    if _has(tokens, *_DOCUMENT_NOUN_STEMS) is not None:
        return "özetle"
    if document_focused and _has_exact(tokens, *_DEICTIC_WORDS):
        return "özetle"
    return None


def _document_common_points_match(tokens: tuple[str, ...]) -> str | None:
    """ "Bunların ortak noktalarını çıkar." (spec §3)."""
    if _has(tokens, _COMMON_POINTS_STEM) is None:
        return None
    if _has(tokens, "nokta") is None:
        return None
    return "ortak noktalar"


def _document_inspect_match(tokens: tuple[str, ...]) -> str | None:
    """ "Bu Excel'de ne var?" (what's in it) / "Bu sunumda kaç slayt var?" (a structural
    count) (spec §3) - both answered from ``file.inspect``'s own structure, never from
    retrieval."""
    if _has(tokens, *_DOCUMENT_NOUN_STEMS) is None:
        return None
    if _has_exact(tokens, "ne") and _has_exact(tokens, "var"):
        return "ne var"
    if _has_exact(tokens, "kaç", "kac") and _has_exact(tokens, "var"):
        return "kaç var"
    return None


def _document_answer_named_match(tokens: tuple[str, ...]) -> str | None:
    """ "Üçüncü sayfada ne yazıyor?" - an internal place noun plus a content question verb
    (spec §3); distinct from DOCUMENT_INSPECT's whole-document nouns."""
    if _has(tokens, *_DOCUMENT_PLACE_STEMS) is None:
        return None
    if _has_exact(tokens, "ne") and _has(tokens, *_CONTENT_QUESTION_VERB_STEMS):
        return "ne yazıyor"
    return None


#: The question-shapes that make a bare content question (no document noun, no place
#: noun at all - "Ödeme süresi kaç gün?") a DOCUMENT_ANSWER, but ONLY while a document is
#: actually focused (``document_focused``) and the question is not already one this
#: resolver's own EXPLAIN table recognises (``explain_kind``, computed once in
#: ``resolve_intent`` and passed in here) - a system-activity question must keep meaning
#: exactly what it always meant regardless of what happens to be focused.
_GENERIC_QUESTION_WORDS: Final[tuple[str, ...]] = (
    "mı",
    "mi",
    "mu",
    "mü",
    "kaç",
    "kac",
    "ne",
    "nedir",
    "nasıl",
    "nasil",
    "kim",
)


def _document_answer_generic_match(
    tokens: tuple[str, ...], *, document_focused: bool, explain_kind: str | None
) -> str | None:
    if not document_focused or explain_kind is not None:
        return None
    if _has_exact(tokens, *_GENERIC_QUESTION_WORDS):
        return "soru"
    return None


def _extract_document_folder(tokens: tuple[str, ...]) -> str | None:
    for tok in tokens:
        for alias, folder in _FOLDER_ALIASES.items():
            if tok.startswith(alias):
                return folder
    return None


def _extract_document_extensions(tokens: tuple[str, ...]) -> list[str] | None:
    out: list[str] = []
    for tok in tokens:
        for alias, ext in _EXTENSION_ALIASES.items():
            if tok.startswith(alias) and ext not in out:
                out.append(ext)
    return out or None


#: ``_extract_document_pattern`` (below ``_DEICTIC_WORDS``, which it needs) resolves the
#: search PATTERN a spoken name leaves behind (see ``_SEARCH_PATTERN_SKIP`` further down).


# --------------------------------------------------------- M21: Mail & Calendar
#
# Built on the SAME token/stem primitives as every intent above - no second Turkish
# pattern table (module docstring's own rule). Freeform CONTENT (a draft's body, a new
# mail's subject/recipient, a proposal's summary/time) is never regex-extracted from the
# raw utterance here — the model supplies it as a tool argument (the same "owner's words
# win only for an unambiguous IDENTITY, content stays the model's job" split
# ``research.start``'s own ``topic`` argument and ``alarm.create``'s own ``when_spoken``
# already establish). This block decides only WHICH tool fires and, where the words are
# genuinely unambiguous, WHICH object ("current"/"previous") they point at.

_MAIL_NOUN_STEMS: Final[tuple[str, ...]] = ("mail", "posta", "eposta")
_INBOX_NOUN_STEMS: Final[tuple[str, ...]] = ("kutu",)
_UNREAD_STEMS: Final[tuple[str, ...]] = ("okunmamış", "okunmamis")
_THREAD_NOUN_STEMS: Final[tuple[str, ...]] = ("konuşma", "konusma", "yazışma", "yazisma")
_REPLY_NOUN_STEMS: Final[tuple[str, ...]] = ("cevab", "cevap", "yanıt", "yanit")
_MAIL_WRITE_VERB_FORMS: Final[tuple[str, ...]] = ("yaz", "yazsana", "yazar")
_MAIL_NEW_STEMS: Final[tuple[str, ...]] = ("yeni",)
_MAIL_SEND_VERB_FORMS: Final[tuple[str, ...]] = ("gönder", "gonder", "göndersene", "gondersene")
_MAIL_SEND_NEGATION_FORMS: Final[tuple[str, ...]] = (
    "gönderme",
    "gonderme",
    "göndermeyin",
    "gondermeyin",
)
_DISCARD_STEMS: Final[tuple[str, ...]] = ("vazgeç", "vazgec")
#: BUG FOUND 2026-09-08 (docs/DECISIONS.md ADR-0091, building the Owner Location Context
#: capability): the bare stem "konu" (subject/topic) also matches "konum"/"konumu"/
#: "konumumu" (location) through ``_has``'s prefix rule — "Varsayılan hava durumu
#: konumumu İstanbul yap." was being read as ``_mail_edit_draft_match``'s "konuyu ... yap"
#: shape and never reached LOCATION_DEFAULT_SET at all. Closed by matching the CLOSED set
#: of inflected forms "konu" actually takes as "subject/topic" ("konuyu", "konusu",
#: "konusunu") rather than a 4-letter prefix that also happens to start "konum" and
#: "konuş-" (to speak).
_SUBJECT_NOUN_FORMS: Final[tuple[str, ...]] = ("konu", "konuyu", "konusu", "konusunu")
_SET_SUBJECT_VERB_FORMS: Final[tuple[str, ...]] = ("yap", "yapsana", "yapar")


def _mail_inbox_match(tokens: tuple[str, ...]) -> str | None:
    """ "Gelen kutumda ne var?" / "Okunmamış maillerim var mı?" / "Gelen kutumu kontrol
    eder misin?" (spec §3)."""
    if _has(tokens, *_INBOX_NOUN_STEMS) and _has_exact(tokens, "ne") and _has_exact(tokens, "var"):
        return "kutuda ne var"
    if _has(tokens, *_UNREAD_STEMS) and _has(tokens, *_MAIL_NOUN_STEMS):
        return "okunmamış mail"
    if _has(tokens, *_INBOX_NOUN_STEMS) and _has(tokens, "kontrol"):
        return "kutuyu kontrol et"
    # B27 req 729. "Maillerime bak." / "Mail var mı?" / "Postalarımı kontrol et." — the
    # audit's most-said mail sentence reached nothing because this matcher knew the inbox
    # NOUN and the unread ADJECTIVE but not the plain verbs a person uses for "look".
    # The mail noun is still required: "bak" alone belongs to everybody.
    mail_noun = _has(tokens, *_MAIL_NOUN_STEMS) or _has(tokens, *_INBOX_NOUN_STEMS)
    if mail_noun is None:
        return None
    if _has_exact(tokens, *_MAIL_LOOK_VERB_FORMS):
        return "maillere bak"
    if _has(tokens, "kontrol"):
        return "mailleri kontrol et"
    # "Mail var mı?" / "Yeni mail var mı?": a question about the inbox, never a compose
    # (``_mail_draft_new_match`` refuses questions for the same reason).
    if _has_exact(tokens, "var") and _is_question(tokens):
        return "mail var mı"
    return None


#: The plain verbs of "look at my mail": exact forms, because the stem "bak" is also the
#: first three letters of "bakım" (maintenance) and "bakan" (minister).
_MAIL_LOOK_VERB_FORMS: Final[tuple[str, ...]] = (
    "bak",
    "baksana",
    "bakar",
    "bakabilir",
    "göster",
    "goster",
    "gösterir",
    "gosterir",
    "göstersene",
    "gostersene",
    "listele",
    "listeler",
)


def _mail_search_match(tokens: tuple[str, ...]) -> str | None:
    """ "Fatura maillerini bul." (spec §3) — the same find/search verbs the document
    family already uses (module comment: not a second table, a shared one)."""
    if _has_exact(tokens, *_FIND_VERB_FORMS, *_SEARCH_VERB_FORMS) is None:
        return None
    if _has(tokens, *_MAIL_NOUN_STEMS) is None:
        return None
    return "mail bul"


def _mail_thread_match(tokens: tuple[str, ...]) -> str | None:
    """ "Bu konuşmanın tamamını oku." (spec §3) — checked before the generic mail READ so
    a thread noun always wins its own shape."""
    if _has(tokens, *_THREAD_NOUN_STEMS) is None:
        return None
    if _has_exact(tokens, *_READ_VERB_FORMS) is None:
        return None
    return "konuşmayı oku"


def _mail_read_draft_match(tokens: tuple[str, ...]) -> str | None:
    """ "Cevabı oku." (spec §3) — the CURRENT DRAFT, never a mailbox message: the reply
    noun with no mail noun alongside it names the thing just prepared, not something to
    search for."""
    if _has(tokens, *_REPLY_NOUN_STEMS) is None:
        return None
    if _has_exact(tokens, *_READ_VERB_FORMS) is None:
        return None
    return "cevabı oku"


#: B45 (req 347, 348): the words for a message's attachments - EXACT forms, because the
#: stem "ek" is also "ekle" (add), "ekip" (team) and "ekran" (screen).
_MAIL_ATTACHMENT_WORDS: Final[tuple[str, ...]] = (
    "ek",
    "eki",
    "ekini",
    "ekler",
    "ekleri",
    "eklerini",
    "ekte",
    "ekteki",
    "ektekini",
    "ektekileri",
)
_MAIL_ATTACHMENT_SAVE_VERBS: Final[tuple[str, ...]] = (
    "kaydet",
    "kaydeder",
    "kaydetsene",
    "indir",
    "indirir",
    "indirsene",
)


def _mail_save_attachment_match(tokens: tuple[str, ...]) -> str | None:
    """ "Eki bilgisayarıma kaydet." / "Ekteki dosyayı indir." (B45 req 348)."""
    if _has_exact(tokens, *_MAIL_ATTACHMENT_WORDS) is None:
        return None
    if _has_exact(tokens, *_MAIL_ATTACHMENT_SAVE_VERBS) is None:
        return None
    return "eki kaydet"


def _mail_attachments_match(tokens: tuple[str, ...]) -> str | None:
    """ "Bu mailin eklerini göster." / "Ekte ne var?" (B45 req 347) - an attachment word with
    a show/what word or the mail noun, and no save verb (that is the save form)."""
    if _has_exact(tokens, *_MAIL_ATTACHMENT_WORDS) is None:
        return None
    if _has_exact(tokens, *_MAIL_ATTACHMENT_SAVE_VERBS) is not None:
        return None
    if (
        _has_exact(tokens, "göster", "goster", "listele", "ne", "neler", "var", "say") is None
        and _has(tokens, *_MAIL_NOUN_STEMS) is None
    ):
        return None
    return "ekleri göster"


def _mail_read_match(tokens: tuple[str, ...]) -> str | None:
    """ "Ali'den gelen son maili oku." (spec §3) — checked after thread/read_draft so
    those more specific nouns win first."""
    if _has_exact(tokens, *_READ_VERB_FORMS) is None:
        return None
    if _has(tokens, *_MAIL_NOUN_STEMS) is None:
        return None
    return "maili oku"


def _mail_draft_reply_match(tokens: tuple[str, ...]) -> str | None:
    """ "Buna cevap yaz: yarın 10'da uygunum." (spec §3) — the CURRENT message, always
    (module comment: the body text itself is the model's own argument)."""
    if _has(tokens, *_REPLY_NOUN_STEMS) is None:
        return None
    if _has(tokens, *_MAIL_WRITE_VERB_FORMS) is None:
        return None
    return "cevap yaz"


def _mail_draft_new_match(tokens: tuple[str, ...]) -> str | None:
    """ "Yeni mail: Ayşe'ye, konu toplantı, yarın gelemiyorum." / "Ali'ye mail gönder."
    (spec §3) — the second shape names the mail noun ALONGSIDE "gönder" with nothing
    prepared yet, which is a request to COMPOSE, never a confirmation to send something
    that does not exist (``_mail_send_match``'s own docstring: a bare "Gönder." never
    names the mail noun at all, which is what keeps the two shapes apart)."""
    if _has(tokens, *_MAIL_NOUN_STEMS) is None:
        return None
    # B27 (found measuring req 729): "Yeni mail var mı?" is a QUESTION about the inbox and
    # was being read as a request to compose a new mail. A question never composes.
    if _is_question(tokens):
        return None
    if _has(tokens, *_MAIL_NEW_STEMS):
        return "yeni mail"
    if _has_exact(tokens, *_MAIL_SEND_VERB_FORMS):
        return "mail gönder"
    return None


def _mail_edit_draft_match(tokens: tuple[str, ...]) -> str | None:
    """ "Konuyu 'Plan onayı' yap." (spec §3) — the new subject text is the model's own
    argument; this only recognises the SHAPE. Matches exact inflected forms of "konu"
    (never a bare prefix — see ``_SUBJECT_NOUN_FORMS``'s own comment: a prefix also
    matches "konum" / "konuş-")."""
    if _has_exact(tokens, *_SUBJECT_NOUN_FORMS) is None:
        return None
    if _has_exact(tokens, *_SET_SUBJECT_VERB_FORMS) is None:
        return None
    return "konuyu değiştir"


#: B26 req 736. The words that may keep a bare confirmation company: a pointer at the thing
#: just read back, politeness, and discourse. Anything else in the sentence NAMES something,
#: and a sentence that names something is about that thing rather than about the draft.
_BARE_CONFIRMATION_WORDS: Final[frozenset[str]] = frozenset(
    {
        "bunu",
        "şunu",
        "sunu",
        "onu",
        "bu",
        "şu",
        "su",
        "o",
        "lütfen",
        "lutfen",
        "rica",
        "ederim",
        "hadi",
        "haydi",
        "tamam",
        "peki",
        "evet",
        "şimdi",
        "simdi",
        "artık",
        "artik",
    }
)


def _is_bare(tokens: tuple[str, ...], verb: str) -> bool:
    """True when the sentence is the VERB and nothing that names a thing.

    B26 req 736: "Gönder." is the owner confirming a draft that was just read back to them.
    "Dosyayı gönder." is about a file — and the audit measured it reaching `mail.send`,
    which is the one action in this family that cannot be taken back. The old guard only
    refused the mail noun itself, so every OTHER noun sailed through.
    """
    for token in tokens:
        if token.startswith(verb):
            continue
        if token not in _BARE_CONFIRMATION_WORDS:
            return False
    return True


def _mail_send_match(tokens: tuple[str, ...]) -> str | None:
    """ "Gönder." (spec §3) — a BARE confirmation never names the mail noun itself (the
    owner does not say "maili gönder" to confirm what was just read back to them; that
    shape is ``_mail_draft_new_match``'s own "Ali'ye mail gönder", a fresh compose
    request). Matches on vocabulary alone otherwise — with nothing prepared, the tool
    this names still runs and answers with an honest clarification from its own service
    layer (module comment above resolve_intent's own M21 block), never a guess here."""
    if _has_exact(tokens, *_MAIL_SEND_NEGATION_FORMS):
        return None
    if _has(tokens, *_MAIL_NOUN_STEMS):
        return None
    matched = _has_exact(tokens, *_MAIL_SEND_VERB_FORMS)
    if matched is None:
        return None
    # req 736: a confirmation is BARE. Anything else in the sentence names a thing, and
    # this family's confirmation never names anything (see `_is_bare`).
    return matched if _is_bare(tokens, "gönder") or _is_bare(tokens, "gonder") else None


def _mail_send_negation_match(tokens: tuple[str, ...]) -> str | None:
    """ "Gönderme." (spec §3) — the mail-specific half of DISCARD; checked ahead of the
    generic ``vazgeç`` so a bare "gönderme" always names the mail draft, never a proposal
    that happens to also be pending."""
    return _has_exact(tokens, *_MAIL_SEND_NEGATION_FORMS)


def _discard_word_match(tokens: tuple[str, ...]) -> str | None:
    """ "Vazgeç." (spec §3) — ambiguous between a pending draft and a pending proposal;
    ``resolve_intent`` decides which object from ``draft_pending``/``proposal_pending``."""
    if _mail_send_negation_match(tokens):
        return "gönderme"
    return _has(tokens, *_DISCARD_STEMS)


# ---------------------------------------------------------------------------------------
# ADR-0091: Owner Location Context, Live Weather, Morning Briefing. A small BUILT-IN
# gazetteer, never a geocoding call at the router layer (the resolver's own tier 1 needs
# only the WORD the owner said; ``app.location.service`` and ``app.weather.providers``
# do any real geocoding). Every city carries three stems where its name contains a
# capital-I-sensitive letter — the SAME "bare / diacritic / diacritic-stripped ASR
# variant" lesson ADR-0089 addendum 1 already paid for with "taslağı": "İstanbul"
# casefolds (``turkish_casefold``) to "istanbul", but an ASR transcript that typed the
# ASCII capital "I" instead of the Turkish dotted "İ" casefolds to "ıstanbul" instead —
# a silent miss neither form alone would catch.
_CITY_STEMS: Final[dict[str, str]] = {
    "istanbul": "İstanbul",
    "ıstanbul": "İstanbul",
    "ankara": "Ankara",
    "izmir": "İzmir",
    "ızmir": "İzmir",
    "bursa": "Bursa",
    "antalya": "Antalya",
    "adana": "Adana",
    "konya": "Konya",
    "gaziantep": "Gaziantep",
    "trabzon": "Trabzon",
    "eskişehir": "Eskişehir",
    "eskisehir": "Eskişehir",
}


def _extract_place(tokens: tuple[str, ...]) -> str | None:
    """The first known city the owner's WORDS named (task brief's resolution tier 1),
    or None — a best-effort, closed-vocabulary lookup, never a guess at an unlisted
    place (an unrecognised city name falls through to ``app.location``'s own tiers, the
    same "the tool then asks / falls back, never invents" rule every other extractor in
    this module follows)."""
    for tok in tokens:
        bare = tok.rstrip("'")
        for stem, canonical in _CITY_STEMS.items():
            if bare == stem or bare.startswith(stem):
                return canonical
    return None


_WEATHER_NOUN_STEMS: Final[tuple[str, ...]] = ("hava",)
_TEMPERATURE_NOUN_STEMS: Final[tuple[str, ...]] = ("derece", "sıcaklık", "sicaklik")
_PRECIPITATION_NOUN_STEMS: Final[tuple[str, ...]] = (
    "yağmur",
    "yagmur",
    "kar",
    "güneş",
    "gunes",
    "bulut",
)
_WEATHER_QUESTION_FORMS: Final[tuple[str, ...]] = ("nasıl", "nasil")
_HOW_MUCH_FORMS: Final[tuple[str, ...]] = ("kaç", "kac")


def _weather_query_match(tokens: tuple[str, ...]) -> str | None:
    """ "Hava nasıl?" / "İstanbul'da hava nasıl?" / "Şu an bulunduğum yerde kaç derece?" /
    "Ankara'da yarın yağmur var mı?" (task brief §4). The PLACE, if any, is extracted
    separately (``_extract_place``, ``weather_place`` below) — this only decides whether
    the utterance is a weather question at all. Deliberately does not match a bare "hava"
    without one of these question forms, so a statement like "Bugün hava güzel" (the exact
    case this module already documents as a non-briefing, see ``_INTENT_CORROBORATION``'s
    own comment) is never read as a query."""
    if _has(tokens, *_WEATHER_NOUN_STEMS) and _has_exact(tokens, *_WEATHER_QUESTION_FORMS):
        return "hava nasıl"
    if _has(tokens, *_TEMPERATURE_NOUN_STEMS) and _has_exact(tokens, *_HOW_MUCH_FORMS):
        return "kaç derece"
    if _has(tokens, *_PRECIPITATION_NOUN_STEMS) and _has_exact(
        tokens, *_FREE_QUESTION_SUFFIX_FORMS
    ):
        return "yağmur var mı"
    return None


_DEFAULT_LOCATION_STEMS: Final[tuple[str, ...]] = ("varsayılan", "varsayilan")
_LOCATION_NOUN_STEMS: Final[tuple[str, ...]] = ("konum",)
_LOCATION_SET_VERB_FORMS: Final[tuple[str, ...]] = ("yap", "ayarla", "değiştir", "degistir")


def _location_default_set_match(tokens: tuple[str, ...]) -> str | None:
    """ "Varsayılan hava durumu konumumu İstanbul yap." (task brief §4)."""
    if _has(tokens, *_DEFAULT_LOCATION_STEMS) is None:
        return None
    if _has(tokens, *_LOCATION_NOUN_STEMS) is None:
        return None
    if _has_exact(tokens, *_LOCATION_SET_VERB_FORMS) is None:
        return None
    return "varsayılan konum ayarla"


def _location_default_query_match(tokens: tuple[str, ...]) -> str | None:
    """ "Varsayılan konumum ne?" (task brief §4) — checked by the CALLER only after
    ``_location_default_set_match`` fails, so "... İstanbul yap" (which also carries
    "varsayılan"/"konum") is never read as a query for lacking the word "ne"."""
    if _has(tokens, *_DEFAULT_LOCATION_STEMS) is None:
        return None
    if _has(tokens, *_LOCATION_NOUN_STEMS) is None:
        return None
    if _has_exact(tokens, "ne", "hangisi"):
        return "varsayılan konum ne"
    return None


def _location_source_query_match(tokens: tuple[str, ...]) -> str | None:
    """ "Şu an konumumu nereden biliyorsun?" / "Hangi konumu kullanıyorsun?" /
    "Konumum güncel mi?" (task brief §4) — all answered from the SAME evidence
    (``app.weather.service.WeatherService.last_evidence``), task brief §2."""
    if _has(tokens, *_LOCATION_NOUN_STEMS) is None:
        return None
    if _has_exact(tokens, "nereden") and _has(tokens, "bili"):
        return "konum nereden biliyorsun"
    if _has_exact(tokens, "hangi") and _has(tokens, "kullan"):
        return "hangi konum"
    if _has_exact(tokens, "güncel", "guncel"):
        return "konum güncel mi"
    # task brief §2's own example: "Hangi konumun havasını söyledin?" - a location
    # question phrased through the weather word, still about location provenance
    # (weather.last_evidence carries exactly that), never WEATHER_QUERY.
    if _has_exact(tokens, "hangi") and _has(tokens, "hava"):
        return "hangi konumun havası"
    return None


_GREETING_WORDS: Final[tuple[str, ...]] = (
    "günaydın",
    "gunaydin",
    # B51 (747): a transcriber that loses only the dotless i ("Günaydin") - the greeting
    # must not fall to the calendar's "bugün ne var".
    "günaydin",
    "gunaydın",
)
_MORNING_STEMS: Final[tuple[str, ...]] = ("sabah",)
_BRIEFING_NOUN_STEMS: Final[tuple[str, ...]] = ("özet", "ozet")


def _morning_briefing_match(tokens: tuple[str, ...]) -> str | None:
    """ "Günaydın." / "Sabah özetimi ver." / "Bugün beni neler bekliyor?" / "Sabah
    durumunu anlat." (task brief §4) — the combined briefing, distinct from the narrower
    ``_system_status_query_match``/``_overnight_work_query_match`` below (task brief §5:
    "weather vs system status vs combined briefing... deterministic and distinct")."""
    if _has_exact(tokens, *_GREETING_WORDS):
        return "günaydın"
    if (
        _has(tokens, *_MORNING_STEMS)
        and _has(tokens, *_BRIEFING_NOUN_STEMS)
        # B51 (746): "Bana sabah özetini verir misin?" is the same request, and without the
        # polite forms it fell to the bare SUMMARIZE control.
        and _has_exact(tokens, "ver", "verir", "versene", "verin", "verebilir")
    ):
        return "sabah özeti ver"
    if _has(tokens, *_MORNING_STEMS) and _has(tokens, "durum") and _has_exact(tokens, "anlat"):
        return "sabah durumu anlat"
    if _has_exact(tokens, "bugün", "bugun") and _has(tokens, "bekli"):
        return "bugün beni neler bekliyor"
    return None


def _system_status_query_match(tokens: tuple[str, ...]) -> str | None:
    """ "Sistem durumu nasıl?" / "Sistemin durumu ne durumda?" (task brief §4) —
    "sistem" is the disambiguator against the plain "durum" words the M17/M26
    activity-explain vocabulary already claims elsewhere in this resolver. Matched by
    STEM ("sistem"/"sistemin"/"sistemi"), not exact word, the same "Turkish suffixes
    vary" reasoning every other stem table in this module already follows."""
    if _has(tokens, "sistem") is None:
        return None
    # "durumu" (the noun, object of "sistem") is required in ADDITION to a question
    # form - never "durumda" alone, which the pre-existing EXPLAIN/world_state phrase
    # "Sistemin şu anda ne durumda?" already owns (test_voice_intents.py's own
    # contract table) and must keep owning.
    if _has_exact(tokens, "durumu") and _has_exact(tokens, *_WEATHER_QUESTION_FORMS, "nedir"):
        return "sistem durumu nasıl"
    return None


def _overnight_work_query_match(tokens: tuple[str, ...]) -> str | None:
    """ "Gece neler yaptın?" / "Gece boyunca ne yaptın?" (task brief §4) — "ne"/"neler"
    both accepted (a bare "ne" is otherwise too generic on its own, but paired with
    "gece" AND a "yap" verb it names nothing this resolver already claims elsewhere)."""
    if _has(tokens, "gece") is None:
        return None
    if _has_exact(tokens, "ne", "neler") and _has(tokens, "yap"):
        return "gece neler yaptın"
    return None


#: M21 calendar vocabulary. ``_CALENDAR_NOUN_STEMS`` deliberately excludes the alarm's own
#: "alarm" noun (a different family, spec §1's own words: "no delete, no move, no mass
#: action") and the "ertele" verb is handled entirely inside ``_alarm_match`` (module
#: comment there) rather than duplicated here.
_CALENDAR_NOUN_STEMS: Final[tuple[str, ...]] = ("takvim", "ajanda")
_AGENDA_QUESTION_WORDS: Final[tuple[str, ...]] = ("ne", "var")
_FREE_QUESTION_FORMS: Final[tuple[str, ...]] = ("boş", "bos")
_SLOT_NOUN_STEMS: Final[tuple[str, ...]] = ("boşluk", "bosluk", "müsaitlik", "musaitlik")
_CALENDAR_ADD_VERB_FORMS: Final[tuple[str, ...]] = ("ekle", "eklesene", "koy", "koysana")
#: B26 req 739. What makes a sentence with "ekle" a CALENDAR sentence: a day, a calendar
#: word, or the name of a thing one keeps appointments for. Without one of these the verb
#: is just "add", and everything gets added to something — the audit measured "Bir hedef
#: ekle: bu ay kitabı bitir." (a goal for the month) becoming a dated calendar entry.
_WEEKDAY_STEMS: Final[tuple[str, ...]] = (
    "pazartesi",
    "salı",
    "sali",
    "çarşamba",
    "carsamba",
    "perşembe",
    "persembe",
    "cuma",
    "cumartesi",
    "pazar",
)
_RELATIVE_DAY_FORMS: Final[tuple[str, ...]] = (
    "yarın",
    "yarin",
    "yarına",
    "yarina",
    "bugün",
    "bugun",
    "öbür",
    "obur",
    "haftaya",
)
#: The nouns one actually keeps in a calendar. "ay" (month) is deliberately NOT here: it is
#: the word that let the measured misroute through, and a month is a span, not an
#: appointment.
_APPOINTMENT_STEMS: Final[tuple[str, ...]] = (
    "toplantı",
    "toplanti",
    "randevu",
    "görüşme",
    "gorusme",
    "duruşma",
    "durusma",
    "etkinlik",
    "seans",
    "mülakat",
    "mulakat",
)


#: B46 (req 356): "her gün / her hafta / her ay / hafta içi" makes "ekle" an appointment even
#: with no day or calendar word ("Hafta içi her gün 9'da stand-up ekle.") - unless the sentence
#: names a list, a note, a goal or a task, which own their own "her gün ... ekle".
_RECURRENCE_UNIT_FORMS: Final[tuple[str, ...]] = (
    "gün",
    "gun",
    "hafta",
    "ay",
    "ayın",
    "ayin",
    "yıl",
    "yil",
    "sabah",
    "akşam",
    "aksam",
)
_RECURRENCE_ADJECTIVE_FORMS: Final[tuple[str, ...]] = (
    "haftalık",
    "haftalik",
    "aylık",
    "aylik",
    "yıllık",
    "yillik",
)
_RECURRENCE_FOREIGN_STEMS: Final[tuple[str, ...]] = (
    "liste",
    "not",
    "hedef",
    "alışveriş",
    "alisveris",
    "görev",
    "gorev",
)


def _recurrence_anchor(tokens: tuple[str, ...]) -> str | None:
    if _has(tokens, *_RECURRENCE_FOREIGN_STEMS) is not None:
        return None
    if _has_exact(tokens, "her") is not None and _has_exact(tokens, *_RECURRENCE_UNIT_FORMS):
        return "her"
    if _has(tokens, "hafta") is not None and _has(tokens, "içi", "ici", "içleri", "icleri"):
        return "hafta içi"
    if _has_exact(tokens, *_RECURRENCE_ADJECTIVE_FORMS) is not None:
        return "tekrar"
    return None


def _calendar_anchor(tokens: tuple[str, ...]) -> str | None:
    """What makes this an appointment rather than an addition (B26 req 739)."""
    return (
        _has(tokens, *_CALENDAR_NOUN_STEMS)
        or _has(tokens, *_WEEKDAY_STEMS)
        or _has_exact(tokens, *_RELATIVE_DAY_FORMS)
        or _has(tokens, *_APPOINTMENT_STEMS)
        or _recurrence_anchor(tokens)
    )


_CALENDAR_RESCHEDULE_VERB_STEMS: Final[tuple[str, ...]] = ("ertele",)
_CALENDAR_APPROVE_FORMS: Final[tuple[str, ...]] = ("onayla", "onaylıyorum", "onayliyorum")
_CALENDAR_APPROVE_OK_FORMS: Final[tuple[str, ...]] = ("tamam",)


#: B27 req 730. The spans a person asks a calendar about without naming the calendar:
#: "Bu hafta ne var?" / "Yarın ne var?" / "Bugün programım ne?". A week word is an anchor
#: of its own here (it is not one for ``_calendar_anchor``: "ekle" + "hafta" is still not
#: an appointment), and "program" is the owner's other word for their agenda.
_WEEK_FORMS: Final[tuple[str, ...]] = ("hafta", "haftaya", "haftalık", "haftalik")
_AGENDA_NOUN_STEMS: Final[tuple[str, ...]] = ("program",)
_AGENDA_TELL_VERB_FORMS: Final[tuple[str, ...]] = ("söyle", "soyle", "oku", "göster", "goster")


def _agenda_span(tokens: tuple[str, ...]) -> str | None:
    """The day or week a "ne var" question is about, when it names one."""
    return (
        _has_exact(tokens, *_RELATIVE_DAY_FORMS)
        or _has(tokens, *_WEEK_FORMS)
        or _has(tokens, *_WEEKDAY_STEMS)
    )


def _calendar_agenda_match(tokens: tuple[str, ...]) -> str | None:
    """ "Bugün takvimimde ne var?" (spec §3); B27 req 730: "Bu hafta ne var?" / "Yarın ne
    var?" / "Bugün programım ne?" / "Haftalık programımı söyle." — the calendar noun is no
    longer required when the sentence names a DAY or a WEEK, because that is how the
    question is actually asked. Placed where it always was (after news and documents, so
    "haberlerde ne var" and "belgede ne var" keep their families) and before the mail
    block, whose own "kutuda ne var" carries no day."""
    asks_what = _has_exact(tokens, "ne") and _has_exact(tokens, "var")
    if _has(tokens, *_CALENDAR_NOUN_STEMS):
        if asks_what:
            return "takvimde ne var"
        return None
    # "Günaydın, bugün ne var?" is the MORNING BRIEFING's (ADR-0091), which is answered
    # further down the ladder and covers the calendar among other things: the greeting
    # decides, and this matcher steps aside for it.
    if asks_what and _agenda_span(tokens) is not None and not _has_exact(tokens, *_GREETING_WORDS):
        return "bu hafta ne var"
    if _has(tokens, *_AGENDA_NOUN_STEMS) and (
        _has_exact(tokens, "ne", "nedir", "neymiş") or _has_exact(tokens, *_AGENDA_TELL_VERB_FORMS)
    ):
        return "programım ne"
    return None


#: B27 req 731. "Toplantıyı iptal et." / "Perşembeki toplantıyı iptal et." / "Randevuyu
#: sil." — an appointment as the OBJECT of a cancel verb, or the calendar itself. The
#: accusative forms and not the stems: "toplantı notlarını sil" deletes notes, not a
#: meeting, and a stem match on "toplantı" would have admitted it (ADR-0133: one word is
#: not a sentence). The alarm and routine families take their own nouns first
#: (resolve_intent's own ordering), so "alarmı iptal et" never reaches here; and
#: "araştırmayı iptal et" is the research family's, checked just before this one.
_APPOINTMENT_OBJECT_FORMS: Final[tuple[str, ...]] = (
    "toplantıyı",
    "toplantiyi",
    "toplantımı",
    "toplantimi",
    "randevuyu",
    "randevumu",
    "görüşmeyi",
    "gorusmeyi",
    "görüşmemi",
    "gorusmemi",
    "duruşmayı",
    "durusmayi",
    "etkinliği",
    "etkinligi",
    "seansı",
    "seansi",
    "mülakatı",
    "mulakati",
)
_CALENDAR_CANCEL_VERB_FORMS: Final[tuple[str, ...]] = ("sil", "silsene", "kaldır", "kaldir")


def _calendar_cancel_match(tokens: tuple[str, ...]) -> str | None:
    if (
        _has_exact(tokens, *_APPOINTMENT_OBJECT_FORMS) is None
        and _has(tokens, *_CALENDAR_NOUN_STEMS) is None
    ):
        return None
    # Turkish negation: "iptal etme" is "do NOT cancel".
    if _has_exact(tokens, "etme", "etmeyin", "silme", "silmeyin", "kaldırma", "kaldirma"):
        return None
    if _has_exact(tokens, "iptal"):
        return "toplantıyı iptal et"
    if _has_exact(tokens, *_CALENDAR_CANCEL_VERB_FORMS):
        return "toplantıyı sil"
    return None


# ------------------------------------------------------- B27: the everyday ten (726-735)
#
# The audit's own words: 103 plausible sentences, 59 reached nothing. The ten below are the
# ones a person says every day, and each is matched on its OWN noun plus a verb - the same
# token/stem primitives as every intent above, and the same rule ADR-0133 drew: a sentence
# is admitted by a noun AND a verb, never by one word.

#: req 734. The second-person forms of "what can you do", exact. Never the stem "yap"
#: (OPERATOR_STATUS's "ne yapıyorsun" and EXEC_STATUS's share it), never "yapabilir" alone
#: ("Bunu yapabilir misin?" asks for a thing, not for a list), and never "biliyorsun"
#: (the memory family's recall verb: "kahve hakkında ne biliyorsun").
_CAN_DO_VERB_FORMS: Final[tuple[str, ...]] = (
    "yapabilirsin",
    "yapabilirsiniz",
    "yapabiliyorsun",
    "yapabiliyorsunuz",
    "yapabildiklerin",
    "yapabildiklerini",
)
_HELP_NOUN_STEMS: Final[tuple[str, ...]] = ("yardım", "yardim")
_HELP_VERB_FORMS: Final[tuple[str, ...]] = (
    "olabilirsin",
    "olabilirsiniz",
    "edebilirsin",
    "edebilirsiniz",
    "olursun",
    "edersin",
)
#: YOUR abilities, second-person possessive and exact: the bare stem "yetenek" is Capability
#: Genesis's own noun ("Yetenek durumu ne?" is CAPABILITY_STATUS, M24), and "yeteneklerin
#: neler" is the only shape that asks the assistant about itself.
_ABILITY_NOUN_FORMS: Final[tuple[str, ...]] = (
    "yeteneklerin",
    "yeteneklerini",
    "yeteneklerinin",
    "marifetlerin",
    "marifetlerini",
    "becerilerin",
    "becerilerini",
    "hünerlerin",
    "hunerlerin",
)
_SAY_TO_YOU_FORMS: Final[tuple[str, ...]] = (
    "diyebilirim",
    "söyleyebilirim",
    "soyleyebilirim",
    "isteyebilirim",
    "sorabilirim",
)
_UNDERSTAND_FORMS: Final[tuple[str, ...]] = ("anlıyorsun", "anliyorsun", "anlarsın", "anlarsin")
_WHAT_FORMS: Final[tuple[str, ...]] = ("ne", "neler", "nelerden", "nedir", "nelerdir", "hangi")
#: The Capability Genesis verbs (M24): "yeni bir yetenek edin" is a request to GROW, and
#: "yeteneklerin neler" a request to LIST. Same noun; the verb decides.
_GENESIS_VERB_STEMS: Final[tuple[str, ...]] = (
    "iste",
    "edin",
    "ekle",
    "kazan",
    "öğren",
    "ogren",
    "geliştir",
    "gelistir",
    "talep",
)


def _capabilities_query_match(tokens: tuple[str, ...]) -> str | None:
    """ "Neler yapabilirsin?" / "Ne yapabiliyorsun?" / "Yeteneklerin neler?" / "Hangi
    konularda yardımcı olabilirsin?" / "Sana ne diyebilirim?" / "Nelerden anlıyorsun?"."""
    if _has_exact(tokens, *_CAN_DO_VERB_FORMS):
        return "neler yapabilirsin"
    if _has(tokens, *_HELP_NOUN_STEMS) and _has_exact(tokens, *_HELP_VERB_FORMS):
        return "yardımcı olabilirsin"
    if (
        _has_exact(tokens, *_ABILITY_NOUN_FORMS)
        and _has_exact(tokens, *_WHAT_FORMS)
        and _has(tokens, *_GENESIS_VERB_STEMS) is None
    ):
        return "yeteneklerin neler"
    if _has_exact(tokens, *_SAY_TO_YOU_FORMS) and _has_exact(tokens, *_WHAT_FORMS):
        return "sana ne diyebilirim"
    if _has_exact(tokens, *_UNDERSTAND_FORMS) and _has_exact(tokens, *_WHAT_FORMS):
        return "nelerden anlıyorsun"
    return None


#: req 734: the area the owner named, as the family KEY ``app.voice.capabilities`` groups
#: tools by (``FAMILY_TR``'s keys). Literals here rather than an import: this module is
#: imported by everything under app/voice/ and ``capabilities`` reads the tool registry,
#: which imports this module back. ``tests/unit/test_intent_daily_coverage.py`` reads the
#: other side and fails if a key here is not a family there.
_CAPABILITY_FAMILY_STEMS: Final[tuple[tuple[tuple[str, ...], str], ...]] = (
    (("mail", "posta", "eposta", "e-posta"), "mail"),
    (("takvim", "ajanda", "randevu", "toplantı", "toplanti"), "calendar"),
    (("alarm", "uyandır", "uyandir"), "alarm"),
    (("hafıza", "hafiza", "bellek", "hatırla", "hatirla"), "memory"),
    (("araştır", "arastir"), "research"),
    (("haber",), "news"),
    (("hava",), "weather"),
    (("rutin",), "routine"),
    (("belge", "doküman", "dokuman"), "document"),
    (("dosya",), "file"),
    (("ekran", "monitör", "monitor"), "display"),
    (("kamera", "göz", "goz"), "eye"),
    (("müzik", "muzik", "şarkı", "sarki", "video", "medya"), "media"),
    (("uygulama",), "app"),
    (("pencere", "bilgisayar", "klavye", "masaüstü", "masaustu"), "operator"),
    (("saat",), "clock"),
    (("konum",), "location"),
    (("telaffuz",), "pronunciation"),
    (("sahne", "üç boyut", "3b"), "scene"),
    (("sürüm", "surum"), "release"),
    (("brifing",), "briefing"),
    (("seslendir", "anlatım", "anlatim"), "narration"),
    (("çizim", "cizim", "görsel", "gorsel", "resim"), "creative"),
)


def _capability_family(tokens: tuple[str, ...]) -> str | None:
    """The one family the question names, or None for the whole question."""
    for stems, family in _CAPABILITY_FAMILY_STEMS:
        if _has(tokens, *stems):
            return family
    return None


#: req 732. The research NOUN ("araştırma", "araştırmayı", "araştırmadan"), never the
#: verb stem "araştır" (that is how a research STARTS). "Araştırmayı yeniden yap." has no
#: cancel verb and stays the retry it always was.
_RESEARCH_NOUN_STEMS: Final[tuple[str, ...]] = ("araştırma", "arastirma")
_RESEARCH_CANCEL_STOP_FORMS: Final[tuple[str, ...]] = ("durdur", "dur", "bırak", "birak", "kes")
_RESEARCH_CANCEL_NEGATION_FORMS: Final[tuple[str, ...]] = (
    "etme",
    "etmeyin",
    "durdurma",
    "durdurmayın",
    "bırakma",
    "birakma",
)


def _research_cancel_match(tokens: tuple[str, ...]) -> str | None:
    """ "Araştırmayı iptal et." / "Araştırmayı durdur." / "Araştırmayı bırak." /
    "Araştırmadan vazgeç." — the research named with a cancel verb. BEFORE the generic
    stop: "durdur" is a STOP token, and with the research named it is about the research.
    """
    if _has(tokens, *_RESEARCH_NOUN_STEMS) is None:
        return None
    # Turkish negation: "iptal etme" / "durdurma" is "do NOT".
    if _has_exact(tokens, *_RESEARCH_CANCEL_NEGATION_FORMS):
        return None
    if _has_exact(tokens, "iptal"):
        return "araştırmayı iptal et"
    if _has_exact(tokens, *_RESEARCH_CANCEL_STOP_FORMS):
        return "araştırmayı durdur"
    if _has(tokens, *_DISCARD_STEMS):
        return "araştırmadan vazgeç"
    return None


#: req 733. The VOLUME noun, exact: "ses" is also the first three letters of "seslendir"
#: (narrate) and "sesli" (spoken), neither of which is a volume. "seviye" alone is
#: nothing; "ses seviyesi" is the noun.
_VOLUME_NOUN_FORMS: Final[tuple[str, ...]] = (
    "ses",
    "sesi",
    "sesini",
    "sesin",
    "sesinin",
    "sesim",
    "sesimi",
    "volüm",
    "volümü",
    "volum",
    "volumu",
    "volume",
)
_VOLUME_DOWN_FORMS: Final[tuple[str, ...]] = (
    "kıs",
    "kis",
    "kıssana",
    "kissana",
    "kısar",
    "kisar",
    "alçalt",
    "alcalt",
    "azalt",
    "düşür",
    "dusur",
    "indir",
)
_VOLUME_UP_FORMS: Final[tuple[str, ...]] = (
    "aç",
    "ac",
    "açsana",
    "acsana",
    "açar",
    "acar",
    "yükselt",
    "yukselt",
    "artır",
    "artir",
    "arttır",
    "arttir",
)
_VOLUME_MUTE_FORMS: Final[tuple[str, ...]] = ("kapat", "kapatsana", "sustur", "kes")
_SILENT_STEMS: Final[tuple[str, ...]] = ("sessiz",)
_SILENT_VERB_FORMS: Final[tuple[str, ...]] = ("al", "alsana", "geç", "gec", "geçsene", "gecsene")

VOLUME_DOWN: Final = "down"
VOLUME_UP: Final = "up"
VOLUME_MUTE: Final = "mute"


def _media_volume_match(tokens: tuple[str, ...]) -> tuple[str, str] | None:
    """ "Sesini kıs." / "Sesi biraz aç." / "Sesini yükselt." / "Sesi kapat." / "Sessize
    al." -> (direction, matched). The alarm family is resolved before this one, so
    "alarmın sesini kapat" is the alarm's; the display family too, so "ekranı aç" never
    gets here at all."""
    if _has(tokens, *_SILENT_STEMS) and _has_exact(tokens, *_SILENT_VERB_FORMS):
        return VOLUME_MUTE, "sessize al"
    if _has_exact(tokens, *_VOLUME_NOUN_FORMS) is None:
        return None
    if _has_exact(tokens, *_VOLUME_DOWN_FORMS):
        return VOLUME_DOWN, "sesi kıs"
    if _has_exact(tokens, *_VOLUME_UP_FORMS):
        return VOLUME_UP, "sesi aç"
    if _has_exact(tokens, *_VOLUME_MUTE_FORMS):
        return VOLUME_MUTE, "sesi kapat"
    return None


#: req 735. "Ekran görüntüsü al." / "Ekranın görüntüsünü al." / "Screenshot al." / "Ekranı
#: yakala." / "Ekranın fotoğrafını çek." The screen noun in every shape the display family
#: knows plus the genitive ("ekranın") that family has no use for.
_SCREEN_GENITIVE_FORMS: Final[tuple[str, ...]] = ("ekranın", "ekranin", "ekranımın", "ekranimin")
_SCREENSHOT_WORD_FORMS: Final[tuple[str, ...]] = (
    "screenshot",
    "screenshotu",
    "screenshotunu",
    "skrinşat",
    "ss",
)
_CAPTURE_VERB_FORMS: Final[tuple[str, ...]] = (
    "al",
    "alsana",
    "alır",
    "alir",
    "alabilir",
    "çek",
    "cek",
    "çeksene",
    "ceksene",
    "kaydet",
)
_PHOTO_STEMS: Final[tuple[str, ...]] = ("fotoğraf", "fotograf", "foto")
_GRAB_VERB_FORMS: Final[tuple[str, ...]] = ("yakala", "yakalasana")


def _screenshot_match(tokens: tuple[str, ...]) -> str | None:
    if _has_exact(tokens, *_SCREENSHOT_WORD_FORMS) and _has_exact(tokens, *_CAPTURE_VERB_FORMS):
        return "screenshot al"
    if _screen_noun(tokens) is None and _has_exact(tokens, *_SCREEN_GENITIVE_FORMS) is None:
        return None
    if _has(tokens, "görüntü", "goruntu") and _has_exact(tokens, *_CAPTURE_VERB_FORMS):
        return "ekran görüntüsü al"
    if _has(tokens, *_PHOTO_STEMS) and _has_exact(tokens, *_CAPTURE_VERB_FORMS):
        return "ekranın fotoğrafını çek"
    if _has_exact(tokens, *_GRAB_VERB_FORMS):
        return "ekranı yakala"
    return None


#: "boş muyum?" / "boş musun?" — the first/second-person question suffix glued onto "mu"
#: (never a standalone token the generic ``_is_question`` helper's exact "mı"/"mu" check
#: would catch), plus the plain "boş mu?" spacing an ASR sometimes keeps separate.
_FREE_QUESTION_SUFFIX_FORMS: Final[tuple[str, ...]] = (
    "muyum",
    "musun",
    "mıyım",
    "miyim",
    "mı",
    "mi",
    "mu",
    "mü",
)


def _calendar_find_slot_match(tokens: tuple[str, ...]) -> str | None:
    """ "Cuma 60 dakikalık boşluk bul." / "Yarın öğleden sonra boş muyum?" (spec §3)."""
    if _has(tokens, *_SLOT_NOUN_STEMS) and _has_exact(tokens, *_FIND_VERB_FORMS):
        return "boşluk bul"
    if _has_exact(tokens, *_FREE_QUESTION_FORMS) and _has_exact(
        tokens, *_FREE_QUESTION_SUFFIX_FORMS
    ):
        return "boş muyum"
    return None


def _calendar_propose_match(tokens: tuple[str, ...]) -> str | None:
    """ "Perşembe 15'e diş hekimi ekle." (spec §3) — a NEW event; the summary/time text is
    the model's own argument."""
    if _has_exact(tokens, *_CALENDAR_ADD_VERB_FORMS) is None:
        return None
    # req 739: "ekle" alone is the verb "add", and everything gets added to something. The
    # calendar owns the sentence only when the sentence is about a time or an appointment.
    if _calendar_anchor(tokens) is None:
        return None
    return "etkinlik ekle"


def _calendar_reschedule_match(tokens: tuple[str, ...], *, event_focused: bool) -> str | None:
    """ "Bunu bir saat ertele." (spec §3) — the FOCUSED event, gated on ``event_focused``
    the same way ``_alarm_match`` is gated on ``alarm_ringing`` for the identical word:
    without a calendar event actually focused, "ertele" keeps meaning the alarm snooze it
    always meant (this function is never even reached in that case — see
    ``resolve_intent``'s own ordering)."""
    if not event_focused:
        return None
    if _has(tokens, *_CALENDAR_RESCHEDULE_VERB_STEMS) is None:
        return None
    return "ertele"


def _calendar_read_proposal_match(tokens: tuple[str, ...]) -> str | None:
    """ "Öneriyi oku." (spec §3)."""
    if _has(tokens, "öneri", "oneri") is None:
        return None
    if _has_exact(tokens, *_READ_VERB_FORMS) is None:
        return None
    return "öneriyi oku"


def _calendar_commit_match(tokens: tuple[str, ...]) -> str | None:
    """ "Onayla." / "Tamam, ekle." (spec §3) — gated by the CALLER on ``proposal_pending``
    (a prepared proposal read back this session), the same discipline ``_mail_send_match``
    uses for ``draft_pending``."""
    if _has_exact(tokens, *_CALENDAR_APPROVE_FORMS):
        return "onayla"
    if _has_exact(tokens, *_CALENDAR_APPROVE_OK_FORMS) and _has_exact(
        tokens, *_CALENDAR_ADD_VERB_FORMS
    ):
        return "tamam ekle"
    return None


# --------------------------------------------------------- M22: the Artifact Factory
#
# Built on the SAME token/stem primitives as every intent above — no second Turkish
# pattern table (module docstring's own rule). The router extracts only the
# DETERMINISTIC part (spec §5): which kind word was said, the title text around it, and
# every bare number ("kalem tutar" pairs read the same way the free-text scan already
# reads "yüzde 8 arttı" -> 8.0 in app.artifacts.spec). The cognitive backend fills the
# rest of the ``spec`` argument; this module never builds one.

_ARTIFACT_CREATE_VERB_STEMS: Final[tuple[str, ...]] = (
    "yap",
    "yapsana",
    "yapar",
    "hazırla",
    "hazirla",
    "hazırlar",
    "hazirlar",
    "oluştur",
    "olustur",
    "oluşturur",
    "olusturur",
    "üret",
    "uret",
    "üretir",
    "uretir",
)
#: Interrogative forms of the SAME verb stems ("ürettin", "oluşturdun", "yaptın") — these
#: must never match the create verb (a past-tense question is not an imperative), so
#: ARTIFACT_LIST is checked FIRST and these exact forms are excluded from the create
#: check below by requiring a PRESENT/IMPERATIVE form the interrogative never takes.
_ARTIFACT_OPEN_VERB_FORMS: Final[tuple[str, ...]] = (
    "aç",
    "açsana",
    "açar",
    "açabilir",
    "açıver",
    "ac",
    "acsana",
    "acar",
    "acabilir",
    "aciver",
)
_ARTIFACT_DEICTIC_WORDS: Final[tuple[str, ...]] = ("bunu", "şunu", "sunu", "onu")
_ARTIFACT_PREVIOUS_WORDS: Final[tuple[str, ...]] = ("önceki", "onceki")
_ARTIFACT_LAST_PRODUCED_STEMS: Final[tuple[str, ...]] = (
    "üretti",
    "uretti",
    "oluştur",
    "olustur",
    "yap",
)
_ARTIFACT_FILE_NOUN_STEMS: Final[tuple[str, ...]] = ("dosya", "çıktı", "cikti", "belge")
_ARTIFACT_LIST_QUESTION_WORDS: Final[tuple[str, ...]] = ("neler", "ne", "hangi")
_ARTIFACT_LIST_VERB_FORMS: Final[tuple[str, ...]] = (
    "ürettin",
    "urettin",
    "oluşturdun",
    "olusturdun",
    "yaptın",
    "yaptin",
)
_ARTIFACT_VALIDATE_WORD_STEMS: Final[tuple[str, ...]] = ("doğru", "dogru")
_ARTIFACT_QUESTION_PARTICLES: Final[tuple[str, ...]] = ("mu", "mü", "mı", "mi")

#: kind-noun stem -> ``ArtifactSpec`` kind (module: "tablo"/"excel" -> spreadsheet,
#: "belge"/"word"/"doküman" -> document, "sunum"/"slayt" -> presentation, "liste"/"csv"
#: -> dataset, "sayfa" -> page). Order is the FIRST matching token in the utterance, not
#: this tuple's own order.
_ARTIFACT_KIND_STEMS: Final[tuple[tuple[str, str], ...]] = (
    ("tablo", "spreadsheet"),
    ("excel", "spreadsheet"),
    ("belge", "document"),
    ("dokuman", "document"),
    ("doküman", "document"),
    ("word", "document"),
    ("sunum", "presentation"),
    ("slayt", "presentation"),
    ("liste", "dataset"),
    ("csv", "dataset"),
    ("sayfa", "page"),
)

_ARTIFACT_TITLE_LEADING_FILLERS: Final[frozenset[str]] = frozenset(
    {"bana", "bir", "lütfen", "lutfen", "şunu", "sunu"}
)

_ARTIFACT_TITLE_VERB_RE = re.compile(
    r"(yap\w*|hazırla\w*|hazirla\w*|oluştur\w*|olustur\w*|üret\w*|uret\w*)", re.IGNORECASE
)


def _artifact_kind_from_tokens(tokens: tuple[str, ...]) -> str | None:
    for tok in tokens:
        for stem, kind in _ARTIFACT_KIND_STEMS:
            if tok.startswith(stem):
                return kind
    return None


def _extract_artifact_title(utterance: str) -> str | None:
    """The text between any leading filler and the create verb, minus a trailing kind
    word ("Bana bir bütçe TABLOSU yap" -> "bütçe") - a best-effort convenience the tool
    prefers only when non-empty (module docstring: the model still names its own title,
    and either may win when this finds nothing worth carrying)."""
    if not utterance:
        return None
    head = utterance.split(":", 1)[0]
    match = _ARTIFACT_TITLE_VERB_RE.search(head)
    before = head[: match.start()] if match else head
    before = before.strip(" ,.'\"")
    words = [w for w in before.split() if w]
    while words and turkish_casefold(words[0]) in _ARTIFACT_TITLE_LEADING_FILLERS:
        words.pop(0)
    if words and any(
        turkish_casefold(words[-1]).startswith(stem) for stem, _kind in _ARTIFACT_KIND_STEMS
    ):
        words.pop()
    title = " ".join(words).strip(" ,.'\"")
    return title or None


def _extract_artifact_numbers(utterance: str) -> list[float] | None:
    """Every number the owner's WORDS actually said ("kira 12000, maaş 45000" ->
    [12000.0, 45000.0]) — bare digit runs scanned off the RAW utterance (never the
    filtered token list: a number is a number whatever else surrounds it), the same
    "never invented" closed set ``ArtifactSpec`` itself validates the model's ``spec``
    argument against. ``None`` when the owner said no number at all."""
    if not utterance:
        return None
    found = [float(m.group().replace(",", ".")) for m in _NUMBER_TOKEN_RE.finditer(utterance)]
    return found or None


_NUMBER_TOKEN_RE = re.compile(r"\d+(?:[.,]\d+)?")


_ARTIFACT_EDIT_VERB_STEMS: Final[tuple[str, ...]] = (
    "ekle",
    "değiştir",
    "degistir",
    "çıkar",
    "cikar",
    "kaldır",
    "kaldir",
)
_ARTIFACT_PART_NOUN_STEMS: Final[tuple[str, ...]] = (
    "bölüm",
    "bolum",
    "başlı",
    "basli",
    "slayt",
    "satır",
    "satir",
    "madde",
)
_ARTIFACT_CLONE_VERB_STEMS: Final[tuple[str, ...]] = (
    "kopyala",
    "çoğalt",
    "cogalt",
    "kopyasını",
    "kopyasini",
)
_ARTIFACT_DELETE_VERB_STEMS: Final[tuple[str, ...]] = ("sil",)
_ARTIFACT_COMPARE_STEMS: Final[tuple[str, ...]] = (
    "karşılaştır",
    "karsilastir",
    "kıyasla",
    "kiyasla",
    "fark",
    "değişti",
    "degisti",
)


def _artifact_lifecycle_match(
    tokens: tuple[str, ...], *, artifact_focused: bool
) -> tuple[Intent, str, dict[str, Any]] | None:
    """B42 (req 410-416): edit / clone / delete / compare, gated on an artifact being
    in focus (the caller's one live fact) so a bare "bunu sil" in an empty room keeps its
    old owners (the document family's delete, the memory's forget)."""
    if not artifact_focused:
        return None
    deictic = _has_exact(tokens, *_ARTIFACT_DEICTIC_WORDS) is not None
    file_noun = _has(tokens, *_ARTIFACT_FILE_NOUN_STEMS) is not None
    if (
        _has_exact(tokens, "evet") is not None
        and _has(tokens, *_ARTIFACT_DELETE_VERB_STEMS) is not None
    ):
        return (
            Intent.ARTIFACT_DELETE,
            "evet sil",
            {"artifact_ref": "current", "artifact_confirm": True},
        )
    if (deictic or file_noun) and _has_exact(tokens, *_ARTIFACT_DELETE_VERB_STEMS) is not None:
        return Intent.ARTIFACT_DELETE, "bunu sil", {"artifact_ref": "current"}
    if (deictic or file_noun) and _has(tokens, *_ARTIFACT_CLONE_VERB_STEMS) is not None:
        return Intent.ARTIFACT_CLONE, "bunu kopyala", {"artifact_ref": "current"}
    if _has(tokens, *_ARTIFACT_COMPARE_STEMS) is not None and (
        deictic
        or file_noun
        or _has_exact(tokens, *_ARTIFACT_PREVIOUS_WORDS, "öncekiyle", "oncekiyle", "ikisini", "ne")
    ):
        return Intent.ARTIFACT_COMPARE, "karşılaştır", {"artifact_ref": "current"}
    if (
        (deictic or file_noun)
        and _has(tokens, *_ARTIFACT_PART_NOUN_STEMS) is not None
        and _has(tokens, *_ARTIFACT_EDIT_VERB_STEMS) is not None
    ):
        return Intent.ARTIFACT_EDIT, "bunu düzenle", {"artifact_ref": "current"}
    return None


def _artifact_create_match(tokens: tuple[str, ...]) -> str | None:
    """ "Bana bir bütçe tablosu yap: ...", "Toplantı notlarını Word belgesi yap", "Üç
    slaytlık bir sunum hazırla: ..." (a genuinely new artifact — a kind word plus the
    create verb), and "Bunu PDF yap" (a deictic FORMAT request against an artifact that
    already exists — the deictic word plus the create verb, with no kind word at all;
    ``artifact_kind`` stays None and the model's own ``format`` argument, on whichever
    tool it picks, carries the rest)."""
    if _has_exact(tokens, *_ARTIFACT_CREATE_VERB_STEMS) is None:
        return None
    # The deictic word wins FIRST: "kind" words double as FORMAT words ("Excel"/"Word"
    # name a spreadsheet/document kind AND an xlsx/docx format), and a deictic pronoun
    # is the unambiguous signal that the owner is pointing at something that already
    # exists ("Bunu Excel yap" = convert the current artifact), never a request to
    # build a brand new one from scratch.
    if _has_exact(tokens, *_ARTIFACT_DEICTIC_WORDS):
        return "bunu yap"
    if _artifact_kind_from_tokens(tokens) is not None:
        return "yap"
    return None


def _artifact_list_match(tokens: tuple[str, ...]) -> str | None:
    """ "Neler ürettin?", "Ne oluşturdun?", "Hangi dosyaları yaptın?" (spec §5) — an
    INTERROGATIVE past-tense form of the create verb, checked before
    ``_artifact_create_match`` so the two families' verb forms can never collide (an
    imperative and a question share no token here)."""
    if _has_exact(tokens, *_ARTIFACT_LIST_QUESTION_WORDS) is None:
        return None
    if _has_exact(tokens, *_ARTIFACT_LIST_VERB_FORMS) is None:
        return None
    return "neler ürettin"


def _artifact_validate_match(tokens: tuple[str, ...]) -> str | None:
    """ "Bu dosya doğru mu?", "Rakamlar doğru mu?" (spec §5) — "doğru" plus a question
    particle; never gated on a file noun (the fixture's own examples say "bu dosya" but
    a bare "Doğru mu?" against the current artifact focus is the same question)."""
    if _has(tokens, *_ARTIFACT_VALIDATE_WORD_STEMS) is None:
        return None
    if _has_exact(tokens, *_ARTIFACT_QUESTION_PARTICLES) is None:
        return None
    return "doğru mu"


def _artifact_open_match(tokens: tuple[str, ...]) -> str | None:
    """ "Bunu aç.", "Son ürettiğin dosyayı aç.", "Önceki dosyayı aç." (spec §5) — the
    open verb plus a deictic pronoun, a file noun, or "önceki"/"son ürettiğin" naming
    the artifact by recency; resolves even with nothing ever produced (the router names
    the INTENT, the tool's own focus lookup is what may come back empty — module
    docstring: a clarification is then the honest answer, never a guess)."""
    if _has_exact(tokens, *_ARTIFACT_OPEN_VERB_FORMS) is None:
        return None
    if _has_exact(tokens, *_ARTIFACT_DEICTIC_WORDS):
        return "bunu aç"
    if _has_exact(tokens, *_ARTIFACT_PREVIOUS_WORDS):
        return "önceki aç"
    if _has(tokens, *_ARTIFACT_FILE_NOUN_STEMS) or _has(tokens, *_ARTIFACT_LAST_PRODUCED_STEMS):
        return "dosyayı aç"
    return None


def _artifact_ref_for(matched: str) -> str | None:
    return "previous" if matched == "önceki aç" else None


# ------------------------------------------------------------- M23: the App Factory
#
# Built on the SAME token/stem primitives as every intent above - no second Turkish
# pattern table (module docstring's own rule). Every one of these is gated on the
# "uygulama"/"proje" noun stem (spec §5's own vocabulary), the SAME "every family
# requires its own noun" discipline that keeps WINDOW_CLOSE/ALARM_STOP/DISPLAY_OFF from
# colliding on a bare "kapat" - so nothing here can be reached by an utterance about a
# window, an alarm, a display or an artifact. Template/name literals are spelled here
# directly (never imported from app.appfactory.spec) - the same "no cross-module import
# for a string literal" choice _ARTIFACT_KIND_STEMS already makes for its own kind words.

_APP_NOUN_STEMS: Final[tuple[str, ...]] = ("uygulam", "proje")
_APP_CREATE_VERB_STEMS: Final[tuple[str, ...]] = (
    "yap",
    "yapsana",
    "yapar",
    "oluştur",
    "olustur",
    "oluşturur",
    "olusturur",
    "hazırla",
    "hazirla",
)
_APP_RUN_VERB_FORMS: Final[tuple[str, ...]] = (
    "çalıştır",
    "calistir",
    "çalıştırsana",
    "calistirsana",
    "çalıştırır",
    "calistirir",
    "başlat",
    "baslat",
    "başlatır",
    "baslatir",
    "başlatsana",
    "baslatsana",
)
_APP_TEST_NOUN_STEMS: Final[tuple[str, ...]] = ("test",)
_APP_STOP_VERB_FORMS: Final[tuple[str, ...]] = (
    "durdur",
    "kapat",
    "durdursana",
    "kapatsana",
    "durdurur",
    "kapatır",
    "kapatir",
)
_APP_STATUS_VERB_FORMS: Final[tuple[str, ...]] = ("çalışıyor", "calisiyor")
_APP_OPEN_VERB_FORMS: Final[tuple[str, ...]] = (
    "aç",
    "açsana",
    "açar",
    "ac",
    "acsana",
    "acar",
)
_APP_LIST_QUESTION_WORDS: Final[tuple[str, ...]] = ("hangi", "neler", "ne")
_APP_LIST_VERB_FORMS: Final[tuple[str, ...]] = (
    "yaptın",
    "yaptin",
    "oluşturdun",
    "olusturdun",
    "ürettin",
    "urettin",
)
_APP_QUESTION_PARTICLES: Final[tuple[str, ...]] = ("mu", "mü", "mı", "mi")

_APP_NAME_RE = re.compile(
    r"\bad[ıi]\b\s*:?\s*(.+?)[.!?]?$|\bismi\b\s*:?\s*(.+?)[.!?]?$", re.IGNORECASE
)


def _appfactory_template_from_tokens(tokens: tuple[str, ...]) -> str | None:
    """ "görev takip" -> "task-tracker", "web sayfası" -> "static-page", "komut satırı"/
    "cli" -> "cli-tool" (spec §5's own three built-in templates) - a best-effort
    convenience the tool prefers only when non-empty, never a substitute for the
    model's own ``template`` argument (the same rule ``_artifact_kind_from_tokens``
    already follows)."""
    if _has(tokens, "görev", "gorev") and _has(tokens, "takip"):
        return "task-tracker"
    if _has_exact(tokens, "web") and _has(tokens, "sayfa"):
        return "static-page"
    if (_has(tokens, "komut") and _has(tokens, "satır", "satir")) or _has_exact(tokens, "cli"):
        return "cli-tool"
    return None


def _extract_app_name(utterance: str) -> str | None:
    """The text after "adı"/"ismi" ("... adı Notlarım." -> "Notlarım") - a best-effort
    convenience the tool prefers only when non-empty, the same "owner's words win"
    discipline ``_extract_artifact_title`` already follows. Read off the RAW utterance
    (never the casefolded tokens) so the name keeps the owner's own capitalisation."""
    if not utterance:
        return None
    match = _APP_NAME_RE.search(utterance)
    if not match:
        return None
    name = (match.group(1) or match.group(2) or "").strip(" ,.'\"")
    return name or None


_APP_COMMANDS_RE = re.compile(r"(.+?)\s+komut(?:lar[ıi])?\b", re.IGNORECASE)


def _extract_app_commands(utterance: str) -> list[str] | None:
    """For a "cli-tool" template, every command name the owner's WORDS carried
    ("... yap: selamla ve say komutları." -> ["selamla", "say"]) - read from the
    segment after any leading colon (the same "the part after ':' names the specifics"
    convention artifact title/number extraction already uses), split on "ve"/",", each
    part reduced to a plain identifier (never a substitute for the model's own
    ``commands`` argument - a best-effort convenience only)."""
    if not utterance:
        return None
    segment = utterance.split(":", 1)
    segment = segment[1] if len(segment) > 1 else segment[0]
    match = _APP_COMMANDS_RE.search(segment)
    if not match:
        return None
    parts = re.split(r"\s*,\s*|\s+ve\s+", match.group(1).strip())
    commands: list[str] = []
    for part in parts:
        token = re.sub(r"[^a-zA-Z0-9_-]", "", turkish_casefold(part.strip()))
        if token:
            commands.append(token)
    return commands or None


def _appfactory_list_match(tokens: tuple[str, ...]) -> str | None:
    """ "Hangi uygulamaları yaptın?" (spec §5) - an INTERROGATIVE past-tense form of the
    create verb, checked FIRST so it can never collide with the imperative create match
    (the same "list before create" ordering ``_artifact_list_match`` already documents),
    and before ``_artifact_list_match`` so "hangi ... yaptın" about an APP is never read
    as a question about an artifact."""
    if _has(tokens, *_APP_NOUN_STEMS) is None:
        return None
    if _has_exact(tokens, *_APP_LIST_QUESTION_WORDS) is None:
        return None
    if _has_exact(tokens, *_APP_LIST_VERB_FORMS) is None:
        return None
    return "hangi uygulamaları yaptın"


def _appfactory_status_match(tokens: tuple[str, ...]) -> str | None:
    """ "Uygulama çalışıyor mu?" (spec §5) - the present-continuous form plus a question
    particle; never the imperative "çalıştır" (a different word entirely, so no
    collision with ``_appfactory_run_match``)."""
    if _has(tokens, *_APP_NOUN_STEMS) is None:
        return None
    if _has_exact(tokens, *_APP_STATUS_VERB_FORMS) is None:
        return None
    if _has_exact(tokens, *_APP_QUESTION_PARTICLES) is None:
        return None
    return "uygulama çalışıyor mu"


_APP_FIX_VERB_STEMS: Final[tuple[str, ...]] = ("düzelt", "duzelt", "onar")
_APP_FIX_NOUN_STEMS: Final[tuple[str, ...]] = ("hata", "bug", "sorun")


_APP_LOG_NOUN_STEMS: Final[tuple[str, ...]] = ("günlü", "gunlu", "log")
_APP_PACKAGE_VERB_STEMS: Final[tuple[str, ...]] = ("paketle", "paket")
_APP_RELEASE_NOUN_STEMS: Final[tuple[str, ...]] = ("sürüm", "surum", "paket")
_APP_HISTORY_NOUN_STEMS: Final[tuple[str, ...]] = (
    "geçmiş",
    "gecmis",
    "sürümler",
    "surumler",
    "tarihçe",
    "tarihce",
)
_APP_RESUME_STEMS: Final[tuple[str, ...]] = ("devam", "kaldığımız", "kaldigimiz", "geri dön")
_APP_VERIFY_STEMS: Final[tuple[str, ...]] = ("doğrula", "dogrula", "kontrol et")
_APP_ADD_VERB_STEMS: Final[tuple[str, ...]] = ("ekle", "eklesene", "eklensin", "katıl", "katil")


def _appfactory_lifecycle_match(
    tokens: tuple[str, ...], text: str, *, app_project_focused: bool
) -> tuple[Intent, str, dict[str, Any]] | None:
    """B41 (req 440-452): the words that belong to a GENERATED application while one is
    in focus - gated on ``app_project_focused`` the way the native family is gated on
    ``native_build_focused``, so nothing here fires in an empty room. Checked AFTER the
    native block, which owns the same words while a native build is in focus."""
    if not app_project_focused:
        return None
    app = _has(tokens, *_APP_NOUN_STEMS) is not None
    if (
        app
        and _has(tokens, *_APP_ADD_VERB_STEMS) is not None
        and _has_exact(tokens, *_SELF_REFERENCE_FORMS) is None
    ):
        return Intent.APP_FACTORY_MODIFY, "uygulamaya ekle", {"app_request": text.strip()}
    if app and _has(tokens, *_APP_LOG_NOUN_STEMS) is not None:
        return Intent.APP_FACTORY_LOG, "uygulamanın günlüğünü oku", {}
    if (
        _has(tokens, *_APP_HISTORY_NOUN_STEMS) is not None and (app or _has_exact(tokens, "neler"))
    ) or (
        app
        and _has_exact(tokens, "neler", "ne") is not None
        and _has(tokens, "yapt", "yapıl", "yapil") is not None
    ):
        return Intent.APP_FACTORY_HISTORY, "uygulamanın geçmişi", {}
    if (
        app
        and _has(tokens, *_APP_PACKAGE_VERB_STEMS) is not None
        and _has(tokens, *_APP_RUN_VERB_FORMS) is None
    ):
        return Intent.APP_FACTORY_PACKAGE, "uygulamayı paketle", {}
    if (
        _has(tokens, *_APP_RELEASE_NOUN_STEMS) is not None
        and _has_exact(tokens, *_APP_RUN_VERB_FORMS) is not None
    ):
        return Intent.APP_FACTORY_LAUNCH, "sürümü başlat", {}
    if (app or _has(tokens, "arayüz", "arayuz")) and (
        _has(tokens, *_APP_VERIFY_STEMS) is not None
        or (_has(tokens, "arayüz", "arayuz") is not None and _has_exact(tokens, "test", "kontrol"))
    ):
        return Intent.APP_FACTORY_VERIFY, "uygulamayı doğrula", {}
    if app and _has(tokens, *_APP_RESUME_STEMS) is not None:
        return Intent.APP_FACTORY_RESUME, "uygulamaya devam", {"app_name": _extract_app_name(text)}
    if (
        _has(tokens, *_APP_FIX_NOUN_STEMS) is not None
        and _has(tokens, *_APP_FIX_VERB_STEMS) is not None
        and _has_exact(tokens, *_SELF_REFERENCE_FORMS) is None
    ):
        return Intent.APP_FACTORY_FIX, "bu bug'ı düzelt", {}
    return None


def _appfactory_fix_match(tokens: tuple[str, ...]) -> str | None:
    """ "Testleri düzelt." / "Uygulamadaki hatayı düzelt." (B40 req 435-437) - a fix verb
    with the test noun, or with a fault noun AND the app noun; never a self-reference
    (the self-development queue owns "kendin düzelt") and never a bare "bunu düzelt"."""
    if _has(tokens, *_APP_FIX_VERB_STEMS) is None:
        return None
    if _has_exact(tokens, *_SELF_REFERENCE_FORMS) is not None:
        return None
    if _has(tokens, *_APP_TEST_NOUN_STEMS):
        return "testleri düzelt"
    if _has(tokens, *_APP_FIX_NOUN_STEMS) and _has(tokens, *_APP_NOUN_STEMS):
        return "uygulamadaki hatayı düzelt"
    return None


def _appfactory_test_match(tokens: tuple[str, ...]) -> str | None:
    """ "Testleri çalıştır." (spec §5) - the "test" noun plus a run-shaped verb; gated on
    ``test`` rather than the app noun, since the canonical phrasing names no app at all
    (the durable ``project`` focus resolves which one)."""
    if _has(tokens, *_APP_TEST_NOUN_STEMS) is None:
        return None
    if (
        _has_exact(tokens, *_APP_RUN_VERB_FORMS) is None
        and _has_exact(tokens, *_APP_CREATE_VERB_STEMS) is None
    ):
        return None
    return "testleri çalıştır"


def _appfactory_stop_match(tokens: tuple[str, ...]) -> str | None:
    """ "Uygulamayı durdur." (spec §5) - gated on the app noun so a window/alarm/display
    "kapat" is never claimed here (module comment)."""
    if _has(tokens, *_APP_NOUN_STEMS) is None:
        return None
    if _has_exact(tokens, *_APP_STOP_VERB_FORMS) is None:
        return None
    return "uygulamayı durdur"


def _appfactory_open_match(tokens: tuple[str, ...]) -> str | None:
    """ "Uygulamayı aç." (spec §5) - gated on the app noun, so it can never be reached by
    M19's own APP_OPEN vocabulary (a named allowlisted application, checked earlier and
    requiring a real alias match - "uygulama" resolves to no alias at all)."""
    if _has(tokens, *_APP_NOUN_STEMS) is None:
        return None
    if _has_exact(tokens, *_APP_OPEN_VERB_FORMS) is None:
        return None
    return "uygulamayı aç"


def _appfactory_run_match(tokens: tuple[str, ...]) -> str | None:
    """ "Uygulamayı çalıştır." (spec §5) - gated on the app noun plus a run-shaped verb;
    checked AFTER the test/stop/open/status matches above so none of their own more
    specific vocabulary is ever swallowed by this more general one."""
    if _has(tokens, *_APP_NOUN_STEMS) is None:
        return None
    if _has_exact(tokens, *_APP_RUN_VERB_FORMS) is None:
        return None
    return "uygulamayı çalıştır"


def _appfactory_create_match(tokens: tuple[str, ...]) -> str | None:
    """ "Bana bir görev takip uygulaması yap." / "Komut satırı aracı yap." (spec §5) - the
    app noun OR a template phrase, plus the create verb. A template phrase alone (no
    "uygulama" word at all, e.g. "Komut satırı aracı yap") is enough: the template
    itself is the unambiguous signal, the same way a kind word alone is enough for
    ``_artifact_create_match``."""
    if _has_exact(tokens, *_APP_CREATE_VERB_STEMS) is None:
        return None
    if _has(tokens, *_APP_NOUN_STEMS):
        return "uygulama yap"
    if _appfactory_template_from_tokens(tokens) is not None:
        return "yap"
    return None


# --------------------------------------------------- M24: Capability Genesis
#
# Built on the SAME token/stem primitives as every intent above - no second Turkish
# pattern table (module docstring's own rule). The TARGET is resolved against
# app.genesis.catalogue.GenesisInterfaceCatalogue (empty in production; the fixtures'
# own spoken names in tests/the voice corpus), the SAME "resolve a name against a small
# registry" shape ``_app_open_match``'s own ``resolve_app_alias`` import already uses for
# M19's OS-application allowlist - imported lazily so this module carries no import-time
# dependency on app.genesis. CAPABILITY_REQUEST/STATUS need no gate beyond that catalogue
# lookup (an utterance about anything else names no catalogue phrase at all, so these can
# never fire against the existing corpus, which registers none). CAPABILITY_APPROVE/
# CANCEL overlap real vocabulary two OTHER families already claim UNCONDITIONALLY
# ("onaylıyorum" - CALENDAR_COMMIT; "vazgeç" - DISCARD/EVOLUTION_CANCEL), so they are
# gated on ``genesis_awaiting_approval`` (the CALLER's one live fact, established from
# ``GenesisService.find_awaiting_approval`` the same lazy once-per-request way
# ``draft_pending``/``proposal_pending`` are) and checked BEFORE the M21 mail/calendar
# block below — with the flag false (every case in the existing corpus), neither matcher
# is even consulted and both words fall through to their EXISTING targets unchanged.

#: "yetenek" itself, "yeteneğ" (the k->ğ consonant softening a vowel-initial suffix
#: triggers: "yetenek" + "-in" -> "yeteneğin") and "yeteneg" (the SAME mutated form
#: with its own diacritic stripped by the corpus's own asr_noise variants) —
#: ``_has``'s plain prefix-stem test needs all three spellings here (it cannot know
#: the mutation, and the diacritic strip happens on the mutated form, not the base).
_CAPABILITY_STATUS_NOUN_STEMS: Final[tuple[str, ...]] = ("yetenek", "yeteneğ", "yeteneg")
_CAPABILITY_STATUS_QUESTION_FORMS: Final[tuple[str, ...]] = (
    "durumda",
    "durumu",
    "yapabiliyor",
    "yapabiliyorsun",
    "yapabildin",
)
_CAPABILITY_APPROVE_FORMS: Final[tuple[str, ...]] = ("onaylıyorum", "onayliyorum")
_CAPABILITY_AUTHORIZE_VERB_FORMS: Final[tuple[str, ...]] = (
    "yetkilendir",
    "yetkilendiriyorum",
    "yetkilendirsene",
)
_CAPABILITY_CANCEL_STEMS: Final[tuple[str, ...]] = ("vazgeç", "vazgec")


def _capability_catalogue_entry(tokens: tuple[str, ...]) -> Any | None:
    from app.genesis.catalogue import get_catalogue

    return get_catalogue().resolve(tokens)


def _capability_request_match(
    tokens: tuple[str, ...],
) -> tuple[Any, str, str] | None:
    """ "Sayaç kutusunu bir artır.", "Test lambasını aç.", "Sayaç kaç?" (spec §6) — BOTH
    a known target (the catalogue) AND a known operation (that target's own verb
    aliases) are REQUIRED: a known target with an UNRECOGNISED verb ("Sayaç kutusunu
    sil." — no delete operation exists, spec §7's own negative case) must fall through
    to NONE rather than become a clarification this family never offers."""
    entry = _capability_catalogue_entry(tokens)
    if entry is None:
        return None
    operation_id = entry.resolve_operation(tokens)
    if operation_id is None:
        return None
    return entry, operation_id, "capability request"


def _capability_status_match(tokens: tuple[str, ...]) -> str | None:
    """ "Yeni yetenek ne durumda?", "Onu yapabiliyor musun artık?" (spec §6)."""
    if _has(tokens, *_CAPABILITY_STATUS_NOUN_STEMS):
        if _has_exact(tokens, *_CAPABILITY_STATUS_QUESTION_FORMS):
            return "yetenek ne durumda"
        return None
    if _has_exact(tokens, "artık", "artik") and _has(tokens, "yapabil"):
        return "yapabiliyor musun artık"
    return None


def _capability_approve_match(tokens: tuple[str, ...]) -> str | None:
    """ "Onaylıyorum.", "Bu uygulamayı yetkilendir." (spec §6) — gated by the CALLER on
    ``genesis_awaiting_approval`` (module comment above)."""
    if _has_exact(tokens, *_CAPABILITY_APPROVE_FORMS):
        return "onaylıyorum"
    if _has_exact(tokens, *_CAPABILITY_AUTHORIZE_VERB_FORMS):
        return "yetkilendir"
    return None


def _capability_cancel_match(tokens: tuple[str, ...]) -> str | None:
    """ "Vazgeç, yapma." (spec §6) — gated by the CALLER on ``genesis_awaiting_approval``
    (module comment above)."""
    return _has(tokens, *_CAPABILITY_CANCEL_STEMS)


# --------------------------------------------------------- M25: 3D Creation
#
# Built on the SAME token/stem primitives as every intent above - no second Turkish
# pattern table (module docstring's own rule). Every matcher is gated on its OWN noun
# (sahne / küp / küre / silindir / düzlem / ışık / kamera / render, spec §5's own
# list) - the same "every family requires its own noun" discipline
# ``_APP_NOUN_STEMS``'s own module comment documents - so nothing here can ever be
# reached by an utterance about a window, an alarm, a document, an app or the M18
# Active Eye ("kamerayı kapat" shares the "kamera" noun with SCENE_CAMERA but never
# its verb: EYE_DISABLE needs "kapat", SCENE_CAMERA needs "çevir"/"yönlendir", and
# EYE_DISABLE is checked first regardless, priority 0). The TOOL WORD ("blender" /
# "unity") is resolved once, from the SAME utterance, by ``_scene_tool_from_tokens`` -
# never a second name-resolution table (the same shape ``_capability_catalogue_entry``
# already gives M24's own target resolution).

_SCENE_NOUN_STEMS: Final[tuple[str, ...]] = ("sahne",)

#: A primitive/object noun -> the closed ``add_primitive.kind`` word it names (spec
#: §2's own vocabulary). "ışık" alone (no "güneş"/sun qualifier) defaults to a point
#: light - the commonest, least surprising reading of a bare "bir ışık ekle".
_PRIMITIVE_KIND_BY_NOUN: Final[dict[str, str]] = {
    "küp": "cube",
    "kup": "cube",
    "küre": "sphere",
    "kure": "sphere",
    "silindir": "cylinder",
    "düzlem": "plane",
    "duzlem": "plane",
    "güneş": "light_sun",
    "gunes": "light_sun",
    "ışık": "light_point",
    "isik": "light_point",
    # The k->ğ consonant softening a vowel-initial suffix triggers ("ışık" + "-ı" ->
    # "ışığı", the same mutation "renk" + "-i" -> "rengi" undergoes below): the
    # MUTATED stem, never reachable through a plain ``.startswith("ışık")`` prefix
    # check.
    "ışığ": "light_point",
    "isig": "light_point",
    # A naive (non-Turkish-aware) lowercasing of "Işık"/"Işığı" maps the dotless
    # capital "I" to the DOTTED lowercase "i" ("işık"/"işığı"), not "ı" -
    # ``turkish_casefold`` fixes this on text that still carries its true case, but
    # the corpus's own ".lower()" ASR-noise variant (``_variants``,
    # tests/voice_corpus/corpus.py) already lowercased the word before this resolver
    # ever sees it, the same way a naive real ASR normalizer could. Both readings
    # are accepted.
    "işık": "light_point",
    "işığ": "light_point",
    "kamera": "camera",
}
_PRIMITIVE_NOUN_STEMS: Final[tuple[str, ...]] = tuple(_PRIMITIVE_KIND_BY_NOUN)

_SCENE_CREATE_VERB_FORMS: Final[tuple[str, ...]] = (
    "oluştur",
    "olustur",
    "oluşturur",
    "olusturur",
    "oluştursana",
    "olustursana",
    "aç",
    "ac",
    "açsana",
    "acsana",
    "açar",
    "acar",
)
_SCENE_ADD_VERB_FORMS: Final[tuple[str, ...]] = (
    "ekle",
    "eklesene",
    "ekler",
    "koy",
    "koysana",
    "oluştur",
    "olustur",
    "oluşturur",
    "olusturur",
)
_SCENE_TRANSFORM_VERB_STEMS: Final[tuple[str, ...]] = (
    "taşı",
    "tasi",
    "büyüt",
    "buyut",
    "küçült",
    "kucult",
    "döndür",
    "dondur",
)
_SCENE_DEICTIC_WORDS: Final[tuple[str, ...]] = ("bunu", "onu")
_SCENE_COLOR_STEMS: Final[tuple[str, ...]] = (
    "kırmızı",
    "kirmizi",
    "mavi",
    "yeşil",
    "yesil",
    "sarı",
    "sari",
    "siyah",
    "beyaz",
    "turuncu",
    "mor",
    "pembe",
)
_SCENE_MATERIAL_NOUN_STEMS: Final[tuple[str, ...]] = ("renk", "reng")
_SCENE_MATERIAL_VERB_FORMS: Final[tuple[str, ...]] = (
    "yap",
    "yapsana",
    "yapar",
    "boya",
    "boyasana",
    "boyar",
)
#: "ışık" itself, "ışığ" (the k->ğ mutated stem "ışığı"/"ışığını" actually carry —
#: see ``_PRIMITIVE_KIND_BY_NOUN``'s identical comment) and their diacritic-stripped
#: ASR-noise forms.
_SCENE_LIGHT_NOUN_STEMS: Final[tuple[str, ...]] = (
    "ışık",
    "isik",
    "ışığ",
    "isig",
    "işık",
    "işığ",
)
_SCENE_LIGHT_VERB_FORMS: Final[tuple[str, ...]] = (
    "ayarla",
    "ayarlasana",
    "artır",
    "artir",
    "artırsana",
    "artirsana",
    "azalt",
    "azaltsana",
)
_SCENE_CAMERA_NOUN_STEMS: Final[tuple[str, ...]] = ("kamera",)
_SCENE_CAMERA_VERB_FORMS: Final[tuple[str, ...]] = (
    "çevir",
    "cevir",
    "çevirsene",
    "cevirsene",
    "çevirir",
    "cevirir",
    "yönlendir",
    "yonlendir",
)
_SCENE_RENDER_NOUN_STEMS: Final[tuple[str, ...]] = ("render",)
_SCENE_RENDER_VERB_FORMS: Final[tuple[str, ...]] = ("al", "alsana", "alır", "alir")


#: B44 (req 527): the 3D formats a scene export names; "gltf" is written as its binary form.
_SCENE_EXPORT_FORMAT_WORDS: Final[dict[str, str]] = {"glb": "glb", "gltf": "glb", "fbx": "fbx"}
#: B44 (req 526): the words that ask for motion rather than a pose.
_SCENE_ANIMATION_STEMS: Final[tuple[str, ...]] = (
    "animasyon",
    "canlandır",
    "canlandir",
    "hareketlendir",
)


def _scene_export_format_from_tokens(tokens: tuple[str, ...]) -> str | None:
    for tok in tokens:
        fmt = _SCENE_EXPORT_FORMAT_WORDS.get(tok)
        if fmt is not None:
            return fmt
    return None


def _scene_export_match(tokens: tuple[str, ...]) -> str | None:
    """ "Sahneyi dışa aktar." / "Sahneyi FBX olarak dışa aktar." (B44 req 527): the scene
    noun or a 3D format word, with the export phrase. M25 kept this sentence a deliberate
    negative because the vocabulary had no export; B44 gives it one. The creative family's
    export refuses the scene noun and (since B44) a 3D format word, so the two never meet."""
    if (
        _has(tokens, *_SCENE_NOUN_STEMS) is None
        and _scene_export_format_from_tokens(tokens) is None
    ):
        return None
    if _has_exact(tokens, _CREATIVE_EXPORT_OUT_WORD, _CREATIVE_EXPORT_OUT_WORD_ASCII) is None:
        return None
    if _has(tokens, *_CREATIVE_EXPORT_VERB_STEMS) is None:
        return None
    return "sahneyi dışa aktar"


def _scene_animate_match(tokens: tuple[str, ...]) -> str | None:
    """ "Küreye bir animasyon ekle." / "Küpü canlandır." (B44 req 526): a motion word with
    an object (a primitive noun, a deictic or the scene). Checked BEFORE SCENE_ADD, which
    would otherwise read "küreye ... ekle" as a new sphere."""
    if _has(tokens, *_SCENE_ANIMATION_STEMS) is None:
        return None
    has_object = (
        _has(tokens, *_PRIMITIVE_NOUN_STEMS) is not None
        or _has_exact(tokens, *_SCENE_DEICTIC_WORDS) is not None
        or _has(tokens, *_SCENE_NOUN_STEMS) is not None
    )
    if not has_object:
        return None
    return "animasyon ekle"


def _scene_tool_from_tokens(tokens: tuple[str, ...]) -> str | None:
    """ "Blender'da" / "Unity'de" (spec §5) - the tool word, resolved once from the
    SAME utterance every matcher below already checked, never a guess."""
    if _has(tokens, "blender"):
        return "blender"
    if _has(tokens, "unity"):
        return "unity"
    return None


def _scene_kind_from_tokens(tokens: tuple[str, ...]) -> str | None:
    for tok in tokens:
        for noun, kind in _PRIMITIVE_KIND_BY_NOUN.items():
            if tok == noun or tok.startswith(noun):
                return kind
    return None


def _scene_create_match(tokens: tuple[str, ...]) -> str | None:
    """ "Unity'de boş bir sahne oluştur.", "Blender'da yeni sahne aç." (spec §5)."""
    if _has(tokens, *_SCENE_NOUN_STEMS) is None:
        return None
    if _has_exact(tokens, *_SCENE_CREATE_VERB_FORMS) is None:
        return None
    return "sahne oluştur"


def _scene_inspect_match(tokens: tuple[str, ...]) -> str | None:
    """ "Sahnede ne var?" (spec §5) - checked before ADD/TRANSFORM/... so the shared
    "sahne" noun never falls through to CREATE by accident (no create verb here
    anyway, but kept first for the same "the more specific question form wins"
    ordering ``_appfactory_list_match`` documents for its own family)."""
    if _has(tokens, *_SCENE_NOUN_STEMS) is None:
        return None
    if _has_exact(tokens, "ne", "neler") is None or _has_exact(tokens, "var") is None:
        return None
    return "sahnede ne var"


def _scene_add_match(tokens: tuple[str, ...]) -> str | None:
    """ "Bir küp ekle.", "Blender'da küre oluştur.", "Bir ışık ekle." (spec §5)."""
    if _scene_kind_from_tokens(tokens) is None:
        return None
    if _has_exact(tokens, *_SCENE_ADD_VERB_FORMS) is None:
        return None
    return "nesne ekle"


def _scene_transform_match(tokens: tuple[str, ...]) -> str | None:
    """ "Küpü sağa taşı.", "Küreyi iki kat büyüt." (spec §5)."""
    has_object = _has(tokens, *_PRIMITIVE_NOUN_STEMS) is not None or _has_exact(
        tokens, *_SCENE_DEICTIC_WORDS
    )
    if not has_object:
        return None
    if _has(tokens, *_SCENE_TRANSFORM_VERB_STEMS) is None:
        return None
    return "nesneyi taşı"


def _scene_material_match(tokens: tuple[str, ...]) -> str | None:
    """ "Küpü kırmızı yap.", "Rengini maviye boya." (spec §5's ``scene.material``
    tool - the router's own reasonable extension, module comment above the
    ``Intent`` block)."""
    has_object = (
        _has(tokens, *_PRIMITIVE_NOUN_STEMS) is not None
        or _has(tokens, *_SCENE_MATERIAL_NOUN_STEMS) is not None
        or _has_exact(tokens, *_SCENE_DEICTIC_WORDS)
    )
    if not has_object:
        return None
    if _has(tokens, *_SCENE_COLOR_STEMS) is None:
        return None
    if _has_exact(tokens, *_SCENE_MATERIAL_VERB_FORMS) is None:
        return None
    return "nesnenin rengini değiştir"


def _scene_light_match(tokens: tuple[str, ...]) -> str | None:
    """ "Işığı ayarla.", "Işığı artır." (spec §5) - checked AFTER ADD so "Bir ışık
    ekle." (no adjust verb here) is never swallowed by this more general noun match."""
    if _has(tokens, *_SCENE_LIGHT_NOUN_STEMS) is None:
        return None
    if _has_exact(tokens, *_SCENE_LIGHT_VERB_FORMS) is None:
        return None
    return "ışığı ayarla"


def _scene_camera_match(tokens: tuple[str, ...]) -> str | None:
    """ "Kamerayı nesneye çevir." (spec §5) - checked AFTER ADD so "Kamera ekle."
    is never swallowed by this more general noun match."""
    if _has(tokens, *_SCENE_CAMERA_NOUN_STEMS) is None:
        return None
    if _has_exact(tokens, *_SCENE_CAMERA_VERB_FORMS) is None:
        return None
    return "kamerayı çevir"


def _scene_render_match(tokens: tuple[str, ...]) -> str | None:
    """ "Render al." (spec §5)."""
    if _has(tokens, *_SCENE_RENDER_NOUN_STEMS) is None:
        return None
    if _has_exact(tokens, *_SCENE_RENDER_VERB_FORMS) is None:
        return None
    return "render al"


# --------------------------------------------------- M27: Creative Tools Operator
#
# Built on the SAME token/stem primitives as every intent above - no second Turkish
# pattern table (module docstring's own rule). Called EARLY in resolve_intent -
# BEFORE the alarm/ambient block (0c) and before ARTIFACT_OPEN (0h) - because three
# real collisions were found against ALREADY-CLAIMED vocabulary while writing this
# family, the exact class of defect
# .claude/agent-memory/backend-engineer/feedback_intents_prefix_collision_risk.md
# warns about:
#   1. "Arka planını kaldır." - "kaldır" is _WAKE_VERB_STEMS/_CANCEL_VERB_STEMS'
#      own alarm verb, and with no alarm noun present the alarm resolver's own bare
#      "kaldır" fallback would otherwise create a TEST-uncovered ALARM_CREATE.
#   2. The SAME utterance also carries "arka" + a "plan"-prefixed token
#      ("planını"), which is exactly ``_technical_match``'s own "arka planda"
#      phrase (the research pipeline's technical-explanation trigger) - checked
#      much later in resolve_intent's own body (the "3. presentation level"
#      section), so priority position alone resolves it.
#   3. "Bunu Photoshop'ta aç." carries "bunu" + "aç", ARTIFACT_OPEN's own deictic
#      open pattern (0h). CREATIVE_OPEN's own tool-word gate makes the two
#      mutually exclusive in EITHER order, but this family is placed first anyway
#      so a bare "Bunu aç." (no tool word) still falls through, unchanged, to
#      ARTIFACT_OPEN/M19's generic file-open exactly as spec §5 requires ("a bare
#      'Bunu aç.' on an image focus is M19's file.open, never a creative tool").
# Every matcher below is gated on its OWN noun/verb combination so nothing here can
# be reached by an utterance this router already claims for something else -
# checked directly against the corpus (``tests/voice_corpus/corpus.py``'s ``creative``
# category, plus a full-suite run) before landing, per the same memory file's own
# "run the FULL corpus, not just the new category" rule.

_CREATIVE_TOOL_WORDS: Final[dict[str, str]] = {
    "paint": "paint",
    "mspaint": "paint",
    "photoshop": "photoshop",
    "illustrator": "illustrator",
    # ``turkish_casefold`` maps an ASCII capital "I" to "ı" (dotless), never "i" —
    # correct for a Turkish word, but "Illustrator" is a PROPER NOUN spelled with the
    # ordinary Latin "I". A real bug found via this module's own test suite (2026-09-09):
    # "Illustrator'da" casefolds to "ıllustrator'da", which the bare "illustrator" key
    # above never matches. The same dual-form fix ``_PRIMITIVE_KIND_BY_NOUN`` already
    # applies for "ışık"/"işık" (module comment there).
    "ıllustrator": "illustrator",
    "figma": "figma",
}


def _creative_tool_from_tokens(tokens: tuple[str, ...]) -> str | None:
    """ "Paint'te" / "Photoshop'ta" / "Illustrator'da" / "Figma'da" (spec §5) - the
    tool word, resolved once from the SAME utterance every matcher below already
    checked, never a guess (the same shape ``_scene_tool_from_tokens`` already gives
    M25's own tool word)."""
    for tok in tokens:
        for word, tool in _CREATIVE_TOOL_WORDS.items():
            if tok == word or tok.startswith(word):
                return tool
    return None


_CREATIVE_IMAGE_NOUN_STEMS: Final[tuple[str, ...]] = (
    "resim",
    "resm",
    "görsel",
    "gorsel",
    "fotoğraf",
    "fotograf",
)
_CREATIVE_REDRAW_VERB_STEMS: Final[tuple[str, ...]] = ("çiz", "ciz")
_CREATIVE_DEICTIC_WORDS: Final[tuple[str, ...]] = ("bunu", "onu")
_CREATIVE_BACKGROUND_NOUN_STEMS: Final[tuple[str, ...]] = ("arka",)
_CREATIVE_BACKGROUND_PLAN_STEM: Final = "plan"
_CREATIVE_BACKGROUND_VERB_FORMS: Final[tuple[str, ...]] = (
    "kaldır",
    "kaldir",
    "kaldırsana",
    "kaldirsana",
    "kaldırır",
    "kaldirir",
)
_CREATIVE_COLOR_NOUN_STEMS: Final[tuple[str, ...]] = ("renk", "reng")
_CREATIVE_ADJUST_VERB_FORMS: Final[tuple[str, ...]] = (
    "düzelt",
    "duzelt",
    "düzeltsene",
    "duzeltsene",
    "düzeltir",
    "duzeltir",
)
_CREATIVE_CLEAN_STEMS: Final[tuple[str, ...]] = ("temiz",)
_CREATIVE_CLEANUP_VERB_FORMS: Final[tuple[str, ...]] = ("getir", "getirsene", "getirir")
_CREATIVE_DESIGN_VERB_FORMS: Final[tuple[str, ...]] = ("tasarla", "tasarlasana", "tasarlar")
_CREATIVE_DESIGN_NOUN_STEMS: Final[tuple[str, ...]] = ("arayüz", "arayuz", "tasarım", "tasarim")
_CREATIVE_EXPORT_OUT_WORD: Final = "dışa"
_CREATIVE_EXPORT_OUT_WORD_ASCII: Final = "disa"
_CREATIVE_EXPORT_VERB_STEMS: Final[tuple[str, ...]] = ("aktar",)
_CREATIVE_EXPORT_FORMAT_WORDS: Final[dict[str, str]] = {
    "png": "png",
    "jpg": "jpg",
    "jpeg": "jpg",
    "svg": "svg",
    "pdf": "pdf",
}


def _creative_export_format_from_tokens(tokens: tuple[str, ...]) -> str | None:
    for tok in tokens:
        fmt = _CREATIVE_EXPORT_FORMAT_WORDS.get(tok)
        if fmt is not None:
            return fmt
    return None


def _creative_redraw_match(tokens: tuple[str, ...]) -> str | None:
    """ "Bu resmi Paint'te yeniden çiz." / "Bunu Paint'te yeniden çiz." (spec §5) - an
    image noun OR a deictic pronoun (the object may be named directly or pointed at),
    the same "noun or deictic" alternative ``_scene_transform_match`` already allows
    for its own object reference."""
    has_noun = _has(tokens, *_CREATIVE_IMAGE_NOUN_STEMS) is not None
    has_deictic = _has_exact(tokens, *_CREATIVE_DEICTIC_WORDS) is not None
    if not has_noun and not has_deictic:
        return None
    if _has(tokens, *_CREATIVE_REDRAW_VERB_STEMS) is None:
        return None
    return "yeniden çiz"


def _creative_open_match(tokens: tuple[str, ...]) -> str | None:
    """ "Bunu Photoshop'ta aç." (spec §5) - requires a creative TOOL WORD, so a bare
    "Bunu aç." (no tool word) never reaches this branch (module comment, collision 3)."""
    if _creative_tool_from_tokens(tokens) is None:
        return None
    if _has_exact(tokens, *_OPEN_VERB_FORMS) is None:
        return None
    return "aç"


def _creative_background_match(tokens: tuple[str, ...]) -> str | None:
    """ "Arka planını kaldır." (spec §5) - requires BOTH "arka" and a "plan"-prefixed
    token, never a bare "kaldır" (module comment, collision 1)."""
    if _has_exact(tokens, *_CREATIVE_BACKGROUND_NOUN_STEMS) is None:
        return None
    if _has(tokens, _CREATIVE_BACKGROUND_PLAN_STEM) is None:
        return None
    if _has_exact(tokens, *_CREATIVE_BACKGROUND_VERB_FORMS) is None:
        return None
    return "arka planını kaldır"


def _creative_adjust_match(tokens: tuple[str, ...]) -> str | None:
    """ "Renkleri biraz düzelt." (spec §5) - "düzelt" is not claimed anywhere else in
    this router, but the colour noun is still required so this can never fire on an
    unrelated "düzelt" ("Bunu düzelt.")."""
    if _has(tokens, *_CREATIVE_COLOR_NOUN_STEMS) is None:
        return None
    if _has_exact(tokens, *_CREATIVE_ADJUST_VERB_FORMS) is None:
        return None
    return "renkleri düzelt"


def _creative_cleanup_match(tokens: tuple[str, ...]) -> str | None:
    """ "Logoyu daha temiz hale getir." (spec §5) - "getir" alone is
    ``WINDOW_RESTORE``'s own verb ("pencereyi eski haline getir"); requiring "temiz"
    keeps the two disjoint in either priority order."""
    if _has(tokens, *_CREATIVE_CLEAN_STEMS) is None:
        return None
    if _has_exact(tokens, *_CREATIVE_CLEANUP_VERB_FORMS) is None:
        return None
    return "temiz hale getir"


def _creative_design_match(tokens: tuple[str, ...]) -> str | None:
    """ "Figma'da buna benzeyen bir arayüz tasarla." (spec §5) - the design verb plus
    either the tool word or an interface/design noun, so a bare "tasarla" about
    something else entirely (never seen elsewhere in this router today) still needs
    ONE of the two before this fires."""
    if _has_exact(tokens, *_CREATIVE_DESIGN_VERB_FORMS) is None:
        return None
    has_tool = _creative_tool_from_tokens(tokens) is not None
    has_noun = _has(tokens, *_CREATIVE_DESIGN_NOUN_STEMS) is not None
    if not has_tool and not has_noun:
        return None
    return "tasarla"


# ------------------------------------------------------------- B43: creative lifecycle

_CREATIVE_GENERATE_VERB_STEMS: Final[tuple[str, ...]] = (
    "üret",
    "uret",
    "oluştur",
    "olustur",
    "tasarla",
)
_CREATIVE_GENERATE_NOUN_STEMS: Final[tuple[str, ...]] = (
    "görsel",
    "gorsel",
    "resim",
    "resmi",
    "logo",
    "afiş",
    "afis",
    "ikon",
    "illüstrasyon",
    "illustrasyon",
    "poster",
    "kapak",
)
_CREATIVE_ENHANCE_VERB_STEMS: Final[tuple[str, ...]] = (
    "düzelt",
    "duzelt",
    "iyileştir",
    "iyilestir",
    "netleştir",
    "netlestir",
    "güzelleştir",
    "guzellestir",
)
_CREATIVE_PHOTO_NOUN_STEMS: Final[tuple[str, ...]] = (
    "fotoğraf",
    "fotograf",
    "foto",
    "resim",
    "resmi",
    "görsel",
    "gorsel",
)
_CREATIVE_DELIVER_PLACE_STEMS: Final[tuple[str, ...]] = (
    "bilgisayar",
    "disk",
    "masaüstü",
    "masaustu",
    "indirilenler",
    "klasör",
    "klasor",
)
_CREATIVE_DELIVER_VERB_STEMS: Final[tuple[str, ...]] = ("indir", "kaydet", "teslim", "koy")
_CREATIVE_SHOW_VERB_STEMS: Final[tuple[str, ...]] = ("göster", "goster")
_CREATIVE_REDO_STEMS: Final[tuple[str, ...]] = ("yinele", "yeniden yap", "ileri al")


def _creative_generate_match(tokens: tuple[str, ...], text: str) -> tuple[str, str] | None:
    """ "Bana bir logo üret: mavi bir dalga." / "Bir afiş oluştur." (req 492): an image
    noun AND a generation verb, never the redraw verb ("yeniden çiz" is CREATIVE_REDRAW)
    and never a creative tool word with "aç" (CREATIVE_OPEN). The prompt is the owner's
    sentence after the colon when there is one, else the whole sentence."""
    if _has(tokens, *_CREATIVE_GENERATE_NOUN_STEMS) is None:
        return None
    if _has(tokens, *_CREATIVE_GENERATE_VERB_STEMS) is None:
        return None
    if (
        _has(tokens, *_CREATIVE_REDRAW_VERB_STEMS) is not None
        and _has_exact(tokens, "yeniden") is not None
    ):
        return None
    if _has(tokens, "tablo", "belge", "sunum", "sayfa", "uygulama") is not None:
        return None
    head, sep, tail = text.partition(":")
    prompt = tail.strip() if sep and tail.strip() else text.strip()
    return "görsel üret", prompt[:1000]


def _creative_enhance_match(tokens: tuple[str, ...]) -> str | None:
    """ "Bu fotoğrafı düzelt." (req 512): a photo noun and an improving verb, and NOT the
    colour word (that is CREATIVE_ADJUST's own sentence)."""
    if _has(tokens, *_CREATIVE_PHOTO_NOUN_STEMS) is None:
        return None
    if _has(tokens, *_CREATIVE_ENHANCE_VERB_STEMS) is None:
        return None
    if _has(tokens, "renk") is not None:
        return None
    return "fotoğrafı düzelt"


def _creative_lifecycle_match(
    tokens: tuple[str, ...], *, creative_focused: bool
) -> tuple[Intent, str, dict[str, Any]] | None:
    """Undo / redo / deliver / show, gated on a creative run being in focus - so "geri al"
    in an empty room keeps its owners (the document's undo needs its own noun anyway)."""
    if not creative_focused:
        return None
    if _has_exact(tokens, "geri") is not None and _has(tokens, "al") is not None:
        return Intent.CREATIVE_UNDO, "geri al", {"creative_ref": "current"}
    if _has(tokens, *_CREATIVE_REDO_STEMS) is not None or (
        _has_exact(tokens, "ileri") is not None and _has(tokens, "al") is not None
    ):
        return Intent.CREATIVE_REDO, "yinele", {"creative_ref": "current"}
    tool_word = _creative_tool_from_tokens(tokens)
    if tool_word == "paint" and _has(tokens, *_CREATIVE_SHOW_VERB_STEMS) is not None:
        return (
            Intent.CREATIVE_DELIVER,
            "paint'te göster",
            {"creative_ref": "current", "creative_application": "mspaint"},
        )
    if (
        _has(tokens, *_CREATIVE_DELIVER_PLACE_STEMS) is not None
        and _has(tokens, *_CREATIVE_DELIVER_VERB_STEMS) is not None
    ):
        return Intent.CREATIVE_DELIVER, "bilgisayarıma indir", {"creative_ref": "current"}
    return None


def _creative_export_match(tokens: tuple[str, ...]) -> str | None:
    """ "Bunu PNG olarak dışa aktar." (spec §5) - requires BOTH "dışa" and "aktar";
    "aktar" alone is ``_RESEARCH_TELLING_VERBS``' own word, but that branch ALSO
    requires a research topic word, so the two are disjoint in either order - "dışa"
    is required here anyway as a second, independent gate.

    And a THIRD gate, which the corpus had to teach: "dışa" + an export verb is every
    "... dışa aktar." sentence in Turkish, including M25's own negative case "Sahneyi
    dışa aktar." (expected: none, because the 3D family has no export operation and spec
    §7 says an operation outside the vocabulary is never guessed). Matching it here would
    have told the owner Paint was exporting a Blender scene.

    So a creative export must be about something creative: it names a FORMAT the family
    supports, or it names nothing else's noun. Narrowed rather than reordered - a
    reordering would only move the collision to whichever family lost the race.
    """
    if _has_exact(tokens, _CREATIVE_EXPORT_OUT_WORD, _CREATIVE_EXPORT_OUT_WORD_ASCII) is None:
        return None
    if _has(tokens, *_CREATIVE_EXPORT_VERB_STEMS) is None:
        return None
    if (
        _has(tokens, *_SCENE_NOUN_STEMS) is not None
        or _scene_export_format_from_tokens(tokens) is not None
    ):
        # Another family's own noun - or, since B44, its own format word (GLB/FBX). Its
        # absence of an export operation is that family's to state, not this one's to
        # satisfy.
        return None
    return "dışa aktar"


# ------------------------------------------ M28: the Native App Factory (spec §6)
#
# Built on the SAME token/stem primitives as every family above - no second Turkish
# pattern table (module docstring's own rule). Called EARLY in resolve_intent (0b''',
# right after the M27 creative block and BEFORE the M19 operator block, the M20
# document block, M23's App Factory block and M22's artifact block) because this
# family's own VERBS are the most heavily shared in the entire router. FOUR real
# collisions were MEASURED against the existing corpus and the live router before any
# of this was written, and each is closed by a NARROWING gate rather than by priority
# alone - the M27 lesson, where CREATIVE_EXPORT's "dışa"+"aktar" matched every "...
# dışa aktar." sentence in Turkish including another family's own negative case, and
# was fixed by refusing that family's noun rather than by moving the branch:
#
#   1. "Bana Windows için masaüstü uygulaması yap." resolved to APP_FACTORY_CREATE
#      (M23 owns "uygulam" + "yap"). Priority position alone would fix THIS
#      utterance and silently break "Web uygulaması yap." / "Bana bir görev takip
#      uygulaması yap.", which must stay M23. The gate is a WINDOWS/ANDROID noun,
#      so an app-factory request that names no platform never reaches here at all.
#   2. "Masaüstündeki teklif dosyalarını karşılaştırıp bir Excel tablosu ve yönetici
#      özeti hazırla." (the corpus's own EXEC_START case, ex.start.folder.*) carries
#      "masaüstü" AND a create verb ("hazırla"). Two independent gates keep it out:
#      the create matchers additionally require an APPLICATION noun ("uygulama" /
#      "proje" / "program" / "sürüm"), and they refuse outright when another
#      family's noun ("dosya", "excel", "tablo", "özet", "rapor", "sunum", ...) is
#      present. Either one alone would have been enough; both are cheap.
#   3. "Uygulamayı emülatörde aç." resolved to APP_FACTORY_OPEN. The gate is the
#      "emülatör" noun, which M23's own open matcher never requires - so a bare
#      "Uygulamayı aç." / "Bir uygulama aç." falls through UNCHANGED to M23, exactly
#      as it did before this family existed.
#   4. "Hata varsa düzelt." resolved to EXPLAIN (``_RESEARCH_PROBLEM_WORDS`` owns
#      "hata"). It carries NO native noun at all - spec §6 spells it that way - so
#      the gate cannot be vocabulary: NATIVE_FIX, NATIVE_CHECK and NATIVE_REBUILD are
#      gated on ``native_build_focused``, the CALLER's one live fact ("this owner has
#      a native build to be asked about"), the same "context, never vocabulary alone"
#      discipline ``operator_running``/``document_focused``/``executive_run_state``
#      already establish. With no build in the system, all three fall through
#      UNCHANGED - "Hata varsa düzelt." is still EXPLAIN.
#
# And the fifth rule, which is the milestone's own character: iOS is refused BY NAME,
# never claimed. ``_NATIVE_IOS_WORDS`` mirrors ``app.nativefactory.stacks.IOS_WORDS``
# (spelled here, per this module's "no cross-module import for a string literal"
# convention); tests/unit/test_voice_native_intents.py reads the OTHER side's source
# and fails if the two drift, so "an iOS request routes to nothing in this family"
# cannot quietly stop being true.

#: Mirrors ``app.nativefactory.stacks.IOS_WORDS``. "app store" is a two-word phrase
#: there; tokenised here it can only ever be seen as its two tokens, and "store" alone
#: is not evidence of anything, so the single-token members are what this checks.
_NATIVE_IOS_WORD_STEMS: Final[tuple[str, ...]] = ("ios", "iphone", "ipad", "ipados")

#: The platform nouns. "masaüstü" is ALSO ``_DOCUMENT_FOLDER_WORDS``' own Desktop word,
#: which is why every matcher using it needs a second gate (collision 2 above).
_NATIVE_WINDOWS_NOUN_STEMS: Final[tuple[str, ...]] = ("windows", "masaüstü", "masaustu")
_NATIVE_ANDROID_NOUN_STEMS: Final[tuple[str, ...]] = ("android",)
_NATIVE_EMULATOR_NOUN_STEMS: Final[tuple[str, ...]] = (
    "emülatör",
    "emulator",
    "emülator",
    "emulatör",
    "emülatörde",
)
#: The artefact nouns - each one names a TARGET on its own, which is what makes these
#: the narrowest gates in the family.
_NATIVE_EXE_NOUN_STEMS: Final[tuple[str, ...]] = ("exe",)
_NATIVE_APK_NOUN_STEMS: Final[tuple[str, ...]] = ("apk",)
_NATIVE_AAB_NOUN_STEMS: Final[tuple[str, ...]] = ("aab",)
#: "kurulum" (an installer), never a bare "kur" - the alarm family's own "kur"
#: ("alarm kur") shares no prefix with this, so the two can never collide.
_NATIVE_INSTALLER_NOUN_STEMS: Final[tuple[str, ...]] = ("kurulum", "msix", "installer", "setup")

#: An APPLICATION noun: the second, independent gate on the two CREATE matchers
#: (collision 2). "sürüm"/"versiyon" is here because spec §6's own Android phrase is
#: "Android sürümünü yap." and names no application word at all.
_NATIVE_APP_NOUN_STEMS: Final[tuple[str, ...]] = (
    "uygulam",
    "proje",
    "program",
    "sürüm",
    "surum",
    "versiyon",
)

_NATIVE_CREATE_VERB_FORMS: Final[tuple[str, ...]] = (
    "yap",
    "yapsana",
    "yapar",
    "yapabilir",
    "oluştur",
    "olustur",
    "oluşturur",
    "olusturur",
    "oluştursana",
    "olustursana",
    "hazırla",
    "hazirla",
    "hazırlar",
    "hazirlar",
)
#: The OUTPUT verbs: "çıkar" (produce), "üret" (generate), "derle"/"build" (compile),
#: "paketle" (package), plus the create verbs (an owner says "EXE yap" as readily as
#: "EXE çıkar"). Every one of these is claimed elsewhere in this router with a
#: different noun, which is exactly why the artefact noun is required first.
_NATIVE_OUTPUT_VERB_FORMS: Final[tuple[str, ...]] = _NATIVE_CREATE_VERB_FORMS + (
    "çıkar",
    "cikar",
    "çıkart",
    "cikart",
    "çıkarsana",
    "cikarsana",
    "çıkarır",
    "cikarir",
    "üret",
    "uret",
    "üretsene",
    "uretsene",
    "üretir",
    "uretir",
    "derle",
    "derlesene",
    "derler",
    "build",
    "paketle",
    "paketlesene",
    "al",
    "alsana",
)
_NATIVE_OPEN_VERB_FORMS: Final[tuple[str, ...]] = _OPEN_VERB_FORMS + (
    "başlat",
    "baslat",
    "başlatsana",
    "baslatsana",
    "çalıştır",
    "calistir",
)
_NATIVE_CHECK_STEMS: Final[tuple[str, ...]] = ("kontrol",)
_NATIVE_PROBLEM_NOUN_STEMS: Final[tuple[str, ...]] = ("hata", "bug", "çökme", "cokme")
_NATIVE_FIX_VERB_FORMS: Final[tuple[str, ...]] = (
    "düzelt",
    "duzelt",
    "düzeltsene",
    "duzeltsene",
    "düzeltir",
    "duzeltir",
    "onar",
    "gider",
)
_NATIVE_REBUILD_VERB_FORMS: Final[tuple[str, ...]] = (
    "build",
    "derle",
    "derlesene",
    "derler",
    "rebuild",
)
_NATIVE_NEW_VERSION_STEMS: Final[tuple[str, ...]] = ("yeni",)

#: Another family's own noun. Its presence means the utterance belongs to that family,
#: whose answer (or honest silence) is that family's to give, never this one's to
#: satisfy - the exact narrowing ``_creative_export_match`` documents for ``sahne``.
#: Deliberately NOT applied blanket-wide: "uygulama" is genuinely part of THIS family's
#: own canonical sentence, and "dosya" is part of "Kurulum dosyasını oluştur." - so each
#: matcher below names the list it actually refuses.
_NATIVE_FOREIGN_ARTIFACT_STEMS: Final[tuple[str, ...]] = (
    "sunum",
    "slayt",
    "tablo",
    "belge",
    "rapor",
    "excel",
    "word",
    "csv",
    "özet",
    "ozet",
)
_NATIVE_FOREIGN_FILE_STEMS: Final[tuple[str, ...]] = (
    "dosya",
    "klasör",
    "klasor",
    "pdf",
)
_NATIVE_FOREIGN_MEDIA_STEMS: Final[tuple[str, ...]] = (
    "resim",
    "resm",
    "görsel",
    "gorsel",
    "fotoğraf",
    "fotograf",
    "renk",
    "reng",
    "logo",
    "video",
    "haber",
)
_NATIVE_FOREIGN_SURFACE_STEMS: Final[tuple[str, ...]] = (
    "pencere",
    "ekran",
    "alarm",
    "mail",
    "posta",
    "eposta",
    "takvim",
    # M21's own inbox noun (``_INBOX_NOUN_STEMS``). A REAL defect, found by this
    # family's own probe before it landed: "Gelen kutumu kontrol eder misin?" carries
    # "kontrol", and with a native build in the system NATIVE_CHECK - which runs
    # earlier - claimed it away from MAIL_INBOX. Narrowed, not reordered: the mail
    # family's own noun is what says the utterance is not about a build.
    "kutu",
    "gelen",
)
#: The full refusal set the noun-less matchers (CHECK / FIX / REBUILD) use: they have
#: no native noun of their own to lean on, so they refuse the WIDEST set, M23's own
#: "uygulam"/"proje" included - "Uygulama çalışıyor mu?" stays APP_FACTORY_STATUS.
_NATIVE_FOREIGN_ALL_STEMS: Final[tuple[str, ...]] = (
    _NATIVE_FOREIGN_ARTIFACT_STEMS
    + _NATIVE_FOREIGN_FILE_STEMS
    + _NATIVE_FOREIGN_MEDIA_STEMS
    + _NATIVE_FOREIGN_SURFACE_STEMS
    + _SCENE_NOUN_STEMS
    + ("uygulam", "proje")
)

#: B33: the lifecycle verbs ("uygulamayı aç / doğrula / güncelle / kaldır", "uygulamanın
#: günlüğünü oku") REQUIRE the application noun that check/fix/rebuild treat as M23's, so
#: their foreign list is the same one without it.
_NATIVE_LIFECYCLE_FOREIGN_STEMS: Final[tuple[str, ...]] = tuple(
    stem for stem in _NATIVE_FOREIGN_ALL_STEMS if stem not in ("uygulam", "proje")
)

#: The TARGET each artefact noun names, in ``app.nativefactory.spec.NATIVE_TARGETS``'
#: own vocabulary. "kurulum" is an MSIX here because MSIX is the only installer format
#: this machine can actually produce (spec §1: Inno Setup and WiX are absent, so an MSI
#: is out of scope) - the tool still refuses honestly when makeappx is missing.
_NATIVE_TARGET_WINDOWS_EXE: Final = "windows_exe"
_NATIVE_TARGET_WINDOWS_MSIX: Final = "windows_msix"
_NATIVE_TARGET_ANDROID_APK: Final = "android_apk"
_NATIVE_TARGET_ANDROID_AAB: Final = "android_aab"


def _native_ios_requested(tokens: tuple[str, ...]) -> bool:
    """An iOS request, recognised so it can be refused BY NAME rather than claimed.

    Spec §1/§6: there is no macOS and no Xcode here, and with no MAUI workload there is
    not even a shared head to compile. Nothing in this family may answer such an
    utterance, so every matcher below returns None the moment one of these words is
    present, and the router falls through to whatever it meant before M28 existed.
    """
    return _has(tokens, *_NATIVE_IOS_WORD_STEMS) is not None


def _native_target_from_tokens(tokens: tuple[str, ...]) -> str | None:
    """The target word the owner's OWN WORDS carried ("EXE" -> ``windows_exe``,
    "kurulum" -> ``windows_msix``, "APK" -> ``android_apk``), or None when the words
    named none - a best-effort convenience the tool prefers when non-empty, never a
    substitute for the model's own argument (the same rule ``scene_kind``/
    ``artifact_kind`` already follow). The artefact nouns are checked before the
    platform nouns: "Windows için EXE çıkar." names an EXE, not merely Windows."""
    if _has(tokens, *_NATIVE_EXE_NOUN_STEMS):
        return _NATIVE_TARGET_WINDOWS_EXE
    if _has(tokens, *_NATIVE_APK_NOUN_STEMS):
        return _NATIVE_TARGET_ANDROID_APK
    if _has(tokens, *_NATIVE_AAB_NOUN_STEMS):
        return _NATIVE_TARGET_ANDROID_AAB
    if _has(tokens, *_NATIVE_INSTALLER_NOUN_STEMS):
        return _NATIVE_TARGET_WINDOWS_MSIX
    if _has(tokens, *_NATIVE_ANDROID_NOUN_STEMS):
        return _NATIVE_TARGET_ANDROID_APK
    if _has(tokens, *_NATIVE_WINDOWS_NOUN_STEMS):
        return _NATIVE_TARGET_WINDOWS_EXE
    return None


def _native_create_windows_match(tokens: tuple[str, ...]) -> str | None:
    """ "Bana Windows için masaüstü uygulaması yap." (spec §6).

    THREE gates, because "yap" is the single most claimed verb in this router: a
    Windows/desktop noun, an APPLICATION noun, and no other family's noun at all. The
    second gate is what keeps the corpus's own "Masaüstündeki teklif dosyalarını ...
    hazırla." with M26 (module comment, collision 2); the third is the same narrowing
    ``_creative_export_match`` already applies for its own reason.
    """
    if _native_ios_requested(tokens):
        return None
    if _has(tokens, *_NATIVE_WINDOWS_NOUN_STEMS) is None:
        return None
    if _has_exact(tokens, *_NATIVE_CREATE_VERB_FORMS) is None:
        return None
    if _has(tokens, *_NATIVE_APP_NOUN_STEMS) is None:
        return None
    if (
        _has(
            tokens,
            *_NATIVE_FOREIGN_ARTIFACT_STEMS,
            *_NATIVE_FOREIGN_FILE_STEMS,
            *_NATIVE_FOREIGN_MEDIA_STEMS,
            *_NATIVE_FOREIGN_SURFACE_STEMS,
            *_SCENE_NOUN_STEMS,
        )
        is not None
    ):
        return None
    return "windows masaüstü uygulaması yap"


def _native_create_android_match(tokens: tuple[str, ...]) -> str | None:
    """ "Android sürümünü yap." / "Bunun Android sürümünü yap." (spec §6) - the same
    three gates the Windows create match uses, on the Android noun."""
    if _native_ios_requested(tokens):
        return None
    if _has(tokens, *_NATIVE_ANDROID_NOUN_STEMS) is None:
        return None
    if _has_exact(tokens, *_NATIVE_CREATE_VERB_FORMS) is None:
        return None
    if _has(tokens, *_NATIVE_APP_NOUN_STEMS) is None:
        return None
    if (
        _has(
            tokens,
            *_NATIVE_FOREIGN_ARTIFACT_STEMS,
            *_NATIVE_FOREIGN_FILE_STEMS,
            *_NATIVE_FOREIGN_MEDIA_STEMS,
            *_NATIVE_FOREIGN_SURFACE_STEMS,
            *_SCENE_NOUN_STEMS,
        )
        is not None
    ):
        return None
    return "android sürümünü yap"


def _native_build_exe_match(tokens: tuple[str, ...]) -> str | None:
    """ "Bunu EXE olarak çıkar." (spec §6) - gated on the "exe" noun, which nothing
    else in this router claims, so the shared output verbs cost nothing here."""
    if _native_ios_requested(tokens):
        return None
    if _has(tokens, *_NATIVE_EXE_NOUN_STEMS) is None:
        return None
    if _has_exact(tokens, *_NATIVE_OUTPUT_VERB_FORMS) is None:
        return None
    return "exe olarak çıkar"


def _native_build_apk_match(tokens: tuple[str, ...]) -> str | None:
    """ "APK üret." (spec §6) - gated on the "apk"/"aab" noun, for the same reason."""
    if _native_ios_requested(tokens):
        return None
    if _has(tokens, *_NATIVE_APK_NOUN_STEMS, *_NATIVE_AAB_NOUN_STEMS) is None:
        return None
    if _has_exact(tokens, *_NATIVE_OUTPUT_VERB_FORMS) is None:
        return None
    return "apk üret"


def _native_build_installer_match(tokens: tuple[str, ...]) -> str | None:
    """ "Kurulum dosyasını oluştur." (spec §6) - gated on "kurulum"/"msix". The
    utterance also carries "dosya", M20's own noun, which is why this matcher (unlike
    the two CREATE matchers) does NOT refuse the file stems: an installer IS a file,
    and M20's own matchers all require a SEARCH/READ verb this one never carries."""
    if _native_ios_requested(tokens):
        return None
    if _has(tokens, *_NATIVE_INSTALLER_NOUN_STEMS) is None:
        return None
    if _has_exact(tokens, *_NATIVE_OUTPUT_VERB_FORMS) is None:
        return None
    return "kurulum dosyasını oluştur"


def _native_emulator_open_match(tokens: tuple[str, ...]) -> str | None:
    """ "Uygulamayı emülatörde aç." (spec §6) - gated on the "emülatör" noun, which
    M23's own open matcher never requires, so a bare "Uygulamayı aç." / "Bir uygulama
    aç." falls through to APP_FACTORY_OPEN exactly as it did before M28 existed
    (module comment, collision 3)."""
    if _native_ios_requested(tokens):
        return None
    if _has(tokens, *_NATIVE_EMULATOR_NOUN_STEMS) is None:
        return None
    if _has_exact(tokens, *_NATIVE_OPEN_VERB_FORMS) is None:
        return None
    return "emülatörde aç"


# ------------------------------------------ B33: launch, verify, log, uninstall, update

_NATIVE_LAUNCH_VERB_FORMS: Final[tuple[str, ...]] = (
    "aç",
    "ac",
    "çalıştır",
    "calistir",
    "başlat",
    "baslat",
)
#: What makes "aç / çalıştır / başlat" the BUILT application's launch rather than M23's
#: web-app open (test_voice_native_intents keeps "Uygulamayı aç." M23's even with a build
#: in the system): the native words, the compile word, or the "program" noun M23 never
#: uses.
_NATIVE_LAUNCH_QUALIFIER_STEMS: Final[tuple[str, ...]] = (
    *_NATIVE_WINDOWS_NOUN_STEMS,
    *_NATIVE_EXE_NOUN_STEMS,
    "yerel",
    "derle",
    "native",
    "program",
)
_NATIVE_VERIFY_STEMS: Final[tuple[str, ...]] = ("doğrula", "dogrula", "sına", "sina")
_NATIVE_UI_NOUN_STEMS: Final[tuple[str, ...]] = ("arayüz", "arayuz", "pencere")
_NATIVE_LOG_NOUN_STEMS: Final[tuple[str, ...]] = ("günlü", "gunlu", "log")  # günlüğünü: k->ğ
_NATIVE_UNINSTALL_VERB_STEMS: Final[tuple[str, ...]] = ("kaldır", "kaldir")
_NATIVE_UPDATE_STEMS: Final[tuple[str, ...]] = ("güncelle", "guncelle")


def _native_launch_match(tokens: tuple[str, ...], *, native_build_focused: bool) -> str | None:
    """ "Masaüstü uygulamasını aç." / "EXE'yi çalıştır." / "Programı başlat." with a
    native build focused (B33 req 462). The BARE "Uygulamayı aç." stays M23's App Factory
    even then (the older contract, kept by test_voice_native_intents): the native launch
    needs a native word, the compile word or the "program" noun. "Emülatör" stays the
    Android branch above."""
    if _native_ios_requested(tokens) or not native_build_focused:
        return None
    if _has(tokens, *_NATIVE_EMULATOR_NOUN_STEMS) is not None:
        return None
    if _has(tokens, *_NATIVE_LAUNCH_QUALIFIER_STEMS) is None:
        return None
    if _has_exact(tokens, *_NATIVE_LAUNCH_VERB_FORMS) is None:
        return None
    if _has(tokens, *_NATIVE_LIFECYCLE_FOREIGN_STEMS) is not None:
        return None
    return "masaüstü uygulamasını aç"


def _native_verify_match(tokens: tuple[str, ...], *, native_build_focused: bool) -> str | None:
    """ "Uygulamayı doğrula." / "Arayüzünü test et." (B33 req 463-465)."""
    if _native_ios_requested(tokens) or not native_build_focused:
        return None
    if _has(tokens, *_NATIVE_APP_NOUN_STEMS, *_NATIVE_UI_NOUN_STEMS) is None:
        return None
    if _has(tokens, *_NATIVE_VERIFY_STEMS) is None and not (
        _has(tokens, *_NATIVE_UI_NOUN_STEMS) is not None and _has_exact(tokens, "test", "kontrol")
    ):
        return None
    if _has(tokens, *_NATIVE_LIFECYCLE_FOREIGN_STEMS) is not None:
        return None
    return "uygulamayı doğrula"


def _native_log_match(tokens: tuple[str, ...], *, native_build_focused: bool) -> str | None:
    """ "Uygulamanın günlüğünü oku." (B33 req 466)."""
    if _native_ios_requested(tokens) or not native_build_focused:
        return None
    if (
        _has(tokens, *_NATIVE_APP_NOUN_STEMS) is None
        or _has(tokens, *_NATIVE_LOG_NOUN_STEMS) is None
    ):
        return None
    if _has_exact(tokens, *_READ_VERB_FORMS, "göster", "goster", "söyle", "soyle") is None:
        return None
    return "uygulamanın günlüğünü oku"


def _native_uninstall_match(tokens: tuple[str, ...], *, native_build_focused: bool) -> str | None:
    """ "Kurulumu kaldır." / "Uygulamayı kaldır." (B33 req 469)."""
    if _native_ios_requested(tokens):
        return None
    has_installer_noun = _has(tokens, *_NATIVE_INSTALLER_NOUN_STEMS) is not None
    if not has_installer_noun and not (
        native_build_focused and _has(tokens, *_NATIVE_APP_NOUN_STEMS) is not None
    ):
        return None
    if _has(tokens, *_NATIVE_UNINSTALL_VERB_STEMS) is None:
        return None
    if _has(tokens, *_NATIVE_LIFECYCLE_FOREIGN_STEMS) is not None:
        return None
    return "kurulumu kaldır"


def _native_update_match(tokens: tuple[str, ...], *, native_build_focused: bool) -> str | None:
    """ "Uygulamayı güncelle." (B33 req 471) - with a native build focused; "sistemi
    güncelle" and the like carry no native noun and stay where they were."""
    if _native_ios_requested(tokens) or not native_build_focused:
        return None
    if _has(tokens, *_NATIVE_APP_NOUN_STEMS) is None:
        return None
    if _has(tokens, *_NATIVE_UPDATE_STEMS) is None:
        return None
    if _has(tokens, *_NATIVE_LIFECYCLE_FOREIGN_STEMS) is not None:
        return None
    return "uygulamayı güncelle"


def _native_check_match(tokens: tuple[str, ...], *, native_build_focused: bool) -> str | None:
    """ "Çalışıyor mu kontrol et." (spec §6).

    Spec §6 spells this with NO native noun, so vocabulary alone cannot gate it and
    ``native_build_focused`` does (module comment, collision 4). Two further gates: the
    "kontrol" stem (M21's inbox check is the only other claim on it, and that one
    requires an inbox noun), and a refusal of every other family's noun - so
    "Uygulama çalışıyor mu?" stays APP_FACTORY_STATUS and "Gelen kutumu kontrol et."
    stays MAIL_INBOX, in either priority order.
    """
    if _native_ios_requested(tokens):
        return None
    if _has(tokens, *_NATIVE_CHECK_STEMS) is None:
        return None
    has_native_noun = (
        _has(
            tokens,
            *_NATIVE_EXE_NOUN_STEMS,
            *_NATIVE_APK_NOUN_STEMS,
            *_NATIVE_AAB_NOUN_STEMS,
            *_NATIVE_INSTALLER_NOUN_STEMS,
            *_NATIVE_EMULATOR_NOUN_STEMS,
            *_NATIVE_ANDROID_NOUN_STEMS,
            *_NATIVE_WINDOWS_NOUN_STEMS,
        )
        is not None
    )
    if not has_native_noun and not native_build_focused:
        return None
    if _has(tokens, *_NATIVE_FOREIGN_ALL_STEMS) is not None:
        return None
    return "çalışıyor mu kontrol et"


def _native_fix_match(tokens: tuple[str, ...], *, native_build_focused: bool) -> str | None:
    """ "Hata varsa düzelt." (spec §6) - the same noun-less shape as NATIVE_CHECK, so
    the same three gates: a problem noun plus a fix verb, ``native_build_focused``, and
    a refusal of every other family's noun. With no build in the system this returns
    None and the utterance stays EXPLAIN, which is what it resolved to before M28
    (module comment, collision 4); "Renkleri biraz düzelt." carries "renk" and stays
    CREATIVE_ADJUST in either order."""
    if _native_ios_requested(tokens):
        return None
    if _has(tokens, *_NATIVE_PROBLEM_NOUN_STEMS) is None:
        return None
    if _has_exact(tokens, *_NATIVE_FIX_VERB_FORMS) is None:
        return None
    has_native_noun = (
        _has(
            tokens,
            *_NATIVE_EXE_NOUN_STEMS,
            *_NATIVE_APK_NOUN_STEMS,
            *_NATIVE_INSTALLER_NOUN_STEMS,
            *_NATIVE_EMULATOR_NOUN_STEMS,
            *_NATIVE_ANDROID_NOUN_STEMS,
            *_NATIVE_WINDOWS_NOUN_STEMS,
        )
        is not None
    )
    if not has_native_noun and not native_build_focused:
        return None
    if _has(tokens, *_NATIVE_FOREIGN_ALL_STEMS) is not None:
        return None
    return "hata varsa düzelt"


def _native_rebuild_match(tokens: tuple[str, ...], *, native_build_focused: bool) -> str | None:
    """ "Yeni sürümü build et." (spec §6).

    "build"/"derle" is claimed nowhere else in this router, but "sürüm" is
    (RELEASE_ROLLBACK's "önceki sürüme dön"), so the COMPILE verb is required and the
    version word alone never fires this. The third gate is the usual foreign-noun
    refusal; the second accepts either the explicit "yeni sürüm" phrasing or a live
    build, so the spec's own sentence works with no context at all.
    """
    if _native_ios_requested(tokens):
        return None
    if _has_exact(tokens, *_NATIVE_REBUILD_VERB_FORMS) is None:
        return None
    said_new_version = (
        _has(tokens, *_NATIVE_NEW_VERSION_STEMS) is not None
        and _has(tokens, *_VERSION_STEMS) is not None
    )
    if not said_new_version and not native_build_focused:
        return None
    if _has(tokens, *_NATIVE_FOREIGN_ALL_STEMS) is not None:
        return None
    return "yeni sürümü build et"


# --------------------------------------------------- M26: Executive Autonomy
#
# Built on the same token/stem primitives as every family above — no second Turkish
# pattern table (module docstring's own rule). ``_executive_start_match`` reuses
# ``_RESEARCH_STEMS`` (defined below for the research interaction classes) rather than
# a duplicate constant of the same name and meaning.

#: An OUTPUT word — required alongside a shape's own noun so a bare "Onu araştır" (the
#: EXISTING single-shot research family) or "Bunu PDF yap" (ARTIFACT_CREATE) is never
#: misread as a multi-step executive directive (spec §5's own three examples all name a
#: concrete deliverable: "Word raporu", "sunum", "Excel", "yönetici özeti", "cevap
#: taslağı"). Deliberately narrower than a bare "hazırla"/"özet", which ARTIFACT_CREATE
#: and plain research already use for a SINGLE deliverable.
_EXEC_OUTPUT_STEMS: Final[tuple[str, ...]] = ("rapor", "sunum", "slayt", "excel", "tablo")
_EXEC_COMPARE_STEMS: Final[tuple[str, ...]] = ("karşılaştır", "karsilastir", "kıyasla", "kiyasla")
_EXEC_FOLDER_STEMS: Final[tuple[str, ...]] = ("klasör", "klasor", "teklif")
_EXEC_MAIL_STEMS: Final[tuple[str, ...]] = ("mail", "posta", "eposta")
_EXEC_MAIL_THREAD_STEMS: Final[tuple[str, ...]] = ("zincir", "konuşma", "konusma")
#: All three surface forms: "taslak" (bare), "taslağ-" (suffixed: taslağı, taslağa —
#: "taslak" softens its final k to ğ before a vowel suffix) and "taslag-" (the SAME
#: suffixed form with its diacritic stripped, exactly what an ASR-noise transcript or
#: ``tests.voice_corpus.corpus._strip_diacritics`` produces) — the same alternation
#: ``app.voice.intents``'s own word-form comments handle elsewhere by listing forms
#: explicitly rather than risking a short, over-eager prefix. Found via the corpus: a
#: diacritic-stripped "taslagi" matched neither of the first two forms.
_EXEC_DRAFT_STEMS: Final[tuple[str, ...]] = ("taslak", "taslağ", "taslag")


def _executive_output_requested(tokens: tuple[str, ...]) -> bool:
    if _has(tokens, *_EXEC_OUTPUT_STEMS):
        return True
    if _has_exact(tokens, "yönetici", "yonetici") and _has(tokens, "özet", "ozet"):
        return True
    return bool(_has(tokens, *_EXEC_DRAFT_STEMS))


def _executive_start_match(tokens: tuple[str, ...]) -> tuple[str, str] | None:
    """(shape, matched) for one of spec §5's three directive shapes, or ``None``. Only
    decides "is this a multi-step executive directive at all" — WHICH shape's full
    graph is built is ``app.executive.planner.RuleBasedExecutivePlanner``'s own job,
    given the SAME directive text verbatim (module comment above)."""
    if not _executive_output_requested(tokens):
        return None
    if _has_exact(tokens, *_EXEC_MAIL_STEMS) and (
        _has(tokens, *_EXEC_MAIL_THREAD_STEMS) or _has(tokens, *_EXEC_DRAFT_STEMS)
    ):
        return "mail_thread", "mail zinciri + taslak"
    if _has(tokens, *_EXEC_COMPARE_STEMS) and _has(tokens, *_EXEC_FOLDER_STEMS):
        return "folder_compare", "klasör karşılaştırma"
    if _has(tokens, *_RESEARCH_STEMS):
        return "research_report", "araştırma + rapor"
    return None


#: "adım" (step) — exact forms only: "adama"/"adamı" (a person) shares no prefix, but
#: "adımı"/"adıma" do share the stem "adım", so a startswith match is safe here.
_EXEC_STEP_NOUN_STEMS: Final[tuple[str, ...]] = ("adım", "adim")
_EXEC_RETRY_VERB_STEMS: Final[tuple[str, ...]] = ("dene", "deneyin", "yeniden")
#: A coarse step-KIND family a retry/amend phrase names instead of an ordinal ("
#: araştırmayı tekrar dene", "excel'i de hazırla") — app.executive.spec.STEP_KINDS all
#: start with one of these family prefixes.
_EXEC_KIND_HINTS: Final[dict[str, str]] = {
    "araştır": "research",
    "arastir": "research",
    "mail": "mail",
    "posta": "mail",
    "taslak": "mail",
    "taslağ": "mail",  # taslağı/taslağa - see _EXEC_DRAFT_STEMS's own comment
    "taslag": "mail",  # diacritic-stripped taslağı/taslağa - see the same comment
    "takvim": "calendar",
    "excel": "artifacts",
    "tablo": "artifacts",
    "sunum": "artifacts",
    "rapor": "artifacts",
    "belge": "documents",
    "dosya": "documents",
}


def _exec_step_ordinal(tokens: tuple[str, ...]) -> int | None:
    if _has(tokens, *_EXEC_STEP_NOUN_STEMS) is None:
        return None
    for tok in tokens:
        if tok in _ORDINALS:
            return _ORDINALS[tok]
        for word, idx in _ORDINALS.items():
            if len(word) > 3 and tok.startswith(word):
                return idx
    return None


def _exec_kind_hint(tokens: tuple[str, ...]) -> str | None:
    for tok in tokens:
        for stem, family in _EXEC_KIND_HINTS.items():
            if tok.startswith(stem):
                return family
    return None


#: For EXEC_AMEND specifically: the SPECIFIC deliverable kind
#: (app.artifacts.spec.ARTIFACT_KINDS), not the coarse family ``_exec_kind_hint`` gives
#: RETRY — "sunumu da ekle" must add a PRESENTATION, never merely "an artifacts step".
_EXEC_AMEND_KIND_WORDS: Final[dict[str, str]] = {
    "sunum": "presentation",
    "slayt": "presentation",
    "excel": "spreadsheet",
    "tablo": "spreadsheet",
    "rapor": "document",
    "belge": "document",
}


def _exec_amend_kind(tokens: tuple[str, ...]) -> str | None:
    for tok in tokens:
        for stem, kind in _EXEC_AMEND_KIND_WORDS.items():
            if tok.startswith(stem):
                return kind
    return None


def _executive_retry_match(tokens: tuple[str, ...]) -> str | None:
    """ "İkinci adımı tekrar dene." / "Araştırmayı tekrar dene." (spec §5) — needs
    EITHER an ordinal+adım OR a recognised kind hint, so a bare "tekrar dene" with
    neither (ambiguous — which step?) falls through to REPEAT instead."""
    if _has(tokens, *_EXEC_RETRY_VERB_STEMS) is None:
        return None
    if _exec_step_ordinal(tokens) is not None or _exec_kind_hint(tokens) is not None:
        return "tekrar dene"
    return None


_EXEC_AMEND_VERB_STEMS: Final[tuple[str, ...]] = ("ekle", "eklesene", "eklermisin")
#: "de"/"da" (Turkish "also/too", a separate word here, not a suffix) — spec's own
#: examples both carry it ("Sunumu DA ekle", "Excel'i DE hazırla"), the discriminator
#: that keeps a plain ARTIFACT_CREATE ("Bana bir sunum hazırla") from being misread as
#: an amendment to something that may not even be running.
_EXEC_ALSO_WORDS: Final[tuple[str, ...]] = ("de", "da")


def _executive_amend_match(tokens: tuple[str, ...]) -> str | None:
    if _has_exact(tokens, *_EXEC_ALSO_WORDS) is None:
        return None
    if _has_exact(tokens, *_EXEC_AMEND_VERB_STEMS):
        return "de ekle"
    if _has(tokens, "hazırla", "hazirla") and _exec_amend_kind(tokens) is not None:
        return "de hazırla"
    return None


def _executive_active_match(
    tokens: tuple[str, ...], *, run_state: str | None
) -> tuple[Intent, str] | None:
    """The six intents that only mean something with a run to point at (module comment
    above) — gated on ``run_state`` (one of app.executive.models.EXECUTIVE_RUN_STATE_
    VALUES, or None when no run exists), the CALLER's one live fact, never guessed from
    vocabulary alone. Order: EXPLAIN before STATUS ("tam olarak" is the discriminator,
    spec's own EXPLAIN phrasing); CANCEL before PAUSE (both can carry "dur"-shaped
    words, and "iptal et"/"vazgeç" are unambiguous); RETRY and AMEND last, each gated on
    their own extra word (module comments).

    PAUSE and RESUME match on ANY active run, not only the exact state each is really
    about — the SAME "vocabulary decides the tool, the tool decides whether it can
    actually act" split MAIL_SEND/CALENDAR_COMMIT already use (resolve_intent's own
    docstring: they "match on vocabulary ALONE ... the tool ... answers with an honest
    clarification from its OWN service layer, never a guess made here"). Gating PAUSE
    strictly on "running" or RESUME strictly on "paused" would make spec §5's own
    negative case ("Devam et" with nothing paused) fall through to the generic RESUME
    control intent instead of an EXECUTIVE clarification whenever a run genuinely
    exists but is not paused — silently correct-looking, and wrong: the owner asked
    THIS system to continue THIS job, and deserves "Duraklatılmış bir iş yok efendim."
    from ``app.executive.service.resume_run_db``, not a narration no-op.
    """
    if run_state is None:
        return None
    if (
        _has_exact(tokens, "tam")
        and _has_exact(tokens, "olarak")
        and _has_exact(tokens, "ne")
        and _has(tokens, "yap")
    ):
        return Intent.EXEC_EXPLAIN, "şu an tam olarak ne yapıyorsun"
    if _has_exact(tokens, "ne") and (
        _has(tokens, "yap") or (_has(tokens, "durum") and _has_exact(tokens, "ne", "nedir"))
    ):
        return Intent.EXEC_STATUS, "ne yapıyorsun / ne durumda"
    if _has_exact(tokens, "iptal") or _has_exact(tokens, "vazgeç", "vazgec"):
        return Intent.EXEC_CANCEL, "iptal et / vazgeç"
    # Never on a run that has already reached a TERMINAL state (spec §3's own seven
    # words, EXECUTIVE_RUN_STATES — frozen, so these three literals cannot silently
    # drift the way a duplicated Turkish table could): a "Bekle"/"Devam et" said long
    # after a run finished means whatever it ordinarily means, not a stale executive
    # command. The strict PAUSE-only-while-running / RESUME-only-while-paused split
    # still belongs to the TOOL, not this check (module docstring above).
    if run_state in ("planned", "running", "paused"):
        if _has_exact(tokens, *STOP_TOKENS):
            return Intent.EXEC_PAUSE, "bu işi durdur / bekle"
        if _has_exact(tokens, "devam", "sürdür", "surdur"):
            return Intent.EXEC_RESUME, "devam et"
    if retry_matched := _executive_retry_match(tokens):
        return Intent.EXEC_RETRY, retry_matched
    if amend_matched := _executive_amend_match(tokens):
        return Intent.EXEC_AMEND, amend_matched
    return None


# --------------------------------------------------- M26 addendum: Latest News Mode
#
# One noun family ("haber" - "the news"), a prefix stem for the same reason
# _RESEARCH_STEMS is: every Turkish inflection ("haberler", "haberi", "haberini",
# "haberlerini") starts with it, and no unrelated word in this vocabulary shares that
# prefix. Deliberately its own small block rather than folded into an existing family:
# news media is neither research (it never crawls or synthesises - NEWS_OPEN plays a
# video; NEWS_SUMMARIZE delegates to research but must never be confused with a bare
# "araştır") nor alarm media (a completely separate browser profile, spec §5).

_NEWS_NOUN_STEMS: Final[tuple[str, ...]] = ("haber",)
_NEWS_OPEN_VERB_FORMS: Final[tuple[str, ...]] = (
    "aç",
    "açsana",
    "açar",
    "ac",
    "acsana",
    "acar",
)
_NEWS_SUMMARIZE_VERB_STEMS: Final[tuple[str, ...]] = ("özetle", "ozetle", "anlat")

#: Words that precede the news noun without themselves naming a channel/source -
#: temporal/superlative qualifiers ("son", "en güncel", "bugünkü") and the noun's own
#: object words (a video, YouTube itself). ``_news_source_ref`` skips every one of
#: these so what is left, if anything, is a genuine channel-name hint ("show",
#: "show'un") - never a guess, just "the words did not name a source at all" versus
#: "the words named this one".
_NEWS_GENERIC_STEMS: Final[frozenset[str]] = frozenset(
    {
        "bugünün",
        "bugunun",
        "bugünkü",
        "bugunku",
        "son",
        "en",
        "güncel",
        "guncel",
        "şu",
        "su",
        "an",
        "şimdiki",
        "simdiki",
        "yüklenen",
        "yuklenen",
        "yüklenmiş",
        "yuklenmis",
        "yükledi",
        "yukledi",
        "ana",
        "video",
        "videosunu",
        "videosu",
        "youtube'dan",
        "youtubedan",
        "youtube'da",
        "hangi",
        "zaman",
        "ne",
        "şimdi",
        "simdi",
        # B51 (746): a polite request's particle and a past-tense question were read as
        # a channel name ("Haberleri açar mısın?" -> source "mısın"), and the tool
        # refused with news_source_not_found instead of opening the default source.
        "mı",
        "mi",
        "mu",
        "mü",
        "mısın",
        "misin",
        "musun",
        "müsün",
        "mısınız",
        "misiniz",
        "musunuz",
        "müsünüz",
        "acaba",
        "lütfen",
        "lutfen",
        "bana",
        "bir",
        "yüklendi",
        "yuklendi",
        "yayınlandı",
        "yayinlandi",
        "yayınlanan",
        "yayinlanan",
    }
)


def _news_noun(tokens: tuple[str, ...]) -> str | None:
    return _has(tokens, *_NEWS_NOUN_STEMS)


#: Stem-prefix matched (like ``_RESEARCH_STEMS`` above): every inflection of "open"
#: ("aç", "açacaksın", "açar mısın") or "summarize"/"tell" ("özetle", "özetler misin",
#: "anlatır mısın") is a verb, never a channel name - a future-tense "açacaksın"
#: ("Şu an hangi haber videosunu açacaksın?") is exactly the shape a query asks with,
#: and matching only the imperative forms in ``_NEWS_OPEN_VERB_FORMS`` (needed for
#: INTENT detection, where a false match on some unrelated word would be worse) missed
#: it.
_NEWS_VERB_STEM_PREFIXES: Final[tuple[str, ...]] = ("aç", "ac", "özet", "ozet", "anlat")


def _news_source_ref(tokens: tuple[str, ...]) -> str | None:
    """The first token that is neither a generic qualifier, the news noun itself, nor
    an open/summarize verb (in any inflection) - a channel-name hint the tool matches
    against configured sources' display names ("Show'un son haberini aç" ->
    "show'un"), or ``None`` when the words named no source at all ("Haberleri aç.",
    "En güncel haber videosunu aç.") - the default configured source then answers,
    never a guessed channel."""
    for tok in tokens:
        if tok in _NEWS_GENERIC_STEMS:
            continue
        if tok.startswith(_NEWS_NOUN_STEMS[0]):
            continue
        if any(tok.startswith(prefix) for prefix in _NEWS_VERB_STEM_PREFIXES):
            continue
        return tok
    return None


def _news_match(tokens: tuple[str, ...]) -> tuple[Intent, str] | None:
    noun = _news_noun(tokens)
    if noun is None:
        return None
    # "hangi" ("which") is its own question trigger, alongside the shared
    # _is_question shapes ("ne zaman", "mı") - "Şu an hangi haber videosunu
    # açacaksın?" carries no "mı" particle and no "ne zaman", only the interrogative
    # pronoun itself.
    if _is_question(tokens) or _has_exact(tokens, "hangi"):
        return Intent.NEWS_QUERY_LATEST, noun
    if _has(tokens, *_NEWS_SUMMARIZE_VERB_STEMS):
        return Intent.NEWS_SUMMARIZE, "haberleri özetle"
    if _has_exact(tokens, *_NEWS_OPEN_VERB_FORMS):
        return Intent.NEWS_OPEN, "haberleri aç"
    return None


# ------------------------------------------------------ owner-requested media

# ADR-0112. The device has always been able to open YouTube (the wake alarm does it
# every morning); what was missing was a way for the OWNER to ask. This matcher is
# deliberately the narrowest thing that can carry the request, because the verb it
# needs -- "aç" -- is the most overloaded word in this resolver: it already opens
# applications, windows, displays, documents, news, the eye and the curtains.
#
# So a play verb is NEVER enough on its own. An explicit media marker must be present,
# and the news noun must not be: "haberleri aç" is Latest News Mode's, decided one
# block above this one and never reached here.

#: Words that say "this is media", not an application, a window or a document.
#: Stems, cut short of Turkish consonant softening: "müzik" becomes "müziği", "klip"
#: becomes "klibi". Matching the full word missed "Müziği kapat." entirely, which is
#: how the owner would actually say it.
_MEDIA_MARKER_STEMS: Final[tuple[str, ...]] = (
    "youtube",
    "şarkı",
    "sarki",
    "müzi",
    "muzi",
    "klip",
    "klib",
    "parça",
    "parca",
    # Widened 2026-09-11 (owner queue item 2). The first list was music-only, so
    # "Şu diziyi aç" and "Güldür Güldür şovunu aç" -- ordinary ways to ask for the same
    # thing -- fell through to nothing at all. These are safe additions because the news
    # noun is checked FIRST and bails out: "haber videosunu aç" is Latest News Mode's,
    # and always was.
    "video",
    "şov",
    "sov",
    "dizi",
    "film",
    "bölüm",
    "bolum",
)
#: Play verbs. "aç" is included but is inert without a marker (see above).
_MEDIA_PLAY_VERB_STEMS: Final[tuple[str, ...]] = ("çal", "cal", "oynat", "aç", "ac", "dinlet")
#: "Durdur." on its own is the ambient/alarm family's; MEDIA_STOP needs the media noun
#: too ("şarkıyı durdur", "müziği kapat"), so a bare "durdur" keeps its old meaning.
_MEDIA_STOP_VERB_STEMS: Final[tuple[str, ...]] = ("durdur", "kapat", "sustur")

#: Stripped from the spoken payload: the markers, the verbs, and the connective words
#: that carry no part of a title. Everything that survives is what the owner named.
_MEDIA_STRIP_PHRASES: Final[tuple[str, ...]] = (
    "youtube'dan",
    "youtube'da",
    "youtube'a",
    "youtubedan",
    "youtube'tan",
    "youtube",
    "bana",
    "bir",
    "şu",
    "su",
    "şarkısını",
    "sarkisini",
    "şarkısı",
    "sarkisi",
    "şarkıyı",
    "sarkiyi",
    "şarkı",
    "sarki",
    "müziğini",
    "muzigini",
    "müziği",
    "muzigi",
    "müzik",
    "muzik",
    "klibini",
    "klibi",
    "klip",
    "parçasını",
    "parcasini",
    "parçayı",
    "parcayi",
    "parça",
    "parca",
    "videosunu",
    "videoyu",
    "video",
    "şovunu",
    "sovunu",
    "şovu",
    "sovu",
    "şov",
    "sov",
    "dizisini",
    "dizisi",
    "diziyi",
    "dizi",
    "filmini",
    "filmi",
    "film",
    "bölümünü",
    "bolumunu",
    "bölümü",
    "bolumu",
    "bölüm",
    "bolum",
)
#: Verbs dropped wherever they appear, not only at the end: "Şu şarkıyı çal: Sezen
#: Aksu" puts the verb in the middle, and leaving it in searches for the word "çal".
_MEDIA_DROPPED_VERBS: Final[frozenset[str]] = frozenset(
    {"çal", "cal", "oynat", "dinlet", "aç", "ac", "açsana", "acsana", "çalsana", "calsana"}
)
#: Trailing verbs to drop once the payload is isolated.
_MEDIA_TRAILING_VERBS: Final[tuple[str, ...]] = (
    "açar mısın",
    "acar misin",
    "çalar mısın",
    "calar misin",
    "oynatır mısın",
    "oynatir misin",
    "aç",
    "ac",
    "çal",
    "cal",
    "oynat",
    "dinlet",
    "açsana",
    "acsana",
)


#: "Şarkıyı TEKRAR çal." is not a new request, it is REPEAT -- an existing intent this
#: matcher stole the moment it was written, caught by the corpus regression suite the
#: same minute. A word that points BACK at something already playing keeps its old
#: meaning; only a fresh naming reaches this family.
_MEDIA_BACKREFERENCE_STEMS: Final[tuple[str, ...]] = (
    "tekrar",
    "yeniden",
    "devam",
    "sürdür",
    "surdur",
    "duraklat",
)


#: A time reference turns "play this song" into "play this song AT". "Sabah yedi
#: otuzda bu şarkıyı çal" is an alarm the owner is trying to create, not a song to
#: start now, and the corpus expects it to stay unrouted rather than become a
#: playback -- sixteen cases said so the first time this matcher was written.
#: EXACT forms, not stems -- corrected 2026-09-11. These were prefixes, and "kur" is
#: three letters: "Kurtlar Vadisi şarkısını çal." was refused outright because "kurtlar"
#: begins with it, and so was "Dakikalar filmini aç." for "dakika". A guard against
#: scheduling was quietly deciding which Turkish titles could be played at all, and
#: nothing said so -- the utterance simply resolved to nothing. The forms below are
#: spelled out with their real inflections instead, the same way every other verb family
#: in this file does it, and for the same reason.
#:
#: A title that genuinely contains a time word ("Gece Yarısı Ekspresi") is still refused
#: when it also carries a play verb, and that is honest: the sentence really is ambiguous,
#: and the sixteen wake-song corpus cases are what that ambiguity costs.
_MEDIA_SCHEDULE_STEMS: Final[tuple[str, ...]] = (
    "sabah",
    "sabaha",
    "sabahleyin",
    "akşam",
    "aksam",
    "akşama",
    "aksama",
    "akşamleyin",
    "aksamleyin",
    "gece",
    "geceye",
    "geceleyin",
    "öğle",
    "ogle",
    "öğlen",
    "oglen",
    "öğleyin",
    "ogleyin",
    "yarın",
    "yarin",
    "yarına",
    "yarina",
    "saat",
    "saatte",
    "saatinde",
    "alarm",
    "alarmı",
    "alarmi",
    "alarma",
    "uyandır",
    "uyandir",
    "uyandırsana",
    "uyandirsana",
    "kur",
    "kursana",
    "sonra",
    "sonrasında",
    "sonrasinda",
    "dakika",
    "dakikada",
)


#: Things the owner might name with "aç" that this system does not act on and no branch
#: above claims. Without them a bare-title rule would send "arka kapıyı aç" to YouTube as
#: a search for "arka kapı"; silence is the better answer, and it is the honest one.
_NOT_A_TITLE_STEMS: Final[tuple[str, ...]] = (
    "kapı",
    "kapi",
    "ışık",
    "isik",
    "lamba",
    "perde",
    "klima",
    "radyatör",
    "radyator",
    "musluk",
    "vana",
    "kilit",
    "garaj",
    "panjur",
)

#: EXACT word forms, never stems -- and this is the whole difference between this matcher
#: and ``_media_match``. ``_has`` matches by prefix, which is right when a media marker has
#: already established the family ("çal" must also catch "çalsana"), and catastrophic
#: without one: "açıkla" starts with "aç", and "çalışıyor" and "çalıştığını" both start
#: with "çal". Twelve corpus cases proved it the first time this rule was written --
#: "Az önceki araştırmanın teknik detayını AÇIKLA" became a YouTube search. Every other
#: open-verb branch in this file (``_APP_OPEN_VERB_FORMS``, ``_ARTIFACT_OPEN_VERB_FORMS``,
#: ``_SCENE_CREATE_VERB_FORMS``) spells its forms out for exactly this reason.
_BARE_TITLE_VERB_FORMS: Final[tuple[str, ...]] = (
    "aç",
    "ac",
    "açsana",
    "acsana",
    "açar",
    "acar",
    "çal",
    "cal",
    "çalsana",
    "calsana",
    "oynat",
    "oynatsana",
    "oynatır",
    "oynatir",
    "dinlet",
    "dinletsene",
)

#: A bare title needs at least this many words. ONE unknown word with a play verb is far
#: more likely a thing than a work: the corpus pins "Winamp'ı aç." as a truthful
#: application refusal, and turning it into a YouTube search for "Winamp" would be wrong
#: in exactly the way this rule is trying to avoid being. Two or more words that no
#: allowlist, catalogue or noun stem in this resolver recognises is a name, and the only
#: family left that takes names is this one.
#:
#: The cost is stated rather than hidden: "Gülümse aç." (a one-word song) still needs a
#: marker -- "Gülümse şarkısını aç." -- and always will under this rule.
_MIN_BARE_TITLE_WORDS: Final[int] = 2


def _bare_title_media_match(
    tokens: tuple[str, ...], utterance: str
) -> tuple[Intent, str, str] | None:
    """Last resort: a play verb and a name nothing else in this resolver wanted.

    ADR-0112 made a media marker mandatory because "aç" is the most overloaded word here
    -- it opens applications, windows, displays, documents, news, the eye and the
    curtains. That was right, and it is why this is a SEPARATE matcher placed at the very
    bottom of the ladder rather than a loosening of ``_media_match``. Everything that
    reaches this point has been refused by all fifty-five branches above it, so "nothing
    else claimed these words" is true by construction rather than by a list somebody has
    to keep up to date. "Chrome'u aç", "ekranı aç", "haberleri aç", "uygulamayı aç",
    "bunu aç" and "Blender'da yeni sahne aç" never get here at all.

    The owner asked for this on 2026-09-11: "Güldür Güldür aç." without saying
    "YouTube'dan". Returns ``(intent, matched, query)`` -- the query too, because the
    payload is the whole reason this matched and re-deriving it would be two clocks for
    one decision.
    """
    if _news_noun(tokens) is not None:
        return None
    # The same four guards ``_media_match`` uses, deliberately not re-expressed: sixteen
    # corpus cases turn on the schedule/clock pair alone ("Sabah yedi otuzda ... çal" is
    # an alarm the owner is drafting, not a playback).
    if _has(tokens, *_MEDIA_BACKREFERENCE_STEMS):
        return None
    if _has_exact(tokens, *_MEDIA_SCHEDULE_STEMS):
        return None
    if utterance and _CLOCK_RE.search(utterance):
        return None
    if _is_question(tokens):
        return None
    if _has(tokens, *_NOT_A_TITLE_STEMS):
        return None
    if _has_exact(tokens, *_BARE_TITLE_VERB_FORMS) is None:
        return None
    query = _extract_media_query(utterance)
    if query is None or len(query.split()) < _MIN_BARE_TITLE_WORDS:
        return None
    return Intent.MEDIA_PLAY, "adıyla aç", query


def _media_match(tokens: tuple[str, ...], utterance: str = "") -> tuple[Intent, str] | None:
    """MEDIA_PLAY/MEDIA_STOP, or None -- and None is the common answer by design."""
    if _news_noun(tokens) is not None:
        return None
    if _has(tokens, *_MEDIA_BACKREFERENCE_STEMS):
        return None
    if _has_exact(tokens, *_MEDIA_SCHEDULE_STEMS):
        return None
    if utterance and _CLOCK_RE.search(utterance):
        return None
    marker = _has(tokens, *_MEDIA_MARKER_STEMS)
    if marker is None:
        return None
    if _is_question(tokens):
        return None
    if _has(tokens, *_MEDIA_STOP_VERB_STEMS):
        return Intent.MEDIA_STOP, f"{marker} durdur"
    if _has(tokens, *_MEDIA_PLAY_VERB_STEMS):
        return Intent.MEDIA_PLAY, f"{marker} aç"
    return None


def _extract_media_query(utterance: str) -> str | None:
    """What the owner NAMED, read off the raw text rather than the token list.

    A song title is the owner's own words, capitals, apostrophes and all, and it is
    what goes to a search engine -- so it is taken from the utterance, the same rule
    ``_extract_type_text`` follows for the text to be typed. ``None`` when the words
    named a medium but no title ("müzik aç"), which the tool turns into a question
    rather than a search for the word "müzik".
    """
    if not utterance:
        return None
    text = turkish_casefold(utterance).strip().strip(".!?")
    quoted = _quoted_span(text)
    if quoted is not None:
        return quoted[:200]
    kept: list[str] = []
    for raw in text.replace(",", " ").replace(":", " ").split():
        word = raw.strip(' ,.:;!?"')
        if not word or word in _MEDIA_STRIP_PHRASES or word in _MEDIA_DROPPED_VERBS:
            continue
        kept.append(word)
    payload = " ".join(kept).strip(' ,.:;"')
    for verb in _MEDIA_TRAILING_VERBS:
        if payload.endswith(" " + verb):
            payload = payload[: -(len(verb) + 1)].strip(' ,.:;"')
            break
        if payload == verb:
            payload = ""
            break
    return payload[:200] or None


def _quoted_span(text: str) -> str | None:
    """A quoted title, when the quotation marks are really quotation marks.

    The apostrophe is the reason this is a function and not two ``find`` calls.
    In Turkish it separates a suffix from a proper noun -- ``YouTube'dan``,
    ``Show'un`` -- so the naive reading of "YouTube'dan 'Doğum günün kutlu olsun
    Kadir' aç" opened its quote inside the FIRST word and returned ``dan`` as the
    song title. A quote only counts when it stands free: preceded by the start of
    the line or a space, and closed by a mark followed by the end, a space or
    punctuation.
    """
    for opener, closer in (("“", "”"), ('"', '"'), ("'", "'")):
        start = -1
        while True:
            start = text.find(opener, start + 1)
            if start < 0:
                break
            if start > 0 and not text[start - 1].isspace():
                continue
            end = start
            while True:
                end = text.find(closer, end + 1)
                if end < 0:
                    break
                after = text[end + 1 : end + 2]
                if after == "" or after.isspace() or after in ",.:;!?":
                    inner = text[start + 1 : end].strip()
                    return inner or None
            break
    return None


# ------------------------------------------------- research interaction classes

#: A research word in any Turkish inflection: "araştır", "araştırma", "araştırmayı",
#: "araştırmasını". A prefix stem is right here (unlike the eye nouns) because every
#: word that starts with "araştır" IS about researching - there is no unrelated Turkish
#: word sharing that prefix the way "gözlük" shares "göz".
_RESEARCH_STEMS: Final[tuple[str, ...]] = ("araştır", "arastir", "research")

#: "Do it again": the words that make a research request a RE-RUN rather than a question
#: about the run that finished.
_RERUN_WORDS: Final[tuple[str, ...]] = ("yeniden", "tekrar", "baştan", "bastan")

#: The imperative that actually asks for a crawl. Exact forms: "araştırmayı tekrar
#: ANLAT" is a question about the finished run, not an order to run it again, and the
#: difference is exactly this verb (ADR-0075: the whole defect is one utterance class
#: being read as another).
_RESEARCH_IMPERATIVES: Final[tuple[str, ...]] = ("araştır", "arastir", "araştırsana")
_RUN_VERB_FORMS: Final[tuple[str, ...]] = ("yap", "yapar", "başlat", "baslat", "çalıştır")

#: Words that make an utterance a question about the PIPELINE (technical/diagnostic)
#: rather than about the findings. Mirrors the two diagnostic query kinds
#: app.explain.classify already owns (research_problems / rejected_pages) - it does not
#: restate them: those kinds ride on ``query_kind`` and are consulted here directly.
_RESEARCH_PROBLEM_WORDS: Final[tuple[str, ...]] = ("sorun", "hata", "problem")


#: "Bunun arka planda nasıl çalıştığını anlat." - the pipeline asked about without the
#: word "teknik" (corpus r.tech.6). One helper for the research CLASS and the TECHNICAL
#: intent, so the two can never disagree about what counts as technical.
def _technical_match(tokens: tuple[str, ...]) -> str | None:
    if tok := _has(tokens, "teknik"):
        return tok
    if _has(tokens, "kod") and _has(tokens, "seviye"):
        return "kod seviyesinde"
    if _has_exact(tokens, "arka") and _has(tokens, "plan"):
        return "arka planda"
    if _has_exact(tokens, "nasıl", "nasil") and _has(tokens, "çalış", "calis"):
        return "nasıl çalışıyor"
    return None


#: Words that make an utterance a follow-up ON the findings of the finished run.
_RESEARCH_FOLLOWUP_STEMS: Final[tuple[str, ...]] = (
    "kaynak",  # "Kaynakları söyle."
    "bulgu",  # "Birinci bulguyu detaylandır."
    "sonuç",  # "Sonuçları anlat."
    "sonuc",
    "detay",  # "detaylandır"
    "ayrıntı",
    "ayrinti",
    "özet",
    "ozet",
    "kısaca",
)
_RESEARCH_TELLING_VERBS: Final[tuple[str, ...]] = ("anlat", "söyle", "soyle", "oku", "aktar")


def classify_research_interaction(
    tokens: tuple[str, ...],
    *,
    has_completed_research: bool,
    query_kind: str | None = None,
) -> str | None:
    """Which of the four research interaction classes this utterance is, or None.

    Pure: the utterance's tokens, whether a COMPLETED research context exists, and the
    query kind the one question table (``app.explain.classify``) already decided. No
    database, no session, no second Turkish table.

    Order is the whole point, and it is the owner's own rule (ADR-0075):

    1. an explicit re-run ("araştırmayı yeniden yap", "tekrar araştır") is a RETRY, and
       a retry may crawl;
    2. a research imperative on a topic ("... gelişmelerini araştır") is NEW_RESEARCH,
       and it may crawl;
    3. with a completed research to answer from, a pipeline question ("teknik anlat",
       "hangi sayfalar elendi", "araştırma sırasında ne sorun oldu") is a
       TECHNICAL_EXPLANATION, and it may NOT crawl;
    4. with a completed research to answer from, a question about the findings
       ("kaynakları söyle", "birinci bulguyu detaylandır", "neden önemli") is a
       FOLLOWUP, and it may NOT crawl.

    Classes 3 and 4 exist only when there IS a completed research: without one,
    "teknik anlat" is an ordinary technical explanation of the last activity and has
    nothing to bind to.
    """
    shape = classify_research_shape(tokens, query_kind=query_kind)
    if shape in RESEARCH_CLASSES_MAY_CRAWL:
        return shape
    if not has_completed_research:
        return None
    return shape


#: Words that frame a research request and are never its topic.
_RESEARCH_TOPIC_TAIL: Final[tuple[str, ...]] = (
    "hakkında",
    "hakkinda",
    "konusunda",
    "konusunu",
    "üzerine",
    "uzerine",
    "ilgili",
    "ile",
)
_RESEARCH_TOPIC_NOISE: Final[tuple[str, ...]] = (
    "lütfen",
    "lutfen",
    "bana",
    "benim",
    "için",
    "icin",
)


def research_topic_of(text: str) -> str | None:
    """The TOPIC of a new research request, in the owner's own words: "yapay zeka ile ilgili
    son haberleri araştır" -> "yapay zeka ile ilgili son haberleri"; "Yapay zeka hakkında
    araştırma yap" -> "Yapay zeka". None when the sentence is not a NEW research request.

    Why it exists (2026-09-19, ADR-0173): on the paid path the MODEL decides to call
    ``research.start`` and writes the topic itself. The free local mode has no model - the
    router is the only reader of the sentence - so a research asked for aloud started
    nothing. The router already knew the sentence was a new research
    (:func:`classify_research_shape`); this gives the tool the one argument it needs."""
    _, tokens, _ = normalize_transcript(text)
    if classify_research_shape(tokens) != RESEARCH_CLASS_NEW:
        return None
    kept: list[str] = []
    for word in text.split():
        _, word_tokens, _ = normalize_transcript(word)
        head = word_tokens[0] if word_tokens else ""
        if not head or head in _RESEARCH_TOPIC_NOISE or head in _RUN_VERB_FORMS:
            continue
        if head.startswith(_RESEARCH_STEMS):
            continue
        kept.append(word.strip(".,!?;:"))
    while kept and normalize_transcript(kept[-1])[0] in _RESEARCH_TOPIC_TAIL:
        kept.pop()
    topic = " ".join(w for w in kept if w).strip()
    return topic[:500] or None


def classify_research_shape(
    tokens: tuple[str, ...], *, query_kind: str | None = None
) -> str | None:
    """The research interaction class of an utterance, WITHOUT any durable context.

    docs/DECISIONS.md ADR-0076. :func:`classify_research_interaction` answers "which class
    is this turn, given that a completed research exists?" — and answers None for
    "Teknik anlat." when none does, because ADR-0075 had nothing for such a turn to bind
    to. That None is what let the guard fall through: on the owner's 2026-09-06 record a
    deictic follow-up with no completed research reachable became a CRAWL.

    This function answers the question that has no such precondition: what SHAPE is the
    utterance? "Teknik anlat." is a technical-explanation shape whether or not a research
    exists — the difference is whether the answer is a report or a question, never whether
    it is a crawl. It is the same table, read without the context gate; there is still one
    Turkish vocabulary here and no second one anywhere.
    """
    research_word = _has(tokens, *_RESEARCH_STEMS)
    rerun_word = _has_exact(tokens, *_RERUN_WORDS)
    imperative = _has_exact(tokens, *_RESEARCH_IMPERATIVES)
    run_verb = _has_exact(tokens, *_RUN_VERB_FORMS)

    # 1. RETRY - "again" plus an order to RUN it, never merely "again" plus the word
    #    research ("araştırmayı tekrar anlat" is a follow-up, not a re-run).
    if rerun_word and (imperative or (research_word and run_verb)):
        return RESEARCH_CLASS_RETRY
    # 2. NEW - the research imperative, or "araştırma yap/başlat", with no "again".
    if imperative or (research_word and run_verb):
        return RESEARCH_CLASS_NEW
    # 3. TECHNICAL EXPLANATION - the pipeline's own diagnostics.
    if query_kind in ("rejected_pages", "research_problems"):
        return RESEARCH_CLASS_TECHNICAL_EXPLANATION
    if _technical_match(tokens):
        return RESEARCH_CLASS_TECHNICAL_EXPLANATION
    if _has(tokens, "elendi", "elen") or (_has(tokens, "hangi") and _has(tokens, "sayfa")):
        return RESEARCH_CLASS_TECHNICAL_EXPLANATION
    if research_word and _has(tokens, *_RESEARCH_PROBLEM_WORDS):
        return RESEARCH_CLASS_TECHNICAL_EXPLANATION
    # 4. FOLLOW-UP - the findings themselves.
    if query_kind == "research_detail":
        return RESEARCH_CLASS_FOLLOWUP
    if _has(tokens, *_RESEARCH_FOLLOWUP_STEMS):
        return RESEARCH_CLASS_FOLLOWUP
    if _has(tokens, "neden") and _has(tokens, "önemli", "onemli"):
        return RESEARCH_CLASS_FOLLOWUP
    if research_word and _has(tokens, *_RESEARCH_TELLING_VERBS):
        return RESEARCH_CLASS_FOLLOWUP
    return None


def research_class_for(text: str, *, has_completed_research: bool) -> str | None:
    """:func:`classify_research_interaction` from raw speech (normalises first).

    The sibling entry point for callers that hold an utterance rather than a resolved
    intent; ``resolve_intent`` attaches the same value to every ``ResolvedIntent``.
    """
    _normalized, tokens, _dropped = normalize_transcript(text)
    if not tokens:
        return None
    return classify_research_interaction(
        tokens,
        has_completed_research=has_completed_research,
        query_kind=_explain_kind(tokens, _normalized),
    )


# --------------------------------------------------- which research is meant

#: docs/DECISIONS.md ADR-0076. WHICH research an utterance points at, as a shape the
#: server can resolve deterministically. Six answers, and they are not intents: an
#: utterance already has one. They live HERE, in the one router, for the reason ADR-0075
#: gave when it refused a second Turkish table — two tables that must agree will not.
RESEARCH_REFERENCE_CURRENT = "current"
RESEARCH_REFERENCE_PREVIOUS = "previous"
RESEARCH_REFERENCE_ORDINAL = "ordinal"
RESEARCH_REFERENCE_TOPIC = "topic"
#: Only a resolver can decide this one: it means "an answer to the question the server
#: just asked", and whether such a question is open is durable state, not vocabulary.
RESEARCH_REFERENCE_SELECTION = "selection"
RESEARCH_REFERENCE_NONE = "none"

RESEARCH_REFERENCES: Final[tuple[str, ...]] = (
    RESEARCH_REFERENCE_CURRENT,
    RESEARCH_REFERENCE_PREVIOUS,
    RESEARCH_REFERENCE_ORDINAL,
    RESEARCH_REFERENCE_TOPIC,
    RESEARCH_REFERENCE_SELECTION,
    RESEARCH_REFERENCE_NONE,
)

#: "bir önceki", "bundan önceki", "öncekini". A stem, because the suffix carries the case.
_PREVIOUS_STEMS: Final[tuple[str, ...]] = ("öncek", "oncek")
#: ... except right after these, where "önceki" means the MOST RECENT one, not the one
#: before it: "az önceki araştırma" is the research that just finished.
_RECENCY_QUALIFIERS: Final[tuple[str, ...]] = ("az", "biraz", "demin", "deminki", "hemen")

#: The deictic pronouns a follow-up actually uses. Exact forms: "bu"/"bunu"/"bunun" are
#: the whole word, and a stem would swallow "bugün", "bunlar", "onay".
_DEICTIC_WORDS: Final[tuple[str, ...]] = (
    "bu",
    "bunu",
    "bunun",
    "bunda",
    "bundaki",
    "buradaki",
    "şu",
    "şunu",
    "şunun",
    "o",
    "onu",
    "onun",
    "ondaki",
)

#: M20 (docs/M20_FILE_DOCUMENT_INTELLIGENCE_SPEC.md §3): words a search PATTERN must
#: never be extracted from: this table's own vocabulary, the folder/extension aliases, a
#: document noun, and ordinary Turkish function words - what is left over is the name
#: fragment the owner actually said ("sözleşme", "bütçe"). Placed here (rather than beside
#: the rest of the M20 helper table above) because it needs ``_DEICTIC_WORDS``, defined
#: just above.
_SEARCH_PATTERN_SKIP: Final[frozenset[str]] = frozenset(
    {
        *_DOCUMENT_NOUN_STEMS,
        *_FOLDER_ALIASES,
        *_EXTENSION_ALIASES,
        *_FIND_VERB_FORMS,
        *_SEARCH_VERB_FORMS,
        *_DEICTIC_WORDS,
        "klasördeki",
        "klasörde",
        "klasör",
        "içindeki",
        "icindeki",
        "içinde",
        "icinde",
        "de",
        "da",
        "leri",
        "ları",
        "lari",
    }
)


def _extract_document_pattern(tokens: tuple[str, ...]) -> str | None:
    for tok in tokens:
        if any(tok.startswith(w) for w in _SEARCH_PATTERN_SKIP):
            continue
        if tok in _NON_TOPIC_WORDS or tok in _NUMBER_WORDS:
            continue
        if len(tok) < 3:
            continue
        return tok
    return None


#: "son araştırma", "en son", "sonuncusu": the most recent one. Also the phrase a
#: clarification answer uses to pick the newer candidate.
_LATEST_WORDS: Final[tuple[str, ...]] = ("son", "sonuncu", "sonuncusu", "sonuncuyu", "en")

#: Nouns that make an ordinal about something INSIDE a research rather than about which
#: research: "birinci bulguyu detaylandır" is a follow-up on finding 1, not a reference to
#: research 1. Keeping these out is the difference between answering and mis-selecting.
_INNER_ITEM_NOUNS: Final[tuple[str, ...]] = (
    "bulgu",
    "madde",
    "paragraf",
    "nokta",
    "başlı",
    "basli",
    "bölüm",
    "bolum",
    "kaynak",
    "sayfa",
    "adım",
    "adim",
)

_DAY_TODAY: Final[tuple[str, ...]] = ("bugün", "bugun", "bugünkü", "bugunku")
_DAY_YESTERDAY: Final[tuple[str, ...]] = ("dün", "dun", "dünkü", "dunku")

#: A clock time as the owner says it, read from the RAW utterance: the tr-TR normaliser
#: turns numerals into words, so "20:19'daki" has to be seen before normalisation.
_CLOCK_RE = re.compile(r"(?<![\d:])([01]?\d|2[0-3])[:.]([0-5]\d)(?![\d:])")

#: Words that are grammar, not topic. A topic phrase is what is LEFT after these — the
#: content words a report's own topic must contain for "OpenAI araştırmasını anlat" to
#: name a run. Everything here is either this module's own command vocabulary or a
#: Turkish function word; nothing here is a subject anyone researches.
_NON_TOPIC_WORDS: Final[frozenset[str]] = frozenset(
    {
        *_DEICTIC_WORDS,
        *_LATEST_WORDS,
        *_DAY_TODAY,
        *_DAY_YESTERDAY,
        *_RECENCY_QUALIFIERS,
        *_RERUN_WORDS,
        *_RUN_VERB_FORMS,
        *_RESEARCH_TELLING_VERBS,
        *_RESEARCH_PROBLEM_WORDS,
        *_ORDINALS,
        "anlatsana",
        "söyler",
        "soyler",
        "misin",
        "mısın",
        "musun",
        "müsün",
        "lütfen",
        "lutfen",
        "efendim",
        "bana",
        "bir",
        "ile",
        "ilgili",
        "hakkında",
        "hakkinda",
        "dair",
        "için",
        "icin",
        "hangi",
        "hangisi",
        "hangisini",
        "ne",
        "neydi",
        "neler",
        "nedir",
        "neden",
        "nasıl",
        "nasil",
        "kim",
        "kaç",
        "kac",
        "var",
        "yok",
        "mı",
        "mi",
        "mu",
        "mü",
        "da",
        "de",
        "ki",
        "teknik",
        "detay",
        "detaylandır",
        "detaylandir",
        "ayrıntı",
        "ayrinti",
        "ayrıntılı",
        "ayrintili",
        "özet",
        "ozet",
        "özetle",
        "ozetle",
        "kısaca",
        "kisaca",
        "tamamını",
        "tamamini",
        "hepsini",
        "önemli",
        "onemli",
        "seviye",
        "seviyesinde",
        "düzey",
        "duzey",
        "elendi",
        "elenen",
        "durum",
        "durumu",
        "sonuç",
        "sonuc",
        "sonuçları",
        "sonuclari",
        "sonuçlarını",
        "sonuclarini",
    }
)

#: "ilk" is three letters, so the generic ordinal-prefix rule below cannot reach its
#: suffixed forms; and they are exactly the words an owner answers a clarification with.
_FIRST_WORDS: Final[tuple[str, ...]] = ("ilk", "ilki", "ilkini", "ilkinden", "ilkiydi")

#: The building blocks of a spoken Turkish number. Never a research topic on their own,
#: and the tr-TR normaliser turns "20:19" into some of them before this ever sees it.
_NUMBER_WORDS: Final[frozenset[str]] = frozenset(
    {
        "sıfır",
        "bir",
        "iki",
        "üç",
        "dört",
        "beş",
        "altı",
        "yedi",
        "sekiz",
        "dokuz",
        "on",
        "yirmi",
        "otuz",
        "kırk",
        "elli",
        "altmış",
        "yetmiş",
        "seksen",
        "doksan",
        "yüz",
        "bin",
    }
)

_MAX_CONTENT_WORDS = 6
_MAX_CONTENT_WORD_CHARS = 32


@dataclass(frozen=True, slots=True)
class ResearchReference:
    """WHICH research an utterance points at, as far as words alone can say.

    Deliberately small and JSON-round-trippable: this — not the transcript — is what the
    session record keeps of a turn, so a tool call arriving moments later (with no
    utterance of its own in its arguments) can be resolved against the same reference the
    router read. ``content_words`` is the topic index, bounded and lower-cased; nothing
    here reconstructs what the owner said.
    """

    kind: str = RESEARCH_REFERENCE_NONE
    #: 1-based, for ``kind == "ordinal"``: 1 is the current focus.
    ordinal: int | None = None
    content_words: tuple[str, ...] = ()
    #: "HH:MM" exactly as spoken, from the raw utterance ("20:19'daki").
    clock: str | None = None
    #: "today" | "yesterday" | None.
    day: str | None = None
    #: Whether the utterance said "the last one" ("en son", "sonuncusu"), which a
    #: clarification answer uses to pick the most recent candidate.
    latest: bool = False
    matched: str = ""

    @property
    def points_at_a_run(self) -> bool:
        """Whether this utterance refers to a research that already exists.

        The guard's question (ADR-0076 decision 5): a turn that points at a run is never
        a reason to start one, even when there is no run to point at.
        """
        return self.kind in (
            RESEARCH_REFERENCE_CURRENT,
            RESEARCH_REFERENCE_PREVIOUS,
            RESEARCH_REFERENCE_ORDINAL,
            RESEARCH_REFERENCE_TOPIC,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "ordinal": self.ordinal,
            "content_words": list(self.content_words),
            "clock": self.clock,
            "day": self.day,
            "latest": self.latest,
            "matched": self.matched,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> ResearchReference:
        data = raw or {}
        kind = str(data.get("kind") or RESEARCH_REFERENCE_NONE)
        if kind not in RESEARCH_REFERENCES:
            kind = RESEARCH_REFERENCE_NONE
        ordinal = data.get("ordinal")
        words = tuple(
            str(w)[:_MAX_CONTENT_WORD_CHARS]
            for w in (data.get("content_words") or ())
            if str(w).strip()
        )[:_MAX_CONTENT_WORDS]
        return cls(
            kind=kind,
            ordinal=int(ordinal) if isinstance(ordinal, int) else None,
            content_words=words,
            clock=(str(data["clock"]) if data.get("clock") else None),
            day=(str(data["day"]) if data.get("day") else None),
            latest=bool(data.get("latest")),
            matched=str(data.get("matched") or "")[:64],
        )


def _content_words(tokens: tuple[str, ...]) -> tuple[str, ...]:
    """The words that could name a TOPIC: everything that is not this module's own
    vocabulary, a Turkish function word, or a number the normaliser produced.

    Bounded and lower-cased on purpose: this is the only part of an utterance that is
    kept on the session record, and it is an index for matching a report's topic - not a
    transcript.
    """
    out: list[str] = []
    for tok in tokens:
        if tok in _NON_TOPIC_WORDS or tok in _NUMBER_WORDS or tok in _FIRST_WORDS:
            continue
        if len(tok) < 3 or any(ch.isdigit() for ch in tok):
            continue
        if any(tok.startswith(word) for word in _ORDINALS):
            continue
        if _has((tok,), *_RESEARCH_STEMS) or _has((tok,), *_PREVIOUS_STEMS):
            continue
        if _has((tok,), *_RESEARCH_FOLLOWUP_STEMS) or _has((tok,), *_INNER_ITEM_NOUNS):
            continue
        if tok not in out:
            out.append(tok[:_MAX_CONTENT_WORD_CHARS])
        if len(out) >= _MAX_CONTENT_WORDS:
            break
    return tuple(out)


def _previous_match(tokens: tuple[str, ...]) -> str | None:
    """ "bir önceki" / "bundan önceki" / "öncekini" — but not "az önceki"."""
    for n, tok in enumerate(tokens):
        if not (tok.startswith("öncek") or tok.startswith("oncek")):
            continue
        if n and tokens[n - 1] in _RECENCY_QUALIFIERS:
            return None  # "az önceki" is the most recent one, not the one before it
        return tok
    return None


def _ordinal_match(tokens: tuple[str, ...]) -> tuple[int, str] | None:
    if _has(tokens, *_INNER_ITEM_NOUNS):
        return None  # "birinci bulguyu" is about a finding, not about which research
    for tok in tokens:
        if tok in _FIRST_WORDS:
            return 1, tok
        if tok in _ORDINALS:
            return _ORDINALS[tok], tok
        # "ikincisi", "üçüncüsü": the same word answering a question.
        for word, idx in _ORDINALS.items():
            if len(word) > 3 and tok.startswith(word):
                return idx, tok
    return None


def classify_research_reference(
    tokens: tuple[str, ...], *, utterance: str | None = None
) -> ResearchReference:
    """WHICH research this utterance points at — deterministic, pure, no database.

    Order, and why (ADR-0076): "bir önceki" is checked before the deictics because
    "bundan önceki" contains one; an ordinal before the deictics because "bu ikinci
    araştırma" means the second; the deictics before a topic phrase because "bu OpenAI
    araştırması" is still "this one". A topic phrase is what is left when no pointer was
    used at all and content words remain.
    """
    clock: str | None = None
    if utterance:
        found = _CLOCK_RE.search(utterance)
        if found:
            clock = f"{int(found.group(1)):02d}:{found.group(2)}"
    day: str | None = None
    if _has_exact(tokens, *_DAY_TODAY):
        day = "today"
    elif _has_exact(tokens, *_DAY_YESTERDAY):
        day = "yesterday"
    latest = bool(
        _has_exact(tokens, "sonuncu", "sonuncusu", "sonuncuyu")
        or (_has_exact(tokens, "son") and not _has(tokens, *_INNER_ITEM_NOUNS))
    )
    words = _content_words(tokens)

    def _ref(kind: str, *, ordinal: int | None = None, matched: str = "") -> ResearchReference:
        return ResearchReference(
            kind=kind,
            ordinal=ordinal,
            content_words=words,
            clock=clock,
            day=day,
            latest=latest,
            matched=matched,
        )

    previous = _previous_match(tokens)
    if previous:
        return _ref(RESEARCH_REFERENCE_PREVIOUS, matched=previous)
    ordinal = _ordinal_match(tokens)
    if ordinal is not None:
        return _ref(RESEARCH_REFERENCE_ORDINAL, ordinal=ordinal[0], matched=ordinal[1])
    deictic = _has_exact(tokens, *_DEICTIC_WORDS)
    if deictic:
        return _ref(RESEARCH_REFERENCE_CURRENT, matched=deictic)
    if latest or _has_exact(tokens, *_RECENCY_QUALIFIERS):
        return _ref(RESEARCH_REFERENCE_CURRENT, matched="son")
    if words:
        return _ref(RESEARCH_REFERENCE_TOPIC, matched=words[0])
    return _ref(RESEARCH_REFERENCE_NONE)


def research_reference_for(text: str) -> ResearchReference:
    """:func:`classify_research_reference` from raw speech (normalises first, and reads
    the clock time off the RAW text before the normaliser turns digits into words)."""
    _normalized, tokens, _dropped = normalize_transcript(text)
    return classify_research_reference(tokens, utterance=text)


def _stop_match(text: str, tokens: tuple[str, ...]) -> str | None:
    for phrase in _MULTI_STOP_PHRASES:
        if re.search(rf"(?<!\S){re.escape(phrase)}(?!\S)", text):
            return phrase
    return _has_exact(tokens, *STOP_TOKENS)


def _scope_for(intent: Intent, narration: NarrationState | None) -> str:
    """Narration owns the intent when there is a narration in a non-idle state."""
    if narration is not None and narration.state != State.IDLE:
        return SCOPE_NARRATION
    if narration is not None and intent in (
        Intent.REPEAT_ITEM,
        Intent.NEXT_ITEM,
        Intent.PREVIOUS_ITEM,
        Intent.FIRST_ITEM,
        Intent.LAST_ITEM,
        Intent.NEXT_SECTION,
        Intent.SKIP,
    ):
        return SCOPE_NARRATION
    return SCOPE_CONVERSATION


def resolve_intent(
    text: str,
    *,
    session_state: RealtimeState | None = None,
    narration: NarrationState | None = None,
    has_completed_research: bool = False,
    alarm_ringing: bool = False,
    operator_running: bool = False,
    document_focused: bool = False,
    event_focused: bool = False,
    draft_pending: bool = False,
    proposal_pending: bool = False,
    genesis_awaiting_approval: bool = False,
    executive_run_state: str | None = None,
    native_build_focused: bool = False,
    mutation_pending: bool = False,
    mission_state: str | None = None,
    app_project_focused: bool = False,
    artifact_focused: bool = False,
    creative_focused: bool = False,
) -> ResolvedIntent:
    """Resolve a transcript into an :class:`Intent` against the live state.

    The words as heard are resolved first (:func:`_resolve_intent_rules`), and whatever
    they reach is the answer - a routed utterance is never re-read (row 741 stays exactly
    as it was). Only when they reach NOTHING are two repair readings tried (B51 req
    746/747), each through the very same rules:

    * ``polite`` - a polite request ("Kamerayı kapatır mısın?", "... kapatabilir misin?")
      read as the imperative it asks for ("Kamerayı kapat"), whose route row 741 already
      pins;
    * ``ascii_fold`` - the same words with every Turkish letter folded on BOTH sides of
      each comparison, for a transcript that lost them ("hizli", "calistir").

    A repair is taken only when its readings agree on one intent, and never into the mail
    or calendar families (deferred by the owner; their routes stay exactly as they are).

    The one exception to "the words as heard first" is an ALL-CAPS transcript whose "I"
    cannot say which i it is (``caps_fold``, :func:`_is_ambiguous_caps`): it is read
    folded first, because its exact reading is not the words as heard at all.
    """
    state: dict[str, Any] = {
        "session_state": session_state,
        "narration": narration,
        "has_completed_research": has_completed_research,
        "alarm_ringing": alarm_ringing,
        "operator_running": operator_running,
        "document_focused": document_focused,
        "event_focused": event_focused,
        "draft_pending": draft_pending,
        "proposal_pending": proposal_pending,
        "genesis_awaiting_approval": genesis_awaiting_approval,
        "executive_run_state": executive_run_state,
        "native_build_focused": native_build_focused,
        "mutation_pending": mutation_pending,
        "mission_state": mission_state,
        "app_project_focused": app_project_focused,
        "artifact_focused": artifact_focused,
        "creative_focused": creative_focused,
    }
    if _is_ambiguous_caps(text):
        # An ALL-CAPS transcript with "I" and no "İ" cannot say which i it meant: "RUTININI"
        # casefolds to "rutınını", lost the routine noun and left "DURDUR" to the bare STOP.
        # Such text is read with "I" as "i" (so "DEFTERI'NI" and "DAKIKA" are the words
        # the alias and number tables know) and every comparison folded (so "KAPATIR",
        # which really had a dotless i, still meets "kapat") - from the start.
        capped = _repair_reading((text.replace("I", "i"),), state, folded=True)
        if capped is not None:
            return replace(capped, route_repair=ROUTE_REPAIR_CAPS_FOLD)
    first = _resolve_intent_rules(text, **state)
    if first.intent is not Intent.NONE or not first.tokens:
        return first
    polite = polite_imperative_readings(text)
    for label, readings, folded in (
        (ROUTE_REPAIR_POLITE, polite, False),
        (ROUTE_REPAIR_ASCII_FOLD, (text,), True),
        (f"{ROUTE_REPAIR_POLITE}+{ROUTE_REPAIR_ASCII_FOLD}", polite, True),
    ):
        repaired = _repair_reading(readings, state, folded=folded)
        if repaired is not None:
            return replace(repaired, route_repair=label)
    return first


ROUTE_REPAIR_POLITE: Final = "polite"
ROUTE_REPAIR_ASCII_FOLD: Final = "ascii_fold"
ROUTE_REPAIR_CAPS_FOLD: Final = "caps_fold"


def _is_ambiguous_caps(text: str) -> bool:
    """An all-caps text whose "I" may be either i (no "İ" says the writer cased it the
    Turkish way)."""
    return (
        "I" in text
        and "İ" not in text
        and not any(ch.islower() for ch in text)
        and sum(ch.isalpha() for ch in text) >= 3
    )


#: Families a repair reading never routes into: mail and calendar are deferred by the
#: owner (B45/B46) and keep exactly the routes they had.
_REPAIR_NEVER_PREFIXES: Final[tuple[str, ...]] = ("mail_", "calendar_")
#: Second-person question particles: "... kapatır mısın / kapatır mısınız". A request, not
#: a yes/no question about state ("açık mı?" carries no person and is never rewritten).
_POLITE_REQUEST_RE: Final[re.Pattern[str]] = re.compile(
    r"(?P<verb>[^\W\d_]{3,})(?P<gap>\s+)(?P<particle>m[ıiuüIİUÜ]s[ıiuüIİUÜ]n(?:[ıiuüIİUÜ]z)?)"
    r"(?![^\W\d_])",
    re.IGNORECASE,
)
#: "-abilir misin": the ability form asks the same thing ("kapatabilir misin").
_ABILITY_SUFFIXES: Final[tuple[str, ...]] = ("yabilir", "yebilir", "abilir", "ebilir")
#: Aorist verbs whose request reading is NOT their imperative: "hatırlar mısın?" asks what
#: I remember, "bilir misin?" what I know.
_POLITE_NOT_A_REQUEST: Final[frozenset[str]] = frozenset(
    # "Bunu yapabilir misin?" asks what I can do (the capability question), not for it.
    {"hatırlar", "hatirlar", "bilir", "anlar", "sever", "ister", "yapabilir"}
)
#: Consonant softening the aorist shows and the imperative does not ("eder" -> "et").
_SOFTENED_STEMS: Final[dict[str, str]] = {"ed": "et", "gid": "git"}
#: Vowel-final stems whose aorist adds only "r" after a close vowel ("taşır" is "taşı",
#: not "taş"; "okur" is "oku").
_CLOSE_VOWEL_STEMS: Final[frozenset[str]] = frozenset(
    {"taşı", "tasi", "oku", "yürü", "yuru", "uyu", "koru", "tanı", "tani"}
)
_VOWELS: Final = "aeıioöuüâîû"


def _imperative_stems(verb: str) -> list[str]:
    """Every imperative the aorist/ability form ``verb`` can be (casefolded), most likely
    first. Two readings survive where Turkish cannot tell them apart from the letters
    ("kopyalar" is "kopyala"+r; "açar" is "aç"+ar); the rules decide, and a repair is only
    taken when all readings that route agree."""
    word = turkish_casefold(verb)
    if word in _POLITE_NOT_A_REQUEST:
        return []
    for suffix in _ABILITY_SUFFIXES:
        if word.endswith(suffix) and len(word) - len(suffix) >= 2:
            return [word[: -len(suffix)]]
    if len(word) < 3 or not word.endswith("r") or word[-2] not in _VOWELS:
        return []
    out: list[str] = []
    one = word[:-1]
    if one in _CLOSE_VOWEL_STEMS:
        return [one]
    two = word[:-2]
    if len(two) >= 2 and two[-1] not in _VOWELS:
        out.append(_SOFTENED_STEMS.get(two, two))
    if one[-1] in "ae" and len(one) >= 3:
        out.append(one)
    return out


def polite_imperative_readings(text: str) -> tuple[str, ...]:
    """The utterance with its first polite request ("X-ır mısın") replaced by each
    imperative X can be; empty when there is no such request."""
    match = _POLITE_REQUEST_RE.search(text)
    if match is None:
        return ()
    readings = []
    for stem in _imperative_stems(match.group("verb")):
        readings.append(text[: match.start()] + stem + text[match.end() :])
    return tuple(readings)


def _repair_reading(
    readings: Sequence[str], state: dict[str, Any], *, folded: bool
) -> ResolvedIntent | None:
    token = _FOLD_MATCHING.set(folded)
    try:
        routed = [
            r
            for r in (_resolve_intent_rules(reading, **state) for reading in readings)
            if r.intent is not Intent.NONE
        ]
    finally:
        _FOLD_MATCHING.reset(token)
    if not routed or len({r.intent for r in routed}) != 1:
        return None
    if routed[0].intent.value.startswith(_REPAIR_NEVER_PREFIXES):
        return None
    return routed[0]


def _resolve_intent_rules(
    text: str,
    *,
    session_state: RealtimeState | None = None,
    narration: NarrationState | None = None,
    has_completed_research: bool = False,
    alarm_ringing: bool = False,
    operator_running: bool = False,
    document_focused: bool = False,
    event_focused: bool = False,
    draft_pending: bool = False,
    proposal_pending: bool = False,
    genesis_awaiting_approval: bool = False,
    executive_run_state: str | None = None,
    native_build_focused: bool = False,
    mutation_pending: bool = False,
    mission_state: str | None = None,
    app_project_focused: bool = False,
    artifact_focused: bool = False,
    creative_focused: bool = False,
) -> ResolvedIntent:
    """Resolve a transcript into an :class:`Intent` against the live state (the words as
    heard; :func:`resolve_intent` is the entry point).

    ``session_state`` is the M4 control FSM state of the conversation;
    ``narration`` the narration machine state when a narration is attached.
    Stop words win from ANY state (spec §5); everything else is resolved in
    a fixed priority order documented inline.

    ``has_completed_research`` is the one piece of durable context this resolver takes:
    whether a COMPLETED research exists for the owner (ADR-0075). It decides nothing
    about the intent; it decides whether "teknik anlat" is additionally a
    ``research_technical_explanation`` (a question about a finished run) or just a
    technical explanation of the last activity. The caller establishes it from the
    research runs/reports - the resolver stays pure.

    ``alarm_ringing`` is the second: whether a wake alarm is ringing right now, which is
    what makes a bare "Sustur." an ``ALARM_STOP`` (see ``_alarm_match``).

    ``operator_running`` is the third: whether a Digital Operator task is running right
    now (spec §3), which is what makes a bare "Dur." / "İptal et." an ``OPERATOR_CANCEL``
    and "Ne yapıyorsun?" an ``OPERATOR_STATUS`` - the same ringing-aware pattern
    ``alarm_ringing`` already gives the alarm family.

    ``document_focused`` is the fourth and last (docs/M20_FILE_DOCUMENT_INTELLIGENCE_SPEC.md
    §3): whether a ``document`` object focus exists right now, which is what makes a bare
    deictic ("Bunu özetle.", "Ödeme süresi kaç gün?") a document intent rather than falling
    through to the plain SUMMARIZE control intent or NONE - the same
    "context, never vocabulary alone" discipline the ringing/running flags already keep.

    ``event_focused``, ``draft_pending`` and ``proposal_pending`` are M21's own three
    (docs/M21_MAIL_CALENDAR_SPEC.md §3, ADR-0084): whether a ``event`` object focus exists
    (the same "ertele" disambiguation ``alarm_ringing`` already gives the alarm's own
    snooze word — see ``_alarm_match``), and whether a PREPARED draft/proposal was read
    back to the owner in THIS session — the one precondition that turns a bare "Gönder."/
    "Onayla."/"Vazgeç." into MAIL_SEND/CALENDAR_COMMIT/DISCARD rather than a clarification
    ("Neyi göndereyim?"/"Neyi onaylayayım?") or nothing at all. The caller establishes all
    three from the durable focus stack and the drafts/proposals tables; the resolver stays
    pure.

    ``genesis_awaiting_approval`` is M24's own one fact (docs/M24_CAPABILITY_GENESIS_SPEC.md
    §6, ADR-0087): whether a ``GenesisRun`` is parked ``awaiting_approval`` in THIS
    session right now — the same "one precondition turns a bare confirmation word into a
    real tool call" shape ``draft_pending``/``proposal_pending`` already give MAIL_SEND/
    CALENDAR_COMMIT, established by the caller from ``GenesisService.find_awaiting_approval``.

    ``native_build_focused`` is M28's own one fact (docs/M28_NATIVE_APP_FACTORY_SPEC.md §6):
    whether this owner has a native build to be asked about at all (established by the
    caller from the ``native_builds`` table). Three of spec §6's own utterances -
    "Çalışıyor mu kontrol et.", "Hata varsa düzelt.", "Yeni sürümü build et." - carry no
    native noun whatsoever, so this flag is what keeps them from stealing "kontrol"/
    "düzelt"/"build" from M21's inbox check, M27's colour adjust and the research
    EXPLAIN branch when there is no build in the system - the same "context, never
    vocabulary alone" discipline ``operator_running``/``executive_run_state`` establish.

    ``executive_run_state`` is M26's own one fact (docs/M26_EXECUTIVE_AUTONOMY_SPEC.md §5,
    ADR-0089): one of ``app.executive.models.EXECUTIVE_RUN_STATE_VALUES`` when the owner
    has a run at all (established by the caller from the owner's most recent non-terminal,
    or otherwise most recent, ``ExecutiveRunRow``), else ``None`` — the same "context, never
    vocabulary alone" discipline ``operator_running``/``genesis_awaiting_approval`` already
    establish, so "Ne yapıyorsun?"/"Vazgeç" is read as EXEC_STATUS/EXEC_CANCEL only when
    there is a run to be asked about, never stolen from OPERATOR_STATUS/DISCARD otherwise.
    """
    normalized, tokens, dropped = normalize_transcript(text)
    confidence = 1.0 if dropped == 0 else 0.9
    base: dict[str, Any] = {
        "normalized_text": normalized,
        "tokens": tokens,
        "fillers_removed": dropped,
        "confidence": confidence,
    }
    if not tokens:
        return ResolvedIntent(Intent.NONE, **{**base, "confidence": 0.0})

    # The question table is consulted ONCE, here, and its answer serves both the
    # research interaction class (below) and the EXPLAIN branch further down - the two
    # can therefore never disagree about what kind of question was asked.
    explain_kind = _explain_kind(tokens, normalized)
    base["research_class"] = classify_research_interaction(
        tokens, has_completed_research=has_completed_research, query_kind=explain_kind
    )
    # ADR-0076: and WHICH research it points at. Decided from the same tokens, in the
    # same pass, so the class and the reference can never describe different utterances.
    base["reference"] = classify_research_reference(tokens, utterance=text)
    # B31 req 192: the mode in the owner's own words, kept with the turn.
    from app.research.policy import derive_mode_from_utterance

    base["research_mode"] = derive_mode_from_utterance(text)

    # 0. Active Eye privacy stop (M18 spec §2) — checked before even STOP. A camera
    #    disable phrase must never be shadowed by anything this resolver learns
    #    later, in any state, including mid-narration or mid-tool-call.
    if eye_matched := _eye_disable_match(tokens):
        return ResolvedIntent(
            Intent.EYE_DISABLE, scope=SCOPE_CONVERSATION, matched=eye_matched, **base
        )
    # 0b. The enable path (contract §2). Same place, same primitives, evaluated second so
    #     the disable direction wins whenever both could read.
    if eye_matched := _eye_enable_match(tokens):
        return ResolvedIntent(
            Intent.EYE_ENABLE, scope=SCOPE_CONVERSATION, matched=eye_matched, **base
        )

    # 0b'. M18.4 (spec §4): the owner's voice over self-evolution. Before the alarm and
    #      the display, because "kendi kendini geliştirmeyi kapat" carries "kapat" (which a
    #      ringing alarm would otherwise claim) and "bunu canlıya alma" carries "alma".
    if evolution_matched := _evolution_match(tokens):
        return ResolvedIntent(
            evolution_matched[0],
            scope=SCOPE_CONVERSATION,
            matched=evolution_matched[1],
            evolution_action=EVOLUTION_ACTION_BY_INTENT.get(evolution_matched[0]),
            **base,
        )

    # 0b'-bis. B35 (req 622/623): the owner assigns the system work on itself. After the
    #      evolution switch (same self-reference), before memory-correct and explain
    #      (which claimed "düzelt" / "hatayı" for themselves).
    if selfdev_matched := _selfdev_match(tokens):
        return ResolvedIntent(
            selfdev_matched[0],
            scope=SCOPE_CONVERSATION,
            matched=selfdev_matched[1],
            selfdev_request=text.strip() if selfdev_matched[0] != Intent.SELFDEV_STATUS else None,
            **base,
        )

    # 0b''. M27 (docs/M27_CREATIVE_TOOLS_SPEC.md §5, ADR-0093): the Creative Tools
    #       Operator. BEFORE the alarm/ambient block (0c) and before ARTIFACT_OPEN
    #       (0h) - the module comment above the ``_creative_*_match`` functions names
    #       the three real collisions this position resolves ("kaldır" vs. the
    #       alarm's own bare-wake fallback; "arka planda" vs. the research
    #       technical-explanation trigger; "bunu ... aç" vs. ARTIFACT_OPEN's own
    #       deictic open, resolved by CREATIVE_OPEN's own tool-word gate either way).
    # B43: the creative run in focus owns undo / redo / deliver; generation and the
    # photo fix are their own sentences. All BEFORE the M27 block so 'Paint'te göster'
    # with a run in focus is a delivery, not a fresh open.
    if lifecycle_creative := _creative_lifecycle_match(tokens, creative_focused=creative_focused):
        lc_intent, lc_text, lc_fields = lifecycle_creative
        return ResolvedIntent(lc_intent, matched=lc_text, **lc_fields, **base)
    if generate_matched := _creative_generate_match(tokens, text):
        return ResolvedIntent(
            Intent.CREATIVE_GENERATE,
            matched=generate_matched[0],
            creative_prompt=generate_matched[1],
            creative_tool=_creative_tool_from_tokens(tokens),
            **base,
        )
    if enhance_matched := _creative_enhance_match(tokens):
        return ResolvedIntent(
            Intent.CREATIVE_ENHANCE, matched=enhance_matched, creative_ref="current", **base
        )
    if creative_redraw_matched := _creative_redraw_match(tokens):
        return ResolvedIntent(
            Intent.CREATIVE_REDRAW,
            matched=creative_redraw_matched,
            creative_tool=_creative_tool_from_tokens(tokens),
            **base,
        )
    if creative_open_matched := _creative_open_match(tokens):
        return ResolvedIntent(
            Intent.CREATIVE_OPEN,
            matched=creative_open_matched,
            creative_tool=_creative_tool_from_tokens(tokens),
            creative_ref="current",
            **base,
        )
    if creative_background_matched := _creative_background_match(tokens):
        return ResolvedIntent(
            Intent.CREATIVE_BACKGROUND,
            matched=creative_background_matched,
            creative_ref="current",
            **base,
        )
    if creative_adjust_matched := _creative_adjust_match(tokens):
        return ResolvedIntent(
            Intent.CREATIVE_ADJUST,
            matched=creative_adjust_matched,
            creative_ref="current",
            **base,
        )
    if creative_cleanup_matched := _creative_cleanup_match(tokens):
        return ResolvedIntent(
            Intent.CREATIVE_CLEANUP,
            matched=creative_cleanup_matched,
            creative_ref="current",
            **base,
        )
    if creative_design_matched := _creative_design_match(tokens):
        return ResolvedIntent(
            Intent.CREATIVE_DESIGN,
            matched=creative_design_matched,
            creative_tool=_creative_tool_from_tokens(tokens),
            **base,
        )
    if creative_export_matched := _creative_export_match(tokens):
        return ResolvedIntent(
            Intent.CREATIVE_EXPORT,
            matched=creative_export_matched,
            creative_ref="current",
            creative_format=_creative_export_format_from_tokens(tokens),
            **base,
        )

    # 0b'''. M28 (docs/M28_NATIVE_APP_FACTORY_SPEC.md §6): the Native App Factory.
    #        Checked here - before the M19 operator block (0e), the M20 document block
    #        (0f), M23's App Factory block (0g-2) and M22's artifact block (0h) -
    #        because four MEASURED collisions live in those families' vocabulary; the
    #        module comment above ``_native_create_windows_match`` names each one and
    #        the NARROWING gate that closes it, so no branch below this one loses any
    #        ground it held before M28 existed. Order inside the block: the artefact
    #        matchers (exe / apk / kurulum) first, because "Windows için EXE çıkar."
    #        names an EXE rather than merely a platform; then the platform CREATE
    #        matchers; then the emulator; then the three noun-less, focus-gated ones.
    if native_exe_matched := _native_build_exe_match(tokens):
        return ResolvedIntent(
            Intent.NATIVE_BUILD_EXE,
            scope=SCOPE_CONVERSATION,
            matched=native_exe_matched,
            native_target=_NATIVE_TARGET_WINDOWS_EXE,
            native_ref="current",
            **base,
        )
    if native_apk_matched := _native_build_apk_match(tokens):
        return ResolvedIntent(
            Intent.NATIVE_BUILD_APK,
            scope=SCOPE_CONVERSATION,
            matched=native_apk_matched,
            native_target=_native_target_from_tokens(tokens),
            native_ref="current",
            **base,
        )
    if native_installer_matched := _native_build_installer_match(tokens):
        return ResolvedIntent(
            Intent.NATIVE_BUILD_INSTALLER,
            scope=SCOPE_CONVERSATION,
            matched=native_installer_matched,
            native_target=_NATIVE_TARGET_WINDOWS_MSIX,
            native_ref="current",
            **base,
        )
    if native_windows_matched := _native_create_windows_match(tokens):
        return ResolvedIntent(
            Intent.NATIVE_CREATE_WINDOWS,
            scope=SCOPE_CONVERSATION,
            matched=native_windows_matched,
            native_target=_NATIVE_TARGET_WINDOWS_EXE,
            **base,
        )
    if native_android_matched := _native_create_android_match(tokens):
        return ResolvedIntent(
            Intent.NATIVE_CREATE_ANDROID,
            scope=SCOPE_CONVERSATION,
            matched=native_android_matched,
            native_target=_NATIVE_TARGET_ANDROID_APK,
            # Spec §7: "Bunun Android sürümünü yap." points at a build that already
            # exists; a bare "Android sürümünü yap." names no antecedent, so the tool
            # resolves the project itself rather than being handed a wrong one.
            native_ref="current" if _has_exact(tokens, "bunun", "bunu") else None,
            **base,
        )
    if native_emulator_matched := _native_emulator_open_match(tokens):
        return ResolvedIntent(
            Intent.NATIVE_EMULATOR_OPEN,
            scope=SCOPE_CONVERSATION,
            matched=native_emulator_matched,
            native_target=_NATIVE_TARGET_ANDROID_APK,
            native_ref="current",
            **base,
        )
    # B33: the lifecycle after the build - each needs the focus (or the installer noun) the
    # same way check/fix/rebuild do, and refuses every other family's noun.
    if native_launch_matched := _native_launch_match(
        tokens, native_build_focused=native_build_focused
    ):
        return ResolvedIntent(
            Intent.NATIVE_LAUNCH,
            scope=SCOPE_CONVERSATION,
            matched=native_launch_matched,
            native_target=_native_target_from_tokens(tokens),
            native_ref="current",
            **base,
        )
    if native_verify_matched := _native_verify_match(
        tokens, native_build_focused=native_build_focused
    ):
        return ResolvedIntent(
            Intent.NATIVE_VERIFY,
            scope=SCOPE_CONVERSATION,
            matched=native_verify_matched,
            native_ref="current",
            **base,
        )
    if native_log_matched := _native_log_match(tokens, native_build_focused=native_build_focused):
        return ResolvedIntent(
            Intent.NATIVE_LOG,
            scope=SCOPE_CONVERSATION,
            matched=native_log_matched,
            native_ref="current",
            **base,
        )
    if native_uninstall_matched := _native_uninstall_match(
        tokens, native_build_focused=native_build_focused
    ):
        return ResolvedIntent(
            Intent.NATIVE_UNINSTALL,
            scope=SCOPE_CONVERSATION,
            matched=native_uninstall_matched,
            native_ref="current",
            **base,
        )
    if native_update_matched := _native_update_match(
        tokens, native_build_focused=native_build_focused
    ):
        return ResolvedIntent(
            Intent.NATIVE_UPDATE,
            scope=SCOPE_CONVERSATION,
            matched=native_update_matched,
            native_ref="current",
            **base,
        )
    if native_check_matched := _native_check_match(
        tokens, native_build_focused=native_build_focused
    ):
        return ResolvedIntent(
            Intent.NATIVE_CHECK,
            scope=SCOPE_CONVERSATION,
            matched=native_check_matched,
            native_target=_native_target_from_tokens(tokens),
            native_ref="current",
            **base,
        )
    if native_fix_matched := _native_fix_match(tokens, native_build_focused=native_build_focused):
        return ResolvedIntent(
            Intent.NATIVE_FIX,
            scope=SCOPE_CONVERSATION,
            matched=native_fix_matched,
            native_target=_native_target_from_tokens(tokens),
            native_ref="current",
            **base,
        )
    if native_rebuild_matched := _native_rebuild_match(
        tokens, native_build_focused=native_build_focused
    ):
        return ResolvedIntent(
            Intent.NATIVE_REBUILD,
            scope=SCOPE_CONVERSATION,
            matched=native_rebuild_matched,
            native_target=_native_target_from_tokens(tokens),
            native_ref="current",
            **base,
        )

    # 0c. M18.3 (spec §3.8, §6): the alarm and the display. BEFORE the generic stop check,
    #     because "Alarmı durdur" / "Alarmı sustur" / "Alarmı kes" are built from words that
    #     are also STOP_TOKENS, and a ringing alarm that answered "dur" by stopping the
    #     NARRATION would leave the owner listening to the alarm. `_alarm_match` requires an
    #     alarm noun (or "ertele", which means nothing else), so a bare "dur" is untouched
    #     and stop keeps its top priority everywhere it ever had it.
    #     The ambient POLICY phrases come before the bare display commands: "uyurken
    #     ekranları kapat" is a standing preference, "ekranları kapat" is a command for now.
    if ambient_matched := _ambient_policy_match(tokens):
        return ResolvedIntent(
            ambient_matched[0],
            scope=SCOPE_CONVERSATION,
            matched=ambient_matched[1],
            policy_changes=(
                ambient_policy_changes(tokens)
                if ambient_matched[0] is Intent.AMBIENT_POLICY_SET
                else None
            ),
            **base,
        )
    # 0c'. B14 (req 287-291, 296-299): the owner's own routines. BEFORE the alarm family,
    #      because "sabah rutinini durdur" carries the alarm's stop verb and "rutini iptal
    #      et" its cancel verb - the noun is what tells them apart, and reading a routine
    #      pause as an alarm cancellation is a mistake the owner discovers by oversleeping.
    if routine_matched := _routine_match(tokens):
        return ResolvedIntent(
            routine_matched[0],
            scope=SCOPE_CONVERSATION,
            matched=routine_matched[1],
            **base,
        )
    if alarm_matched := _alarm_match(
        tokens, alarm_ringing=alarm_ringing, event_focused=event_focused
    ):
        return ResolvedIntent(
            alarm_matched[0],
            scope=SCOPE_CONVERSATION,
            matched=alarm_matched[1],
            alarm_minutes=(
                spoken_minutes(tokens) if alarm_matched[0] is Intent.ALARM_SNOOZE else None
            ),
            **base,
        )
    if display_matched := _display_match(tokens):
        return ResolvedIntent(
            display_matched[0], scope=SCOPE_CONVERSATION, matched=display_matched[1], **base
        )
    # 0c''. B15 req 271: the clock, asked directly. AFTER the alarm and display families,
    #       because "Sabah alarmım kaçta?" is a question about an alarm and "saat yedide
    #       uyandır" is a request to be woken - both carry the clock's own noun.
    if clock_matched := _clock_match(tokens):
        return ResolvedIntent(
            Intent.CLOCK_QUERY, scope=SCOPE_CONVERSATION, matched=clock_matched[1], **base
        )

    # 0c'''. B27 req 731-735: the everyday sentences that reached nothing. HERE — after
    #        the alarm/routine/display/clock families (each of which takes its own noun
    #        first: "alarmı iptal et" is the alarm's) and BEFORE the operator's
    #        running-gated pair (0d) and the generic stop (1), because "Araştırmayı
    #        durdur." carries a STOP token and "Araştırmayı iptal et." the operator's
    #        cancel word, and both are about the research when the research is named.
    #        Every matcher below requires its own noun, so a bare "Dur." / "İptal et." is
    #        untouched and keeps the priority it always had.
    if capabilities_matched := _capabilities_query_match(tokens):
        return ResolvedIntent(
            Intent.CAPABILITIES_QUERY,
            scope=SCOPE_CONVERSATION,
            matched=capabilities_matched,
            capability_family=_capability_family(tokens),
            **base,
        )
    # B31 req 203/204/201/209: the research paused / resumed / opened by reference, and
    # the standing answer register - before cancel ("duraklat" is not "durdur"), before
    # the artifact family's open ("bir önceki araştırmayı aç" names a research, not the
    # last artifact), before the follow-up classification ("bundan sonra teknik anlat" is
    # a register, not one technical answer).
    if research_pause_matched := _research_pause_match(tokens):
        return ResolvedIntent(
            Intent.RESEARCH_PAUSE,
            scope=SCOPE_CONVERSATION,
            matched=research_pause_matched,
            **base,
        )
    if research_resume_matched := _research_resume_match(tokens):
        return ResolvedIntent(
            Intent.RESEARCH_RESUME,
            scope=SCOPE_CONVERSATION,
            matched=research_resume_matched,
            **base,
        )
    if research_open_matched := _research_open_match(tokens):
        return ResolvedIntent(
            Intent.RESEARCH_OPEN,
            scope=SCOPE_CONVERSATION,
            matched=research_open_matched,
            **base,
        )
    if answer_mode_matched := _answer_mode_match(tokens):
        answer_level, answer_mode_text = answer_mode_matched
        return ResolvedIntent(
            Intent.RESEARCH_ANSWER_MODE,
            scope=SCOPE_CONVERSATION,
            matched=answer_mode_text,
            answer_level=answer_level,
            **base,
        )
    if research_cancel_matched := _research_cancel_match(tokens):
        return ResolvedIntent(
            Intent.RESEARCH_CANCEL,
            scope=SCOPE_CONVERSATION,
            matched=research_cancel_matched,
            **base,
        )
    if volume_matched := _media_volume_match(tokens):
        volume_direction, volume_matched_text = volume_matched
        return ResolvedIntent(
            Intent.MEDIA_VOLUME,
            scope=SCOPE_CONVERSATION,
            matched=volume_matched_text,
            media_volume_direction=volume_direction,
            **base,
        )
    if screenshot_matched := _screenshot_match(tokens):
        return ResolvedIntent(
            Intent.SCREENSHOT_CAPTURE,
            scope=SCOPE_CONVERSATION,
            matched=screenshot_matched,
            **base,
        )
    if calendar_cancel_matched := _calendar_cancel_match(tokens):
        return ResolvedIntent(
            Intent.CALENDAR_CANCEL,
            scope=SCOPE_CONVERSATION,
            matched=calendar_cancel_matched,
            calendar_ref="current",
            **base,
        )

    # 0d. M19 (spec §3): the operator's Cancel/Status pair, gated on a task actually
    #     running right now - never on vocabulary alone, the same discipline the alarm's
    #     ringing-aware "Sustur." already established. Checked before the generic stop (1)
    #     so "Dur." while a task runs is never read as a narration stop instead.
    # 0d-0. B39 (req 129/130): the mission's own words, gated on a mission actually
    #       parked or running (the caller's ``mission_state``), before the generic
    #       cancel/status pair so "Evet, başla" while a plan waits is never a bare yes.
    if mission_state == "awaiting_approval" and (approve_mission := _mission_approve_match(tokens)):
        return ResolvedIntent(
            Intent.MISSION_APPROVE,
            scope=SCOPE_CONVERSATION,
            matched=approve_mission,
            mission_action="approve",
            **base,
        )
    if mission_state in ("running", "planned") and (pause_mission := _mission_pause_match(tokens)):
        return ResolvedIntent(
            Intent.MISSION_PAUSE,
            scope=SCOPE_CONVERSATION,
            matched=pause_mission,
            mission_action="pause",
            **base,
        )
    if mission_state == "paused" and (resume_mission := _mission_resume_match(tokens)):
        return ResolvedIntent(
            Intent.MISSION_RESUME,
            scope=SCOPE_CONVERSATION,
            matched=resume_mission,
            mission_action="resume",
            **base,
        )
    if operator_running:
        if cancel_matched := _operator_cancel_match(tokens):
            return ResolvedIntent(
                Intent.OPERATOR_CANCEL, scope=SCOPE_CONVERSATION, matched=cancel_matched, **base
            )
        if status_matched := _operator_status_match(tokens):
            return ResolvedIntent(
                Intent.OPERATOR_STATUS, scope=SCOPE_CONVERSATION, matched=status_matched, **base
            )

    # 0e. M19 (spec §2, §3): the rest of the Digital Operator - a shell reading, the
    #     window-control family, typing into the focused control, opening an application.
    #     Each requires its own verb/noun combination (the per-function docstrings say
    #     which), so none of these can shadow a phrase this resolver already owned.
    if shell_matched := _shell_query_match(tokens):
        return ResolvedIntent(
            Intent.SHELL_QUERY,
            scope=SCOPE_CONVERSATION,
            matched=shell_matched[1],
            shell_query=shell_matched[0],
            **base,
        )
    # 0e-0. B39 (req 127): a sentence that is MORE than one plan, or one no fixed plan
    #       serves, is a mission - asked of the planner itself (its docstring says
    #       what stays with the tools below).
    if mission_matched := _mission_start_match(tokens, text):
        return ResolvedIntent(
            Intent.MISSION_START,
            scope=SCOPE_CONVERSATION,
            matched=mission_matched,
            mission_request=text.strip(),
            mission_action="start",
            **base,
        )
    if window_matched := _window_control_match(tokens):
        window_intent, window_matched_text, window_ref = window_matched
        return ResolvedIntent(
            window_intent,
            scope=SCOPE_CONVERSATION,
            matched=window_matched_text,
            window_ref=window_ref,
            **base,
        )
    # B34 req 153-167, 170: the managed mutations. Every one needs the document noun and
    # refuses another family's noun; the duplicate noun keeps B32's dedup below.
    if undo_matched := _document_undo_match(tokens):
        return ResolvedIntent(
            Intent.DOCUMENT_UNDO, scope=SCOPE_CONVERSATION, matched=undo_matched, **base
        )
    if versions_matched := _document_versions_match(tokens):
        return ResolvedIntent(
            Intent.DOCUMENT_VERSIONS,
            scope=SCOPE_CONVERSATION,
            matched=versions_matched,
            document_ref="current",
            **base,
        )
    if (edit_match := _document_edit_match(tokens)) is not None:
        edit_matched, find_text, replace_text = edit_match
        return ResolvedIntent(
            Intent.DOCUMENT_EDIT,
            scope=SCOPE_CONVERSATION,
            matched=edit_matched,
            document_ref="current",
            find_text=find_text,
            replace_text=replace_text,
            **base,
        )
    if (append_match := _document_append_match(tokens)) is not None:
        append_matched, append_text = append_match
        return ResolvedIntent(
            Intent.DOCUMENT_APPEND,
            scope=SCOPE_CONVERSATION,
            matched=append_matched,
            document_ref="current",
            text_to_type=append_text,
            **base,
        )
    if (rename_match := _document_rename_match(tokens, text)) is not None:
        rename_matched, rename_name = rename_match
        return ResolvedIntent(
            Intent.DOCUMENT_RENAME,
            scope=SCOPE_CONVERSATION,
            matched=rename_matched,
            document_ref="current",
            new_name=rename_name,
            **base,
        )
    if move_matched := _document_move_match(tokens):
        return ResolvedIntent(
            Intent.DOCUMENT_MOVE,
            scope=SCOPE_CONVERSATION,
            matched=move_matched,
            document_ref="current",
            folder=_extract_document_folder(tokens),
            **base,
        )
    if (copy_match := _document_copy_match(tokens, text)) is not None:
        copy_matched, copy_name = copy_match
        return ResolvedIntent(
            Intent.DOCUMENT_COPY,
            scope=SCOPE_CONVERSATION,
            matched=copy_matched,
            document_ref="current",
            folder=_extract_document_folder(tokens),
            new_name=copy_name,
            **base,
        )
    if delete_matched := _document_delete_match(tokens):
        return ResolvedIntent(
            Intent.DOCUMENT_DELETE,
            scope=SCOPE_CONVERSATION,
            matched=delete_matched,
            document_ref="current",
            **base,
        )
    if (write_match := _document_write_match(tokens, text)) is not None:
        write_matched, write_name = write_match
        return ResolvedIntent(
            Intent.DOCUMENT_WRITE,
            scope=SCOPE_CONVERSATION,
            matched=write_matched,
            folder=_extract_document_folder(tokens),
            new_name=write_name,
            **base,
        )
    if type_matched := _type_text_match(tokens):
        return ResolvedIntent(
            Intent.TYPE_TEXT,
            scope=SCOPE_CONVERSATION,
            matched=type_matched,
            text_to_type=_extract_type_text(text),
            window_ref="current",
            **base,
        )
    # 0e-1. B28 req 92/93/98: a key, a chord, a scroll - each with its own noun (a key
    #       name or a direction) AND its own verb (bas / kaydır), so "Dur." and "Yaz."
    #       are untouched and the alarm's "bas"-less vocabulary never gets here.
    # 0e-000. B32 req 141: "Görseldeki metni oku" names a PICTURE's text - before the
    #         screen-reading family (0e-0), which owns a bare "metni oku".
    if image_text_matched := _image_text_match(tokens):
        return ResolvedIntent(
            Intent.IMAGE_TEXT, scope=SCOPE_CONVERSATION, matched=image_text_matched, **base
        )
    # 0e-00. B30 req 82/119-122: an application closed BY NAME, a service or a process
    #        asked about or acted on by name. Before the window family ("Chrome'u kapat"
    #        names an application, not a window) and before the generic repeat ("yeniden
    #        başlat" carries the repeat word).
    if service_matched := _service_match(tokens):
        service_intent, service_word = service_matched
        return ResolvedIntent(
            service_intent,
            scope=SCOPE_CONVERSATION,
            matched="servis",
            service_name=service_word,
            **base,
        )
    if process_matched := _process_match(tokens):
        process_intent, process_word, matched_text = process_matched
        return ResolvedIntent(
            process_intent,
            scope=SCOPE_CONVERSATION,
            matched=matched_text,
            process_name=process_word,
            **base,
        )
    if app_close_matched := _app_close_match(tokens):
        return ResolvedIntent(
            Intent.APP_CLOSE,
            scope=SCOPE_CONVERSATION,
            matched="uygulamayı kapat",
            application=app_close_matched,
            **base,
        )
    # 0e-0. B29 req 100/102/105: a NAMED button, a control's text, the screen described.
    #       Before the key press: "Tamam düğmesine bas" carries the press verb and names
    #       a button, not a key.
    if ui_invoke_matched := _ui_invoke_match(tokens, text):
        return ResolvedIntent(
            Intent.UI_INVOKE,
            scope=SCOPE_CONVERSATION,
            matched="düğmeye tıkla",
            ui_target=ui_invoke_matched,
            window_ref="current",
            **base,
        )
    if ui_read_matched := _ui_read_match(tokens):
        return ResolvedIntent(
            Intent.UI_READ,
            scope=SCOPE_CONVERSATION,
            matched=ui_read_matched[0],
            ui_target=ui_read_matched[1],
            window_ref="current",
            **base,
        )
    if describe_matched := _screen_describe_match(tokens):
        return ResolvedIntent(
            Intent.SCREEN_DESCRIBE,
            scope=SCOPE_CONVERSATION,
            matched=describe_matched,
            **base,
        )
    if key_matched := _key_press_match(tokens):
        return ResolvedIntent(
            Intent.OPERATOR_KEY,
            scope=SCOPE_CONVERSATION,
            matched="tuşa bas",
            key_press=key_matched,
            window_ref="current",
            **base,
        )
    if scroll_matched := _scroll_match(tokens):
        return ResolvedIntent(
            Intent.OPERATOR_SCROLL,
            scope=SCOPE_CONVERSATION,
            matched="kaydır",
            scroll_direction=scroll_matched,
            window_ref="current",
            **base,
        )
    if app_matched := _app_open_match(tokens):
        app_canonical, app_matched_text = app_matched
        return ResolvedIntent(
            Intent.APP_OPEN,
            scope=SCOPE_CONVERSATION,
            matched=app_matched_text,
            application=app_canonical,
            **base,
        )

    # 0e-2. M26 (docs/M26_EXECUTIVE_AUTONOMY_SPEC.md §5): Executive Autonomy. Checked
    #       HERE, before the M20 document block (DOCUMENT_COMPARE also owns
    #       "karşılaştır") and before the M21 mail block (MAIL_THREAD/DISCARD also own
    #       "zincir"/"vazgeç") — a multi-step directive or a live run's own control word
    #       must never be swallowed by either single-step family. The active-run-gated
    #       six are checked FIRST (a run in progress outranks starting a new one on the
    #       same ambiguous words, and there are <= 2 active runs to begin with, spec
    #       §4), then EXEC_START.
    if executive_matched := _executive_active_match(tokens, run_state=executive_run_state):
        exec_intent, exec_matched_text = executive_matched
        return ResolvedIntent(
            exec_intent,
            scope=SCOPE_CONVERSATION,
            matched=exec_matched_text,
            exec_step_ordinal=(
                _exec_step_ordinal(tokens) if exec_intent == Intent.EXEC_RETRY else None
            ),
            exec_kind_hint=(_exec_kind_hint(tokens) if exec_intent == Intent.EXEC_RETRY else None),
            exec_amend_kind=(
                _exec_amend_kind(tokens) if exec_intent == Intent.EXEC_AMEND else None
            ),
            **base,
        )
    if exec_start_matched := _executive_start_match(tokens):
        shape, exec_start_text = exec_start_matched
        return ResolvedIntent(
            Intent.EXEC_START,
            scope=SCOPE_CONVERSATION,
            matched=exec_start_text,
            exec_shape=shape,
            folder=_extract_document_folder(tokens) if shape == "folder_compare" else None,
            **base,
        )

    # 0e-3. M26 addendum (docs/M26_LATEST_NEWS_MODE_SPEC.md §6): Latest News Mode. Checked HERE,
    #       before the M20 document block (DOCUMENT_SUMMARIZE also owns "özetle") and
    #       before the generic SUMMARIZE/TECHNICAL control branches far below (which own
    #       "özetle"/"anlat" with no noun at all) - "haberleri özetle" must resolve to a
    #       NEWS summary, never a document summary or a bare narration control, and
    #       "haberleri aç" must never be read as APP_OPEN/DISPLAY_WAKE/EYE_ENABLE (each
    #       of those requires its OWN noun, which "haber" is not).
    if news_matched := _news_match(tokens):
        news_intent, news_matched_text = news_matched
        return ResolvedIntent(
            news_intent,
            scope=SCOPE_CONVERSATION,
            matched=news_matched_text,
            news_source_ref=_news_source_ref(tokens),
            **base,
        )

    # 0e-4. ADR-0112: media the owner named. AFTER the news block on purpose - "haberleri
    #       aç" is Latest News Mode's and _media_match refuses outright when the news noun
    #       is present - and after the M19 application/window families for the same reason
    #       in reverse: this matcher needs an explicit media marker, so "Chrome'u aç" and
    #       "pencereyi aç" never reach it at all.
    if media_matched := _media_match(tokens, text):
        media_intent, media_matched_text = media_matched
        return ResolvedIntent(
            media_intent,
            scope=SCOPE_CONVERSATION,
            matched=media_matched_text,
            media_query=(_extract_media_query(text) if media_intent is Intent.MEDIA_PLAY else None),
            **base,
        )

    # 0f. M20 (docs/M20_FILE_DOCUMENT_INTELLIGENCE_SPEC.md §3): File & Document
    #     Intelligence. Checked here, alongside the rest of the M19/M20 device-reading
    #     family and before the generic stop/presentation branches, for the same reason
    #     app_open is: none of these words mean anything else this resolver already
    #     claimed higher up, and DOCUMENT_SUMMARIZE must win over the plain SUMMARIZE
    #     control intent whenever a document (not a research) is what "bunu" points at.
    # B32: the picture's text and headers, the preview, the full-text search, the
    # duplicate proposal and its confirmation - each needs its own noun, so none can take a
    # sentence from the families below; checked before them because "önizle" carries no
    # document verb the rest would recognise and "görseldeki metni oku" must beat the
    # screen-reading family's bare "metni oku".
    if image_meta_matched := _image_metadata_match(tokens):
        return ResolvedIntent(
            Intent.IMAGE_METADATA, scope=SCOPE_CONVERSATION, matched=image_meta_matched, **base
        )
    if dedup_matched := _document_dedup_match(tokens):
        return ResolvedIntent(
            Intent.DOCUMENT_DEDUP, scope=SCOPE_CONVERSATION, matched=dedup_matched, **base
        )
    if duplicates_matched := _document_duplicates_match(tokens):
        return ResolvedIntent(
            Intent.DOCUMENT_DUPLICATES,
            scope=SCOPE_CONVERSATION,
            matched=duplicates_matched,
            **base,
        )
    if find_text_matched := _document_find_text_match(tokens):
        text_query, find_text_text = find_text_matched
        return ResolvedIntent(
            Intent.DOCUMENT_FIND_TEXT,
            scope=SCOPE_CONVERSATION,
            matched=find_text_text,
            text_query=text_query,
            **base,
        )
    if preview_matched := _document_preview_match(tokens):
        return ResolvedIntent(
            Intent.DOCUMENT_PREVIEW,
            scope=SCOPE_CONVERSATION,
            matched=preview_matched,
            document_ref="current",
            **base,
        )
    if previous_matched := _document_previous_match(tokens):
        return ResolvedIntent(
            Intent.DOCUMENT_PREVIOUS,
            scope=SCOPE_CONVERSATION,
            matched=previous_matched,
            document_ref="previous",
            **base,
        )
    if compare_matched := _document_compare_match(tokens):
        return ResolvedIntent(
            Intent.DOCUMENT_COMPARE,
            scope=SCOPE_CONVERSATION,
            matched=compare_matched,
            document_ref="current",
            **base,
        )
    if read_matched := _document_read_match(tokens):
        return ResolvedIntent(
            Intent.DOCUMENT_READ,
            scope=SCOPE_CONVERSATION,
            matched=read_matched,
            document_ref="current",
            **base,
        )
    if summarize_matched := _document_summarize_match(tokens, document_focused=document_focused):
        return ResolvedIntent(
            Intent.DOCUMENT_SUMMARIZE,
            scope=SCOPE_CONVERSATION,
            matched=summarize_matched,
            document_ref="current",
            **base,
        )
    if common_points_matched := _document_common_points_match(tokens):
        return ResolvedIntent(
            Intent.DOCUMENT_COMMON_POINTS,
            scope=SCOPE_CONVERSATION,
            matched=common_points_matched,
            **base,
        )
    if inspect_matched := _document_inspect_match(tokens):
        return ResolvedIntent(
            Intent.DOCUMENT_INSPECT,
            scope=SCOPE_CONVERSATION,
            matched=inspect_matched,
            document_ref="current",
            **base,
        )
    if search_matched := _document_search_match(tokens):
        return ResolvedIntent(
            Intent.FILE_SEARCH,
            scope=SCOPE_CONVERSATION,
            matched=search_matched,
            pattern=_extract_document_pattern(tokens),
            folder=_extract_document_folder(tokens),
            extensions=_extract_document_extensions(tokens),
            **base,
        )
    if answer_matched := _document_answer_named_match(tokens):
        return ResolvedIntent(
            Intent.DOCUMENT_ANSWER,
            scope=SCOPE_CONVERSATION,
            matched=answer_matched,
            document_ref="current",
            question=text,
            **base,
        )
    if generic_answer_matched := _document_answer_generic_match(
        tokens, document_focused=document_focused, explain_kind=explain_kind
    ):
        return ResolvedIntent(
            Intent.DOCUMENT_ANSWER,
            scope=SCOPE_CONVERSATION,
            matched=generic_answer_matched,
            document_ref="current",
            question=text,
            **base,
        )

    # 0f-2. M24 (docs/M24_CAPABILITY_GENESIS_SPEC.md §6): Capability Genesis, checked
    #       BEFORE the M21 block below because CAPABILITY_APPROVE/CANCEL overlap real
    #       vocabulary CALENDAR_COMMIT/DISCARD already claim unconditionally
    #       ("onaylıyorum"/"vazgeç") — gated on ``genesis_awaiting_approval`` so neither
    #       matcher is even consulted, and both words fall through UNCHANGED to their
    #       existing targets, when no genesis run is actually parked in this session
    #       (module comment above the matchers). CAPABILITY_REQUEST/STATUS need no gate:
    #       a catalogue miss (the overwhelming majority — the catalogue is empty outside
    #       tests) means neither can ever fire.
    if genesis_awaiting_approval and (approve_matched := _capability_approve_match(tokens)):
        return ResolvedIntent(
            Intent.CAPABILITY_APPROVE, scope=SCOPE_CONVERSATION, matched=approve_matched, **base
        )
    if genesis_awaiting_approval and (cancel_matched := _capability_cancel_match(tokens)):
        return ResolvedIntent(
            Intent.CAPABILITY_CANCEL, scope=SCOPE_CONVERSATION, matched=cancel_matched, **base
        )
    if status_matched := _capability_status_match(tokens):
        return ResolvedIntent(
            Intent.CAPABILITY_STATUS, scope=SCOPE_CONVERSATION, matched=status_matched, **base
        )
    if request_matched := _capability_request_match(tokens):
        entry, operation_id, matched_label = request_matched
        return ResolvedIntent(
            Intent.CAPABILITY_REQUEST,
            scope=SCOPE_CONVERSATION,
            matched=matched_label,
            capability_target_name=entry.name,
            capability_target_url=entry.url,
            capability_operation=operation_id,
            **base,
        )

    # 0f-3. M25 (docs/M25_CREATIVE_3D_SPEC.md §5): 3D Creation, alongside the rest of
    #       the M19-M24 device/account-reading family, for the same reason the
    #       capability-genesis block above is here: none of these words mean anything
    #       else this resolver already claimed higher up (every matcher gated on its
    #       own noun - module comment above ``_SCENE_NOUN_STEMS``). INSPECT is checked
    #       right after CREATE because they share the "sahne" noun (no other overlap);
    #       ADD before LIGHT/CAMERA so "Bir ışık ekle."/"Kamera ekle." are never
    #       swallowed by the more general noun-only LIGHT/CAMERA matches.
    scene_tool = _scene_tool_from_tokens(tokens)
    # B44 (req 526, 527): export and animation first - "küreye bir animasyon ekle"
    # carries SCENE_ADD's noun and verb, and must not become a new sphere.
    if scene_export_matched := _scene_export_match(tokens):
        return ResolvedIntent(
            Intent.SCENE_EXPORT,
            scope=SCOPE_CONVERSATION,
            matched=scene_export_matched,
            scene_tool=scene_tool,
            scene_ref="current",
            scene_format=_scene_export_format_from_tokens(tokens),
            **base,
        )
    if scene_animate_matched := _scene_animate_match(tokens):
        return ResolvedIntent(
            Intent.SCENE_ANIMATE,
            scope=SCOPE_CONVERSATION,
            matched=scene_animate_matched,
            scene_tool=scene_tool,
            scene_ref="current",
            scene_kind=_scene_kind_from_tokens(tokens),
            **base,
        )
    if scene_create_matched := _scene_create_match(tokens):
        return ResolvedIntent(
            Intent.SCENE_CREATE,
            scope=SCOPE_CONVERSATION,
            matched=scene_create_matched,
            scene_tool=scene_tool,
            **base,
        )
    if scene_inspect_matched := _scene_inspect_match(tokens):
        return ResolvedIntent(
            Intent.SCENE_INSPECT,
            scope=SCOPE_CONVERSATION,
            matched=scene_inspect_matched,
            scene_tool=scene_tool,
            scene_ref="current",
            **base,
        )
    if scene_add_matched := _scene_add_match(tokens):
        return ResolvedIntent(
            Intent.SCENE_ADD,
            scope=SCOPE_CONVERSATION,
            matched=scene_add_matched,
            scene_tool=scene_tool,
            scene_ref="current",
            scene_kind=_scene_kind_from_tokens(tokens),
            **base,
        )
    if scene_transform_matched := _scene_transform_match(tokens):
        return ResolvedIntent(
            Intent.SCENE_TRANSFORM,
            scope=SCOPE_CONVERSATION,
            matched=scene_transform_matched,
            scene_tool=scene_tool,
            scene_ref="current",
            **base,
        )
    if scene_material_matched := _scene_material_match(tokens):
        return ResolvedIntent(
            Intent.SCENE_MATERIAL,
            scope=SCOPE_CONVERSATION,
            matched=scene_material_matched,
            scene_tool=scene_tool,
            scene_ref="current",
            **base,
        )
    if scene_light_matched := _scene_light_match(tokens):
        return ResolvedIntent(
            Intent.SCENE_LIGHT,
            scope=SCOPE_CONVERSATION,
            matched=scene_light_matched,
            scene_tool=scene_tool,
            scene_ref="current",
            **base,
        )
    if scene_camera_matched := _scene_camera_match(tokens):
        return ResolvedIntent(
            Intent.SCENE_CAMERA,
            scope=SCOPE_CONVERSATION,
            matched=scene_camera_matched,
            scene_tool=scene_tool,
            scene_ref="current",
            **base,
        )
    if scene_render_matched := _scene_render_match(tokens):
        return ResolvedIntent(
            Intent.SCENE_RENDER,
            scope=SCOPE_CONVERSATION,
            matched=scene_render_matched,
            scene_tool=scene_tool,
            scene_ref="current",
            **base,
        )

    # 0g. M21 (docs/M21_MAIL_CALENDAR_SPEC.md §3): Mail & Calendar, alongside the rest of
    #     the M19/M20/M21 device/account-reading family, for the same reason app_open and
    #     the documents block are here: none of these words mean anything else this
    #     resolver already claimed higher up. CALENDAR_COMMIT is checked BEFORE
    #     CALENDAR_PROPOSE because "Tamam, ekle." shares the word "ekle" with a fresh
    #     "... ekle" proposal, and the explicit "tamam" must win the tie. MAIL_SEND and
    #     CALENDAR_COMMIT match on vocabulary ALONE — with nothing pending, the tool they
    #     name (mail.send / calendar.commit) still runs and answers with an honest
    #     clarification ("Neyi göndereyim?"/"Neyi onaylayayım?") from its OWN service
    #     layer, never a guess made here; ``draft_pending``/``proposal_pending`` decide
    #     only which of the two capabilities a bare DISCARD targets.
    # B34 req 166: a bare "Uygula." / "Kaydet." is the pending FILE change's confirmation
    # while one is pending for this session - checked before M21's own confirmations.
    if apply_matched := _document_apply_match(tokens, mutation_pending=mutation_pending):
        return ResolvedIntent(
            Intent.DOCUMENT_APPLY, scope=SCOPE_CONVERSATION, matched=apply_matched, **base
        )
    if commit_matched := _calendar_commit_match(tokens):
        return ResolvedIntent(
            Intent.CALENDAR_COMMIT, scope=SCOPE_CONVERSATION, matched=commit_matched, **base
        )
    if send_matched := _mail_send_match(tokens):
        return ResolvedIntent(
            Intent.MAIL_SEND, scope=SCOPE_CONVERSATION, matched=send_matched, **base
        )
    if discard_matched := _discard_word_match(tokens):
        if mutation_pending and not draft_pending and not proposal_pending:
            capability = "document.discard"
        else:
            capability = (
                "calendar.discard" if (proposal_pending and not draft_pending) else "mail.discard"
            )
        return ResolvedIntent(
            Intent.DISCARD,
            scope=SCOPE_CONVERSATION,
            matched=discard_matched,
            klass=KLASS_ACTION,
            capability=capability,
            **base,
        )
    if reschedule_matched := _calendar_reschedule_match(tokens, event_focused=event_focused):
        return ResolvedIntent(
            Intent.CALENDAR_PROPOSE,
            scope=SCOPE_CONVERSATION,
            matched=reschedule_matched,
            calendar_ref="current",
            **base,
        )
    if propose_matched := _calendar_propose_match(tokens):
        return ResolvedIntent(
            Intent.CALENDAR_PROPOSE,
            scope=SCOPE_CONVERSATION,
            matched=propose_matched,
            calendar_rrule=calendar_tr_time.extract_recurrence(text),
            calendar_reminder_minutes=calendar_tr_time.extract_reminder_minutes(text),
            **base,
        )
    if read_proposal_matched := _calendar_read_proposal_match(tokens):
        return ResolvedIntent(
            Intent.CALENDAR_READ_PROPOSAL,
            scope=SCOPE_CONVERSATION,
            matched=read_proposal_matched,
            **base,
        )
    if find_slot_matched := _calendar_find_slot_match(tokens):
        return ResolvedIntent(
            Intent.CALENDAR_FIND_SLOT,
            scope=SCOPE_CONVERSATION,
            matched=find_slot_matched,
            **base,
        )
    if agenda_matched := _calendar_agenda_match(tokens):
        return ResolvedIntent(
            Intent.CALENDAR_AGENDA, scope=SCOPE_CONVERSATION, matched=agenda_matched, **base
        )
    if read_draft_matched := _mail_read_draft_match(tokens):
        return ResolvedIntent(
            Intent.MAIL_READ_DRAFT, scope=SCOPE_CONVERSATION, matched=read_draft_matched, **base
        )
    if edit_draft_matched := _mail_edit_draft_match(tokens):
        return ResolvedIntent(
            Intent.MAIL_EDIT_DRAFT, scope=SCOPE_CONVERSATION, matched=edit_draft_matched, **base
        )
    if draft_reply_matched := _mail_draft_reply_match(tokens):
        return ResolvedIntent(
            Intent.MAIL_DRAFT_REPLY,
            scope=SCOPE_CONVERSATION,
            matched=draft_reply_matched,
            mail_ref="current",
            **base,
        )
    if draft_new_matched := _mail_draft_new_match(tokens):
        return ResolvedIntent(
            Intent.MAIL_DRAFT_NEW, scope=SCOPE_CONVERSATION, matched=draft_new_matched, **base
        )
    if thread_matched := _mail_thread_match(tokens):
        return ResolvedIntent(
            Intent.MAIL_THREAD,
            scope=SCOPE_CONVERSATION,
            matched=thread_matched,
            mail_ref="current",
            **base,
        )
    # B45 (req 347, 348): a message's attachments - the save form first ("eki kaydet").
    if save_attachment_matched := _mail_save_attachment_match(tokens):
        return ResolvedIntent(
            Intent.MAIL_SAVE_ATTACHMENT,
            scope=SCOPE_CONVERSATION,
            matched=save_attachment_matched,
            mail_ref="current",
            **base,
        )
    if attachments_matched := _mail_attachments_match(tokens):
        return ResolvedIntent(
            Intent.MAIL_ATTACHMENTS,
            scope=SCOPE_CONVERSATION,
            matched=attachments_matched,
            mail_ref="current",
            **base,
        )
    if mail_read_matched := _mail_read_match(tokens):
        return ResolvedIntent(
            Intent.MAIL_READ, scope=SCOPE_CONVERSATION, matched=mail_read_matched, **base
        )
    if mail_search_matched := _mail_search_match(tokens):
        return ResolvedIntent(
            Intent.MAIL_SEARCH, scope=SCOPE_CONVERSATION, matched=mail_search_matched, **base
        )
    if mail_inbox_matched := _mail_inbox_match(tokens):
        return ResolvedIntent(
            Intent.MAIL_INBOX, scope=SCOPE_CONVERSATION, matched=mail_inbox_matched, **base
        )

    # 0g-1a. ADR-0091: Owner Location Context, Live Weather, Morning Briefing. Checked
    #        here, alongside the rest of the device/account-reading family, for the same
    #        reason the mail/calendar block above is here: none of this vocabulary
    #        ("hava", "konum", "sabah", "sistem", "gece") means anything else this
    #        resolver already claimed higher up. LOCATION_DEFAULT_SET is checked before
    #        LOCATION_DEFAULT_QUERY because both share "varsayılan"+"konum" and only the
    #        SET phrasing also carries a verb ("yap"/"ayarla"); WEATHER_QUERY is checked
    #        before the narrower SYSTEM_STATUS/OVERNIGHT_WORK queries only because their
    #        vocabularies are disjoint ("hava"/"derece"/"yağmur" vs "sistem"/"gece"), not
    #        because either could swallow the other.
    if location_set_matched := _location_default_set_match(tokens):
        return ResolvedIntent(
            Intent.LOCATION_DEFAULT_SET,
            scope=SCOPE_CONVERSATION,
            matched=location_set_matched,
            location_default_city=_extract_place(tokens),
            **base,
        )
    if location_default_matched := _location_default_query_match(tokens):
        return ResolvedIntent(
            Intent.LOCATION_DEFAULT_QUERY,
            scope=SCOPE_CONVERSATION,
            matched=location_default_matched,
            **base,
        )
    if location_source_matched := _location_source_query_match(tokens):
        return ResolvedIntent(
            Intent.LOCATION_SOURCE_QUERY,
            scope=SCOPE_CONVERSATION,
            matched=location_source_matched,
            **base,
        )
    if weather_matched := _weather_query_match(tokens):
        return ResolvedIntent(
            Intent.WEATHER_QUERY,
            scope=SCOPE_CONVERSATION,
            matched=weather_matched,
            weather_place=_extract_place(tokens),
            **base,
        )
    if system_status_matched := _system_status_query_match(tokens):
        return ResolvedIntent(
            Intent.SYSTEM_STATUS_QUERY,
            scope=SCOPE_CONVERSATION,
            matched=system_status_matched,
            **base,
        )
    if overnight_matched := _overnight_work_query_match(tokens):
        return ResolvedIntent(
            Intent.OVERNIGHT_WORK_QUERY,
            scope=SCOPE_CONVERSATION,
            matched=overnight_matched,
            **base,
        )
    if morning_matched := _morning_briefing_match(tokens):
        return ResolvedIntent(
            Intent.MORNING_BRIEFING,
            scope=SCOPE_CONVERSATION,
            matched=morning_matched,
            **base,
        )

    # 0g-2. M23 (docs/M23_APP_FACTORY_SPEC.md §5): the App Factory. Checked BEFORE the
    #       M22 artifact block below because ARTIFACT_LIST's own vocabulary ("hangi" +
    #       "yaptın") would otherwise also match "Hangi uygulamaları yaptın?" - every
    #       matcher here is gated on the "uygulama"/"proje" noun (module comment above
    #       ``_APP_NOUN_STEMS``), so nothing above or below this block loses any ground:
    #       an utterance about an artifact never carries that noun, and an utterance
    #       about an app never reaches the artifact block at all once this one claims it.
    #       Order mirrors the artifact block's own reasoning: LIST (interrogative) before
    #       STATUS/TEST/STOP/OPEN (each own their own exact vocabulary) before RUN (the
    #       most general "app noun + a run-shaped verb") before CREATE (checked last so
    #       none of the above's more specific phrasing is ever swallowed by it).
    if app_list_matched := _appfactory_list_match(tokens):
        return ResolvedIntent(
            Intent.APP_FACTORY_LIST, scope=SCOPE_CONVERSATION, matched=app_list_matched, **base
        )
    if app_status_matched := _appfactory_status_match(tokens):
        return ResolvedIntent(
            Intent.APP_FACTORY_STATUS,
            scope=SCOPE_CONVERSATION,
            matched=app_status_matched,
            app_ref="current",
            **base,
        )
    # B41 (req 440-452): a generated application in focus owns its lifecycle words.
    if lifecycle_matched := _appfactory_lifecycle_match(
        tokens, text, app_project_focused=app_project_focused
    ):
        lifecycle_intent, lifecycle_text, lifecycle_fields = lifecycle_matched
        return ResolvedIntent(
            lifecycle_intent,
            scope=SCOPE_CONVERSATION,
            matched=lifecycle_text,
            app_ref="current",
            **lifecycle_fields,
            **base,
        )
    if app_fix_matched := _appfactory_fix_match(tokens):
        return ResolvedIntent(
            Intent.APP_FACTORY_FIX,
            scope=SCOPE_CONVERSATION,
            matched=app_fix_matched,
            app_ref="current",
            **base,
        )
    if app_test_matched := _appfactory_test_match(tokens):
        return ResolvedIntent(
            Intent.APP_FACTORY_TEST,
            scope=SCOPE_CONVERSATION,
            matched=app_test_matched,
            app_ref="current",
            **base,
        )
    if app_stop_matched := _appfactory_stop_match(tokens):
        return ResolvedIntent(
            Intent.APP_FACTORY_STOP,
            scope=SCOPE_CONVERSATION,
            matched=app_stop_matched,
            app_ref="current",
            **base,
        )
    if app_open_matched := _appfactory_open_match(tokens):
        return ResolvedIntent(
            Intent.APP_FACTORY_OPEN,
            scope=SCOPE_CONVERSATION,
            matched=app_open_matched,
            app_ref="current",
            **base,
        )
    if app_run_matched := _appfactory_run_match(tokens):
        return ResolvedIntent(
            Intent.APP_FACTORY_RUN,
            scope=SCOPE_CONVERSATION,
            matched=app_run_matched,
            app_ref="current",
            **base,
        )
    if app_create_matched := _appfactory_create_match(tokens):
        return ResolvedIntent(
            Intent.APP_FACTORY_CREATE,
            scope=SCOPE_CONVERSATION,
            matched=app_create_matched,
            app_template=_appfactory_template_from_tokens(tokens),
            app_name=_extract_app_name(text),
            app_commands=_extract_app_commands(text),
            # B40 (req 422): no built-in template fits - the whole sentence goes to the
            # requirements parser, never dropped.
            app_request=(
                text.strip() if _appfactory_template_from_tokens(tokens) is None else None
            ),
            **base,
        )

    # 0h. M22 (docs/M22_ARTIFACT_FACTORY_SPEC.md §5): the Artifact Factory, alongside the
    #     rest of the M19/M20/M21 device/account-reading family, for the same reason the
    #     mail/calendar block above is here: none of these words mean anything else this
    #     resolver already claimed higher up. ARTIFACT_LIST is checked before
    #     ARTIFACT_CREATE because the two share verb stems (an interrogative "ürettin"
    #     vs. an imperative "üret") that must never collide; ARTIFACT_VALIDATE and
    #     ARTIFACT_OPEN own their own vocabulary ("doğru mu", "aç") that nothing above
    #     claims.
    # B42 (req 410-416): the artifact in focus owns edit / clone / delete / compare -
    # BEFORE list/create, because "bunu kopyala" carries no create verb and "bu belgeye
    # bölüm ekle" must not become a new artifact.
    if lifecycle_matched := _artifact_lifecycle_match(tokens, artifact_focused=artifact_focused):
        lifecycle_intent, lifecycle_text, lifecycle_fields = lifecycle_matched
        return ResolvedIntent(
            lifecycle_intent,
            scope=SCOPE_CONVERSATION,
            matched=lifecycle_text,
            spoken_numbers=_extract_artifact_numbers(text)
            if lifecycle_intent is Intent.ARTIFACT_EDIT
            else None,
            artifact_title=_extract_artifact_title(text)
            if lifecycle_intent is Intent.ARTIFACT_CLONE
            else None,
            **lifecycle_fields,
            **base,
        )
    if list_matched := _artifact_list_match(tokens):
        return ResolvedIntent(
            Intent.ARTIFACT_LIST, scope=SCOPE_CONVERSATION, matched=list_matched, **base
        )
    if validate_matched := _artifact_validate_match(tokens):
        return ResolvedIntent(
            Intent.ARTIFACT_VALIDATE,
            scope=SCOPE_CONVERSATION,
            matched=validate_matched,
            artifact_ref="current",
            **base,
        )
    if open_matched := _artifact_open_match(tokens):
        return ResolvedIntent(
            Intent.ARTIFACT_OPEN,
            scope=SCOPE_CONVERSATION,
            matched=open_matched,
            artifact_ref=_artifact_ref_for(open_matched),
            **base,
        )
    if create_matched := _artifact_create_match(tokens):
        return ResolvedIntent(
            Intent.ARTIFACT_CREATE,
            scope=SCOPE_CONVERSATION,
            matched=create_matched,
            artifact_ref="current" if create_matched == "bunu yap" else None,
            artifact_kind=_artifact_kind_from_tokens(tokens),
            artifact_title=_extract_artifact_title(text),
            spoken_numbers=_extract_artifact_numbers(text),
            **base,
        )

    # 0i. ADR-0112 addendum (owner queue item 2, asked 2026-09-11): "Güldür Güldür aç."
    #     with no media word in it at all. LAST, below every branch that knows a noun,
    #     because that placement IS the guard: what reaches here is a play verb and a
    #     name that no allowlist, catalogue, deictic or noun stem in this resolver
    #     recognised. ``_media_match`` above keeps its mandatory marker unchanged -- this
    #     is a separate, narrower matcher, not a loosening of that one.
    if bare_media := _bare_title_media_match(tokens, text):
        bare_intent, bare_matched, bare_query = bare_media
        return ResolvedIntent(
            bare_intent,
            scope=SCOPE_CONVERSATION,
            matched=bare_matched,
            media_query=bare_query,
            **base,
        )

    # 1. stop — top priority in any state, including TOOL_RUNNING progress.
    stop = _stop_match(normalized, tokens)
    if stop:
        return ResolvedIntent(
            Intent.STOP, scope=_scope_for(Intent.STOP, narration), matched=stop, **base
        )

    # 1a. "Canlıya al." — an action the policy always refuses, but an ACTION: it goes to
    #     release.promote and comes back as a refused receipt, so the refusal is evidence
    #     (contract §2). Checked before the question classifier so the imperative can never
    #     be softened into the can_deploy question.
    if deploy_matched := _deploy_match(tokens):
        return ResolvedIntent(
            Intent.DEPLOY, scope=SCOPE_CONVERSATION, matched=deploy_matched, **base
        )

    # 1b. questions about the system's own activity resolve BEFORE presentation words,
    #     because "araştırmayı detaylandır" with no briefing open is a request for one,
    #     while the same words with a briefing attached are a jump into its detail section.
    if (
        explain_kind is not None
        and not (narration is not None and explain_kind in ("research_detail", "technical"))
        and not _full_read(tokens)
    ):
        return ResolvedIntent(
            Intent.EXPLAIN,
            scope=SCOPE_CONVERSATION,
            matched=explain_kind,
            query_kind=explain_kind,
            **base,
        )

    # 1c. B16 req 35-38/61: the owner's own memory. LAST of the families, and further
    #     down this function than any of them, because it is the only one whose verbs
    #     belong to everybody. The owner-utterance corpus proved it rather than a
    #     reviewer guessing: placed with the alarm and routine families it took
    #     "Son hangi hatayı düzelttin?" from the evolution status query, "Hata varsa
    #     düzelt." from the native app factory, and "Şu an konumumu nereden
    #     biliyorsun?" from the location source query - nine cases in one run. What
    #     reaches this point carries no other family's noun and no activity question,
    #     which is what makes the family safe to state as simply as it is.
    #
    #     Its own internal order runs the other way round from the rest of this
    #     function: the destructive reading is checked before the widest one, because a
    #     memory written by mistake can be forgotten and one forgotten by mistake
    #     cannot be recovered.
    if memory_matched := _memory_match(tokens):
        return ResolvedIntent(
            memory_matched[0],
            scope=SCOPE_CONVERSATION,
            matched=memory_matched[1],
            **base,
        )

    # 2. speed
    if tok := _has(tokens, "yavaş"):
        return ResolvedIntent(Intent.SLOWER, scope=SCOPE_NARRATION, matched=tok, **base)
    if tok := _has(tokens, "hızlı", "hızlan"):
        return ResolvedIntent(Intent.FASTER, scope=SCOPE_NARRATION, matched=tok, **base)

    # 3. presentation level. Surface wording varies ("detaylandır", "ayrıntı ver", "daha
    #    detaylı anlat"; "teknik detaya gir", "kod seviyesinde anlat"); the durable record
    #    carries the normalised intent, never the wording. The full-read phrases outrank the
    #    level words, and "teknik" outranks "detay" so "teknik detaya gir" is technical.
    if _full_read(tokens):
        return ResolvedIntent(
            Intent.FULL, scope=_scope_for(Intent.FULL, narration), matched="full", **base
        )
    if tok := _has(tokens, "özet", "kısaca", "kısa"):
        return ResolvedIntent(
            Intent.SUMMARIZE, scope=_scope_for(Intent.SUMMARIZE, narration), matched=tok, **base
        )
    if tok := _technical_match(tokens):
        return ResolvedIntent(
            Intent.TECHNICAL,
            scope=_scope_for(Intent.TECHNICAL, narration),
            matched=tok,
            **base,
        )
    if tok := _has(tokens, "detay", "ayrıntı", "derinle"):
        return ResolvedIntent(
            Intent.DETAIL, scope=_scope_for(Intent.DETAIL, narration), matched=tok, **base
        )

    # 4. skip — "burayı atla", "bunu atla", "bunu geç", "burayı geç"
    if tok := _has(tokens, "atla"):
        return ResolvedIntent(
            Intent.SKIP, scope=_scope_for(Intent.SKIP, narration), matched=tok, **base
        )
    if _has_exact(tokens, "geç") and _has_exact(tokens, "bunu", "burayı", "şunu", "burası"):
        return ResolvedIntent(
            Intent.SKIP, scope=_scope_for(Intent.SKIP, narration), matched="geç", **base
        )

    # 5. item / section navigation
    item_noun = _has(tokens, *_ITEM_NOUNS)
    if item_noun:
        is_section = any(item_noun.startswith(n) for n in _SECTION_NOUNS)
        if _has(tokens, "sonraki", "bir sonraki", "diğer"):
            intent = Intent.NEXT_SECTION if is_section else Intent.NEXT_ITEM
            return ResolvedIntent(intent, scope=SCOPE_NARRATION, matched=item_noun, **base)
        if _has(tokens, "önceki", "evvelki"):
            if _has(tokens, "açıkla", "anlat"):
                return ResolvedIntent(
                    Intent.EXPLAIN_PREVIOUS, scope=SCOPE_NARRATION, matched=item_noun, **base
                )
            return ResolvedIntent(
                Intent.PREVIOUS_ITEM, scope=SCOPE_NARRATION, matched=item_noun, **base
            )
        if _has_exact(tokens, "son", "sonuncu"):
            return ResolvedIntent(
                Intent.LAST_ITEM, scope=SCOPE_NARRATION, matched=item_noun, **base
            )
        for tok in tokens:
            if tok in _ORDINALS:
                n = _ORDINALS[tok]
                if n == 1 and tok == "ilk":
                    return ResolvedIntent(
                        Intent.FIRST_ITEM,
                        scope=SCOPE_NARRATION,
                        target_index=1,
                        matched=tok,
                        **base,
                    )
                return ResolvedIntent(
                    Intent.REPEAT_ITEM, scope=SCOPE_NARRATION, target_index=n, matched=tok, **base
                )
        if _has(tokens, "tekrar", "yeniden"):
            return ResolvedIntent(
                Intent.REPEAT, scope=_scope_for(Intent.REPEAT, narration), matched=item_noun, **base
            )

    # 6. repeat / resume
    if tok := _has(tokens, "tekrar", "yeniden"):
        return ResolvedIntent(
            Intent.REPEAT, scope=_scope_for(Intent.REPEAT, narration), matched=tok, **base
        )
    if (
        _has_exact(tokens, "daha")
        and _has_exact(tokens, "bir")
        and _has_exact(tokens, "oku", "söyle")
    ):
        return ResolvedIntent(
            Intent.REPEAT, scope=_scope_for(Intent.REPEAT, narration), matched="bir daha", **base
        )
    if tok := _has(tokens, "devam", "sürdür"):
        return ResolvedIntent(
            Intent.RESUME, scope=_scope_for(Intent.RESUME, narration), matched=tok, **base
        )
    if _has_exact(tokens, "kaldığın", "kaldığımız") and _has(tokens, "yer"):
        return ResolvedIntent(
            Intent.RESUME,
            scope=_scope_for(Intent.RESUME, narration),
            matched="kaldığın yerden",
            **base,
        )

    return ResolvedIntent(Intent.NONE, **{**base, "confidence": 0.0})


# ------------------------------------------------------------------ bridge


@dataclass(frozen=True, slots=True)
class NarrationBridgeResult:
    """The narration-side effect of an intent (pure)."""

    state: NarrationState
    action: str
    ok: bool = True
    message: str | None = None
    presentation: str | None = None  # "summary" | "detail" when changed
    speed: float | None = None  # new speed when changed
    cursor: Cursor | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "ok": self.ok,
            "message": self.message,
            "presentation": self.presentation,
            "speed": self.speed,
            "cursor": self.cursor.as_dict() if self.cursor else None,
            "narration_state": self.state.state.value,
            **self.extra,
        }


def level_section_cursor(plan: NarrationPlan, level: str) -> Cursor | None:
    """Cursor at the first content chunk of the section that carries ``level``
    (an activity briefing's "Özet" / "Ayrıntı" / "Teknik"), or None when the
    document has no such section."""
    titles = LEVEL_SECTION_TITLES.get(level, ())
    for section in plan.sections:
        folded = turkish_casefold(section.title).strip()
        if not any(folded.startswith(t) for t in titles):
            continue
        for ch in plan.chunks:
            if ch.cursor.section_id != section.id:
                continue
            para = plan.paragraphs.get(ch.cursor.paragraph_id)
            if para is not None and para.kind == PARAGRAPH_HEADING:
                continue
            return ch.cursor
    return None


#: Spoken budgets per presentation level (M16 owner UX result 2026-09-04): narration is for
#: listening, not document reading. Roughly 10-20 s for an executive answer, 30-60 s for the
#: detail, a concise technical briefing; only "hepsini oku" lifts the budget. Chunks end at
#: sentence boundaries and the cursor keeps the position, so "devam et" reads the next chunk.
SPEECH_BUDGET_CHARS: dict[str, int] = {
    PRESENTATION_SUMMARY: 420,
    PRESENTATION_DETAIL: 900,
    PRESENTATION_TECHNICAL: 700,
    PRESENTATION_FULL: 6000,
}


def speech_budget(level: str | None) -> int:
    default = SPEECH_BUDGET_CHARS[PRESENTATION_SUMMARY]
    return SPEECH_BUDGET_CHARS.get(level or PRESENTATION_SUMMARY, default)


def speech_from(
    plan: NarrationPlan, cursor: Cursor | None, *, whole_section: bool = True, max_chars: int = 6000
) -> str:
    """The text to speak from ``cursor``: every chunk up to the end of its section
    (or the document when ``whole_section`` is False), headings skipped. This is what
    lets the realtime provider resume at the exact sentence after "devam"."""
    if not plan.chunks:
        return ""
    start = plan.index_of(cursor)
    section_id = plan.chunks[start].cursor.section_id if cursor is None else cursor.section_id
    parts: list[str] = []
    total = 0
    for ch in plan.chunks[start:]:
        if whole_section and ch.cursor.section_id != section_id:
            break
        para = plan.paragraphs.get(ch.cursor.paragraph_id)
        if para is not None and para.kind == PARAGRAPH_HEADING:
            continue
        text = ch.text.strip()
        if not text:
            continue
        if parts and total + len(text) > max_chars:
            break  # the budget ends at a sentence boundary; the cursor reads on from here
        parts.append(text)
        total += len(text) + 1
    return " ".join(parts)


def ordered_paragraph_ids(plan: NarrationPlan, section_id: str | None = None) -> list[str]:
    """Content items in document order. A "madde" is something the owner
    hears as an item: headings are navigation structure, not items, so
    "ikinci madde" is the second content paragraph, not the second block.

    With ``section_id``, only that section's items: while a briefing's "Ayrıntı" is
    being read, "ikinci madde" is its second finding, not the second paragraph of the
    whole document (M16). A section with no items falls back to the document."""
    ordered: list[str] = []
    for ch in plan.chunks:
        if section_id is not None and ch.cursor.section_id != section_id:
            continue
        pid = ch.cursor.paragraph_id
        para = plan.paragraphs.get(pid)
        if para is not None and para.kind == PARAGRAPH_HEADING:
            continue
        if pid not in ordered:
            ordered.append(pid)
    if section_id is not None and not ordered:
        return ordered_paragraph_ids(plan)
    return ordered


def _item_cursors(plan: NarrationPlan, cursor: Cursor | None) -> list[Cursor]:
    """Where each item the owner might name begins.

    When the section being read carries a list, a "madde" is one of ITS entries - the
    plan keeps a list as one paragraph with one chunk per entry, so the items are that
    paragraph's chunks (a briefing's numbered findings). Otherwise the M4/ADR-0036 rule
    stands: items are the document's content paragraphs in order, each starting at its
    first chunk."""
    if cursor is not None:
        listed = [
            ch.cursor
            for ch in plan.chunks
            if ch.cursor.section_id == cursor.section_id
            and (para := plan.paragraphs.get(ch.cursor.paragraph_id)) is not None
            and para.kind == PARAGRAPH_LIST
        ]
        if listed:
            return listed
    starts: list[Cursor] = []
    for pid in ordered_paragraph_ids(plan):
        first = plan.paragraph_start_cursor(pid)
        if first is not None:
            starts.append(first)
    return starts


def _same_item(a: Cursor, b: Cursor, *, by_sentence: bool) -> bool:
    if a.section_id != b.section_id or a.paragraph_id != b.paragraph_id:
        return False
    return a.sentence_index == b.sentence_index if by_sentence else True


def current_item_index(plan: NarrationPlan, cursor: Cursor | None) -> int:
    """1-based index of the item the cursor is in (0 when no cursor or when the
    cursor sits on a heading)."""
    if cursor is None:
        return 0
    items = _item_cursors(plan, cursor)
    by_sentence = _is_list_mode(plan, items)
    for n, item in enumerate(items, start=1):
        if _same_item(item, cursor, by_sentence=by_sentence):
            return n
    return 0


def _is_list_mode(plan: NarrationPlan, items: list[Cursor]) -> bool:
    if not items:
        return False
    para = plan.paragraphs.get(items[0].paragraph_id)
    return para is not None and para.kind == PARAGRAPH_LIST


def _jump_to_item(
    state: NarrationState, plan: NarrationPlan, n: int, *, action: str
) -> NarrationBridgeResult:
    """Same state shape as the M4 ``MADDEYE_GEC`` transition, over content items."""
    items = _item_cursors(plan, state.cursor)
    if n < 1 or n > len(items):
        return NarrationBridgeResult(
            state=state,
            action="jump_failed",
            ok=False,
            message="Böyle bir madde yok.",
            cursor=state.cursor,
        )
    target = items[n - 1]
    new_state = replace(state, state=State.READING, cursor=target, paragraph_anchor=target)
    return NarrationBridgeResult(state=new_state, action=action, cursor=target)


def _via_commands(
    state: NarrationState, parsed: ParsedCommand, plan: NarrationPlan, *, action: str | None = None
) -> NarrationBridgeResult:
    res = commands.apply(state, parsed, plan)
    return NarrationBridgeResult(
        state=res.state,
        action=action or res.action,
        ok=res.ok,
        message=res.message,
        cursor=res.state.cursor,
    )


def apply_to_narration(
    resolved: ResolvedIntent, state: NarrationState, plan: NarrationPlan
) -> NarrationBridgeResult:
    """Translate a resolved intent into narration cursor / speed operations.

    Delegates every state change to the M4 command machine so its invariants
    hold ("dur" always wins and never errors; explain-then-return is intact).
    """
    intent = resolved.intent
    if intent == Intent.STOP:
        return _via_commands(state, ParsedCommand(Command.DUR), plan)
    if intent == Intent.RESUME:
        return _via_commands(state, ParsedCommand(Command.DEVAM), plan)
    if intent == Intent.REPEAT:
        return _via_commands(state, ParsedCommand(Command.TEKRAR), plan)
    if intent == Intent.REPEAT_ITEM:
        return _jump_to_item(state, plan, resolved.target_index or 1, action="jump_item")
    if intent == Intent.FIRST_ITEM:
        return _jump_to_item(state, plan, 1, action="jump_item")
    if intent == Intent.LAST_ITEM:
        return _jump_to_item(
            state, plan, len(_item_cursors(plan, state.cursor)), action="jump_item"
        )
    if intent in (Intent.NEXT_ITEM, Intent.SKIP):
        current = current_item_index(plan, state.cursor)
        res = _jump_to_item(
            state, plan, current + 1, action="skipped" if intent == Intent.SKIP else "jump_item"
        )
        if not res.ok:
            return replace(
                res, action="end_of_document", message="Atlanacak bir sonraki madde yok."
            )
        return res
    if intent == Intent.PREVIOUS_ITEM:
        current = current_item_index(plan, state.cursor)
        return _jump_to_item(state, plan, max(1, current - 1), action="jump_item")
    if intent == Intent.NEXT_SECTION:
        return _via_commands(state, ParsedCommand(Command.SONRAKI_BOLUM), plan)
    if intent in (Intent.SLOWER, Intent.FASTER):
        delta = -SPEED_STEP if intent == Intent.SLOWER else SPEED_STEP
        res = _via_commands(state, ParsedCommand(Command.HIZ, speed=state.speed + delta), plan)
        return replace(res, speed=res.state.speed)
    if intent == Intent.FULL:
        # Read everything from the top: the caller lifts the budget (presentation == full);
        # the cursor starts at the first content chunk.
        start = plan.chunks[0].cursor if plan.chunks else None
        new_state = replace(
            state, state=State.READING, cursor=start, paragraph_anchor=start, saved_cursor=None
        )
        return NarrationBridgeResult(
            state=new_state, action="read_all", presentation=PRESENTATION_FULL, cursor=start
        )
    if intent in (Intent.SUMMARIZE, Intent.DETAIL, Intent.TECHNICAL):
        level = {
            Intent.SUMMARIZE: PRESENTATION_SUMMARY,
            Intent.DETAIL: PRESENTATION_DETAIL,
            Intent.TECHNICAL: PRESENTATION_TECHNICAL,
        }[intent]
        # A briefing carries its levels as sections: move the cursor there, so what is
        # spoken next IS the requested level. A plain document has no such section and
        # only the presentation flag changes, exactly as before.
        target = level_section_cursor(plan, level)
        if target is not None:
            new_state = replace(
                state,
                state=State.READING,
                cursor=target,
                paragraph_anchor=target,
                saved_cursor=None,
            )
            return NarrationBridgeResult(
                state=new_state, action="jump_level", presentation=level, cursor=target
            )
        return NarrationBridgeResult(
            state=state, action="presentation_changed", presentation=level, cursor=state.cursor
        )
    if intent == Intent.EXPLAIN_PREVIOUS:
        # Explain-then-return (M4 invariant): the EXACT current cursor is saved, the
        # previous item is read, and "devam" comes back to where the owner was.
        current = current_item_index(plan, state.cursor)
        jumped = _jump_to_item(state, plan, max(1, current - 1), action="explain_previous")
        if not jumped.ok:
            return jumped
        explaining = replace(jumped.state, state=State.EXPLAINING, saved_cursor=state.cursor)
        return replace(jumped, state=explaining, cursor=explaining.cursor)
    return NarrationBridgeResult(
        state=state, action="noop", ok=False, message="Bilinmeyen komut.", cursor=state.cursor
    )


__all__ = [
    "CAPABILITY_BY_INTENT",
    "FILLERS",
    "KLASS_ACTION",
    "KLASS_CONTROL",
    "KLASS_QUERY",
    "LEVEL_SECTION_TITLES",
    "PRESENTATION_DETAIL",
    "PRESENTATION_FULL",
    "PRESENTATION_SUMMARY",
    "PRESENTATION_TECHNICAL",
    "QUERY_TOOL_BY_INTENT",
    "RESEARCH_CLASSES",
    "RESEARCH_CLASSES_BOUND_TO_A_RUN",
    "RESEARCH_CLASSES_MAY_CRAWL",
    "RESEARCH_CLASS_FOLLOWUP",
    "RESEARCH_CLASS_NEW",
    "RESEARCH_CLASS_RETRY",
    "RESEARCH_CLASS_TECHNICAL_EXPLANATION",
    "RESEARCH_REFERENCES",
    "RESEARCH_REFERENCE_CURRENT",
    "RESEARCH_REFERENCE_NONE",
    "RESEARCH_REFERENCE_ORDINAL",
    "RESEARCH_REFERENCE_PREVIOUS",
    "RESEARCH_REFERENCE_SELECTION",
    "RESEARCH_REFERENCE_TOPIC",
    "ResearchReference",
    "SPEECH_BUDGET_CHARS",
    "SCOPE_CONVERSATION",
    "SCOPE_NARRATION",
    "SPEED_STEP",
    "STOP_TOKENS",
    "Intent",
    "NarrationBridgeResult",
    "ResolvedIntent",
    "apply_to_narration",
    "classify_research_interaction",
    "contains_secret_reference",
    "current_item_index",
    "is_filler",
    "klass_for",
    "level_section_cursor",
    "normalize_transcript",
    "ordered_paragraph_ids",
    "classify_research_reference",
    "classify_research_shape",
    "research_class_for",
    "research_topic_of",
    "research_reference_for",
    "resolve_intent",
    "speech_budget",
    "speech_from",
    "turkish_casefold",
]
