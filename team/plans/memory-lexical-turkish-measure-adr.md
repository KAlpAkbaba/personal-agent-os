# ADR taslağı: hafızanın kelime ayağında varsayılan `trgm` olur, kabul edilen düşüşlerle (memory-lexical-turkish-measure)

Durum: Kabul (geri alınabilir ayar kararı), uygulandı. Tarih: 2026-10-07.
Kart: memory-lexical-turkish-measure. Önceki: memory-lexical-turkish-rrf (`PAGENTOS_MEMORY_LEXICAL=like|trgm`, göç 0073).
Bu taslak, f7845e62'nin "like kalır" taslağının ve 743e4f83'ün "çoğunluk eşiği" taslağının yerini alır.

## Bağlam

Aynı tohumla iki yükleme yapıldığında trgm'in sırası soruların bir kısmında değişiyor (denetleyici, f7845e62). Nedeni:
`hybrid_search` eşit puanları `str(memory.id)` ile ayırıyor; RRF sıralaması ve kelime adaylarının `LIMIT` sınırı da öyle.
Her yükleme yeni uuid4 kimlikler ürettiği için her yükleme ayrı bir "eşitlik çekilişi". like'ın sıraları 10 yüklemede de
birebir aynı. Bu yüzden karar tek yüklemeden değil, 2 tohum x 5 yüklemenin toplamından verilir.

## Kural (kanıttaki `decision.rule` ile aynı metin)

trgm varsayılan olur, ancak: (1) trgm'in ilk-3 oranı (yüklemelerin ortalaması) like'tan yüksekse; (2) **herhangi bir
yüklemede** like'ın ilk-3'ünde olup aynı yüklemede trgm'in ilk-3'ünden düşen her soru, düştüğü yükleme oranıyla
`lost_in_trgm`'e yazılır ve her biri için kabul gerekçesi (soru kimliğiyle ya da sınıfıyla) varsa; (3) trgm'in p95'i
like'tan en çok 50 ms fazlaysa. Biri tutmazsa like kalır. Çoğunluk eşiği yok (743e4f83'teki "yüklemelerin yarısı" kaldırıldı).

Düşüş sınıfı betikte hesaplanır (`classify_drops`): soru, düştüğü her tohumda o tohumun başka bir yüklemesinde trgm
ilk-3'ündeyse `id_tie` (metinler ve sıra aynı, yalnız kimlikler farklı: düşüşü kimlik çekilişi yapıyor); bir tohumun her
yüklemesinde düşüyorsa `consistent` (kimlikle açıklanamaz; sınıfla kabul edilmez, ancak soru kimliğiyle kabul edilebilir).

## Ölçüm (MEASURED_LOCAL)

Betik `scripts/core/bench-memory-lexical.py`, set `names_tr.json` (33 uydurma soru, korpus başına 315 anı). PostgreSQL 16.15,
geçici `pagentos_bench_lexical_de05f4eb` (göç 0075, ölçüm sonunda silindi). Makine `mail`. Git
`843ccbf4c64d78ab14274bca426db094aa2f40af`, `git_dirty: false`. Test sırası ONAY `ts-ce283ed6cf38`. Bellek tepesi 1159 MB.
Ham sayılar `docs/evidence/memory-lexical-turkish-measure.json`.

| yerel gömücü | ilk-1 | ilk-3 (ortalama) | ilk-3 en az–en çok | p95 ms |
|---|---|---|---|---|
| like, tohum 7 | 81.8% | 93.9% | 93.9–93.9 | 8.2 |
| trgm, tohum 7 | 73.3% | 96.4% | 93.9–97.0 | 30.4 |
| like, tohum 11 | 81.8% | 93.9% | 93.9–93.9 | 8.5 |
| trgm, tohum 11 | 64.2% | 97.0% | 97.0–97.0 | 28.9 |
| **toplam (10 yükleme)** | like 81.8 / trgm 68.8 | **like 93.9 / trgm 96.7** | | +21.6 ms |

Tek tek yüklemelerin kararı: 9 `trgm`, 1 `not_better`.

## Karar

Varsayılan **`trgm`, kabul edilen düşüşlerle** (`outcome: trgm_with_accepted_losses`). `lexical_mode()` artık `or "trgm"`;
bilinmeyen değerde de trgm. (1) ilk-3 like 0.939 < trgm 0.967. (3) p95 ek süre +21.6 ms <= 50 ms.
(2) Herhangi bir yüklemede düşen sorular ve kabul gerekçeleri:

- `plaka-06bnr19`: 10 yüklemenin 1'inde (%10), sınıf `id_tie`. Kabul gerekçesi: aynı metinler aynı sırayla yüklenip yalnız
  kimlikler değişince soru aynı tohumun öteki yüklemelerinde trgm ilk-3'üne giriyor (düştüğü yüklemede 4. sırada); yani
  düşüş eşit puanlı anıların kimlikle kırılması, trgm'in kelime bacağının kusuru değil. like'ta bu soru her yüklemede 2.
  sırada; trgm'de tohum 7'nin yüklemelerinde 3, 2, 1, 4, 2, tohum 11'de 3, 2, 3, 2, 2.

Önceki koşularda aynı sınıfta düşenler (aynı kod, başka çekilişler): çalışanın f4ff3209 koşusunda `plaka-06bnr19` 2/10,
`oztoprak-mobilya` 2/10, `volkan-gitar` 1/10 (ham sayılar 743e4f83'teki kanıt dosyasında: her düşüş 4. sıraya, her biri aynı tohumun
başka yüklemelerinde ilk-3'te, yani `id_tie`); denetleyicinin 743e4f83 koşusunda (raporuna göre) `ilgaz-kayak` ve
`oztoprak-mobilya`, 2/10 yüklemede. Hangi sorunun düştüğü çekilişe göre değişiyor. Takip kartı: `retrieval.py`'de kimlikten bağımsız eşitlik kuralı (örneğin önce yeni olan).

Bilinen bedel: ilk-1 81.8'den 68.8'e iniyor (~13 puan). Kartın kuralı ilk-3'e bakıyor; sesli yanıt ilk sonucu söylerse
hissedilir. Sonraki kartın ölçütü ilk-1 olmalı (RRF'de kelime ağırlığı). p95 bütçesi 315 anılık korpusta ev PC'sinde
ölçüldü, üretim boyutunda değil.

## Geri alma

`PAGENTOS_MEMORY_LEXICAL=like` eski davranışı getirir; ya da `lexical_mode()` içindeki tek dize geri alınır. Göç yok.
`test_memory_lexical.py`'nin `like` fikstürü ve `test_memory_rerank.py::test_off_is_the_same_function_float_for_float`
like'ı artık ortam değişkeniyle açıkça seçiyor.
