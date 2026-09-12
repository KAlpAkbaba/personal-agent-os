# PERSONALAGENTOS TAM ÖZELLİK VE OLGUNLUK RAPORU

**Denetim tarihi:** 2026-09-12 · **Depo:** `main` @ `48dfcc3` (CI yeşil) · **Üretim:** `714ff2b`, son bilinen iyi `d86b3d9`
**Kapsam:** Tek cihazlı mevcut sistemin uçtan uca denetimi. Çoklu cihaz / M29 kapsam dışıdır ve bu raporda önerilmez.
**Yöntem:** Kaynak + git geçmişi + CI + canlı üretim + host + Windows çalışma zamanı. **Belge ile ölçüm çeliştiğinde ölçüm kazanır.**
**Uygulama yapılmadı.** Bu tur yalnızca ölçüm ve önerilerdir.

---

## 1. YÖNETİCİ ÖZETİ

PersonalAgentOS, **mühendislik disiplini yüksek, çalışan altyapısı sağlam, ama ulaşılabilirliği dar** bir sistem. Üç cümlede:

1. **Omurga gerçek.** Üretim dokuz gündür ayakta, CI yeşil, blue/green sürüm ve geri alma çalışıyor, yedek ve geri yükleme host üzerinde gerçek koşuyla kanıtlandı, kendini geliştirme motoru gerçek bir kusuru düzeltip politika sınırında durdu, üretimden tetiklenen cihaz derlemesi (26.16) kanıtlandı.
2. **Ama sistemin büyük bölümü erişilemez.** Cihazın duyurduğu 85 yeteneğin **40'ının** bulutta çağıranı yok. 117 sesli aracın 28'i üretimde hiç kullanılmamış. 243 REST rotasının büyük kısmının ne sesi ne arayüzü var. Bellek altsistemi tamamen yazılmış ama sesle yazılamıyor, hiçbir yanıtta kullanılmıyor. Yetenek kataloğu boş, çünkü kayıt yüzeyi hiç yazılmamış.
3. **Ve dört canlı üretim kusuru var** — dördü de aynı desenden: bulut bir şekil gönderiyor, cihaz başka bir şekil bekliyor, iki test paketi de yeşil, çünkü sahte cihaz payload'a hiç bakmıyor.

**En kritik dört bulgu (hepsi doğrulandı):**

| # | Bulgu | Etki |
|---|---|---|
| 1 | `file.search` bulut **klasör adı** gönderiyor, cihaz **mutlak yol** istiyor | Klasöre dayalı her belge araması üretimde reddediliyor. 2026-09-09'da ADR-0102'ye kaydedilmiş, düzeltilmemiş |
| 2 | Uygulama şablonları `{port}` taşıyor; cihaz `{`/`}` karakterini reddediyor (beklediği `<port>`) | `task-tracker` ve `static-page` uygulama üretimi ilk adımda ölür |
| 3 | `cli-tool` manifest'inde `run` ve `port` yok; cihaz ikisini de zorunlu tutuyor | Üçüncü şablon da ölü |
| 4 | Sürüm betiği `set -eu` + `alembic upgrade head \| tail -2` | **Başarısız bir veritabanı göçü sürümü durduramaz**; yeni renk göçsüz şemayla "sağlıklı" açılır |

**Bir de sessiz güvenlik sorunu:** `/v1/world/facts` üretim veritabanı parolasını açık metin döndürüyor (sahip oturumu gerekli, ama projenin kendi redaksiyon kuralı bu dizeyi belleğe yazmayı reddediyor).

**Sistemin en güçlü üç yanı:** artefakt fabrikası (9 biçim, bayt-belirlenimli, yeniden açarak doğrulanıyor), alarmın çevrimdışı cihaz yedeği (bulut tamamen kapalıyken bile çalıyor), ve evrim motorunun üretim yetki sınırı (mühürlü yetki jetonu, üretim izinleri sıfır).

**Günlük kullanım hissi:** Sesli asistan, **açık bir tarayıcı sekmesi olmadan duyamıyor ve konuşamıyor**. Uyandırma sözcüğü yok, cihaz tarafı ses kapalı, push bildirim sağlayıcılarının hiçbiri etkin değil. Yani "iş bitti, haber veririm" sözü bugün teknik olarak tutulamıyor. 103 makul Türkçe cümlenin 59'u hiçbir yeteneğe yönlenmiyor; 7'si **yanlış** yeteneğe yönleniyor (biri sessizce ekran otomasyonunu kapatıyor).

---

## 2. TÜM ÖZELLİK ENVANTERİ

### 2.1 Ses / Gerçek Zamanlı / Anlatım — olgunluk 4 (ses), 1 (anlatım sesi), 2 (telaffuz)

**Çalışan:** Oturum açma, yetenek tabanlı sağlayıcı seçimi (openai-realtime, WebRTC), efemer kimlik bilgisi (satıcı anahtarı tarayıcıya hiç gitmiyor), veri kanalı, mikrofon, hoparlör, barge-in (yerel durdurma önce, sonra iptal — sıralaması testli), VAD, sınırlı tek-uçuşlu yeniden bağlanma, 410 fırtına koruması (ADR-0099; 20 ağ kesintisi → 1 bağlanma denemesi, testli), sideband kuyruğu, oturum sözleşmesi sürümlemesi.

**Kısmi / bağlı değil:**
- **Uzun metin seslendirme SES ÜRETMİYOR.** `NarrationEngine` yalnızca testlerde kuruluyor; seam imzası (`synthesize(text, settings) -> bytes`) gerçek sağlayıcıyla (`synthesize(text, *, voice, speed, fmt) -> TTSResult`) uyumsuz. Anahtar eklemek bunu değiştirmez.
- **Telaffuz kuralları asistanın kendi konuşmasına girmiyor** — yalnızca araç dönüşü metne uygulanıyor; persona talimatlarına hiç taşınmıyor. Üretimde kural sayısı sıfır (tek yazıcı manuel PUT).
- **Ölü oturumları hiçbir şey süpürmüyor.** Üretimde 6-7 oturum "active", en eskisi 9 Eylül. Kaynak kodun kendisi söylüyor: "nothing sweeps in the background".
- **Mikrofon kaybı sessiz.** `track.onended`/`onmute` dinlenmiyor; mikrofon iptal edilirse arayüz "Dinliyor" demeye devam ediyor.
- **Sağlayıcının 60 dakika tavanı** yayınlanıyor ama hiçbir istemci okumuyor.
- `faster-whisper` sağlık çıktısında yanlışlıkla "etkin" görünüyor (kısa devre mantığı ölü dalı atlıyor).
- Alarm karşılamasında anahtar yoksa sessizce sinüs tonu çalıyor; bu dalın testi yok.

