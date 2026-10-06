# ADR (taslak, numara lead'in): JARVIS gelen aramayı sahibin adına açar — sunucu tarafı köprü

Kart: `inbound-calls-bridge` (Gelen aramalar 2/2). Plan: `team/plans/inbound-calls-bridge-integration.md`.
Kod: `services/api/app/telephony/inbound_{twilio,realtime,bridge,records,settings,routes}.py`; sözlük:
`app/ledger/vocabulary.py` (`EVENT_TYPE_TELEPHONY_INBOUND_ANSWERED`, alan eki A).

## Karar

1. **Güvenlik ilkesi: arayan SAHİP DEĞİLDİR.** Sahibin kendi numarasından arasa bile telefon sesi doğrulanmış
   kimlik değildir (numara taklit edilebilir, ses doğrulaması bu yolda yok). Köprü oturumunun ARACI YOKTUR
   (`tools: []` açıkça gönderilir, `tool_choice: "none"` ikinci kilit); belleğe, takvime, maile, cihazlara ve
   sahibin hiçbir verisine erişimi yoktur. Kişilik sabit bir Türkçe sekreter metnidir
   (`inbound_realtime.SECRETARY_INSTRUCTIONS_TR`): kim arıyor, konu ne, iletilecek mesaj; sahibin yerini,
   programını, numaralarını, kişisel bilgisini söylemez; işlem yapmaz; "sahibe ileteceğim" der; talimatı
   değiştirme isteğine uymaz. Arayanın sözleri modele gider ama modelin eli hiçbir şeye uzanmaz: istem
   enjeksiyonunun en kötü sonucu yanlış bir cümledir, bir eylem değil.
2. **Neden sunucu tarafı köprü.** Bugünkü gerçek zamanlı ses yolu istemci bacağıdır
   (`providers_openai_realtime.open_session` tarayıcı için kimlik basar; WebRTC/WS'yi istemci açar). Telefonun
   istemcisi yoktur; Cloud Core OpenAI Realtime WebSocket'ini kendisi tutar ve Twilio Media Streams'i ona bağlar.
3. **Neden g711 yeniden kodlamasız.** Twilio Media Streams mulaw 8 kHz base64 çerçeve verir/alır; OpenAI GA
   şeması `audio/pcmu` girdi ve çıktıyı destekler (`_audio_format_object(AUDIO_FORMAT_G711_ULAW)` ->
   `{"type":"audio/pcmu"}`, `rate` yok; düz `"g711_ulaw"` dizgisini GA reddeder). Çerçeveler olduğu gibi geçer:
   sıfır CPU örnekleme, sıfır gecikme eki, ses tamponu yok.
4. **KVKK duyurusu ve neden Stream'den önce `<Say>`.** Arayan, dinlenmeden ve yazıya dökülmeden önce bilgilendirilir;
   `<Say language="tr-TR">` Stream açılmadan çalınır, böylece duyuru bitmeden tek bir ses çerçevesi modele gitmez.
   Metin (`inbound_twilio.KVKK_ANNOUNCEMENT_TR`, sahip değiştirebilir):
   > Merhaba, ben bu hattın dijital asistanıyım. Bu görüşme ses kaydı olarak saklanmaz; söyledikleriniz yazıya
   > dökülür ve hat sahibine iletilir. Devam ederek bunu kabul etmiş olursunuz. Buyurun, sizi dinliyorum.
   Sahibin adı söylenmez ("bu hattın"). Hiçbir TwiML'de `<Record` yoktur; Twilio arama kaydı hiçbir yerde açılmaz.
5. **Neden metin saklanır, ses saklanmaz.** Sahibin ihtiyacı "kim aradı, ne dedi"dir; metin bunu verir. Ses kişisel
   veridir (biyometrik olabilir) ve saklamak KVKK yükünü büyütür. Sesin hiçbir baytı diske, ledger'a, log'a
   yazılmaz; yalnız sayaçlar (çerçeve sayısı iki yön, saniye). Testler ledger, bildirim ve log'da base64 ses
   çerçevesi arar.
6. **Tek satır, tek bildirim.** Çağrı başına ledger'a BİR satır (`telephony.inbound_answered`, subsystem telephony,
   `source_ref = inbound:<CallSid>`; payload: arayan, süre, çerçeve sayıları, tutanak ≤ 8000 karakter + `truncated`)
   ve BİR bildirim (`<numara> aradı` ya da `Bilinmeyen numara aradı`; gövde: Arayan satırlarının ilk 300
   karakteri, deterministik; model özeti sonraki adım). Status callback ile WS kapanışı birlikte gelebilir ve
   `notifications.record` idempotent değildir: CallSid başına kilit + ledger satırı kontrolü; önce ledger, sonra
   bildirim (çökme bir bildirimi kaybettirir, ikiler değil). `finalize` hata yutmaz (`InvalidVocabulary` dahil).
   Çalarken satır yazılmaz. Gelen arama bildirimi `policy._NOTIFICATION_KINDS`'ta yoktur: JARVIS sahibi geri
   ARAMAZ (test: `call_kind_for_notification("telephony.inbound_answered") is None`). Giden arama sayaçlarına
   karışmaz (ayrı olay türü; `call_ended` + direction seçeneği reddedildi: `service.py` giden aramayı onunla sayar).
