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
BATCH_ID            : B17
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
REAL_PROOF_REQUIRED : PROVEN_REAL — bir tercih söylendi, YENİ bir oturumda tekrar söylenmeden
                      uygulandı (zincirin uçtan uca kanıtı)
ROLLBACK_PLAN       : enjeksiyon bayrakla kapatılır; persona eski haline döner
```

```
BATCH_ID            : B18
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
REAL_PROOF_REQUIRED : PROVEN_REAL — üretimde aktiviteden türetilmiş en az bir bellek satırı
ROLLBACK_PLAN       : zamanlayıcı kapatılır; türetilmiş satırlar işaretli olduğu için silinebilir
```

```
BATCH_ID            : B19
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
REAL_PROOF_REQUIRED : PROVEN_REAL — üretimde öz modelde çalışan SHA ve cihaz build'i görünür
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
```

### FAZ C — P2 GENİŞLETME (B34–B52)

> Bu faz yalnızca FAZ A ve FAZ B kapandıktan sonra başlar.

```
B34  Yönetilen dosya mutasyonu           IDs: 153–167, 170, 674          dep: B28, B05   risk: high    owner: onay politikası
     GOAL: Sahibin dosyaları geri alma günlüğü ve tur bazlı onayla güvenle değiştirilebilsin.
     PROOF: PROVEN_REAL — bir dosya değiştirildi, hash'i kaydedildi, geri alındı, orijinali döndü.
     ROLLBACK: mutasyon yüzeyi tek bayrakla kapanır; undo journal her zaman ileri uyumlu.

B35  SelfDev'in bağlanması ve güvenlik    IDs: 581, 583, 585, 589, 598, 600, 601, 603, 608, 609, 615, 618–623, 680
     dep: B02, B10, B24   risk: high   owner: aday onayı (db9ed85 dahil)
     GOAL: Fırsat→kusur köprüsü, çalıştırıcı, güvenlik incelemesi, gölge koşu ve sahip onayı
           yüzeyi. **Otonom yüksek riskli terfi YOK (624 korunur).**
     PROOF: PROVEN_REAL — ürün yüzeyinden başlatılan bir koşu aday üretti, güvenlik incelemesinden
            geçti, gölgede çalıştı, sahip onayı beklemede kaldı.
     ROLLBACK: çalıştırıcı durdurulur; worktree'ler korunur (silinmez).

B36  Genesis ön kapısı                    IDs: 561–565, 569–580           dep: B35      risk: medium  owner: onay akışı
     GOAL: Katalog kaydı, talep rotası, güvenlik kapısı ve sahip onayı — 577 yalnız 579'dan sonra.
     PROOF: PROVEN_REAL — üretimde bir yetenek talebi katalogdan adaptöre ve kullanıma ulaştı.
     ROLLBACK: yetenek devre dışı bırakılır (571/572 sürümleme ile).

B37  Anlamsal bellek ve bellek arayüzü    IDs: 51, 53, 54, 57–60, 149     dep: B18, B24  risk: medium  owner: gömme sağlayıcısı
     GOAL: Gerçek gömme, sağlayıcı seçimi, yeniden indeksleme ve sahibin belleğini yönetebildiği arayüz.
     PROOF: PROVEN_REAL — yeniden indeksleme sonrası anlamsal bir sorgu doğru kaydı getirdi.
     ROLLBACK: deterministic-ngram'a dönüş (52 korunur).

B38  Genel yürütme planlayıcısı           IDs: 536–538, 544, 546, 549–557 dep: B10, B35  risk: medium  owner: model bütçesi
     GOAL: Üç şablonun ötesinde model destekli planlama; paralel, koşullu ve döngü adımları;
           ön/son koşullar ve gerçek telafi.
     PROOF: PROVEN_REAL — şablonsuz bir istek uçtan uca planlandı ve dürüst durum raporladı.
     ROLLBACK: planlayıcı şablon moduna döner (422 ile dürüst ret korunur).

