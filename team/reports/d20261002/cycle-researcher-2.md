# Araştırmacı raporu — döngü d20261002 (ikinci koşu)

İki öneri yazdım; ikisi de sahibin onayını bekliyor (`awaiting_owner`). Kod, ROADMAP ve kuyruğa dokunmadım. Üçüncü öneri yok: dış taramada roadmap'te bir satırı ilerleten yeni bir şey çıkmadı.

## Yazılan dosyalar

1. `team/proposals/2026-10-02-koruyucu-testler-is-dalinda.md`
   - **Satır:** "Repairs and improves itself" ve ekip döngüsü. Ders türünden.
   - **Öneri:** bütün uygulamayı okuyan altı hızlı testi döngü betiği, çalışan bitince ve denetleyici başlamadan, işin kendi dalında koşar (ajan yok, 0 USD). Kırmızı satır işin kartına yazılır; çözülmeden tam kapı başlamaz.
   - **Kanıt (aynı kusur üç kez):** QUALIFICATION 38.10 (çıplak `.Count`), `2c691585` (`team-feed.tests.ps1`'i hiçbir şey koşmuyordu), `b35c6ddb` (`unexpected` sınıfının Türkçe cümlesi yoktu). 1-2 Ekim'deki yedi entegrasyon kapısının üçü ilk koşuda kırmızıydı.
   - **Neden oluyor:** son iki döngüde en az 14 rapor "full unit suite: NOT_RUN" diyor; yük altında 10 dakikada %7 ilerliyor.
   - **Sınırı:** başka ailenin davranış testini bozan değişikliği (38.9) yakalamaz. İş başına süreyi ölçmedim.
   - **Not:** TEAM_PROTOCOL'e madde eklenir, bu yüzden onay gerekiyor.

2. `team/proposals/2026-10-02-olcum-kaydi.md`
   - **Satır:** "doğal konuşma", sıra 6; onaylı `stt-engines-measure` fikrinin eksik yarısı.
   - **Öneri:** web kabuğunda yirmi cümleyi bir kez okuduğu bir sayfa. Sayfa WAV'ı ve etiket dosyasını kendisi yazar, Cloud Core'a yükler, 30 gün sonra siler. Aynı ses Chrome'un tanıyıcısına da verilir.
   - **Kanıt:** ADR-0242: araç teslim edildi, sıfır kayıt var, sayı yok. Windows Ses Kaydedici `.m4a` yazıyor, araç yalnız WAV alıyor; ADR bunu "open follow-up" bırakmış.
   - **Kanıt ince:** Chrome 135+ `SpeechRecognition.start(audioTrack)` bilgisini yalnız uyumluluk tablosundan okudum; `tr-TR` ile denemedim. Çalışmazsa Chrome satırı NOT_RUN kalır, önerinin geri kalanı durur.
   - **Sahibe sorulan yeni şey:** yirmi cümlelik ses kaydının kendi sunucusunda 30 gün durması.

## Öneri olmayan notlar

- **Lead'e, paylaşılan dev veritabanı (iki kez oldu):**
  - `d20261001/understanding-corrections-memory-inspector-2`: denetleyici ortak `pagentos` veritabanını yanlışlıkla 0063'ten 0064'e yükseltti, sonra geri aldı.
  - `d20261002/cycle-auto-integrate-inspector-3`: başka bir koşu ortak veritabanını sıfırlarken denetim sorgusu düştü ve tam kapı NOT_RUN kaldı.
  - Koşu başına ayrı veritabanı bunu çözer. Öneri yapmadım: aynı raporun "Open decision 1"i zaten lead'in masasında, `cycle-auto-integrate` kartının parçası.
- **pytest-testmon:** MIT, son sürüm 2.2.0 (Aralık 2024), 33 açık kayıt. Önermiyorum: koruyucularımız kaynağı metin olarak okuyor, araç bunu izleyemez.
- **Gerçek zamanlı ses:** `gpt-realtime-2.1`'den (6 Temmuz 2026) sonra yeni model yok.
- **Türkçe STT:** Eylül'de Türkçeye özel yeni model bulamadım.
- **Playwright:** 1.63 (5 Eylül 2026) hâlâ son sürüm; 1.64 çıkmamış.
- **browser-use:** aramam sürüm bilgisi döndürmedi; bakılmadı sayılır.

## Eksikler

- Canlı kuyruğu (`/v1/team/queue`) okumadım. "Kuyrukta var mı" denetimini döngü raporundaki kart adlarından, HANDOFF'tan ve ROADMAP'ten yaptım.
- Home Assistant, Temporal, Mem0 ve fastembed sürümlerine bu koşuda bakmadım.
- Google Drive ve artlist bağlayıcıları yetkilendirilmemiş (claude.ai bağlayıcı ayarlarından açılır); bu koşuda gerekmedi.

## Kaynaklar

- [pytest-testmon sürümleri](https://github.com/tarpas/pytest-testmon/releases) · [açık kayıtlar](https://github.com/tarpas/pytest-testmon/issues)
- [caniuse: SpeechRecognition.start audioTrack](https://caniuse.com/mdn-api_speechrecognition_start_audiotrack) · [MDN: start()](https://developer.mozilla.org/en-US/docs/Web/API/SpeechRecognition/start)
- [OpenAI: gpt-realtime-2.1](https://community.openai.com/t/new-realtime-models-on-the-api-gpt-realtime-2-1-and-gpt-realtime-2-1-mini/1385896)
- [Playwright (PyPI)](https://pypi.org/project/playwright/)
