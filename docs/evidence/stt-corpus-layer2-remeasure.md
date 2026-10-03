# STT corpus, layer 2 as production configures it

Generated 2026-10-03T01:50:59.632899Z at 05ca60596cf4f8ffa54c6be3a47d40b5410527d1. Engine: local-minishlab/potion-multilingual-128M (local, 1427 exemplars, index built in 403.7 ms).

- without the engine: **73 / 106 = 68.9 %**
- with the engine:    **73 / 106 = 68.9 %**
- target 95 %: **NOT met**
- layer 2 made 0 case(s) worse and 0 better
- wrong-device actions with the engine: 0 over 11 observable cases
- confident wrong readings: 8 -> 26

## Made worse

- none

## Made better

- none

## Every moved case

| case | from | to | layer before | layer after | meant | top candidate after |
|---|---|---|---|---|---|---|
| stt.derived.macro.start.1.fused | not_understood | wrong_reading | none | semantic | macro_record_start | macro_record_start 0.6 |
| stt.derived.r.tech.1.fused | not_understood | wrong_reading | none | semantic | technical | technical 0.86 |
| stt.derived.a.create.1.fused | not_understood | wrong_reading | none | semantic | alarm_create | alarm_create 0.95 |
| stt.derived.d.off.1.polite | not_understood | wrong_reading | none | semantic | display_off | display_off 0.95 |
| stt.derived.am.1.fused | not_understood | wrong_reading | none | semantic | ambient_policy_set | ambient_policy_set 0.77 |
| stt.derived.e.off.1.polite | not_understood | wrong_reading | none | semantic | eye_disable | eye_disable 0.79 |
| stt.derived.op.app.8.fused | not_understood | wrong_reading | none | semantic | app_open | app_open 0.83 |
| stt.derived.doc.search.1.polite | not_understood | wrong_reading | none | semantic | file_search | file_search 0.82 |
| stt.derived.mc.search.1.fused | not_understood | wrong_reading | none | semantic | mail_search | mail_search 0.69 |
| stt.derived.art.create.document.polite | not_understood | wrong_reading | none | semantic | artifact_create | artifact_create 0.81 |
| stt.derived.app.create.tracker.polite | not_understood | wrong_reading | none | semantic | app_factory_create | app_factory_create 0.91 |
| stt.derived.genesis.request.increment.polite | not_understood | wrong_reading | none | semantic | capability_request | capability_request 0.97 |
| stt.derived.scene.create.blender.canonical.polite | not_understood | wrong_reading | none | semantic | scene_create | scene_create 0.83 |
| stt.derived.location.default.set.1.polite | not_understood | wrong_reading | none | semantic | location_default_set | location_default_set 0.94 |
| stt.derived.n.open.1.polite | not_understood | wrong_reading | none | semantic | news_open | news_open 0.61 |
| stt.derived.n.open.1.fused | not_understood | wrong_reading | none | semantic | news_open | news_summarize 0.61 |
| stt.derived.nativeapps.create.win.canonical.polite | not_understood | wrong_reading | none | semantic | native_create_windows | native_create_windows 0.91 |
| stt.derived.nativeapps.create.win.canonical.fused | not_understood | wrong_reading | none | semantic | native_create_windows | native_create_windows 0.73 |

## By layer (with the engine / without)

| layer | with: correct / total | without: correct / total |
|---|---|---|
| none | 1 / 1 | 2 / 27 |
| normalize | 2 / 2 | 2 / 2 |
| rule | 69 / 77 | 69 / 77 |
| semantic | 1 / 26 | 0 / 0 |

## By distortion (with the engine / without)

| distortion | with | without |
|---|---|---|
| polite | 16 / 29 | 16 / 29 |
| diacritics | 24 / 24 | 24 / 24 |
| fused | 12 / 29 | 12 / 29 |
| invented_suffix | 18 / 21 | 18 / 21 |

## The 7 largest failure classes (of 7), with the engine