B39  Operatör özerklik döngüsü            IDs: 106, 112–115, 123–130      dep: B29, B38  risk: high    owner: no
     GOAL: GÖZLE→KARAR→UYGULA→DOĞRULA→YENİDEN PLANLA döngüsü Temporal içinde; karma ve çok
           adımlı işler; duraklat/iptal ve "önce göster" modu.
     PROOF: PROVEN_REAL — tarayıcı+masaüstü karma bir iş baştan sona doğrulanarak tamamlandı.
     ROLLBACK: döngü bayrakla kapatılır, sabit planlara dönülür.

B40  App Factory genelleştirme            IDs: 422–439                    dep: B03, B35  risk: high    owner: model bütçesi
     GOAL: Model destekli gerçek kod üretimi, planlama, test üretimi, lint ve güvenlik taraması.
     PROOF: PROVEN_REAL — serbest bir istekten çok dosyalı, testleri geçen bir uygulama üretildi.
     ROLLBACK: şablon moduna dönüş (417–421 korunur).

B41  App Factory yaşam döngüsü            IDs: 440–452, 480               dep: B40, B33  risk: medium  owner: no
     GOAL: Derleme, paketleme, çalıştırma, arayüz/kalıcılık doğrulaması, geçmiş ve sonradan
           değiştirme.
     PROOF: PROVEN_REAL — üretilen uygulama çalıştırıldı, arayüzü doğrulandı, sonra bir özellik eklendi.
     ROLLBACK: proje bazında; üretilen kod korunur.

B42  Artefakt provenans ve yaşam döngüsü  IDs: 393, 394, 398–400, 405–412, 415, 416  dep: B24  risk: low  owner: silme politikası
     GOAL: Dört biçim üretimde kanıtlansın; aktör/kütüphane/manifest provenansı, sürümleme,
           düzenleme, karşılaştırma.
     PROOF: PROVEN_REAL — üretimde xlsx/pptx/csv/json üretildi ve bağımsız doğrulandı.
     ROLLBACK: provenans alanları geriye uyumlu; eski artefaktlar etkilenmez.

B43  Yaratıcı üretim ve teslim            IDs: 489–495, 498, 500, 502, 504, 506–509, 511, 512  dep: B34, B29  risk: medium  owner: görsel sağlayıcı
     GOAL: Görsel üretimi, katman/PSD/SVG, Paint'in gerçekten sürülmesi ve çıktının sahibin diskine
           teslimi.
     PROOF: PROVEN_REAL — üretilen görsel sahibin diskinde açıldı ve piksel düzeyinde doğrulandı.
     ROLLBACK: sağlayıcı devre dışı; Pillow yolu (481–488) korunur.

B44  3B üretim yolu                       IDs: 520–527                    dep: B42      risk: low     owner: no
     GOAL: Üretimden sahne oluşturma, değiştirme, malzeme/ışık/kamera kontrolü, animasyon, dışa aktarma.
     PROOF: PROVEN_REAL — üretimde ilk gerçek sahne oluşturuldu ve render'ı bağımsız doğrulandı.
     ROLLBACK: sahne yolu bayrakla kapatılır; laboratuvar yolu korunur.

B45  Mail canlandırma                     IDs: 278, 335, 336, 338–348, 360, 362, 364  dep: B12, B27  risk: medium  owner: MAIL HESABI
     GOAL: Hesap yapılandırıldığında posta okuma/özetleme/taslak/gönderim onay kapısıyla çalışsın;
           `References` kusuru kapansın.
     PROOF: PROVEN_REAL — gerçek bir hesapta yanıt gönderildi ve alıcının zincirinde göründü.
     ROLLBACK: gönderim varsayılan kapalı kalır; okuma tek başına açılabilir.

B46  Takvim canlandırma                   IDs: 277, 337, 349–359, 361, 363, 365, 366  dep: B45  risk: medium  owner: TAKVİM HESABI
     GOAL: Takvim okuma/yazma, RRULE, VALARM, hatırlatma, indeks ve eşitleme.
     PROOF: PROVEN_REAL — gerçek takvimde tekrarlayan ve hatırlatıcılı bir etkinlik oluşturuldu.
     ROLLBACK: yazma varsayılan kapalı; okuma tek başına açılabilir.

