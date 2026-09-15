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
| 51 | Real semantic embedding provider | deterministic-ngram, 256 boyut | Gerçek anlamsal gömme | MISSING | NYP | P2 | 41 | B37 | app/memory/ | — | canlı sağlık: deterministic-ngram | sağlayıcı kredisi | Anlamsal aramanın ön koşulu |
| 52 | Deterministic n-gram fallback | Çalışıyor | Aynı | DONE | PR | P2 | — | — | app/memory/ | app/memory testleri | canlı sağlık | no | Rewrite gerekmez |
| 53 | Embedding provider selection | Seçim mekanizması yok | Yapılandırmayla seçilir | MISSING | NYP | P2 | 51 | B37 | app/memory/ | — | — | no | — |
| 54 | Re-index pipeline | Yok | Yeniden indeksleme | MISSING | NYP | P2 | 53 | B37 | app/memory/ | — | — | no | 51 olmadan anlamsız |
| 55 | Memory retention scheduler | Yok | Politikayla süpürme | DONE | PA | P1 | — | B17 | app/memory/lifecycle.py:sweep_expired, app/main.py (RetentionSweeper) | test_memory_injection.py (22) | RetentionSweeper lifespan'de koşuyor, /health raporluyor | no | ÖLÇÜMLE DÜZELTİLDİ: retention sınıfına göre süpürüyor (session/short TTL, sabitlenmiş ve açık kayıtlara asla dokunmuyor), Phase 8'den (2026-09-11) beri kayıtlı ve koşuyor. Eksik olan tek şey bir bekçiydi — iki batch boyunca MISSING yazabildi çünkü yanlış olduğunda hiçbir şey düşmüyordu |
| 56 | Sensitive-data exclusion | Kural var (parola dizesi reddediliyor) | Tam kapsam | DONE | PA | P1 | 6 | B17 | app/memory/policy.py:find_secret, app/memory/injection.py | test_memory_injection.py (22), test_memory_policy.py (32) | üretim turu bekliyor (Karar 0) | no | Kapsamı asıl genişleten şey bu batch'in kendisi: bellek artık ÜÇÜNCÜ TARAF bir sağlayıcıya talimat içinde gidiyor. Yazma kapısına güvenen son kapı kapı değildir — enjeksiyon politikanın KENDİ kalıplarıyla tekrar tarıyor, içerik loglamadan |
| 57 | Memory audit UI | Yok | Sahip belleğini görür | MISSING | NYP | P2 | 39 | B37 | apps/web/ | — | — | no | 689 ile aynı sayfa |
| 58 | Pin/unpin UI | Yok | Arayüzden sabitleme | MISSING | NYP | P2 | 57 | B37 | apps/web/ | — | — | no | — |
| 59 | Forget UI | Yok | Arayüzden silme | MISSING | NYP | P2 | 57 | B37 | apps/web/ | — | — | no | — |
| 60 | Correction UI | Yok | Arayüzden düzeltme | MISSING | NYP | P2 | 57 | B37 | apps/web/ | — | — | no | — |
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
| 82 | App close | Cihazda var, bulut çağıranı yok | Erişilebilir | PARTIAL | NYP | P1 | 7? | B30 | devices/windows-agent | — | — | no | — |
| 83 | Window activate | Çalışıyor (FocusGuard) | Aynı | DONE | PA | P1 | — | — | devices/windows-agent | FocusGuard testleri | — | no | — |
| 84 | Window move | Cihazda var, çağıran yok | Erişilebilir | PARTIAL | NYP | P1 | 5 | B30 | devices/windows-agent | — | — | no | — |
| 85 | Window resize | Cihazda var, çağıran yok | Erişilebilir | PARTIAL | NYP | P1 | 5 | B30 | devices/windows-agent | — | — | no | — |
| 86 | Minimize | Cihazda var, çağıran yok | Erişilebilir | PARTIAL | NYP | P1 | 5 | B30 | devices/windows-agent | — | — | no | — |
| 87 | Maximize | Cihazda var, çağıran yok | Erişilebilir | PARTIAL | NYP | P1 | 5 | B30 | devices/windows-agent | — | — | no | — |
| 88 | Restore | Cihazda var, çağıran yok | Erişilebilir | PARTIAL | NYP | P1 | 5 | B30 | devices/windows-agent | — | — | no | — |
| 89 | Window list | Çalışıyor | Aynı | DONE | PA | P1 | — | — | app/operator/ | operator testleri | — | no | — |
| 90 | Current foreground read | Çalışıyor (FocusGuard) | Aynı | DONE | PA | P1 | — | — | devices/windows-agent | FocusGuard testleri | — | no | — |
| 91 | Keyboard typing | Cihaz gerçek + FocusGuard; bulut erişimi dar | Erişilebilir | PARTIAL | PA | P1 | 663 | B28 | devices/windows-agent | FocusGuard testleri | — | no | 109 ile birlikte |
| 92 | Single key press | Çağıran yok | Erişilebilir | PARTIAL | NYP | P1 | 91 | B28 | devices/windows-agent | — | — | no | — |
| 93 | Keyboard shortcuts | Çağıran yok | Erişilebilir | PARTIAL | NYP | P1 | 91 | B28 | devices/windows-agent | — | — | no | — |
| 94 | Mouse move | Çağıran yok | Erişilebilir | PARTIAL | NYP | P1 | 91 | B28 | devices/windows-agent | — | — | no | — |
| 95 | Mouse click | Çağıran yok | Erişilebilir | PARTIAL | NYP | P1 | 91 | B28 | devices/windows-agent | — | — | no | Sistem bugün tıklayamıyor |
| 96 | Double click | Çağıran yok | Erişilebilir | PARTIAL | NYP | P1 | 95 | B28 | devices/windows-agent | — | — | no | — |
| 97 | Right click | Çağıran yok | Erişilebilir | PARTIAL | NYP | P1 | 95 | B28 | devices/windows-agent | — | — | no | — |
| 98 | Scroll | Çağıran yok | Erişilebilir | PARTIAL | NYP | P1 | 95 | B28 | devices/windows-agent | — | — | no | — |
| 99 | UIA tree inspect | Çağıran yok | Erişilebilir | PARTIAL | NYP | P1 | 91 | B29 | devices/windows-agent | — | — | no | — |
| 100 | UIA button invoke | Çağıran yok | Erişilebilir | PARTIAL | NYP | P1 | 99 | B29 | devices/windows-agent | — | — | no | Tarayıcı kuralındaki 4. seviye |
| 101 | UIA set value | Çağıran yok | Erişilebilir | PARTIAL | NYP | P1 | 99 | B29 | devices/windows-agent | — | — | no | — |
| 102 | UIA read text | Çağıran yok | Erişilebilir | PARTIAL | NYP | P1 | 99 | B29 | devices/windows-agent | — | — | no | — |
| 103 | UIA select item | Çağıran yok | Erişilebilir | PARTIAL | NYP | P1 | 99 | B29 | devices/windows-agent | — | — | no | — |
| 104 | Screenshot capture | Çağıran yok | Erişilebilir | PARTIAL | NYP | P1 | 91 | B28 | devices/windows-agent | — | — | no | 735 ile ortak |
| 105 | Screenshot understanding | Yok | Görüntü anlamlandırılır | MISSING | NYP | P1 | 104 | B29 | app/operator/ | — | — | no | Vision sağlayıcısı gerekir |
| 106 | Vision-based fallback | Yok | Görüşe düşülebilir | MISSING | NYP | P2 | 105 | B39 | app/operator/ | — | — | sağlayıcı kararı | Tarayıcı kuralındaki 5. seviye |
| 107 | Coordinate only as last resort | Tarayıcıda var, operatörde politika yok | Politika zorlanır | PARTIAL | PA | P1 | 91 | B28 | app/browser/, app/operator/ | browser testleri | — | no | Tarayıcı yarısı örnek alınmalı |
| 108 | FocusGuard | Çalışıyor, güçlü | Aynı | DONE | PA | P1 | — | — | devices/windows-agent | FocusGuard testleri | kısmi gönderimde tam muhasebe | no | Sistemin en güçlü mekanizmalarından |
| 109 | Secret typing refusal | Sözlüksel; cihazdaki `secret` bayrağı buluttan hiç gelmiyor | Bayrak gönderilir + sunucu kapısı | PARTIAL | PA | P0 | 91 | B28 | app/operator/, devices/windows-agent | operator testleri | — | no | Güvenlik yarısı eksik |
| 110 | Per-action receipt | Kısmi | Her eylem makbuzlu | PARTIAL | NYP | P1 | 91 | B28 | app/operator/ | — | — | no | — |
| 111 | Postcondition verification | Yok | Sonuç doğrulanmadan başarı yok | MISSING | NYP | P1 | 110 | B29 | app/operator/ | — | — | no | Ürün ilkesi gereği zorunlu |
| 112 | Observe-Decide-Act-Verify | Yok, planlar sabit | Kapalı döngü | MISSING | NYP | P2 | 111 | B39 | app/operator/ | — | kaynak beyanı | no | — |
| 113 | Dynamic replanning | Yok | Plan çalışırken değişir | MISSING | NYP | P2 | 112 | B39 | app/operator/ | — | — | no | — |
| 114 | Retry by failure taxonomy | Yok | Hata sınıfına göre | MISSING | NYP | P2 | 112 | B39 | app/operator/ | — | — | no | — |
| 115 | Recovery/escalation | Yok | Seviye yükseltme | MISSING | NYP | P2 | 114 | B39 | app/operator/ | — | — | no | — |
| 116 | App-specific adapters | Yok | Yapısal sürücüler | MISSING | NYP | P1 | 99 | B29 | app/operator/ | — | — | no | — |
| 117 | 6'dan fazla açılabilir uygulama | Bulut 6, cihaz 7 | Genişler + iki liste bağlanır | PARTIAL | NYP | P1 | 5 | B30 | app/operator/, devices/windows-agent | — | mspaint yalnız cihazda | no | İki listeyi bağlayan test yok |
| 118 | Safe terminal expansion | Cihaz 8 desen, bulut 2 | Genişler | PARTIAL | PA | P1 | 117 | B30 | app/operator/ | operator testleri | hostname, ipconfig | no | — |
| 119 | Process inspect | Çağıran yok | Erişilebilir | PARTIAL | NYP | P1 | 117 | B30 | devices/windows-agent | — | — | no | — |
| 120 | Process stop with policy | Yok | Politikayla | MISSING | NYP | P1 | 119 | B30 | app/operator/ | — | — | no | — |
| 121 | Service inspect | Yok | Erişilebilir | MISSING | NYP | P1 | 119 | B30 | devices/windows-agent | — | — | no | — |
| 122 | Service restart with policy | Yok | Politikayla | MISSING | NYP | P1 | 121 | B30 | app/operator/ | — | — | no | UAC sınırı olabilir |
| 123 | Windows Settings navigation | Yok | Gezinilir | MISSING | NYP | P2 | 100 | B39 | app/operator/ | — | — | no | — |
| 124 | File Explorer operations | Yok | Kullanılır | MISSING | NYP | P2 | 100 | B39 | app/operator/ | — | — | no | — |
| 125 | Office app interaction | Yok | Sürülür | MISSING | NYP | P2 | 116 | B39 | app/operator/ | — | — | no | — |
| 126 | IDE interaction | Yok | Sürülür | MISSING | NYP | P2 | 116 | B39 | app/operator/ | — | — | no | — |
| 127 | Browser + desktop mixed plan | Yok | Karma plan | MISSING | NYP | P2 | 112 | B39 | app/operator/, app/browser/ | — | üretimde 2 yetenek boşluğu bu yüzden | no | "Chrome açıp YouTube'a gir" planlanamıyor |
| 128 | Multi-step task persistence | Yok | Kalıcı | MISSING | NYP | P2 | 535 | B39 | app/operator/, app/executive/ | — | — | no | — |
| 129 | Operator pause/cancel | Yok | Duraklat/iptal | MISSING | NYP | P2 | 128 | B39 | app/operator/ | — | — | no | — |
| 130 | "Show me before acting" | Yok | Önizleme modu | MISSING | NYP | P2 | 110 | B39 | app/operator/ | — | — | no | Güven için değerli |

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
| 139 | Image metadata read | Yok | Okunur | MISSING | NYP | P1 | — | B32 | devices/windows-agent | — | — | no | — |
| 140 | OCR | Yok | Metin çıkar | MISSING | NYP | P1 | 139 | B32 | devices/windows-agent | — | — | sağlayıcı kararı | 496 ile aynı iş |
| 141 | Image text extraction | Yok | Kullanılabilir | MISSING | NYP | P1 | 140 | B32 | app/files/ | — | — | no | 140 ile ortak |
| 142 | Archive inspect | Yok | İçerik görülür | MISSING | NYP | P1 | — | B32 | devices/windows-agent | — | — | no | — |
| 143 | EPUB read | Yok | Okunur | MISSING | NYP | P2 | — | B52 | devices/windows-agent | — | — | no | — |
| 144 | RTF read | Yok | Okunur | MISSING | NYP | P2 | — | B52 | devices/windows-agent | — | — | no | — |
| 145 | ODT read | Yok | Okunur | MISSING | NYP | P2 | — | B52 | devices/windows-agent | — | — | no | — |
| 146 | Legacy Office formats | Yok | .doc/.xls/.ppt okunur | MISSING | NYP | P2 | — | B52 | devices/windows-agent | — | — | no | — |
| 147 | File metadata search | Klasör araması çalışıyor | Çalışır | DONE | PA | P0 | 3 | B03 | app/documents/service.py | test_documents_confinement.py (15) | — | no | 3'ün doğrudan sonucu |
| 148 | Full text search | Sözlüksel token örtüşmesi | Gerçek tam metin | PARTIAL | PA | P1 | 3 | B32 | app/files/ | files testleri | — | no | — |
| 149 | Semantic document search | Yok | Anlamsal arama | MISSING | NYP | P2 | 51 | B37 | app/files/, app/memory/ | — | — | no | Gömme sağlayıcısına bağlı |
| 150 | File deduplication | Yok | Sadeleşir | MISSING | NYP | P1 | 151 | B32 | app/files/ | — | — | no | — |
| 151 | Duplicate detection | Yok | Tespit | MISSING | NYP | P1 | — | B32 | app/files/ | — | — | no | — |
| 152 | File preview | Kısmi | Önizleme | PARTIAL | NYP | P1 | — | B32 | app/files/ | — | — | no | — |
| 153 | File edit | Yok (yapısal salt okunur) | Yönetilen düzenleme | MISSING | NYP | P2 | 160 | B34 | app/files/ | — | file.delete yapısal red | no | 674 iznine bağlı |
| 154 | File write | Yalnız 3 ajan-sahipli kök | Yönetilen yazma | PARTIAL | PA | P2 | 160 | B34 | app/files/ | files testleri | — | no | — |
| 155 | File append | Yok | Ekleme | MISSING | NYP | P2 | 154 | B34 | app/files/ | — | — | no | — |
| 156 | File rename | Yok | Yeniden adlandırma | MISSING | NYP | P2 | 160 | B34 | app/files/ | — | — | no | — |
| 157 | File move | Yok | Taşıma | MISSING | NYP | P2 | 160 | B34 | app/files/ | — | — | no | — |
| 158 | File copy | Yok | Kopyalama | MISSING | NYP | P2 | 154 | B34 | app/files/ | — | — | no | — |
| 159 | File delete | Yapısal olarak reddediliyor | Politikayla silme | MISSING | NYP | P2 | 161,678 | B34 | app/files/ | files testleri | dosya hayatta kalıyor | onay politikası | Bilinçli mevcut sınır |
| 160 | Undo journal | Yok | Her mutasyon geri alınabilir | MISSING | NYP | P2 | — | B34 | app/files/ | — | — | no | 153-159'un ÖN KOŞULU |
| 161 | Trash/recycle integration | Yok | Geri dönüşüme gider | MISSING | NYP | P2 | 160 | B34 | devices/windows-agent | — | — | no | — |
| 162 | Mutation receipt | Yok | Makbuzlu | MISSING | NYP | P2 | 160 | B34 | app/files/ | — | — | no | — |
| 163 | Before/after hash | Yok | Hash'li kanıt | MISSING | NYP | P2 | 162 | B34 | app/files/ | — | — | no | — |
| 164 | Document version history | Yok | Sürüm geçmişi | MISSING | NYP | P2 | 160 | B34 | app/files/ | — | — | no | — |
| 165 | Safe atomic write | Artefaktta var, belge ailesinde yok | Her yerde | PARTIAL | PA | P2 | 154 | B34 | app/artifacts/, app/files/ | artifacts testleri | — | no | Artefakt yarısı örnek |
| 166 | Owner approval by risk | Yok | Riskli mutasyon onay ister | MISSING | NYP | P2 | 160,674 | B34 | app/files/, app/security/ | — | — | onay politikası | — |
| 167 | "Bu dosyayı düzenle" | Yok | Doğal akış | MISSING | NYP | P2 | 153 | B34 | app/voice/intent/ | — | — | no | — |
| 168 | "Şu klasördeki dosyaları özetle" | 'Şu klasördeki dosyaları özetle' çalışıyor | Çalışır | DONE | PA | P0 | 3 | B03 | app/documents/service.py | test_file_search_roots_contract.py | — | no | 3'ün doğrudan sonucu |
| 169 | "Bu iki dokümanı karşılaştır" | Executive şablonu var, 3'e bağımlı | Çalışır | PARTIAL | PA | P1 | 3 | B32 | app/executive/ | executive testleri | — | no | 548 ile aynı plan |
| 170 | "Bu belgeyi güncelle ve kaydet" | Yok | Güncelle ve kaydet | MISSING | NYP | P2 | 153 | B34 | app/files/ | — | — | no | — |

