## Şu an üzerinde çalışılan
`execution-call-site-research` (iade sonrası 2. tur) — alan: `services/api/app/{execution/wiring,research/service,research/target}.py` + iki test dosyası + ADR metni; makine: sahibin geliştirme PC'si, worktree `worker-execution-call-site-research`.

**Sha:** `15da3087717c7fcbfbae1d11b884b11b15c447c8` (push edildi, uzak dal aynı sha, ağaç temiz). 6 dosya değişti, hepsi alan içinde; `browser_activities.py` bu turda değişmedi.

**İade maddeleri**
1. **Gerçek hello ile bulut:** `wiring.choose` artık işin gönderdiği operasyonları (`capabilities`) ve çağıranın kayıt görüntüsünü (`views`) alıyor. Bir hedef yalnızca çevrimiçi, her operasyonu ilan eden ve politikaca izinli bir cihazı varsa "uygun" sayılıyor. Araştırma, gateway'in gönderdiği beş operasyonu geçiriyor; bulut işçisi beşini de ilan ettiği için gerçek listesiyle **seçiliyor** ve koşu PLANNED oluyor.
2. **Sunamayan hedef atlanıyor:** bulut ya da `owner_chrome` makinesi yetenek eksikse veya politika reddediyorsa zincirde sıradakine düşülüyor, fallback satırı nedenini söylüyor (`cloud_capability_missing`, `owner_chrome_policy_denied`, `device_capability_missing` …). FAILED koşunun altında `execution.selected` satırı kalmıyor (5 ret biçimiyle test edildi). Kural tablosu ve `select_device` değişmedi.
3. **Entegrasyon temizliği:** her ledger yazımı yazıldığı anda `source_ref` ile not ediliyor, görevler testin kendi niyet metniyle bulunuyor. Ayrı bir test, `research_job_id` taşımayan satırın da silindiğini kanıtlıyor.

**Testler (RED→GREEN)**
- Birim `test_execution_call_site_research.py`: uygulamadan önce **17 kırmızı / 20 yeşil**, sonra **37 yeşil**. Bulut fixture'ı artık `services/browser/browser_agent/policy.py`'den okunan liste.
- Entegrasyon (dev-stack PostgreSQL, `pagentos-postgres`): **5 yeşil**, taze oturumdan geri okundu. Yeni iki test uygulamadan önce koşturulmadı; kırmızıları M3/M6/M8 mutasyonlarıyla gösterildi.
- Komşu birim takımları (`test_execution_*`, `test_research_*`, `test_voice_research_*`, `test_news_routes`, `test_devices_selection`, `test_pilot02_wiring`, `test_cloud_device_registry`): **1002 yeşil**.
- `ruff check` ve `ruff format --check`: temiz (6 dosya).

**Mutasyonlar** — hepsi KIRMIZI, yedek kopyadan geri yüklendi, sha256 önce/sonra aynı:

| Mutasyon | Birim | Entegrasyon |
|---|---|---|
| M1 `device_for` platforma bakmadan ilk çevrimiçi görünüm (kart) | 4 | 1 |
| M2 `_attached` platformu yok sayıyor (kart) | 3 | 0 |
| M3 uygunluk yalnız çevrimiçiliğe bakıyor | 7 | 1 |
| M4 atlama nedeni kesinleştirilmiyor | 10 | 1 |
| M5 servis buluta yine `browser.chrome` soruyor | 4 | 1 |
| M6 araştırma kurala operasyon listesi vermiyor | 8 | 1 |
| M7 adsız durumda `device` yalnız çevrimiçiliğe bakıyor | 2 | 0 |
| M8 `research_job_id` geçirilmiyor | 1 | 5 |

Her mutasyonun öncesinde ve sonrasında dev veritabanında `source='execution'` satır sayısı 0 — M8 dahil.

**Kanıt sınıfları**
- Kural çağrı yeri, sunamayan hedefin atlanması, kesin nedenler, PLANNED/ledger alanları, `_attached`: PROVEN_AUTOMATED (birim + dev-stack PostgreSQL).
- Bulut işçisinin bir araştırmayı gerçekten tamamlaması: NOT_RUN (kart yoklamayı yasaklıyor).
- Üretimde `execution_target=cloud` yazan PLANNED olayı: NOT_RUN.
- Tam birim takımı ve `quality-gate.ps1`: NOT_RUN.

**Açık riskler / lead için**
- Yayınlanınca adsız her araştırma, çevrimiçi olduğu sürece buluta gider; ilk üretim koşusu gerçek kanıt olacak. Üretimdeki `bulut` satırının `capabilities_json` değerini okumadım.
- REST `target_device="bulut"` hâlâ `browser.chrome` isteyen eski yoldan geçiyor; gerçek bulut işçisi için reddedilir (FAILED, execution satırı yok). Kural o yolda sorulmuyor; dokunmadım.
- "bulutta" sesten hâlâ gelmiyor (`devices/aliases.py`, alan dışı) — ayrı kart.
- `wiring.choose` iki yeni isteğe bağlı parametre aldı; `__all__` değişmedi. `test_pilot02_wiring.py` yeşil; `device_for` oraya eklenecekse hâlâ lead'in işi.
- ADR metni `team/plans/execution-call-site-research-adr.md` içinde ek olarak yazıldı (kararlar 9–14).
