# inbound-calls-bridge — entegrasyon planı (Entegratör, d20261006)

Kart: `inbound-calls-bridge` (Gelen aramalar 2/2). Öneri: `team/proposals/2026-10-06-feed-inbound-calls-public-endpoint.md`.
Kod yazılmadı; bu belge çalışanın ve ADR'nin girdisidir. Kaynaklar en altta.

## 0. Karar

**Hiçbir şey benimsenmez (adopt yok); fikir alınır, kod bizimdir (adapt).** Yeni Python bağımlılığı YOK.

| Aday | Lisans | Neden alınmadı |
|---|---|---|
| `twilio` (twilio-python SDK) | MIT | jarvis-calls-owner SDK'sız httpx ile gitti; tek ihtiyaç `RequestValidator` = 40 satır stdlib (hmac/hashlib/base64). Algoritma aşağıda birebir. |
| twilio-samples/speech-assistant-openai-realtime-api-python | MIT | Örnek uygulama; beta `input_audio_format: "g711_ulaw"` alanlarını kullanır, GA API bunu REDDEDER (bkz. §3). Yalnız akış şekli fikir olarak. |
| `openai` SDK realtime istemcisi | Apache-2.0 | Depoda `websockets>=13` var (pyproject:57); SDK eklemek bağımlılık + ikinci bir realtime şeması demek. |
| pipecat / LiveKit agents | BSD-2 / Apache-2.0 | Büyük çerçeveler (VAD, yeniden örnekleme, transportlar); bizde yeniden kodlama yok, 2 bacaklı köprü ~250 satır. |

Cihaz/işveren makinesi etkisi: yok (her şey Cloud Core api kapsayıcısında). Dışarı konuşan: Twilio (zaten kayıtlı sağlayıcı)
ve OpenAI Realtime (zaten kullanılan sağlayıcı, sahibin anahtarı). Ses diske/log'a yazılmaz.

## 1. Repodaki dikişler (okundu, değişmez)

- `app/telephony/service.py:467 _ledger` — `ledger.record(db, ledger.ActivityEvent(event_type, subsystem=v.SUBSYSTEM_TELEPHONY,
  source=SOURCE("telephony"), source_ref, status, occurred_at, module="app.telephony", detail_json))`. `ledger.record`
  `(source, source_ref)` üzerinde idempotenttir (`app/ledger/service.py:124`, yarışta IntegrityError -> var olanı döner).
  -> finalize `source_ref = f"inbound:{call_sid}"` kullanır; bu, kartın "CallSid başına idempotent" anahtarıdır.
- `app/notifications/service.py:75 record(...)` **idempotent DEĞİLDİR** (her çağrı yeni satır). Tek bildirim için finalize:
  (1) süreç içi `asyncio.Lock` (call_sid başına; status callback ile WS kapanışı aynı anda gelir), (2) aynı oturumda
  `select ActivityEventRow where source=="telephony" and source_ref==ref` -> varsa HİÇBİR ŞEY yapma, (3) yoksa ledger satırı
  + bildirim. Postgres testi ikinci finalize'ın 0 satır eklediğini ölçer (kart madde 8).
- `policy.call_kind_for_notification` yalnız `_NOTIFICATION_KINDS` tablosundaki türlerde arar (policy.py:73). Yeni bildirim türü
  `telephony.inbound_answered` orada YOK -> gelen arama bildirimi sahibi geri ARATMAZ. Bunu bir birim testi iddia etsin
  (`policy.call_kind_for_notification("telephony.inbound_answered") is None`), yoksa ileride biri tabloya eklerse döngü olur.
- `app/voice/providers_openai_realtime.py`: `DEFAULT_BASE_URL`, `AUDIO_FORMAT_G711_ULAW`, `_audio_format_object`
  (g711_ulaw -> `{"type": "audio/pcmu"}`), `EV_SPEECH_STARTED`, `EV_OUTPUT_AUDIO_DELTA` (+ `_LEGACY`), `EV_RESPONSE_DONE`,
  `EV_ERROR`, `EV_SESSION_UPDATED`, `EV_INPUT_TRANSCRIPT_COMPLETED`, `CMD_SESSION_UPDATE`, `CMD_RESPONSE_CREATE`,
  `CMD_RESPONSE_CANCEL`, `input_transcript(event)` (satır 804). Settings: `voice_openai_api_key`,
  `voice_realtime_openai_base_url`, `voice_realtime_openai_model` (gpt-realtime-2.1), `voice_realtime_openai_voice` (cedar),
  `voice_realtime_openai_transcription_model` (gpt-4o-transcribe). Twilio: `telephony_twilio_auth_token: SecretStr`,
  `telephony_public_base_url` (config.py:583-592).
