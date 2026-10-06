# ADR (numara lead'in): iPhone'da push ayarı "desteklemiyor" yerine Ana Ekran ipucu

- Görev: `phone-push-home-screen-hint` (cycle d20261006), öneri
  `team/proposals/2026-10-06-feed-iphone-app-apple-account.md` (hesapsız yol).
- Durum: kabul edildi (çalışan kararı, geri alınabilir).

## Bağlam

iOS web push'u yalnız **Ana Ekrana eklenmiş ve ana ekrandan açılmış** web uygulamasına
verir, ve yalnız **iOS 16.4 ve üstünde**. Safari sekmesinde `window.PushManager` hiç yoktur.
`WebPushSettings` bu durumda `pushSupport() === "unsupported"` görüp "Bu tarayıcı push
bildirimlerini desteklemiyor." diyordu. Sahip denemede bunu "bozuk" diye okur; oysa
yapılacak tek şey Ana Ekrana eklemektir. Depodaki geri kalan her şey (manifest standalone,
`sw.js` push/notificationclick, `/v1/webpush`, `new-vapid-key.ps1`) zaten hazır.

## Karar

1. **Ayrı durum.** iOS'ta `PushManager`'ın yokluğu "desteklemiyor" değil "henüz eklenmedi"
   demektir. `resolvePhase()`'in ilk adımı `homeScreenState(readHomeScreenInput())`:
   `ios_needs_home_screen` -> `needs_home_screen` (Paylaş -> Ana Ekrana Ekle -> Çekirdek'i
   ana ekrandan aç -> Ayarlar -> Push bildirimleri), `ios_too_old` -> `ios_too_old`
   ("iOS 16.4 ve üstü ister"). Bu iki durumda sunucuya gidilmez (VAPID anahtarı sorulmaz)
   ve "Aç" düğmesi çıkmaz. `ios_home_screen` ve `not_ios` bugünkü sırayla aynen devam
   eder (unsupported / no_server_key / denied / subscribed / not_subscribed). İzin yalnız
   `registerAndSubscribe` içinde, "Aç" düğmesinin tıklamasında istenir - değişmedi.
2. **Saf fonksiyon.** `homeScreenState(input)` DOM'suzdur; tarayıcı okumaları
   (`navigator.standalone`, `matchMedia("(display-mode: standalone)")`, `userAgent`,
   `maxTouchPoints`) tek yerde, `readHomeScreenInput()` içinde, navigator/window yokken
   güvenli varsayılanlarla (SSR'da import hata vermez). Böylece her cihaz biçimi gerçek UA
   dizgileriyle doğrudan test edilir; bileşen testi `resolvePhase` + `WebPushSettingsView`
   ile, DOM'suz `react-dom/server` düzeninde.
3. **iOS tespiti.** UA'da `iPhone|iPad|iPod`, ya da `Macintosh` + `maxTouchPoints > 1`
   (iPadOS Safari'nin varsayılan masaüstü UA'sı; gerçek Mac'te dokunma noktası yok).
4. **Sürüm 16.4.** Apple web push'u Ana Ekran web uygulamaları için iOS/iPadOS 16.4'te
   açtı; altı -> `ios_too_old` (standalone olsa bile). Sürüm UA'daki `OS 16_4` biçiminden
   okunur; iPadOS masaüstü UA'sında sürüm yoktur -> "bilinmiyor" eski sayılmaz, Ana Ekran
   kuralı yine uygulanır. Eşik `IOS_WEB_PUSH_MIN_VERSION` sabitinde.

## Sonuçlar

- Masaüstü/Android'de hiçbir metin değişmez; Windows Chrome testi bunu bağlar.
- Bir Ana Ekran uygulamasında `PushManager` yine yoksa (beklenmez) bugünkü dürüst
  "desteklemiyor" görünür.
- Yeni bağımlılık yok; `sw.js`, `manifest.ts`, `layout.tsx`, `services/api` değişmedi.

## Sahibin denemesi (READY_FOR_OWNER)

iPhone'da Safari'de https://pagentos-core.tail0e6789.ts.net/settings aç -> Push
bildirimleri kartında "Ana Ekrana Ekle" ipucu görünür (düğme yok). Paylaş -> Ana Ekrana
Ekle -> Çekirdek'i ana ekrandan aç -> Ayarlar -> Push bildirimleri -> "Bildirimlere izin
ver ve aç" -> izin ver. Sunucuda VAPID anahtarı yoksa kartta "sahip eylemi" satırı görünür:
o zaman `scripts/cloud/new-vapid-key.ps1` host adımı **Onay Merkezi**'ne düşer (sahip
onaylar, anahtar sunucuya kurulur), sonra adım tekrarlanır. Ardından ev PC'den bir brifing
kuyruğa yazılınca telefonda bildirim görünür (briefing-ladder-fallback kartının deneme
satırıyla aynı deneme).
