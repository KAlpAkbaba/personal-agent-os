## Şu an üzerinde çalışılan
- Görev `area-widen-rules` · alan: `scripts/lib/TeamArea.ps1`, `scripts/tests/team-area.tests.ps1`, `scripts/quality-gate.ps1`, `team/plans/area-widen-rules-adr.md` · makine: ev PC, worktree `.claude/worktrees/team/d20261002/worker-area-widen-rules`.
- Durum: bitti, commit edildi, push edildi, worktree temiz. Döngüye hiçbir şey bağlanmadı; bağlama sonraki kartın işi.

## Rapor
- **sha:** `c0145539d81db66f4831c2edc128f9823b20c248` (HEAD = origin `team/d20261002/worker-area-widen-rules`).
- **Değişen dosyalar:** 4, hepsi alan içinde (`TeamArea.ps1` yeni, `team-area.tests.ps1` yeni, `quality-gate.ps1` +9 satır, ADR yeni).
- **RED önce (PROVEN_AUTOMATED):** `TeamArea.ps1` yokken `powershell -NoProfile -File scripts\tests\team-area.tests.ps1` exit=1 verdi ("…\scripts\lib\TeamArea.ps1 is not recognized"). Kırmızı hal ayrı commit edilmedi; kanıt yalnız bu koşunun çıktısı.
- **GREEN (PROVEN_AUTOMATED):** PS 5.1 altında 85 passed, 0 failed, exit=0. Vaka grupları: parse 11, widen 5, wait 16, protected 44, cap 3, idempotent 3, d20261001 2, gate 1.
- **Mutasyon RED (PROVEN_AUTOMATED):** yedek kopyadan geri yükleme, özgün sha256 `fde5aa02…195549`, üçünde de geri yükleme sonrası eşit. Mutasyonlar 84 vakalık halde koştu; gate vakası sonra eklendi, kütüphane hash'i değişmedi.
  1. Korunan yol denetimi kaldırıldı (`a60a8384…`): 43 FAIL, tüm `protected:` vakaları ve TeamQueue metnini okuyan vaka dahil.
  2. Çakışma denetimi kaldırıldı (`7466786a…`): 10 FAIL, beş durum vakası, dizin isteği ve d20261001 "wait" dahil.
  3. Tavan kaldırıldı (`0d8814d0…`): 2 FAIL (`cap: the third widening…`, `cap: a third request that would only wait…`).
- **Kapı adımı:** "Agent team area widening rules (PS5.1, no model)", roadmap feeder adımının hemen ardında, aynı biçimde. Adım, kapının kendi `Invoke-Step` / `Assert-ExitCode` işlevleriyle tek başına koşturuldu: PASS, 2,5 sn (PROVEN_AUTOMATED). Bir vaka da kapı metnini okuyup adımın kayıtlı olduğunu doğruluyor.
- **Tam kapı (38 adım): NOT_RUN.** Dev stack, masaüstü ve web kurulumu ister; lead'in dalında koşar.
- **Mevcut paketler, değişmeden:** team-cycle 145/0, team-feed 61/0, script-syntax 148 betik / 0 hata (iki yeni dosya dahil).
- **ADR:** `team/plans/area-widen-rules-adr.md` — sözleşme satırı, yargı sırası, kaynaklı korunan liste, tavan, neden kısmi genişletme yok, şema alanlarının bağlama kartıyla gelmesi.

## Karttan sapmalar (ikisi de reddetme yönünde, ADR'de yazılı)
- Depo içi olmayan yol (mutlak, sürücü harfi, `..`, joker, `.`) `Resolve-TeamAreaRequest`'e doğrudan verilirse isteğin tamamı `refuse`.
- İstenen dosyayı tutan kart zaten bu kartı bekliyorsa (doğrudan ya da dolaylı) `wait` yerine `refuse`: yoksa `depends_on` döngüsü ikisini de sonsuza dek bekletirdi.
- Ayrıştırıcı yol çevresindeki tırnakları da yok sayar (modeller JSON listesi yazıyor).

## Yapamadıklarım ve açık riskler
- **d20261001 fixture alanı gerçek değil:** `narrative-failures-only-model` kartının gerçek `area` değeri izlenen dosyalarda yok (kuyruk veritabanında); temsilî bir alan kullandım. İstenen dosya gerçek.
- **İmza doğrulama listede yok:** anayasa §6'daki "update signature verification" için güvenle adlandırabileceğim tek bir yol bulamadım. Lead bir girdi eklerse paket vakasını zorunlu kılar.
- **`.env.example` da reddediliyor** (`.env.*` deseni): bilinçli, tutucu seçim.
- **Yalnız Bad girdili istek** `Asked = true` döner ve reddedilir. Geçerli dosyaların yanında Bad girdi varsa `Resolve` bunu görmez (karttaki imzada `-Bad` yok); bağlama kartı `Bad` doluysa reddetmeli.
- **`wait` sonrası** istek yalnız `area_history`'de durur; tutan kart main'e girince bağlama kartı aynı dosyaları yeniden çözmeli. Tutan kart `stopped` olursa bekleyen kart lead'e kalır (bugünkü `depends_on` kuralı).
- `area_widenings` / `area_history` henüz `team/queue.schema.json`'da ve Cloud Core doğrulamasında yok; bağlama kartının işi. PROVEN_REAL o kartın ilk gerçek döngüsüne ait.
