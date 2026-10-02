## Şu an üzerinde çalışılan
- Görev: `misheard-ledger-store` (cycle d20261002), yalnız kırmızı testler yazıldı; kartın kendi kuralıyla **durdum**, uygulama yok.
- Alan: `services/api/app/voice/misheard/*`, migration, `alembic/env.py`, `app/main.py`, üç test dosyası, ADR.
- Makine: ev PC'si, worktree `.claude/worktrees/team/d20261002/worker-misheard-ledger-store`.

## Neden durdum
```
ALAN_ISTEGI: [services/api/alembic/versions/20261002_0065_misheard_utterances.py]
```
- `integrate/d20261002` (1b08509a) ve `gate/d20261002-2` üzerinde `20261001_0064_memory_vocabulary_class.py` var (revision `0064_memory_vocabulary_class`, revises `0063_team_state`).
- Kartın adlandırdığı `20261002_0064_misheard_utterances.py`, `0063`'ü revize ederse birleştirmede ikinci bir alembic head olur.
- Doğrusu: `revision = 0065_misheard_utterances`, `down_revision = 0064_memory_vocabulary_class`. Bu dosya adı alanımda değil; sessizce yeniden adlandırmadım.
- **İkinci engel:** dalımın tabanı `0ec2b308`; `0064_memory_vocabulary_class` bu dalda yok (main ve `team/nightly/lead` üzerinde de yok). Alan genişlese bile `alembic upgrade head` ve entegrasyon testi bu tabanda koşamaz. Dalın `integrate/d20261002` üzerine alınması gerekiyor.

## Yapılan
- sha: `dd036e5c45215f1e13efdc93c7061e5ffc2029cd` (push edildi, origin ile aynı; worktree temiz).
- Değişen dosya: 2, ikisi de alanın içinde:
  - `services/api/tests/unit/test_misheard_store.py`: 26 test fonksiyonu (dört neden, listen_only, bilinmeyen neden/kip, boş cümle, 2500→2000, idempotans, `is_request`, hold/held, 30/31. gün, süresi geçmişi listelememe, answer, forget, purge iki kez, sözsüz log).
  - `services/api/tests/unit/test_misheard_routes.py`: 9 test fonksiyonu, gerçek `create_app` üzerinden (dört çağrı, 401, 422 Türkçe, 404, `open`).

## Kanıt
- **RED (PROVEN_AUTOMATED):** iki dosya da toplamada düşüyor, `ModuleNotFoundError: No module named 'app.voice.misheard'`; `2 errors in 7.15s`, exit=2.
- `ruff format --check`: 2 dosya temiz.
- `ruff check`: 2 × I001 (import sırası). Paket henüz olmadığı için ruff onu üçüncü taraf sayıyor; paket yazılınca düzeleceğini doğrulamadım.

## NOT_RUN (kart durdurduğu için)
- GREEN: testler hiç yeşil koşmadı. Varsaydıkları imzalar doğrulanmadı (`record(db, *, sentence, mode, reason, session_id, heard_at, now, listen_only, ...)`, `HeldSentence`, `HOLD_TTL_SECONDS`, `RETENTION_DAYS`); devam eden iş bunları düzeltebilir.
- `models.py`, `service.py`, `routes.py`, migration, `env.py` kaydı, `main.py` router ve lifespan süpürmesi yazılmadı.
- `tests/integration/test_misheard_postgres.py` yazılmadı (revizyon adına bağlı).
- Üç mutasyon RED, tam birim paketi, entegrasyon paketi, coverage ratchet, host snapshot ve migration-model agreement testleri koşmadı.
- ADR (`team/plans/misheard-ledger-store-adr.md`) yazılmadı.

## Açık riskler / lead için
- Kartı yeniden vermeden önce alan satırındaki migration adını `0065` yapın ve dalı `integrate/d20261002` üzerine alın. Dört misheard-* kartında migration adı geçiyorsa aynı düzeltme gerekir.
- Rotalar testi `Settings(_env_file=None)` + `ArtifactRuntime` deseninde (`test_allowlist_editor.py` ile aynı); router'ı test eklemiyor, `create_app` kaydetmezse 404 ile kırmızı kalır.
- Yayın istisnası değişmedi: bu kart migration taşıyacak, yayını sahibe sorulur (ADR-0214 ek 9).
