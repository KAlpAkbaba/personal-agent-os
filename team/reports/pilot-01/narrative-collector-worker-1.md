## Şu an üzerinde çalışılan
Görev `narrative-collector` (pilot-01), alan `services/api/app/narrative` ve iki test dosyası. Makine: ev PC (worktree `.claude/worktrees/team/pilot-01/worker-narrative-collector`). Bu blok HANDOFF'a ben yazmadım; lead birleştirme anında yazar.

**Rapor**
- sha: `8544c885761f7ef9e84aa5a09262f69fe3f7650c`, dal `team/pilot-01/worker-narrative-collector`, push edildi, worktree temiz.
- Değişen dosya: 9, hepsi alanın içinde. Yeni dosyalar: `app/narrative/{__init__,facts,collector,narrator,auditor,service}.py`, `test_narrative_collector.py`, `test_narrative_auditor.py`, `team/plans/narrative-collector-adr.md`.
- `app/ledger/service.py` ve `vocabulary.py`'a dokunmadım. Yalnızca `query()` üzerinden okuyorum. Statü ve olay tipi sabitleri import ediliyor. `no_capable_device` için ledger'da sabit yok, `facts.py`'de tek bir sabit tanımladım.

**Kanıtlar (PROVEN_AUTOMATED)**
- RED: modül yokken iki dosya da `ModuleNotFoundError: No module named 'app.narrative'` ile toplanamadı.
- GREEN: 38 test geçti (collector 17, auditor 21). Worktree'nin kodu import ediliyor, ana checkout'unki değil.
- Mutasyon: her biri kırmızı, ardından sha256 öncesi/sonrası aynı, geri yükleme yedekten yapıldı.
  - Toplayıcı ikinci hatayı atlıyor: 5 test kırmızı.
  - Denetçide sayı kontrolü kapalı: 2 kırmızı.
  - Denetçide eksik-hata kontrolü kapalı: 2 kırmızı.
  - Denetçide eksik-alt-sistem kontrolü kapalı: 1 kırmızı.
  - Cihaz süzgeci kapalı: 4 kırmızı.
  - Pencere sonu dahil edilmiş: 1 kırmızı.
- Yakın-ıska (near miss) çiftleri:
  - Yedi günün tam sınırında bir satır içeride, bir saniye öncesi dışarıda.
  - Bilinmeyen cihaz kelimesi ("kütüphane") hiçbir satırı seçmiyor, hepsini seçmiyor.
  - Özetin içindeki sayı yabancı sayılmıyor, uydurma "47" ve "127" yabancı sayılıyor.
- `ruff check` ve `ruff format --check` temiz.
- Tam birim paketini çalıştırmadım (NOT_RUN): yalnızca yeni iki dosya koştu.

**Davranış**
- Toplayıcı yalnızca `failed` ve `completed` satırlarını anlatır. Başarısızlar önce gelir, en yeni ilk. Tamamlananlar alt sisteme göre gruplanır. Alt sistem ve cihaz başına sayılar tutulur. `no_capable_device` olayları ayrıca listelenir.
- Ledger 200 satırla sınırlı. Toplayıcı zaman damgasıyla geriye sayfalıyor, 230 satırlık test geçiyor.
- Pencereler UTC ve `[başlangıç, bitiş)`. "bu hafta" son 7 gün, "bugün" gece yarısından şimdiye, "dün" önceki UTC günü. Tanınmayan kelime `ValueError` verir.
- `tell()` onarımdan sonra da denetimi geçemeyen anlatıcı çıktısını (uydurma sayı, atlanan alt sistem) atıp `RuleNarrator` metnini döndürür.

**Riskler ve karar noktaları (lead'e)**
1. **Cihaz kaynağı:** ledger'da cihaz sütunu yok. Cihazı `detail_json["device"]`'dan okuyorum ve cihaz adı vermeyen satırı `bulut` sayıyorum. Bugün hiçbir yazıcı bu anahtarı yazmıyor, yani cihaz süzgeci gerçek veride ancak yazıcılar anahtarı yazınca çalışır. Sonraki görev bunu yapmalı, ADR'de yazılı.
2. **Aynı zaman damgalı 200+ satır:** kesilir. ADR'de belgeli sınır.
3. **Test dosyaları:** ortak veri `test_narrative_collector.py` içinde, auditor testi oradan import ediyor. Alan dışına yeni dosya açmamak için böyle yaptım.
4. **Tetikleyiciler:** hiçbiri bağlanmadı (ses, REST). Görev kapsamı dışıydı.
5. **Dokunulmayanlar:** `HANDOFF`, `DECISIONS`, `BUILD_STATE` ve `THIRD_PARTY_COMPONENTS` dosyalarına dokunmadım. ADR metni `team/plans/narrative-collector-adr.md` içinde, numarasız.
