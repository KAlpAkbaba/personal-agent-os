**Onay Merkezi: Cloud Core yarısı hazır, kabuk sayfası yazıldı ama tarayıcıda açılmadı.**
Dal `team/pilot-01/worker-onay-merkezi`, sha `82711a29b23f118ff423fba0831399cfabb401c4`. Çalışma ağacı temiz, 7 dosya, hepsi alanın içinde. Bu oturumda testleri yeniden koşmadım. Sonuçlar önceki koşudan.

**Şu an üzerinde çalışılan:** `onay-merkezi` — app/team, test_team_approvals.py, apps/web/app/core/approvals — makine: bu PC.

**Dosyalar**
- `services/api/app/team/{__init__,approvals,routes}.py`
- `services/api/tests/unit/test_team_approvals.py`
- `apps/web/app/core/approvals/{approvalsApi.ts,page.tsx}`
- `team/plans/onay-merkezi-adr.md`

**Testler**
- **Önce RED:** yeni test dosyası toplama aşamasında düştü, çünkü `app.team` yoktu.
- **GREEN:** `test_team_approvals.py` 34/34 geçti. `test_team_queue_schema.py` ile birlikte 72 geçti.
- **Ruff:** check ve format temiz. Web sayfasında `tsc --noEmit` ve oxlint sessiz.
- **Kural / yakın kaçış çiftleri:**
  - `awaiting_owner` ve `awaiting_release` listelenir. `in_progress`, `done` ve `awaiting_real_evidence` listelenmez.
  - Kabuk kapıyı adlandırabilir. Yanlış kapıyı adlandırmak (eski sayfa) reddedilir.
  - Sesle onay şu durumlarda reddedilir: kapı yok, kapı olmayan bir kelime, kapıda iki görev, kapıda hiç görev yok, öbür kapının görevi.
  - 6 saatten yeni bir döngü kilidi karar vermeyi engeller. 6 saatten eski, tutulmayan veya olmayan kilit engellemez.
  - `team/` dışındaki bir öneri yolu hiç okunmaz.

**Mutasyon kanıtı**
- Sekiz kural için birer mutasyon RED verdi: ses kapısı zorunlu, belirsizlik, döngü kilidi, red gerekçesi, ledger önce yazılır, `team/` içinde kalma, bayat kapı, `awaiting_real_evidence`'in kapı sayılması.
- Her biri `approvals.py`'nin yedek kopyasından geri yüklendi, `git checkout --` kullanılmadı.
- **sha256 notu:** sha256 mutasyonlardan önce ve sonra aynıydı. Kanıttan sonra `ruff format` çalıştırdım. Geri yüklenen dosya biçimlemeden önceki dosyayla eşleşiyor, 72 test biçimlenmiş dosyada da yeşildi.

**Kanıt sınıfı**
- **PROVEN_AUTOMATED (API ve kuralları):**
  - Onayla `approved`, Reddet `stopped` yazar ve red gerekçesini tutar.
  - Yayın onayı yalnız diskteki `queue.json`'u değiştirir, yayın başlatmaz.
  - Her karar tam bir ledger olayıdır. Reddedilen karar ledger'a hiçbir şey yazmaz.
- **READY_FOR_OWNER:** kabuk sayfası. Ses yolunun yalnız API şekli var: `channel:"voice"` ve `gate`.
- **NOT_RUN:** sayfa tarayıcıda, ve route'lar gerçek `create_app` üzerinden (henüz eklenmedi).

**Birleştirmede lead için (ADR metninde ayrıntı)**
1. `app/ledger/vocabulary.py`'ye `team` alt sistemi ile `team.task.approved` ve `team.task.rejected` olay tiplerini ekle. Sabitler `app/team/approvals.py` içinde. Eklenene kadar her karar 503 `ledger_refused` ile reddedilir ve kuyruğa dokunulmaz. Bunu bir test kapsıyor.
2. `app/main.py`'ye `app.team.routes`'tan `include_router(team_router)` ekle. Cloud Core VM'de checkout olmadığı için `app.state.team_root` varsayılan repo-kökü `team/` yerine geçebilir.
3. Kabuk gezinmesine `/core/approvals` bağlantısını ekle.

**Açık riskler**
- **Yayın onayının hedef durumu:** kabul ölçütü Onayla'nın iki kapıyı da `approved`'a taşımasını söylüyor, ben öyle yaptım. Ama döngü `awaiting_release`'ten sonra gelen `approved`'ı "işçi ata" diye okuyor. Yayın için onaylanmış birleştirilmiş bir görev sonraki döngüde yeniden atanır. Bunun kararı sende, ADR'de açıkladım.
- **Web yolu:** kart `apps/web/src/app/core/approvals` diyor, ama repoda `src/` yok. `apps/web/app/core/approvals` kullandım.
- **Kuyruk biçimi:** yeniden yazım 2 boşluklu JSON, BOM'suz UTF-8 ve sondaki yeni satırla yapılıyor. Geçerli ve şemaya uygun, ama PowerShell yazıcısından biraz farklı biçimlenmiş.
- **Ortam:** çalışma ağacında venv yok. Testleri ana checkout'un `.venv` python'uyla koştum. `app.team` çalışma ağacından import edildi.
