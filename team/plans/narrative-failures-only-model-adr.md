# ADR (taslak): anlatı yalnız başarısızları anlatabilir; explain kaynağı, ÇAĞIRANIN verdiği sağlayıcıyla model anlatıcıyı kullanır

Bağlam: ADR-0216/0221/0230 toplayıcıyı, kural anlatıcıyı, denetçiyi ve `ModelNarrator`'ı yazdı.
`LedgerEvidenceSource.narrative` yalnız kural anlatıcıyı çağırıyordu ve `NarrativeAsk.failures_only`
taşınıyor ama uygulanmıyordu: anlatıya ulaşan başarısızlık sorusuna tamamlanan her alan da okunuyordu.

Karar:
1. `facts.only_failures(facts)` saf bir daraltmadır: `completed=()`, sayımlar yalnız başarısız
   satırlardan, `total=len(failed)`; dönem ve cihaz aynen kalır.
2. `narrative.service.tell(..., failures_only=False)` daraltmayı ANLATMADAN ÖNCE uygular. Anlatıcılar
   ve denetçi değişmedi: tamamlanan satır olgularda olmadığı için modele gösterilmez, tamamlananların
   sayısı da denetçi için "yabancı sayı"dır (taslak reddedilir, kural metni okunur). Başarısız iş
   yoksa tek sabit cümle döner (`NO_FAILURES_TEXT`) ve hiçbir anlatıcı çağrılmaz.
3. `LedgerEvidenceSource(db, chat_provider=None)`: sağlayıcı verilmiş VE `configured` ise
   `ModelNarrator(provider)`, değilse kural anlatıcı.
4. (Denetleyici dönüşü, madde 2) Sağlayıcı SÜREÇ AYARINDAN OKUNMAZ. İlk sürümde fabrika
   `get_settings()` okuyordu: anahtarlı bir kabuk ya da `.env`, `explain_to_briefing`'e ulaşan her
   testin (korpus dahil) gerçek API'yi çağırmasına yeterdi. Şimdi:
   - `explain.service.narrative_chat_provider(live)`: oturumun `ctx.live` sözlüğünden, `assistant.chat`
     ile aynı sırayla (`live["chat_provider"]`, yoksa `build_chat_provider(live["settings"])`) alır;
     anahtar yoksa / ayar okunamazsa `None`, asla fırlatmaz. Yeni istemci yok.
   - `explain_to_briefing(..., chat_provider=None)` ve `evidence_source_factory(db, chat_provider=None)`:
     sağlayıcıyı yalnız çağıran verir. Verilmezse kural anlatıcı; tek argümanlı test fabrikaları
     (`lambda db: source`) aynen çalışır.

AÇIK - bu kartın alanında çözülemedi (lead'e, iki ayrı karar):
A. Yönlendirici. Sahibin cümlesi "ne başarısız oldu" (Türkçe harflerle; "bu hafta/bugün/neler …" de)
   explain `failures` ailesine gider, anlatıya ULAŞMAZ; o aile yalnız EN SON başarısızlığı söyler
   (ölçüldü: 2 başarısızdan 1'i okunuyor, tamamlanan okunmuyor). Anlatıya `failures_only=True` ile
   bugün yalnız "ne basarisiz oldu" (ASCII) ulaşır. Kart `query_for`'u ve yönlendiriciyi dondurduğu için
   değiştirilmedi; karar `app/voice/intents` + `test_narrative_intent_wiring._OWNED` alanında.
   Bu yüzden başlık "'ne başarısız oldu' yalnız başarısızları anlatır" İDDİA EDİLEMEZ; birleştirme
   başlığı "anlatı başarısız-yalnız kipini ve model anlatıcı bağlantısını taşır" olmalı.
B. Ses aracı. `tools.py::activity_explain` henüz sağlayıcı geçirmiyor (alan dışı), yani üretimde
   anlatı bu birleştirmeden sonra da KURAL anlatıcıyla okunur; Haiku isteği yapılmaz. Bağlamak tek
   satır: `explain_to_briefing(..., chat_provider=narrative_chat_provider(ctx.live))`. O satır
   girdiğinde "bu hafta ne oldu" her soruda bir Haiku isteği yapar (en çok `assistant_chat_timeout_s`,
   bir yeniden deneme; araç iş parçacığında) - bu davranış değişikliği o kartın başlığında yazmalı.

Sonuçlar: bu birleştirme üretimde sesli davranışı DEĞİŞTİRMEZ (A ve B'ye kadar); anahtarlı bir kabuk
hiçbir testte gerçek API'ye çıkamaz. Hangi anlatıcının konuştuğu (model/kural, düşüş sebebi, token)
hâlâ kaydedilmiyor (`tell()` yalnız metin döner) - ayrı iş.
Kanıt: sahte sağlayıcı ve sahte taşıma ile PROVEN_AUTOMATED; gerçek Haiku NOT_RUN; sahibin sesi NOT_RUN
(A çözülmeden READY_FOR_OWNER değil).
Geri alma: `narrative()` içinde `narrator=None`, `failures_only=False` geçirmek yeter.
