# Hafıza kelime araması ölçümü: like ile trgm (memory-lexical-turkish-measure)

Durum: **MEASURED_LOCAL** — gerçek PostgreSQL'de, geçici veritabanında ölçüldü.

- Makine: `mail`, Windows-10-10.0.19045-SP0, 28 çekirdek, 48991 MB RAM
- Git: `93122258ebf49ffc79521d134c9988ab321e8c77` (kirli ağaç)
- PostgreSQL: PostgreSQL 16.15 (Debian 16.15-1.pgdg12+2) on x86_64-pc-linux-gnu, compiled by gcc (Debian 12.2.0-14+deb12u1) 12.2.0, 64-bit; pg_trgm 1.6, vector 0.8.6, göç `0075_urgent_alert_receipts`
- Geçici DB `pagentos_bench_lexical_1bc3daaa`: 19554831 bayt (memories 2392064 bayt), sonunda silindi
- Set: 33 soru, korpus başına 315 anı (150 gürültü); etiketler company 7, dotted_i 8, person 18, place 12, plate 3, suffix 27, typo 4, verb 15
- Tohumlar [7, 11], 1 ısınma + 5 tur, modlar her turda sıra değiştirerek; yeniden sıralayıcı kapalı (üretim gibi)
- Süreç bellek tepesi: 1169.6 MB

| gömücü | tohum | mod | ilk-1 | ilk-3 | ilk-10 | MRR | p50 ms | p95 ms | max ms |
|---|---|---|---|---|---|---|---|---|---|
| deterministic | 7 | like | 87.9% | 93.9% | 100.0% | 0.919 | 4.0 | 5.3 | 7.2 |
| deterministic | 7 | trgm | 72.7% | 97.0% | 100.0% | 0.848 | 17.3 | 25.6 | 82.9 |
| deterministic | 11 | like | 87.9% | 93.9% | 100.0% | 0.919 | 4.4 | 5.8 | 83.0 |
| deterministic | 11 | trgm | 75.8% | 97.0% | 100.0% | 0.858 | 18.2 | 25.3 | 29.7 |
| local | 7 | like | 81.8% | 93.9% | 97.0% | 0.880 | 4.2 | 5.4 | 9.0 |
| local | 7 | trgm | 69.7% | 90.9% | 100.0% | 0.812 | 16.7 | 23.6 | 32.9 |
| local | 11 | like | 81.8% | 93.9% | 97.0% | 0.880 | 5.0 | 6.9 | 103.3 |
| local | 11 | trgm | 66.7% | 93.9% | 100.0% | 0.810 | 20.6 | 29.2 | 37.2 |

## Soru başına sıra (doğru anının yeri; - = ilk 10'da yok)

### deterministic, tohum 7

| soru | like | trgm |
|---|---|---|
| ahmet-soz | 1 | 1 |
| izmir-ev-sahibi | 1 | 1 |
| mehmet-borc | 9 | 7 |
| eskisehir-otel | 1 | 3 |
| gulseren-dogum-gunu | 1 | 1 |
| kayseri-fabrika | 1 | 1 |
| ilgaz-kayak | 1 | 2 |
| isparta-gul | 1 | 2 |
| ilker-kitap | 5 | 2 |
| irmak-veteriner | 1 | 1 |
| ilicak-tekstil | 1 | 1 |
| plaka-34klm482 | 1 | 1 |
| plaka-06bnr19 | 1 | 1 |
| plaka-35tyz07 | 1 | 1 |
| demirtas-lojistik | 1 | 1 |
| yildizkent-yazilim | 1 | 2 |
| oztoprak-mobilya | 1 | 2 |
| karaca-insaat | 1 | 1 |
| selim-dugun | 2 | 2 |
| zeynep-ilac | 1 | 1 |
| burak-proje | 1 | 1 |
| hakan-anahtar | 1 | 1 |
| canan-kurs | 1 | 1 |
| trabzon-fidan | 1 | 1 |
| nevsehir-balon | 1 | 2 |
| antalya-dis | 1 | 1 |
| bodrum-tekne | 1 | 1 |
| sinop-kira | 1 | 1 |
| volkan-gitar | 2 | 1 |
| emine-tarif | 1 | 1 |
| orhan-avukat | 1 | 1 |
| pinar-kizi | 1 | 1 |
| igdir-kayisi | 1 | 1 |

### local, tohum 7

| soru | like | trgm |
|---|---|---|
| ahmet-soz | 2 | 1 |
| izmir-ev-sahibi | 1 | 1 |
| mehmet-borc | 1 | 2 |
| eskisehir-otel | 2 | 2 |
| gulseren-dogum-gunu | 1 | 1 |
| kayseri-fabrika | 1 | 1 |
| ilgaz-kayak | 1 | 4 |
| isparta-gul | 1 | 1 |
| ilker-kitap | - | 7 |
| irmak-veteriner | 1 | 1 |
| ilicak-tekstil | 3 | 1 |
| plaka-34klm482 | 1 | 1 |
| plaka-06bnr19 | 2 | 3 |
| plaka-35tyz07 | 1 | 1 |
| demirtas-lojistik | 1 | 1 |
| yildizkent-yazilim | 1 | 1 |
| oztoprak-mobilya | 1 | 4 |
| karaca-insaat | 1 | 1 |
| selim-dugun | 1 | 2 |
| zeynep-ilac | 1 | 1 |
| burak-proje | 1 | 1 |
| hakan-anahtar | 1 | 1 |
| canan-kurs | 5 | 1 |
| trabzon-fidan | 1 | 2 |
| nevsehir-balon | 1 | 2 |
| antalya-dis | 1 | 3 |
| bodrum-tekne | 1 | 1 |
| sinop-kira | 1 | 1 |
| volkan-gitar | 1 | 1 |
| emine-tarif | 1 | 1 |
| orhan-avukat | 1 | 1 |
| pinar-kizi | 1 | 1 |
| igdir-kayisi | 1 | 1 |

## Tohuma duyarlılık

- deterministic/like: ilk-3 isabet {'7': 31, '11': 31}
- deterministic/trgm: ilk-3 isabet {'7': 32, '11': 32}
- local/like: ilk-3 isabet {'7': 31, '11': 31}
- local/trgm: ilk-3 isabet {'7': 30, '11': 31}

## Karar

**Varsayılan: `like`** (not_better)

- trgm ilk-3 oranında like'tan iyi değil (ilk-3: like 0.939, trgm 0.909); like kalır.
- trgm'de ilk-3'ten düşen: ['ilgaz-kayak', 'oztoprak-mobilya']
- trgm'de ilk-3'e giren: ['canan-kurs']
- p95 farkı: +18.3 ms (sınır 50 ms)
