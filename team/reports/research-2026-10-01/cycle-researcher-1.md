Üç öneri yazdım, hepsi sahibin onayını bekliyor (`awaiting_owner`). Kod, ROADMAP ve kuyruğa dokunmadım.

**Yazılan dosyalar**
1. `team/proposals/2026-10-01-ev-home-assistant.md`
   - **Satır:** JARVIS tablosunda "evi yöneten", sıra 4 (MISSING).
   - **Öneri:** Home Assistant'ı `smart_home` sağlayıcısı olarak bağlamak. Önce yalnız okuma, sonra yazma. Kilit, kapı ve alarm sahibin sözüne bağlı kalır.
   - **Kanıt:** Home Assistant'ın MCP sunucusu 2025.2'den beri var. Varlık beyaz listesi Home Assistant tarafında. Belge, kilit ve kapı için güvenlik kısıtı olmadığını söylüyor.
   - **Açık soru:** Evde bağlanacak bir cihaz var mı, bilmiyorum.
   - **Kanıt gücü:** zayıf-orta.
2. `team/proposals/2026-10-01-stt-soniox-olcum.md`
   - **Satır:** "doğal konuşma" (HAVE ama kalite işi kalıyor), sıra 6. Kuyruktaki `understanding-stt-corpus` işine bağlı.
   - **Öneri:** Soniox'u Türkçe akışlı STT adayı olarak ölçmek. Benimseme önerilmiyor.
   - **Kanıt:** Fiyat 0,12 USD/saat, 60+ dil, Türkçe sayfası var. Home Assistant 2026.9 de (2 Eylül 2026) Soniox'u Labs'ta deniyor.
   - **Eksikler:** Karşılaştırma rakamları satıcının kendi sayfasından. Bağımsız bir Türkçe WER ölçümü bulamadım. Veri saklama bilgisi fiyat sayfasında yok.
   - **Önkoşul:** İşçi önce şartları ve veri politikasını okuyacak. Hesap açmak ve ses verisinin üçüncü tarafa gitmesi sahibin onayını ister.
   - **Kanıt gücü:** ince.
3. `team/proposals/2026-10-01-gercek-ev-sahibi-provasi.md`
   - **Satır:** Doğrudan bir JARVIS satırı değil; "kendini onaran" satırının PROVEN_REAL şartına ve döngü güvenilirliğine hizmet ediyor.
   - **Öneri:** Sunucuya dokunan betik ve şema değişikliklerinde, birleştirmeden önce gerçek hosta karşı yalnız-okuma bir prova. Host'tan alınan gerçek durum, sahte-host testinin girdisi olur.
   - **Kanıt:** Aynı türden üç olay bugün, hepsi sahte host'ta yeşil, gerçek host'ta kusurlu çıktı. Üçü de QUALIFICATION'da.
     - 38.12: bakım betiği GREEN servis ederken `api-blue`'yu bekledi.
     - 38.17: preflight işlem kilidini bir kez sordu.
     - 38.15: kilit sürümü PostgreSQL'de `varchar(32)`'ye sığmadı.
   - **Not:** Sahibin 38.16 kuralı veritabanını kapsıyor. Bu öneri aynı kuralı betiklere genişletiyor.
   - **Kanıt gücü:** güçlü.

**Taramada çıkan ama öneri olmayan notlar**
- Radicale 3.8.1 (25 Eylül 2026), sıra 3 "Sekreter" için ileride işe yarar. Yeni bir karar gerektirmiyor.
- Parakeet-TDT-0.6B-v3 çok dilli bir ASR modeli. Türkçe sonuçlarını bulamadım, bu yüzden öneri yazmadım.
- Önceki üç öneri (anlatı satırı, bulutta yürütme, bulutta görev döngüsü) hâlâ bekliyor. Tekrarlamadım.

**Eksikler**
- Cycle raporlarındaki "Geri verilenler" boş. "Durdurulanlar" tek satır: `understanding-semantic-index`, entegrasyon dalında çakışma. Aynı türden iki olay olmadığı için buna öneri yazmadım.
- Hiçbir kütüphanenin sorun takipçisini okumadım. Home Assistant lisansı ve Soniox şartları işçinin ilk işi olarak önerilerde yazılı.
