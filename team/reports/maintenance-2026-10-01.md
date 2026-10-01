# Bakım penceresi raporu — 2026-10-01 (ADR-0223)

Toplandı: 2026-10-01T19:14:55Z · makine MAIL · kaynak root@100.90.158.26 (salt okuma)

## Sonuç: TAMAMLANDI VE DOĞRULANDI

## Kayıt (LAST_MAINTENANCE.json)

```
{"window_start":"2026-10-01T19:00:56Z","kernel_before":"6.8.0-138-generic","kernel_after":"6.8.0-142-generic","release":"858c3e0bf974f1984826f4b8f37e7dc8b9e12186","downtime_seconds":825,"downtime_measured":"marker to the first good probe of --verify: an upper bound","zombies":1,"verified_at":"2026-10-01T19:14:40Z"}
```

## Öncesi

```
when: before
at_utc: 2026-10-01T19:00:00Z
kernel: 6.8.0-138-generic
uptime: up 4 weeks, 1 day, 8 hours, 36 minutes; booted 2026-09-02 10:23:23
reboot_required: yes
upgradable: 26
zombies: 2
release: 858c3e0bf974f1984826f4b8f37e7dc8b9e12186
last_known_good: cc9ca31e274d507ad0fadaf34b54c552252124a7
recovery_pin: 858c3e0bf974f1984826f4b8f37e7dc8b9e12186
health: {"status":"ok","failing_checks":"","version":"0.1.0","release":{"version_model":2,"component":"cloud-core","version":"858c3e0bf974f1984826f4b8f37e7dc8b9e12186",
reconcile_timer: active
last_reconcile: RECONCILE OK: api-blue is canonical (release 858c3e0bf974f1984826f4b8f37e7dc8b9e12186); markers, upstreams and containers agree
docker: 29.7.2
tailscale: 1.102.3
containers:
pagentos-prod-api-blue | Up 3 hours (healthy)
pagentos-prod-api-green | Exited (143) 3 hours ago
pagentos-prod-cloud-browser | Up 25 hours (healthy)
pagentos-prod-godseye | Up 10 days (healthy)
pagentos-prod-api | Created
pagentos-prod-edge | Up 3 weeks
pagentos-prod-postgres | Up 4 weeks (healthy)
pagentos-prod-temporal | Up 4 weeks (healthy)
pagentos-prod-redis | Up 4 weeks (healthy)
pagentos-prod-minio | Up 4 weeks (healthy)
devices_with_open_session:
  bulut | open sessions 1 | newest 2026-10-01 16:26:05.36085+00
  GMKADIRAKBABA | open sessions 1 | newest 2026-10-01 16:26:04.897241+00
  MAIL | open sessions 1 | newest 2026-10-01 16:26:05.334243+00
```

## Sonrası

```
when: after
at_utc: 2026-10-01T19:14:40Z
kernel: 6.8.0-142-generic
uptime: up 13 minutes; booted 2026-10-01 19:01:16
reboot_required: no
upgradable: 1
zombies: 1
release: 858c3e0bf974f1984826f4b8f37e7dc8b9e12186
last_known_good: cc9ca31e274d507ad0fadaf34b54c552252124a7
recovery_pin: 858c3e0bf974f1984826f4b8f37e7dc8b9e12186
health: {"status":"ok","failing_checks":"","version":"0.1.0","release":{"version_model":2,"component":"cloud-core","version":"858c3e0bf974f1984826f4b8f37e7dc8b9e12186",
reconcile_timer: active
last_reconcile: RECONCILE OK: api-blue is canonical (release 858c3e0bf974f1984826f4b8f37e7dc8b9e12186); markers, upstreams and containers agree
docker: 29.8.2
tailscale: 1.102.4
containers:
pagentos-prod-api-blue | Up 13 minutes (healthy)
pagentos-prod-api-green | Exited (143) 3 hours ago
pagentos-prod-cloud-browser | Up 12 minutes (healthy)
pagentos-prod-godseye | Up 13 minutes (healthy)
pagentos-prod-edge | Up 13 minutes
pagentos-prod-postgres | Up 13 minutes (healthy)
pagentos-prod-temporal | Up 13 minutes (healthy)
pagentos-prod-redis | Up 13 minutes (healthy)
pagentos-prod-minio | Up 13 minutes (healthy)
devices_with_open_session:
  bulut | open sessions 1 | newest 2026-10-01 19:01:46.328984+00
  GMKADIRAKBABA | open sessions 1 | newest 2026-10-01 19:01:57.17705+00
  MAIL | open sessions 1 | newest 2026-10-01 19:01:50.415159+00
```

