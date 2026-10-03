**Şu an üzerinde çalışılan:** `local-embedder-lru-lock` (2. tur, geri gönderme). Alan: `services/api/app/memory/providers.py`, `tests/unit/test_memory_local_embedder.py`, `team/plans/local-embedder-lru-lock-adr.md`. Makine: sahibin bilgisayarı (Windows).

## Rapor: inspector'ın iki geri gönderme maddesi kapandı; iki kısmi kilit mutantı artık her koşuda kırmızı

- **Commit:** `fe7bfb1283e4b9f4d3073fd23c075068663114b5`. `team/d20261002/worker-local-embedder-lru-lock` dalına gönderildi; origin aynı commit'te, worktree temiz.
- **Değişen dosyalar:** 2 dosya, ikisi de alanın içinde: test dosyası ve ADR taslağı. `providers.py` bu turda değişmedi; sha256 `d1658024e4a5…` (`git show HEAD~1` ile alınan, kilit öncesi `providers.py` sadece mutasyon koşusunda kullanıldı, dosyaya yazılıp bırakılmadı).

**Madde 1: kısmi kilit mutantları artık zorlanmış testlerde kırmızı**
- Testlerin kullandığı önbellek sınıfı değişti (`_HeldLookupCache` yerine `_HeldCache`). Artık bir iş parçacığını iki noktada, her birinde en fazla bir kez durdurabiliyor:
  - `move_to_end(refresh_key)` çağrısının tam önünde: giriş okundu, yeri henüz tazelenmedi.
  - `popitem` çağrısının önünde: giriş yazıldı, en eski henüz atılmadı.
- **`test_an_eviction_cannot_land_between_a_hit_and_the_refresh_of_its_place` (yeniden yazıldı):** A `move_to_end` önünde tutulur. B modelden bırakılır, yazar ve en eskiyi atar. A'nın yerine geçmesi B'nin kilitte beklemesine ya da dönmesine bağlı; uyku yok, sıra olaylarla kurulur.
- **`test_a_hit_cannot_land_between_a_store_and_its_eviction` (yeni):** B yazdıktan sonra `popitem` önünde tutulur, A en eski girişi ister.
  - Kilitli kodda A kilitte bekler ve sonra ıskalar: metni yeniden hesaplar, en yeni giriş olarak yazar.
  - Mutantta A isabet eder, B atar, A'nın tazelemesi `KeyError` verir.

**Madde 2: yazma yolundaki `move_to_end(key)` artık bir testle sabit**
- **`test_a_text_computed_twice_ends_as_the_newest_entry` (yeni):** Bunun için `_GatedModel`'e `once=` seçeneği eklendi.
  - Birinci iş parçacığı metni hesaplarken modelde tutulur. Bu sırada ana iş parçacığı aynı metni hesaplayıp yazar, sonra başka bir metin yazar.
  - Birincinin geç yazması girişi en yeniye taşımalı: `[other, text]`. Bu satır olmasaydı az önce kullanılan metin, ondan sonra kullanılanlardan önce atılırdı.

**Mutasyonlar** (her biri yedek kopyadan geri yüklendi; her seferinde sha256 `d1658024…` olarak doğrulandı, `git checkout --` kullanılmadı)

| mutant | sha256 | sonuç |
|---|---|---|
| kilit yalnız `get`'i kapsıyor (isabetin `move_to_end`'i dışarıda) | `202652448a73…` | hit/refresh testi KIRMIZI 3/3, `KeyError('metin 0')` |
| `popitem` kilidin dışında | `bc00c1f1cda5…` | store/evict testi KIRMIZI 3/3, `KeyError('metin 0')` |
| yazma yolundaki `move_to_end` silindi | `30a2bcb7b70d…` | computed-twice testi KIRMIZI 2/2 |
| kilit yok (kilit öncesi kod) | `2e0872520250…` | üç zorlanmış test KIRMIZI 2/2 |
| model çağrısı kilidin içinde | `88b609ee7971…` | 4 test KIRMIZI; "önbellekteki metin o sırada döner" ve "ikisi de hesaplar" bunların içinde |

Her kırmızı, kendi zorladığı sıradan geliyor; stres döngüsüne ya da şansa bağlı değil.

**Testler ve kontroller**
- `test_memory_local_embedder.py`: düzeltilmiş kodda 5 koşunun 5'inde 24 test geçti (daha önce 22'ydi, 2 test yeni).
- `ruff check` ve `ruff format --check`: temiz.
- Kanıt sınıfı: kilit doğruluğu ve her kilitli bölümün bütünlüğü PROVEN_AUTOMATED.

**Yapamadıklarım**
- **mypy:** NOT_RUN; venv'de mypy kurulu değil.
- **Gecikme ve kurulum süresi ölçümleri:** bu turda yeniden yapılmadı, çünkü üretim kodu değişmedi. 1. turdaki ölçümler ve inspector'ın ölçümleri ADR taslağında duruyor.
- **ADR taslağı:** "Kanıt" bölümü yeni mutasyon tablosuyla güncellendi.

**Açık riskler**
- `OpenAIEmbedder` aynı kilitsiz LRU kalıbını taşıyor. Bu kartın dışında; ayrı bir kart gerekiyor.
- Inspector'ın bildirdiği `test_qualification_evidence` hatası (`docs/QUALIFICATION.md` satır 41.7) temel commit'ten geliyor, bu görevden değil. Lead'in `team/nightly/lead` dalında düzeltmesi gerekiyor.
