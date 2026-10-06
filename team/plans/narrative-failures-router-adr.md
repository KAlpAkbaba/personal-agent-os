# ADR (taslak, numarasız): dönem/çoğul başarısızlık sorusu anlatıya gider (ADR-0244 A)

Bağlam: ADR-0244 A / ADR-0255 C. Türkçe harfli "bu hafta / bugün / dün / neler / ofiste ne başarısız
oldu" yönlendiricinin 1b adımında explain `failures` ailesine düşüyordu (sınıflandırıcının
`("ne","başarısız")`, `("başarısız","oldu")` kökleri); o aile yalnız EN SON başarısızlığı söyler.
ASCII yazımlar sınıflandırıcıya uymadığı için 99. adıma (anlatı) ulaşıyordu.

Karar (intents.py, tabloların kendi mekanizması):
1. 1b adımında `explain_kind == "failures"` iken `_failures_narrative_match`: anlatı tanıyıcısı
   `failures_only=True` diyorsa VE cümle bir aralık soruyorsa (bütün kelime, katlanmış karşılaştırma:
   `neler`, `bugün`, `dün`, ardışık `bu hafta`, ya da tanıyıcının cihazı) sonuç `query_kind="narrative"`,
   `matched=<dönem>`. Dönem ve cihazı tanıyıcı okur (ikinci tablo yok).
2. `son` bütün kelimesi (en son / son hata) en sonuncuyu sorar: `failures` ailesinde kalır.
3. Çıplak tekil "ne başarısız oldu": yerinde kalır (failures). Okumam: tests/voice_corpus'ta
   "başarısız" içeren cümle YOK; ailenin sahibi ADR-0230'un `_OWNED` listesi ve
   test_explain_narrative_model'in OWNER_FAILURE_QUESTIONS'ı. Korpus aksini söylemiyor.
4. `_explain_kind`: `failures` okuması bir "hata" kelimesi ya da "başarısız" yanında bütün bir soru
   kelimesi (`ne, neler, neydi, nelerdi, hangi, hangileri, hangisi, oldu, kaldı`) ister;
   "başarısız olduğunda haber ver" artık ne `failures` ne anlatı ("olduğunda" yalnız "oldu" ile başlar).
5. `_OWNED`: "bu hafta ne başarısız oldu" çıktı, "en son ne başarısız oldu" (failures) girdi
   (gölge testinin ≥ 8 eşiği korunur).

AÇIK (alan dışı; uygulama 2026-10-05 dönüşünde dala commitlendi, aşağıdaki test lead'in alan genişletmesini bekliyor):
- tests/unit/test_explain_narrative_model.py::test_the_owners_spelling_is_answered_without_the_model_whoever_owns_it
  ["bugün ne başarısız oldu"] kırmızı olur: test her iki yolda da cevabın bir başarısızlık adlandırmasını
  bekliyor; tohumda BUGÜN başarısızlık yok, anlatı doğru olarak "Bu dönemde başarısız iş yok." der
  (eski `failures` cevabı dünkü işi "bugün"e söylüyordu). Düzeltme: o testin anlatı dalında
  `record.speech == NO_FAILURES_TEXT` de kabul edilsin (ya da "bugün" parametresi anlatı beklentisine taşınsın).
- ASCII çıplak "ne basarisiz oldu" ve "en son ne basarisiz oldu" bugün de anlatıya gider (sınıflandırıcı
  ASCII kök bilmez; `query_for` sınıflandırıcıyla cevaplar). Türkçe biçimleriyle eşitlemek
  `app/explain/classify.py`'ye ASCII kök ister - alan dışı, ayrı iş.
- "bugün sonuçta ..." sınıflandırıcıda `("son","bug")` → last_defect (iki kök tuzağı, classify.py) - alan dışı.

Kanıt (uygulama yerelde uygulanmışken): yeni test 37/37 (önce 15 kırmızı), wiring 32/32;
router'ı içe aktaran 50 birim dosyası 1919 geçti / 1 kırmızı (yukarıdaki). Mutasyon: dönem kuralı
kaldırıldı → 4 RED; bütün-kelime → önek → 4 RED; sha256 önce/sonra 52430517…823544 eşit.
Owner Utterance Suite: uygulama commitinden sonra koşuldu (sonuç işçi raporunda).
Geri alma: 1b'deki dalı ve `_explain_kind`'deki satırı silmek.

Uygulama dalda (intents.py, test_narrative_intent_wiring.py `_OWNED`); yama metni kaldırıldı.
