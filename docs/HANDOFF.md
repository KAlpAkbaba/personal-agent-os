# Devir notu — canlı (son güncelleme 2026-09-21)

Sahip iki Claude hesabını dönüşümlü kullanır (token bitince diğerine geçer). Yeni oturum,
hangi hesap olursa olsun, buradan devam eder: `.claude/hooks/session-start.ps1` aşağıdaki
işaretli bloğu, `git status`'u ve son commitleri oturum açılır açılmaz bağlama koyar.
Önce `CLAUDE.md`, sonra bu dosya, gerektikçe `docs/DECISIONS.md` sonundaki ADR'ler.
Sahibe Türkçe yaz. Geçiş tarifi (sahip için): `docs/HESAP_GECISI.md`.

**Bu dosyanın canlı kalma kuralı (her iki hesap için bağlayıcı):**
1. Bir işe başlarken, ilk kod değişikliğinden ÖNCE "Şu an üzerinde çalışılan" bölümünü yaz.
2. Her commit'te bu bölümü ve gerekiyorsa "Şu anki durum"u güncelle (aynı commit'e koy).
3. İş bitince bölümü "Yok" yap, işi "Sıradaki işler"den düş.
Token ortada biterse bir sonraki oturum kaldığı yeri buradan ve `git diff`'ten bulur.

<!-- session-start:begin -->
## Şu an üzerinde çalışılan

**Sahibin 2 notu (2026-09-21, sesle verildi):**

1. *Tekrar sayısı.* "Yukarı tuşuna 5 kere bas" / "5 defa yap" kabul edilmiyor; sahip her
   basışı ayrı söylemek zorunda. Yapılan: `intents.py`'de `spoken_repeat()` ("N kere/defa/
   kez/sefer", sözcük ya da rakam), `ResolvedIntent.repeat_count`, tur kaydına kopya;
   `plans.press_key/press_shortcut/pointer` N adımlı plan (tek activate); `operator.key` ve
   `operator.pointer` (kaydırma) sayıyı uygular ve konuşmada söyler. ADR-0195.
2. *Hareket kaydı ("makro").* "Yeni hareket oluştur/başlat" → sonraki komutlar hem yapılır
   hem kaydedilir → "hareketi bitir/tamamla" → ad sorulur → ad söylenir → kaydedilir;
   sonra "<ad> aç" o adımları tarifsiz tekrarlar. Yapılan: `app/macros/` paketi
   (`voice_macros` tablosu, alembic 0061, ad eşleme), `tools_macros.py`
   (macro.record_start / record_end / cancel / name / run / list / delete), yönlendiricide
   MACRO_* niyetleri (kayıtlı adlar ve "ad bekleniyor" durumu oturumdan enjekte edilir),
   `handle_tool_call` içinde adım yakalama, `macro.run` sunucu tarafında sıralı yeniden
   oynatma (adım başına step-up denetimi). ADR-0196.

**Durum (2026-09-21 akşam):** kod + testler yeşil (yeni 3 dosya 91 test; korpus `macro.*` +
`*.repeat.*` 45; dokunulan paketlerde 772; migrasyon 74; ruff temiz). Bağımsız güvenlik ve
test incelemesi alındı, commit atıldı. Kalan: bulut yayını (`release-cloud-core.ps1
-BlueGreen`, alembic 0061 yayın içinde koşar) + sahibin canlı denemesi.

**Sahibin deneyeceği cümleler (yerel mod, bir pencere odaktayken):** "Yukarı tuşuna 5 kere
bas" · "Üç kere aşağı kaydır" · "Kontrol Z'ye iki kere bas" · "Yeni hareket oluştur" →
birkaç komut → "Hareketi bitir" → (ad sorulur) "Yeni mail sekmesi" → "Yeni mail sekmesi aç"
· "Hangi hareketlerim var" · "Yeni mail sekmesi hareketini sil".

Sahip bu 2 nottan sonra "2 yeni ekleme" daha verecek; gelince buraya ekle.

## Şu anki durum

- **Üretim:** Cloud Core `6d9681a` (api-green), `pagentos-bluegreen-reconcile.timer` aktif.
  Recovery bundle pini `6d9681abf66891987664215fbc7c339afbc5f55f` için sahipten
  istendi; yapılıp yapılmadığını sunucudan oku (okuma serbest, uzak yazma sahibin işi).
  Realtime sağlayıcıları: `local-router`, `openai-realtime`.
