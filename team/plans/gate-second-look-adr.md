# ADR (taslak, numarayı lead verir): Kırmızı kapıda ikinci bakış — sınıflayan motor

Kart: `gate-second-look` (d20261004). Öneri: `team/proposals/2026-10-04-kirmizi-kapida-ikinci-bakis.md` (sahip onayladı).
Entegrasyon planı: `team/plans/gate-second-look-integration.md` (ADAPT, yeni bağımlılık yok).

## Karar

Kırmızı bir kapının log'undan düşen testleri okuyup her birini sayılarla sınıflayan bir motor kuruldu, kapıya
BAĞLANMADI (bağlama ayrı kart, metni aşağıda):

- `scripts/lib/TeamGateSecondLook.ps1`
  - `Get-GateFailedTests -LogPath [-GateScript]`: bölüm kuralı `Read-TeamGateLog` ile aynı (`'^=== (.+) ===\s*$'`, BOM
    kırpılır; bölümde `FAILED: ` ya da özet tablosunda `FAIL` = kırmızı adım). Tanınanlar: pytest `FAILED|ERROR
    <dosya>::<test>` (`[param]` kesilir: bir test, tüm parametreleri), vitest `FAIL  <dosya> > ... > <ad>`, repo'nun
    PS koşucusu `  FAIL  <vaka>` ve Pester `[-] <vaka>`. PS adımının `.tests.ps1` dosyası `quality-gate.ps1`'in
    kendisinden okunur (elle yazılmış ikinci liste yok). Tanınmayan kırmızı adım (gizli anahtar, dotnet, …) ->
    `id=''`, `paket='tanınmadı'`, sınıf asla verilmez.
  - `Get-GateRerunCommand`: TEK tablo (`$script:GateRerunTable`), paket -> `tek` / `dosya` / `sira` / `bolme` komutları,
    `cwd`, `db`, test-slot türleri. Her alt süreçten `PAGENTOS_TEST_SHARD` SİLİNİR (`services/api/tests/conftest.py`
    bu değişkenle takımı böler; seçilmeyen test "geçti" sanılırdı).
  - `Get-GateRunOutcome`: bir koşunun sözü `gecti | dustu | hata`. Geçmek OLUMLU kanıt ister: hedefin KENDİ satırı
    (pytest `-rfEp` ile `PASSED <id>[param]`, vitest `--reporter=verbose` tik satırı, PS'de `PASS  <vaka>` / `[+]`).
    "4 passed, 1 skipped" ve atlanan hedef -> `hata` (kararsız sayılmaz). Seçilmeyen/zaman aşan/okunamayan koşu
    `hata`dır, asla `gecti` değil.
  - `Get-GateRedClass` (SAF): sayılar -> `gercek | kararsiz | siraya_bagli | yarim`. Başka değer yok, `yesil` yok.
  - `Find-GatePolluter -Candidates -Target -Invoke [-Deadline]`: ikiye bölme. 16 dosya: 1 doğrulama + 4 bölme = 5 koşu.
- `scripts/team/gate-second-look.ps1 -LogPath -Worktree [-MainWorktree] [-BudgetMinutes 20] -OutFile [-DatabaseUrl]
  [-TestSlotStore] [-Invoke] [-GateScript]`: her test 5 tek başına + 5 dosya sırası + (main verildiyse) 5 main ucunda;
  sonuç JSON: `{surum, durum: tamam|yarim, ozet, testler:[{id, paket, adim, dosya, sinif, sebep, sayilar:{tek,dosya,main:
  {gecti,dustu,hata,n}}, kirleten, main_de_de}]}`. Çıkış 0 = JSON yazıldı (ne derse desin).

## Sınıf kuralları ve eşikler (N = 5, `$script:GateSecondLookRuns`)

Girdi: `tek`, `dosya`, `main` = geçen/5; bir sayı yalnız 5 koşunun hepsi söz verdiyse kullanılır (`hata` > 0 -> bilinmiyor).

1. Bütçe aşıldı -> `yarim` (sınıf yok). Takımda düştüğü bilinmiyor -> `yarim`. `tek` ve `dosya` ikisi de yok -> `yarim`.
2. `tek` ya da `dosya` `$FlakyMinPasses (=1) <= geçen < 5` -> `kararsiz`. Eşik TEK değişkende.
3. `tek = 5`: `dosya = 0` -> `siraya_bagli`, `yer=ayni_dosya` (kirleten "aynı dosyada"; dosyalar arası bölme yapılmaz);
   aksi -> `siraya_bagli`, `yer=onceki_dosyalar` -> pytest paketlerinde ikiye bölme.
4. Bilinen sayıların hepsi 0 -> `gercek`.
5. Geri kalan (bir koşu türünde hep geçti, diğerinde hep düştü; ya da tek başına koşu yok ve dosyada 5/5 geçti ama
   kapıda düştü) -> `kararsiz` ("bazen geçer, koşu türüne göre").
- `main_de_de = true` <=> main ucunda koşuldu ve en az bir kez düştü (`main < 5`): "bu dalın suçu değil".
- `-Filter` almayan PowerShell takımında (44 dosyadan 35'i) tek başına koşu YOK: `tek` bilinmez, kural 3 uygulanamaz,
  `siraya_bagli` verilmez; o paket yalnız dosya sırasıyla `gercek`/`kararsiz` alır.
- Kirleten alanı: `bulundu` -> dosya yolu; `bulunamadı` (bütün önceki dosyalarla düşmedi); bütçe bölmenin ortasında
  biterse `yarım: aday aralığı N dosya` (daraltılmış aralık da işe yarar). Bölme, kirletenin TEK dosya olduğunu varsayar;
  iki dosyanın birlikte kirlettiği durumda yanlış yarıyı seçebilir — sonuç yine "aday", karar değil. Testin dosyası
  toplamada ilkse (önceki dosya yok) ya da toplama onu hiç adlandırmadıysa bölme yapılmaz, kirleten `bulunamadı (…)`;
  bir testteki beklenmedik hata yalnız o testi `yarim` yapar, JSON her durumda yazılır.

## Neden yeniden koşup yeşil saymıyoruz

Kırmızı kapı kırmızı kalır. Motorun çıktı kümesinde yeşil yok (Pester ızgara testi 7×7×7×2×2 = 1372 girdiyi gezer).
`kararsiz` "bu test güvenilmez" demektir, "dal temiz" değil: kararsız bir testin geçtiği koşu hiçbir şeyi kanıtlamaz.
`pytest-rerunfailures` (MPL-2.0) tam da bunu yapar (yeniden koş, geçeni yeşil say) ve kararsızlığı gizler — KULLANILMAZ.
Kararsız ama main'de de kararsız bir testin dalı durdurmaması ayrı bir kural kararıdır (sahibe sorulacak; bu kart değil).

## detect-test-pollution neden eklenmedi

`detect-test-pollution` (asottile, MIT, 1.2.0, 2023-09-28) yalnız REFERANS. Eklenmedi çünkü: (1) servise bir dev
bağımlılığı olur, `uv.lock` değişir ve iş sahibe gider; (2) kendi `pytest` çağrısını yapar, koşturucu dışarıdan
verilemez (`-Invoke` yok) — Pester süreç başlatmadan sınayamaz; (3) bütçeyi, test-slot sırasını ve kapının kendi
veritabanı kuralını bilmez. Algoritması (önceki dosyaları yarıya bölmek) ~30 satır PowerShell. Yeni paket yok:
`pyproject.toml`, `uv.lock`, `package.json` değişmedi; THIRD_PARTY girdisi gerekmez.

## Sınırlar

- Bütçe 20 dk (varsayılan); bitince kalan testler `yarim`, özet "ikinci bakış yarım kaldı (…)". Koşu süreç ağacıyla
  (`taskkill /T /F`) kalan bütçede kesilir.
- Test sırası: koşudan önce `test-slot.ps1 ask -Kind heavy[,database] -Task gate-second-look -Role gate`; ONAY ->
  `Start-TestSlotRun -HolderPid $PID`, sonda `Complete-TestSlotRun`. BEKLE (çıkış 3) -> `yarim` ve `ask`'ın bıraktığı
  bekleyen kayıt `Remove-TestSlotTicket` ile geri verilir (ikinci bakış beklemez; sırayı tutmaz). Kuyruk bozuk
  (çıkış 5 ya da başka) -> `yarim`: bu, kapının "kuyruk nezakettir, bozuk kuyruk adımı durdurmaz" kuralından BİLEREK
  ayrılır — kart "asla sırasız koşmaz" diyor ve ikinci bakış kapı değil, ertelenebilir bir teşhis.
- Veritabanı: `db=true` paket (API integration) yalnız `-DatabaseUrl` ile koşar (alt sürece `PAGENTOS_DATABASE_URL`).
  Verilmezse test `yarim` ("kapının kendi veritabanı verilmedi"), koşucu hiç çağrılmaz — paylaşılan dev veritabanı
  (`config.py` varsayılanı) asla kullanılmaz. `gate-own-database` main'e girince bağlama kartı onun URL'sini geçer.
- Ayak izi (tahmin): tek pytest koşusu ~300-600 MB, 5-15 sn; düşen test başına 15 koşu ≈ 1-4 dk; bölme ≈ önceki
  dosyaların bir kez koşma süresi. Gerçek birim takımında ("kırk dakika") bölme 20 dk'yı aşabilir -> aralık yazılır.

## Bağlama kartı (tam metin — bu kartta YAPILMADI)

- id: `gate-second-look-wire`
- alan: `scripts/team/integrate.ps1`, `scripts/lib/TeamIntegrate.ps1`, `scripts/tests/team-integrate.tests.ps1`
- iş:
  1. `integrate.ps1`'de kırmızı dalında `Set-GateVerdict -Tasks $tasks …` satırından (bugün ~796) HEMEN SONRA,
     `$Outcome.Result = "kapı kırmızı"`'dan önce:
     ```powershell
     $secondLookFile = Join-Path $Item.Directory "gate-$number-second-look.json"
     $look = Invoke-TeamGateSecondLook -LogPath $logPath -Worktree $tree -MainWorktree $mainTree -OutFile $secondLookFile
     ```
     `Invoke-TeamGateSecondLook` (TeamIntegrate.ps1'de yeni, ~15 satır): `powershell -NoProfile -File
     scripts\team\gate-second-look.ps1 -LogPath … -Worktree … [-MainWorktree …] -BudgetMinutes 20 -OutFile …
     [-DatabaseUrl <kapının kendi DB'si, varsa>]` çalıştırır, JSON'u okur; HER hata yutulur ve `$null` döner
     (ikinci bakış kapının kararını, `return 6`/`return 8`'i, iki kırmızıda durmayı ASLA değiştirmez). `$mainTree`:
     kapının `Reset-TeamGateWorktree` yolu gibi `refs/heads/$Base` ucunda ayrı bir detached worktree; yoksa parametre
     verilmez (main sayısı `yok`).
  2. Sonuç `gate-$number.json`'a `Write-TeamGateRecord` ile `ikinci_bakis` anahtarıyla yazılır:
     `{ ozet, durum, dosya: "gate-$number-second-look.json", testler: [{id, paket, sinif, sayilar, kirleten, main_de_de}] }`.
  3. `sinif` `kararsiz` ya da `siraya_bagli` olan her test için kuyruğa kart (bugünkü kart açma yolu, `New-TeamTask`
     benzeri): id `gate-red-<kısa test adı>`, başlık "Kapı kırmızısı: <sınıf> test <id>", hedef metninde sayılar
     (`tek başına a/5, dosya sırasında b/5, main'de c/5`), `kirleten` ve log yolu; aynı test için açık kart varsa yenisi
     açılmaz. `gercek`, `yarim` ve tanınmayan adım kart AÇMAZ (bugünkü geri verme yolu sürer).
  4. `[void]$Outcome.Lines.Add($look.ozet)` — Ofis'in ve döngü raporunun okuduğu TEK satır
     ("kapı kırmızı: kararsız test X (tek başına 3/5, dosya sırasında 2/5, main'de 2/5)").
- değişmeyen: kapının adımları, kırmızı/yeşil kararı, `-ClearGateStop`, iki kırmızıda durma, `team/guards.json`.
- kabul: team-integrate.tests.ps1'de sahte kapı + sahte ikinci bakış (betiğin `-Invoke`'u yok; Invoke-TeamGateSecondLook
  bir betik yolu parametresi alır, test sahte betik verir): kırmızı kapıda `gate-<n>.json`'da `ikinci_bakis.ozet`,
  kararsız sınıfta kuyrukta kart, ikinci bakış patlasa da çıkış 6/8 ve kayıt aynı.
- kanıt: PROVEN_REAL bir sonraki gerçek kırmızı kapının `gate-<n>.json`'unda.

## Kanıt (bu kart)

PROVEN_AUTOMATED: `scripts/tests/team-gate-second-look.tests.ps1` (22 vaka; fixture log'lar elle, `quality-gate.ps1`
biçiminden kuruldu — diskte gerçek kırmızı kapı log'u yoktu; gerçek pytest ile TEMP kum havuzunda ikiye bölme; mutasyon
RED: eşik 0, bölmede ters yarı). PROVEN_REAL yok.

## Geri alma

4 yeni yol + `scripts/tests/fixtures/gate-second-look/` silinir; mevcut hiçbir dosya değişmedi.
