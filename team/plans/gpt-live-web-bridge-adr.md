# ADR (taslak, numarayı lead verir): GPT-Live web köprüsü — 'openai-live' lehçesi + devir köprüsü, ?ses=live

Durum: kabul (YALNIZ ÖLÇÜM). Kart: gpt-live-web-bridge (d20261004). Öneri: team/proposals/2026-10-04-gpt-live-olcum.md.
Varsayılan sağlayıcı, `openai-realtime` lehçesi, `localMode.ts`, yerel kip ve parametresiz oturum gövdesi DEĞİŞMEZ.

## Kaynaklar (okunma: 2026-10-04, Entegratör planı team/plans/gpt-live-web-bridge-integration.md üzerinden)
- https://developers.openai.com/api/docs/guides/live-delegation — devir, `session.commentary.append` /
  `session.thinking.append`, "Interrupting the spoken conversation leaves backend work running",
  "collect transcripts and keep the current task state yourself", ekleme başına ≤ 500 token.
- https://developers.openai.com/api/docs/guides/live-conversations — `session.closed`, transkript deltaları.
- https://developers.openai.com/api/docs/guides/live — WebRTC: medya izi + JSON veri kanalı (ayrıntı yok).
- İKİNCİL: https://learn.microsoft.com/en-us/azure/foundry/openai/gpt-live-reference (Foundry, 2026-09-17).

## Karar
1. `dialects/openaiLive.ts` (`openai-live`), yalnız belgelenmiş olayları eşler:
   `session.input_transcript.delta` → `owner_transcript final:false`; `session.output_transcript.delta` →
   `response_text final:false`; `session.delegation.created` → `{type:'delegation', delegationId, offsetMs?}`
   (id yoksa olay yok); `session.closed` → `disconnected` (`session_closed:<reason>`); `error` → `error`.
   Gerisi `[]` — konuşma/ses başladı-bitti, yanıt yaşam döngüsü, transkript-bitti UYDURULMAZ.
   Giden: `commentaryAppend(id,text)` (id'siz → `[]`), `thinkingAppend(id|null,text)`.
   `cancelResponse/submitToolResult/notifyToolCompleted/say` → `[]` (belgelenmiş karşılık yok; `say`'in id'si yok).
2. **Devir olayı iş metnini taşımaz** (kartın "delegation_id + metin" ifadesi satıcı belgesiyle düzeltildi):
   metni `DelegationBridge` tutar — son devirden beri gelen `input_transcript` deltaları, en çok 4000 karakter
   (`MAX_EVENT_TEXT_CHARS`), her devirde sıfırlanır.
3. **500 token kesmesi:** istemcide tokenizer yok, bağımlılık eklenmez. Kural: 1200 karakter (Türkçe ≈ 3-4
   karakter/token → 500 tokenın güvenli altı), son kelime sınırında, sonuna "…". Bölünmeyen dizgi sert kesilir.
4. `delegation.ts` — durum tablosu `delegation_id → bekliyor | bitti | iptal`. Gönderim yalnız `deliver()`
   içinden: id boş/bilinmiyor → gitmez; `iptal` → gitmez; `bitti` → ikinci kez gitmez. İptal kaynakları:
   kontrolcünün `disconnect()`'i (kapanıştan ÖNCE beklenir), bacak sökümü (`teardownLeg`, yeniden bağlanma dahil:
   yeni bacak = yeni satıcı oturumu) ve `disconnected` olayı (`session.closed` dahil).
4a. (1. dönüş) `commentary` artık `boolean` döner: taşıyıcının `appendCommentary`'si yoksa kontrolcü
   `delegation.commentary_unsupported` yazar ve `false` döner; köprü devri `bitti` DEĞİL `gonderilemedi`
   işaretler, `delegation.undelivered` yazar (`delegation.done` yazılmaz) ve yeniden denemez. İptal edilmiş devirde
   kalan araçlar koşmaz (döngü her araçtan önce durumu okur; testle korunur).
4b. (1. dönüş) Relay her `/events` yanıtında oturumun BÜTÜN bekleyen sideband'ını döndürüp kuyruğu boşaltıyor
   (brifingler o anda teslim sayılır). Köprü `say` satırlarını devrin sonucuna katar; geri kalan her çerçeveyi
   (iptal yanıtında hepsini) kontrolcünün `onSideband`'ına verir — hiçbiri yere düşmez. `say` iptal yanıtında
   da `onSideband`'a gider; `openai-live`'da `say` karşılıksız olduğundan yalnız tanılama satırına yazılır.
4c. Tarayıcıda koşan araçlara `arguments: {}` gider; yerel kipin önce koşturduğu yerel adım (`runLocally`, örn.
   kamera) devirde YOK. Yanıt yine dürüst (makbuzu relay kurar) ama yerel kipinkinden farklı olabilir.
