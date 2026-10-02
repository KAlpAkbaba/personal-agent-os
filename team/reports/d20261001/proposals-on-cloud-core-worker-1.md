## Şu an üzerinde çalışılan
`proposals-on-cloud-core` (d20261001) — alan: `services/api/app/team/{store,routes,approvals,models}.py` + 4 test dosyası + ADR metni — makine: ev PC (dev yığını PostgreSQL). Durum: bitti, itildi, ağaç temiz.

## Rapor
- **sha:** `1df514f06e8fb5c47bf6c9a7e84ec4c6eeb77427` (alan içi iş: `678877e0e0b61d6c248389e0668ff15ba208c072`), dal `team/d20261001/worker-proposals-on-cloud-core`, itildi.
- **Dosyalar:** 11. Dokuzu alan içinde (4 uygulama, 4 yeni test, ADR metni), ilk commit'te.
- **ALAN DIŞI, 2 dosya, ayrı commit (`1df514f0`):** lead birleştirmede alabilir ya da yeniden yazabilir.
  - `tests/unit/test_team_state.py`: kartın istediği `read_by_others` muafiyeti (`POST /v1/team/queue/proposals`, gerekçesiyle; `researcher-every-cycle` silecek).
  - `tests/unit/test_team_approvals.py`: "iki depoda da cycle_running 409" diyen eski test, sahibin değiştirdiği kuralı tutuyordu. Artık FileStore'da 409, DbStore'da 200 bekliyor. Bu düzenleme kartta yoktu; yapmasam birim paketi kırmızı kalırdı.

**Yapılan**
- **Öneri metni:** `put_proposal` / `read_proposal` iki depoda. DbStore `team_state` içinde `kind='proposal'` satırı tutar; yeni tablo ve migration yok. Ad deseni tam eşleşir ve en çok 80 karakterdir (genişlik modelden okunur); 81 karakter 422, kesme yok.
- **Rota:** `POST /v1/team/queue/proposals` → `{ok: true}`; ikinci gönderim metni değiştirir.
- **Okuma:** `proposal_text` depodan gelir; `team/` altındaki dosya yalnız FileStore'da yedektir.
- **Döngü koşarken karar:** `approvals.decisions_open()` hem listenin söylediği hem `decide()`'ın uyguladığı tek kuraldır. DbStore'da hep açık; FileStore'da `cycle_running` reddi aynen duruyor. GET artık `decisions_open` döndürür, `cycle_running` bilgi olarak kalır. Karar yanıtı `cycle_running` ve "bir sonraki döngüde uygulanır" mesajını taşır.
- **Yolda bulunan hata:** rapor adı sondaki `\n` ile geçiyordu (`$`); `fullmatch` yapıldı, regresyon testi eklendi.

**KIRMIZI → YEŞİL** (PROVEN_AUTOMATED)
- Birim, uygulamadan önce: 100 başarısız / 5 geçti. Sonra iki yeni dosya yeşil; ekip, wiring, ratchet ve route/identity testleriyle birlikte **669 geçti, 1 atlandı**.
- Gerçek PostgreSQL (dev yığını), eski kodla:
  - öneriler: `ImportError` (depo yöntemi yok);
  - onaylar: 2 başarısız (`KeyError: 'decisions_open'`; `cycle_running` ≠ `stale_write`).
- Gerçek PostgreSQL, yeni kodla: **10 geçti** (5 öneri + 2 onay + 3 mevcut). Kapsananlar: put/oku/değiştir, 80 ve 81 karakter sınırı, PostgreSQL'in 81'i kendisinin de reddettiği, kilit tutulurken onay ve ret, araya giren yazımda 409. Testten sonra dev veritabanında `team_state` kalıntısı yok.

**Mutasyon** (7'si de KIRMIZI; her biri yedekten geri yüklendi, sha256 önce/sonra aynı: store `a6496184…`, approvals `390ca033…`)
- M1 depo okuması yerine dosya okuması → DbStore'da KIRMIZI (birim + PostgreSQL).
- M2 FileStore reddi kaldırıldı → KIRMIZI.
- M3 DbStore açılışı kaldırıldı → KIRMIZI (birim + PostgreSQL).
- M4 80 karakter denetimi kaldırıldı → KIRMIZI (birim + PostgreSQL).
- M5 `fullmatch` → `match`; M6 DbStore'da dosya yedeği; M7 ikinci put değiştirmiyor → KIRMIZI.

**Hızlı denetimler:** ruff check + format temiz. mypy venv'de kurulu değil → NOT_RUN.

**Yapılmayan**
- Tam birim paketi (~5400 test) NOT_RUN: yalnız ekip ve çevresi koşuldu.
- PowerShell paketleri NOT_RUN: ps1 dosyasına dokunulmadı.
- PROVEN_REAL (sahip, ev PC kapalıyken metni okuyup döngü koşarken onaylar): yayın sonrası, READY_FOR_OWNER.

**Açık riskler**
1. Üretimde bekleyen fikirlerin metni hâlâ yalnız ev PC'de. `cycle.ps1` gönderene kadar (`researcher-every-cycle`) boş görünür; lead bir kez elle POST edebilir.
2. Web sayfası hâlâ `cycle_running` ile düğmeleri kapatıyor; `decisions_open` okumalı (`approvals-detail-view`). O iş inmeden sahip farkı görmez.
3. Defter olayı kuyruk yazımından önce kaydediliyor (ADR-0217). `stale_write` ile reddedilen karar defterde iz bırakır; döngü sırasında karar açılınca bu daha sık olabilir.
4. `scripts/team/fake-team-api.ps1` yeni rotayı bilmiyor (alan dışı, kardeş işin).
5. Entegrasyon testleri defterde (append-only) koşu başına iki-üç olay bırakır; kimlikler koşuya özgü.
6. Liste metni 20 000 karakterde kesmeye devam ediyor; depo tamamını tutuyor.

ADR metni: `team/plans/proposals-on-cloud-core-adr.md` (numarasız; ADR-0222 ve ADR-0217'yi değiştirir).