| verdict | distortion | layer | count | expected -> resolved | cases |
|---|---|---|---|---|---|
| wrong_reading | polite | semantic | 10 | app_factory_create -> none x1, artifact_create -> none x1, capability_request -> none x1, display_off -> none x1, eye_disable -> none x1, file_search -> none x1, location_default_set -> none x1, native_create_windows -> none x1, news_open -> none x1, scene_create -> none x1 | stt.derived.d.off.1.polite, stt.derived.e.off.1.polite, stt.derived.doc.search.1.polite, stt.derived.art.create.document.polite, stt.derived.app.create.tracker.polite, stt.derived.genesis.request.increment.polite, stt.derived.scene.create.blender.canonical.polite, stt.derived.location.default.set.1.polite, stt.derived.n.open.1.polite, stt.derived.nativeapps.create.win.canonical.polite |
| wrong_reading | fused | semantic | 8 | alarm_create -> none x1, ambient_policy_set -> none x1, app_open -> none x1, macro_record_start -> none x1, mail_search -> none x1, native_create_windows -> none x1, news_open -> none x1, technical -> none x1 | stt.derived.macro.start.1.fused, stt.derived.r.tech.1.fused, stt.derived.a.create.1.fused, stt.derived.am.1.fused, stt.derived.op.app.8.fused, stt.derived.mc.search.1.fused, stt.derived.n.open.1.fused, stt.derived.nativeapps.create.win.canonical.fused |
| not_understood | fused | semantic | 5 | app_open -> none x1, display_off -> none x1, eye_disable -> none x1, mail_inbox -> none x1, routine_create -> none x1 | stt.derived.r.create.1.fused, stt.derived.d.inbox.1.fused, stt.derived.d.off.1.fused, stt.derived.e.off.1.fused, stt.derived.op.app.1.fused |
| wrong_reading | fused | rule | 4 | alarm_create -> alarm_create x1, creative_redraw -> repeat x1, scene_create -> media_play x1, selfdev_fix -> memory_correct x1 | stt.derived.c.collision.alarm_create.fused, stt.derived.selfdev.fix.canonical.fused, stt.derived.scene.create.blender.canonical.fused, stt.derived.creative.redraw.canonical.fused |
| wrong_reading | invented_suffix | rule | 3 | app_open -> media_play x2, selfdev_fix -> memory_correct x1 | stt.derived.selfdev.fix.canonical.invented_suffix, stt.derived.op.app.1.invented_suffix, stt.derived.op.app.8.invented_suffix |
| not_understood | polite | semantic | 2 | mail_inbox -> none x1, mail_search -> none x1 | stt.derived.d.inbox.1.polite, stt.derived.mc.search.1.polite |
| wrong_reading | polite | rule | 1 | evolution_pause -> explain x1 | stt.derived.ev.pause.1.polite |

## Every failure with the engine

