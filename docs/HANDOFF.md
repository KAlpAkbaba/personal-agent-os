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

**pilot-02 — YAYINLANDI (main `8d8d0f18`, 2026-09-30 12:38 UTC). Dal `team/nightly/lead`: gece döngüsünün
(02:00) yazacağı kuyruk/rapor değişiklikleri buraya düşer; işçi dalları `main`'den açılır.** ADR-0218…0222.
İlk kapı 30/34 idi: Docker Desktop yine kapanmıştı (3 adım) + sahte API günlüğünü yanıttan sonra yazan bir
test yarışı (düzeltildi). Sonra lead'in sunucu adımları (sahip devretti): bulut işçisi imajı, profil,
`docker stats` ölçümü, `bulut` alias'ı; `PAGENTOS_TEAM_STORE=database` geçişi sahibin oturum belirteci
dosyasından SONRA (yoksa iki ayrı kuyruk olur). Sahipte: kayıt belirteci + oturum belirteci + MAIL kurulumu.
Kuyrukta beş iş `approved` (gece döngüsü 02:00). Bulut kuralı seçenek 4.
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
  açılıyor, malzemesiz başlatma 2 ile çıkıyor); profil BAŞLATILMADI (sahibin kayıt belirtecini bekliyor). Tailscale SSH ek doğrulaması zaman zaman isteniyor.
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
- **Cihaz (sahibin PC'si, "MAIL"):** ajan `0.6.0`; tuşlar, sekmeler, kamera, sahibin
  Chrome'unda araştırma (CDP 127.0.0.1:19222, `-AuthorizeResearch`) kurulu ve canlı denendi.
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