### 2.2 Bellek / Kişisel Bağlam — yazma 4, geri getirme 3, **KULLANIM 1**

Soru: *"Sahip bugün bir tercih öğretip sonra tekrar söylemeden kullandırabilir mi?"* → **Hayır.** Zincir hem ilk hem son halkada kopuk:

- **Giriş yok:** `memory.*` sesli aracı yok, ses paketi `app.memory`'yi hiç import etmiyor, konuşma metni bilinçli olarak atılıyor. Tetikleyici kalıplar (`tercih ederim`, `bundan sonra`, `hatırla`) **yazılmış ama hiç beslenmiyor**.
- **Enjeksiyon yok:** Persona talimatları 10 sabit metin + 7 alanlık ses tercihi; bellek metni oturuma girmiyor.
- **Geri getirme çağrısı yok:** `hybrid_search`'ün ürün içinde çağıranı yok (yalnızca REST ve çevrimdışı değerlendirme).
- **Gömme anlamsal değil:** `deterministic-ngram`, 256 boyut; depoda başka bir gömme sağlayıcısı yok, seçim mekanizması yok, yeniden indeksleme rotası yok.
- Üretimdeki 8 bellek satırının hepsi araştırma kaynaklı; tercih sınıfı hiç üretilmemiş; varlık grafiği boş.
- Düzeltme, sabitleme, unutma, çelişki çözümü: **hepsi yazılmış ve testli**, hiçbiri sesten erişilemiyor.

### 2.3 Öz Model / Dünya Modeli / Aktivite Defteri — 4 / 3 / 3

- Öz model otomatik yenileniyor (açılıştan 5 sn sonra + her 900 sn); üretimde 443 modül, 8334 sembol, 3308 kenar. **Ama her modül `source_only`** — çalışan sürümle ilişkilendirme yok.
- **`tasks.running = 10` yanlış.** Biten (`READY`) araştırmalar "çalışıyor" sayılıyor; üstelik `EVIDENCE` gerçeği olarak etiketlendiği için hiç bayatlamıyor. İçlerinden biri 9 Eylül'den beri `discovering` aşamasında ve onu süpüren hiçbir şey yok.
- **Her sesli oturum deftere iki kez yazılıyor** (canlı + geri doldurma); araştırma tarafındaki tekilleştirme koruması ses tarafına konmamış.
- 1441 aktivite olayından **0 bellek** türetilmiş: Experience Engine üretimde hiç koşmamış (zamanlayıcı yok, yalnızca manuel POST).
- `memory.remembered` olay tipi tanımlı, hiçbir yerden yayılmıyor.

### 2.4 Kendini Geliştirme / Kendini İyileştirme

Üç ayrı gövde var; karıştırılmamalı:

| Motor | Ne yapar | Bağlı mı | Otonom mu | Üretim yetkisi |
|---|---|---|---|---|
| `app/evolution` | Laboratuvarda beceri üretir, fırsat/boşluk yönetir | Evet (REST) | Yalnızca tespit | Yok (izin sayısı 0) |
| `app/selfhealing` | Demo servisteki enjekte kusuru onarır | Evet (REST) | Hayır | Yok |
| `app/selfdev` | **Gerçek ürün kodunu düzeltir** | **HAYIR** | Hayır | Yok |

**On sorunun yanıtı:**

| Soru | Yanıt |
|---|---|
| Gerçek `app` kodunu değiştirebilir mi | Evet — yalnızca `app/selfdev`, yalnızca komut satırından |
| Yeni modül ekleyebilir mi | Evet (kapsam içinde) |
| Gerçek bir üretim kusurunu düzeltebilir mi | Evet — bir kez kanıtlandı (aday `db9ed85`) |
| Gerçek git dalı/worktree açabilir mi | Evet — diskte 7 gerçek `selfdev/*` dalı var |
| Test koşabilir mi | Evet — adayın kendi worktree'sinde gerçek pytest |
| Başarısız teste tepki verip kodu değiştirebilir mi | Evet — teşhis → düzeltme döngüsü gerçekten çalışıyor |
| CI sonucu okuyabilir mi | Evet (`gh run list`) |
| CI başarısızlığından sonra kodu değiştirebilir mi | **Hayır** — aday CI'ı commit'ten sonra okunuyor, döngüye geri dönüş yok |
| Sürüm adayı üretebilir mi | Git adayı evet, sürüm artefaktı hayır |
| Otonom dağıtım yapabilir mi | **Hayır** — dört bağımsız bariyer (yetki, yaşam döngüsü, sahip oturumu, terfi sınıfını okuyan kod yok) |

**Kritik boşluk:** `app/selfdev` çalışan sistemden erişilemez. Rota yok, araç yok, zamanlayıcı yok; üretim imajında git checkout bile yok. Kusur girişi elle yazılan JSON. 14 fırsat "idea"da bekliyor ve onları kusur tanımına çeviren bir köprü yok. selfdev döngüsünde **güvenlik incelemesi yok**; `Grant.SECURITY_REVIEW_CANDIDATE` izni tanımlı ama tüketicisi yok.

**Ölü tuzak:** `ClaudeSkillGenerator` yapılandırma kontrolünü geçtikten *sonra* hata fırlatıyor — yani env değişkenlerini doğru ayarlayan sahip "yapılandırılmamış" hatası alır.

### 2.5 Dijital Operatör / Windows Kontrolü — uygulama 3, pencere 3, girdi 2, UIA 1, terminal 2

- Cihaz 32 operatör yeteneği duyuruyor; bulutun çağırdığı **12**. Sistem bugün **tıklayamıyor**, tuşa basamıyor, kısayol gönderemiyor, UIA ile düğme tetikleyemiyor, ekran göremiyor, pencere taşıyamıyor/boyutlandıramıyor.
- Planlar **sabit**; yeniden planlama, hata sınıfına göre kurtarma, seviye yükseltme yok. Kaynak bunu açıkça söylüyor.
- Odak koruması (FocusGuard) sistemin en güçlü mekanizmalarından: gönderim öncesi doğrulama, her tuş partisinde yeniden doğrulama, kısmi gönderimde tam muhasebe.
- Uygulama izin listesi bulutta 6, cihazda 7 (mspaint fazladan) — iki listeyi bağlayan test yok.
- Kabuk izin listesi 8 desen, bulutun erişebildiği 2 (`hostname`, `ipconfig`).
- Sır yazma koruması sözlüksel (şifre/parola/pin); cihazdaki `secret` bayrağını bulut hiç göndermiyor.
- Üretimdeki iki yetenek boşluğu tam da bu tavanı gösteriyor: *"Chrome'u açıp YouTube'a girerek … aç"* planlanamıyor.

