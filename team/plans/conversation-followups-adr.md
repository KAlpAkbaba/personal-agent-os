# ADR (taslak, numarasız) — Konuşmalardan takip: kişi kartları ve 'tamam' denmeden yazılmayan takvim

**Durum:** önerildi (conversation-followups, d20261006, worker-1). Numara ve `docs/DECISIONS.md`'ye
taşıma lead'in.

## Bağlam
Sahip, 2026-10-05: bitmiş bir konuşmadaki sözler ('yarın ararım', 'cuma göndereceğim'), tarihler ve
kişiler (a) bir kişi kartına, (b) takvime — ama 'tamam' demeden yazılmadan — gitsin; 'Ahmet'e ne söz
vermiştim', 'Ayşe ile en son ne konuştuk' tarihle cevaplansın. Hiçbir şey uydurulmasın.

## Karar
1. **İki yeni tablo** (`people_cards`, `people_followups`; göç `0076_conversation_followups`,
   `0075_urgent_alert_receipts` üstüne, genişletme-yalnız, geri alınabilir). Kart Türkçe katlanmış
   adla tekil; takip satırı alıntısını (`quote`) ve satırını (`conversation_id` + `segment_seq`)
   taşır. **KVKK (sahibin kuralı 2026-09-18/19: başkalarının konuşması 'unut'a kadar):** takip
   konuşmasıyla yaşar — `conversation_id` `ON DELETE CASCADE`; 'unut' (`forget_all`) ya da tek
   konuşmanın silinmesi karşı tarafın hiçbir sözünü hiçbir tabloda bırakmaz. Kart satır METNİ
   tutmaz: `last_topic_seq` yalnız satırın numarasıdır, konu hatırlamada dökümden okunur; döküm
   silinince konu yoktur, kartta yalnız ad, ilişki ve son konuşmanın günü kalır. Kart silinirse
   takipleri gider. (İlk taslaktaki "konuşma silinse de takip alıntısıyla kalır" kararı denetimde
   KVKK'ya aykırı bulundu ve kaldırıldı.)
2. **Çıkarım bir sağlayıcı arayüzünün arkasında** (`FollowupExtractor`): Haiku
   (`assistant_chat_model`), sabit şema araç olarak ZORLANIR (`tool_choice`), döküm kullanıcı
   turunda VERİ olarak gider, sistem isteminde değil. Anahtar yoksa `NoFollowupExtractor` hiçbir şey
   çıkarmaz ve bunu söyler (`followups_not_configured`).
3. **Uydurma yok — deterministik süzgeç** (`_validate`): (a) atıf yapılan satır konuşmada olmalı,
   (b) `quote` o satırın metninde (Türkçe katlanmış) geçmeli, (c) kişi adı konuşmada ya da bir
   konuşmacı etiketinde geçmeli, (d) ilişki ('iş arkadaşı') yalnız atıf satırında geçiyorsa tutulur,
   (e) tarih konuşmadan en çok 400 gün sonrası olabilir. Tutmayan öğe düşer ve sayılır.
4. **Kart ve sözler hemen yazılır** (atıflı hafıza); **takvim öğesi** — sahibin tarihli sözü ya da
   tarihli buluşma — `calendar_state='proposed'` olur ve TEK toplu soru sorulur ("Konuşmadan 2 takvim
   takibi çıkardım: … Takvime ekleyeyim mi?"). Yalnız 'tamam' (`answer_followups(accepted=True)`)
   takvimin kendi propose → read_proposal → commit kapısından geçirir (`calendar_write_enabled`
   bayrağı ve onay kapısı aynen geçerli); 'hayır' hepsini `declined` yapar. Takvim tanımlı değilse
   öneriler bekler, kartlarda durur.
5. **Konuşma başına bir kez**: takipleri alınmış konuşma yeniden işlenmez (ikinci geçiş hiçbir şey
   eklemez).
6. `CalendarService` iki küçük ek aldı: `configured` özelliği ve `read_proposal(proposal_id=…)`
   (odak yerine adla geri okuma — toplu soru birden çok öneriyi birlikte okur).
7. **Rotalar `create_app`'ten**: `app/people/routes.py` `ROUTERS = [router]` ilan eder,
   `app/registry.py` bağlar; `main.py`'ye satır eklenmez (registry-models-and-routers kuralı).

## Sonuçlar / açık kalanlar
- Tetik: `POST /v1/conversations/{id}/followups` (konuşma bittikten sonra) ve
  `POST …/followups/answer {"tamam": bool}`. Konuşma durdurulunca otomatik çağrı
  (`app/conversations/routes.py` stop), sesli 'Ahmet'e ne söz vermiştim' niyeti
  (`app/voice/intents.py` + gerçek zamanlı araç) ve web kartı ekranı bu kartın alanı dışında:
  bağlama kartı gerekir.
- Saatsiz gün için takvim öğesi 09:00–09:30, başında hatırlatma; saatli öğe 60 dk.
- Bir öğe kapıda reddedilirse (ör. yazma bayrağı kapalı) `failed` olur; yeniden deneme yolu yok.
- 'Hatırlatma' (takvim yoksa) ayrı bir hatırlatıcıya yazılmıyor; takvim yoksa öneri bekler.
