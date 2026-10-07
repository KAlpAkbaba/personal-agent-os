# Hafıza kelime araması ölçümü: like ile trgm (memory-lexical-turkish-measure)

Durum: **MEASURED_LOCAL** — gerçek PostgreSQL'de, geçici veritabanında ölçüldü.

- Makine: `mail`, Windows-10-10.0.19045-SP0, 28 çekirdek, 48991 MB RAM
- Git: `843ccbf4c64d78ab14274bca426db094aa2f40af`
- PostgreSQL: PostgreSQL 16.15 (Debian 16.15-1.pgdg12+2) on x86_64-pc-linux-gnu, compiled by gcc (Debian 12.2.0-14+deb12u1) 12.2.0, 64-bit; pg_trgm 1.6, vector 0.8.6, göç `0075_urgent_alert_receipts`
- Geçici DB `pagentos_bench_lexical_de05f4eb`: 23780375 bayt (memories 3211264 bayt), sonunda silindi
- Set: 33 soru, korpus başına 315 anı (150 gürültü); etiketler company 7, dotted_i 8, person 18, place 12, plate 3, suffix 27, typo 4, verb 15
- Tohumlar [7, 11], tohum başına 5 yükleme (her biri yeni kimliklerle), 1 ısınma + 3 tur, modlar her turda sıra değiştirerek; yeniden sıralayıcı kapalı (üretim gibi)
- Süreç bellek tepesi: 1158.7 MB

| gömücü | tohum | mod | ilk-1 | ilk-3 | ilk-10 | MRR | p50 ms | p95 ms | max ms |
|---|---|---|---|---|---|---|---|---|---|
| deterministic | 7 | like | 87.9% | 93.9% | 100.0% | 0.919 | 6.2 | 22.4 | 73.3 |
| deterministic | 7 | trgm | 68.5% | 94.5% | 100.0% | 0.816 | 23.3 | 53.7 | 799.1 |
| deterministic | 11 | like | 87.9% | 93.9% | 100.0% | 0.919 | 5.9 | 8.4 | 13.6 |
| deterministic | 11 | trgm | 72.1% | 95.2% | 100.0% | 0.835 | 21.7 | 30.4 | 42.3 |
| local | 7 | like | 81.8% | 93.9% | 97.0% | 0.880 | 5.8 | 8.2 | 11.6 |
| local | 7 | trgm | 73.3% | 96.4% | 100.0% | 0.852 | 22.3 | 30.4 | 193.1 |
| local | 11 | like | 81.8% | 93.9% | 97.0% | 0.880 | 6.0 | 8.5 | 12.6 |
| local | 11 | trgm | 64.2% | 97.0% | 100.0% | 0.802 | 20.6 | 28.9 | 181.2 |

Oranlar her tohumun yüklemelerinin ortalaması; ilk-3 aralığı (en az-en çok yükleme):

- deterministic tohum 7: like 93.9%-93.9%, trgm 90.9%-97.0%
- deterministic tohum 11: like 93.9%-93.9%, trgm 90.9%-97.0%
- local tohum 7: like 93.9%-93.9%, trgm 93.9%-97.0%
- local tohum 11: like 93.9%-93.9%, trgm 97.0%-97.0%

## Soru başına sıra (doğru anının yeri her yüklemede; - = ilk 10'da yok)

### deterministic, tohum 7

