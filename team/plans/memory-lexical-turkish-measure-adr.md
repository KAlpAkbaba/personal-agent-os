# ADR taslağı: hafızanın kelime ayağında varsayılan `like` kalır (memory-lexical-turkish-measure)

Durum: Kabul (geri alınabilir ayar kararı). Tarih: 2026-10-07. Kart: memory-lexical-turkish-measure.
Önceki: memory-lexical-turkish-rrf (`PAGENTOS_MEMORY_LEXICAL=like|trgm`, göç 0073).

## Bağlam

memory-lexical-turkish-rrf, `trgm` modunu getirdi: Türkçe büyük/küçük harf katlama, durak kelimeleri, pg_trgm,
`turkish` metin araması ve RRF. Varsayılan olarak `like` bırakıldı. Bu kartın kuralı şuydu: trgm, sabit bir sette
ilk-3 oranında like'tan iyiyse, like'ın ilk-3'te bulduğu hiçbir soruyu kaybetmiyorsa (ya da kaybettikleri gerekçeyle
kabul edildiyse) ve p95 ek süresi en fazla 50 ms ise varsayılan trgm olur.

## Ölçüm (MEASURED_LOCAL)

Ölçüm `scripts/core/bench-memory-lexical.py` ile, set `services/api/tests/fixtures/memory_lexical/names_tr.json` üzerinde
yapıldı: 33 uydurma soru, korpus başına 315 anı (150'si gürültü). Gerçek PostgreSQL 16.15 kullanıldı (pg_trgm 1.6), geçici
`pagentos_bench_lexical_1bc3daaa` veritabanında, göç `0075` seviyesinde; veritabanı ölçüm sonunda silindi. Makine: ev PC'si
`mail`. Git taban `93122258ebf49ffc79521d134c9988ab321e8c77`; ağaç kirliydi, çünkü bench dosyaları henüz commit edilmemişti.
Her ölçümde 1 ısınma ve 5 tur koşuldu, modlar her turda sıra değiştirdi. Yeniden sıralayıcı üretimdeki gibi kapalıydı.
Ham sayılar `docs/evidence/memory-lexical-turkish-measure.json` dosyasında.

Karar, üretimin gömücüsü yerel `potion-multilingual-128M` ile ve tohum 7 sonuçlarıyla verildi:

| mod | ilk-1 | ilk-3 | ilk-10 | MRR | p95 ms |
|---|---|---|---|---|---|
| like | 81.8% | 93.9% | 97.0% | 0.880 | 5.4 |
| trgm | 69.7% | 90.9% | 100.0% | 0.812 | 23.6 |

- Tohum 11'de trgm ilk-3 oranı %93.9 çıktı; bu like'a eşit, üstün değil. Sonuç yükleme sırasına duyarlı değil: iki tohum
  arasındaki fark en fazla 1 soru.
- Deterministik gömücüde trgm ilk-3 oranı %97.0, like %93.9. Ancak bu gömücü kelime eşleşmesini kendi başına taklit
  ettiği için karara dayanak alınmadı.
- trgm'de ilk-3'ten düşenler: `ilgaz-kayak` (1→4), `oztoprak-mobilya` (1→4).
- trgm'de ilk-3'e girenler: `canan-kurs` (5→1).
- Yalnız trgm'in ilk 10'da bulduğu soru: `ilker-kitap` ("İLKER'e"). like'ta bu soru hiç bulunamıyor (`-`), trgm'de 7.
  sırada. Python'un `str.lower` fonksiyonu "İ"yi "i" + birleşik nokta yapıyor; like'ın bilinen kusuru bu.
- p95 ek süresi +18.3 ms; sınırın altında.

## Karar

`services/api/app/memory/lexical.py` içindeki varsayılan **`like` kalır** (kural: `not_better`). trgm ilk-3 oranında like'tan
iyi değil. İlk-1 oranında açıkça kötü (-12 puan). Olası açıklama (ÖLÇÜLMEDİ, sonuç listeleri bu kartta incelenmedi): RRF,
kelime sırasını anlam sırasıyla eşit ağırlıkta topluyor; adı başka bağlamda geçen "tuzak" anılar doğru anının önüne
geçebiliyor. trgm'in büyük "İ" kazancı gerçek, ama bu ilk-1 kaybını karşılamıyor.

Kod değişmedi, `test_memory_lexical.py` için alan isteği gerekmedi.

## Sonuç ve sonraki adım

- Büyük "İ" kusuru like modunda duruyor ("İLKER'e" bulunamıyor). Bunu düzeltmenin en dar yolu like ayağının sorgu
  kelimelerini `lexical.fold` ile katlaması olabilir. Bu ayrı bir karttır ve ölçümü bu betikle yeniden koşulur.
- trgm'i yeniden denemek için kelime ayağının RRF ağırlığı ya da tuzak çeldiricilere karşı sıralama ele alınabilir.
  Ölçüt aynı betik ve aynı settir.

## Geri alma

Ayar değişmedi. trgm'e geçmek için çalışma anında `PAGENTOS_MEMORY_LEXICAL=trgm` verilir ya da `lexical_mode()` içindeki
tek dize değiştirilir. Göç yok.