| case | verdict | layer | band | meant | resolved | top candidate | rendering |
|---|---|---|---|---|---|---|---|
| stt.derived.c.collision.alarm_create.fused | wrong_reading | rule | high | alarm_create | alarm_create | alarm_create 1.0 | Saat yedibuçukta beni uyandır. |
| stt.derived.r.create.1.fused | not_understood | semantic | low | routine_create | none | routine_create 0.56 | Her sabah 08:00'de bana haberleri okuyan birrutin kur. |
| stt.derived.macro.start.1.fused | wrong_reading | semantic | medium | macro_record_start | none | macro_record_start 0.6 | Yenihareket oluştur. |
| stt.derived.d.inbox.1.polite | not_understood | semantic | low | mail_inbox | none | mail_inbox 0.33 | Maillerime bakın. |
| stt.derived.d.inbox.1.fused | not_understood | semantic | low | mail_inbox | none | mail_inbox 0.37 | Maillerimebak. |
| stt.derived.r.tech.1.fused | wrong_reading | semantic | high | technical | none | technical 0.86 | Bunuteknik anlat. |
| stt.derived.a.create.1.fused | wrong_reading | semantic | high | alarm_create | none | alarm_create 0.95 | Yarın 7:30'da beniuyandır. |
| stt.derived.d.off.1.polite | wrong_reading | semantic | high | display_off | none | display_off 0.95 | Ekranları kapatın. |
| stt.derived.d.off.1.fused | not_understood | semantic | low | display_off | none | display_off 0.56 | Ekranlarıkapat. |
| stt.derived.am.1.fused | wrong_reading | semantic | medium | ambient_policy_set | none | ambient_policy_set 0.77 | Uyurkenekranları kapat. |
| stt.derived.e.off.1.polite | wrong_reading | semantic | medium | eye_disable | none | eye_disable 0.79 | Gözünü kapatın. |
| stt.derived.e.off.1.fused | not_understood | semantic | low | eye_disable | none | explain 0.48 | Gözünükapat. |
| stt.derived.ev.pause.1.polite | wrong_reading | rule | high | evolution_pause | explain | explain 1.0 | Kendi kendini geliştirmeyi duraklatın. |
| stt.derived.selfdev.fix.canonical.fused | wrong_reading | rule | high | selfdev_fix | memory_correct | memory_correct 1.0 | Şubug'ı kendin düzelt. |
| stt.derived.selfdev.fix.canonical.invented_suffix | wrong_reading | rule | high | selfdev_fix | memory_correct | memory_correct 1.0 | Şu bug'ı kendinü düzelt. |
| stt.derived.op.app.1.fused | not_understood | semantic | low | app_open | none | app_open 0.56 | NotDefteri'ni aç. |
| stt.derived.op.app.1.invented_suffix | wrong_reading | rule | high | app_open | media_play | media_play 1.0 | Notü Defteri'ni aç. |
| stt.derived.op.app.8.fused | wrong_reading | semantic | medium | app_open | none | app_open 0.83 | Hesapmakinesini aç. |
| stt.derived.op.app.8.invented_suffix | wrong_reading | rule | high | app_open | media_play | media_play 1.0 | Hesapü makinesini aç. |
| stt.derived.doc.search.1.polite | wrong_reading | semantic | medium | file_search | none | file_search 0.82 | Bu klasördeki PDF'leri bulun. |
| stt.derived.mc.search.1.polite | not_understood | semantic | low | mail_search | none | mail_search 0.53 | Fatura maillerini bulun. |
| stt.derived.mc.search.1.fused | wrong_reading | semantic | medium | mail_search | none | mail_search 0.69 | Faturamaillerini bul. |
| stt.derived.art.create.document.polite | wrong_reading | semantic | medium | artifact_create | none | artifact_create 0.81 | Toplantı notlarını Word belgesi yapın. |
| stt.derived.app.create.tracker.polite | wrong_reading | semantic | high | app_factory_create | none | app_factory_create 0.91 | Bana bir görev takip uygulaması yapın. |
| stt.derived.genesis.request.increment.polite | wrong_reading | semantic | high | capability_request | none | capability_request 0.97 | Sayaç kutusunu bir artırın. |
| stt.derived.scene.create.blender.canonical.polite | wrong_reading | semantic | medium | scene_create | none | scene_create 0.83 | Blender'da yeni sahne açın. |
| stt.derived.scene.create.blender.canonical.fused | wrong_reading | rule | high | scene_create | media_play | media_play 1.0 | Blender'da yenisahne aç. |
| stt.derived.location.default.set.1.polite | wrong_reading | semantic | high | location_default_set | none | location_default_set 0.94 | Varsayılan hava durumu konumumu İstanbul yapın. |
| stt.derived.n.open.1.polite | wrong_reading | semantic | medium | news_open | none | news_open 0.61 | Haberleri açın. |
| stt.derived.n.open.1.fused | wrong_reading | semantic | medium | news_open | none | news_summarize 0.61 | Haberleriaç. |
| stt.derived.creative.redraw.canonical.fused | wrong_reading | rule | high | creative_redraw | repeat | repeat 1.0 | Buresmi Paint'te yeniden çiz. |
| stt.derived.nativeapps.create.win.canonical.polite | wrong_reading | semantic | high | native_create_windows | none | native_create_windows 0.91 | Bana Windows için masaüstü uygulaması yapın. |
| stt.derived.nativeapps.create.win.canonical.fused | wrong_reading | semantic | medium | native_create_windows | none | native_create_windows 0.73 | Bana Windows için masaüstüuygulaması yap. |

Repeat run: RUN, differing cases: none
