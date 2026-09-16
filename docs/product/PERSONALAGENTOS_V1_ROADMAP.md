# PERSONALAGENTOS v1.0 MASTER ROADMAP

**Sürüm:** 2026-09-12 · **Spec:** `PERSONALAGENTOS_V1_MASTER_CHECKLIST.md` (750 madde, kalıcı kimlikler)
**Matris:** `PERSONALAGENTOS_V1_FEATURE_MATRIX.md` (750/750 satır, 14 kolon, doğrulandı)
**Ölçüm tabanı:** `docs/audit/2026-09-12-tam-olgunluk-raporu.md`
**MULTI-DEVICE / M29: DEFERRED — hiçbir batch'e giremez.**

---

## 1. MEVCUT DURUM (PHASE 0 — korunan gerçek)

| Alan | Ölçüm |
|---|---|
| HEAD | `48dfcc3` — *docs: row 26.16 PROVEN_REAL* (2026-09-12 11:51 +03) |
| origin/main | `48dfcc3` (HEAD ile aynı, ileri/geri fark yok) |
| Çalışma ağacı | Temiz (yalnız yeni `docs/audit/`, `docs/product/`) |
| CI | `48dfcc3` → **success** (7 job). Önceki yeşiller: `714ff2b`, `f2fe54a`, `d86b3d9` |
| Üretim (Cloud Core) | `714ff2b523cac4a42411e5cd7b8a6f4c088a3aee`, `environment=prod`, uptime 19.646 s |
| Last-known-good | `d86b3d94c829d3e3c6119af4d46d72e7b91dfd12` |
| Üretim sağlığı | `status: ok` — db, redis, object_store, temporal, broker, artifacts, voice, memory, selfhealing, evolution, security, identity, mobile, voice_realtime, temporal_worker, research, routine_clock, retention → **18/18 ok** |
| Sözleşme sürümleri | action 13, ui_state 13, ambient 1, voice_qualification 1 |
| Windows runtime | Cihaz `MAIL` — `online`, agent `0.6.0`, build `19f079c4fda2c3c7`, source `f2fe54a` (üretimden 1 sürüm geride), 85 yetenek |
| Dallar | 13 adet + 7 `selfdev/*` + 18 `worktree-agent-*` — **hiçbiri silinmedi** |
| Worktree'ler | 17 aktif (Astra `2561f84`, phase7/8/9, release `714ff2b` ve `c309065` dahil) — **korundu** |
| Kanıt dosyaları | 31 alıntılanan kanıt dosyasının ve 159 test dosyasının tamamı mevcut |

Bu tur **hiçbir şey silinmedi, hiçbir üretim değişikliği yapılmadı** — yalnızca salt okunur sorgular.

---

## 2. 750 MADDE SINIFLANDIRMA ÖZETİ

`IMPLEMENTATION_STATUS` (toplam 750, makineyle sayıldı):

| SINIF | ADET | PAY |
|---|---|---|
| `MISSING` | **300** | %40,0 |
| `DONE` | **191** | %25,5 |
| `PARTIAL` | **183** | %24,4 |
| `BROKEN` | **47** | %6,3 |
| `BLOCKED_PROVIDER` | **25** | %3,3 |
| `DEFERRED` | **3** | %0,4 |
| `BLOCKED_OWNER` | **1** | %0,1 |
| `NOT_APPLICABLE` | **0** (satır olarak; kapsam dışı konular ayrı tabloda) | — |

`PROOF_STATUS`:

| SINIF | ADET |
|---|---|
| `NOT_YET_PROVEN` | 468 |
| `PROVEN_AUTOMATED` | 168 |
| `PROVEN_REAL` | 82 |
| `PROVIDER_UNAVAILABLE` | 25 |
| `PROVEN_PROXY` | 5 |
| `BLOCKED` | 2 |

`PRIORITY`: **P0 = 148 · P1 = 314 · P2 = 274 · P3 = 14**

**Okuma notu.** `DONE` 191 satır gerçekten çalışan iştir, ama bunların 82'si çalışma zamanında
ölçülmüştür (`PROVEN_REAL`); kalanı otomatik testle kanıtlıdır. `PARTIAL`'ların büyük kısmı
"kod var, erişilebilir yüzey yok" — yani eksik olan mühendislik değil **bağlantı**. Bu, yol
haritasının neden bu kadar çok "bağla" ve bu kadar az "sıfırdan yaz" içerdiğini açıklar.

---

## 3. P0 — RELIABILITY / SECURITY / BROKEN EXISTING FEATURES (148 madde)

Ölçüt: bugün yanlış davranıyor, sır sızdırıyor, veri kaybettirebiliyor, yalan doğrulama
üretiyor veya bozuk bir sürümü durdurmuyor.

| Küme | Madde kimlikleri | Neden P0 |
|---|---|---|
| Sürüm güvenliği ve sürüm gerçeği | 1, 2, 21, 22, 23, 24, 631, 632, 638 | Başarısız göç sürümü durduramıyor |
| CI kapsamı ve yanlışlama | 25–30 | ~1472 web testi + 15 PE testi hiçbir kapıda değil |
| Gerçek/sahte sözleşme eşitliği + 4 canlı kusur | 3, 4, 5, 147, 168, 417–421, 467, 505, 510, 715 | Dört canlı üretim kusurunun kökü |
| Sır hijyeni | 6, 7, 8, 667, 668, 683 | Üretim DB parolası açık metin |
| Yetki ve kimlik | 245–248, 658, 659, 663–666, 675, 677, 678 | Cihaz güveni çağıranın gönderdiği boolean |
| Durum gerçeği ve süpürgeler | 9–13, 67–70, 206, 219, 220 | Sistem kendi durumu hakkında yanlış konuşuyor |
| Sınırlı teslim ve döngü dayanıklılığı | 14–20, 375, 376, 390, 679 | Sahte sağlayıcı "iletildi" diyor; kuyruk kalıcı tıkanıyor |
| Yedek/kurtarma sağlığı ve ölçümü | 614, 646–655 | Yedek arızasını kimse görmüyor; reconcile timer'ı yok |
| Host dışı felaket kurtarma | 630, 642–645 | Host kaybında RPO/RTO sonsuz |
| Yürütme dürüstlüğü | 539, 548, 558, 559, 560 | "4/4 tamam" derken 3 adım başarısız |
| Yanlış yönlendirme güvenliği | 736–739, 741, 749, 750 | "Otomatik güncellemeleri kapat" ekran otomasyonunu kapatıyor |
| Zaten doğru ve korunacak P0 satırları | 12, 185–189, 205, 249, 314, 315, 321, 322, 328, 329, 584, 610–613, 624, 626–641, 656, 657, 661, 662, 669, 670, 672, 673, 676, 681, 682, 684, 458 | Bunlara dokunulmaz |

## 4. P1 — DAILY USE / INTELLIGENCE / OPERATOR / VOICE (314 madde)

| Küme | Madde kimlikleri |
|---|---|
| Sahibe ulaşma | 367–374, 377–389 |
| Bellek döngüsü | 31–50, 55, 56, 61, 62, 71–73 |
| Öz/dünya modeli dürüst yanıtları | 63–66, 74–80 |
| Alarm bütünlüğü + tekrarlayan rutinler | 259, 261, 262, 267, 269, 282–299 |
| Tarayıcısız sabah | 270–276, 279–281 |
| Ses dayanıklılığı + anlatım + telaffuz | 215–218, 221–238, 414 |
| Türkçe hata dili ve dürüst durumlar | 704–711 |
| Web kabuğu, eksik sayfalar, keşfedilebilirlik | 660, 685–703, 712–716, 720, 722–725 |
| Niyet kapsamı | 726–735 |
| Operatör erişimi (girdi, UIA, pencere, süreç) | 82, 84–88, 91–105, 107, 109–111, 116–122 |
| Araştırma kontrolü ve tarayıcı boşlukları | 172, 173, 180, 181, 192, 198–204, 207, 209, 210 |
| Belge arama, OCR, yineleme | 139–142, 148, 150–152, 169, 496 |
| Yerel fabrika yaşam döngüsü | 456, 457, 462–466, 468–473 |
| Artefakt üretim kanıtı | 393, 394, 398, 399 |

## 5. P2 — CREATION / ADVANCED AUTOMATION / EXPANSION (274 madde)

| Küme | Madde kimlikleri |
|---|---|
| Yönetilen dosya mutasyonu | 153–167, 170, 674 |
| SelfDev'in bağlanması + güvenlik incelemesi | 581, 583, 585, 589, 598, 600, 601, 603, 608, 609, 615, 618–623, 680 |
| Genesis ön kapısı | 561–565, 569–580 |
| Anlamsal bellek + bellek arayüzü | 51, 53, 54, 57–60, 149 |
| Genel yürütme planlayıcısı | 536–538, 544, 546, 549–557 |
| Operatör özerklik döngüsü | 106, 112–115, 123–130 |
| App Factory genelleştirme + yaşam döngüsü | 422–452 |
| Artefakt provenans ve yaşam döngüsü | 400, 405–412, 415, 416 |
| Yaratıcı üretim ve teslim | 489–495, 498, 500, 506–509, 511, 512 |
| 3B üretim yolu | 520–527 |
| Mail ve takvim canlandırma | 277, 278, 335–366 |
| Cihaz tarafı ses | 239–244, 250–255 |
| Varlık derinliği ve kamera | 300–303, 307, 308, 310, 312, 313, 320, 326, 327, 330–333, 671 |
| Niyet zekâsı | 740, 742–748 |
| Belge formatı genişletme | 143–146 |
| iOS açık ret beyanı | 479 |

## 5b. P3 — v1.0'ı BLOKLAMAYAN (14 madde)

Android fabrikası (474–478), Unity (530–533), Unreal (534), Photoshop/Illustrator sürücüsü
(502, 504), üretilen uygulamanın otomatik güncellemesi (480), düşük riskli AUTO_SAFE (625).

---

## 6. DEPENDENCY GRAPH

Yürütme **numara sırasına göre değildir.** Ok yönü: **önce → sonra**.

### 6.1 Zorlayıcı kökler

```
[5] gerçek/sahte sözleşme eşitliği
      ├─> [3] file.search kökü ──> [147][168][169][548]
      ├─> [4][417][418][419][420][421] App Factory manifest'leri ──> [440..452]
      ├─> [467] dürüst test sayısı ──> [462..473] native yaşam döngüsü
      ├─> [282] alarm çalma makbuzu ──> [283][284]
      └─> [369] desktop.notify ──> [389] teslim merdiveni

[25..30] CI kapsamı  ──> (SONRAKİ HER BATCH'İN REGRESYON KAPISI)

[1] pipefail + göç kapısı ──> [2] şema doğrulaması ──> [613] sürüm sonrası sağlık
[651][652] kurtarma denetçisi + timer ──> [653] bozulma tespiti ──> [654] otomatik geri alma ──> [614]
[644][645] host dışı depo ──> [630][642][643] ──> [649][650] RPO/RTO ölçümü
[646] yedek sağlığı ──> [647] yedek arıza bildirimi ──> [385]
```

### 6.2 Kişisel zekâ zinciri

```
[31] memory.remember ──> [33][34] çıkarım ──> [41] top-k geri getirme ──> [39] persona enjeksiyonu
                                                                       └─> [40] araç bağlamı
[41] ──> [61][62] hatırlama gerekçesi ve makbuzu
[51] gerçek gömme ──> [53] sağlayıcı seçimi ──> [54] reindex ──> [149] anlamsal belge arama
[71] Experience zamanlayıcı ──> [72] ders ──> [73] belleğe yazma ──> [46][50] süreklilik
[49] nesne odağı ──> [745] referans çözümü
```

### 6.3 Ulaşılabilirlik zinciri

```
[367] kalıcı bildirim tablosu ──> [368] kutu ──> [377][378][379][380]
[369] desktop.notify ──> [370] eylem düğmeleri
[367]+[369] ──> [389] merdiven ──> [381..388] olay bildirimleri
[390] sahte "delivered" diyemez ──> [375] gerçek makbuz   (ikisi de [5]'e bağlı)
[224] gerçek TTS ──> [281] tarayıcısız brifing ──> [270..276][279][280]
```

### 6.4 Operatör zinciri

