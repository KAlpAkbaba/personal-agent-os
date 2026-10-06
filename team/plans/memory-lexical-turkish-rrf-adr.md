# ADR (taslak, numarasız): Hafızanın kelime ayağı kendi sırasını alır (RRF), Türkçe katlama, pg_trgm + `turkish` arama, ayar arkasında

Kart: `memory-lexical-turkish-rrf` (d20261006). Öneri: `team/proposals/2026-10-06-hafizada-isimler-kaybolmasin.md`.
Plan: `team/plans/memory-lexical-turkish-rrf-integration.md`.

## Bağlam

`hybrid_search` yalnız kelime ayağında bulunan anıya 0.0 anlam puanı veriyordu; kelime ayağı her ≥3 harfli kelimeyi
(durak kelimeler dahil) `str.lower` ile alıyordu ("İ" -> "i" + birleşik nokta). Sonuç: "Ahmet'e ne söz vermiştim?"
sorusunda Ahmet'i adıyla anan tek anı, anlamca daha yakın 59 benzerin altında kalıyor (gerçek PostgreSQL'de ölçüldü,
testte kırmızı), "İzmir" yazılan sorgu "İzmir" anısını PostgreSQL'de bulamıyordu.

## Karar

1. **Mod:** `PAGENTOS_MEMORY_LEXICAL` = `like` (varsayılan) | `trgm`; tanınmayan değer `like` + uyarı logu.
   `like` bugünkü yolun BİREBİR aynısı (gövde değişmedi; `test_memory_retrieval.py` değişmeden yeşil). Ayar şimdilik
   `app/memory/lexical.py` içinde `os.environ`'dan okunur, çünkü `config.py` bu kartın alanında değildi; **sonraki
   bir kartta `Settings.memory_lexical`'a taşınmalı** (aynı ad, `PAGENTOS_` öneki zaten uyuyor).
2. **RRF (yalnız `trgm`):** `semantic_similarity = (1/(k+r_sem) + 1/(k+r_kw)) / (2/(k+1))`, `k = 60` (Cormack-Clarke-
   Büttcher 2009'un değeri). Normalizasyon: iki listede de birinci = 1.0; kelimelerin bulmadığı anıda `1/(k+r_kw)`
   yoktur (en çok 0.5). Kelime-yalnız adayın kosinüsü ayrıca okunur ve birleşik listede kosinüsle sıralanır, böylece
   her adayın bir anlam sırası vardır (yoksa kelime-1 ile anlam-1 1/61'de berabere kalırdı). Değer kosinüsün yerine,
   `W_SEMANTIC = 0.55` ağırlığıyla girer; yenilik/güven/açık/proje ağırlıkları ve ADR-0206 yeniden sıralayıcının yeri
   değişmez (yeniden sıralayıcı `semantic` bileşenini her iki modda aynı biçimde atar).
3. **Türkçe katlama:** `fold(x) = turkish_lower(x).replace("ı", "i")` (`app/household/parse.py:turkish_lower` içe
   aktarıldı, o dosya değişmedi). SQL tarafı `translate(lower(text), 'ı', 'i')` — dev PG `en_US.utf8`'de `lower('İ')
   = lower('I') = 'i'`; eşitlik testte 7 yazımla kanıtlı. Bedel: "kır"/"kir" arama ayağında birleşir (yalnız aday
   toplar; sıra RRF'de).
4. **Durak kelimeler:** küçük sabit liste (`lexical.STOPWORDS`, ~50 kelime), yalnız `trgm` modunda.
5. **PostgreSQL kelime ayağı:** `term <% fold(text)` (pg_trgm, `word_similarity_threshold` varsayılanı 0.6) **VEYA**
   `to_tsvector('turkish', text) @@ to_tsquery('turkish', 'kök1:* | kök2:*')`; sıra = `greatest(en iyi
   word_similarity, ts_rank)`, eşitlikte id. Planın varsaydığının aksine Snowball `vermiştim`i `vermiş`e, `verdim`i
   `ver`e indirir — `vermiştim:*` `verdim`i TUTMAZ (testte kanıtlı). Bu yüzden `lexical.stem_root` küçük bir zaman+kişi
   eki listesini (miştim, dim, ecek, iyorum ...) atıp kökü önek olarak sorar (`ver:*`), kök ≥ 3 harf. Morfoloji değil,
   aday üretici; fazla yakaladığını sıralama ayıklar. Terimler yalnız harf/rakam, yine de bind parametresiyle geçer.
6. **SQLite (birim testler):** `trgm` modunda Python'da `fold(text)` içinde terim sayımı (aynı terimler, aynı kök).
7. **Göç `memory_text_trgm` (revision `0071_memory_text_trgm`, `down_revision 0070_household_stock`, birleştirmede
   ağacın başına yeniden zincirlenir):** `CREATE EXTENSION IF NOT EXISTS pg_trgm` (yoksa dürüst `RuntimeError`), GIN
   `gin_trgm_ops` dizini `translate(lower(text),'ı','i')` üzerinde, GIN `to_tsvector('turkish'::regconfig, text)`
   dizini. İki ifade sorgununkiyle karakter karakter aynı (`EXPLAIN` testi iki dizin adını görür). Geri alma iki dizini
   düşürür, eklentiyi BIRAKIR (başkası kullanıyor olabilir; boş eklentinin bedeli yok). Yalnız genişletme.

## Lisans / bağımlılık

pg_trgm (PostgreSQL contrib) ve `turkish` metin arama yapılandırması (Snowball, BSD-3 kökenli): PostgreSQL Lisansı,
`pgvector/pgvector:pg16` imajıyla gelir (pg_trgm 1.6, testte doğrulandı). Yeni Python paketi, indirme veya ağ yok.
`docs/THIRD_PARTY_COMPONENTS.md` girdisi için önerilen metin planda (PY yazar).

## Sonuçlar

- `trgm` açıkken kelime ayağı RRF'yle anlamla eşit söz hakkı alır; yalnız `trgm` modunda anlam sırası ilk olan ama
  kelimesi tutmayan anının `semantic` bileşeni 0.55'ten 0.275'e iner (RRF'nin doğası): mutlak eşik kullanan bir
  çağıran yok (grep: `components["semantic"]` yalnız testlerde ve `_rerank_head`'de).
- Varsayılan `like` olduğu için üretim davranışı bu kartla değişmez; açma kararı ve sahibin denemesi (bir söz verip
  ertesi gün "X'e ne söz vermiştim?") READY_FOR_OWNER.
- Açık soru: `trgm`'in varsayılan olması — sahip denemesinden ve yeniden sıralayıcıyla birlikte ölçümden sonra.
