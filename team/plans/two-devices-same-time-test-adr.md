# ADR (taslak, numarasız): "İki cihaz aynı anda" testi - fire_together, üç yarışın kanıtı, yazan yol koruyucusu

Kart: two-devices-same-time-test (d20261006). Öneri: team/proposals/2026-10-06-iki-cihaz-ayni-anda-testi.md.
Plan: team/plans/two-devices-same-time-test-integration.md. Ürün kodu (`services/api/app`) değişmedi.

## Bağlam
2026-10-06'da üç yarış kusuru (conversation-segment-race-loses-lines, household-item-create-race-500,
watch-cap-race-21) denetçinin APPROVE'uyla yayına çıktı. Her testi tek istek gönderiyordu; yarışları yalnız test ekibi buldu.

## Karar
1. **Yardımcı** `services/api/tests/integration/concurrency.py` dosyasındaki `fire_together(target, method, path, *, json, n, headers, meet_after, meet_wait_s, timeout_s, on_meeting)`:
   - `httpx.AsyncClient` + `ASGITransport(raise_app_exceptions=False)` kullanır; 500 bir istisna olarak değil, cihazın gördüğü yanıt olarak döner.
   - İstekler `asyncio.Barrier(n)` ve `asyncio.gather` ile aynı anda bırakılır. Uygulama gerçek `create_app` nesnesidir; kimlik `owner_client`/`attach_owner` yolundan gelir.
   - `threading.Barrier` + `TestClient` yerine bu yolu seçtim. Yazan yollar DB işini `asyncio.to_thread` içinde, her biri kendi oturumuyla yapıyor; bu yüzden N istek N ayrı havuz bağlantısına gider. Kanıt: yardımcının testinde 4 istek 4 ayrı `pg_backend_pid` ile aynı anda açık (tepe = 4).
2. **`meet_after`** (seçimlik): rotanın okumasının SQL'ine bir regex verilir. İlk n eşleşen ifade, `after_cursor_execute` olayında `meet_wait_s` dolana kadar birbirini bekler. Böylece okuma ile yazma arasındaki pencere açık tutulur.
   - Gerekçe: çıplak yarış 5 koşuda 4 kez satır kaybetti, 2 kez sınırı aştı. strict xfail ise her koşuda düşmeyi gerektirir; kaçan bir koşu XPASS, yani kırmızı kapı olur. `meet_after` ile 5/5 düştü.
   - Düzeltilmiş bir rota da bu pencereden geçer: kilitli olanda bekleme `meet_wait_s` ile biter, upsert ise zaten doğru satırı yazar.
   - Hiçbir ifadeyle eşleşmeyen bir desen sessizce zamanlama yarışına dönmez; AssertionError olur.
   - **Bırakış sayılır** (denetçi dönüşü 2026-10-06): `Meeting.released_seeing`, her bırakılan ifade için o an kaç ifadenin geldiğini tutar; `fire_together(..., on_meeting=)` bu nesneyi çağırana verir. Yardımcı testi `[[4, 4, 4, 4]]` ister; bekleme kaldırılınca `[[1, 2, 3, 4]]` ile KIRMIZI. Strict xfail'leri her koşuda tutan şey bu bekleme olduğu için onu kıran değişiklik artık kapıda düşer.
   - `asyncio.Barrier` yük taşımıyor (denetçinin B mutasyonu yeşil kaldı): `meet_after` olan testlerde pencereyi bekleme tutar, engel yalnız istekleri aynı döngü adımında başlatır. Bilerek bırakıldı; `meet_after` olmayan çağrının üst üste binmesini `pg_backend_pid` testi sayar.
