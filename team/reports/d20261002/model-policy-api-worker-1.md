## Şu an üzerinde çalışılan
`model-policy-api` — model politikasının Cloud Core yarısı; alan `services/api/app/team/` + iki test dosyası + ADR metni; makine: ev PC'si, worktree `worker-model-policy-api`. Bitti, dal itildi, worktree temiz.

**sha:** `fab97da18debe230096578fe482b679f0a4bbbbb` (kod `940d754c`, ardından ADR), dal `team/d20261002/worker-model-policy-api`.
**Dosyalar:** 7, hepsi alan içinde: `models_setting.py` (yeni), `store.py`, `routes.py`, `office.py`, `test_team_models_setting.py`, `test_team_models_postgres.py`, `team/plans/model-policy-api-adr.md`.

**Bu dalda bir test KIRMIZI, alan dışında:** `tests/unit/test_team_state.py::test_every_route_and_body_field_the_powershell_client_uses_is_one_the_server_has` — "the server serves it now - remove the entry". Döngü `GET /v1/team/queue/models`'i zaten çağırıyor; rota artık var, test bunu unutturmamak için böyle yazılmış. Dosya alanımda olmadığı için dokunmadım.

**Yapılan**
- Ayar tek belge: FileStore `team/models.json`, DbStore `team_state` içinde `kind='models'`, `key='models'` satırı; yeni tablo ve migration yok.
- `GET /v1/team/queue/models` tam üç anahtar döner; kayıt yoksa varsayılanlar. `PUT` `updated_at`'i kendi damgalar; 422 kodları döngününkilerle aynı (`unknown_key`, `unknown_role`, `unknown_model`, `missing_role`, `invalid`, `inspector_weaker_than_worker`).
- Durum rotası `runs[].model` ve `limits` kabul eder, başka her şey hâlâ 422; eski biçim kabul edilir ve gönderildiği gibi saklanır.
- `office_view`: her koltukta `model`, yalnızca farklıysa `running_model`, koşu girdisinde `model`, `cycle.limits`, `models`. Koşu sayısı ve `runs` listesine dokunmadım.

**RED→GREEN** (PROVEN_AUTOMATED)
- Birim, uygulamadan önce: `63 failed, 43 passed`; sonra `106 passed`. Önceden geçen 43'ün bir kısmı boş geçiyordu (herhangi bir `limits` zaten 422'ydi).
- Gerçek PostgreSQL 16.15 (dev stack): `6 passed` (yeni 3 + mevcut `test_team_state_postgres` 3). Her satır modelden okunan sütun genişliklerinin içinde; DB'de `team_state` = varchar(16)/varchar(80)/jsonb/varchar(32).
- Üç denetleyici koşusu → `running_agents` 3 ve üç görev listeli: GREEN. Kartın "önce 1" hali `office-worker-seats` ile main'de zaten düzelmişti; bu dalda RED görmedim.

**Mutasyon** (yedekten geri yükleme, sha256 önce = sonra, yedisinde de `restored=yes`)
- M1 varsayılan worker fable → 7 failed.
- M2 bilinmeyen model denetimi kaldırıldı → 12 failed.
- M3 denetleyici-zayıf kuralı kaldırıldı → 9 failed.
- M4 depo denetlemiyor → 10 failed.
- M5 `exclude_unset` yok → 2 failed.
- M6 `used_pct` her şeyi alır → 4 failed.
- M7 `running_model` her koltukta → 4 failed.

**Hızlı denetimler:** dokunulan 6 dosyada ruff format + check temiz. 52 koruma dosyası + tüm takım testleri: `2154 passed, 2 failed`. Biri yukarıdaki `test_team_state.py`. Diğeri `test_qualification_evidence` (QUALIFICATION satırı 41.7); taban commit'te koşmadım, hata metni bu dalın dokunmadığı bir doküman satırını gösteriyor.

**Yapamadığım / NOT_RUN**
- Entegrasyon paketinin `alembic upgrade head` fixture'ı bu daldan çalışmıyor: dev DB `0064`'te (kapı dalının), bu ağaç 0063'te bitiyor. Postgres testlerini `--noconftest` ile, paketin advisory kilidini geçici bir eklentiyle tutarak koştum; ortak DB'ye downgrade yapmadım. Paketin kendi yoluyla koşu: NOT_RUN.
- Tam birim paketi ve tam kapı: NOT_RUN.
- PROVEN_REAL (sahip Ofis'te model değiştirir, sonraki koşu onu kullanır): NOT_RUN, `model-policy-office-ui` gerekiyor.
- Gerçek `cycle.ps1` ile bu sunucu arasında uçtan uca koşu: NOT_RUN. Yerine bir test `TeamQueue.ps1` kaynağını okuyup zincir, roller, varsayılanlar ve kodları karşılaştırıyor.

**For the lead at merge**
- `test_team_state.py`: `called_ahead` bloğunu `called_ahead: dict[tuple[str, str], str] = {}` yap; `read_by_others`'a `("PUT", "/v1/team/queue/models")` girdisini Ofis sayfası okuyucusuyla ekle. Geçici kopyada denendi: `1 passed`. Tam metin ADR dosyasında.
- `tools_team.py` `office_view`'ı `models=` vermeden çağırıyor; varsayılanları alır, yeni anahtarları kullanmıyor.
- Yayın sırası: bu, `model-policy-office-ui`'dan önce.

**Açık riskler / kart ötesi kararlar** (ADR'de 2, 4, 5, 6)
- Sıfırlanma saati geçmiş bir limit sayfada `ok` ve yüzde `null` görünür. Kartın "aynen geçir" sözünün bir adım ötesi: dört satır, bir test.
- Durumdaki model kimliği üç kimliğe kilitli değil, en çok 64 karakterlik metin: araç başka modelde koşmuş olabilir ve reddedilen bir durum Ofis'in model/limit gösterimini o döngü boyunca siler. Ayar ise üç kimliğe kilitli.
- Koşu girdisinde `model` yalnızca durum bildirdiyse var, `null` yazılmaz. `test_team_office.py`'deki mevcut biçim testleri bu yüzden yeşil kaldı; UI kartı bunu bilmeli.
- Elle bozulmuş bir `team/models.json` GET'te varsayılanlar olarak döner, hata vermez.