## F. BROWSER / INTERNET / RESEARCH (171–210)

| ID | FEATURE | CURRENT_STATUS | TARGET_STATUS | IMPL | PROOF | PRI | DEPS | BATCH | SOURCE_REFERENCES | TEST_REFERENCES | RUNTIME_PROOF | OWNER_ACTION | NOTES |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 171 | Managed Chrome | Ayrı profille çalışıyor | Aynı | DONE | PR | P1 | — | — | app/browser/, devices/windows-agent | browser testleri | üretim araştırma koşuları | no | Rewrite gerekmez |
| 172 | Owner Chrome reuse | Kayıtlı ama otonom araştırmaya kapalı | Denetimli kullanım | PARTIAL | PA | P1 | 189 | B31 | app/browser/ | browser testleri | owner_authorized_for_research=false | mahremiyet kararı | ADR-0113 bilinçli sınır |
| 173 | Session reuse | Kısmi | Açık oturum kullanılır | PARTIAL | NYP | P1 | 172 | B31 | app/browser/ | — | — | no | — |
| 174 | Search | Çalışıyor | Aynı | DONE | PR | P1 | — | — | app/browser/ | browser testleri | üretim | no | — |
| 175 | Navigation | Çalışıyor | Aynı | DONE | PR | P1 | — | — | app/browser/ | browser testleri | üretim | no | — |
| 176 | DOM inspect | Çalışıyor | Aynı | DONE | PA | P1 | — | — | app/browser/ | browser testleri | — | no | Anlamsal hedefleme (rol/metin/etiket) |
| 177 | DOM click | Çalışıyor | Aynı | DONE | PA | P1 | — | — | app/browser/ | browser testleri | — | no | — |
| 178 | DOM text entry | Çalışıyor | Aynı | DONE | PA | P1 | — | — | app/browser/ | browser testleri | — | no | — |
| 179 | Form fill | Çalışıyor | Aynı | DONE | PA | P1 | — | — | app/browser/ | browser testleri | — | no | — |
| 180 | Download | Var ama boyut sınırı yok, yetki kontrolü zayıf | Sınırlı ve yetkili | PARTIAL | PA | P1 | — | B31 | app/browser/ | browser testleri | — | no | "boş değil mi" kontrolü yetersiz |
| 181 | Upload | Bayrak duyuruluyor, işlem yok | Çalışır | MISSING | NYP | P1 | — | B31 | app/browser/, devices/windows-agent | — | `uploads` bayrağı karşılıksız | no | Yalan duyuru |
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
| 192 | DEEP research | Üretimde hiç koşmadı; sesten 12 kaynağa kırpılıyor | Gerçekten koşar | PARTIAL | PA | P1 | — | B31 | app/research/ | research testleri | 0 DEEP koşusu | no | Mod açıkça seçilebilmeli |
| 193 | Source ranking | Çalışıyor | Aynı | DONE | PA | P1 | — | — | app/research/ | research testleri | — | no | — |
| 194 | Domain diversity | Çalışıyor | Aynı | DONE | PA | P1 | — | — | app/research/ | research testleri | — | no | — |
| 195 | Challenge skip | Çalışıyor | Aynı | DONE | PA | P1 | — | — | app/research/ | research testleri | — | no | — |
| 196 | Source cooldown | Çalışıyor | Aynı | DONE | PA | P1 | — | — | app/research/ | research testleri | — | no | — |
| 197 | Citation binding | Çalışıyor | Aynı | DONE | PA | P1 | — | — | app/research/ | research testleri | kanıt sözleşmesi | no | İnce sonuçta dürüst itiraf |
| 198 | Research focus/current report | Kısmi | Odak çalışır | PARTIAL | NYP | P1 | — | B31 | app/research/ | — | — | no | — |
| 199 | Previous report references | Yok | Atıf yapılır | MISSING | NYP | P1 | 198 | B31 | app/research/ | — | — | no | — |
| 200 | "Bunu teknik anlat" | Kısmi | Çalışır | PARTIAL | NYP | P1 | 209 | B31 | app/voice/, app/research/ | — | — | no | 237 ile ortak |
| 201 | "Bir önceki araştırmayı aç" | Yok | Çalışır | MISSING | NYP | P1 | 199 | B31 | app/voice/intent/ | — | — | no | — |
| 202 | Research cancel | Yok | Sesle ve REST'ten iptal | MISSING | NYP | P1 | — | B31 | app/research/ | — | — | no | 732 ile ortak |
| 203 | Research pause | Yok | Duraklat | MISSING | NYP | P1 | 202 | B31 | app/research/ | — | — | no | — |
| 204 | Research resume | Yok | Devam | MISSING | NYP | P1 | 203 | B31 | app/research/ | — | — | no | — |
| 205 | Temporal typed failures | Çalışıyor | Aynı | DONE | PA | P0 | — | — | app/research/ | research testleri | ADR-0123 | no | 12 ile aynı iş |
| 206 | Orphan cleanup | Yok | Temizlenir | DONE | PA | P0 | 10 | B06 | app/research/service.py:sweep_abandoned_runs | test_orphan_sweeps.py | üretim turu bekliyor (Karar 0) | no | 10, 13 ile tek uygulama |
| 207 | Provider fallback transparency | Kısmi | Şeffaf | PARTIAL | NYP | P1 | — | B31 | app/research/ | — | — | no | — |
| 208 | Result-first narration | Çalışıyor | Aynı | DONE | PA | P1 | — | — | app/research/, app/voice/ | research testleri | — | no | — |
| 209 | Technical mode | Kısmi | Çalışır | PARTIAL | NYP | P1 | — | B31 | app/voice/ | — | — | no | 237 ile ortak |
| 210 | Research history UI | Kısmi | Tam geçmiş | PARTIAL | NYP | P1 | 685 | B31 | apps/web/ | — | — | no | — |

## G. VOICE / REALTIME (211–238)

