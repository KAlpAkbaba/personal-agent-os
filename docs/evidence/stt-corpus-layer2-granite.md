# STT corpus, layer 2 as production configures it

Generated 2026-10-06T19:12:19.001736Z at 3d5f6df961eca39dd65b1947c29e56fb42f60c97. Engine: local-ibm-granite/granite-embedding-311m-multilingual-r2@256 (local, 1502 exemplars, index built in 381915.0 ms).

Neden bu Granite adı (memory-embedding-granite-measure, kart kuralı: INT8 yalnız 14 bench çiftinin hepsi FP32'ye 0.01 içindeyse, değilse FP32): en büyük FP32–INT8 farkı 0.0152 > 0.01, yani FP32 (`docs/evidence/memory-embedding-granite-measure.md`, "Korpus için seçilen Granite adı"). Potion karşılaştırması `docs/evidence/stt-corpus-layer2-remeasure.md` (bu kart yeniden yazmadı).

- without the engine: **104 / 106 = 98.1 %**
- with the engine:    **104 / 106 = 98.1 %**
- target 95 %: **met**
- layer 2 made 0 case(s) worse and 0 better
- wrong-device actions with the engine: 0 over 11 observable cases
- confident wrong readings: 0 -> 2

## Made worse

- none

## Made better

- none

## Every moved case

| case | from | to | layer before | layer after | meant | top candidate after |
|---|---|---|---|---|---|---|
| stt.derived.am.1.fused | not_understood | wrong_reading | none | semantic | ambient_policy_set | ambient_explain 0.63 |
| stt.derived.mc.search.1.fused | not_understood | wrong_reading | none | semantic | mail_search | mail_search 0.69 |

## By layer (with the engine / without)

| layer | with: correct / total | without: correct / total |
|---|---|---|
| none | 1 / 1 | 2 / 4 |
| normalize | 2 / 2 | 2 / 2 |
| rule | 98 / 98 | 100 / 100 |
| semantic | 3 / 5 | 0 / 0 |

## By distortion (with the engine / without)

| distortion | with | without |
|---|---|---|
| polite | 29 / 29 | 29 / 29 |
| diacritics | 24 / 24 | 24 / 24 |
| fused | 27 / 29 | 27 / 29 |
| invented_suffix | 21 / 21 | 21 / 21 |

## The 1 largest failure classes (of 1), with the engine

| verdict | distortion | layer | count | expected -> resolved | cases |
|---|---|---|---|---|---|
| wrong_reading | fused | semantic | 2 | ambient_policy_set -> none x1, mail_search -> none x1 | stt.derived.am.1.fused, stt.derived.mc.search.1.fused |

## Every failure with the engine

| case | verdict | layer | band | meant | resolved | top candidate | rendering |
|---|---|---|---|---|---|---|---|
| stt.derived.am.1.fused | wrong_reading | semantic | medium | ambient_policy_set | none | ambient_explain 0.63 | Uyurkenekranları kapat. |
| stt.derived.mc.search.1.fused | wrong_reading | semantic | medium | mail_search | none | mail_search 0.69 | Faturamaillerini bul. |

Repeat run: NOT_RUN