- Dockerfile:44 uvicorn **tek işçi** -> bellek içi bridge_token deposu ve eşzamanlılık sayacı doğru. Mavi-yeşil geçişte süren
  arama kesilir (yeni kapsayıcı token'ı bilmez) — ADR'ye "yayın anında süren gelen arama düşer" satırı.

## 2. ENGEL (çalışan başlamadan): ledger sözlüğü kapalı

`telephony.inbound_answered` **`app/ledger/vocabulary.py`'de YOK** (yalnız call_placed/ended/skipped/refused/failed, satır 597-601).
`validate_event_type` bilinmeyen türde `InvalidVocabulary` atar ve `_ledger` yolu bunu yutar (service.py:496) -> **satır sessizce
yazılmaz**, Postgres testi kırmızı olur ya da daha kötüsü, yanlış yazılmış bir sahte ledger ile birim testi yeşil kalır.
vocabulary.py kartın alanı DIŞINDA. Öneri (lead seçer):
- **(A, önerilen)** `ALAN_ISTEGI: services/api/app/ledger/vocabulary.py` — `EVENT_TYPE_TELEPHONY_INBOUND_ANSWERED =
  "telephony.inbound_answered"` + `EVENT_TYPES` kümesine ekleme (2 satır, göç yok: event_type metin sütunu). Sözlüğü sayan bir
  koruyucu test varsa o da (çalışan grep'lesin: `tests/unit/test_ledger_vocabulary*`).
- (B) Var olan `telephony.call_ended` + `detail_json.direction="inbound"` ve `source_ref="inbound:<sid>"`. Göçsüz, alan
  dışı dosya yok; ama kabul 6'daki "kind telephony.inbound_answered" metniyle çelişir ve günlük sayaç sorgusu
  `source_ref LIKE 'inbound:%'` olur.
Her iki yolda: finalize `ledger.record`'u kendi çağırsın ve `InvalidVocabulary`'yi YUTMASIN (test görür).

## 3. OpenAI Realtime (sunucu bacağı) — doğrulanmış biçim

- Bağlantı: `wss://api.openai.com/v1/realtime?model=<voice_realtime_openai_model>`, başlık `Authorization: Bearer <voice_openai_api_key>`
  (GA'da `OpenAI-Beta` başlığı GEREKMEZ). Base URL `voice_realtime_openai_base_url`'den, https->wss dönüşümü provider'daki gibi (satır 422).
- **TUZAK:** GA şeması `"g711_ulaw"` dizgisini ve düz `input_audio_format`/`output_audio_format` alanlarını REDDEDER. Doğru:
  `session.audio.input.format = session.audio.output.format = {"type": "audio/pcmu"}` ve **`rate` alanı EKLENMEZ**.
  `_audio_format_object(AUDIO_FORMAT_G711_ULAW)` tam bunu üretir -> onu kullanın. Sahte sunucu testi (kabul 7) literal
  "g711_ulaw" değil `{"type":"audio/pcmu"}` istemeli; yoksa test yanlış şeyi yeşil yapar (iki yarı aynı kaynaktan okusun:
  beklenen değer `_audio_format_object(AUDIO_FORMAT_G711_ULAW)`).
- session.update gövdesi (öneri): `{"type":"session.update","session":{"type":"realtime","output_modalities":["audio"],
  "instructions":SEKRETER_METNI,"tools":[],"tool_choice":"none","audio":{"input":{"format":{"type":"audio/pcmu"},
  "transcription":{"model":<transcription_model>,"language":"tr"},"turn_detection":{"type":"server_vad",
  "create_response":true,"interrupt_response":true}},"output":{"format":{"type":"audio/pcmu"},"voice":<voice>}}}}`.
  `tools: []` açıkça gönderilsin (alanın yokluğu "araç yok" demek değildir); `tool_choice:"none"` ikinci kilit.
- `interrupt_response: true` istemci yolundaki kararla (M16: `False`, satır 486-497) BİLEREK ayrılır: telefonda araya girmeyi
  süzecek istemci yok ve arayan sahip değil; ADR bir cümleyle yazsın. Ayrıca speech_started'ta Twilio'ya `clear` (kart).
- Olaylar: ses `response.output_audio.delta` (`delta` alanı base64 pcmu, Twilio'ya aynen); arayan metni
  `conversation.item.input_audio_transcription.completed` (`transcript`); **JARVIS metni için sabit provider dosyasında YOK**:
  `response.output_audio_transcript.done` (`transcript`) — legacy adı `response.audio_transcript.done`. inbound_realtime.py
  kendi sabitini tanımlar (provider dosyası değişmez), sahte sunucu da bu sabiti okur.
- Veda (5 dk): `{"type":"response.create","response":{"instructions":"Görüşme süresi doldu; kibarca Türkçe veda et ve
  mesajı ileteceğini söyle."}}`, 10 sn sonra iki bacağı kapat. Kapatmadan önce Twilio'ya `mark` gönderip geri gelmesini beklemek
  vedanın sonuna kadar çalınmasını sağlar (opsiyonel; sabit 10 sn kartın kuralı).
- Türkçe transkripsiyon: gpt-4o-transcribe Türkçeyi destekler; `language:"tr"` ipucu verilir. 8 kHz telefon sesi doğruluğu
  düşürür — ölçüm yok; ilk sahip denemesi tutanağı okuyarak değerlendirir (PROVEN_REAL'e not).
- Ses: `marin`/`cedar` önerilir; topluluk raporu: telefon hattında `fable/onyx/nova` anlaşılmaz ses verebilir (eski modeller).
- Oturum sınırı 60 dk (SESSION_MAX_MINUTES) — 5 dk tavanımızın çok üstünde.

## 4. Twilio Media Streams — çerçeve biçimi (bidirectional, `<Connect><Stream>`)

Twilio -> biz: `connected {event,protocol,version}`; `start {event,sequenceNumber,streamSid, start:{accountSid,streamSid,callSid,
tracks:["inbound"],mediaFormat:{encoding:"audio/x-mulaw",sampleRate:8000,channels:1},customParameters:{bridge_token:"…"}}}`;
`media {event,sequenceNumber,streamSid,media:{track,chunk,timestamp,payload(b64)}}`; `mark {…,mark:{name}}`;
`dtmf {…,dtmf:{track,digit}}`; `stop {…,stop:{accountSid,callSid}}`.
Biz -> Twilio: `{"event":"media","streamSid":…,"media":{"payload":b64}}` (yalnız mulaw 8 kHz, BAŞLIK BAYTI YOK),
`{"event":"mark","streamSid":…,"mark":{"name":…}}`, `{"event":"clear","streamSid":…}`.
- `<Stream url>` yalnız `wss`; **sorgu dizgisi desteklenmez** -> belirteç `<Parameter>` ile (kartla uyumlu). Ad+değer < 500 karakter:
  `secrets.token_urlsafe(32)` (43 kr.) uygundur.
- `<Connect><Stream>`'den sonraki TwiML, biz soketi kapatana dek çalışmaz; kapatınca çalışır -> `twiml_answer`'da Stream'den sonra
  bir şey koymayın (ya da bilerek `<Hangup/>`).
- `callSid` start çerçevesinde gelir -> finalize anahtarı buradan; voice webhook'unun form `CallSid`'i ile aynı olmalı
  (token deposunda token->CallSid eşlemesi tutulsun, start'taki callSid farklıysa 1008).

## 5. İmza algoritması ve 8443 tuzağı

Twilio'nun kendi doğrulayıcısı (twilio-python `request_validator.py`, MIT): `s = url + ''.join(ad + değer for ad in sorted(set(alanlar))
for değer in sorted(set(değerler)))`; `base64(HMAC-SHA1(auth_token, s))`; ve **URL'in hem portlu hem portsuz hâli denenir**
(`add_port`/`remove_port`). Yani Twilio'nun kendisi bazı durumlarda portsuz imzaladığını kabul ediyor.
- Kart: "portsuz URL -> 403" (katı; 8443 URL'de yazılıdır). Öneri: **katı kalın** (yalnız yapılandırılmış `https://host:8443/…`),
  ama portsuz biçim tutsaydı `telephony_inbound_signature_port_variant` uyarısı log'a düşsün (imza değeri/ token log'lanmaz).
  İlk gerçek aramada 403 gelirse sebep bir satırda görünür; düzeltme tek satır. ADR'de risk satırı.
- **Asıl tuzak (ters vekil):** uygulama `request.url`'i `http://<BIND_IP>:8001/telephony/inbound/voice` olarak görür. İmza URL'i
  DAİMA `settings.telephony_public_base_url.rstrip('/') + request.url.path` (+ varsa sorgu) ile kurulur, asla `request.url` ile değil.
- Tüm form alanları imzaya girer (Twilio habersiz yeni alan ekler); `await request.form()` -> `multi_items()`, yinelenen anahtarlar
  sıralı değerlerle. `hmac.compare_digest` (kart). Başlık adı WS'de küçük harf: `x-twilio-signature`.
- **Ek bulgu:** Twilio medya WebSocket el sıkışmasını da `x-twilio-signature` ile imzalıyor (Media Streams yapılandırma belgesi).
  Sözleşme medya soketini belirteçle yetkilendiriyor; bu imza ikinci bir kilit olarak sonraki kartta eklenebilir (imzalanan URL'in
  wss mi https mi olduğu belgelenmemiş — gerçek aramada ölçülmeden zorunlu yapılmasın). ADR "sonraki adım" maddesi.

## 6. Deneme (trial) hesabı

- Trial'da **gelen aramalar yalnız o hesapta Doğrulanmış (Verified Caller ID) numaralardan** kabul edilir (Twilio changelog 2023-10-03);
  TwiML'den önce İngilizce kısa bir trial duyurusu çalınır. Hesap yükseltilince ikisi de kalkar.
- Sonuç: trial'da READY_FOR_OWNER denemesi ancak sahibin doğrulanmış kendi numarasından yapılabilir; GSM koşullu yönlendirme
  yolunda arayan başkasıdır (doğrulanmamış) -> **trial'da yönlendirme yolu ÇALIŞMAZ**. Gerçek kullanım için hesap yükseltme
  (ücretli; sahip adımı — CLAUDE.md "paid account" kuralı) gerekir.

## 7. Türkiye numarası ve "sahip açmayınca JARVIS açar"

- Twilio TR'de **yerel/coğrafi ve mobil numara SATMIYOR**; yalnız ulusal **+90 850** ve ücretsiz **+90 800/811/812**. 850 için
  düzenleyici paket: kişi olarak kimlik/pasaport (adres dünyanın her yeri); işletme (Aktivra) olarak ticaret sicil + yetkili kimliği.
  Ücretsiz hat LOA ister ve yurtdışından aranamaz. Fiyat sayfası: numara "1,15 $/ay'dan başlar", 850'nin kesin aylık ücreti ve
  gelen dakika ücreti sayfada ayrılmamış -> konsolda satın alma ekranında görülür (sahip adımı, tahmin yazmıyorum).
- Media Streams: **0,0044 $/dk** (TR fiyat sayfası). OpenAI realtime ses: dakika maliyeti bu kartın 30 dk/gün tavanıyla sınırlı;
  gpt-realtime-2.1'in güncel ücreti ölçülmedi -> ilk hafta ledger'daki süreler × fatura ile ölçülsün (ADR maliyet satırı).
- **Önerilen yol:** sahibin GSM hattında **koşullu yönlendirme**: cevapsızda `**61*<Twilio numarası>#`, meşgulde `**67*…#`,
  ulaşılamazda `**62*…#` (hepsi birden `**004*…#`); iptal `##004#`. Cevapsız süre `**61*<no>**<5-30 sn>#`.
  Ücret: yönlendirilen bacak **sahibin hattından giden arama** olarak ücretlenir. Hedef **0850** ise yurtiçi 0850 tarifesi (çoğu pakette
  dakikadan düşmez, ayrı ücretlidir); hedef **ABD numarası** ise uluslararası giden dakika (pahalı). Turkcell'in yurtdışındayken
  yönlendirmede hem gelen hem giden uluslararası ücret aldığı yazılı. Operatör bazında kesin ücret doğrulanamadı -> sahip
  operatörüne/uygulamasına sorar. Ayrıca bazı operatörler 0850'ye yönlendirmeyi kısıtlayabilir: ilk denemede ölçülür.
- Arayan numarası: yönlendirmede Twilio'ya genellikle ASIL arayanın numarası gelir (operatöre bağlı; garanti değil). Gelmezse
  bildirim başlığı "Bilinmeyen numara aradı" olsun (`From` boş/`anonymous`/sahibin kendi numarası durumları test edilsin).

## 8. Dosyalar (kartın alanı) ve test eki

inbound_twilio.py, inbound_realtime.py, inbound_bridge.py, inbound_records.py, inbound_settings.py, inbound_routes.py + 4 birim +
1 bütünleşme testi (kart madde 7). Kartın listesine ek iddialar:
1. Sahte realtime sunucusu beklenen formatı `_audio_format_object(AUDIO_FORMAT_G711_ULAW)`'dan okur; `rate` anahtarı yok; `tools == []`.
2. İmza URL'i `request.url` değil public base: test uygulaması `http://testserver` iken public `https://x.ts.net:8443` ile imzalanmış
   istek kabul edilir (bu iddia yoksa ters vekil hatası birim testlerinde görünmez).
3. Yinelenen form anahtarı (aynı ad iki değer) imzası doğrulanır.
4. `call_kind_for_notification("telephony.inbound_answered") is None`.
5. start'taki `callSid` token'ın CallSid'inden farklı -> 1008.
6. finalize'da `InvalidVocabulary` yutulmaz (§2).

## 9. Geri alma

Router uygulamaya bağlı değil -> bu kartın geri alınması = dosyaların silinmesi. Bağlama kartından sonra: `PAGENTOS_TELEPHONY_INBOUND_ENABLED=false`
(webhook refuse TwiML döner) ya da Twilio konsolunda Voice webhook'unu boşaltmak; Funnel 8443'ü kapatmak (public-path kartı).

## 10. Ayak izi (tahmin, yöntem: kod okuması)

Arama başına: 2 WebSocket + 1 asyncio görevi; ses tamponu tutulmaz (çerçeve geçer). 20 ms mulaw çerçeve = 160 bayt (b64 ~216);
saniyede 50 çerçeve iki yönde ~25 KB/s. Bellek: tutanak metni ≤ birkaç KB. max_concurrent=1 -> ihmal edilebilir CPU (JSON + b64).
Ölçülmedi; bütünleşme testinde değil, sahip denemesinde `docker stats` ile bakılabilir.

## 11. THIRD_PARTY_COMPONENTS satırı (docs/THIRD_PARTY_COMPONENTS.md'de Twilio KAYDI YOK — grep 0 isabet)

Bağlama kartı (ya da lead) şu bölümü eklesin; jarvis-calls-owner'ın eksik kalan kaydını da kapatır:

```
## Twilio Programmable Voice (REST + TwiML + Media Streams)

Role: telephony provider behind app.telephony (TelephonyProvider / InboundTelephonyProvider). Outbound: JARVIS calls the
owner (jarvis-calls-owner). Inbound: JARVIS answers on the owner's behalf through a server-side bridge to OpenAI Realtime
(inbound-calls-bridge). No SDK: REST over httpx, signature validation in stdlib (algorithm of twilio-python's MIT
RequestValidator). Phones home: api.twilio.com (REST), Twilio -> https://<node>.ts.net:8443 (webhooks, media WebSocket).
Data: caller number, call SID, transcript TEXT in the ledger; no audio stored, Twilio call recording never enabled.
Cost: Media Streams 0.0044 USD/min (TR price page, 2026-10-06); number and inbound minutes per console.
Turkey: only +90 850 national and toll-free numbers; regulatory bundle (ID). Trial accounts accept inbound calls only
from verified caller IDs and play an English trial notice.
```

## Kaynaklar

- https://www.twilio.com/docs/usage/webhooks/webhooks-security
- https://github.com/twilio/twilio-python/blob/main/twilio/request_validator.py
- https://www.twilio.com/docs/voice/media-streams/websocket-messages
- https://www.twilio.com/docs/voice/twiml/stream
- https://www.twilio.com/docs/global-infrastructure/firewall-configurations/media-streams-configuration
- https://www.twilio.com/en-us/changelog/inbound-calls-to-trial-accounts-must-use-verified-callerid
- https://www.twilio.com/docs/usage/trials
- https://www.twilio.com/en-us/guidelines/tr/regulatory , https://www.twilio.com/en-us/guidelines/tr/voice
- https://www.twilio.com/en-us/voice/pricing/tr
- https://community.openai.com/t/gpt-realtime-2-ga-api-what-is-the-correct-audio-format-for-g711-ulaw-twilio-telephony/1380750
- https://www.turkcell.com.tr/servisler/yonlendir