| soru | like | trgm | trgm ilk-3 payı |
|---|---|---|---|
| ahmet-soz | 1 1 1 1 1 | 1 1 1 1 1 | 100.0% |
| izmir-ev-sahibi | 1 1 1 1 1 | 2 1 1 1 1 | 100.0% |
| mehmet-borc | 9 9 9 9 9 | 7 7 7 7 7 | 0.0% |
| eskisehir-otel | 1 1 1 1 1 | 3 3 3 3 3 | 100.0% |
| gulseren-dogum-gunu | 1 1 1 1 1 | 1 2 2 2 2 | 100.0% |
| kayseri-fabrika | 1 1 1 1 1 | 1 1 1 1 1 | 100.0% |
| ilgaz-kayak | 1 1 1 1 1 | 2 2 2 2 2 | 100.0% |
| isparta-gul | 1 1 1 1 1 | 2 1 2 1 3 | 100.0% |
| ilker-kitap | 5 5 5 5 5 | 2 2 1 1 2 | 100.0% |
| irmak-veteriner | 1 1 1 1 1 | 1 1 1 1 1 | 100.0% |
| ilicak-tekstil | 1 1 1 1 1 | 1 1 1 1 1 | 100.0% |
| plaka-34klm482 | 1 1 1 1 1 | 1 1 1 1 1 | 100.0% |
| plaka-06bnr19 | 1 1 1 1 1 | 2 2 2 1 1 | 100.0% |
| plaka-35tyz07 | 1 1 1 1 1 | 1 1 1 1 1 | 100.0% |
| demirtas-lojistik | 1 1 1 1 1 | 1 1 1 1 2 | 100.0% |
| yildizkent-yazilim | 1 1 1 1 1 | 1 2 1 1 2 | 100.0% |
| oztoprak-mobilya | 1 1 1 1 1 | 1 1 1 1 1 | 100.0% |
| karaca-insaat | 1 1 1 1 1 | 1 1 1 2 1 | 100.0% |
| selim-dugun | 2 2 2 2 2 | 1 2 1 1 2 | 100.0% |
| zeynep-ilac | 1 1 1 1 1 | 2 2 1 2 1 | 100.0% |
| burak-proje | 1 1 1 1 1 | 1 1 1 1 1 | 100.0% |
| hakan-anahtar | 1 1 1 1 1 | 1 1 1 1 1 | 100.0% |
| canan-kurs | 1 1 1 1 1 | 1 1 1 1 1 | 100.0% |
| trabzon-fidan | 1 1 1 1 1 | 1 1 1 2 1 | 100.0% |
| nevsehir-balon | 1 1 1 1 1 | 1 1 2 1 2 | 100.0% |
| antalya-dis | 1 1 1 1 1 | 1 1 1 1 1 | 100.0% |
| bodrum-tekne | 1 1 1 1 1 | 1 1 1 1 1 | 100.0% |
| sinop-kira | 1 1 1 1 1 | 1 1 1 1 1 | 100.0% |
| volkan-gitar | 2 2 2 2 2 | 5 5 1 2 6 | 40.0% |
| emine-tarif | 1 1 1 1 1 | 1 1 2 1 2 | 100.0% |
| orhan-avukat | 1 1 1 1 1 | 1 1 1 1 1 | 100.0% |
| pinar-kizi | 1 1 1 1 1 | 1 1 1 1 1 | 100.0% |
| igdir-kayisi | 1 1 1 1 1 | 2 3 2 3 4 | 80.0% |

### deterministic, tohum 11