```
[663] cihaz güveni sunucuda ──> [7?]* yetenek kapısı ──> [91..98] girdi ──> [104] ekran görüntüsü
[99] UIA ağacı ──> [100..103] UIA eylemleri ──> [116] uygulamaya özel sürücü
[110] makbuz ──> [111] postcondition ──> [112] GÖZLE-KARAR-UYGULA-DOĞRULA ──> [113][114][115]
[112] ──> [123..130] karma ve çok adımlı işler
[535] dayanıklı grafik ──> [128] operatör işinin kalıcılığı
[95][100] ──> [500] Paint gerçek sürüş ──> [502][504]
```
*(cihaz komut rotasının yetenek/politika kapısı; matriste 663/675/678 ile birlikte B05'te)*

### 6.5 SelfDev ve Genesis zinciri

```
[582] fırsat girişi ──> [583] fırsat→kusur köprüsü ──> [581] kusur girişi ──> [615] zamanlayıcı
[680] üretilen kod güvenlik incelemesi ──> [598] SelfDev güvenlik adımı ──> [609] sahip onayı
[602] CI okuma ──> [603] CI kırmızısı → kod düzeltme döngüsü
[585] terfi sınıfı tüketimi ──> [608] gölge ──> [609] onay ──> [610] sürüm   (624 ASLA gevşetilmez)
[563] katalog kaydı ──> [562] dolu katalog ──> [561][574] talep yüzeyi ──> [573] üretimde kullanım
[579] güvenlik kapısı ──> [577] model destekli üretim   (ön kapı yokken açılmaz)
```

### 6.6 Yaratım zinciri

```
[425] model destekli kod üretimi ──> [422][423][424] planlama ──> [426..439] üretim ve doğrulama
                                  └─> [440..452] yaşam döngüsü ──> [452] serbest ama sınırlı istek
[160] geri alma günlüğü ──> [153..159][161..167][170]   (674 izni ile birlikte)
[492] görsel üretim sağlayıcısı ──> [489][490][491][493][494][495]
[413] artefakt teslim yolu ──> [509] yaratıcı çıktının diske teslimi
[521] üretim sahne yolu ──> [522..527]
```

### 6.7 Anlama zinciri

```
[741] deterministik kalkan (KORUNUR) ──> [736][737][738] misroute düzeltmesi
[739] negatif külliyat ──> [749] telemetri ──> [750] otomatik misroute tespiti
[742] araç kaydından yönlendirme ──> [701][734] "neler yapabilirsin"
[740] model yönlendirici ──> [743] güven ──> [744] belirsizlik sorusu   (kritik fiiller [741]'de kalır)
[704] Türkçe hata sözlüğü ──> [705..711] dürüst durumlar
[685] gezinme ──> [686][689..700][712..716] tüm web sayfaları
```

### 6.8 Mutlak sıra kuralı

> **B01, B02 ve B03 tamamlanmadan hiçbir P1/P2 batch'i başlayamaz.**
> Gerekçe ölçüm: dört canlı üretim kusurunun tamamı, iki test paketi de yeşilken bulut ve
> cihazın farklı şekil konuşmasından doğdu (madde 5). Kök düzeltilmeden aynı sınıf tekrar
> üretilir, ve CI kapsamı (25–30) olmadan düzeltmelerin kalıcılığı garanti edilemez.

---

## 7. IMPLEMENTATION BATCHES

52 batch, her biri 4–18 gereksinim. **Bir batch bitmeden sonraki başlamaz.**
`DONE` satırlar (181 madde) hiçbir batch'e alınmamıştır — korunur, yeniden yazılmaz.

### FAZ A — P0 ZORUNLU TEMEL (B01–B10)

```
BATCH_ID            : B01                                       [KAPANDI 2026-09-12]
NAME                : Sürüm güvenliği ve sürüm gerçeği
REQUIREMENT_IDS     : 1, 2, 21, 22, 23, 24, 631, 632, 638  — dokuzunun dokuzu DONE
SONUC               : 1 PROVEN_AUTOMATED (altı vaka önce KIRMIZI kanıtlandı) · 2 PROVEN_REAL
                      (gerçek PostgreSQL, docs/evidence/b01-schema-gate-2026-09-12.json) ·
                      21/22 PROVEN_REAL (build_id 516452ef2d144269) · 23/24/631/632/638
                      PROVEN_AUTOMATED. Yol üstünde iki kusur kapandı: matris satır 20'nin
                      yanlış sınıflandırması ve `test_briefing_announcer` saat bombası
                      (yeşil CI'dan 9 dakika sonra altı testi birden düşürecekti).
CI                 : run 34703755179 yeşil (7/7 job) · commit 15df7f3
ACIK               : Üretim sürümü yapılmadı — "bozuk göç gerçek host'ta bir promosyonu
                      durdurdu" kanıtı owner onaylı bir dağıtım ister (bkz. §11).
GOAL                : Başarısız bir göç veya kimliği belirsiz bir build üretime promote edilemesin.
DEPENDENCIES        : none
AFFECTED_SUBSYSTEMS : Release, Health, Build State
EXPECTED_FILES      : scripts/cloud/release-cloud-core-bluegreen.sh, release-cloud-core.ps1,
                      services/api/app/system/health.py, state/BUILD_STATE.json, state/RELEASE*.json
RISK                : medium — sürüm yolu; her adım geri alınabilir
OWNER_ACTION        : no
TEST_PLAN           : kasten bozuk bir göçle sürüm reddi (kırmızı önce kanıtlanır); alembic
                      revizyon uyuşmazlığında sağlık kapısının yeşil dememesi; BUILD_STATE
                      üretecinin çelişkili girdide hata vermesi
REAL_PROOF_REQUIRED : PROVEN_AUTOMATED (bozuk göç testi) + PROVEN_REAL (staging renginde bir
                      sürüm koşusu; sağlıkta doğru build kimliği)
ROLLBACK_PLAN       : betik değişiklikleri commit bazında geri alınır; üretim dokunulmaz
                      (yalnız staging rengi); LKG d86b3d9 elde
```

```
BATCH_ID            : B02                                       [KAPANDI 2026-09-12]
NAME                : CI kapsamı ve yanlışlama kapısı
REQUIREMENT_IDS     : 25, 26, 27, 28, 29, 30  — altısının altısı DONE
SONUC               : Web: 1587 test + oxlint + tsc CI'a girdi (önce yalnız `build` vardı).
                      PowerShell: 24/26 → 26/26; eksik ikiden biri cloud-release-bluegreen
                      (59 vaka, üretimin kullandığı sürüm yolu). 28 zaten karşılanmıştı —
                      ölçüm denetimi düzeltti (CI run 34703755179: 869 test). 30 için
                      mutasyon her koşuda GERÇEKTEN yürütülüyor. Yol üstünde: test_injection'ın
                      "sözleşme yoksa SKIP" kaçamağı kaldırıldı.
CI                 : run 34705909155 yeşil (7/7) · commit 8ca23ac · kanıt
                      docs/evidence/b02-ci-coverage-2026-09-12.json
KALICI KORUMA       : test_ci_covers_every_suite.py — elle tutulan listeler artık denetleniyor;
                      var olan bir paketi adlandırmayan workflow testte düşer.
GOAL                : Kritik hiçbir test kapının dışında kalmasın; kritik sözleşme testleri
                      mutasyonla düşsün.
DEPENDENCIES        : none (B01 ile paralel yürütülebilir, ama B03'ten ÖNCE bitmeli)
AFFECTED_SUBSYSTEMS : CI, Web, Native, Scripts
EXPECTED_FILES      : .github/workflows/*, apps/web/package.json, scripts/tests/
RISK                : low
OWNER_ACTION        : no
TEST_PLAN           : web test + linter job'ları; Windows runner'da PE reader + PowerShell
                      suite'leri; her yeni job için kasten kırık bir değişiklikle kırmızı kanıtı
REAL_PROOF_REQUIRED : PROVEN_AUTOMATED — CI koşusunda yeni job'ların yeşil olduğu VE kasten
                      bozulmuş bir değişiklikte kırmızı olduğu
ROLLBACK_PLAN       : workflow dosyası geri alınır; kod etkilenmez
```

```
BATCH_ID            : B03                                       [KAPANDI 2026-09-12]
NAME                : Gerçek/sahte sözleşme eşitliği ve dört canlı kusur
REQUIREMENT_IDS     : 3, 4, 5, 147, 168, 417-421, 467, 505, 510, 715 — on dördünün on dördü DONE
SONUC               : Dört canlı üretim kusurunun dördü de kapandı. İki yeni paylaşılan
                      sözleşme (file-search-roots, app-manifest.example), her ikisi de iki
                      yarıdan okunuyor. Cihaz da iki yerde eksikti: bir web projesi
                      "hiçbir şeye bağlanmıyorum" diyemiyordu ve kova adı çözemiyordu.
                      B02'den devreden device-protocol.schema.json'ın C# yarısı da kapandı.
CI                 : run 34709341943 yeşil (7/7) · commit 9ddf243 · cihaz testleri
                      869 → 887 · kanıt docs/evidence/b03-contract-parity-2026-09-12.json
YOL USTUNDE         : Türkçe İ katlama kusuru (büyük İ ile yazılan klasör reddediliyordu);
                      üç sahtenin daha `counts_parsed` düşürmesi — artık mekanik bekçisi var.
GOAL                : Bulut ile cihazın konuştuğu her şekil tek paylaşılan artefakttan okunsun;
                      klasör araması, üç uygulama şablonu, yaratıcı paneli ve dürüst test sayısı
                      üretimde çalışsın.
DEPENDENCIES        : B02 (regresyon kapısı)
AFFECTED_SUBSYSTEMS : Protocol, Files, App Factory, Native Factory, Creative, Web
EXPECTED_FILES      : packages/protocol/*, services/api/app/files/, app/appfactory/,
                      app/nativefactory/device_build.py, app/creative/routes.py,
                      apps/web/app/lib/cockpit/creative.ts, devices/windows-agent/**/tests/
RISK                : low
OWNER_ACTION        : no
TEST_PLAN           : NativeManifestContractTests deseni her aileye yayılır — paylaşılan fikstür
                      YOKSA C# testi başarısız olur; cihazın gerçek ayrıştırıcısı bulutun
                      gönderdiği örneği kabul eder; bare-string/`{port}` reddi pinlenir
B02'DEN DEVREDEN    : `packages/schemas/device-protocol.schema.json` kendini "authoritative"
                      ilan ediyor ama YALNIZCA Python yarısı ona tutuluyor (ve yalnız `hello`
                      çerçevesi için). C# tarafı şemayı sadece yorumda anıyor; çerçeve tipleri
                      hiçbir yerde şemaya karşı doğrulanmıyor. B02'nin sözleşme kaydına
                      `unheld` notuyla girdi — kapatması B03'ün işi.
REAL_PROOF_REQUIRED : PROVEN_REAL — üretimden klasör araması sonuç döndürür; üç şablon cihazda
                      kabul edilir; cockpit yaratıcı paneli veri gösterir; native verdict
                      `passed:null` ile "verified" diyemez
ROLLBACK_PLAN       : her aile ayrı commit; cihaz tarafı değişiklik agent sürümüyle geri alınır
```

```
BATCH_ID            : B04                                       [KAPANDI 2026-09-12]
NAME                : Sır redaksiyon hattı
REQUIREMENT_IDS     : 6, 7, 8, 668, 683
GOAL                : Mevcut 13 desen her çıkış yüzeyinde uygulansın; hiçbir sır açık çıkmasın.
DEPENDENCIES        : B03
AFFECTED_SUBSYSTEMS : World Model, Observability, Health, Security
EXPECTED_FILES      : services/api/app/worldmodel/state.py, app/logging.py, app/health.py,
                      app/main.py (sağlık rotası), app/security/redaction.py
                      (gerçek dosyalar: `app/observability/` ve `app/system/` yok — log hattı
                      `app/logging.py`, sağlık `app/health.py`)
RISK                : low
OWNER_ACTION        : no
KAPANIŞ             : commit 25eaede · CI 34717193365 yeşil (7/7) · yerel kapı 8999 geçti
                      kanıt docs/evidence/b04-secret-redaction-2026-09-12.json
YOL ÜSTÜNDE         : `redact_value` iç içe anahtar-ADI kuralını uygulamıyordu; stdlib
                      `logging` (uvicorn/SQLAlchemy) işlemci zincirinin yanından geçiyordu
TEST_PLAN           : bilinen sır dizesi her yanıt/log/sağlık yüzeyinde aranır ve bulunmaz;
                      redaksiyon kapatılınca test kırmızı olur (mutasyon)
REAL_PROOF_REQUIRED : PROVEN_REAL — üretimde /v1/world/facts temiz; PROVEN_AUTOMATED — log hattı
ROLLBACK_PLAN       : tek modül; commit geri alınır
```

```
BATCH_ID            : B05                                       [KAPANDI 2026-09-13]
NAME                : Yetki kapısı ve kimlik sertleştirme
REQUIREMENT_IDS     : 245, 246, 247, 248, 658, 659, 663, 664, 665, 666, 675, 678
GOAL                : Cihaz komutu sunucuda yetenek+politika kapısından geçsin; cihaz güveni ve
                      konuşmacı verdict'i gerçekten tüketilsin; oturumun mutlak yaşı olsun.
                      Ses ASLA tek başına kök kimlik doğrulaması olmaz.
DEPENDENCIES        : B04
AFFECTED_SUBSYSTEMS : Identity, Devices, Voice Identity, Security
EXPECTED_FILES      : services/api/app/identity/, app/devices/, app/voice/identity/, app/security/
RISK                : medium — yetki kapısı mevcut çağrıları kırabilir
OWNER_ACTION        : SORULDU ve YANITLANDI (2026-09-13) — 678 kalıcı silme politikası:
                      "yumuşak silme varsayılan (sesle, geri alınabilir) + KALICI yok etme
                      ikinci kanal onayı ister". app/security/deletion.py bunu uyguluyor.
KAPANIŞ             : commit 0394a83 · CI 34720710232 yeşil (7/7) · yerel kapı 9066 geçti
                      kanıt docs/evidence/b05-authority-gate-2026-09-13.json
TAM KAPANAN         : 246, 663, 658, 659, 675, 678 (altısı da PROVEN_AUTOMATED)
KISMİ KALAN         : 245, 247, 248, 664, 665, 666 — karar yolu var, testli ve tek kapıya
                      bağlı; ama realtime yolda VERDICT ÜRETEN AKIŞ YOK, o yüzden enforce
                      etmek her hassas sesli işlemi reddederdi. "Sınıf var" tamamlandı
                      değildir; eksik parça adıyla yazıldı.
GÖLGE MODDA         : cihaz komut kapısı ve ses step-up politikası. İkisi de sayıyor,
                      engellemiyor. Açmak sahip kararı: cihaz kapısı için üretimde bir
                      günlük sayım (REAL_PROOF), step-up için önce verdict akışı.
TEST_PLAN           : kapsam dışı yetenek çağrısı 403; çağıranın gönderdiği `device_trusted`
                      yok sayılır; düşük güvenli konuşmacı hassas komutta reddedilir; yaşlı
                      jeton yenilenemez
REAL_PROOF_REQUIRED : PROVEN_AUTOMATED (kapı testleri) + PROVEN_REAL (üretimde gölge modda bir
                      gün sayım: kaç çağrı kapıya takılırdı)
ROLLBACK_PLAN       : kapı önce gölge modda (sayar, engellemez); engelleme ayrı commit
```

```
BATCH_ID            : B06                                       [KAPANDI 2026-09-12]
NAME                : Durum gerçeği ve süpürgeler
REQUIREMENT_IDS     : 9, 10, 11, 13, 67, 68, 69, 70, 206, 219, 220
GOAL                : Sistem kendi durumu hakkında doğru konuşsun: zombie oturum yok, yetim
                      araştırma yok, tasks.running gerçek, defter çift yazmıyor.
DEPENDENCIES        : B05
AFFECTED_SUBSYSTEMS : Voice Realtime, Research, World Model, Ledger, Retention
EXPECTED_FILES      : services/api/app/voice/realtime/, app/research/, app/worldmodel/,
                      app/ledger/, app/retention/
RISK                : low
OWNER_ACTION        : no
KAPANIŞ             : commit 41eb2c3 · CI 34712304047 yeşil (7/7) · yerel kapı 8979 geçti
                      kanıt docs/evidence/b06-state-truth-2026-09-12.json
YOL ÜSTÜNDE         : `_collect_tasks` gerçek saati okuyordu (bir karar iki saat, aynı gün
                      altıncı örnek); süpürgenin denetim satırı `idle_since`'ı damgaladıktan
                      sonra okuyordu
SIRA NOTU           : B04 ve B05'ten ÖNCE yapıldı — yanlış numarayla başlanmış bir batch'ti;
                      teknik bağımlılığı yoktu (süpürgeler yetki kapısına bağlı değil).
                      Sıradaki: B04, sonra B05.
TEST_PLAN           : deterministik saatle TTL süpürgesi; READY araştırmanın running sayılmadığı;
                      aynı sesli oturumun tek defter satırı ürettiği (doğal anahtar)
REAL_PROOF_REQUIRED : PROVEN_REAL — üretimde active oturum sayısı gerçeğe iner, 2026-09-09
                      yetimi kapanır, /v1/worldmodel doğru sayar
ROLLBACK_PLAN       : süpürgeler bayrakla kapatılabilir; hiçbir satır silinmez, terminal duruma
                      taşınır (geri alınabilir)
```

```
BATCH_ID            : B07                              [KAPANDI 2026-09-13]
NAME                : Sınırlı teslim, döngü izolasyonu, saklama
REQUIREMENT_IDS     : 14, 15, 16, 17, 18, 19, 20, 375, 376, 390, 679
GOAL                : Hiçbir kuyruk sonsuz denemesin, tek bozuk satır kuyruğu kilitlemesin, bir
                      alt tikin patlaması alarmı sessizce düşürmesin, sahte sağlayıcı "iletildi"
                      diyemesin, denetim tablosu sınırsız büyümesin.
DEPENDENCIES        : B06
AFFECTED_SUBSYSTEMS : Notifications, Research, Routines, Health, Retention, Security Ledger
EXPECTED_FILES      : services/api/app/notifications/, app/research/, app/routines/,
                      app/retention/, app/system/health.py
RISK                : medium — alarm yolu; deterministik saat testi zorunlu
OWNER_ACTION        : no
KAPANIŞ             : commit 690abd5 · CI 34722166605 yeşil (7/7) · yerel kapı 9096 geçti
                      kanıt: kapanış kaydı FEATURE_MATRIX'te
YOL ÜSTÜNDE         : brifing kuyruğundaki tek zehirli satır kalıcı kilit yapıyordu —
                      sahibin kayıp bildirimleri oradaydı
TEST_PLAN           : zehirli mesajın karantinaya gittiği ve kuyruğun ilerlediği; bir alt tik
                      patlarken diğer alt tiklerin koştuğu; `fake` sağlayıcının "delivered"
                      damgası üretemediği (tip düzeyinde); saklama süpürgesinin sayıları düşürdüğü
REAL_PROOF_REQUIRED : PROVEN_AUTOMATED + PROVEN_REAL (sağlıkta sekiz döngünün canlılığı görünür)
ROLLBACK_PLAN       : saklama süpürgesi önce kuru koşu (sayar, silmez)
```

```
BATCH_ID            : B08                              [KISMEN KAPANDI 2026-09-13 — host kurulumu sahipte]
NAME                : Yedek/kurtarma sağlığı, ölçüm ve denetçi
REQUIREMENT_IDS     : 614, 646, 647, 648, 649, 650, 651, 652, 653, 654, 655
GOAL                : Yedek arızası görünsün ve haber versin; kurtarma denetçisi timer'ıyla
                      kurulsun; RPO/RTO ölçülüp yayınlansın.
DEPENDENCIES        : B01, B07
AFFECTED_SUBSYSTEMS : Backup, Recovery Supervisor, Health, Notifications, Operations
EXPECTED_FILES      : scripts/cloud/install-recovery-supervisor.sh, services/recovery-supervisor/,
                      services/api/app/system/health.py, docs/OPERATIONS.md
RISK                : medium — otomatik geri alma üretimi etkiler
OWNER_ACTION        : YES — kurtarma denetçisinin üretime kurulumu (Astra `2561f84`
                      READY_FOR_OWNER_APPROVAL)
KAPANIŞ             : commit ed40936 · CI 34724500752 yeşil (7/7) · yerel kapı 9150
                      646-650 DONE (PROVEN_AUTOMATED); 614, 651-655 PARTIAL
                      kanıt docs/evidence/b08-backup-recovery-2026-09-13.json
SAHİP KAPISI        : `install-recovery-supervisor.sh <sha>` üretim host'unda root ile.
                      Sahip kararı (2026-09-13): şimdilik PARTIAL kalsın, B09-B10'a devam.
YOL ÜSTÜNDE         : Astra birleşmesi iki farklı arızayı `exit 82`'ye koydu (kilit vs göç);
                      göç 79'a taşındı ve mekanik bekçi eklendi
TEST_PLAN           : Astra dalındaki rollback-lock testleri (lock_held, lock_wait_s); yedek
                      yaşı eşiği aşınca sağlığın degrade dediği; OnFailure zincirinin bildirim
                      ürettiği
REAL_PROOF_REQUIRED : PROVEN_REAL — host üzerinde timer armed, reconcile bir kez koştu, sağlıkta
                      yedek yaşı görünüyor, kasten bozulmuş bir renk otomatik geri alındı
ROLLBACK_PLAN       : denetçi systemd biriminden durdurulur; otomatik geri alma bayrağı kapatılır
```

```
BATCH_ID            : B09                              [KISMEN KAPANDI 2026-09-13 — kova sahipte]
NAME                : Host dışı felaket kurtarma
REQUIREMENT_IDS     : 630, 642, 643, 644, 645
GOAL                : Host tamamen kaybedilse bile geri dönüş mümkün olsun.
DEPENDENCIES        : B08
AFFECTED_SUBSYSTEMS : Backup, Recovery, Operations
EXPECTED_FILES      : scripts/cloud/backup-cloud-core.sh, docs/OPERATIONS.md
RISK                : low — salt ekleme; mevcut yedek yolu değişmez
OWNER_ACTION        : YES — S3 uyumlu ikinci kova + erişim anahtarı (DPAPI ile saklanacak;
                      anahtar asla log'a/commit'e girmez)
KAPANIŞ             : commit ed40936 · CI 34724500752 yeşil (7/7)
                      642, 643, 644 DONE; 645 BLOCKED_OWNER
SAHİP KAPISI        : S3 uyumlu ikinci kova + erişim anahtarı. Anahtar DPAPI ile saklanır,
                      asla commit'e/log'a girmez.
YOL ÜSTÜNDE         : off-host kopya YAZILABİLİR ama OKUNAMAZDI — `restore-cloud-core.sh`
                      yalnızca korumaya çalıştığı diskteki depoyu açıyordu
TEST_PLAN           : ikinci hedefe yazım; boş bir kaptan tam geri yükleme tatbikatı;
                      yapılandırma ve sürüm meta verisinin de geri geldiğinin doğrulanması
REAL_PROOF_REQUIRED : PROVEN_REAL — ikinci hedefte snapshot + geçen geri yükleme tatbikatı
ROLLBACK_PLAN       : ikinci hedef yapılandırmadan çıkarılır; birincil yedek etkilenmez
```

```
BATCH_ID            : B10                              [KAPANDI 2026-09-13]
NAME                : Yürütme dürüstlüğü
REQUIREMENT_IDS     : 539, 548, 558, 559, 560
GOAL                : Hiçbir koşu başarısız adımları "tamam" diye raporlamasın; telafi gerçekten
                      telafi etsin.
DEPENDENCIES        : B03 (548 file.search'e bağlı), B06
AFFECTED_SUBSYSTEMS : Executive
EXPECTED_FILES      : services/api/app/executive/
RISK                : low
OWNER_ACTION        : no
KAPANIŞ             : commit ed40936 · CI 34724500752 yeşil (7/7) · beşi de DONE
YOL ÜSTÜNDE         : 560 zaten uygulanmıştı ve 31 testi geçiyordu — matris MISSING diyordu.
                      Ölçüm dokümantasyonu yendi.
TEST_PLAN           : 3 adımı başarısız bir koşunun "4/4" diyemediği; boş telafi dalının
                      "telafi edildi" diyemediği; mutabakatın yarım kalan koşuyu kapattığı
REAL_PROOF_REQUIRED : PROVEN_REAL — üretimdeki 5 koşunun durumu yeniden hesaplandığında gerçeğe
                      uyar
ROLLBACK_PLAN       : tek modül; commit geri alınır
```

### FAZ B — P1 GÜNLÜK KULLANIM (B11–B33)

```
BATCH_ID            : B11                              [KISMEN KAPANDI 2026-09-13 — 369/370 cihaz kurulumu, 372 VAPID sahipte]
NAME                : Sahibe ulaşma omurgası
REQUIREMENT_IDS     : 367, 368, 369, 370, 372, 377, 378, 379, 380, 389
GOAL                : Tarayıcı kapalıyken sistem sahibine güvenilir biçimde ulaşabilsin.
DEPENDENCIES        : B03 (369 sözleşmeye bağlı), B07 (375/390)
AFFECTED_SUBSYSTEMS : Notifications, Device Companion, Protocol, Web
EXPECTED_FILES      : packages/protocol/, devices/windows-agent (SessionCompanion),
                      services/api/app/notifications/, apps/web/
RISK                : medium — yeni cihaz yeteneği, agent kurulumu gerekir
OWNER_ACTION        : VAPID anahtarı (372) — checkpoint, bloklamadı
KAPANIŞ             : commit 86769d7 (+db84bbd, 8d2d56e) · CI 34750573146 · 367/368/377/378/379/380/389 DONE,
                      369/370 PARTIAL (cihazda kurulum), 372 BLOCKED_OWNER (VAPID)
                      kanıt docs/evidence/b11-owner-reachability-2026-09-13.json
YOL ÜSTÜNDE         : (1) `ladder.sweep` yazılmıştı ve HİÇBİR ŞEY çağırmıyordu — bu deponun
                      altıncı "yazıldı ama bağlanmadı" kusuru; RetentionSweeper'a bağlandı.
                      (2) ToastRung, `DeviceActionPort.run`'ın kabul etmediği `device_id`/
                      `trace_id` ile çağırıyordu: her toast merdivenin catch-all'ı içinde
                      TypeError atıp "cihaz göstermedi" diye kaydedilecekti. Elle yazılmış
                      sahteler bunu göremez; regresyon gerçek portun `create_autospec`'ine
                      karşı koşuyor. (3) `desktop.notify` iki kez yazılmıştı — ayna testi
                      kendi işini yaptı, tek yazım `toast.CAPABILITY`'de toplandı.
                      (4) d7f726a'da eklenen "diğer servisler ruff" adımı HİÇ KOŞMAMIŞ:
                      yollardaki ters bölü CR ve BACKSPACE'e dönüşmüş, adım depo kökünde
                      ruff koşup hiç girmediği iki ağaç için "All checks passed!" demiş.
                      Düz bölü + `Test-Path` kapısı. Sessiz geçişi durdurmak için yazılan
                      adımın kendisi sessiz geçiş olmuştu.
TEST_PLAN           : toast yeteneğinin paylaşılan sözleşmeden okunduğu; kalıcı tablonun
                      yeniden başlatmayı atlattığı; merdivenin sırayla düştüğü
REAL_PROOF_REQUIRED : PROVEN_REAL — tarayıcı kapalı, ekran kilitli: toast göründü ve kutuda kaldı
ROLLBACK_PLAN       : agent sürümü geri alınır; bulut tarafı bayrakla eski yola döner
```

```
BATCH_ID            : B12                              [KISMEN KAPANDI 2026-09-13 — 373/374 sağlayıcı hesabı sahipte]
NAME                : Bildirim olayları
REQUIREMENT_IDS     : 373, 374, 381, 382, 383, 384, 385, 386, 387, 388
GOAL                : Sahibin bilmesi gereken her olay (iş bitti/başarısız, onay gerekiyor,
                      geri alma oldu, yedek başarısız, alarm başarısız, aday hazır) ulaşsın.
DEPENDENCIES        : B11
AFFECTED_SUBSYSTEMS : Notifications, Release, Backup, Alarms, SelfDev
EXPECTED_FILES      : services/api/app/notifications/, app/release/, app/alarms/, app/selfdev/
RISK                : low
OWNER_ACTION        : FCM/APNs için sağlayıcı hesabı (373, 374) — checkpoint, bloklamadı
KAPANIŞ             : commit 86769d7 (+db84bbd, 8d2d56e) · CI 34750573146 · 381–388 DONE,
                      373/374 BLOCKED_PROVIDER (merdivenin `push` basamağı ayrılmış ve boş)
                      kanıt docs/evidence/b12-notification-events-2026-09-13.json
YOL ÜSTÜNDE         : (1) Üç en-iyi-çaba kancasının üçü de yutulan hatada session'ı
                      zehirliyordu — B07'nin rutin saatinde öğrendiği tuzağın bir alt sistem
                      ötesi; üçü de artık rollback ediyor ve bir test üç kaynağı da okuyor.
                      (2) Erişilebilirlik bekçisinin ilk hâli regex'le yalan söylüyordu:
                      `backup_failed` meşru olarak `sweep_backup_failures`'tan çağrılıyor.
                      `ast` ile yeniden yazıldı.
TEST_PLAN           : her olay tipinin merdivene girdiği ve dürüst makbuz ürettiği
REAL_PROOF_REQUIRED : PROVEN_REAL — üretimde bir geri alma ve bir yedek arızası simüle edilip
                      sahibe ulaştığı gözlendi
ROLLBACK_PLAN       : olay yayıcıları bayrakla kapatılır
```

```
BATCH_ID            : B13                              [KISMEN KAPANDI 2026-09-13 — 259'un yerel tetikleyicisi B47'de]
NAME                : Alarm bütünlüğü
REQUIREMENT_IDS     : 259, 267, 269, 282, 283, 284, 285, 286
GOAL                : Cihaz çaldığını buluta bildirsin, çift çalma imkânsız olsun, tek gecikme
                      eşiği olsun, ton yedeği testli olsun.
DEPENDENCIES        : B11
AFFECTED_SUBSYSTEMS : Alarms, Device, Protocol
EXPECTED_FILES      : packages/protocol/, services/api/app/alarms/, devices/windows-agent
RISK                : medium — alarm yolu
OWNER_ACTION        : no
KAPANIŞ             : commit c28d70c · CI 34751858233 yeşil (7/7) · 267/269/282/283/284/285/286 DONE,
                      259 PARTIAL (sınır kondu; yerel tetikleyici B47)
                      kanıt docs/evidence/b13-alarm-integrity-2026-09-13.json
YOL ÜSTÜNDE         : (1) 284 tek sayıya indi: `packages/protocol/alarm-timing.json`, iki yarı
                      da okuyor. Cihazın 5 dakikası kazandı — 2026-09-10'da 39 dakika geç
                      çalan alarmla ÖLÇÜLMÜŞ olan oydu; bulut o olaydan hiç öğrenmemişti.
                      (2) 283 için heartbeat beklemek kusurun kendisiydi; disarm SENKRON
                      cevap veriyor. (3) 269 companion içinde çözüldü: bulut kendi üretmediği
                      sesi kısamaz. (4) 267'de vızıltı artık karşılama diye sunulmuyor.
                      (5) 285 için yeni tablo YOK — defter zaten tutuyordu.
TEST_PLAN           : deterministik saatle: cihaz çaldıktan sonra bulutun çalmadığı; tek eşiğin
                      iki yakada da aynı olduğu; anahtarsız ton yedeğinin gerçekten ses ürettiği
REAL_PROOF_REQUIRED : PROVEN_REAL — gerçek bir alarm koşusunda tek çalma
ROLLBACK_PLAN       : makbuz alanı isteğe bağlı; eski davranış bayrakla
```

```
BATCH_ID            : B14                              [KAPANDI 2026-09-13]
NAME                : Tekrarlayan ve tetiklenen rutinler
REQUIREMENT_IDS     : 261, 262, 287, 288, 289, 290, 291, 292, 293, 294, 295, 296, 297, 298, 299
GOAL                : Yazılmış ama görünmez olan rutin altyapısı sesle ve ekrandan erişilebilir
                      olsun; tekrar, varlık ve koşul tetikleyicileri gerçekten kullanılsın.
DEPENDENCIES        : B13
AFFECTED_SUBSYSTEMS : Routines, Voice Tools, Intent, Web
EXPECTED_FILES      : services/api/app/routines/, app/voice/tools/, app/voice/intent/, apps/web/
RISK                : low
OWNER_ACTION        : no
KAPANIŞ             : commit ba24ec7 · CI 34755470859 yeşil (7/7) · 15/15 DONE
                      kanıt docs/evidence/b14-routines-2026-09-13.json
ÖLÇÜMLE DÜZELTİLDİ  : 261/262/293 "yok" değildi — kod M18.3'ten beri vardı, matris ÜRETİM
                      SATIRLARINI anlatıyordu (B10'daki 560 ile aynı şekil).
YOL ÜSTÜNDE         : (1) Ama ölçüm gerçek bir kusur buldu: tekrarlayan alarm bir sabahı
                      kaçırınca ÖLÜYORDU. `scheduled_for` "sonraki oluşum" demek ve bunu
                      sadece `_release` (yani çaldıktan SONRA) güncelliyordu; kaçan bir
                      sabah satırı geçmişte bırakıyor, schedule rutini her sabah tetikleniyor
                      ve `fire_alarm` hepsini "expired" diye reddediyordu. Sessiz: tek
                      belirti sahibin bir daha uyandırılmaması. Kırmızı kanıtlandı (10/7).
                      (2) `test_voice_eye_tools` dosya SIRASINA bağlı yeşildi — aynı tikteki
                      iki makbuba sıra dayatıyordu. Küme karşılaştırmasına çevrildi.
                      (3) Serbest metin taşıyan rutin sesle kurulamıyor: röle `text`
                      anahtarını reddediyor (gizlilik kuralı, kusur değil). Belgelendi,
                      testle sabitlendi; kalan iş "yük değil SEÇİCİ taşıyan brifing eylemi".
TEST_PLAN           : "her sabah 08:00", "evden çıkınca", "bilgisayar boşta kalınca"
                      cümlelerinin doğru tetikleyiciye çözüldüğü; rutin iptalinin çalıştığı
REAL_PROOF_REQUIRED : PROVEN_REAL — üretimde `at` olmayan en az bir tekrarlayan ve bir varlık
                      tetikli rutin koştu
ROLLBACK_PLAN       : yeni tetikleyiciler bayrakla kapatılır; mevcut 7 rutin etkilenmez
```

```
BATCH_ID            : B15                              [KAPANDI 2026-09-13 — 281'in sesli kanıtı TTS anahtarında]
NAME                : Tarayıcısız sabah deneyimi
REQUIREMENT_IDS     : 270, 271, 272, 273, 274, 275, 276, 279, 280, 281
GOAL                : Alarm çaldığında karşılama gerçek brifingi okusun — hava, gecelik iş,
                      araştırma, haber — canlı ses oturumu olmadan.
DEPENDENCIES        : B14, B21 (gerçek TTS)
AFFECTED_SUBSYSTEMS : Briefing, News, Device Companion, Narration
EXPECTED_FILES      : services/api/app/briefing/, app/news/, devices/windows-agent
RISK                : medium
OWNER_ACTION        : TTS sağlayıcı kredisi — 281'in sesli kanıtı için; kod bloklanmadı
KAPANIŞ             : commit af0d4aa · CI 34758642613 yeşil (7/7) · 10/10 DONE
                      (281 PROOF=BLOCKED: anahtarsız sesli kanıt yok, roadmap'in kendi
                      OWNER_ACTION satırının öngördüğü kapanış)
                      kanıt docs/evidence/b15-browser-free-morning-2026-09-13.json
TEST_PLAN'DAN SAPMA : "tek WAV'ın tüm bölümleri içerdiği" maddesi ÖLÇÜMLE reddedildi.
                      `desktop.play_audio` 20 sn'de KESİYOR (reddetmiyor) ve dolu bir
                      brifing ~39 sn. Tek WAV olsaydı sahip karşılamayı, tarihi, havayı ve
                      sistem durumunun yarısını duyar, gerisinin var olduğunu hiç
                      öğrenmezdi — hiçbir yerde bir hata da olmazdı. Cümle sınırında
                      parçalanıyor; sınır C# kaynağından okunuyor, ezberden yazılmıyor.
                      2 MiB'lik depo tavanı zaten ~43 sn'de ısırıyor, yani parçalama
                      20 sn sınırına bir çare değil, tek geçerli şekil.
YOL ÜSTÜNDE         : (1) 279 "yanlış olumsuzlama" değil KOŞULSUZ olumsuzlamaydı: hiçbir şey
                      sorulmuyordu ve `test_build_is_honest_about_the_absent_news_resolver`
                      yalanı yerinde tutuyordu. "Dürüst" adlı bir test yanlışı dayatıyordu.
                      (2) Brifing hiç normalize edilmemişti — kimse sesli okumadığı için.
                      (3) 271'de `clock.now` ham ISO damgası dönüyordu; niyet de yoktu.
                      (3b) Bu batch'in KENDİ yazdığı makbuz `detail_json`'da kalıyordu:
                      `alarm_dict` taşımıyordu, yani sahip karşılamanın neden sessiz
                      kaldığını okuyabiliyor (B13/267) ama brifingin dördün ikisinde
                      kesildiğini okuyamıyordu. Üç testle kapatıldı.
                      (4) Batch dışı, kapıyı bu batch'te tuttuğu için: cihaz süitindeki
                      `A_worker_that_stops_answering_pings_is_killed_and_replaced` yüklü
                      makinede yazı-tura oynuyordu — `--no-pong` yedek işçiye de geçtiği
                      için istek, işçinin 300 ms'lik ömrüne sığmak zorundaydı. Yerine
                      `--no-pong-once`: ilk işçi cevapsız, YEDEĞİ sağlıklı — üretimin
                      şekli bu. Tur döngüsü ve 20 sn'lik bütçe silindi; "tam iki başlangıç,
                      tam bir öldürme" artık taban değil kesin sayı. Ayrıca kapı `-v q` ile
                      koştuğu için hata mesajını YUTUYORDU: tek bir cihaz hatası, hangi
                      iddia olduğunu öğrenmek için 12 dakikalık bir tur daha yaktı.
TEST_PLAN           : BriefingService.build'in alarm yolundan çağrıldığı; haber bağlıyken
                      "bağlı değil" denmediği; tek WAV'ın tüm bölümleri içerdiği
REAL_PROOF_REQUIRED : PROVEN_REAL — tarayıcı kapalı sabah koşusunda brifing seslendi
ROLLBACK_PLAN       : brifing eklentisi bayrakla kapatılır; mevcut karşılama korunur
```

```
BATCH_ID            : B16                              [KAPANDI 2026-09-13]
NAME                : Bellek yazma yolu
REQUIREMENT_IDS     : 31, 32, 33, 34, 35, 36, 37, 38, 61, 62
GOAL                : Sahip bir tercihi sesle öğretebilsin, düzeltebilsin, unutturabilsin,
                      sabitleyebilsin ve neden hatırlandığını sorabilsin.
DEPENDENCIES        : B06
AFFECTED_SUBSYSTEMS : Memory, Voice Tools, Intent
EXPECTED_FILES      : services/api/app/memory/, app/voice/tools/, app/voice/intent/
RISK                : low
OWNER_ACTION        : no
TEST_PLAN           : tetikleyici kalıpların gerçek konuşma metniyle beslendiği; yazılan kaydın
                      provenans ve güven taşıdığı; hassas verinin dışarıda kaldığı
KAPANIŞ             : commit 07bea37 · CI 34762772790 yeşil (7/7) · 10/10 DONE
                      kanıt docs/evidence/b16-memory-write-path-2026-09-13.json
ÖLÇÜM               : `app.memory` eksik değil, OLGUN: 3400 satır, dondurulmuş yazma
                      politikası + sır tarayıcı, kanıt/sürüm/denetim zinciri, hibrit geri
                      getirme, terfi merdiveni, tam REST yüzeyi. Matrisin dediği kopuk
                      halka birebir doğrulandı: app/voice/ altında app.memory'yi import
                      eden TEK satır yoktu; memory dışı tek çağıran `app.experience` ve o
                      da yalnız Etkinlik Defteri'ni okuyor, konuşmayı bilinçle okumuyor.
                      61 için de veri M5'ten beri tamdı (`inspect_memory` provenans,
                      kanıt, sürüm, denetim, çelişki döndürüyor) — söylenemiyordu.
KARAR               : ADR-0126 — sesli oturum `explicit` bayrağını taşıyabilir. Politika
                      OWNER yetkisini yalnız çağıranın öne sürdüğü bayrağa veriyor (M5
                      gözden geçirme #4: bir web sayfası "always use" yazarak sahip kaydı
                      basamamalı). Sesli oturum yutma hattı değil: SENSITIVE kademeden
                      geçen, kimliği doğrulanmış cihazdaki sahibin kendisi — `/remember`
                      ile aynı güven. Aksi hâlde "sesle kalıcı bellek yazılır" yanlış olur,
                      çünkü CANDIDATE satır kalıcı değildir. Otomatik çıkarım ise HER ZAMAN
                      `explicit=False`.
YOL ÜSTÜNDE         : (1) Politikanın kendi karar tablosu M5'ten beri "explicit flag OR
                      explicit owner phrase" diyordu; kod bayrağı şart koşuyor. Bir GÜVENLİK
                      kuralı, o modülü inceleyen herkesin ilk okuduğu tabloda yanlış
                      yazılmıştı. Davranış zaten testliydi — yanlış olan belgeydi.
                      (2) `EVENT_TYPE_MEMORY_REMEMBERED` defter sözlüğünde vardı ve hiçbir
                      şey onu yazmıyordu; ilk yazıcısı bu batch.
                      (3) İlk `_ORIGIN_TR` taslağı hiçbir yazıcının koymadığı bir `kind`
                      anahtarını okuyordu: `memory.why` her kayıt için "nereden geldiğini
                      kaydetmemişim" diyecekti. Sözlük artık `service.py`'nin kendi
                      yazdıklarından okunuyor ve bir bekçi sürüklenmeyi yakalıyor.
                      (4) Türkçe olumsuzlama: `_has` ön ek eşleşmesi, "unutma" ise "unut"
                      ile başlıyor — yani hatırlamanın en güçlü ifadesi geri alınamaz bir
                      silme olarak okunacaktı. `_has_exact` ve `m.negation.*` pinledi.
                      (5) Bellek ailesi ilk konumunda dokuz korpus vakasını sahibinden
                      çaldı ("düzelt" native/evolution'ın, "nereden biliyorsun" konumun).
                      Aile artık tüm ailelerden SONRA çözülüyor.
TEST_PLAN           : tetikleyici kalıpların gerçek konuşma metniyle beslendiği; yazılan kaydın
                      provenans ve güven taşıdığı; hassas verinin dışarıda kaldığı
REAL_PROOF_REQUIRED : PROVEN_REAL — üretimde sesle yazılmış bir tercih satırı
ROLLBACK_PLAN       : araçlar araç kaydından çıkarılır; yazılan satırlar unutma yoluyla temizlenir
```

```
BATCH_ID            : B17                              [KAPANDI 2026-09-13]
NAME                : Bellek okuma ve enjeksiyon
REQUIREMENT_IDS     : 39, 40, 41, 42, 43, 44, 45, 55, 56
GOAL                : Öğretilen tercih bir daha söylenmeden kullanılsın.
DEPENDENCIES        : B16
AFFECTED_SUBSYSTEMS : Memory, Persona, Voice Tools, Retention
EXPECTED_FILES      : services/api/app/memory/, app/persona/, app/voice/tools/
RISK                : medium — persona talimatı büyür, jeton bütçesi ölçülmeli
OWNER_ACTION        : no
TEST_PLAN           : top-k geri getirmenin talimata girdiği; açık kaydın çıkarımı yendiği;
                      çelişen iki kaydın çözüldüğü; talimat boyutunun tavanı aşmadığı
KAPANIŞ             : commit 7728584 · CI 34767855834 yeşil (7/7) · 9/9 DONE
                      kanıt docs/evidence/b17-memory-injection-2026-09-13.json
ÖLÇÜM               : (a) 55 MISSING yazıyordu ve KOŞUYORDU: `sweep_expired` retention
                      sınıfına göre süpürüyor (session/short TTL, sabitlenmiş ve açık
                      kayıtlara dokunmuyor), Phase 8'den beri RetentionSweeper'a kayıtlı,
                      lifespan'de başlıyor, /health raporluyor. Eksik olan bekçiydi — iki
                      batch boyunca yanlış yazabildi çünkü yanlış olduğunda hiçbir şey
                      düşmüyordu. (b) 40 ikinci bir enjeksiyon noktası değil: talimat tek
                      (spec §4 adım 1), araç seçimi onu okuyor.
YOL ÜSTÜNDE         : ÖLÇÜM GERÇEK BİR KUSUR BULDU ve 45'in neden "hiç tetiklenmediğini"
                      açıkladı. Anahtarlı çatışma dalı `value_json` karşılaştırıyor;
                      sesle ya da konuşmadan yazılan HER kaydın değeri boş, yani
                      `{} == {}` her çift için doğru. Sonuç ölçüldü: sahip
                      "Kahveyi sade severim" dedikten sonra "Kahveyi az şekerli severim"
                      dediğinde ikincisi, düzelttiği şeye İKİNCİ KANIT olarak yazılıyordu
                      — tek satır, hâlâ "sade", kanıt sayısı iki. Sahip kendini düzeltti
                      ve sistem düzeltilen şeye daha çok inandı. 44'ün yaşadığı dal her
                      zaman ulaşılamazdı; artık değer yoksa METİN karar veriyor.
                      Ayrıca 56'nın kapsamını asıl genişleten şey bu batch: bellek artık
                      üçüncü taraf bir sağlayıcıya talimat içinde gidiyor, bu yüzden
                      enjeksiyon politikanın kendi kalıplarıyla son bir kez tarıyor.
TEST_PLAN           : top-k geri getirmenin talimata girdiği; açık kaydın çıkarımı yendiği;
                      çelişen iki kaydın çözüldüğü; talimat boyutunun tavanı aşmadığı
REAL_PROOF_REQUIRED : PROVEN_REAL — bir tercih söylendi, YENİ bir oturumda tekrar söylenmeden
                      uygulandı (zincirin uçtan uca kanıtı)
                      → üretim dışındaki tamamı kanıtlandı: gerçek uygulama nesnesi üzerinden
                      HTTP ile oturum açıldı, `memory.remember` ile öğretildi, oturum
                      kapatıldı, YENİ oturumun sağlayıcıya giden talimatı o cümleyi taşıdı
                      (`test_a_preference_taught_by_voice_reaches_a_NEW_session_...`).
                      Eksik olan yalnızca üretim turu (Karar 0).
ROLLBACK_PLAN       : enjeksiyon bayrakla kapatılır; persona eski haline döner
                      (`PAGENTOS_MEMORY_INJECTION_ENABLED=false`)
```

```
BATCH_ID            : B18                              [KAPANDI 2026-09-13]
NAME                : Süreklilik, varlıklar ve deneyim motoru
REQUIREMENT_IDS     : 46, 47, 48, 49, 50, 71, 72, 73
GOAL                : Proje ve görev bağlamı oturumlar arası sürsün; 1441 aktivite olayından
                      gerçekten ders çıksın.
DEPENDENCIES        : B17
AFFECTED_SUBSYSTEMS : Memory, Experience, Ledger
EXPECTED_FILES      : services/api/app/memory/, app/experience/
RISK                : low
OWNER_ACTION        : no
TEST_PLAN           : zamanlayıcının koştuğu; dersin belleğe yazıldığı ve `memory.remembered`
                      olayının yayıldığı; "bunu/şunu" ifadesinin doğru nesneye bağlandığı
KAPANIŞ             : commit efe6195 · CI 34772392586 yeşil (7/7) · 8/8 DONE
                      kanıt docs/evidence/b18-continuity-and-experience-2026-09-13.json
ÖLÇÜM               : Dört mekanizma tam, dördünün de çağıranı yoktu — bu deponun baskın
                      kusuru beşinci batch üst üste. (a) 71: motor tam, tek çağıran elle
                      POST, 1441 olaydan 0 bellek. (b) 47: `Entity`/`EntityEdge`, sekiz
                      kind, servis fonksiyonları ve REST yüzeyi M5'ten beri var, `app/`
                      altında sıfır çağıran. (c) 46/50: `Memory.project_id` ve
                      `conversation_id` şemada, dolduran yok — kimsenin yapamadığı iki
                      join. (d) 49 ÖLÇÜMLE DÜZELTİLDİ: odak mekanizması M19'dan beri var,
                      15 kind, sekiz pakette kullanılıyor; eksik olan `memory` kind'ıydı.
                      48 de düzeltildi: kimlik şeması `(kind, name)` tekilliğiyle zaten
                      cihazdan bağımsızdı — yazıcı gelene kadar sınanamıyordu, o kadar.
YOL ÜSTÜNDE         : (1) Zamanlayıcının İKİ yöne yürümesi gerekti: defter sorgusu en
                      yeniden başlıyor ve 200'de sınırlı, yani yalnız ileri giden bir tur
                      bugüne yetişir ve zaten orada olan 1441'e hiç ulaşmaz.
                      (2) İmleç YAZILANA değil OKUNANA göre ilerlemeli: yazma politikasının
                      yok saydığı bir özet satır üretmez, ve öyle bir sayfa imleci yerinde
                      bırakırdı. İmleç defterdeki `experience.ingested` olayı — motorun
                      kendi durumu yok, yazdığıyla çelişebilecek ikinci kaynak yok.
                      (3) Grafik ilk taslakta motorun KENDİ makbuzunu okuyordu: her tur
                      `system: memory` ve `capability: experience.ingest` düğümü büyüyordu
                      — sistemin, kendi grafik kurmasının grafiğini kurması. "İkinci tur
                      kopya eklemez" testi yakaladı; iki yazıcı artık tek dışlama listesini
                      paylaşıyor.
                      (4) Proje bağlantısı BU KONUŞMAYLA sınırlandı. `focus.current`'ın
                      tazelik sınırı yok (bir işaret zamiri en son şeyi kasteder, ne kadar
                      eski olursa olsun) ama her öğretilene damga vuran kalıcı bir bağlantı
                      başka bir sorudur: martta açılan proje hazirandaki tercihe yapışırdı
                      ve her zaman doğru olan bir join, join değildir.
                      (5) Odak yazımının catch'i oturumu zehirliyordu — bugün üç kez doğru
                      yazdığım rollback'i burada atlamışım; `PendingRollbackError` testte
                      çıktı.
TEST_PLAN           : zamanlayıcının koştuğu; dersin belleğe yazıldığı ve `memory.remembered`
                      olayının yayıldığı; "bunu/şunu" ifadesinin doğru nesneye bağlandığı
REAL_PROOF_REQUIRED : PROVEN_REAL — üretimde aktiviteden türetilmiş en az bir bellek satırı
                      → üretim dışındaki tamamı kanıtlandı: gerçek defter olayları gerçek
                      motordan geçip bellek satırı oldu, zamanlayıcı gerçek uygulama
                      nesnesinde rutin saatine bağlı ve /health raporluyor.
ROLLBACK_PLAN       : zamanlayıcı kapatılır; türetilmiş satırlar işaretli olduğu için silinebilir
                      (`PAGENTOS_EXPERIENCE_INGEST_ENABLED=false`)
```

```
BATCH_ID            : B19                              [KAPANDI 2026-09-13]
NAME                : Öz model ve dürüst yanıtlar
REQUIREMENT_IDS     : 63, 64, 65, 66, 74, 75, 76, 77, 78, 79, 80
GOAL                : "Şu an ne yapıyorsun", "nerede takıldın", "hangi özelliklerin çalışmıyor"
                      sorularına çalışma zamanından doğru yanıt verilsin.
DEPENDENCIES        : B06 (67/68), B01 (21)
AFFECTED_SUBSYSTEMS : Self Model, World Model, Health
EXPECTED_FILES      : services/api/app/selfmodel/, app/worldmodel/, app/system/health.py
RISK                : low
OWNER_ACTION        : no
TEST_PLAN           : runtime provenanslı satırların üretildiği; bayat verinin bayat işaretlendiği;
                      çalışmayan özellik listesinin matristen değil çalışma zamanından geldiği
KAPANIŞ             : commit 67b69d4 · CI 35016883524 yeşil (7/7) · 11/11 DONE
                      kanıt docs/evidence/b19-runtime-self-model-2026-09-13.json
ÖLÇÜM               : Bu batch öncekilerden FARKLI çıktı: mekanizmanın çağıranı vardı,
                      GİRDİSİ yoktu. `_apply_production_states` kanıt gücüne göre doğru
                      sıralı ve `TRUTH_RUNTIME`/`TRUTH_INSTALLED` yazıcıları mevcut — ama
                      ikisi de TARİHSEL: biri `Release` satırlarından, biri tamamlanmış
                      `deployment.*` olaylarından. Üretim turu yapılmadığı için ikisi de
                      boş, dolayısıyla 443 modülün `source_only` olması DÜRÜST cevaptı.
                      Okunmayan şey en doğrudan kanıttı: sürecin kendisi.
                      Ayrıca iki satır daha ölçümle düzeldi. 75 B06'da kapandı
                      (`TASK_ACTIVE_STATUSES`; state.py'nin yorumu kusuru birebir
                      anlatıyor) ve 80 2026-09-05'te bağımsız güvenlik incelemesiyle
                      düzeltilmişti — `stale` her yerde False yazılıp hiç hesaplanmıyordu.
YOL ÜSTÜNDE         : (1) İlk taslakta bayat bir gözlem için 0 dönüyordum, yani cevap
                      "takılı bir şey yok" oluyordu — kimsenin tazelemediği veriden
                      üretilen RAHATLATICI cümle. Rahatlatıcı cevap hak edilmesi gereken
                      cevaptır; artık bilmediğini söylüyor.
                      (2) Cihaz kimliğini durum kayıtçısından okumaya kalkmıştım; o
                      kayıtçı cihazın ne YAPTIĞINI taşıyor (boşta saniyesi, ekran durumu)
                      ve dakikada altı kez tazeleniyor. Kimlik `Device` satırında.
                      (3) `Incident` tablosunda `title` ve `created_at` yok; başlık
                      component+fingerprint'ten kuruluyor ve sıralama `last_seen_at` ile,
                      çünkü `id` bir UUID ve ona göre sıralamak tam bir güvenle rastgele
                      bir olay döndürürdü.
                      (4) `_call_obj` sözlük döndürüyor; liste döndüren iki okuma için
                      onu kullanmak "kayıtlı olay yok" diye okunuyordu — iki yardımcının
                      da var olma sebebi olan kendinden emin boş cevap.
                      (5) Kaynak-metni arayan bir testim, o metnin NEDEN kullanılmadığını
                      açıklayan docstring'e takıldı; artık AST ile import denetliyor.
TEST_PLAN           : runtime provenanslı satırların üretildiği; bayat verinin bayat işaretlendiği;
                      çalışmayan özellik listesinin matristen değil çalışma zamanından geldiği
REAL_PROOF_REQUIRED : PROVEN_REAL — üretimde öz modelde çalışan SHA ve cihaz build'i görünür
                      → üretim dışındaki tamamı kanıtlandı: gerçek `release_model()` ve gerçek
                      `Device` satırı, gerçek indeksten geçip modülü `source_only`'den
                      `running`'e taşıyor; dört soru gerçek sınıflandırıcı ve gerçek
                      `explain()` üzerinden cevaplanıyor.
ROLLBACK_PLAN       : provenans alanı isteğe bağlı; eski cevaplar korunur
```

```
BATCH_ID            : B20
NAME                : Ses oturumu dayanıklılığı ve dürüst durum
REQUIREMENT_IDS     : 215, 216, 217, 218, 221, 222, 223, 231, 233, 234, 235, 236, 238
GOAL                : Mikrofon kaybı görünsün, "Dinliyor" yalan söylemesin, oturum tavan öncesi
                      yenilensin, teşhis dürüst olsun.
DEPENDENCIES        : B06 (219/220), B19
AFFECTED_SUBSYSTEMS : Voice Realtime, Web, Health
EXPECTED_FILES      : services/api/app/voice/realtime/, apps/web/, app/system/health.py
RISK                : low
OWNER_ACTION        : no
TEST_PLAN           : `track.onended`/`onmute` tetiklendiğinde arayüz durumunun değiştiği;
                      faster-whisper'ın sağlıkta yanlış "etkin" görünmediği (kısa devre mutasyonu)
REAL_PROOF_REQUIRED : PROVEN_REAL — üretimde mikrofon iptal edildiğinde durum doğru değişti
ROLLBACK_PLAN       : istemci değişikliği; web sürümü geri alınır
KAPANIŞ             : commit 8ea8b3e · CI 35023336136 yeşil (7/7) · yerel kapı PASS · 13/13 DONE
                      kanıt docs/evidence/b20-voice-resilience-2026-09-13.json
                      (quality-gate -Fast PASS; web kapıları ayrıca: vitest 1694, tsc temiz,
                      oxlint 0 hata)
ÖLÇÜM               : Bu batch NE eksik kod ne de çağrılmayan mekanizma buldu: HİÇBİR ŞEY
                      YAPMAYAN KONTROLLER ve HİÇ DENETLENMEMİŞ İDDİALAR buldu. Bastırma
                      menüsünün üç seçeneği iki davranıştı (`!== "off"`), hassasiyetin dört
                      seçeneği üç davranıştı ("otomatik" ile "normal" birebir aynı
                      parametreleri üretiyordu), durum etiketi yanındaki analizör sessizliği
                      çizerken "Konuşuyor" yazıyordu, ton reddi politikası SINIFA değil tek
                      bir ADA bakıyordu, taşıyıcı `disconnected` uyarısını yere düşürüyordu
                      ve sunucu bildiği tavanı yayımlamıyordu.
                      Üç satır ölçümle düzeldi (7., 8. ve 9. düzeltme): 216 zaten ölçülüyordu
                      (ADR-0047 §4 okuma geri alma + kalıcı yankı kalıntısı), 217'nin modları
                      zaten uçtan uca bağlıydı — eksik olan iki seçenek arasındaki FARKTI —
                      ve 238'in çift yazımı B06'da 70 ile bitmişti; o satırın gerçek kusuru
                      başka yerdeydi: hayatta kalan satırın ne söylediğinde.
YOL ÜSTÜNDE         : (1) Tavan yenilemesinin sert tabanını mutasyonla kaldırınca erteleme
                      sıfır gecikmeli bir döngüye dönüştü ve süiti senkron olarak kilitledi —
                      vitest böyle bir döngüyü kesemez, yani mutasyon HİÇ hata üretmedi,
                      yalnız duran bir saat. `LEG_RENEW_MIN_WAIT_MS` her beklemeye taban
                      koyuyor; sunucudan gelen anlamsız bir tavan artık sınırlı.
                      (2) Politikayı sınıfa bakar hale getirmek, gerçek sağlayıcı yerine
                      `FakeTTSProvider` kullanan dokuz testi kırdı. Bu gerçek bir ihtiyaç
                      (anahtarsız makinede tüm selamlama yolunu kanıtlamak), o yüzden kaçış
                      kapısı yapıldığı yerde açıkça yazılıyor ve `app/` altında AST testiyle
                      yasak. Aksi halde politika sessizce zayıflatılmış olurdu.
                      (3) `runtime_checkable` protokole `leg_max_seconds` eklemek simülatörü
                      ve çevrimdışı fake'i sessizce protokol dışı bıraktı; ikisi de artık 0
                      diyor — "bacak bitirmem" cevabı, cevapsızlık değil.
                      (4) `deliver_as_text` commit eden bir depoya yazıyor ve hata halinde
                      oturumu geri alıyor. `alarm.detail_json` ondan ÖNCE yazılsaydı,
                      başarısız bir bildirim `greeting_failure` kaydını da götürürdü: kusurun
                      kaydını, kusuru telafi etme girişimi yok ederdi.
```

```
BATCH_ID            : B21
NAME                : Anlatım ve telaffuz
REQUIREMENT_IDS     : 224, 225, 226, 227, 228, 229, 230, 237, 414
GOAL                : Uzun metin gerçekten seslensin; telaffuz kuralları asistanın KENDİ
                      konuşmasına girsin.
DEPENDENCIES        : B20
AFFECTED_SUBSYSTEMS : Narration, Voice, Persona, Artifacts
EXPECTED_FILES      : services/api/app/narration/, app/voice/pronunciation/, app/persona/
RISK                : low
OWNER_ACTION        : TTS sağlayıcı kredisi
TEST_PLAN           : seam imzasının gerçek sağlayıcıyla uyuştuğu (sözleşme testi); telaffuz
                      kuralının persona talimatında göründüğü; kuyruk duraklat/devam
REAL_PROOF_REQUIRED : PROVEN_REAL — uzun metinden gerçek ses baytı üretildi (kota yoksa
                      PROVEN_PROXY + BLOCKED notu)
ROLLBACK_PLAN       : anlatım bayrakla kapatılır; metin yolu korunur
KAPANIŞ             : commit 087332b · CI 35028859336 yeşil (7/7) · 9/9 DONE
                      kanıt docs/evidence/b21-narration-and-pronunciation-2026-09-14.json
                      224/414 için PROVEN_PROXY + BLOCKED (TTS kredisi yok); geri kalanı
                      PROVEN_AUTOMATED
                      Yerel kapı: API 9583 test PASS, lint PASS, staged-update PASS;
                      cihaz süitinde 1055 testten 1'i (NotepadLifecycleTests pointer click)
                      MASAÜSTÜ MEŞGUL olduğu için düşüyor — bu batch cihaz kodunda tek satır
                      değiştirmedi (`git status devices/` boş) ve aynı süit gün içinde üç kez
                      yeşil koştu. READY_FOR_OWNER: operatör laboratuvarı boş bir masaüstü
                      ister; test artık ön plandaki pencereyi ADIYLA söylüyor.
ÖLÇÜM               : Bu deponun EN ESKİ kusur şekli, en büyük hâliyle: `app/narration/`
                      M4'ten beri planlıyor, cümlelere bölüyor, önbelleğe alıyor, ileri
                      okuyor ve iptal ediyor — ve `Synthesizer` seam'inin `app/` altında
                      HİÇBİR gerçeklemesi yoktu. Bir tek test geçiyordu ona. Bin altı yüz
                      satır anlatım makinesi, REST yüzeyi, Türkçe komut makinesi, cihazlar
                      arası imleç: eksiksiz, doğru ve DİLSİZ.
                      228 aynı hikâyenin başka sonu: eksiksiz bir alt sistem, kimsenin
                      ulaşamadığı tek bir yazıcıyla (elle PUT) — ve üretimde sıfır kural.
                      230 ise sayının KENDİSİNDE değil, ETRAFINDAKİ karakterlerde eksikti:
                      kesme işareti, eksi işareti, derece simgesi, bölü çizgisi.
YOL ÜSTÜNDE         : (1) `pronunciation.teach` ilk hâlinde `token` ve `context` argümanları
                      alıyordu; relay ikisini de reddediyor (`token` kimlik-biçimli,
                      `context` içinde "text" GEÇİYOR), yani araç hiç çağrılamazdı ve
                      incelemede kusursuz görünürdü. Guard yakaladı.
                      (2) Üç yeni araç kaydedilir kaydedilmez step-up katman haritası
                      testi kırıldı: harita her iki yönde de eksiksiz tutuluyor, çünkü
                      varsayılan OPEN, sonradan eklenen bir aracın kimse karar vermeden
                      yönetimsiz kalmasının yoludur.
                      (3) B20'nin ton politikası `FakeTTSProvider`ı reddettiği için
                      anlatım testleri açık `synthetic_speech=False` vekiliyle yazıldı;
                      `app/` altında bunu kullanmak AST testiyle yasak.
                      (4) "1/2"nin mekanik okunuşu "ikide bir" — bu Türkçede BAŞKA bir
                      ifade ("sık sık"). Kural iki deyimi sabitliyor: 1/2 ve 7/24.
```

```
BATCH_ID            : B22
NAME                : Türkçe hata dili ve dürüst durumlar
REQUIREMENT_IDS     : 704, 705, 706, 707, 708, 709, 710, 711
GOAL                : Sahip asla Python istisnası görmesin/duymasın; her başarısızlık ne olduğunu
                      ve ne yapılacağını Türkçe söylesin.
DEPENDENCIES        : B20
AFFECTED_SUBSYSTEMS : Errors, API, Web, Voice
EXPECTED_FILES      : services/api/app/errors/, apps/web/
RISK                : low
OWNER_ACTION        : no
TEST_PLAN           : bilinen hata sınıflarının sözlüğe eşlendiği; eşlenmemiş hatanın genel ama
                      anlaşılır mesaj verdiği; ham istisna dizesinin yanıtta/seste bulunmadığı
REAL_PROOF_REQUIRED : PROVEN_AUTOMATED + PROVEN_REAL (üretimdeki 10 başarısız araştırmanın
                      mesajları yeniden üretildiğinde Türkçe)
ROLLBACK_PLAN       : sözlük katmanı geçirgen moda alınır
KAPANIŞ             : commit 5a5d734 · CI 35080550278 yeşil (7/7) · 8/8 DONE
                      kanıt docs/evidence/b22-turkish-error-language-2026-09-14.json
                      PROVEN_AUTOMATED; 10 üretim koşusunun yeniden oynatılması Karar 0'da
                      Yerel kapı: API 9583 test PASS, lint PASS, web 1712 test PASS;
                      cihaz süitinde yine aynı tek test (NotepadLifecycleTests pointer
                      click) düşüyor. B21'de adı konmamıştı, artık ölçüldü: ön planı
                      SÜREKLİ olarak `claude.exe` tutuyor (ajanın kendi istemcisi), bu
                      yüzden `window.activate` başarılı dönüp milisaniyeler içinde geri
                      alınıyor. B21/B22 devices/ altında hiçbir şeye dokunmadı; CI'da
                      etkilenmez (etkileşimli masaüstü yok, laboratuvar atlanır).
ÖLÇÜM               : Sözlük hiç yoktu ve taksonomi TEK yerde de değildi: sekiz alt sistem
                      Enum ile 56 sınıf tanımlıyor, kırk sınıf daha modül sabiti olarak
                      yaşıyordu. 33 rota `HTTPException(detail=str(exc))` ile yanıt
                      veriyordu; arkalarındaki cümleler geliştirici İngilizcesi
                      ("no matching weekday within a week - refusing to guess") ve web
                      bunları olduğu gibi basıyordu.
                      706/707 kokpitte zaten doğruydu (Panel yükleniyor/alınamadı/henüz
                      yok/boş ayrımını kuruluşundan beri yapıyor); SAYFALARDA yoktu:
                      /artifacts ilk yanıttan önce boş liste gösteriyordu.
YOL ÜSTÜNDE         : (1) Eksiksizlik testinin ilk hâli, sözlüğün yazıldığı SEKİZ Enum'u
                      okuyordu — yani iddianın iki yarısı tek kaynaktan geliyordu. Yeşil
                      geçti ve kırk sınıf eksikti; ancak bir rota düzenlemesi
                      `identity_unresolved`i genel bir sınıfla değiştirince, o tokenı adıyla
                      sabitleyen haber testi kırmızıya döndü ve eksik görüldü. Test artık
                      kaynağı da tarıyor.
                      (2) Aynı test, sınıf tokenının SÖZLEŞMENİN parçası olduğunu gösterdi:
                      çağıran ona bakıyor. Sahip için değişen şey token değil, yanındaki
                      cümle.
                      (3) `not.toContain("Tekrar dene")` doğru davranan bir panelde patladı:
                      ipucu cümlesi "Tekrar denenebilir." ve içinde o kelimeler geçiyor.
                      Artık kontrolün kendisi (`data-panel-retry`) doğrulanıyor.
```

```
BATCH_ID            : B23
NAME                : Web gezinme kabuğu
REQUIREMENT_IDS     : 685, 686, 690, 691, 692, 695, 716, 720, 722, 723
GOAL                : Altı sayfa birbirine bağlansın; hiçbir sayfa çıkmaz sokak olmasın.
DEPENDENCIES        : B22
AFFECTED_SUBSYSTEMS : Web
EXPECTED_FILES      : apps/web/app/layout.tsx, apps/web/app/*
RISK                : low
OWNER_ACTION        : no
TEST_PLAN           : her sayfadan her sayfaya yol olduğu; PWA girişinden gezinmenin çalıştığı
REAL_PROOF_REQUIRED : PROVEN_AUTOMATED (gezinme testi CI'da — B02 sayesinde koşar)
ROLLBACK_PLAN       : layout değişikliği geri alınır
KAPANIŞ             : commit 5a5d734 · CI 35080550278 yeşil (7/7) · 10/10 DONE
                      kanıt docs/evidence/b23-web-navigation-shell-2026-09-14.json
                      web: 1730 test PASS, tsc temiz, oxlint 0 hata; API 9583 test PASS
                      cihaz süitinde yine aynı tek test (masaüstü ön planı) — B22 kaydına
                      bakınız, sebep ölçüldü ve bu batch devices/ altına dokunmadı
ÖLÇÜM               : Yazmadan önce ölçüldü — her sayfadaki her `href` okundu:
                      / → beşi de, /research → /artifacts, /voice → /core, ve
                      /artifacts, /core, /core/cockpit → HİÇBİR YERE. `layout.tsx`
                      `<body>{children}</body>` idi. Bildirimden gelen sahip için tek
                      çıkış tarayıcının kendi düğmesiydi; PWA olarak kurulduğunda
                      (start_url /core, standalone) o da yoktu.
                      695 klasik şekil: cihaz istemcisi, ayrıştırıcısı ve durumu M18.3'ten
                      beri var; gösterilen tek şey "Ekran / Ortam" içindeki EKRAN satırıydı.
                      722/723 ise "bir kez karar verilmiş" şekli: kademe makinenin ne
                      teslim ettiğine hiç bakmıyordu, WebGL yeteneği yalnızca mount'ta
                      ölçülüyordu.
YOL ÜSTÜNDE         : (1) Nav'ın bariz iki uygulaması da yanlıştı: her yere çubuk koymak
                      Çekirdek'i tam da manifestin reddettiği kabuğa sokuyor, Çekirdek'e
                      hiç koymamak ise bu batch'in kaldırmaya çalıştığı çıkmaz sokağı
                      yeniden üretiyordu. Tek bileşen, iki ağırlık.
                      (2) `/core/cockpit`, `/core` ile başlıyor: saf önek eşleşmesi iki
                      girişi birden işaretliyordu. En uzun eşleşme kazanıyor ve test
                      işaret SAYISINI doğruluyor.
```

```
BATCH_ID            : B24
NAME                : Eksik sayfalar ve durum rozetleri
REQUIREMENT_IDS     : 689, 693, 694, 696, 697, 698, 699, 700, 712, 713, 714
GOAL                : Yazılmış ama görünmeyen aileler (bellek, rutin, alarm, güvenlik, SelfDev,
                      bildirim, ayarlar) sayfa kazansın; boş panel gösterilmesin; özellik durumu
                      ve kanıt rozetleri ürüne yansısın.
DEPENDENCIES        : B23
AFFECTED_SUBSYSTEMS : Web
EXPECTED_FILES      : apps/web/app/*
RISK                : low
OWNER_ACTION        : no
TEST_PLAN           : veri kaynağı boş olan ailenin panel doğurmadığı; rozetlerin FEATURE_MATRIX
                      ile aynı sınıf sözlüğünü kullandığı
REAL_PROOF_REQUIRED : PROVEN_AUTOMATED — 27 panelin tamamı ya veri gösteriyor ya gizli
ROLLBACK_PLAN       : sayfa bazında geri alınır
KAPANIŞ             : commit 5a5d734 · CI 35080550278 yeşil (7/7) · 11/11 DONE
                      kanıt docs/evidence/b24-pages-and-badges-2026-09-14.json
                      web: 1807 test PASS (96 dosya; 1730'du), tsc temiz, oxlint 0 hata
                      API: 9719 PASS / 5 atlanan; ruff temiz; staged-update 89 kontrol PASS
                      REAL_PROOF birebir karşılandı: quiet-families.test.tsx 27 panelin
                      HER BİRİNİ ailesi boşken çiziyor ve çıktının boş olduğunu doğruluyor
                      cihaz süitinde yine aynı TEK test (919/920). Testin kendi mesajı bu kez
                      sebebi söylüyor: tıklama, kayıt defterinin çözemediği bir ön plan
                      varken indi (tam ekran/yükseltilmiş pencere). Ölçüldü: sahibin
                      CarlaUE4 simülatörü tam ekran çalışıyor. Bu batch devices/ altına
                      dokunmadı — READY_FOR_OWNER
ÖLÇÜM               : İki kusur, tek kök. Birincisi matrisin kendi ölçümü: 27 panelden
                      13'ü aynı anda boş — okuyan sahip için ürün "hiçbir şey yapmıyorum"
                      diyor, hem de on üç ayrı yazıyla. İkincisi, yedi ailenin hiç sayfası
                      yoktu ve hepsinin MISSING olma sebebi aynı şekildi: KOD VARDI,
                      ÇAĞIRAN YOKTU. `fetchNotificationHistory` (req 377) hiçbir kanalın
                      taşımadığı satırları ayrıştırıyor ve tek çağıranı yoktu.
                      `/v1/alarms/history` B13 req 285'ten beri her alarm GERÇEKLEŞMESİNİ
                      sunuyor — sahibin alarm hakkında sorduğu tek soru, ve kurulu alarm
                      satırından yapısal olarak cevaplanamayan soru — hiç sorulmamış.
                      Güvenlik yetki izi anayasal bir kuralın uygulanışını kaydediyor,
                      yüzeyi yok. Toplam: çağıranı olmayan on bir uç.
                      "1 kalıcı 422" ise zaten 715'te kapanmıştı (B03, 9ddf243); bu turda
                      27 yolun tamamı gerçek uygulama nesnesinin OpenAPI'sinde doğrulandı.
YOL ÜSTÜNDE         : (1) İlk uygulama React context kullanıyordu (aile sayfasında gizlemeyi
                      kapatmak için). Dokuz panel testi bileşeni DÜZ FONKSİYON olarak
                      çağırıyor — orada hook çalışmaz. Prop hem ikisinde de çalışıyor hem
                      de çağrı yerinde görünüyor.
                      (2) `researchFocus`'un kendi paneli yok; odak şeridi araştırma
                      panelinin içinde. Görev listesi boş + odak ucu FAILED olduğunda panel
                      gizleniyor ve arıza kayboluyordu. Artık iki yarıdan biri konuşuyorsa
                      panel duruyor.
                      (3) `test_web_asks_for_routes_that_exist.py` yalnız
                      `export const X_PATH` yazımını okuyordu — yani ürünün yollarının
                      12'sini denetliyordu. Bu batch'in eklediği on bir uç satır içinde
                      yazılıyor ve denetimsiz girecekti. 43'e genişletildi.
```

```
BATCH_ID            : B25
NAME                : Keşfedilebilirlik
REQUIREMENT_IDS     : 660, 701, 702, 703, 724, 725
GOAL                : Sahip sisteme ne diyebileceğini görebilsin; panik anahtarı bulunabilir
                      olsun; klavye ve erişilebilirlik çalışsın.
DEPENDENCIES        : B24
AFFECTED_SUBSYSTEMS : Web, Voice Tools
EXPECTED_FILES      : apps/web/, services/api/app/voice/tools/
RISK                : low
OWNER_ACTION        : no
TEST_PLAN           : yetenek listesinin araç kaydından üretildiği (elle liste yasak); komut
                      paletinin her sayfadan açıldığı
REAL_PROOF_REQUIRED : PROVEN_AUTOMATED
ROLLBACK_PLAN       : bileşen bazında geri alınır
KAPANIŞ             : commit 5a5d734 · CI 35080550278 yeşil (7/7) · 6/6 DONE
                      kanıt docs/evidence/b25-discoverability-2026-09-14.json
                      web: 1852 test PASS (99 dosya; 1807'ydi), tsc temiz, oxlint 0 hata
                      API: 9744 PASS / 5 atlanan (9719'du); ruff temiz; staged-update PASS
                      TEST_PLAN birebir: test_capability_list.py listeyi İKİ uydurma araçlık
                      bir kayıttan türetiyor (elle yazılmış cevap bunu izleyemez); palette
                      testleri paletin layout'ta olduğunu ve her tuşu doğruluyor
                      cihaz süitinde yine aynı TEK test (919/920) — beşinci tur, aynı ölçülen
                      sebep: tam ekran/yükseltilmiş pencere ön planı tutuyor (sahibin CarlaUE4
                      simülatörü). Bu batch devices/ altına dokunmadı — READY_FOR_OWNER
ÖLÇÜM               : Denetimin bütün üründeki EN DÜŞÜK skoru: keşfedilebilirlik 0.5/5. Tek
                      kusur değil; sistemin SAHİP OLDUĞU üç şeye sahibin ulaşamaması.
                      `POST /v1/identity/panic` B05'ten beri her oturumu iptal ediyor ve
                      matrisin notu üç kelime: "Arayüzde görünmüyor". Araç kaydı 130 araç
                      açıklaması tutuyor — Türkçe, çoğu sahibin KENDİ cümlelerini tırnak
                      içinde taşıyor ('beş dakika ertele', 'gözünü kapat') — ve tek okuyucusu
                      modeldi. Kabukta atlama bağlantısı, odak halkası yoktu; ve bu turda
                      ÖLÇÜLDÜ: iki Çekirdek sayfasının hiç `<main>`'i ve `<h1>`'i yoktu.
                      Eksik olan bir şey yoktu. Eksik olan kapıydı.
YOL ÜSTÜNDE         : (1) Türkçe kesme işareti tırnak değil, EKTİR. Bariz ayıklayıcı
                      `Active Eye'ı ... 'gözünü kapat'` içinde tırnağı `Eye'` de açıp
                      `gözünü`den önce kapatıyor: etiketi çıkarıp cümleyi kaybediyor. Elle
                      bakılan altı aileden üçü bozuktu. Düzeltilen okuyucu iki yanında da
                      sınır arıyor, iki yanı harf olan kesmeyi sözcüğün parçası sayıyor.
                      (2) Var olan bekçi yeni aracı yakaladı: `test_every_registered_tool_has
                      _a_tier` sınıflandırılmamış aracı reddediyor. OPEN verildi — "ne
                      yapabilirsin" sorusunu kapının arkasına koymak, sahibin başka bir şey
                      isteyebilmesi için gereken tek cevabı kapatmak olurdu.
                      (3) B23'ün soluma testi TÜM stil dosyasında `display: none` arıyordu;
                      bu turda eklenen ekran-okuyucu sınıfının YORUMU yüzünden kırmızıya
                      döndü. İddia iki kurala dairdi, artık iki kurala soruluyor — ve
                      okuyucunun onları bulduğu da doğrulanıyor.
                      (4) Yol bekçisi yalnız `app/lib/**/*.ts` yürüyordu; panik anahtarı
                      `app/security/PanicControl.tsx`'te. Yokluğu en çok önem taşıyan tek yol,
                      bekçinin göremediği tek yoldu. 43 → 47.
```

```
BATCH_ID            : B26
NAME                : Niyet güvenliği ve misroute tespiti
REQUIREMENT_IDS     : 736, 737, 738, 739, 741, 749, 750
GOAL                : Yedi ölçülmüş yanlış yönlendirme kapansın; negatif külliyat ve telemetri
                      bu sınıfın tekrarını otomatik yakalasın. Deterministik kalkan KORUNUR.
DEPENDENCIES        : B22
AFFECTED_SUBSYSTEMS : Intent, Ledger
EXPECTED_FILES      : services/api/app/voice/intent/, app/ledger/
RISK                : low
OWNER_ACTION        : no
TEST_PLAN           : 7 misroute cümlesinin ÖNCE kırmızı kanıtlandığı, sonra yeşile döndüğü;
                      negatif külliyatın kritik fiilleri (dur, gönder, işle, dağıt, kapat)
                      koruduğu
REAL_PROOF_REQUIRED : PROVEN_AUTOMATED — 103 cümlelik ölçüm seti CI'da; misroute sayısı 0
ROLLBACK_PLAN       : tablo değişiklikleri commit bazında geri alınır
KAPANIŞ             : commit 5a5d734 · CI 35080550278 yeşil (7/7) · 7/7 DONE
                      kanıt docs/evidence/b26-intent-safety-2026-09-14.json
                      REAL_PROOF birebir: 107 cümle CI'da, misroute 0 (ölçüm 103'tü)
                      test_intent_misroutes.py (46) + test_route_telemetry.py (16)
                      intent + korpus süitleri 2063 PASS; etkilenen aileler 3733 PASS
                      API 9807 PASS / 5 atlanan (9744'tü); ruff temiz; staged-update PASS
                      cihaz süitinde yine aynı TEK test (919/920) — altıncı tur, aynı sebep:
                      operatör laboratuvarı kimsenin kullanmadığı bir masaüstü istiyor ve
                      tam ekran bir uygulama onu tutuyor. Bu batch devices/ altına hiç
                      dokunmadı — READY_FOR_OWNER
ÖLÇÜM               : Yedi cümlenin hepsi, hiçbir şey değiştirilmeden, bu makinede canlı
                      yönlendiriciye karşı yeniden üretildi. Denetim dördünü adlandırmıştı;
                      ölçüm diğer üçünü aynı ailelerde buldu — mesele yedi cümle değil DÖRT
                      KÖK. Ve dört kökün hepsi aynı hata: bir kuralın cümleyi TEK SÖZCÜKLE
                      kabul etmesi.
                      `otomatik` tek başına ekran politikası demekti (güncelleme, yedekleme,
                      otomatik kaydetme hepsi oradan geçti). `gönder` tek başına mail
                      göndermek demekti — bu ailede geri alınamayan tek eylem. `ekle` tek
                      başına takvim etkinliği demekti; her şey bir şeye eklenir. Ve `yaz` ön
                      ek eşleşmesiyle `yazdır`ı da yakalıyordu — Türkçede o, ettirgen:
                      BASTIR. Taban: 107 vaka / 8 misroute. Sonra: 107 / 0.
                      Bu batch 59 yönlenmeyen cümleyi yönlendirmiyor — o 726-735, B27'nin.
                      Yedisi kümede `expected=None` ile duruyor: yalnız YASAK listeleri için,
                      yani "Sesini kıs." bir gün eyleme ulaşırsa süit yine düşer.
YOL ÜSTÜNDE         : (1) Sekizinci misroute: `Bu belgeyi gönder.` kritik-fiil çifti olarak
                      yazılmıştı, bilinen kusur olarak değil — ilk koşuda düştü. Aynı kök.
                      (2) İlk taslakta külliyatın varsayılan yasak listesi TÜM eylemli
                      niyetlerdi ve beklenen niyeti de yasaklıyordu: "Gözünü aç." tam olarak
                      gitmesi gereken yere gittiği için düşüyordu. Kendi beklentisini
                      yasaklayan vaka doğru yönlendirici için de düşer — külliyatın
                      okunmayı bırakması böyle başlar.
                      (3) Telemetri modül düzeyinde tutuluyor (operatör kaydının aynı şekli);
                      conftest her testten önce ve sonra sıfırlıyor, yoksa bir testin
                      çözümlemesi diğerinin "dur"uyla eşleşip kimsenin yapmadığı bir
                      misroute raporlardı.
```

```
BATCH_ID            : B27
NAME                : Günlük niyet kapsamı
REQUIREMENT_IDS     : 726, 727, 728, 729, 730, 731, 732, 733, 734, 735
GOAL                : En sık söylenecek on cümle yönlensin.
DEPENDENCIES        : B26, ilgili yetenek batch'leri (B16, B31, B28, B25, B45, B46)
AFFECTED_SUBSYSTEMS : Intent, Voice Tools
EXPECTED_FILES      : services/api/app/voice/intent/
RISK                : low
OWNER_ACTION        : mail/takvim niyetleri (729, 730, 731) sağlayıcıya bağlı — checkpoint
TEST_PLAN           : her cümle ve en az üç Türkçe eşanlamlısı; kapsanmayan oran eşiğin altında
REAL_PROOF_REQUIRED : PROVEN_AUTOMATED — 103 cümlelik sette kapsanmayan oran %57'den hedefe iner
ROLLBACK_PLAN       : niyet bazında geri alınır
KAPANIŞ             : commit 5a5d734 · CI 35080550278 yeşil (7/7) · 10/10 DONE
                      kanıt docs/evidence/b27-daily-intents-2026-09-14.json
                      REAL_PROOF birebir: günlük küme 49 cümle (on cümle + ≥3 eşanlamlı),
                      kapsanmayan %65,3 (32/49) → %0 (0/49), tek koşuda; B26'nın 107'lik
                      kümesinde yönlenmeyen 19 → 12, misroute yine 0
                      test_intent_daily_coverage.py (82) + test_daily_intent_tools.py (24)
                      + korpus d.* (98, gerçek röle üzerinden); 5 mutasyon kırmızı
                      gate 7/7 PASS (ikinci koşu): API 10015 PASS / 5 atlanan (9807'ydi);
                      ruff temiz; cihaz 920/920 (bu kez önde tam ekran uygulama yoktu);
                      staged-update 89 PASS; web 1852 PASS, tsc temiz
ÖLÇÜM               : 726/727/728 zaten yönleniyordu — denetimin "yönlenmiyor" dediği
                      günden sonra B15 ve B16 kapatmış; B27 ölçtü ve kümeye koydu. Yedisi
                      hiçbir şeye ulaşmıyordu ve üçünün ARACI da yoktu: araştırma iptali
                      (REST vardı, sesi yoktu), ses seviyesi (cihaz alarm için yapıyordu,
                      sahip isteyemiyordu), ekran görüntüsü (ajan `screen.capture`ü M19'dan
                      beri cevaplıyordu, bulutta çağıran yoktu). Dördüncü, takvim iptali,
                      politika gereği YOK — araç dürüst red makbuzu döner (spec §1).
                      Her yeni eşleştirici isim + fiil ister (ADR-0133); her genişletme
                      komşularıyla aynı koşuda tutuluyor: "Günaydın, bugün ne var?" brifing
                      kaldı, "Yetenek durumu ne?" M24'ün kaldı, "Bunu yapabilir misin?"
                      liste değil, "Toplantı notlarını sil." toplantı değil, "iptal etme"
                      olumsuz.
YOL ÜSTÜNDE         : (1) "Yeni mail var mı?" TASLAK açıyordu (soru asla yazmaz) — B26'nın
                      sınıfından bir misroute daha, kapatıldı. (2) İlk taslak "bugün ne
                      var" ile sabah brifingini, "yetenek" kökü ile M24'ün durum sorusunu
                      çaldı — SHIELD ilk koşuda yakaladı. (3) research.cancel'ın defter
                      satırı `research_job_id`'yi str gönderiyordu, sessizce düşüyordu;
                      birim testi yakaladı (korpus geçerken!). (4) Önceki oturumdan kalan
                      yarım B27 izleri (tools.py'da çift handler, harness'ta bozuk
                      bağlam dalı) bulundu ve temizlendi. (5) İlk kapı koşusunda iki
                      koruma düştü: hata sözlüğü (dört yeni sınıfın Türkçesi yoktu —
                      ve `: Final` yazılan sabitlerin o korumaya görünmediği çıktı) ve
                      öz-model indeksi (`operator.screenshot` çıplak isimle kaydedilmişti).
                      İkisi de düzeltildi, kapı yeniden koştu.
```

```
BATCH_ID            : B28
NAME                : Operatör girdi erişimi
REQUIREMENT_IDS     : 91, 92, 93, 94, 95, 96, 97, 98, 104, 107, 109, 110
GOAL                : Sistem tıklayabilsin, tuşa basabilsin, kısayol gönderebilsin, ekran
                      görüntüsü alabilsin — FocusGuard ve sır reddi zorunlu.
DEPENDENCIES        : B05 (yetki kapısı), B03
AFFECTED_SUBSYSTEMS : Operator, Device, Security
EXPECTED_FILES      : services/api/app/operator/, devices/windows-agent
RISK                : **high** — girdi enjeksiyonu; FocusGuard ve izin listesi zorunlu
OWNER_ACTION        : no
TEST_PLAN           : FocusGuard ihlalinde gönderimin durduğu ve kısmi gönderimin muhasebelendiği;
                      cihazın `secret` bayrağının buluttan gönderildiği ve sır yazımının
                      reddedildiği; koordinat yolunun yalnız son çare olarak seçildiği
REAL_PROOF_REQUIRED : PROVEN_REAL — üretimden verilen bir komut gerçek pencerede iş yaptı,
                      postcondition doğrulandı, FocusGuard ihlali kaydedilmedi
ROLLBACK_PLAN       : yetenek bazında bayrak; cihaz tarafı agent sürümüyle geri alınır
KAPANIŞ             : commit 5a5d734 · CI 35080550278 yeşil (7/7) · 12/12 DONE (104 B27'de kapanmıştı)
                      kanıt docs/evidence/b28-operator-input-2026-09-14.json
                      REAL_PROOF: PROVEN_REAL (test lab) — bu masaüstünde gerçek Not
                      Defteri'nde Home tuşu imleci başa aldı ("Xabc"), Ctrl+A + Delete
                      belgeyi boşalttı, kaydırma imleci ±2 px'te yeniden gözlendi, hepsi
                      cihazın kendi geri okumasıyla (NotepadLifecycleTests, 5/5). ÜRETİMDEN
                      verilen komut Karar 0'ın — READY_FOR_OWNER.
                      test_operator_input.py (33) + korpus op.key/shortcut/scroll (37);
                      5 mutasyon kırmızı (secret bayrağı, son çare kapısı, retries, kısmi
                      muhasebe, imleç doğrulaması)
                      gate 7/7 PASS (ilk koşu): API 10086 PASS / 5 atlanan (10015'ti);
                      ruff temiz; cihaz 921/921 (yeni laboratuvar testi dahil);
                      staged-update 89 PASS
ÖLÇÜM               : Cihaz `keyboard.key/shortcut` ve `pointer.*`'ı M19'dan beri cevaplıyor,
                      FocusGuard ve `RefuseSecret` cihazda hazırdı; bulutta ÇAĞIRAN yoktu
                      (92-98 "Çağıran yok"), `secret` bayrağı hiç gönderilmiyordu (109),
                      makbuz kaç adımın yürüdüğünü söylemiyordu (110) ve koordinat
                      politikası yalnız tarayıcıda vardı (107). Bu batch dört bulut yarısını
                      yazdı; cihaz koduna dokunmadı (yalnız laboratuvar testi eklendi).
YOL ÜSTÜNDE         : (1) Röle testi `key_press` alanının araca hiç ulaşmadığını gösterdi:
                      oturum, çözümlenen niyeti alan alan kopyalıyor ve liste açık.
                      B27'nin `capability_family` ve `media_volume_direction` alanları da
                      eksikti — B27'nin birim testleri tur kaydını elle kurduğu için
                      görememişti; dördü eklendi, B27'ye röle üzerinden test kondu.
                      (2) `operator.key`'in "Hangi tuş?" sorusu röle tarafından "başarılı"
                      sayılıyordu: soru sorabilen araç listesi açık, ikisi eklendi.
```

```
BATCH_ID            : B29
NAME                : Operatör UIA ve sonuç doğrulama
REQUIREMENT_IDS     : 99, 100, 101, 102, 103, 105, 111, 116
GOAL                : En yüksek anlamsal kontrol yüzeyi (UIA) kullanılsın; hiçbir eylem
                      postcondition doğrulanmadan başarılı sayılmasın.
DEPENDENCIES        : B28
AFFECTED_SUBSYSTEMS : Operator, UIA, Vision
EXPECTED_FILES      : devices/windows-agent, services/api/app/operator/
RISK                : medium
OWNER_ACTION        : 105 için vision sağlayıcısı — checkpoint, bloklamaz
TEST_PLAN           : UIA ile tetiklenen düğmenin sonucunun ağaçtan doğrulandığı; doğrulama
                      başarısızsa eylemin başarısız raporlandığı
REAL_PROOF_REQUIRED : PROVEN_REAL — gerçek bir uygulamada UIA ile düğme tetiklendi ve sonuç
                      bağımsız okundu
ROLLBACK_PLAN       : UIA yolu bayrakla kapatılır, DOM/uygulama yoluna düşülür
KAPANIŞ             : commit 5a5d734 · CI 35080550278 yeşil (7/7) · 8/8 DONE (105 PU: sağlayıcı anahtarı
                      sahibin — checkpoint, bloklamadı)
                      kanıt docs/evidence/b29-operator-uia-2026-09-14.json
                      REAL_PROOF birebir (test lab, bu masaüstü): kaydedilmemiş Not
                      Defteri kapatılınca çıkan diyalogun "Kaydetme" düğmesi ağaçtan
                      BULUNDU (ui.inspect), UIA ile TETİKLENDİ (ui.invoke), sonuç bağımsız
                      okundu: süreç bitti, ne diyalog ne editör pencere listesinde
                      (NotepadLifecycleTests 6/6). ÜRETİMDEN komut Karar 0'ın.
                      test_operator_ui.py (37) + korpus op.ui/op.see (34);
                      6 mutasyon kırmızı (değişmeyen öğe, geri okumasız set_value,
                      sağlayıcısız cevap, görüntüsüz istek, adaptörsüz uygulama, yalancı
                      window_gone okuması)
                      gate (ikinci koşu): API 10159 PASS / 5 atlanan (10086'ydı); ruff temiz;
                      staged-update 89 PASS; cihaz 921/922 — yine aynı TEK masaüstü testi
                      (önde tam ekran pencere), B29 işaretçi koduna dokunmadı; tek başına
                      yeniden koşunca 6/6 (yeni UIA lab testi dahil) — READY_FOR_OWNER
ÖLÇÜM               : Cihaz `ui.inspect/invoke/set_value/select`'i M19'dan beri cevaplıyor;
                      bulut yalnız `ui.inspect`'i (type_text'in doğrulaması için) çağırıyordu.
                      Bu batch üç araç yazdı: `operator.ui` (invoke/set_value/select —
                      her biri etkinleştir → eylem → BAĞIMSIZ okuma), `operator.inspect`
                      (ağaç/metin okuma), `operator.see` (görüntü anlamlandırma, sağlayıcı
                      arayüzü). 111 yapısal olarak: 22 planın son adımı postcondition
                      taşımak zorunda. 116: Not Defteri + Hesap Makinesi adaptörleri
                      (ölçülmüş ağaçlardan).
YOL ÜSTÜNDE         : (1) İlk okuma eşleştiricisi "Belgeyi oku"yu belge ailesinden, "Ne
                      görüyorsun?"u açıklama ailesinden çaldı — B26 kalkanı yakaladı;
                      "belge" belge ailesinin, "görüyorsun" gözün. (2) Yapısal test plan
                      adının IfExp ile kurulmasını reddetti — `PLAN_BY_READ_MODE` tablosu.
                      (3) Araçlar window.list'i iki kez çağırıyordu (çözümleyici + adaptör
                      için görüntü); tek okuma (`_once`), çünkü masaüstü iki okuma arasında
                      değişebilir. (4) İlk window_gone mutasyonu kırmızıya dönmedi: yedek
                      doğrulama da düşüyordu — mutasyon okumanın kendisine taşındı, kırmızı.
                      (5) İlk kapı koşusu üçüncü hırsızlığı yakaladı: çıplak "ne yazıyor"
                      dalı "Üçüncü sayfada ne yazıyor?"u belge ailesinden aldı (6 vaka);
                      çıplak soru artık gerçekten çıplak — yanına yalnız ekranın sözcükleri.
```

```
BATCH_ID            : B30
NAME                : Operatör pencere, süreç ve servis
REQUIREMENT_IDS     : 82, 84, 85, 86, 87, 88, 117, 118, 119, 120, 121, 122
GOAL                : Pencere yönetimi, izin listesi genişlemesi ve politikayla süreç/servis
                      yönetimi erişilebilir olsun; iki izin listesi tek kaynaktan okunsun.
DEPENDENCIES        : B29
AFFECTED_SUBSYSTEMS : Operator, Device, Security
EXPECTED_FILES      : packages/protocol/, services/api/app/operator/, devices/windows-agent
RISK                : medium — süreç/servis durdurma politikaya bağlı
OWNER_ACTION        : UAC gerektiren servis işlemleri — checkpoint
TEST_PLAN           : bulut ve cihaz izin listelerinin AYNI dosyadan okunduğu (sözleşme testi);
                      politika dışı sürecin durdurulamadığı
REAL_PROOF_REQUIRED : PROVEN_REAL — üretimden pencere taşındı/boyutlandı
ROLLBACK_PLAN       : izin listesi daraltılır
KAPANIŞ             : commit 5a5d734 · CI 35080550278 yeşil (7/7) · 12/12 DONE (122 gerçek yeniden
                      başlatma READY_FOR_OWNER: companion yükseltilmemiş, UAC sahibin —
                      checkpoint, bloklamadı)
                      kanıt docs/evidence/b30-operator-process-service-2026-09-14.json
                      İki izin listesi TEK dosya: packages/protocol/operator-allowlists.json
                      (7 uygulama, 8 terminal deseni, 3 bulut komutu, durdurulabilir imajlar,
                      yeniden başlatılabilir servisler); bulut import'ta okur, C# testi cihaz
                      tablolarını aynı dosyaya eşit tutar, Python testi C# kaynağını da okur.
                      Cihaza 4 yeni yetenek (process.list/stop, service.status/restart;
                      All 32→36), politika iki tarafta da cihaza sorulmadan reddeder.
                      REAL_PROOF: window.move/resize gerçek Not Defteri'nde rect yeniden
                      okunuyor (NotepadLifecycleTests); ÜRETİMDEN komut Karar 0'ın.
                      Bu masaüstünde gerçek: process.list test sürecini imajla buldu,
                      process.stop powershell/svchost'u politikayla reddetti, service.status
                      Spooler'ı SCM'den okudu, service.restart bthserv'i politikayla ve
                      Spooler'ı UAC cevabıyla reddetti (durum önce/sonra aynı).
                      Yolda bulunan: lab'ın ilk koşusu sahibin kendi kaydedilmemiş Not
                      Defteri'ne WM_CLOSE gönderdi (imajla durdurma ürünün vaadi), companion
                      diyaloğu modal olarak bildirdi ve cevaplamadı; test artık yabancı Not
                      Defteri varken çalışmayı reddediyor (ADR-0137).
                      test_operator_allowlists (16) + test_operator_process_service (24) +
                      korpus 53 vaka + C# 42/42; 6 mutasyon kırmızı; matris 86-88 bayat
                      satırları düzeltildi (çağıran M19'dan beri vardı).
```

```
BATCH_ID            : B31
NAME                : Araştırma kontrolü ve tarayıcı boşlukları
REQUIREMENT_IDS     : 172, 173, 180, 181, 192, 198, 199, 200, 201, 202, 203, 204, 207, 209, 210
GOAL                : Araştırma iptal/duraklat/devam edilebilsin, DEEP modu açıkça seçilsin,
                      `uploads` yalan duyurusu kapansın, indirme sınırlı olsun.
DEPENDENCIES        : B06 (yetim süpürgesi), B26
AFFECTED_SUBSYSTEMS : Research, Browser, Web
EXPECTED_FILES      : services/api/app/research/, app/browser/, apps/web/
RISK                : low
OWNER_ACTION        : 172/173 sahibin Chrome'u için mahremiyet kararı — ADR-0113 korunur
TEST_PLAN           : iptal edilen koşunun gerçekten durduğu; duyurulan her tarayıcı bayrağının
                      karşılığı olan bir işlemi olduğu (yalan duyuru testi); indirme boyut sınırı
REAL_PROOF_REQUIRED : PROVEN_REAL — üretimde sesle iptal edilen bir araştırma durdu; bir DEEP
                      koşusu tamamlandı
ROLLBACK_PLAN       : yeni kontroller bayrakla kapatılır
KAPANIŞ             : commit 5a5d734 · CI 35080550278 yeşil (7/7) · 15/15 DONE (172 sınır KORUNDU ve
                      test altına alındı; sahibin Chrome'unu otonom araştırmaya açmak
                      mahremiyet kararı — checkpoint, bloklamadı)
                      kanıt docs/evidence/b31-research-control-browser-2026-09-14.json
                      Araştırma: duraklat/devam (bayrak, aşama değil; REST + ses + Temporal
                      sinyali, workflow her aşama sınırında bekler, tutulan süre bütçeden
                      düşer), DEEP sesten kendi tavanıyla (sahibin sözündeki mod turn'de),
                      'bir önceki araştırmayı aç', aynı konudaki eski rapora atıf, kalıcı
                      teknik kayıt (migration 0046), sentez/arama yedeği rapor+olay+defter.
                      Tarayıcı: sözleşme v1.5 — browser.upload VAR (yalan duyuru testi),
                      indirme/yükleme tek kapı (HIGH_IMPACT + biçimli authorization_ref)
                      ve 64 MiB sınır; ADR-0113 sınırı testle tutuluyor.
                      REAL_PROOF: ÜRETİMDE sesle iptal/DEEP koşusu Karar 0'ın
                      (READY_FOR_OWNER); yerelde fake Temporal ile sinyaller doğrulandı.
                      Yolda bulunan: korpus id'leri rutin ailesinin r.pause.* ile çakıştı;
                      modelin çıkardığı konu 'kapsamlı'yı düşürüyordu (turn taşıyor);
                      sahte cihazın terminal-ack tekrarı 'yeniden başlatma' testini yanılttı.
                      6 mutasyon kırmızı (ADR-0138).
```

```
BATCH_ID            : B32
NAME                : Belge arama, OCR ve yineleme
REQUIREMENT_IDS     : 139, 140, 141, 142, 148, 150, 151, 152, 169, 496
GOAL                : Görsel ve arşiv içeriği okunabilsin, OCR çalışsın, yinelenen dosyalar
                      görünsün, iki belge karşılaştırılabilsin.
DEPENDENCIES        : B03 (klasör yolu), B31
AFFECTED_SUBSYSTEMS : Documents, Device, Creative
EXPECTED_FILES      : devices/windows-agent, services/api/app/files/
RISK                : low
OWNER_ACTION        : OCR motoru seçimi — checkpoint (yerel motor tercih edilir)
TEST_PLAN           : oracle fikstür deseninin yeni formatlara uygulandığı; OCR çıktısının
                      taahhüt edilmiş bir referansla karşılaştırıldığı
REAL_PROOF_REQUIRED : PROVEN_REAL — üretimde bir görselden metin çıkarıldı
ROLLBACK_PLAN       : format bazında geri alınır
KAPANIŞ             : commit 5a5d734 · CI 35080550278 yeşil (7/7) · 10/10 DONE (OCR motoru: yerel
                      Windows.Media.Ocr — checkpoint kaydedildi, bloklamadı)
                      kanıt docs/evidence/b32-documents-ocr-dedup-2026-09-15.json
                      Cihaz: `image`/`archive` türleri; görsel başlıkları (WPF), OCR
                      (PowerShell WinRT konağı, sahibin dil paketleri, tr önce; paket
                      yoksa dependency_unavailable), arşiv merkezi dizini (asla
                      çıkarılmaz), `file.trash` (Recycle Bin, asla kalıcı; aile 7→8).
                      Bulut: document.preview / find_text (dizin üstünde tam metin) /
                      duplicates (sha256 ile teklif) / dedup (yalnız duyulan teklif),
                      görselde 'şu yazıyor', arşivde 'N öğe'; executive compare hedefleri
                      kullanıyor; 'doküman' belge adı. Fikstür: metin.png (oracle: OCR
                      metni taahhütlü), arsiv.zip, yedek/veri-kopya.csv.
                      REAL_PROOF: bu masaüstünde gerçek — Windows OCR 'Merhaba Dünya 1234'
                      birebir (tr, ~15 ms), arşiv dizini, lab dosyası Recycle Bin'e gitti;
                      ÜRETİMDE bir görselden metin Karar 0'ın (READY_FOR_OWNER).
                      Yolda bulunan: PowerShell çıktısı konsol kod sayfasında 'ü'yü
                      bozdu (UTF-8 zorlandı); C# 'is not A and B' kalıbı; oracle
                      'contains' bloğu; FileFetch testi file.fetch'i son sanıyordu.
                      6 mutasyon kırmızı (ADR-0139).
```

```
BATCH_ID            : B33
NAME                : Yerel fabrika yaşam döngüsü
REQUIREMENT_IDS     : 456, 457, 462, 463, 464, 465, 466, 468, 469, 470, 471, 472, 473
GOAL                : Üretilen yerel uygulama çalıştırılabilsin, doğrulanabilsin, kurulabilsin,
                      düzeltilebilsin; paketleme üretimden erişilebilir olsun.
DEPENDENCIES        : B03 (467), B29 (UIA)
AFFECTED_SUBSYSTEMS : Native Factory, Device
EXPECTED_FILES      : services/api/app/nativefactory/, devices/windows-agent
RISK                : medium
OWNER_ACTION        : imzalama sertifikası kararı (472, 473) — checkpoint
TEST_PLAN           : ölü araçların gerçek arka uca bağlandığı; artefakt indirmesinin Linux'ta
                      410 vermediği (cihaz üzerinden); UIA doğrulamasının gerçek pencerede koştuğu
REAL_PROOF_REQUIRED : PROVEN_REAL — 26.16'da üretilen uygulama kuruldu, çalıştırıldı, arayüzü
                      UIA ile doğrulandı, kaldırıldı
ROLLBACK_PLAN       : araç bazında geri alınır; kurulum kaldırma yolu her zaman hazır
KAPANIŞ             : commit 5a5d734 · CI 35080550278 yeşil (7/7) · 12/13 DONE (473 PARTIAL —
                      READY_FOR_OWNER: sertifika kararı; politika modu tanır, imzasız üretir)
                      kanıt docs/evidence/b33-native-lifecycle-2026-09-15.json
                      Cihaz: projects ailesi 5→9 — `project.package` (portable zip /
                      makeappx MSIX, hep `signed:false`), `project.install` (Başlat menüsü
                      kısayolu IShellLinkW ile + installed.json), `project.uninstall`
                      (kısayol+kayıt gider, derleme kalır), `project.artifact` (32 KiB
                      base64 parça, sha256). Bulut: ölü `native.install/launch/fix`
                      cihaza bağlandı; yeni `native.verify` (26.15 akışı kod olarak:
                      aç → NoteInput/AddButton/StatusText → kapat → yeniden aç → sayı →
                      günlük), `native.log`, `native.uninstall` (CRITICAL, ACTING),
                      `native.update` (yeni sürüm satırı + kurulum); portable/MSIX artık
                      cihazda derlenir ve paketlenir; artefakt yolu cihazdan çeker (410
                      yalnız cihazda da yoksa); `native_signing_mode` politikası (472).
                      Router: 5 yaşam döngüsü niyeti yerel derleme odağıyla; açma
                      yerel bir sözcük ister ('masaüstü/Windows uygulamasını aç',
                      'EXE'yi çalıştır', 'programı başlat'); çıplak 'uygulamayı aç'
                      her zaman M23'ün.
                      REAL_PROOF: cihaz laboratuvarı bu masaüstünde gerçek — zip, gerçek
                      .lnk yazıldı/silindi, parça okuma hash'i tuttu; ÜRETİMDE 26.16
                      (kur/aç/UIA/kaldır) Karar 0'ın (READY_FOR_OWNER).
                      Yolda bulunan: WScript.Shell 'Notlarım.lnk'i kaydedemedi (Unicode
                      IShellLinkW); shell, hedef PE'yi okur — rastgele 'MZ' yer tutucu
                      E_FAIL verdi (test gerçek exe kopyalar); 'günlüğünü' k→ğ kök;
                      'uygulam' foreign listesi yaşam döngüsü fiillerini yutuyordu;
                      ilk kapı: çıplak 'uygulamayı aç' M28 sözleşmesince M23'ün kaldı
                      (açma yerel sözcük ister), artefakt yolu sahibe istisna metni
                      veriyordu (log_and_detail), yüklü kapıda tarayıcı işçisinin
                      sağlıklı YEDEĞİ 3×100 ms ping kaçırdı diye öldürüldü (2 s başlangıç
                      hoşgörüsü, hiç cevap vermeyen yine ölür) ve ikinci kapıda istek
                      ölmekte olan işçiye verildi (kill istendiği anda Alive=false).
                      7 mutasyon kırmızı (ADR-0140).
```

### FAZ C — P2 GENİŞLETME (B34–B52)

> Bu faz yalnızca FAZ A ve FAZ B kapandıktan sonra başlar.

```
B34  Yönetilen dosya mutasyonu           IDs: 153–167, 170, 674          dep: B28, B05   risk: high    owner: onay politikası
     GOAL: Sahibin dosyaları geri alma günlüğü ve tur bazlı onayla güvenle değiştirilebilsin.
     PROOF: PROVEN_REAL — bir dosya değiştirildi, hash'i kaydedildi, geri alındı, orijinali döndü.
     ROLLBACK: mutasyon yüzeyi tek bayrakla kapanır; undo journal her zaman ileri uyumlu.
     KAPANIŞ: commit 5a5d734 · CI 35080550278 yeşil (7/7) · 17/17 DONE (kalıcı silme politikasının şekli
              READY_FOR_OWNER: hiçbir araç kalıcı silmez; silme = Recycle Bin + yedek)
              kanıt docs/evidence/b34-managed-file-mutation-2026-09-15.json
              Cihaz: documents ailesi 8→14 — file.write/append/rename/move/copy/restore
              (+ file.trash {backup}); her mutasyon önce `.pagentos-undo` deposuna yedek
              (sidecar + sha256), geçici dosya + tek yeniden adlandırma, cevapta önce/sonra
              kayıt ve hash; yalnız metin türleri yazılır. Bulut: `file_mutations` günlüğü
              (göç 0047), MutationService (öneri → kapı → uygulama → geri okuma → ters plan),
              11 sesli araç (write/append/edit/rename/move/copy/delete/apply/discard/undo/
              versions), risk politikası (yeni dosya/ekleme hemen; düzenleme/ad/kopya hassas;
              taşıma/silme kritik → 'Uygula.'/'Kaydet.' ya da panel onayı, mail taslağıyla
              aynı okunma+onay kapısı), tek bayrak `documents_mutation_enabled`, REST
              /v1/documents/mutations (+pending/confirm/discard/undo), Kokpit Belgeler
              panelinde bekleyen değişiklikler ve aynı Onayla/Vazgeç çifti (28. aile).
              REAL_PROOF: cihaz laboratuvarı bu masaüstünde gerçek — dosya atomik yazıldı,
              yedeği hash'iyle depoya gitti, geri yükleme orijinali (hash eşit) getirdi;
              ad/taşıma/kopya/çöp kutusu+geri gerçek dosyada. ÜRETİMDE sesle düzenleme +
              geri alma Karar 0'ın (READY_FOR_OWNER).
              Yolda bulunan: relay 'text' adlı argümanı reddediyor ('content'); find/replace
              tur kaydına kopyalanmadan araca ulaşmıyordu; harness tablo listesi; sahte
              masaüstü katmanı testler arası sızdı; 'Bütçe'/'bütçe'/'butce' eşleşmesi;
              çıplak adın klasörü; REST onayı bulut cihaz kapısını değil çalışma zamanının
              canlı kapısını okumalıydı; 28. sessiz aile; ilk kapı: 'yeni dosya aç'ın
              'aç'ı operatörün ve artefakt ailesinin 'dosyayı aç'ını çalıyordu (oluştur/
              yarat kaldı). 7 mutasyon kırmızı (ADR-0141).

B35  SelfDev'in bağlanması ve güvenlik    IDs: 581, 583, 585, 589, 598, 600, 601, 603, 608, 609, 615, 618–623, 680
     dep: B02, B10, B24   risk: high   owner: aday onayı (db9ed85 dahil)
     GOAL: Fırsat→kusur köprüsü, çalıştırıcı, güvenlik incelemesi, gölge koşu ve sahip onayı
           yüzeyi. **Otonom yüksek riskli terfi YOK (624 korunur).**
     PROOF: PROVEN_REAL — ürün yüzeyinden başlatılan bir koşu aday üretti, güvenlik incelemesinden
            geçti, gölgede çalıştı, sahip onayı beklemede kaldı.
     ROLLBACK: çalıştırıcı durdurulur; worktree'ler korunur (silinmez).
     KAPANIŞ: commit 5a5d734 · CI 35080550278 yeşil (7/7) · 18/18 DONE (üretim turu ve db9ed85'in kaderi
              READY_FOR_OWNER) — kanıt docs/evidence/b35-selfdev-wiring-2026-09-15.json
              Bulut: `selfdev_defects` kuyruğu (göç 0048; ses/REST/köprü/CI kaynaklı),
              SelfDevService (intake → claim → start → finish → approve/reject; günlük token,
              disk tabanı, paralel sınır, claim TTL — her ret adıyla), fırsat→kusur köprüsü
              (terfi sınıfı taşınır, koşu bitince korunur), zorunlu güvenlik incelemesi
              (`Grant.SECURITY_REVIEW_CANDIDATE` tüketicisi; gizli anahtar/tehlikeli çağrı/
              korunan yol/yeni ağ çıkışı/test silme — yol+satır), worktree içinde tam kapı
              (kırmızı → düzeltme döngüsü), gerçek alt süreç gölge koşu (loopback port,
              yoklama, canlıyla karşılaştırma), sahibin bayrağıyla CI push, CI kırmızısı →
              sınırlı tek takip kusuru, REST /v1/selfdev (defects, pending, status, approve/
              reject, worker/claim, start, finish, ci), `python -m app.selfdev worker`,
              3 sesli araç (selfdev.defect/feature/status) + 3 intent, /selfdev sayfasında
              onay bekleyen adaylar paneli (Onayla — kaydet (canlıya almaz) / Vazgeç).
              624 KORUNDU: onay bir karardır, hiçbir yol canlıya almaz; defter `promoted:false`.
              Yolda bulunan: motorun `reset`'i `checkout -- .` idi ve diff yamayı stage
              ediyordu — ikinci deneme regresyonu düzeltilmiş ağaca karşı yargılıyordu
              (hard reset); "özelliği" k→ğ; ölü evrim koruması kaldırıldı (sıra korur);
              matris hücresinde '|' satırı bölüyor. 9 mutasyon kırmızı (ADR-0142).

B36  Genesis ön kapısı                    IDs: 561–565, 569–580           dep: B35      risk: medium  owner: onay akışı
     GOAL: Katalog kaydı, talep rotası, güvenlik kapısı ve sahip onayı — 577 yalnız 579'dan sonra.
     PROOF: PROVEN_REAL — üretimde bir yetenek talebi katalogdan adaptöre ve kullanıma ulaştı.
     ROLLBACK: yetenek devre dışı bırakılır (571/572 sürümleme ile).
     KAPANIŞ: commit 5a5d734 · CI 35080550278 yeşil (7/7) · 17/17 DONE (üretim turu READY_FOR_OWNER) —
              kanıt docs/evidence/b36-genesis-front-door-2026-09-15.json
              Bulut: `genesis_catalogue` (göç 0049) + CatalogueStore (kayıt → bellek içi
              katalog anında ve açılışta yeniden kurulur; discover öneri, disable), talep
              rotası POST /v1/genesis/runs (url kataloğdan), ana makine kuralı sahibin
              varlık kaydından (loopback kuralları aynen), güvenlik kapısı `_build` ile
              `_test` arasında ZORUNLU (B35 incelemesi + import/yabancı ana makine/sistem
              erişimi; `security_refused`), model seam yalnız bayrakla ve yalnız modül
              dosyası (aynı testler+kapı; provenance), model yazımlı her aday sahibi bekler
              (kod onayı mutasyon yetkisi değildir), sürümleme (new_version → 0.1.1),
              rollback, deactivate/activate (registry'nin tek yolu), use; /selfdev'de
              kayıtlı arayüzler paneli. 575/578 ölçüm düzeltmesi.
              Yolda bulunan: registry rollback 'zaten güncel' için erken dönüyordu (yeniden
              etkinleştirme imkânsızdı); rota `str(exc)` ile cevap veriyordu (sahip dili
              testi yakaladı); matris hücresinde '|'. 8 mutasyon kırmızı (ADR-0143).

B37  Anlamsal bellek ve bellek arayüzü    IDs: 51, 53, 54, 57–60, 149     dep: B18, B24  risk: medium  owner: gömme sağlayıcısı
     GOAL: Gerçek gömme, sağlayıcı seçimi, yeniden indeksleme ve sahibin belleğini yönetebildiği arayüz.
     PROOF: PROVEN_REAL — yeniden indeksleme sonrası anlamsal bir sorgu doğru kaydı getirdi.
     ROLLBACK: deterministic-ngram'a dönüş (52 korunur).
     KAPANIŞ: commit 5a5d734 · CI 35080550278 yeşil (7/7) · 8/8 DONE (gerçek sağlayıcıyla üretim turu
              READY_FOR_OWNER: anahtar sahibin) — kanıt docs/evidence/b37-semantic-memory-2026-09-15.json
              Bulut: `OpenAIEmbedder` (aynı Embedder protokolü, indeks genişliği 256,
              anahtar asla hata metninde), `build_embedder` seçimi (deterministic/openai/auto)
              + EmbedderReport (sağlık ve /v1/memory/embedding 'semantic' ve düşüş nedeni),
              `embedding_coverage` + `reindex_missing` (yalnız eksikler; ikinci geçiş 0) +
              POST /v1/memory/reindex, `unpin_memory` + POST /{id}/unpin, belgelerde
              `top_k(embedder=)` yeniden sıralama ve GET /v1/documents/search (bileşenli).
              Web: /memory satırlarında Sabitle/Kaldır, Unut (iki adım), Düzelt (satır içi →
              supersede); 'Anlamsal indeks' bölümü + iki yeniden indeksleme düğmesi.
              Yolda bulunan: sayfa sunucuda sahip kapısının arkasında (test kaynağı okur);
              `ScoredBlock` dondurulmuş (yeniden kurulur). 7 mutasyon kırmızı (ADR-0144).

B38  Genel yürütme planlayıcısı           IDs: 536–538, 544, 546, 549–557 dep: B10, B35  risk: medium  owner: model bütçesi
     GOAL: Üç şablonun ötesinde model destekli planlama; paralel, koşullu ve döngü adımları;
           ön/son koşullar ve gerçek telafi.
     PROOF: PROVEN_REAL — şablonsuz bir istek uçtan uca planlandı ve dürüst durum raporladı.
     ROLLBACK: planlayıcı şablon moduna döner (422 ile dürüst ret korunur).
     KAPANIŞ: commit 5a5d734 · CI 35080550278 yeşil (7/7) · 15/16 DONE, 549 BLOCKED_PROVIDER (B45) —
              kanıt docs/evidence/b38-executive-planner-2026-09-15.json
              Bulut: spec'e `step_failed` / `step_verified` / `owner_approval` ön koşulları,
              `Repeat(max_rounds ≤ 3)`, `Step.rationale`, `TaskGraph.planner` (rule/model/owner),
              tür başına varsayılan zaman aşımı; doğrulayıcı yeni kontrolleri okur (DAG,
              min'siz döngü, bilinmeyen planlayıcı); aktivite ön koşullara satırlarla karar
              verir, adımı `postcondition.min`'e ulaşana dek sınırlı döngüde yeniden çalıştırır,
              `_mark_awaiting_approval` satıra yazar; iş akışı `owner_approval` adımını park
              eder (diğer hazır adımlar yürür), `approve_step` sinyali + `approvals_json`;
              `ModelExecutivePlanner` + `AnthropicPlannerModel` (tool-use, sözlük profillerden)
              + `CompositeExecutivePlanner` (kural önce, model yalnız bayrakla); POST
              /v1/executive/runs {graph} sahibin kendi grafı (15/15 tür erişilebilir), GET
              /runs/{id}/plan gerekçelerle, POST /runs/{id}/approve; araştırma şekli belge ve
              sunumu paralel kurar; göç 0050 (approvals_json, awaiting_step, repeat_json).
              Web: Onayla çipi (yalnız bekleyen satırda), `executiveClient.approve`.
              Yolda bulunan: bilinmeyen adıma referans doğrulayıcıda literal sayılır (tasarım;
              test gerçek DAG ihlaline çevrildi); wait_condition lambda'sı döngü değişkenini
              default-arg ile bağlar. 9 mutasyon kırmızı (ADR-0145). Model bütçesi
              READY_FOR_OWNER (checkpoint 15).

B39  Operatör özerklik döngüsü            IDs: 106, 112–115, 123–130      dep: B29, B38  risk: high    owner: no
     GOAL: GÖZLE→KARAR→UYGULA→DOĞRULA→YENİDEN PLANLA döngüsü Temporal içinde; karma ve çok
           adımlı işler; duraklat/iptal ve "önce göster" modu.
     PROOF: PROVEN_REAL — tarayıcı+masaüstü karma bir iş baştan sona doğrulanarak tamamlandı.
     ROLLBACK: döngü bayrakla kapatılır, sabit planlara dönülür.
     KAPANIŞ: commit 5a5d734 · CI 35080550278 yeşil (7/7) · 13/13 DONE (masaüstü laboratuvar ölçümü ve
              üretim turu READY_FOR_OWNER) — kanıt docs/evidence/b39-operator-autonomy-2026-09-15.json
              Bulut: `app/operator/mission.py` (GÖZLE→KARAR→UYGULA→DOĞRULA→YENİDEN PLANLA:
              her tur taze gözlem, plan karar fonksiyonlarından, eylem aynı cihaz portunda,
              başarısızlık sınıfı → beyan edilmiş strateji: yeniden dene ≤2 / yeniden gözle ≤2 /
              görsel basamak / sahibe; tur sınırı 6), `plan_mission` (bağlaçla bölünen cümle →
              9 adım türü; 'önce göster' = önizleme), `VisionProvider.locate` + `visual_click`,
              adaptörler WORD/EXCEL/VSCODE, planlar open_settings/explorer_open/ide_open_file/
              office_type/browser_navigate, `OperatorStep.payload_from` (gözlenen pencere kimliği
              sonraki adıma), `operator_missions` (göç 0051) + MissionService + Temporal
              `OperatorMissionWorkflow` (approve/pause/resume/cancel sinyalleri) + REST
              /v1/operator/missions + sesli `operator.mission` (start/approve/pause/resume/
              cancel/status) + MISSION_START/APPROVE/PAUSE/RESUME niyetleri (mission_state ile
              kapılı; 'Dur' ve 'Ne yapıyorsun?' göreve devreder); `settings` uygulaması iki
              taraflı izin listesinde (C# sözleşme testi 111 yeşil).
              Yolda bulunan: 'İndirilenler klasöründe … ara' belge ailesinden çalınıyordu (korpus
              doc.search.5; klasör segmenti yalnız AÇ ister); görev aracı operatörün kendi
              kaydedicisinden kaydedilir (yetenek kümesi = kayıtlı araçlar testi); UWP penceresi
              görüntüyle değil başlıkla tanınır. 11 mutasyon kırmızı (ADR-0146).

B40  App Factory genelleştirme            IDs: 422–439                    dep: B03, B35  risk: high    owner: model bütçesi
     GOAL: Model destekli gerçek kod üretimi, planlama, test üretimi, lint ve güvenlik taraması.
     PROOF: PROVEN_REAL — serbest bir istekten çok dosyalı, testleri geçen bir uygulama üretildi.
     ROLLBACK: şablon moduna dönüş (417–421 korunur).
     KAPANIŞ: commit 5a5d734 · CI 35080550278 yeşil (7/7) · 18/18 DONE (model yuvaları ve düzeltme döngüsü
              sahibin bütçesiyle; laboratuvar READY_FOR_OWNER) — kanıt
              docs/evidence/b40-appfactory-generalisation-2026-09-15.json
              Bulut: `requirements.py` (Türkçe cümle → kayıt türleri/alanlar/tipler/giriş/api,
              okunamayan parça söylenir), `planner.py` (mimari + proje planı, gerekçeli),
              `composer.py` (bileşik uygulama: schema/store/auth/server/public/tests/oracle/
              README/manifest - gerçek node altında üretilen testler geçti: 28/28, 14/14,
              21/21), `lint.py`, `appsecurity.py` (selfdev incelemesiyle ortak), `code_model.py`
              (CodeModel: Scripted/Anthropic; ModelAssistedGenerator yalnız bayrakla, yalnız
              yuvalar), `fixloop.py` + `composed_service.run_fix_loop` (teşhis → düzeltme →
              lint+tarama+doğrulama → cihazda yeni sürüm → test; ≤3, aynı hata → dur, model yok →
              analizle dur), `AppSpec.template=composed` + `requirements`, doğrulayıcı `node
              <entry>` (cihazın kuralı), satırda plan/raporlar/oracle/fix/sürüm (göç 0052),
              POST /v1/apps/plan, POST /v1/apps/{id}/fix, sesli `app.create content=` +
              `app.fix` + APP_FACTORY_FIX niyeti, `app_request` (sahibin cümlesi araca).
              Yolda bulunan: gizli-anahtar taraması `password:` anahtarını yakalıyor (tel alanı
              'parola'); node koşusuna cihaz port geçirmez (sunucu manifest portunu sabitler).
              11 mutasyon kırmızı (ADR-0147).

B41  App Factory yaşam döngüsü            IDs: 440–452, 480               dep: B40, B33  risk: medium  owner: no
     GOAL: Derleme, paketleme, çalıştırma, arayüz/kalıcılık doğrulaması, geçmiş ve sonradan
           değiştirme.
     PROOF: PROVEN_REAL — üretilen uygulama çalıştırıldı, arayüzü doğrulandı, sonra bir özellik eklendi.
     ROLLBACK: proje bazında; üretilen kod korunur.
     KAPANIŞ: commit 5a5d734 · CI 35080550278 yeşil (7/7) · 13/13 DONE + 480 DEFERRED (cihaz laboratuvarı ve
              üretim turu READY_FOR_OWNER) — kanıt docs/evidence/b41-appfactory-lifecycle-2026-09-15.json
              Bulut: `lifecycle.py` (oracle'ı cihazın tarayıcısında oynatan `verify_ui` + gerçek
              yeniden başlatma ile kalıcılık, `read_log`, `package_release` zip+release.json,
              `files_from_release` hash doğrulamalı, `build_id_for`, `merge_requirements` /
              `parse_addition`), `lifecycle_service.py` (verify/log/package/launch/history/
              resume/modify), satırda `lifecycle_json` (göç 0053), 7 rota, 7 sesli araç
              (app.verify/log/package/launch/history/resume/modify), `app_project_focused`
              ile kapılı 7 niyet + odaklı 'Bu bug'ı düzelt' → app.fix.
              Yolda bulunan: `assert_class` cihazın find'ı sınıf raporlamaz (laboratuvara
              bırakıldı, kayıtta söylenir). 10 mutasyon kırmızı (ADR-0148).

B42  Artefakt provenans ve yaşam döngüsü  IDs: 393, 394, 398–400, 405–412, 415, 416  dep: B24  risk: low  owner: silme politikası
     GOAL: Dört biçim üretimde kanıtlansın; aktör/kütüphane/manifest provenansı, sürümleme,
           düzenleme, karşılaştırma.
     PROOF: PROVEN_REAL — üretimde xlsx/pptx/csv/json üretildi ve bağımsız doğrulandı.
     ROLLBACK: provenans alanları geriye uyumlu; eski artefaktlar etkilenmez.
     KAPANIŞ: commit 5a5d734 · CI 35080550278 yeşil (7/7) · 16/16 DONE (üretim duman testi ve silme politikası
              seçimi READY_FOR_OWNER, checkpoint 18) — kanıt docs/evidence/b42-artifact-lifecycle-2026-09-15.json
              Bulut: `provenance.py` (Actor ×7 tür, `runtime_provenance` kütüphane sürümleri,
              `source_manifest`), sürüm satırında `provenance_json` (göç 0054) + artık yazılan
              `source_manifest_json`; `lifecycle.py` (apply_edit/edit_artifact = sonraki sürüm,
              clone_artifact soyuyla, delete_artifact confirm/deny/free, compare_specs +
              compare_sentence + diff_specs, register_image_artifact); 6 rota (versions/edit/
              clone/delete/compare/image), 4 sesli araç (artifact.edit/clone/delete/compare),
              `artifact_focused` ile kapılı 4 niyet + 'Evet, sil' onayı, `artifact_delete_policy`.
              Yolda bulunan: uydurma-sayı kuralı ikinci sürümde de geçer (edit'in söylenen
              sayıları spec'e birleşir); görselin kopyası kendi anahtarında (yaratıcı çıktı
              silinmez). 12 mutasyon kırmızı (ADR-0149).

B43  Yaratıcı üretim ve teslim            IDs: 489–495, 498, 500, 502, 504, 506–509, 511, 512  dep: B34, B29  risk: medium  owner: görsel sağlayıcı
     GOAL: Görsel üretimi, katman/PSD/SVG, Paint'in gerçekten sürülmesi ve çıktının sahibin diskine
           teslimi.
     PROOF: PROVEN_REAL — üretilen görsel sahibin diskinde açıldı ve piksel düzeyinde doğrulandı.
     ROLLBACK: sağlayıcı devre dışı; Pillow yolu (481–488) korunur.
     KAPANIŞ: commit 5a5d734 · CI 35080550278 yeşil (7/7) · 17/17 DONE (görsel sağlayıcı hesabı, cihaz
              laboratuvarı ve Adobe lisansı READY_FOR_OWNER, checkpoint 19) — kanıt
              docs/evidence/b43-creative-generation-delivery-2026-09-15.json
              Bulut: `imaging.py` (ImageProvider: local Pillow / openai gpt-image-1 / scripted;
              üretim sağlayıcısız adıyla ret), yeni işlemler generate/object_remove/object_add/
              style/enhance/upscale/semantic_check, `layers.py` (LayeredDocument, PSD oku,
              OpenRaster yaz/oku, SVG gerçek vektör yaz/oku) + `layered` aracı, `drivers.py`
              (Paint/Adobe kısayol sürücüleri), `lifecycle.py` (generate/semantic_check/undo/
              redo/enhance/deliver/drive), satırda history/semantic/artifact/delivery (göç 0055),
              6 rota, 6 sesli araç, 5 niyet + `creative_focused`; teslim B42 görsel artefaktı +
              artefakt açma yolu (`application=mspaint`).
              Yolda bulunan: artefakt açma yolu görsel türünü tanımıyordu (KIND_FORMATS dışı tür
              için geçerli render'a düşüş eklendi); `file.fetch`'in `application` alanı bulutta
              hiç kullanılmıyordu. Geçmiş rotası bilinmeyen kimliği odaktaki
              çalışmaya düşürüyordu (artık 404); değişmeyen fotoğraf 'düzeltildi' diyordu
              (artık 'düzeltecek bir şey bulmadım' der). 12 mutasyon kırmızı (ADR-0150).

B44  3B üretim yolu                       IDs: 520–527                    dep: B42      risk: low     owner: no
     GOAL: Üretimden sahne oluşturma, değiştirme, malzeme/ışık/kamera kontrolü, animasyon, dışa aktarma.
     PROOF: PROVEN_REAL — üretimde ilk gerçek sahne oluşturuldu ve render'ı bağımsız doğrulandı.
     ROLLBACK: sahne yolu bayrakla kapatılır; laboratuvar yolu korunur.
     KAPANIŞ: commit 5a5d734 · CI 35080550278 yeşil (7/7) · 8/8 DONE (üretimde ilk gerçek sahne
              READY_FOR_OWNER) — kanıt docs/evidence/b44-3d-production-path-2026-09-15.json
              Bu makinede gerçek Blender laboratuvarı PASS (4 nesne, compare checked=22); cihaz laboratuvarı gönderilen sürücüyü gerçek iş
              nesnesinde çalıştırıp render'ı ve GLB'yi doğruladı.
              Bulut: POST /v1/scenes + /apply, set_frames/animate/export + ışık rengi + lens
              (Blender), F-curve okuması, `exports_json` (göç 0056), `scene.animate` /
              `scene.export` sesli araçları, SCENE_ANIMATE/SCENE_EXPORT niyetleri. Cihaz:
              `SceneInspection.ReadExports` (yerinde doğrulama, imza, 64 MiB, en çok 2).
              Yolda bulunan: sürücünün mutlak render yolu ve bulutun yanlış render anahtarı —
              iki yarım da yeşildi, çünkü sahte cihaz buluta, cihaz laboratuvarı kendi
              sürücüsüne uyuyordu; laboratuvar betiği de olmayan demo.blend'i arıyordu.
              12 mutasyon kırmızı (ADR-0151).

B45  Mail canlandırma                     IDs: 278, 335, 336, 338–348, 360, 362, 364  dep: B12, B27  risk: medium  owner: MAIL HESABI
     GOAL: Hesap yapılandırıldığında posta okuma/özetleme/taslak/gönderim onay kapısıyla çalışsın;
           `References` kusuru kapansın.
     PROOF: PROVEN_REAL — gerçek bir hesapta yanıt gönderildi ve alıcının zincirinde göründü.
     ROLLBACK: gönderim varsayılan kapalı kalır; okuma tek başına açılabilir.
     KAPANIŞ: commit 5a5d734 · CI 35080550278 yeşil (7/7) · 8/17 DONE, 9 BLOCKED_PROVIDER (kod tam,
              gerçek hesap READY_FOR_OWNER) — kanıt docs/evidence/b45-mail-revival-2026-09-15.json
              References kusuru kapandı (gönderilen yanıt truth.json'un zincirini taşır);
              gelen kutusu rutin saatte yoklanır; brifingde okunmamış-mail cümlesi;
              ekler listelenir ve cihaza tek kullanımlık jetonla indirilir (göç 0057).
              Yolda bulunan: fikstür eklerinin baytı yoktu (boyut iddiası sınanamazdı);
              liste ile çıkarım ayrı koşullarla sayılsaydı '2. eki kaydet' yanlış dosyayı
              indirirdi — tek yüklem; cihazın jetonlu GET'i sahip kimliği taşımaz, açık
              yüzeyler listesine bilinçli eklendi.
              12/12 mutasyon kırmızı (ADR-0152).
              PROVEN_REAL (gerçek hesapta yanıt alıcının zincirinde) READY_FOR_OWNER.

B46  Takvim canlandırma                   IDs: 277, 337, 349–359, 361, 363, 365, 366  dep: B45  risk: medium  owner: TAKVİM HESABI
     GOAL: Takvim okuma/yazma, RRULE, VALARM, hatırlatma, indeks ve eşitleme.
     PROOF: PROVEN_REAL — gerçek takvimde tekrarlayan ve hatırlatıcılı bir etkinlik oluşturuldu.
     ROLLBACK: yazma varsayılan kapalı; okuma tek başına açılabilir.
     KAPANIŞ: commit 5a5d734 · CI 35080550278 yeşil (7/7) · 10/17 DONE, 6 BLOCKED_PROVIDER (kod tam, gerçek
              hesap READY_FOR_OWNER), 355 DEFERRED (RSVP) — kanıt
              docs/evidence/b46-calendar-revival-2026-09-15.json
              RRULE ve VALARM yazılır ve aynı ayrıştırıcıyla geri okunur; sahibin tekrar ve
              hatırlatma sözcükleri yönlendiriciden öneriye taşınır; indeks okumayla ve saatin
              14 günlük aynasıyla dolar, yukarıda silinen çıkar; hatırlatmalar bir kez,
              sessiz saate takılmadan; iptal sahip politikasıyla (varsayılan red) (göç 0058).
              Yolda bulunan: VALARM içindeki SUMMARY etkinliğin başlığının üstüne yazılıyordu;
              tekrarlayan bir etkinliği ertelemek bütün seriyi tek etkinliğe çevirir ve
              hatırlatıcısını silerdi; '15 dakika önce hatırlat' 15 dakikalık etkinlik
              okunuyordu; gece hatırlatması etkinlik başladıktan sonraya ertelenirdi.
              14/14 mutasyon kırmızı (ADR-0153).
              PROVEN_REAL (gerçek takvimde tekrarlayan ve hatırlatıcılı etkinlik) READY_FOR_OWNER.

B47  Cihaz tarafı ses                     IDs: 239–244, 250–255           dep: B05, B11  risk: high    owner: MAHREMİYET KARARI
     GOAL: Tarayıcısız dinleme, uyandırma sözcüğü, yerel VAD, gizlilik göstergesi, çevrimdışı
           komut kümesi. Ham ses saklanmaz (249 korunur).
     PROOF: PROVEN_REAL — tarayıcı kapalıyken uyandırma sözcüğüyle bir komut tamamlandı.
     ROLLBACK: cihaz ses servisi durdurulur; tarayıcı yolu korunur.
     KAPANIŞ: DURDU — SAHİP KARARI (Karar 7, mahremiyet) · kod yazılmadı · 0/12 · commit yok
              Her satır cihaz tarafı mikrofona (239) bağlı; sürekli açık mikrofon mahremiyet
              kararı ve fiziksel mikrofon değerlendirmesi ister — ikisi de sahibin. Karar paketi
              ADR-0154 (ÖNERİLEN, kabul edilmedi): bas-konuş / yerel uyandırma sözcüğü / sürekli
              dinleme seçenekleri ve her birinin açtığı satırlar. Kanıt:
              docs/evidence/b47-device-voice-owner-decision-2026-09-15.json (cihaz kaynağında
              yakalama yolu taraması). Tarayıcı ses yolu (B05/B11) değişmedi. READY_FOR_OWNER.

B48  Varlık derinliği ve kamera           IDs: 300–303, 307, 308, 310, 312, 313, 320, 326, 327, 330–333, 671  dep: B47  risk: medium  owner: KAMERA KARARI
     GOAL: Cihaz tarafı kamera, RESTING/LIKELY_ASLEEP'in erişilebilir olması, "uyurken ekranı
           kapat" politikasının gerçekten tetiklenebilmesi. Ham görüntü saklanmaz (328, 329 korunur).
     PROOF: PROVEN_REAL — duruş sinyali üretildi ve uyku politikası bir kez tetiklendi.
     ROLLBACK: kamera sağlayıcısı kapatılır; girdi tabanlı varlık (311) korunur.
     KAPANIŞ: commit 5a5d734 · CI 35080550278 yeşil (7/7) · 8/18 DONE, 331 PARTIAL, 9 satır Karar 8'de
              (cihaz kamerası: 300, 307, 308, 326, 327, 333, 671; 320 donanım yargısı) — kanıt
              docs/evidence/b48-presence-depth-2026-09-15.json
              Kamerasız yapılabilen yapıldı: gözlem ağırlığı zamanla azalır, uyku eşiği sahibin
              sessiz saatine bağlı, varlık geçmişi rotası, panelde dört ambient anahtarı, kapanan
              kamera sekmesi kanıt olarak kaydedilir (rıza değişmez), sekmede kamera kapalıyken
              açık uyarı. Yolda bulunan: 'uyurken ekranı kapat' politikası LIKELY_ASLEEP'in
              yanında taze kamera algısı da istiyor - kamera kararı olmadan 333 hiçbir sinyalle
              tetiklenemez; bu yüzden girdi boşluğundan uyku ÇIKARILMADI. LIKELY_ASLEEP bir okuma
              sonra RESTING'e geri düşüyordu (eşik yeni durumun saatinden ölçülüyordu) - uyku artık
              kanıt sürdükçe kalır; '25:00' geçerli saat sayılıyordu; kapanan-sekme rotası ilk
              testte çöktü (source_ref); mutlak yaşlanma çoğunluk oyunu UNKNOWN'a itiyordu; B45
              son BROKEN satırını kapatınca rozet tipi derlenmez oldu (web tsc hızlı kapıda yok).
              13/13 mutasyon kırmızı (ADR-0155). PROVEN_REAL (duruş sinyali + uyku
              politikası) Karar 8 ile READY_FOR_OWNER.

B49  Android fabrikası ve iOS beyanı      IDs: 474–479                    dep: B41      risk: medium  owner: Android SDK / fiziksel cihaz
     GOAL: Android proje/APK/AAB üretimi ve testi; macOS yokken iOS'un AÇIKÇA desteklenmediğinin
           beyanı (Unity lisans reddi deseni örnek alınır).
     PROOF: PROVEN_REAL (APK emülatörde açıldı) / 479 için PROVEN_AUTOMATED (açık ret testi).
     ROLLBACK: Android yolu bayrakla kapatılır.
     KAPANIŞ: commit d5a705d · CI 35084702310 yeşil (7/7) · 474 + 479 DONE, 475-478 BLOCKED_PROVIDER — kanıt
              docs/evidence/b49-android-factory-ios-refusal-2026-09-15.json
              474: `counter-mobile` gerçek Gradle Kotlin projesi üretir (XML olarak ayrıştırılıp
              denetlenir, diske yazılır). 479: iOS adıyla reddedilir, katalogda Türkçe mesajı var,
              rota nedenini söyler. 475-478: bu makinede JDK/Gradle/cmdline-tools/AVD yok (ölçüldü);
              JDK indirmesi sahip izni ister, fiziksel cihaz sahibin — READY_FOR_OWNER (madde 33).
              Yolda bulunan: `platform_unreachable` hata dili korumasına görünmüyordu (Final
              ek açıklamalı sabit); M28 spesifikasyonu kodda hiç olmayan `ios_project` hedefini
              listeliyordu. 8/8 mutasyon kırmızı (ADR-0156).
              B49 çalışma zamanı niteliği (2026-09-16, ADR-0160/0161):
              475/476 PARTIAL + PROVEN_REAL — fabrika APK/AAB'yi kayıtlı cihazda üç sabit
              Gradle biçimiyle derler; cihaz yarısı bu makinede gerçek derlemeyle kanıtlandı
              (AndroidBuildTests); canlı cihaza dağıtım READY_FOR_OWNER. 477 PARTIAL + PROVEN_REAL
              (scripts/qualify-android-factory.ps1, 8 kapı; docs/evidence/b49-android-runtime-qualification-2026-09-16.json). 478 fiziksel cihaz bekler.
              Yolda bulunan: JDK varken dotnet'e build.gradle.kts gidiyordu; cihaz komutları
              30 sn sürede kesiliyordu; başarısız derleme teste geçiyordu — üçü düzeltildi.

B50  Unity ve Unreal                      IDs: 530–534                    dep: B44      risk: low     owner: UNITY LİSANSI
     GOAL: Lisans geldiğinde Unity proje/sahne/derleme/test; Unreal isteğe bağlı kalır.
     PROOF: BLOCKED — lisans yokken dürüst ret (529 deseni) korunur.
     ROLLBACK: yok (salt ekleme).
     KAPANIŞ: BLOCKED (tasarım gereği, Karar 12: Unity lisansı) · kod yok · commit yok
              Dürüst ret koşularak kanıtlandı: 5 Unity ret testi, `5 passed, 28 deselected, 1 warning in 3.42s`.
              530-533 BLOCKED_PROVIDER, 534 DEFERRED kalır; lisans geldiğinde ADR-0157'deki
              adımlar açılır. READY_FOR_OWNER.

B51  Niyet zekâsı                         IDs: 740, 742–748               dep: B26, B27, B18  risk: medium  owner: model bütçesi
     GOAL: Model tabanlı yönlendirme, araç kaydından çözüm, güven skoru, belirsizlik sorusu,
           referans çözümü, ASR gürültüsüne dayanıklılık. **Kritik fiiller deterministik kalır (741).**
     PROOF: PROVEN_AUTOMATED — genişletilmiş külliyatta kapsam artar VE misroute 0 kalır.
     ROLLBACK: model yönlendirici kapatılır, deterministik tablo tek başına çalışır.
     KAPANIŞ: commit 507dc89 · CI 35094060177 yeşil (7/7) · 740, 742, 743 DONE, 744, 745, 746, 747, 748 PARTIAL — kanıt
              docs/evidence/b51-intent-intelligence-2026-09-15.json
              Deterministik tablo (741) değişmedi; model yönlendirici onun ARKASINDA, bayrakla ve
              yalnızca güvenli adaylar arasından seçer. Güven skoru, belirsizlik sorusu ve 'bunu'
              referansı tur kaydına yazılır. 744 sesli soru bayrak arkasında, 745 referansını henüz
              araç okumuyor. ASR: 150 varyant, 0 yanlış, 6 kayıp.
              Yolda bulunan: tur kaydında zaten bir `reference` anahtarı vardı (araştırma);
              yeni alan `deictic_reference` adını aldı. Tur kaydı alanını bir araç okumadıkça
              kimse görmez — 745 bu yüzden PARTIAL. 9/9 mutasyon kırmızı (ADR-0158).

B52  Belge formatı genişletme             IDs: 143–146                    dep: B32      risk: low     owner: no
     GOAL: EPUB, RTF, ODT ve eski Office formatları okunsun.
     PROOF: PROVEN_AUTOMATED — oracle fikstürleriyle her format.
     ROLLBACK: format bazında geri alınır.
     KAPANIŞ: commit c57799c · CI 35098762101 yeşil (7/7) · 4/4 DONE — kanıt
              docs/evidence/b52-document-formats-2026-09-15.json
              EPUB/ODT (zip + XML, paket yok), RTF (sınırlı kontrol sözcüğü), DOC/XLS/PPT (sınırlı OLE +
              BIFF8 formül çözücü). Fikstürler bu makinedeki LibreOffice'in kaydedilmiş DOCX/XLSX/PPTX
              fikstürlerinden ürettiği GERÇEK dosyalar; beklenen çıkarımlar kaynak kâhinden türetildi
              (çıkarıcıdan değil) ve her format farkı adıyla yazıldı.
              Yolda bulunan: LibreOffice ilk profil kurulumunda ODT dışa aktarırken çöküyordu (hazır
              profille çalıştı); EPUB dışa aktarımı başlıkları stilli <p> yazıyor; RTF başlığı ANSI
              kopyada kayıplı, \ud Unicode alternatifi okunmalı; fikstür üretecini yeniden koşmak B32'nin
              izlenmeyen fikstürlerini silerdi (koşulmadı, yalnız eklendi).
              10/10 mutasyon kırmızı (ADR-0159).
```

---

## 8. YENİDEN YAZILMAMASI GEREKEN MEVCUT ÖZELLİKLER (191 `DONE` satırın omurgası)

Bunlara **dokunulmaz**; yalnızca çevrelerine bağlantı eklenir:

| Alan | Kimlikler | Neden korunur |
|---|---|---|
| Artefakt fabrikası | 391, 392, 395–397, 401–404, 413 | Bayt-belirlenimli, bağımsız geri okuma, formül doğrulaması |
| Yedek/geri yükleme | 626–629, 633–641 | Host üzerinde gerçek koşuyla kanıtlı (snapshot 8ad3e923) |
| Alarmın çevrimdışı yolu | 263–266, 268 | Bulut tamamen kapalıyken çalıyor, deterministik saatle testli |
| Tarayıcı güvenliği | 185–189 | SSRF/private-network/CAPTCHA politikaları iki yakada zorlanıyor |
| Kimlik katmanı | 656, 657, 661, 662, 669, 670, 672, 673, 676, 677 | Opak jeton, SHA-256, sabit zaman, Tailscale bağı |
| Ortam öncelikleri | 314, 315, 321–325, 328, 329, 334 | Güvenli varsayılanlar doğru kurulmuş |
| Ses çekirdeği | 211–214, 232 | 410 fırtına koruması ve barge-in sıralaması testli |
| Blender hattı | 513–519 | sha256-sabitlenmiş sürücü, iki kez doğrulanan render |
| SelfDev motoru | 586, 587, 590–597, 599, 604–607, 616, 617, 624 | Gerçek dal/worktree/pytest; politika sınırında duruyor |
| Sürüm mekaniği | 610–613 | 3 üretim sürümü, 16 geri alma, LKG çalışıyor |
| Belge okuyucuları | 131–138 | Gerçek ayrıştırıcılar + oracle fikstürleri |
| Yerel fabrika çekirdeği | 453–455, 458–461 | 26.16 PROVEN_REAL, PE kimliğinden verdict |

## 9. UYGULANMIŞ AMA ERİŞİLEMEZ ÖZELLİKLER (en yüksek getiri/maliyet oranı)

| Alan | Kimlikler | Durum |
|---|---|---|
| Bellek düzeltme/sabitleme/unutma/çelişki | 36, 37, 38, 44, 45 | Yazılmış + testli, sesten erişilemez |
| Deneyim motoru | 71, 72, 73 | Kod tam, üretimde hiç koşmadı |
| Rutin tetikleyicileri | 292, 293 | Tanımlı, 7/7 rutin `at` kullanıyor |
| Uyanma selamlaması | 270 (politika) | Her tikte hesaplanıyor, hiç tüketilmiyor |
| Operatör yetenekleri | 82, 84–88, 91–104, 119 | Cihazda gerçek, bulutta çağıran yok (85 yetenekten 40'ı) |
| SelfDev motoru | 581, 589, 600, 601 | Gerçek ve kanıtlı, çalışan sistemden erişilemez |
| Genesis motoru | 566, 567, 568 | Uçtan uca kanıtlı, ön kapısı yok |
| Yürütme adım türleri | 552 | 15 adım türünün 7'sinin gerçek işleyicisi var, hiçbir plan üretmiyor |
| Mail/takvim onay kapısı | 345 | Çok iyi tasarlanmış, sağlayıcı yok |
| Panik anahtarı | 660 | Çalışıyor, arayüzde görünmüyor |
| Artefakt biçimleri | 393, 394, 398, 399 | Testli, üretimde hiç üretilmedi |
| 3B sahne yolu | 521 | Motor kanıtlı, üretimde 0 sahne |

## 10. SAHTE / STUB / PROXY OLAN ŞEYLER

| Ne | Kimlikler | Gerçek durum |
|---|---|---|
| Cihaz sahteleri payload'a bakmıyor (7 adet) | 5 | Dört canlı kusurun kökü |
| C# tarafı fikstür yoksa kendi manifest'ine düşüyor | 5, 421 | Sözleşme testi hiçbir şey test etmiyor |
| `fake` bildirim sağlayıcısı "delivered" diyor | 375, 390 | Kuyruk teslim sanılıyor |
| `ClaudeAppGenerator` koşulsuz hata | 425 | Atıl seam |
| `ClaudeSkillGenerator` yapılandırma sonrası hata | (Genesis/Evolution) | Doğru yapılandıran sahip "yapılandırılmamış" hatası alır |
| `ClaudeExecutivePlanner` `NotImplementedError` | 550 | Atıl seam |
| `NarrationEngine` çağıranı yok, imza uyumsuz | 224 | Ses üretmiyor |
| `native.install/launch/fix` arka ucu yok | 462, 468, 470 | Ölü araçlar |
| Figma jetonu sabit `False`, `creative.design` varsayılanı Figma | 505 | Araç yapısal olarak her zaman başarısız |
| Mail `References` testi gerçek servisi atlıyor | 346 | Kusuru göremiyor |
| Altı PowerShell testi yürütme yerine kaynak-regex'i kullanıyor | 29, 30 | Biri gerçek bir geri alma boşluğunu gizliyor |
| `counts_parsed` okunmuyor | 467 | `passed:null` ile "verified" |
| Selfhealing demo servisi | (kapsam dışı) | `PROVEN_PROXY` kalır |

## 11. OWNER / PROVIDER BLOKERLERİ

| # | Karar | Bloklayan batch | Kritik yol mu |
|---|---|---|---|
| 0 | **Üretim sürümü** — B01'in düzeltilmiş sürüm betiğini canlıya almak | B01 kapanış kanıtı | **Evet, şimdi** — aşağıya bakın |
| 1 | S3 uyumlu ikinci kova + erişim anahtarı | B09 | **Evet** — felaket kurtarmanın tek eksiği |
| 2 | Kurtarma denetçisinin üretime kurulumu (Astra `2561f84`) | B08 | **Evet** |
| 3 | `db9ed85` adayının kaderi (B35 sonrası: karantina koşusu, onaylanacak aday yok; dalı silmek/tutmak sahibin) + B35 üretim turu (sesle atanan kusur → worker → gölge → panelde karar) | B35 kapanışı | Hayır — READY_FOR_OWNER |
| 4 | TTS sağlayıcı kredisi | B15, B21 kanıtı | Kısmi — `PROVEN_PROXY` ile ilerlenebilir |
| 5 | Mail hesabı (IMAP/SMTP) | B45 | Hayır |
| 6 | Takvim hesabı (CalDAV) | B46 | Hayır |
| 7 | Cihaz tarafı ses / sürekli mikrofon (mahremiyet) | B47 | Hayır |
| 8 | Cihaz tarafı kamera (mahremiyet) | B48 | Hayır |
| 9 | Gömme sağlayıcısı (B37 sonrası: seçim `memory_embedding_provider=auto`, anahtar `PAGENTOS_OPENAI_API_KEY`; anahtarsız deterministic-ngram nedeniyle raporlanır) | B37 kapanışı | Hayır — READY_FOR_OWNER |
| 10 | Görsel üretim sağlayıcısı | B43 | Hayır |
| 11 | Android SDK / fiziksel cihaz | B49 | Hayır (P3) |
| 12 | Unity lisansı | B50 | Hayır (P3) |
| 13 | Kod imzalama sertifikası | B33 | Hayır |
| 14 | Kalıcı silme onay politikasının şekli | B05, B34 | Hayır |
| 15 | Yürütme planlayıcı model bütçesi (B38 sonrası: bayrak `executive_model_planner_enabled=false`, anahtar `PAGENTOS_ANTHROPIC_API_KEY`; bayraksız üç kural şekli hizmet eder, dışı 422 ile dürüst ret) | B38 kapanışı | Hayır — READY_FOR_OWNER |
| 16 | Operatör görev laboratuvarı (B39 sonrası: Ayarlar sayfa araması, Gezgin adres çubuğu, Word belge kontrolü, VS Code hızlı açma bu masaüstünde ölçülür; sonra karma bir görev üretimde - Karar 0) | B39 kapanışı | Hayır — READY_FOR_OWNER |
| 17 | App Factory kod modeli bütçesi ve laboratuvarı (B40 sonrası: bayrak `appfactory_model_generation_enabled=false`, anahtar `PAGENTOS_ANTHROPIC_API_KEY`; bayraksız bileşik uygulama deterministik üretilir, düzeltme döngüsü analizde durur; cihazda bileşik bir uygulamanın `project.test` ve tarayıcı oracle'ı) | B40 kapanışı | Hayır — READY_FOR_OWNER |
| 18 | Artefakt üretim duman testi ve silme politikası (B42 sonrası: üretim VM'de bir xlsx/pptx/csv/json üretilip indirilmesi ve sürümünün `provenance_json`'ının okunması; `artifact_delete_policy` confirm/deny/free seçimi — varsayılan confirm) | B42 kapanışı | Hayır — READY_FOR_OWNER |
| 19 | Görsel sağlayıcı ve yaratıcı laboratuvar (B43 sonrası: `creative_image_provider=openai` için `voice_openai_api_key` (DPAPI secret-store) — anahtarsız yerel yol; cihazda 'Paint'te göster' ve `creative.drive` kısayol dizisi; Adobe sürücüleri için lisans + allowlist) | B43 kapanışı | Hayır — READY_FOR_OWNER |

**Kural:** owner action gereken satırlar `READY_FOR_OWNER` işaretlenir, **bağımsız iş durmaz**,
ve soru ancak kritik yola girdiğinde sorulur (Phase 7).

### Karar 0 — üretim sürümü (B01 sonrası, 2026-09-12)

**Neden owner kapısı:** B01 sürüm mekanizmasının kendisini değiştirdi (`pipefail`, borusuz göç,
yeni çıkış kodları 78/82/83, RELEASE.json). Bunu canlıya almak, promosyonu yapan aracı
değiştirerek bir promosyon yapmak demektir — sahibin durma listesindeki "high-risk production
promotion" tanımına giren tek adım.

**Neden düşük gerçek risk:** anahtarlanmadan önceki her başarısızlık eski ağacı geri yüklüyor,
sonraki her başarısızlık geri anahtarlıyor; 97 betik vakası (59 + 38) yeşil; LKG `d86b3d9`
elde; `--preflight` hiçbir şeyi değiştirmeden yeni ağacı doğruluyor.

**Sahip onaylarsa kazanılan kanıt:** `1` ve `631` `PROVEN_REAL`'e çıkar (gerçek host'ta
RELEASE.json, sağlıkta build_id ve şema revizyonu), ve B03'ün üretim kanıtları da aynı
sürümle mümkün hale gelir.

**Sahip onaylamazsa:** B01 bugünkü haliyle kapalı kalır (1 = `PROVEN_AUTOMATED`), sıradaki
batch B02'dir ve B02'nin hiçbir kanıtı üretim gerektirmez.

## 12. ÖNERİLEN İLK 10 BATCH

| Sıra | Batch | Neden burada |
|---|---|---|
| 1 | **B02** CI kapsamı | Sonraki her batch'in regresyon kapısı; 1472 test + 15 PE testi kapısız |
| 2 | **B01** Sürüm güvenliği | Başarısız göç sürümü durduramıyor — en yüksek üretim riski |
| 3 | **B03** Sözleşme eşitliği + 4 canlı kusur | Dört kusurun kökü; bu düzelmeden aynı sınıf tekrar üretilir |
| 4 | **B04** Sır redaksiyonu | Üretim DB parolası açık metin |
| 5 | **B05** Yetki kapısı ve kimlik | Cihaz güveni çağıranın gönderdiği boolean |
| 6 | **B06** Durum gerçeği ve süpürgeler | Sistem kendi durumu hakkında yanlış konuşuyor |
| 7 | **B07** Sınırlı teslim ve döngü izolasyonu | Bir alt tik alarmı sessizce düşürebiliyor |
| 8 | **B08** Yedek/kurtarma sağlığı ve denetçi | Yedek arızasını kimse görmüyor; reconcile timer'ı yok |
| 9 | **B09** Host dışı DR | Host kaybında RPO/RTO sonsuz (owner gate) |
| 10 | **B10** Yürütme dürüstlüğü | "4/4 tamam" derken 3 adım başarısız |

B01 ve B02 sıra olarak yer değiştirmiştir: **B02 önce**, çünkü B01'in düzeltmesinin kalıcılığını
ancak CI kapısı garanti eder.

## 13. PERSONALAGENTOS v1.0'A TAHMİNİ YOL

Madde sayısına göre yüzde **kullanılmaz** (bir güvenlik satırı ile bir altsistem eşit değildir).
Aşağıda **ağırlıklı altsistem olgunluğu** (0–5 ölçek, ağırlık = günlük kullanım + risk):

| Altsistem | Ağırlık | Bugün | v1.0 hedefi | Katkı açığı |
|---|---|---|---|---|
| Güvenilirlik / sürüm / kurtarma | 10 | 3,2 | 4,5 | 13,0 |
| Gerçek/sahte sözleşme eşitliği | 8 | 1,5 | 4,5 | 24,0 |
| Sahibe ulaşılabilirlik | 9 | 1,0 | 4,0 | 27,0 |
| Bellek / kişisel süreklilik | 9 | 1,8 | 4,0 | 19,8 |
| Ses (tarayıcı) | 7 | 3,6 | 4,5 | 6,3 |
| Ses (cihaz tarafı) | 6 | 0,3 | 3,0 | 16,2 |
| Dijital Operatör | 8 | 2,4 | 4,0 | 12,8 |
| Alarm / rutin / sabah | 7 | 2,8 | 4,5 | 11,9 |
| Belge zekâsı | 6 | 2,6 | 4,0 | 8,4 |
| Tarayıcı / araştırma | 6 | 3,9 | 4,5 | 3,6 |
| Artefakt fabrikası | 5 | 4,0 | 4,5 | 2,5 |
| App / Native fabrika | 6 | 2,1 | 3,5 | 8,4 |
| Yaratıcı / 3B | 4 | 2,0 | 3,0 | 4,0 |
| Yürütme özerkliği | 6 | 2,0 | 3,5 | 9,0 |
| SelfDev / Genesis | 6 | 2,3 | 3,5 | 7,2 |
| Güvenlik / kimlik | 9 | 3,4 | 4,5 | 9,9 |
| Web / UX / keşfedilebilirlik | 8 | 1,3 | 4,0 | 21,6 |
| Niyet / anlama | 8 | 1,9 | 4,0 | 16,8 |

**Ağırlıklı olgunluk bugün: 2,32 / 5 (%46).** Hedef 4,08 / 5 (%82).
Kapanması gereken ağırlıklı açığın dağılımı: **FAZ A (B01–B10) %27 · FAZ B (B11–B33) %48 ·
FAZ C (B34–B52) %25.**

Yani: v1.0 olgunluğunun yarısına yakını FAZ B'de (günlük kullanım) kazanılır, ama **FAZ A
olmadan FAZ B'nin kazanımı kalıcı olmaz** — çünkü ölçülen dört canlı kusurun tamamı, yeşil
testlerin altında oluştu.

---

## PHASE 6 — BATCH YÜRÜTME MODELİ (uygulama başladığında)

Her batch istisnasız şu zinciri izler:

```
MEVCUT DURUMU DOĞRULA (git + CI + üretim sağlığı + cihaz)
→ GÜVENLİ WORKTREE OLUŞTUR/KULLAN
→ UYGULA
→ HEDEFLİ TESTLER
→ BAŞARISIZLIK ANALİZİ
→ DÜZELT
→ YENİDEN TEST
→ SÖZLEŞME TESTLERİ (iki yarı birbirini okur)
→ MUTASYON/YANLIŞLAMA (değerli olduğu yerde)
→ TAM KALİTE KAPISI
→ CI
→ GEREKİYORSA GERÇEK CİHAZ/ÇALIŞMA ZAMANI NİTELENDİRMESİ
→ DAYANIKLI KANIT
→ FEATURE_MATRIX GÜNCELLE (status/commit/tests/proof/date)
→ ROADMAP GÜNCELLE
```

Bağlayıcı kurallar:
- **Gönderim başarısı başarı değildir.** Fiziksel cihaz söz konusuysa gözlenmiş makbuz/postcondition şarttır.
- Gerçek sağlayıcı yoksa **dürüstçe sınıflandır** (`PROVIDER_UNAVAILABLE` / `PROVEN_PROXY`), yükseltme yapma.
- Her gerçek kusur bir regresyon testi alır ve test **önce kırmızı** kanıtlanır.
- Yol üstünde bulunan gerçek kusur aynı batch'e alınır ve matrise yeni satır olarak yazılır
  (mevcut numaralar düşürülmez).
- Owner-only sınır varsa `READY_FOR_OWNER` işaretlenir ve **bağımsız işin tamamı yine bitirilir**.
- **Bir batch bitmeden sonraki başlamaz.**

## PHASE 8 — KORUNAN ÜRÜN İLKELERİ (hiçbir batch bunları gevşetemez)

1. Model çıktısı aday girdidir, otorite değildir.
2. Ses kimliği tek başına asla kök kimlik doğrulaması değildir (245, 246, 664).
3. Varsayılan olarak ham kamera arşivi yoktur (328, 329).
4. Varsayılan olarak ham ses arşivi yoktur (249).
5. CAPTCHA aşılmaz (186, 681). DRM aşılmaz (682).
6. Sır loglanmaz (7, 683).
7. Yüksek riskli üretim terfisi sahip/politika kapısında kalır (624, 677).
8. Gerçek cihaz wire sözleşmeleri paylaşılır ve test edilir (5, 421).
9. Sahteler makineden nazik olamaz (5, 390).
10. Erişilebilir yüzeyi olmayan özellik tamamlanmış değildir.
11. Başarısızlığını dürüstçe bildiremeyen özellik tamamlanmış değildir (559).
12. Geri yüklemesi olmayan yedek `PROVEN_REAL` değildir (637).
13. Bağımsız artefakt incelemesi olmayan derleme `VERIFIED` değildir (458, 519).
14. Postcondition doğrulanmamış operatör eylemi başarılı değildir (111).
