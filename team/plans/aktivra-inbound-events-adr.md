# ADR (numara entegrasyonda): Aktivra'nın "önemli" kanalı - kendi belirteci, sınırlı metin

Kart: aktivra-inbound-events. Yol haritası: "Aktivra's assistant tells JARVIS, JARVIS calls him".

## Bağlam

Aktivra ayrı bir proje ve ayrı bir depo (sahip 2026-10-05). Asistanı önemli bir şey olduğunda
JARVIS'e haber vermeli; JARVIS bunu önemli bildirim merdivenine (Pushover alarmı) ve arama
politikasına verir. Sınır: "Aktivra is not JARVIS" - şirketin ve müşterilerinin verisi bu sisteme
girmez.

## Karar

1. **Ayrı belirteç.** `POST /v1/aktivra/events` yalnız `aktivra_inbound_token`
   (`PAGENTOS_AKTIVRA_INBOUND_TOKEN`, `pagentos_ak_` + `secrets.token_urlsafe(32)`) kabul eder.
   Sahibin oturumu verilmez çünkü (a) oturum her rotada tam yetkidir, başka makinedeki bir
   asistanın elinde kapsam dışı yetki olur; (b) iptali sahibin bütün oturumlarını etkiler. Ayrı
   belirteç bu rotadan başka hiçbir yerde geçmez (oturum tablosunda yok), sahibin oturumu da bu
   rotada geçmez. Karşılaştırma `app.identity.tokens` kuralıyla: SHA-256 üzerinden
   `hmac.compare_digest`; belirteç loglanmaz, yanıtta dönmez; yalnız 16 hex parmak izi ret
   satırına (`aktivra.rejected`) yazılır. Belirteç ayarda boşsa rota 404 (kanal yok).
   Üretim sahibin elinde: `app.aktivra.auth.new_inbound_token()`, `secret-store.ps1 -Set` +
   `set-cloud-secret.ps1 -Name PAGENTOS_AKTIVRA_INBOUND_TOKEN`; aynı değer Aktivra'nın kendi
   gizli deposuna.
2. **Sınırlı metin.** Gövde yalnız `event_id, title (<=120), summary (<=280, isteğe bağlı),
   severity (important|info), occurred_at`; fazla alan, ek, 4096 bayt üstü gövde 422 - sessizce
   atılmaz, Aktivra sözleşme dışı gönderdiğini görür. Sebep: KVKK (bu sistem Aktivra'nın
   müşterilerinin verisini işlemez), tek sahip ve kiracı yok (şirket verisi için bir ayrım
   katmanı yok, olmamalı), ve alarmın kendisi zaten tek sözcük ("Aktivra") taşır.
   422 yanıtı alan adını ve kuralı söyler, girdiyi geri yansıtmaz.
3. **Yineleme.** `event_id` benzersiz (`aktivra_events.event_id` unique). Aynı kimlik ikinci kez
   gelince 200 ve ilk `notification_id`; ikinci bildirim, ikinci zil yok. Talep bildirimden önce
   aynı işlemde yazılır: aynı anda gelen iki kopya Postgres'in benzersiz indeksinde sıraya girer,
   biri bildirim yazar (entegrasyon testi 4 iş parçacığıyla). Satırlar 30 gün tutulduğu için
   yineleme penceresi en az 24 saat, pratikte 30 gün (kartın 24 saatinden sıkı, güvenli yön).
   Bu yüzden `notification_id` sütunu NULL olabilir (talep ile bildirimin commit'i arası).
4. **Sınırlar.** Saatte en çok 10 yeni olay (11. 429), tablodan sayılır - yeniden başlatma
   sıfırlamaz; yineleme 429 almaz. Telefon araması ayrıca kendi saatlik sınırına
   (`telephony_max_calls_per_hour`) tabidir.
5. **Önem.** `important` -> `aktivra.important`, `priority=urgent` (sessiz saatleri geçer);
   tür `app.telephony.policy` listesinde zaten var, kategori "Aktivra" - test sabitler.
   `info` -> `aktivra.info`, normal öncelik, önemli değil.
6. **Saklama.** `aktivra_events` satırı 30 gün sonra `RetentionSweeper` girdisi
   `aktivra_events` ile silinir; bildirim kendi kuralına kalır.

## Sonuçlar

- Açık rota listesine (`test_identity_enforcement.EXPECTED_OPEN`) bir giriş eklendi.
- Ret satırları bir saldırıda defteri büyütebilir (sınır yok); açık risk olarak not edildi.
- Sözleşme: `packages/protocol/AKTIVRA_EVENTS.md` (test modelle aynı alanları sayar).
