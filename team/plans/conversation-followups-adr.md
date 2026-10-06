# ADR (taslak, numarasız) — Konuşmalardan takip: kişi kartları ve 'tamam' denmeden yazılmayan takvim

**Durum:** önerildi (conversation-followups, d20261006, worker-1). Numara ve `docs/DECISIONS.md`'ye
taşıma lead'in.

## Bağlam
Sahip, 2026-10-05: bitmiş bir konuşmadaki sözler ('yarın ararım', 'cuma göndereceğim'), tarihler ve
kişiler (a) bir kişi kartına, (b) takvime — ama 'tamam' demeden yazılmadan — gitsin; 'Ahmet'e ne söz
vermiştim', 'Ayşe ile en son ne konuştuk' tarihle cevaplansın. Hiçbir şey uydurulmasın.

## Karar
1. **İki yeni tablo** (`people_cards`, `people_followups`; göç `0071_conversation_followups`,
   genişletme-yalnız, geri alınabilir). Kart Türkçe katlanmış adla tekil; takip satırı alıntısını
   (`quote`) ve satırını (`conversation_id` + `segment_seq`) taşır. Konuşma silinirse takip kalır
   (`conversation_id` NULL, alıntı satırda); kart silinirse takipleri gider.
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

## Sonuçlar / açık kalanlar
- Tetik: `POST /v1/conversations/{id}/followups` (konuşma bittikten sonra) ve
  `POST …/followups/answer {"tamam": bool}`. Konuşma durdurulunca otomatik çağrı
  (`app/conversations/routes.py` stop), sesli 'Ahmet'e ne söz vermiştim' niyeti
  (`app/voice/intents.py` + gerçek zamanlı araç) ve web kartı ekranı bu kartın alanı dışında:
  bağlama kartı gerekir.
- Saatsiz gün için takvim öğesi 09:00–09:30, başında hatırlatma; saatli öğe 60 dk.
- Bir öğe kapıda reddedilirse (ör. yazma bayrağı kapalı) `failed` olur; yeniden deneme yolu yok.
- 'Hatırlatma' (takvim yoksa) ayrı bir hatırlatıcıya yazılmıyor; takvim yoksa öneri bekler.
