# Öneri: Gerçek sunucunun biçimiyle prova — "sahte ev sahibinde yeşil, gerçekte kırmızı" kusurunu kapıda yakala

Tarih: 2026-10-01 · Araştırmacı · Durum: awaiting_owner
Roadmap: doğrudan bir JARVIS satırı değil; "Repairs and improves itself" (HAVE, PROVEN_REAL şartı) ve takımın
döngü güvenilirliğine hizmet eder. Öğrenilen ders türünden öneri.

## Ne
Sunucuya (Cloud Core) dokunan betikler/şemalar için kapıya bir **"gerçek-biçim provası"**: denetleyici, değişiklik
birleşmeden önce betiği gerçek hosta karşı yalnız-OKUMA kipinde (`--preflight`, `GET`) koşar ve host'tan alınmış
GERÇEK durum anlık görüntüsünü (çalışan renk, kilit tutma süresi, sütun uzunlukları) test sahte-host'unun girdisi yapar.
Sahibin cümlesi yok; sahip yalnız "gece penceresi sorunsuz geçti" görür.

## Neden şimdi (aynı türden iki+ olay, bugün)
1. QUALIFICATION 38.12: bakım betiği `api-blue`'yu adıyla bekledi, GREEN servis ediyordu; sahte-host testi yalnız
   blue'yu modelliyordu. Gerçek hostta yayından SONRA bulundu.
2. QUALIFICATION 38.17: preflight işlem kilidini bir kez sordu; dakikalık reconcile kilidi saniyelerce tutuyor;
   kırk denemede biri "held" dedi — gerçek hostta bulundu.
3. QUALIFICATION 38.15: kilit sürümü `varchar(32)`'ye sığmadı; SQLite yeşil, PostgreSQL 500. (Bu sınıf için sahibin
   yeni kuralı ve 38.16 testi var — kuralı betiklere genişletmek bu öneri.)
Üçü de: sahte, gerçeğin bir özelliğini (renk, zamanlama, sütun uzunluğu) modellemiyordu. Hafızadaki "local dev stack
masks CI" / "two clocks" derslerinin devamı.

## Nasıl
- Seam: `scripts/tests/maintenance-reboot.tests.ps1` (28 test) + denetleyici rolü + `scripts/core/` betikleri.
- Değişen: (a) host'tan salt-okuma anlık görüntü alan küçük betik (`docker ps`, `flock` süresi örnekleri, şema sütun
  uzunlukları) → `scripts/tests/fixtures/host-snapshot.json`; sahte-host onu okur. (b) Denetleyici kontrol listesine
  "host'a dokunan değişiklikte `--preflight` gerçek hostta 10 kez koşturulur"; sonuç rapora. Değişmeyen: yayın
  akışı, sahip onayı, hiçbir yazma.
- Önce kapsam: yalnız betik/şema değişen işler; kod-only işler etkilenmez.

## Maliyet/risk
Efor: küçük. Çalışma: ihmal edilebilir (birkaç SSH salt-okuma). Risk: sahibin "uzaktan yazma yok" kuralı korunur —
yalnız okuma; Tailscale SSH denetimi sahibin elinde, denetleyici erişemezse bu adım `NOT_RUN` olur ve birleştirme
durur (yeni kural 1 ile uyumlu). Gizlilik: anlık görüntü sır içermez (renk, sayı, uzunluk).

## Kanıt planı
PROVEN_AUTOMATED: 38.12'nin eski hatası fixture'a "GREEN servis ediyor" konunca sahte-host'ta KIRMIZI olur (geri
alınmış düzeltmeyle). PROVEN_REAL: bir sonraki bakım penceresi (38.13) ek düzeltme gerektirmeden geçer.

## Karar
Yapalım mı? Alternatif: (a) yalnız checklist maddesi, betik yok (en ucuz, daha zayıf); (b) bugünkü gibi bırak,
hostta bul — her seferinde bir yayın sonrası düzeltme.
