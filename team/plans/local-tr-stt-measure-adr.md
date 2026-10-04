# ADR (taslak, numarayı Proje Yöneticisi verir): cihazda Türkçe tanıyıcı - YALNIZ ÖLÇÜM

Kart: `local-tr-stt-measure` (d20261004). Öneri: `team/proposals/2026-10-04-cihazda-turkce-tanima.md`.
Entegrasyon planı: `team/plans/local-tr-stt-measure-integration.md`.

## Karar

sherpa-onnx + `duxx/turkish-stt-zipformer` (akışlı Zipformer, int8) **yalnız `stt-compare`'de bir satır** olarak
ölçülür (sahip onayı 2026-10-04: YALNIZ ÖLÇÜM). Companion'a, uyandırma motoruna, realtime yola, Chrome tanımasına
dokunulmadı; companion'a geçiş **ayrı karar**dır ve ölçüm sonucuyla sahibe sorulur.

- `services/api/app/voice/providers_sherpa.py`: `SherpaOnnxSTTProvider` (`STTProvider` arayüzü, faster-whisper kalıbı).
  `status()` sırayla paket (`find_spec`) -> dört dosya var mı -> sha256; ilk eksik olanı Türkçe nedenle söyler.
  `available()`/`status()` tanıyıcıyı KURMAZ (dosyaları okuyup hash'ler; hash (yol, boyut, mtime) ile önbellekli).
  Dosya eksik ya da hash tutmuyorsa `transcribe` yüklemeden `OPTIONAL_DEPENDENCY_MISSING` atar
  (`details.reason` = `model missing` / `model hash mismatch`); yeni hata sınıfı açılmadı. WAV yalnız 16 kHz mono
  PCM16 (değilse `VALIDATION_ERROR`, yeniden örnekleme yok). 0,5 s bloklar + 1 s sessizlik + `input_finished()`.
- `download_model()` tek ağ yolu: sabit commit URL'si -> `<ad>.part` -> sha256 -> `os.replace`; tutmazsa `.part`
  silinir, yerine konmaz. Yalnız elle çağrılır; test ve ölçüm ağa çıkmaz.
- `stt_compare.py`: `sherpa-onnx:tr-zipformer-int8` satırı faster-whisper'dan hemen sonra; kurulu değil /
  model eksik / hash tutmuyor üç ayrı neden ve Türkçe karşılığı. Rapor şeması **1.2**: her satırda
  `real_time_factor` (Σ işlem süresi / Σ ses süresi, yalnız dönen dosyalar; ölçülemezse `null`, asla 0),
  `peak_memory_bytes` (yalnız `local=True` satırlarda, SÜREÇ tepe değeri - tüm motorlar tek süreçte; Windows
  `K32GetProcessMemoryInfo` argtypes/restype ile, Linux `ru_maxrss`; okunamazsa `null`), `commands_ran` ve
  `intent_changes_commands`. Özete satır başına bir ek satır.
- Cümleler: `OWNER_SENTENCES` (20) DEĞİŞMEDİ; `OFFLINE_COMMAND_SENTENCES` (10) eklendi, `MEASUREMENT_SENTENCES` =
  20 + 10 tek kaynak. Şablon, `measurement/service.py` (numara 1-30, 60 yuva, manifest) ve `routes.py`
  (`sentences` 30) onu kullanır; 1-20 aynı cümle, eski kayıtlar geçerli. Web kodu değişmedi (cümleleri API'den okur).
- Komut cümleleri (companion `offline_commands`: alarm.stop, alarm.snooze, listening.off, time.tell):
  "Alarmı kapat." "Alarmı ertele." "Saat kaç?" "Dinlemeyi kapat." "Mikrofonu kapat." (şablon) +
  "Alarmı kapatır mısın?" "Beş dakika daha ertele." "Şu an saat kaç acaba?" "Artık dinleme." "Alarmı sustur
  lütfen." (doğal söyleyiş). Ölçüldü: 7'si sunucu `resolve_intent`'te niyete çözülüyor (alarm_stop/alarm_snooze/
  clock_query); üç dinleme cümlesi `none` - onlarda niyet değişimi yalnız yanlış duyma onları bir EYLEME
  çevirirse sayılır (sınır, raporda bilinmeli).

## Sabitler (Entegratör, 2026-10-04 13:20 UTC HF API'den okundu, indirilerek doğrulandı)

Kaynak: https://huggingface.co/duxx/turkish-stt-zipformer - commit `cebec199e0a9a1bdcd195b9124922b4a33e98bc8`.
Dizin: `%LOCALAPPDATA%\PagentOS\models\duxx-turkish-stt-zipformer-cebec199-int8` (ya da `PAGENTOS_SHERPA_MODEL_DIR`).

| dosya | bayt | sha256 |
|---|---:|---|
| encoder-epoch-1-avg-1-chunk-32-left-128.int8.onnx | 26599220 | f5a4b439cf0dd11d01774a97fd580c20ad944756c7e44f539cb6a46165013432 |
| decoder-epoch-1-avg-1-chunk-32-left-128.int8.onnx | 540688 | 28f4caee9c57dc22afc0967b7e81994b85c0b67f9980f1df0bb5648a7554b18b |
| joiner-epoch-1-avg-1-chunk-32-left-128.int8.onnx | 259417 | c1f4ebed5cbbed2fceefabca8c2657f157bcf2deead3ad067c8b2dc1d2c3bfb1 |
| tokens.txt | 5182 | 1885ade359d5939dde820f0f0a1b31f43dad6e5bf8362ad66983fb78d24d65b6 |

Paket: `sherpa-onnx==1.13.8` (Apache-2.0; `sherpa-onnx-core==1.13.8` çeker). pyproject.toml / uv.lock DEĞİŞMEDİ.

## Lisans ve cihaz güvenliği

- sherpa-onnx: Apache-2.0, çalışırken ağ yok. Model: **CC BY-NC 4.0** (ticari olmayan; eğitim verisi WorldSpeech
  CC BY-NC). Tek sahipli kişisel sistem için uygun; **ürün dağıtılır ya da ticarileşirse model gider.** Tek yazar,
  ~2 aylık, bağımsız ölçüm yok - ölçümün amacı bu.
- Kullanıcı dizinine kurulum, yönetici izni yok, sürücü yok. ONNX dosyası hash'le doğrulanmadan yüklenmez.
  Ses makineden çıkmaz.

## THIRD_PARTY_COMPONENTS.md satırı (Proje Yöneticisi yazar)

```
## sherpa-onnx + Turkish streaming Zipformer (measurement only, local-tr-stt-measure)

Role: on-device Turkish STT candidate, MEASURED in `stt-compare` only (owner decision 2026-10-04); not wired
into the companion. Optional, hand-installed, not in pyproject/uv.lock: `pip install sherpa-onnx==1.13.8`
(Apache-2.0; pulls sherpa-onnx-core 1.13.8). Model `duxx/turkish-stt-zipformer` @ cebec199e0a9a1bdcd195b9124922b4a33e98bc8,
int8 encoder/decoder/joiner + tokens.txt, sha256-pinned in `services/api/app/voice/providers_sherpa.py`, stored
under %LOCALAPPDATA%\PagentOS\models (never in the repo). Model licence CC BY-NC 4.0 (non-commercial; training
data includes WorldSpeech CC BY-NC): acceptable for this single-owner personal system; must be removed if the
product is ever distributed or used commercially. Offline inference, no network at run time.
```

## Kurulum ve ölçüm (kullanıcı dizinine, yönetici izni yok)

```
cd services/api
uv pip install "sherpa-onnx==1.13.8"
uv run --no-sync python -c "from app.voice.providers_sherpa import download_model; print(download_model())"
uv run --no-sync python -m app.voice.stt_compare --folder <kayıtlar> --out docs/evidence/stt-compare-<tarih>.json
```
`uv sync` elle kurulan paketi kaldırır: ölçümden önce `--no-sync` kullan ya da yeniden kur. Sahibin gerçek ölçümü
(/voice/measure'da 20 + 10 cümle, ev PC'si; ofis ancak izinle) Onay Merkezi'ne yazılır.

## Geri alma

Kod: commit'ler geri alınır (veri göçü yok; 1-20 aynı). Cihaz: `uv pip uninstall sherpa-onnx sherpa-onnx-core` +
model dizinini sil.
