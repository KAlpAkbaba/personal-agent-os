# PERSONALAGENTOS PC V1 FINAL QUALIFICATION REPORT

**Tarih:** 2026-09-18 · **Kapsam:** PC/core dondurma turu (B45 Mail, B46 Calendar, B49 Android sahip kararıyla ertelenmiş)

---

## 1. Dondurma adayı

| Ne | Değer |
|---|---|
| Cloud Core sha | `44e44482f1170ef0431bc73c5361ba668fdff57b` |
| Üretimde | `api-blue`, edge arkasında, health **ok** |
| Son iyi sürüm (LKG) | `6683e88701d93ca5d358519aec65e553579e9d0d` |
| Şema | `0060_webpush_subscriptions` (ağaçla aynı) |
| Realtime sözleşme | contract_version 2 |
| Windows ajan | `0.6.0+6fa7dfe3d1efe5f52227a8ba4d8b9a87dec231f0`, build `0c4d49fa09c888d4`, 104 yetenek |
| Cihaz kodu | 6fa7dfe'den beri değişmedi (`git diff 6fa7dfe..HEAD -- devices/` boş) |
| CI | run **35344779325**, 9 işin 9'u yeşil |
| Kurtarma paketi | `/opt/pagentos-recovery`, aynı sha'ya sabitli, timer **aktif**, işaret yok |

CI işleri: Web shell, Recovery supervisor, Browser agent, Windows agent, Secret hygiene,
API lint+unit (1/3, 2/3, 3/3), API integration — hepsi `success`.

---

## 2. Satır sayıları (750 madde)

| Durum | Adet |
|---|---|
| DONE | 711 |
| PARTIAL | 15 |
| BLOCKED_PROVIDER | 19 |
| DEFERRED | 4 |
| BLOCKED_OWNER | 1 |

| Kanıt sınıfı | Adet |
|---|---|
| **PROVEN_REAL** (gerçek OS/host/donanım/ağ) | **153** |
| PROVEN_AUTOMATED | 586 |
| PROVIDER_UNAVAILABLE | 4 |
| BLOCKED (sahip) | 2 |
| NOT_YET_PROVEN | 5 |
| **Sahip kararıyla ertelenmiş satır** (B45+B46+B49+DEFERRED) | **43** |

### P0 (149 satır)

| Durum | Adet |
|---|---|
| DONE | 142 |
| PARTIAL | 6 — tamamı B05 konuşmacı doğrulama *enforce* kararı |
| BLOCKED_OWNER | 1 — 645 S3 hedefi |

P0 kanıt dağılımı: PROVEN_REAL 69, PROVEN_AUTOMATED 79, BLOCKED 1.

---

## 3. Bu turda kapanan iş: B08 kurtarma ağı

Sahip onayıyla kurtarma süpervizörü üretim host'una kuruldu ve **kasten bozularak** ölçüldü.

| Tatbikat | Arıza | Tespit | Kesinti | Sonuç |
|---|---|---|---|---|
| A | Renk durduruldu | 14 sn (doğal timer) | 30,2 sn | Kaydedilmiş imajdan yeniden başlattı |
| B | İmaj silindi | 62 sn (doğal timer) | 72 sn | Compose ağaçtan yeniden kurdu |
| C | **Bozuk sürüm** — ayağa kalkıp asla sağlıklı olmayan | 127 sn sabır bütçesi | **139,7 sn** | Diğer rengin son iyi sürümüne **yüksek sesle** geçti (exit 81) |

C turunda korunan provenans: `RELEASE` cedb773'e yazıldı, `LAST_KNOWN_GOOD` değişmedi, env dosyası
bit bit aynı kaldı (sha256 önce/sonra özdeş), başarısız aday silinmeyip `app.interrupted` olarak
saklandı, ve sistem bunu **terfi ilan etmedi**: "not a completed promotion — review required".
Ürün 1,4 sn sonra kendiliğinden `degraded` dedi.

Ayrıca 647'nin tam zinciri canlı prob ile kanıtlandı: işaret dosyası yazıldı → ürün `degraded`
dedi ve birimi adıyla gösterdi → silindi → `ok`. Arkada iz kalmadı.