| ID | FEATURE | CURRENT_STATUS | TARGET_STATUS | IMPL | PROOF | PRI | DEPS | BATCH | SOURCE_REFERENCES | TEST_REFERENCES | RUNTIME_PROOF | OWNER_ACTION | NOTES |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 211 | Browser Voice WebRTC | Çalışıyor | Aynı | DONE | PR | P1 | — | — | app/voice/realtime/, apps/web/ | voice testleri | 97 oturum | no | Rewrite gerekmez |
| 212 | Stable data channel | Çalışıyor | Aynı | DONE | PR | P1 | — | — | app/voice/realtime/ | voice testleri | üretim | no | — |
| 213 | Barge-in | Yerel durdurma önce, sonra iptal | Aynı | DONE | PA | P1 | — | — | apps/web/, app/voice/ | sıralama testi | — | no | Sıralaması testli |
| 214 | Semantic VAD | Çalışıyor | Aynı | DONE | PA | P1 | — | — | app/voice/realtime/ | voice testleri | — | no | — |
| 215 | Noise suppression | Sağlayıcı tarafında | Ölçülür ve ayarlanır | PARTIAL | NYP | P1 | — | B20 | apps/web/ | — | — | no | — |
| 216 | Echo cancellation | Sağlayıcı tarafında | Ölçülür | PARTIAL | NYP | P1 | — | B20 | apps/web/ | — | — | no | — |
| 217 | Mic sensitivity modes | Yok | Seçilebilir | MISSING | NYP | P1 | — | B20 | apps/web/ | — | — | no | — |
| 218 | Reconnect | Tek uçuşlu + 410 fırtına koruması | Proaktif yeniden bağlanma | PARTIAL | PA | P1 | — | B20 | app/voice/realtime/, apps/web/ | 410 fırtına testi (20 kesinti to 1 deneme) | — | no | ADR-0099 güçlü |
| 219 | Dead session cleanup | Yok | Süpürülür | DONE | PA | P0 | — | B06 | app/voice/realtime_sessions/service.py:sweep_idle_sessions, app/main.py:RetentionSweeper | test_orphan_sweeps.py | üretim turu bekliyor (Karar 0) | no | 9 ile tek uygulama |
| 220 | Session TTL | Yok | Ömür sınırı | DONE | PA | P0 | 219 | B06 | app/voice/realtime_sessions/service.py:sweep_idle_sessions:IDLE_SESSION_AFTER | test_orphan_sweeps.py | üretim turu bekliyor (Karar 0) | no | ADR-0105 gereği mutlak ömür DEĞİL, 12 saat atıllık sınırı |
| 221 | False LISTENING prevention | Mikrofon kaybında "Dinliyor" diyor | Durum gerçeğe bağlı | BROKEN | NYP | P1 | 222 | B20 | apps/web/ | — | — | no | Güven kırıcı |
| 222 | Mic track loss detection | `track.onended`/`onmute` dinlenmiyor | Kayıp görülür | MISSING | NYP | P1 | — | B20 | apps/web/ | — | — | no | 221'in nedeni |
| 223 | Provider 60-min limit handling | ADR-0105 düzeltmesi var; tavan değerini istemci okumuyor | Tavan öncesi yenileme | PARTIAL | PA | P1 | — | B20 | app/voice/realtime/, apps/web/ | voice testleri | commit 12b8be4 | no | Yarısı yapıldı |
| 224 | Long-form narration | Motor yalnız testlerde; seam imzası sağlayıcıyla uyumsuz | Gerçek ses üretir | BROKEN | NYP | P1 | — | B21 | app/narration/ | narration testleri | — | TTS kotası | Anahtar eklemek yetmez |
| 225 | Narration queue | Kısmi | Çalışır | PARTIAL | NYP | P1 | 224 | B21 | app/narration/ | — | — | no | — |
| 226 | Read-ahead | Yok | İleri okuma | MISSING | NYP | P1 | 225 | B21 | app/narration/ | — | — | no | — |
| 227 | Pause/resume narration | Yok | Duraklat/devam | MISSING | NYP | P1 | 225 | B21 | app/narration/ | — | — | no | — |
| 228 | Pronunciation dictionary | Var, üretimde 0 kural | Kurallar girilir | PARTIAL | PA | P1 | — | B21 | app/voice/pronunciation/ | voice testleri | 0 kural | no | Tek yazıcı manuel PUT |
| 229 | Pronunciation in assistant speech | Yalnız araç dönüş metnine | Persona talimatına girer | MISSING | NYP | P1 | 228 | B21 | app/voice/pronunciation/, app/persona/ | — | — | no | Asıl eksik |
| 230 | Turkish number pronunciation | Kısmi | Tam | PARTIAL | PA | P1 | 228 | B21 | app/voice/pronunciation/ | voice testleri | — | no | — |
| 231 | Speaking state = real playback | Kısmi | Gerçek sese bağlı | PARTIAL | NYP | P1 | — | B20 | apps/web/ | — | — | no | — |
| 232 | RMS only drives animation | Doğru uygulanmış | Aynı | DONE | PA | P1 | — | — | apps/web/ | core testleri | — | no | Rewrite gerekmez |
| 233 | Text fallback on provider failure | Kısmi | Her düşüşte metin | PARTIAL | NYP | P1 | — | B20 | app/voice/, apps/web/ | — | — | no | — |
| 234 | TTS fallback policy | Sinüs tonu yedeği, testi yok | Politika + test | PARTIAL | NYP | P1 | 233 | B20 | app/voice/, app/alarms/ | — | — | no | Sessiz ton dalı |
| 235 | Provider-unavailable UI | Kısmi | Kontrollü | PARTIAL | NYP | P1 | 709 | B20 | apps/web/ | — | — | no | 709 ile ortak |
| 236 | Voice diagnostics | Kısmi; faster-whisper yanlış "etkin" görünüyor | Dürüst teşhis | PARTIAL | NYP | P1 | — | B20 | app/voice/, app/system/health.py | — | kısa devre ölü dalı atlıyor | no | Yanlış sağlık raporu |
| 237 | "Teknik anlat" mode | Kısmi | Çalışır | PARTIAL | NYP | P1 | 209 | B21 | app/voice/ | — | — | no | 200,209 ile ortak |
| 238 | Voice activity history | Defter çift yazıyor | Doğru geçmiş | PARTIAL | NYP | P1 | 70 | B20 | app/ledger/, app/voice/ | — | — | no | 70 düzelmeden düzelmez |

## H. DEVICE-SIDE / AMBIENT VOICE (239–255)

| ID | FEATURE | CURRENT_STATUS | TARGET_STATUS | IMPL | PROOF | PRI | DEPS | BATCH | SOURCE_REFERENCES | TEST_REFERENCES | RUNTIME_PROOF | OWNER_ACTION | NOTES |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 239 | Device-side microphone provider | Cihaz ses yakalama yeteneği duyurmuyor | Cihazda mikrofon | MISSING | NYP | P2 | — | B47 | devices/windows-agent | — | 85 yetenekte yok | mahremiyet kararı | Sürekli açık mikrofon kararı |
| 240 | Browser-independent listening | Yok | Tarayıcısız dinleme | MISSING | NYP | P2 | 239 | B47 | devices/windows-agent | — | — | mahremiyet kararı | Asistanın ön kapısı bugün fare tıklaması |
| 241 | Wake word | Yok | Uyandırma sözcüğü | MISSING | NYP | P2 | 240 | B47 | devices/windows-agent | — | — | mahremiyet kararı | — |
| 242 | Wake-word enable/disable | Yok | Açılıp kapanır | MISSING | NYP | P2 | 241 | B47 | devices/windows-agent | — | — | no | — |
| 243 | Push-to-talk fallback | Yok | Bas-konuş | MISSING | NYP | P2 | 239 | B47 | devices/windows-agent | — | — | no | Mahremiyet açısından en ucuz seçenek |
| 244 | Local VAD | Yok | Cihazda konuşma tespiti | MISSING | NYP | P2 | 239 | B47 | devices/windows-agent | — | — | no | — |
| 245 | Speaker verification | Tavsiye niteliğinde; verdict'i okuyan yok | Karar yolunda | PARTIAL | PA | P0 | — | B05 | app/security/step_up.py, app/voice/realtime_sessions/service.py:handle_tool_call | test_voice_step_up.py | gölge mod: sayıyor, engellemiyor — enforce sahip kararı | no | Karar yolu var ve test edildi; realtime'da verdict üreten akış YOK |
| 246 | Trusted-device check | `device_trusted` istek gövdesinden geliyor | Sunucu tarafında doğrulanır | DONE | PA | P0 | 663 | B05 | app/voice/device_trust.py, app/voice/routes.py:verify_speaker_route | test_authority_gate.py | üretim turu bekliyor (Karar 0) | no | Alan kaldırıldı: gönderen 422 alıyor, sessizce yok sayılmıyor |
| 247 | Speaker-confidence threshold | Hesaplanıyor, tüketilmiyor | Uygulanır | PARTIAL | PA | P0 | 245 | B05 | app/security/step_up.py, app/voice/realtime_sessions/service.py:handle_tool_call | test_voice_step_up.py | gölge mod: sayıyor, engellemiyor — enforce sahip kararı | no | Eşik tüketiliyor ama gölge modda; enforce iki şey bekliyor |
| 248 | Sensitive-command higher threshold | Yok | Eşik yükselir | PARTIAL | PA | P0 | 247 | B05 | app/security/step_up.py, app/voice/realtime_sessions/service.py:handle_tool_call | test_voice_step_up.py | gölge mod: sayıyor, engellemiyor — enforce sahip kararı | no | CRITICAL için 0.88 skor + 3 dk tazelik; gölge modda |
| 249 | Raw voice not archived | Politika uygulanıyor | Aynı | DONE | PA | P0 | — | — | app/voice/ | voice testleri | — | no | Ürün ilkesi — gevşetilmez |
| 250 | Device-local Voice startup | Yok | Açılışta başlar | MISSING | NYP | P2 | 239 | B47 | devices/windows-agent | — | — | no | — |
| 251 | Voice service restart recovery | Yok | Toparlanır | MISSING | NYP | P2 | 250 | B47 | devices/windows-agent | — | — | no | — |
| 252 | Voice process health | Yok | Sağlık görünür | MISSING | NYP | P2 | 250 | B47 | devices/windows-agent | — | — | no | — |
| 253 | Offline command subset | Yok | Çevrimdışı komutlar | MISSING | NYP | P2 | 240 | B47 | devices/windows-agent | — | — | no | Alarmın çevrimdışı yolu örnek alınmalı |
| 254 | Voice privacy indicator | Yok | Dinleme göstergesi | MISSING | NYP | P2 | 240 | B47 | devices/windows-agent | — | — | no | Mahremiyet için zorunlu |
| 255 | Hardware mic mute awareness | Yok | Donanım susturması bilinir | MISSING | NYP | P2 | 239 | B47 | devices/windows-agent | — | — | no | — |

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
| 277 | Calendar summary | Sağlayıcı yok | Çalışır | BLOCKED_PROVIDER | PU | P2 | 337 | B46 | app/briefing/, app/calendar/ | calendar testleri | — | hesap bilgisi | — |
| 278 | Mail summary | Sağlayıcı yok | Çalışır | BLOCKED_PROVIDER | PU | P2 | 335 | B45 | app/briefing/, app/mail/ | mail testleri | — | hesap bilgisi | — |
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
| 300 | Camera open | Yalnız tarayıcı sekmesinde | Cihazda da | PARTIAL | PA | P2 | 327 | B48 | apps/web/ | presence testleri | eye_enabled=false | kamera kararı | — |
| 301 | Camera close | Sekme kapanınca sessizce susuyor | Bildirimli kapanış | PARTIAL | NYP | P2 | 300 | B48 | apps/web/ | — | — | no | Durum yalnız zaman aşımıyla UNKNOWN'a düşüyor |
| 302 | Camera reopen | Yok | Yeniden açılır | MISSING | NYP | P2 | 301 | B48 | apps/web/ | — | — | no | — |
| 303 | Camera privacy state | Kısmi | Görünür durum | PARTIAL | NYP | P2 | 300 | B48 | apps/web/ | — | — | no | — |
| 304 | PRESENT | Çalışıyor | Aynı | DONE | PR | P1 | — | — | app/presence/ | presence testleri | canlı /v1/presence | no | — |
| 305 | AWAY | Çalışıyor | Aynı | DONE | PR | P1 | — | — | app/presence/ | presence testleri | canlı | no | — |
| 306 | RETURNED | Çalışıyor | Aynı | DONE | PR | P1 | — | — | app/presence/ | presence testleri | canlı | no | — |
| 307 | RESTING | Üretimde ERİŞİLEMEZ (duruş sinyali hep unknown) | Erişilebilir | MISSING | NYP | P2 | 327 | B48 | app/presence/ | presence testleri | canlı | no | 333'ü imkânsız kılıyor |
| 308 | LIKELY_ASLEEP | Üretimde ERİŞİLEMEZ | Erişilebilir | MISSING | NYP | P2 | 327 | B48 | app/presence/ | presence testleri | canlı | no | 333'ü imkânsız kılıyor |
| 309 | UNKNOWN | Çalışıyor | Aynı | DONE | PR | P1 | — | — | app/presence/ | presence testleri | canlı | no | — |
| 310 | Temporal confidence | Kısmi | Tam | PARTIAL | NYP | P2 | — | B48 | app/presence/ | — | — | no | — |
| 311 | Input activity fusion | Çalışıyor, tarayıcısız | Aynı | DONE | PR | P1 | — | — | devices/windows-agent | presence testleri | sources:["input"] | no | Tek gerçek tarayıcısız varlık yolu |
| 312 | Time-of-day fusion | Kısmi | Tam | PARTIAL | NYP | P2 | 310 | B48 | app/presence/ | — | — | no | — |
| 313 | Owner preference fusion | Kısmi | Tam | PARTIAL | NYP | P2 | 310 | B48 | app/presence/ | — | — | no | — |
| 314 | Camera failure is not asleep | Doğru uygulanmış | Aynı | DONE | PA | P0 | — | — | app/presence/ | presence testleri | — | no | Rewrite gerekmez |
| 315 | UNKNOWN keeps display on | Doğru uygulanmış | Aynı | DONE | PA | P0 | — | — | app/ambient/ | ambient testleri | — | no | Güvenli varsayılan |
| 316 | Keyboard wake | Çalışıyor | Aynı | DONE | PR | P1 | — | — | devices/windows-agent | ambient testleri | — | no | — |
| 317 | Mouse wake | Çalışıyor | Aynı | DONE | PR | P1 | — | — | devices/windows-agent | ambient testleri | — | no | — |
| 318 | Display off | Çalışıyor | Aynı | DONE | PR | P1 | — | — | devices/windows-agent | ambient testleri | — | no | — |
| 319 | Display wake | Çalışıyor | Aynı | DONE | PR | P1 | — | — | devices/windows-agent | ambient testleri | — | no | — |
| 320 | Multiple-monitor power | Kısmi | Tam | PARTIAL | NYP | P2 | — | B48 | devices/windows-agent | — | — | donanım yargısı | — |
| 321 | No topology/resolution modification | Uygulanıyor | Aynı | DONE | PA | P0 | — | — | devices/windows-agent | ambient testleri | — | no | Ürün sınırı |
| 322 | Alarm wake precedence | İki yakada da zorlanıyor | Aynı | DONE | PA | P0 | — | — | app/ambient/, devices/windows-agent | ambient testleri | — | no | Cihaz önce alarma bakıyor |
| 323 | Holdoff after input | Çalışıyor | Aynı | DONE | PA | P1 | — | — | app/ambient/ | ambient testleri | — | no | — |
| 324 | Holdoff after alarm | Çalışıyor | Aynı | DONE | PA | P1 | — | — | app/ambient/ | ambient testleri | — | no | — |
| 325 | Holdoff after explicit wake | Çalışıyor | Aynı | DONE | PA | P1 | — | — | app/ambient/ | ambient testleri | — | no | — |
| 326 | Device-local presence provider | Girdi tabanlı var, kamera yok | Tam | PARTIAL | PR | P2 | 327 | B48 | devices/windows-agent | presence testleri | sources:["input"] | no | — |
| 327 | Browser-independent camera provider | Cihazda kamera kodu YOK | Cihazda kamera | MISSING | NYP | P2 | — | B48 | devices/windows-agent | — | — | kamera kararı | 307,308,333'ün ön koşulu |
| 328 | No raw camera archive | Uygulanıyor | Aynı | DONE | PA | P0 | — | — | app/presence/ | presence testleri | — | no | Ürün ilkesi — gevşetilmez |
| 329 | Structured perception only | Uygulanıyor | Aynı | DONE | PA | P0 | — | — | app/presence/ | presence testleri | — | no | Ürün ilkesi |
| 330 | Presence history | Kısmi | Tam | PARTIAL | NYP | P2 | — | B48 | app/presence/ | — | — | no | — |
| 331 | Ambient policy UI | Kısmi | Tam | PARTIAL | NYP | P2 | 685 | B48 | apps/web/ | — | — | no | — |
| 332 | Auto display-off on/off | Kısmi | Tam | PARTIAL | NYP | P2 | 331 | B48 | apps/web/ | — | — | no | — |
| 333 | "Uyurken ekranı kapat" | Hiç tetiklenemiyor (307/308 erişilemez) | Çalışır | MISSING | NYP | P2 | 308 | B48 | app/ambient/, app/voice/intent/ | — | — | no | Politika hazır, sinyal yok |
| 334 | "Ben dönünce aç" | Çalışıyor | Aynı | DONE | PR | P1 | — | — | app/ambient/ | ambient testleri | — | no | — |

