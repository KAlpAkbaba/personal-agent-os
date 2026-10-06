# ios-client-plan - sahibin karar tablosu ve onaydan sonra kesilecek kartlar

> **SAHİP İNCELEMESİ BEKLİYOR.** Bu belge bir karar girdisidir; kod, betik, bağımlılık, hesap, indirme YOK.
> Çalışan, döngü d20261006, taban `8baa66c6dda1fd59ef8c8020803b1408cc0538b5`, 2026-10-06. Sayıların ve
> kaynakların hepsi `team/plans/ios-client-plan-integration.md`'den (entegratör; URL'ler 2026-10-06'da okundu,
> kaynak numaraları [A1]...[A23] o dosyadadır).

**Onay Merkezi'ne düşecek karar cümlesi (öneri dosyasındaki soru, aynen):**

> The order madde 4'teki iPhone uygulaması için Apple Developer Program hesabı (yıllık 99 USD) ve iOS derlemesi için bir macOS ortamı (Mac ya da kiralık bulut Mac; bu depo Windows'ta iOS üretemez) sağlanacak mı, yoksa hesapsız ve bugün hazır olan yolla (telefonda 'Ana Ekrana Ekle' web kabuğu + web push, VAPID anahtarı tek komut) devam edilecek mi - sahibin kararı.

**Değişmez kural (docs/product/PERSONALAGENTOS_V1_FEATURE_MATRIX.md:473-474, madde 373/374, aynen):**

> Merdivenin `push` basamağı yeri ayrılmış ve BOŞ: hesapsız bir taşıyıcı yazmak, hiç denenmemiş bir kanalı denenmiş göstermek olurdu

(374 APNs: "373 ile aynı gerekçe".) **Hesap gelmeden APNs koduna dokunulmaz.** `apns-provider-live` kartı
yalnız **sahip onayından sonra** ve Apple hesabı + `.p8` anahtarı sırlar deposuna girdikten sonra kesilir.

---

## 1. Karar tablosu

