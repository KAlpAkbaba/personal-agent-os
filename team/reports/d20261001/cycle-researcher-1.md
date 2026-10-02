İki öneri yazdım; ikisi de sahibin onayını bekliyor (`awaiting_owner`). Kod, ROADMAP ve kuyruğa dokunmadım. Üçüncü öneri yok: kalan bulguların faydasını üç örnekle gösteremedim.

**Yazılan dosyalar**

1. `team/proposals/2026-10-01-deneme-listesi.md`
   - **Satır:** tek bir JARVIS satırı değil; "Definition of done"un PROVEN_REAL şartı (sahibin üçüncü kapısı).
   - **Öneri:** Onay Merkezi'ne "Dene" listesi. Her yayınlanmış iş için cümle, makine ve beklenen sonuç durur; sahip "Oldu / Olmadı" der.
   - **Kanıt (aynı kusur üç kez):** `adr0224-02`, `office-01` ve `cycle-2026-10-01` raporlarının "Sahibin gerçek cihazda deneyecekleri" bölümü "Yok". Aynı gün QUALIFICATION 38.3–38.5 READY_FOR_OWNER, 36.4 NOT_YET_PROVEN.
   - **Neden boş:** `scripts/lib/TeamRun.ps1:405` yalnız `awaiting_real_evidence` durumundaki işlerin `owner_trials` alanını listeliyor. Bu iki adı API'de ve web'de okuyan ya da yazan kod yok. TEAM_PROTOCOL 3a.2(c) bu listeyi vaat ediyor; ADR-0217 yalnız iki kapıyı kurdu.
   - **Kanıt gücü:** güçlü. Canlı kuyruğu okuyamadım, yalnız eski tohum dosyasına baktım.

2. `team/proposals/2026-10-01-chrome-cihaz-ici-tanima.md`
   - **Satır:** "doğal konuşma" (HAVE, kalite işi kalıyor), sıra 6. ADR-0173, ADR-0224 ve onaylı `stt-engines-measure` ile bağlı.
   - **Öneri:** yerel kipte Chrome'un cihaz içi Türkçe tanıması (`processLocally`), kendi sözcüklerimize öncelik (`phrases`) ve motor adının tur kaydına yazılması. Ayar arkasında, varsayılan kapalı.
   - **Kanıt:** açıklayıcı belgede `tr-TR` Chrome'un cihaz içi dilleri arasında. `quality` seçeneği Chrome 150'de, yalnız masaüstünde (blink-dev, 2026-05-06). `localMode.ts:608` bunların hiçbirini kullanmıyor.
   - **Kanıt gücü:** ince. Türkçe cihaz içi doğruluk için hiçbir ölçüm bulamadım. Chrome 139 bilgisi arama özetinden. Chromium 521896368'in yalnız başlığını okuyabildim.
   - **Belirsizlik:** 2026-09-30'daki "Ofisü bilgisayarında … açın" yanlış duyması hangi kipteydi, kayıtta yok. Ücretli kipteyse bu öneri o cümleye dokunmaz.

**Öneri olmayan notlar**
- **Gerçek zamanlı ses:** `gpt-realtime-2.1` (6 Temmuz 2026) zaten kullanımda (`config.py:136`); yapılacak bir şey yok.
- **`stt-engines-measure` için iki aday** (karar lead'in):
  - GPT-Realtime-Whisper, akışlı STT (Mayıs 2026); Türkçe desteğini doğrulamadım.
  - `mihuai/turkish-stt`: 66M parametre, sherpa-onnx, CPU; FLEURS-TR WER %13,90 (Whisper small %15,12). Rakamlar yazarın kendi sayfasından; lisansı özel ("mihu-community-license") ve okumadım.
- **FreyaTTS-small** (Apache-2.0, 183M, Temmuz 2026): Türkçe öncelikli TTS. M3 CPU'da RTF 0,70, torch istiyor, akış yok, tek konuşmacı. Gerçek zamanlı sese uymaz; anlatım için aday olabilir.
- **fastembed:** kapanan GCS kovası npm paketini etkiliyor; bizdeki Python 0.8.1. Python tarafının indirme yolunu ayrıca doğrulamadım.
- **Playwright 1.63** (5 Eylül 2026) çıkmış; bizim pin ile karşılaştırmadım.
- **Docker Desktop kapalıyken kapı** iki kez kırmızı döndü (bootstrap 29/33, pilot-02 30/34). `quality-gate.ps1`'de ön kontrol görmedim. `cycle-auto-integrate` kapıyı insansız koşacağı için lead'e not.
- **"Alan dışı dosya"** dersi `lead.md`'de zaten yazılı; tekrarlamadım.

**Eksikler**
- Canlı kuyruğu (`/v1/team/queue`) okuyamadım; komut izni reddedildi.
- `d20261001` rapor klasörü boştu.
- FreyaTTS'in sorun takipçisinde yalnız sayıyı gördüm (1 açık).
- Google Drive ve artlist bağlayıcıları yetkilendirilmemiş; bu koşuda gerekmedi.
