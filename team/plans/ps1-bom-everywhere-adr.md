# ADR (taslak, numarayı lead verir): Türkçe/non-ASCII içeren her .ps1 BOM taşır - liste değil kural

Tarih: 2026-10-04. Kart: ps1-bom-everywhere (öneri team/proposals/2026-10-03-bozuk-turkce-kapida-dursun.md, bölüm 2).

## Bağlam
Windows PowerShell 5.1, BOM'suz bir .ps1'i ANSI okur. Em dash'in üçüncü baytı kapanış tırnağı olur
(dosya parse edilmez); Türkçe çıktı "YÃ¶neticisi" olur (2026-10-03: 28 ekip kaydı bu yüzden bozuldu).
Bugüne kadarki koruma team-cycle.tests.ps1 içindeki SABİT bir dosya listesiydi: listede olmayan yeni
bir betik korunmuyordu.

## Karar
1. **Kural, liste değil.** `scripts/tests/script-syntax.tests.ps1` her `scripts/**/*.ps1`'i zaten
   özyinelemeli geziyor ve `scripts/quality-gate.ps1` onu zaten çalıştırıyor; kural buraya eklendi
   (yeni bir dosya kapıya bağlanmadıkça çalışmazdı). Baytlarından biri > 127 olan dosya EF BB BF ile
   başlamıyorsa FAIL: `<yol>: non-ASCII text without a byte-order mark - Windows PowerShell 5.1 reads
   it as ANSI (save as UTF-8 with BOM)`. Kontrol dosya parse edilse de edilmese de çalışır; parse
   hatası da varsa iki neden birlikte yazılır.
2. **Önce kendini kanıtlar.** $env:TEMP altında yeni bir klasöre (finally'de silinir, repo'ya asla)
   prob dosyaları yazılır: BOM'suz 'ö' -> yakalanır; BOM'lu 'ö' -> kabul; BOM'suz saf ASCII -> kabul;
   BOM'suz em dash -> yakalanır.
3. **Kendi kendini küçülten muafiyet.** Kuralın geldiği gün BOM'suz olan dosyalar testin başındaki
   tek `$bomWaiver` listesinde (gerekçe: `BOM-less on 2026-10-04; fixed by card bom-fix-offenders`).
   Listedeki bir dosya artık BOM taşıyorsa, artık non-ASCII içermiyorsa ya da yoksa FAIL:
   `waiver no longer needed: remove <yol>`. Kontrol `Get-BomWaiverProblems` fonksiyonudur; prob onu
   geçici klasördeki BOM'lu / ASCII / olmayan dosyalarla sürer.
4. `script-syntax.tests.ps1`'in kendisi em dash taşır; BOM'la kaydedildi (muafiyette değil).

## Bulunan ihlaller (53 dosya, muafiyette; + script-syntax.tests.ps1 bu kartta düzeltildi)
scripts/: bootstrap-owner-credential, complete-device-enrollment, dev-broker, dev-down, dev-up,
e2e-m1-device, e2e-m13-research, finalize-qualification, install-device-service, qualify-device,
quality-gate, repair-device-material, rotate-owner-credential, verify-device-service;
scripts/browser/: enroll-owner-chrome, real-browser-smoke;
scripts/cloud/: breakglass-ssh, migrate-agent-to-cloud, new-vapid-key, provision;
scripts/core/: evolution-advance, owner-m18-eye, probe-window-by-name, qualify-item28-unlocked,
qualify-pc-production, voice-routing-qualification;
scripts/lib/: AgentUpdate, BrowserProvision, BrowserSmokeEvidence, Deployment, DevBroker, HttpJson,
IdentityStatus, InstallAcl, InstallEvidence, NativeProcess, ServiceInstall, VoiceShell;
scripts/tests/: agent-release-currency, cloud-release-bluegreen, cloud-secret, identity-restore,
installer-acl, installer-browser, installer-deploy, installer-invocation, installer-strictmode,
machine-readable, make-ocr-fixture, provision, utf8-json (.tests.ps1 / .ps1);
scripts/voice/: fetch-benchmark, tts-loopback-qualification.
(scripts/team altında ve team-*.tests.ps1'de ihlal YOK - BOM'lu ya da saf ASCII.)

## Sonuç ve takip
- team-cycle.tests.ps1'deki sabit liste testi (~517-535) artık gereksiz; bu kart ona dokunmadı.
- Lead'in açacağı takip kartı **bom-fix-offenders**: muafiyetteki her dosyaya BOM ekle, `$bomWaiver`'ı
  boşalt (kendini küçülten kontrol her eklemede zorlar), team-cycle.tests.ps1'deki sabit liste testini sil.