| | A: ücretli hesap + kiralık Mac | B: ücretli hesap + kendi Mac | C: web kabuğuyla devam |
|---|---|---|---|
| Yıllık maliyet | 99 USD + Scaleway Mac mini M1: derleme günü başına €2,64 (yılda ~10 gün ≈ €26) ya da sürekli €900/yıl; MacStadium M2.S $1.308/yıl; AWS mac2.metal ≈ $15,60/gün (24 saat en az) | 99 USD/yıl + Mac bir kez: yenilenmiş M1 ≈ 12.749-21.249 TL (**düşük güven**, sahip bakar) ya da yeni Mac mini 59.999 TL | **0** |
| Sahibin yapacağı | ADP üyeliği (ödeme, kimlik), Mac kiralama hesabı, .p8 anahtarını üretip sırlar deposuna koymak, iPhone UDID'ini kaydetmek, Xcode'a giriş | A'nın aynısı, kiralama yerine Mac'i kurmak | VAPID anahtarı (`scripts/cloud/new-vapid-key.ps1`), Safari'de "Ana Ekrana Ekle", /settings'te bildirim izni |
| İlk bildirimin telefonda görünmesine kadar kart | **2**: `apns-provider-live` + `ios-client-swift` (yakalama için +1: `ios-capture-start-stop`) | **2** (aynı kartlar; + Mac kurulumu sahip adımı) | **0** yeni kart (yol bugün hazır; `phone-push-home-screen-hint` zaten bölünmüş durumda, ipucu kartıdır, push'un önkoşulu değildir) |
| Yapılamayan | arka planda sürekli/sınırsız dinleme (iOS); Odaklanma'yı yalnız Time Sensitive deler, sahip kapatabilir; ad hoc profil yılda bir yeniden imza; 100 cihaz/yıl | A'nın aynısı; 8 GB M1'de Xcode dar | arka planda mikrofon yok (kabuk kapalıyken dinleme yok); Time Sensitive düzeyi yok; web push yalnız Ana Ekrana eklenmiş uygulamadan ve iOS 16.4+; bildirime dokununca uygulama açılır; "yakalamayı başlat/durdur" yalnız uygulama ön plandayken |
| "Kendi imzalı" (ADR-0167) karşılığı | ADP + ad hoc (App Review yok) | ADP + ad hoc | imza gerekmez (web) |
| Yeni bağımlılık | `httpx[http2]` (h2 4.4.1 / hpack 4.2.0 / hyperframe 6.1.0, MIT) - uv.lock'ta YOK, sahibin kapısı | aynı | yok |

Seçenek dışı (yazılır): ücretsiz Apple ID - **push yetkisi YOK** (Apple "Supported capabilities (iOS)" tablosu,
"Push notifications" satırının "Apple Developer" hücresi boş [A1]), profil 7 günde düşer; GitHub Actions macOS
koşucusu - depo Actions'ı 2026-09-19'dan beri kapalı (docs/HANDOFF.md:356, ADR-0172 addendum); Enterprise -
100+ çalışan ister; Hetzner'de Mac yok. AltStore benzeri yeniden imzalama yalnız entegrasyon planının tablosunda,
öneri değil.

**Sahip "web kabuğuyla devam" derse hiçbir kart kesilmez**; bu belgedeki üç kart dosyada bekler, ROADMAP satırı
değişmez.

## 2. Bölüm 3 uçlarının grep doğrulaması (taban 8baa66c6, dalda)

Komut: `grep -n '@router\.' services/api/app/{identity,mobile,artifacts,narration,voice/realtime_sessions}/routes.py`
ve `APIRouter(` önekleri. Hepsi bulundu; tek "YOK" aşağıda.

| Uç | dosya:satır |
|---|---|
| önekler | identity/routes.py:39 `/v1/identity`; mobile/routes.py:48-49 `/v1/mobile`; artifacts/routes.py:60-61 `/v1`; narration/routes.py:46-47 `/v1/narration`; voice/realtime_sessions/routes.py:41-42 `/v1/voice/realtime` |
| `POST /v1/identity/sessions` | services/api/app/identity/routes.py:157 |
| `POST /v1/identity/sessions/refresh` | identity/routes.py:209 |
| `GET /v1/identity/sessions/current` | identity/routes.py:230 |
| `DELETE /v1/identity/sessions/current` | identity/routes.py:257 |
| `GET /v1/mobile/push/providers` | services/api/app/mobile/routes.py:102 |
| `POST /v1/mobile/push/registrations` | mobile/routes.py:127 |
| `GET /v1/mobile/push/registrations` | mobile/routes.py:153 |
| `DELETE /v1/mobile/push/registrations/{id}` | mobile/routes.py:175 |
| `POST /v1/mobile/notifications/artifact-ready` | mobile/routes.py:206 |
| `GET /v1/mobile/notifications` | mobile/routes.py:274 |
| `GET /v1/mobile/share/{id}` | mobile/routes.py:334 |
| `GET /v1/mobile/share/{id}/{fmt}` | mobile/routes.py:375 |
| `GET /v1/artifacts/{id}` (`?include=body` yalnız istenince) | services/api/app/artifacts/routes.py:262 |
| `POST /v1/narration/sessions` | services/api/app/narration/routes.py:149 |
| `GET` / `PATCH /v1/narration/sessions/{id}/cursor` | narration/routes.py:190, :221 |
| `GET .../sessions/{id}/chunks/{chunk}/audio` | narration/routes.py:300 |
| `POST .../sessions/{id}/command` | narration/routes.py:362 |
| `GET /v1/voice/realtime/providers` | services/api/app/voice/realtime_sessions/routes.py:225 |
| **başlat** `POST /v1/voice/realtime/sessions` | realtime_sessions/routes.py:251 |
| `GET /v1/voice/realtime/contract` | realtime_sessions/routes.py:330 |
| `POST .../sessions/{id}/tool-calls`, `.../tool-calls/{call}/complete` | realtime_sessions/routes.py:366, :426 |
| `POST .../sessions/{id}/events` | realtime_sessions/routes.py:457 |
| `POST .../sessions/{id}/attach` | realtime_sessions/routes.py:490 |
| **durdur** `POST .../sessions/{id}/close` | realtime_sessions/routes.py:538 |
| `ApnsPushProvider` (atıl) | services/api/app/mobile/providers.py:556; `_send` :376, `httpx.Client(timeout=...)` :388 (http2 yok), 404/410 :405, `interruption-level` :598 |
| `ApnsCredentials` | services/api/app/mobile/config.py:82; env adları :22-26 |
| `PushRung` | services/api/app/notifications/ladder.py:139 |
| sözleşme | clients/reference/mobile_client.py:404 `run_flow`; services/api/tests/integration/test_mobile_reference_client.py:196, :254, :267, :278, :294, :328 |
| iOS'a özel "cihaz jetonu yenilendi" ucu | **YOK** (yeniden `POST /push/registrations` ile karşılanır) |
| `apps/ios/` | **YOK** (kart oluşturur) |
| `services/api/app/urgent_alert/text.py` | **YOK** main'de (dal `team/d20261005/worker-urgent-alert-rung`) |

## 3. Onaydan sonra kesilecek kartlar (tam metin)

Yalnız sahip **A ya da B** derse lead bu üç kartı keser ve ROADMAP 'Approved ideas' satırını yazar. Sıra:
`apns-provider-live` ve `ios-client-swift` paralel (alanları ayrık); `ios-capture-start-stop`, `ios-client-swift`
birleştikten sonra.

### 3.1 apns-provider-live - SAHİP ONAYINDAN SONRA

- **id:** apns-provider-live
- **title:** APNs taşıyıcısı canlı: .p8'den ES256 JWT (50 dk yenileme), HTTP/2, sahte APNs sunucusuna karşı sözleşme testi, 410/BadDeviceToken -> kayıt iptali, anahtarsız dürüst atlama - **sahip onayından ve Apple hesabı + .p8 anahtarı sırlar deposuna girdikten sonra**
- **roadmap_row:** Follows him outside: calendar, people, promises - and tells him at once (ROADMAP 'The order' madde 4, With him everywhere: the iPhone app - notifications at once, start/stop capture)
- **area:** services/api/app/mobile/providers.py, services/api/app/mobile/config.py, services/api/app/mobile/apns_jwt.py (yeni), services/api/app/notifications/ladder.py, services/api/tests/unit/test_apns_provider_live.py (yeni), services/api/pyproject.toml, services/api/uv.lock
- **goal:** Feature matrix 373/374 kuralı: hesapsız taşıyıcı yazılmaz; bu kart hesap geldiği için kesilir. (1) `apns_jwt.py`: `.p8` (PKCS#8 P-256) anahtardan ES256 JWT (`alg`, `kid`=Key ID, `iss`=Team ID, `iat`); `cryptography` ile (pyproject:18, uv.lock:170 - yeni bağımlılık yok), webpush/vapid.py'deki DER->r||s deseni yeniden kullanılır; belirteç 50 dakikada bir yenilenir (Apple: 20 dk'dan sık değil, 60 dk'dan seyrek değil). (2) `ApnsCredentials`'a `PAGENTOS_PUSH_APNS_KEY_P8` (sırlar deposundan; hazır `PAGENTOS_PUSH_APNS_JWT` geriye uyum için kalır). (3) `_send` APNs için `httpx.Client(http2=True)` açar (providers.py:388 bugün HTTP/1.1 - anahtar gelse bile teslimat düşerdi); `httpx[http2]` pyproject + uv.lock'a (h2 4.4.1, hpack 4.2.0, hyperframe 6.1.0, MIT; sahip onayı bu kartın kesilmesidir), THIRD_PARTY satırını lead yazar (metin entegrasyon planı §8). (4) 410 `Unregistered` ve 400 `BadDeviceToken`/`DeviceTokenNotForTopic` -> `PUSH_TOKEN_INVALID`, yeniden denenmez, çağıran kaydı iptal eder (bugün 400 retryable :411-417). (5) Gövde içeriksiz metin kuralına bağlanır (urgent_alert/text.py main'deyse ondan; değilse sabit başlık + kategori); bugün `message.title/body` süzülmeden konuyor. (6) APNs, `PushRung`'un yanında ikinci taşıyıcı. Anahtarsızken `deliver` bugünkü gibi dürüstçe durur.
- **acceptance:** 1) Önce kırmızı test: sahte APNs (`httpx.MockTransport`) 4 başlığı (`authorization: bearer`, `apns-topic`, `apns-push-type`, `apns-priority`) ve JSON gövdeyi doğrular; test içinde üretilen geçici P-256 anahtarıyla JWT'nin imzası açık anahtarla doğrulanır, `iat` 50 dk sonra yenilenir, 49. dakikada yenilenmez. 2) `http2=True`'nun istendiğini okuyan test (mutasyon: kaldırınca RED). 3) 410 ve 400 BadDeviceToken -> kayıt iptal, yeniden deneme yok; 500 -> yeniden denenir. 4) Anahtarsız: `deliver` `_require` ile durur, ağ çağrısı yok. 5) Gövdede ad/tutar/e-posta/telefon yok testi. 6) Depoda anahtar yok (`git grep 'BEGIN PRIVATE KEY'` boş). 7) Gerçek iPhone'a bir push: sahibin cihazında, sandbox ya da üretim; olmazsa READY_FOR_OWNER satırı.
- **evidence_expected:** PROVEN_AUTOMATED (birim testleri, mutasyon RED); gerçek teslimat PROVEN_REAL ya da READY_FOR_OWNER (sahibin iPhone'u + ios-client-swift kurulu). 40-hex sha.

### 3.2 ios-client-swift

- **id:** ios-client-swift
- **title:** iPhone istemcisi (Swift, apps/ios/): cihaza bağlı oturum, push kaydı, bildirim listesi, gövdesiz özet, anlatım imlecinden sürdürme, indirme, iptal - referans istemcinin sözleşmesi gerçek istemciye karşı macOS ortamında
- **roadmap_row:** Follows him outside: calendar, people, promises - and tells him at once (ROADMAP 'The order' madde 4, With him everywhere: the iPhone app - notifications at once, start/stop capture)
- **area:** apps/ios/** (yeni: Swift paketi `PagentOSCore` + Xcode uygulama hedefi + XCTest), docs/IOS_CLIENT.md (yeni)
- **goal:** `MobileReferenceClient.run_flow`'un (clients/reference/mobile_client.py:404) adımlarını Swift'te uygular, uçlar bu planın §2 tablosundaki dosya:satır'lar: kimlik değişimi `POST /v1/identity/sessions`, yenileme, whoami, çıkış; `POST /v1/mobile/push/registrations` (platform `ios`, APNs cihaz jetonu `UNUserNotificationCenter` + `registerForRemoteNotifications`); `GET /v1/mobile/notifications`; `GET /v1/artifacts/{id}` gövdesiz, `?include=body` yalnız istenince; anlatım imleci GET/PATCH + `command`; `GET /v1/mobile/share/{id}/{fmt}`; iptal edilen oturumda temiz durma. Yalnız Apple araçları (URLSession, Keychain, UserNotifications, BackgroundTasks `BGAppRefreshTask`), üçüncü taraf paket YOK. Oturum jetonu Keychain'de (UserDefaults değil). Sunucu adresi tailnet HTTPS (`https://pagentos-core.tail0e6789.ts.net`). Ad hoc imza (sahibin ADP ekibi), iPhone UDID kayıtlı. Sunucu tarafına dokunmaz; eksik uç çıkarsa ALAN_ISTEGI.
- **acceptance:** 1) Önce kırmızı XCTest: `PagentOSCore` paketinin `runFlow`'u, test_mobile_reference_client.py:196/:254/:267/:278/:294/:328'in altı davranışını (bütün sözleşme, gövde istenmeden çekilmez, bildirim yoksa asılı kalmaz, iptalde temiz durur, cihaz iptali oturumu bitirir, istenince yeniden kimlik) macOS'ta `swift test` ile, gerçek api'ye (dev yığını, tailnet) karşı koşar. 2) Mutasyon: `include=body`'yi varsayılan yapınca 2. test RED. 3) `xcodebuild` ad hoc .ipa üretir; derleme günlüğü rapora. 4) Sahibin iPhone'una kurulum ve `artifact-ready` sonrası telefonda bildirim (apns-provider-live birleşmişse) - sahibin cihazı: READY_FOR_OWNER satırı. 5) Depoda sertifika/profil/anahtar yok.
- **evidence_expected:** PROVEN_AUTOMATED (macOS'ta swift test, gerçek api'ye karşı - Windows'ta koşamaz; macOS ortamı yoksa NOT_RUN değil, kart kesilmez); telefonda görünen bildirim PROVEN_REAL ya da READY_FOR_OWNER. 40-hex sha.

### 3.3 ios-capture-start-stop

- **id:** ios-capture-start-stop
- **title:** iPhone'dan yakalamayı başlat/durdur: M12 gerçek zamanlı ses oturumuna bağlanma (başlat, attach, olaylar, kapat), AVAudioSession `audio` arka plan kipi - arka plan sınırı cihazda ölçülür
- **roadmap_row:** Follows him outside: calendar, people, promises - and tells him at once (ROADMAP 'The order' madde 4, With him everywhere: the iPhone app - notifications at once, start/stop capture)
- **area:** apps/ios/Sources/PagentOSCapture/** (yeni), apps/ios/Tests/PagentOSCaptureTests/** (yeni), apps/ios uygulama hedefinin Info.plist'i (`UIBackgroundModes: audio`, `NSMicrophoneUsageDescription` Türkçe), docs/IOS_CLIENT.md
- **goal:** `ios-client-swift` birleştikten sonra. Başlat `POST /v1/voice/realtime/sessions` (realtime_sessions/routes.py:251), sağlayıcı/sözleşme (:225, :330), telefona geçiş `attach` (:490, docs/M12_REALTIME_VOICE_SPEC.md §7), `events` (:457; network_lost, kesinti), araç çağrısı köprüsü (:366, :426), durdur `close` (:538). Ses bacağı önce sağlayıcının WebSocket yolu (`URLSessionWebSocketTask`, bağımlılıksız); WebRTC (libwebrtc, yeni ikili bağımlılık) yalnız ölçüm gerektirirse ve sahibin kapısından. `AVAudioSession(.playAndRecord)`; kayıt ön planda başlar, arka planda `audio` kipiyle sürer; arama/Siri kesintisi `events` ile sunucuya bildirilir ve oturum dürüstçe kapanır. Arka planda kaydı BAŞLATMANIN mümkün olup olmadığı Apple sayfasından alıntılanamadı: kart bunu cihazda ölçer, varsayım olarak taşımaz.
- **acceptance:** 1) Önce kırmızı XCTest: başlat -> attach -> events -> close sırası gerçek api'ye karşı (dev yığını, sahte ses sağlayıcısı), close sonrası oturum `closed`; mutasyon (close çağrısını atla) RED. 2) Kesinti testi: simüle edilen `AVAudioSession.interruptionNotification` -> `events` çağrısı. 3) Cihaz ölçümü: ön planda başlat, ekranı kilitle, 5 dk sonra sunucuda oturum hâlâ akıyor mu; arka planda başlatma denemesi sonucu - rakamlar rapora; sahibin cihazıysa READY_FOR_OWNER. 4) Yeni üçüncü taraf paket yok.
- **evidence_expected:** PROVEN_AUTOMATED (macOS'ta XCTest, gerçek api'ye karşı); arka plan davranışı PROVEN_REAL (cihazda ölçüm) ya da READY_FOR_OWNER. 40-hex sha.

## 4. Hesapsız yol bugün hazır (C'nin dayanağı)

`apps/web/app/manifest.ts` (:29 `start_url: "/core"`, :30 `scope: "/"`, :31 `display: "standalone"`),
`apps/web/app/layout.tsx:12` `appleWebApp`, `apps/web/public/sw.js` (:22 `push`), `apps/web/app/settings/WebPushSettings.tsx`,
`services/api/app/webpush/` (VAPID, RFC 8291, `PushRung` ladder.py:139), `scripts/cloud/new-vapid-key.ps1`, tailnet HTTPS
`https://pagentos-core.tail0e6789.ts.net` (docs/HANDOFF.md:36). Sınır [A23] (WebKit, 2023-02-16): iOS/iPadOS **16.4+**,
web push yalnız **Ana Ekrana eklenmiş** uygulamadan, izin kullanıcı dokunuşuyla. Sahibin denemesi: `briefing-ladder-fallback`
READY_FOR_OWNER satırı (VAPID anahtarı, /settings'te izin, bir araştırma başlat -> 'Brifing' push'u).

**Geri alma:** bu belge tek dosyadır; silmek yeter.
