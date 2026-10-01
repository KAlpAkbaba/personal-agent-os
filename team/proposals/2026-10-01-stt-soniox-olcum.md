# Öneri: Soniox'u Türkçe akışlı STT adayı olarak ÖLÇ (benimseme değil, ölçüm)

Tarih: 2026-10-01 · Araştırmacı · Durum: awaiting_owner
Roadmap: JARVIS tablosu "doğal konuşma" satırı (HAVE ama "Türkçe TTS/gürültü K66 kalite işi kalır"); sıra 6.
Bağlı iş: kuyruktaki `understanding-stt-corpus`.

## Ne
Soniox'un akışlı (streaming) STT'sini, mevcut STT sağlayıcı arayüzünün arkasında BİR aday olarak ekleyip, elimizdeki
STT derlemi + sahibin kendi kayıtlarıyla Chrome Web Speech ve OpenAI ile aynı ölçüte koymak. Sahibin cümlesi
değişmez; yalnız "yanlış anlama" azalır mı, ölçülür.

## Neden şimdi
- Soniox: gerçek zamanlı 0,12 USD/saat, 60+ dil (Türkçe sayfası var), diarization/dil tespiti fiyata dahil
  ([fiyat](https://soniox.com/pricing), [Türkçe](https://soniox.com/speech-to-text/turkish)). Karşılaştırma
  rakamları (Google ~0,54, Azure ~1,00) SATICININ kendi sayfasından.
- Home Assistant 2026.9 (2 Eylül 2026) kendi bulut STT'sini Soniox'a çevirip Labs'ta "aksan, gürültü, İngilizce
  dışı diller" için deniyor ([sürüm notu](https://www.home-assistant.io/blog/2026/09/02/release-20269/)) —
  bağımsız bir işaret ama Türkçeyi belirtmiyor.
- Bizde STT hatası gerçek bir ağrı: bulgu K66 (gürültü) ve STT karışıklık dosyası (`stt-confusions.json`) bu yüzden var.
- **Kanıt ince:** bağımsız Türkçe WER karşılaştırması bulamadım (aramada Whisper ince ayar modelleri çıktı; Common
  Voice 17'de ince ayarlı large-v3-turbo WER ~18,9, large-v3 ~12,8 — farklı veri, karşılaştırılamaz).
  Gizlilik/veri saklama bilgisi fiyat sayfasında YOK; okumadan güvenli saymıyorum.

## Nasıl
- Seam: `providers.py`'deki STT sağlayıcı arayüzü + `benchmark.py` (≥2 sağlayıcı kıyaslaması, M4A zaten var).
- Değişen: yeni adaptör (WebSocket), benchmark'a bir satır. Değişmeyen: router, ses hattı, varsayılan sağlayıcı.
- Önce işçi: veri saklama/eğitim politikası + DPA + lisans/şartlar okunur; olumsuzsa durulur.

## Maliyet/risk
- Efor: küçük-orta. Çalışma: ölçüm için birkaç dolar; kullanılırsa ~0,12 USD/saat. Bellek/CPU: yok (uzak API).
- KVKK: ses verisi üçüncü tarafa gider — mevcut OpenAI realtime ile aynı sınıf, ama yeni bir veri işleyen demektir;
  sahibin onayı + politika okuması şart. Yeni API anahtarı = sahibin hesabı açması gerekir.
- Sağlayıcı arayüzü arkasında kalır (CLAUDE.md kuralı), kilitlenme yok.

## Kanıt planı
PROVEN_PROXY: derlem WER/karışıklık tablosu (3 sağlayıcı yan yana). PROVEN_REAL: sahibin gerçek odada 20 cümlesi,
kör karşılaştırma. Karar ölçüme göre; benimseme ayrı onay.

## Karar
Ölçelim mi? (Hesap açma ve ses verisinin gitmesi sahibin onayını ister.) Alternatifler: (a) ölçme, Chrome Web
Speech'le sürdür; (b) yerel faster-whisper/Parakeet'i ölç (ücretsiz, gizli, ama CPX32'de yavaş).
