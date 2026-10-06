# radicale-stack-ops - entegrasyon planı (Entegratör, d20261006, 2026-10-06)

Kart: radicale-stack-ops (Takvim sahibin kendi sunucusunda, 1/2). Öneri:
team/proposals/2026-10-06-feed-radicale-calendar-server.md (sahip onayladı). ADR taslağı:
team/plans/radicale-stack-ops-adr.md (bağlama kartının tam metni orada).

## 1. Karar: ADOPT Radicale 3.8.1 (PyPI'dan, değiştirilmeden) + ADAPT imaj (depodaki Dockerfile)

Radicale'in kendisi benimsenir (kendi CalDAV sunucumuzu yazmak yok). Topluluk imajı
(tomsquest/docker-radicale) BENİMSENMEZ; fikri alınır, imaj depodaki küçük Dockerfile'dan
kurulur. Nedenleri (ölçülmüş/okunmuş, 2026-10-06):

| | depodaki Dockerfile (seçilen) | tomsquest/docker-radicale |
|---|---|---|
| lisans | Radicale GPL-3.0 (aynı) | GPL-3.0 |
| bakım | Kozea/Radicale: son push 2026-10-06, 11 açık issue, 3.8.1 = 2026-09-25 | 3.8.1.1 = 2026-09-26, 0 açık issue, 1,1k yıldız; `latest` BUGÜN yeniden itildi (etiketler yeniden yazılıyor: özet şart) |
| cap_drop ALL | çalışır: ölçüldü (aşağıda) | entrypoint ROOT başlar, `chown -R /data` + `su-exec` ile düşer (docker-entrypoint.sh, okundu) -> CHOWN/SETUID/SETGID ister; kartın `cap_drop: ALL`'ı ile çelişir |
| dev parola entrypoint'i | bizim (kart madde 2) | yok; yine sarmalamak gerekirdi |
| bcrypt | `bcrypt==5.0.0` + Radicale'in `libpass 1.9.3`'ü, ölçüldü | var |
| sabitleme | taban `python:3.12-alpine@sha256:1b668429b3511ab407d8e00648891631b0b1a4d7e15e3ca70f38ab5b91ad4ab4` + 14 paket `==` | `3.8.1.1@sha256:e6d8c17bc4d75f3fd40e52b20421ba244c17ca08fb01cd7dc90d5d78ba51b58d` |

Sonuç: test (6) "build depodaki Dockerfile'ı gösterir" kolunu kullanır. Taban imaj da özetle
sabit (minio satırı gibi). İmaj adı fragment'te ve (bağlama kartında) prod'da AYNI:
`pagentos/radicale:local` (cloud-browser parçası ile prod'un adları farklıydı - burada eşitlik
testi birebir istediği için tek ad).

## 2. Ölçüm (ev PC, Docker 28.3.2, 2026-10-06 08:37-08:41; geçici imaj, sonra silindi)

Deneme imajı: yukarıdaki taban + `pip install radicale==3.8.1 bcrypt==5.0.0`, uid 10002, config
kartın sözleşmesi + `[web] type = none`. Koşum: `--read-only --cap-drop ALL
--security-opt no-new-privileges:true --memory 256m --pids-limit 256 --tmpfs /tmp
--tmpfs /etc/radicale:uid=10002,mode=0700 -v <birim>:/data`.
- kimliksiz PROPFIND /owner/ -> 401; yanlış parola -> 401; owner:dev-takvim PROPFIND Depth 0 -> 207
- MKCALENDAR /owner/takvim/ -> 201; üç PUT .ics -> 201 x3; PROPFIND Depth 1 -> 207
- owner ile /someone/ -> 403 (owner_only çalışıyor)
- konteyner içinden GET http://127.0.0.1:5232/ (python urllib) -> 200 (`web = none` "Radicale works!")
- bellek: boşta 24,46 MiB; 60 paralel PROPFIND sonrası 23,8 MiB (`docker stats`); pids 1.
  256m sınırı ~10 kat pay. CPU ölçülmedi (iddia yok).
- `pip freeze` (tam kapanış): bcrypt 5.0.0, certifi 2026.7.22, charset-normalizer 3.5.2,
  defusedxml 0.7.1, idna 3.20, libpass 1.9.3, pika 1.4.4, python-dateutil 2.9.0.post0,
  pytz 2026.5, Radicale 3.8.1, requests 2.34.2, six 1.17.0, urllib3 2.8.0, vobject 0.9.9.
  Hepsi musl wheel/pure: derleyici gerekmez.

