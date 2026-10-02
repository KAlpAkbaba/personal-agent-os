**Split raporu — idea-2026-10-02-olcum-kaydi (cycle d20261002)**

Yazılan tek dosya: `team/plans/d20261002-split-idea-2026-10-02-olcum-kaydi.json` — 3 kart. Hiçbir komut koşmadı, başka dosyaya dokunulmadı.

**Kartlar**
1. `measure-recordings-api` — yeni paket `services/api/app/voice/measurement/` (`__init__.py` yalnız bu kartta), `app/main.py`, iki birim testi, bir MinIO entegrasyon testi, ADR. Kaydı alır, 30 gün tutar, "sil" deyince siler, ölçüm aracının manifest'ini verir.
2. `measure-recording-page` (depends_on 1) — `apps/web/app/voice/measure`, `apps/web/app/lib/voice/measure`, `apps/web/tests/measure`, `voice/page.tsx` içinde tek bağlantı, ADR. Sayfa 16 kHz WAV yazar, yükler, "Tekrar"/"Sil" sunar ve Chrome'un aynı sesten yazdığını saklar.
3. `measure-compare-from-core` (depends_on 1) — `scripts/voice/stt-compare.ps1` (`-FromCore`), `stt_compare.py`, üç test dosyası, `measurement/service.py`, ADR. Chrome'un kayıt anında yazdığı cümle tabloda kendi satırı olur.

**Neden bu bölme**
- Üç ayrı dosya alanı var: API, web, betik ve ölçüm aracı. 2 ve 3, 1 birleşince paralel koşar.
- Sözleşme metni (yollar, alanlar, sınırlar) üç kartta harfi harfine aynı.
- İki yarının ayrışmaması için testler karşı tarafı okur: web testi `routes.py`'yi, API testi `load_manifest`'i, betik testi gerçek uygulamaya TestClient üzerinden bağlanır.
- 3. kartın alanına `measurement/service.py` eklendi; denetleyicinin manifest biçimi için oraya göndermesi beklenir.

**Lead olarak verdiğim kararlar (ADR'lere yazılacak)**
- **Tablo ve migration yok.** Kayıtlar mevcut `ObjectStore`'da durur. Listeleme çağrısı olmadığı için anahtarlar sayılabilir tutuldu (2 yer × 20 cümle). Yayın bu yüzden otomatik.
- **Yükleme JSON + base64.** API'de multipart ayrıştırıcı yok; yeni bağımlılık eklenmedi.
- **30 günü sunucu süreci tutar** (açılışta, 24 saatte bir ve her listelemede süpürme), oturum değil.
- **Chrome `start(audioTrack)` korumalı.** Araştırmacının "denemedim" notu karta taşındı: çalışmazsa `browser_transcript` null olur, kayıt yine yüklenir, Chrome satırı o cümle için ölçülmez. Null ile boş cümle ayrı tutulur.
- **`-FromCore` kayıtları depo dışındaki geçici klasöre indirir**, sha256 doğrular, sonunda siler. Testte gerçek motor çağrılmaz.

**Bilerek dışarıda bırakılanlar (protokol boşluğu olarak kayda geçsin)**
- **"Ölçüm kaydını başlat" ve "ölçüm kayıtlarını sil" sesli cümleleri kartlanmadı.** `intents.py` üç başka kartın, `realtime_sessions/tools.py` bir kartın alanında; eklemek bütün listeyi reddettirirdi. Sayfaya `/voice` üzerindeki bağlantıyla girilir. O alanlar boşalınca ayrı kart kesilmeli.
- **Gece otomatik ölçüm kartlanmadı.** `register-nightly.ps1` başka kartın alanında; şimdilik betiği sahibin okumasından sonra lead koşar.
- **ROADMAP "Approved ideas" satırı yazılmadı.** Bu koşu tek dosya yazabiliyor; satır lead'in bir sonraki koşusunda eklenmeli (hizmet ettiği satır: sıra 6, doğal konuşma; görevler bu üç id).
- **Onay durumu doğrulanmadı.** Öneri dosyasında hâlâ `awaiting_owner` yazıyor; bölme koşusu döngü tarafından başlatıldığı için onaylı sayıldı. Onay aynı zamanda ses kaydının 30 gün sunucuda durmasına izindir.

**Beklenen kanıt**
- PROVEN_AUTOMATED: üç kartın hepsi (1. kartta MinIO gerçek altyapıda, NOT_RUN değil).
- PROVEN_PROXY: sentezlenmiş kayıtla uçtan uca `-FromCore` raporu.
- READY_FOR_OWNER: sahip MAIL'de yirmi cümleyi okur (ofiste ikinci kez), ardından raporda en az iki motorun sayısı çıkar. İlk gerçek WER hiçbir kartta iddia edilmiyor.
