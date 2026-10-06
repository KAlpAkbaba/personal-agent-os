# ADR (numara lead'in): Canlı oturum yokken brifing bildirim merdivenine düşer

- Durum: kabul (briefing-ladder-fallback, döngü d20261006)
- Bağlam: ROADMAP "Owner's queue" madde 1'in eksik yarısı. `PendingBriefingAnnouncer` yalnız
  canlı realtime oturuma konuşuyordu; oturum yoksa `narrate` `no_live_session` döner, satır
  `say_failed` sayılır ve sahip uyurken süresi dolar. Bildirim merdiveni (toast -> sound ->
  push -> inbox) üretimde çalışıyor ve sahibe konuşmadan ulaşan tek yol o.

## Karar

1. `BriefingSpeaker.say` artık `SayOutcome(delivered, reason)` döner; `RealtimeSayBriefingSpeaker`
   `BriefingDelivery.reason`'ı aynen geçirir.
2. `PendingBriefingAnnouncer(..., fallback=None)`: `None` bugünkü davranıştır.
   `NotificationBriefingFallback(session_factory)` yalnız `notifications.service.record`
   çağırır, `notifications/` paketinde değişiklik yok.
3. **Yalnız `no_live_session` düşer.** `queued_to_session` / `already_queued` web oturumunun
   tamponunda bekleyen bir cümledir; sahibin bir sonraki sözünde boşalır ve satırı boşaltan
   damgalar. Üstüne bildirim aynı haberi iki kez vermektir. `queue_failed` ve istisna da bir
   oturum varken olan arızadır; bugünkü sayım (`say_failed`) aynen kalır.
4. **Digest hariç.** Sıradan özerk etkinlik; her biri için bildirim, anayasanın yasakladığı
   taşkındır. Digest satırları canlı oturumda tek cümle olarak söylenmeye devam eder.
5. **Kuyruk teslim değildir.** Brifing `record()` anında damgalanmaz; sonraki süpürmelerde
   bildirim satırına merdivenin yazdığı `delivered_at`/`delivered_via` okunur ve brifing
   `notification:<kanal>` ile damgalanır (`VIA_NOTIFICATION_PREFIX`, `delivered_via`
   String(32) yeter; şema ve ledger sözlüğü değişmez, `briefing.delivered` detail.via taşır).
6. **Kutu duyulma sayılmaz.** Yalnız `inbox` ile "teslim" edilmiş ya da hâlâ açık bir bildirim
   satırı bekletir: başarısızlık sayılmaz, karantinaya gitmez, ikinci bildirim yazılmaz.
   Sonraki canlı oturumda sesle söylenir ve `voice` damgası alır; kutudaki bildirim yerinde
   kalır (zemin, kaybolmaz). Merdiven vazgeçtiyse (`quarantined_at` ya da `ladder_exhausted`,
   teslimsiz) brifing `ladder_failed` ile bir başarısızlık sayar.
7. **Asla `urgent` değil.** Brifing önemli bir alarm değildir; öncelik daima `normal`,
   sessiz saatlerde `record`'un kendi kuralı `deferred_until` yazar ve telefon çalmaz. Önem
   listesi urgent-alert-wire kartınındır. Yapısal test kaynakta `PRIORITY_URGENT` arar.
8. **Yineleme kilidi:** `group_key = 'briefing:<briefing_id>'`. `open_for` satırı her durumda
   (okunmuş, aşılmış, karantinada) bulur; bulursa ikinci bildirim asla yazılmaz.
9. **Tavan 5:** bir süpürme en çok 5 bildirim YAZAR (en eskiden başlayarak). Bir gecenin
   biriken araştırmaları tek süpürmede bir duvar olmasın; gerisi 20 sn sonra gelir. Damgalama
   ve okuma tavansızdır (ucuz, ve tavan onlara uygulanırsa eski kutu satırları yenilerini
   sonsuza kadar bekletir).

## Sonuçlar

- main.py'de tek satır gerekir (`fallback=NotificationBriefingFallback(artifacts.session)`);
  alan dışı olduğundan ALAN_ISTEGI ile istendi.
- `delivered_via='push'` RFC 8030'da yalnız push servisinin kabulüdür (ladder.py docstring'i);
  `notification:push` damgası bu sınırı devralır.
