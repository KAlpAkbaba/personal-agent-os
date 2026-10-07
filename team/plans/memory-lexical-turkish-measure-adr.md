# ADR taslağı: hafızanın kelime ayağında varsayılan `trgm` olur (memory-lexical-turkish-measure)

Durum: Kabul (geri alınabilir ayar kararı); uygulanması alan isteğine bağlı. Tarih: 2026-10-07.
Kart: memory-lexical-turkish-measure. Önceki: memory-lexical-turkish-rrf (`PAGENTOS_MEMORY_LEXICAL=like|trgm`, göç 0073).
Bu taslak, f7845e62'nin "like kalır" taslağının yerini alır. O karar tek bir kimlik çekilişine dayanıyordu.

## Bağlam

Kartın kuralı şu: trgm, sabit sette ilk-3 oranında like'tan iyiyse, like'ın ilk-3'te bulduğu hiçbir soruyu kaybetmiyorsa
(ya da kaybettikleri gerekçeyle kabul edildiyse) ve p95 ek süresi en fazla 50 ms ise varsayılan trgm olur.

Denetleyici f7845e62'de bir sorun buldu: aynı tohumla iki yükleme yapıldığında trgm'in sırası 11 soruda değişiyor ve karar
tersine dönüyor. Bunun nedeni şu: `hybrid_search` eşit puanları `str(memory.id)` ile ayırıyor. RRF sıralaması ve kelime
adaylarının `LIMIT` sınırı da aynı şekilde çalışıyor. Her yüklemede yeni uuid4 kimlikler üretildiği için her yükleme ayrı bir
"eşitlik çekilişi" oluyor. like modunda eşitlik yok gibi: sıraları 10 yüklemede de birebir aynı çıktı.

## Ölçüm (MEASURED_LOCAL)

Betik `scripts/core/bench-memory-lexical.py`, set `names_tr.json` (33 uydurma soru, korpus başına 315 anı). Gerçek PostgreSQL
16.15 kullanıldı (pg_trgm 1.6), geçici `pagentos_bench_lexical_e8c896c3` veritabanında, göç 0075 seviyesinde; veritabanı
ölçüm sonunda silindi. Makine `mail`. Git `f4ff3209ea92eb9e78f23c577722a386fdc0cfb7`, `git_dirty: false`. Test sırasından
ONAY `ts-2ce532592b50` alındı.

Her tohum için **5 yükleme** yapıldı, her biri yeni kimliklerle (tohumlar 7 ve 11). Her yüklemede 1 ısınma ve 3 tur koşuldu.
Karar, yerel gömücünün **10 yüklemesinin toplamından** verildi. Bir soru, yüklemelerin en az yarısında ilk-3'teyse "ilk-3'te"
sayıldı. Ham sayılar `docs/evidence/memory-lexical-turkish-measure.json` dosyasında.

| yerel gömücü | ilk-1 | ilk-3 (ortalama) | ilk-3 en az–en çok | p95 ms |
|---|---|---|---|---|
| like, tohum 7 | 81.8% | 93.9% | 93.9–93.9 | 10.4 |
| trgm, tohum 7 | 75.2% | 97.0% | 97.0–97.0 | 31.9 |
| like, tohum 11 | 81.8% | 93.9% | 93.9–93.9 | 10.1 |
| trgm, tohum 11 | 70.3% | 93.9% | 90.9–97.0 | 31.6 |
| **toplam (10 yükleme)** | like 81.8 / trgm 72.7 | **like 93.9 / trgm 95.5** | | +21.5 ms |

- Tek tek yüklemelerin kararları: 6'sı `trgm`, 4'ü `not_better`. Tek bir yükleme kararı veremezdi; f7845e62'deki tek çekiliş
  `not_better` tarafına düşmüştü.
- Toplamda trgm'de ilk-3'ten düşen soru yok. Ancak bazı sorular bazı yüklemelerde düşüyor (tohum 11, trgm'in ilk-3 payı):
  `plaka-06bnr19` %60, `oztoprak-mobilya` %60, `volkan-gitar` %80. like'ta bu üç soru her yüklemede 1. sırada.
- trgm'de ilk-3'e giren: `canan-kurs`. `ilker-kitap` ("İLKER'e") like'ta hiç bulunamıyor, trgm'de bulunuyor.
- p95 ek süresi +21.5 ms; 50 ms sınırının altında, 40–60 ms sınır bandının dışında.

## Karar

Kural sağlandı (`rule: trgm`), bu yüzden `lexical.py` içindeki varsayılan **`trgm`** olur. Bunu uygulamak tek dizeyi
değiştirmekle olur: `lexical_mode()` içinde `or "like"` yerine `or "trgm"`, bilinmeyen değerde de trgm'e düşmek. Ancak bu
değişiklik memory-lexical-turkish-rrf'in 5 testini kırıyor. Bu testler like modunu ortam değişkenini silerek seçiyor
(`test_memory_lexical.py`'deki `like` fikstürü ve `test_mode_defaults_to_like`, `test_memory_rerank.py`'deki
`test_off_is_the_same_function_float_for_float`). Bu iki dosya kartın alanında değil. Bu yüzden değişiklik alan isteğiyle
bekliyor. Bekleyen kırmızı test: `test_the_default_is_what_the_measurement_decided`.

Bilinen bedeller:
- **İlk-1 oranı düşüyor:** 81.8'den 72.7'ye, yaklaşık 9 puan. Kartın kuralı ilk-3'e bakıyor. Sesli asistan ilk sonucu
  söylerse bu düşüş hissedilir. Sonraki kartın ölçütü ilk-1 olmalı (RRF'de kelime ağırlığı, tuzak çeldiriciler).
- **Eşit puanlı anıların sırası kimliğe göre belirleniyor**, yani rastgele. Bu durum trgm modunda üretimde de geçerli
  (denetleyicinin notu). Kimlikten bağımsız bir eşitlik kuralı (örneğin önce yeni olan, sonra kimlik) `retrieval.py`'de
  yapılmalı. Bu kartın alanı dışında; ayrı bir kart olarak önerilir. Ölçüm toplamı bu rastgeleliği içeriyor, gizlemiyor.

## Geri alma

`PAGENTOS_MEMORY_LEXICAL=like` verilirse eski davranış geri gelir. Ya da `lexical_mode()` içindeki tek dize geri alınır.
Göç yok.
