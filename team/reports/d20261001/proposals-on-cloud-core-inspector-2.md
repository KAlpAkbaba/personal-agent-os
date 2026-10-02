## Denetleyici raporu — `proposals-on-cloud-core` @ `c7e00763`

**Karar: onay.** Kabul ölçütlerinin hepsi gerçek PostgreSQL'de kanıtlı, kartın iki asıl mutasyonu RED. Ağaç temiz, üç ürün dosyasının sha256'sı koşu öncesiyle aynı.

**Pass 1 — koşturdum**
- **Unit:** beş dosya (proposals, approvals_while_running, approvals, state, ratchet) 251 passed; queue_schema, office ve office01_wiring 71 passed.
- **PostgreSQL** (`pagentos-postgres`, dev stack): üç ekip entegrasyon dosyası 13 passed. Koşudan sonra test satırı kalmadı.
- **Lead ucuyla deneme birleştirmesi** (`db1ff2d9`): çakışma yok. Birleşik ağaçta ekip/ofis/ses dosyaları ve ratchet 338 passed, 2 failed. İki hata `test_team_queue_schema.py` içinde ve benim eksik çıkarımımdan (`FileNotFound`); bu iki test birleşik ağaçta NOT_RUN, dalda geçiyor.
- **`ruff check`** temiz.
- **NOT_RUN:** mypy (venv'de kurulu değil: `No module named mypy`), tam unit paketi ve `quality-gate.ps1` (ana kopyada tam kapı koşuyor; yan yana koşmadım). Lead entegrasyon dalında koşar.

**Mutasyonlar** (işçininkilerden farklı; yedekten geri alındı, her seferinde sha256 eşleşti)

| Mutasyon | Unit | PostgreSQL |
|---|---|---|
| FileStore reddi kaldırıldı (kartın mutasyonu) | RED | hayatta (dosya deposu kuralı) |
| Depodan okuma yerine dosya okuma (kartın mutasyonu) | RED (db) | RED |
| 80 karakter kuralı kaldırıldı | RED | RED |
| DbStore ikinci put eski metni bırakıyor | RED | RED |
| DbStore kilit altında kararı reddediyor | RED | RED |
| Depodaki 200 000 sınırı kaldırıldı | RED | hayatta (rotada pydantic tutuyor) |
| `decisions_open` hep true | RED | hayatta (dosya deposu kuralı) |

İşçinin bu turda koşmadığı iki kart mutasyonu böylece kapandı.

**Pass 2 — kırmayı denedim** (gerçek PostgreSQL, rota üzerinden)
- **Hatalı gövdeler:** 15 gövdenin hepsi 422, yazılan satır 0 (tür hataları, fazla alan, büyük harf, satır sonu, `/`, `\`, `../`, `nul.md`, `com1.x.md`, 81 karakter, 200 001 karakter, boş gövde).
- **Yarış:** aynı ada 12 iş parçacığıyla ilk put, 5 tur: hata yok, tek satır.
- **Metinler:** boş metin, CRLF, kontrol karakterleri, U+2028, BMP dışı ve yazı olarak `\u0000` aynen geri okundu. 200 000 adet 4 baytlık karakter (2,4 MB gövde) 200 ve tam.
- **Öncül doğru:** `Save-TeamQueueApi` yalnız değişen görevi, okuduğu `updated_at` ile yazıyor (`scripts/lib/TeamQueue.ps1:743`).
- **Dosya gösterilmiyor:** DbStore'da sunan makinedeki `team/proposals/` dosyası okunmuyor.

**Bulgular** (hiçbiri kabul ölçütünü bozmuyor; lead'e)
1. **Ofis yoklaması artık fikir metnini taşıyor.** `GET /v1/team/office` 5 sn'de bir çağrılıyor ve `routes.py:315` depoyu `list_pending`'e geçiriyor; sayfa `proposal_text`'i kullanmıyor. Ölçüm: 20 fikirde yoklama başına 408 561 karakter ve 20 satır okuması. Üç fikirle küçük kalır; düzeltme tek satır (ofis çağrısında metni okumamak).
2. **Yarım surrogate 500 veriyor** (doğruladım, hiçbir şey yazılmıyor). Hata işleyici alanın dışında; ayrı kart ister.
3. **İşçinin 4. maddesi bu dalda doğrulanamaz:** `tools_team.py` dalda yok, lead dalında var. Orada yalnız `team.status` için çağrılıyor ve metin gerekmiyor; depoyu geçirmemek 1. maddedeki maliyeti de getirmez. Yapılacak bir şey yok.
4. **Sahip için etki henüz yok:** web sayfası hâlâ `cycle_running` ile kilitli (`approvals-detail-view`). Üretimde bekleyen fikirlerin metni de `cycle.ps1` gönderene ya da lead bir kez gönderene kadar boş.
5. **`stale_write` defterde iz bırakıyor:** reddedilen karar için olay yazılmış oluyor (ADR'de açık yazılı; döngü tüm gün koştuğu için artık daha sık).

**Kanıt sınıfları**
- Depo, rota, onay listesi, döngü koşarken karar: PROVEN_AUTOMATED (gerçek PostgreSQL dahil).
- Ev PC'si kapalıyken fikir metnini okumak ve döngü koşarken onaylamak: READY_FOR_OWNER (yayın ve `approvals-detail-view` sonrası).
- mypy, tam unit paketi, tam kapı, birleşik ağaçta iki şema testi: NOT_RUN.

`APPROVE`
