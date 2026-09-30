**Şu an üzerinde çalışılan:** execution-target-wiring, alan `services/api/app/{execution,research,routines}` ve `tests/unit`, makine: bu dizüstü.

**sha:** 32524c2ed8f8689124e364f0784cb942de1a41f1 (dal `team/pilot-02/worker-execution-target-wiring`, push edildi, worktree temiz)

**Dosyalar (5, hepsi alanın içinde):** `app/execution/wiring.py`, `app/research/target.py`, `app/routines/target.py`, `tests/unit/test_execution_wiring.py`, `team/plans/execution-target-wiring-adr.md`.

**Testler:** 17 test geçiyor (`.venv` python, `app.__file__` worktree'yi gösteriyor), ruff temiz. Kanıt sınıfı: PROVEN_AUTOMATED.
- Bulut çevrimiçi, research → cloud ve tek `execution.selected`.
- Bulut çevrimdışı, sahibin Chrome'u kayıtlı → owner_chrome, `execution.fallback` (reason `cloud_offline`) sonra `execution.selected`.
- Bulut seçilince fallback yazılmıyor (yakın ıska).
- Chrome cihazı çevrimdışıysa seçilmiyor → `no_capable_device`.
- `scheduled=True` ile `acting=True` verilse bile kurala `acting=False` ve `scheduled` türü gidiyor. Yasak listedeki sitede zamanlanmış iş okuma olarak seçiliyor; aynı URL'de zamanlanmamış acting iş `deny_listed_site` ile reddediliyor.
- Allow-list dışı bulut acting adımı `not_on_owner_allow_list` ile reddediliyor ve `execution.refused` yazılıyor. Allow-list'te olan seçiliyor. Modül yoksa (ImportError) "izin yok" sayılıyor. Okuma adımı allow-list istemiyor.
- "Hedef yok" `no_capable_device` sınıfına çıkıyor. Politika reddi (yasak site) bu sınıfa çıkmıyor.
- Adaptörler hedef yoksa `NoCapableDeviceError` fırlatıyor. Rutin adaptörü sahibin Chrome'unu ödünç almıyor.

**Mutasyon (RED kanıtı):** `wiring.py` sha256 önce ve sonra `520074ccd5fd2e1be969574fa4d4b00962f925b08be02202ca39ae96ac217620`; yedekten geri yüklendi, `git checkout --` kullanılmadı.
1. Fallback olayı düşürüldü: `..._falls_back_to_the_enrolled_owner_chrome_once` KIRMIZI (1 failed).
2. `acting = acting` (zamanlanmış acting geçişi): `test_a_scheduled_job_never_reaches_the_rule_as_acting` ve `..._read_not_refused` KIRMIZI (2 failed).
3. Geri yüklemeden sonra 17 geçti.

**Sıra sapması:** Önce kodu, sonra testi yazdım; testin kendi RED'ini önce göstermedim. RED kanıtı yalnız iki mutasyondan geliyor.

**Yapamadığım / NOT_RUN:**
- `app.execution.allowlist` bu dalda yok. `acting_allowed(url) -> bool` imzasını varsaydım; testler sahte modülle çalışıyor. Gerçek modülle birleşme NOT_RUN.
- Gerçek `list_device_views` / `BrokerRuntime` ile çalıştırma NOT_RUN. Testler sahte kayıt defteri kullandı. Canlı Postgres ve Temporal yok.
- Sahibin alias'ı (`spoken_target`) gerçek cihazlarla çözülüyor mu, sadece sahte cihazla denendi.

**Açık riskler:**
- Kayıt defterinde `kind` alanı ve "sahibin Chrome'u kayıtlı" işareti yok. `platform == "cloud"` = bulut işçisi, `labels` içinde `owner_chrome` = kayıtlı Chrome kabul ettim (ADR'de yazılı). Bu iki kural gerçek cihazlarda henüz doğrulanmadı ve `enroll-owner-chrome.ps1` etiketi atmıyor olabilir.
- `choose` her çağrıda yeni bir `source_ref` ile olay yazıyor. Aynı iş için iki kez çağrılırsa olaylar çift yazılır.
- Adlandırılmış cihaz çevrimdışıyken `resolved_device` için yer tutucu bir değer veriyorum (`named-but-unavailable`); kural yalnızca `None` olup olmadığına bakıyor.

**For the lead at merge:**
- **Çağrı yerleri.** `choose_research_target(db, runtime, spoken_target=, url=, needs_signed_in_session=)` araştırma çalıştırmasının başında (`app/research/runs_service.py` veya `workflow.py`) çağrılmalı; `NoCapableDeviceError`'ı mevcut `no_capable_device` yolu yakalıyor. `choose_routine_target(db, runtime, url=)` rutin eylemi gönderilmeden önce (`app/routines/dispatch.py`) çağrılmalı. Web-task için doğrudan `wiring.choose(JobKind.BROWSER_TASK, ..., acting=True, scheduled=False)`.
- **Ledger sözlüğü.** `EVENT_TYPE_EXECUTION_*` zaten kayıtlı. `wiring.py` `SUBSYSTEM_RESEARCH`, `SUBSYSTEM_ROUTINE` ve `SUBSYSTEM_BROWSER` kullanıyor, yeni sözlük girdisi gerekmiyor.
- **ADR.** Metin `team/plans/execution-target-wiring-adr.md` içinde, numarasız. `docs/DECISIONS.md`'ye taşıyıp numaralandırın.
- **Allow-list.** `app/execution/allowlist.py` birleşince `acting_allowed` imzasını `(url) -> bool` ile karşılaştırın.
- **Kayıt defteri.** Bulut işçisinin `platform="cloud"` ile, sahibin Chrome cihazının `owner_chrome` etiketiyle kaydedildiğini doğrulayın. Yoksa rota hep "hedef yok" verir.
