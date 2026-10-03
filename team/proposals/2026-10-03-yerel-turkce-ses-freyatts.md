# Öneri: Ücretsiz yerel Türkçe ses (FreyaTTS) — anahtar olmadan da uzun okuma ve alarm karşılaması konuşsun (önce ÖLÇ)

Roadmap: sıra 6 "Voice and character — close the Turkish TTS gap"; JARVIS tablosu 1. satır (doğal konuşma,
"Turkish TTS gap" notu) ve "ucuz, hep açık katman" sınırı. Master checklist 224 (uzun okuma), 414 (artefakt
okuma), 234/267 (yedek ses) — üçü de bugün "TTS kredisi yok: gerçek ses üretimi bekliyor" diyor.

## Ne

Roadmap "Türkçe TTS açığını kapat" diyor ama NASIL kapanacağını söylemiyor; bugünkü tek yol ücretli bir
sağlayıcı (OpenAI/ElevenLabs/Azure) ve onun kredisi yok. Yeni olan şu: Temmuz 2026'da Türkçe için sıfırdan
eğitilmiş, Apache-2.0 lisanslı, ağırlıkları açık, 183 M parametrelik bir TTS çıktı (FreyaTTS-small). Öneri:
mevcut `TTSProvider` arayüzünün arkasına bir `LocalTurkishTTSProvider` (FreyaTTS) eklemek, ÖNCE yalnız
ölçmek (Cloud Core CPX32'de ve ev PC'sinde hız; senin kulağınla ses kalitesi), sonra ayrı bir kararla
`TTSRouter`'da ücretli sağlayıcının yedeği — anahtar yoksa sinüs tonu ya da sessizlik yerine Türkçe ses.
Kullanımda: "Raporu oku" dediğinde rapor, kredisi olmayan bir ayda da okunur; sabah alarmı "Günaydın efendim"
der, vızıltı çalmaz.

## Faydası — örneklerle

1. Bugün: "Dünkü araştırma raporunu oku." → anahtarsız dağıtımda ses YOK, yalnız metin gelir (feature matrix 224:
   "anahtarsız dağıtımda ses YOK, metin var").
   Bununla: rapor kendi sunucunda üretilen Türkçe sesle okunur; "dur" / "devam et" aynı imleçle çalışır.
2. Bugün: sabah alarmı karşılamayı söyleyemiyor; `greeting_failure="no_tts_key"` satıra yazılıyor ve karşılama
   sunulmuyor (267).
   Bununla: "Günaydın efendim, saat yedi" yerel sesle çalınır; ücretli anahtar dönerse o öne geçer.
3. Bugün: ücretsiz yerel kipte (ADR-0173) yanıtlar tarayıcının `speechSynthesis` sesiyle okunur — makineden
   makineye değişen, Windows'un hazır Türkçe sesi.
   Bununla: iki PC'de de aynı, Türkçe için eğitilmiş ses (ölçüm iyi çıkarsa; bu madde ayrı bir karar).

Kazanç: 224/414/267 satırları kredi beklemeden PROVEN_REAL'e gidebilir; karakter başına ücret yok (OpenAI TTS
bugün dakika başına ücretli); ses metni sunucudan çıkmaz.
Kazanmadığımız: canlı sohbet sesi (realtime, gpt-realtime-2.1) değişmez — bu yalnız okuma/anlatı/karşılama;
tek bir ses var (ses klonlama yok, seçim "seed" ile); duygu/ton kontrolü yok; kalite senin kulağına bağlı.

## Neden şimdi

- FreyaTTS teknik raporu, arXiv 2607.09530 (v1 10 Temmuz 2026, v2 22 Temmuz 2026): 183,2 M parametre, karakter
  düzeyi (92 Türkçe sembol, fonemleyici yok), 48 kHz; WER %8,0 / CER %3,0; XTTS-v2 ve F5-TTS'i parametrelerinin
  %40-55'iyle geçtiğini iddia ediyor. https://arxiv.org/abs/2607.09530
