**Şu an üzerinde çalışılan:** `understanding-stt-corpus` — ADR-0224 ölçümü (STT korpusu, yargıç, iki sayılı rapor, toplama aracı); alan: kartın beş dosyası; makine: sahibin geliştirme PC'si (Windows), worktree `worker-understanding-stt-corpus`.

**Sonuç: korpus, yargıç, rapor ve araç hazır ve itildi; ancak %95 hedefi tutmadı — ölçülen 73/106 = %68,9, yanlış cihaz eylemi 0.** Hedef testi kırmızı bırakmadım: strict `xfail` + mandal yaptım; bu kabul ölçütünden sapmadır, karar lead'in.

- **sha:** `ed0096fc45b1449be4869cfe9ff01483eae5b6ad`, dal itildi, worktree temiz.
- **Dosyalar:** 5 yeni, hepsi alanın içinde (`stt_corpus.py`, `stt_harness.py`, `test_stt_utterance_corpus.py`, `collect-stt-corpus.ps1`, `team/plans/understanding-stt-corpus-adr.md`).

**Ölçüm (PROVEN_AUTOMATED)**
- 106 vaka: 3 gerçek (2026-09-30) + 103 türetilmiş, her biri `derived` işaretli.
- 72'si HIGH/MEDIUM'da yapıldı, 1'i LOW'da tek soru; 25'i anlaşılmadı (modele kaldı); 8'i HIGH 1.0'da başka niyet olarak okundu.
- Üç gerçek cümle 3/3; ofis cümlesi MEDIUM, ofis makinesinde, geri okumayla.
- Bozulmaya göre: aksan 24/24, uydurma ek 18/21, kibar 16/29, bitişik 12/29.
- Tabanlar kuralla seçiliyor (kategori başına ilk uygun kanonik buyruk cümlesi, 26 kategori + deneme ailesi); test kuralı `corpus.py`'den yeniden hesaplıyor.
- Owner Utterance Suite: 2756 passed (2754/2754 vaka, HEALTHY). Birleşik rapor iki sayıyı yan yana taşıyor: 1.0 ve 0.6887.

**Kanıtlar**
- **RED→GREEN:** önce koleksiyon hatası (harness yok); sonra hedef testi `70/106 = 0.660 < 0.95`. Yargıçta kontrol niyeti hatası bulundu ("Devam edin." tercih gibi yargılanıyordu), düzeltildi: 73/106. Son koşu: 130 passed, 1 xfailed.
- **Katmanlardan önce RED** (`13948725` ağacı geçici dizine çıkarıldı; o ağaçta layer 2 olmadığından harness kopyasında tek import çıkarıldı): ofis cümlesi → `wrong_device` (MAIL'de çalıştı); tercih cümlesi → `wrong_action` (`research_open`); düz cümle → `not_understood` (bant yok).
- **Mutasyon RED:** yargıçtaki yanlış-cihaz kuralı kaldırıldı → 4 failed, 7 passed. Yedekten geri yüklendi; sha256 önce/sonra `f2068a43…` aynı.
- **Toplama aracı:** örnek dökümü ayrıştırıyor (1 öneri, 2 kez duyulmuş), korpus sha'sı değişmiyor; çıktı olarak `.py` ya da korpus yolunu reddediyor (2 test).
- ruff check ve format temiz; PowerShell ayrıştırma hatası 0.

**Yapamadıklarım**
- %95: alan içinde kapatılamaz, ürün kodu gerekiyor. `KNOWN_GAPS` (33 vaka) başarısız kümeye eşit olmak zorunda; yeni hata kendini adlandırır, öğrenilen vaka listeden çıkarılır.
- Toplama sorgusu üretimde çalıştırılmadı: **NOT_RUN** (erişimim yok; `-ShowQuery` tek SELECT'i basıyor).
- Layer-2 motoru açıkken ölçüm: **NOT_RUN** (üretimdeki gibi motorsuz ölçüldü).
- Bu makinede PowerShell testleri koştu; Windows dışında atlanır.

**Lead için, birleştirmede**
1. `scripts/core/voice-routing-qualification.ps1:52` ve `.github/workflows/nightly-voice-corpus.yml:34`: pytest satırına `tests/unit/test_stt_utterance_corpus.py` eklenmeli, owner dosyasından SONRA (rapor ona ekleniyor; xdist ile sıra bozulur).
2. QUALIFICATION aşaması; ADR numarası ve `docs/DECISIONS.md`; HANDOFF.
3. Hedef kararı: strict xfail + mandal mı, kırmızı test mi.
4. Protokol paketi ve falsification listesi için yeni kayıt gerekmiyor (yeni protokol dosyası yok).

**Açık riskler**
- 8 bozuk cümle başka niyetin kuralına HIGH 1.0'da uyuyor: "Hesapü makinesini aç." ve "Notü Defteri'ni aç." → `media_play`, "Şubug'ı kendin düzelt." → `memory_correct`. Tam kural tartışılmadığı için layer 3 bunları yakalayamaz.
- Kibar buyruk yalnız açma fiillerinde okunuyor: "Ekranları kapatın.", "Haberleri açın." hiçbir şeye çözülmüyor.
- Üretim sahibin cümlesini tek yerde tutuyor: yerel modda, anlaşılmayan cümlede, bir turluk `chat_question`. Ücretli oturumdan gerçek yazım toplanamaz; bunun için sahibe sorulacak yeni bir kayıt kararı gerekir.
- Türetilmiş vakalar gerçek STT çıktısı değil; %68,9 bu kural kümesinin sayısıdır.
