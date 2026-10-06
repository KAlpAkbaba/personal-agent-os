# Hafıza gömme ölçümü: IBM Granite Embedding Multilingual R2, potion'ın yanında

Yalnız ÖLÇÜM (memory-embedding-granite-measure). Üretimde hiçbir şey değişmedi: varsayılan `minishlab/potion-multilingual-128M`, şema, indeks ve bağımlılıklar aynı. Cümleler sabit Türkçe bench cümleleri, sahibin gerçek hafıza metni değil; model yerelde koştu.

- Biçim: `ev-pc` (threads hepsi)
- Önbellek: %LOCALAPPDATA%/PagentOS/fastembed-cache (depo ve %TEMP% dışında)
- Ölçüm zamanı: 2026-10-06T18:59:51+00:00

| model | AYRIM | anlamdaş ort. | alakasız ort. | yükleme | embed ortanca | RSS |
|---|---|---|---|---|---|---|
| deterministic n-gram (ADR-0200 öncesi üretim) | +0.277 | 0.282 | 0.005 | — | — | — |
| `minishlab/potion-multilingual-128M` | +0.457 | 0.480 | 0.023 | ilk 3.32 s / ısınmış 3.16 s | 0.301 ms (bench) · cpx32-bicimi 0.26 ms · ev-pc 0.37 ms | cpx32-bicimi 1099.40 MB tepe · ev-pc 1102.10 MB tepe |
| `ibm-granite/granite-embedding-311m-multilingual-r2` (768→256, MRL) | +0.150 | 0.927 | 0.777 | ilk 10.71 s / ısınmış 11.17 s | 171.525 ms (bench) · cpx32-bicimi 45.47 ms · ev-pc 211.12 ms | cpx32-bicimi 3329.10 MB tepe · ev-pc 3333.90 MB tepe |
| `ibm-granite/granite-embedding-311m-multilingual-r2-int8` (768→256, MRL) | +0.144 | 0.922 | 0.778 | ilk 6.36 s / ısınmış 6.37 s | 87.705 ms (bench) · cpx32-bicimi 23.91 ms · ev-pc 92.42 ms | cpx32-bicimi 981.00 MB tepe · ev-pc 985.20 MB tepe |

Önceki koşu (ADR-0200 Kanıt, 2026-09-27, aynı 14 çift): `minishlab/potion-multilingual-128M` +0.457, `Qwen/Qwen3-Embedding-0.6B-Q` +0.354.

## Zor iki çift (ortak kelimesi yok)

| model | Ekranlar 15 dakika sonra kapansın ~ Monitörler çeyrek saat boşta kalınca sönsün | Sahip sabahları kahve içmeyi sever ~ Kadir her sabah bir fincan kahve içer |
|---|---|---|
| `minishlab/potion-multilingual-128M` | 0.170 | 0.739 |
| `ibm-granite/granite-embedding-311m-multilingual-r2` | 0.898 | 0.912 |
| `ibm-granite/granite-embedding-311m-multilingual-r2-int8` | 0.887 | 0.916 |

## 200 satırlık tutma partisi

- `minishlab/potion-multilingual-128M`: 0.05 s (maliyet ölçümü, cpx32-bicimi)
- `ibm-granite/granite-embedding-311m-multilingual-r2`: 9.09 s (maliyet ölçümü, cpx32-bicimi)
- `ibm-granite/granite-embedding-311m-multilingual-r2-int8`: 4.78 s (maliyet ölçümü, cpx32-bicimi)

## Sabitlenmiş dosyalar (boyut ve sha256 ilk gömmeden önce doğrulandı)

- `ibm-granite/granite-embedding-311m-multilingual-r2` @ `44399559930365213510b1ee2eb15ded83374f0e`, yükleme yolu: onnxruntime-yedek
  - `onnx/model.onnx` 1247170481 bayt sha256 `75f9f258bf5013f5fe8a4dad61dd0fd16ac0cbaa7a106e3d3f41c2d04a42d541`: doğrulandı
  - `tokenizer.json` 33384821 bayt sha256 `0087c868b33bad550a78a08d19798cfd7f713cde4f020803b8f51f405503e15f`: doğrulandı
  - `tokenizer_config.json` 1155500 bayt sha256 `7947bdf0378520e69ca412b8c4dacd1cffa8aef099f851fdd5c65aa27c6b36a0`: doğrulandı
  - `special_tokens_map.json` 694 bayt sha256 `cb9e60dcf4d8d314315cb3e761fe4c2e664fda8dbf66d7815372b2639e381182`: doğrulandı
  - `config.json` 1191 bayt sha256 `e1e3fc842a8e0537e25d6e4c93879698b92ae96722e8c162bef334b57978a3b0`: doğrulandı
- `ibm-granite/granite-embedding-311m-multilingual-r2-int8` @ `44399559930365213510b1ee2eb15ded83374f0e`, yükleme yolu: onnxruntime-yedek
  - `onnx/model_quint8_avx2.onnx` 313421909 bayt sha256 `f1fdd44e7e1ac51f12ab7957c7bd092e064d596c288513bf9d326842f669edee`: doğrulandı
  - `tokenizer.json` 33384821 bayt sha256 `0087c868b33bad550a78a08d19798cfd7f713cde4f020803b8f51f405503e15f`: doğrulandı
  - `tokenizer_config.json` 1155500 bayt sha256 `7947bdf0378520e69ca412b8c4dacd1cffa8aef099f851fdd5c65aa27c6b36a0`: doğrulandı
  - `special_tokens_map.json` 694 bayt sha256 `cb9e60dcf4d8d314315cb3e761fe4c2e664fda8dbf66d7815372b2639e381182`: doğrulandı
  - `config.json` 1191 bayt sha256 `e1e3fc842a8e0537e25d6e4c93879698b92ae96722e8c162bef334b57978a3b0`: doğrulandı

## Korpus için seçilen Granite adı (ADR-0224 katman 2)

`ibm-granite/granite-embedding-311m-multilingual-r2`: 14 çiftte en büyük FP32–INT8 farkı 0.0152 ('Yapay zeka haberlerini düzenli araştırıyorum' ~ 'AI gelişmelerini sık sık takip ederim'), eşik 0.01: eşiği aşıyor (kural: INT8, 14 çiftin hepsi FP32'ye 0.01 içindeyse; değilse FP32).

## Hüküm

**(c)** AYRIM potion'dan geniş değil: ölçüldü, alınmadı. Bu kart benimsemez: varsayılan model potion kalır, yeniden indeksleme yok.

Model başına: `ibm-granite/granite-embedding-311m-multilingual-r2` (c), `ibm-granite/granite-embedding-311m-multilingual-r2-int8` (c)
