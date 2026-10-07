# ADR (taslak, numarasız): inbound-calls-wire — gelen arama hattı uygulamaya bağlandı

**Durum:** kabul (worker, 2026-10-07). **Bağlam:** inbound-calls-bridge ADR'sinin "BAĞLAMA KARTI".

## Karar

- `Settings`: `telephony_inbound_enabled: bool = False` (`PAGENTOS_TELEPHONY_INBOUND_ENABLED`),
  `telephony_inbound_daily_minutes: int = 30` (`PAGENTOS_TELEPHONY_INBOUND_DAILY_MINUTES`).
  Varsayılan KAPALI: env yokken imzalı her arama Türkçe refuse TwiML + Hangup alır.
- Yol: BAĞLAMA KARTI'ndaki `app.include_router(inbound_routes.router)` satırı **uygulanmadı**
  (registry-models-and-routers sonrası main.py cırcırı, tavan 56 aynı kaldı). Yerine
  `app/telephony/routes.py`: `ROUTERS = [inbound_router]`; `router`/`audio_router` main.py'deki
  satırlarında kalır (ikisi birden = `include_discovered_routers` açılışı durdurur).
- `main.py`: yalnız `app.state.telephony_inbound = InboundLine(...)`; recorder'ın session scope'u
  `build_owner_caller`'a verilen `dispatch_session_factory` ile aynı.
- Kimliksiz uçlar: `POST /telephony/inbound/voice`, `POST /telephony/inbound/status` EXPECTED_OPEN'da
  (yetki: Twilio imzası); kimliksiz soket kümesi `{"/v1/devices/connect", "/telephony/inbound/media"}`
  (yetki: tek kullanımlık, 2 dk'lık, CallSid'e bağlı bridge_token).
- Prod compose api ortamına iki satır (`:-false`, `:-30`); başka servis değişmedi.

## Geri alma

`ROUTERS` satırını sil (uçlar kalkar) veya env'de `PAGENTOS_TELEPHONY_INBOUND_ENABLED=false` bırak
(varsayılan). Göç yok.
