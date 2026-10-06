# ADR-XXXX (numara lead'in): Gelen aramalar için Cloud Core'a tek dışa açık yol - Tailscale Funnel, yalnız 8443, yalnız iki yol kökü

- Durum: Önerildi - **sahip incelemesi bekliyor** (TEAM_PROTOCOL 3a.3: en kısıtlayıcı güvenli seçenek uygulanır, sahip sonra onaylar ya da geri aldırır)
- Tarih: 2026-10-06 (cycle d20261006, kart inbound-calls-public-path; entegrasyon planı team/plans/inbound-calls-public-path-integration.md)
- Kaynak karar: sahip, idea inbound-calls-public-endpoint: "tek bir açık uç" (team/proposals/2026-10-06-feed-inbound-calls-public-endpoint.md)
- İlgili: DECISIONS "HTTPS: `tailscale serve`, never `funnel`" (web kabuğu) - AYNEN KALIR; jarvis-calls-owner denetimi (d20261005 inspector-1 bulgu 4)

## Bağlam
Twilio gelen arama webhook'u (HTTPS POST) ve Media Streams (wss) internetten erişilen bir adres ister. Giden aramanın tek kullanımlık
ses bağlantısını (`/v1/telephony/audio`) Twilio da tailnet'ten çekemiyor (d20261005 inspector-1 bulgu 4). Cloud Core'un alan adı yok;
kenar nginx yalnız `${PAGENTOS_BIND_IP}:8001` ve `127.0.0.1:8001`'de; host'ta genel uygulama portu yok (anayasa). Kurulu Tailscale 1.102.4.

## Karar
1. Tailscale Funnel, **yalnız 8443 portunda**, **yalnız iki yol kökü**: `/telephony/inbound` (Twilio: `POST /telephony/inbound/voice`,
   `POST /telephony/inbound/status`, WebSocket `/telephony/inbound/media`) ve `/v1/telephony/audio` (giden aramanın tek kullanımlık ses
   bağlantısı). Genel adres `https://<düğüm>.<tailnet>.ts.net:8443` (bugün `https://pagentos-core.tail0e6789.ts.net:8443`).
2. Arka uç kenar nginx `http://${PAGENTOS_BIND_IP}:8001/<aynı kök>` - hedef yolu taşır, çünkü Funnel mount önekini keser ve hedef yola
   ekler; böylece api aynı yolu görür.
3. **443 hiçbir zaman Funnel'a açılmaz.** Funnel PORT başınadır (AllowFunnel anahtarı `<düğüm>:<port>`): 443 açılsaydı 443'teki `/`
   mount'u - web kabuğu - de internete açılırdı. Web kabuğu 443'te tailnet'te kalır; "never funnel" kararı web için değişmez.
4. Host adımı `scripts/cloud/enable-telephony-funnel.sh` (aç / `--status` / `--off`); funnel komutundan önce düğüm yeteneklerini
   (`https`, `funnel`, `funnel-ports`) okur, sonra `serve status --json` ile doğrular: AllowFunnel yalnız `<düğüm>:8443`, 8443 mount'ları
   TAM bu iki kök, 443'te Funnel yok - tutmazsa 8443'ü kapatır ve çıkış 4. `serve reset` / `funnel reset` / 443'e funnel asla.
5. `enable-web-tailnet-https.sh` Funnel'ı hâlâ hiç ÇALIŞTIRMAZ; yalnız durumu port başına okur: 8443'te tam bu iki mount ile Funnel ->
   "Funnel 8443: telefon yolu (enable-telephony-funnel.sh), web 443 tailnet'te" ve devam; başka her Funnel -> STOP 4 (eskisi gibi).

### Uygulama notu (worker, d20261006)
- İki betik aynı satırı taşır: `telephony_mounts="/telephony/inbound /v1/telephony/audio"`; `test_compose_web_shell.py`
  ikisini birbirine karşı okur (biri değişirse kırmızı).
- JSON okuması `${PAGENTOS_JSON_PYTHON:-python3}` ile; python yoksa **kapalı-güvenli**: funnel betiği çıkış 2, web betiği
  JSON'da `"AllowFunnel"` geçiyorsa STOP 4 (Funnel yoksa web kabuğu yine sunulur). Genel açıklık kararı hiçbir regex'e kalmaz.
