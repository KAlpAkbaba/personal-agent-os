**Denetleyici raporu — model-policy-api @ `fab97da1`**

Kabul ölçütlerinin hepsi karşılanıyor ve işçinin NOT_RUN bıraktığı iki gerçek koşuyu ben yaptım; ikisi de geçti. Kod değiştirmedim, ağaç temiz, çalışan süreç yok, kendi scratch veritabanımı sildim.

**Pass 1 — koşturdum**
- **Birim:** `test_team_models_setting.py` 106 passed (iddia 106).
- **Takım paketleri:** `tests/unit/test_team_*.py` 445 passed, 1 failed. Tek hata bilinen `test_team_state.py` (aşağıda, lead'in).
- **Gerçek PostgreSQL 16.15, paketin kendi yoluyla:** dev stack'te ayrı bir veritabanı açtım, conftest'in `alembic upgrade head`'i (0063) ve advisory kilidiyle `test_team_models_postgres.py` + `test_team_state_postgres.py` 6 passed. `team_state` sütunları varchar(16)/varchar(80)/jsonb/varchar(32). İşçinin `--noconftest` NOT_RUN'ı kapandı.
- **İlk yazma yarışı (PostgreSQL):** 6 eşzamanlı `put_models`, 25 tur; hata 0, yırtık satır 0. `IntegrityError` dalını hiçbir test kapsamıyordu.
- **Gerçek sunucu + döngünün kendi istemcisi (PROVEN_PROXY):** bu ağacın uygulamasını uvicorn'da PostgreSQL üstünde açtım.
  - `cycle.ps1`'in `New-CycleStatus` / `Get-LimitsDocument` işlevleri ve `TeamQueue.ps1`'in istemcisiyle 7 durum biçimi yazdım (koşusuz, 3 koşu + 20 `lowered`, tüm modeller limitli, tek `lowered`, eski biçim): hepsi 200.
  - GET'i `Read-TeamModelSetting` kabul etti. 8 bozuk PUT'ta sunucunun 422 kodu döngünün koduyla birebir aynı.
  - Ofis: üç denetleyici koşusu + bir işçi → `running_agents` 4, denetleyici koltuğunda üç koşu, `running_model` yalnızca farklı olanda. Oturumsuz 401.
- **Mutasyon (benimkiler, işçininkilerden farklı):** 12'nin 11'i RED, hepsi yedekten geri yüklendi, sha256 önce = sonra. Üçü PostgreSQL'de de RED (satır değişmiyor, `by_alias` yok, `models=` verilmiyor).
- **Lint:** `ruff check .` (services/api) ve dokunulan dosyalarda format temiz.

**Pass 2 — bulgular**
1. **Test açığı:** durumdaki model kimliğinin 64 karakter sınırını gevşettim, 106 test yeşil kaldı. Sınır çalışıyor (HTTP'de 65 karakter → 422), ama hiçbir test tutmuyor.
2. **Main'de zaten var, bu dal açığa çıkardı:** durumun `updated_at`'i sınırsız metin. 46 karakter PostgreSQL'de 500 (`value too long for type character varying(32)`); SQLite ve dosya deposunda 200. Döngü 20 karakter yazdığı için bugün tetiklenmez, ama ADR-0214 ek 4'teki hatanın aynı biçimi. Bu dal o satırı değiştirmedi.
3. **`used_pct: 1e999` → 500, 422 değil.** Hiçbir şey yazılmıyor. Aynı sınıf main'de `estimated_usd` için de var; döngü tam sayı gönderiyor.
4. **`used_pct` aralıksız:** -5 ve 250000 kabul ediliyor.
5. **Ölü döngünün limitleri sayfada kalıyor:** iki gün eski, kilitsiz bir durumda `running` false, ama `limits` hâlâ "limited", %97 ve eski `lowered`. Sıfırlanma saati olmayan limit ve yüzde hiç eskimiyor. UI kartı `cycle.running` / `updated_at` ile "şu tarih itibarıyla" göstermeli.
6. **Entegrasyon fixture'ı** dev DB'deki gerçek `models`/`status` satırlarını silip geri koyuyor; koşu öldürülürse kaybolurlar (yalnızca dev).
7. Alan dışı dosya yok, sır veya yol yok, migration yok, KVKK verisi yok. Geri alma: commit'i geri almak yeter.

**Lead için, merge'de**
- `test_team_state.py`'deki iki düzenleme (ADR dosyasındaki metin) şart; geçici kopyada denedim: 52 passed.
- `test_qualification_evidence` bu dalda da kırmızı (1 failed, 4 passed): QUALIFICATION satırı 41.7 makinenin izleyebileceği bir kanıt adı vermiyor. Bu dalın farkı o dosyaya dokunmuyor; kapıdan önce düzeltilmeli.
- Küçük bir takip kartı öneririm: bulgu 1–4, hepsi `routes.py` + bir test.

**Kanıt sınıfları**
- PROVEN_AUTOMATED: birim + gerçek PostgreSQL.
- PROVEN_PROXY: gerçek sunucu ↔ döngünün kendi işlevleri.
- READY_FOR_OWNER: sahip Ofis'te model değiştirir, sonraki koşu onu kullanır (`model-policy-office-ui` gerekli).
- NOT_RUN: tam kapı ve tam birim paketi (entegrasyon dalında değil); sahte koşucuyla tam `cycle.ps1` süreci bu sunucuya karşı. Host snapshot kuralı uygulanmaz (`scripts/cloud`, `infra/docker`, migration yok).

APPROVE
