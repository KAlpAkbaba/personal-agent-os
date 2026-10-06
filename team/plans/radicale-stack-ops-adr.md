# ADR-<lead verir>: Sahibin takvimi kendi Cloud Core'unda - Radicale (radicale-stack-ops)

Durum: önerildi (Entegratör taslağı, 2026-10-06; çalışan tamamladı - "Çalışan eki"; lead numaralar).
İlgili: öneri team/proposals/2026-10-06-feed-radicale-calendar-server.md (sahip onayı),
plan team/plans/radicale-stack-ops-integration.md, eş kart radicale-caldav-live.

## Bağlam
Takvim katmanı (app/calendar, M21/B46) ve `CalDavCalendarProvider` yazılı, ama bağlanacak hesap
yok. Google/Microsoft OAuth hesabı sahibin bir oturum/MFA adımını ve bir sağlayıcı kaydını bekliyor.
Sahip, takvimin kendi sunucusunda durmasını onayladı (yeni konteyner + dış bağımlılık).

## Karar
1. **Neden Radicale:** ücretsiz, tek süreçli, dosya tabanlı CalDAV/CardDAV sunucusu; OAuth hesabı
   beklemeden bugün çalışır; veri sahibin diskinde düz `.ics` dosyaları (okunur, yedeklenir,
   taşınır). GPL-3.0; ayrı konteynerde değiştirilmeden çalışır, api'ye yalnız HTTP ile bağlanır.
   İmaj depodaki Dockerfile'dan (taban özetle sabit, 14 paket `==`); topluluk imajı
   (tomsquest) root entrypoint'i yüzünden `cap_drop: ALL` ile çalışmaz (plan §1).
2. **Neden port yok:** api, Radicale'e compose ağından ulaşır (`http://radicale:5232`). Hiçbir
   host arayüzünde (tailnet dahil) yayın yok: tek kullanıcı api'dir. Kimliksiz XML işleme
   Radicale'in tarihindeki DoS yüzeyi; port yokken yalnız api konteyneri ulaşabilir.
3. **iPhone Takvim / harici CalDAV istemcisi ERTELENDİ, ayrı karar:** sahibin telefonu doğrudan
   CalDAV ile bağlanmak isterse yol edge (nginx `location /caldav/`) ya da `tailscale serve`
   arkasında HTTPS olur; bu, kimlik (aynı htpasswd mi, uygulama parolası mı), `/.well-known/caldav`
   yönlendirmesi ve tailnet dışına sızmama testleri ister. Bu kartta yapılmaz; ihtiyaç doğarsa
   ayrı kart. Bugün takvime erişim api üzerinden (ses, Kokpit).
4. **Neden bind mount:** veri `/mnt/pagentos-data/radicale` (Hetzner veri birimi, prevent_destroy);
   yedek betiği klasörü doğrudan okur, Radicale'in kendi `flock` kilidini paylaşımlı alarak
   (plan B2). Adlandırılmış birim yedekte `docker cp` ya da birim yolu tahmini isterdi.
5. **Parola nerede:** düz metin yalnız `/opt/pagentos/.env` içinde `PAGENTOS_CALDAV_PASSWORD`
   (root 0600; set-cloud-secret.ps1 ile, stdin). Radicale tarafı yalnız bcrypt satırını görür:
   `/mnt/pagentos-data/radicale-auth/users` (uid 10002, 0600, konteynere ro). install-radicale.sh
   satırı imajın kendi python'uyla, ağsız, parolayı STDIN'den vererek üretir; parola hiçbir
   çıktıya, argv'ye, `docker inspect`'e girmez. Yedek users dosyasını şifreli depoya alır.
   Dev yığını: sabit `dev-takvim` (gerçek sır değil), users dosyası tmpfs'e entrypoint yazar.
