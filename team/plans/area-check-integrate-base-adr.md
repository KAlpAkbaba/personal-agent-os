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
  test edilir. İçermiyorsa BaseDiff olduğu gibi; içeriyorsa BaseDiff ∩ AlsoBaseDiff, BaseDiff'in
  sırasıyla, yollar git'in yazdığı gibi (ordinal) karşılaştırılır.
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

## Kesişim neden güvenli

İşçinin değiştirdiği her dosya, entegrasyon dalının ucuna göre de farklıdır - tek istisna:
işçinin dosyası entegrasyon dalındakiyle BAYT BAYT aynı ise (ör. yeniden kurulumun getirdiği hali
hiç değiştirmemiş, ya da aynı içeriği yazmış). O durumda işçinin dalı o dosyaya entegrasyon dalının
zaten taşıdığından başka bir şey katmaz; birleştirme o dosyada hiçbir şey değiştirmez, kaçan bir
ihlal yoktur. Entegrasyon dalının değiştirdiği bir dosyayı işçi farklı içerikle yeniden değiştirirse
iki farkta da görünür ve sayılır (test (f)). Kesişim yalnız işçi dalı entegrasyon dalını
İÇERİYORSA uygulanır; içermeyen dalda sonuç birebir bugünkü (test (c)).

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