B47  Cihaz tarafı ses                     IDs: 239–244, 250–255           dep: B05, B11  risk: high    owner: MAHREMİYET KARARI
     GOAL: Tarayıcısız dinleme, uyandırma sözcüğü, yerel VAD, gizlilik göstergesi, çevrimdışı
           komut kümesi. Ham ses saklanmaz (249 korunur).
     PROOF: PROVEN_REAL — tarayıcı kapalıyken uyandırma sözcüğüyle bir komut tamamlandı.
     ROLLBACK: cihaz ses servisi durdurulur; tarayıcı yolu korunur.

B48  Varlık derinliği ve kamera           IDs: 300–303, 307, 308, 310, 312, 313, 320, 326, 327, 330–333, 671  dep: B47  risk: medium  owner: KAMERA KARARI
     GOAL: Cihaz tarafı kamera, RESTING/LIKELY_ASLEEP'in erişilebilir olması, "uyurken ekranı
           kapat" politikasının gerçekten tetiklenebilmesi. Ham görüntü saklanmaz (328, 329 korunur).
     PROOF: PROVEN_REAL — duruş sinyali üretildi ve uyku politikası bir kez tetiklendi.
     ROLLBACK: kamera sağlayıcısı kapatılır; girdi tabanlı varlık (311) korunur.

B49  Android fabrikası ve iOS beyanı      IDs: 474–479                    dep: B41      risk: medium  owner: Android SDK / fiziksel cihaz
     GOAL: Android proje/APK/AAB üretimi ve testi; macOS yokken iOS'un AÇIKÇA desteklenmediğinin
           beyanı (Unity lisans reddi deseni örnek alınır).
     PROOF: PROVEN_REAL (APK emülatörde açıldı) / 479 için PROVEN_AUTOMATED (açık ret testi).
     ROLLBACK: Android yolu bayrakla kapatılır.

B50  Unity ve Unreal                      IDs: 530–534                    dep: B44      risk: low     owner: UNITY LİSANSI
     GOAL: Lisans geldiğinde Unity proje/sahne/derleme/test; Unreal isteğe bağlı kalır.
     PROOF: BLOCKED — lisans yokken dürüst ret (529 deseni) korunur.
     ROLLBACK: yok (salt ekleme).

B51  Niyet zekâsı                         IDs: 740, 742–748               dep: B26, B27, B18  risk: medium  owner: model bütçesi
     GOAL: Model tabanlı yönlendirme, araç kaydından çözüm, güven skoru, belirsizlik sorusu,
           referans çözümü, ASR gürültüsüne dayanıklılık. **Kritik fiiller deterministik kalır (741).**
     PROOF: PROVEN_AUTOMATED — genişletilmiş külliyatta kapsam artar VE misroute 0 kalır.
     ROLLBACK: model yönlendirici kapatılır, deterministik tablo tek başına çalışır.

B52  Belge formatı genişletme             IDs: 143–146                    dep: B32      risk: low     owner: no
     GOAL: EPUB, RTF, ODT ve eski Office formatları okunsun.
     PROOF: PROVEN_AUTOMATED — oracle fikstürleriyle her format.
     ROLLBACK: format bazında geri alınır.
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
| 3 | `db9ed85` adayının kaderi | B35 | Hayır (B35'e kadar bekleyebilir) |
| 4 | TTS sağlayıcı kredisi | B15, B21 kanıtı | Kısmi — `PROVEN_PROXY` ile ilerlenebilir |
| 5 | Mail hesabı (IMAP/SMTP) | B45 | Hayır |
| 6 | Takvim hesabı (CalDAV) | B46 | Hayır |
| 7 | Cihaz tarafı ses / sürekli mikrofon (mahremiyet) | B47 | Hayır |
| 8 | Cihaz tarafı kamera (mahremiyet) | B48 | Hayır |
| 9 | Gömme sağlayıcısı | B37 | Hayır |
| 10 | Görsel üretim sağlayıcısı | B43 | Hayır |
| 11 | Android SDK / fiziksel cihaz | B49 | Hayır (P3) |
| 12 | Unity lisansı | B50 | Hayır (P3) |
| 13 | Kod imzalama sertifikası | B33 | Hayır |
| 14 | Kalıcı silme onay politikasının şekli | B05, B34 | Hayır |

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
