# Yerel Türkçe ses adayları yan yana (FreyaTTS, Antalia 1)

YALNIZ ÖLÇÜM: hiçbir motor bağlanmadı. Aynı yirmi cümle (`OWNER_SENTENCES`), aynı konteyner kuralları (`--network none`, salt okunur, uid 10001, 8 GB tavan, takas yok).

| motor | etiket | tür | RTF havuz | RTF p95 | ilk ses p95 ms | tepe bellek MB | yükleme ms | ok/hata |
|---|---|---|---|---|---|---|---|---|
| freya | ev-pc | - | ölçülmedi | ölçülmedi | ölçülmedi | ölçülmedi | ölçülmedi | ölçülmedi |
| freya | cpx32-bicimi | - | ölçülmedi | ölçülmedi | ölçülmedi | ölçülmedi | ölçülmedi | ölçülmedi |
| antalia | ev-pc | gerçek | 25,231 | 39,695 | 160628 | 2864 | 7334 | 20/0 |
| antalia | cpx32-bicimi | VEKİL (CPX32) | 8,048 | 8,387 | 32926 | 3240 | 3831 | 20/0 |

## Karar (yalnız sayılardan)

freya: ölçülmedi (kanıt dosyası yok). antalia / ev-pc: gerçek zamana ulaşılamadı (havuzlanmış RTF 25,23 ≥ 1). antalia / cpx32-bicimi: gerçek zamana ulaşılamadı (havuzlanmış RTF 8,05 ≥ 1). Bellek karşılaştırması: iki motor birden ölçülmedi. Ses kalitesi burada değerlendirilmez; ona sahibin kulağı karar verir.

- cpx32-gercek: NOT_RUN (uzak makinede çalıştırma ayrı adım)
- CER: ölçülmedi (WAV'lar üzerinde bir STT geçişi gerekir; sonraki kart).
- VEKİL satırı sunucunun kendisi değildir: ev PC'sinde `--cpus 4` ile sınırlanmış aynı konteyner.
- İki model de akış yapmıyor: ilk ses gecikmesi = tüm sentez süresi.

## Dinleme örnekleri (sahip: motor başına 'kullanılır' / 'kullanılmaz')

freya: ölçülmedi
antalia (ev-pc):
- 1: `C:\Users\alpak\AppData\Local\PagentOS\tts-measure\antalia\ev-pc\01.wav`
- 5: `C:\Users\alpak\AppData\Local\PagentOS\tts-measure\antalia\ev-pc\05.wav`
- 10: `C:\Users\alpak\AppData\Local\PagentOS\tts-measure\antalia\ev-pc\10.wav`
- 15: `C:\Users\alpak\AppData\Local\PagentOS\tts-measure\antalia\ev-pc\15.wav`
- 20: `C:\Users\alpak\AppData\Local\PagentOS\tts-measure\antalia\ev-pc\20.wav`

Ses kalitesi burada değerlendirilmez; karar sahibin kulağınındır.