| soru | like | trgm | trgm ilk-3 payı |
|---|---|---|---|
| ahmet-soz | 1 1 1 1 1 | 1 1 1 1 1 | 100.0% |
| izmir-ev-sahibi | 1 1 1 1 1 | 1 1 1 1 1 | 100.0% |
| mehmet-borc | 9 9 9 9 9 | 7 7 7 7 7 | 0.0% |
| eskisehir-otel | 1 1 1 1 1 | 3 3 3 3 3 | 100.0% |
| gulseren-dogum-gunu | 1 1 1 1 1 | 2 1 2 1 1 | 100.0% |
| kayseri-fabrika | 1 1 1 1 1 | 2 1 1 2 1 | 100.0% |
| ilgaz-kayak | 1 1 1 1 1 | 1 2 2 2 2 | 100.0% |
| isparta-gul | 1 1 1 1 1 | 1 1 1 2 1 | 100.0% |
| ilker-kitap | 5 5 5 5 5 | 1 2 2 1 2 | 100.0% |
| irmak-veteriner | 1 1 1 1 1 | 1 1 1 1 1 | 100.0% |
| ilicak-tekstil | 1 1 1 1 1 | 1 1 1 1 1 | 100.0% |
| plaka-34klm482 | 1 1 1 1 1 | 1 1 1 1 1 | 100.0% |
| plaka-06bnr19 | 1 1 1 1 1 | 1 2 1 1 2 | 100.0% |
| plaka-35tyz07 | 1 1 1 1 1 | 1 1 2 1 1 | 100.0% |
| demirtas-lojistik | 1 1 1 1 1 | 1 1 1 1 1 | 100.0% |
| yildizkent-yazilim | 1 1 1 1 1 | 1 1 1 2 1 | 100.0% |
| oztoprak-mobilya | 1 1 1 1 1 | 1 3 1 1 2 | 100.0% |
| karaca-insaat | 1 1 1 1 1 | 2 1 1 1 1 | 100.0% |
| selim-dugun | 2 2 2 2 2 | 2 2 2 2 2 | 100.0% |
| zeynep-ilac | 1 1 1 1 1 | 1 1 1 1 1 | 100.0% |
| burak-proje | 1 1 1 1 1 | 1 1 1 1 1 | 100.0% |
| hakan-anahtar | 1 1 1 1 1 | 1 1 1 1 1 | 100.0% |
| canan-kurs | 1 1 1 1 1 | 1 1 1 1 1 | 100.0% |
| trabzon-fidan | 1 1 1 1 1 | 1 1 1 1 1 | 100.0% |
| nevsehir-balon | 1 1 1 1 1 | 2 3 2 1 3 | 100.0% |
| antalya-dis | 1 1 1 1 1 | 1 1 1 1 1 | 100.0% |
| bodrum-tekne | 1 1 1 1 1 | 1 1 1 1 1 | 100.0% |
| sinop-kira | 1 1 1 1 1 | 1 1 1 1 1 | 100.0% |
| volkan-gitar | 2 2 2 2 2 | 4 3 1 2 4 | 60.0% |
| emine-tarif | 1 1 1 1 1 | 1 1 1 1 2 | 100.0% |
| orhan-avukat | 1 1 1 1 1 | 1 1 1 1 1 | 100.0% |
| pinar-kizi | 1 1 1 1 1 | 1 1 1 1 1 | 100.0% |
| igdir-kayisi | 1 1 1 1 1 | 4 1 1 2 3 | 80.0% |

### local, tohum 7

