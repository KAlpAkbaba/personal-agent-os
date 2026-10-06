# inbound-calls-public-path - entegrasyon planı (entegratör, d20261006, 2026-10-06)

Kart: Gelen aramalar 1/2 - Cloud Core'a tek dışa açık yol (Tailscale Funnel, yalnız 8443, yalnız
`/telephony/inbound` ve `/v1/telephony/audio`). Öneri: team/proposals/2026-10-06-feed-inbound-calls-public-endpoint.md.

## Karar: ADOPT (zaten kurulu araç) - yeni bağımlılık YOK

Tailscale Funnel; Cloud Core'da kurulu `tailscale` CLI'nin `funnel` alt komutu. Yeni Python/Node/apt paketi yok,
lisans değişmiyor (Tailscale istemcisi BSD-3-Clause; zaten altyapının parçası). THIRD_PARTY satırı: aşağıda
(Tailscale bugün THIRD_PARTY_COMPONENTS.md'de AYRI bir satır olarak yok - yalnız 111. satırda adı geçiyor;
lead isterse ekler, bu kart eklemez - dosya alan dışında).

## Ölçülen gerçekler (Cloud Core, salt-okunur SSH, 2026-10-06 ~06:05 UTC)

| Ne | Değer | Nasıl |
|---|---|---|
| tailscale sürümü | **1.102.4** (commit 3caf7d9e7dca, go1.26.6) | `tailscale version` |
| düğüm adı | `pagentos-core.tail0e6789.ts.net` | `tailscale status --json` -> `Self.DNSName` (sonda nokta var, kırpılır) |
| PAGENTOS_BIND_IP | `100.90.158.26` | `grep -E '^PAGENTOS_BIND_IP=' /opt/pagentos/.env` (yalnız o satır) |
| kenar yayınları | `100.90.158.26:8001` VE `127.0.0.1:8001` (docker-proxy) | `ss -ltnp`; compose prod 259-260 |
| arka uç erişimi | `http://100.90.158.26:8001/v1/system/health` -> 200; 127.0.0.1 -> 200; `POST /telephony/inbound/voice` -> 404 (rota henüz yok - bridge kartı) | host'ta curl |
| bugünkü serve durumu | yalnız 443, `/` -> `http://127.0.0.1:3000`, **AllowFunnel yok** | `tailscale serve status --json` (aşağıda aynen) |
| düğüm yetenekleri | `https` VAR, **`funnel` YOK**, `funnel-ports` YOK | `Self.CapMap` anahtarları |
| python3 / jq | `/usr/bin/python3` VAR; jq YOK | `which jq python3` |

**Sonuç: bugün host adımı çıkış 3 verir** - sahip tailnet ilke dosyasına `funnel` düğüm özniteliğini eklemeden Funnel açılmaz.

## Kurulu sürüm (1.102.4) için doğrulanmış söz dizimi (kaynak: tailscale v1.102.4 `cmd/tailscale/cli/serve_v2.go`, `funnel.go`, `ipn/serve.go`, `ipn/ipnlocal/serve.go`)

Aç (iki ayrı komut, aynı port, iki mount - `SetWebHandler` mount başına ekler, değiştirmez):
```
tailscale funnel --bg --yes --https=8443 --set-path=/telephony/inbound   http://100.90.158.26:8001/telephony/inbound
tailscale funnel --bg --yes --https=8443 --set-path=/v1/telephony/audio  http://100.90.158.26:8001/v1/telephony/audio
```
- `applyFunnel` -> `SetFunnel(dnsName, 8443, true)`: AllowFunnel anahtarı **`<düğüm>:8443`** (port başına; mount başına DEĞİL).
  443'ün AllowFunnel'ına dokunmaz. Bir `tailscale serve` (funnel değil) komutu 8443'e mount eklerse 8443'ün Funnel'ı
  KAPANIR (`applyFunnel(..., false)`); 443'e serve koşmak 8443'ü etkilemez - web betiği güvenle yeniden koşar.
- Arka uç hedefi YOLU TAŞIMALI: Funnel istek yolundan mount'u keser (`http.StripPrefix(mount)`), sonra hedefin yoluna
  ekler (`ReverseProxy.SetURL`). `/telephony/inbound/voice` -> mount `/telephony/inbound` -> `/voice` ->
  `http://100.90.158.26:8001/telephony/inbound/voice`. Yani sözleşmedeki "yol yeniden yazılmaz" ancak hedef
  `http://<ip>:8001/<aynı kök>` olursa doğrudur; hedef `http://<ip>:8001` olsaydı api `/voice` görürdü.
