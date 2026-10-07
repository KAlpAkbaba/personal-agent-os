# Aktivra → JARVIS: "önemli" olay kanalı (AKTIVRA_EVENTS v1)

Aktivra deposunun (ayrı proje) uygulayacağı sözleşme. Karşı taraf:
`services/api/app/aktivra/routes.py` (kart aktivra-inbound-events). Bu belgedeki gövde
şeması tablosu ile `AktivraEvent` modeli aynı alanları sayar; `tests/unit/test_aktivra_inbound.py`
ikisini birbirine okutur.

## Kural: müşteri verisi gönderilmez

Aktivra JARVIS değildir. Bu kanaldan geçen tek şey "önemli bir şey oldu" ve kısa bir başlıktır.
**Müşteri verisi, şirket belgesi, kişi adı, tutar, ek, bağlantı GÖNDERİLMEZ.** Başlık bir kapı
zilidir, mektup değil: ayrıntı Aktivra'nın kendi ekranında kalır. Tek sahip, kiracı yok; KVKK
açısından bu sistem Aktivra'nın müşterilerinin verisini işlemez. Fazladan bir alan gelirse istek
422 ile reddedilir (sessizce atılmaz).

## Uç nokta

```
POST /v1/aktivra/events
Authorization: Bearer <AKTIVRA_INBOUND_TOKEN>
Content-Type: application/json
```

- Taban adres: Cloud Core'un sahip tarafından verilen https kökü.
- Belirteç: `pagentos_ak_` + 43 karakter (`secrets.token_urlsafe(32)`, 256 bit). Sahip üretir,
  bu tarafta `PAGENTOS_AKTIVRA_INBOUND_TOKEN` (set-cloud-secret.ps1), Aktivra tarafında kendi
  gizli deposuna koyar. Sahibin oturum belirteci bu rotada GEÇMEZ; bu belirteç başka hiçbir
  rotada geçmez. Belirteç loglanmaz, yanıtlarda dönmez.

## Gövde şeması

| alan | tür | zorunlu | sınır |
|---|---|---|---|
| `event_id` | string | evet | 8-64 karakter, `A-Z a-z 0-9 . _ : -`; yineleme anahtarı |
| `title` | string | evet | 1-120 karakter |
| `summary` | string | hayır | en çok 280 karakter |
| `severity` | `"important"` / `"info"` | evet | `important` telefonu çaldırır, `info` gelen kutusunda bekler |
| `occurred_at` | ISO 8601 zaman | evet | ör. `2026-10-07T09:00:00Z` |

Başka alan yok. Gövdenin tamamı en çok 4096 bayt.

## Sınırlar

- Gövde 4096 bayttan büyük, fazla alan, ek, 120 karakteri aşan başlık, 280 karakteri aşan özet,
  bilinmeyen `severity` → **422**.
- Aynı `event_id` ikinci kez (en az 24 saat; satırlar 30 gün tutulur) → **200**, ilk
  `notification_id`; ikinci bildirim YOK. Ağ hatasında aynı `event_id` ile güvenle yeniden dene.
- Saatte en çok 10 yeni olay; 11. → **429** (yineleme 429 almaz, ilk yanıtı alır).

## Yanıtlar

| durum | anlamı | gövde |
|---|---|---|
| 201 | kabul edildi, bildirim yazıldı | `{"notification_id": "<uuid>"}` |
| 200 | bu `event_id` zaten var | `{"notification_id": "<uuid>"}` (ilkinin) |
| 401 | belirteç eksik ya da yanlış (ret kayda geçer, yalnız parmak izi) | `{"detail": "unauthorized"}` |
| 404 | bu tarafta kanal kurulmamış (belirteç ayarlı değil) | |
| 422 | gövde sözleşmeye uymuyor | alan ve kural (girdi geri yansıtılmaz) |
| 429 | saatlik sınır | |

## Sonrası (JARVIS tarafı)

- `important` → `aktivra.important` acil bildirim: sessiz saatlerde ertelenmez, önemli
  bildirim telefonu (Pushover alarmı) bağlıysa telefon "Aktivra" sözcüğüyle çalar, arama
  politikası açıksa JARVIS arar ("Aktivra'dan önemli bir haber var.").
- `info` → `aktivra.info` normal bildirim; sessiz saatlerde sabahı bekler.

## Örnek

```bash
curl -sS -X POST "https://<cloud-core>/v1/aktivra/events" \
  -H "Authorization: Bearer $AKTIVRA_INBOUND_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"event_id":"aktivra-2026-10-07-0001","title":"Yeni sözleşme imzalandı","severity":"important","occurred_at":"2026-10-07T09:00:00Z"}'
```
