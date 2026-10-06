# ADR (numarayı lead verir): Önemli olunca telefon çalsın - bağlama (urgent-alert-wire)

Tarih: 2026-10-07 · Kart: `urgent-alert-wire` · Önceki: `team/plans/urgent-alert-rung-adr.md` (paket)
Durum: UYGULANDI (main.py kayıt satırları hariç - ALAN_ISTEGI: `services/api/app/main.py`)

## Karar

1. **Sıra** `models.LADDER = (toast, alarm, sound, push, inbox)`; `CHANNEL_ALARM = "alarm"`. Şema değişmez
   (`delivered_via` String(16)). `ladder.default_rungs(..., alarm_rung=None, owner_present=None)`; alarm
   yoksa merdiven ve toast davranışı bugünküyle aynı. Alarm `True` = Pushover kabul etti + makbuz tabloda;
   "gördü" değil.
2. **Gece tuzağı.** `alarm_rung` verilince toast, alarm'ın çalacağı satırlarda (`AlarmRung.rings_for`:
   rung kullanılabilir VE önemli) "gösterildi"yi ancak sahip varlığı biliniyorsa sona sayar.
   Varlık `app.urgent_alert.presence.owner_present`: companion kalp atışlarındaki `input_idle_s`,
   rapor yaşı eklenerek, yalnız 90 sn'den taze rapordan; < 120 sn = var. Rapor yok / bayat / boşta
   süresi yok = bilinmiyor = yok: toast gösterilir, `attempted`a yazılır, merdiven `alarm`a iner.
   Önemsiz satırda toast eskisi gibi biter.
3. **Önemin tek kaynağı** `app.telephony.policy`: `important_kind()` = `_NOTIFICATION_KINDS` (aramanın
   tablosu) ∪ `ALERT_ONLY_KINDS` (`urgent_alert.test` - önemli ama asla arama değil: bir kanalın denemesi
   ötekinin dakikasını harcamaz). Çalışma anında okunur; ikinci liste yok. `ALERT_CATEGORIES` çağrı
   türü → tek sözcük (güvenlik/sürüm/harcama → `sistem`, aktivra → `Aktivra`, alarm → `ev`). "Nöbet/haber"
   ve "ev nabzı" için bugün bildirim türü yok; eklenen tür `_NOTIFICATION_KINDS`'a girdiği an önemlidir,
   kategorisi yoksa test kırmızıdır.
4. **Makbuz tablosu** `urgent_alert_receipts` (göç `0073_urgent_alert_receipts`, down `0072_money_ledger`;
   birleştirmede yeniden zincirlenebilir): receipt_id unique, outcome/source CHECK, FK notifications,
   kısmi index `closed_at IS NULL`. `SqlReceiptStore` makbuz satırını ledger satırından ÖNCE commit eder
   (ledger hatası bir satıra mal olur, çalmayı durduracak tek tutamağa değil). Ledger: `urgent_alert`
   alt sistemi, `alert.sent|seen|unseen|refused`, `source = olay türü`, `source_ref = notification:<id>`;
   detayda makbuzun ilk 6 karakteri, asla sağlayıcı yanıtı (kullanıcı anahtarını taşır).
5. **Görüldü başka yoldan.** Döngü her geçişte önce açık makbuzu olup bildirimi okunmuş (`read_at`)
   satırları bulur: Pushover'da `cancel`, `outcome=cancelled`, `source=inbox`, `seen_at=read_at`, ledger
   `alert.seen` (`source: inbox`). `mark_read`'e kanca yerine döngü: `read_at` yazan her yol (web, toast,
   ileride ses) aynı şekilde yakalanır; gecikme en çok bir döngü aralığı (60 sn).
6. **Döngü** `UrgentAlertLoop` (`urgent_alert_receipts` sağlık adı, 60 sn): açık makbuz yoksa sağlayıcıya
   sıfır istek; anahtarsız süreçte de koşar (sağlık "yapılandırılmamış"ı "ölü"den ayırır).
7. **Bağlantı kökü** `urgent_alert_link_base` (tailnet https kökü); bağlantı `{kök}/notifications/{id}`,
   `text.link_refusal(..., root=kök)` şema+host:port'u TAM eşler (`link_not_configured_root`): başka bir
   `*.ts.net`, alt alan, port farkı reddedilir. Kök boş/geçersizse rung kurulmaz (her alarm reddedilirdi).
   Web'de `notifications/[id]` sayfası yok; bağlantı mevcut `/notifications`'ı açar.
8. **Sırlar.** İki anahtar SecretStr; compose iletir, `set-cloud-secret.ps1` örneklerinde adları geçer.
   httpx/httpcore `token=` maskesi artık `pushover` modülü yüklenirken kurulur (Twilio SID filtresinin
   yaptığı gibi), sağlayıcı oluşmadan önce. Rotalar anahtar döndürmez; testte caplog DEBUG + httpx.
9. **Rotalar** `POST /v1/urgent-alert/test` (sahibin oturumu, step-up yok; bağlı değilse 409; saatte 3,
   `notifications` tablosundan sayılır - yeniden başlatma sıfırlamaz; 4. → 429) → `urgent_alert.test`
   türünde `urgent` öncelikli bildirim; `GET /status` → `{configured, open_receipts, last_seen_at,
   last_outcome}`. Web `/core/urgent-alert`: bağlı/bağlı değil, "görüldü HH:MM"/"görülmedi", düğme yalnız
   bağlıyken.

## Paket değişiklikleri (arayüz gerektirdiği için)

`AlarmRung`: `link_root`, `on_refused` (alert.refused için), `rings_for()`, `provider`/`store` özellikleri;
`CHANNEL_ALARM` `notifications.models`'tan. `text.link_refusal/compose`: `root=`. `pushover`: maske import'ta.

## Sonuçlar

+ Önemli olay gece boş masada toast'ta kaybolmaz; telefon çalar, gördü/görmedi ledger'da.
− Varlık bilgisi companion'a bağlı; boşta süresi gelmeyen kurulumda önemli satırda masa başındayken de
  telefon çalar (bilinçli: yüksek ses sessizlikten iyidir).
− `read_at` → iptal en çok 60 sn gecikir.