6. **Kapsam sınırı:** Radicale sahibin takviminden başka veri tutmaz; staging compose'a girmez
   (test_staging_isolation REAL_ACCOUNT_MARKERS `radicale`'i zaten yakalıyor).

## Sonuçlar
- +1 konteyner, ölçülen ~24 MiB (sınır 256m), 7,7 GiB host'ta önemsiz.
- Yedek: radicale/ ağacı + users; kurulmadan önceki geceler "radicale: not installed" ile OK.
- Geri yükleme --apply Radicale'i durdurur, klasörü değiştirir (.pre-restore yanında), başlatır.
- Yükseltme: pinler birlikte, CHANGELOG'da depolama biçimi okunur, drill.

## Çalışan eki (radicale-stack-ops, 2026-10-06) - yapılan, taslaktan farkları
- Başlangıç betiği ayrı dosya: `infra/docker/radicale/entrypoint.sh` (Dockerfile COPY eder).
  users DOSYA ise dokunmaz; yoksa ve `PAGENTOS_RADICALE_DEV_PASSWORD` doluysa satırı bcrypt ile
  (stdin) yazar; dizinse (eksik bind kaynağı) ya da parola yoksa çıkış 1, Radicale başlamaz.
  Ölçüldü: users dosyasız ve eksik-bind (dizin) koşularında çıkış 1.
- Config imajda `/usr/local/etc/radicale/config` (prod'da tam iki bind kalır). Ek anahtarlar:
  `[web] type = none`, `max_content_length = 10000000`, `[auth] delay = 1`, `[logging] warning`.
- Yedek kilidi fd ile: `exec 7<.Radicale.lock; flock -s -w 60 7; cp -a ...` (tek cp satırı);
  60 sn'de alınamazsa kilitsiz kopya + uyarı (B2 önerisi). Kopya başarısızsa yeni çıkış 96
  (backup-cloud-core.sh başlığında). restore --apply: durdurma/taşıma/kopya/chown hatası 102,
  Radicale geri başlamazsa 101 ("services did not come back").
- Dev yığınında ölçüldü (PROVEN_PROXY, ev PC, Docker 28.3.2): `up -d --build --wait radicale`
  18,6 sn'de Healthy; PROPFIND owner:dev-takvim 207, kimliksiz 401, yanlış parola 401,
  MKCALENDAR 201, PUT 201, /someone/ 403; konteyner uid 10002, ro kök, CapDrop [ALL],
  no-new-privileges, 256 MiB, pids 256, port yalnız 127.0.0.1:15232. Prod yolu (ro bind
  users + gerçek bcrypt stdin'den, imajın python'u, `--network none`): 207 / kimliksiz 401.
- Host'taki gece yedeği /opt/pagentos-backup altındaki SABİTLENMİŞ kopyayı (SHA256SUMS) koşar:
  yeni radicale kapsamı ancak `scripts/cloud/install-backup.sh` root olarak yeniden koşunca
  host'a geçer (READY_FOR_OWNER, install-radicale.sh ile aynı oturumda; bağlama kartının ön
  koşuluna (c) olarak eklenmeli).
- Bağlama kartı için: parçada `build.context: .`; prod'da aynı servis `context: ./radicale`
  olur - eşitlik testi (test_radicale_stack.py) context'i dosyanın konumuna göre çözüp karşılaştırır.

## BAĞLAMA KARTININ TAM METNİ (lead, urgent-alert-wire ve aktivra-inbound-events birleşince keser)

- id: radicale-prod-wire
- title: Takvim sahibin kendi sunucusunda, 2b: Radicale'i prod compose'a bağla (parça birebir, api ortamı, yayın adımı)
- roadmap_row: Secretary: mail, calendar, answers calls on his behalf (The order madde 3)
- ön koşul (READY_FOR_OWNER, bu kart bundan ÖNCE yayınlanmaz): (a) sahip
  `scripts/cloud/set-cloud-secret.ps1` ile `PAGENTOS_CALDAV_PASSWORD`'ü .env'e koydu (≤ 72 bayt;
  set-cloud-secret'in ad listesi bu kartla genişler); (b) host'ta root olarak
  `scripts/cloud/install-radicale.sh` 0 çıktı. NEDEN: aşağıdaki `:?` biçimi .env'de değer yokken
  compose'un TÜM komutlarını reddettirir; değer yokken yayın, api'nin kendisini de başlatamaz
  ("an alarm that blocks its own remedy" desenine düşmemek için ön kontrol madde 4'te).
- goal:
  1. infra/docker/docker-compose.prod.yml'e `services.radicale` = infra/docker/radicale/
     compose.fragment.yml'deki `services.radicale` BİREBİR (YAML olarak eşit; build context yolu
     dosyanın konumuna göre aynı: ikisi de infra/docker/ altında değilse `context` göreli yolu
     düzeltilir ve eşitlik testi build.context'i çözümlenmiş mutlak yol olarak karşılaştırır).
     Profil YOK (her `up -d` başlatır), `ports` YOK.
  2. `&cloud-core-env` çapasına (api, api-blue, api-green üçü de miras alır):
     ```
     # radicale-prod-wire (ADR-<n>): the owner's own CalDAV server, compose network only.
     PAGENTOS_CALDAV_URL: http://radicale:5232/owner/takvim/
     PAGENTOS_CALDAV_USER: owner
     PAGENTOS_CALDAV_PASSWORD: ${PAGENTOS_CALDAV_PASSWORD:?set with scripts/cloud/set-cloud-secret.ps1}
     PAGENTOS_CALENDAR_WRITE_ENABLED: "true"
     ```
     ve `api` (dolayısıyla renkler) `depends_on`'una:
     ```
     radicale:
       condition: service_healthy
     ```
  3. scripts/cloud/release-cloud-core-bluegreen.sh: renkler `up -d --no-deps --wait api-$idle`
     ile kalkıyor; `--no-deps` depends_on'u başlatmaz. Boştaki renkten ÖNCE (≈ satır 982'nin
     önü) `compose up -d --no-deps --wait radicale`; başarısızsa yeni, belgelenmiş bir çıkış
     kodu (başlık satırı + test_release_exit_codes çakışma kuralı) ve HİÇBİR renk değişmeden
     çıkış. reconcile/rollback yolları (satır ~617/644/738/767) aynı ön adımı alır.
  4. Ön kontrol: aynı betik, compose'a dokunmadan önce `.env`'de `PAGENTOS_CALDAV_PASSWORD=`
     satırı boş değil mi bakar; yoksa ayrı çıkış kodu ve "set-cloud-secret.ps1 ile koy" (değer
     yazılmaz). Bu sayede eksik sır yayını yarıda değil BAŞTA durdurur.
  5. scripts/cloud/set-cloud-secret.ps1 izinli adlar listesine `PAGENTOS_CALDAV_PASSWORD`
     (betik compose'un adı bağlamadığı bir host'u reddeder; bu kartla bağlanır).
  6. services/api/tests/unit/test_radicale_stack.py: "henüz bağlanmadı" vakası artık bağlı
     vakaya düşer ve birebir eşitlik ister; ek vakalar: üç renkte de dört PAGENTOS_CALDAV_* /
     CALENDAR_WRITE anahtarı, api depends_on radicale service_healthy, prod'da radicale için
     `ports` yok, betikte radicale adımı idle renkten önce.
  7. docs/CLOUD_INFRASTRUCTURE.md §5: Radicale "bağlı".
- acceptance: 1) test_radicale_stack.py, test_release_exit_codes.py, test_compose_images.py,
  test_staging_isolation.py, cloud-release-bluegreen.tests.ps1 yeşil. 2) Mutasyon RED:
  parçada mem_limit değişince eşitlik vakası kırmızı; betikte radicale adımı silinince sıra
  vakası kırmızı; .env'de parola yokken ön kontrol çıkış kodu (sahte host). 3) `docker compose
  -f docker-compose.prod.yml config` sahte .env ile 0 çıkar, `ports` altında radicale yok.
  4) Yayın (bluegreen) sonrası host'ta: `docker inspect pagentos-prod-radicale` healthy;
  `docker port pagentos-prod-radicale` BOŞ; api-<aktif> içinden PROPFIND /owner/takvim/ 207;
  `ss -ltnp` host'ta 5232 yok. 5) Ses: "yarın saat 10'da toplantı ekle" -> Radicale'de .ics
  (radicale-caldav-live'ın deneme listesi; PROVEN_REAL sahibin raporuyla). 6) Gece yedeği
  LAST_BACKUP.json `radicale_items` ≥ 1; haftalık drill raporu `radicale_items`.
- evidence_expected: PROVEN_AUTOMATED (compose/betik testleri, mutasyonlar); PROVEN_REAL
  (yayın sonrası host ölçümleri + sahibin sesli denemesi).
- area: infra/docker/docker-compose.prod.yml, scripts/cloud/release-cloud-core-bluegreen.sh,
  scripts/cloud/set-cloud-secret.ps1, services/api/tests/unit/test_radicale_stack.py,
  services/api/tests/unit/test_release_exit_codes.py, scripts/tests/cloud-release-bluegreen.tests.ps1,
  scripts/tests/cloud-secret.tests.ps1, docs/CLOUD_INFRASTRUCTURE.md

## Değerlendirilen ve seçilmeyenler
- Google/Microsoft takvimi (OAuth): sahibin hesap adımı bekleniyor; kendi sunucu onu dışlamaz,
  ileride ikinci sağlayıcı olarak eklenir.
- Nextcloud: tek takvim için ağır (PHP + DB + web), bakım yükü sahibe düşer.
- tomsquest/docker-radicale: plan §1.
- Baïkal / DAViCal: PHP/PostgreSQL bağımlılığı; Radicale'in dosya deposu yedeği basitleştirir.
