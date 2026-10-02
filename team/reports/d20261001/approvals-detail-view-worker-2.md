## Şu an üzerinde çalışılan
`approvals-detail-view` (inspector iadesi, döngü d20261001) · alan: `apps/web/app/core/approvals`, `apps/web/tests/approvals`, `team/plans/approvals-detail-view-adr.md` · makine: sahibin PC'si, worktree `worker-approvals-detail-view`. Durum: dört iade maddesi kapandı, commit atıldı ve dal push edildi, ağaç temiz.

## Rapor
**sha:** `c8012291e1f49d1e5259896e8fda9ab4e72270de` (önceki `270249b7`) · 7 dosya, hepsi alanın içinde (`proposalSections.ts`, `ProposalDetail.tsx`, `ApprovalsList.tsx`, `detail-view.test.tsx`, `fixtures.ts`, yeni `proposal-shapes.test.ts`, ADR metni).

**İade maddeleri**
1. **F1:** `Bugün: … / Bununla: …` tek satırda iki yarıya ayrılıyor (ayraç `/`, `→`, `->`, `=>`, `—`, `–`, `|` veya hiç; sarılmış satırda da). Yarım çift yalnız olan yarısını gösteriyor; boş "→ Bununla:" etiketi artık basılmıyor.
2. **F2:** `**Ad**:` satırı yalnız hiç `## ` başlığı olmayan öneride ve liste öğesi değilken bölüm kesiyor. `## Maliyet/risk` altındaki `- **Maliyet**:` ve `- **Karar**:` madde olarak kalıyor.
3. **F3:** Başlık ve bağlantı artık geri izlemeli regex yerine tek ileri taramayla bulunuyor. Test sonucu doğruluyor (100 KB `# `, 500 KB `[`, 500 KB `[a](x `); zaman aşımı yalnız takılma bekçisi.
4. **Gerçek biçim:** `proposal-shapes.test.ts`, `team/proposals/*.md` altındaki altı dosyanın hepsini okuyor ve bölüm adlarını dosyanın kendi `## ` satırlarıyla karşılaştırıyor. `REAL_SHAPE` sabiti de eklendi.
5. **F4'ten alınanlar:** numaralı liste `<ol>`; kod çitindeki `## ` metin; büyük harfli `KAZANMADIĞIMIZ:` okunuyor; çiftsiz Faydası bölümü "fayda örnekleri olmadan" cümlesini alıyor; panel kartta kendi satırını alıyor (`flex-basis:100%`).

**RED → GREEN** (PROVEN_AUTOMATED)
- **RED (eski kodda):** 11 başarısız, 26 geçti. Başlık testi 17,6 s sürüp "Test timed out in 5000ms" verdi; bağlantı testi yanlış bağlantı metniyle düştü.
- **GREEN:** approvals 37/37 (test süresi 87 ms); tüm web paketi 121 dosya, 2078/2078; `tsc --noEmit` 0; oxlint 0; `next build` 0.
- Gerçek dosya testi eski kodda da yeşildi: altı dosyanın hiçbirinde F2'yi tetikleyen madde yok. F2'nin RED'i `REAL_SHAPE` sabitinden geliyor.

**Mutasyonlar** (her biri yedek kopyadan geri yüklendi, sha256 önce/sonra aynı, sonda 45/45 yeşil; PROVEN_AUTOMATED)

| Mutasyon | Sonuç |
|---|---|
| Bağlantı şeması denetimi kaldırıldı (kart şartı) | RED, 4 başarısız |
| `decisions_open` yok sayıldı (kart şartı) | RED, 3 |
| `**Ad**:` `## ` belgesinde de kesiyor | RED, 2 |
| Madde imli kalın başlık kesiyor | RED, 1 |
| Tek satır çift bölünmüyor | RED, 2 |
| Boş "→ Bununla:" etiketi geri geldi | RED, 1 |
| Kod çiti yok sayıldı | RED, 2 |
| Başlık yine geri izlemeli regex ile | RED, 1 (zaman aşımı) |
| Bağlantı metni ilk `[`'den başlıyor | RED, 1 |
| Çiftsiz Faydası "örnekli" sayılıyor | RED, 1 |
| Numaralı liste madde imli basılıyor | RED, 2 |

**Yapmadıklarım**
- **NOT_RUN:** Detay tıklaması, klavye ile açma ve panelin tarayıcıdaki görünümü. `apps/web` içinde DOM kütüphanesi ya da tarayıcı aracı yok; `flex-basis` yalnız üretilen işaretlemede doğrulandı.
- **F4'ten bırakılanlar:** ters tırnaklar düz metin kalıyor, ikinci bir Faydası bölümü sıradan bölüm gibi gösteriliyor, iç içe listeler düz. ADR'de yazılı.
- **READY_FOR_OWNER:** bir fikirde Detay'ı açıp döngü koşarken onaylamak (kardeş API görevi `decisions_open` gönderince).

**Açık riskler**
- Gerçek önerilerin hiçbirinde hâlâ Faydası bölümü yok; çift yolu yalnız sabit metinlerle sınandı. Araştırmacı ilk gerçek bölümü yazdığında dosya testi her çiftin iki yarısını da ister.
- `team/proposals/` boşaltılır ya da taşınırsa dosya testi bilerek kırmızıya düşer.
- Bağlantı metni artık `](` öncesindeki SON `[`'den başlıyor (eskiden ilkinden); iç içe köşeli parantezli metinlerde görünüm değişir.
- Sunucu `decisions_open` göndermiyor; sunucu tarafını okuyan test kardeş görevde.
