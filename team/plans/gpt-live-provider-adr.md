# ADR taslağı: gpt-live-provider — gpt-live-1 ikinci realtime sağlayıcı olarak YALNIZ ÖLÇÜM (TASLAK, işçi d20261004)

Durum: TASLAK. Uygulama ALAN_ISTEGI nedeniyle başlamadı (aşağıda). Numara yok; Proje Yöneticisi numaralar.

## Karar (kart)
`OpenAILiveProvider` ayar arkasında (`PAGENTOS_VOICE_REALTIME_OPENAI_LIVE_ENABLED`, varsayılan KAPALI); varsayılan
seçim `openai-realtime` kalır; oturum başına `prefer_provider` yalnız tercih sırasının başına ad koyar. Benimseme ayrı karar.

## Kaynaklar (hepsi 2026-10-04 okundu, curl ile, resmî sayfa)
- https://developers.openai.com/api/docs/models/gpt-live-1 — "Voice sessions cost $0.05 per minute, billed per second.
  Backend model and tool usage is billed separately"; uç nokta yalnız `v1/live/sessions`; girdi/çıktı ses+metin.
- https://developers.openai.com/api/docs/guides/live — WebRTC (tarayıcı: medya izleri + JSON veri kanalı), WebSocket,
  SIP; "Your server creates the session and exchanges the browser's connection offer for an answer"; anahtar sunucuda.
- https://developers.openai.com/api/docs/guides/voice-webrtc?api=live — belgelenmiş oturum açma:
  `POST /v1/live/sessions`, proje API anahtarıyla, JSON gövde
  `{"session": {"model": "gpt-live-1", "instructions": ..., "delegation": {...}}, "transport": {"type": "webrtc", "sdp": "<offer>"}}`
  → HTTP 201 `{"session": {"id": "live_123"}, "transport": {"type": "webrtc", "sdp": "<answer>"}}`.
  Veri kanalı etiketi `oai-events`. "The HTTP request starts the session. Do not send session.start".
  WebRTC'de `audio.format` gönderilmez. Oturum açılışı 15 sn faturalar (sonradan mahsup).
  Kapanış: `session.close` gönder, `session.closed` (usage) bekle.
- https://developers.openai.com/api/docs/guides/live-conversations — ses `audio.output.voice`, varsayılan `marin`;
  ek sesler quartz, ripple, vesper, willow, stone, gleam, meridian, bossa, tempo, beacon, delta, cinder (hepsi
  İngilizce/Portekizce olarak listelenmiş); `delegation.type` = `client` | `responses`, null/yok = client;
  `instructions` ≤ 16 384 token; `input` ≤ 128 mesaj / 8 192 token; `session.instructions.append` /
  `session.thinking.append` / `session.commentary.append`: `content` ≤ 500 token + ZORUNLU `delegation_id` (null = oturum geneli).
- https://developers.openai.com/api/docs/guides/live-delegation — client devri:
  `{"type":"session.delegation.created","event_id":...,"offset_ms":1000,"delegation":{"id":"item_...","type":"delegation","target":"client"}}`;
  devir olayı iş metnini TAŞIMAZ (`session.input_transcript.delta` / `session.output_transcript.delta`, alanlar
  `delta`, `start_ms`, `end_ms`); sonuç `session.commentary.append` + aynı `delegation_id`;
  "Interrupting the spoken conversation leaves backend work running".

## Belgeden çıkan, kartı değiştiren bulgu (Proje Yöneticisi'ne)
GPT-Live için **efemeral anahtar basma uç noktası BELGELENMEMİŞ.** Realtime'daki `client_secrets` yolu Live sayfalarında
geçmiyor; Live'ın belgelenmiş tek yolu: tarayıcının SDP teklifini BİZİM sunucumuz alır, proje anahtarıyla
`POST /v1/live/sessions`'a JSON olarak yollar, yanıtın `transport.sdp`'sini tarayıcıya döner. Sonuçları:
1. Kartın "mint" testi (1b) = bu oturum açma isteğinin şekli (uç nokta + gövde), anahtar yalnız Authorization başlığında.
2. Descriptor'daki `sdp_exchange_url` satıcı adresi OLAMAZ (tarayıcının anahtarı yok); Cloud Core'da bir SDP değişim
   rotası gerekir (routes.py alanda). Tarayıcıya giden `secret`, Cloud Core'un oturuma bağlı kısa ömürlü değişim
   bileti olur (satıcı anahtarı asla değil). `webrtc.ts` bugün `Authorization: Bearer <secret>` yazıyor; sahip
   oturumu da Authorization ile doğrulanıyorsa çakışır → biletin ayrı başlıkta taşınması web-bridge kartının işi.
3. `ephemeral_credentials=True` ancak bu bilet yoluyla dürüst olur; selection.py `require_ephemeral_credentials=True`
   istediği için bu bayrak False olursa sağlayıcı hiç seçilemez.
4. `rank_key` önce `end_of_turn=semantic` sıralıyor: Live'ın tur yönetimi modelin kendisinde (tam çift yönlü);
   `semantic` olarak bildirilmezse prefer_provider ile bile kazanamaz. Bildirim ADR'de gerekçelenecek.

## UNVERIFIED (resmî sayfada yazmayan; koda tahmin olarak girmez)
- **Türkçe desteği/kalitesi** (hiçbir resmî Live sayfasında dil listesi yok; ses tablosu yalnız İngilizce/Portekizce).
- Live için efemeral istemci anahtarı.
- Konuşma başladı/durdu, yanıt sesi başladı/bitti olayları (Live'da belgelenmemiş; yalnız transkript deltaları).
- Modelin konuşmasını kesen istemci komutu; satıcı tarafı devir iptal olayı.
- Varsayılan `marin` dışındaki seslerin Türkçe telaffuzu.

## Sır disiplini
Mevcut `providers_openai_realtime.py` aynen: anahtar yalnız oturum açma isteğinin Authorization başlığında; hata
mesajı, details ve log kaydından temizlenir; `__repr__` anahtarı göstermez.

## THIRD_PARTY satır metni (paylaşılan dosyayı Proje Yöneticisi yazar)
"OpenAI GPT-Live (`gpt-live-1`, `POST /v1/live/sessions`) — ticari satıcı API'si, yeni bağımlılık yok, mevcut OpenAI
alıcısı (yeni veri alıcısı değil); ayar arkasında, varsayılan KAPALI; 0,05 USD/dk (2026-10-04)."

## Sahibin/Danışman'ın adımı (işçi canlı çalıştırmaz)
Ayarı açma: `PAGENTOS_VOICE_REALTIME_OPENAI_LIVE_ENABLED=true`; smoke: `scripts/realtime_live_smoke.py` (henüz yazılmadı).
