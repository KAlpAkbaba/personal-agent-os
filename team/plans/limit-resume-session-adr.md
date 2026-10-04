# ADR (taslak, numarayı lead verir): Limitte kesilen koşu kaldığı yerden sürer

Görev: `limit-resume-session` (döngü d20261004). Öneri: `team/proposals/2026-10-04-limitte-kaldigi-yerden.md`.

## Bağlam

Her koşu `--no-session-persistence` ile başlıyor (`scripts/lib/TeamRun.ps1:283`). Limitte kesilen
koşu `Resume-LimitedRun` (`scripts/team/cycle.ps1:1235`) ile AYNI uzun istemle YENİ oturumda baştan
açılıyor; d20261003 + d20261004 raporlarında 9 kesinti, ~19 000 sn, ~24,26 USD yeniden okuma/yeniden yapma.

## Karar

Karar ve komut satırı yeni, saf bir kütüphanede: `scripts/lib/TeamResume.ps1` (süreç başlatmaz; claude/git
çağrısı yok; tek yan etki `Remove-TeamRunSessionFiles`'ın silmesi). Fonksiyonlar:

| Fonksiyon | Ne döner |
|---|---|
| `New-TeamRunSessionId` | küçük harfli, tireli yeni GUID |
| `Get-TeamRunSessionArgs -SessionId` | `@('--session-id', <uuid>)` (geçersiz uuid -> throw) |
| `Select-TeamResumePlan -RunSession -ElapsedSeconds -StopReason -SameAccount -SessionFile -Role -SessionRole [-MinSeconds 600] [-Part 1]` | `Mode` resume/fresh, `ResumeTarget`, `Reason` (Türkçe tek satır) |
| `Get-TeamResumeArgs -Plan -Model` | `Arguments = --resume <hedef> --model <model>`, `Prompt` = kısa devam istemi; fresh plan -> throw |
| `Get-TeamProjectDirName -Path` | Claude Code'un proje klasör adı |
| `Get-TeamSessionFilePath -AccountDir -ProjectDir -SessionId` | `<hesap>\projects\<proje>\<uuid>.jsonl` |
| `Remove-TeamRunSessionFiles -Paths -AccountDirs` | `Removed`, `RemovedDirs` (`<uuid>\` klasörleri), `Refused`, `Missing` |

Kural (`Select-TeamResumePlan`), sırayla; ilk tutan 'fresh' döner:
1. `StopReason` tam olarak `usage_limit` değil -> 'baştan: durma nedeni kullanım limiti değil (<neden>)'. Hata, zaman aşımı, boş: hep baştan.
2. `RunSession` boş / geçerli uuid değil -> 'baştan: koşunun kayıtlı oturumu yok'.
3. `Role` boş ya da `SessionRole`'den farklı -> 'baştan: oturum <rol> rolünün, ...' (ikisi de boşsa da baştan).
4. `ElapsedSeconds < MinSeconds` -> 'baştan: koşu 10 dakikadan kısa (<n> sn)'. Tam 600 sn devam eder (>=).
5. Hesap değişti (`SameAccount=$false`) ve `SessionFile` boş ya da dosya yok -> 'baştan: hesap değişti, oturum dosyası bulunamadı'.
Aksi halde 'resume': aynı hesapta hedef uuid; hesap değiştiyse .jsonl'in TAM yolu.
Reason: `devam etti (oturum <uuid>, <Part+1>. parça[, öbür hesabın dosyasından])`.

Devam istemi (ilk koşunun uzun kartı DEĞİL): `Limit kalktı; kaldığın yerden devam et, bitmiş adımları yeniden yapma; son adımın sonucunu doğrula.`
Model devamda da geçer: `Resume-LimitedRun` bir alt modele inerek devam ettirir.

## 10 dakika eşiğinin gerekçesi

Devam, oturumun tüm geçmişini (rol dosyası, kart, okunan dosyalar, araç çıktıları) bağlam olarak yeniden
yükler; önbellek limit beklemesinde soğuduğu için bu yükleme tam ücretle okunur. 10 dakikadan kısa bir
koşu çoğunlukla okuma aşamasındadır (kart, policy, ilgili kod): baştan açmak kabaca aynı okumayı yapar,
ama yarım kalmış bir aracın bıraktığı belirsiz durumu taşımaz. 10 dakikadan sonra koşu kırmızı test,
uygulama, mutasyon gibi pahalı adımlara girmiş olur; bunları yeniden yaptırmak yeniden yüklemeden pahalı.
Eşik `-MinSeconds` ile değişir; ayara bağlamak bağlama kartının işi.

## Kaynak: `--resume`, `--session-id`, `--no-session-persistence`

- code.claude.com/docs/en/cli-reference (bu koşuda web aracı yoktu; işçi rolünde WebFetch yok - okunamadı,
  okunduğu tarih: YOK). Yerine yerel kaynak, okunduğu tarih 2026-10-04:
  `%USERPROFILE%\.local\bin\claude.exe --help` (sürüm `2.1.285 (Claude Code)`, döngünün `-ClaudePath` varsayılanı):
  - `-r, --resume [value]  Resume a conversation by session ID, or open interactive picker ...`
  - `--session-id <uuid>  Use a specific session ID for the conversation (must be a valid UUID)`
  - `--no-session-persistence  Disable session persistence - sessions will not be saved to disk and cannot be resumed (only works with --print)`
  - `--fork-session  When resuming, create a new session ID instead of reusing the original` (KULLANILMIYOR: aynı id sürsün ki silme listesi tek dosya olsun).
- Oturum dosyası yeri, gözlem 2026-10-04: `C:\Users\alpak\.claude\projects\C--Users-alpak\<uuid>.jsonl`,
  bu worktree'nin klasörü `.claude-hesap2\projects\E--AI-PersonalAgentOS-Claude-Autonomous-Build-Package-v1--claude-worktrees-team-d20261004-worker-limit-resume-session`.
  Kural: ASCII harf/rakam dışındaki her karakter '-' (`_` ve `.` dahil, yalnız `:` `\` `/` değil).
  Tuzak: `-replace` büyük/küçük harf duyarsız; tr-TR'de `[^A-Za-z0-9]` 'I'yı da değiştirdi (`E:\AI` -> `E--A-`), testte yakalandı; `-creplace` kullanılır.
- Bağlama kartı İLK iş olarak `--resume <tam .jsonl yolu>`nun başka hesabın dosyasını açtığını bir kez gerçek
  koşuyla doğrulamalı (bu kartta PROVEN değil); açmıyorsa plan 5. adımda dosyayı hedef hesabın
  `projects\<proje>\` altına kopyalayıp uuid ile devam eder.

## Denetleyici bağımsızlığı

Denetleyici koşusu yalnız KENDİ önceki oturumunu sürdürür (`Role` = `SessionRole` = inspector). İşçinin
oturumunu sürdürmek denetleyiciye işçinin gerekçesini miras bırakır; rol uyuşmazlığı ya da bilinmeyen rol -> 'fresh'.
Denetleyici tabanı, koşu sayısı kuralı, iki koşu kuralı, rapor ve maliyet biçimi DEĞİŞMEZ.

## KVKK silme kuralı

Oturum dosyası deponun içeriğini ve araç çıktılarını taşır. Kart kapanınca (merged/stopped) `run_session`
listesindeki her oturumun dosyası `Remove-TeamRunSessionFiles` ile silinir. Yalnız `.jsonl` uzantılı ve
`GetFullPath` ile çözülmüş hali (`..` dahil) verilen hesap dizinlerinden birinin ALTINDA olan yol silinir;
benzer adlı kardeş dizin (`.claude-hesap1-copy`) dışarıdır; sürücü kökü hesap dizini sayılmaz. Diğer her yol
silinmez, `Write-Warning` ile söylenir ve `Refused`'a yazılır.

Oturum klasörü: Claude Code `<uuid>.jsonl`'in yanına `<uuid>\tool-results\` klasörü yazar (araç çıktıları, yani depo
içeriği). Bu klasör `--no-session-persistence` ile de yazılır (gözlem 2026-10-04, denetçi: `.claude-hesap2\projects\E--AI-…`
altında 15 klasör, hiç `.jsonl` yok). Bu yüzden silme, kabul edilen (`.jsonl`, hesap dizini altında) bir yolun adı geçerli bir
uuid ise yanındaki `<uuid>\` klasörünü de özyinelemeli siler; `.jsonl` dosyası yoksa da (`Missing`) klasör silinir. Klasör yolu
kabul edilmiş `.jsonl` yolundan türetildiği için aynı hesap dizini sınırının içindedir; uuid adı taşımayan `.jsonl`'in yanındaki
klasör silinmez; klasörün kendisi bir bağlantı noktasıysa (junction/symlink) silinmez, `Refused`'a yazılır; içindeki bağlantılar
`[System.IO.Directory]::Delete` ile hedefleri izlenmeden kaldırılır. Silinen klasörler `RemovedDirs`'te döner.

## Bağlama kartı: tam satır listesi (TeamRun.ps1 / cycle.ps1 / quality-gate.ps1 serbest kalınca)

1. `scripts/lib/TeamRun.ps1:283` - `Get-TeamRunArguments`'a `[string]$SessionId = ""` ve `[string[]]$ResumeArguments = @()`
   parametreleri; listeden `"--no-session-persistence"` çıkar. Yerine: `$ResumeArguments` doluysa onlar (içinde
   `--model` var, satır 298'deki `--model` eklemesi o zaman atlanır), değilse `Get-TeamRunSessionArgs -SessionId $SessionId`
   (boşsa `New-TeamRunSessionId`). TeamRun.ps1 başına `. (Join-Path $PSScriptRoot "TeamResume.ps1")`.
2. `scripts/team/cycle.ps1:764` (`Start-RoleRun`) - `$sessionId = New-TeamRunSessionId`; `Get-TeamRunArguments ... -SessionId $sessionId`;
   başlatılan koşu nesnesine `SessionId`, `Role`, `StartedAt`, `Account` alanları. Kartın `run_session` alanı
   `Set-TeamProperty -InputObject $Task -Name "run_session" -Value @{ id = $sessionId; role = $Role; account = <hesap dizini>; part = <n> }`
   ile yazılır; `run_sessions` (liste) kapanışta silme için biriktirilir.
3. `scripts/team/cycle.ps1:1235` (`Resume-LimitedRun`) - `Start-RoleRun`'dan önce:
   `$rs = $Task.run_session` (2. satırda yazılan kayıt; oturumun SAHİBİ) ve
   `$plan = Select-TeamResumePlan -RunSession $rs.id -ElapsedSeconds <bitiş-başlangıç> -StopReason ($(if ($Done.UsageLimited) {'usage_limit'} else {'other'})) -SameAccount (<yeni hesap> -eq $rs.account) -SessionFile (Get-TeamSessionFilePath -AccountDir $rs.account -ProjectDir (Get-TeamProjectDirName -Path $Started.Where) -SessionId $rs.id) -Role <başlatılacak koşunun rolü: $Again.Role> -SessionRole $rs.role -Part $rs.part`.
   İki rol AYRI kaynaktan gelir: `-Role` başlatılacak koşudan, `-SessionRole` kartın `run_session.role` alanından.
   İkisine aynı değeri (`$Started.Role`) vermek rol denetimini her zaman tutturur, denetleyici işçinin oturumunu sürdürebilir hale gelir - YAPILMAZ.
   `run_session` yoksa (eski kart) `-RunSession ""` -> 'fresh'.
   'resume' ise `$r = Get-TeamResumeArgs -Plan $plan -Model $Again.Model` ve `Start-RoleRun ... -Prompt $r.Prompt -ResumeArguments $r.Arguments`;
   'fresh' ise bugünkü satır. Her iki durumda `Add-CycleNote -List "risks"` değil, kartın rapor satırına `$plan.Reason`.
   Çağıranlar (1261, 1370) `-Done` geçirmeli (bugün yalnız `$Started`, `$Again`).
4. `scripts/team/cycle.ps1` kart kapanışı (merged/stopped yazılan yerler) - `Remove-TeamRunSessionFiles -Paths <run_sessions'tan Get-TeamSessionFilePath> -AccountDirs <hesap havuzu dizinleri>`;
   bu çağrı `<uuid>.jsonl` ile birlikte yanındaki `<uuid>\` klasörünü (`tool-results\`) de siler, `.jsonl` hiç yazılmamış olsa bile; ayrı çağrı gerekmez.
   `Removed` + `RemovedDirs` sayısı kartın kapanış satırına, `Refused` risk notuna. Geçiş dönemi: bugüne kadar `--no-session-persistence`
   ile birikmiş `tool-results` klasörlerinin (`.jsonl`'siz, kimlikleri kayıtsız) temizliği bu satırın işi DEĞİL; bağlama kartı
   bunu ayrı bir tek seferlik adım olarak (hesap dizinlerinde `projects\<ekip worktree klasörü>\<uuid>\`) önerir.
5. `scripts/tests/team-cycle.tests.ps1:674` - `Assert-True ($line -match "--no-session-persistence") -Because "a fresh run"` ->
   `Assert-True ($line -match "--session-id [0-9a-f-]{36}( |$)") -Because "a kept, named session"` ve
   `Assert-True ($line -notmatch "--no-session-persistence")`; yeni vaka: `-ResumeArguments` verilince `--resume` var, `--session-id` yok, tek `--model`.
6. `scripts/quality-gate.ps1:613-615` (team-board adımının yanına aynı biçimde "team-resume tests" adımı) ve `.github/workflows/ci.yml:238` (aynı `powershell -NoProfile -File scripts\tests\team-resume.tests.ps1 && ^` satırı).
7. Ofis: kartın `run_session.part > 1` iken 'devam ediyor (<part>. parça)' etiketi (services/api ofis özeti + web).

## Kanıt

PROVEN_AUTOMATED: `scripts/tests/team-resume.tests.ps1` (12 vaka, TEMP kum havuzu), 7 mutasyon RED (resume dalı kapalı,
eşik 0, rol denetimi kapalı, yol sınırı kapalı, `-lt` -> `-le` (tam 600 sn), boş-rol denetimi kapalı, oturum klasörü silmesi kapalı).
PROVEN_REAL: YOK (bağlama kartı girince sonraki gerçek limit olayında döngü raporunda 'devam etti (oturum ...)').
