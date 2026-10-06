# ADR (numara lead'in): CalDAV sağlayıcısı gerçek Radicale'e uyar

- Durum: kabul (card radicale-caldav-live, cycle d20261006)
- Öneri: team/proposals/2026-10-06-feed-radicale-calendar-server.md (sahip onayladı)

## Bağlam

`CalDavCalendarProvider` yalnız `httpx.MockTransport`'a karşı sınanmıştı ve sahte sunucu
REPORT'a ham `.ics` döndürüyordu. Gerçek CalDAV sunucusu 207 multistatus döner; .ics metni
`<C:calendar-data>` düğümlerindedir. `parse_calendar(response.text)` gerçek sunucuda hiç
etkinlik görmezdi ("iki yarı birbirini okumalı" hatası). Stack-ops denetçisi: dev'de
`/owner/takvim/` PROPFIND 404 (koleksiyon yok).

## Kararlar

1. **Multistatus.** REPORT cevabı stdlib `xml.etree` ile okunur (DAV: + caldav ad alanları);
   her `calendar-data` ayrı VCALENDAR olarak `app.calendar.ics.parse_calendar`'a gider, href
   uid'e eşlenir (update/delete/get o adrese gider: iPhone'un yazdığı öğe `<uid>.ics` olmayabilir).
   `200 text/calendar` eski yol aynı fonksiyonda, Content-Type'a göre kabul. DTD/ENTITY taşıyan
   cevap reddedilir (`CalDavError`): entity açılımı bir sunucu cevabında asla gerekmez.
   Yeni bağımlılık yok.
2. **MKCALENDAR sağlayıcıda, kurulum betiğinde değil.** Koleksiyonu **sağlayıcı yaratır**:
   ilk çağrıda PROPFIND Depth 0; 404 ise MKCALENDAR (displayname `Takvim`, calendar-timezone
   yok - sağlayıcı UTC yazar). Sonuç sağlayıcı ömrü boyunca önbellekte. Gerekçe: api yeniden
   kurulumda / boş birimde kendi takvimini kendisi bulur ya da yapar; ayrı bir betiğin
   unutulması takvimi sessizce kırmaz. MKCALENDAR 405 = başkası arada yarattı, kabul.
3. **If-None-Match: \*.** Yeni etkinliğin PUT'u bunu taşır; Radicale 412 = aynı uid var ->
   `CalDavConflictError` (`uid_conflict`), üzerine yazılmaz. Güncelleme (onaylı erteleme)
   önkoşulsuz PUT'tur (sahibin kendi etkinliği). DELETE 404 sessiz (istenen sonuç).
4. **401/403** -> `CalDavAuthError` (`account_invalid`, Türkçe cümle, `CalendarApiError` alt
   sınıfı; şifre mesajda yok). `service.py` bu kartta değişmez.
5. **get_event GET yolu.** Önce `GET <href ya da uid>.ics`; yalnız 404'te ±365 günlük REPORT.
6. **Yerel saat (bütünleşme koşusunun bulduğu hata).** `create` UTC yazar; geri okunan olay
   UTC kalınca "yarın saat 10'da" eklenen toplantı "yarın ne var"da "(07:00)" söylendi. CalDAV
   sağlayıcısı zamanlı olayları Europe/Istanbul'a çevirerek döndürür; regresyon testi
   `test_an_event_this_provider_wrote_reads_back_in_the_owners_local_time`.
7. **iPhone Takvim istemcisinin edge yolu** (Radicale'i telefona açmak) ayrı karar/kart;
   bu kart yalnız api <-> Radicale.

## Deneme listesi

- PROVEN_AUTOMATED: `tests/unit/test_calendar_caldav_provider.py` (sahte sunucu gerçek
  Radicale'den yakalanmış `radicale_report.xml` döndürür) + mutasyon RED.
- PROVEN_PROXY (inspector): dev yığınında `docker compose ... up -d radicale --wait`, sonra
  `cd services/api; uv run pytest tests/integration/test_calendar_radicale.py -q -m integration`.
  Son test gerçek uygulama nesnesiyle (create_app, `tests/voice_corpus/harness.py`,
  `PAGENTOS_CALDAV_URL=http://127.0.0.1:15232/owner/takvim/`, write enabled): "Yarın saat
  10'da toplantı ekle." -> öneri -> commit (sahip kararı 2026-09-19: ikinci onay yok; sonraki
  "Onayla." bekleyen bir şey bulmaz) -> Radicale'de `/owner/takvim/<uid>.ics` GET 200 ->
  "Yarın ne var?" cevabında başlık ve "10:00". Koşu sonunda koleksiyonda test öğesi kalmaz.
- READY_FOR_OWNER (PROVEN_REAL, bağlama kartı - radicale-stack-ops ADR'sindeki metin -
  yayınlandıktan sonra): Kokpit'ten "yarın saat 10'da toplantı ekle", "Onayla"; etkinlik
  Kokpit takvim panelinde ve "yarın ne var" cevabında 10:00 ile görünür; Radicale konteyneri
  /health'te sağlıklı.

## Açık risk

`CalendarService.agenda/find_slot` sağlayıcı istisnasını yakalamaz (service.py bu kartta
değişmez): 401'de `CalDavAuthError` (error_class + speech taşır) çağırana kadar çıkar; okuma
yolunda receipt'e çevirmek service.py'ye dokunan ayrı bir kart ister.