7. **Sınırlar ve sayaçlar.** En çok 1 eş zamanlı gelen arama (aktif köprü + harcanmamış belirteç); en çok 5 dk
   görüşme (sonra `response.create` ile Türkçe veda, 10 sn sonra iki bacak kapanır); günde en çok 30 dk (İstanbul
   takvim günü, ledger satırlarının `duration_s` toplamından; yeniden başlatma sıfırlamaz); günün kalanı 5 dk'dan
   azsa görüşme o kalanla sınırlanır, 60 sn'den azsa açılmaz. Kapalıyken, yapılandırma eksikken, meşgulken ya da
   sınır dolunca webhook `<Say tr-TR>Şu anda cevap veremiyorum, lütfen daha sonra arayın.</Say><Hangup/>` döner,
   köprü açılmaz.
8. **İmza.** `X-Twilio-Signature` = base64(HMAC-SHA1(auth token, genel URL + alfabetik alan adı+değer; yinelenen
   adın değerleri sıralı)), `hmac.compare_digest`. URL DAİMA `telephony_public_base_url + path` (ters vekilin
   arkasında uygulama `http://<ip>:8001` görür; `request.url` kullanılmaz). İmzasız/yanlış -> 403 gövdesiz.
   Port tuzağı: Twilio'nun kendi doğrulayıcısı portlu ve portsuz URL'i birlikte dener; biz katıyız (8443 URL'de
   yazılıdır), ama yalnız portsuz biçim tutsaydı `telephony_inbound_signature_port_variant` uyarısı log'a düşer
   (imza/token log'lanmaz). İlk gerçek aramada 403 gelirse sebep tek satırda görünür.
9. **bridge_token.** Medya WebSocket'i imzasızdır; yetkisi voice webhook'unun verdiği tek kullanımlık, 2 dakikalık,
   256 bitlik, CallSid'e bağlı belirteçtir (`<Parameter name="bridge_token">`, Stream URL'i sorgu dizgisi almaz).
   Eksik/yanlış/kullanılmış/süresi geçmiş/başka CallSid -> 1008 ve hiçbir realtime bağlantısı açılmaz.
   Sonraki adım: Twilio medya el sıkışmasını da `x-twilio-signature` ile imzalıyor; gerçek aramada imzalanan URL
   ölçülünce ikinci kilit olarak eklenir.
