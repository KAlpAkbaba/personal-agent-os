# ADR (numarayı lead verir): Önemli olunca telefon çalsın - Pushover acil öncelik basamağı (AlarmRung)

Tarih: 2026-10-05 · Kart: `urgent-alert-rung` · Öneri: `team/proposals/2026-10-05-onemli-olunca-telefon-calsin.md` (sahip onayladı)
Entegrasyon planı: `team/plans/urgent-alert-rung-integration.md` · Durum: ÖNERİLDİ (bu kart paketi kurar; bağlama ayrı kart)

## Bağlam

Merdiven `toast → sound → push → inbox` (`app/notifications/ladder.py`, sıra `models.LADDER`). Hiçbir basamak
iPhone sessizdeyken/Rahatsız Etme'deyken çalmaz; hiçbiri sahibin gördüğünü bilmez (push'un `True`'su = servis kabul
etti). Twilio araması (jarvis-calls-owner) ayrı ve daha pahalı yol: deneme hesabında ayda ~75 dk, saatte 3 arama sınırı.

## Karar

1. **Sağlayıcı arayüzü** `app.urgent_alert.provider.AlarmProvider` (`configured/send/poll/cancel`); tek uygulama
   `PushoverProvider`, yalnız `httpx`, priority=2, `retry=60` (API alt sınırı 30), `expire=10800` (API üst sınırı = 3 saat,
   en çok 50 tekrar). Yeni bağımlılık yok. `callback` kullanılmaz (dışa port yok); durum makbuz sorgusuyla öğrenilir.
2. **Basamak** `AlarmRung`, `ladder.Rung` Protocol'ünü aynen uygular. `True` = Pushover mesajı kabul etti VE makbuz
   kaydedildi. Bu hâlâ "gördü" değildir; "gördü" ayrı bir olaydır (`alert.seen`), makbuz sorgusundan gelir.
3. **Yalnız önemli.** Önem satırdan tek bir yüklemle okunur (`is_important`); kurucuya verilir. Bağlama kartında bu
   yüklem jarvis-calls-owner'ın `app.telephony.policy` tür listesiyle AYNI kaynaktan beslenir (iki ayrı "önemli"
   tanımı olmaz): `security.critical`, `release.failed`'e eşlenen türler, `alarm.call_me`, `spend.unanswered`,
   `aktivra.important`, test türü. Kategori eşlemesi: güvenlik/sürüm → `sistem`, aktivra → `Aktivra`, alarm/ev nabzı →
   `ev`, nöbet/haber → `haber`, harcama → `sistem` (tutar ASLA gövdeye girmez).