## K. MAIL / CALENDAR (335–366)

> Ölçüm: IMAP/SMTP/CalDAV kodu **gerçek ve iyi test edilmiş** (soket düzeyinde testler, MIME
> yuvalama ve başlık enjeksiyonu sertleştirmesi). Hiçbir hesap yapılandırılmamış; `.env.example`
> bu anahtarları içermiyor. Bu yüzden çoğu satır `BLOCKED_PROVIDER` + `PROVIDER_UNAVAILABLE`.

| ID | FEATURE | CURRENT_STATUS | TARGET_STATUS | IMPL | PROOF | PRI | DEPS | BATCH | SOURCE_REFERENCES | TEST_REFERENCES | RUNTIME_PROOF | OWNER_ACTION | NOTES |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 335 | IMAP read | Kod gerçek, hesap yok | Çalışır | BLOCKED_PROVIDER | PU | P2 | — | B45 | app/mail/ | soket düzeyi testler | — | hesap bilgisi | Kod rewrite gerekmez |
| 336 | SMTP send | Kod gerçek, varsayılan kapalı | Çalışır | BLOCKED_PROVIDER | PU | P2 | 335 | B45 | app/mail/ | soket düzeyi testler | — | hesap bilgisi | — |
| 337 | CalDAV read | Kod gerçek, hesap yok | Çalışır | BLOCKED_PROVIDER | PU | P2 | — | B46 | app/calendar/ | calendar testleri | — | hesap bilgisi | — |
| 338 | Mail list | Sağlayıcıya bağlı | Çalışır | BLOCKED_PROVIDER | PU | P2 | 335 | B45 | app/mail/ | mail testleri | — | hesap bilgisi | — |
| 339 | Mail search | Sağlayıcıya bağlı | Çalışır | BLOCKED_PROVIDER | PU | P2 | 335 | B45 | app/mail/ | mail testleri | — | hesap bilgisi | — |
| 340 | Mail summarize | Sağlayıcıya bağlı | Çalışır | BLOCKED_PROVIDER | PU | P2 | 338 | B45 | app/mail/ | mail testleri | — | hesap bilgisi | — |
| 341 | Thread summarize | Sağlayıcıya bağlı | Çalışır | BLOCKED_PROVIDER | PU | P2 | 340 | B45 | app/mail/ | mail testleri | — | hesap bilgisi | — |
| 342 | Draft | Kod var | Çalışır | BLOCKED_PROVIDER | PU | P2 | 335 | B45 | app/mail/ | mail testleri | — | hesap bilgisi | — |
| 343 | Reply draft | Kod var, References kusuru | Doğru zincir | BROKEN | NYP | P2 | 346 | B45 | app/mail/ | mail testleri | — | hesap bilgisi | 346 ile aynı kusur |
| 344 | Forward draft | Kod var | Çalışır | BLOCKED_PROVIDER | PU | P2 | 342 | B45 | app/mail/ | mail testleri | — | hesap bilgisi | — |
| 345 | Send confirmation gate | Çok iyi tasarlanmış, hiç çalışmamış | Çalışır | PARTIAL | PA | P2 | 336 | B45 | app/mail/, app/security/ | atomik durum geçişi testleri | — | hesap bilgisi | Okundu-geri-bildirim + sonraki tur |
| 346 | Reply References correctness | `References` boş geçiliyor | Zincir kırılmaz | BROKEN | NYP | P2 | — | B45 | app/mail/ | mevcut test gerçek servisi ATLIYOR | — | no | Sahte-nazik test örneği |
| 347 | Attachment read | Kısmi | Çalışır | PARTIAL | PA | P2 | 335 | B45 | app/mail/ | mail testleri | — | hesap bilgisi | — |
| 348 | Attachment save | Kısmi | Çalışır | PARTIAL | NYP | P2 | 347 | B45 | app/mail/ | — | — | hesap bilgisi | — |
| 349 | Calendar list | Sağlayıcıya bağlı | Çalışır | BLOCKED_PROVIDER | PU | P2 | 337 | B46 | app/calendar/ | calendar testleri | — | hesap bilgisi | — |
| 350 | Calendar today | Sağlayıcıya bağlı | Çalışır | BLOCKED_PROVIDER | PU | P2 | 349 | B46 | app/calendar/ | calendar testleri | — | hesap bilgisi | — |
| 351 | Calendar week | Sağlayıcıya bağlı | Çalışır | BLOCKED_PROVIDER | PU | P2 | 349 | B46 | app/calendar/ | calendar testleri | — | hesap bilgisi | 365 ile ortak |
| 352 | Create event proposal | Kod var | Çalışır | BLOCKED_PROVIDER | PU | P2 | 337 | B46 | app/calendar/ | calendar testleri | — | hesap bilgisi | — |
| 353 | Edit event proposal | Kod var | Çalışır | BLOCKED_PROVIDER | PU | P2 | 352 | B46 | app/calendar/ | calendar testleri | — | hesap bilgisi | — |
| 354 | Cancel event | Yok (bilinçli silme sınırı) | Politikayla | MISSING | NYP | P2 | 678 | B46 | app/calendar/ | — | — | onay politikası | 366 ile ortak |
| 355 | RSVP | Yok | Çalışır | MISSING | NYP | P2 | 349 | B46 | app/calendar/ | — | — | hesap bilgisi | — |
| 356 | RRULE | Yazım yok | Tekrar kuralı yazılır | MISSING | NYP | P2 | 352 | B46 | app/calendar/ | — | — | no | — |
| 357 | VALARM | Yok | Hatırlatıcı yazılır | MISSING | NYP | P2 | 352 | B46 | app/calendar/ | — | — | no | — |
| 358 | Reminder | Yok | Hatırlatma | MISSING | NYP | P2 | 357 | B46 | app/calendar/, app/notifications/ | — | — | no | 367 ile bağlı |
| 359 | Calendar index | Tablo tanımlı, hiç yazılmıyor | Doldurulur | MISSING | NYP | P2 | 337 | B46 | app/calendar/ | — | — | no | Ölü tablo |
| 360 | Mail polling | Yok | Düzenli çekme | MISSING | NYP | P2 | 335 | B45 | app/mail/ | — | — | hesap bilgisi | — |
| 361 | Calendar sync | Yok | Eşitleme | MISSING | NYP | P2 | 359 | B46 | app/calendar/ | — | — | hesap bilgisi | — |
| 362 | Morning mail summary | Sağlayıcı yok | Çalışır | BLOCKED_PROVIDER | PU | P2 | 340 | B45 | app/briefing/ | — | — | hesap bilgisi | 278 ile aynı |
| 363 | Morning calendar summary | Sağlayıcı yok | Çalışır | BLOCKED_PROVIDER | PU | P2 | 350 | B46 | app/briefing/ | — | — | hesap bilgisi | 277 ile aynı |
| 364 | "Maillerime bak" | Niyet yok | Yönlenir | MISSING | NYP | P2 | 338 | B45 | app/voice/intent/ | — | 103 cümle ölçümü | no | 729 ile aynı iş |
| 365 | "Bu hafta ne var?" | Niyet yok | Yönlenir | MISSING | NYP | P2 | 351 | B46 | app/voice/intent/ | — | 103 cümle ölçümü | no | 730 ile aynı iş |
| 366 | "Perşembe toplantısını iptal et" | Niyet yok | Yönlenir | MISSING | NYP | P2 | 354 | B46 | app/voice/intent/ | — | 103 cümle ölçümü | no | 731 ile aynı iş |

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
| 393 | XLSX | Kod gerçek, üretimde hiç üretilmedi | Üretimde kanıtlanır | DONE | PA | P1 | — | B42 | app/artifacts/ | formül doğrulama testi | 0 üretim | no | Duman testi gerekir |
| 394 | PPTX | Kod gerçek, üretimde hiç üretilmedi | Üretimde kanıtlanır | DONE | PA | P1 | — | B42 | app/artifacts/ | artifacts testleri | 0 üretim | no | — |
| 395 | HTML | Çalışıyor | Aynı | DONE | PR | P1 | — | — | app/artifacts/ | artifacts testleri | üretim | no | — |
| 396 | Markdown | Çalışıyor | Aynı | DONE | PR | P1 | — | — | app/artifacts/ | artifacts testleri | üretim | no | — |
| 397 | TXT | Çalışıyor | Aynı | DONE | PR | P1 | — | — | app/artifacts/ | artifacts testleri | üretim | no | — |
| 398 | CSV | Kod gerçek, üretimde hiç üretilmedi | Üretimde kanıtlanır | DONE | PA | P1 | — | B42 | app/artifacts/ | artifacts testleri | 0 üretim | no | — |
| 399 | JSON | Kod gerçek, üretimde hiç üretilmedi | Üretimde kanıtlanır | DONE | PA | P1 | — | B42 | app/artifacts/ | artifacts testleri | 0 üretim | no | — |
| 400 | Image artifact | Kısmi | Tam | PARTIAL | NYP | P2 | 492 | B42 | app/artifacts/, app/creative/ | — | — | no | — |
| 401 | Deterministic render | Aynı girdi aynı bayt | Aynı | DONE | PA | P1 | — | — | app/artifacts/ | belirlenimlilik testi | — | no | OOXML zip normalize |
| 402 | Independent read-back | Üretilen dosya yeniden açılıyor | Aynı | DONE | PA | P1 | — | — | app/artifacts/ | read-back testleri | — | no | Ürün ilkesi uygulanmış |
| 403 | Reopen validation | Çalışıyor | Aynı | DONE | PA | P1 | — | — | app/artifacts/ | read-back testleri | — | no | — |
| 404 | Spreadsheet formula validation | Toplamlar bağımsız yeniden hesaplanıyor | Aynı | DONE | PA | P1 | — | — | app/artifacts/ | formül testi | — | no | — |
| 405 | Provenance | Kısmi | Tam | PARTIAL | NYP | P2 | — | B42 | app/artifacts/ | — | — | no | 406-408'in şemsiyesi |
| 406 | Actor provenance | Aktör kolonu yok | Kim üretti kayıtlı | MISSING | NYP | P2 | 405 | B42 | app/artifacts/ | — | — | no | — |
| 407 | Library/runtime version provenance | Kaydedilmiyor | Kaydedilir | MISSING | NYP | P2 | 405 | B42 | app/artifacts/ | — | — | no | Belirlenimliliğin kanıtı için gerekli |
| 408 | Source manifest | Hiç yazılmıyor | Yazılır | MISSING | NYP | P2 | 405 | B42 | app/artifacts/ | — | — | no | — |
| 409 | Artifact versioning | Kısmi | Tam | PARTIAL | NYP | P2 | 405 | B42 | app/artifacts/ | — | — | no | — |
| 410 | Artifact edit | Yok (değişen spec yeni artefakt) | Düzenleme | MISSING | NYP | P2 | 409 | B42 | app/artifacts/ | — | — | no | — |
| 411 | Artifact clone | Yok | Kopyalama | MISSING | NYP | P2 | 409 | B42 | app/artifacts/ | — | — | no | — |
| 412 | Artifact delete policy | Yok (bilinçli) | Politikayla silme | MISSING | NYP | P2 | 678 | B42 | app/artifacts/ | — | — | onay politikası | — |
| 413 | Export to owner disk | Tek kullanımlık 10 dk jetonla cihaza teslim | Aynı | DONE | PR | P1 | — | — | app/artifacts/, devices/windows-agent | artifacts testleri | üretim teslimi | no | Rewrite gerekmez |
| 414 | Artifact narration | Yok | Seslendirilir | MISSING | NYP | P1 | 224 | B21 | app/narration/, app/artifacts/ | — | — | TTS kotası | — |
| 415 | Artifact compare | Yok | Karşılaştırma | MISSING | NYP | P2 | 409 | B42 | app/artifacts/ | — | — | no | — |
| 416 | Artifact diff | Yok | Fark | MISSING | NYP | P2 | 415 | B42 | app/artifacts/ | — | — | no | — |

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
| 422 | General requirements parser | Ayrıştırıyor sonra sessizce atıyor | Kullanılır | PARTIAL | PA | P2 | 425 | B40 | app/appfactory/ | appfactory testleri | — | no | Sessiz veri kaybı |
| 423 | Architecture planner | Yok | Planlar | MISSING | NYP | P2 | 425 | B40 | app/appfactory/ | — | — | no | — |
| 424 | Project planner | Yok | Planlar | MISSING | NYP | P2 | 423 | B40 | app/appfactory/ | — | — | no | — |
| 425 | Model-backed code generation | `ClaudeAppGenerator` koşulsuz hata | Model üretir | MISSING | NYP | P2 | 417 | B40 | app/appfactory/ | — | — | model bütçesi | Atıl seam |
| 426 | Multiple source files | Kısmi | Tam | PARTIAL | NYP | P2 | 425 | B40 | app/appfactory/ | — | — | no | — |
| 427 | Database generation | Yok | Üretilir | MISSING | NYP | P2 | 425 | B40 | app/appfactory/ | — | — | no | — |
| 428 | API generation | Yok | Üretilir | MISSING | NYP | P2 | 425 | B40 | app/appfactory/ | — | — | no | — |
| 429 | Frontend generation | Kısmi (şablon) | Üretilir | PARTIAL | NYP | P2 | 425 | B40 | app/appfactory/ | — | — | no | — |
| 430 | Auth generation | Yok | Üretilir | MISSING | NYP | P2 | 425 | B40 | app/appfactory/ | — | — | no | — |
| 431 | Test generation | Yok | Üretilir | MISSING | NYP | P2 | 425 | B40 | app/appfactory/ | — | — | no | — |
| 432 | Unit tests | Yok | Koşar | MISSING | NYP | P2 | 431 | B40 | app/appfactory/ | — | — | no | — |
| 433 | Integration tests | Yok | Koşar | MISSING | NYP | P2 | 431 | B40 | app/appfactory/ | — | — | no | — |
| 434 | Browser tests | Yok | Koşar | MISSING | NYP | P2 | 431 | B40 | app/appfactory/ | — | — | no | — |
| 435 | Failed test analysis | Yok | Analiz eder | MISSING | NYP | P2 | 432 | B40 | app/appfactory/ | — | — | no | SelfDev'deki motor örnek alınmalı |
| 436 | Automated bug fix | Yok | Düzeltir | MISSING | NYP | P2 | 435 | B40 | app/appfactory/ | — | — | no | — |
| 437 | Retry loop | Yok | Döngü | MISSING | NYP | P2 | 436 | B40 | app/appfactory/ | — | — | no | — |
| 438 | Lint | Yok | Koşar | MISSING | NYP | P2 | 425 | B40 | app/appfactory/ | — | — | no | — |
| 439 | Security scan | Yok | Koşar | MISSING | NYP | P2 | 438 | B40 | app/appfactory/, app/security/ | — | — | no | 680 ile ortak |
| 440 | Build | Kısmi | Çalışır | PARTIAL | NYP | P2 | 417 | B41 | app/appfactory/ | — | — | no | — |
| 441 | Package | Kısmi | Çalışır | PARTIAL | NYP | P2 | 440 | B41 | app/appfactory/ | — | — | no | — |
| 442 | Launch | Yok | Çalıştırılır | MISSING | NYP | P2 | 441 | B41 | app/appfactory/, devices/windows-agent | — | — | no | — |
| 443 | UI verification | Yok | Doğrulanır | MISSING | NYP | P2 | 442,463 | B41 | devices/windows-agent | — | — | no | UIA'ya bağlı |
| 444 | Persistence verification | Yok | Doğrulanır | MISSING | NYP | P2 | 442 | B41 | devices/windows-agent | — | — | no | — |
| 445 | Log read-back | Yok | Okunur | MISSING | NYP | P2 | 442 | B41 | devices/windows-agent | — | — | no | — |
| 446 | Release artifact | Yok | Üretilir | MISSING | NYP | P2 | 441 | B41 | app/appfactory/ | — | — | no | — |
| 447 | Project history | Kısmi | Tam | PARTIAL | NYP | P2 | — | B41 | app/appfactory/ | — | — | no | — |
| 448 | Resume development later | Yok | Devam edilir | MISSING | NYP | P2 | 447 | B41 | app/appfactory/ | — | — | no | — |
| 449 | Modify existing generated app | Yok | Değiştirilir | MISSING | NYP | P2 | 448 | B41 | app/appfactory/ | — | — | no | — |
| 450 | "Bu uygulamaya şu özelliği ekle" | Yok | Çalışır | MISSING | NYP | P2 | 449 | B41 | app/voice/intent/ | — | — | no | — |
| 451 | "Bu bug'ı düzelt" | Yok | Çalışır | MISSING | NYP | P2 | 449,436 | B41 | app/voice/intent/ | — | — | no | 622 ile karıştırılmamalı |
| 452 | Arbitrary bounded app request | Yok | Sınırlı serbest istek | MISSING | NYP | P2 | 425 | B41 | app/appfactory/ | — | — | no | App Factory'nin nihai hedefi |

