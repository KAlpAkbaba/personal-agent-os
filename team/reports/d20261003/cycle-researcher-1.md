Araştırmacı koşusu tamamlandı (döngü d20261003). İki öneri yazdım; ikisi de Onay Merkezi'nde "fikir" olarak senin kararını bekliyor.

**Yazılan dosyalar**
1. `team/proposals/2026-10-03-yerel-turkce-ses-freyatts.md`
   - **Hizmet ettiği satır:** sıra 6, "Türkçe TTS açığını kapat"; master checklist 224 / 414 / 267.
   - **Ne:** FreyaTTS-small, Türkçe için eğitilmiş, 183 milyon parametrelik bir seslendirme modeli. Kod ve ağırlıklar Apache-2.0 lisanslı (arXiv 2607.09530, Temmuz 2026; HF `freyavoice/Freya-TTS`). Var olan `TTSProvider` arayüzünün arkasına bağlanır.
   - **Önce yalnız ölçüm:** Cloud Core CPX32'de ve ev PC'sinde hız ölçülür, sen de birkaç örneği dinlersin. Yedek olarak bağlamak ayrı bir onay.
   - **Kazanç:** Ücretli seslendirme anahtarı olmadan da uzun okuma ve alarm karşılaması konuşur. Bugün anahtarsız dağıtımda ses hiç yok.
   - **Kanıt ince:** Proje genç (163 yıldız, 9 commit). İşlemci hızı yalnız Apple M3 için ölçülmüş (RTF 0,70, yani gerçek zamandan hızlı); sunucu işlemcisi için sayı yok. Bu yüzden önce ölçüm öneriyorum.
2. `team/proposals/2026-10-03-ikinci-donus-yeni-bulgu.md` (son döngülerden çıkan ders)
   - **Ne:** Denetleyici işi ikinci kez geri verdiğinde iş bugün her durumda duruyor. Öneri: denetleyici raporuna `onceki_bulgular: kapandi | acik [...]` satırını yazsın. Önceki bulgular kapandıysa iş bir kez daha çalışana döner; düzeltilmemiş bulgu tekrar ederse ya da üçüncü dönüşse eskisi gibi durur. Koşu tavanı 8 olarak kalır.
   - **Kanıt (d20261002 raporu):** `cycle-auto-integrate` (lead 00:05) ve `understanding-rules-read-lemmas` (lead 01:25). İkisinde de lead "durmanın tek nedeni ikinci dönüş kuralıydı" deyip işi elle yeniden açtı. `local-embedder-lru-lock` da yalnız bir test maddesiyle bekliyor, ama durma nedeni raporda yazılı değil; bunu zayıf kanıt olarak not ettim.
   - Aynı ilke ADR-0253'te alan dışı dosya için zaten uygulandı.

**Öneri olmayan notlar**
- **BuzzASR/turkish:** Whisper large-v3 tabanlı, MIT lisanslı (HF `BuzzASR/turkish`, 22–24 Eylül 2026; iki kaynak farklı tarih veriyor). Türkçe hata oranları kartta yok, bilgiyi yalnız bir ayna siteden okudum. Onaylı STT ölçümüne (`stt-engines-measure`) bir aday olarak eklenebilir; yeni öneri gerekmiyor.
- **gpt-realtime-2.1:** Temmuz 2026'da çıktı ve kodda zaten kullanılıyor (`config.py:136`). Eylül'de yeni bir realtime modeli çıkmadı.
- **Qwen3-ASR:** Ocak 2026'dan, yani yeni değil. Gerekirse STT ölçümünde aday olabilir.

**Roadmap'te zaten olanlar** (öneri yazmadım)
- Türkçe STT ölçümü ve ölçüm kaydı sayfası ("Approved ideas").
- Koruyucu testler, alan dışı genişletme, deneme listesi.

**Not:** Cloud Core'daki kuyruğu okumadım (yalnız eski `team/queue.json` tohumunu gördüm). İki önerinin kuyrukta kart olarak bulunmadığını ROADMAP, HANDOFF ve rapordaki kart adlarından kontrol ettim.