4. **Sıra: `toast → alarm → sound → push → inbox`** (toast'tan sonra, push'tan önce - sahibin önerisi). Masa başındaysa
   ekranda görür, telefon çalmaz. **Bilinen tuzak (entegratör bulgusu):** ToastRung `True` = Windows toast'u gösterdi,
   kimse masada olmasa da (models.py `read_at` notu: "a toast that appeared while nobody was at the desk was delivered
   and not read"). Ev PC'si gece açıkken önemli olay toast'ta "ulaştı" sayılır ve telefon hiç çalmaz - basamağın
   bütün amacı boşa düşer. Karar: önemli satırlarda toast merdiveni ancak sahip varlığı biliniyorsa bitirir
   (companion'ın kullanıcı boşta süresi < 2 dk); varlık bilinmiyorsa ya da boştaysa toast denenmiş sayılır, merdiven
   `alarm`a iner. Bağlama kartı bunu testle sabitler; companion boşta süresini vermiyorsa ilk sürümde önemli satırda
   toast hiç bitirmez (gösterilir ama merdiven sürer) - yüksek ses, sessizlikten iyidir.
5. **Metin kuralı** (`app.urgent_alert.text`): başlık sabit `JARVIS: önemli`; gövde kapalı listeden TEK sözcük
   (`Aktivra`, `ev`, `haber`, `sistem`); `url` = sahibin tailnet'teki Cloud Core'unda o bildirimin bağlantısı (https,
   `*.ts.net` ya da yapılandırılmış kök). Ad, tutar (`1.250 TL`, `₺300`), e-posta, telefon, rakam, ikinci sözcük → ret,
   gönderim yok. Neden: Pushover ABD'de işler (KVKK yurt dışı aktarım); içerik zaten Cloud Core gelen kutusunda.
6. **Makbuz** (`poll_open_receipts`, saf): `acknowledged` → `alert.seen` (acknowledged_at); `expired` → `alert.unseen`;
   okunamayan makbuz açık kalır, 7 gün sonra `unseen` olarak kapanır (Pushover makbuzu 1 hafta tutar). Sorgu en sık
   60 sn (API sınırı 5 sn); açık makbuz yoksa sıfır istek.
7. **Görüldü başka yoldan:** sahip bildirimi web/toast'ta okursa (`read_at`), telefon boşuna 3 saat çalmasın - bağlama
   kartı makbuzu `cancel` eder ve `seen` (kaynak `inbox`) yazar.
8. **Sırlar:** uygulama anahtarı + kullanıcı anahtarı yalnız Cloud Core env dosyası (SecretStr). Makbuz sorgusu anahtarı
   URL'de taşır; httpx INFO logu URL'yi yazar → `httpx`/`httpcore` loglarında `token=` maskelenir (testle).

## Neden başkası değil

- **ntfy** (açık kaynak, kendi sunucunda): iOS Critical Alert yetkisi yok, sessizi delemez; Web Push'tan fazlası değil.
- **Telefon köprüsü (Twilio, jarvis-calls-owner):** gerçek arama ama dakika/arama kotası, deneme hesabı İngilizce uyarısı,
  saatte 3 sınır. İkisi yan yana: Pushover "önemli"nin varsayılan yolu, arama en kritik türler ve "beni ara" için.
  İkisi aynı olayda: önce alarm, `alert.unseen` ya da 10 dk görülmeme → arama politikası karar verir (bağlama kartı
  sınırına bırakılır, burada sabitlenmez).
- **Kendi iPhone uygulamamız:** Apple geliştirici 99 USD/yıl, Critical Alert yetkisi yalnız sağlık/ev güvenliği/kamu
  güvenliği uygulamalarına - alınması belirsiz.
- **Pushover istemci kütüphaneleri:** üç uç nokta için bağımlılık gereksiz; `requests` getirir.

## Sonuçlar

+ Merdiven ilk kez "gördü / görmedi" bilir; ölçü: önemli olay → görülme süresi, görülmeden süresi dolan sayısı.
− ABD'deki bir servise bağımlılık (anahtar yoksa basamak atlanır, merdiven bugünkü haline döner). 4,99 USD.
− Telefon "çalar", konuşmaz; içerik telefonda yok, bağlantı yalnız tailnet'ten açılır.

---

## BAĞLAMA KARTININ TAM METNİ (lead, jarvis-calls-owner birleşince keser)

- id: urgent-alert-wire
- title: Önemli olunca telefon çalsın - bağlama: AlarmRung merdivene (toast → alarm → push), iki anahtar, kalıcı makbuz tablosu, makbuz sorgu döngüsü, ledger alert.seen/alert.unseen, Onay Merkezi deneme düğmesi
- depends_on: urgent-alert-rung (birleşmiş), jarvis-calls-owner (birleşmiş; `app.telephony.policy` önem kaynağı ve httpx log filtresi)
- goal: `app.urgent_alert` paketini gerçek uygulama nesnesine bağla (DEVELOPMENT_POLICY: entegrasyon bitmiş işin parçası). Paket kodunu yalnız arayüz gerekirse değiştir.
- files (alan):
  - `services/api/app/notifications/models.py`: `CHANNEL_ALARM = "alarm"`; `LADDER = (toast, alarm, sound, push, inbox)`. (`delivered_via` String(16) yeter; şema değişmez.)
  - `services/api/app/notifications/ladder.py`: `default_rungs(..., alarm_rung=None)`; modül docstring'inde sıra ve "alarm `True` = kabul+makbuz, gördü değil"; ADR madde 4 toast kuralı (önemli satırda toast ancak sahip varlığı biliniyorsa bitirir; bilinmiyorsa `attempted`a yazılır, merdiven sürer).
  - `services/api/app/notifications/service.py`: yalnız madde 4 gerekirse (`next_channel` değişmez; LADDER'dan okur).
  - `services/api/app/config.py`: `urgent_alert_pushover_app_token: SecretStr = SecretStr("")`, `urgent_alert_pushover_user_key: SecretStr = SecretStr("")`, `urgent_alert_poll_interval_s: float = 60.0`, `urgent_alert_link_base: str = ""` (tailnet https kökü).
  - `services/api/app/main.py`: `_build_alarm_rung(settings)` (iki anahtar doluysa), `default_rungs`'a geçir; lifespan'de makbuz döngüsü (`app/urgent_alert/loop.py`, 60 sn, açık makbuz yoksa istek yok); kapanışta durdur.
  - `services/api/app/urgent_alert/store_sql.py` + `models.py`: `SqlReceiptStore` (`ReceiptStore` Protocol'ü).
  - `services/api/migrations/versions/<sıradaki>_urgent_alert_receipts.py`: `urgent_alert_receipts(id uuid pk, notification_id uuid fk notifications.id, receipt_id varchar(64) unique, sent_at, expires_at, closed_at null, outcome varchar(16) null [seen|unseen|cancelled], seen_at null, source varchar(16) [pushover|inbox])`, index `closed_at IS NULL`.
  - `services/api/app/ledger/vocabulary.py`: `SUBSYSTEM_URGENT_ALERT = "urgent_alert"`, `EVENT_TYPE_ALERT_SENT = "alert.sent"`, `EVENT_TYPE_ALERT_SEEN = "alert.seen"`, `EVENT_TYPE_ALERT_UNSEEN = "alert.unseen"`, `EVENT_TYPE_ALERT_REFUSED = "alert.refused"` (metin bekçisi reddi); `source_ref = notification:<id>`.
  - `services/api/app/urgent_alert/routes.py`: `POST /v1/urgent-alert/test` (oturum + step-up değil, sahibin oturumu yeter; saatte en çok 3) → `urgent_alert.test` türünde önemli bildirim; `GET /v1/urgent-alert/status` → `{configured, open_receipts, last_seen_at}` (anahtar YOK).
  - `apps/web/app/settings/` (Onay Merkezi/ayarlar): "Önemli deneme bildirimi gönder" düğmesi + "bağlı / bağlı değil" + son "görüldü HH:MM"; Kokpit'te önemli bildirimin yanında "görüldü HH:MM" / "görülmedi".
  - `infra/docker/docker-compose.prod.yml`: iki env'i ilet. `scripts/cloud/set-cloud-secret.ps1` adlarını tanısın (gerekirse).
  - Sağlık koruma testleri (`test_bounded_delivery`, `test_health_endpoint`): yeni döngü/tablo kümelere eklenir; `test_identity_enforcement`: yeni rotalar oturumlu.
  - Testler: `tests/unit/test_urgent_alert_wire.py`, `tests/integration/test_urgent_alert_receipts_pg.py`, web vitest.
- acceptance:
  1) `LADDER == ("toast","alarm","sound","push","inbox")`; önemli satır, toast başarısız → `alarm` denenir ve `delivered_via="alarm"`; önemsiz satır alarm'ı atlar (`attempted`ta `alarm` "not_reached" değil, gönderim sıfır) ve push'a iner.
  2) Önemli satır, toast "gösterildi" ama sahip varlığı bilinmiyor → merdiven `alarm`a iner (gece tuzağı testi); varlık < 2 dk → toast'ta biter.
  3) Anahtarlar boş → `default_rungs` alarm içermez ya da `available()` False; merdiven bugünkü davranışla aynı (mevcut ladder testleri değişmeden yeşil).
  4) Makbuz döngüsü (sahte sağlayıcı + Postgres): ack → `alert.seen` ledger satırı + `seen_at`; expired → `alert.unseen`; açık makbuz yokken sağlayıcıya sıfır istek; yeniden başlatma sonrası açık makbuzlar tablodan sürer.
  5) Bildirim web'de okundu (`read_at`) → makbuz `cancel` edilir, `outcome=cancelled`, `alert.seen` kaynak `inbox`.
  6) `POST /v1/urgent-alert/test` oturumsuz 401; oturumla önemli deneme bildirimi; saatte 4. istek 429. `GET status` yanıtında ve hiçbir logda anahtar değeri yok (caplog DEBUG, httpx dahil).
  7) Önem kaynağı tek: `is_important` `app.telephony.policy` tür listesinden; iki listenin ayrışmasını yakalayan test.
  8) Migrasyon: up/down Postgres'te yeşil; `test_ci_covers_every_suite.py` yeşil; ruff 0; web tsc/oxlint 0.
  9) Mutasyon RED: (a) LADDER'da alarm push'tan sonraya → test 1 kırmızı; (b) gece tuzağı kuralı kaldırılınca test 2 kırmızı; (c) cancel kaldırılınca test 5 kırmızı.
  10) Yeni bağımlılık yok.