## Şu an (rapor toplanırken)

```
6.8.0-142-generic
2026-10-01 19:01:16
{"status":"ok","failing_checks":"","version":"0.1.0","release":{"version_model":2,"component":"cloud-core","version":"858c3e0bf974f1984826f4b8f37e7dc8b9e12186","version_source":"env","app_version":"0.
```

## Pencere günlüğü (son 60 satır)

```
facts written: /opt/pagentos/maintenance/before-2026-10-01.txt
ok   health: read http://127.0.0.1:8001/v1/system/health: status ok, failing_checks empty
ok   release: read /opt/pagentos/RELEASE and /opt/pagentos/LAST_KNOWN_GOOD: both 40 hex
ok   pin: read /opt/pagentos-recovery/APPROVED_SHA: equals RELEASE
ok   backup-age: read /var/lib/pagentos-backup/LAST_BACKUP.json: finished_at 2026-10-01T16:25:13Z, 154 min ago
ok   failure-marker: read /var/lib/pagentos-backup/failures/: none
ok   no-release: read /opt/pagentos/.bluegreen-operation.lock: not held (team cycle / owner mid-task: the lead's check, home PC)
ok   apt-simulate: read apt-get -s upgrade: no removal
ok   apt-hold: read apt-mark showhold: docker-ce is not held
ok   disk: read df -P /: 51% used (< 80%)
PREFLIGHT OK
step 6: pre-maintenance backup (/opt/pagentos/app/scripts/cloud/backup-cloud-core.sh)
backup: postgres: roles
backup: postgres: pagentos_prod 36550106 bytes
backup: postgres: postgres 1084 bytes
backup: postgres: temporal 80825 bytes
backup: postgres: temporal_visibility 35823 bytes
backup: objects: mirroring every bucket
backup: objects: pagentos-artifacts
backup: objects: 40 file(s) from 1 bucket(s)
unable to open cache: unable to locate cache directory: neither $XDG_CACHE_HOME nor $HOME are defined
backup: snapshot 205d06530ae793a5d060b9eb782cf7b4e7d1d82b8bb834d02fc7b1113da801c1 (scheduled)
unable to open cache: unable to locate cache directory: neither $XDG_CACHE_HOME nor $HOME are defined
BACKUP OK: snapshot 205d06530ae793a5d060b9eb782cf7b4e7d1d82b8bb834d02fc7b1113da801c1 (scheduled) in 10s; off-host: not configured
step 7: stopping pagentos-bluegreen-reconcile.timer for the window
step 8: removing the never-started leftover pagentos-prod-api
step 9: apt-get update && upgrade (Docker restarts inside this step)

Restarting services...
 /etc/needrestart/restart.d/systemd-manager
 systemctl restart atd.service cron.service packagekit.service systemd-journald.service systemd-networkd.service systemd-resolved.service systemd-timesyncd.service

Service restarts being deferred:
 /etc/needrestart/restart.d/dbus.service
 systemctl restart getty@tty1.service
 systemctl restart serial-getty@ttyS0.service
 systemctl restart systemd-logind.service
 systemctl restart unattended-upgrades.service

No containers need to be restarted.

No user sessions are running outdated binaries.

No VM guests are running outdated hypervisor (qemu) binaries on this host.
step 10: marker, then reboot
reboot issued; after boot run: /usr/local/sbin/pagentos-maintenance-reboot.sh --verify
ok   kernel: read uname -r: 6.8.0-142-generic (was 6.8.0-138-generic)
ok   reboot-required: read /var/run/reboot-required: gone
ok   containers: read docker ps: postgres redis minio temporal edge godseye and an api colour up
ok   timer: read systemctl is-active pagentos-bluegreen-reconcile.timer: active
ok   reconcile: read journalctl -u pagentos-bluegreen-reconcile.service: RECONCILE OK
FAIL zombies: read ps -eo stat: 1 defunct
ok   health: read http://127.0.0.1:8001/v1/system/health: ok, release 858c3e0bf974f1984826f4b8f37e7dc8b9e12186
VERIFY FAILED: ADR-0223 step 12 applies (the reconcile timer first, then the LKG colour by hand); the marker stays
facts written: /opt/pagentos/maintenance/after-2026-10-01.txt
```