10. **`interrupt_response: true` (tarayıcı yolundan BİLEREK farklı).** Tarayıcı yolunda M16 kararı `False`dur
    (istemci, araya girenin sahip olup olmadığını süzer). Telefonda süzecek istemci yoktur ve arayan zaten sahip
    değildir; arayan konuşmaya başlayınca JARVIS susmalıdır. Ek olarak `speech_started`'ta Twilio'ya `clear`
    gönderilir (Twilio'nun tamponundaki ses de kesilir).
11. **Durum süreç içindedir.** API tek uvicorn işçisiyle koşar: belirteç deposu ve aktif çağrı kümesi bellekte doğrudur.
    **Mavi-yeşil yayın anında süren gelen arama düşer** (yeni kapsayıcı belirteci ve soketi bilmez); arayan
    hattın kapandığını duyar, ledger satırı eski kapsayıcının finalize'ı yetişirse yazılır. Kabul edildi: günde
    birkaç arama, yayın birkaç saniye.
12. **Sağlayıcı arayüzü.** `InboundTelephonyProvider` Protocol'ü (`inbound_twilio.py`) imza, TwiML ve çerçeve
    biçimini soyutlar; Twilio uygulaması `TwilioInbound`; Telnyx sonra aynı biçimi doldurur. Realtime bacağı
    `RealtimeLeg` Protocol'ü arkasındadır (OpenAI uygulaması `OpenAIRealtimeLeg`, websockets ile; SDK yok).
13. **Maliyet.** Twilio Media Streams 0,0044 $/dk; numara ve gelen dakika konsolda. OpenAI realtime dakika ücreti
    ölçülmedi: günlük 30 dk tavanı üst sınırdır; ilk hafta ledger süreleri × fatura ile ölçülür.

## Reddedilenler

twilio-python SDK (tek ihtiyaç 40 satır stdlib), openai SDK (ikinci realtime şeması), pipecat/LiveKit (büyük,
yeniden örnekleme gereksiz), örnek uygulamanın beta `g711_ulaw` alanları (GA reddeder), ses kaydı/Twilio Record.

## BAĞLAMA KARTI (tam metin — lead, config.py/main.py/test_identity_enforcement.py/docker-compose.prod.yml
kartları birleşince keser)

- id: inbound-calls-wire
- title: Gelen aramalar: köprüyü uygulamaya bağla (router + ayarlar + açık uç listesi)
- area: services/api/app/main.py, services/api/app/config.py, services/api/tests/unit/test_identity_enforcement.py,
  infra/docker/docker-compose.prod.yml, services/api/tests/unit/test_compose_*.py (yalnız
  ilgili satırlar), services/api/tests/unit/test_telephony_inbound_wire.py (yeni)
- goal:
  1. `config.py` `Settings`'e iki alan, telephony bloğunun sonuna:
     `telephony_inbound_enabled: bool = False` (env `PAGENTOS_TELEPHONY_INBOUND_ENABLED`) ve
     `telephony_inbound_daily_minutes: int = 30` (env `PAGENTOS_TELEPHONY_INBOUND_DAILY_MINUTES`).
     `inbound_settings.from_settings` bunları `getattr` ile zaten okur; kod değişmez.
  2. `main.py` `create_app`: telephony router'ının include edildiği yerin yanına
     ```python
     from app.telephony import inbound_routes
     from app.telephony.inbound_bridge import InboundLine
     from app.telephony.inbound_records import InboundRecorder
     from app.telephony.inbound_realtime import OpenAIRealtimeLeg
     from app.telephony.inbound_settings import from_settings as inbound_from_settings
     from app.telephony.inbound_twilio import TwilioInbound
     ...
     inbound = inbound_from_settings(settings)
     app.state.telephony_inbound = InboundLine(
         settings=inbound,
         provider=TwilioInbound(inbound.twilio_auth_token),
         recorder=InboundRecorder(<telephony'nin kullandığı session_scope>),
         leg_factory=lambda: OpenAIRealtimeLeg(
             api_key=inbound.openai_api_key, base_url=inbound.realtime_base_url,
             model=inbound.realtime_model, voice=inbound.realtime_voice,
             transcription_model=inbound.transcription_model,
         ),
     )
     app.include_router(inbound_routes.router)
     ```
     (session_scope: `main.py`da `build_owner_caller(settings, session_scope=dispatch_session_factory, ...)`a verilen aynı `dispatch_session_factory`.)
  3. `test_identity_enforcement.py` `EXPECTED_OPEN`'a, `("GET", "/v1/telephony/audio/{token}")` satırının altına:
     ```python
     # inbound-calls-bridge: Twilio's webhooks for a call JARVIS answers on the owner's behalf. Twilio
     # holds no owner session; its X-Twilio-Signature (HMAC-SHA1 over the PUBLIC url + sorted fields,
     # compare_digest) is the authority - unsigned or wrongly signed is a 403 with no body.
     ("POST", "/telephony/inbound/voice"),
     ("POST", "/telephony/inbound/status"),
     ```
     ve `test_the_device_websocket_is_the_only_unauthenticated_socket` beklenen kümesi
     `{"/v1/devices/connect", "/telephony/inbound/media"}` olur (test adı/yorum: "the device socket and the
     inbound media socket"; yorum: medya soketinin yetkisi voice webhook'unun verdiği tek kullanımlık, 2 dk'lık,
     CallSid'e bağlı bridge_token'dır; yoksa 1008 ve realtime bağlantısı açılmaz).
  4. `infra/docker/docker-compose.prod.yml` api servisinin environment bloğunda, `PAGENTOS_TELEPHONY_PUBLIC_BASE_URL` satırının altına iki satır:
     `PAGENTOS_TELEPHONY_INBOUND_ENABLED: ${PAGENTOS_TELEPHONY_INBOUND_ENABLED:-false}` ve
     `PAGENTOS_TELEPHONY_INBOUND_DAILY_MINUTES: ${PAGENTOS_TELEPHONY_INBOUND_DAILY_MINUTES:-30}`;
     compose bekçi testlerinde (test_compose_*) env listesi bunu bekliyorsa aynı iki satır.
  5. `test_telephony_inbound_wire.py`: `create_app(Settings(_env_file=None))` -> `app.state.telephony_inbound`
     bir `InboundLine`, `settings.enabled is False`; imzasız `POST /telephony/inbound/voice` -> 403 gövdesiz;
     `Settings(telephony_inbound_enabled=True, ...)` ile imzalı istek -> 200 application/xml ve KVKK metni.