- evidence_expected: PROVEN_AUTOMATED (yukarısı). PROVEN_REAL - READY_FOR_OWNER: iPhone sessizde + Rahatsız Etme açık; Onay Merkezi'nden "önemli deneme bildirimi gönder" → telefon çalar; "Gördüm" → Kokpit'te "görüldü HH:MM"; ikinci denemede basmadan 5 dk → hâlâ çalıyor; web'den okununca çalma durur.
- owner (Onay Merkezi, tek toplu istek): Pushover hesabı + iOS uygulaması 4,99 USD (30 gün deneme); "JARVIS" uygulaması oluştur; uygulama anahtarı + kullanıcı anahtarı `set-cloud-secret.ps1` ile; iPhone Pushover ayarında Critical Alerts (emergency) AÇ + iOS onayı; Pushover Quiet Hours kullanılıyorsa priority 2 geçsin.

## Uygulama notu (çalışan, 2026-10-05) - bağlama kartı bu imzalara göre keser

- `AlarmRung(provider, store, *, link_for, is_important=marked_important, category=category_of)`; `name = "alarm"`
  (`CHANNEL_ALARM`). Varsayılan yüklem tek saf fonksiyon `marked_important(row)` = `data_json["important"] is True`;
  kategori `data_json["alert_category"]`. Bağlama kartı ya bu iki anahtarı politika kaynağından doldurur ya da kendi
  yüklemini kurucuya verir - iki yol birden değil.
- Bağlantı kuralı yalnız `https` + `*.ts.net` + kullanıcı bilgisi yok + ≤512; "yapılandırılmış kök" (madde 5) bu kartta
  YOK. Gerekirse bağlama kartı `text.link_refusal`'a bir izinli kök parametresi ekler.
- `text.refusal` sırası: uzunluk, tutar, e-posta, telefon, rakam, ikinci sözcük, kapalı liste - ret nedeni adıyla döner;
  log yalnız nedeni yazar, metni değil.
- Makbuz kimliği `^[A-Za-z0-9]{1,64}$` değilse istek hiç yapılmaz (yol enjeksiyonu yok).
- Kayıt (`store.open`) başarısızsa makbuz `cancel` edilir ve `False` döner.