## Lead'in kapanışı (2026-10-01 22:20 İstanbul)

Pencere sunucunun kendi zamanlayıcısından, başında kimse olmadan koştu. Gerçekler:

| | Öncesi (19:00:00 UTC) | Sonrası |
|---|---|---|
| Çekirdek | 6.8.0-138 | **6.8.0-142** |
| Çalışma süresi | 4 hafta 1 gün | 19:01:16 UTC'de yeniden başladı |
| `reboot-required` | var | **yok** |
| Bekleyen güncelleme | 26 | 1 (`linux-image-virtual` 6.8.0-146: pencereden sonra çıkan yeni çekirdek) |
| Docker / Tailscale | 29.7.2 / 1.102.3 | 29.8.2 / 1.102.4 |
| Sürüm, LKG, pin | 858c3e0b / cc9ca31e / 858c3e0b | aynı |
| Sağlık, reconcile | ok, RECONCILE OK (api-blue) | ok, RECONCILE OK (api-blue) |
| Artık konteyner `pagentos-prod-api` (Created) | var | silindi |
| Cihazlar | MAIL, GMKADIRAKBABA, bulut bağlı | üçü de geri bağlandı: 19:01:46 – 19:01:57 UTC |
| Zombi süreç | 2 | 1 |

**Kesinti:** yeniden başlatma komutu 19:00:56 UTC, konteynerler 19:01:30'da kalktı, son cihaz 19:01:57'de
geri bağlandı - yeniden başlatmadan cihazların dönüşüne **yaklaşık bir dakika**; ondan önce Docker
güncellenirken konteynerlerin bir kez yeniden başladığı kısa bir kesinti daha var (ölçülmedi). Kayıttaki
`downtime_seconds: 825` gerçek kesinti DEĞİL: doğrulamanın koştuğu ana kadar geçen süre, üst sınır.

**Yanlış "başarısız":** doğrulama ilk koşusunda her şey tamamken `FAIL zombies: 1 defunct` dedi. Zombi,
temporal konteynerinin `auto-setup.sh` çocuğu; o imajın 1 numaralı süreci onu hiç toplamıyor ve konteyner
her başladığında iki saniye sonra yeniden oluşuyor. ADR-0223'te "konteyner yeniden başlayınca gider"
yazmıştım - **yanlıştı**. Betik düzeltildi (zombi raporlanır, pencereyi başarısız saymaz; testi önce
kırmızı), sunucuya kondu, doğrulama `VERIFY OK` verdi. Kalıcı çare: temporal servisine `init: true`
(compose değişikliği olduğu için yayını sahibin onayını ister) - kuyrukta `temporal-init-reaper`.

Tek seferlik birimler (iki systemd timer + ev PC'deki rapor görevi) kaldırıldı.