## O. NATIVE APP FACTORY (453–480)

> Ölçüm: üretimden tetiklenen gerçek cihaz derlemesi kanıtlı (notlarim.exe, 162.304 bayt,
> sha256 1a73dab4..., PeImageReader ile doğrulandı). Ama yaşam döngüsünün kalanı ölü.

| ID | FEATURE | CURRENT_STATUS | TARGET_STATUS | IMPL | PROOF | PRI | DEPS | BATCH | SOURCE_REFERENCES | TEST_REFERENCES | RUNTIME_PROOF | OWNER_ACTION | NOTES |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 453 | NativeAppSpec | Çalışıyor | Aynı | DONE | PA | P1 | — | — | app/nativefactory/ | nativefactory testleri | — | no | — |
| 454 | WPF generation | Çalışıyor | Aynı | DONE | PR | P1 | — | — | app/nativefactory/ | nativefactory testleri | 26.16 | no | Rewrite gerekmez |
| 455 | EXE | Çalışıyor | Aynı | DONE | PR | P1 | — | — | app/nativefactory/, devices/windows-agent | PeImageReader testleri | notlarim.exe 162.304 B | no | — |
| 456 | Portable ZIP | Üretimden erişilemez | Erişilebilir | PARTIAL | NYP | P1 | — | B33 | devices/windows-agent | — | — | no | — |
| 457 | MSIX | Üretimden erişilemez | Erişilebilir | PARTIAL | NYP | P1 | 472 | B33 | devices/windows-agent | — | — | imza kararı | — |
| 458 | PE validation | Bağımsız doğrulama var | Aynı | DONE | PA | P0 | — | — | devices/windows-agent | PeImageReader (15 test) | sha256 1a73dab4 | no | 28: CI'da koşmuyor |
| 459 | Build identity | Çalışıyor | Aynı | DONE | PR | P1 | — | — | devices/windows-agent | — | build 19f079c4fda2c3c7 | no | — |
| 460 | Device build | Çalışıyor | Aynı | DONE | PR | P1 | — | — | devices/windows-agent | — | 26.16 | no | — |
| 461 | Cloud-triggered device build | Çalışıyor | Aynı | DONE | PR | P1 | — | — | app/nativefactory/device_build.py | NativeManifestContractTests.cs | 26.16 | no | Sözleşme fikstürü burada doğdu |
| 462 | App launch | `native.launch` ölü (arka uç yok) | Çalışır | MISSING | NYP | P1 | — | B33 | app/nativefactory/ | — | — | no | Ölü araç |
| 463 | UI Automation | Yok | Doğrular | MISSING | NYP | P1 | 462,99 | B33 | devices/windows-agent | — | — | no | — |
| 464 | Relaunch | Yok | Çalışır | MISSING | NYP | P1 | 462 | B33 | devices/windows-agent | — | — | no | — |
| 465 | Persistence | Yok | Doğrulanır | MISSING | NYP | P1 | 462 | B33 | devices/windows-agent | — | — | no | — |
| 466 | App log | Yok | Okunur | MISSING | NYP | P1 | 462 | B33 | devices/windows-agent | — | — | no | — |
| 467 | Test count parsing | `counts_parsed` okunuyor; sayılamayan koşu `tests_unreadable` | Dürüst sayı | DONE | PA | P0 | 5 | B03 | app/nativefactory/device_build.py | test_nativefactory_device_build.py (20, 3 yeni regresyon) | — | no | `exit_code: None` da artık geçer not değil |
| 468 | Native install | Ölü araç | Çalışır | MISSING | NYP | P1 | — | B33 | app/nativefactory/ | — | — | no | — |
| 469 | Native uninstall | Yok | Çalışır | MISSING | NYP | P1 | 468 | B33 | app/nativefactory/ | — | — | no | — |
| 470 | Native fix | Ölü araç | Çalışır | MISSING | NYP | P1 | 462 | B33 | app/nativefactory/ | — | — | no | — |
| 471 | Native update | Yok | Çalışır | MISSING | NYP | P1 | 468 | B33 | app/nativefactory/ | — | — | no | — |
| 472 | Signing policy | Yok | Politika | MISSING | NYP | P1 | — | B33 | app/nativefactory/, app/security/ | — | — | sertifika kararı | — |
| 473 | Signing optional/test cert | Yok | Test sertifikası | MISSING | NYP | P1 | 472 | B33 | devices/windows-agent | — | — | sertifika kararı | — |
| 474 | Android project generation | Yok | Üretilir | MISSING | NYP | P3 | 425 | B49 | app/nativefactory/ | — | — | SDK kurulumu | v1.0'ı bloklamaz |
| 475 | APK | Yok | Üretilir | MISSING | NYP | P3 | 474 | B49 | app/nativefactory/ | — | — | SDK kurulumu | — |
| 476 | AAB | Yok | Üretilir | MISSING | NYP | P3 | 475 | B49 | app/nativefactory/ | — | — | SDK kurulumu | — |
| 477 | Android emulator test | Yok | Koşar | MISSING | NYP | P3 | 475 | B49 | app/nativefactory/ | — | — | SDK kurulumu | — |
| 478 | Android device test | Yok | Koşar | MISSING | NYP | P3 | 477 | B49 | app/nativefactory/ | — | — | fiziksel cihaz | — |
| 479 | iOS explicit NOT_SUPPORTED without macOS | Açık beyan yok | Açıkça reddedilir | MISSING | NYP | P2 | — | B49 | app/nativefactory/ | — | — | no | Ucuz ve dürüst; Unity lisans reddi örnek |
| 480 | Generated app auto-update later | Yok | Sonra | DEFERRED | NYP | P3 | 449 | B41 | app/appfactory/ | — | — | no | v1.0'ı bloklamaz |

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
| 489 | Background removal | Yok | Çalışır | MISSING | NYP | P2 | 492 | B43 | app/creative/ | — | — | sağlayıcı kararı | — |
| 490 | Object removal | Yok | Çalışır | MISSING | NYP | P2 | 492 | B43 | app/creative/ | — | — | sağlayıcı kararı | — |
| 491 | Object addition | Yok | Çalışır | MISSING | NYP | P2 | 492 | B43 | app/creative/ | — | — | sağlayıcı kararı | — |
| 492 | Image generation | Sağlayıcı, uç nokta, model kimliği YOK | Çalışır | MISSING | NYP | P2 | — | B43 | app/creative/ | — | — | sağlayıcı hesabı | 489-495'in ön koşulu |
| 493 | Image style transformation | Yok | Çalışır | MISSING | NYP | P2 | 492 | B43 | app/creative/ | — | — | sağlayıcı kararı | — |
| 494 | Image enhancement | Yok | Çalışır | MISSING | NYP | P2 | 492 | B43 | app/creative/ | — | — | sağlayıcı kararı | — |
| 495 | Upscale | Yok | Çalışır | MISSING | NYP | P2 | 492 | B43 | app/creative/ | — | — | sağlayıcı kararı | — |
| 496 | OCR | Yok | Çalışır | MISSING | NYP | P1 | — | B32 | app/creative/, devices/windows-agent | — | — | sağlayıcı kararı | 140 ile AYNI iş |
| 497 | Independent pixel validation | Çalışıyor | Aynı | DONE | PA | P2 | — | — | app/creative/ | piksel testleri | — | no | Ürün ilkesi uygulanmış |
| 498 | Visual semantic validation | Yok | Çalışır | MISSING | NYP | P2 | 105 | B43 | app/creative/ | — | — | sağlayıcı kararı | — |
| 499 | Paint open/display | Algılama + açma var | Aynı | DONE | PA | P2 | — | — | app/creative/, devices/windows-agent | creative testleri | — | no | — |
| 500 | Paint real UI operation | Modül düzeyinde "asla çalıştırma" reddi | Gerçekten sürülür | MISSING | NYP | P2 | 95,100 | B43 | devices/windows-agent | — | — | no | Operatör tıklamasına bağlı |
| 501 | Photoshop detection | Çalışıyor | Aynı | DONE | PA | P2 | — | — | app/creative/ | creative testleri | — | no | — |
| 502 | Photoshop driver | Red | Sürücü | MISSING | NYP | P3 | 500 | B43 | app/creative/ | — | — | lisans | v1.0'ı bloklamaz |
| 503 | Illustrator detection | Çalışıyor | Aynı | DONE | PA | P2 | — | — | app/creative/ | creative testleri | — | no | — |
| 504 | Illustrator driver | Red | Sürücü | MISSING | NYP | P3 | 500 | B43 | app/creative/ | — | — | lisans | v1.0'ı bloklamaz |
| 505 | Figma integration | `creative.design` varsayılanı Paint | Varsayılan çalışan yola | DONE | PA | P0 | — | B03 | app/voice/realtime_sessions/tools_creative.py | test_creative_tools.py::..._reaches_a_tool_that_can_work | — | Figma jetonu | Figma jetonu hâlâ bağlı değil; adıyla istenirse dürüst ret veriyor (kasıtlı) |
| 506 | Layer-aware editing | Yok | Çalışır | MISSING | NYP | P2 | 507 | B43 | app/creative/ | — | — | no | — |
| 507 | PSD support | Yok | Çalışır | MISSING | NYP | P2 | — | B43 | app/creative/ | — | — | no | — |
| 508 | SVG support | Yok | Çalışır | MISSING | NYP | P2 | — | B43 | app/creative/ | — | — | no | — |
| 509 | Export to owner disk | Çıktı yalnız MinIO'da | Diske teslim | MISSING | NYP | P2 | 413 | B43 | app/creative/, devices/windows-agent | — | — | no | Artefakt teslim yolu örnek alınmalı |
| 510 | Creative history | Panel veri gösteriyor | Çalışır | DONE | PA | P0 | 715 | B03 | app/creative/routes.py | test_web_asks_for_routes_that_exist.py (12) | — | no | 715 ile aynı düzeltme |
| 511 | Undo/redo | Yok | Çalışır | MISSING | NYP | P2 | 160 | B43 | app/creative/ | — | — | no | — |
| 512 | "Bu fotoğrafı düzelt" | Yok | Çalışır | MISSING | NYP | P2 | 494 | B43 | app/voice/intent/ | — | — | no | — |

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
| 520 | Scene inspect | Kısmi | Tam | PARTIAL | NYP | P2 | — | B44 | app/creative3d/ | — | — | no | — |
| 521 | Production scene creation | Üretimde 0 sahne; REST yolu yok | Üretimde sahne | MISSING | NYP | P2 | — | B44 | app/creative3d/ | — | 0 sahne | no | Yalnız sesli araç var, hiç çağrılmamış |
| 522 | Modify existing scene | Yok | Değişir | MISSING | NYP | P2 | 521 | B44 | app/creative3d/ | — | — | no | — |
| 523 | Material control | Kısmi | Tam | PARTIAL | NYP | P2 | 521 | B44 | app/creative3d/ | — | — | no | — |
| 524 | Lighting control | Kısmi | Tam | PARTIAL | NYP | P2 | 521 | B44 | app/creative3d/ | — | — | no | — |
| 525 | Camera control | Kısmi | Tam | PARTIAL | NYP | P2 | 521 | B44 | app/creative3d/ | — | — | no | — |
| 526 | Animation | Yok | Çalışır | MISSING | NYP | P2 | 522 | B44 | app/creative3d/ | — | — | no | — |
| 527 | Export FBX/GLTF | Yok | Çalışır | MISSING | NYP | P2 | 521 | B44 | app/creative3d/ | — | — | no | — |
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
| 536 | Step preconditions | Kısmi | Tam | PARTIAL | NYP | P2 | — | B38 | app/executive/ | — | — | no | — |
| 537 | Step postconditions | Kısmi | Tam | PARTIAL | NYP | P2 | 536 | B38 | app/executive/ | — | — | no | 111 ile aynı ilke |
| 538 | Retry | Kısmi | Tam | PARTIAL | NYP | P2 | — | B38 | app/executive/ | — | — | no | — |
| 539 | Compensation | Bir dal hiçbir şey yapmadan "telafi edildi" diyor | Gerçek telafi | DONE | PA | P0 | — | B10 | app/executive/activities.py:_run_compensation | test_execution_honesty.py | üretim turu bekliyor (Karar 0) | no | Koşulsuz 'compensated' bitti: undone / nothing_to_undo / attempted_and_failed |
| 540 | Pause | Gerçek Temporal sinyali | Aynı | DONE | PA | P1 | — | — | app/executive/ | executive testleri | — | no | — |
| 541 | Resume | Gerçek Temporal sinyali | Aynı | DONE | PA | P1 | — | — | app/executive/ | executive testleri | — | no | — |
| 542 | Cancel | Gerçek Temporal sinyali | Aynı | DONE | PA | P1 | — | — | app/executive/ | executive testleri | — | no | Telafi yalnız iptalde çalışıyor |
| 543 | Modify | Gerçek Temporal sinyali | Aynı | DONE | PA | P1 | — | — | app/executive/ | executive testleri | — | no | — |
| 544 | Human approval step | Kısmi | Tam | PARTIAL | NYP | P2 | 383 | B38 | app/executive/ | — | — | no | — |
| 545 | Task history | Çalışıyor | Aynı | DONE | PA | P1 | — | — | app/executive/ | executive testleri | — | no | — |
| 546 | Task explanation | Kısmi | Tam | PARTIAL | NYP | P2 | 558 | B38 | app/executive/ | — | — | no | — |
| 547 | Research-report plan | Çalışıyor | Aynı | DONE | PR | P1 | — | — | app/executive/ | executive testleri | üretim | no | — |
| 548 | Folder-compare plan | file.search kusuruna bağımlı | Çalışır | DONE | PA | P0 | 3 | B10 | app/executive/planner.py | test_executive_planner.py | üretim turu bekliyor (Karar 0) | no | B03'ün file.search sözleşmesi düzeltti; klasör planı artık çalışan bir yeteneğe dayanıyor |
| 549 | Mail-sequence plan | Sağlayıcı yok | Çalışır | BLOCKED_PROVIDER | PU | P2 | 336 | B38 | app/executive/ | executive testleri | — | hesap bilgisi | — |
| 550 | General model planner | `NotImplementedError` | Model planlar | MISSING | NYP | P2 | — | B38 | app/executive/ | — | — | model bütçesi | Atıl seam |
| 551 | Dynamic plan generation | Yok | Dinamik | MISSING | NYP | P2 | 550 | B38 | app/executive/ | — | — | no | — |
| 552 | Dynamic step selection | 15 adım türünün 8'i erişilebilir | Hepsi erişilebilir | MISSING | NYP | P2 | 551 | B38 | app/executive/ | — | — | no | Belge çıkarma, artefakt render, takvim, uygulama/sahne adımları ölü |
| 553 | Parallel steps | Yok | Paralel | MISSING | NYP | P2 | 551 | B38 | app/executive/ | — | — | no | — |
| 554 | Conditional branches | Yok | Koşullu | MISSING | NYP | P2 | 551 | B38 | app/executive/ | — | — | no | — |
| 555 | Loop steps | Yok | Döngü | MISSING | NYP | P2 | 551 | B38 | app/executive/ | — | — | no | — |
| 556 | Timeout policy | Kısmi | Tam | PARTIAL | NYP | P2 | — | B38 | app/executive/ | — | — | no | — |
| 557 | Recovery from partial failure | Kısmi | Tam | PARTIAL | NYP | P2 | 539 | B38 | app/executive/ | — | 5 koşunun 4'ü partial | no | — |
| 558 | Correct final status | Yanlış | Doğru | DONE | PA | P0 | — | B10 | app/executive/activities.py, app/executive/service.py, app/executive/models.py | test_execution_honesty.py | üretim turu bekliyor (Karar 0) | no | steps_done artık yalnız VERIFIED sayıyor |
| 559 | No false "4/4 completed" | İki koşu yalan söylüyor | Dürüst | DONE | PA | P0 | 558 | B10 | app/executive/activities.py, app/executive/service.py, app/executive/models.py | test_execution_honesty.py | üretim turu bekliyor (Karar 0) | no | '4/4' kırmızıyla kanıtlandı: assert 4 == 1 |
| 560 | Task reconciliation | Yok | Mutabakat | DONE | PA | P0 | 11 | B10 | app/executive/reconcile.py, app/main.py | test_executive_reconcile.py (31) | üretim turu bekliyor (Karar 0) | no | ÖLÇÜM DÜZELTMESİ: MISSING değildi — kod var, saate bağlı, 31 testi geçiyor |

