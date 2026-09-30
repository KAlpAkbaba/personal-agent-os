# Öneri: Bulutta yürütme — "bulut" sanal cihazı, execution_target kuralı, ağsız compute.run

Tarih: 2026-09-30 · Araştırmacı · Durum: awaiting_owner

## Ne
Roadmap satırı: order 2b (ADR-0213; sahibin 2026-09-29 sorusu: "işlemleri LLM'in koştuğu makine
üzerinde yapsak daha stabil olmaz mı?"). Cloud Core üzerinde headless Chromium çalıştıran bir
tarayıcı işçisi, cihaz kaydında sanal cihaz olarak (`device_kind=cloud`, takma ad "bulut");
her işe `execution_target` (cloud | owner_chrome | device) kuralı ve düşüş zinciri; ağsız bir
`compute.run` kum havuzu; zamanlanmış işler her zaman bulutta.
Sahibin cümlesi: "bulutta şu siteden fiyatları çek" / "gece bunu bulutta çalıştır, ben yokken".

Not: ADR-0213 `docs/DECISIONS.md` içinde henüz YAZILMAMIŞ (yalnız ROADMAP 2b ve bootstrap
istemi anıyor). Onaydan sonra ilk iş ADR'yi yazmak.

## Neden şimdi
- Sahip kendisi istedi; ofis günü (2026-09-29) iki PC'nin de ancak açıkken işe yaradığını gösterdi
  (`no_capable_device` olayları). Zamanlanmış/gece işi bugün hiçbir yerde koşamıyor: ADR-0207
  karar 3 "sahip yokken görev yok" dedi; bulut işçisi ise sahibin Chrome'una dokunmadan
  yalnızca READ/NAVIGATE tipi işleri (araştırma, fiyat, haber) koşturabilir.
- Parça zaten var: `services/browser` içinde `ManagedBackend` (Playwright, `headless=True`,
  ayrı profil, gerçek profil yolunu reddeder — `backends.py`). Yeni tarayıcı yazılmıyor;
  aynı işçi Linux imajında, `device_kind=cloud` olarak kendini kaydeder.
- Bulutta oturum açık site yok, ama T1/T4-tipi işler (haber, araştırma, form doldurma-gönderme
  yok) buna ihtiyaç duymuyor.
- Kanıt (dış): Playwright üretimde Docker'da standart; boşta 0,5–1 GB, çok bağlamda 2 GB+;
  `--shm-size=2g` ve `--init` şart; eşzamanlılık 3–4 ile sınırlanmalı
  ([Bug0 rehberi](https://bug0.com/knowledge-base/playwright-docker),
  [Medium: 8GB Was a Lie](https://medium.com/@onurmaciit/8gb-was-a-lie-playwright-in-production-c2bdbe4429d6),
  [Browserless](https://www.browserless.io/blog/run-playwright-in-docker)). Kanıt orta kalitede
  (blog); ölçüm bizim ana kanıtımız olmalı.
- **Kapasite bulgusu (önemli):** ADR'lerdeki "CPX41" artık satışta değil. Hetzner 15 Haziran
  2026 fiyat düzenlemesi tablosunda CPX22/32/42/52/62 var, CPX41 yok
  ([Hetzner belgesi](https://docs.hetzner.com/general/infrastructure-and-availability/price-adjustment/)).
  16 GB'lık karşılık **CPX42: €69,49/ay** (net) — CPX32'nin (€35,49) iki katı. ROADMAP 2b ve
  ADR-0206 "CPX41 planla" diyor; gerçek karar "+€34/ay" demek.
- Bugünkü bellek: CPX32 = 8 GB; yerleşik yığın ~2–3 GB, gömücü ~1 GB × mavi-yeşil iki renk
  (ADR-0200), godseye 2 GB tavan (DECISIONS.md:15004). Chromium için ~1–2 GB daha, mavi-yeşil
  geçişinde sıkışma riski gerçek; ölçülmedi.

## Nasıl
- **Takılacağı yer:** cihaz kaydı (`app/devices`, `DeviceView.platform`), komut yolu
  (`DeviceTaskBrowser` / `DeviceBrowserGateway`), broker teslimi (ADR-0215 deliver-once).
  Bulut işçisi Cloud Core'a dışarıdan değil, aynı broker sözleşmesiyle (contract v1.7) bağlanan
  ayrı bir kapsayıcı; API sürecine gömülmez (çökerse API düşmesin).
- **execution_target kuralı:** varsayılan tablo (araştırma → bulut; oturum gerektiren/sahibin
  sitesi → owner_chrome; masaüstü → device; zamanlanmış → bulut). Düşüş zinciri olay yazar
  (`execution.fallback`); sessiz düşüş yok. Bulut "oturum/CAPTCHA/giriş duvarı" görürse
  owner_chrome'a *kendiliğinden geçmez* — `ask_owner` (ADR-0207 karar 6 ile tutarlı).
- **compute.run:** ağsız Docker (`--network none --cap-drop ALL --read-only`, CPU/bellek/süre
  tavanı) ilk sürüm; gVisor (`runsc`) ikinci adım. nsjail/Pyodide alternatif
  ([karşılaştırma](https://northflank.com/blog/how-to-sandbox-ai-agents),
  [nsjail](https://www.morphllm.com/nsjail-sandbox)). "Sağlamlaştırılmış kapsayıcı güvenilmeyen
  kod için yetmez, gVisor/microVM ekleyin" görüşü yaygın; model üretimi kodu güvenilmeyen sayılır,
  bu yüzden gVisor'u ilk PR'a değil ikinciye koymak bilinçli bir borç — sahibe açık yazıyorum.
- **Değişmeyenler:** ADR-0207 yasak listesi (banka, e-Devlet, işveren `turka.com`, Kolay Monitor)
  bulutta da geçerli; ödeme kapsam dışı; gizli anahtarlar işçiye verilmez; bulutta sahibin
  çerezi/profili yoktur.

## Maliyet/risk
- **Efor:** büyük (üç PR: işçi+kayıt; kural+olaylar; compute.run).
- **Çalışma maliyeti:** CPX32'de kalırsa €0 ek, ama bellek ölçümü şart (işçiye `mem_limit` 2 GB,
  eşzamanlı oturum 2). CPX42'ye geçiş: +€34/ay, yerinde yeniden boyutlandırma destekli (ADR-0033),
  ayrıca reranker (ADR-0206) da açılabilir hale gelir — iki karar tek geçişte.
- **Lisans:** Playwright Apache-2.0, Chromium BSD/çoklu; zaten projede (THIRD_PARTY). gVisor
  Apache-2.0. Yeni kütüphane önerilmiyor. gVisor'un sorun izleyicisini (syscall uyumsuzlukları)
  okumadım; PR'da entegratör okuyacak.
- **Gizlilik/KVKK:** bulut işçisi sahibin oturumlarını taşımaz; sayfa metni model bağlamına girer
  (ADR-0207 enjeksiyon sınırı aynen). Sunucu IP'si veri merkezi: bot engeli/CAPTCHA çok olur —
  bazı siteler bulutta hiç çalışmayabilir (kanıt: yok, ilk denemede ölçülecek).
- **Cihaz güvenliği / işveren makinesi:** etkilenmez; bu yol sahibin PC'lerine hiç dokunmaz.
- **Risk:** Chromium sızıntısı API ile aynı ana makinede → API'yi düşürmek. Önlem: ayrı
  kapsayıcı, `mem_limit`, sağlık denetimi, öldür-yeniden-başlat.

## Kanıt planı
1. PROVEN_AUTOMATED: kural tablosu + düşüş olayları birim testleri; compute.run'ın ağa çıkamadığı
   (DNS/soket denemesi RED-mutasyonla) testi; broker sözleşme testi iki yarıyı da okur.
2. PROVEN_PROXY: gerçek headless Chromium fixture sitesinde bulut işçisiyle T1 benzeri iş; dev
   yığınında gerçek Temporal.
3. PROVEN_REAL: üretim CPX32'de `docker stats` ile ölçülen tepe bellek (mavi-yeşil geçişi
   dahil) ve sahibin sesle verdiği tek "bulutta ... yap" cümlesi. **Sahip denemeli:** bir haber
   araştırmasını "bulutta" ile istemek; gece işi olarak bir kez bırakmak.

## Karar
**Bulut işçisini CPX32'de (ölçerek, Chromium'a 2 GB tavanla) başlatalım mı; ölçüm sıkışırsa
CPX42'ye (+€34/ay) ayrıca soralım mı?**
Alternatifler: (1) hemen CPX42'ye geç; (2) yalnız tarayıcı için ikinci küçük bir VPS (~€8–15,
izole, API'yi riske atmaz — fiyatını doğrulamadım); (3) yönetilen bulut tarayıcı (Browserbase
vb.) — ücretli, üçüncü tarafa sayfa verisi gider, sağlayıcı arayüzü arkasında ancak önerilmez.
