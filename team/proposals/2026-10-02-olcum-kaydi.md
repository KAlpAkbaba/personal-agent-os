# Öneri: Ölçüm kaydı — yirmi cümleyi /voice sayfasında bir kez oku, STT ölçümünün ölçecek bir şeyi olsun

Tarih: 2026-10-02 · Araştırmacı · Durum: awaiting_owner
Roadmap: JARVIS tablosu 1. satır "doğal konuşma" (HAVE, "quality work remains"); sıra 6. Onayladığın
`stt-engines-measure` fikrinin (Approved ideas, 2026-10-01) eksik yarısı: araç hazır, ölçülecek kayıt YOK (ADR-0242).
Yeni olan ve sana sorulan: yirmi cümlelik SES kaydının kendi sunucunda 30 gün saklanması.

## Ne
Web kabuğunda küçük bir sayfa: ekranda sırayla yirmi cümle çıkar ("Ofis bilgisayarımda hesap makinesini aç" …),
her birini bir kez okursun; sayfa sesi gerçek ses yolunun kullandığı aynı mikrofondan alır, ölçüm aracının istediği
biçimde (WAV, 16 kHz, tek kanal) kendi Cloud Core'una yükler ve yanına etiket dosyasını (hangi cümle, hangi makine)
kendisi yazar. Kayıt sürerken aynı ses Chrome'un tanıyıcısına da verilir ve onun yazdığı cümle de saklanır.
`stt-compare.ps1` kayıtları oradan alır, gece raporu sayıları yazar. Kayıtlar 30 gün sonra silinir; "ölçüm
kayıtlarını sil" dersen hemen. Senin cümlen: "Ölçüm kaydını başlat." Süre: yaklaşık beş dakika, bir kez (ofiste
ikinci kez okursan iki mikrofon karşılaştırılır).

## Faydası — örneklerle
1. Bugün: onayladığın STT ölçümü "araç teslim edildi, sayı yok" durumunda. `stt-compare.ps1 -Folder …` şablon dosyayı
   yazıp duruyor; depoda ve bu makinede senin sesinden sıfır kayıt var (ADR-0242: "nothing to measure yet").
   Bununla: beş dakikalık okumadan sonra ilk rapor çıkar: motor başına sözcük hata oranı ve "kaç cümlede niyet değişti".
2. Bugün: kaydı senin yapman gerekiyor: Windows Ses Kaydedici `.m4a` yazıyor, araç yalnız WAV kabul ediyor; yirmi
   dosyayı çevirmen ve `manifest.json`'u elle doldurman gerekir. Bu, "sahip operatör olmasın" kuralına aykırı; ADR
   bunu "open follow-up" diye bırakmış.
   Bununla: dosya biçimini, adları ve etiketleri sayfa yazar; sen yalnız okursun, "tekrar" dersen cümle yeniden alınır.
3. Bugün: yerel kipte her gün kullandığın Chrome tanıyıcısı ölçüm tablosunda "NOT_RUN: no file input" satırı; yani
   en çok kullandığın motor karşılaştırmaya giremiyor. %68,9 sayısı 106 cümlenin 103'ünde bizim türettiğimiz
   bozulmalarla ölçülüyor.
   Bununla: aynı okuma, aynı anda Chrome'a da gider; Chrome'un yazdığı cümle OpenAI'ınkiyle aynı tabloda, aynı
   sesten durur. Soniox'a hesap açıp açmamaya bu tabloya bakarak karar verirsin.

Kazanç: kayıt sayısı 0'dan 20'ye (ofisle 40'a) çıkar; ölçülebilen motor sayısı 0'dan en az 2'ye (OpenAI'ın iki
modeli, anahtar zaten var) ve Chrome ile 3'e; yeni hesap gerekmez.
Kazanmadığımız: okunan cümle, kendiliğinden söylenen cümle değildir — gerçek hayattaki yanlış duymaların hepsini
göstermez (onu bekleyen "yanlış anlaşılanlar defteri" önerisi toplar). Hiçbir yanlış duymayı düzeltmez; yalnız ölçer.
Soniox satırı, ayrı onayın olmadan NOT_RUN kalır. Gürültü (K66) ancak sen gürültülü odada okursan ölçülür.

## Neden şimdi
- ADR-0242 (2026-10-02): "The INSTRUMENT is delivered. No number exists"; "READY_FOR_OWNER: twenty WAV recordings
  (PCM 16-bit, mono, 16 kHz) … how they are recorded without the owner becoming an operator is an open follow-up".
  Entegratör planı R3 aynı şeyi söylüyor (`team/plans/stt-engines-measure-integration.md`).
