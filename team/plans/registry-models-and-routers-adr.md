# ADR (taslak, numarasız): Ortak kayıtlar ağaçtan bulunur - app/registry.py

Kart: registry-models-and-routers (döngü d20261006). Öneri: team/proposals/2026-10-06-kayit-dosyalari-kendiliginden.md.

## Bağlam

İki elle tutulan liste her paralel dalın aynı yerine satır ekletiyordu ve entegrasyonda
çakışıyordu (6 Ekim: conversation-transcripts, home-stock-list, mail-accounts-connect):
`alembic/env.py`'deki `import app.X.models` satırları ve `main.py` create_app'teki
`app.include_router` satırları. Ayrıca env.py listesi kaymıştı: ağaçta 46 models modülü (app.models dahil)
varken app.models dışında 21'i içe aktarılıyordu; 46 tablo (conversations, mail_accounts, household_items,
goals, research_*, webpush_subscriptions ...) autogenerate'e görünmüyordu - bir sonraki
`alembic revision --autogenerate` bunları DROP olarak önerirdi.

## Karar

`services/api/app/registry.py` (yalnız stdlib `pkgutil.iter_modules` + `importlib`):

- `discover_model_modules()` - son adı `models` ya da `*_models` olan modüller, alfabetik.
  Dosya sisteminden okunur; adlandırmak hiçbir modülü çalıştırmaz. `register_models()` içe aktarır.
  env.py'de 21 satır yerine tek `register_models()`.
- `discover_routers()` - yalnız `routes` adlı modüllerde AÇIKÇA tanımlı modül düzeyi `ROUTERS`
  listesi (APIRouter). Bir dosyanın varlığından ya da `router` değişkeninden uç türetilmez.
  Sıra: `ROUTER_ORDER` (varsayılan 1000), sonra modül adı. `tests` ve `scripts` taranmaz.
- `include_discovered_routers(app)` - create_app'in açık bloğunun sonunda çağrılır; main.py'de
  zaten bağlı bir router ROUTERS'ta da varsa açılış Türkçe hatayla durur ("iki kez").

Neden stdlib: fastapi-endpoints / benzeri dosya-tabanlı yönlendirme paketleri reddedildi -
yeni bağımlılık, örtük uç türetme (Risk 1) ve 30 satırlık işi yapan bir kütüphane.

## Cırcır ve kural (sonraki kartlar için bağlayıcı)

- Yeni router = kendi `routes.py`'nde `ROUTERS = [router]`; main.py'ye `include_router` satırı YOK.
- Yeni uç kendi satırlarını `tests/unit/route_table_snapshot.txt`'ye ekler
  (`PAGENTOS_UPDATE_ROUTE_SNAPSHOT=1` ile test dosyasını koş, farkı gözden geçir).
- `test_route_table_snapshot.py::MAIN_INCLUDE_ROUTER_CEILING = 56`: main.py'deki çağrı sayısı
  bunu aşamaz; satırlar taşındıkça sabit düşürülür, asla yükseltilmez.
- Yeni models modülü env.py'ye satır istemez; `test_alembic_env_registers_every_model.py`
  ağaçtaki küme ile keşif kümesini, her eşlenmiş sınıfın modülünü ve env.py'nin
  `register_models()`'ı modül düzeyinde DEYİM olarak (AST, göçleri koşan `if`'ten önce) çağırdığını
  denetler - metin araması yorumla tatmin oluyordu (denetçi, 2026-10-06).

## Kapsam dışı / bilinen sınırlar

- Mevcut 56 `include_router` satırı bu kartta TAŞINMADI (paketlerin routes.py dosyaları başka
  kartların alanında); toplu taşıma ayrı, sonraki bir iş. Her taşıma: satırı sil, ROUTERS ekle,
  görüntü değişmez, tavanı bir düşür.
- Diğer ortak dosyalar hâlâ hub: `voice/intents.py` niyet merdiveni, `config.py`,
  `understanding/exemplars.json`.
- Açılış maliyeti: create_app ortalaması 23,0 -> 46,5 ms (5 koşu; routes modülleri zaten
  yüklü, maliyet dosya sistemi yürüyüşü ~21 ms).
- env.py artık 46 modülün hepsini yükler; bir models modülü ağır/yan etkili bir içe aktarma
  eklerse göç komutları da onu yükler (bugün birim testi hepsini yüklüyor, yeşil).