3. **Üç kusurun kırmızı kanıtı** `test_two_devices_same_time_pg.py` dosyasında, her biri `xfail(strict=True, reason=<kusur kimliği>)` ile işaretli. Tabanda üçü de düzeltilmemiş, hiçbiri xfail'siz değil. Rotalar ve sınır koddan geliyor (`MAX_WATCHES` içe aktarılıyor). Kusuru düzelten kart, XPASS kırmızıya dönünce işareti kaldırır.
4. **Koruyucu** `services/api/tests/unit/test_write_routes_concurrency_ratchet.py`:
   - Yollar AST ile okunur; `APIRouter(prefix=)` ve modül sabitleri çözülür.
   - `app` altındaki HER modül okunur (eski "APIRouter" metin süzgeci kalktı) ve her `@<ad>.post/put/patch` sayılır. Ad aynı modülde `APIRouter(...)` ile yapılmamışsa (içe aktarılmış router, `@Box.router.post`) koruyucu `a router this guard cannot read` ile durur (denetçinin M4'ü). Bugünkü 193 yolun hepsi yerel `router`; baseline değişmedi. `add_api_route(...)` çağrıları dekoratör olmadığı için okunmaz (açık risk).
   - Kapsam eşleşmesi: tests/integration altındaki her `fire_together(...)` çağrısının yöntem ve yol argümanı (sabit, f-string ya da modül sabiti) rotanın şablonuyla birebir eşleşmeli. Yalnızca adı geçmek ya da tek `client.post` yetmez.
   - **193 yazan yol** var (177 POST, 8 PUT, 8 PATCH). Bu kart 3'ünü kapsadı.
   - **UNCOVERED_BASELINE = 190**, `BASELINE_CEILING = 190`. Listeye giriş eklemek tavan testiyle reddedilir; ölü ya da kapsanmış giriş de kırmızı olur.
   - **EXEMPT boş.** Muafiyet, rotanın koduna karşı doğrulanmış bir gerekçe ister; doğrulanmamış bir muafiyet borçtan kötüdür.
   - Mesaj: `yazan yol için eşzamanlılık testi yok: <YÖNTEM> <yol>`.

## guards.json satırı (Proje Yöneticisi birleştirmede bağlar; bu kartın alanında değil)
```
{"id":"write-route-concurrency","kind":"pytest","path":"services/api/tests/unit/test_write_routes_concurrency_ratchet.py","label":"yazan yolun eşzamanlılık testi yok"}
```

## Rol satırları (.claude/agents/ yazımı izin katmanınca reddedildi - ALAN_ISTEGI ile PY'ye)
`.claude/agents/worker.md`: "3. Implement" satırının hemen ÜSTÜNE eklenecek:
```
   **İki cihaz aynı anda:** every new or changed WRITING REST route and voice intent also gets
   a `fire_together` test (`services/api/tests/integration/concurrency.py`, real PostgreSQL):
   expected rows and status codes; a capped write fires cap+5 (2026-10-06: three races shipped).
```
`.claude/agents/inspector.md`: Pass 2'deki "memory/CPU on CPX32; rollback path." satırının hemen ALTINA eklenecek:
```
- **Tek istek yeşil yetmez** (2026-10-06: üç yarış APPROVE alıp yayına çıktı): yeni ya da
  değişen yazan yol / sesli niyet için `fire_together` testi var mı, satır SAYIYOR mu (durum
  kodları + tablo satırı; sınırlı yazmada sınır+5)? Yoksa `RETURN (iki cihaz aynı anda testi)`.
```

## Kazanmadıklarımız
- İki Cloud Core süreci arasındaki yarış: tek süreç ve tek havuz test ediliyor; süreç içi kilitle yapılan bir "düzeltme" burada yeşil görünür ama iki süreçte yine kaybeder. Düzeltme kartları DB düzeyinde çözmeli (kısıt, upsert, kilit).
- Ağ kopması, yeniden deneme ve saat kayması.
- Sesli niyet yolu: bu kart yalnız REST'i kapsıyor; rol satırı sesli niyeti de istiyor.
- Koruyucu yalnızca adın geçtiğini denetler, satırın sayıldığını denetlemez; onu denetçinin satırı yakalar.
- Aynı yolda POST ve PUT ayrı sayılır; ama bir yolun her kullanım biçimi (ör. farklı gövde dalları) ayrı ayrı yarıştırılmaz.