## 3. ÇALIŞAN İÇİN BULGULAR (kartın metninde yok, kabulü etkiler)

B1. **`.ics` sayımı önbelleği sayar.** Radicale her öğe için `.Radicale.cache/item/<href>` ve
`.Radicale.cache/history/<href>` yazar; href `e1.ics` ise önbellek dosyası da `e1.ics`.
Ölçümde 3 etkinlik = 9 `.ics` dosyası. `radicale_items` şöyle sayılmalı:
`find "$staging/radicale" -name .Radicale.cache -prune -o -type f -name '*.ics' -print | wc -l`.
test_backup_radicale.py sahte ağaçta `.Radicale.cache/item/x.ics` ve `history/x.ics` de
koymalı ve 3 beklemeli (yoksa önbelleği sayan betik yeşil geçer). Mutasyon: prune'u silmek kırmızı.

B2. **Tutarlı kopya için Radicale'in kendi kilidi.** Radicale `/data/.Radicale.lock` üzerinde
`fcntl.flock` kullanır (yazar LOCK_EX, okur LOCK_SH; radicale/pathutils.py okundu). Bind mount
aynı çekirdek/aynı inode: yedek `"$flock_bin" -s -w 60 "$data/radicale/.Radicale.lock" cp -a
"$data/radicale" "$staging/radicale"` ile yazarları kopya süresince bekletir (birkaç ms; okuyucular
etkilenmez). Kilit dosyası yoksa (henüz hiç başlamamış) düz `cp -a`. Kilit alınamazsa yeni bir
çıkış kodu UYDURMA: 94'ten ayrı bir anlam olacağı için ya mevcut sözlüğe yeni numara + başlık
satırı (test_release_exit_codes çakışma kuralı), ya da kilitsiz kopya + "radicale: copied without
lock" uyarısı. Önerim ikincisi (yedek takvim yüzünden kırmızı olmaz; dosya yazımı Radicale'de
atomik rename, tek dosya yarım kalmaz).

B3. **Sahiplik.** Kart "0700 klasör" diyor; root:root 0700 olursa uid 10002 /data'ya yazamaz.
install-radicale.sh: `/mnt/pagentos-data/radicale` -> `chown 10002:10002`, 0700;
`radicale-auth/` root 0700; `radicale-auth/users` -> `chown 10002:10002`, 0600 (dosya bind'ı
ro; dizin izinleri konteynerden görünmez). uid Dockerfile'da sabit (`ARG RADICALE_UID=10002`;
cloud-browser 10001) ve betik aynı sayıyı kullanır (test ikisini birden okusun: contract halves).
restore --apply değiştirdikten sonra `chown -R 10002:10002` (restic restore sayısal sahipliği
korur ama sahte/elle taşınmış ağaçlar için ucuz sigorta).

B4. **Parola argv'de/env'de görünmesin.** install-radicale.sh bcrypt'i
`printf '%s' "$pw" | docker run --rm -i --network none pagentos/radicale:local python -c
'import sys,bcrypt;print(bcrypt.hashpw(sys.stdin.buffer.read(),bcrypt.gensalt(12)).decode())'`
ile üretir: STDIN, asla `-e` ya da argüman (ps ve docker inspect görür). bcrypt 5.0 72 bayttan
uzun parolada ValueError atar: betik uzunluğu önce ölçer, >72 bayt ayrı çıkış kodu ve parola
YAZILMADAN mesaj. İmaj yoksa betik `docker build -t pagentos/radicale:local
infra/docker/radicale` yapar (bağlama kartı yayınlanmadan da çalışsın).