## S. CAPABILITY GENESIS (561–580)

> Ölçüm: motor gerçek ve uçtan uca kanıtlı (canlı bir fikstüre karşı gerçek HTTP adaptörü
> üretip çalıştırıyor). Ama **ön kapısı yok**: katalog boş kuruluyor, `.register()` çağıran
> üretim kodu yok, `POST /v1/genesis/runs` rotası yok. Genesis kapalı değil — **erişilemez**.

| ID | FEATURE | CURRENT_STATUS | TARGET_STATUS | IMPL | PROOF | PRI | DEPS | BATCH | SOURCE_REFERENCES | TEST_REFERENCES | RUNTIME_PROOF | OWNER_ACTION | NOTES |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 561 | Capability request | Rota yok | Talep alınır | MISSING | NYP | P2 | — | B36 | app/genesis/ | — | — | no | Ön kapının kendisi |
| 562 | Catalogue | Boş kuruluyor | Dolu | PARTIAL | PA | P2 | 563 | B36 | app/genesis/ | genesis testleri | /v1/genesis/capabilities boş | no | — |
| 563 | Catalogue registration | `.register()` çağıranı yok | Kayıt yüzeyi | MISSING | NYP | P2 | — | B36 | app/genesis/ | — | — | no | Kataloğun boş olmasının NEDENİ |
| 564 | Discovery | Kısmi | Tam | PARTIAL | PA | P2 | 562 | B36 | app/genesis/ | genesis testleri | — | no | — |
| 565 | Interface inspection | Loopback HTTP uygulaması istiyor | Genel | PARTIAL | PA | P2 | 564 | B36 | app/genesis/ | genesis testleri | — | no | Dar ön koşul |
| 566 | Adapter generation | Çalışıyor | Aynı | DONE | PA | P2 | — | — | app/genesis/ | canlı fikstüre karşı üretim | — | no | Rewrite gerekmez |
| 567 | Adapter tests | Çalışıyor | Aynı | DONE | PA | P2 | — | — | app/genesis/ | genesis testleri | — | no | — |
| 568 | Sandbox | Çalışıyor | Aynı | DONE | PA | P2 | — | — | app/genesis/ | genesis testleri | — | no | — |
| 569 | Registration | Kısmi | Tam | PARTIAL | PA | P2 | 563 | B36 | app/genesis/ | genesis testleri | — | no | — |
| 570 | Activation | Kısmi | Tam | PARTIAL | PA | P2 | 569 | B36 | app/genesis/ | genesis testleri | — | no | — |
| 571 | Versioning | Kısmi | Tam | PARTIAL | NYP | P2 | 570 | B36 | app/genesis/ | — | — | no | — |
| 572 | Rollback | Kısmi | Tam | PARTIAL | NYP | P2 | 571 | B36 | app/genesis/ | — | — | no | — |
| 573 | Production capability use | Hiç kullanılmamış | Üretimde kullanılır | MISSING | NYP | P2 | 570 | B36 | app/genesis/ | — | — | no | — |
| 574 | Genesis REST | `GET capabilities` var, `POST runs` yok | Tam | PARTIAL | PA | P2 | 561 | B36 | app/genesis/ | genesis testleri | — | no | — |
| 575 | Genesis Voice tool | Yok | Sesli araç | MISSING | NYP | P2 | 574 | B36 | app/voice/tools/ | — | — | no | — |
| 576 | Genesis Cockpit UI | Yok | Arayüz | MISSING | NYP | P2 | 574 | B36 | apps/web/ | — | — | no | — |
| 577 | Model-backed capability generation | Kapalı | Açılır (kontrollü) | MISSING | NYP | P2 | 579 | B36 | app/genesis/ | — | — | model bütçesi | Ön kapı yokken açılmamalı |
| 578 | Restricted app-only generation | 5 fonksiyonluk kapalı liste | Genişler | PARTIAL | PA | P2 | 577 | B36 | app/genesis/ | genesis testleri | — | no | — |
| 579 | Security gate | Yok | Zorunlu kapı | MISSING | NYP | P2 | 680 | B36 | app/genesis/, app/security/ | — | — | no | 577'nin ÖN KOŞULU |
| 580 | Owner approval | Yok | Sahip onayı | MISSING | NYP | P2 | 579 | B36 | app/genesis/, app/security/ | — | — | onay akışı | — |

