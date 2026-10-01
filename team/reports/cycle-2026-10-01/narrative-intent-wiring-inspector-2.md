**Denetleyici raporu: narrative-intent-wiring**

Kapsam: alan yalnızca 4 dosya (`git diff` dal tabanına karşı). Başka dosya değişmemiş. `main...HEAD` farkı kuyruk ve betik dosyalarını da gösterir, çünkü dal `team/nightly/lead` tabanından açılmış. Bunlar bu görevin değişikliği değil.

**Geçiş 1, çalıştırma** (ana checkout venv, `app.__file__` worktree içinde):
- `test_narrative_intent_wiring.py`: 32 passed, sıfırdan çalıştırıldı.
- Komşu kümeler (`-k "intent or explain or narrative or misroute or voice"`): 1667 passed, 11912 deselected (65,9 sn). Kümeyi işçiden farklı seçtim. İşçinin 896 sayısıyla karşılaştırılamaz.
- `ruff check` üç kod dosyasında temiz.
- Benim mutasyonum M-C: `_narrative_match` tanıyıcıyı yok sayıp her metin için "bu hafta" döndürüyor. Sonuç 4 failed, 28 passed. Düşen testler tam olarak yakın-kaçırma testleri: "hafta sonu ne yapalım", "geçen hafta ne oldu", "ne oldu", "bu hafta sonu ne oldu". Dosya yedekten geri yüklendi, sha256 `231feaef…7259ea8`, tam 64 hex, işçiyle aynı. `git status` temiz.
- Önceki aşamalarda benim M-A ve M-B mutasyonlarım da RED idi. Bu turda yeniden çalıştırmadım, önceki rapordan taşıdım.

**Geçiş 2, kırmaya çalışma:**
- Gölgeleme: narrative dalı son sırada, NONE'dan hemen önce. `_OWNED` tablosundaki 10 cümle eskisi gibi yönleniyor. Tablonun en az 8'i tanıyıcı tek başına olsaydı alırdı. Bu sayıyı bir test koruyor.
- Sınıf: `klass_for` "query" döner (okuma). Step-up, onay ve cihaz komutu yok.
- Dairesel import: `engine.py`, `voice.intents`'ten içe aktarıyor. Tersi yön yerel import olarak kalmış. Çalışma zamanında döngü çıkmadı, testler geçti.
- Kapsam dışı düzeltme: `engine.py`'de iki ilgisiz ruff-format değişikliği var (`finding.detail` ve `research.completed` satırları). Davranış değişmiyor, ama diff'i gürültülü yapıyor. Engelleyici değil.
- Sızıntı/gizlilik: dalda yeni sır, yol veya log yok. Yeni bağımlılık yok.
- ADR taslağı dört cümleyle kararı, geri almayı ve gölgeleme korumasını anlatıyor. Numarası yok, lead verecek.

**Doğrulanan boşluk** (`app/explain/service.py`, alan dışı):
- `LedgerEvidenceSource` içinde `narrative` metodu yok.
- `explain_to_briefing` yalnızca `classify(question, now=now)` çağırıyor (satır 469), `narrative_query` çağırmıyor.
- Sonuç: bugün canlıda yönlendirici EXPLAIN/narrative verir, ama `activity.explain` "Bu konuda kayıt bulamadım." der. Görev kartı "explain'e sorgu türü" istiyordu, alan da yalnızca `engine.py`. Yani boşluk işçinin hatası değil, ama canlı kullanım için birleştirme engelleyicisi.

**Açık riskler:**
- `_OWNED` tablosu elle yazılmış. Başka bir aile kapsamını genişletirse test bunu kendiliğinden yakalamaz.
- `query_kind="narrative"` için `_scope_for`, `classify_research_shape` ve realtime oturum servisi yalnızca komşu kümelerin yeşilliğiyle kapsanıyor. Bu türü kullanan doğrudan test yok.

**Lead için birleştirmede** (işçi raporundaki 5 madde geçerli):
1. `LedgerEvidenceSource.narrative` ekle: `tell(db, ask.period, ask.device, narrator)`. Narrator `ModelNarrator(router sağlayıcı)`, sağlayıcı yoksa `RuleNarrator`.
2. `explain_to_briefing` içinde `narrative_query(...) or classify(...)`.
3. `explain_to_briefing` üzerinden giden bir `narrative` kaynak testi yaz.
4. ADR'yi numaralandırıp `docs/DECISIONS.md`'ye taşı.

**Kanıt sınıfları:**
- PROVEN_AUTOMATED: yönlendirici ve motor, sqlite ledger ve sahte sağlayıcıyla.
- NOT_RUN: canlı ses yolu, gerçek Postgres, gerçek Haiku çağrısı.
- READY_FOR_OWNER: sahibin "bu hafta ne oldu" demesi. Ancak `service.py` bağlantısı yapıldıktan sonra anlamlı.

**APPROVE**
