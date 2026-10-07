# Devir notu — canlı (son güncelleme 2026-09-21)

Sahip iki Claude hesabını dönüşümlü kullanır (token bitince diğerine geçer). Yeni oturum,
hangi hesap olursa olsun, buradan devam eder: `.claude/hooks/session-start.ps1` aşağıdaki
işaretli bloğu, `git status`'u ve son commitleri oturum açılır açılmaz bağlama koyar.
Önce `CLAUDE.md`, sonra bu dosya, gerektikçe `docs/DECISIONS.md` sonundaki ADR'ler.
Sahibe Türkçe yaz. Geçiş tarifi (sahip için): `docs/HESAP_GECISI.md`.

**Bu dosyanın canlı kalma kuralı (her iki hesap için bağlayıcı):**
1. Bir işe başlarken, ilk kod değişikliğinden ÖNCE "Şu an üzerinde çalışılan" bölümünü yaz.
2. Her commit'te bu bölümü ve gerekiyorsa "Şu anki durum"u güncelle (aynı commit'e koy).
3. İş bitince bölümü "Yok" yap, işi "Sıradaki işler"den düş.
Token ortada biterse bir sonraki oturum kaldığı yeri buradan ve `git diff`'ten bulur.

<!-- session-start:begin -->
## Şu an üzerinde çalışılan

