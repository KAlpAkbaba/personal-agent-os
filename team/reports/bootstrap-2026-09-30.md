# Döngü raporu — bootstrap-2026-09-30

Makine: MAIL (ev PC'si) · Proje Hakimi oturumu · AŞAMA 0 (açık işler) + AŞAMA 1 (ekibin kurulması).
Bu döngüde model koşusu başlatılmadı; pilot döngü AŞAMA 2'dir.

## Hazır olanlar (sha)

- Ofis günü main'de: `integrate/office-day-2026-09-29` ucu `8cff1a69d88789f8403bee6f961895318d9915cf`,
  tam kapı 33/33 PASS; main birleştirmesi `771a9e53483bc952b17a404dce456d0109d44abe` (`--no-ff`, push edildi).
  İçinde: ADR-0208 (oturum cihazı yakınlığı), ADR-0209 (Operatörsüz uygulama açma), ADR-0210 + eki
  (rapor sahibin Chrome'unda sekme; dar SSRF istisnası), ADR-0211 (audit klasörü), ADR-0212 (söylenen cihaz).
- Tarayıcı görev döngüsü PR-B main'de: `4d5dd637b0cb0609948ff6421be53105bbeb1429` (sözleşme v1.7).
- Ekip kurulumu: dal `feat/dev-team` — sha'lar bu raporun sonundaki "Kapanış" satırında.

## Onay bekleyenler (fikir / yayın)

- YAYIN: bekleyen yok. **main `771a9e53483bc952b17a404dce456d0109d44abe` sahibin cümlesiyle yayınlandı**
  (2026-09-29 19:58 UTC, blue/green, api-green). Göç `0062_web_tasks` uygulandı, gerçek zamanlı
  sözleşme v3, iki cihaz oturumu 1 saniyede taşındı, sağlık `ok`, `failing_checks` boş.
  Pin `771a9e53483bc952b17a404dce456d0109d44abe`; timer bir döngüde `RECONCILE OK`.
  LKG `be2975ae673af9e7739260abbc9eb9761bfc9bf6` (yayın kendisi yazdı; elle dokunulmadı).
  `.env` satırı eklendi ve api süreci onu görüyor.
- **SAHİP ADIMI: MAIL ajanının bu ağaçtan yeniden kurulması** (aşağıda, komutlarıyla).
- FİKİR: yok. İlk öneriler pilot döngüde (AŞAMA 2) araştırmacıdan gelecek.

### MAIL ajanı — komutların tam hali (sırayla, sahibin kendi PowerShell'inde)

1. Kurulum (tek UAC; iki anahtar da zorunlu — biri eksikse o özellik KAPANIR):

       Start-Process "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" -Verb RunAs -Wait -ArgumentList '-NoProfile -ExecutionPolicy Bypass -NoExit -File "E:\AI\PersonalAgentOS_Claude_Autonomous_Build_Package_v1\scripts\install-device-service.ps1" -DisplayPower -Operator'

   Açılan pencerede `broker endpoints (...)` satırı `http://100.90.158.26:8001` göstermeli.
2. Doğrulama (yükseltme gerekmez; 6b.4 dahil her satır PROVEN olmalı):

       & "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File "E:\AI\PersonalAgentOS_Claude_Autonomous_Build_Package_v1\scripts\verify-device-service.ps1"

3. `enroll-owner-chrome.ps1 -AuthorizeTasks` ÇALIŞTIRILMAZ: bu anahtar kodda yok (PR-C'nin işi).
   Mevcut araştırma izni yeniden kurulumda silinmez.

Ön koşul: main checkout'u `771a9e53` ya da sonrası olmalı (kurucu çalışma ağacından derler).

## Sahibin gerçek cihazda deneyecekleri (cümle cümle, hangi makinede)

Hepsi YAYINDAN ve MAIL kurulumundan SONRA:

- MAIL (ev), web kabuğu `/voice`: cihaz seçicide **"Bu bilgisayar"** bir kez seçilir.
- MAIL, yerel mod: **"Bundan sonra araştırma raporlarını her zaman Türkçe oku"** → hafıza satırını
  lead okur; ADR-0201 PROVEN_REAL olur (QUALIFICATION 30.10).
- MAIL: **"hesap makinesini aç"** → hesap makinesi EVDE açılır (ADR-0208/0209).
- MAIL: **"ofis bilgisayarımda hesap makinesini aç"** → ofis açıksa orada açılır; kapalıysa SESLİ RET
  beklenir, evde açılmaz (ADR-0212).
- GMKADIRAKBABA (ofis), kurucu yeniden çalıştırıldıktan sonra: **"araştırma raporunu aç"** → rapor
  sahibin Chrome'unda sekme olarak açılır (ADR-0210 eki).

## Geri verilenler ve nedeni

Yok.

## Durdurulanlar

- İlk tam kapı (uç `7b77986e`) 29/33: Docker Desktop kapalıydı (dev stack, alembic, entegrasyon) ve bir
  masaüstü testi Not Defteri'nde araya giren bir tuşla düştü (`Binside the root`; tek başına 3/3 geçti).
  Docker açıldı. İkinci kapı, ofis dalı ilerlediği için (SSRF düzeltmesi itildi) yarıda durduruldu.
  Üçüncü kapı güncel uçta 33/33.

## Harcanan bütçe

- Model koşusu: 0 (bu döngü `cycle.ps1` ile koşulmadı; bootstrap'ı lead oturumu yaptı).
- Tam kapı: 3 kez başlatıldı, 2 kez tamamlandı (~45 dk / koşu).

## Açık riskler

- **Üretimdeki broker aynı komutu cihaza iki kez verebiliyor (ADR-0215).** Ekip dalının kapısı buldu;
  dalda düzeltildi, YAYINLANMADI. Görünen etkisi: zaten açık bir uygulamanın ikinci kez açılması.
  Seyrek (bugün dört kapıda bir kez), ama gerçek. Sonraki yayın onayını bekliyor.
- **`RECOVERY_BUNDLE_STALE` dosyası pin'den sonra da duruyor.** Paket ile canlı ağaç bayt bayt aynı
  (sunucuda `cmp`), timer 0 ile çıkıyor, sağlık `ok`; ama dosyayı yalnız bir sonraki yayın siliyor.
  Çaresi uygulanmış bir uyarının durması bir kusur: kuyruğa iş olarak girmeli.
- **İlk ön kontrol 82 ile döndü** (dakikalık reconcile kilidi tutuyordu); ikinci deneme temiz. Yayın
  betiği bu çakışmada kendisi beklemiyor.
- **Sunucuda `.env.bak-20260930` duruyor** (sırlar içerir, 0600 root). Sahip isterse silinir.
- **ADR-0210 yayından sonra da ofiste çalışmaz**, ofis PC'sinde kurucu yeniden koşulana kadar
  (`--trusted-origin` kurucunun yazdığı bir ayar).
- **PR-C'ye bağlayıcı altı sınır açık** (DECISIONS.md, ADR-0207 "Recorded for PR-C").
- **Gerçek modelle hiçbir ekip döngüsü koşulmadı.** Betik ve kuyruk PROVEN_AUTOMATED (sahte model);
  maliyet, süre ve geri verme oranı pilotta ölçülecek.
- **Başsız bir koşu `Bash` aracına sahip** (worker, inspector, integrator, lead). Çalışma dizini işin
  worktree'sidir; ama bir kabuk komutu dizinle sınırlı değildir. Rol dosyaları yasakları söyler,
  betik alan dışı DOSYAYI yakalar; alan dışı bir KOMUTU yakalayan bir şey yok.
- **Masaüstü testleri sahibin klavyesiyle yarışıyor.** Gece döngüsü 02:00'de bu yüzden daha güvenli.

## Protokol boşlukları

1. Pester istendi; depo kendi test düzeneğini kullanıyor (kapıda 30 paket), makinede yalnız Pester 3.4.0 var.
   Depo düzenine uyuldu.
2. "İlk sahip kapısında durur" İŞ başına okundu: kapıdaki iş bekler, koşabilen işler koşar.
3. Kilit checkout içinde bir dosya: bir makinede iki döngüyü durdurur; iki MAKİNEYİ ancak itilip
   çekilirse durdurur. Ortak yer (Cloud Core) gelene kadar ofis PC'si döngü koşmaz.
4. "Worktree'ler main checkout'un içinde olmaz" ile `.claude/worktrees/` yolu çelişiyor; yol esas alındı
   (git bu dizini yok sayıyor).
5. Koşu başına tavanlar rol dosyalarında değil, `cycle.ps1` parametrelerinde ve işin `budget` alanında.
6. Rol başına model yok; döngü başına tek `-Model`.
7. Token değil USD ölçülüyor (koşunun ham belgesi `team/reports/<döngü>/` altında, kullanım içinde).
8. Var olan salt-okunur `researcher` rolünün yerini ekibin araştırmacısı aldı (aynı ad).
9. Lead "özellik kodu yazmaz"; bootstrap betiklerini lead yazdı, çünkü yazacak ekip yoktu.
10. Sabah raporunun kabukta ve sesle verilmesi yok; Onay Merkezi işinin parçası.
11. Yayın onayı önce sahibin kendi cümlesiyle verildi ("Şimdi yayınla…"), ardından gelen ekip metni
    yayını sahip kapısı saydı ve onaydan sonra dal değişti (SSRF düzeltmesi eklendi). Onay,
    onaylanan ağaç için geçerli sayıldı; güncel main için YENİDEN bekleniyor.
12. `-AuthorizeTasks` iki görev metninde de istendi; kodda yok.

Onay vermek için (Onay Merkezi hazır olana kadar): `team/queue.json` içinde ilgili işin `state` alanını
`approved` yapın ve kaydedin; bir sonraki döngü bunu okur.

Gece döngüsünü açmak için (pilot ölçüldükten ve siz onayladıktan SONRA; şu an KAYITLI DEĞİL):

    & "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File "E:\AI\PersonalAgentOS_Claude_Autonomous_Build_Package_v1\scripts\team\register-nightly.ps1" -MaxUsd 15 -Register
