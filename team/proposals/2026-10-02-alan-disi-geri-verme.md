# Öneri: Alan dışı geri verme — düzeltmesi kartın alanı dışında kalan iş durmasın, alanı kendiliğinden genişlesin

Tarih: 2026-10-02 · Araştırmacı · Durum: awaiting_owner
Roadmap: tek bir JARVIS satırı değil; "Repairs and improves itself" ve "How it is built from here" (ekip döngüsü).
Dolaylı olarak sıra 2b ve 2c: aşağıdaki iki durmuş iş o satırların işleri. Öğrenilen ders türünden öneri.

## Ne
Bugün bir işin eksiği kartın dosya alanının DIŞINDAYSA çalışan onu düzeltemez; denetleyici geri verir, ikinci geri
vermede iş durur ve lead'in elle açmasını bekler. Öneri üç küçük kural:
(1) Denetleyicinin geri verme maddesi alan dışı bir dosyayı gösteriyorsa bunu yapılandırılmış yazar
(`alan_disi: [dosyalar]`); bu geri verme çalışanın "iki hak"kından sayılmaz.
(2) Döngü, istenen dosyalar başka bir açık kartın alanıyla çakışmıyorsa alanı kendisi genişletir ve işi aynı turda
yeniden koşturur; çakışıyorsa işi o kartın arkasına `depends_on` ile koyar.
(3) Çalışan, kırmızı kabul testini yazdıktan sonra, yeşile çevirmek için alan dışı dosya gerektiğini görürse
uygulamaya girmeden `ALAN_ISTEGI` ile döner (dakikalar, bir saat değil).
Senin cümlen yok; Ofis sayfasında "durduruldu" yerine "alanı genişletildi, yeniden koşuyor" görürsün.

## Faydası — örneklerle
1. Bugün: `narrative-failures-only-model` iki tur koştu (4 koşu, 11,31 USD), ikisinde de aynı madde: "ne başarısız
   oldu" gerçek yönlendiriciden anlatıya ulaşmıyor, düzeltme `app/voice/intents` içinde, alan dışı. İş durdu; sen
   "ne başarısız oldu" dediğinde hâlâ yalnız son başarısızlığı duyuyorsun.
   Bununla: ilk geri vermede alan `app/voice/intents`'e genişler, ikinci tur asıl işi yapar.
2. Bugün: `execution-call-site-research` iki tur koştu (4 koşu, 14,55 USD); denetleyici engeli okuyarak buldu
   (bulut işçisi geçidin gönderdiği `visible: true, channel: chrome` oturumunu açamaz), düzeltme geçit dosyasında,
   alan dışı. "Aynı iş iki kez geri verildi" diye durdu; "bulutta araştır" ilerlemedi.
   Bununla: geri verme çalışanın hakkından düşmez; alan geçit dosyasına genişler ya da ayrı kart kendiliğinden kesilir.
3. Bugün: `cycle-2026-10-01`'de üç kart "alan dışı dosya" ile durdu (00:16 UTC); lead alanları elle genişletip
   07:49 UTC'de yeniden başlattı — yedi buçuk saat boş koltuk.
   Bununla: çakışma yoksa aynı turda sürer; gece durmaz.

Kazanç: d20261001'de harcanan tahmini 101,23 USD'nin 25,86'sı (yaklaşık dörtte biri) bu iki işe gitti ve ikisi de
ürüne ulaşmadı; "Durdurulanlar"daki alan kaynaklı satır sayısı ölçülür ve düşmesi beklenir.
Kazanmadığımız: kartın amacı yanlışsa (yanlış iş istenmişse) bu kural yardım etmez; gerçek kusurdan geri vermeler
(örn. `understanding-corrections-memory`) aynen sayılır; lead'in kartı doğru kesme sorumluluğu kalkmaz.

## Neden şimdi (yazılı kural var, iki kez daha oldu)
- Kural `lead.md`'de yazılı (17-24. satırlar: "four cards in two cycles … stopped as 'alan dışı dosya'"). Yazıldıktan
  SONRA, aynı gün iki iş daha aynı nedenle durdu: `team/reports/d20261001.md` "Durdurulanlar" ve
  `d20261001/narrative-failures-only-model-inspector-2.md` ("it cannot be fixed inside the card's area"),
  `d20261001/execution-call-site-research-inspector-2.md` ("The fix is outside this card's area").
- Bir kuralın metni iki kez yetmediyse makine denetimi gerekir; çakışma denetimi için gereken bilgi (her kartın
  alanı) kuyrukta zaten var.

## Nasıl
- Seam: `scripts/team/cycle.ps1:969` (bugün "alan dışı dosya" diye durduran yer), denetleyici ve çalışan rol
  metinleri, kuyruk şemasındaki `area` ve `depends_on` alanları, `scripts/tests/team-cycle.tests.ps1` + sahte ajan
  (`fake-claude.ps1`'de `outside` senaryosu zaten var).
- Değişen: hüküm satırından `alan_disi` listesini okuyan ayrıştırma; çakışma yoksa alanı genişletip kartın
  geçmişine "kim, neden, hangi dosyalar" yazan adım; genişletme sayısına tavan (iş başına 2; aşılırsa lead'e).
- Değişmeyen: "aynı alanda iki iş yok" kuralı; denetleyicinin hükmü; birleştirme ve yayın kapıları; korunan yollar
  (sırlar, LKG, kurtarma kökleri, ROADMAP, TEAM_PROTOCOL) hiçbir zaman genişletmeyle alana girmez.

## Maliyet/risk
Efor: küçük. Çalışma: 0 USD; beklenen etki harcamayı azaltmak. CPX32: yük yok (betik ev PC'de koşar).
Lisans/bağımlılık: yok. KVKK: veri yok. Risk: alan durmadan büyüyen bir iş gözden kaçabilir — tavan ve kart
geçmişindeki kayıt bunun için; korunan yollar listesi sabit ve testte.
TEAM_PROTOCOL metni değişeceği için onayın gerekli (lead onu onaysız değiştiremez).

## Kanıt planı
PROVEN_AUTOMATED: sahte ajanla üç senaryo — alan dışı geri verme + çakışma yok → alan genişler, iş aynı turda
yeniden koşar, hak sayılmaz; çakışma var → `depends_on`; korunan yol istenirse → reddedilir ve lead'e düşer.
Kural geri alınınca üçü de KIRMIZI.
PROVEN_REAL: gerçek bir döngüde bu iki durmuş iş yeniden açılır; raporda "alanı genişletildi" satırı ve işlerin
`merged` olması görülür; sonraki üç döngü raporunda alan kaynaklı "durduruldu" satırı sayılır.

## Karar
Yapalım mı? Alternatifler: (a) yalnız kural (1): alan dışı geri verme haktan sayılmasın, genişletmeyi lead yapsın —
en küçük adım; (b) çalışan alanı hiç sınırlanmasın — önermiyorum, paralel işler birbirinin dosyasını ezer;
(c) bugünkü gibi, lead elle açsın.