## T. SELF-DEVELOPMENT ENGINE (581–625)

> Ölçüm: motor **gerçek ve kanıtlı** — 7 gerçek `selfdev/*` dalı, gerçek worktree, gerçek
> pytest, gerçek bir kusuru düzelten aday `db9ed85`, politika sınırında `STOPPED_AT_POLICY_BOUNDARY`.
> Ama **çalışan sistemden erişilemez**: rota yok, araç yok, zamanlayıcı yok, üretim imajında
> git checkout yok. Kusur girişi elle yazılan JSON. Anayasa gereği otonom dağıtım yasak.

| ID | FEATURE | CURRENT_STATUS | TARGET_STATUS | IMPL | PROOF | PRI | DEPS | BATCH | SOURCE_REFERENCES | TEST_REFERENCES | RUNTIME_PROOF | OWNER_ACTION | NOTES |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 581 | Defect intake | Elle yazılan JSON | Ürün yüzeyi | PARTIAL | PA | P2 | — | B35 | app/selfdev/ | test_selfdev_engine.py | 7 koşu | no | Erişilebilirlik eksik |
| 582 | Opportunity intake | Çalışıyor | Aynı | DONE | PR | P2 | — | — | app/evolution/ | evolution testleri | 14 fırsat "idea"da | no | — |
| 583 | Opportunity to defect bridge | Yok | Köprü | MISSING | NYP | P2 | 581,582 | B35 | app/evolution/, app/selfdev/ | — | 14 fırsat bekliyor | no | En değerli tek bağlantı |
| 584 | Risk classification | Tier tablosu çalışıyor | Aynı | DONE | PA | P0 | — | — | app/evolution/risk.py | risk testleri | tier 5: supervisor.py, app/selfdev/ | no | Rewrite gerekmez |
| 585 | Promotion class | Hesaplanıyor, tüketen yok | Tüketilir | PARTIAL | PA | P2 | 584 | B35 | app/evolution/supervisor.py | supervisor testleri | — | no | Otonom dağıtımın 4. bariyeri |
| 586 | Real git branch | Çalışıyor | Aynı | DONE | PR | P2 | — | — | app/selfdev/workspace.py | test_selfdev_engine.py | 7 `selfdev/*` dalı | no | — |
| 587 | Real git worktree | Çalışıyor | Aynı | DONE | PR | P2 | — | — | app/selfdev/workspace.py | test_selfdev_engine.py | 2 canlı worktree | no | — |
| 588 | Codebase analysis | Çalışıyor | Aynı | DONE | PA | P2 | — | — | app/selfdev/ | test_selfdev_anthropic_model.py | — | no | — |
| 589 | Architecture planning | Kısmi | Tam | PARTIAL | PA | P2 | 588 | B35 | app/selfdev/ | test_selfdev_anthropic_model.py | — | no | — |
| 590 | Patch generation | Çalışıyor (SEARCH/REPLACE blok formatı) | Aynı | DONE | PR | P2 | — | — | app/selfdev/anthropic_model.py | test_selfdev_anthropic_model.py | aday db9ed85 | no | Düz dize formatı çözdü |
| 591 | New module generation | Çalışıyor | Aynı | DONE | PA | P2 | — | — | app/selfdev/ | test_selfdev_engine.py | — | no | — |
| 592 | Regression test generation | Çalışıyor | Aynı | DONE | PA | P2 | — | — | app/selfdev/ | test_selfdev_engine.py | — | no | Plan kendi testini adlandırabiliyor |
| 593 | Targeted tests | Adayın kendi worktree'sinde gerçek pytest | Aynı | DONE | PR | P2 | — | — | app/selfdev/runner.py | test_selfdev_cli.py | — | no | — |
| 594 | Failure analysis | Çalışıyor | Aynı | DONE | PR | P2 | — | — | app/selfdev/ | test_selfdev_engine.py | teşhis-düzeltme döngüsü | no | — |
| 595 | Automated fix | Artımlı (carry) düzeltme | Aynı | DONE | PR | P2 | — | — | app/selfdev/anthropic_model.py | test_selfdev_anthropic_model.py | — | no | — |
| 596 | Retry | Çalışıyor | Aynı | DONE | PA | P2 | — | — | app/selfdev/engine.py | test_selfdev_engine.py | — | no | — |
| 597 | Static review | `ruff check --fix` (yalnız güvenli) | Aynı | DONE | PA | P2 | — | — | app/selfdev/runner.py, reviewer.py | test_selfdev_engine.py | — | no | — |
| 598 | Security review | YOK | Zorunlu adım | MISSING | NYP | P0 | 680 | B35 | app/selfdev/reviewer.py | — | Grant.SECURITY_REVIEW_CANDIDATE tüketicisi yok | no | Güvenlik boşluğu |
| 599 | General code review | IndependentReviewer çalışıyor | Aynı | DONE | PA | P2 | — | — | app/selfdev/reviewer.py | test_selfdev_engine.py | regresyon kırmızı to yeşil | no | Rewrite gerekmez |
| 600 | Full quality gate | Kısmi | Tam | PARTIAL | PA | P2 | 25 | B35 | app/selfdev/runner.py | test_selfdev_cli.py | — | no | — |
| 601 | CI trigger | Kısmi | Tam | PARTIAL | NYP | P2 | 600 | B35 | app/selfdev/ | — | — | no | — |
| 602 | CI result read | `gh run list` çalışıyor | Aynı | DONE | PR | P2 | — | — | app/selfdev/ | — | CI okuma | no | — |
| 603 | CI failure to code fix loop | Yok (CI commit'ten sonra okunuyor) | Döngüye döner | MISSING | NYP | P2 | 602 | B35 | app/selfdev/engine.py | — | — | no | Döngünün açık ucu |
| 604 | Candidate commit | Çalışıyor | Aynı | DONE | PR | P2 | — | — | app/selfdev/engine.py | test_selfdev_engine.py | db9ed85 | no | — |
| 605 | Candidate evidence | record.json, model-exchanges.json, candidate.diff | Aynı | DONE | PR | P2 | — | — | app/selfdev/engine.py | test_selfdev_engine.py | — | no | `write_bytes` ile CRLF sorunu çözüldü |
| 606 | Candidate history | Çalışıyor | Aynı | DONE | PR | P2 | — | — | app/selfdev/ | test_selfdev_engine.py | 7 kayıt | no | — |
| 607 | Candidate quarantine | Çalışıyor | Aynı | DONE | PA | P2 | — | — | app/selfdev/engine.py | test_selfdev_engine.py | — | no | — |
| 608 | Candidate shadow | Yok | Gölge | MISSING | NYP | P2 | 607 | B35 | app/selfdev/ | — | — | no | Anayasadaki zorunlu yol |
| 609 | Owner approval | Yüzey yok | Onay yüzeyi | MISSING | NYP | P2 | 606 | B35 | app/selfdev/, apps/web/ | — | — | onay akışı | 621 ile ortak |
| 610 | Blue/green release | Çalışıyor | Aynı | DONE | PR | P0 | — | — | scripts/cloud/release-cloud-core.ps1 | release testleri | 3 üretim sürümü | no | 1,2 kusurları hariç |
| 611 | Rollback | Çalışıyor | Aynı | DONE | PR | P0 | — | — | scripts/cloud/ | release testleri | 16 geri alma | no | — |
| 612 | Last-known-good | Çalışıyor | Aynı | DONE | PR | P0 | — | — | state/, app/system/health.py | release testleri | LKG d86b3d9 | no | — |
| 613 | Post-release health | Çalışıyor | Aynı | DONE | PR | P0 | — | — | app/system/health.py | release testleri | 18 bileşen ok | no | — |
| 614 | Post-release rollback | Kısmi (timer yok) | Otomatik | PARTIAL | PA | P0 | 652 | B08 | services/recovery-supervisor/, scripts/cloud/install-recovery-supervisor.sh | test_systemd_install.py | host kurulumu bekliyor (sahip) | no | 651/652 ile ortak: timer kurulmadan otomatik değil |
| 615 | Scheduler | Yok | Zamanlayıcı | MISSING | NYP | P2 | 583 | B35 | app/selfdev/ | — | — | no | — |
| 616 | Attempt budget | Çalışıyor | Aynı | DONE | PA | P2 | — | — | app/selfdev/engine.py | test_selfdev_engine.py | — | no | — |
| 617 | Time budget | Çalışıyor | Aynı | DONE | PA | P2 | — | — | app/selfdev/engine.py | test_selfdev_engine.py | — | no | — |
| 618 | Token/model budget | Kısmi | Tam | PARTIAL | NYP | P2 | — | B35 | app/selfdev/ | — | — | model bütçesi | — |
| 619 | Disk budget | Yok | Sınır | MISSING | NYP | P2 | — | B35 | app/selfdev/workspace.py | — | 2 canlı worktree | no | Worktree birikimi riski |
| 620 | Parallel candidate limit | Yok | Sınır | MISSING | NYP | P2 | 619 | B35 | app/selfdev/ | — | — | no | — |
| 621 | SelfDev Cockpit | Yok | Arayüz | MISSING | NYP | P2 | 606 | B35 | apps/web/ | — | — | no | 697 ile aynı sayfa |
| 622 | "Şu bug'ı kendin düzelt" | Yok | Sesle atama | MISSING | NYP | P2 | 581 | B35 | app/voice/tools/ | — | — | no | — |
| 623 | "Şu özelliği kendine ekle" | Yok | Sesle atama | MISSING | NYP | P2 | 622 | B35 | app/voice/tools/ | — | — | no | — |
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
| 660 | Panic revoke | Var ama keşfedilebilir değil | Erişilebilir | DONE | PA | P0 | — | B25 | app/identity/ | identity testleri | — | no | Arayüzde görünmüyor |
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
| 671 | Camera permission | Kısmi | Tam | PARTIAL | NYP | P2 | 327 | B48 | app/security/, app/presence/ | — | eye_enabled=false | kamera kararı | — |
| 672 | Browser permission | Çalışıyor | Aynı | DONE | PA | P0 | — | — | app/security/ | security testleri | — | no | — |
| 673 | Research permission | Gerçekten zorlanıyor | Aynı | DONE | PA | P0 | — | — | app/security/, app/research/ | research testleri | owner_authorized=false | no | ADR-0113 |
| 674 | File mutation permission | Mutasyon olmadığı için yok | İzin modeli | MISSING | NYP | P2 | 160 | B34 | app/security/, app/files/ | — | — | onay politikası | 153-170'in ön koşulu |
| 675 | Code promotion permission | Grant'lar var | Aynı | DONE | PA | P0 | — | B05 | app/evolution/routes.py:advance_opportunity | test_evolution_routes.py | üretim turu bekliyor (Karar 0) | no | Çıkış geçişi açıldı: üretim tarafından ÇIKMAK da üretim eylemi |
| 676 | Production deployment permission | Çalışıyor | Aynı | DONE | PA | P0 | — | — | app/evolution/ | supervisor testleri | — | no | — |
| 677 | High-risk owner gate | Çalışıyor | Aynı | DONE | PA | P0 | — | — | app/evolution/supervisor.py | supervisor testleri | — | no | Ürün ilkesi — gevşetilmez |
| 678 | Permanent delete owner gate | Kısmi | Tam | DONE | PA | P0 | — | B05 | app/security/deletion.py | test_deletion_gate.py | tüketiciler B34/B42/B46 | onay politikası | Sahip kararı 2026-09-13: yumuşak silme varsayılan, kalıcı yok etme ikinci kanal |
| 679 | Security audit ledger | Çalışıyor | Saklama politikası eklenir | DONE | PA | P0 | 16 | B07 | app/security/audit_retention.py | test_bounded_delivery.py | 13.560 olay → kuru koşu sayıyor | no | Activity Ledger ASLA süpürülmüyor: kanıt yaşlanmaz, gerekçesi yazılı |
| 680 | Security review of generated code | Yok | Zorunlu | MISSING | NYP | P0 | — | B35 | app/security/, app/selfdev/ | — | Grant tüketicisi yok | no | 598,579,439 ile ortak |
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
| 685 | Global navigation | YOK | Nav çubuğu | MISSING | NYP | P1 | — | B23 | apps/web/app/layout.tsx | — | layout yalnız body | no | Tüm web işlerinin ön koşulu |
| 686 | Home dashboard | Kısmi | Tam | PARTIAL | NYP | P1 | 685 | B23 | apps/web/ | — | — | no | — |
| 687 | Voice page | Çalışıyor | Aynı | DONE | PR | P1 | — | — | apps/web/ | web testleri | üretim | no | Rewrite gerekmez |
| 688 | Core page | Çalışıyor | Aynı | DONE | PR | P1 | — | — | apps/web/ | web testleri | üretim | no | — |
| 689 | Memory page | Yok | Sayfa | MISSING | NYP | P2 | 685,39 | B24 | apps/web/ | — | — | no | 57-60 ile aynı sayfa |
| 690 | Tasks page | Kısmi | Tam | PARTIAL | NYP | P1 | 685 | B23 | apps/web/ | — | — | no | — |
| 691 | Research page | Kısmi | Tam | PARTIAL | NYP | P1 | 685 | B23 | apps/web/ | — | — | no | 210 ile ortak |
| 692 | Artifacts page | Çıkışsız (dead end) | Çıkışlı | PARTIAL | NYP | P1 | 685 | B23 | apps/web/ | — | — | no | — |
| 693 | Routines page | Yok | Sayfa | MISSING | NYP | P1 | 685,287 | B24 | apps/web/ | — | — | no | 295 ile aynı sayfa |
| 694 | Alarm page | Yok | Sayfa | MISSING | NYP | P1 | 685 | B24 | apps/web/ | — | — | no | — |
| 695 | Device page | Kısmi | Tam | PARTIAL | NYP | P1 | 685 | B23 | apps/web/ | — | — | no | — |
| 696 | Security page | Yok | Sayfa | MISSING | NYP | P1 | 685,660 | B24 | apps/web/ | — | — | no | Panik anahtarı burada görünür |
| 697 | SelfDev page | Yok | Sayfa | MISSING | NYP | P2 | 685,606 | B24 | apps/web/ | — | — | no | 621 ile aynı sayfa |
| 698 | Notifications page | Yok | Sayfa | MISSING | NYP | P1 | 685,367 | B24 | apps/web/ | — | — | no | 368 ile aynı sayfa |
| 699 | Settings page | Yok | Sayfa | MISSING | NYP | P1 | 685 | B24 | apps/web/ | — | — | no | — |
| 700 | Feature availability page | Yok | Sayfa | MISSING | NYP | P1 | 685 | B24 | apps/web/ | — | — | no | 78,712,713 ile ortak veri |
| 701 | "Neler yapabilirsin?" | Yok | Yetenek listesi | MISSING | NYP | P1 | 742 | B25 | apps/web/, app/voice/tools/ | — | — | no | 734 ile aynı iş |
| 702 | Search | Yok | Arama | MISSING | NYP | P1 | 685 | B25 | apps/web/ | — | — | no | — |
| 703 | Global command palette | Yok | Palet | MISSING | NYP | P1 | 702 | B25 | apps/web/ | — | — | no | — |
| 704 | Turkish error dictionary | Yok | Tek sözlük | MISSING | NYP | P1 | — | B22 | app/errors/, apps/web/ | — | — | no | 705'in ön koşulu |
| 705 | No Python errors shown | Ham istisna gösteriliyor | Hiç gösterilmez | BROKEN | NYP | P1 | 704 | B22 | app/errors/, apps/web/ | — | 19 araştırmanın 10'u başarısız | no | Güven kırıcı |
| 706 | Empty-state UX | Kısmi | Tam | PARTIAL | NYP | P1 | 704 | B22 | apps/web/ | — | — | no | — |
| 707 | Loading state | Kısmi | Tam | PARTIAL | NYP | P1 | — | B22 | apps/web/ | — | — | no | — |
| 708 | Retry state | Yok | Var | MISSING | NYP | P1 | 704 | B22 | apps/web/ | — | — | no | — |
| 709 | Provider-blocked state | Yok | Var | MISSING | NYP | P1 | 704 | B22 | apps/web/ | — | — | no | 235 ile ortak |
| 710 | Permission-required state | Yok | Var | MISSING | NYP | P1 | 704 | B22 | apps/web/ | — | — | no | — |
| 711 | Owner-action-required state | Yok | Var | MISSING | NYP | P1 | 704,383 | B22 | apps/web/ | — | — | no | — |
| 712 | Feature status badges | Yok | Rozetler | MISSING | NYP | P1 | 700 | B24 | apps/web/ | — | — | no | — |
| 713 | PROVEN_REAL badges | Yok | Rozetler | MISSING | NYP | P1 | 712 | B24 | apps/web/ | — | — | no | Bu matrisi ürüne bağlar |
| 714 | Cockpit panel cleanup | 13/27 boş, 1 kalıcı 422 | Boş aile panel doğurmaz | BROKEN | NYP | P1 | 685 | B24 | apps/web/ | — | — | no | — |
| 715 | Broken /creative/runs route fix | `/v1/creative/runs` artık `/{run_id}`'den önce | Çalışır | DONE | PA | P0 | — | B03 | app/creative/routes.py | test_web_asks_for_routes_that_exist.py (12) | — | no | Web'in istediği 10 yolun hepsi API'de doğrulanıyor |
| 716 | PWA navigation | Kurulu PWA tek bağlantılı sayfada açılıyor | Gezinilebilir | PARTIAL | NYP | P1 | 685 | B23 | apps/web/ | — | — | no | — |
| 717 | Dark fullscreen Living Core | Çalışıyor | Aynı | DONE | PR | P1 | — | — | apps/web/ | web testleri | üretim | no | Rewrite gerekmez |
| 718 | Gold/amber visual identity | Çalışıyor | Aynı | DONE | PA | P1 | — | — | apps/web/ | web testleri | — | no | — |
| 719 | State-driven animation | Çalışıyor | Aynı | DONE | PA | P1 | — | — | apps/web/ | web testleri | — | no | — |
| 720 | Minimal mode | Kısmi | Tam | PARTIAL | NYP | P1 | — | B23 | apps/web/ | — | — | no | — |
| 721 | Cockpit mode | Çalışıyor | Aynı | DONE | PA | P1 | — | — | apps/web/ | web testleri | — | no | — |
| 722 | Performance levels | Kısmi | Tam | PARTIAL | NYP | P1 | — | B23 | apps/web/ | — | — | no | — |
| 723 | WebGL fallback | Kısmi | Tam | PARTIAL | NYP | P1 | — | B23 | apps/web/ | — | — | no | — |
| 724 | Accessibility | Yok | Erişilebilir | MISSING | NYP | P1 | 685 | B25 | apps/web/ | — | — | no | — |
| 725 | Keyboard navigation | Yok | Klavye | MISSING | NYP | P1 | 685 | B25 | apps/web/ | — | — | no | — |

## X. NATURAL LANGUAGE / INTENT ROUTING (726–750)

> Ölçüm: niyet eşleme elle yazılmış 6.558 satırlık deterministik bir tablo. 103 makul Türkçe
> cümlenin **59'u** hiçbir yeteneğe yönlenmiyor; **7'si yanlış** yeteneğe yönleniyor.

| ID | FEATURE | CURRENT_STATUS | TARGET_STATUS | IMPL | PROOF | PRI | DEPS | BATCH | SOURCE_REFERENCES | TEST_REFERENCES | RUNTIME_PROOF | OWNER_ACTION | NOTES |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 726 | "Saat kaç?" | Yönlenmiyor | Yönlenir | MISSING | NYP | P1 | — | B27 | app/voice/intent/ | 103 cümlelik ölçüm | — | no | `clock.now` aracı görünmez |
| 727 | "Bunu hatırla" | Yönlenmiyor | Yönlenir | MISSING | NYP | P1 | 31 | B27 | app/voice/intent/ | 103 cümlelik ölçüm | — | no | 35 ile aynı iş |
| 728 | "Bunu unut" | Yönlenmiyor | Yönlenir | MISSING | NYP | P1 | 36 | B27 | app/voice/intent/ | 103 cümlelik ölçüm | — | no | 36 ile aynı iş |
| 729 | "Maillerime bak" | Yönlenmiyor | Yönlenir | MISSING | NYP | P1 | 338 | B27 | app/voice/intent/ | 103 cümlelik ölçüm | — | no | 364 ile aynı iş |
| 730 | "Bu hafta ne var" | Yönlenmiyor | Yönlenir | MISSING | NYP | P1 | 351 | B27 | app/voice/intent/ | 103 cümlelik ölçüm | — | no | 365 ile aynı iş |
| 731 | "Toplantıyı iptal et" | Yönlenmiyor | Yönlenir | MISSING | NYP | P1 | 354 | B27 | app/voice/intent/ | 103 cümlelik ölçüm | — | no | 366 ile aynı iş |
| 732 | "Araştırmayı iptal et" | Yönlenmiyor | Yönlenir | MISSING | NYP | P1 | 202 | B27 | app/voice/intent/ | 103 cümlelik ölçüm | — | no | 202 ile aynı iş |
| 733 | "Sesini kıs" | Yönlenmiyor | Yönlenir | MISSING | NYP | P1 | — | B27 | app/voice/intent/ | 103 cümlelik ölçüm | — | no | Medya ses seviyesi aracı görünmez |
| 734 | "Neler yapabilirsin" | Yönlenmiyor | Yönlenir | MISSING | NYP | P1 | 701 | B27 | app/voice/intent/ | 103 cümlelik ölçüm | — | no | 701 ile aynı iş |
| 735 | "Ekran görüntüsü al" | Yönlenmiyor | Yönlenir | MISSING | NYP | P1 | 104 | B27 | app/voice/intent/ | 103 cümlelik ölçüm | — | no | 104 ile aynı iş |
| 736 | "Dosyayı gönder" | **Mail göndermeye** yönleniyor | Yanlış yönlenmez | BROKEN | NYP | P0 | — | B26 | app/voice/intent/ | 7 misroute ölçümü | — | no | Tehlikeli misroute |
| 737 | "Bunu yazdır" | **Operatör yazmaya** yönleniyor | Yanlış yönlenmez | BROKEN | NYP | P0 | — | B26 | app/voice/intent/ | 7 misroute ölçümü | — | no | Tehlikeli misroute |
| 738 | "Otomatik güncellemeleri kapat" | **Ekran otomasyonunu kapatıyor** | Yanlış yönlenmez | BROKEN | NYP | P0 | — | B26 | app/voice/intent/ | 7 misroute ölçümü | — | no | En tehlikeli misroute |
| 739 | Wrong-route negatives | Yok | Negatif külliyat | MISSING | NYP | P0 | 736 | B26 | app/voice/intent/ | — | — | no | 7 misroute buradan yakalanacaktı |
| 740 | Semantic model router | Yok | Model yönlendirici | MISSING | NYP | P2 | 741 | B51 | app/voice/intent/ | — | — | model bütçesi | Kritik fiiller hariç |
| 741 | Deterministic safety router | 6.558 satırlık tablo | Güvenlik kalkanı rolü | PARTIAL | PA | P0 | — | B26 | app/voice/intent/ | intent testleri | — | no | Kalkan olarak KALIR |
| 742 | Tool registry-driven routing | Yok | Araç kaydından | MISSING | NYP | P2 | 740 | B51 | app/voice/tools/ | — | 117 aracın 28'i hiç kullanılmamış | no | 701 ile ortak |
| 743 | Intent confidence | Yok | Güven skoru | MISSING | NYP | P2 | 740 | B51 | app/voice/intent/ | — | — | no | — |
| 744 | Ambiguity clarification | Yok | Soru sorar | MISSING | NYP | P2 | 743 | B51 | app/voice/intent/ | — | — | no | — |
| 745 | Reference resolution | Yok | "bunu/şunu" çözülür | MISSING | NYP | P2 | 49 | B51 | app/voice/intent/, app/memory/ | — | — | no | 49 ile aynı iş |
| 746 | Turkish paraphrases | Kısmi | Geniş | PARTIAL | PA | P2 | — | B51 | app/voice/intent/ | intent testleri | 59/103 yönlenmiyor | no | — |
| 747 | ASR-noise variants | Yok | Dayanıklı | MISSING | NYP | P2 | 746 | B51 | app/voice/intent/ | — | — | no | — |
| 748 | Routing corpus expansion | Kısmi | Sürekli büyür | PARTIAL | PA | P2 | 739 | B51 | app/voice/intent/ | intent testleri | 103 cümlelik set | no | — |
| 749 | Route telemetry | Yok | Telemetri | MISSING | NYP | P0 | — | B26 | app/voice/intent/, app/ledger/ | — | — | no | 750'nin ön koşulu |
| 750 | Misroute auto-detection | Yok | Otomatik tespit | MISSING | NYP | P0 | 749 | B26 | app/voice/intent/ | — | — | no | Bu sınıfın tekrarını önler |

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
