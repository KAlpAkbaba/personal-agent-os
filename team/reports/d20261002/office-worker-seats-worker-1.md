## Şu an üzerinde çalışılan
`office-worker-seats` (d20261002) — Ofis: 4. çalışan koltuğu, koltuk başına `runs`, sayı koşuyu sayar. Alan: `services/api/app/team/office.py`, onun testi, `apps/web/app/core/office`, `apps/web/tests/office`, ADR metni. Makine: sahibin geliştirme PC'si, bu worktree. Durum: bitti, commit edildi, push edildi, ağaç temiz.

## Rapor
- **sha:** `db3d971a34f4b88327c859d982fab493c683d277`, dal `team/d20261002/worker-office-worker-seats` (origin aynı sha).
- **Dosyalar:** 11, hepsi alan içinde. `docs/HANDOFF.md`, `DECISIONS.md`, `BUILD_STATE.json` dokunulmadı.
- **ADR:** `team/plans/office-worker-seats-adr.md` (numarasız).

**API (`office_view`)** — PROVEN_AUTOMATED
- Çalışan koltukları `worker-1..N`, `N = max(4, canlı çalışan koşusu)`; sıra lead, researcher, integrator, çalışanlar, inspector, owner.
- Her koltukta `runs` var (`task_id`, `task_title`, `since`, başlangıç sırasıyla); koltuğun kendi alanları ilk koşunun.
- `running_agents` canlı koşu sayısı, `capacity = max(6, running_agents)`.
- RED→GREEN: yeni testlerle 21 kırmızı / 20 yeşil → 42 yeşil. Kabuldeki her madde bir test; ayrıca gerçek uygulama nesnesi üzerinden rota testi (file + db store, 5 çalışan + 2 denetim → 7/7).
- Diğer okuyucular: `test_team_*`, `test_office01_wiring`, `test_voice_intents_team_status` 353 yeşil. ruff check + format temiz.

**Sayfa** — PROVEN_AUTOMATED
- Sabit sekizlik liste kalktı: ad kimlik deseninden (`Çalışan <n>`); tanınmayan kimlik, başında kimse olmayan düz masa + kimliği.
- Birden çok koşulu koltuk `çalışıyor ×3` rozeti gösterir; paneli "Koşan işler (3)" listesini ilk işin kartı ve raporunun üstünde verir.
- RED→GREEN: kaynak HEAD'deyken 14 kırmızı / 40 yeşil → ofis testleri yeşil. Tüm web paketi 119 dosya / 2067 test yeşil, tsc 0, oxlint 0 (ofiste uyarı yok).
- Üst çubuğun `5/6` testi önceden de yeşildi: sayfa bu sayıları zaten API'den okuyordu, hata API'deydi. Artık testle kilitli.

**Mutasyonlar** — hepsi RED, her biri yedek kopyadan geri yüklendi, sha256 önce = sonra
- API (`office.py`, `29404fcb…`): `max(4, …)` → canlı sayı; `running_agents` → koltuk sayısı; sabit kapasite; rol başına yalnız ilk koşu; başlangıç sırası yerine depo sırası.
- Web: `officeModel.ts` (`0c6b656b…`, düzeltmeden sonra `6e15e066…`) 7 mutasyon, `OfficeScene.tsx` (`279ca719…`) 2, `OfficePanel.tsx` (`adf80079…`) 1.
- "`running_agents` koltuk sayar" mutasyonunu 4 çalışan + 1 denetim testi yakalamıyor (5 koltuk = 5 koşu); üç denetim testi yakalıyor.

**Yolda bulunan hata (düzeltildi, regresyon testi var)**
- Betikle yazarken regex'teki ters eğik çizgi düştü (`[1-9]d*`): `worker-10` tanınmıyordu, `worker-1d` tanınıyordu. Hiçbir test görmedi. Test eklendi (kırmızı), düzeltildi; bu hal mutasyon olarak da kırmızı.

**Yapamadıklarım / NOT_RUN**
- PROVEN_REAL: sahibin canlı döngüde dört masayı ve doğru sayıyı görmesi — NOT_RUN (yayın gerekir).
- Tarayıcıda gerçek çizim — NOT_RUN; kanıt statik HTML + CSS okuması.
- Tam API birim paketi (~5400 test) — NOT_RUN; yalnız ekip/ofis dosyaları koştu.

**Açık riskler (lead için)**
- `app/team/speech.py` (alan dışı) hâlâ çalışan koltukları sayıyor: üç denetim koşarken ses "altı kişiden bir çalışan" der. Ayrı kart gerekir.
- Dokuz koltuk mevcut ızgarada 4+4+1 dizilir, sahip üçüncü satırda tek kalır; ızgaraya dokunmadım.
- `capacity`, durum şeması yuva sayısı taşıyana kadar `max(6, koşu)`.
- `SeatId` artık `string`, `SEAT_NAME_TR` dışa açık değil (yerine `seatName()`); `model-policy-office-ui` buna göre yazılmalı.
- Çalışan koltuklarında üst sınır yok: durum 12 çalışan koşusu bildirirse 12 masa çizilir.
