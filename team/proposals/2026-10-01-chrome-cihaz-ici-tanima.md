# Öneri: Yerel kipte Chrome'un cihaz içi Türkçe tanıması + kendi sözcüklerimize öncelik (ayar arkasında, ölçerek)

Tarih: 2026-10-01 · Araştırmacı · Durum: awaiting_owner
Roadmap: JARVIS tablosu 1. satır "doğal konuşma" (HAVE, "quality work remains") ve "ucuz, hep açık katman" sınırı;
sıra 6. Bağlı: ADR-0173 (ücretsiz yerel kip), ADR-0224 (anlama katmanları), onaylı `stt-engines-measure`.

## Ne
Ücretsiz yerel kip bugün Chrome'un konuşma tanımasını varsayılan haliyle kullanıyor
(`apps/web/app/lib/voice/localMode.ts:608`, `tr-TR`, sürekli). Chrome'da artık üç şey var ve hiçbirini kullanmıyoruz:
(1) `processLocally` — tanıma makinenin içinde yapılır, ses dışarı çıkmaz; (2) `phrases` — tanıyıcıya "şu sözcükleri
bekle" denir (0–10 arası ağırlık); (3) `quality` — "komut / dikte / sohbet" düzeyi istenir. Öneri: bunları bir ayarın
arkasında yerel kipe eklemek, hangi motorun duyduğunu tur kaydına yazmak ve aynı derlemde eskisiyle ölçmek.
Senin cümlen değişmez: "Ofis bilgisayarımda hesap makinesini aç."

## Faydası — örneklerle
1. Bugün: yerel kipte söylediğin her cümlenin sesi tanınmak için Google'a gidiyor (MDN: varsayılan tanıma sunucuda);
   şirket PC'sinde (GMKADIRAKBABA) de öyle.
   Bununla: Türkçe dil paketi bir kez iner, ses makineden çıkmaz; Cloud Core'a yalnız yazıya dönmüş cümle gider.
2. Bugün: "ofis", "bulut", "Kokpit", "hesap makinesi" gibi kendi sözcüklerimiz için tanıyıcıya hiçbir ipucu vermiyoruz;
   yanlış duyulanı SONRADAN `stt-confusions.json` ile onarıyoruz (ADR-0224 katman 1).
   Bununla: cihaz adları, uygulama adları ve yetenek listesindeki kalıp cümleler (`capabilities.ts` zaten yüklüyor)
   tanıyıcıya ÖNCEDEN verilir; derlemde "yanlış duyulan sözcük" sayısı önce/sonra ölçülür.
3. Bugün: bir cümleyi hangi motorun duyduğu kayıtta yok. Chromium'da açık bir kayıt (521896368) dil paketi kuruluysa
   `processLocally=false` iken de Chrome'un sessizce cihaz içini seçtiğini söylüyor — aynı cümle ev ve ofiste farklı
   duyulabilir, nedenini göremeyiz.
   Bununla: kip açıkça seçilir, tur kaydına `chrome-cihaz-içi` / `chrome-bulut` yazılır; ADR-0224 derlemi neyi
   ölçtüğünü bilir.

Kazanç: yerel kipte üçüncü tarafa giden ses 0; yanlış duyma oranı ölçülebilir ve (umulan) daha düşük; 0 USD.
Kazanmadığımız: ücretli gerçek zamanlı kip (OpenAI) hiç değişmez. 2026-09-30'daki "Ofisü bilgisayarında … açın"
yanlış duyması hangi kipteydi, kayıtta yazmıyor; ücretli kipteyse bu öneri o cümleye dokunmaz. Telefon: Android'de yok.

## Neden şimdi
- Cihaz içi tanıma (`processLocally`, `available()`, `install()`) Chrome 139 ile geldi (arama sonucu özeti;
  MDN hâlâ "experimental" diyor). Açıklayıcı belgede Chrome'un cihaz içi dilleri arasında `tr-TR` AÇIKÇA var:
  https://github.com/WebAudio/web-speech-api/blob/main/explainers/on-device-speech-recognition.md
- `quality` (command/dictation/conversation): "Intent to Ship", 2026-05-06, Chrome 150, yalnız masaüstü (Windows/Mac/
  Linux); Android ve ChromeOS'ta cihaz içi yok: https://groups.google.com/a/chromium.org/g/blink-dev/c/P8P-x7AnC6I
- `phrases`: https://developer.mozilla.org/en-US/docs/Web/API/SpeechRecognition/phrases (ağırlık 0.0–10.0).
- **Kanıt ince, açıkça:** Türkçe cihaz içi modelin doğruluğu hakkında hiçbir ölçüm bulamadım — buluttakinden KÖTÜ
  olabilir. `phrases`'in sunucu tanımasıyla da çalışıp çalışmadığını MDN söylemiyor. Okuduğum sorunlar: Brave'de dil
  paketi "downloading"de asılı kalıyor (brave-browser#55414); Chromebook'ta "language-not-supported". Chromium
  521896368'in yalnız başlığını okuyabildim (sayfa oturum istiyor). Bu yüzden öneri "geç" değil "ayar arkasında kur, ölç".

## Nasıl
- Seam: `localMode.ts` içindeki `recognition` portu (`browserLocalModeDeps`) — tanıyıcı zaten bir arayüzün arkasında.
- Değişen: başlamadan önce `available({langs:["tr-TR"], processLocally:true})`; "available" ise cihaz içi + `phrases`;
  "downloadable" ise kabukta tek satır "Türkçe paketi indirilsin mi?"; değilse bugünkü yol (düşüş, kayıtla). Tur
  kaydına motor adı. Ayar: kapalı / açık / yalnız ölç. Değişmeyen: yönlendirici, ADR-0224 katmanları, ücretli kip,
  tarayıcı TTS'i; yeni bağımlılık YOK, yeni hesap YOK.
- `stt-engines-measure` onaylı ve yalnız ölçüm: bu motor oraya dördüncü satır olarak ücretsiz girer.

## Maliyet/risk
Efor: küçük. Çalışma: 0 USD. CPX32: yük yok (tanıma senin PC'nde); PC'de dil paketi diski ve CPU'su — boyutunu
bulamadım, ölçülecek (C: neredeyse dolu). Lisans: tarayıcı özelliği. KVKK: iyileşme (ses üçüncü tarafa gitmez).
Şirket PC'si: paket indirme kurumsal politika ile kapalı olabilir → "unavailable" → bugünkü yola düşer; ama o yol sesi
şirket makinesinden Google'a yollar, bu bugün de böyle. Risk: Türkçe doğruluk düşerse ayar kapalı kalır.

## Kanıt planı
PROVEN_AUTOMATED: sahte tanıyıcıyla üç durum (available / downloadable / unavailable) ve düşüş; motor adı tur kaydında.
PROVEN_PROXY: ADR-0224 STT derlemi iki motorda yan yana (yanlış duyulan sözcük sayısı).
PROVEN_REAL: MAIL'de yerel kipte 20 cümlen, önce bulut sonra cihaz içi; hangisi daha az yanlış duydu, sen söylersin.

## Karar
Yapalım mı? (Ayar arkasında, varsayılan KAPALI; açmak ölçümden sonra ayrı karar.) Alternatifler: (a) yalnız ölç,
kurma — `stt-engines-measure`'a not; (b) yalnız motor adını kayda yaz (en küçük adım); (c) dokunma.