- Mount eşleşmesi: tam yol, sonra `path.Dir` ile üst klasörler. `/telephony/inboundX` eşleşmez; `/telephony` eşleşmez;
  `/` hiçbir mount'a düşmez -> tailscaled 404 verir (api'ye gitmez). `GET *`/CONNECT DoS'u 1.102.4'te düzeltilmiş.
- `--set-path` değeri `cleanURLPath`'ten geçer: sondaki `/` YOK ve mount anahtarı JSON'da aynen `"/telephony/inbound"`.
- Hedef host kuralı (`ExpandProxyTargetValue`): localhost dışı her geçerli IP/ad kabul, **şema zorunlu** (`http://`).
  `100.90.158.26` kabul -> bağlama kartına "127.0.0.1 ikinci yayın" satırı GEREKMEZ (zaten yayında da).

Kapat (mount başına; `serve` alt komutu - `funnel` alt komutu her koşuda funnel yetkisini yeniden sorar, kapatırken gerekmez):
```
tailscale serve --https=8443 --set-path=/telephony/inbound off
tailscale serve --https=8443 --set-path=/v1/telephony/audio off
```
`RemoveWebHandler(..., cleanupFunnel=true)`: port boşalınca `Web[<düğüm>:8443]`, `TCP[8443]` ve `AllowFunnel[<düğüm>:8443]`
birlikte silinir. 443 ayrı anahtar - dokunulmaz.

