# Araştırmacı raporu — döngü d20261002

İki öneri yazdım; ikisi de sahibin onayını bekliyor (`awaiting_owner`). Kod, ROADMAP ve kuyruğa dokunmadım. Üçüncü öneri yok: dış taramada roadmap'te bir satırı ilerleten yeni bir şey bulamadım.

## Yazılan dosyalar

1. `team/proposals/2026-10-02-yanlis-anlasilan-cumle-defteri.md`
   - **Satır:** "doğal konuşma" (HAVE, kalite işi kalıyor), sıra 6; ADR-0224'ün ≥ %95 hedefi.
   - **Öneri:** anlaşılmayan ya da yanlış anlaşılan cümle yalnız yazı olarak, kipi ve makinesiyle 30 gün saklanır. Onay Merkezi'nde "Ne demek istemiştin?" listesi çıkar; cevap cümleyi gerçek STT derlemine taşır.
   - **Kanıt:** güçlü, içeriden. ADR-0224 ek 4: derlem 106 cümle, yalnız 3'ü gerçek, sonuç %68,9. Aynı ek bunu "a new idea for the owner" diye not ediyor. Ücretli oturum cümleyi hiç tutmuyor (`realtime_sessions/service.py:1968-1978`).
   - **Risk:** ses kimliği yok (ADR-0171), odadaki başkasının cümlesi de deftere düşebilir. Migration taşıdığı için yayında sahibe sorulur.

2. `team/proposals/2026-10-02-alan-disi-geri-verme.md`
   - **Satır:** "Repairs and improves itself" ve ekip döngüsü; dolaylı olarak sıra 2b ve 2c.
   - **Öneri:** düzeltmesi kartın alanı dışında kalan geri verme çalışanın iki hakkından sayılmaz. Başka kartla çakışma yoksa döngü alanı kendisi genişletir.
   - **Kanıt:** kural `lead.md`'de yazılıyken d20261001'de iki iş aynı nedenle durdu: `narrative-failures-only-model` (11,31 USD) ve `execution-call-site-research` (14,55 USD). Toplam 25,86 USD, döngünün 101,23 USD'sinin yaklaşık dörtte biri.
   - **Not:** TEAM_PROTOCOL metni değişeceği için sahibin onayı gerekiyor.

## Öneri olmayan notlar

- **Windows 10 güvenlik güncellemeleri:** MAIL Windows 10 Home 19045. Arama sonuçları "13 Ekim 2026'da bitiyor" diyordu; Microsoft'un kendi sayfası tüketici ESU'nun 12 Ekim 2027'ye uzatıldığını söylüyor, yani acil bir uçurum yok. MAIL'in ESU'ya kayıtlı olup olmadığını bilmiyorum; sahibin Ayarlar > Windows Update'ten bir kez bakması yeter. Kayıt için KB5126256 (8 Eylül 2026) gerekiyor.
- **C: diski:** bugün ölçtüm, 447 GB'ın 65 GB'ı boş. Denetleyici d20261001'de bulut imajını "C: neredeyse dolu" diyerek kurmadı ve engeli NOT_RUN bıraktı; bu varsayım eskiydi. Hafıza notunu düzelttim. Lead'e: denetleyici diski varsaymasın, ölçsün.
- **Lead için, onaylı `real-host-rehearsal` kapsamında:** `execution-call-site-research`'ün engeli de "sahtede yeşil, gerçekte kırmızı" türünden. Sahte bulut işçisi her yükü kabul ediyor; gerçeği `visible: true, channel: chrome` oturumunu açamaz. Denetleyicinin istediği gerçek `session_open` denemesi bu provaya eklenebilir.
- **Gerçek zamanlı ses:** `gpt-realtime-2.1`'den (6 Temmuz 2026) sonra yeni model bulamadım.
- **Playwright:** 1.63 hâlâ son sürüm; bulut imajımız 1.62.0. Farkı okumadım.
- **Türkçe STT:** Eylül'de Türkçeye özel yeni bir açık model bulamadım. Qwen3-ASR ve VibeVoice-ASR-Streaming çok dilli; Türkçe ölçümlerini görmedim.
- **model2vec:** 0.8.2 (29 Mayıs 2026); potion-multilingual-128M değişmemiş.
- **Chrome:** Windows 10 için duyurulmuş bir destek bitiş tarihi yok.
- **Claude Code (Eylül):** başsız oturumlarda arka plan alt-ajan raporlarının kaybolması düzeltilmiş; döngü `-p` ile koştuğu için sürümü güncel tutmak yeter.

## Eksikler

- Canlı kuyruğu (`/v1/team/queue`) yine okuyamadım; komut izni reddedildi. "Kuyrukta var mı" denetimini HANDOFF, DECISIONS ve döngü raporundaki kart adlarından yaptım.
- `team/reports/d20261002/` boştu; üç rapor olarak `d20261001`, `adr0224-02` ve HANDOFF'taki `cycle-2026-10-01` özetini okudum.
- Google Drive ve artlist bağlayıcıları yetkilendirilmemiş (claude.ai bağlayıcı ayarlarından açılır); bu koşuda gerekmedi.

## Kaynaklar

- [Microsoft: tüketici ESU sayfası](https://www.microsoft.com/en-us/windows/extended-security-updates)
- [BleepingComputer: ESU Ekim 2027'ye uzatıldı](https://www.bleepingcomputer.com/news/microsoft/microsoft-quietly-extends-free-windows-10-esu-support-to-october-2027/)
- [KB5126256](https://support.microsoft.com/en-us/servicing/os/windows-10/2026/09/kb5126256-windows-10-21h2-22h2-standalone-cbs)
- [OpenAI: gpt-realtime-2.1](https://community.openai.com/t/new-realtime-models-on-the-api-gpt-realtime-2-1-and-gpt-realtime-2-1-mini/1385896)
- [Playwright (PyPI)](https://pypi.org/project/playwright/)
- [model2vec sürümleri](https://github.com/MinishLab/model2vec/releases)
- [Claude Code değişiklik günlüğü](https://code.claude.com/docs/en/changelog)