- **Cihaz (sahibin PC'si, "MAIL"):** ajan `0.6.0`; tuşlar, sekmeler, kamera, sahibin
  Chrome'unda araştırma (CDP 127.0.0.1:19222, `-AuthorizeResearch`) kurulu ve canlı denendi.
- **Hafıza:** 2 temizlikten sonra ~452 satır; ilk öğrenilmiş tercih durable; hafıza bloğu
  hem ücretli oturumda hem yerel moddaki serbest sohbette (ADR-0183…0193).
- **CI yok:** GitHub Actions kapalı (sahip ödeyemiyor). Kanıt yereldir; sahip 2026-09-19'da
  "her seferinde tüm testleri koşma" dedi → dokunulan paketler + hedefli korpus yeter.

## Sıradaki işler

1. **Sahibin "2 not + 2 yeni ekleme"si** — hafıza bitince vereceğini söyledi (2026-09-21).
2. *Tarifle tıklama.* "Şu kameralı videoyu aç", "Kratos'un olduğu videoyu aç" bugün
   çalışmaz: `vision.LOCATE_QUESTION_TR` bir ADA göre soruyor ("X adlı düğme ya da öğe
   nerede?"), tarif değil; üstelik bulunamayınca `search_if_missing` YouTube'da o kelimeyi
   aratıyor. Yapılacak: planlayıcı ad/tarif ayrımı yapsın, tarif için ayrı bir görsel soru
   kurulsun, tarif bulunamazsa arama yapılmasın. Yazıyla bulunabilen hedefler yine ücretsiz
   yerel OCR'da kalsın (ücretli görsel çağrı yalnız tarif için).
3. Bildirim "hiçbir kanal taşımadı" (başarısız iş bildirimi hiçbir kanaldan gitmedi).
4. Sözcü sayfalarında alıntıya sayfa çerçevesi satırı karışıyor.
5. `scripts/verify-device-service.ps1` 6b.4: DateTime taşması (ayrı iş çipi açıldı).
6. Test defteri (claude.ai artefaktı) eski hesaba ait; gerekirse `default_registry()`'den
   yeniden üret.
<!-- session-start:end -->

## Nasıl yayınlanır / kurulur

- Bulut: `scripts/cloud/release-cloud-core.ps1 -BlueGreen` (asla `2>&1` ile değil; kirli
  ağaçta `-AllowDirty` yalnız HEAD'i gönderir). Sonra sunucuda
  `bash /opt/pagentos/app/scripts/cloud/install-recovery-supervisor.sh <TAM 40 haneli sha>`
  (kısa sha reddedilir). SSH: `root@100.90.158.26` (Tailscale).
- Cihaz: yalnız sahip, tek UAC —
  `Start-Process powershell -Verb RunAs -Wait -ArgumentList '-NoProfile -ExecutionPolicy Bypass -NoExit -File "E:\AI\PersonalAgentOS_Claude_Autonomous_Build_Package_v1\scripts\install-device-service.ps1" -DisplayPower -Operator'`
  (iki anahtar da zorunlu). Önce `devices/windows-agent` Release derle +
  `scripts/qualify-staged-update.ps1 -Quiet`.
- Üretim kayıtlarını okumak: `docker exec -i pagentos-prod-postgres psql -U pagentos -d pagentos_prod`
  (tablolar: `operator_missions`, `device_commands`, `realtime_tool_calls`, `research_runs`,
  `research_evidence`, `devices`).

## Bugün yayınlananlar (sahip henüz CANLI denemedi — işaretli olanlar)

| Commit | Ne | Canlı denendi mi |
|---|---|---|
| 42ce5a9 | Görev heartbeat'i — "YouTube'u aç" yeniden çalışıyor | ✅ |
| 961952f | "Arama kısmına X yaz" = açık sekmenin sitesinde arama | kısmen |
| 8596048 | /core düzen (canlı göstergesi, kırpılmayan kartlar, 4K yazı) | — |
| 7fa04ea | Ücretsiz yerel mod (Chrome Web Speech + yönlendirici + tarayıcı sesi) | ✅ |
| 67fff03, b4f8ff9 | Cihaz: ekran yakalama çerçeveye sığar; `screen.ocr` + JPEG | ✅ |
| 6615ad4, 27e2067 | Video adı yerel OCR ile bulunur, tüm Chrome pencereleri, doğru monitör; yanlış video açılırsa geri döner | ✅ ("Adana Adliyesi 4" açıldı) / geri dönme denenmedi |
| f53527e | "Videoyu durdur/başlat/başa al", "X sekmesine geç", "0 tuşuna bas" | ❌ (tuşlar cihaz kurulumu bekliyor) |
| c968d04 | Araştırma sayfaları sahibin Chrome'unda, OCR ile (ADR-0177) | ❌ başarısız oldu → 2336300 |
| 60060b2 | Araştırma: emekli model kimliği + sağlayıcı yedeği | ❌ |
| 52fd71b | Yerel modda serbest sohbet = Claude Haiku (`claude-haiku-4-5`) | ❌ |
| 85b5432 | Chrome'a yazma OCR ile doğrulanır | ❌ |
| 2336300 | Araştırma: Türkçe kaynaklar önce, OCR paragraf birleştirme (ADR-0178) | ❌ |

## Sahibin kararları (bugün)

- Yerel mod: STT = Chrome Web Speech; komutlar yönlendiricide; serbest sohbet = Claude Haiku.
- Araştırma: "Her şeyi kendi Chrome'umda, gözümün önünde yapsın" (ADR-0177).
- Ekranda bulma: önce yerel OCR (ücretsiz), görsel sağlayıcı yalnız konum/oynatıcı için.
- İkinci sesli onay yok (mail gönderme hariç) — ADR-0171 ek.
- "Birden fazla pencere varsa ikisine de baksın."

## 2026-09-20 gecesi yayınlananlar (sahip henüz CANLI denemedi)

`42eb997` yorum cümlesi tıklama sanılmıyor · `c71fc46` başarısız araştırma raporunu okuyor +
arama bölgesi işçide uygulanıyor (ADR-0179) · `4553004` ok tuşları, yarım kalan sıra sayısı,
"X hariç tüm sekmeleri kapat" (ADR-0180) · `9989f57` bildirim sayacı korunan sekmeyi
kaybettirmiyor · `d040659` yerel modda sesle kamera (ADR-0181).

Doğrulanacak cümleler: "kamerayı aç/kapat" (yerel mod, Chrome izin soracak), "sağ/sol tuşuna
bas", "YouTube hariç tüm sekmeleri kapat", "birinci sekmeye geç", "yanlış yere tıkladım"
(görev başlatmamalı), "yapay zeka haberlerini araştır".

## 2026-09-20/21: araştırma ve hafıza (ADR-0183…0193)

- Araştırma sahibin kendi Chrome'unda, Google'da, sekmelerde, DOM'dan okunarak çalışıyor
  (enroll-owner-chrome.ps1 -AuthorizeResearch yapıldı). "araştırmayı oku" haberin kendisini
  okuyor; yabancı kaynaklar Türkçeye çevriliyor.
- Hafıza: 955 nabız satırı sahibin onayıyla silindi (ADR-0190). Makine kayıtları artık
  hafızaya yazılmıyor (ADR-0193). İlk öğrenilmiş tercih üretimde durable:
  "Sahip 'yapay zeka' konusunu düzenli olarak araştırıyor". "bunu hatırla" ve
  "… hakkında ne biliyorsun" yerel modda da çalışıyor (ADR-0192).
- ADR-0193 kapsamındaki eski makine-kaydı satırları da sahibin onayıyla silindi
  (2026-09-21, 935 satır). İki temizlikte toplam 1890 satır; ledger'da hepsinin aslı duruyor.

## Bilinen tuzaklar

- `services/api/app/operator/plans.py` CRLF; yama betikleri CRLF-duyarlı olmalı.
- Bash heredoc'larda ters eğik çizgi bozulur → dosya araçlarını kullan.
- Mutasyon kanıtında dosyayı yedekten sha256 ile geri yükle, asla `git checkout --`.
- Tam birim paketi ~25 dk, tam korpus ~12 dk; aynı anda kalite kapısıyla koşma
  (`test_contract_falsification` canlı bir dosyayı geçici siler).
- Alt ajanlar worktree'de izole çalışır; sonuçları yama olarak ana ağaca uygula,
  `docs/DECISIONS.md` çakışırsa ADR metnini sona ekle.