B5. **Prod'da users dosyası yoksa açık başlamasın.** Kısa bind sözdizimi kaynak yoksa DİZİN
yaratır. Entrypoint: `/etc/radicale/users` dosya ise dokunmaz; dizinse ya da yoksa ve
`PAGENTOS_RADICALE_DEV_PASSWORD` boşsa -> stderr "users dosyası yok: install-radicale.sh" ve
çıkış 1 (konteyner sağlıksız kalır, sessizce açık başlamaz). Dev'de: `tmpfs:
/etc/radicale:uid=10002,mode=0700` (ölçümde çalışan düzen) ve entrypoint satırı oraya yazar;
böylece dev de `read_only: true` kalır. Config imaja `COPY config /usr/local/etc/radicale/config`
(root sahipli, 0644) ve `radicale --config /usr/local/etc/radicale/config` - /etc/radicale yalnız
users için; bu yüzden prod'daki "tam iki bind" kuralı korunur.

B6. **`[web] type = none` ekleyin** (sözleşmeye aykırı değil, ek anahtar): sahip web arayüzünü
kullanmaz, saldırı yüzeyi küçülür, GET / yine 200. Ayrıca `[server] max_content_length = 10000000`
(varsayılan 100 MB; kimliksiz XML DoS geçmişi var, aşağıda) ve `[auth] delay = 1` (varsayılan).

B7. **Healthcheck curl'süz:** imajda curl yok;
`["CMD", "python", "-c", "import urllib.request as u,urllib.error as e,sys\ntry: u.urlopen('http://127.0.0.1:5232/',timeout=4)\nexcept e.HTTPError as x: sys.exit(0 if x.code==401 else 1)\nexcept Exception: sys.exit(1)"]`
(200 = urlopen döner = 0; 401 = 0; bağlantı reddi = 1).

B8. **Yedek kaydı:** LAST_BACKUP.json'a `"radicale_items":N` eklemek backup_health.py'yi bozmaz
(`record.get(...)` ile okuyor, bilinmeyen alan yok sayılıyor; services/api/app/backup_health.py:127-150).
Klasör yoksa `"radicale_items":null` değil, alanı yine yazın ve `0` + ayrı `"radicale":"not installed"`
(sayısal alan hep sayı kalsın).

B9. **Drill:** restore --drill zaten her dosyayı MANIFEST'e karşı doğruluyor; radicale/ ağacı
otomatik kapsanır. Ek iş yalnız sayım (B1 ile aynı find) ve rapora alan.

## 4. Dokunulacak dosyalar (hepsi kartın alanında)
(Çalışan notu 2026-10-06: başlangıç betiği alana eklenen `infra/docker/radicale/entrypoint.sh`
oldu; B1-B7 uygulandı, ölçümler ve farklar ADR'nin "Çalışan eki"nde.)
infra/docker/radicale/{Dockerfile,config,compose.fragment.yml,entrypoint (Dockerfile içinde
heredoc ya da COPY - ayrı dosya alan listesinde YOK: Dockerfile içinde `RUN printf ... >
/usr/local/bin/pagentos-radicale-entrypoint` ile ya da ALAN_ISTEGI)}, docker-compose.dev.yml,
scripts/cloud/{install-radicale.sh,backup-cloud-core.sh,restore-cloud-core.sh},
services/api/tests/unit/{test_radicale_stack.py,test_backup_radicale.py,test_release_exit_codes.py},
docs/CLOUD_INFRASTRUCTURE.md (§5 servis listesi, §7 yedek kapsamı). backup_manifest.py büyük
olasılıkla değişmez (manifest zaten her dosyayı tarar).

Dockerfile iskeleti (çalışan sabitler):
```
FROM python:3.12-alpine@sha256:1b668429b3511ab407d8e00648891631b0b1a4d7e15e3ca70f38ab5b91ad4ab4
ARG RADICALE_UID=10002
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
RUN pip install --no-deps radicale==3.8.1 bcrypt==5.0.0 libpass==1.9.3 defusedxml==0.7.1 \
      vobject==0.9.9 python-dateutil==2.9.0.post0 six==1.17.0 pytz==2026.5 pika==1.4.4 \
      requests==2.34.2 urllib3==2.8.0 idna==3.20 charset-normalizer==3.5.2 certifi==2026.7.22 \
 && pip check && adduser -D -H -u ${RADICALE_UID} radicale && mkdir -p /etc/radicale /data \
 && chown radicale /data