| soru | like | trgm | trgm ilk-3 payı |
|---|---|---|---|
| ahmet-soz | 2 2 2 2 2 | 1 1 1 2 2 | 100.0% |
| izmir-ev-sahibi | 1 1 1 1 1 | 1 1 1 1 1 | 100.0% |
| mehmet-borc | 1 1 1 1 1 | 2 2 2 2 2 | 100.0% |
| eskisehir-otel | 2 2 2 2 2 | 2 2 1 1 2 | 100.0% |
| gulseren-dogum-gunu | 1 1 1 1 1 | 1 1 1 1 1 | 100.0% |
| kayseri-fabrika | 1 1 1 1 1 | 1 1 1 1 2 | 100.0% |
| ilgaz-kayak | 1 1 1 1 1 | 2 1 1 3 1 | 100.0% |
| isparta-gul | 1 1 1 1 1 | 1 1 1 1 1 | 100.0% |
| ilker-kitap | - - - - - | 7 7 7 7 7 | 0.0% |
| irmak-veteriner | 1 1 1 1 1 | 1 1 1 1 1 | 100.0% |
| ilicak-tekstil | 3 3 3 3 3 | 1 1 1 1 1 | 100.0% |
| plaka-34klm482 | 1 1 1 1 1 | 1 1 1 1 1 | 100.0% |
| plaka-06bnr19 | 2 2 2 2 2 | 3 2 1 4 2 | 80.0% |
| plaka-35tyz07 | 1 1 1 1 1 | 1 1 1 1 1 | 100.0% |
| demirtas-lojistik | 1 1 1 1 1 | 1 1 1 1 1 | 100.0% |
| yildizkent-yazilim | 1 1 1 1 1 | 2 1 2 1 1 | 100.0% |
| oztoprak-mobilya | 1 1 1 1 1 | 2 1 1 1 1 | 100.0% |
| karaca-insaat | 1 1 1 1 1 | 2 2 1 2 1 | 100.0% |
| selim-dugun | 1 1 1 1 1 | 1 1 1 1 1 | 100.0% |
| zeynep-ilac | 1 1 1 1 1 | 1 1 1 2 1 | 100.0% |
| burak-proje | 1 1 1 1 1 | 1 1 1 1 1 | 100.0% |
| hakan-anahtar | 1 1 1 1 1 | 2 1 1 1 2 | 100.0% |
| canan-kurs | 5 5 5 5 5 | 1 1 1 1 1 | 100.0% |
| trabzon-fidan | 1 1 1 1 1 | 1 2 2 2 1 | 100.0% |
| nevsehir-balon | 1 1 1 1 1 | 2 2 2 1 2 | 100.0% |
| antalya-dis | 1 1 1 1 1 | 1 2 1 1 2 | 100.0% |
| bodrum-tekne | 1 1 1 1 1 | 1 1 1 1 1 | 100.0% |
| sinop-kira | 1 1 1 1 1 | 1 1 1 1 1 | 100.0% |
| volkan-gitar | 1 1 1 1 1 | 1 1 2 2 1 | 100.0% |
| emine-tarif | 1 1 1 1 1 | 1 1 1 1 1 | 100.0% |
| orhan-avukat | 1 1 1 1 1 | 1 1 1 1 1 | 100.0% |
| pinar-kizi | 1 1 1 1 1 | 1 1 1 1 1 | 100.0% |
| igdir-kayisi | 1 1 1 1 1 | 2 2 1 1 1 | 100.0% |

### local, tohum 11

| soru | like | trgm | trgm ilk-3 payı |
|---|---|---|---|
| ahmet-soz | 2 2 2 2 2 | 1 1 1 1 2 | 100.0% |
| izmir-ev-sahibi | 1 1 1 1 1 | 1 1 1 2 1 | 100.0% |
| mehmet-borc | 1 1 1 1 1 | 2 2 2 2 2 | 100.0% |
| eskisehir-otel | 2 2 2 2 2 | 2 1 1 2 2 | 100.0% |
| gulseren-dogum-gunu | 1 1 1 1 1 | 2 1 1 1 1 | 100.0% |
| kayseri-fabrika | 1 1 1 1 1 | 1 2 2 1 1 | 100.0% |
| ilgaz-kayak | 1 1 1 1 1 | 2 2 1 2 3 | 100.0% |
| isparta-gul | 1 1 1 1 1 | 1 1 1 1 1 | 100.0% |
| ilker-kitap | - - - - - | 7 7 7 6 7 | 0.0% |
| irmak-veteriner | 1 1 1 1 1 | 1 1 1 1 1 | 100.0% |
| ilicak-tekstil | 3 3 3 3 3 | 1 1 1 1 1 | 100.0% |
| plaka-34klm482 | 1 1 1 1 1 | 1 1 1 1 1 | 100.0% |
| plaka-06bnr19 | 2 2 2 2 2 | 3 2 3 2 2 | 100.0% |
| plaka-35tyz07 | 1 1 1 1 1 | 2 2 1 2 1 | 100.0% |
| demirtas-lojistik | 1 1 1 1 1 | 1 1 1 1 1 | 100.0% |
| yildizkent-yazilim | 1 1 1 1 1 | 2 3 2 2 2 | 100.0% |
| oztoprak-mobilya | 1 1 1 1 1 | 2 3 1 1 3 | 100.0% |
| karaca-insaat | 1 1 1 1 1 | 2 2 1 1 1 | 100.0% |
| selim-dugun | 1 1 1 1 1 | 1 2 2 2 2 | 100.0% |
| zeynep-ilac | 1 1 1 1 1 | 1 1 1 1 2 | 100.0% |
| burak-proje | 1 1 1 1 1 | 1 1 1 1 1 | 100.0% |
| hakan-anahtar | 1 1 1 1 1 | 1 1 1 1 1 | 100.0% |
| canan-kurs | 5 5 5 5 5 | 1 1 1 1 1 | 100.0% |
| trabzon-fidan | 1 1 1 1 1 | 1 2 1 2 1 | 100.0% |
| nevsehir-balon | 1 1 1 1 1 | 2 2 1 1 2 | 100.0% |
| antalya-dis | 1 1 1 1 1 | 1 3 2 2 1 | 100.0% |
| bodrum-tekne | 1 1 1 1 1 | 1 1 1 1 1 | 100.0% |
| sinop-kira | 1 1 1 1 1 | 1 1 1 1 1 | 100.0% |
| volkan-gitar | 1 1 1 1 1 | 2 3 1 2 2 | 100.0% |
| emine-tarif | 1 1 1 1 1 | 1 1 1 1 1 | 100.0% |
| orhan-avukat | 1 1 1 1 1 | 1 1 1 1 1 | 100.0% |
| pinar-kizi | 1 1 1 1 1 | 1 1 1 1 1 | 100.0% |
| igdir-kayisi | 1 1 1 1 1 | 1 1 1 2 2 | 100.0% |

