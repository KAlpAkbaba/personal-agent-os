**Şu an üzerinde çalışılan:** office-data-api, alan `services/api/app/team/` + `tests/unit/test_team_office.py`, ev PC. HANDOFF'a dokunmadım.

**Sonuç:** İş bitti, tek bir kırık test dışında. sha `f26aa3c965b25529017f273c09fc9460f9743f81`, branch push edildi, worktree temiz. 5 dosya, hepsi alan içinde: `office.py` (yeni), `routes.py`, `store.py`, `test_team_office.py` (yeni), `team/plans/office-data-api-adr.md`.

**Yapılanlar**
- `TeamStore` Protocol'üne `read_status()` / `put_status()` eklendi.
  - `FileStore` bunları `team/status.json`'a yazıyor.
  - `DbStore` `team_state` içinde `kind="status"` tek satırı kullanıyor, kilit gibi. Yeni tablo ve migration yok.
- Rotalar: `GET` ve `PUT /v1/team/queue/status` (PUT katı doğrulama, yanlış şekil 422) ve `GET /v1/team/office`. Hepsi owner session altında.
- `office.office_view(queue, lock, status, approvals, now)` saf fonksiyon: koltuk kuralları, bayatlık kuralı, özet 40 satıra kesme.
- Mağaza yoksa veya kuyruk okunamıyorsa ofis 200 ve boş dönüyor: 8 koltuk waiting, `running=false`, tasks boş.

**Kanıt (PROVEN_AUTOMATED)**
- RED: `office` modülü yokken test dosyası toplanamadı (ImportError).
- GREEN: `test_team_office.py` 19 test geçti. Rota testleri hem FileStore (`tmp_path`) hem DbStore (SQLite) ile koşuyor.
- Kapsam:
  - İki worker koşusu: worker-1/2 working, worker-3 waiting, running_agents 2, capacity 6.
  - Inspector koşusu: inspector working.
  - Newest task returned/stopped: seat returned.
  - Durum 11 dk eski, kilit bırakılmış, başka cycle_id'li veya 6 saatten eski kilit: running false, kimse working değil. 9 dk eski durum hâlâ canlı.
  - 55 satırlık özet 40'a kesiliyor.
  - Her zaman sekiz koltuk, sözleşme sırasıyla.
  - PUT'ta bilinmeyen anahtar, yanlış tip, yanlış enum: 422.
  - Oturumsuz çağrı: 401.
  - Approvals, `/v1/team/approvals` ile birebir aynı.
- Mutasyon 1: bayatlık kuralı çıkarıldı → `test_a_stale_status_means_not_running_and_nobody_working` RED.
- Mutasyon 2: 40 satır kesmesi çıkarıldı → `test_a_summary_longer_than_forty_lines_is_cut_to_forty` RED.
- Restore: yedek kopyadan; sha256 öncesi/sonrası aynı (`089d4c1f…e5cc`).
- `ruff check` ve `ruff format` temiz.

**Kırık test (alan dışı)**
- `tests/unit/test_team_state.py::test_every_route_and_body_field_the_powershell_client_uses_is_one_the_server_has` bu değişiklikle kırıldı (68 geçti, 1 kaldı).
- Hata: "a served route the client never calls": `GET` ve `PUT /v1/team/queue/status`.
- Sebep: PowerShell istemcisi (`TeamQueue.ps1`) henüz status rotalarını çağırmıyor.
- Düzeliş: döngünün status yazıcısı işi, `scripts/lib/TeamQueue.ps1` içinde `PUT /v1/team/queue/status` çağıracak. Yazıcı gelene kadar bu test kırmızı kalır.

**Lead'e: birleştirme anında**
- ADR numaralanıp `docs/DECISIONS.md`'ye taşınacak. Taslak `team/plans/office-data-api-adr.md`'de, numarasız.
- `docs/HANDOFF.md` ve `BUILD_STATE.json` sizde. Ofis veri ucu satırı için BUILD_STATE.json'a PROVEN_AUTOMATED yazılabilir.
- Yukarıdaki PowerShell istemci/test uyumu.

**Yorumladığım noktalar (ADR'de)**
- "Rolün en yeni görevi": o rolün en yeni raporunu taşıyan görev.
- Koşusu olmayan worker koltukları: returned/stopped olan en yeni üç worker görevini sırayla alıyor.
- Kart bunu açıkça söylemiyordu; sizin tarafınızda başka bir okuma varsa söyleyin.

**NOT_RUN**
- PROVEN_REAL (gerçek döngüde sayfa ekran görüntüsü): sayfa ve status yazıcısı henüz yok.
- Postgres üzerinde DbStore: testler SQLite ile koştu.
- Tam unit corpus: yalnızca `test_team_office.py` + `test_team_state.py` koştum.

**Açık risk:** `put_status` DB'de "son yazan kazanır". İki makine aynı anda yazarsa nabız birbirini ezer. Kilit tek makineyi zaten garanti ediyor.
