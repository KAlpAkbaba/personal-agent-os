## Şu an üzerinde çalışılan
- **İş:** `proposals-on-cloud-core` (geri dönen iş: "alan dışı dosya")
- **Alan:** `services/api/app/team/{store,routes,approvals,models}.py`, dört test dosyası, `team/plans/proposals-on-cloud-core-adr.md`
- **Makine:** sahibin geliştirme PC'si, ağaç `.claude/worktrees/team/d20261001/worker-proposals-on-cloud-core`

## Sonuç
Dal artık alan dışında hiçbir dosyayı değiştirmiyor, ama bunun bedeli olarak iki mevcut birim testi bu dalda KIRMIZI. İkisi de yalnızca alan dışındaki dosyalarda düzeltilebilir; iş bu haliyle alanın içinde tamamlanamıyor.

- **sha:** `db480b227d512fe7af2597ef217a22ae1eff7301` (push edildi, uzak dal aynı, ağaç temiz)
- **Geri dönüş nedeni:** `1df514f0` commit'i `test_team_approvals.py` ve `test_team_state.py` dosyalarına dokunuyordu. Revert commit'i ile geri aldım; geçmiş yeniden yazılmadı.
- **Değişen dosyalar:** `git diff --name-only team/nightly/lead...HEAD` 9 dosya veriyor, hepsi alanın içinde.
- **Ürün kodu:** değişmedi. Bu turda yalnız revert ve ADR metnine bir bölüm eklendi.

## Kırmızı kalan iki test (lead'in kararı gerekiyor)
1. `test_team_state.py::test_every_route_and_body_field_the_powershell_client_uses_is_one_the_server_has` — `POST /v1/team/queue/proposals` sunuluyor, `TeamQueue.ps1` henüz çağırmıyor. Kart `read_by_others` istisnasını istiyor, ama o sözlük alan dışındaki bu dosyada.
2. `test_team_approvals.py::test_a_decision_is_refused_while_a_cycle_holds_the_lock_on_both_stores[db]` — sahibin 2026-10-01'de değiştirdiği kuralı (DbStore'da 409 `cycle_running`) tutuyor. Yeni kural bunu 200 yapıyor.

**Lead için çözüm:** entegrasyon dalında `git cherry-pick 1df514f0` iki düzeltmeyi de uygular (commit dalda hâlâ erişilebilir). Alternatif olarak kartın alanına bu iki dosya eklenip iş geri gönderilebilir. `researcher-every-cycle` önce inerse birinci istisnaya gerek kalmaz; şu an `team/nightly/lead` üzerinde o çağrı yok. Ayrıntı ADR metninin son bölümünde.

Rotayı sözleşme testinin okumadığı ayrı bir router'dan sunarak testi yeşile çevirmedim; bu, rotayı testten saklamak olurdu.

## Kanıt (bu sha üzerinde koşuldu)
- **Birim, ekip dosyaları** (PROVEN_AUTOMATED): 2 başarısız, 208 geçti. Başarısızlar yukarıdaki ikisi. Kapsam: `test_team_approvals`, `test_team_state`, `test_team_proposals`, `test_team_approvals_while_running`, `test_postgres_coverage_ratchet`.
- **Gerçek PostgreSQL, dev yığını** (PROVEN_AUTOMATED): 10 geçti (5 öneri, 2 onay, 3 mevcut `team_state`).
- **ruff check + format:** temiz.
- **KIRMIZI→YEŞİL ve 7 mutasyon:** önceki turda `678877e0` üzerinde kanıtlandı; ürün kodu o commit'ten beri aynı. Bu turda yeniden koşulmadı → bu tur için NOT_RUN.

## Yapılmayan
- Tam birim paketi (~5400 test): NOT_RUN.
- PowerShell paketleri: NOT_RUN (ps1 dosyasına dokunulmadı).
- mypy: NOT_RUN (venv'de kurulu değil).
- PROVEN_REAL: yayın sonrası sahibin adımı, READY_FOR_OWNER.

## Açık riskler
1. Üretimde bekleyen fikirlerin metni yalnız ev PC'de; `researcher-every-cycle` inene ya da lead bir kez elle POST edene kadar boş görünür.
2. Web sayfası düğmeleri hâlâ `cycle_running` ile kapatıyor; `decisions_open` okumalı (`approvals-detail-view`).
3. `stale_write` ile reddedilen karar defterde iz bırakır (ADR-0217 sırası); döngü sırasında karar açılınca daha sık olabilir.
4. `scripts/team/fake-team-api.ps1` yeni rotayı bilmiyor (alan dışı, kardeş işin).

ADR metni: `team/plans/proposals-on-cloud-core-adr.md` (numarasız).
