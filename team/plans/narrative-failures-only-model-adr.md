# ADR (taslak): "ne başarısız oldu" yalnız başarısızları anlatır; explain kaynağı model anlatıcıyı kullanır

Bağlam: ADR-0216/0221/0230 toplayıcıyı, kural anlatıcıyı, denetçiyi ve `ModelNarrator`'ı yazdı.
`LedgerEvidenceSource.narrative` yalnız kural anlatıcıyı çağırıyordu ve `NarrativeAsk.failures_only`
taşınıyor ama uygulanmıyordu: başarısızlık sorusuna tamamlanan her alan da okunuyordu.

Karar:
1. `facts.only_failures(facts)` saf bir daraltmadır: `completed=()`, sayımlar yalnız başarısız
   satırlardan, `total=len(failed)`; dönem ve cihaz aynen kalır.
2. `narrative.service.tell(..., failures_only=False)` daraltmayı ANLATMADAN ÖNCE uygular. Anlatıcılar
   ve denetçi değişmedi: tamamlanan satır olgularda olmadığı için modele gösterilmez, tamamlananların
   sayısı da denetçi için "yabancı sayı"dır (taslak reddedilir, kural metni okunur). Başarısız iş
   yoksa tek sabit cümle döner (`NO_FAILURES_TEXT`) ve hiçbir anlatıcı çağrılmaz.
3. `LedgerEvidenceSource(db, chat_provider=None)`: sağlayıcı verilmiş VE `configured` ise
   `ModelNarrator(provider)`, değilse kural anlatıcı. `evidence_source_factory` artık bir fonksiyon;
   sağlayıcıyı `app.assistant_chat.build_chat_provider(get_settings())` ile kurar, ayar okunamazsa
   `None` verir (asla fırlatmaz). Yeni istemci yok.

Bilinen sınır (bu işin alanı dışında, lead'e): yönlendirici "ne başarısız oldu"yu Türkçe harfleriyle
explain `failures` ailesine verir; `query_for` yönlendiriciye uyduğu için o cümle anlatıya ULAŞMAZ.
Bugün anlatıya `failures_only=True` ile yalnız sınıflandırıcının tanımadığı yazım ulaşır
("ne basarisiz oldu"). Sahibin sesli sorusunda davranış değişsin isteniyorsa yönlendiricide ayrı bir
karar gerekir (`test_narrative_intent_wiring._OWNED` bugünkü sahipliği sabitliyor).

Sonuçlar: anahtar tanımlı üretimde "bu hafta ne oldu" artık bir Haiku isteği yapar (en çok
`assistant_chat_timeout_s`, bir yeniden deneme); hata/ret/denetim reddinde kural metni okunur.
Fabrika süreç ayarını (`get_settings`) okur, ses aracının `ctx.live` ayarını değil: `tools.py`
çağrısı `source=`/sağlayıcı geçirecek şekilde bağlanırsa tek kaynak olur (alan dışı).
Kanıt: sahte sağlayıcıyla PROVEN_AUTOMATED; gerçek Haiku + gerçek PostgreSQL NOT_RUN.
Geri alma: `narrative()` içinde `narrator=None`, `failures_only=False` geçirmek yeter.
