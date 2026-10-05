# Entegrasyon planı: urgent-alert-rung (Pushover acil öncelik basamağı)

Tarih: 2026-10-05 · Entegratör · Kart: `urgent-alert-rung` · Öneri: `team/proposals/2026-10-05-onemli-olunca-telefon-calsin.md`
ADR taslağı: `team/plans/urgent-alert-rung-adr.md` (bağlama kartının tam metni orada).

## Karar: ADAPT (hizmet = Pushover, istemci = kendi kodumuz, yalnız `httpx`)

| Aday | Ne | Karar | Neden |
|---|---|---|---|
| **Pushover REST API** (kapalı ticari servis) | priority=2 acil mesaj + makbuz | **Hizmet olarak ADOPT** | iOS Critical Alert yetkisi Apple'dan alınmış (sessiz anahtarı + Rahatsız Etme'yi deler); makbuz API'si "gördü" bilgisini verir; tek seferlik 4,99 USD. |
| `python-pushover` / `chump` / `pushover-complete` (PyPI, MIT/BSD) | istemci kütüphaneleri | **ADOPT ETME** | Üç uç nokta (messages, receipts, cancel) için bağımlılık gereksiz; çoğu `requests` getirir (ağaçta `httpx>=0.27` var, `services/api/pyproject.toml:23`); sahte sunucu testinde `httpx.MockTransport` deseni ağaçta zaten var (`tests/unit/test_calendar_caldav_provider.py`). Aynı karar jarvis-calls-owner'da Twilio için verildi (SDK yok). |
| ntfy (Apache-2.0/GPL-2.0, kendi sunucunda) | açık kaynak push | **HAYIR** | iOS'ta Critical Alert yetkisi yok; sessizi delemez → bugünkü Web Push'tan fazlası değil. |
| Apprise (BSD-2) | çok servisli bildirim | **HAYIR** | Makbuz/ack sorgusunu soyutlamaz; büyük bağımlılık. |
| Pushsafer / SIGNL4 | Critical Alert'li rakipler | **HAYIR (şimdilik)** | SIGNL4 abonelik ücretli (ekip ürünü); Pushsafer kredi bazlı. `AlarmProvider` arayüzü ikinci sağlayıcıya açık kalır. |

**Yeni bağımlılık YOK.** `pyproject.toml` / `uv.lock` değişmez (kabul 8).

## Doğrulanan API gerçekleri (pushover.net/api, /api/receipts, /pricing, /privacy - 2026-10-05 okundu)

