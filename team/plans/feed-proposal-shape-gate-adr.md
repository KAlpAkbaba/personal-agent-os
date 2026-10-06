# ADR (numara lead'in): besleyicinin sahip maddesi araştırmacının öneri biçimini taşır

Tarih: 2026-10-06 · Görev: feed-proposal-shape-gate · Durum: kabul

## Bağlam

Stage 55 kapısı (2568bfc5) bir web testi dışında yeşildi: `apps/web/tests/approvals/proposal-shapes.test.ts`
"reads every file under team/proposals" ana kopyadaki iki izlenmeyen `team/proposals/2026-10-06-feed-*.md`
dosyasını okudu. `scripts/lib/TeamFeed.ps1` ConvertTo-TeamFeedTasks bu dosyaları `## Sahibe sorulan` ile
yazıyordu; kural tam bir "Ne" bölümü (Onay Merkezi'nin Detay görünümünün ilk gösterdiği bölüm) ister.
Kapı 80 dakika ve bir yayın; bir biçim farkı yüzünden kırmızı kalması pahalı.

## Karar

1. Biçim YAZANDA düzeltilir, test gevşetilmez. Sahip maddesi artık: `# başlık`, `Kaynak: ...` satırı,
   `## Ne` (sahibe sorulan cümle), `## Roadmap satırı`, `## Hedef`, `## Kabul`, `## Karar`
   (`Sahip: evet / hayır / ertele.`). Hedef/Kabul verilmemişse bölüm yine yazılır: `Belirtilmedi.`
   Böylece her dosyada 5 başlık, bir "Ne", 6 satır içi parça var; betik kendi `**`/bağlantı eklemez.
2. İki yarı birbirini okur: `apps/web/tests/approvals/fixtures.ts` FEED_SHAPE betiğin çıktısının birebir
   kopyası; `scripts/tests/team-feed.tests.ps1` o sabiti dosyadan okuyup betiğin aynı girdiyle yazdığı
   metinle satır satır karşılaştırır, vitest aynı sabiti "every file" kuralından geçirir. Eski biçim
   (OLD_FEED_SHAPE) kuraldan geçmemeye devam eder - kapının yakaladığı şey budur.
3. Kuyruktaki `proposal` yolu (`team/proposals/<tarih>-feed-<id>.md`) değişmez; `scripts/team/feed.ps1`
   değişmez. TeamFeed.ps1 UTF-8 BOM taşımaya devam eder (PS 5.1, Türkçe içerik).

## Lead'in bağlama satırı

Ana kopyadaki iki izlenmeyen dosya (`2026-10-06-feed-radicale-calendar-server.md`,
`2026-10-06-feed-iphone-app-apple-account.md`) yeni biçimde yeniden yazılır (`## Sahibe sorulan` -> `## Ne`,
eksik Hedef/Kabul için `Belirtilmedi.`, sona `## Karar` + `Sahip: evet / hayır / ertele.`) ya da silinir;
sonra proposal-shapes.test.ts ana kopyada koşulur. Kart bu dosyalara dokunmadı.

## Sonuçlar

Bir sonraki besleme koşusunun yazdığı dosya kapıyı kırmızıya çevirmez ve Detay görünümünde soruyu ilk
bölüm olarak gösterir. Biçim değiştirilecekse iki dosya birlikte değişir; biri unutulursa iki test de kırmızı.
