# Öneri: Koruyucu testler iş dalında — kapının saatler sonra bulduğunu döngü, denetleyiciden önce, parasız bulsun

Tarih: 2026-10-02 · Araştırmacı · Durum: awaiting_owner
Roadmap: tek bir JARVIS satırı değil; "Repairs and improves itself" ve "How it is built from here" (ekip döngüsü).
Dolaylı olarak her satır: yayın, kapı yeşil olmadan çıkmıyor ve 1-2 Ekim'deki yedi entegrasyon kapısının üçü ilk
koşuda kırmızıydı.
Öğrenilen ders türünden öneri.

## Ne
Depoda "bütün uygulamayı okuyan" küçük bir test ailesi var: her hata sınıfının Türkçe cümlesi var mı, her test
dosyasını kapı koşuyor mu, her tablo gerçek PostgreSQL'de sınanmış mı, PowerShell betikleri katı kipte geçiyor mu.
Bunlar saniyeler sürer, ama bugün yalnız lead'in tam kapısında koşuyor; çalışan ve denetleyici yalnız kendi
dosyalarını koşturuyor ve raporuna "tam birim takımı: NOT_RUN (lead'in kapısı koşar)" yazıyor.
Öneri: bu ailenin listesi tek dosyada dursun (`team/guards.json`); döngü betiği, çalışan bitince ve denetleyici
başlamadan önce, listeyi işin kendi dalında KENDİSİ koşsun (ajan yok, ücret yok). Kırmızı çıkan satır işin kartına
yazılır; denetleyici onu görür: düzeltme alanın içindeyse geri verir, dışındaysa (ortak dosya) lead'in bağlama
listesine satır olur. Kartında çözülmemiş koruyucu satırı olan iş için tam kapı başlatılmaz.
Senin cümlen yok; Ofis sayfasında işin yanında "koruyucular: yeşil" ya da "koruyucu kırmızı: Türkçe hata cümlesi eksik".

## Faydası — örneklerle
1. Bugün: bu sabah dördüncü entegrasyonun kapısı (`b35c6ddb`) tek testte kırmızı döndü: STT ölçüm aracı `unexpected`
   diye yeni bir hata sınıfı eklemiş, Türkçe karşılığı yoktu. Çalışan da denetleyici de tam takımı koşmamıştı
   (ikisi de "NOT_RUN" yazdı); lead cümleyi ekledi, tam kapı baştan koştu, yayın o kadar gecikti.
   Bununla: aynı test işin dalında saniyeler içinde kırmızı olur; satır denetleyicinin önüne ve lead'in bağlama
   listesine düşer, kapı ilk koşuda bu yüzden kırmızı olmaz.
2. Bugün: dün gece ikinci entegrasyonun kapısı (`2c691585`) kırmızıydı: `team-feed.tests.ps1` yazılmış ama onu hiçbir
   şey koşmuyordu (`test_ci_runs_every_powershell_suite`). O entegrasyondan hiçbir şey yayınlanmadı.
   Bununla: iş dalında "yeni test dosyası kapıya bağlı değil" satırı çıkar; bağlama, birleştirme anında unutulamaz.
3. Bugün: 1 Ekim sabahı ilk kapı 33/35 idi; biri `TeamRun.ps1` içinde katı kipte patlayan çıplak bir `.Count`
   (QUALIFICATION 38.10). İşin kendi testleri yeşildi.
   Bununla: `installer-strictmode` koruyucu listesindedir; iş, denetleyiciye gitmeden çalışana döner.

Kazanç: "ilk kapı kırmızı" sayısı ölçülür (1-2 Ekim: 7 kapıda 3; QUALIFICATION Stage 38-41 ve ADR-0242) ve yukarıdaki
üç nedenin bir daha kapıya kadar gelmemesi beklenir; her kırmızı kapı bir tam kapı koşusu ve bir yayın gecikmesidir.
Kazanmadığımız: başka bir ailenin davranış testini bozan değişikliği yakalamaz (QUALIFICATION 38.9: anlatı bağlaması
"Bugün neler yaptın" cümlesini sahibinden aldı) — onu yine tam kapı bulur. Tam birim takımını (14 401 test) iş başına
koşturmaz: yük altında 10 dakikada %7 ilerlediği ölçüldü, altı koşu yan yana bunu kaldırmaz.