- `POST https://api.pushover.net/1/messages.json` form alanları: `token` (uygulama), `user` (kullanıcı), `title` (≤250), `message` (≤1024 UTF-8, zorunlu), `url` (≤512), `url_title` (≤100), `priority=2`, `retry` (**≥30 sn**), `expire` (**≤10800 sn**), en çok 50 tekrar (expire ne olursa olsun), isteğe bağlı `tags` (virgüllü; cancel_by_tag için), isteğe bağlı `callback` (dışa açık URL - **KULLANMIYORUZ**, port açmayız).
- Başarı: HTTP 200, `{"status":1,"request":"...","receipt":"<30 karakter>"}`. Yanıt başlıkları `X-Limit-App-Limit/Remaining/Reset` (aylık kota).
- Hatalar: **4xx** = girdi geçersiz, aynısını yeniden deneme; **429** = aylık kota doldu; **5xx** = geçici, en erken 5 sn sonra yeniden. Aynı anda en çok 2 HTTP isteği; keep-alive önerilir.
- `GET https://api.pushover.net/1/receipts/<receipt>.json?token=<APP_TOKEN>` → `status, acknowledged (0/1), acknowledged_at (unix), acknowledged_by (kullanıcı anahtarı!), acknowledged_by_device, last_delivered_at, expired (0/1), expires_at, called_back, called_back_at`. **5 sn'den sık sorgulanmaz.** Makbuz **1 hafta** sorgulanabilir.
- `POST https://api.pushover.net/1/receipts/<receipt>/cancel.json` (form `token`); `POST .../receipts/cancel_by_tag/<tag>.json`.
- Kota: kullanıcı başına **ayda 10.000 mesaj ücretsiz**. iOS lisansı **4,99 USD tek seferlik** (iPhone+iPad), 30 gün deneme.
- iOS: Critical Alert **varsayılan kapalı**; Pushover uygulamasında "Critical Alerts for high-priority" (4.2'den beri emergency için ayrı anahtar) açılır, iOS onay penceresi + ses seviyesi seçilir. Pushover'ın kendi "Quiet Hours" ayarında priority 2'nin geçmesi ayrıca açılmalı (READY_FOR_OWNER listesine).
- Gizlilik: "Pushover ABD merkezli, veriyi ABD sunucularında işler"; teslimi doğrulanan mesaj silinir, doğrulanmayan en çok 21 gün tutulur; alt işleyici Apple APNs.

## Seam (dikiş) ve dosyalar - bu kart

Yeni paket `services/api/app/urgent_alert/` (alan dışına dokunulmaz; `notifications/`, `config.py`, `main.py`, `ledger/` yalnız OKUNUR):

- `__init__.py` - dışa açılanlar.
- `provider.py` - `AlarmReceipt(receipt_id, sent_at, expires_at)`, `ReceiptStatus(acknowledged_at: datetime|None, expired: bool, last_delivered_at: datetime|None, expires_at)`, `AlarmProvider` Protocol: `configured() -> bool`, `send(title, body, url) -> AlarmReceipt | None`, `poll(receipt_id) -> ReceiptStatus | None`, `cancel(receipt_id) -> bool`. `None` = gönderilemedi/okunamadı (istisna yok).
- `pushover.py` - `PushoverProvider(app_token: str, user_key: str, *, client: httpx.Client | None = None, retry_s=60, expire_s=10800, timeout_s=10.0, base_url="https://api.pushover.net/1")`. Kurucu `retry_s<30` veya `expire_s>10800` veya `expire_s<retry_s` ise `ValueError` (yanlış kurulum yüksek sesle). `poll` yanıtından `acknowledged_by` **okunmaz/saklanmaz** (o kullanıcı anahtarıdır).
- `rung.py` - `AlarmRung(provider, store, *, is_important: Callable[[NotificationRow], bool], category_of: Callable[[NotificationRow], str|None], link_for: Callable[[NotificationRow], str], name="alarm")`. `available()` = `provider is not None and provider.configured()`. `deliver(row)`: önemli değilse `False` (gönderim YOK); `text.compose(category, link)` reddederse `False` + log (kategori adı değil yalnız "text_refused" ve satır id); `send` `None` ise `False`; makbuz `store.open(...)` ile kaydedilir, kayıt başarısızsa `provider.cancel` + `False`; ikisi de olursa `True`.
- `receipts.py` - `ReceiptStore` Protocol (`open(notification_id, receipt) / list_open() / close(receipt_id, outcome, at)`), `InMemoryReceiptStore`, `AlertEvent(kind: Literal["seen","unseen"], notification_id, receipt_id, at)`, saf `poll_open_receipts(store, provider, now) -> list[AlertEvent]`: açık makbuz yoksa sağlayıcıya istek YOK; `acknowledged` → `seen` (at = acknowledged_at) + kapat; `expired` ve görülmemiş → `unseen` (at = expires_at) + kapat; `poll` `None` → açık kalır, olay yok; `now > sent_at + 7 gün` ve hâlâ okunamıyorsa → `unseen` + kapat (makbuz Pushover'da 1 hafta yaşar - sonsuz açık kayıt olmaz).
- `text.py` - `TITLE = "JARVIS: önemli"`, `CATEGORIES = ("Aktivra", "ev", "haber", "sistem")`, `compose(category, link) -> (title, body, url)` ya da `TextRefused`. Gövde = tam olarak bir kategori sözcüğü; bağlantı yalnız `https://` ve tailnet (`*.ts.net`) ya da bağlama kartının verdiği izinli kök, yol yalnız `/notifications` ya da `/notifications/<uuid>`, sorgu/parça/yüzde kaçışı yok; rakam/`TL`/`₺`/`@`/telefon kalıbı/ikinci sözcük → ret; toplam ≤1024, url ≤512, başlık ≤250.
- `tests/unit/test_urgent_alert_rung.py` - kabul 1-5 (aşağıda).

## Güvenlik bulguları (işçi mutlaka uygular)

1. **ANAHTAR SIZINTISI - httpx INFO logu.** Makbuz sorgusu uygulama anahtarını **URL'de** (`?token=`) taşır (Pushover başka yol vermiyor). httpx her isteği INFO'da `HTTP Request: GET <tam url>` diye loglar. jarvis-calls-owner aynı hatayı Twilio SID'inde buldu (onların ADR'si madde 7). Çözüm: `pushover.py` bir `logging.Filter` ile `httpx`/`httpcore` kayıtlarında `token=<...>` değerini `***` yapar (idempotent ekleme) VE kendi logları yalnız durum kodu/makbuz id'sinin ilk 6 karakteri yazar. Test: `caplog.set_level(logging.DEBUG)` + **gerçek httpx Client + MockTransport** ile gönder/sorgu/iptal; tüm kayıtların `getMessage()` ve `args`'ında iki anahtar değeri yok. jarvis-calls-owner birleşince iki filtre tek ortak yere taşınabilir (bağlama kartına not).
2. 4xx/5xx gövdesi (`errors[]`) loglanmaz; yalnız `status_code` ve `len(errors)`. `user_key` POST gövdesinde gider, URL'de değil.
3. `poll` yanıtındaki `acknowledged_by` (kullanıcı anahtarı) hiçbir nesneye/loga girmez.
4. `callback` parametresi kullanılmaz: Cloud Core dışa port açmaz (anayasa: outbound-only).
5. Cihaz güvenliği: telefona bizim kodumuz kurulmaz; App Store uygulaması. Sürücü/ekran yakalama/kimlik bilgisi erişimi yok. **Dışarı konuşan:** yalnız `api.pushover.net` (ABD) - mesaj başlığı, tek kategori sözcüğü ve tailnet bağlantısı (tailnet dışından açılamaz).

## Testler (kabul eşlemesi)

- Sağlayıcı (MockTransport): form `priority=="2"`, `retry>=30`, `expire<=10800`, title/message/url; 200+receipt → `AlarmReceipt`; 400, 429, 500, `httpx.TimeoutException`, bozuk JSON, `status:0` → `None` ve istisna yok; poll/cancel aynı; kurucu sınırları `ValueError`.
- Log: yukarıdaki madde 1 testi.
- Rung: `ladder.Rung` gerçek Protocol'ü ile yapısal uyum - Protocol `runtime_checkable` DEĞİL, bu yüzden `isinstance` çalışmaz; test `rung: Rung = AlarmRung(...)` atamasını içerir ve `hasattr`/`inspect.signature` ile `available()`/`deliver(row)` imzasını `ladder.Rung` üyeleriyle karşılaştırır (ruff/mypy de görür). `NotificationRow` DB'siz kurulabilir (`NotificationRow(id=uuid4(), kind=..., title=..., body=...)`).
- Makbuz + metin: kartın kabul 4-5 listesi.
- Süre: tamamı saf/bellek içi; < 2 sn beklenir (kabul < 30 sn).

## Ayak izi

- Bellek: modül + `httpx.Client` ≈ 1-2 MB RSS (yöntem: httpx istemcisi zaten süreçte yüklü; ek yalnız modül kodu ve bir bağlantı havuzu - tahmin, ölçülmedi). CPU: açık makbuz varken 60 sn'de bir HTTPS GET (bağlama kartı); açık makbuz yoksa sıfır istek. Ağ: mesaj başına ~1 KB gönderim; makbuz başına en çok 180 sorgu (3 saat/60 sn).
- Kota: 10.000/ay'a karşı "önemli" günde birkaç; poll istekleri mesaj kotasından sayılmaz (yalnız mesajlar sayılır - API sayfası kotayı "messages" olarak tanımlar).

## Geri alma

Bu kart yalnız yeni paket + test ekler: `git revert` ya da paketi silmek yeter; hiçbir şey onu çağırmaz (bağlama ayrı kart). Bağlamadan sonra: iki anahtar boşaltılınca `available()` False → merdiven bugünkü haline döner (merdiven kuralı).

## THIRD_PARTY_COMPONENTS.md satırı (lead işler)

```
## Pushover (acil öncelikli bildirim, priority=2)

Role: `services/api/app/urgent_alert/` - "önemli" işaretli bildirimler için telefonu sessizde ve
Rahatsız Etme'de çaldıran basamak (iOS Critical Alert), sahip "Gördüm"e basana kadar 60 sn'de bir
tekrar (en çok 3 saat); makbuz sorgusuyla "gördü / görülmedi" bilgisi.

- Tür: kapalı ticari servis (Pushover, LLC, ABD). Kod tarafında kütüphane YOK: üç REST uç noktası
  (`/1/messages.json`, `/1/receipts/<id>.json`, `/1/receipts/<id>/cancel.json`) mevcut `httpx`
  ile, `AlarmProvider` arayüzünün arkasında. Yeni bağımlılık yok.
- Maliyet: iOS uygulaması 4,99 USD tek seferlik (30 gün deneme); ayda 10.000 mesaj ücretsiz.
- Veri: ABD sunucuları, alt işleyici Apple APNs; teslim edilen mesaj silinir, edilmeyen ≤21 gün.
  Bu yüzden mesaj içeriksizdir: sabit başlık "JARVIS: önemli" + kapalı listeden tek kategori sözcüğü
  + tailnet bağlantısı (`app.urgent_alert.text`, testle). Kişisel veri, ad, tutar gitmez (KVKK).
- Sırlar: uygulama anahtarı ve kullanıcı anahtarı yalnız Cloud Core env dosyasında (SecretStr);
  makbuz sorgusu anahtarı URL'de taşıdığı için httpx/httpcore loglarında maskelenir.
- `callback` kullanılmaz (dışa port açılmaz); durum Cloud Core'dan sorgulanır (outbound-only).
- Doğrulandı: 2026-10-05, pushover.net/api, /api/receipts, /pricing, /privacy.
```

## READY_FOR_OWNER (Onay Merkezi'ne, bağlama kartından sonra)

1. pushover.net hesabı + iOS Pushover uygulaması (4,99 USD, 30 gün deneme ile başlanabilir).
2. Pushover'da bir "Application" oluştur (ad: JARVIS) → uygulama anahtarı; hesap sayfasındaki kullanıcı anahtarı. İkisi `set-cloud-secret.ps1` ile sırlar deposuna.
3. iPhone Pushover ayarları: "Critical Alerts" (emergency) AÇ + iOS onayı + ses seviyesi; Pushover Quiet Hours kullanılıyorsa "priority 2 geçsin" AÇ.
