# ADR taslağı: bulut görev döngüsünün üç kusuru (cloud-task-loop-evidence, dönüş 3)

Durum: kabul edildi (worker), numarayı Proje Yöneticisi verir.

Bağlam: 2026-10-06 canlı koşusunda (dev yığını, gerçek bulut yardımcısı, gerçek planlayıcı)
hiçbir görev `done` olmadı. Üç kök neden doğrulandı.

Kararlar:

1. `model_planner.py` `temperature` göndermez. Yetenekli model (`claude-sonnet-5`) onu 400
   "deprecated for this model" ile reddediyor; aynı istek onsuz 200. Belirlilik zorunlu tek
   araç çağrısından gelir.
2. Eylem adımı `expect_kind` taşımadan gelirse model BİR kez daha sorulur; ipucu GOAL
   bloğuna eklenir (sayfa yine en sonda ve sarmalayıcıda). İkinci yanıt da beklentisizse
   `PlannerError` (`planner_unavailable`). Model 200 ile yanıtladığı her istek sayılır
   (`ModelPlanner.last_calls`, `ChainPlanner.last_calls`, döngü `planner_model_calls`'a
   ekler; planlayıcı hata verse bile ödenen çağrı sayılır). İstem ve araç açıklaması
   "done/ask_owner dışında her adım expect_kind taşır" der. Şema `required`'a
   `expect_kind` eklenmedi: done/ask_owner için gereksiz olurdu.
3. Biten görev tarayıcı oturumunu kapatır: `BrowserPort.close(task_id)`,
   `DeviceTaskBrowser.close` `browser.session_close` gönderir (görev başına tek
   idempotency anahtarı `webtask:<id>:session_close`), `_OPEN`'dan düşer, hatayı loglar ve
   yutar; süreç oturumu bilmese de gönderir (yeniden başlatma sonrası sızıntı olmasın;
   işçi bilinmeyen oturuma `closed` der). Tur aktivitesi sonuç terminalse (done / failed /
   cancelled), iptal aktivitesi ve iş akışının son sözü (`web_task_fail`) her zaman
   kapatır. `waiting_owner` oturumu açık tutar (sahip sayfayı değiştirebilir).
4. Buluttaki `not_on_owner_allow_list` reddi görevin SON SÖZÜdür: `_fail(reason =
   not_on_owner_allow_list, mesaj = NOT_ON_LIST_TR)`. Bu reddi yalnız sahip kaldırabilir
   (Onay Merkezi); yeniden planlamak görevi "Art arda 3 adım tutmadı" ile bitirip sahibin
   cümlesini kaybediyordu. Diğer retler (deny_listed_site, cloud_risk_not_allowed) eskisi
   gibi planlayıcıya ipucu olur.

Sonuç: T1 hedefi haber sitesini adlandırır (`url_not_from_owner_or_page` tasarımdır).
Kanıt dosyalarını testin kendi koşusu yazar (`PAGENTOS_EVIDENCE_OUT`), elle birleştirme yok.
