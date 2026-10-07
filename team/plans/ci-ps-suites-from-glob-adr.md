# ADR (taslak, numarasız): CI PowerShell paketleri glob ile bulunur

Kart: ci-ps-suites-from-glob (cycle d20261006). Öneri: team/proposals/2026-10-06-kayit-dosyalari-kendiliginden.md, "Nasıl" 4.

## Bağlam

`.github/workflows/ci.yml` içindeki "PowerShell 5.1 script suites" adımı 51 paketi elle, `&& ^` zinciriyle
sayıyordu. Her yeni paket aynı yere bir satır ekliyordu; paralel iki dal entegrasyonda orada çakıştı
(6 Ekim, run-temp-keeps-git-bash-tmp, ci.yml 238).

## Karar

1. Adım `scripts\tests\*.tests.ps1`'i `Get-ChildItem ... | Sort-Object Name` ile bulur ve her paketi
   `& powershell.exe -NoProfile -File` ile tek tek koşar.
2. **5.1 motoru korunur.** Paketler `powershell.exe` (Windows PowerShell 5.1) ile koşar; adımın kendi kabuğu
   `shell: powershell` (o da 5.1; `pwsh` değil). Sebep adımın üstündeki yorumda: dört gerçek nitelendirme
   hatası yalnız 5.1'deydi.
3. **Hepsi koşar, sonda liste.** İlk kırmızıda durmak yerine her paket koşar; kırmızılar `=== RED <ad> (exit N)`
   ile yazılır, sonda "Red PowerShell suites:" listesi ve `exit 1`. Bir kırmızı sonrakilerin bilgisini
   saklamasın diye. `$ErrorActionPreference = 'Continue'`: Actions'ın başa koyduğu `'stop'` bir çocuğun
   stderr'i yüzünden döngüyü erken bitirmesin (yerelde 5.1'de stderr + Write-Error + exit 3 yazan sahte
   paketle denendi: diğerleri koştu, adım 1 ile bitti).
4. **İstisna tablosu** adımın içinde: `$notInCi = @{ 'ad.tests.ps1' = 'neden' }`. Bugün boş (bugünkü 51 paketin
   hepsi koşuyordu; glob aynı 51'i buluyor). `test_ci_covers_every_suite.py` bu tabloyu okur ve
   `PS_SUITES_NOT_IN_CI` ile aynı küme olmasını ister - biri tek başına değişirse kırmızı.
5. `PS_SUITES_LIST_ONLY=1` ortam değişkeni adımı kuru koşuya çevirir (yalnız `=== RUN <ad>` yazar). CI bunu
   hiç kurmaz; yerel doğrulama içindir.
6. Sözleşme testi iki biçimi de okur: glob biçiminde "eksik paket" glob'un kapsadığı küme (eksi istisna
   tablosu) üzerinden; elle listeli eski biçim (`-File scripts\tests\<ad>`) yine tanınır, geri dönüş yolu
   açık. Sayım artık yalnız adımın bloğuna bakar - yorumlarda geçen bir ad "koşuluyor" sayılmaz.

## Sonuçlar

- Yeni paket = yalnız yeni dosya; ci.yml'e dokunulmaz, paralel dallar orada çakışmaz.
- Koşu sırası alfabetik oldu (eski listede `script-syntax` başta idi). Paketler birbirine bağlı değil;
  hepsi koştuğu için sıra sonucu değiştirmez.
- `scripts/quality-gate.ps1`'deki aynı elle liste bu kartın dışında: dosya gate-unit-parallel'in alanında.
  O iş bitince Proje Yöneticisi ayrı kart keser (önerinin registry-ps-suites kartının gate yarısı).
