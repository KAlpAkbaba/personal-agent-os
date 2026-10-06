# ios-client-plan - entegrasyon planı (iPhone uygulaması: sahibin kararı için sayılar)

> **SAHİP İNCELEMESİ BEKLİYOR.** Bu belge bir karar girdisidir, iş kartı değildir. Kod, betik,
> bağımlılık, hesap, indirme YOK. Entegratör, döngü d20261006, taban `8baa66c6dda1fd59ef8c8020803b1408cc0538b5`,
> 2026-10-06. Bütün URL'ler **2026-10-06** tarihinde okundu (aşağıda "okundu" tarihi ayrıca yazılmadıkça).

**Onay Merkezi'ne düşecek karar cümlesi (öneri dosyasındaki soru, aynen):**

> The order madde 4'teki iPhone uygulaması için Apple Developer Program hesabı (yıllık 99 USD) ve iOS derlemesi için bir macOS ortamı (Mac ya da kiralık bulut Mac; bu depo Windows'ta iOS üretemez) sağlanacak mı, yoksa hesapsız ve bugün hazır olan yolla (telefonda 'Ana Ekrana Ekle' web kabuğu + web push, VAPID anahtarı tek komut) devam edilecek mi - sahibin kararı.

**Karar (entegratör): `none exists` + `adapt`.** Bugün depoya eklenecek bir kütüphane yok. Sahip "hesap açılsın"
derse: APNs sunucu tarafı için tek YENİ Python bağımlılığı `h2` (httpx'in `http2` ekstrası; sahibin kapısı, §4/§8);
iOS istemcisi için üçüncü taraf paket önerilmez (URLSession / AVAudioSession / UserNotifications yeterli, §3).

**Değişmez kural (docs/product/PERSONALAGENTOS_V1_FEATURE_MATRIX.md:473-474, madde 373/374, aynen):**

> Merdivenin `push` basamağı yeri ayrılmış ve BOŞ: hesapsız bir taşıyıcı yazmak, hiç denenmemiş bir kanalı denenmiş göstermek olurdu

(374 APNs: "373 ile aynı gerekçe".) Buradan: **hesap gelmeden APNs koduna dokunulmaz; `apns-provider-live` kartı
yalnız sahip onayından ve Apple hesabı + .p8 anahtarı geldikten SONRA kesilir.**

---

## 1. Apple'ın dağıtım yolları ve iOS'ta "kendi imzalı" ne demek

| Yol | Ücret | Cihaza kurulum | Push (APNs) | Süre / sınır | Kaynak |
|---|---|---|---|---|---|
| Ücretsiz Apple ID ("Apple Developer", kişisel ekip) | 0 | Xcode ile, kablo/ağ | **YOK** | profil 7 gün; 3 cihaz, cihaz başına 3 uygulama, 10 App ID (7 gün) | [A1], [A2] |
| Apple Developer Program (ADP) | 99 USD / üyelik yılı ("or in local currency where available") | ad hoc (UDID kayıtlı), TestFlight, App Store | VAR | yılda ürün ailesi başına 100 cihaz; yıllık yenileme | [A2], [A3], [A4] |
| Enterprise (ADEP) | 299 USD/yıl | şirket içi | VAR | **uymaz:** "Have 100 or more employees", D-U-N-S, tüzel kişi, yalnız çalışanlara | [A5] |
| Yeniden imzalama araçları (AltStore vb.) | - | ücretsiz Apple ID'nin 7 günlük imzasını otomatik yeniler | ücretsiz ID'de YOK | AltStore lisansı AGPL-3.0 [A10] | **yalnız tablo, öneri değil** |

**Ücretsiz Apple ID'nin push yetkisi - Apple'ın kendi sayfasından.** [A1] "Supported capabilities (iOS)":
sayfanın cümlesi "The capabilities available to an iOS provisioning profile depend on your program membership."
Tablonun ham HTML'inden okunan satırlar (sütunlar `ADP | ADEP | Apple Developer`):

```
Push notifications            YES YES [EMPTY]
Time Sensitive Notifications  YES YES [EMPTY]
Background modes              YES YES YES
```

Aynı sayfa "Apple Developer" sütununu şöyle tanımlar: "No cost is associated with this agreement and developers
can't distribute apps." Yani **ücretsiz hesapla APNs push ve Time Sensitive bildirim YOK**; arka plan kipleri var.
(Not: WebFetch özeti bu tabloyu yanlış özetledi - "hepsi var" dedi; karar ham HTML'den `curl` ile okunan
`<td></td>` boş hücreye dayanır.)

Ücretsiz hesabın sınırları [A2] (aynen): "You can register up to 10 App IDs, which expire after 7 days. You can
register up to 3 devices, which expire after 7 days. You can install up to 3 apps per device. Provisioning profiles
that enable apps to be installed on a device will expire 7 days from issuance. You'll need to rebuild and reinstall
your app to your device after expiration."

**"Kendi imzalı" (ADR-0167, 2026-09-16; docs/DECISIONS.md:12772) iOS'ta neye karşılık gelir.** Windows'ta paket,
cihazda üretilen kendi sertifikamızla imzalanır ve sahip bir kez güvenir. **iOS'ta bunun karşılığı yoktur**: iOS
yalnız Apple'ın verdiği bir imza zincirine güvenir. İki gerçek yol kalır:
- ücretsiz Apple ID imzası: 7 günde bir yeniden derle + kur (Mac gerekir), **push yok** - "bildirimler hemen"
  hedefini karşılamaz;
- ADP ad hoc imzası: sahibin kendi ekibi imzalar, App Store/App Review yok, iPhone'un UDID'i kayıtlı; profil ve
  dağıtım sertifikası **1 yıl** geçerli (ikincil kaynak [A11]; Apple'ın kendi sayfasından doğrulanmadı) - yılda bir
  yeniden imzalama.
ROADMAP satırı (docs/ROADMAP.md:453) bunu zaten söyler: "self-signed install per the 2026-09-16 decision needs an
Apple developer account - the owner's call". Bu planın karşılığı: **"kendi imzalı" iOS'ta = ADP + ad hoc**.

Cihaz sınırı [A4] (aynen): "register up to 100 of the following devices, per product family, per membership year".

## 2. macOS derleme ortamı - seçenekler ve fiyatlar

Bu depo Windows'ta iOS üretemez (Xcode yalnız macOS). Gereken sürüm [A6]: güncel kararlı **Xcode 26.x** ("Xcode
26.6 - macOS Tahoe 26.2 - macOS Tahoe 26.x, iOS SDK 26.5, deployment targets iOS 15-26.5"); Xcode 27 "macOS Tahoe
26.6 or later". iOS 17/18 hedefi için Xcode 16.x da yeter (Xcode 16.4: "macOS Sequoia 15.3 - macOS Tahoe 26.1.x",
"iOS 15-18"), ama ad hoc dışına (TestFlight) yükleme güncel SDK ister - plan Xcode 26 + iOS 17 en düşük hedef
varsayar. Mac mini (M1, 2020) macOS Tahoe'yu destekler [A12].

| Seçenek | Fiyat (kaynak, okunma 2026-10-06) | Kural | Yıllık (ortam) |
|---|---|---|---|
| Scaleway Mac mini M1 8 GB / 256 GB | €0,11/saat, €75/ay [A7] | **en az 24 saat** kiralama (Apple lisansı) [A8] | sürekli: €900; günlük kiralama: €2,64/gün |
| Scaleway Mac mini M2 16 GB | €0,17/saat, €115/ay [A7] | 24 saat [A8] | sürekli: €1.380; €4,08/gün |
| MacStadium M2.S 8 GB | $109/ay, aylık ön ödeme [A9] | ay başı ön ödeme | $1.308 |
| AWS EC2 mac2.metal (M1) | $0,6498/saat (AWS duyuru fiyatı; güncel tablo sayfada görünmedi) [A13] | "per second with a 24-hour minimum allocation period" [A14] | sürekli ≈ $5.692; $15,60/gün |
| Hetzner | Mac yok (Hetzner'in ürün listesinde Mac/Apple silicon yok - bulut çekirdeğimizin sağlayıcısı) | - | - |
| GitHub Actions macOS koşucusu | $0,062/dk [A15] | **SEÇENEK DEĞİL:** depo Actions'ı 2026-09-19'dan beri kapalı (docs/DECISIONS.md:13212 ADR-0172 addendum, docs/HANDOFF.md:356 "GitHub Actions kapalı (sahip ödeyemiyor)") | - |
| Kendi Mac: ikinci el / yenilenmiş Mac mini M1 8 GB/256 GB | ≈ 12.749 - 21.249 TL (TR yenilenmiş satıcılar; arama özeti [A16], sayfa doğrudan 403 verdi - **düşük güven**, sahip kendi bakar) | 8 GB RAM Xcode için dar | tek seferlik |
| Kendi Mac: yeni Mac mini (M6, 16 GB/256 GB) | "Başlangıç fiyatı: 59.999 TL" [A17] | - | tek seferlik |

**Yıllık toplam (hesap + ortam), ilk yıl:**

| | Hesap | Ortam | İlk yıl | Sonraki yıllar |
|---|---|---|---|---|
| A1 ücretli + Scaleway M1, yılda ~10 derleme günü | 99 USD | ≈ €26 | ≈ 99 USD + €26 | aynı |
| A2 ücretli + Scaleway M1 sürekli | 99 USD | €900 | ≈ 99 USD + €900 | aynı |
| A3 ücretli + MacStadium M2.S | 99 USD | $1.308 | ≈ $1.407 | aynı |
| B ücretli + kendi ikinci el M1 | 99 USD | 12.749-21.249 TL (bir kez) | 99 USD + Mac | 99 USD |
| C web kabuğu | 0 | 0 | 0 | 0 |

"~10 derleme günü" bir tahmindir: üç kartın macOS'ta koşan adımları (Xcode kurulumu ~1 gün, kart başına 1-3 gün)
+ yıllık yeniden imzalama 1 gün. Kiralık Mac'te kurulum (Xcode indirme ~ birkaç GB) her yeni kiralamada tekrarlanır
- "kiralamayı aç/kapat" yerine kart süresince tek kiralama önerilir.

## 3. Swift istemcinin uygulayacağı sözleşme

Kaynak sözleşme: `clients/reference/mobile_client.py` `MobileReferenceClient.run_flow` (satır 404-522) ve
`services/api/tests/integration/test_mobile_reference_client.py` (testler: :196 bütün sözleşme, :254 gövde
istenmeden çekilmez, :267 bildirim yoksa asılı kalmaz, :278 iptal edilen oturum temiz durur, :294 cihaz iptali
oturumu bitirir, :328 istenince yeniden kimlik doğrular). Her uç depoda grep ile doğrulandı (sunucu tarafı dosya:satır):

| Adım (run_flow) | Uç | Sunucu |
|---|---|---|
| signed_in (kimlik değişimi, cihaza bağlı oturum) | `POST /v1/identity/sessions` | services/api/app/identity/routes.py:157 |
| session yenile | `POST /v1/identity/sessions/refresh` | identity/routes.py:209 |
| connected (whoami) | `GET /v1/identity/sessions/current` | identity/routes.py:230 |
| sign_out | `DELETE /v1/identity/sessions/current` | identity/routes.py:257 |
| push sağlayıcıları | `GET /v1/mobile/push/providers` | services/api/app/mobile/routes.py:102 (önek :48-49 `/v1/mobile`) |
| push_registered | `POST /v1/mobile/push/registrations` | mobile/routes.py:127 |
| kayıt listesi / silme | `GET`, `DELETE /v1/mobile/push/registrations[/{id}]` | mobile/routes.py:153, :175 |
| (sunucu) artifact-ready | `POST /v1/mobile/notifications/artifact-ready` | mobile/routes.py:206 |
| notified (bekleme) | `GET /v1/mobile/notifications?limit=` | mobile/routes.py:274 |
| summary_fetched (gövdesiz) | `GET /v1/artifacts/{id}` | services/api/app/artifacts/routes.py:262 (önek :61 `/v1`) |
| tam gövde (yalnız istenince) | `GET /v1/artifacts/{id}?include=body` | artifacts/routes.py:262 |
| narration başlat | `POST /v1/narration/sessions` | services/api/app/narration/routes.py:149 (önek :47) |
| imleç oku / kaydet | `GET`, `PATCH /v1/narration/sessions/{id}/cursor` | narration/routes.py:190, :221 |
| narration_resumed | `POST /v1/narration/sessions/{id}/command` | narration/routes.py:362 |
| parça sesi | `GET /v1/narration/sessions/{id}/chunks/{chunk}/audio` | narration/routes.py:300 |
| share_index | `GET /v1/mobile/share/{id}` | mobile/routes.py:334 |
| downloaded | `GET /v1/mobile/share/{id}/{fmt}` | mobile/routes.py:375 |
| **yakalamayı başlat** (M12) | `POST /v1/voice/realtime/sessions` | services/api/app/voice/realtime_sessions/routes.py:251 (önek :42 `/v1/voice/realtime`) |
| sağlayıcı / sözleşme | `GET .../providers`, `GET .../contract` | realtime_sessions/routes.py:225, :330 |
| cihaz değiştir (telefona geç) | `POST .../sessions/{id}/attach` | realtime_sessions/routes.py:490; docs/M12_REALTIME_VOICE_SPEC.md §7 |
| olay (network_lost vb.) | `POST .../sessions/{id}/events` | realtime_sessions/routes.py:457 |
| araç çağrısı köprüsü | `POST .../tool-calls`, `.../complete` | realtime_sessions/routes.py:366, :426 |
| **yakalamayı durdur** | `POST .../sessions/{id}/close` | realtime_sessions/routes.py:538 |
| APNs taşıyıcısı | (sunucu içi) `ApnsPushProvider` | mobile/providers.py:556-614 - **atıl** |
| iOS'a özel "cihaz jetonu yenilendi" ucu | - | **YOK** (bugünkü `POST registrations` yeniden kayıtla karşılanır) |

Sağlayıcı tarafında "ios" platformu zaten tanımlı: providers.py:288 (fake: `"ios", "android", "web", "headless"`),
:572 (APNs: `"ios", "ipados", "macos"`).

**iOS API'leri:** `URLSession` (REST + `URLSessionWebSocketTask`), `AVAudioSession` (`.playAndRecord`),
`UNUserNotificationCenter` (izin, `registerForRemoteNotifications` -> cihaz jetonu), `BackgroundTasks`
(`BGAppRefreshTask`, bildirim listesini tazelemek için - zamanlaması iOS'un elinde). Gerçek zamanlı ses bacağı:
M12 §10 B/D WebRTC ister; iOS'ta WebRTC Google'ın libwebrtc'si (yeni ikili bağımlılık) demektir -
`ios-capture-start-stop` kartı önce sağlayıcının WebSocket bacağını (bağımlılıksız) dener, WebRTC'yi ancak ölçüm
gerektirirse sahibin kapısına getirir.

**iOS arka plan sınırları:**
- Arka plan kipleri ücretsiz hesapta da var (§1 tablo), ama iOS, `audio` kipi açık bir uygulamayı yalnız ses
  oturumu sürerken canlı tutar: Apple "Enabling this category means your app can play background audio if you're
  using the Audio, AirPlay, and Picture in Picture background mode" [A18]. **Arka planda sürekli, sınırsız dinleme
  yok**; kayıt ön planda başlatılır, uygulama arka plana geçince sürer; sistem kesintisi (arama, Siri) oturumu
  keser. Arka planda kaydı *başlatmanın* engellendiği Apple sayfasından alıntılanamadı - `ios-capture-start-stop`
  kartı bunu cihazda ölçer, varsayım olarak taşımaz. ROADMAP:453 "background listening is limited by iOS" der.
- Ad hoc dağıtımda App Review yoktur (App Store Connect'e yükleme yok); "audio" kipinin kötüye kullanım
  incelemesi bu yolda devreye girmez - kural yine de kayıt sürerken ekranda gösterge ister (iOS turuncu nokta).
- Bildirimler Odaklanma / Bildirim Özeti'ne tabidir; `time-sensitive` düzeyi: "can break through system controls
  such as Notification Summary and Focus. The user can turn off the ability for time sensitive notification
  interruptions." [A19]. Bu yetki ücretsiz hesapta YOK (§1). Bugünkü `build_request` zaten
  `"interruption-level": "time-sensitive"` gönderiyor (providers.py:596-599).

## 4. APNs sunucu tarafı

**Var olan:** `ApnsPushProvider` (providers.py:556): üretim/sandbox adresi (:560-561), başlıklar `authorization:
bearer <jwt>`, `apns-topic`, `apns-push-type: alert`, `apns-priority` 10/5, `apns-collapse-id` (:581-591), `aps`
gövdesi + `pagentos_*` veri (:592-603); `deliver` anahtarsızken `_require` ile dürüstçe durur (:605-610);
`_send` 404/410'u `PUSH_TOKEN_INVALID` (yeniden denenmez) yapar (:401-410). `ApnsCredentials`
(services/api/app/mobile/config.py:82-102): `team_id`, `key_id`, `bundle_id`, `jwt` (hazır JWT, env
`PAGENTOS_PUSH_APNS_JWT`), `sandbox`; `configured = bundle_id and jwt`.

**Apple'ın kuralları** [A20], [A21], [A22] (Apple JSON belge uçlarından okundu):
- "APNs supports only the ES256 algorithm"; başlık `alg`, `kid` (10 karakter Key ID); gövde `iss` (Team ID), `iat`.
- "Refresh your token no more than once every 20 minutes and no less than once every 60 minutes. APNs rejects any
  request whose token contains a timestamp that's more than one hour old."
- "Use HTTP/2 and TLS 1.2 or later"; `api.push.apple.com:443`, `api.sandbox.push.apple.com:443`; yük en çok
  "4 KB (4096 bytes)".
- "410 Unregistered The device token is inactive for the specified topic"; "Don't retry ... BadDeviceToken,
  DeviceTokenNotForTopic, Forbidden, ExpiredToken, Unregistered, or PayloadTooLarge."

**Eksik olan (hepsi hesaptan SONRA, `apns-provider-live` kartı):**
1. `.p8`'den ES256 JWT üretimi + 50 dakikada bir yenileme (20 dk alt, 60 dk üst sınırın içinde). `cryptography`
   zaten var: services/api/pyproject.toml:18 `cryptography>=43`, uv.lock:170 `cryptography 50.0.1` - ES256 için
   **yeni bağımlılık yok** (PyJWT gerekmez; başlık+gövde base64url + `ec.ECDSA(SHA256)` + DER->raw r||s).
   Webpush'un VAPID imzası (services/api/app/webpush/vapid.py) aynı ES256 deseni - yeniden kullanılır.
2. **HTTP/2:** bugünkü `_send` `httpx.Client(timeout=...)` açar (providers.py:388) - `http2=True` yok, yani gerçek
   APNs'e HTTP/1.1 gider ve **anahtar gelse bile teslimat başarısız olur** (bu bir bulgu; bugün atıl yol olduğu
   için zarar yok). httpx'in HTTP/2'si `h2` paketini ister. **services/api/uv.lock'ta `h2`, `hpack`, `hyperframe`
   YOK** (`grep -c '"h2"' services/api/uv.lock services/browser/uv.lock services/recovery-supervisor/uv.lock` ->
   0/0/0); `httpx 0.28.1` (uv.lock:390) bağımlılıkları yalnız `anyio, certifi, httpcore, idna`. Kök dizinde
   uv.lock yok. **=> YENİ bağımlılık, sahibin kapısı** (`httpx[http2]` -> h2 4.4.1 MIT, hpack 4.2.0 MIT,
   hyperframe 6.1.0 MIT; PyPI JSON, 2026-10-06).
3. Sahte APNs sunucusuna karşı birim testi (httpx `MockTransport` ile; HTTP/2 çerçevesi değil sözleşme test edilir)
   + `http2=True`'nun gerçekten istendiğini okuyan test.
4. 410 -> kaydı iptal (`_send` bugün `PUSH_TOKEN_INVALID` atıyor; çağıranın kaydı `invalidate` ettiği yol APNs
   için testle kanıtlanmalı); 400 `BadDeviceToken` / `DeviceTokenNotForTopic` da yeniden denenmez sınıfa girmeli
   (bugün 400 `DEPENDENCY_UNAVAILABLE, retryable=True` olur - :411-417).
5. `ApnsCredentials`'a `.p8` anahtarının sırlar deposundan okunması (`PAGENTOS_PUSH_APNS_KEY_P8`), hazır JWT
   env'i geriye uyum için kalır.
6. Merdiven: APNs, `PushRung`'un (services/api/app/notifications/ladder.py:139) yanına ikinci taşıyıcı olarak.

## 5. Hesapsız yol - BUGÜN hazır, ve iOS'taki dürüst sınırları

Depoda hazır (dosya yollarıyla):
- `apps/web/app/manifest.ts` - `start_url: "/core"` (:29), `scope: "/"` (:30), `display: "standalone"` (:31);
- `apps/web/app/layout.tsx:12` - `appleWebApp: { capable: true, title: "Çekirdek", statusBarStyle: "black-translucent" }`;
- `apps/web/public/sw.js` - `push` olayı (:22) ve bildirim tıklaması;
- `apps/web/app/settings/WebPushSettings.tsx` - izin + abonelik;
- `services/api/app/webpush/` - `vapid.py` (RFC 8292), `ece.py` (RFC 8291 şifreleme), `service.py`
  (`build_payload`, `send_to_all`), `provider.py`, `routes.py`; merdivenin `PushRung`'u (ladder.py:139);
- `scripts/cloud/new-vapid-key.ps1` - VAPID anahtarı tek komut;
- tailnet HTTPS: `https://pagentos-core.tail0e6789.ts.net` (docs/HANDOFF.md:36 TELEFON satırı, 49.3, PROVEN_REAL).

iOS sınırları [A23] (WebKit, 2023-02-16): "iOS and iPadOS 16.4"; web push yalnız **"added to the Home Screen"**
uygulamadan; izin isteği "in response to direct user interaction — such as tapping on a 'subscribe' button";
"Notifications for Home Screen web apps on iPhone and iPad integrate with Focus". Ek dürüst sınırlar:
arka planda mikrofon yok (web kabuğu kapalıyken dinleme yok); bildirime dokununca uygulama açılır;
Time Sensitive düzeyi web push'ta seçilemez.

Sahibin deneme listesi: `briefing-ladder-fallback` READY_FOR_OWNER satırı
(team/reports/d20261006/briefing-ladder-fallback-inspector-1.md): "VAPID anahtarını üret
(scripts/cloud/new-vapid-key.ps1), telefonda /settings'ten bildirim iznini ver, ... Kokpit'ten bir araştırma
başlat ve bitmesini bekle" -> "telefona 'Brifing' push'u gelir". Ayrıca `phone-push-home-screen-hint` kartı
(team/plans/d20261006-split-iphone-app-apple-account.json:16): Safari sekmesinde ya da iOS 16.4 altında ayar
sayfası "Paylaş -> Ana Ekrana Ekle" yolunu Türkçe söyler.

## 6. Alternatif çerçeveler (öneri yok)

| Çerçeve | Lisans (GitHub API, 2026-10-06) | Son itme | iOS derlemesi için Mac + Apple hesabı | Not |
|---|---|---|---|---|
| Swift (yerel) | Apple araç zinciri | - | Mac + push için ADP | bağımlılıksız |
| Expo / React Native | MIT (expo/expo), MIT (react/react-native) | 2026-10-06 | Mac **ya da** EAS Build bulutu (ücretli/kotalı, yeni hesap); push için ADP | JS bağımlılık ağacı büyük |
| Flutter | BSD-3-Clause | 2026-10-06 | Mac + ADP | Dart, yeni araç zinciri |
| Capacitor | MIT | 2026-10-05 | Mac + ADP | web kabuğunu sarar; push için yine APNs ve ADP |

Hiçbiri "Mac ya da Apple hesabı olmadan iPhone'a push" sağlamaz; seçim karar tablosunu değiştirmez.

## 7. KVKK / güvenlik

- Cihaz jetonu: sunucuda bugün `hash_token`/`fingerprint` (providers.py:57-66) ile; ham jeton yalnız teslimat
  için saklanır - yeni kart aynı deseni korur.
- APNs `.p8` anahtarı, Key ID, Team ID: yalnız sırlar deposunda - `.\scripts\secret-store.ps1 -Set <AD>` sonra
  `.\scripts\cloud\set-cloud-secret.ps1 -Name <AD>` (set-cloud-secret.ps1:10-11 deseni). **Depoya anahtar girmez**;
  kartın testleri test içinde üretilen geçici bir P-256 anahtarı kullanır.
- Bildirim gövdesi: APNs yükü Apple sunucularından geçer. İçeriksiz metin kuralı
  `services/api/app/urgent_alert/text.py` ("the alarm is a doorbell, not a letter": sabit başlık, kapalı kategori
  listesinden bir kelime, yalnız tailnet bağlantısı; ad/tutar/e-posta/telefon reddedilir) **henüz main'de değil** -
  dal `team/d20261005/worker-urgent-alert-rung` (eac170d4, 3f123ed4). `apns-provider-live` kartı bu kuralı
  main'deki yerinden içe aktarır; kural main'e girmeden APNs gövdesi yalnız sabit başlık + kategori taşır.
  Bugünkü `build_request` `message.title/body`'yi olduğu gibi koyar (providers.py:594) - kart bunu kurala bağlar.
- Telefondaki uygulama oturum jetonunu Keychain'de tutar (UserDefaults değil).

## 8. THIRD_PARTY_COMPONENTS satırı

**Bugün eklenecek bileşen yok.** Sahip "hesap açılsın" der ve `apns-provider-live` kesilirse, satır metni hazır:

> | h2 (+ hpack, hyperframe) via `httpx[http2]` | 4.4.1 / 4.2.0 / 6.1.0 | MIT | services/api | APNs HTTP/2 (Apple yalnız HTTP/2 kabul eder) | saf Python, yerel ikili yok, ağ yalnız api.push.apple.com:443 | sahip onayı: <tarih> |

Ayak izi (tahmin, yöntem: saf Python paketleri, PyPI tekerlek boyutları sırasıyla ~60/~35/~15 KB mertebesi; içe
aktarma yalnız APNs teslimatında, tembel): bellek < 5 MB, CPU ihmal edilir. Ölçüm kartın kendisinde yapılır.

**Geri alma:** bu belge tek dosyadır; silmek yeter. Kesilen kartlar kendi geri almalarını taşır.

---

## Kaynaklar (hepsi 2026-10-06'da okundu)

- [A1] https://developer.apple.com/help/account/reference/supported-capabilities-ios (ham HTML, curl)
- [A2] https://developer.apple.com/support/compare-memberships/
- [A3] https://developer.apple.com/programs/whats-included/ - "The Apple Developer Program is 99 USD per membership year, or in local currency where available."
- [A4] https://developer.apple.com/help/account/devices/devices-overview/
- [A5] https://developer.apple.com/programs/enterprise/
- [A6] https://developer.apple.com/xcode/system-requirements/
- [A7] https://www.scaleway.com/en/pricing/apple-silicon/
- [A8] https://www.scaleway.com/en/docs/apple-silicon/faq/ (arama özeti: "Due to license constraints, the minimum lease for Apple silicon is 24 hours")
- [A9] https://www.macstadium.com/pricing
- [A10] https://api.github.com/repos/altstoreio/AltStore (spdx AGPL-3.0)
- [A11] https://html2app.dev/docs/credentials/ios/certificates-profiles.html , https://help-center.testapp.io/en/articles/13882831-understanding-provisioning-profiles-in-app-development (ikincil)
- [A12] https://support.apple.com/en-mz/122867 (macOS Tahoe uyumlu bilgisayarlar)
- [A13] https://aws.amazon.com/blogs/aws/use-amazon-ec2-m1-mac-instances-to-build-test-macos-ios-ipados-tvos-and-watchos-apps/
- [A14] https://aws.amazon.com/ec2/instance-types/mac/
- [A15] https://docs.github.com/en/billing/reference/actions-runner-pricing
- [A16] https://www.yenilio.com/yenilenmis-mac/mac-mini/mac-mini-m1 (403; arama özeti), https://www.trendyol.com/pd/apple/mac-mini-m1-8c-cpu-8gb-ram-256gb-ssd-gumus-mini-pc-mgnr3tu-a-p-98529414
- [A17] https://www.apple.com/tr/shop/buy-mac/mac-mini (sayfa JSON'u: `"fullPrice-comparative":"Başlangıç fiyatı: 59.999 TL"`)
- [A18] https://developer.apple.com/documentation/avfoundation/configuring-your-app-for-media-playback
- [A19] https://developer.apple.com/documentation/usernotifications/unnotificationinterruptionlevel/timesensitive
- [A20] https://developer.apple.com/documentation/usernotifications/establishing-a-token-based-connection-to-apns
- [A21] https://developer.apple.com/documentation/usernotifications/sending-notification-requests-to-apns
- [A22] https://developer.apple.com/documentation/usernotifications/handling-notification-responses-from-apns
- [A23] https://webkit.org/blog/13878/web-push-for-web-apps-on-ios-and-ipados/
- PyPI JSON: https://pypi.org/pypi/h2/json , /hpack/json , /hyperframe/json ; GitHub API lisansları: flutter/flutter, expo/expo, react/react-native, ionic-team/capacitor