- Ağırlıklar: https://huggingface.co/freyavoice/Freya-TTS (Apache-2.0, geçen ay 5 487 indirme). Kod:
  https://github.com/freyavoiceai/FreyaTTS (Apache-2.0, 163 yıldız, 1 açık konu, 9 commit). Ses kodlayıcısı
  AudioVAE2, openbmb/VoxCPM2'den yüklenme anında indiriliyor, o da Apache-2.0.
- CPU hızı: model kartında yalnız Apple M3 için RTF 0,70 (fp32) var. **Kanıt ince:** x86 sunucu CPU'su (CPX32, 4
  vCPU) için sayı yok; genç bir proje (9 commit). Bu yüzden öneri önce ölçüm.
- Bizim tarafımızda: `services/api/app/voice/providers.py` `TTSProvider` protokolü ve `voice/router.py`
  `TTSRouter` hazır; 224/414/267 satırlarının tek eksiği gerçek ses.

## Nasıl

- Yeni sağlayıcı `LocalTurkishTTSProvider` (`TTSProvider` arkasında; CLAUDE.md'nin ses kuralı: anlatı TTS'i
  realtime'dan ayrı alt sistem — aynen kalır). Model ayrı bir süreçte (konteyner ya da companion'da yerel süreç)
  koşar; API yalnız HTTP/stdio ile konuşur, torch API imajına girmez.
- İlk kart: ölçüm betiği — aynı 20 cümle (ADR-0242'nin metinleri) CPX32'de ve ev PC'sinde; RTF, ilk ses
  gecikmesi, bellek; WAV'lar Onay Merkezi'nde dinlemen için.
- İkinci kart (ayrı onay): `TTSRouter`'da ücretli sağlayıcının arkasına yedek olarak, ayar KAPALI varsayılanla.
- Değişmeyen: realtime sohbet yolu, anlatı imleci, 234'ün yedek politikası (yalnız yedeğin ne olduğu değişir).

## Maliyet/risk

- Emek: ölçüm küçük; sağlayıcı + yedek bağlama orta.
- Çalışma maliyeti: 0 USD/ay (kendi CPU'n). CPX32'de bellek: model ~0,7 GB fp32 + torch; CPU'yu okuma süresince
  doldurabilir — Cloud Core'un API'si ile yarışır. RTF 1'in üstündeyse sunucuda değil ev PC'sinde (GPU'lu) üretilir
  ya da önceden sentezlenir (uzun belge zaten parça parça).
- Lisans: Apache-2.0 (kod + ağırlık + AudioVAE2). THIRD_PARTY kaydı entegratörün işi.
- Gizlilik (KVKK): metin dışarı çıkmaz — ücretli sağlayıcılardan daha iyi.
- Cihaz güvenliği: yalnız ses dosyası üretir; ilk yüklemede HF'den ağırlık indirir (sürüm sabitlenmeli, hash'le).
- İşveren makinesi: ofis PC'sinde koşturulmaz; yalnız ev PC'si ve Cloud Core.

## Kanıt planı

1. Ölçüm (PROVEN_AUTOMATED): 20 cümle × 2 makine, RTF/gecikme/bellek tablosu.
2. Dinleme (PROVEN_REAL, senin): Onay Merkezi'nde 5 örneği dinle — "kullanılır" / "kullanılmaz".
3. Yedek açıldıktan sonra (PROVEN_REAL): anahtarsız kipte "Son raporu oku." → kulaklıkta Türkçe ses; alarm
   karşılaması `greeting_failure` satırı yazmadan çalınır.

## Karar

Yapalım mı — önce yalnız ölçüm ve dinleme örnekleri (sağlayıcıyı bağlamak ayrı onay)?
Alternatifler: ücretli TTS kredisi almak (aylık maliyet, ses dışarı gider); Piper gibi eski yerel TTS'ler
(Türkçe sesleri zayıf, ölçülmedi); beklemek.