COPY config /usr/local/etc/radicale/config
USER radicale
```
(`--no-deps` + tam liste = yeniden üretilebilir kurulum; `pip check` eksik varsa build'i düşürür.)

## 5. Testler (karttaki 6/7 + bu planın ekleri)
- test_radicale_stack.py: kartın listesi + Dockerfile'daki `RADICALE_UID` == install-radicale.sh'taki
  uid; FROM satırı `@sha256:` taşır; dev'de `read_only` + `/etc/radicale` tmpfs.
- test_backup_radicale.py: B1 önbellek dosyalarıyla 3; B2 kilit dosyası varken yedek 0 çıkar;
  sahte docker `run --rm -i` stdin'den bcrypt üretmeyi taklit eder (gerçek bcrypt değil: `$2b$12$`
  önekli sahte satır) - gerçek bcrypt kanıtı dev yığınındaki kabul 6'dır.
- install: parola yokken 2, >72 bayt kendi kodu; iki durumda da parola çıktıda yok.

## 6. Geri alma
Parça bağlanmadan önce prod'a etkisi yok (yalnız dev servisi + betiklerde koşullu dallar).
Geri alma = commit'i geri al; dev'de `docker compose ... rm -sf radicale` + `docker volume rm
pagentos-radicale-data`. Yedek değişikliği klasör yokken davranışı değiştirmez ("not installed").

## 7. Güvenlik notları
- Açık CVE yok (osv.dev/NVD taraması 2026-10-06: kayıtlı olanlar 1.1/2.0 öncesi - Windows dizin
  aşımı, htpasswd zamanlama kâhini CVE-2017-8342, owner_only regex atlatma; hepsi 3.x'te kapalı).
  Snyk'te kimliksiz XML işleme DoS kaydı var -> port yok + tailnet + `max_content_length` (B6).
- Dışarı bağlantı: Radicale güncelleme denetimi YAPMAZ; `requests`/`pika` yalnız isteğe bağlı
  hook/auth eklentileri içindir, varsayılan `[hook] type = none`. Konteyner api ağında; internete
  çıkışı compose ağı izin verir ama kod çağırmaz (bağlama kartında `internal` ağ ayrı karar).
- Cihaz güvenliği: sürücü yok, ekran/kimlik erişimi yok; yalnız sahibin takvim dosyaları.
- GPL-3.0: Radicale ayrı süreçte, değiştirilmeden, HTTP ile konuşur (birleştirme değil toplama);
  imaj dağıtılmaz (host'ta kurulur). Kaynak kodumuza GPL yükümlülüğü geçmez.

## 8. THIRD_PARTY_COMPONENTS.md satırının tam metni (lead paylaşılan dosyaya işler)

```
## Radicale - the owner's own CalDAV server (2026-10-06, radicale-stack-ops, ADR-<lead>)

Role: the owner's calendar on the owner's own Cloud Core, without a Google/Microsoft OAuth account.
Runs as its own container (`infra/docker/radicale/Dockerfile`), never inside the api image;
the api reaches it over the compose network (`http://radicale:5232/owner/takvim/`) through
the CalDAV provider (`app/calendar/providers.py`). No published port.

- `Radicale` 3.8.1 (GPL-3.0-or-later; Kozea, https://github.com/Kozea/Radicale), installed
  unmodified from PyPI and run as a separate process: our code talks to it over HTTP only.
  The image is built on the host, not distributed.
- Its closure, pinned `==` with `--no-deps` in the Dockerfile: bcrypt 5.0.0 (Apache-2.0),
  libpass 1.9.3 (BSD), defusedxml 0.7.1 (PSF), vobject 0.9.9 (Apache-2.0), python-dateutil
  2.9.0.post0 (Apache-2.0/BSD), six 1.17.0 (MIT), pytz 2026.5 (MIT), pika 1.4.4 (BSD-3),
  requests 2.34.2 (Apache-2.0), urllib3 2.8.0 (MIT), idna 3.20 (BSD-3), charset-normalizer
  3.5.2 (MIT), certifi 2026.7.22 (MPL-2.0). pika/requests are only used by optional hooks,
  which stay off.
- Base: `python:3.12-alpine` pinned by digest `sha256:1b668429...4ab4`.
- Auth: htpasswd + bcrypt, rights owner_only, one user `owner`. The password lives only in
  /opt/pagentos/.env (`PAGENTOS_CALDAV_PASSWORD`, via set-cloud-secret.ps1) and as a bcrypt
  line in /mnt/pagentos-data/radicale-auth/users (0600). Data: /mnt/pagentos-data/radicale,
  in the nightly restic backup.
- Measured 2026-10-06: ~24 MiB resident idle and after 60 parallel PROPFINDs (cap 256m).
- Considered and not used: tomsquest/docker-radicale (GPL-3.0, well maintained) - its
  entrypoint starts as root and needs CHOWN/SETUID/SETGID, which `cap_drop: ALL` forbids.

Upgrade rule: bump the pins together, read Radicale's CHANGELOG for storage-format and
config changes, rebuild, run test_radicale_stack + the dev-stack PROPFIND proof, then a
restore drill.
```