**ÜRETİM: main `d74a8daa83389d87a3ad68e1177e99e802e816b3` (2026-10-07 07:33 UTC = 10:33 yerel, api-blue), LKG `b1f8ef94`,
pin = RELEASE, reconcile OK, şema `0075_urgent_alert_receipts`. QUALIFICATION Stage 60 (kanıt şeridi, alarm saat ifadeleri, Nöbetler sayfası, paralel kapı). EKİP HESABI: `.claude-hesap3` (sahip 20x yükseltti, 2026-10-07 08:21). Test turu geçici kökü K:\AI	mp-team	estteam-root (Fable limitliyken; kart test-round-model-fallback). Önceki Stage 59: konuşma devamı, acil uyarı, yarış/olumsuz-emir; Stage 58: para defteri, test koltuklarının yüzleri, ekip
entegrasyon düzeltmeleri). Proje K:'de (NVMe); E: kopyası yedek, sahip onayıyla silinir. GECE NÖBETİ (sahip 2026-10-06 23:40):
zamanlanmış görev 'PagentOS Danisman Watch' 15 dk'da bir %USERPROFILE%\.pagentos-team\danisman-watch.ps1 çalıştırır
(sonuç watch-latest.txt / watch.log; yeni bulguda başsız Danışman koşusu kart yazar); kart danisman-watch-in-repo depoya taşır.
Gece bulguları kartlara yazıldı (test-board-notes-per-job, area-check-wire-integrate-base, two-devices-tests-late-write-routes, completed-work-to-test-lead, untested-roadmap-to-test-lead).**
**ZAMANLAYICI (2026-10-04): ekip görevi `C:\Users\alpak\.pagentos-team\team-tick-wrapper.ps1`'i çalıştırır (hesap dosyası yanında:
`team-account.txt` = `.claude-hesap2`). %LOCALAPPDATA% altına bu oturumdan yazılan dosyalar Claude masaüstünün MSIX klasörüne
yönleniyor, Görev Zamanlayıcı göremiyor (0xFFFD0000). Exit 3 = kilit çalışan döngüde (doğru). Görev `-MaxHours 12` geçiriyor (2026-10-04 04:55).**
**ÇALIŞAN SINIRI: `team/cycle-settings.json` max_parallel 4 (bellek ölçüldü: kapının birim adımı 21,7 GB; o adımda 3'e indir).**
**DEV KABUĞU: ev PC'sindeki `next dev` (:3000) ana kopyanın dal değişimlerinde bozulabiliyor (telefonda 'Oturum kontrol ediliyor'
takılı, web.err.log'da 'Blocked cross-origin'). Yeniden başlatma: süreç ağacını durdur, `scripts/voice/start-web-voice.ps1 -PnpmPath
C:/Users/alpak/AppData/Roaming/npm/pnpm.cmd` Start-Process ile, çıktılar %LOCALAPPDATA%/PagentOS/web-shell. Kalıcı adres sunucudaki.**
**KUYRUK BİRLEŞTİRİLDİ (ek 20): 17 başlamamış kart -> 8 (team-engine ÖNCELİKLİ, project-manager-seat ÖNCELİKLİ, team-board-talk,
office-talk-visible, gate-faster, memory-safe-runs, account-pool, run-liveness-visible-all). DİKKAT: 28 kaydın Türkçesi BOM'suz .ps1
yüzünden bozulmuştu ("YÃ¶neticisi"), onarıldı (scratchpad fix_mojibake.ps1); Türkçe metinli .ps1'e BOM koy.**
**TELEFON (49.3, PROVEN_REAL): web kabuğu sunucuda, tailnet HTTPS: https://pagentos-core.tail0e6789.ts.net (yalnız tailnet;
`tailscale serve` -> 127.0.0.1:3000; geri almak: `enable-web-tailnet-https.sh --off`). Ev PC dev kabuğu da telefondan
`http://100.92.148.30:3000` (allowedDevOrigins). Sahip yeni adrese bir kez giriş yapar (oturum adres başına).**
**DİSK (2026-10-03): C: ~12:00'de SIFIRA indi (kapı `OSError(28)`, Docker motoru durdu - lead Docker Desktop'ı yeniden başlattı).
C:'nin sahipleri ölçüldü: Program Files 112 GB, Docker 47 GB, pagefile+hiberfil 53 GB; OneDrive 222 GB'ın tamamı yalnız bulutta
(diskte 0). AppData altında E:'ye junction ÇÖZÜLMÜYOR (STATUS_MOUNT_POINT_NOT_RESOLVED, Android ile ölçüldü, geri alındı);
kullanıcı kökündekiler (.gradle, anaconda3) junction ile E:\C-tasinan'a taşınıyor (`scratchpad/move_to_e.ps1`). Docker kendi
"Disk image location" ayarıyla taşınmalı (junction değil). Program listesi sahibe verildi; silmeler onun.**
**SAHİBİN OFİS MODELİ (2026-10-03, kartlar kuyrukta, hepsi sahibin fikri): `team-board` (pano, denetimde), `team-board-consult`
(danışma: bağlam + A/B seçenekleri, cevaplayan işi okuyarak), `office-board-bubbles`, `inspector-advice` (öneri + ekip dersleri),
`lead-on-duty` (nöbetçi Hakim), `test-slots-on-board`, `worker-owns-its-return` (geri dönen iş aynı çalışana, oturumu sürdürerek;
ÖNCELİKLİ), `continuous-team-loop` (döngü kalkar, sürekli akış, devralma; ÖNCELİKLİ). 15:00'te döngü 4 saat sınırında boşaldı -
yarım saatten fazla 1 çalışan + 1 denetleyici (ölçüldü) - continuous-team-loop'un sebebi.**
**BELLEK ÇÖKÜŞÜ (2026-10-03 ~05:00-05:50 yerel): 48 GB'lık ev bilgisayarının belleği TÜKENDİ - lead'in oturumu, sahibin web
kabuğu (`next dev` :3000) ve sekizinci entegrasyonun kapısı (`gate/d20261003-4` @ `4f80a564`) çöktü. Kapı kırmızı ama KUSUR DEĞİL:
PS paketlerinde `OutOfMemoryException`, `git: Out of memory`, pnpm 0xC0000409. 10:57'de bulunan: `test-slots` çalışanının TÜM birim
paketi (tek `pytest tests/unit` süreci) 14 GB'a çıkmış ve büyüyordu - lead durdurdu (boş bellek 17,6 -> 28 GB). Önlemler: (1) web
kabuğu oturumdan BAĞIMSIZ yeniden başlatıldı (günlük `%LOCALAPPDATA%/PagentOS/web-shell`); (2) `team/cycle-settings.json`: 3 çalışan +
2 denetleyici (GEÇİCİ - bellek önlemi gelene kadar; sahip daha çok çalışan istiyor); (3) worker.md / inspector.md: ajanlar TÜM birim
paketini kendileri koşturmaz, kapı koşturur. Kartlar: `run-memory-cap`, `unit-suite-memory`.**
**SAHİBİN KURALI (2026-10-03 00:07): "bundan sonra kapısı yeşil olanlar otomatik canlıya geçsin, beklemesinler" - denetimden geçen
iş HEMEN kapıya, yeşil kapı HEMEN yayına, döngü kodu değiştiyse döngü HEMEN yeni koda (`team/stop.flag`). Ek 9'un üç istisnası
(compose/ortam değişikliği, geri alınamayan migration, sağlık ok değil) duruyor.**
**HAVUZ ÇALIŞIYOR (PROVEN_REAL, 47.4): döngü `d20261003` 23:00 UTC'de (02:00 yerel) havuz koduyla başladı (pid 46484): 6 çalışan +
2 denetleyici aynı anda, biten koşunun koltuğu diğerleri sürerken doluyor. `-MaxHours 4`: döngü 4 saatte bir kendini güncel betiğe
bırakır (kilit de böylece 6 saate yaklaşmaz). Kapı yanında yavaşlarsa `team/cycle-settings.json`.**
**KAPIDA (lead, 03:05 yerel): YEDİNCİ ENTEGRASYON, dal `gate/d20261003-3` (worktree `.claude/worktrees/gate10`) = lead dalı (ADR-0214
ek 16 dahil) + `local-embedder-lru-lock` (ADR-0256). Yeşilse onaysız yayın.**
**03:00 yerel - ERKEN BİTEN KOŞULAR (ADR-0214 ek 16): havuzun ilk saatinde beş koşu "test arka planda koşuyor, bitince
raporlarım" diyerek bitti; `claude -p` hiç uyandırılmaz, iş boş sayıldı (görevler yanlışlıkla durdu/döndü - lead üçünü sayılmadan
geri koydu). DÜZELTME lead dalında (`Start-TeamRun`: `CLAUDE_CODE_DISABLE_BACKGROUND_TASKS=1`, `BASH_MAX_TIMEOUT_MS=3600000`;
test kırmızı→yeşil; rol dosyalarında üç satır). Çalışan döngü eski fonksiyonları taşıdığı için `team/stop.flag` YAZILDI: döngü
`d20261003` koşuları bitince kapanır, sonraki tick düzeltilmiş kodla başlar. Kapıya bir sonraki entegrasyonla girer
(`local-embedder-lru-lock` ile birlikte).**
**BU GECE YAYINLANAN (3 Ekim): 01:14 `65cd94ff` döngü havuzu; 02:32 `e9f8c2d6` defter deposu (tablo 0065, süpürme döngüsü sağlıkta:
passes 1, failures 0) + anlatı modeli bağlantısı (ayar KAPALI). `execution-call-site-routines` kapatma ayarı için çalışanda;
`understanding-rules-read-lemmas` (sahibin 1. önceliği) yeniden açıldı ve yazılıyor.**
**YAYINDA (2 Ekim'de dört yayın): `e5c4d1f3` 4 çalışan koltuğu, döngü kuyruğu her turda okur; `f91ad1e3` ADR-0224 katman 2 üretimde,
düzeltmeler hafızaya; `f60e02e4` iki onaylı compose değişikliği (araştırma kuralı anahtarı KAPALI iletiliyor, temporal Docker
init altında - host adımı yapıldı); `86e6fde9` alan genişletme kuralları (ADR-0253, kablosuz) + model politikası Cloud Core'da
(ADR-0214 ek 14: `GET/PUT /v1/team/queue/models`, sunucu yerel `team/models.json` ile AYNI ayarı veriyor).**
**DÖNGÜ: `d20261002`, 14:05'te başladı (pid 36220, eski kodla). 6 saati geçtiği için Ofis 20:05'ten beri "0/6, çalışmıyor"
gösteriyor - YANLIŞ (kilit yalnız `acquired_at`'e bakıyor): kart `team-lock-heartbeat` (yalnız sunucu, öncelikli). Fable ana
hesapta 2026-10-05 16:00 UTC'ye kadar DOLU.**
**SAHİBİN BU AKŞAMKİ KARARLARI (ayrıntı: hafıza `owner-decisions-2026-10-02-accounts-test-slots`): (1) İKİ EK CLAUDE HESABI
BAĞLANDI (sahip giriş yaptı 21:55): `%USERPROFILE%\.claude-hesap2` (team planı, bir şirket kuruluşu) ve `.claude-hesap3` (pro).
Lead ölçtü: Opus 5.5 üçünde de çalışıyor; Fable HİÇBİRİNDE yok (ek hesaplarda `credits_required` - kredi satın almak sahibin).
Kartlar `account-pool-cycle` (hesap model düşürmeden ÖNCE denenir; sıra hesap-1, -2, -3), `account-pool-visible`. (2) SAHİBİN
FİKRİ - test sırası ONAY / BEKLE: kart `test-slots` (bağımlılığı yok). (3) test sırası + `cycle-seat-pool` yayına girince
çalışan koltuğu İKİ artırılır, ölçülür. (4) sahip artık oyun oynamayacak; o gece VR oyunu vardı (17:25 kapısının Unity hatası).**
**GEÇİCİ KLASÖR: kullanıcının TEMP'inde 2 671 896 öğe vardı (ses derlemi harness'inin dört `mkdtemp`'i, hiç silinmiyordu) -
testler bekliyordu, birim adımı 1 sa 48 dk. Sahip lead'in betiğiyle (`scratchpad/clean-test-temp.ps1`, yalnız dört önek, 24 sa'ten
eski) 2 312 840 klasörü sildi (0 hata, 24 dk). Kalıcı düzeltme kartı `corpus-temp-dirs-leak` (ÖNCELİKLİ). Sonraki kapının süresi
bunun ölçümüdür - QUALIFICATION 45.5'e yazılacak.**
**İŞTE / SIRADA: `cycle-seat-pool` geri döndü (başlatılamayan iş döngüyü bitiriyordu) - sahibin önceliği, onaylanınca AYRI kapı;
`cycle-auto-integrate` geri döndü (iş nesnesine başladıktan sonra alınıyor; lead'in bağlaması `lead/auto-integrate-wiring` @
`742d4f51` dalında saklı); `understanding-rules-read-lemmas`, `execution-call-site-routines`, `narrative-model-wiring` 20:53'te
denetimden döndü; `misheard-ledger-store` yazılıyor. Bugün lead'in kestiği kartlar: stt-engine-on-turn-audit, pack-question-button,
webtask-write-ceiling-retention, cloud-search-engines-probe, stt-corpus-layer2-remeasure, feeder-own-lock, office-stable-seats,
office-panel-plain-turkish, run-liveness-visible, team-status-bounds, area-widen-cycle-wiring, integrate-own-lock,
gate-own-database, gate-parallel-suites, integrate-skips-visible, team-lock-heartbeat, test-slots, account-pool-cycle,
account-pool-visible, corpus-temp-dirs-leak.**
**SAHİBİ BEKLEYEN (acil değil): 38.3-38.5, 39.2-39.4 cümleleri; 43.1 ("Bugün nasılsın" - denetim satırında katman `semantic`);
43.2 (bir düzeltme öğret); STT ölçümü için yirmi cümle (ölçüm kaydı sayfası gelince).**
**BİLİNEN AÇIKLAR: (1) besleyici döngü koşarken kart kesmez (`feeder-own-lock`). (2) döngü turdaki en uzun koşuyu bekler
(`cycle-seat-pool`). (3) Ofis 6 saatten sonra döngüyü ölü gösterir ve başka makine kilidi devralabilir (`team-lock-heartbeat`).
(4) koşuların süre sınırı yok ve takıldığı görünmez (`run-liveness-visible`). (5) kapı tek sıra, ~1,5-2 sa (`gate-parallel-suites`,
`gate-own-database`). (6) tarayıcı görev zinciri üretimde ulaşılamaz (PR-D).**
**KURALLAR (sahip, 2026-10-01; ADR-0214 ek 3-9): (a) sahibe yalnız YENİ FİKİR ve gerçek cihaz denemesi sorulur; roadmap'te
olan iş onaysız kartlanır, kapıdan geçince ONAYSIZ YAYINLANIR ('kapı yeşilse otomatik yayınla'; ek 9'daki istisnalarda
dur ve sor); (b) onaylanan fikir ROADMAP 'Approved ideas'e yazılır; (c) hiçbir ajan durmasın: döngü 30 dk'da bir,
3 çalışan koltuğu, lead'in kapısı için DURAKLATILMAZ; çalıştırılabilir iş 3'ün altına düşerse lead roadmap'ten kart keser;
(d) araştırmacı yalnız roadmap'te OLMAYANI getirir; (e) Postgres/gerçek altyapı NOT_RUN ile merge yok.**
**OTOMATİK ZİNCİR kuyrukta (bitene kadar lead elle yapar): `lead-roadmap-feeder` → `cycle-seat-pool` →
`cycle-auto-integrate` → `cycle-auto-release`. DİKKAT: döngü süreci başladığı andaki koda ve ayara bağlı kalır ve iş
oldukça bitmez; ayar/kod değişince `team/stop.flag` ile güvenli durdur, yenisi kendiliğinden başlar (2026-10-01'de 3 saat
eski 2 koltuklu süreç böyle yenilendi).**
**KUYRUK VERİTABANINDA (ADR-0222): doğrusu `GET http://100.90.158.26:8001/v1/team/queue`; `team/queue.json` ESKİ tohum.
İş eklemek / karar işlemek: `Invoke-TeamApi PUT /v1/team/queue/tasks/<id>` (belirteç `%LOCALAPPDATA%/PagentOS/team-queue.token`;
`NativeProcess.ps1`+`TeamQueue.ps1`+`HttpJson.ps1` dot-source). Sıra `created_at` (öncelik alanı yok).
Onay Merkezi düğmeleri döngü koşarken AÇIK (5f250e5b ile yayında); sahip kararını sayfadan verir. Lead kararı
döngüye taşımak zorunda değil: ek 11 lead dalına girince döngü kararı bir sonraki turda kendisi görür.**
**Web kabuğu: `preview_start web-cloud` (port 3000; 3210 yerel API'ye bağlıdır, sahibin kimliğini TANIMAZ). Lead sahibin
kimliğiyle GİRİŞ YAPMAZ; sayfayı API'den doğrular (`GET /v1/team/approvals`, kuyruk belirteciyle).**
**BAKIM PENCERESİ KOŞTU VE DOĞRULANDI (2026-10-01 19:00 UTC): çekirdek 6.8.0-142, 26→1 güncelleme, cihazlar ~1 dk'da geri;
rapor `team/reports/maintenance-2026-10-01.md`, QUALIFICATION 38.13 PROVEN_REAL, ADR-0223 ek 2. Tek seferlik birimler
kaldırıldı. Kalan: temporal'ın zombisi (`temporal-init-reaper` kuyrukta; compose değişikliği → yayını sahip onaylar).
Döngü 22:16'da 6 yuvaya çıkarıldı (3 denetim 3 yuvayı doldurup çalışanları boş bırakıyordu).**
**MODEL POLİTİKASI (sahip, 2026-10-01, ADR-0214 ek 7): `team/models.json` — lead/inspector `claude-fable-5-1`,
worker/integrator/researcher `claude-opus-5-5`; `cycle.ps1` her koşuyu `--model` ile başlatır (YÜRÜRLÜKTE). Düşüş
zinciri (Fable→Opus→Sonnet), denetleyici ≥ işçi kuralı, Ofis'te seçici + limit yüzdeleri: kuyrukta
`model-policy-cycle` / `-api` / `-office-ui`, `proposals-on-cloud-core`'dan hemen sonra.**
**YENİ KALICI KURALLAR (sahip, 2026-10-01; ADR-0214 ek 4-5, TEAM_PROTOCOL 9a): (1) Postgres/gerçek altyapı iddiası
NOT_RUN kalırsa merge yok — denetleyici dev stack'te koşar; SQLite-only DB değişikliği kapıdan geçmez
(`test_postgres_coverage_ratchet.py`, 51 tablo dondurulmuş borç). (2) Araştırmacı HER döngüde koşar; her öneri
Onay Merkezi'nde 'fikir' olarak sahibi bekler. (3) Zaman bağlı iş kalıcı göreve bağlanır, oturuma değil.**
**Bugün üretimde bulunan kusurlar (hepsi regresyon testli): kilit sürümü varchar(32)'ye sığmıyordu (38.15); bakım
betiği `api-blue`'yu adıyla bekliyordu (38.12) ve kilidi bir kez soruyordu (38.17). Sahibin yeniden deneyecekleri:
üç cümle (38.3-38.5).**
**BAKIM PENCERESİ: BU AKŞAM 2026-10-01 22:00–22:30 İstanbul (19:00 UTC), sahip onaylı. OTURUMA BAĞLI DEĞİL:
sunucunun kendi `pagentos-maintenance-window.timer`'ı çalıştırır (ADR-0223 eki); açılıştan 4 dk sonra
`pagentos-maintenance-verify` doğrular; ev PC'de `PagentOS Maintenance Report 2026-10-01` görevi 22:40 ve 23:10'da
`team/reports/maintenance-2026-10-01.md` yazar. İlk pencere (06:30) KOŞMADI: oturum uyandırmasına bağlıydı, oturum
kapandı. KALICI KURAL (sahip): zaman bağlı her iş kalıcı göreve bağlanır, oturuma değil (TEAM_PROTOCOL 9).
Sonraki oturum: raporu oku; sonuç iyiyse sunucudaki iki `pagentos-maintenance-*` birimini ve ev PC görevini kaldır;
QUALIFICATION'a bakım satırını PROVEN_REAL yaz. Rapor yoksa: `collect-maintenance-report.ps1 -Date 2026-10-01`.**
**DÖNGÜ `cycle-2026-10-01`: ilk tur 2026-09-30 21:02 → 2026-10-01 00:16 UTC normal bitti (32 koşu, tahmini 21,67 USD,
limit yok). 8 iş `merged` (`integrate/cycle-2026-10-01`): understanding-normalize, narrative-intent-wiring,
ledger-device-stamp, allowlist-editor, cloud-device-registry, cycle-lead-run, maintenance-reboot-script,
operator-postcondition-uwp. 3 iş "alan dışı dosya" ile durdu — LEAD'İN KART HATASI (dar alan); alanlar genişletildi,
2026-10-01 07:49 UTC'de aynı CycleId ile yeniden başlatıldı (iki geçiş: önce understanding-semantic-index +
answer-mode-intent-precision, sonra `intents.py` paylaşan app-open-named-device-not-dropped; betik scratchpad'de,
yeniden üretmek için: işi `inspecting` yap, `cycle.ps1 -CycleId cycle-2026-10-01 -Base team/nightly/lead`).
3 iş `depends_on` ile bekliyor (threshold-policy, corrections-memory, stt-corpus): katman 1+2 MAIN'e girince açılır.
LEAD'İN SIRADAKİ İŞİ: yeniden koşu bitince `integrate/cycle-2026-10-01` üzerinde ortak dosyaları bağla
(`stt-confusions.json` → `protocol_files.BUNDLED` + falsification listesi; `team/plans/*-adr.md` → DECISIONS'a
numaralı; `maintenance-reboot.tests.ps1` → quality-gate + ci.yml; THIRD_PARTY_COMPONENTS'e "değerlendirildi,
reddedildi" kaydı; LocalEmbedder süre ölçümü), tam kapı, main'e birleştir, işleri `awaiting_release` yap, döngüyü
yeniden başlat (3 bekleyen iş), sahibin yayın onayını iste. Tavansız (ADR-0214 ek 3). Gece görevi 02:00.**
**Sahibin gerçek cihaz denemesi (2026-09-30 20:10 UTC, MAIL, `/voice` "Bu bilgisayar"):** QUALIFICATION 30.10
PROVEN_REAL (aday hafıza satırı üretimde). İki kusur görüldü, kuyruğa `approved` düştü (gece döngüsü):
`answer-mode-intent-precision` ("Türkçe oku" cümlesi cevap kipini `detail` yaptı + asistanın kendi cevabı hafıza
adayı oldu) ve `operator-postcondition-uwp` (Hesap Makinesi açıldı ama "açamadım" dendi: `UWP_HOSTED_IMAGES`
yalnız `systemsettings.exe`). Sahibin kalıcı cevap seviyesi şu an `detail`; geri alınması sahibe soruldu.
Üçüncü cümle ("Ofis bilgisayarımdan hesap makinesini aç") STT'de "Ofisü bilgisayarında … açın" oldu: yönlendirici
niyet bulamadı ("açın" kipi tabloda yok), model cihazsız `operator.app_open` çağırdı (araçta cihaz alanı yok),
Hesap Makinesi MAIL'de açıldı — `app-open-named-device-not-dropped` kuyrukta. MAIL yeniden kuruldu (verify 14/14).
Bulut işçisi ÇALIŞIYOR (`bulut`, ölçüm evidence'ta); `/tmp/cb-watch.sh` durduruldu.
**pilot-02 — YAYINLANDI (main `8d8d0f18`, 2026-09-30 12:38 UTC). Dal `team/nightly/lead`: gece döngüsünün
(02:00) yazacağı kuyruk/rapor değişiklikleri buraya düşer; işçi dalları `main`'den açılır.** ADR-0218…0222.
İlk kapı 30/34 idi: Docker Desktop yine kapanmıştı (3 adım) + sahte API günlüğünü yanıttan sonra yazan bir
test yarışı (düzeltildi). Sonra lead'in sunucu adımları (sahip devretti): bulut işçisi imajı, profil,
`docker stats` ölçümü, `bulut` alias'ı; `PAGENTOS_TEAM_STORE=database` geçişi sahibin oturum belirteci
dosyasından SONRA (yoksa iki ayrı kuyruk olur). Sahipte: kayıt belirteci + oturum belirteci + MAIL kurulumu.
Kuyrukta on dört iş `approved` (gece döngüsü 02:00). Bulut kuralı seçenek 4.
Önceki: pilot-01 MAIN'DE (tam kapı 34/34, uç `1c7014c3`).
Sahip üç öneriyi onayladı (2026-09-30). Lead sekiz işe böldü (`team/plans/pilot-01-split.md`); bu
döngüde üçü koştu ve denetleyiciden geçti: `narrative-collector` (ADR-0216), `execution-target-rule`
(ADR-0213 PR 1), `onay-merkezi` (ADR-0217). Lead birleştirmede ortak dosyaları bağladı (ledger sözlüğü,
`main.py` router, kabuk bağlantısı, şema, `test_pilot01_wiring.py`). Toplam 5,89 USD; 0 çakışma, 3 geri
verme (biri lead'in kart hatası). Döngünün öğrettikleri ADR-0214 ekinde.
**Sıradaki:** YAYIN ONAYI (sahip): main'de ADR-0215 (broker tek
teslim) + pilot işleri, üretim `771a9e53`. Sonra pilot-02: bulut işçisi (entegratör önce), kural
tablosunun bağlanması, anlatının sesi; sahibe soru: bulut işinde "sahip yokken görev yok".

Son biten iş (2026-09-30): **team bootstrap (TEAM_PROTOCOL.md, ADR-0214)** — main'de. Dal
`feat/dev-team`, uç `137d7ef5` üzerinde tam kapı 34/34 PASS.
Sahibin kararı (2026-09-29): proje bundan sonra bir Claude ajan EKİBİYLE, döngülerle
geliştirilir; sahip yalnız üç kapıda konuşur (fikir onayı, yayın onayı, gerçek cihaz kanıtı +
son hüküm). Kurulan: `.claude/agents/` (lead, researcher, integrator, worker, inspector),
`docs/TEAM_PROTOCOL.md`, ROADMAP güncellemesi, `team/` (kuyruk + şema + kilit),
`scripts/team/` (cycle, new-worktree, close-worktree, integration-branch, collect-reports,
register-nightly) + `scripts/tests/team-cycle.tests.ps1` (kapıda). Gece görevi KAYDEDİLMEDİ.
**YAYINLANDI (sahibin cümlesiyle, 2026-09-29 19:58 UTC):** main `771a9e53` (ofis günü,
ADR-0208…0212 + PR-B) üretimde; pin ve timer doğrulandı.
**Kapının bulduğu kusur (ADR-0215):** broker aynı komutu cihaza İKİ KEZ verebiliyordu (18 Eylül
yarışının kapanmamış sıralaması; "gönderdim" notu gönderimden SONRA düşülüyordu). Dalda düzeltildi
(`deliver_command` göndermeden ÖNCE sahipleniyor), `test_broker_deliver_once.py`. **Yayınlanmadı:**
üretim `771a9e53` bu yarışı hâlâ taşıyor; sonraki yayınla gider.
**Sahip kapısında bekleyenler:** (1) MAIL ajanının bu ağaçtan yeniden kurulması (komutlar döngü
raporunda); `-AuthorizeTasks` anahtarı YOK (PR-C'nin işi); (2) gerçek cihaz denemeleri
(`team/reports/bootstrap-2026-09-30.md`); (3) ofis PC'sinde kurucunun yeniden koşulması.
Ofis günü kapandı: `integrate/office-day-2026-09-29` tam kapı 33/33 (`8cff1a69`), main'de.

**Ofis günü (2026-09-29, makine GMKADIRAKBABA — şirket PC'si): İŞ BİTTİ, EV PC'DE KAPI BEKLİYOR.**
Toplama dalı `integrate/office-day-2026-09-29` (origin'de; main `4d5dd637`'den) altı olayın
hepsinin düzeltmesini taşıyor; ADR-0208…0212 + ADR-0203 eki DECISIONS.md sonunda,
`state/BUILD_STATE.json` `office_day_2026_09_29`. **Bu makinede yayın YOK, main'e merge YOK.**
- **A/B/C** audit klasörü ACL + kayıt dosyası + 6b.4 (ADR-0211; bağımsız güvenlik incelemesi
  5 bulgu buldu, hepsi kapandı — junction takibi dahil).
- **D** oturum cihazı yakınlığı (ADR-0208, gerçek zamanlı sözleşme **v3**).
- **E** Operatörsüz cihazda tek adımlık uygulama açma (ADR-0209) + **D+E birleşim düzeltmesi**
  (`a36125ac`: oturuma bağlı portun yoklaması yoktu → ofis oturumunda hesap makinesi evde açılıyordu).
- **F** araştırma raporu: `research.open` düzeltildi (F1); ofiste sahibin Chrome'unda sekme
  olarak açma (ADR-0210, F2) **bulut yarısı hazır, ÜRETİMDE ÇALIŞMAZ** — tarayıcı işçisi tailnet
  hedeflerini reddediyordu; **sahip dar istisnayı onayladı** (`fix/artifact-origin-ssrf-allow`: yalnız broker
  host+port + `/v1/artifacts/renders/view`; **ofis PC'de kurucu yeniden çalışmalı**; `godseye.open` KAPSAM DIŞI) ve `PAGENTOS_ARTIFACT_DOWNLOAD_ORIGIN`
  üretimde api'ye hiç ulaşmıyordu (compose düzeltildi → yayın + pin).
- **G** söylenen cihaz adı artık işi yapan cihaz (ADR-0212). Ses yolunda hiç iletilmiyordu;
  ekran görüntüsünde Google sorgusu "ofis bilgisayarında Yapay Zeka son gelişmeler" idi.
**EV PC'DE YAPILACAK (bu makinede yapılmaz):** `git fetch && git checkout
integrate/office-day-2026-09-29` → tam kapı: **`dotnet test`** (17 Operatör/Belge masaüstü
testi dahil; ofis PC'sinde koşulmaz çünkü sahibin masaüstünde Not Defteri açar), docker
adımları (dev stack, alembic, entegrasyon) → main'e merge → yayın için sahibin sözü
(`-BlueGreen` + recovery pin; sözleşme v3, compose env satırları) → MAIL ajanını yeniden kur,
`-AuthorizeTasks`. Sonra sahip: web kabuğunda "Bu bilgisayar"ı bir kez seçer.
**Bu makinenin sınırı (kalıcı):** docker/WSL yok; `dotnet test` yasak (bkz. bellek).
Temel `4d5dd637` üzerinde de aynı 14 Temporal testi kırmızı (14 kaldı / 206 geçti): ortam.
Son biten iş (2026-09-29): **tarayıcı görev döngüsü PR-B (ADR-0207)** — main'de, YAYINLANMADI.
Dal `feat/browser-task-loop`, uç `f0d556b5` üzerinde tam kapı 32/32 PASS. `app/webtask/`
(döngü, kapı, doğrulayıcı, planlayıcı arayüzü, servis, cihaz portu, `BrowserTaskWorkflow`),
`web_tasks` tablosu (göç 0062), sözleşme **v1.7** (işlem adı eklemeden: tıklamada
`risk_ceiling`, yasak liste cihazda da). Sahibin tetikleyebileceği hiçbir şey YOK (PR-D);
sahibin Chrome'u sürülmedi (PR-C). Kanıt: QUALIFICATION Aşama 34, 20 mutasyon KIRMIZI
(`docs/evidence/adr-0207-pr-b-mutations-2026-09-29.json`), bağımsız güvenlik incelemesi.
**PR-C başlamadan okunacak:** DECISIONS.md "Recorded for PR-C, and binding on it" — altı
sınır (fill/select/set_checked sınıflandırılmıyor; adsız düğme; site adı kuralı; gözlem
saklama süresi; read-back ile söz arasında yeniden bağlanan düğme; kart alanı).
Yayınlanırsa göç 0062 üretimde uygulanır; yayın yalnız sahibin onayıyla.
Rerank KAPALI. `feat/hand-gestures-stage1` DOKUNULMAZ.

Önceki durum: Yok. (2026-09-28 akşam: M29 ilk adım tamam — iki cihaz kayıtlı ve adlarıyla seçiliyor;
ADR-0203/0204/0205 main'de ve üretimde; pin yenilendi.)
**Sahibi bekleyen:** (1) yerel modda bir cümle ("Bundan sonra araştırma raporlarını her zaman
Türkçe oku") → CANDIDATE satırı okununca ADR-0201 PROVEN_REAL olur (QUALIFICATION 30.10);
(2) M19b kabul satırları: iki gerçek makinede sesle "ofis bilgisayarımda … aç".
`feat/hand-gestures-stage1` DOKUNULMADI, sahibin "birleştir"ini bekliyor.
Sonraki: PR-3 rerank (Jev/cross-encoder) → sonra JARVIS sırası 2: browser-use.

**Sahibin 2 notu (2026-09-21, sesle verildi):**

1. *Tekrar sayısı.* "Yukarı tuşuna 5 kere bas" / "5 defa yap" kabul edilmiyor; sahip her
   basışı ayrı söylemek zorunda. Yapılan: `intents.py`'de `spoken_repeat()` ("N kere/defa/
   kez/sefer", sözcük ya da rakam), `ResolvedIntent.repeat_count`, tur kaydına kopya;
   `plans.press_key/press_shortcut/pointer` N adımlı plan (tek activate); `operator.key` ve
   `operator.pointer` (kaydırma) sayıyı uygular ve konuşmada söyler. ADR-0195.
2. *Hareket kaydı ("makro").* "Yeni hareket oluştur/başlat" → sonraki komutlar hem yapılır
   hem kaydedilir → "hareketi bitir/tamamla" → ad sorulur → ad söylenir → kaydedilir;
   sonra "<ad> aç" o adımları tarifsiz tekrarlar. Yapılan: `app/macros/` paketi
   (`voice_macros` tablosu, alembic 0061, ad eşleme), `tools_macros.py`
   (macro.record_start / record_end / cancel / name / run / list / delete), yönlendiricide
   MACRO_* niyetleri (kayıtlı adlar ve "ad bekleniyor" durumu oturumdan enjekte edilir),
   `handle_tool_call` içinde adım yakalama, `macro.run` sunucu tarafında sıralı yeniden
   oynatma (adım başına step-up denetimi). ADR-0196.

**Durum (2026-09-21 akşam): BİTTİ ve YAYINDA.** Commit `6de7ab39`, blue-green yayın OK
(api-blue, migrasyon 0061 uygulandı, `voice_macros` tablosu üretimde, sağlık ok). Güvenlik
+ test incelemesi bulguları (rezerve adlar, "hareketi durdur" = operatör iptali, "unutma"
silme değil) aynı commit'te düzeltildi. Recovery pin sahip tarafından yapıldı. Kalan
yalnız sahibin canlı denemesi.

**Sahibin deneyeceği cümleler (yerel mod, bir pencere odaktayken):** "Yukarı tuşuna 5 kere
bas" · "Üç kere aşağı kaydır" · "Kontrol Z'ye iki kere bas" · "Yeni hareket oluştur" →
birkaç komut → "Hareketi bitir" → (ad sorulur) "Yeni mail sekmesi" → "Yeni mail sekmesi aç"
· "Hangi hareketlerim var" · "Yeni mail sekmesi hareketini sil".

**Sahibin 2 yeni eklemesi (2026-09-21 akşam, sesle; sahip "testi sonra yapacağım" dedi):**

3. *God's Eye View (ADR-0197).* MIT, Vite dev sunucusu (anahtar aracısı içinde), anahtar
   şart değil. Yapılan: Cloud Core'da `aux` profilli `godseye` compose servisi
   (`infra/docker/godseye/Dockerfile`, üst kaynak commit `0dbde1e3` pinli, yalnız tailnet
   IP'sinde `:4173`, isteğe bağlı anahtarlar `/opt/pagentos/godseye.env`), yayın betiği
   `aux_up` (api işleminden SONRA, en iyi çaba); web kabuğunda `/gods-eye` "Dünya Gözü"
   sayfası (iframe + yeni sekme); sesle "Dünya gözünü aç" → `godseye.open` (sahibin
   Chrome'unda yeni sekme). **YAYINDA** (`dad462ac`, api-green, 2026-09-21 18:08):
   `http://pagentos-core:4173/` tailnet'ten HTTP 200 "God's Eye View", konteyner
   healthy, bellek 1.03/1.5 GiB (sınır bir sonraki yayında 2 GiB'a çıkarıldı). Sahibin
   kalanı: canlı deneme ("Dünya gözünü aç" + web kabuğunda "Dünya Gözü") ve recovery
   pinini yenilemek (compose değişti):
   `bash /opt/pagentos/app/scripts/cloud/install-recovery-supervisor.sh dad462ac5a56b8130f23bdbf6ce14f3860176b59`
4. *El hareketiyle kumanda (ADR-0198, iki aşama).* 1. aşama tarayıcı mühendisi ajanında
   (worktree `agent-ac27be8adddc2920d`): MediaPipe el takibi gözün açık kamerasında,
   kaydırma = ok tuşları, baş+işaret çevirme = ses, iki el açma = "f" (tam ekran), sunucuda
   `gesture` istemci olayı → aynı araçlar; pinç olayları yalnız üretilir. 2. aşama (pinç-fare)
   akış kanalı ister; 1. aşama sahibin elinde ölçüldükten sonra tasarlanacak.
   Sahip: "ajan bitince birleştir, önce ayrı test edelim" → **dal `feat/hand-gestures-stage1`
   (`9e15476f`, origin'de; worktree `.claude/worktrees/agent-ac27be8adddc2920d`) AYRI
   DENEME İÇİN ÜRETİMDE** (api-green, kontrat v3, 2026-09-21 19:01; son iyi bilinen
   `5b4e771c`, ondan önce `dad462ac`). Gate: API 268+87 (worktree'den), web 2049+ (vitest),
   tsc/oxlint temiz. İlk canlı denemede bulunan ve dalda düzeltilen 2 hata: (a) `EyeStore`
   sayaçlı kaynak sarmalayıcısı `videoElement()`'ı iletmiyordu → el kumandası sonsuza dek
   "Bekleniyor" (`72000f3f`, regresyon testi KIRMIZI kanıtlı); (b) çevirme her zaman
   `media.volume`'a gidip "volume_failed" ile reddediliyordu (sahip videoyu elle açmıştı)
   → canlı medya oturumu yoksa odaktaki oynatıcının ok tuşları (`9e15476f`). Sonraki
   ekleme adayı: cihaz ajanına sistem ses tuşları (`volume_up/down/mute`), ajan yayını ister.
   Canlıda görülen: izleme 18 kare/sn, pinç "tutma" algılanıyor; kaydırma henüz raporlanmadı.
   Sahip web kabuğunu worktree'den başlatır: `…\agent-ac27be8adddc2920d\scripts\voice\
   start-web-voice.ps1` → /core/cockpit → Yerel mod → kamera aç → "El kumandası" aç.
   **SAHİP KARARI (2026-09-22): dal, sahip "birleştir" demeden main'e BİRLEŞTİRİLMEZ**
   (deneme yayınları dalı Cloud Core'a çıkarmaya devam edebilir; main'in son yayını
   `dad462ac` son iyi bilinen olarak durur). "Birleştir" gelince: `git merge
   feat/hand-gestures-stage1` main'e, ADR-0198/0199'a kanıt satırı, yayın, recovery pin.
   Kötüyse: `release-cloud-core.ps1 -BlueGreen` main'den geri yayın (dad462ac LKG).
   2. aşama (ADR-0199) ÜÇ YARISI DA DALDA: `2dcf434a` — sunucu (`operator.pointer_session`
   + `/pointer` WebSocket, `pointer_stream` çerçevesi `type` ayrıştırıcısıyla), tarayıcı
   (pinç-fare / tık / uzun tık / yumruk sürükleme, `PointerStreamClient`, HUD), cihaz
   (`PointerStreamController`, `pointer.stream_begin/end`, tek yönlü pipe akışı). Gate:
   API 303, web 2115, cihaz 60 (+3 lab), Release derleme, qualify 89 ✓. **Yayın:**
   `2dcf434a` Cloud Core'da (api-green, 2026-09-21 21:33; LKG `999ccee9`). **Cihaz
   kurulumu sahipte (UAC):** worktree'den
   `E:\AI\PersonalAgentOS_Claude_Autonomous_Build_Package_v1\.claude\worktrees\agent-ac27be8adddc2920d\scripts\install-device-service.ps1 -DisplayPower -Operator`
   (yükseltilmiş PowerShell). Sağlık "degraded [backup]" bu dizinin ÖNCESİNDEN geliyor;
   ayrı bakılacak. MediaPipe INFO satırları artık console.error'a düşmüyor.
5. *Türkiye trafik kameraları God's Eye'a (sahip 2026-09-21: "tüm Türkiye'deki
   mobeseleri ekleyemez miyiz, paylaşılan").* Kapsam kararı: EGM MOBESE akışları herkese
   açık DEĞİL (eklenemez); belediyelerin paylaştığı trafik kameraları (İBB `application.
   ibb.gov.tr/IBB/tk.htm` ~700+, diğer büyükşehirler) eklenebilir. God's Eye kamera
   katmanı `config/cctv_sources.<şehir>.json` (id, ad, lat/lon, feedType image|hls, url,
   provider, license) + proxy allowlist ile çalışıyor. Yapılacak: İBB liste + akış deseni
   keşfi (site 21.09 akşam 503 verdi), JSON üretimi, Dockerfile overlay ile konteynere
   kopya, `cctv.js`'nin dosyayı otomatik yükleyip yüklemediğinin doğrulanması. BEKLİYOR
   (4/1. aşama birleşince).

## Şu anki durum

- **Üretim:** Cloud Core `aa35fcf3` (api-blue, 2026-09-30 17:05 UTC; son iyi bilinen `8d8d0f18` —
  yayın kendisi yazdı; pin `aa35fcf3a57e8df4ad7dfabced705848c0976377`) + `godseye`. Sağlık **ok**, `failing_checks` boş. Recovery pini
  `8d8d0f18d6ae102f0650bafc1dffad6152ea38f3`; timer 0 ile çıkıyor; `RECOVERY_BUNDLE_STALE` YOK (pin'den
  sonra paket ile ağaç cmp ile aynı bulundu, dosya silindi). Şema `0063_team_state`, sözleşme v3.
  `PAGENTOS_TEAM_STORE=file` (veritabanına geçiş sahibin oturum belirteci dosyasını bekliyor).
  Bulut işçisi: dizinler hazır, imaj `aa35fcf3` ağacından derlendi ve sınandı (Playwright 1.62.0, Chromium
  açılıyor, malzemesiz başlatma 2 ile çıkıyor); profil ÇALIŞIYOR: cihaz `bulut` (`ad64617c-b1e8-465f-ac65-4c780082cc18`, platform cloud, alias `bulut`),
  broker'a bağlı, sağlıklı; tepe bellek 650 MB / 2 GB (kanıt dosyası). Bekçi ve yardımcı betik: `/usr/local/sbin/cloud-browser-mint.sh` kaldı, `/tmp/cb-watch.sh` durduruldu. Tailscale SSH ek doğrulaması zaman zaman isteniyor.
  Geri dönüş: `bash /opt/pagentos/app/scripts/cloud/release-cloud-core-bluegreen.sh --rollback`.
  Realtime sağlayıcıları: `local-router`, `openai-realtime`.
- **M29 ilk adım TAMAM (2026-09-28): iki cihaz.** MAIL = `ev` (`3f60fdb5-5022-48cf-bb3c-d7192466b701`, ev
  PC'si, tam yetki, 105 yetenek); GMKADIRAKBABA = `ofis` / `iş`
  (`9efa9d8b-b0e6-4758-a03a-387c3e20a0d2`, şirket PC'si, azaltılmış yetki: operator, ekran gücü,
  tarayıcı işçisi YOK, 13 yetenek). Alias'lar `scripts/core/set-device-aliases.ps1` ile
  ayarlanır; envanter `docs/OPERATIONS.md` "Device inventory". Yayın yalnız ev PC'sinden.
  Bu makinede PATH bozuk: `powershell` adıyla bulunmaz, betik `& "tam\yol.ps1"` ile çağrılır.
- **Kapı notu:** `-Fast` kapısı tarayıcı e2e, entegrasyon ve PS paketlerini KOŞMAZ; yayın
  betiklerine, `scripts/lib`'e ya da `services/browser`'a dokunan iş tam `quality-gate.ps1`
  ister (~45 dk). Sahibe verilen her betik önce çalıştırılır.
- **Cihaz (sahibin PC'si, "MAIL"):** ajan 2026-09-30 20:03 UTC'de main `aa35fcf3` ağacından yeniden kuruldu
  (`-DisplayPower -Operator`); `verify-device-service.ps1` 14/14 PROVEN_REAL (6b.4 dahil), 106 yetenek, `browser.observe`
  duyuruluyor (`docs/evidence/verify-device-service-mail-2026-09-30.md`). Araştırma izni yerinde. Sahibin gerçek cihaz
  denemeleri (dört cümle) hâlâ bekliyor.
- **Hafıza (2026-09-27):** her modda sahibin cümlesi yazım politikasından geçiyor (ADR-0201), geri çağırma semantik (ADR-0200). Önceki not: 2 temizlikten sonra ~452 satır; ilk öğrenilmiş tercih durable; hafıza bloğu
  hem ücretli oturumda hem yerel moddaki serbest sohbette (ADR-0183…0193).
- **CI yok:** GitHub Actions kapalı (sahip ödeyemiyor). Kanıt yereldir; sahip 2026-09-19'da
  "her seferinde tüm testleri koşma" dedi → dokunulan paketler + hedefli korpus yeter.


**JARVIS hedefi (2026-09-27, sahip: "aslında birebir aynı hale getirmek istiyorum"):**
`docs/ROADMAP.md` sonuna "The JARVIS target" bölümü eklendi — yetenek↔durum tablosu, tek seferlik
sınırlar ve **bağlayıcı sıra**: 1 hafıza (PR-2/PR-3) → 2 browser-use → 3 sekreter (Radicale+mail+telefon
köprüsü) → 4 ev (Home Assistant) → 5 her yerde (M29 yeniden açılır) → 6 ses+karakter → 7 görüş.

## Sıradaki işler

00. **Pilot döngü** (`scripts/team/cycle.ps1 -Research -ResearchBrief ...`): konular ADR-0213
   (bulutta yürütme), anlatı satırı, PR-C'nin bulutta koşan hali. Sonra **YAYIN ONAYI**: ADR-0215
   (broker tek teslim) main'de, üretimde değil.
0. **ADR-0207 PR-C** (model planlayıcılar + sahibin kendi Chrome'u, PROVEN_REAL). Ön koşul,
   hepsi sahipte: T3 mağazasının ve T5 web postasının adı; MAIL'de ajanın bu ağaçtan yeniden
   kurulması (kurulu 0.6.0 `browser.observe` adını reddediyor); `-AuthorizeTasks` izni;
   varsa yasak listeye eklenecek şirket paneli / Kolay Monitor host'ları. Sonra PR-D (niyet,
   ses araçları, web kabuğu) ve sözleşme v1.8 (iframe, kapalı gölge DOM).
1. **Sahibin "2 not + 2 yeni ekleme"si** — hafıza bitince vereceğini söyledi (2026-09-21).
2. *Tarifle tıklama.* "Şu kameralı videoyu aç", "Kratos'un olduğu videoyu aç" bugün
   çalışmaz: `vision.LOCATE_QUESTION_TR` bir ADA göre soruyor ("X adlı düğme ya da öğe
   nerede?"), tarif değil; üstelik bulunamayınca `search_if_missing` YouTube'da o kelimeyi
   aratıyor. Yapılacak: planlayıcı ad/tarif ayrımı yapsın, tarif için ayrı bir görsel soru
   kurulsun, tarif bulunamazsa arama yapılmasın. Yazıyla bulunabilen hedefler yine ücretsiz
   yerel OCR'da kalsın (ücretli görsel çağrı yalnız tarif için).
3. Bildirim "hiçbir kanal taşımadı" (başarısız iş bildirimi hiçbir kanaldan gitmedi).
4. Sözcü sayfalarında alıntıya sayfa çerçevesi satırı karışıyor.
5. `scripts/verify-device-service.ps1` 6b.4: DateTime taşması (ayrı iş çipi açıldı).
6. Test defteri (claude.ai artefaktı) eski hesaba ait; gerekirse `default_registry()`'den
   yeniden üret.
<!-- session-start:end -->

## Nasıl yayınlanır / kurulur

- Bulut: `scripts/cloud/release-cloud-core.ps1 -BlueGreen` (asla `2>&1` ile değil; kirli
  ağaçta `-AllowDirty` yalnız HEAD'i gönderir). Sonra sunucuda
  `bash /opt/pagentos/app/scripts/cloud/install-recovery-supervisor.sh <TAM 40 haneli sha>`
  (kısa sha reddedilir). SSH: `root@100.90.158.26` (Tailscale).
- Cihaz: yalnız sahip, tek UAC —
  `Start-Process powershell -Verb RunAs -Wait -ArgumentList '-NoProfile -ExecutionPolicy Bypass -NoExit -File "E:\AI\PersonalAgentOS_Claude_Autonomous_Build_Package_v1\scripts\install-device-service.ps1" -DisplayPower -Operator'`
  (iki anahtar da zorunlu). Önce `devices/windows-agent` Release derle +
  `scripts/qualify-staged-update.ps1 -Quiet`.
- Üretim kayıtlarını okumak: `docker exec -i pagentos-prod-postgres psql -U pagentos -d pagentos_prod`
  (tablolar: `operator_missions`, `device_commands`, `realtime_tool_calls`, `research_runs`,
  `research_evidence`, `devices`).

## Bugün yayınlananlar (sahip henüz CANLI denemedi — işaretli olanlar)

| Commit | Ne | Canlı denendi mi |
|---|---|---|
| 42ce5a9 | Görev heartbeat'i — "YouTube'u aç" yeniden çalışıyor | ✅ |
| 961952f | "Arama kısmına X yaz" = açık sekmenin sitesinde arama | kısmen |
| 8596048 | /core düzen (canlı göstergesi, kırpılmayan kartlar, 4K yazı) | — |
| 7fa04ea | Ücretsiz yerel mod (Chrome Web Speech + yönlendirici + tarayıcı sesi) | ✅ |
| 67fff03, b4f8ff9 | Cihaz: ekran yakalama çerçeveye sığar; `screen.ocr` + JPEG | ✅ |
| 6615ad4, 27e2067 | Video adı yerel OCR ile bulunur, tüm Chrome pencereleri, doğru monitör; yanlış video açılırsa geri döner | ✅ ("Adana Adliyesi 4" açıldı) / geri dönme denenmedi |
| f53527e | "Videoyu durdur/başlat/başa al", "X sekmesine geç", "0 tuşuna bas" | ❌ (tuşlar cihaz kurulumu bekliyor) |
| c968d04 | Araştırma sayfaları sahibin Chrome'unda, OCR ile (ADR-0177) | ❌ başarısız oldu → 2336300 |
| 60060b2 | Araştırma: emekli model kimliği + sağlayıcı yedeği | ❌ |
| 52fd71b | Yerel modda serbest sohbet = Claude Haiku (`claude-haiku-4-5`) | ❌ |
| 85b5432 | Chrome'a yazma OCR ile doğrulanır | ❌ |
| 2336300 | Araştırma: Türkçe kaynaklar önce, OCR paragraf birleştirme (ADR-0178) | ❌ |

## Sahibin kararları (bugün)

- Yerel mod: STT = Chrome Web Speech; komutlar yönlendiricide; serbest sohbet = Claude Haiku.
- Araştırma: "Her şeyi kendi Chrome'umda, gözümün önünde yapsın" (ADR-0177).
- Ekranda bulma: önce yerel OCR (ücretsiz), görsel sağlayıcı yalnız konum/oynatıcı için.
- İkinci sesli onay yok (mail gönderme hariç) — ADR-0171 ek.
- "Birden fazla pencere varsa ikisine de baksın."

## 2026-09-20 gecesi yayınlananlar (sahip henüz CANLI denemedi)

`42eb997` yorum cümlesi tıklama sanılmıyor · `c71fc46` başarısız araştırma raporunu okuyor +
arama bölgesi işçide uygulanıyor (ADR-0179) · `4553004` ok tuşları, yarım kalan sıra sayısı,
"X hariç tüm sekmeleri kapat" (ADR-0180) · `9989f57` bildirim sayacı korunan sekmeyi
kaybettirmiyor · `d040659` yerel modda sesle kamera (ADR-0181).

Doğrulanacak cümleler: "kamerayı aç/kapat" (yerel mod, Chrome izin soracak), "sağ/sol tuşuna
bas", "YouTube hariç tüm sekmeleri kapat", "birinci sekmeye geç", "yanlış yere tıkladım"
(görev başlatmamalı), "yapay zeka haberlerini araştır".

## 2026-09-20/21: araştırma ve hafıza (ADR-0183…0193)

- Araştırma sahibin kendi Chrome'unda, Google'da, sekmelerde, DOM'dan okunarak çalışıyor
  (enroll-owner-chrome.ps1 -AuthorizeResearch yapıldı). "araştırmayı oku" haberin kendisini
  okuyor; yabancı kaynaklar Türkçeye çevriliyor.
- Hafıza: 955 nabız satırı sahibin onayıyla silindi (ADR-0190). Makine kayıtları artık
  hafızaya yazılmıyor (ADR-0193). İlk öğrenilmiş tercih üretimde durable:
  "Sahip 'yapay zeka' konusunu düzenli olarak araştırıyor". "bunu hatırla" ve
  "… hakkında ne biliyorsun" yerel modda da çalışıyor (ADR-0192).
- ADR-0193 kapsamındaki eski makine-kaydı satırları da sahibin onayıyla silindi
  (2026-09-21, 935 satır). İki temizlikte toplam 1890 satır; ledger'da hepsinin aslı duruyor.

## Bilinen tuzaklar

- `services/api/app/operator/plans.py` CRLF; yama betikleri CRLF-duyarlı olmalı.
- Bash heredoc'larda ters eğik çizgi bozulur → dosya araçlarını kullan.
- Mutasyon kanıtında dosyayı yedekten sha256 ile geri yükle, asla `git checkout --`.
- Tam birim paketi ~25 dk, tam korpus ~12 dk; aynı anda kalite kapısıyla koşma
  (`test_contract_falsification` canlı bir dosyayı geçici siler).
- Alt ajanlar worktree'de izole çalışır; sonuçları yama olarak ana ağaca uygula,
  `docs/DECISIONS.md` çakışırsa ADR metnini sona ekle.