**Kapanan satırlar:** 614, 647, 651, 652, 653, 654, 655 → PROVEN_REAL
(646, 648, 649, 650 zaten PROVEN_AUTOMATED ve üretimde okunuyor).
Kanıt: `docs/evidence/b08-recovery-supervisor-2026-09-18.json`.

---

## 4. Bu turda bulunan ve düzeltilen gerçek hatalar

### 4.1 Güvenlik ağının alarmı, kendi çaresini engelliyordu (ADR-0170)

Tatbikat C'nin exit 81'i arıza işareti yazdı → `backup` kontrolü `fail` → uygulama `degraded` →
blue/green sürüm kapısı tam `ok` istediği için **olayı bitirecek sürüm terfi edemedi**. Üretim
ancak elle dosya silinerek açıldı — yani tam olarak anayasanın yasakladığı "sahip operatöre
dönüşüyor" durumu. Aynı şekil dakikalar içinde ikinci kez tekrarladı (exit 83, her turda işareti
yeniden yazarak).

Düzeltme: sağlık durumu ile *bu adayın hizmete uygunluğu* ayrı iki soru olarak ayrıldı.
`failing_checks()` hangi zorunlu kontrollerin düştüğünü adıyla veriyor, `/v1/system/health` bunu
düz bir üst-seviye alan olarak yayınlıyor, ve sürüm kapısı artık "host kusursuz mu?" yerine
**"bu aday, şu an hizmet veren renkten daha kötü mü?"** diye soruyor. Her iki renkte de düşen bir
kontrol host'a aittir ve renk değiştirmek onu onarmaz; adayın tek başına düşürdüğü bir kontrol ise
o yapının kendi regresyonudur ve hâlâ reddedilir.

Regresyon: 8 Python testi + 5 PowerShell iddiası. Mutasyonla RED: kapının iki yarısı **ayrı ayrı**
kanıtlandı (anahtar öncesi kapı → 2 FAIL; anahtar sonrası edge probu → 1 FAIL).

### 4.2 Bağlantı açılırken oluşturulan komut cihaza iki kez gidiyordu

CI run 35337102502 integration testini düşürdü. Günlük kesindi: tek komut kimliği için 3 ms arayla
**iki** `broker_command_delivered` satırı — ilki POST'un trace_id'siyle, ikincisi trace_id'siz.
Bağlantı, kendisini bekleyen komutları tekrar oynatmadan **önce** canlı dağıtıma kaydediliyor; bu
pencerede oluşturulan komutu hem POST hem tekrar-oynatma gönderiyor.

Protokol kopyayı tolere eder (ajan yeniden ack'ler) ama tolere etmek istemek değildir: o çerçeve
cihaza ikinci kez verilen gerçek bir `desktop.open_application`. Bağlantı artık açılış anında ne
gönderdiğini hatırlıyor, tekrar-oynatma onları atlıyor, ve pencere kapanınca koruma bırakılıyor —
uzun ömürlü bir bağlantı hiçbir şey biriktirmiyor, gerçek yeniden-bağlanma teslimi çalışmaya
devam ediyor. Mutasyonla RED kanıtlandı.

### 4.3 Bayat kayıt düzeltildi

`state/BUILD_STATE.json` sürüm betiğinin ssh/scp çağrılarında zaman aşımı olmadığını "OPEN, not
fixed" diye taşıyordu; bu 2026-09-06'da kapanmış (her çağrıda `ConnectTimeout` +
`ServerAliveInterval`/`CountMax`), bugün yeniden doğrulandı ve kayıt düzeltildi.

---

## 5. Bugün dondurma adayı üzerinde koşan üretim turları

| Tur | Sonuç | Kanıt |
|---|---|---|
| P0 üretim turu | 42 satırın **35'i** PROVEN_REAL | `docs/evidence/p0-production-round-2026-09-18-125507.json` |
| PC üretim turu | 27 satırın **17'si** PROVEN_REAL | `docs/evidence/pc-production-round-2026-09-18-125520.json` |
| B08 kurtarma tatbikatı | 3 tatbikat, hepsi ölçülü | `docs/evidence/b08-recovery-supervisor-2026-09-18.json` |

Kanıtlanamayanların her birinin adı ve gerekçesi kanıt dosyalarında; hiçbiri "çalışmıyor" demek
değil, "bu turda dürüstçe kanıtlanamaz" demek. Örnekler: **660** panic revoke *her* oturumu iptal
eder (sizin canlı oturumlarınız dahil), **539/548** yeni bir executive koşusu başlatmayı gerektirir,
**259** çalan bir alarm ister, **276** son 12 saatte biten bir araştırma ister (koşullu bölüm).

