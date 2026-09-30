**Denetleyici raporu: narrative-collector (pilot-01), sha 8544c885, dal team/pilot-01/worker-narrative-collector**

**Tur 1: çalıştırma**
- İki test dosyası temiz durumdan koşuldu: **38 geçti, 0 kırık** (collector 17, auditor 21). `app.narrative` worktree'den import ediliyor.
- Worktree'de `.venv` yok, ana checkout'un venv'i kullanıldı. Import yolu worktree'yi gösteriyor.
- `ruff check` temiz, `ruff format --check` temiz (8 dosya).
- Alan denetimi: diff yalnızca alanın içinde (9 dosya). `app/ledger/` değişmemiş. `HANDOFF`, `DECISIONS`, `BUILD_STATE` ve `THIRD_PARTY` dosyalarına dokunulmamış.
- Tam birim paketi ve tam `quality-gate.ps1` koşmadım (NOT_RUN). Görev entegrasyon dalında değil, yeni paket hiçbir yere bağlı değil.
- Gerçek çalıştırma yok: ses veya REST tetikleyicisi bağlanmamış ve dev stack kullanılmadı. Kanıt yalnızca SQLite fixture'ları.

**Benim mutasyonlarım** (workerınkilerden farklı; her biri yedekten geri yüklendi, sha256 aynı):

| Mutasyon | Sonuç |
|---|---|
| `tell()` içinde kural metnine düşme yolu kaldırıldı | 1 kırmızı |
| `RuleNarrator` başarısızlık bloğu kapatıldı | 9 kırmızı |
| Auditor'da `all(...)` → `any(...)` (özet kelimelerinin tümü yerine biri yeter) | 2 kırmızı |
| `no_capable_device` tespiti kapatıldı | 1 kırmızı |
| Başarısızları yeniden eskiye sıralayan anahtar `event_id` ile değiştirildi | **38 geçti, yeşil kaldı** |

**Tur 2: kırma denemesi**
- **Canlı mutant (RETURN nedeni değil, kayıt):** başarısızların "en yeni ilk" sırasını hiçbir test doğrulamıyor. Görev kartı yalnızca "başarısızlar tamamlananlardan önce" diyor, iç sıra belirtilmemiş, sıralama yine de deterministik. Worker'ın raporu "en yeni ilk" diyor, bunu koruyan test yok. Sonraki görevde bir sıra testi eklenmeli.
- **Denetçinin gevşekliği (bilinen, kart böyle istiyor):** eşleşme, özet kelimelerinin metinde alt dize olarak geçmesi. Ayrı ayrı yazılmış "posta … gönderilemedi" cümlesi geçer. Sıra ve bitişiklik denetlenmiyor. Sayı kuralı da yalnızca gerçek sayaçları kabul ediyor, yani uydurulan "7" reddediliyor (denendi), ama uydurma sayı gerçek bir sayaçla çakışırsa geçer. Bu bir kusur değil, kartın "match by its summary words" ifadesinin sonucu.
- **Cihaz kaynağı:** ledger'da cihaz sütunu yok. Cihaz `detail_json["device"]`'dan okunuyor, hiçbir satır bu anahtarı bugün yazmıyor. Yani gerçek veride "ofiste" süzgeci şimdilik boş sonuç verir ve tüm satırlar `bulut` sayılır. Worker bunu ADR'de yazmış, dürüst. Bir sonraki görev yazıcıları bağlamazsa cihaz özelliği çalışmaz. **Kanıt sınıfı bu özellik için PROVEN_AUTOMATED (yalnızca fixture), gerçek veride NOT_RUN.**
- **Sözleşme kayması:** BROWSER_CAPABILITIES, DEVICE_PROTOCOL ve API şemalarına dokunulmamış. Sabitler `app.ledger.vocabulary`'den import ediliyor, `failed`/`completed` string kopyası yok. `no_capable_device` için tek bir yerel sabit var, ledger'da karşılığı yok (worker bildirmiş).
- **KVKK/gizlilik:** metin `factual_summary` ve `result` alanlarını olduğu gibi aktarıyor. Bu alanlar ledger'ın kendi gizlilik süzgecinden geçmiş varsayılıyor, doğrulamadım. Bağlama yapılırken (ses/REST) gözden geçirilmeli.
- **Bellek/CPU (CPX32):** her sayfa 200 satır, geriye sayfalama, sınırsız bir döngü yok. 230 satırlık test var. Aynı zaman damgalı 200+ satır kesilir (ADR'de belgeli).
- **Geri alma:** yeni paket hiçbir yere bağlı değil, dalı geri almak yeterli.
- **Güvenli varsayılan:** tanınmayan dönem kelimesi `ValueError`, tanınmayan cihaz kelimesi hiçbir satır seçmiyor. Yanlış pencere veya "hepsi" anlatılmıyor.

**Kanıt sınıfları:** toplayıcı, anlatıcı, denetçi ve `tell()` için PROVEN_AUTOMATED. Cihaz süzgecinin gerçek veride çalışması NOT_RUN. Hiçbir şey READY_FOR_OWNER değil.

**Birleştirme için notlar (lead):** ADR metni numarasız, `team/plans/narrative-collector-adr.md` içinde. Sıra testi eksikliği izlenmeli.

`APPROVE`
