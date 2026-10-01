# Öneri: Ev — Home Assistant'ı `smart_home` sağlayıcısı olarak bağla (salt-okuma ile başla)

Tarih: 2026-10-01 · Araştırmacı · Durum: awaiting_owner
Roadmap: JARVIS tablosu "evi yöneten" satırı (MISSING), sıra 4 ("The house").

## Ne
`smart_home` sağlayıcı arayüzü arkasında Home Assistant (HA). İlk adım yalnız OKUMA ("salon ışığı açık mı?",
"evde ne açık kaldı?"); yazma (aç/kapat) ikinci adımda, geri alınabilir olanlar (ışık, priz) serbest, kilit/kapı/alarm
okuma-geri-söyleme + sahibin sözü ile. Sahibin cümlesi: "Salonun ışığını kapat."

## Neden şimdi
- Sıra 4'e geldik denecek kadar erken değil: sıra 2-3 sürüyor. Bu öneri **hazırlık** niteliğinde; onaylanırsa kuyruğa
  düşük öncelikle girer. Asıl açık soru donanım: sahibin evinde HA'ya bağlanabilir cihaz var mı? Bilmiyorum.
- HA'nın resmi MCP sunucusu 2025.2'den beri var (Silver kalite), Streamable HTTP, OAuth veya uzun ömürlü belirteç; ve
  istemci yalnız "exposed" tutulan varlıkları görür/kontrol eder
  ([belge](https://www.home-assistant.io/integrations/mcp_server/)). Yani erişim sınırı HA tarafında sahibin elinde.
- HA 2026.9 (2 Eylül 2026) ses tarafında Soniox'a geçişi Labs'ta deniyor
  ([sürüm notu](https://www.home-assistant.io/blog/2026/09/02/release-20269/)); Türkçe desteği notta belirtilmemiş.
  Ses işini biz kendi yolumuzdan yapıyoruz, HA'nın ses hattına ihtiyaç yok.
- Kanıt zayıf-orta: HA belgeleri güvenilir ama "kilit/kapı için güvenlik kısıtı yok" (belge de öyle diyor); güvenlik
  kuralı bizim tarafımızda olmalı.

## Nasıl
- Seam: `smart_home` sağlayıcısı (ROADMAP'teki sağlayıcı kuralı), cihaz seçimi + makbuz + step-up yapısı zaten var.
- HA'yı **Tailscale içinde** ev ağındaki bir makinede/ofis PC'sinde çalıştır; bulut Core'a dışarıdan port açılmaz.
  Bağlantı cihaz tarafından dışarı doğru (mevcut cihaz modeli: yalnız outbound) — HA'ya yalnız o cihaz değer.
- Varlık beyaz listesi HA'da; PAOS tarafı ayrıca bir risk sınıfı tablosu (ışık=serbest, kilit/kapı/alarm=sahibin sözü).
- Değişmeyen: ses hattı, hafıza, ledger (her komut ledger'a).

## Maliyet/risk
- Efor: orta (sağlayıcı + risk tablosu + test sahte HA). Çalışma maliyeti: 0 USD; HA ~1 GB RAM, bulut CPX32'ye
  KOYMA — ev/ofis makinesinde. Lisans: HA Apache-2.0 (kullanmadan önce işçi doğrulasın).
- Gizlilik: ev durumu hassas veri; ledger'a girer, KVKK açısından yalnız sahibe ait. İş makinesi: ofis PC'sine HA
  kurmak işveren politikasına takılabilir — ev PC'si tercih.
- Cihaz güvenliği: kilit/kapı yazması varsayılan KAPALI.

## Kanıt planı
PROVEN_AUTOMATED: sahte HA ile risk tablosu + step-up. PROVEN_REAL: sahibin gerçek bir ampulü/prizi ("salonun ışığını
kapat") — bunu sahibin denemesi gerekir.

## Karar
Yapalım mı? (Önce: evde HA'ya bağlanacak bir cihaz var mı?) Alternatifler: (a) cihaz alınana kadar beklet;
(b) doğrudan Matter/Zigbee kütüphanesi — reddedilir, HA zaten bunu soyutluyor.