---

## 6. Kalan gerçek hata

**Bilinen açık hata yok.** Bu turda bulunan iki hata da düzeltildi, regresyonlandı ve CI'dan geçti.

Takibe alınan tek nokta (hata değil, tutarsızlık): `services/recovery-supervisor/recovery_supervisor/runner.py`
hâlâ katı `status == "ok"` kuralını kullanıyor. Orada dürüstçe bozuluyor (`unhealthy_no_rollback`),
kilitlenmiyor; ADR-0170'in kuralının oraya da taşınıp taşınmayacağı ayrı bir iş olarak açıldı.

---

## 7. Sizden bekleyen fiziksel testler (hiçbiri gerisini bloklamıyor)

| Satır | Ne | Neden yalnız siz |
|---|---|---|
| B47 241 | Uyandırma sözcüğü | Makinede Türkçe tanıyıcı yok (yalnız en-US SAPI/OneCore) |
| B47 253 | Çevrimdışı komut alt kümesi | Yürütme ve politika tam; Türkçe tanıma kalitesi fiziksel tur ister |
| B47 255 | Donanım mikrofon susturma | Gerçek WASAPI okuması ve dizüstü tuşu |
| B48 300 | Kamera açma | Gerçek MediaCapture, sahte karelerle değil |
| B48 320 | Çoklu monitör kararması | Monitörün gerçekten karardığını yalnız siz görürsünüz |
| B48 327 | Tarayıcıdan bağımsız kamera | Gerçek yakalama yolu derlendi, otomatik testte kamera açılmıyor |
| B11 | Windows toast'a basma | Gerçek bir bildirime gerçek bir tıklama |
| B13 259 | Alarm erteleme | Çalan bir alarm gerekir |
| B33 473 | MSIX güven adımı | Yönetici PowerShell'de tek seferlik sertifika güveni |

---

## 8. Sağlayıcı blokerleri (19 satır, dürüst red ile)

| Batch | Satır | Gereken |
|---|---|---|
| B45 | 335, 336, 338–342, 344, 345 | Mail hesabı (sahip kararıyla **ertelendi**) |
| B46 | 337, 349–353 | Takvim hesabı (sahip kararıyla **ertelendi**) |
| B12 | 373, 374 | FCM / APNs sağlayıcı hesabı |
| B38 | 549 | B45 hesabıyla PROVEN_REAL olur |
| B49 | 478 | Fiziksel Android telefon (sahip kararıyla **ertelendi**) |

Hiçbirinde sahte bir taşıyıcı yazılmadı: hesapsız bir kanalı denenmiş göstermek, denenmemiş bir
kanalı denenmiş göstermek olurdu.

---

## 9. Sizden bekleyen kararlar

1. **B05 konuşmacı doğrulama enforce** (245, 247, 248, 664, 665, 666) — karar yolu kurulu ve test
   edilmiş, **gölge modda** çalışıyor: sayıyor, engellemiyor. Enforce etmek bir mahremiyet/politika
   kararı. Kural sabit kalır: VoiceIdentity yalnız destekleyicidir, **asla tek başına kimlik kökü
   değildir**.
2. **645 — S3 uyumlu host-dışı yedek hedefi** (kova + anahtar). Yol hazır; anahtar asla commit'e
   girmez, yalnız DPAPI/stdin üzerinden.
3. **B42 412** silme politikası ve **WebPush VAPID** konusu (372) — birer onay adımı.
4. **db9ed85** self-development adayı — onayınızı bekliyor.

---

## 10. Sonuç

PC/core tarafında 750 maddenin 711'i DONE; açık P0'ların tamamı ya sizin kararınızı ya da fiziksel
donanımınızı bekliyor. Bilinen açık hata yok, CI yeşil, üretim dondurma adayını çalıştırıyor ve
kurtarma ağı gerçek bir arızayla sınandı: bozuk bir sürümden **139,7 saniyede, sahip müdahalesi
olmadan** dönüldü ve sistem bunu terfi gibi göstermedi.

```
PC_V1_FREEZE_READY = YES
```
