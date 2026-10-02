## Şu an üzerinde çalışılan
**chrome-on-device-stt** (dönen iş, cycle d20261002) — alan: `apps/web/app/lib/voice`, `apps/web/tests/voice`, `team/plans/chrome-on-device-stt-adr.md` — makine: ev PC'si, worktree `worker-chrome-on-device-stt`. Bitti; dal push edildi, ağaç temiz.

**sha:** `993abde46a6f405bb9dbbb58bd40cabeeffb3c58` (`9b4974e2` üzerine, origin ile aynı)
**Dosyalar (4, hepsi alan içinde):** `fake.ts`, `localMode.ts`, `tests/voice/local-stt-runs.test.ts` (yeni, 15 test), `team/plans/chrome-on-device-stt-adr.md`

**Dört bulgu**
1. **Gerçek alias kaynağı:** `browserLocalModeDeps(...).phraseSources` stub'lanmış `fetch` üzerinden sürülüyor. `/v1/devices` alias'ları ve yetenek cümleleri gerçek deps ile tanıyıcının `phrases` listesine ulaşıyor. Tek kaynağın düşmesi (500, 404, ağ hatası) yalnız kendi listesini boşaltıyor.
2. **`stop()` sonrası gelen `end`:** sahte tanıyıcıya `delayEnd` / `deliverEnd()` eklendi. Hâlâ başlamış koşuya hiçbir şey yazılmıyor; arada gelen geç final eski bacağın adını taşıyor.
3. **`olc` sessiz turdan sonra da dönüşüyor (ürün değişti):** konuşma üretmeyen tur bitince, yalnız `olc` ve kullanılabilir paket varken koşu `stop()` ile bitiriliyor; sonraki koşu öbür bacakta başlıyor. `kapali`, `acik` ve paketsiz `olc` için tanıyıcı durdurulmuyor (`stops === 0` ile sabitlendi).
4. **Geç yanıtlar:** probe, phrase kaynağı ve install için "stop'tan sonra" ve "yeniden başlatılan oturumun içinde" olmak üzere altı test var. Çoğunda snapshot öncesi ve sonrası aynı nesne.

**RED→GREEN:** ürün değişmeden önce 3. bulgunun üç testi RED idi (`3 failed | 12 passed`); değişiklikten sonra altı yerel/STT dosyası 103/103. 1, 2 ve 4'te ürün zaten doğruydu, RED kanıtları mutasyonla.

**Mutasyonlar** (yedek kopyadan geri yükleme, sha256 önce/sonra aynı: `localMode.ts` `ae0417a60f8c…`, `sttSetting.ts` `1768ae5adf45…`):

| Mutasyon | Sonuç |
|---|---|
| Varsayılan `acik` | RED, 6 |
| "Hayır" yine de kuruyor (rıza koruması kaldırıldı) | RED, 1 |
| `downloadable` sormadan kuruyor | RED, 3 |
| Tarayıcı kaynakları alias döndürmüyor | RED, 3 |
| Tarayıcı kaynakları yetenek cümlesi döndürmüyor | RED, 3 |
| Bir kaynağın hatası ikisini de düşürüyor | RED, 1 |
| `if (!this.running)` kaldırıldı | RED, 2 |
| Geç probe koruması: tümü / generation yarısı / active yarısı | RED, 2 / 1 / 1 |
| Geç `useDevice` koruması kaldırıldı | RED, 2 |
| Geç install koruması kaldırıldı | RED, 2 |
| `olc` sessiz tur sonrası koşuyu bitirmiyor | RED, 3 |

Mutasyonlardan sonra `fake.ts`'te tsc için bir cast ve testte `.sort()` → `.toSorted()` değişti; mutasyonlar yeniden koşulmadı, sonrasında tam paket yeşil.

**Kanıt sınıfları**
- PROVEN_AUTOMATED: `tsc --noEmit` exit 0; oxlint exit 0, yeni dosyalarda uyarı yok.
- PROVEN_AUTOMATED, bir kayıtla: tam web paketi ilk koşuda 1 failed / 2143 passed, ikinci koşuda 125 dosya 2144/2144.
- NOT_RUN: gerçek Chrome; `test_voice_local_mode.py` (sunucu dosyası değişmedi); `quality-gate.ps1`; PS paketleri.
- READY_FOR_OWNER: MAIL'de `olc` ile yirmi cümle.

**Yapamadıklarım / açık riskler**
- İlk tam koşuda düşen test `tests/pages/discoverability.test.tsx` ("the panic control … findable", 5818 ms). Tek başına 15/15 geçti; alanım dışında, nedenine bakmadım.
- `olc` sessiz turdan sonra tanıyıcıyı yeniden başlatıyor; o boşlukta başlayan cümle kesik gelebilir. Yalnız `olc`'de; ADR'nin bilinen sınırlarına yazıldı.
- Bir oturumda başlatılıp sonraki oturumda biten install'u o oturum benimsemiyor. Motor adı kendi probe'una bağlı; Chrome'un o sırada `downloading` dediği doğrulanmadı.
- Sahte Cloud Core kapandıktan sonra 410 veriyor; yeniden başlatma testleri `core.closed = null` ile açıyor (yorumda yazılı).

**Lead için (merge'de):** ADR metnine denetçinin 5. ve 6. maddeleri işlendi (`kapali` bayt bayt aynı değil: tek salt-okunur `available()`; ilk oturumda mikrofon izninden önce kurulu paket `downloadable` okunur). Sunucu satırı (`service.py`, `stt_engine`) ve `VoiceControlView` düğmesi hâlâ ADR'nin "Not done here" bölümünde.