- Funnel betiğinin sarmalayıcısı reddeder: `reset` argümanı, 8443 dışı her `--https=`/`--tcp=`/`--tls-terminated-tcp=`,
  konumsal port biçimi (`funnel 8443 on`), iki kökten biri olmayan `funnel`, 8443 dışına `serve` değişikliği.
  Kapatma yalnız `serve --https=8443 --set-path=<kök> off` (`--off`) ve uyuşmazlıkta `serve --yes --https=8443 off`.
- Doğrulama hükmü (`none|partial|ours|funnel443|bad`): 443'te AllowFunnel -> funnel443 (açarken hiçbir komut yok, çıkış 4);
  8443 dışı Funnel portu, 8443'te fazladan kök, yanlış hedef, Foreground'da Funnel/8443, 8443 TCPForward, başka düğüm adı -> bad
  (8443 kapatılır, çıkış 4).

## Neden nginx'te değişiklik yok
Kısıt Funnel mount'unda: `/` ya da başka bir yol tailscaled'de 404 olur, kenara hiç ulaşmaz. Kenar zaten `location /` altında api'ye
geçiriyor; `Upgrade`/`Connection`, HTTP/1.1 ve 3600 sn okuma süresi WebSocket'i taşıyor. nginx'te yol kısıtı ikinci bir kopya olur ve
tailnet istemcilerini de etkilerdi. Not (bridge kartına): nginx `Host`'u portsuz, `X-Forwarded-Proto`'yu `http` yazar - Twilio imza URL'si
istekten DEĞİL `PAGENTOS_TELEPHONY_PUBLIC_BASE_URL`'den kurulur; telefon rotalarında kaynak IP'ye dayalı yetki yoktur (Funnel istekleri
düğümün kendi adresinden gelir); tailscaled'in eklediği `Tailscale-Funnel-Request: ?1` "genelden geldi" işaretidir.

## Açılan yüzey (tanımları inbound-calls-bridge kartında)
- `POST /telephony/inbound/voice`, `POST /telephony/inbound/status`: `X-Twilio-Signature` = base64(HMAC-SHA1(auth token, tam genel URL +
  alfabetik sıralı POST alanları ad+değer)); imzasız/yanlış imzalı -> 403, gövdesiz.
- WebSocket `/telephony/inbound/media`: imzasız; ilk `start` çerçevesinin `customParameters.bridge_token`'ı voice webhook'unun verdiği
  tek kullanımlık, 2 dakikalık belirteç değilse soket 1008 ile kapanır.
- `/v1/telephony/audio/...`: tek kullanımlık belirteçli ses bağlantısı.
- Başka hiçbir yol yok: `https://<düğüm>:8443/`, `:8443/v1/system/health` vb. tailscaled'de 404.

## Reddedilen seçenekler
- Alan adı + Let's Encrypt + kenarda genel port: yeni ücretli kalem (alan adı), sertifika yenileme bakımı, host'ta genel port (anayasa),
  tüm tarama trafiği nginx'e. - Cloudflare Tunnel: alan adı + yeni üçüncü taraf ajan. - ngrok: sabit ad ücretli.
- Funnel 443: web kabuğunu da açar (port başına). - Funnel 10000: mümkün, ama 8443 daha yaygın HTTPS alternatif portu.

## Sonuçlar ve riskler
- Tailscale tek dış bağımlılık: Tailscale/Funnel düşerse gelen arama da düşer (giden arama etkilenmez).
- Funnel bant genişliği "non-configurable", sayı yayımlanmıyor; bir arama ~90 kbit/s/yön (G.711 + base64) - sorun beklenmez, ilk aramada ölçülür.
- **Twilio'nun 8443'e bağlandığı belgede açıkça yazmıyor** - ilk test isteğiyle doğrulanır. Reddederse yedek: web kabuğu tailnet'te başka
  porta (`serve --https=10000`), telefon 443'te Funnel - bu ayrı bir ADR ve sahip kararı olur.
