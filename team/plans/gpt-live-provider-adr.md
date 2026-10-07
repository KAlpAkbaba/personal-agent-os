# ADR taslağı: gpt-live-provider — gpt-live-1 ikinci realtime sağlayıcı olarak YALNIZ ÖLÇÜM

Durum: UYGULANDI (işçi d20261004, 2. tur). Numara yok; Proje Yöneticisi numaralar.

## Karar
- `OpenAILiveProvider` (`services/api/app/voice/providers_openai_live.py`) ayar arkasında:
  `PAGENTOS_VOICE_REALTIME_OPENAI_LIVE_ENABLED` (varsayılan KAPALI) + `PAGENTOS_VOICE_REALTIME_OPENAI_LIVE_MODEL`
  (varsayılan `gpt-live-1`). Yalnız ayar açık VE `PAGENTOS_VOICE_OPENAI_API_KEY` varken kaydedilir; kapalıyken
  `inactive_candidates` Türkçe nedeni söyler.
- Varsayılan seçim DEĞİŞMEDİ: `voice_realtime_provider_preference` = (`openai-realtime`, `simulator`). Live,
  openai-realtime ile her yetenek tercihinde (semantic, WebRTC) berabere kalır; tercihsiz oturumu sıra
  belirler → openai-realtime. `selection.py` değişmedi.
- Oturum başına `prefer_provider` (sözleşme v4, desen `^[a-z][a-z0-9-]{1,31}$`): runtime adı tercih sırasının
  başına koyar, seçim yine yeteneğe göre; kayıtlı olmayan ad yok sayılır, `reasons` bunu söyler (422 yok:
  hangi adların kayıtlı olduğunu sızdırmamak için). Desene uymayan → 422.
- Benimseme (varsayılanı değiştirmek) AYRI karardır; bu yalnız ölçümdür.

## Kaynaklar (hepsi 2026-10-04 okundu, curl ile, resmî sayfa)
- https://developers.openai.com/api/docs/models/gpt-live-1 — "Voice sessions cost $0.05 per minute, billed per
  second. Backend model and tool usage is billed separately".
- https://developers.openai.com/api/docs/guides/live — WebRTC / WebSocket / SIP; sunucu oturumu açar.
- https://developers.openai.com/api/docs/guides/voice-webrtc?api=live — "Your application server exchanges it
  [SDP offer] for an answer with POST /v1/live/sessions, using the project API key"; gövde
  `{"session": {"model", "instructions", "delegation"}, "transport": {"type": "webrtc", "sdp": <offer>}}` →
  201 `{"session": {"id": "live_123"}, "transport": {"type": "webrtc", "sdp": <answer>}}`; veri kanalı
  etiketi `oai-events`; "Do not send session.start"; WebRTC'de `audio.format` gönderilmez; açılış 15 sn
  faturalanır (sonra mahsup). Aynı sayfadaki `realtime/client_secrets` Realtime API sekmesine aittir.
- https://developers.openai.com/api/docs/guides/realtime-websocket?api=live — `wss://api.openai.com/v1/live/sessions`,
  `Authorization: Bearer`, ilk mesaj `session.start` (`session.audio.format`), `session.started`,
  `session.input_audio.append`, `session.output_audio.delta`.
- https://developers.openai.com/api/docs/guides/live-conversations — ses `audio.output.voice` (varsayılan
  `marin`; quartz, ripple, vesper, willow, stone, gleam, meridian, bossa, tempo, beacon, delta, cinder);
  `delegation.type` client|responses; `input` ≤128 mesaj; `error` olayı `{type, event_id, error:{type, code,
  message, param, client_event_id}}`; `session.usage.updated` `{usage:{seconds}}`; `session.close`/`session.closed`;
  `session.input_transcript.delta` / `session.output_transcript.delta` (`delta`, `start_ms`, `end_ms`).
- https://developers.openai.com/api/docs/guides/live-delegation — `session.delegation.created`
  `{offset_ms, delegation:{id, type, target}}`, iş metnini taşımaz; sonuç `session.commentary.append` + aynı
  `delegation_id`.

## Belgenin kartı değiştirdiği yerler (lead'in 2. maddesi)
1. **Efemeral anahtar yok → Cloud Core bileti.** `mint_credential` satıcıyı ÇAĞIRMAZ: oturuma bağlı, tek
   kullanımlık, ≤600 sn'lik bir bilet (`secrets.token_urlsafe(32)`) basar; Cloud Core'un kurduğu
   `RealtimeSessionConfig` (persona, ses) biletin yanında sunucuda kalır. Descriptor:
   `sdp_exchange_url=/v1/voice/realtime/sessions/{id}/live-sdp` (API tabanına göreli), `ticket_header=
   X-PagentOS-Live-Ticket` (Authorization sahip oturumunda), `dialect=openai-live`, `data_channel=oai-events`.
   Yeni rota `POST .../live-sdp` (sahip kapılı + bilet) → `exchange_sdp` → `POST /v1/live/sessions` →
   `application/sdp` yanıt. `ephemeral_credentials=True` bu bilet için dürüsttür (satıcı anahtarı değil).
   Kabul 1b = bu oturum açma isteğinin şekli. Bilet bellekte (tek süreç); çok süreçte aynı süreç şart (risk).
