## Şu an üzerinde çalışılan
`understanding-corrections-memory` (ADR-0224 düzeltmeler) · alan: `corrections.py`, `intents.py`, `memory/policy.py`, test, ADR planı · makine: sahibin geliştirme PC'si, worktree `worker-understanding-corrections-memory`. Durum: alan içi iş bitti ve push edildi; **relay bağlantısı ve migration alan dışında, lead'in** — onlar olmadan üretimde hiçbir şey bu modülü çağırmaz.

**sha:** `0a58152428c0def777ff06470c6b149c942b02bf` (push edildi, ağaç temiz). **Dosya:** 5, hepsi alan içinde.

**Yapılan**
- `memory/policy.py`: `vocabulary` sınıfı (satır 1a). Bayraksız gözlem hiç yazılmaz (aday da olmaz); bayraklıysa kalıcı, açık, sahip kaynaklı.
- `corrections.py`: düzeltme turu ("hayır, ofis bilgisayarında", "onu değil, Not Defteri", "ona X deme, Y de"), hafıza servisi üzerinden yazım, sürüm kontrollü yeniden yükleme, `read_turn` sarmalayıcısı, eşanlamlı başına tek öneri dosyası. `stt-confusions.json` hiç açılmaz (sha256 testle sabit).
- `intents.py`: `_app_open_match`, izin listesi bir ad bulamazsa turun sözlüğüne sorar.
- Silme: `forget_memory` sonrası eşanlamlı sonraki kontrolde düşer, cümle yeniden LOW olur.

**Kanıt (PROVEN_AUTOMATED, SQLite)**
- RED→GREEN: önce toplama hatası (modül ve sınıf yok); şimdi 34 passed, 2 skipped (iki relay testi relay bağlanana dek atlanır).
- Kabul: LOW soru → "ofis bilgisayarında" → satır `ofüs = ofis (cihaz)`, durable/explicit. Aynı cümle ikinci çağrıda HIGH 1.0, katman `vocabulary`, kanıtta eşanlamlı.
- Mutasyon (son halde, sha256 `74ee1e9b…` önce = sonra, yedekten geri yükleme): sürüm kontrolü kaldırıldı → 6 test RED, ikinci çağrı LOW.
- Diğer mutasyonlar (ara halde, tümü RED, yedekten geri yüklendi): policy kuralı, öneri dosyasının tekilliği, intents kancası, dört bilinen-kelime koruması, işaret kelimeleri, bayat tur, read-back sonrası çıplak ifade, katman adı.
- Owner Utterance Suite, commit edilmiş halde: **2756 passed, 0 failed** (959 s). Ruff check ve format temiz.
- Relay yaması `service.py`'ye geçici uygulandı, sonra bayt bayt geri alındı (sha `be5fbe2b…fba8ad` önce = sonra). Yamalıyken: iki relay testi geçti, relay/understanding/memory suitleri 394 passed, korpus 2756 passed (917 s).

**Yolda bulunan ve düzeltilen hata**
- Duyulan kelime konumdan tahmin ediliyor: "hemen bilgisayarımda … aç" + "ev" cevabı "hemen = ev" öğretiyordu; sonraki "hemen hesap makinesini aç" evde HIGH açılırdı. Tahmin edilen kelime artık cevaptaki takma ada benzemek zorunda (≥ 0.6); regresyon testi RED→GREEN. "Ona X deme, Y de" iki tarafı söylediği için muaf.

**For the lead at merge** (ayrıntı ve yama: `team/plans/understanding-corrections-memory-adr.md`)
1. **Migration 0064:** `ck_memories_class` bugün `vocabulary` satırını reddeder. SQLite'ta bu kısıt yok; birim testleri bunu göremez.
2. `app/memory/types.py`: `MemoryClass.VOCABULARY`. Yoksa `service.supersede_memory` (satır 648) vocabulary satırında ValueError verir.
3. Relay: `service.py`'ye 5 parçalı yama.
4. HANDOFF, ADR numarası, DECISIONS. `protocol_files.py` ve falsification listesi için bir şey yok.

**Yapılamayan / NOT_RUN**
- Postgres'te vocabulary satırı yazımı: NOT_RUN (migration alan dışı).
- Commit edilmiş dalda relay uçtan uca: çalışmıyor, iki test SKIPPED; yalnız yamalı halde kanıtlandı.
- `LocalEmbedder` ile yakın biçim (MEDIUM): NOT_RUN; 0.67 `DeterministicEmbedder` sayısı.
- Tam birim suiti: NOT_RUN (yalnız ilgili suitler ve korpus).
- PROVEN_REAL: sahibin üretimdeki ilk düzeltmesinde.

**Açık riskler**
- "ofüs'ü unut" tek cümleyle silmez: `memory.forget` hâlâ arama + read-back ister. `named_synonym` hazır, `tools_memory.py` alan dışı.
- Cloud Core imajında checkout yok: `PAGENTOS_TEAM_PROPOSALS_DIR` verilmezse hafıza yazılır, öneri yazılmaz (`no_proposals_dir` olarak raporlanır).
- "Ona X deme, Y de" sessiz öğretir; sesli alındı yok.
- "Onu değil, Not Defteri" turu yeniden çalıştırır ama kelime öğretmez (duyulan uygulama kelimesi tutulmuyor).
- `learn` relay oturumunu erken commit eder (`_extract_memories` emsali).