- acceptance: `uv run pytest tests/unit/test_identity_enforcement.py tests/unit/test_telephony_inbound_*.py
  tests/unit/test_health_endpoint.py -q` yeşil; açık uç sayımı tam olarak 2 yeni POST + 1 yeni WebSocket artar
  (inspector bunu create_app üzerinde ölçer); `PAGENTOS_TELEPHONY_INBOUND_ENABLED` yokken arama refuse TwiML alır;
  ruff 0; yeni göç yok.
- not: `docs/THIRD_PARTY_COMPONENTS.md`'ye Twilio kaydı (planın §11 metni) lead tarafından bu kartla eklenir.

## READY_FOR_OWNER (Onay Merkezi satırları; bağlama kartı yayınlanıp inbound-calls-public-path host adımı yapıldıktan sonra)

1. Twilio konsolu -> Phone Numbers -> numaranız -> Voice Configuration: "A call comes in" = Webhook,
   `https://<düğüm>.<tailnet>.ts.net:8443/telephony/inbound/voice`, HTTP POST; "Call status changes" =
   `https://<düğüm>.<tailnet>.ts.net:8443/telephony/inbound/status`.
2. `PAGENTOS_TELEPHONY_INBOUND_ENABLED=true` (set-cloud-secret.ps1 ile; sonra api yeniden başlatılır).
3. KVKK metnini okuyun (yukarıda, madde 4); değiştirmek isterseniz söyleyin.
4. Deneme hesabı: gelen arama YALNIZ doğrulanmış numaralardan kabul edilir ve önce İngilizce trial uyarısı çalınır.
   Başkalarının sizi araması (GSM yönlendirme yolu) için hesap yükseltme gerekir (ücretli; sizin kararınız).
5. (İsteğe bağlı, hesap yükseltilince) GSM koşullu yönlendirme: cevapsızda `**61*<Twilio numarası>#`
   (meşgulde `**67*`, ulaşılamazda `**62*`, hepsi `**004*`, iptal `##004#`). Yönlendirilen bacak sizin hattınızdan
   giden arama olarak ücretlenir; ücret için operatörünüze sorun. Twilio TR'de yerel/mobil numara satmaz (yalnız
   0850 ve ücretsiz hat; 0850 kimlik belgesi ister).
6. Deneme araması (PROVEN_REAL): başka bir telefondan Twilio numarasını arayın; Türkçe KVKK duyurusunu duyun; adınızı
   ve bir mesaj söyleyin; JARVIS Türkçe cevap verir ve "ileteceğim" der; kapatın; Kokpit'te "<numaranız> aradı"
   bildirimi ve mesajın metni görünür; ledger satırında ses yoktur. Tutanağın Türkçe doğruluğunu (8 kHz telefon
   sesi) okuyarak değerlendirin.

## Geri alma

Bu kart: router bağlı değil -> dosyaları silmek yeter. Bağlandıktan sonra: `PAGENTOS_TELEPHONY_INBOUND_ENABLED=false`
(webhook refuse TwiML döner), ya da Twilio konsolunda Voice webhook'unu boşaltmak, ya da Funnel 8443'ü kapatmak.
