# ADR (taslak, numarayı Proje Yöneticisi verir): alan kontrolü entegrasyon dalını da taban sayar

- Kart: area-check-integrate-base (döngü d20261004)
- Öneri: team/proposals/2026-10-04-alan-kontrolu-entegrasyon-tabani.md
- Durum: kabul edildi (kural + testler); bağlama ayrı kartta

## Bağlam

'Entegrasyon dalında çakışma' ile dönen bir işçi dalı integrate/<döngü> üstüne yeniden kurulunca
ya da o dal işçi dalına birleştirilince, `git diff --name-only main...dal` entegrasyon dalının
taşıdığı BAŞKA kartların dosyalarını da gösterir. Döngünün alan kontrolü
(scripts/team/cycle.ps1, `"worker"` kolu, `Get-TeamChangedFiles -RepoRoot $repoRoot -Branch $branch -Base $Base`)
bu dosyaları işçinin sayıp işi 'alan dışı dosya' ile durdurdu (3 Ekim: integrate-own-lock ve
ps1-bom-everywhere, aynı beş yabancı dosya).

## Karar

scripts/lib/TeamArea.ps1'e iki fonksiyon:

- `Select-TeamWorkerChangedFiles -BaseDiff -AlsoBaseDiff -ContainsAlsoBase` - saf kural, git'siz
  test edilir. İçermiyorsa BaseDiff olduğu gibi. İçeriyorsa AlsoBaseDiff'in TÜM dosyaları: önce
  BaseDiff'te de olanlar BaseDiff'in sırasıyla, sonra yalnız AlsoBaseDiff'te olanlar (işçinin
  main haline geri aldığı yabancı dosyalar) AlsoBaseDiff'in sırasıyla. Sıra seçimi: listenin başı
  bugünkü ret metninin sırası kalsın, geri alınanlar sona eklensin. Yollar git'in yazdığı gibi
  (ordinal) karşılaştırılır.
- İlk sürüm (3e9258f8) BaseDiff ∩ AlsoBaseDiff idi; denetçi 4 Ekim'de geri alma kaçışını buldu
  (aşağıda), bu yüzden kesişim tek başına cevap değil.
- `Get-TeamWorkerChangedFiles -RepoRoot -Branch [-Base main] [-AlsoBase <integrate/döngü>] [-Git]` -
  -AlsoBase yoksa, o dal yoksa (`rev-parse --verify refs/heads/<AlsoBase>`) ya da işçi dalı onu
  içermiyorsa (`merge-base --is-ancestor`, çıkış kodu 0 değilse) sonuç bugünkü
  Get-TeamChangedFiles'ınki. Başarısız bir diff hata atar (boş liste asla).

Git'e erişim: dosyanın "süreç başlatmaz" cümlesinin tek, başlıkta adı konmuş istisnası
Get-TeamWorkerChangedFiles'tır ve git'i kendisi değil, çağıranın verdiği `-Git` scriptblock'u
(`{ param($Directory, $Arguments) }`, Invoke-TeamGit'in şekli) ile sorar. Verilmezse TeamRun.ps1'in
Invoke-TeamGit'i kullanılır (cycle.ps1 onu zaten yükler); o da yoksa açık bir hata. Böylece
TeamArea.ps1 TeamRun/NativeProcess yüklemez, bağlama satırı tek satır kalır.

Alan kuralı, ret metni ('alan dışı dosya: ...'), -Base ve integrate.ps1'in kapı suçlama süzgeci değişmedi.

## Neden güvenli (ve ilk sürümün yanlış iddiası)

İlk sürüm "bayt bayt aynı değişiklik tek kaçış, zararsız" diyordu. YANLIŞTI: kesişimde ikinci bir
kaçış vardı - geri alma. İşçi, entegrasyon dalının değiştirdiği bir dosyayı main haline geri alırsa
ya da entegrasyon dalının eklediği dosyayı silerse, o dosya main'e göre değişmemiştir
(BaseDiff'te yok), ama entegrasyon dalına göre değişmiştir (AlsoBaseDiff'te var). Kesişim onu
düşürüyordu; dal integrate'e birleşince başka bir kartın onaylı işi sessizce geri alınırdı.
Kapanış: dal entegrasyon dalını içeriyorsa cevap AlsoBaseDiff'in tamamıdır (test (g), yeniden
kurulmuş ve birleştirmeli iki dal; mutasyon "yalnız kesişim" ile ikisi de KIRMIZI).

Bugünkü iddia: dal entegrasyon dalını içerdiğinde `git diff <integrate>...<dal>` = dalın ucunun
entegrasyon dalının ucundan farkı. Dalın integrate'e birleşmesinin değiştireceği HER dosya bu
listededir; listede olmayan bir dosya entegrasyon dalındakiyle bayt bayt aynıdır ve birleştirme onda
hiçbir şey değiştirmez (yeniden kurulumun getirdiği yabancı dosyalar böyle düşer). Entegrasyon
dalının dosyasını işçi farklı içerikle değiştirirse sayılır (test (f)). İçermeyen dalda sonuç
birebir bugünkü (test (c)); integrate yoksa da öyle (test (e)).

Kalan risk (yanlış ret yönünde, kaçış değil): dal integrate'i içeriyor VE integrate'in çatallandığı
yerden sonra main'i de birleştirmişse, main'in yeni dosyaları AlsoBaseDiff'te görünür ve 'alan dışı'
sayılır. Durdurur, ihlal kaçırmaz. Integrate zorla yeniden yazılıp dalın atası olmaktan çıkarsa
bugünkü sonuca düşülür (yine yalnız fazla ret).

Proje Yöneticisi'ne not: kart metnindeki "bayt bayt aynı değişiklik tek kaçış, zararsız" cümlesi
aynı yanlışı taşır; bağlama kartında ya da kartın arşiv metninde düzeltilmeli.

## Bağlama (ayrı kart; cycle.ps1 team-engine ve onaylı başka kartlarda olduğu için bu kartın işi değil)

Proje Yöneticisi, cycle.ps1 serbest kalınca tek satırlık bir kart keser:

1. Yükleme (cycle.ps1 satır 160 civarı, TeamRun.ps1'den sonra):
   `. (Join-Path $repoRoot "scripts\lib\TeamArea.ps1")`
   (TeamArea.ps1 TeamQueue.ps1'i kendisi yükler; zaten yüklüyse yeniden tanımlar, zararsız.)
2. Alan kontrolü (bugün cycle.ps1:1404, `"worker"` kolu):
   eski: `$outside = @(Get-TeamChangedFiles -RepoRoot $repoRoot -Branch $branch -Base $Base |`
   yeni: `$outside = @(Get-TeamWorkerChangedFiles -RepoRoot $repoRoot -Branch $branch -Base $Base -AlsoBase "integrate/$CycleId" |`
   Entegrasyon dalının adı Merge-TeamBranch'teki (scripts/lib/TeamRun.ps1) `"integrate/$CycleId"`
   ile aynıdır; `$CycleId` cycle.ps1'in parametresidir (satır 101/164).

Kanıt: bu kartta PROVEN_AUTOMATED (team-area tests, kum havuzu git deposu, mutasyon RED).
PROVEN_REAL bağlama kartından sonra: bir sonraki 'entegrasyon dalında çakışma' dönüşü 'alan dışı'
ile durmadan inspecting'e geçer ve döngü raporunda görülür.
