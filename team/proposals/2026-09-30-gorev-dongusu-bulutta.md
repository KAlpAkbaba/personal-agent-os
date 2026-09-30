# Öneri: Tarayıcı görev döngüsü PR-C'nin bulutta koşan hali

Tarih: 2026-09-30 · Araştırmacı · Durum: awaiting_owner
Bağımlılık: `2026-09-30-bulutta-yurutme.md` onaylanmadan başlamaz.

## Ne
Roadmap: order 2a/2b kesişimi. ADR-0207 PR-C (model planlayıcılar + altı bağlayıcı risk +
gerçek Chrome) bugün "sahibin kendi Chrome'unda" tanımlı; ama PR-C `<MAĞAZA>` ve `<WEBMAIL>`
yer tutucuları doldurulmadan başlayamıyor (ADR-0207 "Still open"). Öneri: PR-C'nin ilk
kanıt hedefini **bulut tarayıcı işçisine** (ADR-0213) çevirmek; sahibin Chrome'u ikinci ayak.
Sahibin cümlesi: "Bulutta şu haberi bul ve özetle", "şu formu doldur ama gönderme" (T1, T2, T4).

## Neden şimdi
- PR-C'nin bloke eden girdileri sahipte (mağaza, webmail). Bulut işçisinde oturum gerektiren
  T3 (gerçek mağaza sepeti) ve T5 (webmail) ZATEN yapılamaz; T1, T2, T4 yapılabilir ve
  sahibin hiçbir şeyine dokunmaz. Yani PR-C'nin yarısı sahibi beklemeden ilerler.
- Bulutta hata maliyeti düşük: sahibin oturumu, çerezi, işveren sitesi yok. En riskli
  kısım (kimliksiz yanlış tıklama) daha güvenli ortamda önce denenir.
- Altı bağlayıcı risk (ADR-0207, "Recorded for PR-C") bulut için de aynen geçerli; bulutta
  ikisi kolaylaşır (saklama: sahibin postası okunmaz; site-adı kuralı) ikisi zorlaşır (bot
  engeli/CAPTCHA, veri merkezi IP'si).
- Dış kanıt: açık kaynak ajan çerçeveleri var — Stagehand (MIT), Browser Use (açık kaynak)
  ([Firecrawl](https://www.firecrawl.dev/blog/best-browser-agents),
  [Skyvern karşılaştırması](https://www.skyvern.com/blog/browser-use-vs-stagehand-which-is-better/)) —
  ama projenin kendi döngüsü (numaralı gözlem, ref, `risk_ceiling`, yasak liste) zaten yazıldı ve
  sahibin altı kararını taşıyor; bunları yerine koymak PR-B'yi çöpe atar. Önerim: kullanma,
  yalnız planlayıcı istem tasarımı için oku. Lisans/sorun izleyicisini okumadım; kullanmayı
  önermediğim için gerek görmedim.

## Nasıl
- **Takılacağı yer:** `TaskPlanner` arayüzü (kural tablosu → Haiku → Sonnet, ADR-0207 b);
  `DeviceTaskBrowser` bulut cihazını aynı port olarak kullanır (`device_kind=cloud`); yeni
  ana kod planlayıcı modelleri ve altı riskin kapatılması.
- **Altı riskin karşılığı:** (1) `fill/select/set_checked` öğe adından sınıflanır ve `risk_ceiling`
  taşır; (2) isimsiz/sözlük dışı kontrol için ikon-yalnız kabul testi ve karar (öneri: isimsiz
  ve düz bağlantı olmayan kontrol `ask_owner`); (3) site-adı kuralı yalnızca "…'da/'dan/'ya/sitesi"
  konumundaki kelimeleri okur; (4) `state_json` son gözlem için saklama süresi — bulutta
  N saat sonra silinir, sahibin postası zaten yok; (5) SPA'da okuma-geri ile eylem arası yeniden
  bağlanan kontrol için düşmanca kabul testi; (6) kart alanı `autocomplete`/ad dışında tür ve
  çevre etiketiyle de tanınır, ayrı test.
- **Değişmeyen:** ödeme kalıcı kapsam dışı; sahip yokken görev yok (karar 3) — DİKKAT: bulut
  yürütme sahip yokken de koşabildiği için bu karar bulutta *yeniden yorumlanmalı*: öneri, sahip
  bulutta koşan görevi başlatmış ve açık bırakmışsa devam eder, ama yeni görevi hiçbir rutin
  başlatmaz. Bu sahibin kararının kapsamını değiştirdiği için ayrıca sorulmalı (aşağıda).

## Maliyet/risk
- **Efor:** büyük (planlayıcı+altı risk+değerlendirme kümesi).
- **Çalışma maliyeti:** görev başına ≤25 tur × ≤2 model çağrısı; çoğu tur kural tablosu/Haiku.
  Bir T1 için kabaca 10–15 Haiku çağrısı; gerçek tutarı **ölçmedim**, kabul testinde
  ölçülüp sahibe yazılacak. Sonnet yalnız başarısız doğrulama sonrası.
- **Lisans:** yeni bağımlılık yok.
- **Gizlilik/KVKK:** sayfa içeriği modele gider (mevcut araştırma yoluyla aynı); bulutta sahibin
  kişisel oturumu yok. Yasak liste (banka, e-Devlet, işveren, Kolay Monitor) bulutta da işler.
- **Risk:** bot engeli yüzünden T1/T4'ün bazı sitelerde bulutta çalışmaması (kanıt yok; ilk denemeden
  öğrenilecek); model istem enjeksiyonu — yapısal savunma zaten kodda, düşmanca fixture'lar korunur.

## Kanıt planı
- PROVEN_AUTOMATED: mevcut T1–T5 + üç ret sahte planlayıcıyla; altı risk için yeni testler,
  hepsi için mutasyon RED (ADR-0207 tarzı).
- PROVEN_PROXY: gerçek modeller, fixture sitesi, gerçek headless Chromium (bulut işçisi imajı).
- PROVEN_REAL: T1, T2, T4'ün gerçek sitelerde bulut işçisiyle koşması, kanıt dosyaları
  `docs/evidence/`. **Sahip denemeli:** bir haber özeti, bir kişi-formu doldurma (gönderme),
  bir YouTube açma (bulutta ses çalmaz → `currentTime` ilerlemesi yeterli; sesi sahip duymaz,
  bu satırın anlamı değişir, kabul kriteri yeniden yazılmalı). T3/T5 sahibin Chrome'una kalır.

## Karar
**Önce T1/T2/T4'ü bulutta kanıtlayalım mı ve sahip yokken, sahibin başlattığı açık bir görevin
sürmesine izin verelim mi (yeni görev başlatan rutin hâlâ yok)?**
Alternatifler: (1) PR-C'yi yalnız sahibin Chrome'unda bekle (mağaza/webmail cevabı gelene kadar
durur); (2) hem bulut hem sahip Chrome'u aynı anda ilerlet (iki kat kanıt yükü); (3) bulut hazır
olana kadar hiçbir şey yapma.
