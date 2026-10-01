**Report: answer-mode-intent-precision**

**Şu an üzerinde çalışılan:** answer-mode-intent-precision; alan: intents.py, realtime_sessions/tools.py, memory/extraction.py, iki test dosyası, ADR metni; makine: MAIL. `docs/HANDOFF.md`'ye dokunmadım, lead birleştirirken yazacak.

**sha:** 0fa841a904ad0d8eeb186dc12b8ac0d528ddcf6d, branch push edildi, worktree temiz.
**Files:** 6, hepsi alanın içinde (3 app, 2 yeni test, `team/plans/answer-mode-intent-precision-adr.md`).

**Fixes**
- **(a) Router:** `_research_open_match` artık, okuma fiili + duran işaretçi ("bundan sonra/artık/hep/her zaman") + seviye kelimesi yok ise `None` döner. İşaretçi testi `_is_standing_sentence` yardımcısında; `_answer_mode_match` da onu kullanıyor, ikinci ifade tablosu yok.
  - `intents.py`'de tercih/hafıza niyeti yok (`MEMORY_REMEMBER` yalnız "hatırla/aklında tut" içindir). Bu yüzden cümle düz yola düşer (`Intent.NONE`).
  - Ayrı bir dil-tercihi niyeti bu görevin dışında.
- **(b) `research_answer_mode`:** düzey yalnızca `intent == research_answer_mode` ve geçerli `answer_level` taşıyan tur kaydından gelir. Aksi halde `VALIDATION_ERROR` ve sözlü cümle atar (`ANSWER_MODE_NO_LEVEL_TR`). Modelin `level` argümanına artık bakmıyor. Kasıtlı olarak kartın istediğinden biraz daha sıkı: "Araştırmayı oku" kaydındaki `detail` de artık durable düzeyi değiştiremez.
- **(c) `extract_from_summary`:** özet `|` ve satır sonundan bölünüyor. `Asistan:`/`Assistant:` satırları atlanıp `skipped` sayılıyor, hiç dosyalanmıyor. `Sahip:`/`Owner:` öneki sahibin satırından soyuluyor (davranış değişikliği, bilinçli).

**Tests:** 13 yeni (`test_voice_intents_answer_mode.py`, `test_memory_extraction_assistant_lines.py`). **RED→GREEN:** öncesi 9 failed / 4 passed, sonrası 13 passed. Üç dil cümlesi, üç komşu cümle (`Bundan sonra teknik anlat`, `Teknik modu kapat`, `Araştırmayı oku`), tool reddi ve düzeyin değişmemesi, dört özet biçimi (Sahip/Asistan, Owner/Assistant, `|` ve satır sonu) kapsanıyor.

**Mutation RED** (yedek `/tmp/mutbak`'tan geri yüklendi; sha256 önce/sonra aynı: extraction `a4f37945…`, intents `45cb9934…`, tools `a6d1f897…`):
- Konuşmacı öneki atlaması kaldırıldı → 5 failed.
- Duran işaretçi koruması kaldırıldı → 3 failed.
- Tool reddi kaldırıldı → 1 failed.

**Evidence class (çalıştırıldı):** PROVEN_AUTOMATED. Yeni testler + `test_memory_extraction*`, `test_voice_research_b31`, `test_voice_intents`, `test_voice_corpus_regressions`, `test_owner_utterance_corpus`, `test_voice_realtime_sessions` yeşil (hata satırı yok). Ruff format + check temiz. Sonuçlar ruff'tan önceki çalıştırmalardan; ruff'ın yeniden biçimlediği tek dosya test_voice_intents_answer_mode.py için testi ruff sonrası tekrar koşmadım.
**PROVEN_REAL:** NOT_RUN; sahip yayından sonra cümleyi tekrarlamalı.

**Yapamadığım / açık riskler**
- "Üç cümleyi corpus'a ekle" eksik: `tests/voice_corpus/corpus.py` alanımda değil. Cümleler kendi test dosyamda, corpus'a eklemek ayrı iş.
- Sahibin durable düzeyi hâlâ `detail`; bu kod onu geri almıyor, lead'in sahibe sorduğu karar bekliyor.
- Model argümanı artık yok sayıldığından, STT "teknik" kelimesini kaçırırsa model tek başına kipi değiştiremez. Sahip bir soru duyar ve tekrar söyler; bilinçli tercih.
- `|` bölmesi: sahibin cümlesi içinde düz `|` geçerse iki parçaya bölünür. Özette pratikte görülmedi.
- Ayrı "Sahip:" öneki soyma, varsa önceki kayıtların metnini değiştirmez (yalnız yeni yazımlar).
- ADR metni `team/plans/` altında, numarasız; lead numaralayıp `docs/DECISIONS.md`'ye taşır.
