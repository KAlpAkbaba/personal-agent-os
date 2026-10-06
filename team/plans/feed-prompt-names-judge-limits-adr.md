# ADR (numara lead'in): Besleyici istemi yargıcın sınırlarını yargıcın kendi sayılarıyla söyler

Tarih: 2026-10-06. Kart: feed-prompt-names-judge-limits. Alan: scripts/lib/TeamFeed.ps1.

## Bağlam

2026-10-06 01:30 ve 02:30 besleme koşuları (5,75 + 5,88 USD, 584 + 623 sn) yalnız
"needs_owner is one sentence (a string on one line, at most 400 characters)" ile BÜTÜN olarak
reddedildi; kesilen beş kart kuyruğa girmedi, koltuklar iki kez yarım saat boş kaldı. İstem
(New-TeamFeedCard) sahip maddesi için yalnız "ONE sentence" diyordu; yargıç (Test-TeamFeed)
400 karakter ve tek satır sayıyordu. İki yarı birbirini okumuyordu.

İstemde hiç geçmeyen başka yargıç kuralları da vardı: kart ve sahip maddesi sayılarının AYRI
sayılması (istem "at most max_new items" diyordu), başlığın büyük/küçük harf farkıyla da
yinelenemeyeceği, roadmap_row ardındaki notun içinde parantez olamayacağı, depends_on'daki her
id'nin var olması; alan sınırı "25" elle yazılmıştı.

## Karar

1. Bütün-ya-hiç kuralı sahibin kuralıdır, DEĞİŞMEDİ. Değişen istem ve ret satırının bilgisi.
2. İstemdeki her sayı yargıcın değişkeninden yazılır: $script:TeamFeedOwnerSentenceMax (400) ve
   $script:TeamMaxAreaEntries (25). Metne ikinci kez elle yazılmaz; test değişkeni 401 yapıp
   istemde "401" arar ve hiçbir yerde "400 characters" kalmadığını doğrular.
3. "The script judges, not you" bölümü yukarıdaki her kuralı sayar; "What needs the owner is not a
   card" bölümü "ONE sentence on ONE line, at most N characters" der ve ayrıntıyı goal'a yönlendirir.
4. needs_owner reddi ölçtüğünü yazar: "...; this one has 612 characters" / "...; this one spans 3
   lines" / "...; this one is not a string". Kuralın ilk cümlesi aynı kaldı (mevcut eşleşmeler bozulmaz).

## Neden tek kaynak

İki yerde yazılan bir sayı ayrı ayrı değişir ve iki test paketi de yeşil kalır (memory: contract
halves must read each other). Sözleşme testi istemi yargıcın değişkeninden okur.

## Sonraki adım (bu kartın alanı dışında)

- .claude/agents/lead.md (lead-korumalı): lead rol metnine aynı sınırları yazmak ayrı bir iş.
- scripts/lib/TeamRun.ps1:237 lead'in bölme istemi de "at most 25" diye elle yazıyor; aynı
  düzeltme ($script:TeamMaxAreaEntries) ayrı kartla yapılmalı.