2. **Konuşma başladı/durdu ve yanıt sesi olayları Live'da WebRTC'de belgelenmemiş** → eşlenmez (tahmin yok).
   Kabul 1d şöyle karşılandı: `session.delegation.created` → `RT_TOOL_CALL` (`call_id` = `delegation_id`,
   korunur), `error` → `RT_ERROR`; Realtime'ın `speech_started/stopped`, `response.output_audio.delta` adları ve
   `session.started` → `()` (test bunu sabitler). Zamanlama ölçüleri istemci kaynağından gelir (bench'in
   `client` kaynağı), sağlayıcıdan değil.
3. `end_of_turn=semantic`: tur alma modelin kendisinde (tam çift yönlü, VAD düğmesi belgelenmemiş); en yakın
   bildirilen kip. `tool_calling=True`: istemci devri ile (fonksiyon aracı değil).

## UNVERIFIED (resmî sayfada yazmayan; koda tahmin olarak girmedi)
- **Türkçe desteği/kalitesi** — hiçbir Live sayfasında dil listesi yok. Kodda: `languages` tr-TR'yi aday olmak
  için içerir (ölçmenin tek yolu), `cost_metadata.language_verified=False` bunu /providers'ta söyler.
- Live için efemeral istemci anahtarı.
- Konuşma başladı/durdu, yanıt sesi başladı/bitti olayları (WebRTC); `session.output_audio.delta` alan adları.
- Modelin konuşmasını kesen istemci komutu; satıcı tarafı devir iptal olayı.
- Bir medya bacağının azami süresi (`leg_max_seconds()` = 0 = bilinmiyor).
- WebSocket'te SDK'nın eklediği ek bağlantı başlıkları (smoke yalnız Authorization yollar).
- Oturum açılışında `input` ile tohumlanan kullanıcı mesajının modeli konuşturup konuşturmadığı (smoke ölçer).
- `marin` dışındaki seslerin Türkçe telaffuzu.

## Sır disiplini
Anahtar yalnız `POST /v1/live/sessions` isteğinin Authorization başlığında. Hata mesajı, details (sır şekilli
anahtarlar düşürülür + metin içi değiştirme) ve log kaydından temizlenir; `__repr__` göstermez; bilet anahtar
değildir; descriptor'da satıcı adresi yoktur. Smoke betiği anahtarı yalnız ortamdan okur, çıktıda yok.

## Maliyet karşılaştırması
`realtime_bench.compare_reports(a, b)`: aynı ölçüt adları yan yana (ilk ses = `eot_to_first_audio_ms`,
araya girme→susma = `barge_in_to_stop_ms`, yanlış kesilme = `false_barge_count`, devir/araç =
`tool_preamble_ms` + `tool_done_to_speech_ms`, `gap_count`), dakika başı maliyet sağlayıcı modülünün sabitinden
(Live 0,05); token faturalı ya da bilinmeyen → null (asla 0). Rapor şeması m12.1 → m12.2 (provider + model).

## THIRD_PARTY satır metni (paylaşılan dosyayı Proje Yöneticisi yazar)
"OpenAI GPT-Live (`gpt-live-1`, `POST /v1/live/sessions`, `wss://api.openai.com/v1/live/sessions`) — ticari satıcı
API'si, yeni bağımlılık yok (httpx/websockets mevcut), mevcut OpenAI alıcısı (yeni veri alıcısı değil); ayar
arkasında, varsayılan KAPALI; 0,05 USD/dk (2026-10-04)."

## Sahibin/Danışman'ın adımı (işçi canlı çalıştırmadı; ücretli + sır)
1. Türkçe ilk adım (ölçüm burada biter, Türkçe dönmezse):
   `cd services/api && PAGENTOS_VOICE_OPENAI_API_KEY=... uv run python ../../scripts/realtime_live_smoke.py --live`
   (isteğe bağlı `--audio-file merhaba.pcm` = ham PCM16 24 kHz mono). Çıkış 0 = cevap transkripti döndü;
   5 = bağlandı ama cevap yok (sonuçsuz); 1 = satıcı hatası; 3 = anahtar yok.
2. Ayarı açma: `PAGENTOS_VOICE_REALTIME_OPENAI_LIVE_ENABLED=true` (+ yeniden başlatma); web `prefer_provider:
   "openai-live"` yollar (gpt-live-web-bridge). K66 akşamı PROVEN_REAL yalnız sahibin raporundan.