STOP geri alma (8443'te bilinmeyen mount var): `tailscale serve --yes --https=8443 off` (set-path yok = 8443'ün TÜM mount'ları;
birden çok mount'ta onay sorar, `--yes` ile sormaz; TTY yoksa `prompt.YesNo` varsayılan EVET döner ama `--yes` yine verilsin).
`tailscale funnel --https=8443 off` da çalışır ama önce funnel yetki akışına girer - **kullanılmasın**. Kartın
"hangisi sürümde varsa" sorusunun cevabı: yalnız `serve --yes --https=8443 off`.

ASLA (betik sarmalayıcısı reddetsin, metin testi (h) ile birlikte):
- `tailscale serve reset` / `tailscale funnel reset` - İKİSİ de tüm serve ayarını siler (443 web kabuğu dahil). Kart
  yalnız `serve reset` diyor; `funnel reset` de aynı etkide, sarmalayıcıda `reset` kelimesi reddedilsin.
- eski konumsal biçim `tailscale funnel 8443 on|off` / `funnel 443 on` - v1.102.4'te hâlâ var (`funnel.go`), mount'suz
  AllowFunnel açar. Betik `funnel`'ı yalnız `--https=8443 --set-path=<iki kökten biri>` ile çağırsın; sarmalayıcı
  `funnel` + (`--https=8443` değil) bileşimini reddetsin.
- `--https=443` ile her şey (yalnız JSON karşılaştırmasında geçer).

## Funnel kapalıyken CLI davranışı - TUZAK (çıkış 3 tespiti çıktıyla YAPILMAMALI)

`verifyFunnelEnabled` -> `enableFeatureInteractive(ctx, "funnel", https, funnel)`: yetenek yoksa kontrol sunucusuna sorar;
`ShouldWait=false` ise metni + URL'yi (`https://login.tailscale.com/f/funnel?node=...`) STDOUT'a yazar ve **`os.Exit(0)`**
- hiçbir şey değişmeden ÇIKIŞ 0; `ShouldWait=true` ise sahip tıklayana kadar BEKLER (zaman sınırı 124 ile keser).
Bu yüzden betik funnel komutundan ÖNCE `tailscale status --json` -> `Self.CapMap` okur:
- `"https"` yok -> çıkış 3 (HTTPS sertifikaları sahip satırı, web betiğindekiyle aynı);
- `"funnel"` yok -> çıkış 3, tek satır: "OWNER STEP: Tailscale admin konsolu -> Access controls (policy file) ->
  nodeAttrs'a {\"target\": [\"autogroup:member\"], \"attr\": [\"funnel\"]} ekle (ya da yalnız bu düğüm/etiketi hedefle); sonra yeniden koş.";
- `https://tailscale.com/cap/funnel-ports?ports=...` anahtarı VARSA ve 8443'ü içermiyorsa -> çıkış 3 (port izinli değil).
  (Anahtar yoksa varsayılan 443/8443/10000; CLI 1.102.4 kendisi yalnız 443'ü kontrol ediyor - üst kaynak tuhaflığı.)
Çıktı deseni (`Funnel not available|f/funnel\?node=|funnel" node attribute not set|not allowed for funnel`) yalnız İKİNCİ
savunma; asıl karar sonradan `serve status --json` doğrulaması (çıkış 0 ama durum değişmediyse -> çıkış 3 değil 1/4 değil:
"funnel komutu 0 döndü ama 8443'te mount yok" = 3, çünkü tek bilinen nedeni yetenek akışı).

## `serve status --json` alan adları (ipn.ServeConfig, v1.102.4) ve fixture'lar

Alanlar: `TCP` (anahtar port DİZGESİ "443", değer `{"HTTPS": true}`), `Web` (anahtar `"<düğüm>:<port>"`, değer
`{"Handlers": {"<mount>": {"Proxy": "<url>"}}}`), `AllowFunnel` (anahtar `"<düğüm>:<port>"`, değer `true`; hiç funnel yoksa
ALAN YOK - `omitempty`), ayrıca olabilir: `Foreground` (ön plan oturumları - betik `--bg` kullanır; Foreground'da 8443 varsa
STOP 4), `Services`. `tailscale funnel status --json` aynı yapıyı verir. Boş ayar: `{}`.

Fixture A - GERÇEK, host'tan aynen alındı (2026-10-06, düğüm adı test için `tail1234` yapılır):
```json
{"TCP":{"443":{"HTTPS":true}},"Web":{"pagentos-core.tail0e6789.ts.net:443":{"Handlers":{"/":{"Proxy":"http://127.0.0.1:3000"}}}}}
```
Fixture B - hedef durum; ipn.ServeConfig yapısından ve `SetFunnel`/`SetWebHandler` kodundan KURULDU (Funnel bu düğümde açık
olmadığından yakalanamadı; worker raporunda kaynağı "entegratör, v1.102.4 kaynağından" yazsın):
```json
{"TCP":{"443":{"HTTPS":true},"8443":{"HTTPS":true}},
 "Web":{"pagentos-core.tail1234.ts.net:443":{"Handlers":{"/":{"Proxy":"http://127.0.0.1:3000"}}},
        "pagentos-core.tail1234.ts.net:8443":{"Handlers":{
          "/telephony/inbound":{"Proxy":"http://100.64.0.9:8001/telephony/inbound"},
          "/v1/telephony/audio":{"Proxy":"http://100.64.0.9:8001/v1/telephony/audio"}}}},
 "AllowFunnel":{"pagentos-core.tail1234.ts.net:8443":true}}
```
Fixture C (443'te Funnel): B + `"AllowFunnel":{"...:443":true,...}`. Fixture D (fazladan mount): B'nin 8443 Handlers'ına
`"/":{"Proxy":"http://100.64.0.9:8001"}`. `tailscale status --json` sahtesi: `{"Self":{"DNSName":"pagentos-core.tail1234.ts.net.",
"CapMap":{"https":null,"funnel":null}}}` (CapMap değerleri gerçekte null/dizi - yalnız ANAHTAR okunur).
Düz metin `serve status` (Funnel'sız, gerçek): `https://pagentos-core.tail0e6789.ts.net (tailnet only)` / `|-- / proxy http://127.0.0.1:3000`.
Funnel'da metin "(Funnel on)" der - web betiğinin bugünkü `grep -i 'funnel on'` kontrolü bu yüzden 8443'te de tetiklenir.

## Doğrulama kuralı (her iki betik aynı JSON okumasını yapar)

python3 ile (host'ta var): `AllowFunnel` içinde true olan anahtarlar kümesi == {`<düğüm>:8443`} (ya da kapalıysa boş);
`Web["<düğüm>:8443"].Handlers` anahtar kümesi == {`/telephony/inbound`, `/v1/telephony/audio`}; her Proxy ==
`http://<BIND_IP>:8001` + mount; `Foreground` içinde 8443/AllowFunnel yok. 443 için AllowFunnel true -> 4.
python3 YOKSA (Git Bash'te yok - ölçüldü: `command -v python3` boş, yalnız `python`): grep/sed ile yalnız şu
**fail-closed** karar: çıktıda `"AllowFunnel"` hiç geçmiyorsa "Funnel yok"; geçiyorsa ve tam doğrulama yapılamıyorsa
web betiği STOP 4, funnel betiği çıkış 2 ("python3 gerekli"). Kamuya açıklık kararını kırılgan bir regex vermesin.
Test önerisi: betik yorumlayıcıyı `${PAGENTOS_JSON_PYTHON:-python3}`'ten alsın; pytest `sys.executable`'ı verir (Windows
yolu, Git Bash çalıştırır); bir vaka da `PAGENTOS_JSON_PYTHON=/yok` ile fail-closed dalı sınar.

## Git Bash test tuzakları (bu makine)
- Sahte `tailscale` BASH betiği olmalı (web-tailnet-https.tests.ps1 düzeni). Yerel bir .exe/.cmd/python olursa MSYS
  `--set-path=/telephony/inbound` argümanını `C:/Program Files/Git/telephony/inbound`'a çevirir (tailscale'in kendi
  `cleanMinGWPathConversionIfNeeded`'i bu yüzden var). Gerekirse `MSYS_NO_PATHCONV=1`.
- Sahte argv'yi bir log dosyasına satır başına yazsın; "hiç komut yok" = log'da `funnel`/`serve --https`/`off` yok
  (`status` okumaları sayılmaz).
- `.env`'i `source` ETME, `set -x` YOK; yalnız `grep -E '^PAGENTOS_BIND_IP=' | head -1 | cut -d= -f2- | tr -d '\r"'"'"' '`;
  değer `^[0-9]{1,3}(\.[0-9]{1,3}){3}$` değilse çıkış 2 (değeri yazmadan). Test (g) sır satırını .env'in BAŞINA ve SONUNA koysun.
- Bash'ten pytest çocuğuna stdin miras verme (`stdin=subprocess.DEVNULL`), her koşuya `timeout=30`.

## WebSocket (Twilio Media Streams wss) ve uçtan uca başlıklar
- Funnel girişi: Tailscale'in genel giriş düğümleri TLS'yi açmadan SNI ile düğüme iletir; TLS düğümde (tailscaled) biter,
  sonra `httputil.ReverseProxy` (Go stdlib) - Upgrade/101 yanıtını ve çift yönlü akışı yerel olarak taşır. WS geçişi kaynakta
  ayrıca kapatılmıyor. Kenar nginx `location /`'ta zaten `Upgrade`/`Connection $connection_upgrade`, `proxy_http_version 1.1`,
  `proxy_read_timeout 3600s` taşıyor - **nginx değişmez**.
- **Bridge kartı için kritik (imza):** tailscaled `Host`'u korur (`<düğüm>:8443`), `X-Forwarded-Proto: https` ekler; AMA kenar nginx
  `Host $host` (PORTSUZ) ve `X-Forwarded-Proto $scheme` (= **http**) ile ezer. api isteği yeniden kurarsa
  `http://pagentos-core...ts.net/telephony/inbound/voice` görür; Twilio `https://...:8443/telephony/inbound/voice` imzalar ->
  her istek 403. İmza URL'si **yalnız** `PAGENTOS_TELEPHONY_PUBLIC_BASE_URL` + yol + sorgu ile kurulmalı. (twilio-python
  RequestValidator portlu/portsuz iki biçimi dener ama yalnız VARSAYILAN portlar için; 8443 hep URL'de kalır.)
- **Kaynak IP:** Funnel isteğini tailscaled düğümün kendisinden `100.90.158.26:8001`'e açar -> nginx `X-Real-IP` = düğüm IP'si ya da
  docker köprü adresi (host'ta ölçülmeli), asıl arayan `X-Forwarded-For`'da. api'de `devices/affinity.py` X-Real-IP'ye güveniyor;
  telefon rotaları IP'ye dayalı HİÇBİR yetki kullanmamalı (yetki = imza / belirteç). tailscaled ayrıca `Tailscale-Funnel-Request: ?1`
  ekler ve gelen sahtesini siler - bridge kartı bunu "genelden geldi" işareti olarak kullanabilir (yalnız iki mount bunu görür).

## Sınırlar ve bir aramanın ihtiyacı
- Funnel yalnız 443, 8443, 10000 portlarını dinler (tailscale.com/kb/1223/funnel). 443 web kabuğunun -> 8443 seçimi doğru.
- Bant genişliği: belgede "non-configurable bandwidth limits", sayı YAYIMLANMIYOR (2026-09 itibarıyla). Bir arama: G.711 µ-law
  8 kHz = 64 kbit/s yön başına; Media Streams JSON+base64 ile ~1,4x = ~90 kbit/s yön başına, iki yön ~180 kbit/s. Tek sahipli
  kullanımda (aynı anda 1-2 arama) sınıra yaklaşması beklenmez; ölçülmedi - PROVEN_REAL ilk aramada.
- Gecikme: genel giriş -> DERP/doğrudan -> düğüm; Hetzner NBG1'de doğrudan bağlantı var (`direct 2.28.67.130:41641`). Ek gecikme
  ölçülmedi; Twilio webhook zaman aşımı 15 sn (varsayılan) - sorun değil; ses akışında ilk aramada ölçülmeli.
- DNS: genel kayıt ilk açılışta 10 dakikaya kadar sürebilir (KB). Sertifika: düğüm zaten 443 için ts.net sertifikası alıyor; 8443 aynı adı kullanır.
- **RİSK - Twilio ve 8443:** Twilio belgeleri webhook URL'sinde standart dışı port için açık bir izin/ret yazmıyor (aranıp
  bulunamadı). Doğrulama: Twilio numarası bağlanınca ilk test isteği. Twilio reddederse tek Funnel seçeneği 443 olur -> web
  kabuğu tailnet'te başka bir porta (ör. `serve --https=8443`) taşınır ve telefon 443'te; ADR'de yedek olarak yazıldı.

## Alternatif: alan adı + Let's Encrypt + kenarda genel port
| | Funnel 8443 | Alan adı + LE + genel port |
|---|---|---|
| Para | 0 (tüm planlarda) | alan adı ~10-15 EUR/yıl (yeni ücretli kalem, sahip hesabı) |
| Yeni bileşen | yok | certbot/acme.sh + yenileme zamanlayıcısı + Hetzner güvenlik duvarı kuralı + nginx genel server bloğu |
| Açılan yüzey | yalnız iki yol kökü, tailscaled'de (geri kalan 404, api'ye gitmez) | host'un genel IP'sinde bir port; tüm tarama trafiği nginx'e gelir; yol kısıtı nginx yapılandırmasına bağlı |
| Anayasa | "genel uygulama portu yok" korunur (host'ta genel port açılmaz) | genel port açılır - ayrı sahip kararı |
| Bakım | sertifika/DNS Tailscale'de | yenileme hatası = gelen aramalar sessizce ölür |
| Bağımlılık riski | Tailscale (zaten tek yol) çökerse gelen arama da düşer | bağımsız |
Cloudflare Tunnel (ücretsiz ama alan adı + yeni üçüncü taraf ajan) ve ngrok (sabit ad ücretli) de elendi.

## Dosyalar, testler, geri alma
Dokunulacak (kart alanı): `scripts/cloud/enable-telephony-funnel.sh` (yeni), `scripts/cloud/enable-web-tailnet-https.sh`
(Funnel kontrolü JSON'a), `scripts/tests/web-tailnet-https.tests.ps1`, `services/api/tests/unit/test_telephony_funnel_script.py` (yeni),
`services/api/tests/unit/test_compose_web_shell.py`. Testler kartın (a)-(h) listesi + fail-closed python'suz vaka + "funnel komutu 0
döndü ama durum değişmedi -> 3" vakası. Ek öneri (kartın (h)'sine): metinde `funnel reset` da geçmesin.
Geri alma: host'ta `enable-telephony-funnel.sh --off` (443'e dokunmaz); kod tarafında iki betik commit'i geri alınır.
Ayak izi: betikler tek seferlik; tailscaled'in ek bellek/CPU'su ölçülemez düzeyde (aynı süreç, iki proxy handler) - ölçülmedi, tahmin.
Ağa çıkan: Funnel açılınca Tailscale kontrol sunucusu düğümün genel DNS kaydını yayımlar (`pagentos-core.tail0e6789.ts.net`
genel DNS'te ve Sertifika Şeffaflığı günlüklerinde zaten görünür - ts.net sertifikası 443 için alınmış).

## THIRD_PARTY_COMPONENTS satırı (lead ekler; yeni bileşen değil, mevcut aracın kaydı)
`| Tailscale client (tailscale, tailscaled) | 1.102.4 on the Cloud Core (apt pkgs.tailscale.com) | BSD-3-Clause | tailnet transport, tailscale serve (web shell, 443, tailnet only), tailscale funnel (8443, only /telephony/inbound and /v1/telephony/audio - public, owner review pending) | no new dependency |`

Kaynaklar: github.com/tailscale/tailscale@v1.102.4 (`cmd/tailscale/cli/serve_v2.go`, `funnel.go`, `serve_legacy.go`, `ipn/serve.go`,
`ipn/ipnlocal/serve.go`, `util/prompt/prompt.go`); https://tailscale.com/kb/1223/funnel ; https://tailscale.com/docs/features/tailscale-funnel ;
https://www.twilio.com/docs/usage/webhooks/webhooks-overview ; Twilio "Fixing Request Validation 403 Errors Behind a Proxy" (support.twilio.com/hc/en-us/articles/50535649249307).
