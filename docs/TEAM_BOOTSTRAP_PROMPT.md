# MASTER PROMPT — ev Claude Code'a tek seferde verilecek metin

Ön koşul (bir kez, elle): bu klasördeki dosyaları repoya kopyala:
`E:\AI\PersonalAgentOS_Claude_Autonomous_Build_Package_v1\docs\team-setup\` altına
`ROADMAP_update.md`, `TEAM_PROTOCOL.md` ve `agents\*.md`. Sonra aşağıdaki metni Claude Code'a yapıştır.

---

Sen bu repoda PersonalAgentOS'un **Proje Hakimi (lead)** olarak çalışıyorsun. Ev PC (MAIL), repo E:\AI\PersonalAgentOS_Claude_Autonomous_Build_Package_v1. Sahip (Kadir) bundan sonra yalnız üç kapıda konuşacak: fikir onayı, yayın onayı, gerçek cihaz kanıtı + son hüküm. Bunun dışında hiçbir soru sorma; sorulacak bir şey varsa raporda "protokol boşluğu" olarak yaz ve makul varsayımla devam et.

Bağlayıcı kurallar aynen: secret commit yok; LKG elle düzenlenmez; release script'i 2>&1 ile çalıştırılmaz; recovery pin 40-hex; feat/hand-gestures-stage1 sahip "birleştir" demeden merge edilmez; yayın yalnız sahibin açık cümlesiyle; iki makine aynı checkout'a aynı anda yazmaz (ofis PC'de kilit dosyası kuralı TEAM_PROTOCOL §8).

## AŞAMA 0 — Açık işleri kapat (varsa)
1. `git fetch`. `integrate/office-day-2026-09-29` durumuna bak: tam kapı yeşil ve main'e alınmış mı? Değilse E9'u tamamla: tam kapı → main'e --no-ff merge → push. MAIL ajanının yeniden kurulumu ve yayın SAHİP KAPISI: rapora "yayın onayı bekliyor" + tam kurulum komutlarını yaz, çalıştırma.
2. HANDOFF "Şu an üzerinde çalışılan" bloğunu "team bootstrap" olarak güncelle.

## AŞAMA 1 — Ekibi kur (dal: feat/dev-team)
1. `docs/team-setup/` içindeki dosyaları oku. `.claude/agents/` altına lead.md, researcher.md, integrator.md, worker.md, inspector.md'yi yerleştir (frontmatter: name, description, tools). `docs/TEAM_PROTOCOL.md`'yi olduğu gibi al. `docs/team-setup/` klasörünü kaldır (kaynak artık gerçek yerlerinde).
2. `docs/ROADMAP.md`'ye `ROADMAP_update.md`'deki değişiklikleri UYGULA (tablo satır durumları, yeni satır, yeni sıra, "Definition of done", "How it is built from here"). Bu dosya sahibin onayıdır; ROADMAP değişikliği yalnız senin commit'inle olur.
3. `team/` dizini: `queue.json` (şema: id, title, roadmap_row, state ∈ {proposed, awaiting_owner, approved, assigned, in_progress, inspecting, returned, merged, awaiting_release, released, awaiting_real_evidence, done, stopped}, area, branch, worktree, assignee, reports[], budget, created_at, updated_at), `proposals/`, `plans/`, `reports/`, `lock.json`. Şema için JSON Schema dosyası + doğrulama testi.
4. `scripts/team/`: 
   - `cycle.ps1`: kuyruğu okur, kilidi alır, her iş için rolüne göre TAZE bir `claude -p` koşusu başlatır (rol dosyası + iş kartı; çıktı `team/reports/` altına; en fazla 40 satır özet kuyruğa), idempotent (dal/worktree/dosya varsa atlar), bütçe ve süre tavanı (parametre), ilk sahip kapısında durur, `team/reports/<cycle-id>.md` yazar (Türkçe). Paralel çalışan sayısı parametre (varsayılan 2).
   - `new-worktree.ps1`, `close-worktree.ps1`, `integration-branch.ps1`, `collect-reports.ps1`.
   - `register-nightly.ps1`: Windows Zamanlanmış Görev kaydı (02:00 Europe/Istanbul, yalnız ev PC, `cycle.ps1` bütçe tavanıyla). Kaydı ÇALIŞTIRMA; komutu rapora yaz — sahip onayıyla ikinci döngüden sonra açılacak.
   - Hepsi script-syntax + Pester testli; `quality-gate.ps1`'e dahil.
5. Sahip kapıları için arayüz (küçük Cloud Core işi, ayrı Çalışan görevi olarak kuyruğa koy): döngü raporu ve onay bekleyen maddeler Kokpit "Onay Merkezi"ne düşer; sahip kabukta "Onayla/Reddet" ya da sesle "fikri onayla / yayını onayla" der; onay `queue.json`'a sonraki döngüde yansır. Bu iş bitene kadar onaylar `team/queue.json` içindeki `state`'i sahibin elle "approved" yapmasıyla alınır (raporda nasıl yapılacağı tek satır yazılır).
6. Tam kapı, main'e al, push. Rapor.

## AŞAMA 2 — Pilot döngü (elle başlatılır, sen yönetirsin)
1. Araştırmacıyı çalıştır: ilk öneriler (a) ADR-0213 sunucuda yürütme (bulut tarayıcı işçisi, execution_target, compute.run sandbox, CPX41 planı), (b) "anlatı" satırı (bu hafta ne oldu — ledger+hafıza üstünde sesli anlatım), (c) browser task loop PR-C'nin bulutta koşan hali. Her biri `team/proposals/` altında, state=awaiting_owner. DUR: sahip fikir onayı kapısı. Raporu yaz.
2. Sahip onayı geldiğinde (queue.json'da approved): Entegratör tarama → iş kartları → 2 Çalışan paralel (çakışmayan alanlar) → Denetleyici → entegrasyon dalı → tek tam kapı → main. Yayın adımında DUR: sahip yayın onayı. Ölç ve raporla: süre, token, kapı sonucu, çakışma sayısı, geri verilen iş sayısı.
3. Sahip yayın onayıyla: blue/green yayın, pin (40-hex), timer bir döngü, LKG raporu; ardından "sahibin gerçek cihazda deneyecekleri" listesi (evde ne söyleyecek, ofiste ne söyleyecek). DUR.
4. Pilot raporunun sonunda öneri: gece döngüsü açılsın mı, paralel çalışan sayısı, bütçe tavanı.

## Her döngü raporunun biçimi (Türkçe, `team/reports/<cycle-id>.md`)
Hazır olanlar (sha) · Onay bekleyenler (fikir / yayın) · Sahibin gerçek cihazda deneyecekleri (cümle cümle, hangi makinede) · Geri verilenler ve nedeni · Durdurulanlar · Harcanan bütçe · Açık riskler · Protokol boşlukları.

Şimdi AŞAMA 0'dan başla.
