## Şu an üzerinde çalışılan
`release-lock-waits` (worker, döngü d20261002) — alan: `scripts/cloud/release-cloud-core-bluegreen.sh`, `scripts/tests/cloud-release-bluegreen.tests.ps1`, `team/plans/release-lock-waits-adr.md` — makine: ev PC'si, worktree `.claude/worktrees/team/d20261002/worker-release-lock-waits`. İş bitti; HANDOFF'a dokunulmadı.

## Rapor
- **sha:** `2cceeb844c6c7ae40ab104f0672a701b2c4035d2`, dal `team/d20261002/worker-release-lock-waits`, push edildi, worktree temiz.
- **Dosyalar:** 3, hepsi alan içinde (betik +34, test +122/−7, ADR metni yeni).
- **Yapılan:**
  - Yayın, preflight ve rollback önce `flock -n 9` sorar; kilit tutuluyorsa stderr'e bir kez `waiting for the release lock (held by another operation), up to N s` yazar ve `flock -w N 9` ile bekler.
  - Bekleme dolarsa 82 verir: `... is still running after waiting N s; retry later`.
  - `PAGENTOS_LOCK_WAIT_S` varsayılan 45; 0 dünkü davranış ve dünkü mesajdır.
  - 0..600 tam sayı olmayan değer (`045` dahil) kilit dosyası açılmadan **exit 64** ile reddedilir. Kart kod adı vermiyordu; 64 bu betikte boştu.
  - `--reconcile` kipi `flock -n` olarak kaldı ve değişkeni hiç okumaz. Betik reconcile olduğunu yalnızca kipinden bilir (zamanlayıcının `reconcile.sh --reconcile` biçimi ve `SHA --reconcile`).
- **Stage-only:** `release-cloud-core.ps1 -StageOnly` bu betiği hiç çağırmıyor (kilit almaz); değiştirilecek yol yok, ADR'de yazılı.
- **Sahte flock:** kilidin tutulma süresi `host-snapshot.json`'dan gelir (2 s). Her yayın/preflight/rollback vakası artık reconcile'a rastlar. Reconcile vakalarında varsayılan boş kilittir (tutan kendisi); tutulmuş kilit açıkça verilir. Çağrılar `state/flock.log`'a yazılır.
- **RED → GREEN (PROVEN_AUTOMATED):**
  - Temel koşu (değişiklik öncesi): 85/85.
  - Eski betik + yeni testler, tam paket: ilk preflight `exit 82`, 0 PASS / 6 FAIL, paket çöküyor.
  - Eski betik, adlı vakalar (kısaltılmış kopya, varsayılan kilit boş): 25 PASS / 14 FAIL — 3 s vakaları, "bekleme sonrası 82", 600 sınırı, yedi geçersiz değer.
  - GREEN, tam paket, worktree: **112 passed, 0 failed** (27 yeni doğrulama).
- **Mutasyonlar** (geçici kopyalarda; worktree dosyası hiç bozulmadı, sha256 önce = sonra `e727f29b…` betik, `ae3d8e61…` test):
  - M1, bekleme kaldırıldı (`if true`): tam pakette 0 PASS / 6 FAIL; adlı vakalarda 7 FAIL. **RED.**
  - M2, reconcile bekletildi (`if true`): 8 FAIL, sekiz reconcile vakasının tamamı. **RED.**
- **Komşu paketler** (son betikle): `cloud-release` 38/0, `maintenance-reboot` 35/0, `host-snapshot` 95/0.
- **Python** (betiğin kaynağını okuyanlar): `test_release_exit_codes` + `test_systemd_onfailure_units` 20 passed; recovery-supervisor `test_systemd_install` 33 passed, 4 skipped. `bash -n` temiz.
- **NOT_RUN:**
  - Gerçek util-linux `flock` (aynı fd'de `-n` ardından `-w`): atlanan 4 test Linux'a özel çekirdek kilidi testleri. Sahte flock uyumaz, aritmetikle cevap verir.
  - PROVEN_REAL: preflight'ı reconcile'a rastlayıp geçen ilk yayın.
  - Tam API birim paketi koşulmadı (yalnız dokunulan betiği okuyan dosyalar).
- **Karardan sapma:** RED ve mutasyon koşularının adlı vakaları, tam paketin satır seçimiyle kısaltılmış kopyasında (baş vakalar + kilit bölümü) koşuldu. Tam paket bu makinede 25 dk sürüyor; GREEN tam pakettir.
- **Açık riskler:**
  - Fixture `collected_at` 2026-10-01 19:18 UTC, son yayından (23:50) eski — denetleyici kuralı gereği lead yenisini toplamalı.
  - Paketin `Reset-Host` varsayılan rengi hâlâ sabit `blue` (bugün snapshot ile aynı); yalnız kilit fixture'dan alındı.
  - Sabitlenmiş kurtarma kopyası (`/opt/pagentos-recovery/reconcile.sh`) aynı dosya: sha256'sı değişir, bu yayında pin yenilenmeli (sahibin adımı).
  - 82 mesajı ölçülen süreyi değil yapılandırılmış sınırı söyler (bilinçli; ADR'de).
- **ADR:** `team/plans/release-lock-waits-adr.md` (numarasız; ADR-0223 ek 2'ye ek olarak önerildi).
