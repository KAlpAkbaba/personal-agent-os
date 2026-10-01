**Şu an üzerinde çalışılan:** narrative-intent-wiring · alan: intents.py, explain/engine.py, yeni test, ADR taslağı · makine: dev PC (worktree worker-narrative-intent-wiring). Bitti; HANDOFF'a yazmak lead'in işi.

**sha:** `07d8fb1e02b4202516d388d96b77504eda74ffc1` (dal push edildi, worktree temiz). Dört dosya değişti, hepsi alan içinde: `intents.py`, `engine.py`, `test_narrative_intent_wiring.py` (yeni), `team/plans/narrative-intent-wiring-adr.md`.

**Yapılan**
- `resolve_intent` saf olduğu için anlatı bir okuma olarak yönlendiriliyor: `Intent.EXPLAIN` ve `query_kind="narrative"`. Sınıfı `query`: step-up yok, onay yok, cihaz komutu yok.
- Tanıyıcı `_resolve_intent_rules` içinde en sonda, NONE'dan hemen önce çalışıyor. Başka bir ailenin aldığı cümleler eskisi gibi gidiyor.
- `engine.py`'ye `QUERY_NARRATIVE`, `narrative_query()` ve `explain()` içinde erken bir dal eklendi. Dal, kaynağın isteğe bağlı `narrative(ask, *, now)` metodunu çağırıyor.
- Metin özet, ayrıntı ve teknik seviyelerde aynı. Kaynakta metot yoksa "Bu konuda kayıt bulamadım." diyor, uydurma yok.

**RED → GREEN**
- Testler önce yazıldı. İlk çalıştırma `ImportError: cannot import name 'QUERY_NARRATIVE'` ile toplama aşamasında RED verdi.
- Sonra 7 test RED kaldı. Sebepleri `klass_for` beklentimin yanlış olması ("read" yerine "query") ve boş `Briefing` kurucusu; ikisini düzeltince `32 passed` oldu. Kalıcı RED kanıtı aşağıdaki mutasyonlar.
- Yakın komşu suitler de temiz: 762 passed (intent, explain ve narrative test dosyaları) ve ayrıca 424 passed (voice intents/router/explain tools/misroutes/b51/native/daily). `ruff check` ve `ruff format` temiz.

**Test kapsamı (dosya başına 32 test)**
- Router: "bu hafta ne oldu", "dün ne oldu", "ofiste bu hafta ne oldu", "evde ne oldu" ve "bu hafta ofiste ne yapıldı" anlatı olarak yönleniyor.
- Near miss: "hafta sonu ne yapalım", "geçen hafta ne oldu", "dün ne yaptım", "ne oldu", "bu hafta sonu ne oldu" anlatı değil.
- Başka ailenin sahip olduğu 10 cümle değişmedi: "bugün ne yaptın", "dün ne yaptın", "ofiste ne yaptın" ve "bugün neler yaptın" artifact_list'te; "bugün neler oldu", "bugün ne oldu", "ne başarısız oldu", "bu hafta ne başarısız oldu", "son yaptıkların neler" ve "hata varsa düzelt" explain'de. Bunların ilk sekizini tanıyıcı da kendi başına alıyor; bu bir test ile doğrulanıyor.
- Cevap, gerçek sqlite ledger'ı (collector fixture'ı) üzerinden `tell` ile üretiliyor ve sabit ledger'daki iki başarısızlığı adıyla söylüyor (`FAIL_RESEARCH`, `FAIL_MAIL`). Cihaz kelimesi daraltıyor. Sağlayıcı iki başarısızlığı atlarsa auditor onları geri koyuyor. Sağlayıcı yoksa RuleNarrator cevap veriyor.

**Mutasyonlar (tam sha256 ile geri yüklendi, `git checkout --` kullanılmadı)**
- Önce: `intents.py` 231feaefe0527067fa33ef302b7cdefb95bab39f8935c1ce3cda4eb7b7259ea8, `engine.py` 07c8e336f06bb65f37fcc62235196ab90d92e48de59be7528a144c2c48ba474f. Sonra aynı iki değer, geri yüklemeden sonra `32 passed`.
- M1 tanıyıcı çağrısı kaldırıldı: 10 failed.
- M2 anlatı bloğu explain'in 1b dalından önceye taşındı: 4 failed.
- M3 engine'deki narrative dalı kaldırıldı: 8 failed.

**Kanıt sınıfı:** hepsi PROVEN_AUTOMATED (gerçek router fonksiyonu, sqlite ledger, sahte sağlayıcı). Gerçek Postgres, gerçek canlı ses turu ve gerçek Haiku çağrısı NOT_RUN. READY_FOR_OWNER: sahip "bu hafta ne oldu" der.

**For the lead at merge** (alan dışı, ben dokunmadım; bunlar yapılmadan ses yolunda canlı değil)
1. `app/explain/service.py`, `LedgerEvidenceSource`: `narrative(self, ask, *, now)` ekle. `tell(self._db, ask.period, ask.device, ModelNarrator(provider) or None, now=now)` döndürsün; provider router'ın chat sağlayıcısı, yoksa None.
2. Aynı dosyada `explain_to_briefing` içinde `query = narrative_query(question, now=now) or classify(question, now=now)` yap. `classify` anlatıyı tanımıyor, bu tek satır olmadan `activity.explain` yolu anlatıyı hiç görmez.
3. Sağlayıcı verilmezse RuleNarrator kullanılır, bu istenen davranış. `ModelNarrator` yalnızca ledger'ın gerçek olgularını görür.
4. ADR metni `team/plans/narrative-intent-wiring-adr.md` içinde, numarasız. `docs/DECISIONS.md`'ye sen taşı.

**Açık riskler**
- `test_narrative_intent_wiring.py` içindeki `_OWNED` tablosu, başka bir ailenin bu cümleleri ileride daha geniş alırsa kırılır; beklenen, o zaman tablo güncellenir.
- `engine.py` artık `app.voice.intents`'ten `QUERY_KIND_NARRATIVE` import ediyor. Aynı zincir zaten `classify` üzerinden vardı, döngü oluşmadı (testler geçti).
- Makine notu: bash'te Türkçe argv'li scratch script'in asılı kaldığını gördüm (dosyadan okuyunca sorun yok). Ürüne etkisi yok.