- Düğümün ts.net adı genel DNS'e yayımlanır (sertifika günlüklerinde zaten görünür). Yalnız iki yol yanıt verir.
- Geri alma: `enable-telephony-funnel.sh --off` (443'e dokunmaz) ya da admin konsolunda `funnel` özniteliğini kaldırmak.

## docs/CLOUD_INFRASTRUCTURE.md paragrafı (TAM METİN - lead işler; dosya radicale-stack-ops kartında)

> **Public path for telephony (Tailscale Funnel on 8443 only; owner review pending, ADR-XXXX).** The Cloud Core has no public
> application port and no domain. The only public path is a Tailscale Funnel on port **8443** of the node's own name
> (`https://pagentos-core.<tailnet>.ts.net:8443`), carrying exactly two path roots to the edge nginx on the host's tailnet address:
> `/telephony/inbound` (Twilio voice and status webhooks, signature-checked, 403 without a body otherwise; the Media Streams WebSocket,
> closed with 1008 without the one-time `bridge_token`) and `/v1/telephony/audio` (the outbound call's one-time audio link). Any other
> path on 8443 is answered 404 by tailscaled and never reaches the edge. **Port 443 is never Funneled**: Funnel is per port, and 443
> carries the web shell, which stays tailnet-only (`tailscale serve`, see "HTTPS: tailscale serve, never funnel"). The host step is
> `scripts/cloud/enable-telephony-funnel.sh` (`--status`, `--off`); it reads the node's capabilities before acting, verifies the
> resulting `tailscale serve status --json` (Funnel only on `<node>:8443`, exactly the two mounts, none on 443) and closes 8443 on any
> mismatch (exit 4). It never runs `tailscale serve reset` or `tailscale funnel reset`. `enable-web-tailnet-https.sh` recognises this
> Funnel and continues; any other Funnel stops it (exit 4). Prerequisites: the tailnet policy file grants the node the `funnel` node
> attribute, and `PAGENTOS_TELEPHONY_PUBLIC_BASE_URL` is `https://<node>.<tailnet>.ts.net:8443`; Twilio signatures are computed over that
> URL, never over the Host header (the edge rewrites Host without the port and X-Forwarded-Proto to http).

## Onay Merkezi satırları (TAM METİN)

1. **[Sahip kararı - inceleme] Gelen aramalar için tek genel yol: Funnel yalnız 8443'te.** "Web kabuğu için 'never funnel' kararı aynen kalır
   (443 tailnet'te). Telefon için 8443'te yalnız `/telephony/inbound` ve `/v1/telephony/audio` internete açıldı (ADR-XXXX). Onaylıyor musun,
   yoksa geri alalım mı (`enable-telephony-funnel.sh --off`)?" ONAY / GERİ AL.
2. **[Sahip adımı - Tailscale admin konsolu] Funnel düğüm özniteliği.** "https://login.tailscale.com/admin/acls/file -> tailnet ilke dosyasına
   ekle: `"nodeAttrs": [{"target": ["autogroup:member"], "attr": ["funnel"]}]` (yalnız Cloud Core için: `"target": ["<pagentos-core'un e-postası ya da etiketi>"]`),
   Kaydet. MagicDNS ve HTTPS Certificates zaten açık (443 web kabuğu çalışıyor)." Bugün düğümde `funnel` özniteliği YOK (ölçüldü 2026-10-06).
3. **[Host adımı - root] Funnel'ı aç.** (2. satırdan sonra; lead/sahip, Tailscale SSH ile):
   `ssh root@pagentos-core 'bash /opt/pagentos/app/scripts/cloud/enable-telephony-funnel.sh && bash /opt/pagentos/app/scripts/cloud/enable-telephony-funnel.sh --status'`
   Beklenen: `FUNNEL https://pagentos-core.tail0e6789.ts.net:8443 -> ... (telefon yolu)` ve `--status` çıkış 0. Çıkış 3 = 2. satır yapılmamış.
4. **[Sahip/lead - ayar] Genel temel URL.** PowerShell, ev PC'si:
   `.\scripts\secret-store.ps1 -Set PAGENTOS_TELEPHONY_PUBLIC_BASE_URL` (değer: `https://pagentos-core.tail0e6789.ts.net:8443`), sonra
   `.\scripts\cloud\set-cloud-secret.ps1 -Name PAGENTOS_TELEPHONY_PUBLIC_BASE_URL -ExpectProvider '' -VerifyCommand ''`
   (compose bu değişkeni bağlamadan 67 ile reddeder - bağlama inbound-calls-bridge kartında; bu satır o karttan sonra.)
5. **[Sahip doğrulaması - PROVEN_REAL] Telefonun mobil verisinden (Wi-Fi kapalı, Tailscale kapalı):**
   `https://pagentos-core.tail0e6789.ts.net:8443/telephony/inbound/voice` -> 403 ya da 405; `:8443/` ve `:8443/v1/system/health` -> açılmaz (404);
   `https://pagentos-core.tail0e6789.ts.net/` (443) -> açılmaz. Tailscale açıkken 443 web kabuğu açılır.