4d. İş metni son devirden beri duyulan BÜTÜN sahip transkripti parçalarıdır (sohbet dahil); satıcı devirle iş
   metni göndermediği için kabul edildi.
5. Araya girme devri iptal ETMEZ; yön değiştirme yeni bir devir olarak tek yönlendiriciden geçer.
6. `?ses=live` → oturum açma gövdesine `prefer_provider:'openai-live'`; yoksa gövde bugünküyle birebir (anahtar yok).
   Kaynak: `deps.preferProvider`, verilmemişse `api.ts pagePreferProvider()` (sayfanın `location.search`'ü).
   Sunucu sözleşmesi alanı bilmiyorsa `validateCreateBody` düşürür ve Türkçe söyler; sunucu başka sağlayıcı
   açarsa tanılama satırına (`contractNotice`) "Ölçüm: istenen sağlayıcı X, açılan Y." yazılır.

## ORTAK SÖZLEŞME (gpt-live-provider kartında AYNI metin)
Dialect adı 'openai-live'; oturum açma gövdesinde isteğe bağlı 'prefer_provider' (örn. 'openai-live'); web, bir
devri mevcut relay'e 'utterance' olayı olarak yollar, payload {source: 'delegation', delegation_id: '<satıcının
id'si>'}; devrin sonucu satıcıya YALNIZ aynı delegation_id ile 'session.commentary.append' olarak döner,
delegation_id'siz ya da iptal edilmiş devrin sonucu asla gönderilmez; iptal edilen devir relay'e
'delegation_cancelled' istemci olayı olarak yazılır (payload {delegation_id}).

**Geçici taşıyıcı:** relay `delegation_cancelled` türünü bilmiyor (bilinmeyen tür → 422, bütün parti düşer;
`service.py STATE_EVENT_KINDS` watch-voice alanında). Bu yüzden şimdilik `{kind:'state', payload:{event:
'delegation_cancelled', delegation_id, reason}}` yazılır: sunucu FSM'yi değiştirmez (`state` alanı yok), payload
denetim kaydına girer. Tür sunucuya eklenince yalnız `delegation.ts cancelledEvent` değişir.

## Doğru konuşma (ADR-0063)
Söylenen cümle relay'in makbuzlu yanıtından gelir (`localMode.ts speechOf`/`toolOf`/`saidFrames`, değiştirilmeden
içe aktarılır). Araç `failed` → relay'in hata cümlesi ya da "Komut yürütülemedi efendim."; istek düşerse aynı;
hiçbir niyet çözülmezse "Anlayamadım efendim." Başarı cümlesi ancak relay `result.speech` döndürdüyse.

## UNVERIFIED
Türkçe desteği (hiçbir sayfada yok); WebRTC SDP uç noktası ve veri kanalı adı (sunucu kartının descriptor'ı söyler);
modelin konuşmasını kesen komut; satıcı tarafı devir iptal olayı; eşzamanlı birden çok client devri (yasak değil,
belgelenmemiş); `commentary.appended` "söylendi" demek DEĞİL; `commentary.append`'in `delegation_id:null` kabulü.

## Bilinen eksik (alan dışı — ALAN_ISTEGI ile istendi; kırmızı test: delegation.test.ts "the real WebRTC transport carries commentary to the data channel")
- `webrtc.ts`: `appendCommentary` yok → gerçek veri kanalına commentary ancak 3 satırla gider
  (`appendCommentary(id,t){ for (const m of this.dialect.commentaryAppend?.(id,t) ?? []) this.send(m); }`).
  O gelene kadar kontrolcü `delegation.commentary_unsupported` günlüğü yazar; sahibin ölçümü bunu bekler.
- `tests/voice/transport.test.ts:41` lehçe listesini `["openai-realtime"]` sabitliyor → `["openai-live","openai-realtime"]`.

## Sahibin A/B akşamı (PROVEN_REAL yalnız sahibin raporundan)
1. Sunucuda gpt-live-provider ayarı açılır (varsayılan KAPALI), `realtime-session-contract.json` `prefer_provider`'ı içerir.
2. Aynı on cümle (K66 listesi) hazırlanır; içinde en az biri araştırma ("… araştır"), biri yolda yön değiştirme
   ("… aç — yok, vazgeç, şunu yap").
3. Önce `?ses` OLMADAN kabuk açılır; tanılama satırında sağlayıcı okunur; on cümle söylenir; oturum kapatılır.
4. Sonra aynı adres `?ses=live` ile açılır; tanılama satırı `openai-live` göstermeli ("istenen …, açılan …" notu
   ÇIKMAMALI); aynı on cümle aynı sırayla söylenir; yön değiştirmede araya girilir.
5. `session_benchmark` iki oturumu `compare_reports` ile yan yana verir; "hangisi daha doğal" kararı sahibindir.
6. Deneme Onay Merkezi'ne yazılır; benimseme ayrı karar. Geri alma: `?ses=live` kullanmamak; kod için tek revert.
