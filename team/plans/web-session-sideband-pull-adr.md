# ADR (numara lead'in): web oturumu sideband'ı 15 sn'de bir çeker

- Tarih: 2026-10-06
- Kart: web-session-sideband-pull (ROADMAP "Owner's queue" madde 1'in web yarısı;
  briefing-ladder-fallback'in tamamlayıcısı)

## Bağlam

Web oturumu cihaza bağlı değil; sunucunun ona itebileceği bir kanal yok. Kuyruğa giren kare
(`queue_sideband_frame` -> `context_json['pending_sideband']`) yalnız `/events` cevabıyla ya
da attach'te istemciye gidiyordu. Sahip sekmeyi açık bırakıp sustuğunda brifing
(`queued_to_session`) bir sonraki sözüne kadar bekliyordu.

## Karar

1. `GET /v1/voice/realtime/sessions/{id}/sideband` (`service.pull_pending_sideband`):
   `/events` ile aynı kimlik kuralı (`require_live` + `require_leg`: 409 yanlış bacak,
   410 kapalı/süresi dolmuş). Bekleyen kare varsa boşaltır, `say` karelerinin
   `briefing_ids`'ini `VIA_VOICE` damgalar, `updated_at`'i ilerletir, commit eder. Yeni audit
   eylemi yok.
2. Boş çekiş hiçbir şey yazmaz: context, `updated_at`, audit satırı, commit yok.
3. İstemci (`controller.ts` ve `localMode.ts`, tek sabit `SIDEBAND_PULL_MS = 15_000`,
   `contract.ts`'te): bacak canlıyken sabit aralıkla çeker; uçakta en çok bir istek;
   raporlayıcıda olay bekliyorsa (yerel kipte bir tur sürüyorsa) tik atlanır; kareler
   `/events` karesinin gittiği aynı yoldan (`onSideband` / `saidFrames` + `speak`)
   işlenir; 410 terminal (yeniden bağlanma yok); başka hata yalnız o tiki kaybettirir.
   Zamanlayıcı `openLeg`'de kurulur, `teardownLeg`'de temizlenir (kapanış, gone, ağ
   kaybı, reattach, dispose). Yerel kipte ayrı bir `sidebandScheduler` portu var
   (tarayıcıda gerçek zamanlayıcı); konuşma koruma zamanlayıcısının listesi karışmaz.

## Neden

- **Neden SSE/WebSocket değil:** tek yönlü bir kanal bugün yok; çekiş mevcut kimlik
  kuralıyla ve mevcut yönlendiriciyle, yeni bağımlılık ve yeni bağlantı yaşam döngüsü
  olmadan çalışan en ucuz yol. SSE sonraki adım olarak yazılır (15 sn yetmezse).
- **Neden boş çekiş yazmaz:** `sweep_idle_sessions` atıllığı `updated_at` ile ölçer.
  Unutulmuş bir sekme 15 sn'de bir dokunsaydı ölü oturum hiç süpürülmezdi; dakikada 4
  audit satırı da kayda gürültü olurdu.
- **Neden damga çekişte:** "kuyruk teslim değildir" - satırı teslim edildi diye
  işaretleyen, kareyi gerçekten seslendirecek istemciye veren istektir (ADR-0114 dersi).
- **Neden 15 sn:** "kısaca haber ver ve bekle" için yeterli hız; sunucuya oturum başına
  dakikada 4 hafif GET, boşsa yazmasız.

## Sonuç / risk

- Brifing sessiz açık sekmede en geç ~15 sn içinde söylenir ve `delivered_via='voice'`
  olur; aynı kare boşaltıldığı için ikinci kez söylenmez.
- Bilinen sınır: kare istemciye verildikten sonra sekme o an kapanırsa (cevap gelmiş,
  konuşma başlamamış) satır teslim sayılır - `/events` yolunun bugünkü sınırıyla aynı.
- Testlerde iki eski "canlı bacakta hiç zamanlayıcı yok" iddiası (gone-is-terminal:266,
  session-storm:233) artık canlı bacağın çekiş zamanlayıcısını görür (1).
- Kural: canlı bacak = 1 zamanlayıcı (15 sn çekiş); yeniden bağlanma ikincisini yığmaz,
  kapanmış / 410 bacakta 0. İki test bunu `toBe(1)` ile tutar (iki clear kaldırılınca
  "expected 3 to be 1").
