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

AÇIK (alan dışı - bu yüzden uygulama bu dalda COMMITLENMEDİ):
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
Owner Utterance Suite: NOT_RUN (uygulama commitlenmedi).
Geri alma: 1b'deki dalı ve `_explain_kind`'deki satırı silmek.

## Hazır yama (`git apply` ile, dal tabanı 920cf300)

```diff
diff --git a/services/api/app/voice/intents.py b/services/api/app/voice/intents.py
index 6851d207..ed9eb4f8 100644
--- a/services/api/app/voice/intents.py
+++ b/services/api/app/voice/intents.py
@@ -1413,6 +1413,65 @@ def _narrative_match(text: str) -> str | None:
     return None if ask is None else ask.period
 
 
+#: ADR-0244 A: the words that make a failure question one about a SPAN of the record - a
+#: period or the plural - rather than "what failed just now?". Whole words, compared folded
+#: (Turkish letters and the ASCII an STT writes resolve alike): "dün" must not be read in
+#: "dünyada", "neler" not in "nelerden". A named machine counts too (the recogniser's device).
+_FAILURE_SPAN_WORDS: Final[frozenset[str]] = frozenset({"neler", "bugün", "dün"})
+_FAILURE_SPAN_PAIRS: Final[tuple[tuple[str, str], ...]] = (("bu", "hafta"),)
+#: "en son ne başarısız oldu" / "son hata": the latest one - the explain ``failures`` family's
+#: own answer, whatever period is named with it. A whole word: "sonuçta" is not "son".
+_FAILURE_LATEST_WORDS: Final[frozenset[str]] = frozenset({"son"})
+#: The words that make "başarısız" a QUESTION about failures. Without one of them (and
+#: without a "hata" word) the sentence merely contains it: "başarısız olduğunda haber ver"
+#: is a request for later, and "olduğunda" only starts like "oldu" (the classifier's stems).
+_FAILURE_QUESTION_WORDS: Final[frozenset[str]] = frozenset(
+    {"ne", "neler", "neydi", "nelerdi", "hangi", "hangileri", "hangisi", "oldu", "kaldı"}
+)
+
+
+def _folded_words(tokens: tuple[str, ...]) -> tuple[str, ...]:
+    return tuple(asr_fold(tok) for tok in tokens)
+
+
+def _has_folded_word(folded: tuple[str, ...], words: frozenset[str]) -> bool:
+    wanted = {asr_fold(word) for word in words}
+    return any(tok in wanted for tok in folded)
+
+
+def _failures_narrative_match(tokens: tuple[str, ...], text: str) -> str | None:
+    """The period of a failure question over a span ("bu hafta ne başarısız oldu", "neler
+    başarısız oldu", "ofiste ne başarısız oldu"), or None.
+
+    The narrative recogniser decides that it IS a failure question (``failures_only``) and
+    reads its period and device; this only decides that the owner asked about a span, not
+    about the latest failure. The bare singular "ne başarısız oldu" stays with the explain
+    ``failures`` family (ADR-0230's owned list)."""
+    from app.narrative.intent import recognise
+
+    ask = recognise(text)
+    if ask is None or not ask.failures_only:
+        return None
+    folded = _folded_words(tokens)
+    if _has_folded_word(folded, _FAILURE_LATEST_WORDS):
+        return None
+    pairs = {(asr_fold(a), asr_fold(b)) for a, b in _FAILURE_SPAN_PAIRS}
+    spanned = (
+        ask.device is not None
+        or _has_folded_word(folded, _FAILURE_SPAN_WORDS)
+        or any(pair in pairs for pair in zip(folded, folded[1:], strict=False))
+    )
+    return ask.period if spanned else None
+
+
+def _failures_asked(tokens: tuple[str, ...]) -> bool:
+    """A ``failures`` reading is a question: a "hata" word, or "başarısız" with a question
+    word beside it (``_FAILURE_QUESTION_WORDS``), each a whole word."""
+    if _has(tokens, "hata"):
+        return True
+    return _has_folded_word(_folded_words(tokens), _FAILURE_QUESTION_WORDS)
+
+
 def _explain_kind(tokens: tuple[str, ...], text: str = "") -> str | None:
     """The kind of question about the system's own activity, or None.
 
@@ -1437,6 +1496,8 @@ def _explain_kind(tokens: tuple[str, ...], text: str = "") -> str | None:
     required = _INTENT_CORROBORATION.get(query.kind)
     if required is not None and not any(_has(tokens, stem) for stem in required):
         return None
+    if query.kind == "failures" and not _failures_asked(tokens):
+        return None
     return query.kind
 
 
@@ -10310,6 +10371,16 @@ def _resolve_intent_rules(
         and not (narration is not None and explain_kind in ("research_detail", "technical"))
         and not _full_read(tokens)
     ):
+        # ADR-0244 A: "bu hafta / neler / ofiste ne başarısız oldu" asks for the failures of a
+        # span - the narrative told failures only - not the latest one this family speaks.
+        if explain_kind == "failures" and (span := _failures_narrative_match(tokens, text)):
+            return ResolvedIntent(
+                Intent.EXPLAIN,
+                scope=SCOPE_CONVERSATION,
+                matched=span,
+                query_kind=QUERY_KIND_NARRATIVE,
+                **base,
+            )
         return ResolvedIntent(
             Intent.EXPLAIN,
             scope=SCOPE_CONVERSATION,
diff --git a/services/api/tests/unit/test_narrative_intent_wiring.py b/services/api/tests/unit/test_narrative_intent_wiring.py
index 1fccd543..cd1df968 100644
--- a/services/api/tests/unit/test_narrative_intent_wiring.py
+++ b/services/api/tests/unit/test_narrative_intent_wiring.py
@@ -132,7 +132,9 @@ _OWNED = [
     ("bugün neler oldu", Intent.EXPLAIN, "today"),
     ("bugün ne oldu", Intent.EXPLAIN, "today"),
     ("ne başarısız oldu", Intent.EXPLAIN, "failures"),
-    ("bu hafta ne başarısız oldu", Intent.EXPLAIN, "failures"),
+    # "bu hafta ne başarısız oldu" left this list: a failure question over a span is the
+    # narrative told failures only (ADR-0244 A, test_narrative_failures_router.py).
+    ("en son ne başarısız oldu", Intent.EXPLAIN, "failures"),
     ("son yaptıkların neler", Intent.EXPLAIN, "last_activity"),
     ("hata varsa düzelt", Intent.EXPLAIN, "failures"),
 ]
```