## Neden şimdi (aynı kusur üç kez; hepsi kayıtta)
- QUALIFICATION 38.10 "Found by the gate" (2026-10-01); DECISIONS ADR-0237 "The lead's wiring": `2c691585` kırmızı
  (2026-10-01 gece); ADR-0242 son paragraf: `b35c6ddb` kırmızı, "neither the worker nor the inspector ran the full
  unit suite (both said so: NOT_RUN, 'the lead's gate runs them'), and the gate did" (2026-10-02).
- Son iki döngüde en az 14 çalışan/denetleyici raporu "full unit suite: NOT_RUN" diyor (`team/reports/d20261001/`,
  `d20261002/`); nedeni tembellik değil süre: "stopped it at 7 % after 10 minutes", "ran green to about 60 % and my
  1700 s cap killed it".
- `cycle-auto-integrate` (kuyrukta) kapıyı insansız koşturacak; kapı kırmızı olursa suçu işe yazıp geri verecek.
  Bu öneri onun yerine geçmez; aynı bulguyu saatler önce ve bir tam kapı harcamadan verir.
- Dış tarama: "değişen koda göre test seç" kitaplığı pytest-testmon'a baktım (MIT, son sürüm 2.2.0, Aralık 2024,
  33 açık kayıt; aralarında "global/sınıf değişkeni değişikliği görülmüyor", "veri dosyaları nasıl izleniyor?").
  ÖNERMİYORUM: bizim koruyucular kaynak dosyaları METİN olarak okuyor, bu araç onları izleyemez ve yanlış güven verir.
  https://github.com/tarpas/pytest-testmon/releases · https://github.com/tarpas/pytest-testmon/issues

## Nasıl
- Seam: `scripts/team/cycle.ps1` içinde çalışan bitince alan denetiminin yapıldığı yer (964. satır civarı: "a worker
  never leaves its area. Not the inspector's to find") — koruyucu koşusu onun hemen ardına girer; sonucu karta yazan
  `TeamRun.ps1`; denetleyici ve lead rol metinleri; `scripts/tests/team-cycle.tests.ps1` + sahte ajan.
- İlk liste (hepsi bugün var): `test_owner_error_language.py`, `test_ci_covers_every_suite.py`,
  `test_postgres_coverage_ratchet.py`, `test_host_snapshot_schema.py`, `script-syntax.tests.ps1`,
  `installer-strictmode.tests.ps1`. Kapıda bir test, listedeki her dosyanın var olduğunu sınar.
- Değişen: liste dosyası, betikte bir adım (süre sınırı yalnız asılı kalma bekçisi), kartta `guards` alanı, iki rol
  metninde birer paragraf. Değişmeyen: tam kapı, denetleyicinin hükmü, alan kuralı, yayın kuralı; yeni bağımlılık yok.

## Maliyet/risk
Efor: küçük. Çalışma: 0 USD (ajan koşmaz). Ev PC: iş başına tahminen bir dakikanın altında CPU — ÖLÇMEDİM, ilk iş
olarak ölçülür; üç dakikayı aşarsa liste kısaltılır. CPX32: yük yok. Lisans: yok. KVKK: veri yok.
Risk: (1) liste eskir — yeni bir "bütünü okuyan" test yazılır ama listeye girmez; kapı onu yine koşar, yani kayıp
bugünkü durumdan kötü değil. (2) Koruyucu, işin doğası gereği dalda kırmızı olabilir (yeni test dosyası ancak
birleştirmede kapıya bağlanır); bu yüzden kırmızı tek başına işi durdurmaz, karta satır olur.
TEAM_PROTOCOL'e bir madde eklenir; bu yüzden onayın gerekli.

## Kanıt planı
PROVEN_AUTOMATED: sahte ajanla üç senaryo — koruyucu yeşil → denetleyici başlar, kartta "yeşil"; koruyucu kırmızı →
satır kartta ve denetleyicinin girdisinde; çözülmemiş satır varken entegrasyon adımı kapıyı başlatmaz. Adım
çıkarılınca üçü de KIRMIZI. Gerçek depo üzerinde: `catalog.py`'den bir Türkçe cümle silinmiş dalda adım kırmızı verir.
PROVEN_REAL: gerçek bir döngüde en az bir işte "koruyucu kırmızı" satırı görülür ve o entegrasyonun kapısı ilk koşuda
yeşil geçer; sonraki beş entegrasyonda "ilk kapı kırmızı" sayılır ve nedenleri yazılır.

## Karar
Yapalım mı? Alternatifler: (a) yalnız rol metnine "şu altı dosyayı koş" yaz — metin kuralı; "alan dışı" dersinde iki kez
yetmedi; (b) her işte tam birim takımı — ölçülen süreyle altı koşu yan yana taşınmaz; (c) bugünkü gibi, kapı bulsun.