- Chrome 135'ten beri `SpeechRecognition.start(audioTrack)` var: tanıyıcıya mikrofon yerine verilen bir ses hattı
  dinletilebiliyor (https://caniuse.com/mdn-api_speechrecognition_start_audiotrack ,
  https://developer.mozilla.org/en-US/docs/Web/API/SpeechRecognition/start). Aynı hat hem kaydediciye hem Chrome'a gider.
- **Kanıt ince, açıkça:** `start(audioTrack)` bilgisini yalnız uyumluluk tablosundan okudum; `tr-TR` ile ve cihaz içi
  tanımayla çalıştığını denemedim. Çalışmazsa Chrome satırı bugünkü gibi NOT_RUN kalır, önerinin geri kalanı durur.
- Dış taramada bu döngüde ölçüme eklenecek yeni bir Türkçe motor çıkmadı (gpt-realtime-2.1'den sonra yeni model yok).

## Nasıl
- Seam: `apps/web/app/lib/voice/audio.ts` içindeki `BrowserMicrophone` (gerçek ses yolunun mikrofonu, aynı ayarlar);
  `app/voice/stt_compare.py`'nin `manifest.json` biçimi (`file`, `reference`, `recorded_where`) ve yirmi cümlelik
  şablonu; `scripts/voice/stt-compare.ps1`; Cloud Core'un mevcut artifact deposu; tek yönlendiricide bir niyet.
- Değişen: bir sayfa (cümle, kayıt, "tekrar", "sil"), sayfada 16 kHz WAV yazan küçük kodlayıcı, yükleme ucu ve 30
  günlük süpürme, betiğe `-FromCore` (kayıtları indir, raporu yaz), ölçüm aracına "hazır yazı" satırı (Chrome'un
  kayıt anında yazdığı cümle). Değişmeyen: ses hattı, yönlendirici, varsayılan motor, sağlayıcılar; yeni bağımlılık yok.

## Maliyet/risk
Efor: küçük-orta. Çalışma: OpenAI'a yirmi kısa cümle, birkaç sent. CPX32: yirmi kayıt yaklaşık 3 MB; CPU yükü yok.
Lisans: yok. KVKK: ses kaydı kişisel veridir; yalnız yirmi hazır cümle, kendi sunucunda (Hetzner NBG1), 30 gün;
kimlik doğrulamada kullanılmaz (ADR-0171 değişmez). Kayıtlar yalnız bugün de sesini alan OpenAI'a gider; Soniox ayrı
onay. Rapor dosyası (`docs/evidence/stt-compare-<tarih>.json`) cümlelerin yazısını içerir ve GitHub'daki depoya girer:
hazır cümlelerle zararsız, serbest konuşma bu sayfaya hiç verilmez. Şirket PC'si: ofiste okursan ses şirket
makinesinden senin Cloud Core'una gider (bugünkü ses yoluyla aynı); odada başkası konuşuyorsa kaydetme — "sil" düğmesi
bunun için. Risk: tarayıcının gürültü bastırması açık olduğu için kayıt "ham" değil "sistemin duyduğu" sestir; rapor
bunu yazar. Yayın: mevcut depo kullanılırsa migration yok; gerekirse yayında sana sorulur.

## Kanıt planı
PROVEN_AUTOMATED: sahte mikrofonla kayıt → dosya `providers.wav_info` ile 16 kHz / tek kanal / 16 bit okunur; etiket
dosyası cümleyle eşleşir; "tekrar" eskisinin yerine geçer; "sil" ve 31. gün süpürmesi dosyayı kaldırır (dev stack).
PROVEN_PROXY: sentezlenmiş bir Türkçe ses aynı yoldan yüklenir, `stt-compare.ps1 -FromCore` rapor üretir.
PROVEN_REAL: MAIL'de "Ölçüm kaydını başlat" dersin, yirmi cümleyi okursun; ertesi rapor
`docs/evidence/stt-compare-<tarih>.md` içinde en az iki motorun sayısı vardır; sen "sil" deyince kayıtlar gider.

## Karar
Yapalım mı? (Onayın aynı zamanda yirmi cümlelik ses kaydının 30 gün kendi sunucunda durmasına izindir.)
Alternatifler: (a) ADR'nin önerdiği `-Record`: ev PC'de betik, kayıt sunucuya hiç çıkmaz — ama betiği sen
çalıştırırsın ve ofis mikrofonu ölçülmez; (b) telefonla kaydet, biz çevirelim — elle iş, mikrofon gerçek yol değil;
(c) bekle — onaylı ölçüm sayısız kalır.
