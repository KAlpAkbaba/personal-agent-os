# PERSONALAGENTOS v1.0 — FEATURE MATRIX

**Sürüm:** 2026-09-12 · **Taban:** `main` @ `48dfcc3` · üretim `714ff2b` · cihaz agent `0.6.0`
**Spec:** `PERSONALAGENTOS_V1_MASTER_CHECKLIST.md` · **Plan:** `PERSONALAGENTOS_V1_ROADMAP.md`
**Ölçüm kaynağı:** `docs/audit/2026-09-12-tam-olgunluk-raporu.md` + bu turda doğrulanan çalışma zamanı

## LEGEND

Kolonlar: `ID | FEATURE | CURRENT_STATUS | TARGET_STATUS | IMPL | PROOF | PRI | DEPS | BATCH | SOURCE_REFERENCES | TEST_REFERENCES | RUNTIME_PROOF | OWNER_ACTION | NOTES`

`IMPL` = IMPLEMENTATION_STATUS · `PROOF` = PROOF_STATUS · `PRI` = PRIORITY · `DEPS` = DEPENDENCIES

| Kısaltma | Açılım |
|---|---|
| `PR` | PROVEN_REAL |
| `PA` | PROVEN_AUTOMATED |
| `PX` | PROVEN_PROXY |
| `NYP` | NOT_YET_PROVEN |
| `BLK` | BLOCKED |
| `PU` | PROVIDER_UNAVAILABLE |

**Kural:** `PARTIAL` yalnızca test bulunduğu için `DONE`'a çevrilemez. Sahte, gerçek wire
shape ile uyuşmuyorsa satır `DONE` olmaz. `RUNTIME_PROOF` sütunu boşsa (`—`) o satır için
çalışma zamanı ölçümü yoktur; prose kanıt sayılmaz.

**Kapanış kaydı (her `DONE` için):** `status / commit / tests / proof / date` — bkz. §KAPANIŞ.

---

## A. BUG & RELIABILITY (1–30)

| ID | FEATURE | CURRENT_STATUS | TARGET_STATUS | IMPL | PROOF | PRI | DEPS | BATCH | SOURCE_REFERENCES | TEST_REFERENCES | RUNTIME_PROOF | OWNER_ACTION | NOTES |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | Migration hatası release'i durdurur | `set -eu -o pipefail`; göç borusuz, rc 82 ile durur | Göç hatası promote'u bloklar | DONE | PA | P0 | — | B01 | scripts/cloud/release-cloud-core-bluegreen.sh, release-cloud-core.sh | cloud-release-bluegreen.tests.ps1, cloud-release.tests.ps1, test_release_health_contract.py | — | no | Kırmızı ÖNCE kanıtlandı (6 vaka); sahte docker artık göçü düşürebiliyor — eskiden düşüremiyordu |
| 2 | Alembic sürümü release health'te doğrulanır | Sağlık `schema` kontrolü current vs head; sürüm rc 83 ile reddeder | Beklenen revizyon görülmeden yeşil yok | DONE | PR | P0 | 1 | B01 | services/api/app/release/schema.py, app/health.py | test_release_schema.py, test_release_health_contract.py, cloud-release-bluegreen.tests.ps1, cloud-release.tests.ps1 | docs/evidence/b01-schema-gate-2026-09-12.json | no | Gerçek PostgreSQL'de ok/fail gözlendi; satır geri yüklenip doğrulandı |
| 3 | file.search mutlak path çözümü | Kova adı sözleşmesi: cihaz çözüyor, sonra sınırlıyor | Klasör araması üretimde çalışır | DONE | PA | P0 | 5 | B03 | packages/protocol/file-search-roots.json, app/documents/service.py, .../Documents/WellKnownFolders.cs | test_file_search_roots_contract.py (29), FileSearchRootsContractTests.cs (9) | — | no | Kırmızı önce kanıtlandı (3/9 düştü). Yol üstünde: Türkçe İ katlama kusuru |
| 4 | App Factory manifest'leri device contract'a uyar | Üç şablon da cihazın kabul ettiği şekilde | Üç şablon kabul edilir | DONE | PA | P0 | 5,421 | B03 | packages/protocol/app-manifest.example.json, app/appfactory/validation.py, templates/*/manifest.json | test_app_manifest_contract.py (17), AppManifestContractTests.cs (5) | — | no | `{port}`→`<port>`; cli-tool `run`+`port: 0` kazandı; doğrulayıcı artık cihazdan nazik değil |
| 5 | Fake device payload = gerçek wire shape | Sahteler cihazın kendi kaynağından denetleniyor | Sahte makineden nazik olamaz | DONE | PA | P0 | — | B03 | tests/appfactory_support.py, tests/unit/test_nativefactory_device_build.py | test_device_fakes_match_the_device.py (5), test_contract_falsification.py (26) | — | no | Üç sahte `counts_parsed`/`duration_ms` düşürüyordu; artık eksik alan testte düşüyor |
| 6 | /v1/world/facts secret redaction | DB parolası açık metin dönüyor | Sır hiçbir yanıtta yok | DONE | PA | P0 | — | B04 | app/worldmodel/state.py:_Collector.fact, app/security/redaction.py:strip_uri_credentials | test_secret_redaction_surfaces.py | üretim turu bekliyor (Karar 0) | no | Redaksiyon toplayıcı kapısında; DSN'in yalnız userinfo'su siliniyor, host/veritabanı duruyor |
| 7 | Log secret redaction | 13 desen log hattında yok | Sır loga düşmez | DONE | PA | P0 | 6 | B04 | app/logging.py:redact_secrets, app/logging.py:SecretRedactingFilter | test_secret_redaction_surfaces.py | üretim turu bekliyor (Karar 0) | no | structlog zinciri + stdlib handler filtresi (uvicorn/SQLAlchemy oradan yazıyor) |
| 8 | Health secret redaction | Kimliksiz sağlıkta desen yok | Sağlık sır sızdırmaz | DONE | PA | P0 | 6 | B04 | app/health.py:_run_check, app/main.py:system_health | test_secret_redaction_surfaces.py, test_health_endpoint.py | üretim turu bekliyor (Karar 0) | no | Kimliksiz uç: hem hata dizesi hem tüm checks haritası |
| 9 | Dead Voice session sweeper | 6-7 zombie `active`, en eskisi 09-09 | Zombie kalmaz | DONE | PA | P0 | — | B06 | app/voice/realtime_sessions/service.py:sweep_idle_sessions, app/main.py:RetentionSweeper | test_orphan_sweeps.py | üretim turu bekliyor (Karar 0) | no | Yaş değil atıllık: konuşulan oturum hiç süpürülmez |
| 10 | Takılmış research/task sweeper | 09-09'dan beri `discovering` | Terminal duruma taşınır | DONE | PA | P0 | — | B06 | app/research/service.py:sweep_abandoned_runs, app/main.py:RetentionSweeper | test_orphan_sweeps.py | üretim turu bekliyor (Karar 0) | no | Görev FAILED_TERMINAL, koşu STAGE_FAILED; satır silinmez |
| 11 | Stale RUNNING/CREATED reconciliation | Mutabakat yok | Durum gerçeğe uyar | DONE | PA | P0 | 10 | B06 | app/research/service.py:sweep_abandoned_runs, app/worldmodel/state.py:_collect_tasks | test_orphan_sweeps.py, test_worldmodel.py | üretim turu bekliyor (Karar 0) | no | Süpürge taşır, dünya modeli sayar |
| 12 | Temporal unavailable typed refusal | Tipli 503 var | Aynı | DONE | PA | P0 | — | — | services/api/app/research/ | services/api/tests/unit | ADR-0123 | no | Rewrite gerekmez |
| 13 | Orphan research row cleanup | Yetim koşu temizlenmiyor | Yetim kalmaz | DONE | PA | P0 | 10 | B06 | app/research/service.py:sweep_abandoned_runs | test_orphan_sweeps.py | üretim turu bekliyor (Karar 0) | no | 10 ile tek uygulama |
| 14 | Retry loop'ları bounded | Üç sınırsız döngü | Sınırlı geri çekilme | DONE | PA | P0 | — | B07 | app/notifications/delivery.py | test_bounded_delivery.py | üretim turu bekliyor (Karar 0) | no | Üç kuyruğun ortak politikası: deneme sayısı, tavanlı geri çekilme, karantina |
| 15 | Push announcer bounded retry | Kalıcı arızada günde 17.280 deneme | Sınırlı deneme | DONE | PA | P0 | 14 | B07 | app/notifications/delivery.py, app/mobile/announcer.py | test_bounded_delivery.py, test_mobile_announcer.py | üretim turu bekliyor (Karar 0) | no | 17.280/gün bitti; başarısız görev karantinaya, kuyruk ilerliyor |
| 16 | Briefing announcer kuyruk kilidi yok | Tek bozuk satır kuyruğu kalıcı tıkıyor | Zehirli mesaj karantinaya | DONE | PA | P0 | 14 | B07 | app/notifications/delivery.py, app/ledger/briefing.py:pending | test_bounded_delivery.py | üretim turu bekliyor (Karar 0) | no | Zehirli satır artık kenara çekiliyor: kırmızı kanıtlandı |
| 17 | Research announcer bounded retry | Sınırsız | Sınırlı | DONE | PA | P0 | 14 | B07 | app/notifications/delivery.py, app/voice/realtime_sessions/research_announcer.py | test_bounded_delivery.py | üretim turu bekliyor (Karar 0) | no | Karantina 'duyuramadık' der, 'araştırma başarısız' demez |
| 18 | Background loop health tek tek | Yalnız broker süpürgesi görünür | Sekiz döngü ayrı ayrı | DONE | PA | P0 | — | B07 | app/loops.py, app/main.py (sağlık haritası) | test_bounded_delivery.py | canlı sağlık: 24 bileşen | no | Dokuz döngü, dokuzu da sağlıkta; başlatılan ama görünmeyeni test yakalıyor |
| 19 | Routine clock alt bileşen izolasyonu | Tek try/except; bir tik patlarsa hepsi düşer | Alt tikler bağımsız | DONE | PA | P0 | 18 | B07 | app/routines/clock.py:_run_tick | test_bounded_delivery.py, test_routines_clock.py | üretim turu bekliyor (Karar 0) | no | Beş alt tik izole + başarısızlıktan sonra rollback |
| 20 | Redis kritik sağlıktan çıkarılmalı (kullanılmıyorsa) | `ADVISORY_CHECKS` redis'i kritik olmaktan çıkarıyor | Koşul ölçülür, karar verilir | DONE | PA | P0 | — | — | services/api/app/health.py:28 | test_health_endpoint.py::test_an_advisory_check_is_one_nothing_in_the_app_depends_on | canlı sağlık: redis required=false | no | B01'de ölçüldü: matris yanlışlıkla PARTIAL sayıyordu — 2026-09-11'de zaten çözülmüş |
| 21 | Build/version provenance canonical | `release.build_id`: kaynaklardan türetilmiş 16 hex | Tek kanonik gerçek | DONE | PR | P0 | — | B01 | services/api/app/release/build.py, release/version.py | test_release_build_identity.py | docs/evidence/b01-schema-gate-2026-09-12.json | no | Cihazın AgentInfo.BuildId kuralı aynada; test cihazın kendi kaynağını okuyor |
| 22 | Aynı version altında build ayrımı | build_id aynı app_version altında iki imajı ayırıyor | Build ayrımı yapılır | DONE | PR | P0 | 21 | B01 | services/api/app/release/build.py | test_release_build_identity.py | docs/evidence/b01-schema-gate-2026-09-12.json (build_id 516452ef2d144269) | no | version_model 1→2 (additive alan) |
| 23 | BUILD_STATE otomatik reconcile | `reconcile_build_state.py` türetilen alanları üretir | Otomatik üretilir | DONE | PA | P0 | 21 | B01 | scripts/core/reconcile_build_state.py | test_build_state_reconciled.py | state/BUILD_STATE.json bu commit'te mutabık | no | Anlatı yeniden yazılmıyor, DENETLENİYOR: blok stage'inden fazlasını iddia edemez |
| 24 | Evidence-only-in-prose yasak | Kanıt işaretli satır var olan bir şeyi göstermeli | Her iddia kanıt dosyasına bağlı | DONE | PA | P0 | — | B01 | docs/QUALIFICATION.md | test_qualification_evidence.py | 272 satır tarandı | no | 16 tarihi satır adıyla listelendi; liste YALNIZCA küçülebilir |
| 25 | CI tüm kritik testleri kapsar | 7 job; kapsamı bekçi test zorluyor | Kritik test dışarıda kalmaz | DONE | PA | P0 | — | B02 | .github/workflows/ci.yml | test_ci_covers_every_suite.py (7) | CI run 34703755179 | no | Elle tutulan liste artık denetleniyor: var olan her paket adlandırılmak zorunda |
| 26 | Web testleri CI'da | 1587 test (82 dosya) CI'da koşuyor | CI'da koşar | DONE | PR | P0 | 25 | B02 | .github/workflows/ci.yml (web-build job) | apps/web vitest 82 dosya | docs/evidence/b02-ci-coverage-2026-09-12.json — CI 34705909155: 82 dosya / 1587 test | no | Önceden yalnız `pnpm build` vardı |
| 27 | Web linter CI'da | oxlint + tsc --noEmit CI'da | CI'da koşar | DONE | PR | P0 | 25 | B02 | .github/workflows/ci.yml, apps/web/package.json (yeni `typecheck`) | test_ci_covers_every_suite.py::test_ci_runs_every_gate_the_web_package_defines | docs/evidence/b02-ci-coverage-2026-09-12.json — CI: oxlint 0 error, tsc adimi gecti | no | `next build` testleri tip denetlemiyordu; tsc onları da kapsıyor |
| 28 | PE reader testleri CI'da | PagentOS.Agent.Tests.dll: 869 test, 6 atlanan (isimlendirilmiş nedenle) | CI'da koşar | DONE | PR | P0 | 25 | B02 | devices/windows-agent/PagentOS.WindowsAgent.sln | PeImageReaderTests (7 Fact) | docs/evidence/b02-ci-coverage-2026-09-12.json — CI: PagentOS.Agent.Tests.dll 869 test | no | B02'de ölçüldü: matris MISSING sayıyordu — testler zaten CI'da koşuyordu. Düzeltildi |
| 29 | PowerShell qualification CI'da | 26/26 PowerShell paketi CI'da | CI'da koşar | DONE | PR | P0 | 25 | B02 | .github/workflows/ci.yml | test_ci_covers_every_suite.py::test_ci_runs_every_powershell_suite | docs/evidence/b02-ci-coverage-2026-09-12.json — CI: bluegreen 59/59, owner-rotation 26/26 | no | Eksik ikisinden biri cloud-release-bluegreen (59 vaka, ÜRETİMİN kullandığı sürüm yolu) |
| 30 | Mutation/falsification kritik contract'ta | Sözleşme gizlenip bekçinin düştüğü her koşuda kanıtlanıyor | Kalıcı kapı | DONE | PA | P0 | 5 | B02 | packages/protocol/*.json | test_contract_falsification.py (11) | mutasyon her koşuda yürütülüyor (5,6 sn) | no | Yol üstünde: test_injection'ın 'dosya yoksa SKIP' kaçamağı kaldırıldı — sözleşmeyi silmek bekçisini yeşile çeviriyordu |

## B. MEMORY / PERSONAL INTELLIGENCE (31–62)

| ID | FEATURE | CURRENT_STATUS | TARGET_STATUS | IMPL | PROOF | PRI | DEPS | BATCH | SOURCE_REFERENCES | TEST_REFERENCES | RUNTIME_PROOF | OWNER_ACTION | NOTES |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 31 | memory.remember Voice tool | Araç yok; ses paketi app.memory'yi import etmiyor | Sesle kalıcı yazma | DONE | PA | P1 | — | B16 | app/voice/realtime_sessions/tools_memory.py:memory_remember, app/main.py (memory_runtime live source) | test_memory_voice_tools.py (22), korpus m.* (12) | üretim turu bekliyor (Karar 0) | no | Zincirin ilk kopuk halkasıydı ve tam olarak öyleydi: `app.memory` M5'ten beri 3400 satır ve app/voice/ altında onu import eden TEK satır yoktu. ADR-0126: sesli oturum `explicit` bayrağını taşır — CANDIDATE satır "kalıcı" değildir |
| 32 | memory.search Voice tool | Yok | Sesle arama | DONE | PA | P1 | 31 | B16 | app/voice/realtime_sessions/tools_memory.py:memory_search | test_memory_voice_tools.py (22) | üretim turu bekliyor (Karar 0) | no | Yıkıcı her aracın dayandığı adım: "bunu"yu sahibin DUYDUĞU bir id'ye çeviren şey bu |
| 33 | Preference extraction | Tetikleyici kalıplar yazılı, hiç beslenmiyor | Konuşmadan tercih çıkar | DONE | PA | P1 | 31 | B16 | app/memory/extraction.py, app/voice/realtime_sessions/service.py:_extract_memories | test_memory_extraction.py (14) | üretim turu bekliyor (Karar 0) | no | Matrisin notu birebir doğruydu. Girdi artık var: oturumun KENDİ `transcript_summary`'si — sunucu tarafında, zaten kalıcı, hiçbir deşifre araç argümanı olarak yolculuk etmiyor. `explicit=False` her zaman |
| 34 | Project/context extraction | Konuşma metni bilinçli atılıyor | Bağlam çıkar | DONE | PA | P1 | 33 | B16 | app/memory/extraction.py:classify | test_memory_extraction.py (14) | üretim turu bekliyor (Karar 0) | no | `MemoryClass.PROJECT` M5'ten beri var ve konuşmayı okuyan hiçbir şey bir tane yazmamıştı. Türkçe eklemeli: her kök sonuna sözcük-sonu çapası DEĞİL sonek toleransı alır — iki ucu çapalı bir kalıp "repoda"yı kaçırır ve ilk taslak "Bu repoda testleri önce yazıyoruz"u PREFERENCE diye dosyaladı |
| 35 | "bunu hatırla" | Niyet yok | Açık yazma komutu | DONE | PA | P1 | 31 | B16 | app/voice/intents.py:_memory_match | korpus m.* (12), test_voice_intents.py (138) | üretim turu bekliyor (Karar 0) | no | 727 ile ortak uygulama. Aile EN SONDA çözülüyor: fiilleri herkesin — korpus tek turda dokuz vakayı sahibinden aldığını gösterdi |
| 36 | "bunu unut" | REST'te var, sesten erişilemez | Sesle unutma | DONE | PA | P1 | 31 | B16 | app/voice/realtime_sessions/tools_memory.py:memory_forget, app/voice/intents.py:_MEMORY_FORGET_FORMS | test_memory_voice_tools.py (22), korpus m.negation.* | üretim turu bekliyor (Karar 0) | no | 728 ile ortak. HARD delete, geri alınamaz: yalnız id alır, açıklama almaz. Türkçe olumsuzlama tuzağı: "unut" sil, "unutMA" hatırla — `_has` ön ek eşleşmesi olduğu için `_has_exact` şart |
| 37 | "bunu düzelt" | REST'te var, sesten erişilemez | Sesle düzeltme | DONE | PA | P1 | 31 | B16 | app/voice/realtime_sessions/tools_memory.py:memory_correct | test_memory_voice_tools.py (22) | üretim turu bekliyor (Karar 0) | no | `edit_memory`, `supersede` değil: sahip sistemin YANLIŞ anladığını düzeltiyor, eskiyen bir olguyu değiştirmiyor — eski metin sürüm olarak kalıyor |
| 38 | "bunu sabitle" | REST'te var, sesten erişilemez | Sesle sabitleme | DONE | PA | P1 | 31 | B16 | app/voice/realtime_sessions/tools_memory.py:memory_pin | test_memory_voice_tools.py (22) | üretim turu bekliyor (Karar 0) | no | — |
| 39 | Persona memory injection | Persona = 10 sabit metin + 7 ses alanı | Bellek talimata girer | DONE | PA | P1 | 41 | B17 | app/memory/injection.py, app/voice/realtime_sessions/persona.py:build_instructions, .../service.py:_memory_block | test_memory_injection.py (22) | üretim turu bekliyor (Karar 0) | no | Zincirin son kopuk halkasıydı. Blok EN SONDA: iki dakika önce söylenen, martta söylenene baskın çıkar. Tavan ÖLÇÜLDÜ — persona zaten 10.110 karakter ve her create ile her attach'ta gönderiliyor |
| 40 | Tool decision context injection | Yok | Araç seçimi belleği görür | DONE | PA | P1 | 39 | B17 | app/voice/realtime_sessions/persona.py | test_memory_injection.py (22) | üretim turu bekliyor (Karar 0) | no | İkinci bir enjeksiyon noktası DEĞİL: talimat tektir (spec §4 adım 1) ve araç seçimi onu okur. 39 doğru olduğunda 40 da doğrudur — ayrı bir yol yapmak ikinci bir gerçek kaynağı olurdu |
| 41 | Top-k contextual retrieval | hybrid_search'ün ürün çağıranı yok | Yanıt yolunda geri getirme | DONE | PA | P1 | 31 | B17 | app/memory/injection.py:select_for_instruction | test_memory_injection.py (22) | üretim turu bekliyor (Karar 0) | no | B16 ilk ürün çağıranını verdi (sahip SORDUĞUNDA); bu, sahip sormadan yanıt yolunda. Aday havuzu tavandan büyük: geri getirmediğini bütçeden düşüremezsin |
| 42 | Memory provenance | Şemada var, tek yazıcı araştırma | Her kaynaktan provenans | DONE | PA | P1 | 31 | B17 | app/memory/service.py:_new_memory, app/memory/receipts.py | test_memory_injection.py (22), test_memory_extraction.py (14) | üretim turu bekliyor (Karar 0) | no | B16 şemaya iki yazıcı daha ekledi: `owner_statement` (ses) ve `observation` (konuşma çıkarımı). Ayırt edilebilir olması şart — 45 okuyacak bir şey olmadan çalışamaz |
| 43 | Memory confidence | Yazılı, üretimde tek sınıf | Çok kaynaklı güven | DONE | PA | P1 | 42 | B17 | app/memory/policy.py, app/memory/lifecycle.py | test_memory_injection.py (22) | üretim turu bekliyor (Karar 0) | no | Üretimde tek sınıftı çünkü tek yazıcı vardı. Artık sayı bir şey söylüyor: 1.0 sahibin söylediği, çıkarım ne kadar tekrarlanırsa tekrarlansın altında kalıyor. Enjeksiyon tabanı politikanın tek-gözlem tavanının hemen üstünde |
| 44 | Conflict resolution | Yazılı ve testli, sesten erişilemez | Çalışır | DONE | PA | P1 | 31 | B17 | app/memory/service.py:_states_the_same_thing | test_memory_service.py, test_memory_injection.py (22) | üretim turu bekliyor (Karar 0) | no | ÖLÇÜM bir kusur buldu: anahtarlı çatışma dalı `value_json` karşılaştırıyordu ve sesle ya da çıkarımla yazılan her kaydın değeri boş. Sahip kendini düzelttiğinde düzelttiği şeye İKİNCİ KANIT yazılıyordu. Değer yoksa metin karar veriyor |
| 45 | Explicit > inferred precedence | Yazılı, hiç tetiklenmedi | Çalışır | DONE | PA | P1 | 44 | B17 | app/memory/service.py, app/memory/injection.py:rank | test_memory_injection.py (22) | üretim turu bekliyor (Karar 0) | no | "Hiç tetiklenmedi"nin sebebi 44'te ölçüldü: içinde yaşadığı dal ulaşılamazdı. Enjeksiyonda ayrıca AĞIRLIK değil ÖNCELİK — `hybrid_search` açıklığı tazelikle aynı toplamda tartıyor, bu sabahki bir tahmin marttaki bir beyanı geçebilir |
| 46 | Project continuity | Yok | Oturumlar arası proje bağlamı | DONE | PA | P1 | 39 | B18 | app/voice/realtime_sessions/tools_memory.py:_links | test_memory_voice_tools.py (32) | üretim turu bekliyor (Karar 0) | no | `Memory.project_id` M5'ten beri şemada, `RetrievalFilters` ona göre daraltabiliyor, `hybrid_search` proje eşleşmesini zaten puanlıyor — ve hiçbir şey doldurmuyordu. Doldurulmayan bir bağlantı sütunu, kimsenin yapamadığı bir join. BU KONUŞMADA açılan projeye bağlanıyor: `focus.current`'ın tazelik sınırı yok, martta açılan bir proje hazirandaki tercihe yapışmamalı |
| 47 | Person/entity relationships | Varlık grafiği boş | Grafik kurulur | DONE | PA | P1 | 33 | B18 | app/memory/graph.py:sync_from_events | test_experience_scheduler.py (22) | üretim turu bekliyor (Karar 0) | no | `Entity`, `EntityEdge`, sekiz kind, `create_entity`/`create_edge` ve REST yüzeyi M5'ten beri var; `app/` altında birini çağıran tek satır yoktu. Yalnızca defterin KENDİ kimlik alanlarından kuruluyor: `person` düğümü YOK, çünkü defterde kişi adlandıran alan yok — tahminlerden bir grafik, kanıtsızı reddeden bir bellek altsistemine yakışmaz |
| 48 | Device-independent entity identity | Yazılı, kullanılmıyor | Çalışır | DONE | PA | P1 | 47 | B18 | app/memory/models.py (uq_entities_kind_name) | test_experience_scheduler.py (22) | üretim turu bekliyor (Karar 0) | no | M29 DEĞİL. Kimlik ŞEMASI zaten sağlıyordu: `Entity` `(kind, name)` üzerinde tekil ve cihaz sütunu taşımıyor, yani iki farklı kaynaktan görülen aynı mantıksal şey TEK düğüm. Tablo yazıldığında doğruydu ve hiçbir şey yazmadığı için sınanamazdı; yazıcı gelince sınanabildi |
| 49 | Current/previous object focus | Yok | "bunu/şunu" çözülür | DONE | PA | P1 | 39 | B18 | app/operator/focus.py (M19'dan beri), app/operator/models.py:FOCUS_KIND_MEMORY, tools_memory.py:_focused_memory | test_memory_voice_tools.py (32) | üretim turu bekliyor (Karar 0) | no | ÖLÇÜMLE DÜZELTİLDİ: mekanizma M19'dan beri var — 15 kind, `current`/`previous`/`stack`, sekiz pakette kullanılıyor. Gerçekten eksik olan `memory` kind'ıydı: `memory.search` okuduğunu odağa alıyor, böylece "bunu unut" sahibin DUYDUĞU kayda çözülüyor. B16'nın reddettiği bulanık eşleştirici değil — cümleden hiçbir şey çözülmüyor, sesli söylenenin kaydı okunuyor |
| 50 | Recent task continuity | Yok | Son görev bağlamı sürer | DONE | PA | P1 | 46 | B18 | app/voice/realtime_sessions/tools_memory.py:_links | test_memory_voice_tools.py (32) | üretim turu bekliyor (Karar 0) | no | `Memory.conversation_id` da M5'ten beri şemadaydı ve hiçbir şey doldurmuyordu, yani "bunu bana X'i konuşurken söylemiştin" yanıtlanamıyordu. Artık her öğretilen kayıt onu öğreten oturumu taşıyor |
| 51 | Real semantic embedding provider | `OpenAIEmbedder` (text-embedding-3-small, tam indeks genişliği `dimensions=256`, L2 normalize, süreç içi LRU; anahtar ayarlardan — hata metninde asla anahtar/metin yok) `Embedder` protokolü arkasında | Gerçek anlamsal gömme | DONE | PA | P2 | 41 | B37 | app/memory/providers.py:OpenAIEmbedder | test_memory_b37.py (kayıtlı taşıma: gönderilen istek, normalize, önbellek, 429, yanlış genişlik) | canlı: sağlayıcı sahibin anahtarıyla | sağlayıcı kredisi (PAGENTOS_OPENAI_API_KEY) | Anahtar yokken deterministic-ngram, NEDENİYLE raporlanır |
| 52 | Deterministic n-gram fallback | Çalışıyor | Aynı | DONE | PR | P2 | — | — | app/memory/ | app/memory testleri | canlı sağlık | no | Rewrite gerekmez |
| 53 | Embedding provider selection | `memory_embedding_provider` = deterministic / openai / auto (varsayılan auto: anahtar varsa OpenAI, yoksa deterministic) → `build_embedder(settings)` + `EmbedderReport` (requested/active/semantic/fallback_reason); /v1/system/health `embedder.provider / semantic / fallback_reason`; `GET /v1/memory/embedding` | Yapılandırmayla seçilir | DONE | PA | P2 | 51 | B37 | app/memory/providers.py:build_embedder; runtime.py; config.py | test_memory_b37.py (6 satırlık seçim matrisi; sağlık) | — | no | Karma 'anlamsal' diye raporlanmaz |
| 54 | Re-index pipeline | `lifecycle.embedding_coverage` (etkin model için eksikler + model başına satır) ve `reindex_missing` (yalnız eksikler; ikinci geçiş 0 yazar); `POST /v1/memory/reindex {only_missing}`; sayfada 'Eksikleri indeksle / Hepsini yeniden indeksle'; eski modelin satırları korunur | Yeniden indeksleme | DONE | PA | P2 | 53 | B37 | app/memory/lifecycle.py; routes.py; apps/web/app/memory/page.tsx | test_memory_b37.py (kapsam 3/0 → model değişince 0/3 → eksik doldur → 0; REST) | — | no | Sağlayıcı değişimi = yeni model_id, tabloya dokunulmaz |
| 55 | Memory retention scheduler | Yok | Politikayla süpürme | DONE | PA | P1 | — | B17 | app/memory/lifecycle.py:sweep_expired, app/main.py (RetentionSweeper) | test_memory_injection.py (22) | RetentionSweeper lifespan'de koşuyor, /health raporluyor | no | ÖLÇÜMLE DÜZELTİLDİ: retention sınıfına göre süpürüyor (session/short TTL, sabitlenmiş ve açık kayıtlara asla dokunmuyor), Phase 8'den (2026-09-11) beri kayıtlı ve koşuyor. Eksik olan tek şey bir bekçiydi — iki batch boyunca MISSING yazabildi çünkü yanlış olduğunda hiçbir şey düşmüyordu |
| 56 | Sensitive-data exclusion | Kural var (parola dizesi reddediliyor) | Tam kapsam | DONE | PA | P1 | 6 | B17 | app/memory/policy.py:find_secret, app/memory/injection.py | test_memory_injection.py (22), test_memory_policy.py (32) | üretim turu bekliyor (Karar 0) | no | Kapsamı asıl genişleten şey bu batch'in kendisi: bellek artık ÜÇÜNCÜ TARAF bir sağlayıcıya talimat içinde gidiyor. Yazma kapısına güvenen son kapı kapı değildir — enjeksiyon politikanın KENDİ kalıplarıyla tekrar tarıyor, içerik loglamadan |
| 57 | Memory audit UI | /memory sayfası: hatıralar (sınıf, kaynak, sabit, evre, zaman), varlıklar, denetim günlüğü (MemoryPanel) + B37: 'Anlamsal indeks' bölümü (sağlayıcı, kapsam, düşüş nedeni) ve her satırda kontroller | Sahip belleğini görür | DONE | PA | P2 | 39 | B37 | apps/web/app/memory/page.tsx; lib/pages/memory.ts | memory-controls.test.tsx (5), family-pages.test.ts | — | no | 689 ile aynı sayfa |
| 58 | Pin/unpin UI | Satırda Sabitle / Sabitlemeyi kaldır → `POST /v1/memory/{id}/pin` / `unpin`; `unpin_memory` retention'ı standarda döndürür, denetim `unpinned` | Arayüzden sabitleme | DONE | PA | P2 | 57 | B37 | app/memory/service.py:unpin_memory; routes.py; apps/web/app/lib/pages/memory.ts | test_memory_b37.py (unpin + REST), memory-controls.test.tsx | — | no | — |
| 59 | Forget UI | Satırda Unut → ikinci tıkla 'Evet, unut (geri alınamaz)' → `DELETE /v1/memory/{id}`; sonuç satırı silinen kayıt sayısını söyler | Arayüzden silme | DONE | PA | P2 | 57 | B37 | apps/web/app/memory/page.tsx:MemoryRowControls; lib/pages/memory.ts | memory-controls.test.tsx | — | no | Tek geri alınamaz eylem: iki adım |
| 60 | Correction UI | Satırda Düzelt → satır içi metin → `POST /v1/memory/{id}/supersede {text, reason: owner_correction}` (eski kayıt yenisiyle değiştirilir, yerinde düzenleme yok); boş metin çağrısız reddedilir | Arayüzden düzeltme | DONE | PA | P2 | 57 | B37 | apps/web/app/lib/pages/memory.ts:runMemoryAction | memory-controls.test.tsx | — | no | — |
| 61 | "Neden bunu hatırladın?" | Yok | Gerekçe açıklanır | DONE | PA | P1 | 41 | B16 | app/memory/receipts.py:why_sentence, tools_memory.py:memory_why | test_memory_voice_tools.py (22) | üretim turu bekliyor (Karar 0) | no | `inspect_memory` M5'ten beri provenans, kanıt, sürüm, denetim ve çelişkileri döndürüyordu; hiçbir şey bunu cümleye çevirmemişti. Veri tamdı ve söylenemezdi. Sözlük `service.py`'nin KENDİ yazdığı `origin` değerlerinden okunuyor |
| 62 | Memory usage receipts | Yok | Kullanım raporlanır | DONE | PA | P1 | 41 | B16 | app/memory/receipts.py:record_use, app/ledger/vocabulary.py:EVENT_TYPE_MEMORY_USED | test_memory_voice_tools.py (22) | üretim turu bekliyor (Karar 0) | no | 61 ile ortak uygulama. Hiçbir yerde bir kaydın KULLANILDIĞI yazılmıyordu. Makbuz kaydın sahibe OKUNDUĞU yerde basılıyor, `hybrid_search`'te değil: kırk satır döndüren bir arama kırk kullanım değildir |

## C. SELF MODEL / WORLD MODEL (63–80)

| ID | FEATURE | CURRENT_STATUS | TARGET_STATUS | IMPL | PROOF | PRI | DEPS | BATCH | SOURCE_REFERENCES | TEST_REFERENCES | RUNTIME_PROOF | OWNER_ACTION | NOTES |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 63 | Runtime/source correlation | 443 modülün hepsi `source_only` | Çalışan sürümle ilişki | DONE | PA | P1 | 21 | B19 | app/selfmodel/runtime_truth.py, app/selfmodel/refresh.py | test_selfmodel_runtime_truth.py (21) | üretim turu bekliyor (Karar 0) | no | `_apply_production_states` kanıt gücüne göre zaten doğru sıralıydı; sorun runtime doğruluğunun tek üreticisinin TARİHSEL olmasıydı (Release satırı, tamamlanmış deployment olayı) ve bu kurulumda ikisi de yok — yani `source_only` dürüst cevaptı. Var olan en doğrudan kanıt hiç okunmuyordu: sürecin kendisi |
| 64 | Deployed SHA awareness | Sağlıkta var, öz modelde yok | Öz model bilir | DONE | PA | P1 | 63 | B19 | app/selfmodel/runtime_truth.py:observe | test_selfmodel_runtime_truth.py (21) | üretim turu bekliyor (Karar 0) | no | `release_model()` sürümü ve kaynaklardan türetilmiş build_id'yi zaten döndürüyordu; öz model onu yalnız geçmişe dair bir olaydan öğrenebiliyordu. Güven 0.99 — tamamlanmış bir dağıtım olayının 0.95'inden yüksek, çünkü o bir kaydın anlatımı, bu şeyin kendisinin cevabı |
| 65 | Windows build awareness | Cihaz build_id gönderiyor, öz model okumuyor | Öz model bilir | DONE | PA | P1 | 63 | B19 | app/selfmodel/runtime_truth.py:device_rows | test_selfmodel_runtime_truth.py (21) | üretim turu bekliyor (Karar 0) | no | `Device.build_id` (ADR-0118) her hello'da tazeleniyor. KAYIT satırından okunuyor, durum kayıtçısından değil: durum kayıtçısı cihazın ne YAPTIĞINI taşır ve dakikada altı kez tazelenir; kimlik, ajan değiştiğinde değişen satıra aittir |
| 66 | Device capability awareness | Kayıtta var, öz modelde yok | Öz model bilir | DONE | PA | P1 | 63 | B19 | app/selfmodel/runtime_truth.py | test_selfmodel_runtime_truth.py (21) | üretim turu bekliyor (Karar 0) | no | 85 yetenekli manifest kanıt referansında sayılıyor |
| 67 | World model terminal-state accuracy | `tasks.running=10` yanlış | Doğru sayım | DONE | PA | P0 | — | B06 | app/artifacts/state.py:TASK_*_STATUSES, app/worldmodel/state.py:_collect_tasks | test_task_status_meaning.py, test_worldmodel.py | üretim turu bekliyor (Karar 0) | no | Dört anlam kovası; her durum tam olarak bir kovada |
| 68 | READY research running sayılmasın | Biten iş "çalışıyor" görünüyor | Doğru | DONE | PA | P0 | 67 | B06 | app/artifacts/state.py:TASK_*_STATUSES, app/worldmodel/state.py:_collect_tasks | test_task_status_meaning.py, test_worldmodel.py | üretim turu bekliyor (Karar 0) | no | READY artık awaiting_owner, running değil |
| 69 | Stuck task detection | Yok | Tespit edilir | DONE | PA | P0 | 10 | B06 | app/worldmodel/state.py:_collect_tasks:STUCK_TASK_AFTER | test_worldmodel.py | üretim turu bekliyor (Karar 0) | no | tasks.stuck_count, 24 saat |
| 70 | Ledger duplicate Voice event prevention | Her sesli oturum iki satır | Tek satır | DONE | PA | P0 | — | B06 | app/ledger/service.py:voice_session_source_ref, app/ledger/service.py:_voice_live_already_recorded | test_ledger_service.py, test_voice_realtime_sessions.py | üretim turu bekliyor (Karar 0) | no | Doğal anahtar = canlı yazarın kendi anahtarı; tek tanım, iki yarı |
| 71 | Experience Engine scheduler | Zamanlayıcı yok, hiç koşmadı | Düzenli koşar | DONE | PA | P1 | — | B18 | app/experience/scheduler.py, app/main.py (routine clock rider) | test_experience_scheduler.py (22) | /health experience_ingest raporluyor | no | 1441 olaydan 0 bellek: motor tamdı, tek çağıranı elle POST'tu. Rutin saatine biniyor ve kendi aralığıyla kısıyor (Evolution Supervisor'ün deseni). İKİ YÖN: defter sorgusu en yeniden başlıyor ve 200'de sınırlı, yani "son turdan beri" bugüne yetişir ama zaten orada olan 1441'e hiç ulaşmaz |
| 72 | Activity to lesson extraction | Kod var, hiç koşmadı | Ders çıkar | DONE | PA | P1 | 71 | B18 | app/experience/engine.py | test_experience_scheduler.py (22) | üretim turu bekliyor (Karar 0) | no | Kod gerçekten vardı; eksik olan onu çağıran şeydi (71) |
| 73 | Lesson to memory integration | Kod var, hiç koşmadı | Belleğe yazılır | DONE | PA | P1 | 72 | B18 | app/experience/engine.py, app/memory/service.py | test_experience_scheduler.py (22) | üretim turu bekliyor (Karar 0) | no | "memory.remembered hiç yayılmıyor" notu B16'da çözüldü (ilk yazıcısı sesli öğretme); bu batch defterin `experience.ingested` makbuzunu da ekliyor — o makbuz aynı zamanda İMLEÇ: motor kendi durumunu tutmuyor, yazdığı şeyle çelişebilecek ikinci bir gerçek kaynağı olmuyor |
| 74 | System health explanation | Ham JSON | İnsan dili | DONE | PA | P1 | — | B19 | app/selfmodel/diagnosis.py:health_sentence | test_selfmodel_runtime_truth.py (21) | üretim turu bekliyor (Karar 0) | no | On sekiz JSON bloğu tek cümlede. İKİNCİL kontroller ayrı sayılıyor: `app.health` onları `required: false` diye işaretliyor ve iki ikincil kontrol için "iki özelliğim çalışmıyor" demek yalancı çoban olurdu |
| 75 | "Şu an ne yapıyorsun?" | Yanlış (running=10) | Doğru | DONE | PA | P1 | 67 | B06 | app/worldmodel/state.py:TASK_ACTIVE_STATUSES | test_worldmodel.py | üretim turu bekliyor (Karar 0) | no | ÖLÇÜMLE DÜZELTİLDİ: 67 B06'da kapandı ve bu satır onunla düzeldi — `state.py`'nin kendi yorumu tam olarak bu kusuru anlatıyor ("production said 10 running while nothing ran"). Matris B06'dan sonra güncellenmemiş |
| 76 | "Nerede takıldın?" | Yok | Dürüst yanıt | DONE | PA | P1 | 69 | B19 | app/selfmodel/diagnosis.py:stuck_now, app/explain/classify.py:QUERY_STUCK_NOW | test_selfmodel_runtime_truth.py (21) | üretim turu bekliyor (Karar 0) | no | Takılı bir görev olay da açmaz hata da yazmaz — bu yüzden kendi cevabını hak ediyor: takılmış bir sistem, diğer her yüzeye BOŞTA bir sistem gibi görünür. Bayat gözlemde "takılı bir şey yok" DEMİYOR, bilmediğini söylüyor |
| 77 | "Son bug neydi?" | Yok | Kanıta dayalı yanıt | DONE | PA | P1 | 581 | B19 | app/selfmodel/diagnosis.py:last_defect, app/explain/service.py:recent_incidents | test_selfmodel_runtime_truth.py (21) | üretim turu bekliyor (Karar 0) | no | Yalnız TEŞHİS EDİLMİŞ kayıtlardan: en son görülen istisnadan cevaplayan bir sistem sahibine gürültü anlatır. `open_incidents` değil `recent_incidents`: düzeltilmiş bir hata hâlâ son hatadır |
| 78 | "Hangi özelliklerin çalışmıyor?" | Yok | Çalışma zamanından | DONE | PA | P1 | 700 | B19 | app/selfmodel/diagnosis.py:not_working | test_selfmodel_runtime_truth.py (21) | üretim turu bekliyor (Karar 0) | no | Gereksinim "çalışma zamanından" diyor ve o kelime boşuna değil: matristen cevaplamak, cevabı elle tutulan bir belge ne kadar doğruysa o kadar doğru yapardı. Dünya modelinin bağımlılık olgularından okunuyor — kendi gözlem zamanı ve bayatlığıyla birlikte |
| 79 | Self-diagnostic summary | Kısmi | Tam özet | DONE | PA | P1 | 74 | B19 | app/selfmodel/diagnosis.py:summary | test_selfmodel_runtime_truth.py (21) | üretim turu bekliyor (Karar 0) | no | Önce ne bozuk, sonra ne takılı, sonra ne çalışıyor. Build id ile başlayan bir özet, sistemi kuran kişi için yazılmış olurdu; sisteme güvenen kişi için değil |
| 80 | Self-model stale-data detection | Yok | Bayat veri tespiti | DONE | PA | P1 | 63 | B19 | app/selfmodel/query.py:STALE_AFTER,is_stale | test_selfmodel_runtime_truth.py (21) | üretim turu bekliyor (Karar 0) | no | ÖLÇÜMLE DÜZELTİLDİ: 2026-09-05'te bağımsız güvenlik incelemesi `stale` alanının her yerde False yazılıp hiç hesaplanmadığını buldu ve düzeltildi — her okumada yaşa göre hesaplanıyor. EVIDENCE'ın bayatlamaması kusur değil tasarım: olan olmuştur. B19 bunu ilk kez GEREKLİ kıldı, çünkü artık şimdi gözlenen bir runtime doğruluğu var |

## D. DIGITAL OPERATOR 2.0 (81–130)

> Ölçüm: cihaz 32 operatör yeteneği duyuruyor, bulutun çağırdığı 12. Aşağıda `PARTIAL`
> çoğunlukla "cihaz tarafı gerçek, bulutta çağıran yok" anlamına gelir — yani kod değil
> **erişilebilirlik** eksiktir.

| ID | FEATURE | CURRENT_STATUS | TARGET_STATUS | IMPL | PROOF | PRI | DEPS | BATCH | SOURCE_REFERENCES | TEST_REFERENCES | RUNTIME_PROOF | OWNER_ACTION | NOTES |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 81 | App launch | Çalışıyor | Aynı | DONE | PR | P1 | — | — | app/operator/, devices/windows-agent | operator testleri | üretim komutu | no | Rewrite gerekmez |
| 82 | App close | `operator.app_close` → `app.close` + `window.list` (ilk çağıran; pencere cihaz listesinde İMAJla bulunur) | Erişilebilir | DONE | PA | P1 | 7? | B30 | app/operator/plans.py:close_app; tools_operator.py:operator_app_close; intents.py:_app_close_match | test_operator_allowlists.py (16), test_operator_process_service.py (24), korpus op.app_close/op.process/op.service/op.shell.who (53), OperatorAllowlistsContractTests (4) + ProcessServiceTests (5, C#) | cihaz laboratuvarı bu masaüstünde yeşil (ProcessServiceTests 5/5, sözleşme 4/4); üretim turu Karar 0 | no | "Not Defteri'ni kapat": WM_CLOSE, kaydet diyaloğu modal_open olarak bildirilir, asla cevaplanmaz; açık değilse noop 'zaten açık değil' |
| 83 | Window activate | Çalışıyor (FocusGuard) | Aynı | DONE | PA | P1 | — | — | devices/windows-agent | FocusGuard testleri | — | no | — |
| 84 | Window move | `operator.window_control` move (x,y) → `window.move`, rect 8 px içinde yeniden okunur | Erişilebilir | DONE | PA | P1 | 5 | B30 | app/operator/plans.py:move_window | test_operator_allowlists.py (16), test_operator_process_service.py (24), korpus op.app_close/op.process/op.service/op.shell.who (53), OperatorAllowlistsContractTests (4) + ProcessServiceTests (5, C#) | NotepadLifecycleTests (lab, window.move/resize gerçek Not Defteri'nde rect yeniden okundu); üretimden komut Karar 0 | no | RECT_TOLERANCE_PX = cihazın RectTolerance'ı |
| 85 | Window resize | `operator.window_control` resize (width,height) → `window.resize`, rect 8 px içinde yeniden okunur | Erişilebilir | DONE | PA | P1 | 5 | B30 | app/operator/plans.py:resize_window | test_operator_allowlists.py (16), test_operator_process_service.py (24), korpus op.app_close/op.process/op.service/op.shell.who (53), OperatorAllowlistsContractTests (4) + ProcessServiceTests (5, C#) | NotepadLifecycleTests (lab, window.move/resize gerçek Not Defteri'nde rect yeniden okundu); üretimden komut Karar 0 | no | Oturmayan rect postcondition_failed |
| 86 | Minimize | `operator.window_control` minimize → `window.minimize` (M19'dan beri çağıranı vardı; satır bayattı) | Erişilebilir | DONE | PA | P1 | 5 | B30 | app/operator/plans.py:minimize_window | test_operator_tools.py, korpus op.window.* | NotepadLifecycleTests (lab) | no | B30 ölçümü: çağıran zaten vardı |
| 87 | Maximize | `operator.window_control` maximize → `window.maximize` (M19'dan beri çağıranı vardı; satır bayattı) | Erişilebilir | DONE | PA | P1 | 5 | B30 | app/operator/plans.py:maximize_window | test_operator_tools.py, korpus op.window.* | NotepadLifecycleTests (lab) | no | B30 ölçümü: çağıran zaten vardı |
| 88 | Restore | `operator.window_control` restore → `window.restore` (M19'dan beri çağıranı vardı; satır bayattı) | Erişilebilir | DONE | PA | P1 | 5 | B30 | app/operator/plans.py:restore_window | test_operator_tools.py, korpus op.window.* | NotepadLifecycleTests (lab) | no | B30 ölçümü: çağıran zaten vardı |
| 89 | Window list | Çalışıyor | Aynı | DONE | PA | P1 | — | — | app/operator/ | operator testleri | — | no | — |
| 90 | Current foreground read | Çalışıyor (FocusGuard) | Aynı | DONE | PA | P1 | — | — | devices/windows-agent | FocusGuard testleri | — | no | — |
| 91 | Keyboard typing | Cihaz gerçek + FocusGuard; bulut `secret` bayrağını gönderiyor; makbuz adım muhasebeli | Erişilebilir | DONE | PA | P1 | 663 | B28 | devices/windows-agent; app/operator/plans.py:type_text; app/operator/service.py:_receipt | test_operator_input.py (33), korpus op.key/op.shortcut/op.scroll (37), NotepadLifecycleTests (lab, 5) | cihaz laboratuvarı bu masaüstünde yeşil (A_key_a_chord_and_a_scroll…); üretim turu Karar 0 | no | 109/110 ile birlikte kapandı: FocusGuard reddi görevi `focus_mismatch` ile bitirir, makbuz kaç adımın yürüdüğünü ve hangi adımın durdurduğunu söyler |
| 92 | Single key press | `operator.key` → `keyboard.key` (ilk çağıran) | Erişilebilir | DONE | PA | P1 | 91 | B28 | app/operator/plans.py:press_key; tools_operator.py:operator_key; intents.py:_key_press_match | test_operator_input.py (33), korpus op.key/op.shortcut/op.scroll (37), NotepadLifecycleTests (lab, 5) | cihaz laboratuvarı bu masaüstünde yeşil (A_key_a_chord_and_a_scroll…); üretim turu Karar 0 | no | "Enter'a bas." sesle; sunucu sözlüğü cihazın `KeyMap` sözlüğüyle test okuyarak eşit tutuluyor; giriş adımı retries=0 |
| 93 | Keyboard shortcuts | `operator.key` (keys) → `keyboard.shortcut` | Erişilebilir | DONE | PA | P1 | 91 | B28 | app/operator/plans.py:press_shortcut; tools_operator.py:operator_key | test_operator_input.py (33), korpus op.key/op.shortcut/op.scroll (37), NotepadLifecycleTests (lab, 5) | cihaz laboratuvarı bu masaüstünde yeşil (A_key_a_chord_and_a_scroll…); üretim turu Karar 0 | no | "Ctrl S'ye bas." → ["ctrl","s"]; akor bir kez gönderilir (Ctrl+Z iki kez = iki geri alma) |
| 94 | Mouse move | `operator.pointer` action=move (son çare kapısı) | Erişilebilir | DONE | PA | P1 | 91 | B28 | app/operator/plans.py:pointer; tools_operator.py:operator_pointer | test_operator_input.py (33), korpus op.key/op.shortcut/op.scroll (37), NotepadLifecycleTests (lab, 5) | cihaz laboratuvarı bu masaüstünde yeşil (A_key_a_chord_and_a_scroll…); üretim turu Karar 0 | no | 107 politikası: `last_resort=true` + gerekçe olmadan RED makbuzu |
| 95 | Mouse click | `operator.pointer` action=click; imleç geri okunur (±2 px) | Erişilebilir | DONE | PA | P1 | 91 | B28 | app/operator/plans.py:pointer | test_operator_input.py (33), korpus op.key/op.shortcut/op.scroll (37), NotepadLifecycleTests (lab, 5) | cihaz laboratuvarı bu masaüstünde yeşil (A_key_a_chord_and_a_scroll…); üretim turu Karar 0 | no | "Sistem bugün tıklayamıyor" kapandı: tıklama pencere uzayında, cihazın kendi imleç okumasıyla doğrulanır; yerleşmeyen tıklama `postcondition_failed` |
| 96 | Double click | `operator.pointer` action=double_click | Erişilebilir | DONE | PA | P1 | 95 | B28 | app/operator/plans.py:pointer | test_operator_input.py (33), korpus op.key/op.shortcut/op.scroll (37), NotepadLifecycleTests (lab, 5) | cihaz laboratuvarı bu masaüstünde yeşil (A_key_a_chord_and_a_scroll…); üretim turu Karar 0 | no | — |
| 97 | Right click | `operator.pointer` action=right_click | Erişilebilir | DONE | PA | P1 | 95 | B28 | app/operator/plans.py:pointer | test_operator_input.py (33), korpus op.key/op.shortcut/op.scroll (37), NotepadLifecycleTests (lab, 5) | cihaz laboratuvarı bu masaüstünde yeşil (A_key_a_chord_and_a_scroll…); üretim turu Karar 0 | no | — |
| 98 | Scroll | `operator.pointer` action=scroll; "Aşağı kaydır." sesle | Erişilebilir | DONE | PA | P1 | 95 | B28 | app/operator/plans.py:pointer; intents.py:_scroll_match | test_operator_input.py (33), korpus op.key/op.shortcut/op.scroll (37), NotepadLifecycleTests (lab, 5) | cihaz laboratuvarı bu masaüstünde yeşil (A_key_a_chord_and_a_scroll…); üretim turu Karar 0 | no | Sesli kaydırma pencerenin kendi merkezine iner (cihazın rect'i), koordinat tahmini yok; son çare kapısından muaf (öğe hedeflemiyor) |
| 99 | UIA tree inspect | `operator.inspect` → `ui.inspect` (sınırlı ağaç; öğeleri sayar/adlandırır) | Erişilebilir | DONE | PA | P1 | 91 | B29 | app/operator/plans.py:ui_read; tools_operator.py:operator_inspect | test_operator_ui.py (37), korpus op.ui/op.see (34), NotepadLifecycleTests lab (6) | cihaz laboratuvarı bu masaüstünde yeşil (A_dialog_button_invoked_through_UI_Automation…); üretim turu Karar 0 | no | İlk bulut çağıranı; sorgu anahtarları cihazın `ReadQuery`'siyle test okuyarak eşit |
| 100 | UIA button invoke | `operator.ui` action=invoke; "Tamam düğmesine tıkla" sesle; sonuç BAĞIMSIZ okunur | Erişilebilir | DONE | PA | P1 | 99 | B29 | app/operator/plans.py:ui_invoke; intents.py:_ui_invoke_match | test_operator_ui.py (37), korpus op.ui/op.see (34), NotepadLifecycleTests lab (6) | cihaz laboratuvarı bu masaüstünde yeşil (A_dialog_button_invoked_through_UI_Automation…); üretim turu Karar 0 | no | Tarayıcı kuralının 4. seviyesi; öğe ne değişti ne kayboldu → `postcondition_failed`, "sonuç göremedim"; `expect=window_gone` ise window.list'ten okunur |
| 101 | UIA set value | `operator.ui` action=set_value; ui.set_value + ui.inspect geri okuma; `secret` bayrağı | Erişilebilir | DONE | PA | P1 | 99 | B29 | app/operator/plans.py:ui_set_value | test_operator_ui.py (37), korpus op.ui/op.see (34), NotepadLifecycleTests lab (6) | cihaz laboratuvarı bu masaüstünde yeşil (A_dialog_button_invoked_through_UI_Automation…); üretim turu Karar 0 | no | Sunucu sır kapısı + cihaz `RefuseSecret` (109 ile aynı iki kapı) |
| 102 | UIA read text | `operator.inspect` (ui_read) → "Şöyle yazıyor: …"; "Ekrandaki metni oku", "Ne yazıyor?" | Erişilebilir | DONE | PA | P1 | 99 | B29 | app/operator/plans.py:ui_read, tree_text; intents.py:_ui_read_match | test_operator_ui.py (37), korpus op.ui/op.see (34), NotepadLifecycleTests lab (6) | cihaz laboratuvarı bu masaüstünde yeşil (A_dialog_button_invoked_through_UI_Automation…); üretim turu Karar 0 | no | "Belgeyi oku" belge ailesinin kalır (DOCUMENT_READ) |
| 103 | UIA select item | `operator.ui` action=select; ui.select + kapsayıcıdan geri okuma (seçili düğüm) | Erişilebilir | DONE | PA | P1 | 99 | B29 | app/operator/plans.py:ui_select | test_operator_ui.py (37), korpus op.ui/op.see (34), NotepadLifecycleTests lab (6) | cihaz laboratuvarı bu masaüstünde yeşil (A_dialog_button_invoked_through_UI_Automation…); üretim turu Karar 0 | no | — |
| 104 | Screenshot capture | `operator.screenshot` → `screen.capture` (B27 çağıranı ekledi) | Erişilebilir | DONE | PA | P1 | 91 | B28 | devices/windows-agent; app/voice/realtime_sessions/tools_operator.py:operator_screenshot | test_daily_intent_tools.py (24), korpus d.shot.* | canlı cihaz kanıtı üretim turunda (Karar 0) | no | 735 ile ortak — B27 bulut çağıranını yazdı; ajan `screen.capture`ü M19'dan beri cevaplıyor. Gerçek cihazda görüntü alınması B28'in PROVEN_REAL turunda |
| 105 | Screenshot understanding | `operator.see`: screen.capture + `VisionProvider` (OpenAI / Fake / yok) | Görüntü anlamlandırılır | DONE | PU | P1 | 104 | B29 | app/operator/vision.py; tools_operator.py:operator_see | test_operator_ui.py (37), korpus op.see (11) | sağlayıcı anahtarı yok — "tanımlı değil" dürüst red ölçüldü; gerçek model cevabı sahibin OpenAI anahtarıyla (Karar 0 / checkpoint) | vision sağlayıcı anahtarı | CLAUDE.md gereği sağlayıcı arayüzü; anahtar `PAGENTOS_OPENAI_API_KEY` (aynı sahibin anahtarı); görüntü hiç saklanmaz, makbuzda yalnız boyut+sha256 |
| 106 | Vision-based fallback | `VisionProvider.locate` (OpenAI: locate sorusu, tek JSON cevap, `parse_location` - bulunamadı = None, düz yazı = hata) + görev döngüsünün 5. basamağı: ağaç bulamayınca `screen.capture` → `locate` → `pointer.click` (ekran uzayı) → ağaçtan BAĞIMSIZ doğrulama; sağlayıcı yoksa dürüst 'görsel sağlayıcı tanımlı değil' ile sahibe | Görüşe düşülebilir | DONE | PA | P2 | 105 | B39 | app/operator/vision.py:locate/parse_location; mission.py:_decide_visual; plans.py:visual_click | test_operator_b39.py (görsel basamak; sağlayıcısız ret; JSON ayrıştırma) | canlı: sağlayıcı anahtarı sahibin | sağlayıcı kararı (PAGENTOS_OPENAI_API_KEY) | Tarayıcı kuralındaki 5. seviye; koordinat yalnız görüntüden, asla tahminden |
| 107 | Coordinate only as last resort | Sunucu kapısı: `last_resort` yoksa RED (`coordinate_not_last_resort`), gerekçe makbuzda, `interaction_level` kayıtlı | Politika zorlanır | DONE | PA | P1 | 91 | B28 | tools_operator.py:operator_pointer; app/operator/service.py:_LEVEL_RANK | test_operator_input.py (33), korpus op.key/op.shortcut/op.scroll (37), NotepadLifecycleTests (lab, 5) | cihaz laboratuvarı bu masaüstünde yeşil (A_key_a_chord_and_a_scroll…); üretim turu Karar 0 | no | Tarayıcı kuralı genelleştirildi (spec §2 merdiveni): makbuz kullanılan EN YÜKSEK basamağı taşır |
| 108 | FocusGuard | Çalışıyor, güçlü | Aynı | DONE | PA | P1 | — | — | devices/windows-agent | FocusGuard testleri | kısmi gönderimde tam muhasebe | no | Sistemin en güçlü mekanizmalarından |
| 109 | Secret typing refusal | Sunucu kapısı (sözlüksel) + `secret` bayrağı cihaza GİDİYOR + cihaz `RefuseSecret` | Bayrak gönderilir + sunucu kapısı | DONE | PA | P0 | 91 | B28 | app/operator/plans.py:type_text; devices/windows-agent/.../OperatorCapabilities.cs:RefuseSecret | test_operator_input.py (33), korpus op.key/op.shortcut/op.scroll (37), NotepadLifecycleTests (lab, 5) | cihaz laboratuvarı bu masaüstünde yeşil (A_key_a_chord_and_a_scroll…); üretim turu Karar 0 | no | Güvenlik yarısı tamam: iki kapı; test iki tarafın kaynağını okuyor (`payload["secret"]`) |
| 110 | Per-action receipt | Her operatör eylemi tek makbuz; `steps_completed/step_count/stopped_at/interaction_level` | Her eylem makbuzlu | DONE | PA | P1 | 91 | B28 | app/operator/service.py:_receipt | test_operator_input.py (33), korpus op.key/op.shortcut/op.scroll (37), NotepadLifecycleTests (lab, 5) | cihaz laboratuvarı bu masaüstünde yeşil (A_key_a_chord_and_a_scroll…); üretim turu Karar 0 | no | Kısmi gönderim muhasebesi: FocusGuard reddinde makbuz 1/2 adım + durduran adımın adı |
| 111 | Postcondition verification | Her planın SON adımı postcondition taşır (yapısal test); UIA eylemleri bağımsız okumayla doğrulanır | Sonuç doğrulanmadan başarı yok | DONE | PA | P1 | 110 | B29 | app/operator/task.py:run_task; app/operator/plans.py; tests/unit/test_operator_ui.py:test_every_plan_ends_in_a_verified_postcondition | test_operator_ui.py (37), korpus op.ui/op.see (34), NotepadLifecycleTests lab (6) | cihaz laboratuvarı bu masaüstünde yeşil (A_dialog_button_invoked_through_UI_Automation…); üretim turu Karar 0 | no | 22 planın hepsi; "invoked" dedi ama öğe değişmedi = başarısızlık |
| 112 | Observe-Decide-Act-Verify | `app/operator/mission.py`: her turda ÖNCE gözlem (window.current + window.list), karar (taze gözlemden plan), eylem (`run_task`, aynı DeviceActionPort), doğrulama (planın kendi son koşulları), başarısızlıkta beyan edilmiş strateji; her tur izde | Kapalı döngü | DONE | PA | P2 | 111 | B39 | app/operator/mission.py:run_mission_step | test_operator_b39.py (karma görev, iz) | — | no | Model karar vermez: karar tablosu ve planlar deterministik |
| 113 | Dynamic replanning | Başarısız doğrulama → yeniden gözlem ve planın taze gözlemden yeniden kurulması (en çok 2); açık bir uygulama yeniden başlatılmaz, penceresi öne alınır | Plan çalışırken değişir | DONE | PA | P2 | 112 | B39 | app/operator/mission.py:_decide_app_open, STRATEGY_REOBSERVE | test_operator_b39.py (zaten açık → activate; ikinci bakışta başarı) | — | no | — |
| 114 | Retry by failure taxonomy | `STRATEGY_BY_ERROR_CLASS`: timeout/dependency_unavailable → yeniden dene (≤2); postcondition_failed/ui_target_not_found/focus_mismatch → yeniden gözle (≤2); permission_denied/validation_error/modal_open/capability_missing → sahibe; cancelled → dur; bilinmeyen sınıf → sahibe (asla tahmin) | Hata sınıfına göre | DONE | PA | P2 | 112 | B39 | app/operator/mission.py:STRATEGY_BY_ERROR_CLASS | test_operator_b39.py (tablo; geçici hata; izin reddi tek tur) | — | no | Tur sınırı 6 / adım |
| 115 | Recovery/escalation | Merdiven: UIA/klavye başarısızlığı → görsel basamak (bir kez); sonra `escalation` ile sahibe (adım, neden, cümle), görev `paused`; sahibin `resume`'ü adıma taze tur verir; defterde `operator.mission.escalated` | Seviye yükseltme | DONE | PA | P2 | 114 | B39 | app/operator/mission.py:_escalate/resume; mission_service.py | test_operator_b39.py (izin reddi → sahibe → resume → başarı) | — | no | — |
| 116 | App-specific adapters | `app/operator/adapters.py`: Not Defteri + Hesap Makinesi; belge sorgusu, diyalog düğmeleri, sözlü hedefler | Yapısal sürücüler | DONE | PA | P1 | 99 | B29 | app/operator/adapters.py; tools_operator.py:_ui_query | test_operator_ui.py (37), korpus op.ui/op.see (34), NotepadLifecycleTests lab (6) | cihaz laboratuvarı bu masaüstünde yeşil (A_dialog_button_invoked_through_UI_Automation…); üretim turu Karar 0 | no | "metni oku" Not Defteri'nde Edit denetimine gider; genel adaptör düğmeyi adıyla bulur |
| 117 | 6'dan fazla açılabilir uygulama | Tek sözleşme `packages/protocol/operator-allowlists.json` (7 uygulama, TR ad, imaj, takma adlar); bulut import'ta okur, cihaz tablosu C# testle eşit | Genişler + iki liste bağlanır | DONE | PA | P1 | 5 | B30 | app/operator/allowlists.py; OperatorAllowlistsContractTests.cs | test_operator_allowlists.py (16), test_operator_process_service.py (24), korpus op.app_close/op.process/op.service/op.shell.who (53), OperatorAllowlistsContractTests (4) + ProcessServiceTests (5, C#) | cihaz laboratuvarı bu masaüstünde yeşil (ProcessServiceTests 5/5, sözleşme 4/4); üretim turu Karar 0 | no | plans.APP_ALLOWLIST sözleşmenin kendisi; Python testi C# kaynağını da okur (M5 mutasyonu C# çalıştırmadan kırmızı) |
| 118 | Safe terminal expansion | Cihazın 8 deseni sözleşmede birebir; bulut komutları (hostname, ipconfig, whoami) her biri bir desenle eşleşir | Genişler | DONE | PA | P1 | 117 | B30 | app/operator/allowlists.py:SHELL_COMMANDS; intents.py:_shell_query_match whoami | test_operator_allowlists.py (16), test_operator_process_service.py (24), korpus op.app_close/op.process/op.service/op.shell.who (53), OperatorAllowlistsContractTests (4) + ProcessServiceTests (5, C#) | cihaz laboratuvarı bu masaüstünde yeşil (ProcessServiceTests 5/5, sözleşme 4/4); üretim turu Karar 0 | no | "Kullanıcı adım ne?" → whoami, alan adı düşürülür; desen kuralı (bileşim karakterleri, tek token) bulutta yeniden ifade edildi |
| 119 | Process inspect | `operator.process` list → `process.list` (Process tablosu; imaj/ad/pid/pencere sayısı, pencereli önce, 200 sınır) | Erişilebilir | DONE | PA | P1 | 117 | B30 | OperatorCapabilities.cs:ProcessList; tools_operator.py:operator_process | test_operator_allowlists.py (16), test_operator_process_service.py (24), korpus op.app_close/op.process/op.service/op.shell.who (53), OperatorAllowlistsContractTests (4) + ProcessServiceTests (5, C#) | cihaz laboratuvarı bu masaüstünde yeşil (ProcessServiceTests 5/5, sözleşme 4/4); üretim turu Karar 0 | no | "Chrome çalışıyor mu?" / "Hangi uygulamalar açık?" |
| 120 | Process stop with policy | `operator.process` stop → `process.stop` + `process.list` (yalnız sözleşmedeki imajlar; bulutta ve cihazda ayrı ayrı permission_denied, cihaza sorulmadan) | Politikayla | DONE | PA | P1 | 119 | B30 | app/operator/plans.py:process_stop; OperatorCapabilities.cs:ProcessStop | test_operator_allowlists.py (16), test_operator_process_service.py (24), korpus op.app_close/op.process/op.service/op.shell.who (53), OperatorAllowlistsContractTests (4) + ProcessServiceTests (5, C#) | cihaz laboratuvarı bu masaüstünde yeşil (ProcessServiceTests 5/5, sözleşme 4/4); üretim turu Karar 0 | no | WM_CLOSE önce, modal bildirilir, force yalnız sahibin sözüyle; lab ilk koşuda sahibin kendi Not Defteri'ne ulaştı ve doğru davrandı (ADR-0137) |
| 121 | Service inspect | `operator.service` status → `service.status` (Win32_Service: state/start_mode/pid) | Erişilebilir | DONE | PA | P1 | 119 | B30 | OperatorCapabilities.cs:ServiceStatus; tools_operator.py:operator_service | test_operator_allowlists.py (16), test_operator_process_service.py (24), korpus op.app_close/op.process/op.service/op.shell.who (53), OperatorAllowlistsContractTests (4) + ProcessServiceTests (5, C#) | cihaz laboratuvarı bu masaüstünde yeşil (ProcessServiceTests 5/5, sözleşme 4/4); üretim turu Karar 0 | no | "Yazdırma servisi çalışıyor mu?" → Spooler; enjeksiyon biçimli ad validation_error |
| 122 | Service restart with policy | `operator.service` restart → `service.restart` + `service.status` (yalnız sözleşmedeki servisler; yükseltilmemiş companion permission_denied ile UAC'yi adlandırır, hiçbir şeye dokunmaz) | Politikayla | DONE | PA | P1 | 121 | B30 | app/operator/plans.py:service_restart; OperatorCapabilities.cs:ServiceRestart | test_operator_allowlists.py (16), test_operator_process_service.py (24), korpus op.app_close/op.process/op.service/op.shell.who (53), OperatorAllowlistsContractTests (4) + ProcessServiceTests (5, C#) | cihaz laboratuvarı bu masaüstünde yeşil (ProcessServiceTests 5/5, sözleşme 4/4); üretim turu Karar 0 | no | Katman CRITICAL; gerçek yeniden başlatma READY_FOR_OWNER (companion sahibin oturumunda yükseltilmemiş — checkpoint) |
| 123 | Windows Settings navigation | `settings` uygulaması iki taraflı izin listesinde (SystemSettings.exe; ApplicationFrameHost penceresi BAŞLIKLA tanınır); `plans.open_settings(page)`: başlat/öne al → arama kutusuna sayfa adı (`ui.set_value`) → Enter → ağaçta sayfa; `SETTINGS_PAGES` sözlüğü (bluetooth, wifi, ekran, ses, güncelleme…) | Gezinilir | DONE | PA | P2 | 100 | B39 | app/operator/plans.py:open_settings; packages/protocol/operator-allowlists.json; OperatorCapabilities.cs | test_operator_b39.py; test_operator_allowlists.py; OperatorAllowlistsContractTests (C#, 111 yeşil) | laboratuvar ölçümü READY_FOR_OWNER (checkpoint 16) | no | Sayfa arama yolu uygulamanın belgelenen arayüzünden; masaüstünde ölçüm bekliyor |
| 124 | File Explorer operations | `plans.explorer_open(target, titles)`: explorer başlat → pencere görüntüyle bulunur → Ctrl+L → `shell:Downloads` gibi yerelden bağımsız hedef yazılır → Enter → pencere BAŞLIĞI klasör adı; `EXPLORER_FOLDERS` (İndirilenler, Belgeler, Masaüstü, Resimler, Videolar, Müzik) | Kullanılır | DONE | PA | P2 | 100 | B39 | app/operator/plans.py:explorer_open; mission.py:EXPLORER_FOLDERS | test_operator_b39.py (adres çubuğu yolu; başlık doğrulaması) | laboratuvar ölçümü READY_FOR_OWNER | no | Klasör içinde arama belge ailesinde kalır (korpus doc.search.5) |
| 125 | Office app interaction | Adaptörler `WORD` (Document kontrolü, Kaydet/Kaydetme/İptal) ve `EXCEL` (belge sorgusu yok; klavye sayımı + ön plan ile doğrulanır); `plans.office_type`: öne al → yaz → adaptörün belge kontrolünden okuma; pencere yoksa adıyla sahibe | Sürülür | DONE | PA | P2 | 116 | B39 | app/operator/adapters.py:WORD/EXCEL; plans.py:office_type | test_operator_b39.py (Word yaz+oku; Excel adaptörü; pencere yok) | laboratuvar ölçümü READY_FOR_OWNER (Office derleme makinesinde yok) | no | Adaptörler belgelenen UIA yüzeyinden beyan edildi |
| 126 | IDE interaction | Adaptör `VSCODE` (quick_open Ctrl+P, komut paleti, kaydet); `plans.ide_open_file`: öne al → Ctrl+P → dosya adı → Enter → pencere BAŞLIĞI dosyayı adlar | Sürülür | DONE | PA | P2 | 116 | B39 | app/operator/adapters.py:VSCODE; plans.py:ide_open_file | test_operator_b39.py (kısayol adaptörden; başlık doğrulaması) | laboratuvar ölçümü READY_FOR_OWNER | no | Electron penceresi: ağaç değil, belgelenen kısayollar ve başlık |
| 127 | Browser + desktop mixed plan | `plan_mission`: 've / sonra / ardından' ile bölünen cümle → adımlar (app_open, navigate, type_text, ui_invoke, window_close, settings_open, explorer_open, ide_open_file, office_type); 'Chrome'u aç ve YouTube'a gir' = app.launch + browser.session_open (sahibin tarayıcısı, olmazsa kendi profili - dürüstçe) + browser.navigate + browser.inspect (ikinci okuma) AYNI cihaz portunda; sesle `operator.mission`, REST POST /v1/operator/missions | Karma plan | DONE | PA | P2 | 112 | B39 | app/operator/mission.py:plan_mission; plans.py:browser_navigate; tools_mission.py; mission_routes.py | test_operator_b39.py (karma görev uçtan uca; planlayıcı; rota; ses); korpus | üretim turu READY_FOR_OWNER (Karar 0) | no | Tek basit adım eski aracında kalır (APP_OPEN vb.) |
| 128 | Multi-step task persistence | `operator_missions` satırı (göç 0051: adımlar, iz, yükseltme, pause/cancel istekleri) + `OperatorMissionWorkflow` (Temporal; adım başına bir aktivite, sahibin sözü için bekleme) + `MissionService` (start/approve/pause/resume/cancel, aktif görev tekil) | Kalıcı | DONE | PA | P2 | 535 | B39 | app/operator/mission_models.py; mission_service.py; mission_workflow.py; mission_activities.py | test_operator_b39.py (satır yaşam döngüsü; gerçek Temporal test ortamında iş akışı) | — | no | Satır gerçek, iş akışı sürücü |
| 129 | Operator pause/cancel | Sesle 'Bekle/Duraklat' (MISSION_PAUSE), 'Devam et' (MISSION_RESUME), 'Dur' (operator.cancel görev varsa göreve devreder), 'Ne yapıyorsun?' (durum); REST pause/resume/cancel; istek satıra yazılır, bir sonraki turda uygulanır; iş akışı sinyalleri | Duraklat/iptal | DONE | PA | P2 | 128 | B39 | app/voice/intents.py:MISSION_*; tools_mission.py:mission_control; mission_service.py | test_operator_b39.py (turlar arası iptal; adımlar arası duraklat/devam; rota) | — | no | Kontrol sözleri yalnız görev park/yürürken (mission_state) |
| 130 | "Show me before acting" | '… önce göster / planı göster / önizle' → görev `awaiting_approval`, plan cümle olarak söylenir ('Planım şu: 1. … 2. …. Başlayayım mı?'), hiçbir cihaz çağrısı yapılmaz; 'Evet, başla / Onaylıyorum' (MISSION_APPROVE) ya da REST approve ile başlar | Önizleme modu | DONE | PA | P2 | 110 | B39 | app/operator/mission.py:Mission.preview/plan_speech; mission_service.py:approve_db | test_operator_b39.py (önizleme cihazı çağırmaz; onay; ses) | — | no | Güven için değerli |

## E. FILE / DOCUMENT INTELLIGENCE (131–170)

| ID | FEATURE | CURRENT_STATUS | TARGET_STATUS | IMPL | PROOF | PRI | DEPS | BATCH | SOURCE_REFERENCES | TEST_REFERENCES | RUNTIME_PROOF | OWNER_ACTION | NOTES |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 131 | PDF read | PdfPig ile gerçek | Aynı | DONE | PA | P1 | — | — | devices/windows-agent | oracle fikstürleri | — | no | Rewrite gerekmez |
| 132 | DOCX read | Open XML SDK | Aynı | DONE | PA | P1 | — | — | devices/windows-agent | oracle fikstürleri | — | no | — |
| 133 | XLSX read | Open XML SDK | Aynı | DONE | PA | P1 | — | — | devices/windows-agent | oracle fikstürleri | — | no | — |
| 134 | PPTX read | Open XML SDK | Aynı | DONE | PA | P1 | — | — | devices/windows-agent | oracle fikstürleri | — | no | — |
| 135 | CSV read | Gerçek | Aynı | DONE | PA | P1 | — | — | devices/windows-agent | oracle fikstürleri | — | no | — |
| 136 | JSON read | Gerçek | Aynı | DONE | PA | P1 | — | — | devices/windows-agent | oracle fikstürleri | — | no | — |
| 137 | Markdown read | Gerçek | Aynı | DONE | PA | P1 | — | — | devices/windows-agent | oracle fikstürleri | — | no | — |
| 138 | Source code read | Gerçek | Aynı | DONE | PA | P1 | — | — | devices/windows-agent | oracle fikstürleri | — | no | — |
| 139 | Image metadata read | `file.inspect` → `image: {width,height,format,dpi,frames,metadata{date_taken,camera,application,title}}` (WPF BitmapDecoder, piksel çözülmez); `document.inspect` sesle söyler | Okunur | DONE | PA | P1 | — | B32 | Documents/ImageExtractor.cs:ReadHeaders; app/documents/service.py:_inspect_speech | test_documents_b32.py (15), korpus doc.preview/doc.text/doc.dupes/doc.dedup/doc.image/doc.archive/doc.cmp.two (177 belge vakası), C# ImageArchiveTests (5) + ExtractionOracleTests (oracle metin.png) | cihaz laboratuvarı bu masaüstünde yeşil (Windows.Media.Ocr tr: 'Merhaba Dünya 1234' birebir; arşiv dizini; Recycle Bin); üretimde bir görselden metin Karar 0 | no | "Fotoğrafın bilgilerini oku" |
| 140 | OCR | Yerel motor: Windows.Media.Ocr (sahibin kurulu dil paketleri, tr önce), companion'da PowerShell WinRT konağı (`OcrHost`, EncodedCommand, yol/dil ortam değişkeninden) → `document.extract` `o<n>` satırları | Metin çıkar | DONE | PA | P1 | 139 | B32 | Documents/OcrHost.cs; ImageExtractor.cs:Extract | test_documents_b32.py (15), korpus doc.preview/doc.text/doc.dupes/doc.dedup/doc.image/doc.archive/doc.cmp.two (177 belge vakası), C# ImageArchiveTests (5) + ExtractionOracleTests (oracle metin.png) | cihaz laboratuvarı bu masaüstünde yeşil (Windows.Media.Ocr tr: 'Merhaba Dünya 1234' birebir; arşiv dizini; Recycle Bin); üretimde bir görselden metin Karar 0 | sağlayıcı kararı: yerel Windows OCR seçildi (checkpoint: sahip başka motor isterse `IDocumentExtractor` arkasında) | Dil paketi yoksa dependency_unavailable/ocr_language_missing — asla boş başarı; 496 ile aynı iş |
| 141 | Image text extraction | `document.read` görselde OCR satırlarını söyler ('görselinde şu yazıyor: …'); Intent IMAGE_TEXT ('görseldeki metni oku', ekran-okuma ailesinden önce) | Kullanılabilir | DONE | PA | P1 | 140 | B32 | app/documents/service.py:read; intents.py:_image_text_match | test_documents_b32.py (15), korpus doc.preview/doc.text/doc.dupes/doc.dedup/doc.image/doc.archive/doc.cmp.two (177 belge vakası), C# ImageArchiveTests (5) + ExtractionOracleTests (oracle metin.png) | cihaz laboratuvarı bu masaüstünde yeşil (Windows.Media.Ocr tr: 'Merhaba Dünya 1234' birebir; arşiv dizini; Recycle Bin); üretimde bir görselden metin Karar 0 | no | app/files/ yerine app/documents/ (mevcut aile) |
| 142 | Archive inspect | `archive` türü: `file.inspect` merkezi dizinden `{entry_count, entries[name,size,compressed_size,modified,kind], total_uncompressed, truncated}`; asla çıkarılmaz (`unsupported_format`) | İçerik görülür | DONE | PA | P1 | — | B32 | Documents/ArchiveInspector.cs; FileKinds.cs:Archive | test_documents_b32.py (15), korpus doc.preview/doc.text/doc.dupes/doc.dedup/doc.image/doc.archive/doc.cmp.two (177 belge vakası), C# ImageArchiveTests (5) + ExtractionOracleTests (oracle metin.png) | cihaz laboratuvarı bu masaüstünde yeşil (Windows.Media.Ocr tr: 'Merhaba Dünya 1234' birebir; arşiv dizini; Recycle Bin); üretimde bir görselden metin Karar 0 | no | "Arşivin içinde ne var?" → document.inspect |
| 143 | EPUB read | Yok | Okunur | MISSING | NYP | P2 | — | B52 | devices/windows-agent | — | — | no | — |
| 144 | RTF read | Yok | Okunur | MISSING | NYP | P2 | — | B52 | devices/windows-agent | — | — | no | — |
| 145 | ODT read | Yok | Okunur | MISSING | NYP | P2 | — | B52 | devices/windows-agent | — | — | no | — |
| 146 | Legacy Office formats | Yok | .doc/.xls/.ppt okunur | MISSING | NYP | P2 | — | B52 | devices/windows-agent | — | — | no | — |
| 147 | File metadata search | Klasör araması çalışıyor | Çalışır | DONE | PA | P0 | 3 | B03 | app/documents/service.py | test_documents_confinement.py (15) | — | no | 3'ün doğrudan sonucu |
| 148 | Full text search | `document.find_text`: okunmuş belgelerin METNİ üzerinde, retrieval'ın kendi kuralıyla (tam kelime 2 / kök 1), belge başına en iyi blok, sıralı; tarama yok, kaç belgeye bakıldığı söylenir | Gerçek tam metin | DONE | PA | P1 | 3 | B32 | app/documents/service.py:find_text; intents.py:_document_find_text_match | test_documents_b32.py (15), korpus doc.preview/doc.text/doc.dupes/doc.dedup/doc.image/doc.archive/doc.cmp.two (177 belge vakası), C# ImageArchiveTests (5) + ExtractionOracleTests (oracle metin.png) | cihaz laboratuvarı bu masaüstünde yeşil (Windows.Media.Ocr tr: 'Merhaba Dünya 1234' birebir; arşiv dizini; Recycle Bin); üretimde bir görselden metin Karar 0 | no | Postgres tsvector/GIN yok — dizin küçükken Python; büyürse 685 ile |
| 149 | Semantic document search | `documents/semantic.py` (blok = sözcük örtüşmesi + gömme kosinüsü, bileşenleriyle; belge adı da bir blok) + `GET /v1/documents/search?q=` (indeksli belgeler; cevap hangi gömmenin hizmet verdiğini söyler) + `retrieval.top_k(embedder=)` cevap yolunda yeniden sıralama (bellek çalışma zamanının AYNI gömmesi) | Anlamsal arama | DONE | PA | P2 | 51 | B37 | app/documents/semantic.py; retrieval.py:top_k; service.py:search_indexed; routes.py | test_memory_b37.py (bileşenli sıralama; rota) | — | no | Gerçek anlam sağlayıcı anahtarıyla; karma dürüstçe 'semantic: false' |
| 150 | File deduplication | `document.dedup`: az önce DUYULAN teklifteki kopyalar `file.trash` ile ÇÖP KUTUSUNA (Recycle Bin, geri alınabilir; asla kalıcı silme), her taşıma cihazın `observed.exists`'inden okunur; teklif olmadan soru | Sadeleşir | DONE | PA | P1 | 151 | B32 | app/documents/service.py:dedup; Documents/DocumentCapabilities.cs:Trash; ProtocolConstants.cs:FileTrash | test_documents_b32.py (15), korpus doc.preview/doc.text/doc.dupes/doc.dedup/doc.image/doc.archive/doc.cmp.two (177 belge vakası), C# ImageArchiveTests (5) + ExtractionOracleTests (oracle metin.png) | cihaz laboratuvarı bu masaüstünde yeşil (Windows.Media.Ocr tr: 'Merhaba Dünya 1234' birebir; arşiv dizini; Recycle Bin); üretimde bir görselden metin Karar 0 | no | Katman CRITICAL; ACTING_INTENTS'te; cihaz ailesi 7→8 |
| 151 | Duplicate detection | `document.duplicates`: `file.search` listeler, ≤8 MiB her isabet `file.locate` ile sha256 alır, aynı özet gruplanır; teklif (en eski kalır, diğerleri gider), hiçbir şey taşınmaz | Tespit | DONE | PA | P1 | — | B32 | app/documents/service.py:duplicates | test_documents_b32.py (15), korpus doc.preview/doc.text/doc.dupes/doc.dedup/doc.image/doc.archive/doc.cmp.two (177 belge vakası), C# ImageArchiveTests (5) + ExtractionOracleTests (oracle metin.png) | cihaz laboratuvarı bu masaüstünde yeşil (Windows.Media.Ocr tr: 'Merhaba Dünya 1234' birebir; arşiv dizini; Recycle Bin); üretimde bir görselden metin Karar 0 | no | Fikstür: yedek/veri-kopya.csv (bayt-aynı) |
| 152 | File preview | `document.preview`: tür + kendi biriminde boyut (sayfa/sayfa/slayt/satır/piksel) + ilk 240 karakter; arşiv için dizin | Önizleme | DONE | PA | P1 | — | B32 | app/documents/service.py:preview,_preview_facts | test_documents_b32.py (15), korpus doc.preview/doc.text/doc.dupes/doc.dedup/doc.image/doc.archive/doc.cmp.two (177 belge vakası), C# ImageArchiveTests (5) + ExtractionOracleTests (oracle metin.png) | cihaz laboratuvarı bu masaüstünde yeşil (Windows.Media.Ocr tr: 'Merhaba Dünya 1234' birebir; arşiv dizini; Recycle Bin); üretimde bir görselden metin Karar 0 | no | Web DocumentKind image/archive eklendi |
| 153 | File edit | `document.edit`: 'bu dosyada X yerine Y yaz' → dosyanın metni `file.read` ile okunur, sözcük SÖYLENDİĞİ gibi (Türkçe casefold, ASR aksanı düşürmüşse aksansız) değiştirilir, `file.write` planı + okunan hash ile ÖNERİ satırı; 'uygula' demeden hiçbir şey yazılmaz; sözcük geçmiyorsa `text_not_found` | Yönetilen düzenleme | DONE | PA | P2 | 160 | B34 | app/documents/mutations.py:edit; tools_documents.py:document_edit; intents.py:_document_edit_match | test_documents_b34.py (edit propose→apply, not found, office, stale), korpus doc.edit.* | Karar 0 | no | Office türleri byte düzeyinde düzenlenmez (`not_text`) |
| 154 | File write | `document.write`: 'X adında bir dosya oluştur' + içerik → Belgeler'de (ya da söylenen kova klasörde) yeni metin dosyası; düşük risk, hemen uygulanır, günlüğe yazılır, geri alınabilir (çöp kutusu + yedek) | Yönetilen yazma | DONE | PA | P2 | 160 | B34 | mutations.py:write; FileMutations.cs:Write | test_documents_b34.py (write/undo, refusals), C# FileMutationTests, korpus doc.write.* | lab gerçek atomik yazma | no | kova: documents/desktop/downloads (cihaz çözer) |
| 155 | File append | `document.append`: 'bu dosyanın sonuna ... ekle' → `file.append` (expected_sha256 ile), düşük risk, yedek alınır, geri alınabilir | Ekleme | DONE | PA | P2 | 154 | B34 | mutations.py:append; FileMutations.cs:Append | test_documents_b34.py, C# FileMutationTests, korpus doc.append.* | lab gerçek | no | — |
| 156 | File rename | `document.rename`: 'bu dosyanın adını X yap' → öneri → `file.rename` (uzantı söylenmediyse korunur); geri alma eski ada döner | Yeniden adlandırma | DONE | PA | P2 | 160 | B34 | mutations.py:rename; FileMutations.cs:Rename | test_documents_b34.py (rename/undo), C#, korpus doc.rename.* | lab gerçek | no | var olan adın üstüne asla |
| 157 | File move | `document.move`: 'bu dosyayı Masaüstüne taşı' → öneri (kritik) → `file.move {destination_folder}`; geri alma eski klasöre taşır | Taşıma | DONE | PA | P2 | 160 | B34 | mutations.py:move; FileMutations.cs:Move | test_documents_b34.py, C#, korpus doc.move.* | lab gerçek | no | — |
| 158 | File copy | `document.copy`: 'bu dosyayı kopyala / X adıyla / Masaüstüne' → öneri → `file.copy` (asla üstüne yazmaz; ad söylenmediyse '- kopya'); geri alma kopyayı çöpe (yedekli) gönderir | Kopyalama | DONE | PA | P2 | 154 | B34 | mutations.py:copy; FileMutations.cs:Copy | test_documents_b34.py, C#, korpus doc.copy.* | lab gerçek | no | — |
| 159 | File delete | `document.delete`: 'bu dosyayı sil' → öneri (kritik) → `file.trash {backup:true}`: Recycle Bin + geri alma deposunda kopya; kalıcı silme hiçbir yerde yok | Politikayla silme | DONE | PA | P2 | 161,678 | B34 | mutations.py:delete; DocumentCapabilities.cs:Trash(backup) | test_documents_b34.py (delete/restore), C# (trash+restore), korpus doc.neg.delete (öneri) | lab gerçek | onay politikası (kalıcı silme: READY_FOR_OWNER) | ADR-0083 karar 7 bu batch'le değişti |
| 160 | Undo journal | `file_mutations` tablosu (göç 0047): öneri satırı cihazdan ÖNCE, sonuç cihazın CEVABINDAN; her uygulanan satırda cevaptan türetilen ters plan; `document.undo` tersi çalıştırır ve geri okur; cihaz tarafı `.pagentos-undo` deposu (yedek + sidecar, sha256, 500 kayıt) | Her mutasyon geri alınabilir | DONE | PA | P2 | — | B34 | app/documents/models.py:FileMutationRow; mutations.py:undo,_undo_plan; FileMutations.cs:Backup,Restore | test_documents_b34.py (her akışta undo), C# (restore, bozuk yedek reddi), mutasyon M4 | lab: gerçek yedek ve geri yükleme | no | 153-159'un ÖN KOŞULU — kapandı |
| 161 | Trash/recycle integration | silme = Recycle Bin (`file.trash`, B32) + B34 `backup:true` ile depoya kopya; `file.restore` geri getirir | Geri dönüşüme gider | DONE | PA | P2 | 160 | B34 | DocumentCapabilities.cs:Trash; FileMutations.cs:Restore | C# FileMutationTests (trash with backup + restore) | lab gerçek Recycle Bin | no | — |
| 162 | Mutation receipt | her mutasyon `ActionReceipt` (documents alt sistemi) + ledger `document.mutation_proposed/applied/undone/discarded`; makbuz satırdan derlenir | Makbuzlu | DONE | PA | P2 | 160 | B34 | mutations.py:_apply_row,_receipt | test_documents_b34.py | — | no | — |
| 163 | Before/after hash | satırda sha_before/sha_after/size; öneri okunan hash'i `expected_sha256` olarak taşır, cihaz uymazsa yazmaz (`content_changed`); cevapta 'after' hash yoksa satır `failed` | Hash'li kanıt | DONE | PA | P2 | 162 | B34 | mutations.py:_verify; FileMutations.cs:RequireExpected | test_documents_b34.py (stale proposal, verify unit), C# (changed file refused), mutasyon M2/M7 | lab gerçek | no | — |
| 164 | Document version history | `document.versions`: yolun günlük satırları (uygulanan/geri alınan), zaman + tür; REST `GET /v1/documents/mutations` | Sürüm geçmişi | DONE | PA | P2 | 160 | B34 | mutations.py:versions; routes.py | test_documents_b34.py, korpus doc.versions.* | — | no | — |
| 165 | Safe atomic write | cihaz `FileMutations.WriteAtomic`: hedefin yanında geçici dosya + tek yeniden adlandırma; BOM korunur | Her yerde | DONE | PA | P2 | 154 | B34 | FileMutations.cs:WriteAtomic | C# FileMutationTests (geçici dosya kalmaz) | lab gerçek | no | artefakt yarısıyla aynı disiplin |
| 166 | Owner approval by risk | `risk_of`: yeni dosya/ekleme düşük (hemen), üstüne yazma/düzenleme/yeniden adlandırma/kopyalama hassas, taşıma/silme kritik; hassas+kritik ÖNERİ → okunma + onay kapısı (mail taslağıyla aynı `check_gate`): sesli 'uygula/kaydet' (yalnız bu oturuma okunmuşsa; router `mutation_pending` ile) ya da panelde Onayla; başka oturum `not_read_back`, ikinci onay `nothing_pending` | Riskli mutasyon onay ister | DONE | PA | P2 | 160,674 | B34 | mutations.py:apply,risk_of; intents.py:_document_apply_match; routes.py; apps/web approvals.ts/useApprovalPair.ts/CockpitPanels.tsx | test_documents_b34.py (gate, cross-session, REST), korpus doc.apply.*, web mutations-approvals (6), mutasyon M1/M3/M5 | — | onay politikası | — |
| 167 | "Bu dosyayı düzenle" | `_document_edit_match`: 'X yerine Y yaz' (bul/değiştir) — find/replace turdan araca taşınır; operatörün 'yaz'ından önce çözülür | Doğal akış | DONE | PA | P2 | 153 | B34 | intents.py:_document_edit_match; service.py (last_utterance find_text/replace_text) | test_documents_b34.py (router ×17), korpus doc.edit.* | — | no | — |
| 168 | "Şu klasördeki dosyaları özetle" | 'Şu klasördeki dosyaları özetle' çalışıyor | Çalışır | DONE | PA | P0 | 3 | B03 | app/documents/service.py | test_file_search_roots_contract.py | — | no | 3'ün doğrudan sonucu |
| 169 | "Bu iki dokümanı karşılaştır" | 'doküman' belge adı oldu; executive `documents.compare` artık planlayıcının hedeflerini (`s1.document_refs`) kullanıyor (eskiden yok sayılıyordu) | Çalışır | DONE | PA | P1 | 3 | B32 | app/executive/activities.py:_kind_documents_compare; intents.py:_DOCUMENT_NOUN_STEMS | test_documents_b32.py (15), korpus doc.preview/doc.text/doc.dupes/doc.dedup/doc.image/doc.archive/doc.cmp.two (177 belge vakası), C# ImageArchiveTests (5) + ExtractionOracleTests (oracle metin.png) | cihaz laboratuvarı bu masaüstünde yeşil (Windows.Media.Ocr tr: 'Merhaba Dünya 1234' birebir; arşiv dizini; Recycle Bin); üretimde bir görselden metin Karar 0 | no | 548 ile aynı plan; korpus doc.cmp.two |
| 170 | "Bu belgeyi güncelle ve kaydet" | aynı DOCUMENT_EDIT şekli, yeni içerik modelin `content` argümanı; öneri → 'kaydet' uygular | Güncelle ve kaydet | DONE | PA | P2 | 153 | B34 | intents.py; tools_documents.py:document_edit | korpus doc.edit.update_save, doc.edit.office_refused | — | no | Office belgesi `not_text` ile reddedilir |

## F. BROWSER / INTERNET / RESEARCH (171–210)

| ID | FEATURE | CURRENT_STATUS | TARGET_STATUS | IMPL | PROOF | PRI | DEPS | BATCH | SOURCE_REFERENCES | TEST_REFERENCES | RUNTIME_PROOF | OWNER_ACTION | NOTES |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 171 | Managed Chrome | Ayrı profille çalışıyor | Aynı | DONE | PR | P1 | — | — | app/browser/, devices/windows-agent | browser testleri | üretim araştırma koşuları | no | Rewrite gerekmez |
| 172 | Owner Chrome reuse | Kayıtlı ama otonom araştırmaya KAPALI (ADR-0113 sınırı testle tutuluyor: enroll betiği $false yazar, bulut bayrağa dokunmaz, gateway yalnız `research` profili açar) | Denetimli kullanım | DONE | PA | P1 | 189 | B31 | services/api/tests/unit/test_browser_transfer_contract.py; services/browser/browser_agent/worker.py:require_research_authorization | services/browser test_transfer_policy.py (22) + test_capabilities/test_capability_mirrors, services/api test_browser_transfer_contract.py (5), C# BrowserDispatchTests (29 op) | sınır korunuyor; sahibin Chrome'unu otonom araştırmaya AÇMAK mahremiyet kararı — checkpoint (READY_FOR_OWNER) | mahremiyet kararı | ADR-0113 bilinçli sınır; B31 sınırı test altına aldı, açmadı |
| 173 | Session reuse | Süreç içi bilinen-açık kayıt + worker `created:false`/`lifecycle.reused`; yeniden başlatma sonrası tek yeniden açma | Açık oturum kullanılır | DONE | PA | P1 | 172 | B31 | app/research/browser_gateway.py:ensure_session | services/browser test_transfer_policy.py (22) + test_capabilities/test_capability_mirrors, services/api test_browser_transfer_contract.py (5), C# BrowserDispatchTests (29 op) | fake Temporal ile yerel; üretimde sesle iptal/duraklatma Karar 0 (READY_FOR_OWNER) | no | test_a_live_session_is_reused_in_process_and_across_a_restart |
| 174 | Search | Çalışıyor | Aynı | DONE | PR | P1 | — | — | app/browser/ | browser testleri | üretim | no | — |
| 175 | Navigation | Çalışıyor | Aynı | DONE | PR | P1 | — | — | app/browser/ | browser testleri | üretim | no | — |
| 176 | DOM inspect | Çalışıyor | Aynı | DONE | PA | P1 | — | — | app/browser/ | browser testleri | — | no | Anlamsal hedefleme (rol/metin/etiket) |
| 177 | DOM click | Çalışıyor | Aynı | DONE | PA | P1 | — | — | app/browser/ | browser testleri | — | no | — |
| 178 | DOM text entry | Çalışıyor | Aynı | DONE | PA | P1 | — | — | app/browser/ | browser testleri | — | no | — |
| 179 | Form fill | Çalışıyor | Aynı | DONE | PA | P1 | — | — | app/browser/ | browser testleri | — | no | — |
| 180 | Download | HIGH_IMPACT + biçimli `authorization_ref` (8–128, [A-Za-z0-9._:-]); 64 MiB sınır (çağrıda düşürülebilir, asla artırılamaz); aşan indirme silinip reddedilir; `save_dir` file_io_root dışına çıkamaz | Sınırlı ve yetkili | DONE | PA | P1 | — | B31 | services/browser/browser_agent/worker.py:_op_download,require_transfer_authorization,enforce_transfer_size; session.py:download | services/browser test_transfer_policy.py (22) + test_capabilities/test_capability_mirrors, services/api test_browser_transfer_contract.py (5), C# BrowserDispatchTests (29 op) | worker birim testleri (tarayıcısız) | no | Sözleşme v1.5; onay kaydı eşleştirmesi (approval registry) sonraki batch — biçim + politika bugün |
| 181 | Upload | `browser.upload` VAR: worker `_op_upload` (uploads/ altından, aynı kapı, aynı sınır, sayfa görmeden), policy HIGH_IMPACT, C# `Upload`, bulut allowlist, 3 PowerShell listesi; bayrak→işlem testi | Çalışır | DONE | PA | P1 | — | B31 | services/browser/browser_agent/worker.py:_op_upload; ProtocolConstants.cs:Upload | services/browser test_transfer_policy.py (22) + test_capabilities/test_capability_mirrors, services/api test_browser_transfer_contract.py (5), C# BrowserDispatchTests (29 op) | worker birim testleri (tarayıcısız) | no | "yalan duyuru" kapandı: test_every_capability_flag_that_names_an_operation_has_a_handler |
| 182 | Multi-tab handling | Çalışıyor | Aynı | DONE | PA | P1 | — | — | app/browser/ | browser testleri | — | no | — |
| 183 | Tab focus | Çalışıyor | Aynı | DONE | PA | P1 | — | — | app/browser/ | browser testleri | — | no | — |
| 184 | Browser screenshot | Çalışıyor | Aynı | DONE | PA | P1 | — | — | app/browser/ | browser testleri | — | no | — |
| 185 | CAPTCHA detection | Tespit + soğuma | Aynı | DONE | PA | P0 | — | — | app/browser/ | browser testleri | — | no | — |
| 186 | CAPTCHA bypass yasak | Zorlanıyor | Aynı | DONE | PA | P0 | — | — | app/browser/ | browser testleri | — | no | Ürün ilkesi — asla gevşetilmez |
| 187 | SSRF guard | İki yakada her gezinmede zorlanıyor | Aynı | DONE | PA | P0 | — | — | app/browser/, devices/windows-agent | browser testleri | — | no | Rewrite gerekmez |
| 188 | Private network guard | Zorlanıyor | Aynı | DONE | PA | P0 | — | — | app/browser/ | browser testleri | — | no | — |
| 189 | Owner research authorization | Gerçekten zorlanıyor | Aynı | DONE | PA | P0 | — | — | app/research/, app/security/ | research testleri | owner_authorized_for_research=false | no | ADR-0113 |
| 190 | QUICK research | Çalışıyor | Aynı | DONE | PR | P1 | — | — | app/research/ | research testleri | üretim koşuları | no | — |
| 191 | STANDARD research | Çalışıyor | Aynı | DONE | PR | P1 | — | — | app/research/ | research testleri | üretim koşuları | no | — |
| 192 | DEEP research | Sesten: sahibin sözündeki mod turn'de taşınır (`research_mode`), `research.start` modun KENDİ politika tavanıyla başlar (deep 24, quick için ayar tabanı 12); model yalnız daraltabilir; web'de mod seçici | Gerçekten koşar | DONE | PA | P1 | — | B31 | app/voice/realtime_sessions/tools.py:_voice_max_sources; intents.py:research_mode; apps/web/app/research/page.tsx | test_research_control.py (16), test_voice_research_b31.py (11), korpus r.hold/r.continue/r.open/r.mode (12), test_research_routes.py | üretimde bir DEEP koşusu Karar 0 (READY_FOR_OWNER) | no | 'sesten 12 kaynağa kırpılıyor' ölçüldü ve kapandı |
| 193 | Source ranking | Çalışıyor | Aynı | DONE | PA | P1 | — | — | app/research/ | research testleri | — | no | — |
| 194 | Domain diversity | Çalışıyor | Aynı | DONE | PA | P1 | — | — | app/research/ | research testleri | — | no | — |
| 195 | Challenge skip | Çalışıyor | Aynı | DONE | PA | P1 | — | — | app/research/ | research testleri | — | no | — |
| 196 | Source cooldown | Çalışıyor | Aynı | DONE | PA | P1 | — | — | app/research/ | research testleri | — | no | — |
| 197 | Citation binding | Çalışıyor | Aynı | DONE | PA | P1 | — | — | app/research/ | research testleri | kanıt sözleşmesi | no | İnce sonuçta dürüst itiraf |
| 198 | Research focus/current report | Odak yığını + `Bu araştırmayı anlat` (korpus r.cur.* 7 vaka) + research.open odağı taşır | Odak çalışır | DONE | PA | P1 | — | B31 | app/research/focus.py; tools.py:_resolve_research | test_research_control.py (16), test_voice_research_b31.py (11), korpus r.hold/r.continue/r.open/r.mode (12), test_research_routes.py | fake Temporal ile yerel; üretimde sesle iptal/duraklatma Karar 0 (READY_FOR_OWNER) | no | B31 ölçümü: odak zaten çalışıyordu; açma ve atıf üstüne geldi |
| 199 | Previous report references | Aynı konudaki daha eski tamamlanmış araştırma her takip cevabında ANILIR (`previous_report` + 'Bu konuda daha önce de bir araştırma var: … raporu.') | Atıf yapılır | DONE | PA | P1 | 198 | B31 | tools.py:_previous_report_on_topic | test_research_control.py (16), test_voice_research_b31.py (11), korpus r.hold/r.continue/r.open/r.mode (12), test_research_routes.py | fake Temporal ile yerel; üretimde sesle iptal/duraklatma Karar 0 (READY_FOR_OWNER) | no | Referans çözücünün konu kuralı (tüm içerik kelimeleri) |
| 200 | "Bunu teknik anlat" | research.explain level=technical (korpus r.tech.* 11 vaka) + kalıcı kayıt (209) | Çalışır | DONE | PA | P1 | 209 | B31 | app/research/answers.py:technical_speech | test_research_control.py (16), test_voice_research_b31.py (11), korpus r.hold/r.continue/r.open/r.mode (12), test_research_routes.py | fake Temporal ile yerel; üretimde sesle iptal/duraklatma Karar 0 (READY_FOR_OWNER) | no | 237 ile ortak; 207 yedek sağlayıcı cümlesi de burada |
| 201 | "Bir önceki araştırmayı aç" | Intent RESEARCH_OPEN → `research.open`: referans çözülür, odak taşınır, özet söylenir, rapor artifact'ı cihazda açılır (açılamazsa söyler) | Çalışır | DONE | PA | P1 | 199 | B31 | intents.py:_research_open_match; tools.py:research_open | test_research_control.py (16), test_voice_research_b31.py (11), korpus r.hold/r.continue/r.open/r.mode (12), test_research_routes.py | fake Temporal ile yerel; üretimde sesle iptal/duraklatma Karar 0 (READY_FOR_OWNER) | no | Eskiden artifact_open'a gidiyordu (ölçüldü) |
| 202 | Research cancel | REST (M13) + ses `research.cancel` (B27); duraklatılmış araştırma da iptal edilebilir (B31) | Sesle ve REST'ten iptal | DONE | PA | P1 | — | B31 | app/research/routes.py:cancel_research, app/research/service.py:mark_research_cancelled, tools.py:research_cancel | test_daily_intent_tools.py (24), test_research_control.py, korpus | üretim turu bekliyor (Karar 0) | no | 732 ile ortak — iki yarı TEK fonksiyonu çağırır; Temporal iptali followup |
| 203 | Research pause | `mark_research_paused` (bayrak, aşama değil) + `POST /{id}/pause` + `research.pause` + Temporal `pause` sinyali; workflow her aşama sınırında `_gate()` | Duraklat | DONE | PA | P1 | 202 | B31 | app/research/service.py:mark_research_paused; browser_workflow.py:_gate; routes.py:pause_research; tools.py:research_pause | test_research_control.py (16), test_voice_research_b31.py (11), korpus r.hold/r.continue/r.open/r.mode (12), test_research_routes.py | fake Temporal ile yerel; üretimde sesle iptal/duraklatma Karar 0 (READY_FOR_OWNER) | no | Tutulan saniyeler bütçeden düşülür (`paused_s`); zaten duraklatılmış → refused |
| 204 | Research resume | `mark_research_resumed` + `POST /{id}/resume` + `research.resume` + Temporal `resume` sinyali; web Duraklat/Devam et | Devam | DONE | PA | P1 | 203 | B31 | app/research/service.py:mark_research_resumed; routes.py:resume_research; tools.py:research_resume | test_research_control.py (16), test_voice_research_b31.py (11), korpus r.hold/r.continue/r.open/r.mode (12), test_research_routes.py | fake Temporal ile yerel; üretimde sesle iptal/duraklatma Karar 0 (READY_FOR_OWNER) | no | Duraklatılmamış → refused ('zaten sürüyor') |
| 205 | Temporal typed failures | Çalışıyor | Aynı | DONE | PA | P0 | — | — | app/research/ | research testleri | ADR-0123 | no | 12 ile aynı iş |
| 206 | Orphan cleanup | Yok | Temizlenir | DONE | PA | P0 | 10 | B06 | app/research/service.py:sweep_abandoned_runs | test_orphan_sweeps.py | üretim turu bekliyor (Karar 0) | no | 10, 13 ile tek uygulama |
| 207 | Provider fallback transparency | Sentez yedeği rapora (`synthesis_fallback`), koşu olaylarına ve deftere (`research.provider_fallback`) yazılır; arama yedekleri sayılır (`search_fallbacks`); teknik cevap ve web raporu söyler | Şeffaf | DONE | PA | P1 | — | B31 | app/research/browser_activities.py:_record_provider_fallback; result.py; answers.py; ReportView.tsx | test_research_control.py (16), test_voice_research_b31.py (11), korpus r.hold/r.continue/r.open/r.mode (12), test_research_routes.py | fake Temporal ile yerel; üretimde sesle iptal/duraklatma Karar 0 (READY_FOR_OWNER) | no | Eskiden yalnız log satırı |
| 208 | Result-first narration | Çalışıyor | Aynı | DONE | PA | P1 | — | — | app/research/, app/voice/ | research testleri | — | no | — |
| 209 | Technical mode | `research.answer_mode` ('bundan sonra teknik anlat' / 'teknik modu kapat') → `research_owner_state.preferences_json.answer_level` (migration 0046); explain ve tur seviyesi okur | Çalışır | DONE | PA | P1 | — | B31 | intents.py:_answer_mode_match; focus.py:set_answer_level; tools.py:research_answer_mode | test_research_control.py (16), test_voice_research_b31.py (11), korpus r.hold/r.continue/r.open/r.mode (12), test_research_routes.py | fake Temporal ile yerel; üretimde sesle iptal/duraklatma Karar 0 (READY_FOR_OWNER) | no | Tek seferlik 'teknik anlat' takip olarak kalır (mutasyon M4) |
| 210 | Research history UI | Liste + detay + odak + iptal + mod seçici + Duraklat/Devam et + duraklatıldı uyarısı + yedek sağlayıcı satırı | Tam geçmiş | DONE | PA | P1 | 685 | B31 | apps/web/app/research/page.tsx, ProgressPanel.tsx, ReportView.tsx, lib/research/api.ts | apps/web tests/research (69) | vitest + tsc yeşil | no | Sayfalama/filtre yok (liste küçük); ihtiyaç doğarsa 685 ile |

## G. VOICE / REALTIME (211–238)

| ID | FEATURE | CURRENT_STATUS | TARGET_STATUS | IMPL | PROOF | PRI | DEPS | BATCH | SOURCE_REFERENCES | TEST_REFERENCES | RUNTIME_PROOF | OWNER_ACTION | NOTES |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 211 | Browser Voice WebRTC | Çalışıyor | Aynı | DONE | PR | P1 | — | — | app/voice/realtime/, apps/web/ | voice testleri | 97 oturum | no | Rewrite gerekmez |
| 212 | Stable data channel | Çalışıyor | Aynı | DONE | PR | P1 | — | — | app/voice/realtime/ | voice testleri | üretim | no | — |
| 213 | Barge-in | Yerel durdurma önce, sonra iptal | Aynı | DONE | PA | P1 | — | — | apps/web/, app/voice/ | sıralama testi | — | no | Sıralaması testli |
| 214 | Semantic VAD | Çalışıyor | Aynı | DONE | PA | P1 | — | — | app/voice/realtime/ | voice testleri | — | no | — |
| 215 | Noise suppression | Sağlayıcı tarafında | Ölçülür ve ayarlanır | DONE | PA | P1 | — | B20 | apps/web/app/lib/voice/profile.ts:suppressionDecision, apps/web/app/lib/voice/audio.ts:BrowserMicrophone.applyLive | input-tuning.test.ts | üretim turu bekliyor (Karar 0) | no | otomatik ölçüme uyar; canlı izde uygulanır, okuma geri alınır |
| 216 | Echo cancellation | Sağlayıcı tarafında | Ölçülür | DONE | PA | P1 | — | B20 | apps/web/app/lib/voice/audio.ts:readBackTrack, apps/web/app/lib/voice/rig.ts:measuredEchoResidualDb | input-tuning.test.ts, gate.test.ts | üretim turu bekliyor (Karar 0) | no | ADR-0047 §4 okuma geri alma + ölçülen yankı kalıntısı; ölçüm düzeltmesi |
| 217 | Mic sensitivity modes | Yok | Seçilebilir | DONE | PA | P1 | — | B20 | apps/web/app/lib/voice/calibration.ts:deriveGateParameters, apps/web/app/voice/page.tsx | input-tuning.test.ts | üretim turu bekliyor (Karar 0) | no | dört mod vardı, üçü ayrıydı: normal artık öğrenileni yok sayar; ölçüm düzeltmesi |
| 218 | Reconnect | Tek uçuşlu + 410 fırtına koruması | Proaktif yeniden bağlanma | DONE | PA | P1 | — | B20 | apps/web/app/lib/voice/webrtc.ts:onconnectionstatechange, apps/web/app/lib/voice/controller.ts:onLinkImpaired | proactive-reconnect.test.ts, session-storm.test.ts | üretim turu bekliyor (Karar 0) | no | `disconnected` uyarısı 2 sn içinde temizlenmezse yeniden bağlanır |
| 219 | Dead session cleanup | Yok | Süpürülür | DONE | PA | P0 | — | B06 | app/voice/realtime_sessions/service.py:sweep_idle_sessions, app/main.py:RetentionSweeper | test_orphan_sweeps.py | üretim turu bekliyor (Karar 0) | no | 9 ile tek uygulama |
| 220 | Session TTL | Yok | Ömür sınırı | DONE | PA | P0 | 219 | B06 | app/voice/realtime_sessions/service.py:sweep_idle_sessions:IDLE_SESSION_AFTER | test_orphan_sweeps.py | üretim turu bekliyor (Karar 0) | no | ADR-0105 gereği mutlak ömür DEĞİL, 12 saat atıllık sınırı |
| 221 | False LISTENING prevention | Mikrofon kaybında "Dinliyor" diyor | Durum gerçeğe bağlı | DONE | PA | P1 | 222 | B20 | apps/web/app/lib/voice/controller.ts:setState:mic_lost | microphone-loss.test.ts, voice-overlay.test.ts | üretim turu bekliyor (Karar 0) | no | `listening` canlı iz gerektirir; kontrol setState içinde |
| 222 | Mic track loss detection | `track.onended`/`onmute` dinlenmiyor | Kayıp görülür | DONE | PA | P1 | — | B20 | apps/web/app/lib/voice/controller.ts:watchMicrophone | microphone-loss.test.ts | üretim turu bekliyor (Karar 0) | no | ended/mute/unmute + sunucuya olay |
| 223 | Provider 60-min limit handling | ADR-0105 düzeltmesi var; tavan değerini istemci okumuyor | Tavan öncesi yenileme | DONE | PA | P1 | — | B20 | app/voice/providers_openai_realtime.py:leg_max_seconds, apps/web/app/lib/voice/controller.ts:armLegRenewal | leg-ceiling.test.ts, test_voice_realtime_sessions.py | üretim turu bekliyor (Karar 0) | no | sunucu tavanı yayımlar, istemci 2 dk önce yeniler; tur ortasında beklenir |
| 224 | Long-form narration | Motor yalnız testlerde; seam imzası sağlayıcıyla uyumsuz | Gerçek ses üretir | DONE | PA | P1 | — | B21 | app/narration/synth.py:ProviderSynthesizer, app/narration/runtime.py:narrator, app/narration/routes.py:_synthesise_window | test_narration_audio.py | TTS kredisi yok: gerçek sağlayıcıyla üretim turu bekliyor | TTS kotası | seam çevrildi; anahtarsız dağıtımda ses YOK, metin var (233 kuralı) |
| 225 | Narration queue | Kısmi | Çalışır | DONE | PA | P1 | 224 | B21 | app/narration/engine.py:NarrationEngine, app/narration/routes.py:_synthesise_window | test_narration_audio.py, test_narration_engine.py | üretim turu bekliyor (Karar 0) | no | kuyruk+önbellek vardı, çağıranı yoktu |
| 226 | Read-ahead | Yok | İleri okuma | DONE | PA | P1 | 225 | B21 | app/narration/engine.py:ensure_ahead, app/narration/runtime.py:NARRATION_LOOKAHEAD | test_narration_audio.py | üretim turu bekliyor (Karar 0) | no | `ready_ahead` isimle döner; ikinci komut önbellekten |
| 227 | Pause/resume narration | Yok | Duraklat/devam | DONE | PA | P1 | 225 | B21 | app/narration/commands.py:apply, app/narration/routes.py:post_command | test_narration_audio.py, test_narration_commands.py | üretim turu bekliyor (Karar 0) | no | duraklatılmış oturum sağlayıcıya tek çağrı yapmaz |
| 228 | Pronunciation dictionary | Var, üretimde 0 kural | Kurallar girilir | DONE | PA | P1 | — | B21 | app/voice/realtime_sessions/tools_pronunciation.py | test_pronunciation_voice.py | üretimde kural sayısı sahibin kullanımına bağlı | no | sesle öğretilir/silinir; tek yazıcı manuel PUT değil artık |
| 229 | Pronunciation in assistant speech | Yalnız araç dönüş metnine | Persona talimatına girer | DONE | PA | P1 | 228 | B21 | app/voice/realtime_sessions/persona.py:pronunciation_block, app/voice/realtime_sessions/service.py:_pronunciation_rules | test_pronunciation_voice.py, test_voice_realtime_sessions.py | üretim turu bekliyor (Karar 0) | no | 24 kural/700 karakter sınırlı; asıl eksik kapandı |
| 230 | Turkish number pronunciation | Kısmi | Tam | DONE | PA | P1 | 228 | B21 | app/narration/numbers.py:attach_suffix, app/narration/normalizer.py:degree/fraction/negative | test_narration_numbers_tr.py | üretim turu bekliyor (Karar 0) | no | kesme işareti+ünlü uyumu, eksi, derece, kesir; deyimler korundu |
| 231 | Speaking state = real playback | Kısmi | Gerçek sese bağlı | DONE | PA | P1 | — | B20 | apps/web/app/lib/voice/labels.ts:voiceStateLabel | speaking-truth.test.ts | üretim turu bekliyor (Karar 0) | no | ADR-0066 evresi etiketi belirler: ses yokken "Yanıt hazırlanıyor" |
| 232 | RMS only drives animation | Doğru uygulanmış | Aynı | DONE | PA | P1 | — | — | apps/web/ | core testleri | — | no | Rewrite gerekmez |
| 233 | Text fallback on provider failure | Kısmi | Her düşüşte metin | DONE | PA | P1 | — | B20 | app/voice/text_fallback.py, app/alarms/sequence.py:_briefing_as_text | test_voice_text_fallback.py | üretim turu bekliyor (Karar 0) | no | söylenemeyen selamlama ve brifing bildirim kutusuna yazılır |
| 234 | TTS fallback policy | Sinüs tonu yedeği, testi yok | Politika + test | DONE | PA | P1 | 233 | B20 | app/voice/providers.py:FakeTTSProvider.synthetic_speech, app/alarms/greeting_audio.py:is_fallback_provider | test_tts_fallback_policy.py | üretim turu bekliyor (Karar 0) | no | politika tek ada değil sınıfa bakar; her teslim yolu için kanıt |
| 235 | Provider-unavailable UI | Kısmi | Kontrollü | DONE | PA | P1 | 709 | B20 | apps/web/app/lib/voice/api.ts:providerUnavailable, apps/web/app/lib/voice/controller.ts:enterUnavailable | provider-unavailable.test.ts, test_voice_unavailable_contract.py | üretim turu bekliyor (Karar 0) | no | durum olarak gösterilir; anahtar yoksa düğme kapalı |
| 236 | Voice diagnostics | Kısmi; faster-whisper yanlış "etkin" görünüyor | Dürüst teşhis | DONE | PA | P1 | — | B20 | app/voice/registry.py:_activated, app/voice/providers.py:FasterWhisperSTTProvider.available | test_voice_providers.py | üretim turu bekliyor (Karar 0) | no | kısa devre kaldırıldı; sağlayıcı kendi kullanılabilirliğini söyler |
| 237 | "Teknik anlat" mode | Kısmi | Çalışır | DONE | PA | P1 | 209 | B21 | app/voice/realtime_sessions/tools.py:_narration_mode | test_voice_realtime_sessions.py | üretim turu bekliyor (Karar 0) | no | teknik sunum artık OKUMA biçimini de değiştirir |
| 238 | Voice activity history | Defter çift yazıyor | Doğru geçmiş | DONE | PA | P1 | 70 | B20 | app/voice/realtime_sessions/service.py:_closed_session_facts | test_voice_activity_history.py, test_ledger_service.py | üretim turu bekliyor (Karar 0) | no | çift yazım 70 ile B06'da bitti (ölçüm düzeltmesi); kapanış satırı süre/söze girme/araç sayısını taşır |

## H. DEVICE-SIDE / AMBIENT VOICE (239–255)

| ID | FEATURE | CURRENT_STATUS | TARGET_STATUS | IMPL | PROOF | PRI | DEPS | BATCH | SOURCE_REFERENCES | TEST_REFERENCES | RUNTIME_PROOF | OWNER_ACTION | NOTES |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 239 | Device-side microphone provider | Cihaz ses yakalama yeteneği duyurmuyor | Cihazda mikrofon | MISSING | NYP | P2 | — | B47 | devices/windows-agent | — | 85 yetenekte yok | mahremiyet kararı | Sürekli açık mikrofon kararı; B47 DURDU: Karar 7 (mahremiyet) — ADR-0154 karar paketi |
| 240 | Browser-independent listening | Yok | Tarayıcısız dinleme | MISSING | NYP | P2 | 239 | B47 | devices/windows-agent | — | — | mahremiyet kararı | Asistanın ön kapısı bugün fare tıklaması; B47 DURDU: Karar 7 (mahremiyet) — ADR-0154 karar paketi |
| 241 | Wake word | Yok | Uyandırma sözcüğü | MISSING | NYP | P2 | 240 | B47 | devices/windows-agent | — | — | mahremiyet kararı | B47 DURDU: Karar 7 (mahremiyet) — ADR-0154 karar paketi |
| 242 | Wake-word enable/disable | Yok | Açılıp kapanır | MISSING | NYP | P2 | 241 | B47 | devices/windows-agent | — | — | no | B47 DURDU: Karar 7 (mahremiyet) — ADR-0154 karar paketi |
| 243 | Push-to-talk fallback | Yok | Bas-konuş | MISSING | NYP | P2 | 239 | B47 | devices/windows-agent | — | — | no | Mahremiyet açısından en ucuz seçenek; B47 DURDU: Karar 7 (mahremiyet) — ADR-0154 karar paketi |
| 244 | Local VAD | Yok | Cihazda konuşma tespiti | MISSING | NYP | P2 | 239 | B47 | devices/windows-agent | — | — | no | B47 DURDU: Karar 7 (mahremiyet) — ADR-0154 karar paketi |
| 245 | Speaker verification | Tavsiye niteliğinde; verdict'i okuyan yok | Karar yolunda | PARTIAL | PA | P0 | — | B05 | app/security/step_up.py, app/voice/realtime_sessions/service.py:handle_tool_call | test_voice_step_up.py | gölge mod: sayıyor, engellemiyor — enforce sahip kararı | no | Karar yolu var ve test edildi; realtime'da verdict üreten akış YOK |
| 246 | Trusted-device check | `device_trusted` istek gövdesinden geliyor | Sunucu tarafında doğrulanır | DONE | PA | P0 | 663 | B05 | app/voice/device_trust.py, app/voice/routes.py:verify_speaker_route | test_authority_gate.py | üretim turu bekliyor (Karar 0) | no | Alan kaldırıldı: gönderen 422 alıyor, sessizce yok sayılmıyor |
| 247 | Speaker-confidence threshold | Hesaplanıyor, tüketilmiyor | Uygulanır | PARTIAL | PA | P0 | 245 | B05 | app/security/step_up.py, app/voice/realtime_sessions/service.py:handle_tool_call | test_voice_step_up.py | gölge mod: sayıyor, engellemiyor — enforce sahip kararı | no | Eşik tüketiliyor ama gölge modda; enforce iki şey bekliyor |
| 248 | Sensitive-command higher threshold | Yok | Eşik yükselir | PARTIAL | PA | P0 | 247 | B05 | app/security/step_up.py, app/voice/realtime_sessions/service.py:handle_tool_call | test_voice_step_up.py | gölge mod: sayıyor, engellemiyor — enforce sahip kararı | no | CRITICAL için 0.88 skor + 3 dk tazelik; gölge modda |
| 249 | Raw voice not archived | Politika uygulanıyor | Aynı | DONE | PA | P0 | — | — | app/voice/ | voice testleri | — | no | Ürün ilkesi — gevşetilmez |
| 250 | Device-local Voice startup | Yok | Açılışta başlar | MISSING | NYP | P2 | 239 | B47 | devices/windows-agent | — | — | no | B47 DURDU: Karar 7 (mahremiyet) — ADR-0154 karar paketi |
| 251 | Voice service restart recovery | Yok | Toparlanır | MISSING | NYP | P2 | 250 | B47 | devices/windows-agent | — | — | no | B47 DURDU: Karar 7 (mahremiyet) — ADR-0154 karar paketi |
| 252 | Voice process health | Yok | Sağlık görünür | MISSING | NYP | P2 | 250 | B47 | devices/windows-agent | — | — | no | B47 DURDU: Karar 7 (mahremiyet) — ADR-0154 karar paketi |
| 253 | Offline command subset | Yok | Çevrimdışı komutlar | MISSING | NYP | P2 | 240 | B47 | devices/windows-agent | — | — | no | Alarmın çevrimdışı yolu örnek alınmalı; B47 DURDU: Karar 7 (mahremiyet) — ADR-0154 karar paketi |
| 254 | Voice privacy indicator | Yok | Dinleme göstergesi | MISSING | NYP | P2 | 240 | B47 | devices/windows-agent | — | — | no | Mahremiyet için zorunlu; B47 DURDU: Karar 7 (mahremiyet) — ADR-0154 karar paketi |
| 255 | Hardware mic mute awareness | Yok | Donanım susturması bilinir | MISSING | NYP | P2 | 239 | B47 | devices/windows-agent | — | — | no | B47 DURDU: Karar 7 (mahremiyet) — ADR-0154 karar paketi |

## I. ALARM / ROUTINES / MORNING EXPERIENCE (256–299)

| ID | FEATURE | CURRENT_STATUS | TARGET_STATUS | IMPL | PROOF | PRI | DEPS | BATCH | SOURCE_REFERENCES | TEST_REFERENCES | RUNTIME_PROOF | OWNER_ACTION | NOTES |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 256 | Durable alarm create | Çalışıyor | Aynı | DONE | PR | P1 | — | — | app/alarms/ | alarm testleri | 7 rutin üretimde | no | Rewrite gerekmez |
| 257 | Alarm cancel | Çalışıyor | Aynı | DONE | PA | P1 | — | — | app/alarms/ | alarm testleri | — | no | — |
| 258 | Alarm list | Çalışıyor | Aynı | DONE | PA | P1 | — | — | app/alarms/ | alarm testleri | — | no | — |
| 259 | Snooze | Kısmi | Tam | PARTIAL | PA | P1 | — | B13 | app/alarms/service.py, app/alarms/models.py:MAX_SNOOZE_COUNT | test_alarms_service.py (51) | üretim turu bekliyor (Karar 0) | no | Sınır kondu (5): sınırsız erteleme alarmı hiç terminal duruma sokmuyordu. KALAN: bulut erişilemezken yerel çalma sırasında erteleme — yeni bir cihaz YETENEĞİ değil, yerel bir TETİKLEYİCİ gerekiyor (B47) |
| 260 | Stop | Çalışıyor | Aynı | DONE | PA | P1 | — | — | app/alarms/ | alarm testleri | — | no | — |
| 261 | Weekday recurring alarm | Yok (7/7 tek seferlik) | Hafta içi tekrar | DONE | PA | P1 | — | B14 | app/alarms/service.py:_catch_up_recurring, app/alarms/tr_time.py, app/routines/triggers.py | test_alarms_recurrence.py (10) | üretim turu bekliyor (Karar 0) | no | Kod M18.3'ten beri vardı; matris ÜRETİM SATIRLARINI anlatıyordu. Döngüyü sürünce gerçek kusur çıktı: bir sabah kaçırılınca satır geçmişte kalıyor ve her sonraki sabah "expired" diye reddediliyordu — tekrarlayan alarm sessizce ölüyordu |
| 262 | Custom recurring alarm | Yok | Özel tekrar | DONE | PA | P1 | 261 | B14 | app/alarms/tr_time.py:_weekdays_from, app/routines/triggers.py | test_alarms_recurrence.py (10) | üretim turu bekliyor (Karar 0) | no | "Her pazartesi ve perşembe" — günler cümleden geliyor, ön tanımlı kümeden değil; diğer beş gün sessiz kalıyor |
| 263 | Local device backup alarm | Cihaz 12 saat öncesinden diske yazıyor | Aynı | DONE | PA | P1 | — | — | devices/windows-agent | deterministik saat testi | — | no | Sistemin en güçlü garantisi |
| 264 | Cloud-independent firing | Bulut kapalıyken çalıyor | Aynı | DONE | PA | P1 | — | — | devices/windows-agent | deterministik saat testi | — | no | Rewrite gerekmez |
| 265 | Display wake | Çalışıyor | Aynı | DONE | PR | P1 | — | — | devices/windows-agent | ambient testleri | — | no | — |
| 266 | Saved YouTube wake song | Çalışıyor (companion Chrome) | Aynı | DONE | PR | P1 | — | — | devices/windows-agent | — | — | no | — |
| 267 | Safe tone fallback | Anahtar yoksa sessizce sinüs tonu, testi yok | Testli ve duyurulan yedek | DONE | PA | P1 | 234 | B13 | app/alarms/greeting_audio.py, app/alarms/sequence.py, app/alarms/service.py:alarm_dict | test_alarms_sequence.py (42) | üretim turu bekliyor (Karar 0) | no | 110 Hz vızıltı artık KARŞILAMA diye çalınmıyor; `greeting_failure="no_tts_key"` satırda okunuyor. Yedek yolu duruyor ve sınanıyor — sadece sahibe sunulmuyor |
| 268 | Volume ramp | Çalışıyor | Aynı | DONE | PA | P1 | — | — | devices/windows-agent | — | — | no | — |
| 269 | Media duck | Kısmi | Tam | DONE | PA | P1 | — | B13 | devices/.../AlarmController.cs:Duck, devices/.../CompanionRuntime.cs | AlarmDuckAndTestModeTests.cs (7) | cihazda kurulum bekliyor | no | Bulut MÜZİĞİ zaten kısıyordu; TONU kısamıyordu (companion içinde üretiliyor). Chunk başına 0.15, referans sayımlı, tek kapıda (`desktop.play_audio`) |
| 270 | Greeting | Çalışıyor (sesli karşılama) | Brifingle zenginleşir | DONE | PA | P1 | — | B15 | app/alarms/sequence.py:_speak_briefing | test_briefing_delivery.py (21) | üretim alarmı | TTS kotası | Karşılama önce, brifing sonra — AYNI duck penceresinde; "Günaydın efendim" yarı uykulu birine gürültünün bittiğini söyleyen cümle |
| 271 | Current time | Brifingde var, niyet yok | Sesle sorulabilir | DONE | PA | P1 | 726 | B15 | app/voice/intents.py:_clock_match, app/voice/realtime_sessions/tools.py:clock_now, app/briefing/service.py:local_now | corpus c.* (6), test_voice_intents.py (138) | üretim turu bekliyor (Karar 0) | no | Cümle brifingde vardı, niyeti yoktu — ve `clock.now` ham ISO damgası dönüyordu. İkisi de düzeldi; brifingin KENDİ biçimlendiricisi okunuyor, ikinci bir tane doğmadı. Alarm ailesinden SONRA çözülüyor |
| 272 | Weather | Canlı ses oturumu istiyor | Tarayıcısız | DONE | PA | P1 | 281 | B15 | app/briefing/delivery.py, app/alarms/sequence.py | test_briefing_delivery.py (21) | üretim turu bekliyor (Karar 0) | no | Tarayıcısız: alarm yolu brifingi okuyor. İçerik 273'e bağlı |
| 273 | Location | Kısmi | Çalışır | DONE | PA | P1 | 272 | B15 | app/location/service.py (altı kademe), app/voice/.../tools_weather.py:location_set_default | test_location_service.py (11) | üretim turu bekliyor (Karar 0) | no | Mekanizma uçtan uca çalışıyor: sahip "İstanbul'u varsayılan yap" diyebiliyor. AYARLANMAMIŞ olduğu sürece brifing dürüstçe "neredesiniz bilmiyorum" diyor — kusur değil, boş bir ayar |
| 274 | System status | Canlı ses oturumu istiyor | Tarayıcısız | DONE | PA | P1 | 281 | B15 | app/briefing/service.py, app/briefing/delivery.py | test_briefing_service.py (12), test_briefing_delivery.py (21) | üretim turu bekliyor (Karar 0) | no | Tarayıcısız |
| 275 | Overnight task summary | Canlı ses oturumu istiyor | Tarayıcısız | DONE | PA | P1 | 281 | B15 | app/briefing/service.py, app/briefing/delivery.py | test_briefing_service.py (12), test_briefing_delivery.py (21) | üretim turu bekliyor (Karar 0) | no | Tarayıcısız |
| 276 | Research completion summary | Canlı ses oturumu istiyor | Tarayıcısız | DONE | PA | P1 | 281 | B15 | app/briefing/service.py:_research_sentence | test_briefing_service.py (12) | üretim turu bekliyor (Karar 0) | no | Teslim yolu vardı (bekleyen brifing kuyruğu) ama SABAH brifinginde yoktu: oturum açmayan sahip sorusunun yanıtlandığını hiç duymuyordu. Aynı defter, aynı 12 saatlik pencere |
| 277 | Calendar summary | Brifingin takvim cümlesi CalendarService.agenda'dan (bugünün etkinlikleri); hesap yoksa cümle yok | Çalışır | DONE | PA | P2 | 337 | B46 | app/briefing/service.py:_calendar_sentence | test_calendar_b46.py (fikstür takvimiyle brifing) | gerçek hesapta brifing | hesap bilgisi (READY_FOR_OWNER) | 363 ile aynı yol |
| 278 | Mail summary | Brifingin okunmamış-mail cümlesi: MailService.inbox_summary'den okunur (tahmin yok), sahibin `include_mail` tercihiyle kapatılır (göç 0057); hesap yoksa cümle yoktur | Çalışır | DONE | PA | P2 | 335 | B45 | app/briefing/service.py:_mail_sentence | test_mail_b45.py (okunmamış sayısı; kapatma; hesapsız sessiz) | gerçek hesapta brifing | hesap bilgisi (READY_FOR_OWNER) | 362 ile aynı yol |
| 279 | News summary | Haber çalışıyor ama brifing "bağlı değil" diyor | Doğru söyler | DONE | PA | P1 | — | B15 | app/briefing/service.py:_news_sentence | test_briefing_service.py (12) | üretim turu bekliyor (Karar 0) | no | Yanlış olumsuzlama DEĞİL, KOŞULSUZ olumsuzlama: hiçbir şey sorulmuyordu. Yorum yazıldığında doğruydu, haber izi indiğinde yanlış oldu, kimse dönüp bakmadı — ve `test_build_is_honest_about_the_absent_news_resolver` o yalanı yerinde tutuyordu. Artık soruyor: manşet / kaynak yok / ulaşılamadı / yeni video yok |
| 280 | Morning briefing | Yalnız sesli oturumdan | Tam brifing | DONE | PA | P1 | 281 | B15 | app/briefing/delivery.py, app/main.py | test_briefing_delivery.py (21) | üretim turu bekliyor (Karar 0) | no | `BriefingService.build`'in ikinci çağıranı var: alarm yolu. Tek çağıranı sesli araç olduğu için bütün sabah deneyimi açık bir tarayıcı istiyordu |
| 281 | Browser-independent morning briefing | Yok | Tarayıcısız teslim | DONE | BLK | P1 | 224 | B15 | app/briefing/delivery.py, app/alarms/sequence.py, app/config.py:alarm_briefing_enabled, app/alarms/service.py:alarm_dict | test_briefing_delivery.py (21), test_alarms_sequence.py (briefing makbuzu 3) | TTS anahtarı gerekiyor (sesli kanıt) | TTS kotası | ÖLÇÜM: cihaz 20 sn'de KESİYOR (reddetmiyor) ve dolu bir brifing ~39 sn. Tek WAV olsaydı sahip yarısını duyar, kesildiğini hiçbir yerde göremezdi. Cümle sınırında parçalara bölünüyor; sınır C# kaynağından OKUNUYOR. Anahtarsız: vızıltı yok, `no_tts_key` var. Makbuz satırda okunuyor: `{clips, spoken, failure, complete}` — "dördün ikisini duydunuz" da bir sonuçtur ve yalnız günlükte kalmamalı |
| 282 | Alarm fired receipt to Cloud | `local_alarm_fired` cihazın kapalı alan setinde yok | Cihaz bildirir | DONE | PA | P1 | 5 | B13 | packages/schemas/device-protocol.schema.json, packages/protocol/DEVICE_PROTOCOL.md, devices/.../HeartbeatStatus.cs, devices/.../ActivityStatusReporter.cs, devices/.../AlarmArmController.cs | LocalFiredReportingTests.cs (10) | cihazda kurulum bekliyor | no | Sayaç değil KİMLİK: bulutun sorusu "kaç tane" değil "hangisi". Rapor edilince boşaltılıyor |
| 283 | Prevent duplicate firing | Bulut yarım saat sonra ikinci kez çalabilir | Çift çalma imkânsız | DONE | PA | P1 | 282 | B13 | app/alarms/sequence.py, devices/.../AlarmArmController.cs:Disarm | test_alarms_sequence.py (42), LocalFiredReportingTests.cs (10) | cihazda kurulum bekliyor | no | Disarm SENKRON cevap veriyor (`already_fired`) — heartbeat'i beklemek kusurun ta kendisiydi. `was_armed:false` tek başına belirsizdi |
| 284 | Single late threshold | Cihaz 5 dk, bulut 2 saat | Tek eşik | DONE | PA | P1 | 5 | B13 | packages/protocol/alarm-timing.json, app/alarms/timing.py, app/alarms/service.py, devices/.../AlarmArmController.cs | test_alarms_service.py (51), AlarmTimingContractTests.cs (5), test_contract_falsification.py (34) | cihazda kurulum bekliyor | no | Tek sayı, tek dosya, iki yarı da okuyor. Cihazın 5 dakikası kazandı: olay raporuyla ölçülmüş olan oydu. Kırmızı kanıtlandı |
| 285 | Alarm history | Kısmi | Tam | DONE | PA | P1 | — | B13 | app/alarms/history.py, app/alarms/routes.py | test_alarms_history.py (10) | üretim turu bekliyor (Karar 0) | no | YENİ TABLO YOK: defter zaten tutuyordu, kimse geri okuyamıyordu. Tekrarlayan alarmın SATIRI dünü unutuyor (`_release` geri sarıyor) — defter unutmuyor |
| 286 | Alarm test mode | Kısmi | Tam | DONE | PA | P1 | — | B13 | app/alarms/sequence.py, devices/.../ArmedAlarmStore.cs, devices/.../AlarmArmController.cs, app/alarms/history.py | AlarmDuckAndTestModeTests.cs (7), test_alarms_history.py (10) | cihazda kurulum bekliyor | no | Cihaz artık BİLİYOR: test çalmasının denetim satırı gerçek 07:30'unkiyle bayt bayt aynıydı. Diskte kalıcı (yerel çalma bulut yokken olur) ve geçmişten süzülebiliyor |
| 287 | routine.create | Sesli araç yok | Sesle kurulur | DONE | PA | P1 | — | B14 | app/voice/realtime_sessions/tools_routines.py | test_routines_voice_tools.py (17), corpus r.* (16) | üretim turu bekliyor (Karar 0) | no | "En büyük görünmez özellik" aynen doğruydu: motor M18'den beri tam, üretimdeki 7 rutinin hepsini ALARM alt sistemi kurmuş. KISIT: `voice_briefing` serbest metin taşıyor, röle `text` anahtarını reddediyor (gizlilik kuralı) — metinli rutin REST'e özel |
| 288 | routine.list | Yok | Listelenir | DONE | PA | P1 | 287 | B14 | app/voice/realtime_sessions/tools_routines.py | test_routines_voice_tools.py (17), corpus r.* (16) | üretim turu bekliyor (Karar 0) | no | Duraklatılmışlar da sayılıyor: sahibin HÂLÂ sahip olduğu bir rutin |
| 289 | routine.cancel | Yok | İptal | DONE | PA | P1 | 287 | B14 | app/voice/realtime_sessions/tools_routines.py | test_routines_voice_tools.py (17), corpus r.* (16) | üretim turu bekliyor (Karar 0) | no | Motorun reddi kendi cümlesiyle geçiyor, yeniden yazılmıyor |
| 290 | routine.pause | Yok | Duraklat | DONE | PA | P1 | 287 | B14 | app/routines/models.py, app/routines/state.py, app/routines/service.py:pause_routine, göç 0045 | test_routines_pause_and_condition.py (19), test_routines_voice_tools.py (17) | üretim turu bekliyor (Karar 0) | no | İptal DEĞİL: `paused` terminal değil. Öncesinde "bu hafta durdur"u onurlandırmanın tek yolu iptal-et-yeniden-kur'du — kimlik, geçmiş ve defter izi kayboluyordu |
| 291 | routine.resume | Yok | Devam | DONE | PA | P1 | 290 | B14 | app/routines/service.py:resume_routine, app/routines/routes.py | test_routines_pause_and_condition.py (19), test_routines_voice_tools.py (17) | üretim turu bekliyor (Karar 0) | no | Uyurken kaçırdıklarını TEKRAR OYNATMIYOR: sahip o sabahların olmamasını istedi |
| 292 | Presence-trigger routine | Tanımlı, hiç kullanılmamış | Kullanılır | DONE | PA | P1 | 287 | B14 | app/routines/triggers.py, app/voice/realtime_sessions/tools_routines.py | test_routines_voice_tools.py (17) | üretim turu bekliyor (Karar 0) | no | Tetikleyici M18'den beri test edilmiş ve `app/` içinde HİÇBİR YARATICISI yoktu. Test aracın cevabında durmuyor: `evaluate_due`'dan geçip gerçek bir dispatch'e ulaşıyor |
| 293 | Schedule-trigger routine | Tanımlı, hiç kullanılmamış | Kullanılır | DONE | PA | P1 | 287 | B14 | app/alarms/service.py:_create_trigger_routine, app/routines/triggers.py | test_alarms_recurrence.py (10) | üretim turu bekliyor (Karar 0) | no | "Hiç kullanılmamış" yanlıştı: tekrarlayan her alarm bir schedule rutini yaratıyor. Ölçüm dokümantasyonu yendi (B10'daki 560 gibi) |
| 294 | Condition-trigger routine | Yok | Kullanılır | DONE | PA | P1 | 293 | B14 | app/routines/triggers.py:check_condition_due, app/routines/models.py, göç 0045 | test_routines_pause_and_condition.py (19) | üretim turu bekliyor (Karar 0) | no | Diğer üçü bir ANI, bu bir DURUMU soruyor. KENARDA tetikleniyor: durum doğru kaldığı sürece tetiklenseydi sahip klavyeden uzakken saatte onlarca kez ateşlenirdi |
| 295 | Routine panel in Cockpit | Yok | Panel | DONE | PA | P1 | 685 | B14 | apps/web/app/lib/cockpit/routines.ts, routine-rows.ts, useRoutineControl.ts, CockpitPanels.tsx | routines-panel.test.tsx (26) | üretim turu bekliyor (Karar 0) | no | Tetikleyici VERİ, sahip KELİME ile istedi — çeviri işin kendisi. Bilmediği şekle güvenli cümle kurmuyor. İki kontrol, ikisi de geri alınabilir |
| 296 | Natural-language recurring routines | Yok | Doğal dille | DONE | PA | P1 | 287 | B14 | app/voice/intents.py:_routine_match | corpus r.* (16) | üretim turu bekliyor (Karar 0) | no | Alarm ailesinden ÖNCE çözülüyor: "rutini durdur" alarmın stop fiilini taşıyor, ayıran şey isim |
| 297 | "Her sabah 08:00..." | Yok | Anlaşılır | DONE | PA | P1 | 296 | B14 | app/voice/intents.py, tests/voice_corpus/corpus.py | corpus r.* (16) | üretim turu bekliyor (Karar 0) | no | r.create.1-3. Haber okuyan rutin için eylem `display_action` gibi metinsiz bir tür olmalı — serbest metin röleden geçmiyor (287'deki kısıt) |
| 298 | "Evden çıkınca..." | Yok | Anlaşılır | DONE | PA | P1 | 292,296 | B14 | app/voice/intents.py, app/routines/triggers.py | corpus r.* (16), test_routines_voice_tools.py (17) | üretim turu bekliyor (Karar 0) | no | r.create.4; varlık tetikleyicisi artık yaratılabiliyor ve gerçekten ateşleniyor |
| 299 | "Bilgisayar boşta kalınca..." | Yok | Anlaşılır | DONE | PA | P1 | 294,296 | B14 | app/voice/intents.py, app/routines/triggers.py, app/main.py:_owner_device_idle_s | corpus r.* (16), test_routines_pause_and_condition.py (19) | üretim turu bekliyor (Karar 0) | no | r.create.5. Otobüste boşta-geçiş OLAYI yok, o yüzden varlık tetikleyicisi olamazdı — koşul tetikleyicisi her tikte `input_idle_s`'e bakıyor. Cihazların EN DÜŞÜĞÜ: kapalı dizüstü, masaüstünde yazan sahip hakkında bir şey söylemez |

## J. PRESENCE / ACTIVE EYE / DISPLAY (300–334)

| ID | FEATURE | CURRENT_STATUS | TARGET_STATUS | IMPL | PROOF | PRI | DEPS | BATCH | SOURCE_REFERENCES | TEST_REFERENCES | RUNTIME_PROOF | OWNER_ACTION | NOTES |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 300 | Camera open | Yalnız tarayıcı sekmesinde | Cihazda da | PARTIAL | PA | P2 | 327 | B48 | apps/web/ | presence testleri | eye_enabled=false | kamera kararı | B48: cihaz kamerası Karar 8 (kamera kararı) bekliyor |
| 301 | Camera close | Kamerayı çalıştıran sekme kapanırken `eye.stream_stopped` kaydedilir (keepalive; rıza bayrağına dokunmaz - kapanan sekme bir karar değildir) | Bildirimli kapanış | DONE | PA | P2 | 300 | B48 | app/presence/routes.py:post_eye_stream_stopped; apps/web/app/lib/eye/client.ts:notifyEyeStreamStopped; core/EyeControl.tsx | test_presence_b48.py (kanıt, rıza değişmez) · b48-web.test.tsx (keepalive, disable değil) | — | no | — |
| 302 | Camera reopen | Sunucu 'açık' derken bu sekmede kamera çalışmıyorsa kontrol bunu söyler ve 'Gözü aç'ı gösterir; kamera asla kendiliğinden açılmaz | Yeniden açılır | DONE | PA | P2 | 301 | B48 | apps/web/app/core/EyeControlView.tsx | b48-web.test.tsx (bildirim yalnız uyuşmazlıkta) | tarayıcıda görsel doğrulama | no | — |
| 303 | Camera privacy state | Sunucu durumu ile bu sekmenin kamerası ayrı ayrı ve uyuşmazlık açıkça gösterilir | Görünür durum | DONE | PA | P2 | 300 | B48 | apps/web/app/core/EyeControlView.tsx | b48-web.test.tsx | tarayıcıda görsel doğrulama | no | — |
| 304 | PRESENT | Çalışıyor | Aynı | DONE | PR | P1 | — | — | app/presence/ | presence testleri | canlı /v1/presence | no | — |
| 305 | AWAY | Çalışıyor | Aynı | DONE | PR | P1 | — | — | app/presence/ | presence testleri | canlı | no | — |
| 306 | RETURNED | Çalışıyor | Aynı | DONE | PR | P1 | — | — | app/presence/ | presence testleri | canlı | no | — |
| 307 | RESTING | Üretimde ERİŞİLEMEZ (duruş sinyali hep unknown) | Erişilebilir | MISSING | NYP | P2 | 327 | B48 | app/presence/ | presence testleri | canlı | no | 333'ü imkânsız kılıyor; B48: duruş sinyali yalnız kamerayla - Karar 8; motor artık sessiz saatle eşik uygular (312) |
| 308 | LIKELY_ASLEEP | Üretimde ERİŞİLEMEZ | Erişilebilir | MISSING | NYP | P2 | 327 | B48 | app/presence/ | presence testleri | canlı | no | 333'ü imkânsız kılıyor; B48: duruş sinyali yalnız kamerayla - Karar 8; eşik sahibin sessiz saatine bağlı (312/313) |
| 309 | UNKNOWN | Çalışıyor | Aynı | DONE | PR | P1 | — | — | app/presence/ | presence testleri | canlı | no | — |
| 310 | Temporal confidence | Gözlemin ağırlığı kaynağının güven ömrü boyunca doğrusal azalır (ömür sonunda 1 - freshness_decay) ve ağırlıklı ortalamaya girer: taze gözlem eskisinden çok sayılır, eşit yaşlı pencere güvenini kaybetmez; ömür dolunca eskisi gibi düşer | Tam | DONE | PA | P2 | — | B48 | app/presence/engine.py:_freshness_weight/_base_confidence | test_presence_b48.py (taze önde > eski önde; eşit yaşlı pencere; decay=0 eski davranış) · test_presence_engine | — | no | — |
| 311 | Input activity fusion | Çalışıyor, tarayıcısız | Aynı | DONE | PR | P1 | — | — | devices/windows-agent | presence testleri | sources:["input"] | no | Tek gerçek tarayıcısız varlık yolu |
| 312 | Time-of-day fusion | Dinlenmenin LIKELY_ASLEEP'e yükselme eşiği sahibin sessiz saatlerine göre: içinde normal, dışında `likely_asleep_after_outside_quiet_s`; okunamayan pencere eşiği değiştirmez | Tam | DONE | PA | P2 | 310 | B48 | app/presence/engine.py:_inside_quiet_hours/_time_of_day_policy | test_presence_b48.py (gece yarısını aşan pencere kendi saat diliminde; gece 30 dk = uyku, öğleden sonra = dinlenme) | — | no | Durumlar üretimde kamerayla erişilir (307/308) |
| 313 | Owner preference fusion | Her girişte sahibin ambient politikasındaki sessiz saatler ve dış eşik füzyon politikasına taşınır (okunamazsa eşikler aynen kalır) | Tam | DONE | PA | P2 | 310 | B48 | app/presence/service.py:_apply_owner_preferences; engine.py:set_policy | test_presence_b48.py (ambient politikası → motor) | — | no | — |
| 314 | Camera failure is not asleep | Doğru uygulanmış | Aynı | DONE | PA | P0 | — | — | app/presence/ | presence testleri | — | no | Rewrite gerekmez |
| 315 | UNKNOWN keeps display on | Doğru uygulanmış | Aynı | DONE | PA | P0 | — | — | app/ambient/ | ambient testleri | — | no | Güvenli varsayılan |
| 316 | Keyboard wake | Çalışıyor | Aynı | DONE | PR | P1 | — | — | devices/windows-agent | ambient testleri | — | no | — |
| 317 | Mouse wake | Çalışıyor | Aynı | DONE | PR | P1 | — | — | devices/windows-agent | ambient testleri | — | no | — |
| 318 | Display off | Çalışıyor | Aynı | DONE | PR | P1 | — | — | devices/windows-agent | ambient testleri | — | no | — |
| 319 | Display wake | Çalışıyor | Aynı | DONE | PR | P1 | — | — | devices/windows-agent | ambient testleri | — | no | — |
| 320 | Multiple-monitor power | Kısmi | Tam | PARTIAL | NYP | P2 | — | B48 | devices/windows-agent | — | — | donanım yargısı | B48: çoklu monitör gücü donanım yargısı ister (sahip) |
| 321 | No topology/resolution modification | Uygulanıyor | Aynı | DONE | PA | P0 | — | — | devices/windows-agent | ambient testleri | — | no | Ürün sınırı |
| 322 | Alarm wake precedence | İki yakada da zorlanıyor | Aynı | DONE | PA | P0 | — | — | app/ambient/, devices/windows-agent | ambient testleri | — | no | Cihaz önce alarma bakıyor |
| 323 | Holdoff after input | Çalışıyor | Aynı | DONE | PA | P1 | — | — | app/ambient/ | ambient testleri | — | no | — |
| 324 | Holdoff after alarm | Çalışıyor | Aynı | DONE | PA | P1 | — | — | app/ambient/ | ambient testleri | — | no | — |
| 325 | Holdoff after explicit wake | Çalışıyor | Aynı | DONE | PA | P1 | — | — | app/ambient/ | ambient testleri | — | no | — |
| 326 | Device-local presence provider | Girdi tabanlı var, kamera yok | Tam | PARTIAL | PR | P2 | 327 | B48 | devices/windows-agent | presence testleri | sources:["input"] | no | B48: girdi tabanlı sağlayıcı korunur; 'Tam' için cihaz kamerası Karar 8 |
| 327 | Browser-independent camera provider | Cihazda kamera kodu YOK | Cihazda kamera | MISSING | NYP | P2 | — | B48 | devices/windows-agent | — | — | kamera kararı | 307,308,333'ün ön koşulu; B48: Karar 8 (kamera kararı) - kod yazılmadı |
| 328 | No raw camera archive | Uygulanıyor | Aynı | DONE | PA | P0 | — | — | app/presence/ | presence testleri | — | no | Ürün ilkesi — gevşetilmez |
| 329 | Structured perception only | Uygulanıyor | Aynı | DONE | PA | P0 | — | — | app/presence/ | presence testleri | — | no | Ürün ilkesi |
| 330 | Presence history | GET /v1/presence/history: defterdeki kalıcı geçişler (en yeni önce; güven, gerekçe, kaynaklar) ve motorun son bölümleri, sınırlı | Tam | DONE | PA | P2 | — | B48 | app/presence/routes.py:get_presence_history | test_presence_b48.py (liste; sınır 422; sahip kapısı) | — | no | — |
| 331 | Ambient policy UI | Kokpit ve Ayarlar'da dört anahtar (otomatik kapatma, yokken, uyurken, dönünce) sahip kontrolü; eşikler ve sessiz saatler hâlâ salt okunur (ses/REST ile değişir) | Tam | PARTIAL | PA | P2 | 685 | B48 | apps/web/app/core/panels/CockpitPanels.tsx:AmbientPanel; lib/cockpit/api.ts:updateAmbientPolicy | b48-web.test.tsx (düğmeler yalnız işleyici varken; PUT gövdesi) | tarayıcıda görsel doğrulama | no | Eşik düzenleme ayrı iş |
| 332 | Auto display-off on/off | 'Otomatik ekran kapatma' anahtarı panelden PUT /v1/ambient/policy ile (sesli aracın aynı yazımı), sonra panel yenilenir | Tam | DONE | PA | P2 | 331 | B48 | apps/web/app/core/panels/CockpitPanels.tsx; app/core/cockpit/page.tsx; app/settings/page.tsx | b48-web.test.tsx | tarayıcıda görsel doğrulama | no | — |
| 333 | "Uyurken ekranı kapat" | Hiç tetiklenemiyor (307/308 erişilemez) | Çalışır | MISSING | NYP | P2 | 308 | B48 | app/ambient/, app/voice/intent/ | — | — | no | Politika hazır, sinyal yok; B48: politika kamera + LIKELY_ASLEEP ister (ambient decide); Karar 8 bekliyor |
| 334 | "Ben dönünce aç" | Çalışıyor | Aynı | DONE | PR | P1 | — | — | app/ambient/ | ambient testleri | — | no | — |

## K. MAIL / CALENDAR (335–366)

> Ölçüm: IMAP/SMTP/CalDAV kodu **gerçek ve iyi test edilmiş** (soket düzeyinde testler, MIME
> yuvalama ve başlık enjeksiyonu sertleştirmesi). Hiçbir hesap yapılandırılmamış; `.env.example`
> bu anahtarları içermiyor. Bu yüzden çoğu satır `BLOCKED_PROVIDER` + `PROVIDER_UNAVAILABLE`.

| ID | FEATURE | CURRENT_STATUS | TARGET_STATUS | IMPL | PROOF | PRI | DEPS | BATCH | SOURCE_REFERENCES | TEST_REFERENCES | RUNTIME_PROOF | OWNER_ACTION | NOTES |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 335 | IMAP read | Kod gerçek; B45'te ek baytları için Message-ID ile salt-okuma (EXAMINE) yeniden çekme eklendi | Çalışır | BLOCKED_PROVIDER | PA | P2 | — | B45 | app/mail/providers.py:ImapMailProvider | soket düzeyi testler + test_mail_b45.py (çok parçalı mesaj, sahte IMAP sunucusu) | gerçek hesap | hesap bilgisi (READY_FOR_OWNER) | Kod rewrite gerekmez |
| 336 | SMTP send | Kod gerçek, varsayılan kapalı; yanıt artık References zinciriyle gider | Çalışır | BLOCKED_PROVIDER | PA | P2 | 335 | B45 | app/mail/providers.py:SmtpMailSender | soket düzeyi testler + test_mail_b45.py | gerçek hesapta gönderim | hesap bilgisi (READY_FOR_OWNER) | ROLLBACK: gönderim bayrağı kapalı kalır |
| 337 | CalDAV read | Kod gerçek; B46'da VALARM okunur (özellikleri etkinliğe sızmaz), her tekrarda hatırlatıcı ve `recurring` bayrağı | Çalışır | BLOCKED_PROVIDER | PA | P2 | — | B46 | app/calendar/ics.py:parse_calendar | calendar testleri + test_calendar_b46.py | gerçek hesap | hesap bilgisi (READY_FOR_OWNER) | — |
| 338 | Mail list | Sağlayıcıya bağlı; saatli yoklama indeksi de doldurur (360) | Çalışır | BLOCKED_PROVIDER | PA | P2 | 335 | B45 | app/mail/ | mail testleri + test_mail_b45.py | gerçek hesap | hesap bilgisi (READY_FOR_OWNER) | — |
| 339 | Mail search | Sağlayıcıya bağlı | Çalışır | BLOCKED_PROVIDER | PA | P2 | 335 | B45 | app/mail/ | mail testleri | gerçek hesap | hesap bilgisi (READY_FOR_OWNER) | — |
| 340 | Mail summarize | Sağlayıcıya bağlı | Çalışır | BLOCKED_PROVIDER | PA | P2 | 338 | B45 | app/mail/ | mail testleri | gerçek hesap | hesap bilgisi (READY_FOR_OWNER) | — |
| 341 | Thread summarize | Sağlayıcıya bağlı | Çalışır | BLOCKED_PROVIDER | PA | P2 | 340 | B45 | app/mail/ | mail testleri | gerçek hesap | hesap bilgisi (READY_FOR_OWNER) | — |
| 342 | Draft | Kod var | Çalışır | BLOCKED_PROVIDER | PA | P2 | 335 | B45 | app/mail/ | mail testleri | gerçek hesap | hesap bilgisi (READY_FOR_OWNER) | — |
| 343 | Reply draft | Yanıt gönderilirken References zinciri özgün mesajdan yeniden hesaplanır (özgünün References'ı + Message-ID'si, en çok 50) | Doğru zincir | DONE | PA | P2 | 346 | B45 | app/mail/service.py:_reply_references | test_mail_b45.py (truth.json'un zinciri gönderilen taslakta) | gerçek hesapta alıcının zinciri | hesap bilgisi (READY_FOR_OWNER) | 346 ile aynı düzeltme |
| 344 | Forward draft | Kod var | Çalışır | BLOCKED_PROVIDER | PA | P2 | 342 | B45 | app/mail/ | mail testleri | gerçek hesap | hesap bilgisi (READY_FOR_OWNER) | — |
| 345 | Send confirmation gate | Kapı sahte gönderici üzerinden uçtan uca çalışıyor (okundu-geri-bildirim + sonraki tur); gerçek gönderim hesap ister | Çalışır | BLOCKED_PROVIDER | PA | P2 | 336 | B45 | app/mail/, app/security/ | atomik durum geçişi testleri + test_mail_b45.py | gerçek hesap | hesap bilgisi (READY_FOR_OWNER) | — |
| 346 | Reply References correctness | `send` boş tuple geçiyordu; artık özgünün zinciri + Message-ID; 100 derinlikte en yeni 50 bağlantı; özgün okunamazsa en az yanıtlanan mesaj | Zincir kırılmaz | DONE | PA | P2 | — | B45 | app/mail/service.py:_reply_references/send | test_mail_b45.py (gerçek servis; derin zincir; okunamayan özgün) | — | no | Eski test servisi atlıyordu; yenisi gönderileni okur |
| 347 | Attachment read | Odaktaki mesajın ekleri (ad, tür, boyut) mesajın kendisinden; baytlar hiçbir makbuza, listeye ya da defter satırına girmez; sahte sağlayıcı fikstür baytlarını meta veriden ayrı tutar; `mail.attachments` sesli aracı + MAIL_ATTACHMENTS niyeti (tam sözcükler: 'ekip', 'ekran', 'ekle' eşleşmez) | Çalışır | DONE | PA | P2 | 335 | B45 | app/mail/service.py:attachments; providers.py:_is_attachment_part | test_mail_b45.py (liste; bayt sızmaz; yönlendirme; korpus mc.attachments.*) | gerçek hesap | hesap bilgisi (READY_FOR_OWNER) | — |
| 348 | Attachment save | Baytlar sağlayıcıdan (IMAP: Message-ID ile yeniden çekme) çekilir, nesne deposunda kendi hash'iyle tutulur, cihaz tek kullanımlık 10 dk jetonla `file.fetch` ile İndirilenler'e çeker ve hash'i yeniden doğrular; liste ve çıkarım AYNI ek yüklemini kullanır (sıra numarası kayamaz); güvenli dosya adı; sahibin web indirmesi GET /v1/mail/attachments; `mail.save_attachment` | Çalışır | DONE | PA | P2 | 347 | B45 | app/mail/service.py:save_attachment; attachment_fetch.py; routes.py:device_router | test_mail_b45.py (cihaza giden bayt = sağlayıcının baytı; tek kullanım; hash uyuşmazlığı 404; dizin sınırı; cihazsız red; IMAP yeniden çekme) | gerçek hesapta ek | hesap bilgisi (READY_FOR_OWNER) | Cihaz yolu artefakt render jetonunun disiplini |
| 349 | Calendar list | Sağlayıcıya bağlı; okunan etkinlikler indekse yazılır (359) | Çalışır | BLOCKED_PROVIDER | PA | P2 | 337 | B46 | app/calendar/ | calendar testleri + test_calendar_b46.py | gerçek hesap | hesap bilgisi (READY_FOR_OWNER) | — |
| 350 | Calendar today | Sağlayıcıya bağlı | Çalışır | BLOCKED_PROVIDER | PA | P2 | 349 | B46 | app/calendar/ | calendar testleri | gerçek hesap | hesap bilgisi (READY_FOR_OWNER) | — |
| 351 | Calendar week | Sağlayıcıya bağlı | Çalışır | BLOCKED_PROVIDER | PA | P2 | 349 | B46 | app/calendar/ | calendar testleri | gerçek hesap | hesap bilgisi (READY_FOR_OWNER) | 365 ile ortak |
| 352 | Create event proposal | Öneri tekrar kuralını ve hatırlatmayı taşır; sahip ikisini de okunan öneride duyar | Çalışır | BLOCKED_PROVIDER | PA | P2 | 337 | B46 | app/calendar/service.py:propose | calendar testleri + test_calendar_b46.py | gerçek hesap | hesap bilgisi (READY_FOR_OWNER) | — |
| 353 | Edit event proposal | Kod var; tekrarlayan bir etkinliğin tek tekrarı taşınmaz (yazıcı bütün etkinliği değiştirir, seri silinirdi); erteleme var olan hatırlatmayı korur | Çalışır | BLOCKED_PROVIDER | PA | P2 | 352 | B46 | app/calendar/service.py:propose_reschedule | calendar testleri + test_calendar_b46.py | gerçek hesap | hesap bilgisi (READY_FOR_OWNER) | — |
| 354 | Cancel event | Sahip politikası `calendar_cancel_policy`: varsayılan `refuse` (M21 sınırı, dürüst red); `confirm` seçilirse iptal bir ÖNERİ olur - okunur, sonraki turda onaylanır, sonra yazıcının `delete`'i (CalDAV DELETE; 404 = zaten yok); indeks satırları silinir; tekrarlayan seri tek cümleyle silinmez; bilinmeyen politika sözcüğü reddi korur | Politikayla | DONE | PA | P2 | 678 | B46 | app/calendar/service.py:cancel_event/_propose_cancel; providers.py:delete | test_calendar_b46.py (varsayılan red; öneri → okuma → onay → tek silme; seri reddi; bilinmeyen politika) | politika seçimi READY_FOR_OWNER | onay politikası (sahip) | 366 ile ortak |
| 355 | RSVP | Yazılmadı: yanıt (PARTSTAT) iTIP/CalDAV zamanlama sunucusu davranışına bağlı ve katılımcı satırları okunmuyor; gerçek hesapla doğrulanamayan bir yanıt gönderme yolu yazılmadı | Çalışır | DEFERRED | NYP | P2 | 349 | B46 | app/calendar/ | — | — | hesap bilgisi + zamanlama sunucusu | Hesap bağlandığında ayrı iş |
| 356 | RRULE | Yazılır: sahibin sözcükleri (her gün / her hafta pazartesi / hafta içi / iki haftada bir / her ay / her yıl / N kez) yönlendiricide kurala çevrilir, dar bir sözlükle doğrulanır (FREQ, INTERVAL, COUNT, UNTIL, BYDAY) ve dateutil ile ayrıştırılır; yazıcının gönderdiği kural okuyucunun genişlettiği seridir | Tekrar kuralı yazılır | DONE | PA | P2 | 352 | B46 | app/calendar/ics.py:validate_rrule/build_vevent; tr_time.py:extract_recurrence; intents.py:_recurrence_anchor | test_calendar_b46.py (kanonik; reddedilen kurallar; yazıcı → okuyucu gidiş-dönüş; sözcük tablosu; yönlendirme) | — | no | BYMONTHDAY yok: ayın günü başlangıç tarihinden |
| 357 | VALARM | Yazılır (DISPLAY, TRIGGER -PT{m}M, en çok 7 gün) ve okunur (göreli başlangıç tetikleyicileri; mutlak / bitişe göre / başlangıçtan sonra olanlar hatırlatma sayılmaz); 'X dakika önce hatırlat' saat ya da süre olarak okunmaz | Hatırlatıcı yazılır | DONE | PA | P2 | 352 | B46 | app/calendar/ics.py:parse_trigger_minutes/build_vevent; tr_time.py:extract_reminder_minutes/without_reminder | test_calendar_b46.py (tetikleyici tablosu; VALARM SUMMARY sızmaz; süre olarak okunmaz) | — | no | — |
| 358 | Reminder | Rutin saatin `calendar` alt-tikinde: önümüzdeki gün içinde hatırlatıcısı gelmiş ve başlamamış her tekrar için bir sahip bildirimi (`calendar.reminder`, normal öncelik, sessiz saatlere takılmaz - sahibin kendi koyduğu an); her tekrar bir kez (`reminded_at`); başlamış etkinliğe hatırlatma yok | Hatırlatma | DONE | PA | P2 | 357 | B46 | app/calendar/service.py:remind_due; syncer.py; notifications/events.py:calendar_reminder | test_calendar_b46.py (gece bir kez; başlamışa yok; diğer bildirimler sabahı bekler) | — | no | 367 ile bağlı |
| 359 | Calendar index | Doldurulur: agenda okumaları (`source=read`) ve saatin aynası (`source=sync`) (uid, başlangıç) başına bir satır; ikinci okuma çoğaltmaz | Doldurulur | DONE | PA | P2 | 337 | B46 | app/calendar/service.py:_index_occurrences | test_calendar_b46.py (okuma bir kez; ayna iki kez çalışır) | — | no | Ölü tablo canlandı (göç 0058) |
| 360 | Mail polling | MailPoller rutin saatin `mail` alt-tikinde; aralık (varsayılan 300 sn, en az 60) kendi kendini kısar; yoklama yalnız listeler ve indeksler; hesapsız sessiz; başarısız yoklama bir sonraki aralıkta; ikinci yoklama hiçbir şey yazmaz | Düzenli çekme | DONE | PA | P2 | 335 | B45 | app/mail/poller.py; service.py:poll; routines/clock.py | test_mail_b45.py (iki kez çalıştırılır; kısma; başarısızlık; uygulama kablolaması) | gerçek hesap | hesap bilgisi (READY_FOR_OWNER) | — |
| 361 | Calendar sync | CalendarSyncer rutin saatte (varsayılan 120 sn, en az 60): 14 günlük ufuk indekse yansıtılır, yukarıda silinen etkinlik indeksten çıkar; kırpılmış (truncated) yanıt silme kanıtı sayılmaz; hesapsız sessiz; takvime asla yazmaz | Eşitleme | DONE | PA | P2 | 359 | B46 | app/calendar/service.py:sync; syncer.py; routines/clock.py | test_calendar_b46.py (ikinci geçiş sessiz; yukarıda silinen; kırpılmış yanıt; hesapsız; uygulama kablolaması) | gerçek hesap | hesap bilgisi (READY_FOR_OWNER) | — |
| 362 | Morning mail summary | 278 ile aynı cümle, sabah brifinginde | Çalışır | DONE | PA | P2 | 340 | B45 | app/briefing/service.py:_mail_sentence | test_mail_b45.py | gerçek hesap | hesap bilgisi (READY_FOR_OWNER) | 278 ile aynı |
| 363 | Morning calendar summary | 277 ile aynı cümle | Çalışır | DONE | PA | P2 | 350 | B46 | app/briefing/service.py:_calendar_sentence | test_calendar_b46.py | gerçek hesap | hesap bilgisi (READY_FOR_OWNER) | 277 ile aynı |
| 364 | "Maillerime bak" | Yönlenir (B27) | Yönlenir | DONE | PA | P2 | 338 | B45 | app/voice/intents.py:_mail_inbox_match | test_intent_daily_coverage.py (82), korpus d.inbox.* | üretim turu bekliyor (Karar 0) | no | 729 ile aynı iş — B27'de kapandı; B45 ek niyetleri bu cümleyi yakalamaz (test) |
| 365 | "Bu hafta ne var?" | Yönlenir (B27) | Yönlenir | DONE | PA | P2 | 351 | B46 | app/voice/intents.py:_calendar_agenda_match | test_intent_daily_coverage.py (82), korpus d.agenda.* | üretim turu bekliyor (Karar 0) | no | 730 ile aynı iş — B27'de kapandı |
| 366 | "Perşembe toplantısını iptal et" | Yönlenir (B27) → `calendar.cancel`: varsayılan dürüst red; B46 politika `confirm` iken öneri | Yönlenir | DONE | PA | P2 | 354 | B46 | app/voice/intents.py:_calendar_cancel_match; service.py:cancel_event | test_intent_daily_coverage.py (82), korpus d.cancel_event.*, test_calendar_b46.py | üretim turu bekliyor (Karar 0) | onay politikası (sahip) | 731 ile aynı iş |

## L. NOTIFICATIONS / OWNER REACHABILITY (367–390)

> Ölçüm: tarayıcı kapalıyken sahibe ulaşan **tek** canlı yol cihaz sesidir. Masaüstü
> bildirimi (toast) cihazın 85 yeteneği arasında yoktur. Anayasanın "tamamlanan iş kısaca
> haber verir" sözü bugün teknik olarak tutulamıyor.

| ID | FEATURE | CURRENT_STATUS | TARGET_STATUS | IMPL | PROOF | PRI | DEPS | BATCH | SOURCE_REFERENCES | TEST_REFERENCES | RUNTIME_PROOF | OWNER_ACTION | NOTES |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 367 | Durable notification table | Bellek içi kuyruk; yeniden başlatmada kaybolur | Kalıcı tablo | DONE | PA | P1 | — | B11 | app/notifications/models.py, app/notifications/service.py | test_notifications.py | üretim turu bekliyor (Karar 0) | no | Bellek içi deque bitti: satır her denemeden ÖNCE yazılıyor |
| 368 | In-app inbox | Kalıcı değil | Kalıcı kutu | DONE | PA | P1 | 367 | B11 | app/notifications/models.py, app/notifications/service.py, app/notifications/routes.py, apps/web/app/lib/cockpit/notifications.ts, apps/web/app/core/panels/CockpitPanels.tsx | test_notifications.py, notifications-panel.test.tsx (29) | üretim turu bekliyor (Karar 0) | no | Kutu tabloyu okuyor, hiçbir taşıyıcıya sormuyor. Panel üç durumu AYRI söylüyor: hiçbir kanal taşımadı / iletildi, okunmadı / okundu |
| 369 | Desktop toast | Cihazda böyle bir yetenek YOK | `desktop.notify` | PARTIAL | PA | P1 | 5 | B11 | packages/protocol/desktop-notify.json, app/notifications/toast.py, devices/.../Notify/ | test_notifications.py, DesktopNotifyContractTests.cs (11) | cihazda kurulum bekliyor | no | Sözleşme + iki yarı da bağlı; balon gerçek toast ama PROVEN_REAL kilitli ekran ister |
| 370 | Toast action buttons | Yok | Eylem düğmeleri | PARTIAL | PA | P1 | 369 | B11 | packages/protocol/desktop-notify.json, app/notifications/toast.py, devices/.../Notify/ | test_notifications.py, DesktopNotifyContractTests.cs (11) | WinRT yüzeyi gerekiyor | no | Düğmeler ayrıştırılıyor/taşınıyor; balonda düğme YOK — cihaz bunu 'detail' ile söylüyor |
| 371 | Device audio notification | Çalışıyor (broker WS, ses oturumu gerekmez) | Aynı | DONE | PR | P1 | — | — | devices/windows-agent | alarm testleri | üretim alarmı | no | Tek canlı ulaşma yolu |
| 372 | WebPush | İstemci tarafı (service worker) hiç yazılmamış, yük şifrelemesi yok | Çalışır | BLOCKED_OWNER | BLK | P1 | 367 | B11 | apps/web/ (service worker) | — | VAPID anahtarı (sahip) | VAPID anahtarı | Sunucu tarafı hazır değil; anahtar olmadan yazmak anlamsız |
| 373 | FCM | Yapılandırılmamış | Çalışır | BLOCKED_PROVIDER | PU | P1 | 367 | B12 | app/notifications/ | — | — | sağlayıcı hesabı | Merdivenin `push` basamağı yeri ayrılmış ve BOŞ: hesapsız bir taşıyıcı yazmak, hiç denenmemiş bir kanalı denenmiş göstermek olurdu |
| 374 | APNs | Yapılandırılmamış | Çalışır | BLOCKED_PROVIDER | PU | P1 | 367 | B12 | app/notifications/ | — | — | sağlayıcı hesabı | 373 ile aynı gerekçe |
| 375 | Delivery receipt | `fake` sağlayıcı "iletildi" diyor; duyurucu kalıcı damgalıyor | Gerçek makbuz | DONE | PA | P0 | 390 | B07 | app/mobile/providers.py:PushDelivery | test_bounded_delivery.py | üretim turu bekliyor (Karar 0) | no | Makbuzsuz 'delivered' inşa edilemiyor — kural değil, tip |
| 376 | Delivery retry | Sınırsız | Sınırlı + karantina | DONE | PA | P0 | 14 | B07 | app/notifications/delivery.py | test_bounded_delivery.py | üretim turu bekliyor (Karar 0) | no | 14 ile tek uygulama |
| 377 | Delivery history | Kısmi | Tam | DONE | PA | P1 | 367 | B11 | app/notifications/models.py, app/notifications/service.py, app/notifications/routes.py | test_notifications.py | üretim turu bekliyor (Karar 0) | no | Ulaşılamayanlar da listede — asıl önemli kısım o |
| 378 | Priority | Yok | Öncelik | DONE | PA | P1 | 367 | B11 | app/notifications/models.py, app/notifications/service.py | test_notifications.py | üretim turu bekliyor (Karar 0) | no | Üç seviye; bilinmeyen bir değer normal'e düşüyor, dördüncü seviye yaratmıyor |
| 379 | Quiet hours | Yok | Sessiz saatler | DONE | PA | P1 | 378 | B11 | app/notifications/models.py, app/notifications/service.py | test_notifications.py | üretim turu bekliyor (Karar 0) | no | Gürültü hakkında, gizleme hakkında değil: ertelenen satır kutuda duruyor |
| 380 | Notification grouping | Yok | Gruplama | DONE | PA | P1 | 367 | B11 | app/notifications/models.py, app/notifications/service.py | test_notifications.py | üretim turu bekliyor (Karar 0) | no | Yalnız OKUNMAMIŞ kardeş devralınıyor; okunmuş olan sahibin bildiği şeydir |
| 381 | "Task completed" | Kısmi (teslim yalan olabilir) | Gerçek teslim | DONE | PA | P1 | 375 | B12 | app/notifications/events.py, app/artifacts/service.py | test_notification_events.py (17) | üretim turu bekliyor (Karar 0) | no | Görev READY'ye GEÇERKEN yayılıyor, kuyruğa girerken değil; hook en iyi çaba ve rollback'li |
| 382 | "Task failed" | Kısmi | Gerçek teslim | DONE | PA | P1 | 375 | B12 | app/notifications/events.py, app/artifacts/service.py | test_notification_events.py (17) | üretim turu bekliyor (Karar 0) | no | Sahibe hata SINIFI gidiyor, traceback değil |
| 383 | "Owner approval required" | Yok | Bildirilir | DONE | PA | P1 | 367 | B12 | app/notifications/events.py, app/evolution/service.py | test_notification_events.py (17) | üretim turu bekliyor (Karar 0) | no | OWNER_APPROVAL_REQUIRED durumuna geçişte; owner gate'leri artık sessiz beklemiyor |
| 384 | "Rollback happened" | Yok | Bildirilir | DONE | PA | P1 | 367 | B12 | app/notifications/events.py, app/selfhealing/service.py | test_notification_events.py (17) | üretim turu bekliyor (Karar 0) | no | 7 günde 16 geri alma vardı ve hiçbiri duyurulmadı; gövde iki sürümü de adlandırıyor |
| 385 | "Backup failed" | Yok | Bildirilir | DONE | PA | P0 | 647 | B12 | app/notifications/events.py, app/main.py | test_notification_events.py (17) | üretim turu bekliyor (Karar 0) | no | B08 arızayı GÖRÜNÜR yaptı; bu SÖYLÜYOR. Süpürge, çünkü arızalanan birim bu sürece çağrı yapamaz |
| 386 | "Alarm failed" | Yok | Bildirilir | DONE | PA | P1 | 367 | B12 | app/notifications/events.py, app/alarms/service.py | test_notification_events.py (17) | üretim turu bekliyor (Karar 0) | no | Çalmayan alarm aksi halde uyanamayarak keşfedilir |
| 387 | "Research finished" | Kısmi | Gerçek teslim | DONE | PA | P1 | 375 | B12 | app/notifications/events.py, app/artifacts/service.py | test_notification_events.py (17) | üretim turu bekliyor (Karar 0) | no | Araştırma türleri kendi olayını alıyor, genel "iş bitti" değil |
| 388 | "SelfDev candidate ready" | Yok | Bildirilir | DONE | PA | P2 | 609 | B12 | app/notifications/events.py, app/evolution/service.py | test_notification_events.py (17) | üretim turu bekliyor (Karar 0) | no | Sistemin ürettiği en az acil şey: LOW, ve sessiz saatlerde sabahı bekliyor |
| 389 | Notification fallback ladder | Yok | toast to ses to push to kutu | DONE | PA | P1 | 367,369 | B11 | app/notifications/ladder.py | test_notifications.py | üretim turu bekliyor (Karar 0) | no | toast→ses→push→kutu; kutu zemin, o yüzden merdiven düşemez |
| 390 | Fake provider must never report delivered | Sahte "delivered" diyebiliyor | Yapısal olarak diyemez | DONE | PA | P0 | 5 | B07 | app/mobile/providers.py:FakePushProvider | test_bounded_delivery.py | üretim turu bekliyor (Karar 0) | no | Sahtenin gösterecek makbuzu yok, o yüzden kelimeyi söyleyemiyor |

## M. ARTIFACT FACTORY (391–416)

> Ölçüm: denetimdeki en iyi mühendislik. Bayt-belirlenimli, OOXML normalize, bağımsız
> yeniden açma, xlsx'te toplamların bağımsız yeniden hesabı.

| ID | FEATURE | CURRENT_STATUS | TARGET_STATUS | IMPL | PROOF | PRI | DEPS | BATCH | SOURCE_REFERENCES | TEST_REFERENCES | RUNTIME_PROOF | OWNER_ACTION | NOTES |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 391 | PDF | Çalışıyor | Aynı | DONE | PR | P1 | — | — | app/artifacts/ | artifacts testleri | 76 artefakt üretimde | no | Rewrite gerekmez |
| 392 | DOCX | Çalışıyor | Aynı | DONE | PR | P1 | — | — | app/artifacts/ | artifacts testleri | üretim | no | — |
| 393 | XLSX | Kod gerçek; her sürüm provenans + kaynak manifesti taşır (B42) | Üretimde kanıtlanır | DONE | PA | P1 | — | B42 | app/artifacts/renderers.py; provenance.py | test_artifact_renderers.py; test_artifact_validation.py | üretim duman testi READY_FOR_OWNER | no | Üretim VM'de bir xlsx üretilip indirilmesi sahibin (checkpoint 18) |
| 394 | PPTX | Kod gerçek; her sürüm provenans + kaynak manifesti taşır (B42) | Üretimde kanıtlanır | DONE | PA | P1 | — | B42 | app/artifacts/renderers.py; provenance.py | test_artifact_renderers.py; test_artifact_factory.py | üretim duman testi READY_FOR_OWNER | no | Checkpoint 18 |
| 395 | HTML | Çalışıyor | Aynı | DONE | PR | P1 | — | — | app/artifacts/ | artifacts testleri | üretim | no | — |
| 396 | Markdown | Çalışıyor | Aynı | DONE | PR | P1 | — | — | app/artifacts/ | artifacts testleri | üretim | no | — |
| 397 | TXT | Çalışıyor | Aynı | DONE | PR | P1 | — | — | app/artifacts/ | artifacts testleri | üretim | no | — |
| 398 | CSV | Kod gerçek; her sürüm provenans + kaynak manifesti taşır (B42) | Üretimde kanıtlanır | DONE | PA | P1 | — | B42 | app/artifacts/renderers.py; provenance.py | test_artifact_renderers.py | üretim duman testi READY_FOR_OWNER | no | Checkpoint 18 |
| 399 | JSON | Kod gerçek; her sürüm provenans + kaynak manifesti taşır (B42) | Üretimde kanıtlanır | DONE | PA | P1 | — | B42 | app/artifacts/renderers.py; provenance.py | test_artifact_renderers.py | üretim duman testi READY_FOR_OWNER | no | Checkpoint 18 |
| 400 | Image artifact | Yaratıcı yolun depoladığı görsel (`creative/<run>/<ad>`) POST /v1/artifacts/image ile kendi kopyasıyla (`artifacts/<id>/v1/…`) `image` türünde artefakt olur: provenans (aktör, Pillow sürümü), manifest (kaynak creative run), sürüm, kopyalama, politikayla silme, sha256 karşılaştırma; düzenleme yaratıcı araca bırakılır (`image_not_editable`) | Tam | DONE | PA | P2 | 492 | B42 | app/artifacts/lifecycle.py:register_image_artifact; routes.py:register_image_artifact | test_artifact_b42.py (kayıt, kopya silinince kaynak kalır; kopya/karşılaştır; yok/görsel değil; rota) | — | no | Sesli kayıt B43 (yaratıcı teslim) |
| 401 | Deterministic render | Aynı girdi aynı bayt | Aynı | DONE | PA | P1 | — | — | app/artifacts/ | belirlenimlilik testi | — | no | OOXML zip normalize |
| 402 | Independent read-back | Üretilen dosya yeniden açılıyor | Aynı | DONE | PA | P1 | — | — | app/artifacts/ | read-back testleri | — | no | Ürün ilkesi uygulanmış |
| 403 | Reopen validation | Çalışıyor | Aynı | DONE | PA | P1 | — | — | app/artifacts/ | read-back testleri | — | no | — |
| 404 | Spreadsheet formula validation | Toplamlar bağımsız yeniden hesaplanıyor | Aynı | DONE | PA | P1 | — | — | app/artifacts/ | formül testi | — | no | — |
| 405 | Provenance | Her sürüm satırında `provenance_json` (aktör + çalışma zamanı + soy + an) ve `source_manifest_json` (ne ile yapıldı) — üretim anında yazılır, sonradan çıkarsanmaz (göç 0054) | Tam | DONE | PA | P2 | — | B42 | app/artifacts/provenance.py; factory.py; models.py | test_artifact_b42.py (create provenans+manifest; aktörsüz = system) | — | no | 406-408'in şemsiyesi |
| 406 | Actor provenance | `Actor(kind, ref, session_id)`: owner_voice (araç çağrı kimliği + oturum), owner_rest (oturum), executive_run, research_task, edit, clone, system; bilinmeyen tür reddedilir | Kim üretti kayıtlı | DONE | PA | P2 | 405 | B42 | app/artifacts/provenance.py:Actor; tools_artifacts.py; routes.py; executive/activities.py | test_artifact_b42.py (owner_rest; edit; clone; bilinmeyen tür) | — | no | — |
| 407 | Library/runtime version provenance | `runtime_provenance`: Python sürümü + biçimin kütüphanelerinin (python-docx, openpyxl, python-pptx, fpdf2/pypdf, pillow) kurulu sürümleri `importlib.metadata`'dan + renderer modülü | Kaydedilir | DONE | PA | P2 | 405 | B42 | app/artifacts/provenance.py:runtime_provenance | test_artifact_b42.py (xlsx+docx → openpyxl+python-docx) | — | no | Belirlenimliliğin kanıtı: aynı spec + aynı sürümler = aynı bayt |
| 408 | Source manifest | `source_manifest`: tür, başlık, dil, spec hash, biçimler, söylenen sayılar, sayımlar (bölüm/sayfa/satır/slayt), kaynaklar (≤200) — fabrika her sürümde yazar | Yazılır | DONE | PA | P2 | 405 | B42 | app/artifacts/provenance.py:source_manifest; factory.py | test_artifact_b42.py (sayımlar, hash, kaynaklar) | — | no | M13'ün boş kalan kolonu artık dolu |
| 409 | Artifact versioning | `service.list_versions` + GET /v1/artifacts/{id}/versions: her sürüm hash/tarih/manifest/provenans ile; düzenleme aynı artefaktın SONRAKİ sürümü, geçmiş okunur kalır | Tam | DONE | PA | P2 | 405 | B42 | app/artifacts/service.py:list_versions; lifecycle.py:add_version; routes.py:list_artifact_versions | test_artifact_b42.py (v1+v2; rota) | — | no | — |
| 410 | Artifact edit | `artifact.edit` / POST /edit: yapısal işlemler (set_title, append/replace/remove_section, append_slide, append_row, append_bullet; ≤20) mevcut spec'e uygulanır, spec'in kendi kuralları yeniden geçer (sahibin söylemediği sayı reddedilir), her biçim yeniden üretilir; uymayan işlem adıyla ret | Düzenleme | DONE | PA | P2 | 409 | B42 | app/artifacts/lifecycle.py:apply_edit/edit_artifact; tools_artifacts.py:artifact_edit | test_artifact_b42.py (v2 + soy; uydurma sayı ret; uymayan ret; satır ekleme; ses) | — | no | Görsel için `image_not_editable` |
| 411 | Artifact clone | `artifact.clone` / POST /clone: yeni artefakt, ilk sürümü kaynağın (istenen) sürümünün kopyası, provenans `derived_from` kaynağı adlar; kopya odağa alınır | Kopyalama | DONE | PA | P2 | 409 | B42 | app/artifacts/lifecycle.py:clone_artifact; tools_artifacts.py:artifact_clone | test_artifact_b42.py (kopya + soy; adlı sürüm; ses + odak) | — | no | — |
| 412 | Artifact delete policy | `artifact_delete_policy` = confirm (varsayılan: aynı çağrıda açık 'Evet, sil'), deny (asla), free; silme her render nesnesini depodan kaldırır, satır+sürümler+provenans ARŞİV olarak kalır; `artifact.delete` / POST /delete; deftere yazılır | Politikayla silme | DONE | PA | P2 | 678 | B42 | app/artifacts/lifecycle.py:delete_artifact; config.py:artifact_delete_policy | test_artifact_b42.py (confirm bekler→siler; deny; free; ses: sor→evet; modelin confirm'i deny'ı aşamaz) | politika seçimi READY_FOR_OWNER | onay politikası | Varsayılan confirm; checkpoint 18 |
| 413 | Export to owner disk | Tek kullanımlık 10 dk jetonla cihaza teslim | Aynı | DONE | PR | P1 | — | — | app/artifacts/, devices/windows-agent | artifacts testleri | üretim teslimi | no | Rewrite gerekmez |
| 414 | Artifact narration | Yok | Seslendirilir | DONE | PA | P1 | 224 | B21 | app/voice/realtime_sessions/tools.py:narration_start | test_voice_realtime_sessions.py | TTS kredisi yok: gerçek ses üretimi bekliyor | TTS kotası | sesle başlatılır; kimlikle, tahminle değil |
| 415 | Artifact compare | `artifact.compare` / GET /compare: iki sürüm/artefakt arasında tür, başlık, eklenen/çıkarılan/değişen bölümler, satır sayısı, yalnız birinde olan sayılar — adlandırılmış cümle, asla çıplak 'farklı' | Karşılaştırma | DONE | PA | P2 | 409 | B42 | app/artifacts/lifecycle.py:compare_specs/compare_sentence | test_artifact_b42.py (eklenen/çıkarılan/değişen; aynı; türler farklı) | — | no | Görseller sha256 ile |
| 416 | Artifact diff | Kanonik spec'lerin birleşik farkı (`difflib`, ≤400 satır, eklenen/çıkarılan sayımı) karşılaştırma yanıtında `diff` | Fark | DONE | PA | P2 | 415 | B42 | app/artifacts/lifecycle.py:diff_specs | test_artifact_b42.py (+Riskler / -Sorumlular satırları) | — | no | — |

## N. APP FACTORY / CODE FACTORY (417–452)

> Ölçüm: 3 şablon, yalnızca slot doldurma. Model üreteci **koşulsuz hata** fırlatıyor.
> `entities`/`screens` doğrulanıp saklanıyor ama hiçbir şablonda karşılığı yok — sessizce
> atılıyor. Üretimde hiç koşmadı ve üç şablonun üçü de cihaz tarafından reddedilir.

| ID | FEATURE | CURRENT_STATUS | TARGET_STATUS | IMPL | PROOF | PRI | DEPS | BATCH | SOURCE_REFERENCES | TEST_REFERENCES | RUNTIME_PROOF | OWNER_ACTION | NOTES |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 417 | Fixed template generation | Şablonla üretim cihazda kabul ediliyor | Çalışır | DONE | PA | P0 | 4,421 | B03 | app/appfactory/ | test_app_manifest_contract.py | — | no | — |
| 418 | Task tracker | `<port>` + port 8765 | Çalışır | DONE | PA | P0 | 421 | B03 | templates/task-tracker/manifest.json | test_appfactory_generator.py | — | no | — |
| 419 | Static site | `<port>` + port 8766 | Çalışır | DONE | PA | P0 | 421 | B03 | templates/static-page/manifest.json | test_appfactory_generator.py | — | no | — |
| 420 | CLI tool | `run: {start: node cli.js}` + `port: 0` | Çalışır | DONE | PA | P0 | 421 | B03 | templates/cli-tool/manifest.json, ProjectManifest.cs, ProjectCapabilities.cs | AppManifestContractTests.cs | — | no | Cihaz da eksikti: web projesi 'hiçbir şeye bağlanmıyorum' diyemiyordu |
| 421 | Template manifest contract | Tek paylaşılan artefakt, iki yarı okuyor | Tek paylaşılan artefakt | DONE | PA | P0 | 5 | B03 | packages/protocol/app-manifest.example.json | test_app_manifest_contract.py, AppManifestContractTests.cs | — | no | — |
| 422 | General requirements parser | `app/appfactory/requirements.py`: Türkçe cümleden kayıt türleri (bağlaçla ayrılan isimler; ':' sonrası alanlar, ';' ile tür grupları), alan tipleri adından (tarih → date, tutar/adet → number, 'ödendi mi' → boolean), giriş / api / yalnız-api özellikleri, ad; okunamayan HER parça `unparsed` olarak makbuzda söylenir, asla sessizce atılmaz | Kullanılır | DONE | PA | P2 | 425 | B40 | app/appfactory/requirements.py | test_appfactory_b40.py (cümleler; belirsiz istek → soru; şablon sözleri korunur; okunamayan parça) | — | no | Sessiz veri kaybı bitti; boş istek 'Hangi kayıtları tutacağını söyler misiniz?' ile döner |
| 423 | Architecture planner | `planner.plan_architecture`: cihazın çalıştırabildiği şekil (node stdlib, JSON dosya deposu, /api katmanı, istenirse giriş, arayüz, testler) her katman gerekçesiyle; POST /v1/apps/plan sahibe hiçbir şey yazmadan gösterir | Planlar | DONE | PA | P2 | 425 | B40 | app/appfactory/planner.py; routes.py:plan | test_appfactory_b40.py (katmanlar + gerekçe; plan rotası) | — | no | Model karar vermez: plan deterministik |
| 424 | Project planner | `planner.plan_project`: dosya listesi (yol, amaç, katman, yazar) yazılış sırasıyla, giriş dosyası, run/test komutları, test planı; model yalnız `custom.js` ve `tests/custom.js` yuvalarını yazabilir | Planlar | DONE | PA | P2 | 423 | B40 | app/appfactory/planner.py:plan_project | test_appfactory_b40.py | — | no | — |
| 425 | Model-backed code generation | `code_model.py`: `CodeModel` (ScriptedCodeModel testler için; AnthropicCodeModel zorunlu tool-use, anahtar hatada asla), `ModelAssistedGenerator` (deterministik dosyalar + bayrak ve anahtar varsa modelin yuvaları; yuva dışı yol ret; model reddederse uygulama bütün kalır ve makbuz söyler) | Model üretir | DONE | PA | P2 | 417 | B40 | app/appfactory/code_model.py; main.py | test_appfactory_b40.py (scripted model; bayrak kapalı; ret; yuva dışı) | canlı: sahibin model bütçesiyle | model bütçesi (appfactory_model_generation_enabled + PAGENTOS_ANTHROPIC_API_KEY) | ClaudeAppGenerator seam'i yerini bu seam'e bıraktı |
| 426 | Multiple source files | Bileşik uygulama 8–14 dosya: schema.js, store.js, (auth.js), server.js, public/index.html+app.css+app.js+schema.js, tests/unit.js+integration.js+run.js(+custom.js)+browser-oracle.json, README.md, manifest.json | Tam | DONE | PA | P2 | 425 | B40 | app/appfactory/composer.py | test_appfactory_b40.py (dosya kümesi = plan) | — | no | — |
| 427 | Database generation | `schema.js` (tür/alan/doğrulama, UMD: tarayıcı ve node aynı) + `store.js` (JSON dosya, atomik yazma temp+rename, şema denetimi, id/createdAt/updatedAt, bellek modu) | Üretilir | DONE | PA | P2 | 425 | B40 | app/appfactory/composer.py:SCHEMA_JS/STORE_JS | test_appfactory_b40.py (üretilen birim testleri gerçek node altında geçer) | — | no | Sunucu kurulumu yok: JSON dosya dürüst kalıcı depo |
| 428 | API generation | `server.js`: /api/schema, /api/<tür> list/create/update/delete (400/401/404/405), /api/custom yüzeyi, public/ statik servis (yol hapsi); doğrulayıcı `node <entry>` komutunu cihazın kuralıyla kabul eder | Üretilir | DONE | PA | P2 | 425 | B40 | app/appfactory/composer.py:SERVER_JS; validation.py:validate_manifest | test_appfactory_b40.py (bütünleşme testleri geçici portta; yalnız entry) | — | no | — |
| 429 | Frontend generation | `public/index.html` + `app.css` + `app.js`: tür başına bölüm, şemadan formlar, listeler, sil; giriş formu (ilk kullanımda parola belirleme); Türkçe | Üretilir | DONE | PA | P2 | 425 | B40 | app/appfactory/composer.py:INDEX_HTML/APP_JS | test_appfactory_b40.py; lint | cihaz laboratuvarı READY_FOR_OWNER | no | — |
| 430 | Auth generation | `auth.js`: scrypt (rastgele tuz), timingSafeEqual, oturum çerezi HttpOnly+SameSite=Strict, /api/setup /login /logout /me; her kayıt yolu oturum ister (401) | Üretilir | DONE | PA | P2 | 425 | B40 | app/appfactory/composer.py:AUTH_JS | test_appfactory_b40.py (401 → setup → yanlış parola 401 → giriş → CRUD) | — | no | Parola tel alanı 'parola' (gizli-anahtar taraması) |
| 431 | Test generation | `tests/unit.js` (şema), `tests/integration.js` (geçici portta gerçek sunucu), `tests/run.js` (ok / not ok / N/M passed - cihazın ayrıştırdığı biçim), `tests/custom.js` (model), `tests/browser-oracle.json` | Üretilir | DONE | PA | P2 | 425 | B40 | app/appfactory/composer.py | test_appfactory_b40.py | — | no | — |
| 432 | Unit tests | Üretilen birim testleri gerçek node altında koşar: 3 örnek uygulamada 14–28 test geçti (test_appfactory_b40) | Koşar | DONE | PA | P2 | 431 | B40 | app/appfactory/composer.py:UNIT_TESTS_JS | test_appfactory_b40.py (node tests/run.js) | cihazda `project.test` | no | — |
| 433 | Integration tests | Üretilen bütünleşme testleri sunucuyu 0 portunda açar, HTTP ile CRUD + auth akışını kanıtlar; node altında geçer | Koşar | DONE | PA | P2 | 431 | B40 | app/appfactory/composer.py:INTEGRATION_TESTS_JS | test_appfactory_b40.py | — | no | — |
| 434 | Browser tests | Her bileşik uygulama kendi DOM oracle'ını taşır (`tests/browser-oracle.json`; satırda `oracle_json`); `exercise` şablon oracle'ı yerine onu okur; cihaz laboratuvarı adımları oynatır | Koşar | DONE | PA | P2 | 431 | B40 | app/appfactory/composer.py:_oracle; service.py:exercise | test_appfactory_b40.py (oracle üretimi) | laboratuvar READY_FOR_OWNER (checkpoint 17) | no | Cloud gözlemleyemediği DOM iddiasını sahiplenmez |
| 435 | Failed test analysis | `fixloop.analyze_failures`: cihaz raporunun 'not ok - ad :: mesaj' satırları → adlı başarısızlıklar, dosya ipucu, çökme; özet cümlesi | Analiz eder | DONE | PA | P2 | 432 | B40 | app/appfactory/fixloop.py | test_appfactory_b40.py | — | no | SelfDev motorunun şekli |
| 436 | Automated bug fix | `composed_service.run_fix_loop`: teşhis → düzeltme (yalnız model yuvaları) → lint + diff güvenlik taraması + doğrulama → cihazda YENİ sürüm (`<slug>-vN`, parent_id) → `project.test`; `app.fix` sesli araç + POST /v1/apps/{id}/fix; defterde app.project.fix | Düzeltir | DONE | PA | P2 | 435 | B40 | app/appfactory/composed_service.py; service.py:fix; tools_apps.py:app_fix | test_appfactory_b40.py (v2 yeşil; model yok; aynı hata) | — | model bütçesi | Cihaz projeyi yerinde yeniden yazmaz |
| 437 | Retry loop | En çok 3 deneme; aynı testler ikinci kez → dur; model yok → analizle dur ve söyle; her deneme kayıtta | Döngü | DONE | PA | P2 | 436 | B40 | app/appfactory/fixloop.py:MAX_FIX_ATTEMPTS; composed_service.py | test_appfactory_b40.py | — | no | — |
| 438 | Lint | `lint.py` (saf Python, yapısal): parantez dengesi (dizgi/yorum/regex atlanır), eval / new Function / document.write / with / child_process / vm, sekme, satır uzunluğu, html lang+charset+script eşleşmesi, JSON; hata scaffold'u durdurur | Koşar | DONE | PA | P2 | 425 | B40 | app/appfactory/lint.py | test_appfactory_b40.py (hatalı dosyalar; regex içindeki parantez) | — | no | Yapısal; sözdizimi cihazın node'unda |
| 439 | Security scan | `appsecurity.scan_files` = kendini geliştirme kuyruğunun güvenlik incelemesi (gizli anahtar, tehlikeli çağrı, loopback dışı ağ çıkışı) üretilen her dosya üzerinde; düzeltmelerde önceki dosyalara göre fark; bulgu scaffold'u durdurur ve makbuza yazılır | Koşar | DONE | PA | P2 | 438 | B40 | app/appfactory/appsecurity.py; app/selfdev/security_review.py | test_appfactory_b40.py | — | no | 680 ile ortak |
| 440 | Build | Bileşik uygulama için 'derleme' = üretim + lint + güvenlik taraması + doğrulama + cihazda `project.scaffold` + `project.test` (B40); yapı kimliği dosya içeriklerinden (`lifecycle.build_id_for`, aynı dosyalar = aynı kimlik) | Çalışır | DONE | PA | P2 | 417 | B41 | app/appfactory/lifecycle.py:build_id_for; composed_service.py | test_appfactory_b41.py (yapı kimliği içerikle değişir) | — | no | Yerel derleme B33'te (native) |
| 441 | Package | `app.package` / POST /v1/apps/{id}/package: yalnız test edilmiş sürüm; tam olarak scaffold edilen dosyaların zip'i + `release.json` (ad, sürüm, yapı kimliği, dosya başına sha256, manifest, spec) nesne deposunda `apps/releases/<slug>/v<n>/…zip` | Çalışır | DONE | PA | P2 | 440 | B41 | app/appfactory/lifecycle.py:package_release; lifecycle_service.py:package | test_appfactory_b41.py (zip içeriği = scaffold dosyaları; test edilmemiş → ret) | — | no | Depo: artifacts ile aynı S3 (InMemory testte) |
| 442 | Launch | `app.launch` / POST /launch: sürüm paketi depodan okunur, her dosya kendi kaydındaki sha256 ile doğrulanır (uyuşmazsa `release_corrupt`, çalıştırılmaz), `<slug>-release-v<n>` olarak kendi satırıyla scaffold edilir ve `project.run` ile başlatılır | Çalıştırılır | DONE | PA | P2 | 441 | B41 | app/appfactory/lifecycle.py:files_from_release; lifecycle_service.py:launch | test_appfactory_b41.py (paketten başlatma; bozuk paket reddi) | cihaz laboratuvarı READY_FOR_OWNER | no | Paketlenen çalışır, çalışma klasörü değil |
| 443 | UI verification | `app.verify` / POST /verify: uygulamanın kendi tarayıcı oracle'ı cihazın tarayıcı ailesinde oynatılır (isolated profil; `browser.find/fill/click/wait/extract`), her adım kaydedilir, ilk takılan adım adıyla; `assert_class` laboratuvara bırakılır (dürüstçe 'burada denetlenmedi') | Doğrulanır | DONE | PA | P2 | 442,463 | B41 | app/appfactory/lifecycle.py:verify_ui; lifecycle_service.py:verify | test_appfactory_b41.py (oracle oynatma; eksik öğe adıyla) | cihaz tarayıcı işçisi READY_FOR_OWNER | no | UIA yerine DOM: web uygulaması için üst basamak |
| 444 | Persistence verification | Doğrulama sonrası GERÇEK yeniden başlatma (`project.stop` → `project.run`), giriş adımları tekrar, eklenen kaydın son iddiası yeniden okunur; kayıt kaybolursa `persistence=false` ve adım adıyla ret | Doğrulanır | DONE | PA | P2 | 442 | B41 | app/appfactory/lifecycle.py:verify_ui(restart=) | test_appfactory_b41.py (unutkan depo yakalanır) | — | no | — |
| 445 | Log read-back | `app.log` / GET /log: `project.status` `log_tail`/`log_path`/durum; satır sayısı ve son satır söylenir; satıra yazılır | Okunur | DONE | PA | P2 | 442 | B41 | app/appfactory/lifecycle.py:read_log; lifecycle_service.py:log | test_appfactory_b41.py | — | no | — |
| 446 | Release artifact | Zip + `release.json` (yapı kimliği, hash'ler) nesne deposunda; satırda `lifecycle_json.release`; defterde app.project.package | Üretilir | DONE | PA | P2 | 441 | B41 | app/appfactory/lifecycle.py:ReleaseArtifact | test_appfactory_b41.py | — | no | İmzasız (ADR-0140 sözlü politika) |
| 447 | Project history | `app.history` / GET /history: aynı adlı tüm sürümler (sürüm, ebeveyn, durum, testler, paket, nereden başlatıldı) + defterdeki app.project.* olayları sırayla | Tam | DONE | PA | P2 | — | B41 | app/appfactory/lifecycle_service.py:history/lineage | test_appfactory_b41.py | — | no | — |
| 448 | Resume development later | `app.resume` / 'Kitaplık uygulamasına devam edelim': adla (ilike) ya da odakla en SON sürüm bulunur, odağa alınır, durum/sürüm/test/port/paket özetlenir | Devam edilir | DONE | PA | P2 | 447 | B41 | app/appfactory/lifecycle_service.py:resume | test_appfactory_b41.py (adla dönüş, son sürüm) | — | no | — |
| 449 | Modify existing generated app | `app.modify` / POST /modify: sonraki istek aynı ayrıştırıcıdan (ekle-fiili tut-fiiline çevrilir), gereksinimlere birleştirilir (yeni tür, yeni alan, giriş), bileşik yeniden kurulur, YENİ sürüm scaffold + test; okunamayan istek modelle (yuvalar) ya da dürüst ret | Değiştirilir | DONE | PA | P2 | 448 | B41 | app/appfactory/lifecycle.py:merge_requirements/parse_addition; lifecycle_service.py:modify | test_appfactory_b41.py (alan ekleme v2 + test; tür + giriş; ret; model) | — | no | Şablon uygulamaları sabit |
| 450 | "Bu uygulamaya şu özelliği ekle" | APP_FACTORY_MODIFY niyeti (uygulama odaktayken 'uygulamaya … ekle', öz-referans hariç) → `app.modify` (sahibin cümlesi `app_request`) | Çalışır | DONE | PA | P2 | 449 | B41 | app/voice/intents.py:_appfactory_lifecycle_match; tools_apps.py:app_modify | test_appfactory_b41.py (yönlendirme; ses) | — | no | — |
| 451 | "Bu bug'ı düzelt" | Uygulama odaktayken (ve öz-referans yokken) APP_FACTORY_FIX → `app.fix`; odak yokken 'Bu bug'ı düzelt' bellek düzeltmesi, 'kendin düzelt' kendini geliştirme (622) olarak kalır | Çalışır | DONE | PA | P2 | 449,436 | B41 | app/voice/intents.py:_appfactory_lifecycle_match | test_appfactory_b41.py (odaklı/odaksız) | — | no | 622 ile karışmaz: öz-referans ayırır |
| 452 | Arbitrary bounded app request | Serbest istek → bileşik uygulama (B40) + yaşam döngüsü (B41); sınırlar: cümle ≤600, ≤8 kayıt türü, ≤12 alan, ≤200 dosya/2 MiB, ≤3 düzeltme denemesi, model yalnız iki yuva | Sınırlı serbest istek | DONE | PA | P2 | 425 | B41 | app/appfactory/requirements.py; composer.py; composed_service.py | test_appfactory_b40.py; test_appfactory_b41.py | üretim turu READY_FOR_OWNER | model bütçesi | App Factory'nin nihai hedefi: sınırlar adlı |

## O. NATIVE APP FACTORY (453–480)

> Ölçüm: üretimden tetiklenen gerçek cihaz derlemesi kanıtlı (notlarim.exe, 162.304 bayt,
> sha256 1a73dab4..., PeImageReader ile doğrulandı). Ama yaşam döngüsünün kalanı ölü.

| ID | FEATURE | CURRENT_STATUS | TARGET_STATUS | IMPL | PROOF | PRI | DEPS | BATCH | SOURCE_REFERENCES | TEST_REFERENCES | RUNTIME_PROOF | OWNER_ACTION | NOTES |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 453 | NativeAppSpec | Çalışıyor | Aynı | DONE | PA | P1 | — | — | app/nativefactory/ | nativefactory testleri | — | no | — |
| 454 | WPF generation | Çalışıyor | Aynı | DONE | PR | P1 | — | — | app/nativefactory/ | nativefactory testleri | 26.16 | no | Rewrite gerekmez |
| 455 | EXE | Çalışıyor | Aynı | DONE | PR | P1 | — | — | app/nativefactory/, devices/windows-agent | PeImageReader testleri | notlarim.exe 162.304 B | no | — |
| 456 | Portable ZIP | `project.package portable` cihazda `dist/<slug>-portable.zip` (sha256, bytes, `signed:false`, observed); `device_build` EXE okunup yargılandıktan SONRA paketi ister ve satırın artefaktı paket olur (EXE altında kalır); `GET /v1/native/{id}/artifact` Linux'ta göremediği dosyayı `project.artifact` ile 32 KiB parçalar halinde cihazdan çeker, hash doğrular (`X-Artifact-Sha256`, `X-Artifact-Source: device`) | Erişilebilir | DONE | PA | P1 | — | B33 | Projects/NativeLifecycle.cs:Package,Artifact; app/nativefactory/device_build.py:build_on_device; device_lifecycle.py:pull_artifact; routes.py:get_artifact | test_nativefactory_device_build.py (+2), test_nativefactory_lifecycle.py (22), test_nativefactory_routes.py (+2), C# NativeLifecycleTests (8) | cihaz laboratuvarı bu masaüstünde gerçek: zip + parça okuma + hash; üretimde indirme Karar 0 | no | 410 artık yalnız cihazda da yoksa |
| 457 | MSIX | `staging/AppxManifest.xml` Cloud Core'dan iskelelenir (cihaz üretmez), cihaz `makeappx pack /o /nv` ile paketler; makeappx yoksa `dependency_unavailable`; her cevap `signed:false`; `native.package` cihaz üstünden ve imza politikasını söyler | Erişilebilir | DONE | PA | P1 | 472 | B33 | NativeLifecycle.cs:Package(msix); packaging.py:appx_manifest_text; tools_native.py:_package_on_device | test_voice_native_tools.py (msix cihazda), test_nativefactory_device_build.py (manifest iskelesi), C# NativeLifecycleTests (msix: makeappx varsa) | lab: makeappx bu masaüstünde (NativeLabFact) | imza kararı (473) | imzalama hiçbir yerde yok: ForbiddenPrograms duruyor |
| 458 | PE validation | Bağımsız doğrulama var | Aynı | DONE | PA | P0 | — | — | devices/windows-agent | PeImageReader (15 test) | sha256 1a73dab4 | no | 28: CI'da koşmuyor |
| 459 | Build identity | Çalışıyor | Aynı | DONE | PR | P1 | — | — | devices/windows-agent | — | build 19f079c4fda2c3c7 | no | — |
| 460 | Device build | Çalışıyor | Aynı | DONE | PR | P1 | — | — | devices/windows-agent | — | 26.16 | no | — |
| 461 | Cloud-triggered device build | Çalışıyor | Aynı | DONE | PR | P1 | — | — | app/nativefactory/device_build.py | NativeManifestContractTests.cs | 26.16 | no | Sözleşme fikstürü burada doğdu |
| 462 | App launch | `native.launch` → cihaz `app.launch` (ADR-0098 yerel kök altındaki exe) + bir `ui.inspect`; pid var pencere yoksa `window.list`; Android'de 33 reddi korunur; cihaz yoksa eski dürüst ret | Çalışır | DONE | PA | P1 | — | B33 | device_lifecycle.py:launch_on_device,inspect_window; tools_native.py:native_launch; intents.py:_native_launch_match | test_nativefactory_lifecycle.py (launch 4), test_voice_native_tools.py, test_voice_intents_native_lifecycle.py (25), korpus nativeapps.win.launch.* | gerçek pencerede Karar 0 (26.16) | no | 'masaüstü/Windows uygulamasını aç', 'EXE'yi çalıştır', 'programı başlat' yalnız yerel derleme odaktayken; çıplak 'uygulamayı aç' HER ZAMAN M23'ün (mutasyon M5) |
| 463 | UI Automation | `native.verify`: `NoteInput`'a `ui.set_value`, `AddButton` `ui.invoke`, `StatusText` '<n> not' okunur — şablonun kendi automation id'leri (MainWindow.xaml) | Doğrular | DONE | PA | P1 | 462,99 | B33 | device_lifecycle.py:verify_on_device,inspect_window; tools_native.py:native_verify | test_nativefactory_lifecycle.py (verify 5 vaka), test_voice_native_tools.py, korpus nativeapps.verify.* | gerçek pencerede Karar 0 (26.16) | no | kontrol bulunamazsa verified=false, failed_step 'ui.inspect' |
| 464 | Relaunch | doğrulama akışında `window.close` → ikinci `app.launch` → ikinci okuma → `window.close` | Çalışır | DONE | PA | P1 | 462 | B33 | device_lifecycle.py:verify_on_device | test_nativefactory_lifecycle.py (adım dizisi 10 çağrı) | Karar 0 | no | — |
| 465 | Persistence | yeniden açılışta StatusText sayısı ≥1 değilse `verified=false`, `failed_step='persistence'` | Doğrulanır | DONE | PA | P1 | 462 | B33 | device_lifecycle.py:verify_on_device | test_nativefactory_lifecycle.py (persistence), mutasyon M1 | Karar 0 | no | — |
| 466 | App log | `native.log` ve doğrulama: `file.read` `<exe dizini>\data\app.log`; 'started' satırı aranır (`log_has_startup_line`); kuyruk satıra (`log_tail`) yazılır; yoksa not_found adıyla | Okunur | DONE | PA | P1 | 462 | B33 | device_lifecycle.py:read_log_on_device; tools_native.py:native_log | test_nativefactory_lifecycle.py, test_voice_native_tools.py, korpus nativeapps.log.* | Karar 0 | no | — |
| 467 | Test count parsing | `counts_parsed` okunuyor; sayılamayan koşu `tests_unreadable` | Dürüst sayı | DONE | PA | P0 | 5 | B03 | app/nativefactory/device_build.py | test_nativefactory_device_build.py (20, 3 yeni regresyon) | — | no | `exit_code: None` da artık geçer not değil |
| 468 | Native install | `native.install` → cihaz `project.install`: Başlat menüsü kısayolu (`IShellLinkW`+`IPersistFile`, STA; `PAGENTOS_NATIVE_PROGRAMS_DIR` yalnız lab) + yerel kök altında `installed.json`; `observed.shortcut_exists` okunmadan 'kuruldu' denmez; imzasız MSIX satırı 472 ile reddedilir | Çalışır | DONE | PA | P1 | — | B33 | NativeLifecycle.cs:Install; ShellLinkInterop.cs; device_lifecycle.py:install_on_device; tools_native.py:native_install | C# NativeLifecycleTests (gerçek .lnk), test_nativefactory_lifecycle.py, test_voice_native_tools.py (install→uninstall→not_found), mutasyon M2 | lab: gerçek kısayol bu masaüstünde yazıldı ve silindi | no | WScript.Shell 'Notlarım.lnk'i kaydedemedi → shell'in Unicode arayüzü |
| 469 | Native uninstall | `native.uninstall` → `project.uninstall`: kısayol + kayıt gider, derleme klasörü kalır (`build_kept:true`); bu sistemin kurmadığı → `not_found` adıyla; CRITICAL kademe, ACTING_INTENTS | Çalışır | DONE | PA | P1 | 468 | B33 | NativeLifecycle.cs:Uninstall; tools_native.py:native_uninstall; step_up.py | C# NativeLifecycleTests, test_voice_native_tools.py, korpus nativeapps.uninstall.*, mutasyon M7 (C#) | lab gerçek | no | — |
| 470 | Native fix | `native.fix`: kaynak spec'ten yeniden üretilir ve cihazda yeniden derlenir; `verified` → 'Düzelttim', aksi → derleyicinin sözüyle 'Düzeltemedim' (`fixed:false`); cihaz yoksa eski dürüst ret (kodlayıcı ucu yok) | Çalışır | DONE | PA | P1 | 462 | B33 | tools_native.py:native_fix | test_voice_native_tools.py (3), korpus nativeapps.fix.rebuilt_on_device, mutasyon M6 | — | no | model destekli kod düzeltme B40'ın işi |
| 471 | Native update | `native.update`: aynı tarif sonraki sürümle YENİ satır (eski yargı dokunulmaz), cihazda derlenir, kurulur (kısayol yeni sürüme) | Çalışır | DONE | PA | P1 | 468 | B33 | tools_native.py:native_update | korpus nativeapps.update.* (native_version 0.1.1) | Karar 0 | no | — |
| 472 | Signing policy | `native_signing_mode` (varsayılan `unsigned`; `test_certificate`/`owner_certificate` ADLA reddedilir, uygulanmış gibi gösterilmez); her paket cevabı imza durumunu ve nedenini söyler; imzasız MSIX'in kurulumu 'sertifika kararı sizin' ile reddedilir | Politika | DONE | PA | P1 | — | B33 | app/nativefactory/signing.py; config.py:native_signing_mode | test_nativefactory_lifecycle.py (signing 2), test_voice_native_tools.py | — | sertifika kararı | sertifika bir credential: DPAPI/secret-store dışında hiçbir yerde durmaz |
| 473 | Signing optional/test cert | politika `test_certificate` modunu TANIR ve karar verilene kadar imzasız üretir (checkpoint); cihazda `signtool` ForbiddenPrograms'ta kalır | Test sertifikası | PARTIAL | PA | P1 | 472 | B33 | app/nativefactory/signing.py | test_nativefactory_lifecycle.py | — | sertifika kararı (READY_FOR_OWNER) | karar: kendinden imzalı test sertifikası mı, sahibin sertifikası mı, imzasız mı |
| 474 | Android project generation | Yok | Üretilir | MISSING | NYP | P3 | 425 | B49 | app/nativefactory/ | — | — | SDK kurulumu | v1.0'ı bloklamaz |
| 475 | APK | Yok | Üretilir | MISSING | NYP | P3 | 474 | B49 | app/nativefactory/ | — | — | SDK kurulumu | — |
| 476 | AAB | Yok | Üretilir | MISSING | NYP | P3 | 475 | B49 | app/nativefactory/ | — | — | SDK kurulumu | — |
| 477 | Android emulator test | Yok | Koşar | MISSING | NYP | P3 | 475 | B49 | app/nativefactory/ | — | — | SDK kurulumu | — |
| 478 | Android device test | Yok | Koşar | MISSING | NYP | P3 | 477 | B49 | app/nativefactory/ | — | — | fiziksel cihaz | — |
| 479 | iOS explicit NOT_SUPPORTED without macOS | Açık beyan yok | Açıkça reddedilir | MISSING | NYP | P2 | — | B49 | app/nativefactory/ | — | — | no | Ucuz ve dürüst; Unity lisans reddi örnek |
| 480 | Generated app auto-update later | Yok | Sonra | DEFERRED | NYP | P3 | 449 | B41 | app/appfactory/ | — | — | no | v1.0'ı bloklamaz; B41 sürüm soyunu (parent_id, launched_from) hazır bırakır |

## P. CREATIVE / IMAGE (481–512)

> Ölçüm: Pillow hattı gerçek ve piksel düzeyinde bağımsız doğrulanıyor. Görsel üretimi ve
> OCR **hiç yok**. Çıktı sahibin diskine ulaşmıyor. `creative.design` varsayılanı Figma ve
> Figma jetonu sabit `False` — yani bu araç **yapısal olarak her zaman başarısız**.

| ID | FEATURE | CURRENT_STATUS | TARGET_STATUS | IMPL | PROOF | PRI | DEPS | BATCH | SOURCE_REFERENCES | TEST_REFERENCES | RUNTIME_PROOF | OWNER_ACTION | NOTES |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 481 | Pillow raster edits | Çalışıyor | Aynı | DONE | PA | P2 | — | — | app/creative/ | piksel doğrulama testleri | — | no | Rewrite gerekmez |
| 482 | Crop | Çalışıyor | Aynı | DONE | PA | P2 | — | — | app/creative/ | creative testleri | — | no | — |
| 483 | Resize | Çalışıyor | Aynı | DONE | PA | P2 | — | — | app/creative/ | creative testleri | — | no | — |
| 484 | Rotate | Çalışıyor | Aynı | DONE | PA | P2 | — | — | app/creative/ | creative testleri | — | no | — |
| 485 | Draw | Çalışıyor | Aynı | DONE | PA | P2 | — | — | app/creative/ | creative testleri | — | no | — |
| 486 | Text | Çalışıyor | Aynı | DONE | PA | P2 | — | — | app/creative/ | creative testleri | — | no | — |
| 487 | Shapes | Çalışıyor | Aynı | DONE | PA | P2 | — | — | app/creative/ | creative testleri | — | no | — |
| 488 | Composite | Çalışıyor | Aynı | DONE | PA | P2 | — | — | app/creative/ | creative testleri | — | no | — |
| 489 | Background removal | Yerel: eşik/taşma (M27) + sağlayıcı arayüzü (`ImageProvider`); yaratıcı planda `background_remove`, `object_remove` ile aynı sınır | Çalışır | DONE | PA | P2 | 492 | B43 | app/creative/imaging.py; execute.py | test_creative_execute.py; test_creative_b43.py | — | sağlayıcı kararı READY_FOR_OWNER | Yerel yol sağlayıcısız çalışır |
| 490 | Object removal | `object_remove` (kutu): yerel sağlayıcı kutuyu kendi kenarından doldurur (medyan + bulanıklık, asla uydurma); istem verilirse sağlayıcının `edit`'i (maske = kutu) | Çalışır | DONE | PA | P2 | 492 | B43 | app/creative/imaging.py:LocalImageProvider.object_remove; execute.py | test_creative_b43.py (mavi blok gider, çevre sürer; istemli yol scripted) | — | sağlayıcı kararı | — |
| 491 | Object addition | `object_add` (kutu): şekil / metin / depodaki görsel (`asset` nesne anahtarı) yerel; `kind=prompt` sağlayıcıyla | Çalışır | DONE | PA | P2 | 492 | B43 | app/creative/imaging.py:object_add; execute.py | test_creative_b43.py (elips, görsel; asset yoksa ret) | — | sağlayıcı kararı | — |
| 492 | Image generation | `ImageProvider` arayüzü: `local` (üretimi ADIYLA reddeder: `provider_not_configured`) / `openai` (gpt-image-1, `voice_openai_api_key`, `creative_image_provider=openai`; yanıt Pillow ile yeniden açılmadan güvenilmez); `creative.generate` + POST /v1/creative/generate; anahtarsız openai yerel'e düşer | Çalışır | DONE | PA | P2 | — | B43 | app/creative/imaging.py:OpenAIImageProvider/build_image_provider; lifecycle.py:generate; config.py | test_creative_b43.py (MockTransport ile API; ayar seçimi; sağlayıcısız ret; ses: sahibin cümlesi istemdir) | sağlayıcı hesabı READY_FOR_OWNER (checkpoint 19) | sağlayıcı hesabı | 489-495'in ön koşulu; anahtar DPAPI/secret-store ile |
| 493 | Image style transformation | `style`: grayscale / sepia / posterize / edges / invert yerel; `prompt` verilirse sağlayıcı | Çalışır | DONE | PA | P2 | 492 | B43 | app/creative/imaging.py:LocalImageProvider.style | test_creative_b43.py (gri kanal eşitliği; ters renk pikseli) | — | sağlayıcı kararı | — |
| 494 | Image enhancement | `enhance`: auto (autocontrast + unsharp mask) / sharpen / denoise / autocontrast — yerel, belirlenimli | Çalışır | DONE | PA | P2 | 492 | B43 | app/creative/imaging.py:LocalImageProvider.enhance; lifecycle.py:enhance | test_creative_b43.py | — | no | 512'nin motoru |
| 495 | Upscale | `upscale`: Lanczos x2/x4, kenar ≤8192 (aşarsa adıyla ret) | Çalışır | DONE | PA | P2 | 492 | B43 | app/creative/imaging.py:LocalImageProvider.upscale | test_creative_b43.py (64x48 → 128x96; x3 ret) | — | no | — |
| 496 | OCR | 140 ile AYNI iş: Windows.Media.Ocr (yerel) `document.extract` üzerinden; creative (M27) aynı yeteneği çağırabilir | Çalışır | DONE | PA | P1 | — | B32 | Documents/OcrHost.cs | test_documents_b32.py (15), korpus doc.preview/doc.text/doc.dupes/doc.dedup/doc.image/doc.archive/doc.cmp.two (177 belge vakası), C# ImageArchiveTests (5) + ExtractionOracleTests (oracle metin.png) | cihaz laboratuvarı bu masaüstünde yeşil (Windows.Media.Ocr tr: 'Merhaba Dünya 1234' birebir; arşiv dizini; Recycle Bin); üretimde bir görselden metin Karar 0 | sağlayıcı kararı (140 ile aynı) | — |
| 497 | Independent pixel validation | Çalışıyor | Aynı | DONE | PA | P2 | — | — | app/creative/ | piksel testleri | — | no | Ürün ilkesi uygulanmış |
| 498 | Visual semantic validation | `semantic_check` (beklenti): çalışma bitince B29 görsel sağlayıcısına tek kapalı soru; `semantic_json` (checked/ok/answer/provider); 'hayır' → run `unverified`; sağlayıcı yoksa 'çalışmadı' olarak kaydedilir, asla geçti | Çalışır | DONE | PA | P2 | 105 | B43 | app/creative/lifecycle.py:semantic_check; service.py | test_creative_b43.py (evet; hayır → unverified; sağlayıcısız checked=false) | gerçek görsel sağlayıcı (105) READY_FOR_OWNER | sağlayıcı kararı | — |
| 499 | Paint open/display | Algılama + açma var | Aynı | DONE | PA | P2 | — | — | app/creative/, devices/windows-agent | creative testleri | — | no | — |
| 500 | Paint real UI operation | `PaintDriver`: teslim edilen dosya `app.launch mspaint <yol>` (ArgumentPolicy: yetkili kökte tek yol) ile açılır, pencere başlıkla doğrulanır, Paint'in kendi kısayolları (Ctrl+W boyut, Ctrl+I ters renk, Ctrl+A/Del temizle, Ctrl+Z/Y, Ctrl+S) `keyboard.shortcut`/`keyboard.type`/`keyboard.key` ile basılır, `screen.capture` ile kapanır; `creative.drive` / POST /drive; takılan adım adıyla | Gerçekten sürülür | DONE | PA | P2 | 95,100 | B43 | app/creative/drivers.py:PaintDriver; lifecycle.py:drive | test_creative_b43.py (kısayol dizisi; takılan adım; teslimsiz ret) | cihaz laboratuvarı READY_FOR_OWNER | no | Koordinat yok, menü tahmini yok |
| 501 | Photoshop detection | Çalışıyor | Aynı | DONE | PA | P2 | — | — | app/creative/ | creative testleri | — | no | — |
| 502 | Photoshop driver | `AdobeDriver(photoshop)`: aynı `app.launch` + ortak kısayollar (Ctrl+Alt+Z geri, Ctrl+Shift+Z yinele, Ctrl+S, Ctrl+A) + yakalama; kurulu değilse `dependency_unavailable` adıyla; boyut/ters renk iddia edilmez | Sürücü | DONE | PA | P3 | 500 | B43 | app/creative/drivers.py:AdobeDriver | test_creative_b43.py (adım şekli; desteklenmeyen ret) | lisans + cihaz allowlist READY_FOR_OWNER | lisans | v1.0'ı bloklamaz |
| 503 | Illustrator detection | Çalışıyor | Aynı | DONE | PA | P2 | — | — | app/creative/ | creative testleri | — | no | — |
| 504 | Illustrator driver | `AdobeDriver(illustrator)`: Ctrl+Z / Ctrl+Shift+Z / Ctrl+S / Ctrl+A + yakalama; kurulu değilse ret | Sürücü | DONE | PA | P3 | 500 | B43 | app/creative/drivers.py:AdobeDriver | test_creative_b43.py | lisans READY_FOR_OWNER | lisans | v1.0'ı bloklamaz |
| 505 | Figma integration | `creative.design` varsayılanı Paint | Varsayılan çalışan yola | DONE | PA | P0 | — | B03 | app/voice/realtime_sessions/tools_creative.py | test_creative_tools.py::..._reaches_a_tool_that_can_work | — | Figma jetonu | Figma jetonu hâlâ bağlı değil; adıyla istenirse dürüst ret veriyor (kasıtlı) |
| 506 | Layer-aware editing | `layered` aracı (`LayeredProvider`, her zaman kurulu — bu süreç): `LayeredDocument` (ad, RGBA, ofset, görünürlük, opaklık), `layer add` tuvali katman yapar / `merge` aşağı birleştirir, `flatten` sırayla; Paint düz bitmap kalır | Çalışır | DONE | PA | P2 | 507 | B43 | app/creative/layers.py; execute.py | test_creative_b43.py (opaklık 0.5 karışımı; merge; Paint'te layer reddi) | — | no | — |
| 507 | PSD support | OKUMA: Pillow'un PSD eklentisi (bileşik + katman bbox'ları); YAZMA: Pillow PSD yazamaz — katmanlı belge OpenRaster (.ora: mimetype, stack.xml, katman PNG'leri, mergedimage) olarak yazılır ve geri okunur; sınır belgede söylenir | Çalışır | DONE | PA | P2 | — | B43 | app/creative/layers.py:from_psd/to_ora/from_ora | test_creative_b43.py (elle yazılmış gerçek PSD başlığı; ORA tur; PSD kaynağı katmanlı araçta) | — | no | PSD yazımı dürüstçe yok |
| 508 | SVG support | YAZMA: çizilen şekil/metinler gerçek vektör öğeleri (`rect/ellipse/line/polygon/text`), raster katmanlar `image`; OKUMA: aynı kapalı öğe kümesi plan işlemlerine (`shape/draw/add_text`), desteklenmeyen öğe adıyla sayılır; SVG kaynağı executor'da yeniden çizilir | Çalışır | DONE | PA | P2 | — | B43 | app/creative/layers.py:to_svg/from_svg; execute.py | test_creative_b43.py (yazılan SVG'de base64 yok; geri okuma; kaynak olarak açma) | — | no | Bitmap 'svg' sarmalayıcı (M27) şekil yoksa kalır |
| 509 | Export to owner disk | `creative.deliver` / POST /deliver: çıktı B42 görsel ARTEFAKTI olur (kendi kopyası, provenans creative run'ı adlar), artefakt açma yolu (`file.fetch`: hash'li, İndirilenler, `open`, istenirse `application=mspaint`) ile diske iner; `delivery_json` (yol, sha256); aynı bayt aynı artefakt, yeni çıktı yeni artefakt | Diske teslim | DONE | PA | P2 | 413 | B43 | app/creative/lifecycle.py:deliver; artifacts/open_service.py (application, image fallback) | test_creative_b43.py (file.fetch payload; artefakt+manifest; kopya; yeniden teslim) | cihaz laboratuvarı READY_FOR_OWNER | no | Artefakt teslim yolu örnek alındı, ikinci yol yok |
| 510 | Creative history | Panel veri gösteriyor | Çalışır | DONE | PA | P0 | 715 | B03 | app/creative/routes.py | test_web_asks_for_routes_that_exist.py (12) | — | no | 715 ile aynı düzeltme |
| 511 | Undo/redo | `history_json` (her çıktı kendi anahtarında `creative/<run>/v<n>/…`) + `history_index`; `creative.undo/redo` / POST /undo,/redo; sınırda adıyla ret; geri alınmış dal yeni düzenlemede düşer (doğrusal yığın) | Çalışır | DONE | PA | P2 | 160 | B43 | app/creative/lifecycle.py:record_output/undo/redo | test_creative_b43.py (işaretçi; iki uç; dal düşer; rota) | — | no | Odaktaki çalışma varsa 'Geri al' buradadır |
| 512 | "Bu fotoğrafı düzelt" | CREATIVE_ENHANCE (fotoğraf/resim/görsel + düzelt/iyileştir/netleştir; 'renk' varsa CREATIVE_ADJUST) → `creative.enhance` (auto) odaktaki çalışmada; odak yoksa dürüst soru | Çalışır | DONE | PA | P2 | 494 | B43 | app/voice/intents.py:_creative_enhance_match; tools_creative.py:creative_enhance | test_creative_b43.py (yönlendirme; korpus fixture'ı sesle iyileştirilir) | — | no | — |

## Q. 3D / BLENDER / UNITY (513–534)

> Ölçüm: Blender **gerçekten sürülüyor** — başsız, sha256-sabitlenmiş sürücü betiğiyle,
> kendi Python API'si üzerinden; render iki kez doğrulanıyor. Üretimde **sıfır sahne**.

| ID | FEATURE | CURRENT_STATUS | TARGET_STATUS | IMPL | PROOF | PRI | DEPS | BATCH | SOURCE_REFERENCES | TEST_REFERENCES | RUNTIME_PROOF | OWNER_ACTION | NOTES |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 513 | Blender detection | Çalışıyor | Aynı | DONE | PR | P2 | — | — | devices/windows-agent | 3d testleri | laboratuvar koşusu | no | Rewrite gerekmez |
| 514 | Blender structured scene | Çalışıyor | Aynı | DONE | PA | P2 | — | — | app/creative3d/ | 3d testleri | — | no | — |
| 515 | Scene plan JSON | Çalışıyor | Aynı | DONE | PA | P2 | — | — | app/creative3d/ | 3d testleri | — | no | — |
| 516 | Blender Python driver | sha256-sabitlenmiş sürücü | Aynı | DONE | PR | P2 | — | — | devices/windows-agent | 3d testleri | laboratuvar | no | Tedarik zinciri disiplini iyi |
| 517 | Save .blend | Çalışıyor | Aynı | DONE | PR | P2 | — | — | devices/windows-agent | 3d testleri | laboratuvar | no | — |
| 518 | Render | Çalışıyor | Aynı | DONE | PR | P2 | — | — | devices/windows-agent | 3d testleri | laboratuvar | no | — |
| 519 | Independent render verify | Cihazda hash, bulutta bağımsız çözme + boş görüntü reddi | Aynı | DONE | PA | P2 | — | — | app/creative3d/ | 3d testleri | — | no | Ürün ilkesi uygulanmış |
| 520 | Scene inspect | Okuma: nesneler (dönüşüm, malzeme rengi + metallic/roughness, kamera lens), ışıklar (enerji + renk), kare aralığı, F-curve'lerden anahtar kareler, dışa aktarımlar; cihaz render'ı ve her dışa aktarımı yerinde doğrular (göreli yol, sha256, biçim imzası) | Tam | DONE | PA | P2 | — | B44 | app/creative3d/drivers/blender_driver.py:build_inspection/animation_tracks; SceneInspection.cs:ReadExports | test_creative3d_b44.py (sürücü okuması; sahte cihaz = cihaz anahtarları, C# kaynağından) · SceneExportTests.cs | — | no | Unity okuması sabit (pinli sürücü) |
| 521 | Production scene creation | POST /v1/scenes (sesli araçla aynı SceneService.create, aynı cihaz portu). Yolda bulunan iki sözleşme hatası kapatıldı: sürücü render'ı MUTLAK yolla bildiriyordu (cihaz reddeder) ve bulut render baytlarını cihazın hiç göndermediği `render_png_base64` anahtarından okuyordu (cihaz `render.png_base64`) — gerçek hiçbir render buluta ulaşamazdı | Üretimde sahne | DONE | PA | P2 | — | B44 | app/creative3d/routes.py:create_scene; service.py:_inspect_device; blender_driver.py:do_render | test_creative3d_b44.py (REST oluşturma; cihaz şekilli render buluta ulaşır) · SceneExportTests (gerçek Blender, gönderilen sürücü) | üretimde ilk sahne READY_FOR_OWNER | no | Sahte cihaz artık cihazın şeklinde cevaplar ve reddeder |
| 522 | Modify existing scene | POST /v1/scenes/{id}/apply: işlemler cihazın sakladığı scene.blend üzerinde uygulanır, geri okunur, karşılaştırılır (aynı `apply`) | Değişir | DONE | PA | P2 | 521 | B44 | app/creative3d/routes.py:apply_scene | test_creative3d_b44.py (oluştur → dışa aktar; bilinmeyen işlem 422; bilinmeyen sahne 404) | — | no | Sesli değişiklik M25'ten beri |
| 523 | Material control | set_material renk + metallic + roughness; okuma artık metallic/roughness'u da taşır ve karşılaştırma üçünü de denetler | Tam | DONE | PA | P2 | 521 | B44 | blender_driver.py:build_inspection; compare.py | test_creative3d_b44.py (metallic sapması adıyla) | — | no | — |
| 524 | Lighting control | set_light enerji + renk (Blender); ışık tipine göre tanınır (kameraya enerji verilmez); `scene.light` araç argümanı `color` | Tam | DONE | PA | P2 | 521 | B44 | blender_driver.py:apply_light; compare.py; tools_scene.py:scene_light | test_creative3d_b44.py (renk okuması; renk sapması; kameraya ışık reddi) | — | no | Unity'de renk adıyla reddedilir |
| 525 | Camera control | set_camera look_at + lens (mm, 1..500); okuma kamera nesnesinde `lens`; `scene.camera` araç argümanı `lens` (look_at olmadan da) | Tam | DONE | PA | P2 | 521 | B44 | blender_driver.py:apply_camera; compare.py; tools_scene.py:scene_camera | test_creative3d_b44.py (lens okuması; lens sapması) | — | no | Unity'de lens adıyla reddedilir |
| 526 | Animation | set_frames (başlangıç/bitiş/fps) + animate (nesne, kanal location/rotation/scale, ≤32 artan anahtar kare) Blender'ın `keyframe_insert`'ü ile; sahne ilk karede bırakılır; okuma F-curve'lerden (eski `fcurves` ya da 4.4+ katmanlı eylem); `scene.animate` sesli araç (düz argümanlar: name/channel/to/from/seconds); SCENE_ANIMATE niyeti SCENE_ADD'den önce | Çalışır | DONE | PA | P2 | 522 | B44 | spec.py:SetFrames/Animate; blender_driver.py:apply_animation/animation_tracks; compare.py; tools_scene.py:scene_animate; intents.py:_scene_animate_match | test_creative3d_b44.py (anahtar kare okuması; kare sapması; ses akışı) · SceneExportTests (gerçek Blender) | — | no | Yalnız Blender |
| 527 | Export FBX/GLTF | export (glb/fbx) Blender'ın paketli dışa aktarıcılarıyla scene.glb/scene.fbx olarak sahnenin yanına; cihaz dosyayı YERİNDE doğrular (göreli yol, ≤64 MiB, sha256, GLB başlığı/FBX imzası) ve yalnız kanıtı döner (bağlantı çerçevesi 1 MiB); bulut `exports_json`'u tutar, karşılaştırma bildirilen ↔ doğrulanan hash'i denetler; `scene.export` sesli araç (sahibin biçim sözcüğü kazanır); M25'in 'Sahneyi dışa aktar.' olumsuz vakası olumlu regresyona çevrildi | Çalışır | DONE | PA | P2 | 521 | B44 | blender_driver.py:do_export; SceneInspection.cs:ReadExports/SignatureOk; service.py:_device_exports; tools_scene.py:scene_export; intents.py:_scene_export_match | test_creative3d_b44.py (imza; doğrulanmamış/yalan hash; REST; ses) · SceneExportTests (sınırlar, imza, kaçış yolu, gerçek Blender GLB) | — | no | Dosya bayt olarak buluta taşınmaz |
| 528 | Unity detection | Çalışıyor | Aynı | DONE | PA | P2 | — | — | app/creative3d/ | 3d testleri | — | no | — |
| 529 | Unity licensing state | Dürüst ve sınıflandırılmış red | Aynı | DONE | PA | P2 | — | — | app/creative3d/ | 3d testleri | — | no | 479 için örnek desen |
| 530 | Unity project create | Lisans yok | Çalışır | BLOCKED_PROVIDER | PU | P3 | 529 | B50 | app/creative3d/ | — | — | Unity lisansı | v1.0'ı bloklamaz |
| 531 | Unity scene create | Lisans yok | Çalışır | BLOCKED_PROVIDER | PU | P3 | 530 | B50 | app/creative3d/ | — | — | Unity lisansı | — |
| 532 | Unity build | Lisans yok | Çalışır | BLOCKED_PROVIDER | PU | P3 | 530 | B50 | app/creative3d/ | — | — | Unity lisansı | — |
| 533 | Unity test | Lisans yok | Çalışır | BLOCKED_PROVIDER | PU | P3 | 532 | B50 | app/creative3d/ | — | — | Unity lisansı | — |
| 534 | Unreal later/optional | Yok | İsteğe bağlı | DEFERRED | NYP | P3 | — | B50 | — | — | — | lisans | v1.0 kapsamı dışı |

## R. EXECUTIVE AUTONOMY (535–560)

> Ölçüm: planlayıcı genel değil — üç şablon, Türkçe/İngilizce kök eşlemesiyle seçiliyor;
> eşleşmezse dürüstçe 422 ile reddediyor. LLM planlayıcı `NotImplementedError`. Üretimdeki
> 5 koşunun 4'ü "partial"; ikisi sahibe "4/4 adım tamam" diyor ama 4 adımın 3'ü başarısız.

| ID | FEATURE | CURRENT_STATUS | TARGET_STATUS | IMPL | PROOF | PRI | DEPS | BATCH | SOURCE_REFERENCES | TEST_REFERENCES | RUNTIME_PROOF | OWNER_ACTION | NOTES |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 535 | Durable task graph | Temporal üzerinde çalışıyor | Aynı | DONE | PR | P1 | — | — | app/executive/ | executive testleri | 5 üretim koşusu | no | Rewrite gerekmez |
| 536 | Step preconditions | `none / step_done / step_failed / step_verified / owner_approval` — aktivite `_precondition_satisfied` gerçek satırlara göre karar verir; doğrulayıcı step_failed/step_verified'ın ÖNCEKİ bir adımı adlamasını ister | Tam | DONE | PA | P2 | — | B38 | app/executive/spec.py; graph.py; activities.py:_precondition_satisfied | test_executive_b38.py (satırlarla üç yeni kontrol; DAG ihlali adıyla) | — | no | focus_exists/device_capability/artifact_valid/account_present hâlâ 'sağlandı' sayılır (B38 kapsamı dışı; hiçbir planlayıcı üretmez) |
| 537 | Step postconditions | `postcondition.min` döngü hedefi olarak da okunur; her tur yargılanır, SON turun kanıtı kayda geçer | Tam | DONE | PA | P2 | 536 | B38 | app/executive/activities.py:run_step_activity (_evidence_meets_minimum) | test_executive_b38.py (sınırlı döngü) | — | no | 111 ile aynı ilke |
| 538 | Retry | Adımın kendi retry politikası korunur; B38 döngüsü her turu bir deneme sayar (`attempt` artar, kısmi kanıt saklanır) | Tam | DONE | PA | P2 | — | B38 | app/executive/activities.py | test_executive_activities.py; test_executive_b38.py | — | no | — |
| 539 | Compensation | Bir dal hiçbir şey yapmadan "telafi edildi" diyor | Gerçek telafi | DONE | PA | P0 | — | B10 | app/executive/activities.py:_run_compensation | test_execution_honesty.py | üretim turu bekliyor (Karar 0) | no | Koşulsuz 'compensated' bitti: undone / nothing_to_undo / attempted_and_failed |
| 540 | Pause | Gerçek Temporal sinyali | Aynı | DONE | PA | P1 | — | — | app/executive/ | executive testleri | — | no | — |
| 541 | Resume | Gerçek Temporal sinyali | Aynı | DONE | PA | P1 | — | — | app/executive/ | executive testleri | — | no | — |
| 542 | Cancel | Gerçek Temporal sinyali | Aynı | DONE | PA | P1 | — | — | app/executive/ | executive testleri | — | no | Telafi yalnız iptalde çalışıyor |
| 543 | Modify | Gerçek Temporal sinyali | Aynı | DONE | PA | P1 | — | — | app/executive/ | executive testleri | — | no | — |
| 544 | Human approval step | `owner_approval` ön koşulu: iş akışı adımı park eder (`awaiting_step` satırda, durum notu 'onay bekleniyor'), `approve_step` sinyali + `approvals_json` (hangi adım, ne zaman), aktivite ikinci duvar; POST /v1/executive/runs/{id}/approve; kokpitte Onayla çipi yalnız bekleyen satırda | Tam | DONE | PA | P2 | 383 | B38 | app/executive/workflow.py:_needs_approval/approve_step; activities.py:_mark_awaiting_approval; service.py:approve_step_db; routes.py; apps/web/app/lib/cockpit/executive-rows.ts | test_executive_b38.py (gerçek Temporal test ortamında park + sinyal; rota); executive-approve.test.tsx | — | no | 624 korunur: onay adımı yalnız sahibin sinyaliyle geçer, diğer hazır adımlar beklemez |
| 545 | Task history | Çalışıyor | Aynı | DONE | PA | P1 | — | — | app/executive/ | executive testleri | — | no | — |
| 546 | Task explanation | Her adımın `rationale`'ı (en çok 300) plana yazılır; GET /v1/executive/runs/{id}/plan grafı gerekçeleriyle döner; explain 'şu an … çünkü …' der | Tam | DONE | PA | P2 | 558 | B38 | app/executive/spec.py:Step.rationale; service.py:get_plan/get_explain; routes.py | test_executive_b38.py (plan rotası gerekçeyi okur) | — | no | Kural şekilleri de her adımda gerekçe taşır |
| 547 | Research-report plan | Çalışıyor | Aynı | DONE | PR | P1 | — | — | app/executive/ | executive testleri | üretim | no | — |
| 548 | Folder-compare plan | file.search kusuruna bağımlı | Çalışır | DONE | PA | P0 | 3 | B10 | app/executive/planner.py | test_executive_planner.py | üretim turu bekliyor (Karar 0) | no | B03'ün file.search sözleşmesi düzeltti; klasör planı artık çalışan bir yeteneğe dayanıyor |
| 549 | Mail-sequence plan | Sağlayıcı yok; `mail.analyze_thread` ve `mail.draft` adım türleri artık model ve sahip grafından erişilebilir (552) | Çalışır | BLOCKED_PROVIDER | PU | P2 | 336 | B38 | app/executive/ | executive testleri | — | hesap bilgisi | B45 hesabıyla PROVEN_REAL |
| 550 | General model planner | `ModelExecutivePlanner` + `AnthropicPlannerModel` (zorunlu tool-use ile PROPOSAL_SCHEMA; sözlük = STEP_KIND_PROFILES) + `CompositeExecutivePlanner` (kural önce, model YALNIZ `executive_model_planner_enabled` ile; graf `planner` etiketi rule/model/owner) create_app'te kurulur | Model planlar | DONE | PA | P2 | — | B38 | app/executive/model_planner.py; main.py | test_executive_b38.py (scripted model → doğrulanmış graf; bayrak; ret → açıklama) | canlı: sahibin model bütçesiyle | model bütçesi (executive_model_planner_enabled + PAGENTOS_ANTHROPIC_API_KEY) | Model yalnız ÖNERİR; risk/telafi/kanıt/zaman aşımı profilden, doğrulayıcı aynı |
| 551 | Dynamic plan generation | Öneri → `graph_from_proposal` → TaskGraph + validate_graph; sözlük dışı tür, boş öneri, kural ihlali → PlanningClarificationNeeded (dürüst Türkçe cümle, 422) | Dinamik | DONE | PA | P2 | 550 | B38 | app/executive/model_planner.py:graph_from_proposal | test_executive_b38.py (dört ret) | — | no | — |
| 552 | Dynamic step selection | 15 adım türünün 15'i erişilebilir: model önerisiyle veya POST /v1/executive/runs {graph} ile sahibin kendi grafı (planner=owner, AYNI doğrulayıcı, 422 ile adlı ret) | Hepsi erişilebilir | DONE | PA | P2 | 551 | B38 | app/executive/service.py:start_run_db(graph=); routes.py | test_executive_b38.py (scene.create → scene.render → synthesis sahip grafı rotadan) | — | no | — |
| 553 | Parallel steps | İş akışı hazır adımları 3'lük partiler hâlinde birlikte çalıştırır; araştırma şekli Word raporu ve sunumu s2'nin kardeşleri yapar (ikisi de yalnız s2'ye bağlı) | Paralel | DONE | PA | P2 | 551 | B38 | app/executive/workflow.py; planner.py | test_executive_b38.py (kardeş adımlar) | — | no | Bağımlılık precondition'dan türer (step_done/step_failed/step_verified) |
| 554 | Conditional branches | `step_failed` yedek dalı (adlanan adım doğrulanınca dal 'gerekmedi' nedeniyle atlanır), `step_verified` katı bağımlılık (doğrulanmayan üst adım alt adımı adıyla atlatır) | Koşullu | DONE | PA | P2 | 551 | B38 | app/executive/activities.py:_precondition_satisfied; graph.py | test_executive_b38.py | — | no | — |
| 555 | Loop steps | `Step.repeat.max_rounds` (1–3): kanıt `postcondition.min`'i karşılayana dek adım yeniden çalışır, önceki kanıt ve tur sayısı girdide; doğrulayıcı min'siz döngüyü reddeder; satırda `repeat_json` (göç 0050) | Döngü | DONE | PA | P2 | 551 | B38 | app/executive/spec.py:Repeat; activities.py:run_step_activity | test_executive_b38.py (üç tur, son tur yargılanır) | — | no | — |
| 556 | Timeout policy | `DEFAULT_TIMEOUT_S_BY_KIND`: öneri zaman aşımı vermezse türün varsayılanı; kural şekilleri açık değerlerle; her adım 5–900 s sınırında | Tam | DONE | PA | P2 | — | B38 | app/executive/spec.py; model_planner.py | test_executive_b38.py | — | no | — |
| 557 | Recovery from partial failure | Başarısız adımdan sonra yedek dal (step_failed) koşar, katı bağımlılar adıyla atlanır, kalan hazır adımlar yürür; koşu sonucu dürüst (partial) | Tam | DONE | PA | P2 | 539 | B38 | app/executive/activities.py | test_executive_b38.py; test_execution_honesty.py | — | no | B10'daki '5 koşunun 4'ü partial' ölçümü kalır; B38 yedek dalı ve atlamayı ekler |
| 558 | Correct final status | Yanlış | Doğru | DONE | PA | P0 | — | B10 | app/executive/activities.py, app/executive/service.py, app/executive/models.py | test_execution_honesty.py | üretim turu bekliyor (Karar 0) | no | steps_done artık yalnız VERIFIED sayıyor |
| 559 | No false "4/4 completed" | İki koşu yalan söylüyor | Dürüst | DONE | PA | P0 | 558 | B10 | app/executive/activities.py, app/executive/service.py, app/executive/models.py | test_execution_honesty.py | üretim turu bekliyor (Karar 0) | no | '4/4' kırmızıyla kanıtlandı: assert 4 == 1 |
| 560 | Task reconciliation | Yok | Mutabakat | DONE | PA | P0 | 11 | B10 | app/executive/reconcile.py, app/main.py | test_executive_reconcile.py (31) | üretim turu bekliyor (Karar 0) | no | ÖLÇÜM DÜZELTMESİ: MISSING değildi — kod var, saate bağlı, 31 testi geçiyor |

## S. CAPABILITY GENESIS (561–580)

> Ölçüm: motor gerçek ve uçtan uca kanıtlı (canlı bir fikstüre karşı gerçek HTTP adaptörü
> üretip çalıştırıyor). Ama **ön kapısı yok**: katalog boş kuruluyor, `.register()` çağıran
> üretim kodu yok, `POST /v1/genesis/runs` rotası yok. Genesis kapalı değil — **erişilemez**.

| ID | FEATURE | CURRENT_STATUS | TARGET_STATUS | IMPL | PROOF | PRI | DEPS | BATCH | SOURCE_REFERENCES | TEST_REFERENCES | RUNTIME_PROOF | OWNER_ACTION | NOTES |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 561 | Capability request | `POST /v1/genesis/runs {interface_name, operation_id, arguments?, interface_url?, new_version?}` — url kataloğdan; kayıtsız arayüz + url yok → 422 adıyla | Talep alınır | DONE | PA | P2 | — | B36 | app/genesis/routes.py:request_capability | test_genesis_b36.py (REST uçtan uca: 422 → kayıt → 201 verified) | — | no | Ön kapı açıldı |
| 562 | Catalogue | `genesis_catalogue` tablosu (göç 0049) + `CatalogueStore.load()` açılışta (lifespan) ve her kayıttan sonra bellek içi kataloğu yeniden kurar; devre dışı satır listede kalır, sözlü dizinden çıkar | Dolu | DONE | PA | P2 | 563 | B36 | app/genesis/catalogue.py:CatalogueStore; models.py:GenesisCatalogueRow; main.py (lifespan) | test_genesis_b36.py (kaydet → çözümle → ikinci süreç yükler → devre dışı) | — | no | Boş kalmasının nedeni (563) kapandı |
| 563 | Catalogue registration | `POST /v1/genesis/catalogue {name, url, target_phrases, operations, spec_digest}` (url aynı ana makine kuralından geçer; şekil hatası 422, sahibe cümle) + `DELETE /catalogue/{name}` | Kayıt yüzeyi | DONE | PA | P2 | — | B36 | app/genesis/routes.py; catalogue.py:register/disable/entry_from_dict | test_genesis_b36.py (REST + 4 şekil reddi) | — | no | — |
| 564 | Discovery | `POST /v1/genesis/catalogue/discover {url}` → açıklama çekilir, giriş ÖNERİLİR (ad, işlemler kendi id'leriyle fiil, read_back, spec sha256); hiçbir şey kaydedilmez | Tam | DONE | PA | P2 | 562 | B36 | catalogue.py:discover; routes.py | test_genesis_b36.py (öneri + REST) | — | no | — |
| 565 | Interface inspection | `fetch_interface(url, host_allowed=)`: loopback kuralları aynen; loopback DIŞI ana makine yalnız sahip varlık olarak kaydedip kendi üstünde ağ izni verdiyse (`GenesisRuntime.host_allowed` → RegistryAuthorizationProvider.covers); https izinli | Genel | DONE | PA | P2 | 564 | B36 | app/genesis/interface.py; runtime.py:host_allowed | test_genesis_b36.py (yetkisiz → validation_error; yetkili → ağ; ayrıcalıklı port hâlâ ret; kayıttan izin) | — | no | Dar ön koşul kalktı; izin sahibin varlık kaydından |
| 566 | Adapter generation | Çalışıyor | Aynı | DONE | PA | P2 | — | — | app/genesis/ | canlı fikstüre karşı üretim | — | no | Rewrite gerekmez |
| 567 | Adapter tests | Çalışıyor | Aynı | DONE | PA | P2 | — | — | app/genesis/ | genesis testleri | — | no | — |
| 568 | Sandbox | Çalışıyor | Aynı | DONE | PA | P2 | — | — | app/genesis/ | genesis testleri | — | no | — |
| 569 | Registration | Kayıt yalnız güvenlik kapısı + değerlendirme + bağımsız inceleme + gölge + kanarya sonrası (M24) ve B36'da provenance {generator, model_generated, security_review_passed} manifestte | Tam | DONE | PA | P2 | 563 | B36 | app/genesis/service.py:_publish_and_register | test_genesis_b36.py (provenance), test_genesis_end_to_end.py | — | no | — |
| 570 | Activation | `deactivate` (status deprecated → çözümlenmez, sesle ulaşılmaz) / `activate` (registry'nin tek yolu `rollback_to` ile mevcut kayıtlı sürüm yeniden servis; düz status yazımı reddedilir) + REST | Tam | DONE | PA | P2 | 569 | B36 | service.py:deactivate/activate; registry.py:rollback_to (aynı sürüm yeniden etkin) | test_genesis_b36.py | — | no | — |
| 571 | Versioning | `request(new_version=True)`: çözümlenen yeteneğin bir sonraki yama sürümü (0.1.0 → 0.1.1) yeni koşuyla üretilir, kayıtta eskisi superseded; `GET /capabilities/{id}/versions` | Tam | DONE | PA | P2 | 570 | B36 | service.py:request/_design/versions; routes.py | test_genesis_b36.py (0.1.1 üretildi, iki sürüm listelendi) | — | no | — |
| 572 | Rollback | `POST /capabilities/{id}/rollback {version}` → registry.rollback_to (yalnız daha önce KAYITLI sürüm; hiçbir şey silinmez); bilinmeyen sürüm 404 | Tam | DONE | PA | P2 | 571 | B36 | service.py:rollback; routes.py | test_genesis_b36.py (0.1.1 → 0.1.0 geri) | — | no | — |
| 573 | Production capability use | `service.use` / `POST /capabilities/{id}/use {arguments}` → dispatcher (kayıtlı bağdaştırıcı alt süreçte); çözümlenmeyen yetenek `capability_missing`; sesli yol aynı (`capability.request` mevcut yeteneği kullanır) | Üretimde kullanılır | DONE | PA | P2 | 570 | B36 | service.py:use; routes.py | test_genesis_b36.py (kullan; devre dışıyken ret) | üretim turu READY_FOR_OWNER | onay akışı | Üretimde gerçek kullanım Karar 0 sonrası |
| 574 | Genesis REST | runs (GET/POST) + runs/{id} (+approve/cancel) + catalogue (GET/POST/discover/DELETE) + capabilities/{id}/versions / rollback / deactivate / activate / use; `security_refused` 409 | Tam | DONE | PA | P2 | 561 | B36 | app/genesis/routes.py | test_genesis_wiring.py (13 yol), test_genesis_b36.py, 401 kontrolü | — | no | — |
| 575 | Genesis Voice tool | ÖLÇÜM DÜZELTMESİ: `capability.request/status/approve/cancel` M24'ten beri var ve korpusta (`genesis` kategorisi); B36 kataloğu doldurduğu için üretimde de ulaşılır | Sesli araç | DONE | PA | P2 | 574 | B36 | app/voice/realtime_sessions/tools_genesis.py | test_genesis_tools.py, korpus genesis | — | no | Matris 'Yok' diyordu; değildi — kataloğun boşluğu sesli yolu görünmez kılıyordu |
| 576 | Genesis Cockpit UI | Kokpit 'Yeni Yetenek' paneli (M24) + B36: /selfdev'de `GenesisCataloguePanel` (kayıtlı arayüzler: ana makine, sözlü adlar, işlem fiilleri, devre dışı) | Arayüz | DONE | PA | P2 | 574 | B36 | apps/web/app/core/panels/CockpitPanels.tsx:GenesisCataloguePanel; lib/cockpit/genesis.ts:fetchGenesisCatalogue | genesis-catalogue.test.tsx (3), genesis-panel.test.tsx | — | no | — |
| 577 | Model-backed capability generation | `AdapterCodeModel` seam (`AnthropicAdapterCodeModel` / scripted): YALNIZ `genesis_model_generation_enabled` (varsayılan kapalı) altında, yalnız bağdaştırıcı modülünü değiştirir; aynı testler/evals/tedarik zinciri/güvenlik kapısından geçer; provenance `http_adapter+model` | Açılır (kontrollü) | DONE | PA | P2 | 579 | B36 | app/genesis/model_generator.py; adapter.py (model seam); service.py:_build | test_genesis_b36.py (bayrak kapalı → sorulmaz; açık → aday onay bekler; düşman öneri reddedilir) | — | model bütçesi | 579'dan sonra açıldı; gerçek model çağrısı sahibin bayrağı+anahtarıyla |
| 578 | Restricted app-only generation | ÖLÇÜM DÜZELTMESİ: M24'ün HttpAdapterGenerator'ı §2 alt kümesindeki HER arayüz/işlem için üretir (5 fonksiyonluk liste M7'nin saf dönüşümleri içindi); B36 modeli aynı kapı altında ekler | Genişler | DONE | PA | P2 | 577 | B36 | app/genesis/adapter.py | test_genesis_generalisation.py, test_genesis_b36.py | — | no | — |
| 579 | Security gate | `security_gate.review_layout` — B35 güvenlik incelemesi + üretilen koda özel: izin listesi dışı import, araştırılan ana makine dışı URL, sistem erişimi (subprocess/dosya yazma/os.system/shutil/ctypes/socket); `_build` ile `_test` arasında ZORUNLU; kırmızı → `security_refused`, sürüm rejected, kanıtta bulgular | Zorunlu kapı | DONE | PA | P0 | 680 | B36 | app/genesis/security_gate.py; service.py:_secure | test_genesis_b36.py (satır bazlı bulgular; uçtan uca ret) | — | no | 577'nin ön koşulu yerinde |
| 580 | Owner approval | Model yazımlı HER bağdaştırıcı (okuma dahil) `awaiting_approval`'da sahibi bekler; onay aynı okunma+oturum kapısından; onay KODU onaylar, mutasyonu yetkilendirmez (read_only kalır) | Sahip onayı | DONE | PA | P2 | 579 | B36 | service.py:_drive/approve | test_genesis_b36.py (bekler → onay → verified, read_only) | — | onay akışı | — |

## T. SELF-DEVELOPMENT ENGINE (581–625)

> Ölçüm: motor **gerçek ve kanıtlı** — 7 gerçek `selfdev/*` dalı, gerçek worktree, gerçek
> pytest, gerçek bir kusuru düzelten aday `db9ed85`, politika sınırında `STOPPED_AT_POLICY_BOUNDARY`.
> Ama **çalışan sistemden erişilemez**: rota yok, araç yok, zamanlayıcı yok, üretim imajında
> git checkout yok. Kusur girişi elle yazılan JSON. Anayasa gereği otonom dağıtım yasak.

| ID | FEATURE | CURRENT_STATUS | TARGET_STATUS | IMPL | PROOF | PRI | DEPS | BATCH | SOURCE_REFERENCES | TEST_REFERENCES | RUNTIME_PROOF | OWNER_ACTION | NOTES |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 581 | Defect intake | `POST /v1/selfdev/defects` (başlık/kanıt/kapsam/başarısız test) → `selfdev_defects` satırı (göç 0048); sesle `selfdev.defect`; köprüden `from-opportunity`; CI kırmızısından `ci_failure` | Ürün yüzeyi | DONE | PA | P2 | — | B35 | app/selfdev/models.py; service.py:intake; routes.py:create_defect | test_selfdev_b35.py (intake→claim→finish→approve; REST uçtan uca) | 7 koşu + B35 kuyruk | no | Elle JSON kaldı (CLI); kuyruk asıl yüzey |
| 582 | Opportunity intake | Çalışıyor | Aynı | DONE | PR | P2 | — | — | app/evolution/ | evolution testleri | 14 fırsat "idea"da | no | — |
| 583 | Opportunity to defect bridge | `bridge.defect_from_opportunity` (kind: olay→bug / boşluk→feature; kapsam detail.paths ya da varsayılan; terfi sınıfı satırdan ya da yollardan TÜRETİLİR) + `intake_opportunity` (canlı ikinci satır yok) + `POST /defects/from-opportunity/{id}` | Köprü | DONE | PA | P2 | 581,582 | B35 | app/selfdev/bridge.py; service.py:intake_opportunity; routes.py | test_selfdev_b35.py (bridge + REST bridge + 409 tekrar) | 14 fırsat köprüye hazır | no | En değerli tek bağlantı kuruldu |
| 584 | Risk classification | Tier tablosu çalışıyor | Aynı | DONE | PA | P0 | — | — | app/evolution/risk.py | risk testleri | tier 5: supervisor.py, app/selfdev/ | no | Rewrite gerekmez |
| 585 | Promotion class | Satır `promotion_class` taşır: köprünün sınıfı koşu bitince KORUNUR (motorun kendi türetimi yalnız sınıf yoksa); giriş `never_auto_promote` söyler; panelde sınıf Türkçe | Tüketilir | DONE | PA | P2 | 584 | B35 | service.py:finish; bridge.py; CockpitPanels.tsx:CandidateRow | test_selfdev_b35.py (AUTO_SAFE motor sınıfı NEVER_AUTO_PROMOTE satırını ezmez), selfdev-queue.test.tsx | — | no | Otonom dağıtımın 4. bariyeri tüketildi |
| 586 | Real git branch | Çalışıyor | Aynı | DONE | PR | P2 | — | — | app/selfdev/workspace.py | test_selfdev_engine.py | 7 `selfdev/*` dalı | no | — |
| 587 | Real git worktree | Çalışıyor | Aynı | DONE | PR | P2 | — | — | app/selfdev/workspace.py | test_selfdev_engine.py | 2 canlı worktree | no | — |
| 588 | Codebase analysis | Çalışıyor | Aynı | DONE | PA | P2 | — | — | app/selfdev/ | test_selfdev_anthropic_model.py | — | no | — |
| 589 | Architecture planning | Plan + kapsam doğrulaması + B35: motor kapı/gölge/tetik/güvenlik kaydını planın ötesinde çalıştırır; worker `defect_spec_of` kuyruk satırını motora çevirir | Tam | DONE | PA | P2 | 588 | B35 | app/selfdev/engine.py; worker.py | test_selfdev_engine.py (27), test_selfdev_b35.py | — | no | Model destekli planın kalitesi model bütçesine bağlı |
| 590 | Patch generation | Çalışıyor (SEARCH/REPLACE blok formatı) | Aynı | DONE | PR | P2 | — | — | app/selfdev/anthropic_model.py | test_selfdev_anthropic_model.py | aday db9ed85 | no | Düz dize formatı çözdü |
| 591 | New module generation | Çalışıyor | Aynı | DONE | PA | P2 | — | — | app/selfdev/ | test_selfdev_engine.py | — | no | — |
| 592 | Regression test generation | Çalışıyor | Aynı | DONE | PA | P2 | — | — | app/selfdev/ | test_selfdev_engine.py | — | no | Plan kendi testini adlandırabiliyor |
| 593 | Targeted tests | Adayın kendi worktree'sinde gerçek pytest | Aynı | DONE | PR | P2 | — | — | app/selfdev/runner.py | test_selfdev_cli.py | — | no | — |
| 594 | Failure analysis | Çalışıyor | Aynı | DONE | PR | P2 | — | — | app/selfdev/ | test_selfdev_engine.py | teşhis-düzeltme döngüsü | no | — |
| 595 | Automated fix | Artımlı (carry) düzeltme | Aynı | DONE | PR | P2 | — | — | app/selfdev/anthropic_model.py | test_selfdev_anthropic_model.py | — | no | — |
| 596 | Retry | Çalışıyor | Aynı | DONE | PA | P2 | — | — | app/selfdev/engine.py | test_selfdev_engine.py | — | no | — |
| 597 | Static review | `ruff check --fix` (yalnız güvenli) | Aynı | DONE | PA | P2 | — | — | app/selfdev/runner.py, reviewer.py | test_selfdev_engine.py | — | no | — |
| 598 | Security review | `security_review.review_candidate` — `Grant.SECURITY_REVIEW_CANDIDATE` olmadan çalışmaz; gizli anahtar deseni, tehlikeli çağrı (eval/exec/os.system/shell=True/pickle), korunan yollar (identity/security/authority/CI/deploy/secret/bağımlılık manifestleri), yeni ağ çıkışı, test silme — her bulgu yol+satır; bağımsız incelemenin ZORUNLU adımı, kırmızıysa test bile koşmaz | Zorunlu adım | DONE | PA | P0 | 680 | B35 | app/selfdev/security_review.py; reviewer.py:review (security_review check) | test_selfdev_b35.py (5 bulgu türü, grant kapısı, korunan yollar), test_selfdev_engine.py (identity sınırı karantina) | — | no | Güvenlik boşluğu kapandı |
| 599 | General code review | IndependentReviewer çalışıyor | Aynı | DONE | PA | P2 | — | — | app/selfdev/reviewer.py | test_selfdev_engine.py | regresyon kırmızı to yeşil | no | Rewrite gerekmez |
| 600 | Full quality gate | `PackageGate` (worktree içinde tests/unit + ruff; her adım adıyla) motorda her iki incelemeden SONRA, commit'ten ÖNCE; kırmızı = düzeltme döngüsüne bir başarısızlık daha (aynı deneme bütçesi) | Tam | DONE | PA | P2 | 25 | B35 | app/selfdev/gate.py; engine.py:_drive | test_selfdev_engine.py (kırmızı kapı → teşhis → yeşil; bütçe dolunca karantina) | — | no | Gerçek kapı ~25 dk; worker'da `--no-full-gate` ile kapatılabilir |
| 601 | CI trigger | `GitPushTrigger` — yalnız `selfdev/*` dalı, yalnız `PAGENTOS_SELFDEV_CI_PUSH=true` (varsayılan kapalı; kayıt neden itmediğini söyler) | Tam | DONE | PA | P2 | 600 | B35 | app/selfdev/gate.py:GitPushTrigger; __main__.py:worker | test_selfdev_b35.py (kapalı/yabancı dal reddi), test_selfdev_engine.py (tetik kaydı) | — | no | Gerçek push sahibin bayrağıyla |
| 602 | CI result read | `gh run list` çalışıyor | Aynı | DONE | PR | P2 | — | — | app/selfdev/ | — | CI okuma | no | — |
| 603 | CI failure to code fix loop | Yerelde: kırmızı kapı → teşhis → yeni yama (deneme bütçesi). Uzakta: `reconcile_ci` / `POST /defects/{id}/ci` — bekleyen adayın CI'ı kırmızıysa ebeveynini, dalını ve CI sözünü taşıyan TEK yeni kusur (`max_ci_fix_rounds` sınırı, aynı kırmızı ikinci satır açmaz) | Döngüye döner | DONE | PA | P2 | 602 | B35 | app/selfdev/service.py:reconcile_ci; engine.py | test_selfdev_b35.py (sınırlı takip; tekrar; yeşil hiçbir şey açmaz) | — | no | Döngünün açık ucu kapandı |
| 604 | Candidate commit | Çalışıyor | Aynı | DONE | PR | P2 | — | — | app/selfdev/engine.py | test_selfdev_engine.py | db9ed85 | no | — |
| 605 | Candidate evidence | record.json, model-exchanges.json, candidate.diff | Aynı | DONE | PR | P2 | — | — | app/selfdev/engine.py | test_selfdev_engine.py | — | no | `write_bytes` ile CRLF sorunu çözüldü |
| 606 | Candidate history | Çalışıyor | Aynı | DONE | PR | P2 | — | — | app/selfdev/ | test_selfdev_engine.py | 7 kayıt | no | — |
| 607 | Candidate quarantine | Çalışıyor | Aynı | DONE | PA | P2 | — | — | app/selfdev/engine.py | test_selfdev_engine.py | — | no | — |
| 608 | Candidate shadow | `ProcessShadowRunner`: adayın API'si worktree'den boş bir loopback portunda, hazır yolunu bekler, salt-okur yolları yoklar, canlıyla durum kodlarını karşılaştırır, süreci öldürür; asla cevap vermeyen gölge başarısız gölgedir; rapor koşu kaydında | Gölge | DONE | PA | P2 | 607 | B35 | app/selfdev/shadow.py; engine.py | test_selfdev_b35.py (gerçek alt süreç: 200/404 yoklama, canlıyla uyuşmazlık, zaman aşımı) | gerçek loopback süreç | no | Anayasadaki zorunlu yol |
| 609 | Owner approval | `POST /defects/{id}/approve` / `reject` — yalnız doğrulanmış sahip oturumundan `mint_owner_capability`; karar satıra ve deftere (`promoted: false`) yazılır; hiçbir şey canlıya alınmaz | Onay yüzeyi | DONE | PA | P2 | 606 | B35 | app/selfdev/routes.py; service.py:_decide | test_selfdev_b35.py (kapasitesiz nesne reddi; REST approve promoted=false; ikinci karar 409) | — | aday onayı (db9ed85 dahil): READY_FOR_OWNER | 621 ile ortak |
| 610 | Blue/green release | Çalışıyor | Aynı | DONE | PR | P0 | — | — | scripts/cloud/release-cloud-core.ps1 | release testleri | 3 üretim sürümü | no | 1,2 kusurları hariç |
| 611 | Rollback | Çalışıyor | Aynı | DONE | PR | P0 | — | — | scripts/cloud/ | release testleri | 16 geri alma | no | — |
| 612 | Last-known-good | Çalışıyor | Aynı | DONE | PR | P0 | — | — | state/, app/system/health.py | release testleri | LKG d86b3d9 | no | — |
| 613 | Post-release health | Çalışıyor | Aynı | DONE | PR | P0 | — | — | app/system/health.py | release testleri | 18 bileşen ok | no | — |
| 614 | Post-release rollback | Kısmi (timer yok) | Otomatik | PARTIAL | PA | P0 | 652 | B08 | services/recovery-supervisor/, scripts/cloud/install-recovery-supervisor.sh | test_systemd_install.py | host kurulumu bekliyor (sahip) | no | 651/652 ile ortak: timer kurulmadan otomatik değil |
| 615 | Scheduler | Kuyruk + worker: `POST /worker/claim` (en eski/öncelikli `queued`; süresi dolan claim yeniden kuyruğa) → `python -m app.selfdev worker --api URL` (PAGENTOS_OWNER_TOKEN, 30 sn yoklama) → `start`/`finish`; bulut sunucusu depo tutmaz, worker geliştirme makinesinde | Zamanlayıcı | DONE | PA | P2 | 583 | B35 | app/selfdev/worker.py; __main__.py:worker_main; service.py:claim/expire_stale_claims | test_selfdev_b35.py (worker döngüsü; claim/expire; REST) | — | no | Worker'ı sahibin makinesi çalıştırır |
| 616 | Attempt budget | Çalışıyor | Aynı | DONE | PA | P2 | — | — | app/selfdev/engine.py | test_selfdev_engine.py | — | no | — |
| 617 | Time budget | Çalışıyor | Aynı | DONE | PA | P2 | — | — | app/selfdev/engine.py | test_selfdev_engine.py | — | no | — |
| 618 | Token/model budget | Koşu başına bütçe (ADR-0124) + GÜNLÜK toplam `selfdev_daily_token_budget` (bitmiş koşuların tokenları, UTC gün); dolunca claim `budget_exhausted`; `/status` harcanan/kalan | Tam | DONE | PA | P2 | — | B35 | app/selfdev/service.py:budget_report/claim; config.py | test_selfdev_b35.py (bütçe dolunca ad ile ret; status sayıları) | — | model bütçesi (sayı sahibin) | — |
| 619 | Disk budget | `selfdev_min_free_bytes` (varsayılan 5 GiB) worktree kökünün altında; altındaysa claim `disk_floor`; `/status` boş alanı söyler | Sınır | DONE | PA | P2 | — | B35 | app/selfdev/service.py:budget_report; workspace.py (min_free_bytes) | test_selfdev_b35.py (disk tabanı reddi) | — | no | Worktree birikimi: koşu sonrası worktree serbest, dal kalır |
| 620 | Parallel candidate limit | `selfdev_max_parallel` (varsayılan 1): aktif (claimed/running) sayısı sınırdaysa claim `parallel_limit`, nedeni `/status`'ta | Sınır | DONE | PA | P2 | 619 | B35 | app/selfdev/service.py:claim | test_selfdev_b35.py (ikinci worker ad ile reddedilir; REST) | — | no | — |
| 621 | SelfDev Cockpit | /selfdev sayfasında `SelfDevQueuePanel`: onay bekleyen adaylar — tür, terfi sınıfı, risk, güvenlik incelemesi, kapı, gölge, CI, açıklama; Onayla — kaydet (canlıya almaz) / Vazgeç çifti; NEVER_AUTO_PROMOTE satırı cümlesiyle | Arayüz | DONE | PA | P2 | 606 | B35 | apps/web/app/core/panels/CockpitPanels.tsx:SelfDevQueuePanel; lib/cockpit/approvals.ts (candidate); selfdev/page.tsx | selfdev-queue.test.tsx (6), approvals-client (8 rota) | — | no | 697 ile aynı sayfa |
| 622 | "Şu bug'ı kendin düzelt" | `_selfdev_match` (kendin/kendine/sen + hata/bug + düzelt/çöz; evrim anahtarından sonra, bellek düzeltmesinden önce) → `selfdev.defect`: sahibin cümlesi satırda, kuyruk + makbuz ("onayınızı isteyeceğim, canlıya almam") | Sesle atama | DONE | PA | P2 | 581 | B35 | app/voice/intents.py:_selfdev_match; realtime_sessions/tools_selfdev.py | test_selfdev_b35.py (ses uçtan uca; komşular), korpus selfdev.fix.* | — | no | — |
| 623 | "Şu özelliği kendine ekle" | Aynı eşleyici (özelli/yetene + ekle/kazandır) → `selfdev.feature`; `selfdev.status` "Kendinde ne düzeltiyorsun?" | Sesle atama | DONE | PA | P2 | 622 | B35 | app/voice/intents.py; tools_selfdev.py | korpus selfdev.feature.* / selfdev.status, test_selfdev_b35.py | — | no | "özelliği" k→ğ: kök "özelli" |
| 624 | No autonomous high-risk promotion | Dört bağımsız bariyer | Aynı | DONE | PA | P0 | — | — | app/evolution/supervisor.py, risk.py | supervisor testleri | üretim izni 0 | no | Ürün ilkesi — ASLA gevşetilmez |
| 625 | Low-risk AUTO_SAFE option later | Yok | Sonra | DEFERRED | NYP | P3 | 585,608 | — | app/evolution/ | — | — | sahip kararı | v1.0 kapsamı dışı |

## U. BACKUP / RECOVERY (626–655)

> Ölçüm: yedek + geri yükleme host üzerinde **gerçek koşuyla kanıtlandı** (snapshot 8ad3e923,
> 4 veritabanı + 21 nesne, tatbikat geçti, timer'lar armed, anahtar escrow'da). Ama host dışı
> kopya yok: **host kaybında RPO/RTO sonsuz**. `main` dalında reconcile timer'ı yok.

| ID | FEATURE | CURRENT_STATUS | TARGET_STATUS | IMPL | PROOF | PRI | DEPS | BATCH | SOURCE_REFERENCES | TEST_REFERENCES | RUNTIME_PROOF | OWNER_ACTION | NOTES |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 626 | PostgreSQL backup | Çalışıyor (pg_dump -Fc) | Aynı | DONE | PR | P0 | — | — | scripts/cloud/backup-cloud-core.sh | recovery-supervisor testleri | 4 veritabanı | no | Rewrite gerekmez |
| 627 | PostgreSQL role backup | Çalışıyor | Aynı | DONE | PR | P0 | — | — | scripts/cloud/backup-cloud-core.sh | recovery testleri | rol tanımları | no | — |
| 628 | MinIO backup | API üzerinden, host tarafı ayrıştırma | Aynı | DONE | PR | P0 | — | — | scripts/cloud/backup-cloud-core.sh | backup_fakes.py | 21 nesne | no | "0 nesne" kusuru düzeltildi |
| 629 | Config backup | .env, kimlik kökü, edge durumu, systemd | Aynı | DONE | PR | P0 | — | — | scripts/cloud/backup-cloud-core.sh | recovery testleri | — | no | — |
| 630 | Identity-root backup strategy | DPAPI escrow | Host dışı da | DONE | PR | P0 | 644 | B09 | scripts/secret-store.ps1 | — | anahtar escrow'da | no | 644 ile tamamlanır |
| 631 | Release metadata | RELEASE + LAST_KNOWN_GOOD + RELEASE.json | Kanonik | DONE | PR | P0 | 21 | B01 | scripts/cloud/release-cloud-core-bluegreen.sh (write_release_metadata) | cloud-release-bluegreen.tests.ps1, cloud-release.tests.ps1 | üretimde LKG d86b3d9 (düz metin) | no | RELEASE.json bir sonraki sürümde üretimde oluşacak |
| 632 | Recovery metadata | RELEASE.json: sha, renk, build_id, şema, önceki sha, zaman | Tam | DONE | PA | P0 | 631 | B01 | scripts/cloud/release-cloud-core-bluegreen.sh | cloud-release-bluegreen.tests.ps1, cloud-release.tests.ps1 | — | no | Sürüm, reconcile ve rollback üçü de yazıyor; tamamlanmamış sürüm yazmıyor |
| 633 | Encryption | Çalışıyor | Aynı | DONE | PR | P0 | — | — | scripts/cloud/backup-cloud-core.sh | recovery testleri | restic | no | — |
| 634 | Integrity hash | Tablo bazında sha256, dökümün kendisinden | Aynı | DONE | PR | P0 | — | — | scripts/cloud/backup-cloud-core.sh | recovery testleri | parmak izleri | no | Güçlü disiplin |
| 635 | Retention | Çalışıyor | Aynı | DONE | PA | P0 | — | — | scripts/cloud/backup-cloud-core.sh | recovery testleri | — | no | — |
| 636 | Daily schedule | Timer armed | Aynı | DONE | PR | P0 | — | — | scripts/cloud/install-recovery-supervisor.sh | — | systemd timer | no | — |
| 637 | Weekly restore drill | Geçti | Aynı | DONE | PR | P0 | — | — | scripts/cloud/ | recovery testleri | tatbikat verdict passed | no | Ürün ilkesi uygulanmış |
| 638 | Pre-migration backup | `timeout --kill-after=60`, rc 74/67 | Aynı | DONE | PA | P0 | — | — | scripts/cloud/release-cloud-core*.sh | cloud-release-bluegreen.tests.ps1, cloud-release.tests.ps1 | — | no | pipefail altında gözden geçirildi: rc zaten açıkça yakalanıyordu, değişmedi |
| 639 | Restore script | Çalışıyor | Aynı | DONE | PR | P0 | — | — | scripts/cloud/ | recovery testleri | — | no | — |
| 640 | PostgreSQL restore | Kanıtlandı | Aynı | DONE | PR | P0 | — | — | scripts/cloud/ | recovery testleri | scratch container drill | no | — |
| 641 | MinIO restore | Kanıtlandı | Aynı | DONE | PR | P0 | — | — | scripts/cloud/ | recovery testleri | 21 nesne | no | — |
| 642 | Config restore | Dosyalar var, host yeniden kurulumu denenmedi | Tatbikatlı | DONE | PA | P0 | 644 | B09 | scripts/cloud/restore-cloud-core.sh | test_backup_health.py | S3 kovası + anahtar (sahip) | no | Tatbikat config/.env ve RELEASE yoksa anlık görüntüyü reddediyor |
| 643 | Release restore | Kısmi | Tatbikatlı | DONE | PA | P0 | 642 | B09 | scripts/cloud/restore-cloud-core.sh | test_backup_health.py | S3 kovası + anahtar (sahip) | no | 642 ile tek uygulama: RELEASE metadata'sı zorunlu |
| 644 | Off-host repository | YOK — depo koruduğu diskte | İkinci hedef | DONE | PA | P0 | — | B09 | scripts/cloud/restore-cloud-core.sh:--from-offhost | test_backup_health.py | S3 kovası + anahtar (sahip) | S3 kovası + anahtar | Kopya yazılabiliyordu ama OKUNAMIYORDU; artık ikinci depodan geri yükleme yolu var ve testli |
| 645 | S3-compatible target | Yapılandırılmamış | Yapılandırılır | BLOCKED_OWNER | BLK | P0 | 644 | B09 | scripts/cloud/backup-cloud-core.sh | — | S3 kovası + anahtar (sahip) | S3 kovası + anahtar | Yol hazır; kova ve anahtar sahip eylemi — anahtar asla commit'e girmez |
| 646 | Backup health | Okuyan sağlık yok | Sağlıkta görünür | DONE | PA | P0 | — | B08 | services/api/app/backup_health.py, app/main.py | test_backup_health.py | üretim /v1/system/health checks.backup | no | LAST_BACKUP.json artık okunuyor; görünmüyorsa 'skipped', uydurma arıza yok |
| 647 | Backup failure notification | `OnFailure=` yok | Bildirilir | DONE | PA | P0 | 646 | B08 | infra/systemd/pagentos-failure-marker@.service, infra/systemd/*.service | test_backup_health.py | host kurulumu bekliyor (sahip) | no | OnFailure her timer biriminde; işaret dosyası — kredi yok, ağ yok, sabaha kalır |
| 648 | Restore health | Kısmi | Tam | DONE | PA | P0 | 646 | B08 | services/api/app/backup_health.py, app/main.py | test_backup_health.py | üretim checks.backup.last_drill_* | no | Tatbikat raporu okunuyor; hiç tatbikat yoksa 'stale', 'iyi' değil |
| 649 | RPO measurement | Veri için <=24 sa; host kaybı için sonsuz | Ölçülür ve yayınlanır | DONE | PA | P0 | 644 | B08 | services/api/app/backup_health.py, app/main.py, docs/OPERATIONS.md | test_backup_health.py | üretim rpo_hours | no | Son yedeğin yaşı — belge değil ölçüm |
| 650 | RTO measurement | Dakikalar; host kaybı için sonsuz | Ölçülür | DONE | PA | P0 | 644 | B08 | services/api/app/backup_health.py, app/main.py, docs/OPERATIONS.md | test_backup_health.py | üretim rto_seconds | no | Son tatbikatın gerçek süresi; 14 günde bayatlıyor |
| 651 | Recovery supervisor | Servis `main`'de, timer değil | Kurulu | PARTIAL | PA | P0 | — | B08 | services/recovery-supervisor/, scripts/cloud/install-recovery-supervisor.sh | test_systemd_install.py (842 satır) | host kurulumu bekliyor (sahip) | kurulum onayı | Kod main'de (Astra birleşti); systemd kurulumu sahip kapısı |
| 652 | Recovery systemd timer | `main`'de YOK | Kurulu | PARTIAL | PA | P0 | 651 | B08 | infra/systemd/pagentos-bluegreen-reconcile.timer | test_systemd_install.py | host kurulumu bekliyor (sahip) | kurulum onayı | Timer birimi main'de; host'a kurulum sahip kapısı |
| 653 | Healthy to degraded detection | Kısmi | Tam | PARTIAL | PA | P0 | 652 | B08 | services/recovery-supervisor/, scripts/cloud/install-recovery-supervisor.sh | test_health_policy.py | host kurulumu bekliyor (sahip) | no | Kod hazır; PROVEN_REAL için host'ta bir tur gerekiyor |
| 654 | Automatic rollback | Kısmi | Tam | PARTIAL | PA | P0 | 653 | B08 | services/recovery-supervisor/, scripts/cloud/install-recovery-supervisor.sh, scripts/cloud/release-cloud-core-bluegreen.sh | test_systemd_install.py, cloud-release-bluegreen.tests.ps1 | host kurulumu bekliyor (sahip) | kurulum onayı | Operasyon kilidi (exit 82) main'de; kasten bozulmuş renk denemesi host'ta |
| 655 | Recovery audit | Kısmi | Tam | PARTIAL | PA | P0 | 654 | B08 | services/recovery-supervisor/recovery_supervisor/incidents.py | test_incidents.py | host kurulumu bekliyor (sahip) | no | Rapor üretiliyor; host turu olmadan denetim izi PROVEN_REAL değil |

## V. SECURITY / IDENTITY (656–684)

> Ölçüm: kimlik katmanı güçlü — opak 256-bit jetonlar, yalnızca SHA-256 saklama, sabit
> zamanlı karşılaştırma, kısıtlama, kapsam daraltmanın yapısal zorlanması, kimlik kökü
> imaj dışında, yayımlanan tek soket Tailscale adresine bağlı. Kimliksiz erişilebilen
> rotalar denetlendi ve **hepsi doğru**. Zayıflık zorlamada, tasarımda değil.

| ID | FEATURE | CURRENT_STATUS | TARGET_STATUS | IMPL | PROOF | PRI | DEPS | BATCH | SOURCE_REFERENCES | TEST_REFERENCES | RUNTIME_PROOF | OWNER_ACTION | NOTES |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 656 | Owner credential | Çalışıyor | Aynı | DONE | PR | P0 | — | — | app/identity/ | identity testleri | üretim oturumu | no | Rewrite gerekmez |
| 657 | Session authentication | Opak jeton, SHA-256, sabit zaman | Aynı | DONE | PA | P0 | — | — | app/identity/ | identity testleri | — | no | — |
| 658 | Absolute session lifetime | YOK — yenilenen jeton süresiz yaşar | Mutlak tavan | DONE | PA | P0 | — | B05 | app/identity/service.py:_past_absolute_lifetime, :sweep_expired | test_authority_gate.py | üretim turu bekliyor (Karar 0) | no | 90 gün, created_at'ten; refresh tavanı kaldırmıyor |
| 659 | Idle session lifetime | Kısmi | Tam | DONE | PA | P0 | 658 | B05 | app/identity/service.py:_past_absolute_lifetime, :sweep_expired | test_authority_gate.py | üretim turu bekliyor (Karar 0) | no | Atıl kural artık süpürgede de: jeton sunulmasa da oturum kapanıyor |
| 660 | Panic revoke | Güvenlik sayfasında, iki adımlı | Erişilebilir | DONE | PA | P0 | — | B25 | app/identity/routes.py:panic, apps/web/app/security/PanicControl.tsx | discoverability.test.tsx (15), test_web_asks_for_routes_that_exist.py (47) | üretim turu bekliyor (Karar 0) | no | Kurar-sonra-ateşler: tek yanlış tıklama her oturumu iptal edemez. NE YAPMADIĞINI da söylüyor — kimlik bilgisi geçerli kalıyor. Kendi 401'i başarı sayılıyor (bu oturum da iptal edildi) |
| 661 | Device enrollment | Çalışıyor | Aynı | DONE | PR | P0 | — | — | app/devices/ | device testleri | cihaz MAIL online | no | — |
| 662 | Device revoke | Oturumları da iptal ediyor | Aynı | DONE | PA | P0 | — | — | app/devices/ | device testleri | — | no | — |
| 663 | Device trust | Çağıranın gönderdiği boolean | Sunucuda doğrulanır | DONE | PA | P0 | — | B05 | app/voice/device_trust.py, app/voice/routes.py:verify_speaker_route | test_authority_gate.py | üretim turu bekliyor (Karar 0) | no | 246 ile tek uygulama; oturumun cihaz bağı + cihazın canlı olması |
| 664 | Speaker verification | Tavsiye niteliğinde | Karar yolunda (tek başına kök DEĞİL) | PARTIAL | PA | P0 | 663 | B05 | app/security/step_up.py, app/voice/realtime_sessions/service.py:handle_tool_call | test_voice_step_up.py | gölge mod: sayıyor, engellemiyor — enforce sahip kararı | no | Güvenilmeyen cihazda hiçbir skor yetmiyor — skordan ÖNCE bakılıyor |
| 665 | Speaker embedding | Kısmi | Tam | PARTIAL | PA | P0 | 664 | B05 | app/security/step_up.py, app/voice/realtime_sessions/service.py:handle_tool_call, app/voice/models.py:SpeakerVerdictRow | test_voice_step_up.py | gölge mod: sayıyor, engellemiyor — enforce sahip kararı | no | Verdict kalıcı; probe embedding ASLA saklanmıyor |
| 666 | Sensitive-operation re-auth | Yok | Yeniden doğrulama | PARTIAL | PA | P0 | 658 | B05 | app/security/step_up.py, app/voice/realtime_sessions/service.py:handle_tool_call | test_voice_step_up.py | gölge mod: sayıyor, engellemiyor — enforce sahip kararı | no | Hassas işlemde taze verdict = yeniden doğrulama; gölge modda |
| 667 | Secret detection | Çalışıyor | Aynı | DONE | PA | P0 | — | — | app/security/ | security testleri | 13 desen | no | Tespit var, uygulama eksik |
| 668 | Secret redaction | Desenler log hattında ve dünya modelinde yok | Her yüzeyde | DONE | PA | P0 | 667 | B04 | app/worldmodel/state.py:_Collector.fact, app/logging.py:redact_secrets, app/logging.py:SecretRedactingFilter, app/health.py:_run_check, app/main.py:system_health | test_secret_redaction_surfaces.py | üretim turu bekliyor (Karar 0) | no | Tek kelime dağarcığı: 13 desen app.memory.policy'den, anahtar adları app.research.forbidden_keys'ten |
| 669 | DPAPI storage | Çalışıyor | Aynı | DONE | PR | P0 | — | — | scripts/secret-store.ps1 | owner-harness.tests.ps1 | escrow | no | Rewrite gerekmez |
| 670 | Cloud credential separation | Çalışıyor | Aynı | DONE | PA | P0 | — | — | scripts/cloud/set-cloud-secret.ps1 | — | stdin ile | no | — |
| 671 | Camera permission | Kısmi | Tam | PARTIAL | NYP | P2 | 327 | B48 | app/security/, app/presence/ | — | eye_enabled=false | kamera kararı | B48: cihaz kamera izni Karar 8 ile birlikte |
| 672 | Browser permission | Çalışıyor | Aynı | DONE | PA | P0 | — | — | app/security/ | security testleri | — | no | — |
| 673 | Research permission | Gerçekten zorlanıyor | Aynı | DONE | PA | P0 | — | — | app/security/, app/research/ | research testleri | owner_authorized=false | no | ADR-0113 |
| 674 | File mutation permission | `documents_mutation_enabled` (tek bayrak; kapalıysa her araç `mutation_disabled`) + cihaz yetkili kökleri + gizli ad reddi + yalnız metin türleri + step-up kademeleri (write/append/apply/undo CRITICAL) | İzin modeli | DONE | PA | P2 | 160 | B34 | config.py; mutations.py:_disabled; step_up.py | test_documents_b34.py (host flag) | — | onay politikası | 153-170'in ön koşulu — kapandı |
| 675 | Code promotion permission | Grant'lar var | Aynı | DONE | PA | P0 | — | B05 | app/evolution/routes.py:advance_opportunity | test_evolution_routes.py | üretim turu bekliyor (Karar 0) | no | Çıkış geçişi açıldı: üretim tarafından ÇIKMAK da üretim eylemi |
| 676 | Production deployment permission | Çalışıyor | Aynı | DONE | PA | P0 | — | — | app/evolution/ | supervisor testleri | — | no | — |
| 677 | High-risk owner gate | Çalışıyor | Aynı | DONE | PA | P0 | — | — | app/evolution/supervisor.py | supervisor testleri | — | no | Ürün ilkesi — gevşetilmez |
| 678 | Permanent delete owner gate | Kısmi | Tam | DONE | PA | P0 | — | B05 | app/security/deletion.py | test_deletion_gate.py | tüketiciler B34/B42/B46 | onay politikası | Sahip kararı 2026-09-13: yumuşak silme varsayılan, kalıcı yok etme ikinci kanal |
| 679 | Security audit ledger | Çalışıyor | Saklama politikası eklenir | DONE | PA | P0 | 16 | B07 | app/security/audit_retention.py | test_bounded_delivery.py | 13.560 olay → kuru koşu sayıyor | no | Activity Ledger ASLA süpürülmüyor: kanıt yaşlanmaz, gerekçesi yazılı |
| 680 | Security review of generated code | 598'in modülü tüm üretilen yamalara: `Grant.SECURITY_REVIEW_CANDIDATE` tüketicisi `reviewer_authority()`; identity sınırı artık 5. kademe aday değil, reddedilen yama | Zorunlu | DONE | PA | P0 | — | B35 | app/selfdev/security_review.py; reviewer.py | test_selfdev_b35.py, test_selfdev_engine.py | — | no | 579 (Genesis) ve 439 (App Factory) aynı modülü B36/B40'ta tüketir |
| 681 | No CAPTCHA bypass | Zorlanıyor | Aynı | DONE | PA | P0 | — | — | app/browser/ | browser testleri | — | no | Ürün ilkesi — ASLA gevşetilmez |
| 682 | No DRM bypass | Zorlanıyor | Aynı | DONE | PA | P0 | — | — | app/browser/ | browser testleri | — | no | Ürün ilkesi |
| 683 | No secret logging | İhlal var | Hiçbir sır loglanmaz | DONE | PA | P0 | 668 | B04 | app/logging.py:redact_secrets, app/logging.py:SecretRedactingFilter | test_secret_redaction_surfaces.py | üretim turu bekliyor (Karar 0) | no | Anahtar-adı kuralı her derinlikte, ama yalnız dize değerlere: tokens:1430 sayı kalıyor |
| 684 | No hidden camera | Uygulanıyor | Aynı | DONE | PA | P0 | — | — | app/presence/, apps/web/ | presence testleri | — | no | Ürün ilkesi |

## W. WEB / COCKPIT / UX (685–725)

> Ölçüm: 6 sayfa, **hiç gezinme yok** (`apps/web/app/layout.tsx` yalnızca
> `<html lang="tr"><body>{children}</body></html>`). Cockpit'in 27 panelinden 13'ü boş,
> biri kalıcı `HTTP 422`. Keşfedilebilirlik denetimdeki en düşük skor (0.5/5).

| ID | FEATURE | CURRENT_STATUS | TARGET_STATUS | IMPL | PROOF | PRI | DEPS | BATCH | SOURCE_REFERENCES | TEST_REFERENCES | RUNTIME_PROOF | OWNER_ACTION | NOTES |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 685 | Global navigation | YOK | Nav çubuğu | DONE | PA | P1 | — | B23 | apps/web/app/components/SiteNav.tsx, app/layout.tsx | site-nav.test.tsx | üretim turu bekliyor (Karar 0) | no | tek liste, layout'ta; Çekirdek'te sessiz varyant |
| 686 | Home dashboard | Kısmi | Tam | DONE | PA | P1 | 685 | B23 | apps/web/app/page.tsx | site-nav.test.tsx | üretim turu bekliyor (Karar 0) | no | dizin NAV'dan üretiliyor; elle yazılmış bağlantı satırı kalktı |
| 687 | Voice page | Çalışıyor | Aynı | DONE | PR | P1 | — | — | apps/web/ | web testleri | üretim | no | Rewrite gerekmez |
| 688 | Core page | Çalışıyor | Aynı | DONE | PR | P1 | — | — | apps/web/ | web testleri | üretim | no | — |
| 689 | Memory page | Sayfa var | Sayfa | DONE | PA | P2 | 685,39 | B24 | apps/web/app/memory/page.tsx, app/lib/pages/detail.ts:fetchMemories/fetchEntities | family-pages.test.ts (35), site-nav.test.tsx (12) | üretim turu bekliyor (Karar 0) | no | Liste geri getirme yolunun KENDİSİ (hybrid_search): sahip, sistemin gerçekten hatırlayacağını görüyor. Salt okunur — unutma sert silmedir (36) |
| 690 | Tasks page | Kısmi | Tam | DONE | PA | P1 | 685 | B23 | apps/web/app/artifacts/page.tsx, app/research/page.tsx | site-nav.test.tsx, failure-states.test.tsx | üretim turu bekliyor (Karar 0) | no | görev yüzeyi iki sayfa; ikisi de her yerden erişilir, /artifacts artık yükleme/boş ayırıyor |
| 691 | Research page | Kısmi | Tam | DONE | PA | P1 | 685 | B23 | apps/web/app/research/, app/components/SiteNav.tsx | site-nav.test.tsx | üretim turu bekliyor (Karar 0) | no | 210 ile ortak; çıkışları nav'dan |
| 692 | Artifacts page | Çıkışsız (dead end) | Çıkışlı | DONE | PA | P1 | 685 | B23 | apps/web/app/artifacts/page.tsx | site-nav.test.tsx | üretim turu bekliyor (Karar 0) | no | ölçülmüştü: hiçbir yere link yoktu; artık altı sayfa |
| 693 | Routines page | Sayfa var | Sayfa | DONE | PA | P1 | 685,287 | B24 | apps/web/app/routines/page.tsx | family-pages.test.ts (35), site-nav.test.tsx (12) | üretim turu bekliyor (Karar 0) | no | B14'ün duraklat/devam istemcisi aynen; yanında bu Cloud Core'un rutin kuralları (/v1/routines/policy — çağıranı yoktu) |
| 694 | Alarm page | Sayfa var | Sayfa | DONE | PA | P1 | 685 | B24 | apps/web/app/alarms/page.tsx, app/lib/pages/detail.ts:fetchAlarmHistory | family-pages.test.ts (35), site-nav.test.tsx (12) | üretim turu bekliyor (Karar 0) | no | Kurulu liste + GERÇEKLEŞMELER (B13 req 285, Activity Ledger). Tekrarlayan alarm satırını geri sardığı için 'salı çaldı mı' satırdan yapısal olarak cevaplanamaz |
| 695 | Device page | Kısmi | Tam | DONE | PA | P1 | 685 | B23 | apps/web/app/core/panels/CockpitPanels.tsx:DevicesPanel | site-nav.test.tsx | üretim turu bekliyor (Karar 0) | no | istemci+ayrıştırıcı+durum M18.3'ten beri vardı, yalnızca EKRAN satırı gösteriliyordu |
| 696 | Security page | Sayfa var | Sayfa | DONE | PA | P1 | 685,660 | B24 | apps/web/app/security/page.tsx, app/lib/pages/detail.ts:fetchSecurityAssets/Findings/Assessments/Audit | family-pages.test.ts (35), site-nav.test.tsx (12) | üretim turu bekliyor (Karar 0) | no | Yetki izi RETLERİYLE birlikte: anayasal kuralın uygulandığını sahip ilk kez görebiliyor. Panik anahtarı (660) B25'te bu sayfaya iner |
| 697 | SelfDev page | Sayfa var | Sayfa | DONE | PA | P2 | 685,606 | B24 | apps/web/app/selfdev/page.tsx, app/lib/pages/detail.ts:fetchCapabilityGaps/fetchSkillVersions | family-pages.test.ts (35), site-nav.test.tsx (12) | üretim turu bekliyor (Karar 0) | no | Anayasanın boru hattı SIRASIYLA tek sayfada; dört ayrı panel birer aşama gösteriyordu ve aynı hat olduklarını söyleyen hiçbir şey yoktu. Onay yerinde kalır (ADR-0053 §5) |
| 698 | Notifications page | Sayfa var | Sayfa | DONE | PA | P1 | 685,367 | B24 | apps/web/app/notifications/page.tsx | family-pages.test.ts (35), site-nav.test.tsx (12) | üretim turu bekliyor (Karar 0) | no | `fetchNotificationHistory` (req 377) yazılmıştı ve TEK çağıranı yoktu: 'hiçbir kanal taşımadı' satırları ilk kez görünüyor |
| 699 | Settings page | Sayfa var | Sayfa | DONE | PA | P1 | 685 | B24 | apps/web/app/settings/page.tsx, app/lib/pages/detail.ts:fetchPolicy | family-pages.test.ts (35), site-nav.test.tsx (12) | üretim turu bekliyor (Karar 0) | no | Form DEĞİL: altı /policy ucu çalışan kuralları bildiriyordu, çağıranı yoktu. Aynı kuralı değiştirebilen ikinci yüzey ikinci otorite olurdu; yalnız tarayıcıya ait olan (kademe, 2B) değişir |
| 700 | Feature availability page | Sayfa var | Sayfa | DONE | PA | P1 | 685 | B24 | apps/web/app/availability/page.tsx, scripts/web/sync-feature-matrix.mjs, app/lib/features/matrix.generated.ts | matrix-badges.test.tsx (12), quiet-families.test.tsx (10) | üretim turu bekliyor (Karar 0) | no | İki yarı ayrı: 'şimdi' ÖLÇÜLÜR (panellerin okuduğu aynı veri), 'kayıt' bu matristen ÜRETİLİR. 78'in kuralı — elle tutulan belgeden okunan cevap ancak belge kadar doğrudur |
| 701 | "Neler yapabilirsin?" | Kayıttan türetilen liste + sesli araç | Yetenek listesi | DONE | PA | P1 | 742 | B25 | app/voice/capabilities.py, app/voice/routes.py:list_capabilities, .../tools_assistant.py, apps/web/app/voice/Capabilities.tsx | test_capability_list.py (19), discoverability.test.tsx (15) | üretim turu bekliyor (Karar 0) | no | ELLE LİSTE YOK: `default_registry()` okunuyor. Örnek cümleler araç açıklamalarından ÇIKARILIYOR — sahibe söylenen cümle, modele dinlemesi söylenen cümlenin aynısı. Sesli cevap listeyi OKUMAZ. Yönlendirme yarısı 734 (B27) |
| 702 | Search | Palet aramayı da yapıyor | Arama | DONE | PA | P1 | 685 | B25 | apps/web/app/components/CommandPalette.tsx:search | palette.test.tsx (20) | üretim turu bekliyor (Karar 0) | no | Üç kaynak, hepsi ürünün zaten tuttuğu listeler: NAV, FAMILIES, araç kaydı. Büyük/küçük harf katlaması tr-TR — `HAFIZA` → `Hafıza`, `GÜVENLİK` → /security |
| 703 | Global command palette | ⌘K / Ctrl+K her sayfada | Palet | DONE | PA | P1 | 702 | B25 | apps/web/app/components/CommandPalette.tsx, app/layout.tsx | palette.test.tsx (20) | üretim turu bekliyor (Karar 0) | no | Layout'ta, nav gibi: sonradan eklenen sayfa unutamaz. İkinci açıcı `/` — ama yazarken değil |
| 704 | Turkish error dictionary | Yok | Tek sözlük | DONE | PA | P1 | — | B22 | services/api/app/errors/catalog.py | test_owner_error_language.py | üretim turu bekliyor (Karar 0) | no | 96 sınıf; enum AİLELERİ + string sabitler, iki yönde eksiksiz |
| 705 | No Python errors shown | Ham istisna gösteriliyor | Hiç gösterilmez | DONE | PA | P1 | 704 | B22 | app/errors/owner.py, 33 rota + apps/web/app/lib/errors/failure.ts | test_owner_error_language.py, failure-states.test.tsx | üretim turu bekliyor (Karar 0) | no | AST guard: bir rota istisnayı sahibe VEREMEZ; istemcide ikinci kilit |
| 706 | Empty-state UX | Kısmi | Tam | DONE | PA | P1 | 704 | B22 | apps/web/app/core/panels/Panel.tsx, apps/web/app/artifacts/page.tsx | failure-states.test.tsx | üretim turu bekliyor (Karar 0) | no | artifacts sayfası ilk yanıttan ÖNCE boş liste gösteriyordu |
| 707 | Loading state | Kısmi | Tam | DONE | PA | P1 | — | B22 | apps/web/app/core/panels/Panel.tsx, apps/web/app/artifacts/page.tsx | failure-states.test.tsx | üretim turu bekliyor (Karar 0) | no | kokpit zaten ayırıyordu; sayfalar ayırmıyordu |
| 708 | Retry state | Yok | Var | DONE | PA | P1 | 704 | B22 | apps/web/app/lib/errors/failure.ts:RETRYABLE_CLASSES | failure-states.test.tsx | üretim turu bekliyor (Karar 0) | no | düğme yalnızca kendiliğinden düzelebilecek hatalarda |
| 709 | Provider-blocked state | Yok | Var | DONE | PA | P1 | 704 | B22 | apps/web/app/lib/errors/failure.ts:PROVIDER_BLOCKED_CLASSES | failure-states.test.tsx, provider-unavailable.test.ts | üretim turu bekliyor (Karar 0) | no | 235 ile ortak; anahtar yoksa tekrar düğmesi YOK |
| 710 | Permission-required state | Yok | Var | DONE | PA | P1 | 704 | B22 | apps/web/app/lib/errors/failure.ts:PERMISSION_CLASSES | failure-states.test.tsx | üretim turu bekliyor (Karar 0) | no | "izni yalnızca sen verebilirsin" |
| 711 | Owner-action-required state | Yok | Var | DONE | PA | P1 | 704,383 | B22 | apps/web/app/lib/errors/failure.ts:OWNER_ACTION_CLASSES | failure-states.test.tsx | üretim turu bekliyor (Karar 0) | no | hata değil bekleme; "Alınamadı" yazmaz |
| 712 | Feature status badges | Rozet var | Rozetler | DONE | PA | P1 | 700 | B24 | apps/web/app/components/FeatureBadge.tsx, app/lib/features/badges.ts | matrix-badges.test.tsx (12) | üretim turu bekliyor (Karar 0) | no | Sınıflar yeniden yazılmıyor: belgenin kullandığı IMPL değerlerinden üretiliyor, her rozet ham sınıfı `data-badge-class` ile taşıyor |
| 713 | PROVEN_REAL badges | Rozet var | Rozetler | DONE | PA | P1 | 712 | B24 | apps/web/app/components/FeatureBadge.tsx, app/lib/features/matrix.generated.ts | matrix-badges.test.tsx (12) | üretim turu bekliyor (Karar 0) | no | Kısaltmalar belgenin KENDİ LEGEND tablosundan okunuyor. Sahibin en çok ihtiyaç duyduğu ayrım burada: test söylüyor / gerçekte görüldü / kimse bakmadı |
| 714 | Cockpit panel cleanup | 27/27 ya veri ya gizli | Boş aile panel doğurmaz | DONE | PA | P1 | 685 | B24 | apps/web/app/lib/cockpit/families.ts, app/core/panels/Panel.tsx, app/core/panels/CockpitPanels.tsx | quiet-families.test.tsx (10) | üretim turu bekliyor (Karar 0) | no | `failed` ve `loading` asla gizlenmez. Otobüs konuşuyorsa panel kalır. Kalıcı 422 zaten 715'te (B03, 9ddf243) kapanmıştı; bu turda 27 yolun hepsi OpenAPI'de doğrulandı |
| 715 | Broken /creative/runs route fix | `/v1/creative/runs` artık `/{run_id}`'den önce | Çalışır | DONE | PA | P0 | — | B03 | app/creative/routes.py | test_web_asks_for_routes_that_exist.py (12) | — | no | Web'in istediği 10 yolun hepsi API'de doğrulanıyor |
| 716 | PWA navigation | Kurulu PWA tek bağlantılı sayfada açılıyor | Gezinilebilir | DONE | PA | P1 | 685 | B23 | apps/web/app/components/SiteNav.tsx | site-nav.test.tsx | üretim turu bekliyor (Karar 0) | no | start_url /core; orada da altı bağlantı, sessiz varyantla |
| 717 | Dark fullscreen Living Core | Çalışıyor | Aynı | DONE | PR | P1 | — | — | apps/web/ | web testleri | üretim | no | Rewrite gerekmez |
| 718 | Gold/amber visual identity | Çalışıyor | Aynı | DONE | PA | P1 | — | — | apps/web/ | web testleri | — | no | — |
| 719 | State-driven animation | Çalışıyor | Aynı | DONE | PA | P1 | — | — | apps/web/ | web testleri | — | no | — |
| 720 | Minimal mode | Kısmi | Tam | DONE | PA | P1 | — | B23 | apps/web/app/core/page.tsx, app/globals.css | site-nav.test.tsx, stage.test.ts | üretim turu bekliyor (Karar 0) | no | nav da kontrol kümesiyle birlikte soluyor; kaybolmuyor |
| 721 | Cockpit mode | Çalışıyor | Aynı | DONE | PA | P1 | — | — | apps/web/ | web testleri | — | no | — |
| 722 | Performance levels | Kısmi | Tam | DONE | PA | P1 | — | B23 | apps/web/app/lib/uistate/quality.ts:degradedTier, useFrameHealth.ts | quality-adaptation.test.ts | üretim turu bekliyor (Karar 0) | no | kademeler ölçüme uyuyor; yalnız AŞAĞI, tek adım, örneklem şartıyla |
| 723 | WebGL fallback | Kısmi | Tam | DONE | PA | P1 | — | B23 | apps/web/app/core/CoreCanvas.tsx, CoreView.tsx | quality-adaptation.test.ts | üretim turu bekliyor (Karar 0) | no | bağlam SONRADAN kaybolursa da 2B'ye geçer; preventDefault ile geri dönebilir |
| 724 | Accessibility | Atlama bağlantısı, odak halkası, landmark | Erişilebilir | DONE | PA | P1 | 685 | B25 | apps/web/app/layout.tsx, app/globals.css, app/core/page.tsx, app/core/cockpit/page.tsx | accessibility.test.ts (9) | üretim turu bekliyor (Karar 0) | no | Test ÖNCE kusuru buldu: /core ve /core/cockpit'in hiç `<main>`'i ve `<h1>`'i yoktu — ürünün en yoğun sayfası ekran okuyucuya giriş noktası vermiyordu. Başlık görsel olarak gizli: görünür başlık manifestin reddettiği kabuk olurdu |
| 725 | Keyboard navigation | Paletin tuş modeli saf fonksiyon | Klavye | DONE | PA | P1 | 685 | B25 | apps/web/app/components/CommandPalette.tsx:paletteAction | palette.test.tsx (20) | üretim turu bekliyor (Karar 0) | no | Her tuş her durumda tek yerde karar veriliyor ve orada sınanıyor; bileşen yalnızca olayı bağlıyor. Liste iki uçta da sarıyor; odak açılışta kutuya girip kapanışta geldiği yere dönüyor |

## X. NATURAL LANGUAGE / INTENT ROUTING (726–750)

> Ölçüm: niyet eşleme elle yazılmış 6.558 satırlık deterministik bir tablo. 103 makul Türkçe
> cümlenin **59'u** hiçbir yeteneğe yönlenmiyor; **7'si yanlış** yeteneğe yönleniyor.

| ID | FEATURE | CURRENT_STATUS | TARGET_STATUS | IMPL | PROOF | PRI | DEPS | BATCH | SOURCE_REFERENCES | TEST_REFERENCES | RUNTIME_PROOF | OWNER_ACTION | NOTES |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 726 | "Saat kaç?" | Yönlenir (B15 clock_query; ölçüm 2026-09-14) | Yönlenir | DONE | PA | P1 | — | B27 | app/voice/intents.py:_clock_match, tools.py:clock_now | test_intent_daily_coverage.py (82), test_daily_intent_tools.py (24), korpus d.* (98) | üretim turu bekliyor (Karar 0) | no | Denetim "yönlenmiyor" demişti; B15 (271) o günden sonra kapatmış. B27 ölçtü, dört biçim `clock.now`a ulaşıyor; 49 cümlelik günlük küme: 32 yönlenmeyen → 0 |
| 727 | "Bunu hatırla" | Yönlenir (B16 memory_remember) | Yönlenir | DONE | PA | P1 | 31 | B27 | app/voice/intents.py:_memory_match | test_intent_daily_coverage.py (82), test_daily_intent_tools.py (24), korpus d.* (98) | üretim turu bekliyor (Karar 0) | no | 35 ile aynı iş — B16 kapattı, B27 kümede ölçtü (4 biçim) |
| 728 | "Bunu unut" | Yönlenir (B16 memory_forget) | Yönlenir | DONE | PA | P1 | 36 | B27 | app/voice/intents.py:_memory_match | test_intent_daily_coverage.py (82), test_daily_intent_tools.py (24), korpus d.* (98) | üretim turu bekliyor (Karar 0) | no | 36 ile aynı iş — B16 kapattı, B27 kümede ölçtü (4 biçim); `unutma` hâlâ HATIRLA |
| 729 | "Maillerime bak" | Mail ismi + bak/göster/kontrol/"var mı" → mail_inbox | Yönlenir | DONE | PA | P1 | 338 | B27 | app/voice/intents.py:_mail_inbox_match, _MAIL_LOOK_VERB_FORMS | test_intent_daily_coverage.py (82), test_daily_intent_tools.py (24), korpus d.* (98) | üretim turu bekliyor (Karar 0) | no | Eşleştirici kutu İSMİNİ ve okunmamış SIFATINI biliyordu, "bak" fiilini değil. Yol üstünde: "Yeni mail var mı?" TASLAK açıyordu (soru asla yazmaz). Sağlayıcı yokken araç dürüst: "Tanımlı bir posta hesabı yok" |
| 730 | "Bu hafta ne var" | Gün/hafta çıpası + "ne var" → calendar_agenda; araç hafta aralığı | Yönlenir | DONE | PA | P1 | 351 | B27 | app/voice/intents.py:_calendar_agenda_match, _agenda_span; tools_calendar.py:_range_bounds | test_intent_daily_coverage.py (82), test_daily_intent_tools.py (24), korpus d.* (98) | üretim turu bekliyor (Karar 0) | no | Takvim ismi artık şart değil; gün ya da hafta sözcüğü yeter. "Günaydın, bugün ne var?" BRİFİNG kalır (selam karar verir). Hafta = bugünden pazara; "haftaya" = gelecek pazartesi-pazar |
| 731 | "Toplantıyı iptal et" | Belirtme hâlindeki randevu ismi + iptal/sil → calendar_cancel → `calendar.cancel` | Yönlenir | DONE | PA | P1 | 354 | B27 | app/voice/intents.py:_calendar_cancel_match; tools_calendar.py:calendar_cancel; app/calendar/service.py:cancel_event | test_intent_daily_coverage.py (82), test_daily_intent_tools.py (24), korpus d.* (98) | üretim turu bekliyor (Karar 0) | no | Yazıcının silme yetkisi YOK (spec §1) — araç dürüst RED makbuzu döner (`deletion_not_permitted`), hangi etkinlik olduğunu adlandırır; odak yoksa "Hangi etkinlik?". Politika B46'nın (354). Yalnız belirtme hâli: "toplantı notlarını sil" toplantı değil |
| 732 | "Araştırmayı iptal et" | Araştırma ismi + iptal/durdur/bırak/vazgeç → research_cancel → `research.cancel` | Yönlenir | DONE | PA | P1 | 202 | B27 | app/voice/intents.py:_research_cancel_match; tools.py:research_cancel; app/research/service.py:active_research, mark_research_cancelled | test_intent_daily_coverage.py (82), test_daily_intent_tools.py (24), korpus d.* (98) | üretim turu bekliyor (Karar 0) | no | REST iptal M13'ten beri vardı, sesi yoktu. Kalıcı yarı (run→cancelled, task kapanır) REST ile AYNI fonksiyon; Temporal yarısı followup. Süren araştırma yoksa red makbuzu. "iptal etme" (olumsuz) yönlenmez |
| 733 | "Sesini kıs" | Ses ismi + kıs/aç/kapat, "sessize al" → media_volume → `media.volume` | Yönlenir | DONE | PA | P1 | — | B27 | app/voice/intents.py:_media_volume_match; tools_media.py:media_volume; app/media/playback_service.py:set_volume | test_intent_daily_coverage.py (82), test_daily_intent_tools.py (24), korpus d.* (98) | üretim turu bekliyor (Karar 0) | no | Cihaz `browser.media_volume`ü M18.3'ten beri alarm için yapıyordu; sahibin ulaşabildiği araç yoktu. Seviye önce SAYFADAN okunur (media_status.volume), sonra bir adım (0.25) oynatılır; yön sahibin fiilinden. Çalan bir şey yoksa dürüst red |
| 734 | "Neler yapabilirsin" | İkinci tekil "yapabilirsin/olabilirsin" biçimleri → capabilities_query → `assistant.capabilities` | Yönlenir | DONE | PA | P1 | 701 | B27 | app/voice/intents.py:_capabilities_query_match, _capability_family; tools_assistant.py | test_intent_daily_coverage.py (82), test_daily_intent_tools.py (24), korpus d.* (98) | üretim turu bekliyor (Karar 0) | no | 701'in yönlendirme yarısı. Sahibin adlandırdığı alan ("mail konusunda…") niyetle taşınır, araç modelin argümanına tercih eder. "Yetenek durumu ne?" M24'ün kalır; "Bunu yapabilir misin?" liste değil |
| 735 | "Ekran görüntüsü al" | Ekran ismi + görüntü/foto + al/çek, "screenshot al" → screenshot_capture → `operator.screenshot` | Yönlenir | DONE | PA | P1 | 104 | B27 | app/voice/intents.py:_screenshot_match; tools_operator.py:operator_screenshot (device `screen.capture`) | test_intent_daily_coverage.py (82), test_daily_intent_tools.py (24), korpus d.* (98) | üretim turu bekliyor (Karar 0) | no | 104'ün İLK çağıranı. Görüntü nesne deposuna yazılır, makbuz yalnız boyut+sha256+anahtar taşır (defterde base64 yok). Yetenek bildirmeyen cihaz kayıt defterinin kendi cevabıyla reddedilir |
| 736 | "Dosyayı gönder" | Onay ÇIPLAK olmak zorunda | Yanlış yönlenmez | DONE | PA | P0 | — | B26 | app/voice/intents.py:_is_bare, _mail_send_match | test_intent_misroutes.py (46), test_route_telemetry.py (16) | üretim turu bekliyor (Karar 0) | no | Eşleştirici yalnız mail ismini reddediyordu; DİĞER her isim geçiyordu. Bu ailede geri alınamayan tek eylem |
| 737 | "Bunu yazdır" | `yazdır` artık `yaz` değil | Yanlış yönlenmez | DONE | PA | P0 | — | B26 | app/voice/intents.py:_write_verb, _WRITE_VERB_RE | test_intent_misroutes.py (46), test_route_telemetry.py (16) | üretim turu bekliyor (Karar 0) | no | Türkçe ek üretir: `yazdır` ettirgen = BASTIR. Ön ek eşleşmesi odaktaki pencereye yazıyordu. Kapalı liste + aynı sözcüklerle lookahead (iki yarı ayrışamaz) |
| 738 | "Otomatik güncellemeleri kapat" | `otomatik` ekran ister | Yanlış yönlenmez | DONE | PA | P0 | — | B26 | app/voice/intents.py:_ambient_policy_match, ambient_policy_changes | test_intent_misroutes.py (46), test_route_telemetry.py (16) | üretim turu bekliyor (Karar 0) | no | Kapı "ekran yok VE otomatik yok → bizim değil" diyordu: çıplak sözcük ikinci bir kapıydı. Güncelleme, yedekleme ve otomatik kaydetme hepsi oradan geçti. Yazan yarı da daraltıldı |
| 739 | Wrong-route negatives | 107 cümle, her biri NE OLMAMALI ile | Negatif külliyat | DONE | PA | P0 | 736 | B26 | services/api/tests/voice_corpus/routing.py | test_intent_misroutes.py (46), test_route_telemetry.py (16) | üretim turu bekliyor (Karar 0) | no | Her vaka iki alan taşıyor: nereye gitmeli VE nereye ASLA gitmemeli. `expected=None` olan (B27'nin onu) yine de eyleme ulaşırsa düşüyor. Ölçüm 103'tü; küme yalnız büyüyebilir |
| 740 | Semantic model router | Yok | Model yönlendirici | MISSING | NYP | P2 | 741 | B51 | app/voice/intent/ | — | — | model bütçesi | Kritik fiiller hariç |
| 741 | Deterministic safety router | Kalkan; dört daraltma, 20 koruma cümlesi | Güvenlik kalkanı rolü | DONE | PA | P0 | — | B26 | app/voice/intents.py | test_intent_misroutes.py (46), test_route_telemetry.py (16), test_voice_intents.py, test_owner_utterance_corpus.py (2063) | üretim turu bekliyor (Karar 0) | no | Her düzeltme bir DARALTMA — çalışan cümleyi yanında götürmeye en yatkın değişiklik. Her biri aynı koşuda dokunmaması gereken cümlelerle eşleniyor |
| 742 | Tool registry-driven routing | Yok | Araç kaydından | MISSING | NYP | P2 | 740 | B51 | app/voice/tools/ | — | 117 aracın 28'i hiç kullanılmamış | no | 701 ile ortak |
| 743 | Intent confidence | Yok | Güven skoru | MISSING | NYP | P2 | 740 | B51 | app/voice/intent/ | — | — | no | — |
| 744 | Ambiguity clarification | Yok | Soru sorar | MISSING | NYP | P2 | 743 | B51 | app/voice/intent/ | — | — | no | — |
| 745 | Reference resolution | Yok | "bunu/şunu" çözülür | MISSING | NYP | P2 | 49 | B51 | app/voice/intent/, app/memory/ | — | — | no | 49 ile aynı iş |
| 746 | Turkish paraphrases | Kısmi | Geniş | PARTIAL | PA | P2 | — | B51 | app/voice/intent/ | intent testleri | 59/103 yönlenmiyor | no | — |
| 747 | ASR-noise variants | Yok | Dayanıklı | MISSING | NYP | P2 | 746 | B51 | app/voice/intent/ | — | — | no | — |
| 748 | Routing corpus expansion | Kısmi | Sürekli büyür | PARTIAL | PA | P2 | 739 | B51 | app/voice/intent/ | intent testleri | 103 cümlelik set | no | — |
| 749 | Route telemetry | Her çözümleme kayıtlı, sözler DEĞİL | Telemetri | DONE | PA | P0 | — | B26 | app/voice/route_telemetry.py, app/voice/realtime_sessions/service.py | test_route_telemetry.py (16) | üretim turu bekliyor (Karar 0) | no | Niyet, eşleşen kural, yönlendi mi. Deşifre YOK: sistemin işlediği en özel şey ve dedektörün ona ihtiyacı yok. Tek istisna sekiz sözcüklük kapalı tepki kümesi |
| 750 | Misroute auto-detection | Sahibin İTİRAZI sinyaldir | Otomatik tespit | DONE | PA | P0 | 749 | B26 | app/voice/route_telemetry.py:candidates, observe | test_route_telemetry.py (16) | üretim turu bekliyor (Karar 0) | no | Yönlendirici kendi hatasını göremez — görebilseydi yapmazdı. Görebildiği şey sahibin SONRA ne yaptığı: eylemli niyetten 12 sn içinde dur/vazgeç/hayır. ADAY dedektörü: rapor eder, yol değiştirmez. Bir itiraz bir eylemi adlandırır |

---

## KAPSAM DIŞI (hiçbir batch'e giremez)

| KONU | STATUS | GEREKÇE |
|---|---|---|
| Multi-device / M29 | `DEFERRED` | Sahip direktifi 2026-09-12 — v1.0 kapsamı dışı |
| SaaS / multi-tenant | `NOT_APPLICABLE` | `PROJECT_CONSTITUTION.md`: tek sahip |
| Otonom üretim dağıtımı | `NOT_APPLICABLE` | Anayasa: "model üretim kaynağını düzenleyip yeniden başlatır" yasak (bkz. 624) |
| Kendini iyileştirme (selfhealing) üretime alınması | `DEFERRED` | Hedef servis üretim imajında yok; `PROVEN_PROXY` kalır |
| CAPTCHA / DRM aşma | `NOT_APPLICABLE` | Ürün ilkesi (681, 682) |

---

## KAPANIŞ KAYDI ŞABLONU

Her `DONE` satırı kapanışta şu bloğu kazanır (batch raporunda ve bu dosyada):

```
<ID>   status : DONE
       commit : <sha>
       tests  : <dosya::test>, <dosya::test>
       proof  : PROVEN_REAL | PROVEN_AUTOMATED | PROVEN_PROXY — <kanıt dosyası / çalışma zamanı ölçümü>
       date   : YYYY-MM-DD
```

**Kapatma kuralları:**
- `PARTIAL` yalnızca test bulunduğu için `DONE` olmaz.
- Sahte, gerçek wire shape ile uyuşmuyorsa satır `DONE` olmaz (`PROVEN_PROXY` kalır, vekilin sınırı yazılır).
- Erişilebilir yüzeyi olmayan özellik `DONE` olmaz.
- Başarısızlığını dürüstçe bildiremeyen özellik `DONE` olmaz.
- Geri yüklemesi kanıtlanmamış yedek `PROVEN_REAL` olmaz.
- Bağımsız artefakt incelemesi olmayan derleme `VERIFIED` olmaz.
- Postcondition doğrulanmamış operatör eylemi başarılı sayılmaz.

---

## KAPANIŞ KAYITLARI

### B01 — Sürüm güvenliği ve sürüm gerçeği · 2026-09-12

```
1    status : DONE
     commit : 15df7f3
     tests  : scripts/tests/cloud-release-bluegreen.tests.ps1 (a failed migration stops the
              release and says what alembic said; a failed image build stops it before any
              migration), scripts/tests/cloud-release.tests.ps1 (aynı iki vaka tek renkli yolda),
              services/api/tests/unit/test_release_health_contract.py::test_neither_release_script_can_lose_a_failure_in_a_pipe_again
     proof  : PROVEN_AUTOMATED — altı vakanın hepsi düzeltilmemiş betiğe karşı ÖNCE kırmızı
              kanıtlandı (49 geçen / 6 başarısız), sonra yeşile döndü (59/59, 38/38)
     date   : 2026-09-12

2    status : DONE
     commit : 15df7f3
     tests  : services/api/tests/unit/test_release_schema.py (7), test_release_health_contract.py,
              scripts/tests/cloud-release*.tests.ps1 (exit 83 + "serves no schema check" uyarısı)
     proof  : PROVEN_REAL — docs/evidence/b01-schema-gate-2026-09-12.json: gerçek PostgreSQL'de
              head'de `ok`, bir revizyon geride `fail` (iki revizyon da adıyla), satır geri
              yüklenip yeniden okundu
     date   : 2026-09-12

20   status : DONE
     commit : 15df7f3
     tests  : test_health_endpoint.py::test_an_advisory_check_is_one_nothing_in_the_app_depends_on
     proof  : PROVEN_AUTOMATED — `ADVISORY_CHECKS` + app/ içinde redis istemcisi arayan test
     date   : 2026-09-12
     note   : B01'de yol üstünde ölçüldü; 2026-09-11'de zaten çözülmüştü, matris yanlış
              sınıflandırmıştı. Düzeltildi, B07'den düşürüldü.

21   status : DONE
22   status : DONE
     commit : 15df7f3
     tests  : services/api/tests/unit/test_release_build_identity.py (7; sonuncusu cihazın
              ProtocolConstants.cs kaynağını okuyup iki yarının aynı şekli kullandığını pinliyor)
     proof  : PROVEN_REAL — gerçek ağaçta build_id 516452ef2d144269 (16 hex), aynı bayt aynı
              kimlik, tek bayt farkı farklı kimlik, __pycache__ etkisiz
     date   : 2026-09-12

23   status : DONE
     commit : 15df7f3
     tests  : services/api/tests/unit/test_build_state_reconciled.py (13)
     proof  : PROVEN_AUTOMATED — state/BUILD_STATE.json bu commit'te türetilen kayıtla mutabık;
              üç çelişkinin üçü de kapandı (last_completed M27→M28_NATIVE_APP_FACTORY,
              milestones bloğu, M28 = ENGINEERING CLOSED çünkü 26.17 PROVIDER_UNAVAILABLE)
     date   : 2026-09-12

24   status : DONE
     commit : 15df7f3
     tests  : services/api/tests/unit/test_qualification_evidence.py (5; biri kuralın kendisini
              yanlışlar: düz yazı reddedilir, dört referans türü kabul edilir)
     proof  : PROVEN_AUTOMATED — 272 kanıt işaretli satır tarandı, 16'sı tarihi borç olarak
              adıyla listelendi; liste yalnızca küçülebilir
     date   : 2026-09-12

631  status : DONE
632  status : DONE
     commit : 15df7f3
     tests  : scripts/tests/cloud-release-bluegreen.tests.ps1 (RELEASE.json'ın alanları;
              tamamlanmamış bir sürümün metadata YAZMADIĞI)
     proof  : PROVEN_AUTOMATED — sürüm, reconcile ve rollback yollarının üçü de yazıyor
     date   : 2026-09-12

638  status : DONE
     commit : 15df7f3
     tests  : scripts/tests/cloud-release*.tests.ps1 (rc 74/67 + zaman aşımı vakaları)
     proof  : PROVEN_AUTOMATED — değişmedi; pipefail altında gözden geçirildi
     date   : 2026-09-12
```

### B03 — Gerçek/sahte sözleşme eşitliği ve dört canlı kusur · 2026-09-12

```
3, 147, 168   status : DONE
     commit : 9ddf243
     tests  : services/api/tests/unit/test_file_search_roots_contract.py (29),
              test_documents_confinement.py (15),
              devices/.../Documents/FileSearchRootsContractTests.cs (9)
     proof  : PROVEN_AUTOMATED — cihaz düzeltmesi geri alınarak KIRMIZI kanıtlandı
              (3 başarısız / 6 geçen), dosya sha256 ile geri yüklendi
     date   : 2026-09-12
     note   : Kök neden hiç yazılmamış bir sözleşmeydi. Bulut kova adı (hatta sahibin
              "Masaüstü" kelimesi) gönderiyordu; cihaz mutlak yol istiyordu. Artık
              packages/protocol/file-search-roots.json var: cihaz kova adını KENDİ çözüyor
              (sahibin klasörünü yalnız o bilebilir) ve tam olarak mutlak yolu sınırladığı
              gibi sınırlıyor — kova kök listesinin etrafından dolaşamıyor.

4, 417-421    status : DONE
     commit : 9ddf243
     tests  : test_app_manifest_contract.py (17), AppManifestContractTests.cs (5),
              test_appfactory_generator.py, test_appfactory_validation.py
     proof  : PROVEN_AUTOMATED — üç şablon da gerçek ProjectScaffold'dan geçiyor;
              `{port}`, eksik `run` ve eksik `port` şekilleri ayrı ayrı reddediliyor
     date   : 2026-09-12
     note   : Üçüncü sebep cihazın da eksiğiydi: `port: 0` ("hiçbir şeye bağlanmıyorum")
              3B'nin kelimesiydi ve bir web projesi onu söyleyemiyordu, yani CLI aracının
              gönderebileceği DOĞRU bir manifest yoktu. Artık var ve portsuz proje batch
              olarak çalıştırılıyor. Bulut doğrulayıcısı da artık cihazdan nazik değil.

5             status : DONE
     commit : 9ddf243
     tests  : test_device_fakes_match_the_device.py (5), test_contract_falsification.py (26)
     proof  : PROVEN_AUTOMATED — sahtenin anahtarları CİHAZIN kendi kaynağından okunuyor;
              eksik veya uydurulmuş alan testte düşüyor
     date   : 2026-09-12
     note   : Üç ayrı sahte `counts_parsed` (ve biri `duration_ms`) düşürüyordu. Bu, bu
              deponun dördüncü "sahte makineden nazik" vakası; artık mekanik olarak yakalanıyor.

467           status : DONE
     commit : 9ddf243
     tests  : test_nativefactory_device_build.py (20; üçü yeni regresyon)
     proof  : PROVEN_AUTOMATED — sayılamayan bir test koşusu `tests_unreadable` ile duruyor
              ve publish'e hiç geçmiyor
     date   : 2026-09-12
     note   : Cihaz `counts_parsed: false` diyordu, bulut okumuyordu; üretimdeki 26.16
              `passed: null, failed: null` ile "verified" damgalanmıştı. `exit_code: None`
              de artık geçer not değil.

505           status : DONE
     commit : 9ddf243
     tests  : test_creative_tools.py::..._reaches_a_tool_that_can_work
     proof  : PROVEN_AUTOMATED — tool adlandırılmadığında Paint'e gidiyor ve iş yapıyor
     date   : 2026-09-12
     note   : Varsayılan Figma'ydı ve `FigmaProvider.token_present` hiçbir şeyin set etmediği
              sabit `False`. Adıyla Figma isteyen hâlâ dürüst reddi alıyor — kasıtlı.

510, 715      status : DONE
     commit : 9ddf243
     tests  : test_web_asks_for_routes_that_exist.py (12)
     proof  : PROVEN_AUTOMATED — Cockpit'in bildirdiği 10 yolun hepsi API'nin OpenAPI
              belgesinde var; mekanizma da pinlendi (parametreden sonra bildirilen literal)
     date   : 2026-09-12
     note   : `/{run_id}` "runs" segmentini yutuyordu: UUID değil → kalıcı 422. Bekçi
              METODU henüz kontrol etmiyor; bu sınır testin kendi docstring'inde yazılı.

B02'DEN DEVREDEN  status : DONE
     tests  : devices/.../Protocol/DeviceProtocolSchemaContractTests.cs (4)
     proof  : PROVEN_AUTOMATED — HelloMessage'ın wire adları şemanın kendi `$defs.hello`
              tanımıyla karşılaştırılıyor; ADR-0118'in eklediği build_id pinlendi
     date   : 2026-09-12
     note   : device-protocol.schema.json kendini "authoritative" ilan ediyordu ve C# yarısı
              ona hiç tutulmuyordu. Artık tutuluyor; sözleşme kaydında `unheld` notu kalktı.
```

**B03'te yol üstünde bulunan ve aynı batch'te kapatılan kusur:** Python'un `"İndirilenler".lower()`
sonucu `i` + U+0307 (birleşik nokta) — hiçbir alias anahtarına uymuyor. Yani sahibin Türkçe
klavyeyle yazdığı büyük **İ**'li klasör adı "tanımıyorum" ile reddediliyordu. `_fold` artık
İ/I/ı/i̇ biçimlerini tek harfe indiriyor; kapalı bir klasör kelime dağarcığında nokta konusunda
hoşgörülü olmak doğru, yanlış ret değil. (`tr-TR` birinci sınıf — `CLAUDE.md`.)

### B02 — CI kapsamı ve yanlışlama kapısı · 2026-09-12

```
25   status : DONE
     commit : 8ca23ac
     tests  : services/api/tests/unit/test_ci_covers_every_suite.py (7)
     proof  : PROVEN_AUTOMATED — var olan bir paketi adlandırmayan workflow testte düşüyor;
              kural kendi üzerinde yanlışlandı (sürüklenmiş bir liste fikstürüne karşı)
     date   : 2026-09-12

26   status : DONE
27   status : DONE
     commit : 8ca23ac
     tests  : apps/web vitest (82 dosya / 1587 test), oxlint, tsc --noEmit
     proof  : PROVEN_AUTOMATED — üçü de CI'a eklendi ve yerelde pnpm ile tam olarak CI'ın
              çağırdığı şekilde yeşil koştu (1587/1587, lint exit 0, tsc temiz)
     date   : 2026-09-12
     note   : `apps/web` bir `typecheck` betiği kazandı: `next build` uygulamayı tipliyor
              ama TESTLERİ tiplemiyordu — sözleşme tiplerinin asıl kullanıldığı yer orası.

28   status : DONE
     commit : 8ca23ac
     tests  : devices/.../Documents/PeImageReaderTests.cs (7 Fact)
     proof  : PROVEN_REAL — CI run 34703755179 "Windows agent build + tests" job logu:
              `PagentOS.Agent.Tests.dll` Passed 863 / Skipped 6 / Total 869
     date   : 2026-09-12
     note   : B02'de ölçüldü. Denetim "CI'da atlanıyor" demişti; ölçüm aksini gösterdi —
              testler .sln içinde ve `dotnet test` onları koşuyor. Matris düzeltildi.

29   status : DONE
     commit : 8ca23ac
     tests  : test_ci_covers_every_suite.py::test_ci_runs_every_powershell_suite
     proof  : PROVEN_AUTOMATED — 24/26 idi, 26/26 oldu. Eksik ikiden biri
              cloud-release-bluegreen.tests.ps1: 59 vaka, üretimin kullandığı sürüm yolu
     date   : 2026-09-12

30   status : DONE
     commit : 8ca23ac
     tests  : services/api/tests/unit/test_contract_falsification.py (11)
     proof  : PROVEN_AUTOMATED — mutasyon GERÇEKTEN yürütülüyor: paylaşılan sözleşme
              gizleniyor, bekçisi alt süreçte koşuluyor, DÜŞTÜĞÜ doğrulanıyor, dosya
              sha256 ile geri yükleniyor, sonra bekçinin yeniden geçtiği görülüyor
     date   : 2026-09-12
     note   : Yol üstünde bir kaçamak kapandı: test_injection'ın sözleşme dosyası yoksa
              SKIP eden karşılaştırması. Sözleşmeyi silmek bekçisini yeşile çeviriyordu —
              bu deponun iki kez ödediği şekil. Artık dosya zorunlu.
```

**B01'de yol üstünde bulunan ve aynı batch'te kapatılan kusur (batch dışı değil, batch'in
kendi CI kapısı):** `test_briefing_announcer.py`'nin altı vakası 2026-09-11 09:00 tarihli satır
kuruyor ama süpürmeyi GERÇEK saatle yapıyordu; `pending` 24 saatlik süresi geçmiş satırı
düşürdüğü için suite tam olarak o pencere kapanana kadar yeşildi ve **yeşil bir CI koşusundan
dokuz dakika sonra** altı yerden birden kırmızıya döndü. `sweep_once(now)` artık tek saatle
karar veriyor ve `test_one_pass_is_decided_by_one_clock` bunu pinliyor. Bu, bu deponun en sık
tekrar eden hata şekli ("bir karar, iki saat") ve B01'in CI kapısını bloklayan tek şeydi.

---

### B06 — Durum gerçeği ve süpürgeler · 2026-09-12

CI 34712304047 yeşil (7/7) · commit 41eb2c3 · yerel kapı 8979 geçti / 5 atlandı ·
kanıt `docs/evidence/b06-state-truth-2026-09-12.json`

```
9, 219, 220   status : DONE
     commit : 41eb2c3
     tests  : services/api/tests/unit/test_orphan_sweeps.py (6 sesli oturum testi)
     proof  : PROVEN_AUTOMATED — deterministik saatle: 3 gün önce kapanmış sekme EXPIRED
              oluyor, 5 dakika önce konuşulan oturuma dokunulmuyor, bir hafta önce açılıp
              bir dakika önce kullanılan oturum yaşıyor
     date   : 2026-09-12
     note   : Kaynak kodun kendi yorumu "nothing sweeps in the background" diyordu ve doğruydu:
              bir oturumu yalnızca ONU KULLANAN kapatabiliyordu, yani kapatılan sekme sonsuza
              kadar `active` kalıyordu. Süpürge YAŞ değil ATILLIK ölçüyor — sabit ufuk her web
              oturumunu tam bir saatte cümlenin ortasında öldürüyordu (ADR-0105, sahip
              direktifi "hiç kapanmasın"), canlı konuşma her olayda `updated_at` damgaladığı
              için ona hiç ulaşılmıyor. 220 bu yüzden mutlak ömür DEĞİL: 12 saat atıllık.
              Hiçbir satır silinmiyor; EXPIRED'a taşınıyor ve denetim satırı `reason: idle`
              yazıyor.

10, 13, 206, 11   status : DONE
     commit : 41eb2c3
     tests  : test_orphan_sweeps.py (7 araştırma testi)
     proof  : PROVEN_AUTOMATED — iş akışı kaybolmuş koşu 24 saat sonra görev
              FAILED_TERMINAL + koşu STAGE_FAILED oluyor; 30 dakika önce ilerlemiş koşuya
              dokunulmuyor; zaten terminal olan görev ikinci kez geçirilmiyor
     date   : 2026-09-12
     note   : `fail_unstarted_research` hiç başlamayan koşuyu kapatıyordu; başlayıp terk
              edileni kimse kapatmıyordu — üretimde 2026-09-09'dan beri `discovering`.
              Süpürge satırı kendisi yazmıyor, `runs_service.update_run` üzerinden geçiyor:
              aşama geçişi, olay günlüğü ve arayüz durumu tek yoldan olsun diye. Sınır 24
              saat, çünkü etkileşimli aşama SAHİBİN bir sayfayı temizlemesini bekliyor
              olabilir.

67, 68        status : DONE
     commit : 41eb2c3
     tests  : test_task_status_meaning.py (5), test_worldmodel.py (yeni 3 regresyon)
     proof  : PROVEN_AUTOMATED — dört anlam kovası tüm görev kelime dağarcığını tam olarak
              bir kez kaplıyor; sonradan eklenen bir durum kovasız kalırsa test düşüyor
     date   : 2026-09-12
     note   : Dünya modeli "kaç iş çalışıyor?" sorusunu "kaçı terminal değil?" diye
              yanıtlıyordu; üretim hiçbir şey çalışmazken on iş çalışıyor diyordu — onu
              sahibe söylenmeyi bekleyen BİTMİŞ araştırmalardı. İki farklı sorunun tek
              yanıtı vardı. READY artık `awaiting_owner`, `running` değil.

69            status : DONE
     commit : 41eb2c3
     tests  : test_worldmodel.py::..._stuck
     proof  : PROVEN_AUTOMATED — 24 saatten uzun süredir ilerlemeyen iş `tasks.stuck_count`
              ile RUNTIME gerçeği olarak görünür
     date   : 2026-09-12
     note   : Dünya modeli tespit eder ve söyler; taşıyan süpürgedir (ayrı yetki).

70            status : DONE
     commit : 41eb2c3
     tests  : test_ledger_service.py (4 yeni), test_voice_realtime_sessions.py::
              test_a_session_is_one_ledger_row_per_state_after_the_backfill_runs
     proof  : PROVEN_AUTOMATED — düzeltme geri alınarak KIRMIZI kanıtlandı: gerçek HTTP
              yüzeyinden tek oturum (oluştur + üç kez bağlan + kapat) backfill'de 5 kopya
              satır daha üretiyordu (3 canlı + 5 backfill = 8 satır, 3 olması gereken yerde)
     date   : 2026-09-12
     note   : Araştırmada M16'dan beri doğal anahtar koruması vardı, seste yoktu:
              `(source, source_ref)` tekilliği `live` ile `backfill:audit_events`'in AYNI
              olguyu anlattığını göremez. Artık iki yarı tek tanımı okuyor —
              `ledger_service.voice_session_source_ref` anahtarı üretiyor, canlı yazar onu
              kendi `source_ref`'i olarak yazıyor, backfill aynı fonksiyonla soruyor. Kopya
              engelleme (oturum, durum) başına: bağlanma tekrarlanabilir ve canlı yazar
              hepsini tek satıra indiriyor, backfill de en eski denetim satırını alarak aynı
              şeyi söylüyor. Canlı yazar yokken oluşmuş eski oturum hâlâ backfill ediliyor.
```

**B06'da yol üstünde bulunan ve aynı batch'te kapatılan kusur:** `_collect_tasks`'ın ilk hâli
görev sayımını GERÇEK saatten, anlık görüntünün geri kalanını `assemble_snapshot(now=...)`
saatinden okuyordu — bu deponun en sık tekrar eden hata şekli ("bir karar, iki saat"), aynı gün
altıncı örneği. `now` artık tek yerden geçiyor ve regresyon testi bunu pinliyor.

**İkinci kusur, yine kendi kodumda:** süpürge denetim satırına `idle_since` yazarken
`updated_at`'i damgaladıktan SONRA okuyordu — yani her satır "oturum tam süpürüldüğü anda
atıl oldu" diyordu, ki bu satırın zaten bildiği tek şey. Düzeltildi;
`test_the_audit_row_says_when_the_session_went_quiet` tutuyor.

**İki bekçi işini yaptı:** `test_maintenance.py` ve `/v1/system/health`'in şekil testi süpürge
kümesini İSİMLERİYLE pinliyordu, iki yeni süpürge ikisini de kırdı. Gevşetilmedi, kasıtlı olarak
genişletildi — 2026-09-11'deki "üç süpürge var, çağıran yok" kusuru tam olarak bu sayede
görünür olmuştu.

**Sıra sapması, açıkça:** Bu batch roadmap'te **B06**; B04 (sır redaksiyon hattı) ve B05 (yetki
kapısı) henüz yapılmadı. Çalışmaya yanlış numarayla (B04) başladım ve bunu ancak roadmap'i
yeniden okuduğumda fark ettim. İş bitmiş, testleri yeşil ve teknik bağımlılığı yoktu (süpürgeler
yetki kapısına bağlı değil), bu yüzden doğru numarayla kapatıldı. Sıradaki iki batch B04 ve B05.

---

### B04 — Sır redaksiyon hattı · 2026-09-12

CI 34717193365 yeşil (7/7) · commit 25eaede · yerel kapı 8999 geçti / 5 atlandı · kanıt `docs/evidence/b04-secret-redaction-2026-09-12.json`

```
6             status : DONE
     commit : 25eaede
     tests  : test_secret_redaction_surfaces.py (dünya modeli bölümü, 4 test)
     proof  : PROVEN_AUTOMATED — düzeltme yedekten geri alınarak KIRMIZI kanıtlandı
              (dünya modeli + sağlık mutasyonu: 5 başarısız / 12 geçen), dosyalar
              sha256 ile geri yüklendi
     date   : 2026-09-12
     note   : Üretim veritabanı parolası `/v1/world/facts` yanıtında açık metindi. Sahip
              oturumu gerekiyordu — ama bu "güvenli" demek değil: değer Cockpit'e, anlık
              görüntüyü alıntılayan her şeye ve her ekran görüntüsüne gidiyor. Düzeltme
              sızdıran TOPLAYICIYA değil, her toplayıcının yazdığı KAPIYA kondu
              (`_Collector.fact` / `.uncertain`), yani sonradan eklenen bir bölüm bunu
              bedava alıyor. DSN'in yalnızca userinfo'su siliniyor: olgu hâlâ hangi host,
              hangi port, hangi veritabanı olduğunu söylüyor — değerini yok eden redaksiyon
              düzeltme değildir.

7, 683        status : DONE
     commit : 25eaede
     tests  : test_secret_redaction_surfaces.py (log hattı bölümü, 8 test)
     proof  : PROVEN_AUTOMATED — işlemci etkisizleştirilerek KIRMIZI kanıtlandı
              (5 başarısız / 12 geçen); ayrıca zincirdeki KONUMU pinli (renderer'dan
              hemen önce)
     date   : 2026-09-12
     note   : On üç desen M8'den beri vardı ve güvenlik ajanının kendi yazma yollarında
              uygulanıyordu; log satırına uygulayan hiçbir şey yoktu. İki kural, çünkü sır
              iki ayrı şekilde geliyor: adı "kimlik bilgisi" anlamına gelen ANAHTAR (hiçbir
              desen `hunter2`'yi tanıyamaz — ne olduğunu yalnızca anahtar söyler) ve desenle
              taranan her dize. Anahtar kuralı HER DERİNLİKTE uygulanıyor: bir log olayı
              neredeyse her zaman iç içe şekildedir ve yalnız üst seviyeyi taramak,
              `{"body": {"password": ...}}`'ı yakalamakla açık basmak arasındaki farktır.
              Ama yalnız DİZE değerlere: `tokens: 1430` anahtar kelime dağarcığına uyuyor ve
              sır değil — koruduğu gözlemlenebilirliği yiyen redaksiyon kazandığından
              fazlasını götürür. 683 "hiçbir sır loglanmaz" dediği için stdlib `logging`
              hattı da kapsandı: uvicorn erişim satırları ve SQLAlchemy motoru işlemci
              zincirinin tamamen yanından geçiyordu.

8             status : DONE
     commit : 25eaede
     tests  : test_secret_redaction_surfaces.py (sağlık bölümü, 2 test),
              test_health_endpoint.py::test_no_subsystems_check_can_leak_a_secret_...
     proof  : PROVEN_AUTOMATED — sızdıran bir check ile gerçek uçtan sürülerek
     date   : 2026-09-12
     note   : Sömürü basitti: veritabanı düşükken `/v1/system/health`'e herhangi bir şey
              yönelt, üretim kimlik bilgisini yanıt gövdesinden oku — sürücünün bağlantı
              hatası kendisine verilen DSN'i alıntılıyor ve bu uç, SAHİP OTURUMU İSTEMEYEN
              tek uç. Tek alanla yetinilmedi: uç ~18 bağımsız yazılmış `health_check()`
              metodunu topluyor, her birinin yorumu "sır yok" diyor ve hiçbiri denetlenmiyor,
              bu yüzden birleştirilmiş harita bir bütün olarak redakte ediliyor.

668           status : DONE
     commit : 25eaede
     tests  : test_secret_redaction_surfaces.py::test_all_thirteen_patterns_are_the_ones_...
     proof  : PROVEN_AUTOMATED — üç yüzey de AYNI SECRET_PATTERNS'a ulaşıyor; testi
              `app.memory.policy`'nin listesini okuyor, yeniden yazmıyor
     date   : 2026-09-12
     note   : Üç yüzey, dört kelime dağarcığı olmasın diye: desenler `app.memory.policy`'den
              (M8'in kendi listesi onları zaten oradan alıyor), anahtar adları
              `app.research.forbidden_keys`'ten — tarayıcı işçisi ve Cloud Core'un zaten
              üzerinde anlaştığı liste. Bu hatta eklenen yeni bir desen üçünde birden çıkar.
```

**B04'te yol üstünde bulunan ve aynı batch'te kapatılan iki boşluk:** (1) `redact_value` iç içe
sözlüklerde anahtar-ADI kuralını uygulamıyor, yalnız deseni uyguluyordu — yani
`{"body": {"password": "hunter2"}}` ilk yazdığım işlemciden açık geçiyordu; log olayları
neredeyse her zaman bu şekilde. (2) structlog, bu sürecin tek yazarı değil: stdlib `logging`
üzerinden yazan uvicorn ve SQLAlchemy işlemci zincirinin yanından geçiyordu. Filtre
handler'lara takıldı, logger'a değil — `logging`'de bir logger'daki filtre, alt logger'dan
yukarı propagate eden kayıtlar için sorulmaz ve bu kayıtlar tam olarak onlar.

**Ölçüldü, tahmin edilmedi:** redaksiyonun maliyeti temsili bir alt küme üzerinde
%1'in altında (10,25 s → 10,34 s). İlk tam kapı koşusundaki 694→776 s farkı koşu
değişkenliğiydi; işlemci zincirin içinde/dışındayken aynı süite ayrı ayrı ölçüldü.

---

### B05 — Yetki kapısı ve kimlik sertleştirme · 2026-09-13

CI 34720710232 yeşil (7/7) · commit 0394a83 · yerel kapı 9066 geçti / 5 atlandı · kanıt `docs/evidence/b05-authority-gate-2026-09-13.json`

```
246, 663      status : DONE
     commit : 0394a83
     tests  : test_authority_gate.py (cihaz güveni bölümü, 5 test)
     proof  : PROVEN_AUTOMATED — kırmızı kanıtlandı (11 başarısız / 11 geçen), dosyalar
              sha256 ile geri yüklendi
     date   : 2026-09-13
     note   : `device_trusted` istek GÖVDESİNDEN geliyordu. Alanın kendi yorumu ne olduğunu
              söylüyordu — "UNTRUSTED, client-asserted hint" — ve sınıflandırıcı güvenilmeyen
              cihazı UNCERTAIN'de kapıyordu; yani kural yazılmış ve tam da kısıtladığı tarafa
              teslim edilmişti. Sahip jetonu olan her şey `device_trusted: true` gönderip
              kabul bandını kendi lehine kaydırabiliyordu. Artık oturumun cihaz bağından
              türetiliyor, ve model extra alan kabul etmediği için hâlâ gönderen 422 alıyor:
              kendi güven seviyesini seçtiğini sanan bir istek, sessizce yok sayılmak yerine
              öyle olmadığını öğrenmeli.

658           status : DONE
     commit : 0394a83
     tests  : test_authority_gate.py::test_the_ceiling_is_not_reset_by_refreshing (+kontrol)
     proof  : PROVEN_AUTOMATED — kusuru canlandıran test: beş günde bir yenilenen bir
              istemci seksen beş gün boyunca sorunsuz yenileniyor ve yüzüncü günde bitiyor;
              kontrol testi tavansız aynı dizinin sonsuza kadar yaşadığını gösteriyor
     date   : 2026-09-13
     note   : `refresh` jetonu döndürüp AYNI satırda `expires_at`'i ileri itiyordu ve tavan
              yoktu. Yani bir loga, bir yedeğe ya da bir proxy'e sızmış jeton, onu yenileyen
              bir şey olduğu sürece tam olarak sonsuza kadar yaşıyordu. Tavan `created_at`'ten
              ölçülüyor ve yenileme ona dokunmuyor — tavanı tavan yapan şey bu.

659           status : DONE
     commit : 0394a83
     tests  : test_authority_gate.py (süpürge bölümü, 3 test)
     proof  : PROVEN_AUTOMATED — on gün dokunulmamış oturum süpürülüyor, dün kullanılan
              oturuma dokunulmuyor, ve denetim hangi kuralın bitirdiğini yazıyor
     date   : 2026-09-13
     note   : Atıl kuralı yalnızca `verify` içindeydi, yani terk edilmiş bir istemcinin
              oturumu birileri jetonunu sunana kadar veritabanında `active` kalıyordu — terk
              edilmiş bir istemci için asla. O süre boyunca aktif oturum olarak sayılıyor ve
              listeleniyordu. B06'nın seste kapattığı kusurun aynısı, kimlikte.

675           status : DONE
     commit : 0394a83
     tests  : test_evolution_routes.py (2 yeni: çıkış açık, lab hâlâ giremiyor)
     proof  : PROVEN_AUTOMATED — düzeltme geri alınarak KIRMIZI kanıtlandı: `403 == 200`
     date   : 2026-09-13
     note   : "Üretim tarafı" geçişin özelliği, hedefinin değil. Servis hem üretim tarafına
              GİREN hem ORADAN ÇIKAN geçişi koruyordu; rota yalnız birincisi için yetki
              basıyordu. Sonuç: üretim tarafında park etmiş bir adayı kapatmak
              (`live -> superseded`, `qualifying -> rejected`) her seferinde yetkisiz
              `guard_production_action`'a düşüyor ve reddediliyordu. İzin yazılmış, çıkış
              erişilemezdi — denetimin "izin sayısı 0" ölçümü tam olarak buydu.

678           status : DONE
     commit : 0394a83
     tests  : test_deletion_gate.py (16)
     proof  : PROVEN_AUTOMATED — ses kanalı kalıcı silmeyi onaylayamıyor; onay tek bir
              özneyi adlandırıyor ve beş dakikada bayatlıyor
     date   : 2026-09-13
     note   : SAHİP KARARI (2026-09-13, soruldu): yumuşak silme varsayılan ve geri alınabilir,
              o yüzden sesle tek adımda istenebilir; KALICI yok etme ikinci kanal onayı ister
              — tıklanan ya da yazılan bir şey, asla bir söz. Gerekçe step-up politikasıyla
              aynı: bir söz bu sistemde kazayla üretilmesi en kolay şey (televizyon, misafir,
              yanlış duyulan kelime) ve yok etme, arkasında hatayı geri alacak hiçbir şey
              olmayan tek eylem. `forget_memory` TEK adlandırılmış istisna, gerekçesiyle:
              unutulmak bir gizlilik HAKKI ve sahibi, sistemin onu unutması için önce bir
              yere tıklamaya zorlamak hakkı tersine çevirir.

245, 247, 248, 664, 665, 666   status : PARTIAL (DONE DEĞİL)
     commit : 0394a83
     tests  : test_voice_step_up.py (21)
     proof  : PROVEN_AUTOMATED (mekanizma) — ama erişilebilir uçtan uca akış YOK
     date   : 2026-09-13
     note   : Karar yolu var: `handle_tool_call`'un — her sesli araç çağrısının geçtiği tek
              rölenin — içinde, herhangi bir işleyiciden ÖNCE, tıpkı yanındaki araştırma
              reddi gibi, ki modelin araç seçimi etrafından dolaşamasın. 117 kayıtlı aracın
              hepsi açıkça OPEN/SENSITIVE/CRITICAL olarak sınıflandırıldı ("yoksa OPEN" değil:
              varsayılan, sonradan eklenen bir aracın kimse karar vermeden yönetimsiz kalma
              yoludur) ve bir test bunu gerçek kayıtla iki yönlü karşılaştırıyor.
              EKSİK OLAN: realtime yolda konuşmacı verdict'i ÜRETEN hiçbir akış yok — tek
              yazan, otomatik çağrılmayan bir REST ucu. Bugün enforce etsem her hassas sesli
              işlem reddedilirdi. Bu yüzden DONE değil: "sınıf var" tamamlandı değildir.
     invariant : Güvenilmeyen cihazda hiçbir skor yetmez. Cihaz kontrolü verdict daha
              YÜKLENMEDEN yapılıyor, yani skorun söz alabileceği bir sıralama yok. Güvenilmeyen
              cihazda alınmış bir verdict, cihaz sonradan güvenilir olsa da yeniden
              kullanılamıyor: farklı koşullarda alınmış bir ölçümdü.
```

**Gölge modda ne var, neden:** cihaz komut kapısı (`create_command` içinde, her çağıran için,
çünkü çağıran başına konsa unutulabilirdi) ve ses step-up politikası. İkisi de değerlendirip
kaydediyor ve geçiriyor. Roadmap'in kendi rollback planı bu — üretimdeki ilk görünümü bir
kesinti olan kapı, kapatılan kapıdır. Açmak sahip kararı: cihaz kapısı için üretimde bir
günlük sayım (batch'in kendi REAL_PROOF şartı), step-up için önce verdict üreten akış.

---

### B07 — Sınırlı teslim, döngü izolasyonu, saklama · 2026-09-13

CI 34722166605 yeşil (7/7) · commit 690abd5 · yerel kapı 9096 geçti / 5 atlandı

```
14, 15, 16, 17, 376   status : DONE
     commit : 690abd5
     tests  : test_bounded_delivery.py (25), test_mobile_announcer.py (güncellendi)
     proof  : PROVEN_AUTOMATED — düzeltme dört dosyada geri alınarak KIRMIZI kanıtlandı
              (7 başarısız / 12 geçen), sha256 ile geri yüklendi
     date   : 2026-09-13
     note   : Üç duyurucu, aralarında tek bir eksik fikir: hiçbiri ne yaptığını bilmiyordu.
              En pahalısı brifing kuyruğuydu — konuşulamayan satır EN ÖNCELİKLİ olduğu için
              her turda seçiliyor, `_speak` düşünce hiçbir şeye dokunmadan `return 0`
              deniyordu. Aynı satır bir sonraki turda yine en öncelikli. Arkasındaki her
              bildirim kalıcı olarak, sessizce bloke. Sahibin kayıp bildirimleri oradaydı.
              Push duyurucusu beş saniyede bir yeniden deniyordu: asla cevap vermeyecek bir
              sağlayıcıya günde 17.280 deneme. Üçü artık tek politikayı paylaşıyor, çünkü
              bir yeniden deneme kuralının üç yakın kopyası ayrışır. Karantina = TEK bir
              öğeden vazgeçmek; vazgeçemeyen kuyruğun yeniden deneme politikası değil,
              kilidi vardır.

18            status : DONE
     commit : 690abd5
     tests  : test_bounded_delivery.py (döngü sağlığı bölümü)
     proof  : PROVEN_AUTOMATED — `create_app`'in lifespan'ındaki her `.start()` okunuyor;
              sağlıkta karşılığı olmayan bir döngü testi düşürüyor
     date   : 2026-09-13
     note   : Dokuz döngü başlıyor; beşi görülebiliyordu, dördü hiç görülemiyordu — ve o
              dördü sahibe bildirim TAŞIYAN dörtlüydü, yani sahip olabilecekleri arıza tam
              olarak kimsenin fark etmeyeceği arıza. "Süreç ayakta" ≠ "döngüler çalışıyor",
              ve `task.done()` uyanıp hiçbir şey yapmayan bir döngü için False'tur: kalp
              atışı tamamlanan turu sayıyor, canlılığı değil.

19            status : DONE
     commit : 690abd5
     tests  : test_routines_clock.py (güncellendi), test_bounded_delivery.py
     proof  : PROVEN_AUTOMATED — routines patlarken alarm tiki koşuyor ve arada rollback var
     date   : 2026-09-13
     note   : Tek try/except beş alt tiki sarıyordu: rutin değerlendirmesindeki bir arıza
              ALARM tikinin o turda hiç koşmaması demekti — alarmla hiç ilgisi olmayan bir
              şey yüzünden sessizce düşen bir alarm. Testin kendi yorumu kusuru bir beklenti
              olarak yazmıştı: "The later ticks were skipped by the exception". Ayrıca
              başarısızlıktan sonra rollback: bir sonraki çağırana bozuk bir işlem bırakan
              izolasyon, izolasyon değildir.

375, 390      status : DONE
     commit : 690abd5
     tests  : test_bounded_delivery.py (sahte sağlayıcı bölümü), test_mobile_providers.py
     proof  : PROVEN_AUTOMATED — makbuzsuz `delivered` İNŞA EDİLEMİYOR (ValueError)
     date   : 2026-09-13
     note   : Varsayılan argümanla ihlal edilen bir ürün ilkesi. `PushDelivery.status`
              varsayılanı "delivered"dı ve sahte sağlayıcı onu alıyordu. Artık `delivered`
              satıcının makbuzunu gerektiriyor; sahtenin gösterecek bir satıcısı yok, o
              yüzden kelimeyi söyleyemiyor. Kural değil, tip.

679           status : DONE
     commit : 690abd5
     tests  : test_bounded_delivery.py (saklama bölümü, 6 test)
     proof  : PROVEN_AUTOMATED — kuru koşu sayıyor ve silmiyor; gerçek koşu yalnız süresi
              dolanı siliyor
     date   : 2026-09-13
     note   : 13.560 olay ve hiçbirini silecek bir şey yok. Üç tablo üç farklı şey: cihaz
              trafiği altı ay, kimlik doğrulama kararları bir yıl, ve Activity Ledger ASLA —
              sahibin "ne yaptın" yanıtlarının dayandığı kanıt tabanı ve EVIDENCE yaşlanmaz.
              Sessizce atlanan bir tablo, unutulmuş bir tablodan ayırt edilemez; o yüzden
              atlama gerekçesiyle adlandırıldı. Kuru koşu varsayılan: hatası geri alınamayan
              tek ev işi türü.
```

---

### B08 — Yedek/kurtarma sağlığı · 2026-09-13 (KISMEN)

kanıt `docs/evidence/b08-backup-recovery-2026-09-13.json`

```
646, 647, 648, 649, 650   status : DONE
     commit : ed40936
     tests  : test_backup_health.py (25)
     proof  : PROVEN_AUTOMATED
     date   : 2026-09-13
     note   : İki dosya haftalardır yazılıyordu ve hiçbir şey okumuyordu. RPO ve RTO artık
              belgeden değil o iki dosyadan geliyor: kurtarma noktası son iyi yedeğin yaşı,
              kurtarma süresi son gerçek tatbikatın ölçtüğü saniye. Görünmüyorsa "skipped" —
              API konteynerde, dosya host'ta; gözlemleyemediği şey için alarm veren bir
              kontrol, sahibin okumayı bıraktığı kontroldür. Ve hiçbir zamanlanmış birimin
              `OnFailure=`'ı yoktu: arıza, kimsenin okumadığı bir journal satırıydı.

614, 651, 652, 653, 654, 655   status : PARTIAL
     note   : KOD main'de — Astra dalı (codex/astra-p0-reliability @2561f84,
              READY_FOR_OWNER_APPROVAL) birleştirildi: kurulum/kaldırma betikleri, reconcile
              timer birimi, blue/green operasyon kilidi ve 842 satırlık kurulum testi.
              EKSİK olan üretim host'unda root kurulum — batch'in kendi tanımıyla sahip
              eylemi. Sahip kararı (2026-09-13): şimdilik PARTIAL kalsın, B09-B10'a devam.
              Depoda duran bir systemd birimi, host'ta duran bir systemd birimi değildir.
```

---

### B09 — Host dışı felaket kurtarma · 2026-09-13 (KISMEN)

```
642, 643, 644   status : DONE
     commit : ed40936
     tests  : test_backup_health.py (off-host bölümü)
     proof  : PROVEN_AUTOMATED — `--from-offhost` var ve testli; tatbikat config/.env ve
              RELEASE olmadan anlık görüntüyü reddediyor
     date   : 2026-09-13
     note   : Off-host kopya YAZILABİLİR ama OKUNAMAZDI. Her anlık görüntü haftalardır
              ikinci depoya kopyalanıyordu ve `restore-cloud-core.sh` yalnızca
              `$backup_root/restic`'i açıyordu — yedeğin korumak için var olduğu diskteki
              depoyu. Kaybolmuş bir host'ta o depo da kaybolmuştur. Geri yükleyemediğiniz
              bir yedek, yedek değil dosyadır. Yordam OPERATIONS.md'de, olması gereken
              sırayla: önce escrow'dan parola, sonra off-host kimlik bilgileri.

645           status : BLOCKED_OWNER
     note   : S3 uyumlu ikinci kova + erişim anahtarı. Anahtar DPAPI ile saklanır, asla
              commit'e/log'a girmez. O zamana kadar sağlık `advisories: ["no_offhost_copy"]`
              diyor — kurtarma hedefleri host'un VERİSİNİ kaybetmeyi kapsıyor, host'u
              kaybetmeyi değil, ve bu artık yazılı.
```

---

### B10 — Yürütme dürüstlüğü · 2026-09-13

kanıt `docs/evidence/b10-execution-honesty-2026-09-13.json`

```
558, 559      status : DONE
     commit : ed40936
     tests  : test_execution_honesty.py (14)
     proof  : PROVEN_AUTOMATED — düzeltme geri alınarak KIRMIZI: `assert 4 == 1`
     date   : 2026-09-13
     note   : İki üretim koşusu "4/4 adım tamam" diyordu; o dördün üçü başarısızdı.
              `steps_done` terminal duruma ulaşan HER adımı sayıyordu ve failed, cancelled,
              compensated, skipped hepsi terminal. Sonra cümle ona "adım tamam" diyordu.
              B06'nın dünya modelinde düzelttiği şeklin aynısı, bu kez yürütmede.
     yol üstünde : İLK yazdığım testler satırı elle kurup CÜMLEYİ doğruluyordu — kusuru
              geri koyduğumda yeşil kaldılar, çünkü sayıyı hesaplayan kodu hiç
              çalıştırmıyorlardı. Yazıldığı hatayı yakalayamayan test o hatanın kapsamı
              değildir; `_recompute_run_progress`'i gerçekten süren test eklendi.

539           status : DONE
     commit : ed40936
     tests  : test_execution_honesty.py (telafi bölümü, 5 test)
     proof  : PROVEN_AUTOMATED — üç dal da kırmızıyla kanıtlandı
     date   : 2026-09-13
     note   : `_run_compensation` KOŞULSUZ `state = COMPENSATED` yazıyordu. Hiçbir dalı
              eşleşmeyen, kanıtında iş yapacak bir id olmayan, ve sağlayıcısı patlayıp
              kasıtlı best-effort yakalamaya düşen adım — üçü de gerçekten iş geri alan bir
              adımla aynı şeyi söylüyordu. "Telafi edildi" bir şeyin geri alındığı anlamına
              gelmeli, yoksa sahibin onu okuduğu tek durumda kelime hiçbir şey etmez.
              Best-effort yakalama kalıyor (sahip durmak istedi, bu çalışmalı); değişen,
              adımın sonrasında NE SÖYLEDİĞİ.

548           status : DONE
     note   : B03'ün file.search sözleşmesi bunu düzeltti. Yeniden düzeltilmedi, ölçüldü.

560           status : DONE (ÖLÇÜM DÜZELTMESİ)
     note   : Matris MISSING diyordu. Değil: `app/executive/reconcile.py` var, `create_app`
              `executive_tick`'i rutin saatine enjekte ediyor, 31 test geçiyor. B07 bunu
              maddi olarak daha da doğru yaptı — saatin beş alt tiki tek try/except
              paylaşıyordu, yani rutin değerlendirmesindeki bir arıza bunun hiç koşmamasına
              yol açıyordu. Ölçüm dokümantasyonu yendi; bu projenin kendi dokümantasyonu
              dahil.
```