### 2.6 Dosyalar / Belgeler / Office — okuma 4, yazma 1, anlama 2

- **Sahibin dosyaları değiştirilemez.** Belge ailesi yapısal olarak salt okunur; `file.delete` `capability_missing` ile reddediliyor ve test dosyanın hayatta kaldığını doğruluyor. Yeni dosya yalnızca üç ajan-sahipli kökte oluşur (indirme, proje, 3B).
- Gerçek ayrıştırıcılar: PDF (PdfPig), DOCX/XLSX/PPTX (Open XML SDK), md/csv/json/kaynak kodu. Hepsi taahhüt edilmiş bir "oracle" fikstürüne karşı doğrulanıyor.
- Yok: eski .doc/.xls/.ppt, .rtf/.odt/.epub, görseller, arşivler, **OCR**, **anlamsal arama** (yalnızca sözlüksel token örtüşmesi).
- **Canlı kusur:** klasöre dayalı arama üretimde reddediliyor (bkz. yönetici özeti #1).

### 2.7 Tarayıcı / İnternet / Araştırma — tarayıcı 4, araştırma 4, haber 3

- Üretimde kullanılan yol: **yönetilen, ayrı profilli Chrome**. Sahibin kendi Chrome'u kayıtlı ama otonom araştırmaya kapalı (ADR-0113 gereği, `owner_authorized_for_research=false` ve bu gerçekten zorlanıyor).
- Anlamsal hedefleme (rol/metin/etiket), ham koordinat yalnızca son çare ve gürültülü loglanıyor; SSRF politikası **iki yakada da** her gezinme işleminde zorlanıyor; CAPTCHA asla çözülmüyor, tespit edilip soğuma uygulanıyor; üç katmanlı orphan Chrome temizliği.
- Araştırma: dayanıklı Temporal hattı, kanıt sözleşmesi, ince sonuçta dürüst itiraf, Temporal yokken tipli 503 (ADR-0123).
- **Boşluklar:** `uploads` bayrağı duyuruluyor ama karşılığı olan işlem yok; `browser.download` yetkilendirme referansı yalnızca "boş değil mi" diye bakılıyor, boyut sınırı yok; DEEP modu üretimde hiç koşmamış ve sesten çağrıldığında 12 kaynağa kırpılıyor; **araştırma koşusu yetimleri süpürülmüyor** (3 gündür açık bir koşu var).

### 2.8 Alarm / Rutin / Sabah Deneyimi — alarm 4, rutin 2, brifing 1

**Tarayıcı kapalıyken çalışanlar:** alarm kararı, cihaz kurulumu, ekran uyandırma, YouTube uyandırma şarkısı (companion'ın kendi Chrome'u), ton yedeği, ses rampası, sesli karşılama.
**Çalışmayanlar:** hava durumu, haber, sistem durumu, sabah brifingi — hepsi canlı ses oturumu istiyor. `BriefingService.build`'in tek çağıranı sesli araç; alarm yolundan hiç çağrılmıyor.

- **En güçlü çevrimdışı garanti:** cihaz alarmı 12 saat öncesinden diske yazıyor ve bulut tamamen kapalıyken tek başına çalıyor (deterministik saatle testli).
- **Ama cihaz çaldığını buluta bildiremiyor** — `local_alarm_fired` alanı cihazın kapalı alan setinde yok. Sonuç: bulut yarım saat sonra ikinci kez çalabilir.
- İki farklı "çok geç" eşiği: cihaz 5 dakika, bulut 2 saat.
- Brifing *"Haber özeti şu an bağlı değil efendim"* diyor — oysa haber altsistemi çalışıyor ve kaynak tanımlı.
- `voice_briefing` rutini donmuş bir metni okuyor; `BriefingService`'i hiç çağırmıyor.
- Üretimdeki 7 rutinin hepsi tek seferlik alarm; `schedule` ve `presence` tetikleyicileri hiç kullanılmamış.

### 2.9 Varlık / Aktif Göz / Kamera / Ekran — varlık 2, ekran 3

- **Kamera yalnızca tarayıcı sekmesinde.** Cihazda kamera kodu yok. Sekme kapanınca kamera susuyor ve bunu bildiren bir çağrı yok — durum yalnızca zaman aşımıyla UNKNOWN'a düşüyor.
- Üretimde şu an `eye_enabled: false`; varlık yalnızca klavye/fare boştalığından geliyor ve bu **tarayıcısız çalışıyor**.
- `RESTING` / `LIKELY_ASLEEP` durumları üretimde **erişilemez** (duruş sinyali hep "unknown"), dolayısıyla "uyurken ekranı kapat" politikası hiç tetiklenemez.
- Ekran kapatma/uyandırma companion'da; **alarm önceliği iki yakada da zorlanıyor** ve cihaz önce alarma bakıyor.
- Rutin yolundaki ekran eylemi kalıcı olarak reddediliyor (ayrı onay bekliyor).

### 2.10 Mail / Takvim — 2 / 2

- IMAP, SMTP ve CalDAV kodu **gerçek ve iyi test edilmiş** (soket düzeyinde testler, MIME yuvalama ve başlık enjeksiyonu sertleştirmesi).
- **Hiçbir hesap yapılandırılmamış**; gönderim ve takvim yazma varsayılan olarak kapalı; `.env.example`'da bu anahtarlar hiç yok.
- Onay kapısı (okundu-geri-bildirim + sonraki tur + deterministik yönlendirme + atomik durum geçişi) çok iyi tasarlanmış, ama sağlayıcı olmadığı için hiç çalışmamış.
- **Kusur:** yanıt gönderiminde `References` başlığı boş geçiliyor → gerçek bir yanıt alıcının konu zincirini kırar. Tek test gerçek servisi atlayarak gönderici sınıfını sürüyor, bu yüzden görmüyor.
- Takvim: hatırlatıcı (VALARM) yok, tekrar kuralı (RRULE) yazımı yok, silme yok (bilinçli), indeks tablosu tanımlı ama hiç yazılmıyor.

### 2.11 Artefakt Fabrikası — 4 (denetimdeki en iyi mühendislik)

- 9 biçim: pdf, docx, html, md, txt, xlsx, pptx, csv, json. Hepsi gerçek kütüphanelerle üretiliyor.
- **Bayt-belirlenimli** (aynı girdi iki kez → aynı bayt; testle kanıtlı), OOXML zip'i normalize ediliyor, tarih/üretici sabitleniyor.
- **Bağımsız doğrulama:** üretilen dosya yeniden açılıyor; xlsx'te toplamlar bağımsız olarak yeniden hesaplanıyor.
- Depolama MinIO, içerik adresli, cihaza tek kullanımlık 10 dakikalık jetonla teslim.
- **Boşluklar:** xlsx/pptx/csv/json üretimde **hiç üretilmemiş**; düzenleme yolu yok (değişen spec yeni artefakt doğurur); silme yok (bilinçli); provenans eksik (aktör kolonu yok, kaynak manifest hiç yazılmıyor, kütüphane sürümü kaydedilmiyor).

### 2.12 Uygulama / Yerel Fabrika — 1.5 / 3.5

- **Uygulama fabrikası:** 3 şablon, yalnızca slot doldurma. Model üreteci **atıl** (koşulsuz hata). `entities`/`screens` doğrulanıp saklanıyor ama hiçbir şablonda karşılığı yok — sessizce atılıyor. Üretimde hiç koşmamış. **Üç şablonun üçü de cihaz tarafından reddedilir** (yönetici özeti #2 ve #3).
- **Yerel fabrika:** üretimden tetiklenen gerçek cihaz derlemesi kanıtlı; verdict artefaktın kendi PE kimliğinden okunuyor. Ama tek şablon; `native.install`, `native.launch`, `native.fix` **ölü** (arka uç hiç bağlanmamış); MSIX ve portable paketleme üretimden erişilemez; artefakt uç noktası Linux'ta Windows yolunu stat'ladığı için hep 410 döner.
- Cihazın "test sayıları ayrıştırılamadı" bayrağı buluta geliyor ama okunmuyor; üretimdeki satır `passed: null, failed: null` ile `verified` oldu.

### 2.13 Yaratıcı / Görsel / Paint / Adobe — 1

- Pillow tabanlı raster hattı **gerçek** ve çıktısı piksel düzeyinde bağımsız doğrulanıyor.
- Paint: yalnızca **algılama**; modül düzeyinde "asla çalıştırma" reddi var. Photoshop/Illustrator: algılama + red. **Figma: jeton sabit `False`** ve `creative.design` varsayılanı Figma → bu araç **yapısal olarak her zaman başarısız**.
- **Görsel üretimi hiç yok** (sağlayıcı, uç nokta, model kimliği yok). **OCR hiç yok.**
- Çıktı sahibin diskine ulaşmıyor; yalnızca MinIO'da duruyor.
- Cockpit'teki yaratıcı paneli **kalıcı olarak bozuk**: web `/v1/creative/runs` istiyor, API `/{run_id}` bekliyor → 422 → sahip "Alınamadı: HTTP 422" görüyor.

### 2.14 3B / Blender / Unity — 3

- Blender **gerçekten sürülüyor**: başsız, sabitlenmiş (sha256) sürücü betiğiyle, kendi Python API'si üzerinden; render üretiliyor ve **iki kez** doğrulanıyor (cihazda hash, bulutta bağımsız çözme + boş görüntü reddi).
- Unity kodu gerçek ama lisans yok; red dürüst ve sınıflandırılmış.
- Üretimde **sıfır sahne** — tüm kanıt laboratuvardan. Sahne oluşturmanın REST yolu yok, yalnızca sesli araç var ve hiç çağrılmamış.

### 2.15 Yürütme Özerkliği — 2

- Planlayıcı **genel değil**: üç şablon (araştırma raporu, klasör karşılaştırma, mail zinciri), Türkçe/İngilizce kök eşlemesiyle seçiliyor. Eşleşmezse dürüstçe 422 ile reddediyor. LLM planlayıcı `NotImplementedError`.
- 15 adım türünden 8'i herhangi bir planlayıcıdan erişilebilir; belge çıkarma, artefakt render, takvim önerisi, uygulama/sahne adımlarının gerçek işleyicileri var ama hiçbir plan onları üretmiyor.
- Duraklat/devam/iptal/değiştir **gerçek Temporal sinyalleri**; telafi yalnızca iptalde çalışıyor ve bir dalı hiçbir şey yapmadan "telafi edildi" diyor.
- Üretimdeki 5 koşunun 4'ü "partial"; ikisi sahibe "4/4 adım tamam" diyor ama 4 adımın 3'ü başarısız.

### 2.16 Yetenek Doğuşu (Genesis) — 1

- Motor gerçek ve uçtan uca kanıtlı (canlı bir fikstüre karşı gerçek HTTP adaptörü üretip çalıştırıyor).
- **Ama ön kapısı yok:** arayüz kataloğu boş kuruluyor ve `.register()` çağıran hiçbir üretim kodu yok; `POST /v1/genesis/runs` rotası yok; arayüz araştırması loopback HTTP uygulaması istiyor. Yani katalog boş çünkü **istek alımı hiç koşmadı**.
- Beceri üreteci 5 fonksiyonluk kapalı bir listeyle sınırlı; LLM üreteci kapalı.
- Genesis kapalı değil — **erişilemez**.

### 2.17 Yedek / Geri Yükleme / Felaket Kurtarma — yedek 4, geri yükleme 4, felaket **1**

| Bileşen | Yedek var | Geri yükleme testli | Kurtarma kurulu |
|---|---|---|---|
| PostgreSQL | Evet | Evet (haftalık otomatik) | Evet |
| MinIO nesneleri | Evet | Evet | Evet |
| Yapılandırma | Evet | Kısmi (dosyalar var, host yeniden kurulumu denenmemiş) | Kısmi |
| Sürüm meta verisi | Evet | Kısmi | Kısmi |
| Kurtarma paketi | Koşullu | — | **Hayır** |
| **Host dışı kopya** | **Hayır** | Hayır | Hayır |

- Yedek içeriği kapsamlı (tüm veritabanları + rol tanımları, tablo bazında satır sayısı ve sha256 parmak izi **dökümün kendisinden**, tüm kovalar API üzerinden, `.env`, kimlik kökü, edge durumu, systemd birimleri), şifreli, bütünlük kontrollü.
- Kurulum, kanıt yedeği **ve** kanıt geri yüklemesi geçmeden zamanlayıcıları açmıyor — bu, çoğu üretim sisteminden iyi bir disiplin.
- **RPO/RTO:** bozuk veri/göç için ≤ 24 saat ve dakikalar. **Host kaybı için ikisi de sonsuz** — depo koruduğu diskin üzerinde ve host dışı kopya yapılandırılmamış.
- **`main` dalında reconcile zamanlayıcısı yok** (servis var, timer yalnızca Astra dalında) → sürüm sonrası bozulmayı izleyen hiçbir şey kurulu değil.
- Yedek başarısı ürüne görünmüyor: `LAST_BACKUP.json` ve tatbikat raporlarını okuyan hiçbir sağlık kontrolü, hiçbir `OnFailure=` yok.

### 2.18 Güvenlik / Yetki / Kimlik — kimlik 4, sır 3, yetki zorlaması 3

**Güçlü:** Opak 256-bit jetonlar, yalnızca SHA-256 saklama, sabit zamanlı karşılaştırma, kaba ret sınıfları, kısıtlama, kapsam daraltmanın yapısal zorlanması, kimlik kökü veritabanı ve imaj dışında, cihaz iptali oturumları da iptal ediyor. Yayımlanan tek soket Tailscale adresine bağlı; IP yoksa yığın başlamıyor. Kimliksiz erişilebilen rotalar denetlendi ve **hepsi doğru** (sağlık, bootstrap-loopback, oturum açma, cihaz kaydı, WS kimlik doğrulama, tek kullanımlık jetonlar).

**Zorlanmayan kurallar:**
1. **Konuşmacı doğrulama tavsiye niteliğinde.** "Ses asla tek başına sır değildir; cihaz güveni zorunlu ikinci faktördür" deniyor ama `device_trusted` istek gövdesinden geliyor ve kararı okuyan hiçbir kod yok.
2. **Cihaz komut rotası yetenek/politika denetimi yapmıyor.** Sahip oturumu olan bir istek, cihazın 85 yeteneğinden herhangi birini doğrudan çağırabilir; tek kapı cihazın kendi reddi.
3. **Üretim tarafından çıkış geçişi erişilemez** — `FAILED`/`QUARANTINED` hedefleri için yetki mintlenmediğinden, yarıda kalmış bir adayı REST'ten başarısız işaretlemek mümkün değil (güvenli tarafta hata veriyor ama kaçış yolu çalışmıyor).
4. **Oturum yenilemenin mutlak yaş sınırı yok** — sızan bir jeton yenilendikçe süresiz yaşar.
5. **Redaksiyon desenleri (13 adet) log hattında ve kimliksiz sağlık uç noktasında yok.**
6. **`/v1/world/facts` veritabanı parolasını açık metin yayınlıyor.**

### 2.19 Bildirimler / Sahibe Ulaşma — 1

**Soru:** *Tarayıcı ve ses sayfası kapalıyken sistem sahibine ulaşabilir mi?* → **Yalnızca alarm sesiyle.**

- Canlı tek yol: cihaz sesi (`desktop.alarm_start`, `play_audio`, `display_wake`) — broker WebSocket'i üzerinden, ses oturumu gerektirmeden.
- **Masaüstü bildirimi (toast) hiç yok** — cihaz yetenek listesinde böyle bir ad yok.
- WebPush / FCM / APNs: üçü de yapılandırılmamış; WebPush'un istemci tarafı (service worker) hiç yazılmamış ve yük şifrelemesi yok.
- Uygulama içi gelen kutusu **kalıcı değil** (bellek içi kuyruk; süreç yeniden başlarsa kaybolur).
- **`fake` sağlayıcı yeni kayıtlarda varsayılan** ve "iletildi" diyor; duyurucu bunu görünce görevi kalıcı olarak "haber verildi" diye damgalıyor.
- Anayasanın *"tamamlanan iş kısaca haber verir"* sözü bugün **teknik olarak tutulamıyor**.

### 2.20 CI / Test / Kanıt Kalitesi — CI 2.5, kanıt 3.5

- HEAD'de yedi job da yeşil; flake maskeleme, retry, `continue-on-error` yok.
- **CI boşlukları:** ~1472 web testi ve iki linter **hiçbir kapıda koşmuyor** (CI yalnızca `pnpm build` yapıyor). PE okuyucusunun 15 testi (yerel fabrikanın "verified" kararının yargıcı) CI'da tamamen atlanıyor. İki PowerShell paketi ya yalnızca yerel kapıda ya hiçbir yerde. 26.16 kanıt betiği hiçbir yerden referanslanmıyor.
- **Kanıt bütünlüğü iyi:** alıntılanan 31 kanıt dosyasının ve 159 test dosyasının **tamamı mevcut**; içerikler gerçek ölçüm. Tek kırık alıntı: bir satır olmayan bir kaynak dosyayı gösteriyor.
- **Gerçek kanıt boşluğu:** Stage 1-6'daki 22 satır makineyle doğrulanabilir hiçbir şey taşımıyor (ilk kanıt dosyası 6 Eylül'de eklenmiş, o iddialar 1-2 Eylül'den).
- **"Sahte, üretimden nazik" deseni sistemik:** yedi ayrı cihaz sahtesi payload'a hiç bakmıyor; paylaşılan fikstür dosyası yoksa C# tarafı sessizce kendi manifest'ine düşüyor; altı PowerShell testi yürütme yerine kaynak-regex'i kullanıyor (biri gerçek bir geri alma boşluğunu gizliyor); bir iddia operatör önceliği hatası yüzünden hiçbir şeyi test etmiyor.
- **BUILD_STATE çelişkileri:** `current_milestone` M28 bitti derken alt blok "IN PROGRESS" diyor; `last_completed_milestone` hâlâ M27; üst düzey `status` dokuz kilometre taşı eski; ACCEPTANCE_TESTS iki kanıtlanmış satırı kanıtlanmamış gösteriyor.

### 2.21 Performans / Operasyon — 2

- Host rahat: 9 gün açık, yük 0.12, disk %31, RAM 1.5/7.7 GB, API 274 MB, DB 35 MB.
- **Tek süreç, sekiz gömülü döngü** (broker süpürgesi, rutin saati, evrim nöbetçisi, saklama süpürgesi, üç duyurucu, öz model yenileyici, Temporal işçisi). Ölen bir döngü yeniden başlatılmıyor; yalnızca broker süpürgesinin canlılığı sağlıkta görünüyor.
- **Rutin saati tek try/except ile sarılı:** bir alt tik patlarsa alarmlar, ortam, evrim taraması ve yürütme mutabakatı o turda sessizce atlanıyor — ve sağlık yine "ok" diyor.
- **Üç sınırsız yeniden deneme döngüsü:** push duyurucu (kalıcı arızada günde 17.280 deneme), brifing duyurucu (konuşulamayan tek satır tüm kuyruğu kalıcı tıkıyor), araştırma duyurucu.
- **Saklama politikası olmayan tablolar:** `audit_events` (13.560), `device_commands` (1.060), `session_events`, `owner_sessions`, `research_candidates` (2.610), defter olayları.
- **Kullanılabilirlik hiç ölçülmüyor** (`measurement: none`); son 7 günde 26 sürüm ve 16 geri alma kayıtlı, nedenleri kayıtlı değil.

### 2.22 Günlük Kullanım (UX) — ses erişilebilirliği 2.5, web 1.5, hata netliği 1, ortam hissi 1, keşfedilebilirlik 0.5

- **Sesli asistanın ön kapısı bir fare tıklaması.** Uyandırma sözcüğü yok, otomatik bağlanma yok, cihaz tarafı ses varsayılan kapalı ve üretimdeki cihaz hiç ses yakalama yeteneği duyurmuyor.
- **Niyet eşleme elle yazılmış 6.558 satırlık deterministik bir tablo.** 103 makul cümlenin 59'u hiçbir yeteneğe yönlenmiyor. Örnekler: *"Saat kaç?"*, *"Bana hatırlat…"*, *"Bunu hatırla…"*, *"Maillerime bak."*, *"Bu hafta ne var?"*, *"Perşembeki toplantıyı iptal et."*, *"Araştırmayı iptal et."*, *"Sesini kıs."*, *"Neler yapabilirsin?"*, *"Ekran görüntüsü al."*
- **Yedi ölçülmüş yanlış yönlendirme** var ve bunlar daha tehlikeli: *"Otomatik güncellemeleri kapat"* → **ekran otomasyonunu kapatıyor**; *"Dosyayı gönder"* → **mail gönderimi**; *"Bunu yazdır"* → **operatör yazma**; *"Bir hedef ekle…"* → **takvim önerisi**.
- **Web:** 6 sayfa, **hiç gezinme yok** (layout yalnızca `<body>{children}</body>`), `/artifacts` çıkışsız, kurulu PWA tek bağlantısı olan sayfada açılıyor. Cockpit'in 27 panelinin **13'ü boş**, biri kalıcı `HTTP 422`.
- **Hata netliği:** merkezî Türkçe hata sözlüğü yok; başarısız araştırmalarda sahibe Python istisna metni gösteriliyor (üretimdeki 19 koşunun 10'u başarısız); dört yerde ham hata/enum/derleme logu **sesli okunuyor**.
- **Görünmez özellikler:** tekrarlayan ve varlık tetikli rutinler, uyanma selamlaması (politikası hazır, her tikte hesaplanıyor, hiç tüketilmiyor), uyandırma şarkısı ayarı, deneyim dersleri, `clock.now`, medya ses seviyesi, araştırma odağı, panik anahtarı, konuşmacı kaydı.

### 2.23 Ölü / Erişilemez Kod

- Modül düzeyinde ölü kod **yok**; 143 bin satırda yalnızca 2 gerçek ölü sınıf.
- 243 REST rotasının tamamı bağlı; 37 router'ın tamamı mount edilmiş; 117 sesli aracın tamamı canlı.
- **Asıl ölü yüzey cihaz tarafında:** 85 yetenekten 40'ının bulutta çağıranı yok.
- Ölü/atıl seam'ler: `ClaudeSkillGenerator` (yapılandırılsa bile hata), `ClaudeAppGenerator` (koşulsuz hata), `ClaudeExecutivePlanner` (`NotImplementedError`), `NarrationEngine` (çağıranı yok), `ReleaseExecutor` (çağıranı yok), `Grant.SECURITY_REVIEW_CANDIDATE` (tüketicisi yok), `plans.open_terminal` (çağıranı yok ve çağrılsa hata verir), `native.install`/`native.launch`/`native.fix` (arka uç yok).

---

## 3. ÖZELLİK TABLOSU

| ÖZELLİK | BUGÜN | KANIT | OLGUNLUK | EKSİK | İYİLEŞTİRME |
|---|---|---|---|---|---|
| Gerçek zamanlı ses | Çalışıyor, tarayıcıya bağlı | 97 oturum, canlı sağlık | 4 | Oturum süpürgesi, mikrofon kaybı tespiti, 60 dk yenileme | Süpürücü + `track.onended` + proaktif yeniden bağlanma |
| Uzun metin seslendirme | Yalnızca metin | Motorun çağıranı yok | 1 | Ses üretimi | Seam imzasını gerçek sağlayıcıya uydur |
| Telaffuz | Araç metnine uygulanıyor | 0 kural üretimde | 2 | Persona enjeksiyonu, ses arayüzü | Sözlüğü oturum talimatına taşı |
| Bellek yazma | Yalnızca araştırma yazıyor | 8 satır, hepsi episodik | 4 | Sesli giriş | `memory.remember` aracı |
| Bellek kullanımı | Hiç | Persona'da bellek yok | **1** | Enjeksiyon + geri getirme çağrısı | Oturum talimatına top-k kalıcı bellek |
| Anlamsal gömme | Deterministik n-gram | Sağlık: `deterministic-ngram` | 2 | Gerçek gömme sağlayıcısı | Anahtarla seçilen sağlayıcı + reindex rotası |
| Öz model | Otomatik yenileniyor | 443 modül / 8334 sembol | 4 | Çalışan sürüm ilişkisi | `runtime` provenans satırları |
| Dünya modeli | Çalışıyor ama yanlış sayıyor | `tasks.running=10` | 3 | Doğru terminal durum kümesi | RUNTIME gerçeği + takılan görev süpürgesi |
| Aktivite defteri | Çalışıyor | 1441 olay | 3 | Ses tekilleştirme | Geri doldurmaya doğal anahtar koruması |
| Kendini geliştirme motoru | Gerçek, bağlı değil | 7 koşu, aday `db9ed85` | 3 | Giriş, çalıştırıcı, kayıt görünürlüğü | Fırsat→kusur köprüsü + kayıt POST'u |
| Kendini iyileştirme | Demo servis | Üretimde 0 olay | 1 | Gerçek hedef | (Şimdilik genişletme) |
| Operatör: uygulama/pencere | Çalışıyor, dar | 12/32 yetenek | 3 | Tıklama, tuş, UIA | Üç bildirimsel plan ekle |
| Operatör: planlama | Sabit planlar | Kaynak beyanı | 2 | Yeniden planlama | GÖZLE→KARAR→UYGULA→DOĞRULA→YENİDEN PLANLA |
| Varlık | Girdi tabanlı çalışıyor | `sources:["input"]` | 2 | Cihaz tarafı kamera | Kamera hatasını bildir + duruş sinyali kararı |
| Ekran kontrolü | Çalışıyor | Canlı ortam kararı | 3 | Rutin yolu onayı | Ayrı nitelendirme koşusu |
| Alarm | Çok iyi | 7 rutin, cihaz yedeği | 4 | Çalma bildirimi, tek saat | Heartbeat alanı + tek gecikme eşiği |
| Sabah brifingi | Sese bağlı | Alarm yolundan çağrılmıyor | 1 | Tarayıcısız teslim | Karşılamaya brifing metnini ekle |
| Rutinler | Tek tetikleyici kullanılıyor | 7/7 `at` | 2 | Ses + arayüz | `routine.*` araçları + panel |
| Belge okuma | Gerçek ayrıştırıcılar | Oracle testleri | 4 | OCR, görsel, arşiv | (Sonra) |
| Belge arama | **Üretimde bozuk** | Cihaz reddi | 2 | Mutlak yol çözümü | Klasör adını yola çevir |
| Dosya yazma | Yok (bilinçli) | Yapısal red | 1 | Yönetilen mutasyon | Geri alma günlüklü `file.write` ailesi |
| Artefakt fabrikası | Çok iyi | 76 artefakt, 21 render | 4 | 4 biçim üretimde kanıtsız, düzenleme yok | Üretim duman testi + aktör provenansı |
| Uygulama fabrikası | **Üretimde bozuk** | 0 uygulama | 1.5 | Manifest uyumu, gerçek üreteç | Şablon manifest'lerini düzelt + fikstür sözleşmesi |
| Yerel fabrika | Kanıtlı | 26.16 PROVEN_REAL | 3.5 | install/launch/fix ölü | Ölüleri bağla veya kaldır |
| Yaratıcı | Yalnızca Pillow | 0 koşu | 1 | Görsel üretimi, OCR, teslim | Varsayılanı Paint yap + çıktıyı diske teslim et |
| 3B | Blender gerçek | 0 üretim sahnesi | 3 | Üretim yolu | Sahne oluşturma yolu + ilk gerçek koşu |
| Yürütme özerkliği | Üç şablon | 5 koşu, 4 partial | 2 | Genel planlama | Adım türlerini aç + telafiyi düzelt |
| Yetenek doğuşu | Erişilemez | Katalog boş | 1 | Kayıt yüzeyi | `POST /v1/genesis/catalogue` + runs rotası |
| Yedekleme | Kanıtlı | Host tatbikatı | 4 | Host dışı kopya | Kimlik bilgisi + ikinci depo |
| Felaket kurtarma | Yok | Tek host | **1** | Host dışı + kurtarma denetçisi | Astra dalını kur + off-host |
| Kimlik/yetki | Güçlü | Denetlendi | 4 | Mutlak oturum yaşı | Yaş tavanı + kapsam |
| Sır hijyeni | İyi ama sızıntı var | `/v1/world/facts` | 3 | Redaksiyon hattı | Log + sağlık + dünya modeli redaksiyonu |
| Bildirim | Yalnızca alarm sesi | Sağlayıcılar atıl | **1** | Toast / push | `desktop.notify` yeteneği |
| CI | 7 job yeşil | HEAD | 2.5 | Web testleri, PE testleri | CI'a ekle |
| Kanıt kalitesi | Yüksek | 31/31, 159/159 | 3.5 | Stage 1-6, sahte-nazik testler | Fikstür sözleşmeleri |
| Operasyon | Tek süreç | 8 döngü | 2 | Denetim, saklama, ölçüm | Döngü gözetimi + saklama + SLO örnekleri |
| UX / keşfedilebilirlik | Zayıf | 59/103 yönlenmiyor | 0.5-1.5 | Gezinme, yetenek listesi, hata sözlüğü | Nav + "ne diyebilirim" + Türkçe hata tablosu |

---

## 4. ÖNCELİK TABLOSU

| # | ÖZELLİK | NEDEN | EFOR | RİSK | BEKLENEN FAYDA |
|---|---|---|---|---|---|
| 1 | Göç hatasının sürümü durdurması | Bozuk şemayla "sağlıklı" sürüm mümkün | Saatler | Düşük | Sürüm güvenliği |
| 2 | `file.search` kök uyumu | Belge araması üretimde tamamen bozuk | Saatler | Düşük | Bir özellik ailesi geri geliyor |
| 3 | Uygulama şablonu manifest'leri | Uygulama fabrikası tamamen bozuk | Saatler | Düşük | İkinci aile geri geliyor |
| 4 | Dünya modelinden parola redaksiyonu | Sır sızıntısı | Saatler | Düşük | Sır hijyeni |
| 5 | Ölü oturum + takılan görev süpürgesi | Yanlış durum, yutulan brifingler | Saatler | Düşük | Doğru durum, teslim edilen bildirim |
| 6 | `desktop.notify` (masaüstü bildirimi) | Anayasa sözü tutulamıyor | Günler | Orta | Sahibe ulaşabilme |
| 7 | Bellek: sesli yazma + oturuma enjeksiyon | Kişisel asistanın çekirdeği | Günler | Orta | "Öğret, bir daha söyleme" |
| 8 | Host dışı yedek | Host kaybında her şey gider | Saatler + kimlik bilgisi | Düşük | Gerçek felaket kurtarma |
| 9 | Kurtarma denetçisini kurmak | Sürüm sonrası bozulmayı kimse görmüyor | Sahip onayı | Orta | Otomatik toparlanma |
| 10 | Web testlerini CI'a almak | 1472 test hiçbir kapıda değil | Saatler | Düşük | Regresyon koruması |
| 11 | Rutinleri sesle/arayüzle erişilebilir yapmak | Yazılmış en büyük görünmez özellik | Günler | Düşük | Tekrarlayan otomasyon |
| 12 | Türkçe hata sözlüğü | Sahip Python istisnası okuyor | Günler | Düşük | Güven ve anlaşılırlık |
| 13 | Web gezinme çubuğu | Sayfalar arası geçiş yok | Saatler | Düşük | Temel kullanılabilirlik |
| 14 | Yanlış yönlendirmeleri düzeltmek | "Güncellemeleri kapat" ekranı kapatıyor | Saatler | Düşük | Güvenli yönlendirme |
| 15 | Cihaz sahtelerini gerçeğe uydurmak | Dört canlı kusurun kök nedeni | Günler | Düşük | Bu sınıfın sonu |

---

## 5. TOP 10 HIZLI KAZANIM (saatler)

1. **Sürüm betiğine `pipefail` ekle, göçü borulamadan çalıştır** ve sağlık kapısına alembic sürüm doğrulaması koy.
2. **`file.search` köklerini çöz:** bulut klasör adını mutlak yola çevirsin (veya cihaz kapalı sözlüğü öğrensin) + cihazın kuralını okuyan bir test.
3. **`cli-tool`, `task-tracker`, `static-page` manifest'lerini cihazın kabul ettiği şekle getir** (`run`, `port`, `<port>`).
4. **`/v1/world/facts` ve log hattına redaksiyon uygula** (13 desen zaten yazılı).
5. **Ölü gerçek zamanlı oturumları ve takılan görevleri süpür** (mevcut saklama süpürücüsüne iki lambda).
6. **`creative.design` varsayılanını Paint yap** (bugün yapısal olarak başarısız) ve cockpit yaratıcı panelinin yolunu düzelt.
7. **Web testlerini ve linterleri CI'a ekle** (`pnpm test`, `pnpm lint`).
8. **Gezinme çubuğu ekle** (altı bağlantı + çıkış), `/artifacts` çıkmazını kapat.
9. **İki yanlış yönlendirmeyi düzelt:** `otomatik` kelimesi tek başına ekran politikasını değiştirmesin; çıplak `gönder` mail göndermesin.
10. **"Neler yapabilirsin?" niyeti ve aracı** — kayıtlı araç listesinden üretilen bir yanıt.

## 6. TOP 10 ORTA İYİLEŞTİRME (günler)

1. **Belleği oturuma bağla:** `memory.remember` aracı + persona talimatına top-k kalıcı bellek enjeksiyonu.
2. **`desktop.notify` yeteneği** ve tüm duyurucuların ona düşmesi.
3. **Rutinleri erişilebilir yap:** `routine.create/list/cancel` araçları + niyetler + cockpit paneli; uyanma selamlamasını varsayılan bir varlık-tetikli rutine bağla.
4. **Sabah karşılamasına brifing metnini ekle** (hava + gecelik iş + haber), tarayıcısız tek WAV olarak.
5. **Merkezî Türkçe hata sözlüğü** (sunucu + web) ve sesli okunan ham hataların temizlenmesi.
6. **Cihaz sahtelerini tek ve katı hale getir**, paylaşılan manifest fikstürleri (`packages/protocol/…`) ve C# tarafında "dosya yoksa başarısız ol" kuralı.
7. **Araştırma yetimlerini süpür**, `research.cancel` aracı ekle, DEEP modunu açıkça argümanla seçilebilir yap.
8. **Yerel fabrikanın üretim döngüsünü kapat:** `counts_parsed` onurlandırılsın, artefakt indirme cihaz üzerinden olsun, ölü araçlar bağlansın veya kaldırılsın.
9. **Döngü gözetimi + saklama politikaları + üç yeniden deneme fırtınasının sınırlanması.**
10. **Operatöre üç bildirimsel plan ekle** (tuş, kısayol, UIA tetikleme) — ölü yeteneklerin en değerli beşi.

## 7. TOP 10 STRATEJİK İYİLEŞTİRME

1. **Cihaz tarafı sesli döngü** (uyandırma sözcüğü + konuşmacı doğrulama) — tarayıcı bağımlılığını bitirir.
2. **Tek "sahibe ulaş" servisi**: bildirim merdiveni (toast → cihaz sesi → push → gelen kutusu), tek dürüst teslim makbuzu, sahte sağlayıcı yapısal olarak "iletildi" diyemesin.
3. **Niyet yönlendirmesini modele devret, deterministik katmanı güvenlik kalkanı yap** — kritik fiiller (dur, gönder, işle, dağıt) deterministik kalsın, gerisi araç kaydından çözülsün.
4. **Kendini geliştirme motorunu sahip onayıyla erişilebilir yap:** fırsat→kusur köprüsü, sahibin PC'sinde çalışan bir runner, kayıtların deftere ve cockpit'e düşmesi. Otonom dağıtım yok.
5. **Operatörü GÖZLE→KARAR→UYGULA→DOĞRULA→YENİDEN PLANLA döngüsüne taşı** ve mevcut graf motorunun (Temporal) içine yerleştir.
6. **Yönetilen dosya mutasyon omurgası** (geri alma günlüğü + tur bazlı onay) — "sahibin dosyalarına dokunamama" sınırını güvenle kaldırır.
7. **Anlamsal bellek** (gerçek gömme sağlayıcısı + yeniden indeksleme) ve deneyim motorunun zamanlanması.
8. **Kurtarma denetçisi + host dışı yedek + SLO ölçümü** — "iyi kurtarma makinesi" ile "gerçekten kurtaran sistem" arasındaki fark.
9. **Gerçek üreteç (model destekli) uygulama fabrikası** — mevcut doğrulayıcı ve cihaz kanıt zinciri korunarak.
10. **Tek yetenek manifestosundan türetilen arayüz** — boş aileler panel doğurmasın, yeni araç günü geldiğinde hem seste hem ekranda görünsün.

## 8. ŞİMDİLİK GENİŞLETİLMEMESİ GEREKENLER

1. **Çoklu cihaz / M29** — talimat gereği kapsam dışı; tek cihaz deneyimi henüz erişilebilir değil.
2. **Yeni artefakt biçimleri** — mevcut dördü üretimde hiç kullanılmamış.
3. **Yeni 3B/Unity yetenekleri** — üretimde sıfır sahne; önce üretim yolu.
4. **Yeni yaratıcı sağlayıcılar (Photoshop/Illustrator sürücüsü)** — mevcut tek gerçek yol (Pillow) sahibe ulaşmıyor.
5. **Genesis'in LLM üreteci** — ön kapı yokken üreteç açmak riski artırır.
6. **Kendini iyileştirme motorunun üretime alınması** — hedef servis üretim imajında yok; PROVEN_PROXY olarak kalsın.
7. **Yeni sesli araç eklemek** — 117 aracın 28'i zaten hiç kullanılmamış; önce erişilebilirlik.
8. **Mail/takvim yazma yolları** — sağlayıcı yapılandırılmadan genişletme anlamsız.
9. **Otonom dağıtım** — anayasa gereği ve terfi sınıfını okuyan kod yokken tehlikeli.

## 9. SAHİPTEN GEREKEN KARARLAR

1. **Host dışı yedek hedefi** (S3 uyumlu kova + anahtar) — felaket kurtarmanın tek eksiği, kod hazır.
2. **Kurtarma denetçisinin kurulumu** — üç incelemeden onaya hazır çıktı, hâlâ kurulmadı.
3. **`db9ed85` adayının kaderi** — motorun ürettiği düzeltme; düzeltilmiş tabloya göre asla otomatik terfi etmez.
4. **Cihaz tarafı sesin açılması** — tarayıcısız sesli asistan için gerekli; mikrofon her zaman açık bir süreç demek, bu bilinçli bir mahremiyet kararı.
5. **TTS sağlayıcı kredisi** — sabah karşılaması bugün sağlayıcı kotası yüzünden susuyor.
6. **Mail/takvim hesabı** — bu aileleri canlandırmak istiyorsan sağlayıcı bilgileri gerekiyor.

---

**READY FOR ROADMAP DESIGN = YES**

*Bu rapor ölçümdür; hiçbir kod değiştirilmedi, üretimde yalnızca salt okunur sorgular yapıldı.*