## Tohuma ve yüklemeye duyarlılık (ilk-3 isabet, yükleme başına)

- deterministic/like: {'7': [31, 31, 31, 31, 31], '11': [31, 31, 31, 31, 31]}
- deterministic/trgm: {'7': [31, 31, 32, 32, 30], '11': [30, 32, 32, 32, 31]} — sıraya/kimlik çekilişine duyarlı
- local/like: {'7': [31, 31, 31, 31, 31], '11': [31, 31, 31, 31, 31]}
- local/trgm: {'7': [32, 32, 32, 31, 32], '11': [32, 32, 32, 32, 32]}

## Karar

**Varsayılan: `trgm`** — trgm, kabul edilen düşüşlerle (trgm_with_accepted_losses)

Kural: trgm varsayılan olur, ancak: (1) trgm'in ilk-3 oranı (yüklemelerin ortalaması) like'tan yüksekse; (2) herhangi bir yüklemede like'ın ilk-3'ünde olup aynı yüklemede trgm'in ilk-3'ünden düşen her soru, düştüğü yükleme oranıyla lost_in_trgm'e yazılır ve her biri için kabul gerekçesi (soru kimliğiyle ya da sınıfıyla) varsa; (3) trgm'in p95'i like'tan en çok 50 ms fazlaysa. Biri tutmazsa like kalır.

- trgm ilk-3'te daha iyi (ilk-3: like 0.939, trgm 0.967), düşen soru 1, hepsi kabul gerekçeli, p95 ek süre +21.6 ms (sınır 50 ms): varsayılan trgm.
- Okunan: local, tohumlar 7, 11, toplam 10 yükleme
- Tek tek yüklemelerin kararı: {'not_better': 1, 'trgm': 9} — tek yükleme kararı veremezdi
- trgm'de ilk-3'ten düşen (herhangi bir yüklemede): 
  - plaka-06bnr19: 1/10 yüklemede (10.0%), sınıf id_tie — id_tie: aynı metinler aynı sırayla yüklenip yalnız kimlikler değişince soru başka bir yüklemede trgm ilk-3'üne giriyor; yani düşüş eşit puanlı anıların kimlikle kırılması (retrieval.py eşitlik kuralı), trgm'in kelime bacağından değil. Takip kartı: retrieval.py kimlikten bağımsız eşitlik kuralı.
- trgm'de ilk-3'e giren: ['canan-kurs']
- p95 farkı: +21.6 ms (sınır 50 ms)
