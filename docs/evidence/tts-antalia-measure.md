# Antalia 1 ölçümü (tts-antalia-measure)

YALNIZ ÖLÇÜM: sağlayıcı bağlanmadı, ayar yok, API'ye bağımlılık eklenmedi.
Kod `20f9bfeaaefefb3ef723c292fe2bb0306e08823d`; ağırlıklar `cloud0day3/antalia-1@eaec2aad2da8c0db5fc359734470874dae82c603`, `nvidia/bigvgan_v2_24khz_100band_256x@c329ede9e9bbc100ddf5c91e2330a61921262370`.
Sentez `--network none` ile, imaj `pip install --require-hashes` ile kuruldu. Cümleler: `OWNER_SENTENCES` (yazılı, sentetik; sahibin sesi kullanılmadı).

## Karar (yalnız sayılardan)

ev-pc: gerçek zamana ulaşılamadı (havuzlanmış RTF 25,23 ≥ 1). cpx32-bicimi: gerçek zamana ulaşılamadı (havuzlanmış RTF 8,05 ≥ 1). Ses kalitesi burada değerlendirilmez; ona sahibin kulağı karar verir.

## Makineler

Etiket | tür | CPU | iş parçacığı | --cpus | yükleme ms | RTF havuz | RTF p50/p95 | ilk ses ms p50/p95 | tepe bellek MB | ok/hata | yeniden deneme
---|---|---|---|---|---|---|---|---|---|---|---
ev-pc | gerçek | Intel(R) Core(TM) i7-14700KF | 28 | tümü | 7334 | 25,231 | 19,552 / 39,695 | 67637 / 160628 | 2864 | 20/0 | 0
cpx32-bicimi | VEKİL (CPX32 biçimi, ev PC'sinde) | Intel(R) Core(TM) i7-14700KF | 4 | 4.0 | 3831 | 8,048 | 8,062 / 8,387 | 28458 / 32926 | 3240 | 20/0 | 0

- cpx32-gercek: NOT_RUN (uzak makinede çalıştırma ayrı adım)
- VEKİL satırı sunucunun kendisi değildir: ev PC'sinde `--cpus 4` ile sınırlanmış aynı konteyner. Sunucu CPU'su farklıdır.
- Model akış yapmıyor (streamed: false): ilk ses gecikmesi = tüm sentez süresi.
- Yükleme süresi ayrı ölçülür; hiçbir cümlenin süresine girmez.
- Antalia: tarif v2 (32 Euler adımı, tohum 20260803, konuşmacı voicedata-candidate-b), tek tohum, best-of-8 yok. Gürültü zarfı sabitleme (pin_noise_envelope): açık kodda yok, uygulanmadı. Model geliştirmesi durdurulmuş (düzeltme gelmeyecek). Ağırlıklar Antalia Open RAIL-M: 'Antalia 1', Sezgin Saygili, Emre Kaplaner, Oncel Ozgul, Fikri San Koktas (Patientdesk.ai), https://huggingface.co/cloud0day3/antalia-1 .
- Tepe bellek: konteyner sürecinin o ana kadarki en yüksek RSS'i (yükleme dahil).

## Dinleme örnekleri (sahip: 'kullanılır' / 'kullanılmaz')

ev-pc:
- 1: `C:\Users\alpak\AppData\Local\PagentOS\tts-measure\antalia\ev-pc\01.wav`
- 5: `C:\Users\alpak\AppData\Local\PagentOS\tts-measure\antalia\ev-pc\05.wav`
- 10: `C:\Users\alpak\AppData\Local\PagentOS\tts-measure\antalia\ev-pc\10.wav`
- 15: `C:\Users\alpak\AppData\Local\PagentOS\tts-measure\antalia\ev-pc\15.wav`
- 20: `C:\Users\alpak\AppData\Local\PagentOS\tts-measure\antalia\ev-pc\20.wav`
cpx32-bicimi:
- 1: `C:\Users\alpak\AppData\Local\PagentOS\tts-measure\antalia\cpx32-bicimi\01.wav`
- 5: `C:\Users\alpak\AppData\Local\PagentOS\tts-measure\antalia\cpx32-bicimi\05.wav`
- 10: `C:\Users\alpak\AppData\Local\PagentOS\tts-measure\antalia\cpx32-bicimi\10.wav`
- 15: `C:\Users\alpak\AppData\Local\PagentOS\tts-measure\antalia\cpx32-bicimi\15.wav`
- 20: `C:\Users\alpak\AppData\Local\PagentOS\tts-measure\antalia\cpx32-bicimi\20.wav`

Ses kalitesi burada değerlendirilmez; karar sahibin kulağınındır.
