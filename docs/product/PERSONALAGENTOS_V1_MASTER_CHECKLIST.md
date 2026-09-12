# PERSONALAGENTOS v1.0 — MASTER PRODUCT SPECIFICATION (MASTER CHECKLIST)

**Yürürlük:** 2026-09-12 · Sahip direktifi "MASTER PRODUCT SPECIFICATION & ROADMAP DIRECTIVE"
**Taban:** `main` @ `48dfcc3` · üretim `714ff2b` · cihaz agent `0.6.0` (`19f079c4fda2c3c7`)
**Kardeş belgeler:** `PERSONALAGENTOS_V1_FEATURE_MATRIX.md` · `PERSONALAGENTOS_V1_ROADMAP.md`

---

## 0. BU BELGENİN YETKİSİ

Bu belge PersonalAgentOS v1.0'ın **kanonik ürün spesifikasyonudur**. `docs/MASTER_SPEC.md`
ve milestone spec'leri buna tabidir; çelişkide **ölçülen kaynak + gerçek çağıran + çalışma
zamanı** kazanır.

Değiştirilemez kurallar:

1. **1–750 kimlikleri kalıcı gereksinim kimlikleridir.** Yeniden numaralandırma, birleştirme,
   silme veya özetleyip düşürme yoktur. Kavramsal tekrarlar **ortak batch'e** bağlanır ama
   her kimlik ayrı satır olarak izlenebilir kalır.
2. **Multi-device / M29 kapsam dışıdır** ve hiçbir batch'e giremez.
3. Bir madde `DONE` olmak için **kaynak + test + gerçek çağıran + (gerekiyorsa) çalışma
   zamanı kanıtı** ister. Sınıf/fonksiyon varlığı tamamlanma değildir.
4. **Sahte test gerçek wire shape ile uyuşmuyorsa madde tamamlanmış sayılmaz.**
5. `PARTIAL`, yalnızca test bulunduğu için `DONE`'a çevrilemez.
6. Erişilebilir yüzeyi olmayan özellik tamamlanmış değildir.
7. Başarısızlığını dürüstçe bildiremeyen özellik tamamlanmış değildir.
8. Geri yüklemesi kanıtlanmamış yedek `PROVEN_REAL` değildir.
9. Bağımsız artefakt incelemesi olmayan derleme `VERIFIED` değildir.
10. Postcondition doğrulanmamış operatör eylemi başarılı değildir.

## 0.1 SINIF SÖZLÜĞÜ

`IMPLEMENTATION_STATUS`: `DONE` `PARTIAL` `BROKEN` `MISSING` `BLOCKED_OWNER` `BLOCKED_PROVIDER` `DEFERRED` `NOT_APPLICABLE`
`PROOF_STATUS`: `PROVEN_REAL` `PROVEN_AUTOMATED` `PROVEN_PROXY` `NOT_YET_PROVEN` `BLOCKED` `PROVIDER_UNAVAILABLE`

## 0.2 KAPANIŞ KAYDI (her `DONE` için zorunlu)

```
status / commit / tests / proof / date
```

---

## A. BUG & RELIABILITY

| ID | FEATURE | SUBSYSTEM | INTENDED OUTCOME |
|---|---|---|---|
| 1 | Migration başarısızsa release kesinlikle durmalı | Release / Cloud Core | Başarısız göç sürümü bloklar, yeni renk promote edilmez |
| 2 | Alembic schema version release health içinde doğrulanmalı | Release / Health | Sağlık kapısı beklenen şema sürümünü görmeden yeşil demez |
| 3 | file.search klasör adı → gerçek mutlak path çözümü düzeltilmeli | Files / Device | Klasör tabanlı arama üretimde sonuç döndürür |
| 4 | App Factory üç template manifest'i device contract ile uyumlu olmalı | App Factory / Device | Üç şablon da cihazda kabul edilir |
| 5 | Fake device payload'ları gerçek device wire shape ile birebir aynı olmalı | Test altyapısı | Sahte, makineden nazik olamaz |
| 6 | /v1/world/facts secret redaction | World Model / Security | Hiçbir sır API yanıtında açık çıkmaz |
| 7 | Log'larda secret redaction | Observability / Security | Hiçbir sır log'a düşmez |
| 8 | Health output'larında secret redaction | Health / Security | Kimliksiz sağlık yüzeyi sır sızdırmaz |
| 9 | Dead Voice session sweeper | Voice Realtime | Zombie `active` oturum kalmaz |
| 10 | Takılmış research/task satırı sweeper | Research / Tasks | Takılan satır terminal duruma taşınır |
| 11 | Stale RUNNING/CREATED reconciliation | Executive / Research | Durum gerçeği çalışma zamanıyla uyumlu |
| 12 | Temporal unavailable durumunda typed refusal | Research / Temporal | Sağlayıcı yokken tipli, anlaşılır ret |
| 13 | Orphan research row cleanup | Research | Yetim koşu kalmaz |
| 14 | Retry loop'ları bounded olmalı | Notifications / Research | Hiçbir kuyruk sonsuz denemez |
| 15 | Push announcer sınırsız retry yapmamalı | Notifications | Kalıcı arızada geri çekilir |
| 16 | Briefing announcer tek bozuk item yüzünden kuyruğu kilitlememeli | Notifications | Zehirli mesaj karantinaya gider |
| 17 | Research announcer bounded retry kullanmalı | Research / Notifications | Sınırlı deneme + karantina |
| 18 | Embedded background loop health ayrı ayrı izlenmeli | Operations / Health | Sekiz döngünün canlılığı tek tek görünür |
| 19 | Routine clock alt bileşen hataları birbirini düşürmemeli | Routines | Bir alt tik patlarsa diğerleri koşar |
| 20 | Redis kullanılmıyorsa critical health'tan çıkarılmalı | Health | Kritik sağlık yalnız gerçekten kritik bağımlılıkları içerir |
| 21 | Build/version provenance canonical olmalı | Release / Self Model | Tek kanonik sürüm gerçeği |
| 22 | Same product version altında farklı build ayırt edilmeli | Release | Aynı app_version altında build ayrımı yapılır |
| 23 | BUILD_STATE otomatik reconcile edilmeli | Build State | Elle güncelleme kaynaklı çelişki kalmaz |
| 24 | Evidence-only-in-prose yasaklanmalı | Evidence / QA | Her iddia makineyle doğrulanabilir kanıta bağlı |
| 25 | CI tüm gerçek critical testleri kapsamalı | CI | Kritik test hiçbir kapının dışında kalmaz |
| 26 | Web testleri CI'a eklenmeli | CI / Web | ~1472 web testi kapıda koşar |
| 27 | Web linter CI'a eklenmeli | CI / Web | İki linter kapıda koşar |
| 28 | PE reader testleri CI'a eklenmeli | CI / Native | Native verdict yargıcı CI'da doğrulanır |
| 29 | PowerShell qualification suites CI'a eklenmeli | CI / Scripts | Owner harness + qualification CI'da |
| 30 | Mutation/falsification kritik contractlarda kullanılmalı | QA | Kritik sözleşme testi mutasyonla düşer |

## B. MEMORY / PERSONAL INTELLIGENCE

| ID | FEATURE | SUBSYSTEM | INTENDED OUTCOME |
|---|---|---|---|
| 31 | memory.remember Voice tool | Memory / Voice | Sesle kalıcı bellek yazılır |
| 32 | memory.search Voice tool | Memory / Voice | Sesle bellek aranır |
| 33 | Konuşma içinden preference extraction | Memory | Tercih cümlesi otomatik yakalanır |
| 34 | Konuşma içinden project/context extraction | Memory | Proje/bağlam otomatik yakalanır |
| 35 | Explicit "bunu hatırla" komutu | Memory / Intent | Açık yazma komutu çalışır |
| 36 | Explicit "bunu unut" komutu | Memory / Intent | Açık unutma komutu çalışır |
| 37 | Explicit "bunu düzelt" komutu | Memory / Intent | Açık düzeltme komutu çalışır |
| 38 | Explicit "bunu sabitle" komutu | Memory / Intent | Açık sabitleme komutu çalışır |
| 39 | Persona prompt'a ilgili memory injection | Memory / Persona | Bellek asistanın talimatına girer |
| 40 | Tool decision context'e memory injection | Memory / Tools | Araç seçimi belleği görür |
| 41 | Top-k contextual retrieval | Memory | Yanıt yolunda ilgili k kayıt getirilir |
| 42 | Memory provenance | Memory | Her kaydın kaynağı bilinir |
| 43 | Memory confidence | Memory | Her kaydın güveni bilinir |
| 44 | Conflict resolution | Memory | Çelişen kayıtlar çözülür |
| 45 | Explicit > inferred precedence | Memory | Açık söylenen çıkarımı yener |
| 46 | Project continuity | Memory | Proje bağlamı oturumlar arası sürer |
| 47 | Person/entity relationships | Memory | Kişi/varlık grafiği kurulur |
| 48 | Device-independent logical entity identity | Memory | Varlık kimliği cihazdan bağımsız |
| 49 | Current/previous object focus | Memory | "bunu/şunu" çözülür |
| 50 | Recent task continuity | Memory | Son görev bağlamı sürer |
| 51 | Real semantic embedding provider | Memory | Gerçek anlamsal gömme |
| 52 | Deterministic n-gram fallback | Memory | Sağlayıcı yokken deterministik yedek |
| 53 | Embedding provider selection | Memory | Sağlayıcı yapılandırmayla seçilir |
| 54 | Re-index pipeline | Memory | Gömme değişince yeniden indekslenir |
| 55 | Memory retention scheduler | Memory | Bellek saklama politikayla süpürülür |
| 56 | Sensitive-data exclusion | Memory / Security | Hassas veri belleğe yazılmaz |
| 57 | Memory audit UI | Web | Sahip belleğini görebilir |
| 58 | Pin/unpin UI | Web | Sahip kayıt sabitleyebilir |
| 59 | Forget UI | Web | Sahip kayıt sildirebilir |
| 60 | Correction UI | Web | Sahip kayıt düzeltebilir |
| 61 | "Neden bunu hatırladın?" explanation | Memory | Hatırlama gerekçesi açıklanır |
| 62 | Memory usage receipts | Memory | Hangi kaydın kullanıldığı raporlanır |

## C. SELF MODEL / WORLD MODEL

| ID | FEATURE | SUBSYSTEM | INTENDED OUTCOME |
|---|---|---|---|
| 63 | Self Model runtime/source correlation | Self Model | Kaynak ile çalışan sürüm ilişkilendirilir |
| 64 | Current deployed SHA awareness | Self Model | Sistem hangi SHA'yı koştuğunu bilir |
| 65 | Current Windows build awareness | Self Model / Device | Cihaz build kimliği bilinir |
| 66 | Current device capability awareness | Self Model / Device | Güncel yetenek listesi bilinir |
| 67 | World model terminal-state accuracy | World Model | Terminal durumlar doğru sayılır |
| 68 | READY research'ün running sayılmaması | World Model | Biten iş "çalışıyor" görünmez |
| 69 | Stuck task detection | World Model | Takılan iş tespit edilir |
| 70 | Activity Ledger duplicate Voice event prevention | Ledger | Tek oturum tek satır |
| 71 | Experience Engine scheduler | Experience | Deneyim motoru düzenli koşar |
| 72 | Activity → lesson extraction | Experience | Olaylardan ders çıkar |
| 73 | Lesson → memory integration | Experience / Memory | Ders belleğe yazılır |
| 74 | Current system health explanation | Self Model | Sağlık insan diliyle açıklanır |
| 75 | "Şu an ne yapıyorsun?" truthful answer | Self Model | Doğru anlık iş yanıtı |
| 76 | "Nerede takıldın?" truthful answer | Self Model | Takılma noktası dürüstçe söylenir |
| 77 | "Son bug neydi?" evidence-based answer | Self Model | Kanıta dayalı bug yanıtı |
| 78 | "Hangi özelliklerin çalışmıyor?" runtime answer | Self Model | Çalışmayanlar çalışma zamanından |
| 79 | Self-diagnostic summary | Self Model | Öz teşhis özeti |
| 80 | Self-model stale-data detection | Self Model | Bayat veri tespit edilir |

## D. DIGITAL OPERATOR 2.0

| ID | FEATURE | SUBSYSTEM | INTENDED OUTCOME |
|---|---|---|---|
| 81 | App launch | Operator | Uygulama açılır |
| 82 | App close | Operator | Uygulama kapatılır |
| 83 | Window activate | Operator | Pencere öne alınır |
| 84 | Window move | Operator | Pencere taşınır |
| 85 | Window resize | Operator | Pencere boyutlanır |
| 86 | Minimize | Operator | Küçültülür |
| 87 | Maximize | Operator | Büyütülür |
| 88 | Restore | Operator | Eski haline döner |
| 89 | Window list | Operator | Pencereler listelenir |
| 90 | Current foreground read | Operator | Öndeki pencere okunur |
| 91 | Keyboard typing | Operator | Metin yazılır |
| 92 | Single key press | Operator | Tek tuş gönderilir |
| 93 | Keyboard shortcuts | Operator | Kısayol gönderilir |
| 94 | Mouse move | Operator | Fare taşınır |
| 95 | Mouse click | Operator | Tıklanır |
| 96 | Double click | Operator | Çift tıklanır |
| 97 | Right click | Operator | Sağ tıklanır |
| 98 | Scroll | Operator | Kaydırılır |
| 99 | UI Automation tree inspect | Operator / UIA | UIA ağacı okunur |
| 100 | UIA button invoke | Operator / UIA | Düğme UIA ile tetiklenir |
| 101 | UIA set value | Operator / UIA | Alan UIA ile doldurulur |
| 102 | UIA read text | Operator / UIA | Metin UIA ile okunur |
| 103 | UIA select item | Operator / UIA | Öğe UIA ile seçilir |
| 104 | Screenshot capture | Operator | Ekran görüntüsü alınır |
| 105 | Screenshot understanding | Operator / Vision | Görüntü anlamlandırılır |
| 106 | Vision-based fallback | Operator / Vision | Görüşe düşülebilir |
| 107 | Coordinate fallback only as last resort | Operator | Koordinat yalnız son çare |
| 108 | FocusGuard | Operator | Yanlış pencereye girdi gitmez |
| 109 | Secret typing refusal | Operator / Security | Sır yazımı reddedilir |
| 110 | Per-action receipt | Operator | Her eylem makbuz üretir |
| 111 | Postcondition verification | Operator | Sonuç doğrulanmadan başarı yok |
| 112 | Observe → Decide → Act → Verify | Operator | Kapalı döngü yürütme |
| 113 | Dynamic replanning | Operator | Plan çalışırken değişebilir |
| 114 | Retry by failure taxonomy | Operator | Hata sınıfına göre deneme |
| 115 | Recovery/escalation strategy | Operator | Kurtarma ve seviye yükseltme |
| 116 | App-specific structured adapters | Operator | Uygulamaya özel yapısal sürücü |
| 117 | More than 6 launchable apps | Operator | İzin listesi genişler |
| 118 | Safe terminal command expansion | Operator | Güvenli komut kümesi büyür |
| 119 | Process inspect | Operator | Süreçler incelenir |
| 120 | Process stop with policy | Operator | Politikayla süreç durdurulur |
| 121 | Service inspect | Operator | Servisler incelenir |
| 122 | Service restart with policy | Operator | Politikayla servis yeniden başlar |
| 123 | Windows Settings navigation | Operator | Ayarlarda gezinilir |
| 124 | File Explorer operations | Operator | Dosya gezgini kullanılır |
| 125 | Office app interaction | Operator | Office uygulamaları sürülür |
| 126 | IDE interaction | Operator | IDE sürülür |
| 127 | Browser + desktop mixed plan | Operator / Browser | Karma plan çalışır |
| 128 | Multi-step operator task persistence | Operator | Çok adımlı iş kalıcıdır |
| 129 | Operator pause/cancel | Operator | Duraklat/iptal |
| 130 | Operator "show me before acting" mode | Operator | Önizleme modu |

## E. FILE / DOCUMENT INTELLIGENCE

| ID | FEATURE | SUBSYSTEM | INTENDED OUTCOME |
|---|---|---|---|
| 131 | PDF read | Documents | PDF okunur |
| 132 | DOCX read | Documents | DOCX okunur |
| 133 | XLSX read | Documents | XLSX okunur |
| 134 | PPTX read | Documents | PPTX okunur |
| 135 | CSV read | Documents | CSV okunur |
| 136 | JSON read | Documents | JSON okunur |
| 137 | Markdown read | Documents | MD okunur |
| 138 | Source code read | Documents | Kaynak kod okunur |
| 139 | Image metadata read | Documents | Görsel meta verisi okunur |
| 140 | OCR | Documents / Vision | Görselden metin çıkar |
| 141 | Image text extraction | Documents / Vision | Görsel metni kullanılabilir |
| 142 | Archive inspect | Documents | Arşiv içeriği görülür |
| 143 | EPUB read | Documents | EPUB okunur |
| 144 | RTF read | Documents | RTF okunur |
| 145 | ODT read | Documents | ODT okunur |
| 146 | Legacy Office formats | Documents | .doc/.xls/.ppt okunur |
| 147 | File metadata search | Documents | Meta veriyle arama |
| 148 | Full text search | Documents | Tam metin arama |
| 149 | Semantic document search | Documents / Memory | Anlamsal belge arama |
| 150 | File deduplication | Documents | Yinelenen dosya sadeleşir |
| 151 | Duplicate detection | Documents | Yineleme tespit edilir |
| 152 | File preview | Documents | Önizleme |
| 153 | File edit | Documents | Dosya düzenlenir |
| 154 | File write | Documents | Dosya yazılır |
| 155 | File append | Documents | Dosyaya eklenir |
| 156 | File rename | Documents | Dosya yeniden adlandırılır |
| 157 | File move | Documents | Dosya taşınır |
| 158 | File copy | Documents | Dosya kopyalanır |
| 159 | File delete | Documents | Dosya silinir (politikayla) |
| 160 | Undo journal | Documents | Her mutasyon geri alınabilir |
| 161 | Trash/recycle integration | Documents | Silinen geri dönüşüme gider |
| 162 | Mutation receipt | Documents | Her değişiklik makbuzlu |
| 163 | Before/after hash | Documents | Değişim hash'le kanıtlı |
| 164 | Document version history | Documents | Sürüm geçmişi |
| 165 | Safe atomic write | Documents | Yarım yazma olmaz |
| 166 | Owner approval by risk | Documents / Security | Riskli mutasyon onay ister |
| 167 | "Bu dosyayı düzenle" natural workflow | Documents / Intent | Doğal düzenleme akışı |
| 168 | "Şu klasördeki dosyaları özetle" | Documents / Intent | Klasör özeti |
| 169 | "Bu iki dokümanı karşılaştır" | Documents / Executive | Belge karşılaştırma |
| 170 | "Bu belgeyi güncelle ve kaydet" | Documents / Intent | Güncelle ve kaydet |

## F. BROWSER / INTERNET / RESEARCH

| ID | FEATURE | SUBSYSTEM | INTENDED OUTCOME |
|---|---|---|---|
| 171 | Managed Chrome | Browser | Yönetilen profil kullanılır |
| 172 | Owner Chrome reuse | Browser | Sahibin Chrome'u kullanılabilir |
| 173 | Session reuse | Browser | Açık oturumlar yeniden kullanılır |
| 174 | Search | Browser | Arama yapılır |
| 175 | Navigation | Browser | Gezinilir |
| 176 | DOM inspect | Browser | DOM incelenir |
| 177 | DOM click | Browser | DOM'dan tıklanır |
| 178 | DOM text entry | Browser | DOM'a yazılır |
| 179 | Form fill | Browser | Form doldurulur |
| 180 | Download | Browser | Dosya indirilir |
| 181 | Upload | Browser | Dosya yüklenir |
| 182 | Multi-tab handling | Browser | Çoklu sekme |
| 183 | Tab focus | Browser | Sekme öne alınır |
| 184 | Browser screenshot | Browser | Sayfa görüntüsü |
| 185 | CAPTCHA detection | Browser | CAPTCHA tespit edilir |
| 186 | CAPTCHA bypass prohibited | Browser / Security | Asla çözülmez |
| 187 | SSRF guard | Browser / Security | SSRF engellenir |
| 188 | Private network guard | Browser / Security | Özel ağ korunur |
| 189 | Owner research authorization enforcement | Research / Security | Yetkisiz araştırma yapılmaz |
| 190 | QUICK research | Research | Hızlı mod |
| 191 | STANDARD research | Research | Standart mod |
| 192 | DEEP research | Research | Derin mod gerçekten koşar |
| 193 | Source ranking | Research | Kaynak sıralaması |
| 194 | Domain diversity | Research | Alan çeşitliliği |
| 195 | Challenge skip | Research | Engelli kaynak atlanır |
| 196 | Source cooldown | Research | Kaynak soğuması |
| 197 | Citation binding | Research | İddia kaynağa bağlı |
| 198 | Research focus/current report | Research | Güncel rapor odağı |
| 199 | Previous report references | Research | Önceki rapora atıf |
| 200 | "Bunu teknik anlat" | Research / Voice | Teknik anlatım |
| 201 | "Bir önceki araştırmayı aç" | Research / Intent | Önceki rapor açılır |
| 202 | Research cancel | Research | Araştırma iptal edilir |
| 203 | Research pause | Research | Duraklatılır |
| 204 | Research resume | Research | Devam eder |
| 205 | Temporal typed failures | Research | Tipli hata sınıfları |
| 206 | Orphan cleanup | Research | Yetim temizliği |
| 207 | Provider fallback transparency | Research | Sağlayıcı düşüşü şeffaf |
| 208 | Result-first narration | Research / Voice | Önce sonuç anlatılır |
| 209 | Technical mode | Research / Voice | Teknik mod |
| 210 | Research history UI | Web | Araştırma geçmişi |

## G. VOICE / REALTIME

| ID | FEATURE | SUBSYSTEM | INTENDED OUTCOME |
|---|---|---|---|
| 211 | Browser Voice WebRTC | Voice | Tarayıcıda gerçek zamanlı ses |
| 212 | Stable data channel | Voice | Kararlı veri kanalı |
| 213 | Barge-in | Voice | Araya girilebilir |
| 214 | Semantic VAD | Voice | Anlamsal konuşma tespiti |
| 215 | Noise suppression | Voice | Gürültü bastırma |
| 216 | Echo cancellation | Voice | Yankı giderme |
| 217 | Mic sensitivity modes | Voice | Mikrofon hassasiyeti seçilir |
| 218 | Reconnect | Voice | Kopmada geri bağlanır |
| 219 | Dead session cleanup | Voice | Ölü oturum temizlenir |
| 220 | Session TTL | Voice | Oturumun ömrü sınırlı |
| 221 | False LISTENING prevention | Voice / UX | Dinlemiyorken "dinliyor" demez |
| 222 | Mic track loss detection | Voice | Mikrofon kaybı görülür |
| 223 | Provider 60-min limit handling | Voice | Tavan öncesi yenilenir |
| 224 | Long-form narration | Narration | Uzun metin seslendirilir |
| 225 | Narration queue | Narration | Anlatım kuyruğu |
| 226 | Read-ahead | Narration | İleri okuma |
| 227 | Pause/resume narration | Narration | Duraklat/devam |
| 228 | Pronunciation dictionary | Voice | Telaffuz sözlüğü |
| 229 | Pronunciation applied to assistant speech | Voice | Kural asistanın konuşmasında |
| 230 | Turkish number pronunciation | Voice | Türkçe sayı okuma |
| 231 | Speaking state tied to real playback | Voice / UX | Konuşma durumu gerçek sese bağlı |
| 232 | RMS only drives animation intensity | Voice / Core | RMS yalnız animasyon şiddeti |
| 233 | Text fallback on provider failure | Voice | Ses düşerse metin |
| 234 | TTS fallback policy | Voice | Yedek TTS politikası |
| 235 | Controlled provider-unavailable UI | Voice / Web | Sağlayıcı yok durumu kontrollü |
| 236 | Voice diagnostics | Voice | Ses teşhis yüzeyi |
| 237 | "Teknik anlat" mode | Voice | Teknik anlatım modu |
| 238 | Voice activity history | Voice / Ledger | Ses etkinlik geçmişi |

## H. DEVICE-SIDE / AMBIENT VOICE

| ID | FEATURE | SUBSYSTEM | INTENDED OUTCOME |
|---|---|---|---|
| 239 | Device-side microphone provider | Device Voice | Cihazda mikrofon sağlayıcısı |
| 240 | Browser-independent listening | Device Voice | Tarayıcısız dinleme |
| 241 | Wake word | Device Voice | Uyandırma sözcüğü |
| 242 | Wake-word enable/disable | Device Voice | Açılıp kapatılabilir |
| 243 | Push-to-talk fallback | Device Voice | Bas-konuş yedeği |
| 244 | Local VAD | Device Voice | Cihazda konuşma tespiti |
| 245 | Speaker verification | Voice Identity | Konuşmacı doğrulanır |
| 246 | Trusted-device check | Voice Identity / Security | Cihaz güveni sunucuda doğrulanır |
| 247 | Speaker-confidence threshold | Voice Identity | Güven eşiği uygulanır |
| 248 | Sensitive-command higher threshold | Voice Identity | Hassas komutta eşik yükselir |
| 249 | Raw voice not archived | Voice / Privacy | Ham ses saklanmaz |
| 250 | Device-local Voice startup | Device Voice | Cihazda ses açılışta başlar |
| 251 | Voice service restart recovery | Device Voice | Çökerse toparlanır |
| 252 | Voice process health | Device Voice | Ses süreci sağlığı |
| 253 | Offline command subset | Device Voice | Çevrimdışı komut kümesi |
| 254 | Voice privacy indicator | Device Voice / UX | Dinleme göstergesi |
| 255 | Hardware mic mute awareness | Device Voice | Donanım susturması bilinir |

## I. ALARM / ROUTINES / MORNING EXPERIENCE

| ID | FEATURE | SUBSYSTEM | INTENDED OUTCOME |
|---|---|---|---|
| 256 | Durable alarm create | Alarms | Kalıcı alarm kurulur |
| 257 | Alarm cancel | Alarms | Alarm iptal |
| 258 | Alarm list | Alarms | Alarm listesi |
| 259 | Snooze | Alarms | Ertele |
| 260 | Stop | Alarms | Durdur |
| 261 | Weekday recurring alarm | Alarms | Hafta içi tekrar |
| 262 | Custom recurring alarm | Alarms | Özel tekrar |
| 263 | Local device backup alarm | Alarms / Device | Cihazda yedek alarm |
| 264 | Cloud-independent alarm firing | Alarms / Device | Bulut kapalıyken çalar |
| 265 | Display wake | Ambient | Ekran uyanır |
| 266 | Saved YouTube wake song | Alarms / Device | Kayıtlı uyandırma şarkısı |
| 267 | Safe tone fallback | Alarms | Güvenli ton yedeği |
| 268 | Volume ramp | Alarms | Ses kademeli artar |
| 269 | Media duck | Alarms | Diğer ses kısılır |
| 270 | Greeting | Briefing | Karşılama |
| 271 | Current time | Briefing | Saat söylenir |
| 272 | Weather | Briefing | Hava durumu |
| 273 | Location | Briefing | Konum |
| 274 | System status | Briefing | Sistem durumu |
| 275 | Overnight task summary | Briefing | Gecelik iş özeti |
| 276 | Research completion summary | Briefing | Biten araştırma özeti |
| 277 | Calendar summary | Briefing / Calendar | Takvim özeti |
| 278 | Mail summary | Briefing / Mail | Mail özeti |
| 279 | News summary | Briefing / News | Haber özeti |
| 280 | Morning briefing | Briefing | Sabah brifingi |
| 281 | Browser-independent morning briefing | Briefing / Device | Tarayıcısız brifing |
| 282 | Alarm fired receipt back to Cloud | Alarms / Device | Cihaz çaldığını bildirir |
| 283 | Prevent duplicate cloud/local firing | Alarms | Çift çalma olmaz |
| 284 | Single late threshold | Alarms | Tek gecikme eşiği |
| 285 | Alarm history | Alarms | Alarm geçmişi |
| 286 | Alarm test mode | Alarms | Test modu |
| 287 | routine.create | Routines / Voice | Sesle rutin kurulur |
| 288 | routine.list | Routines / Voice | Rutinler listelenir |
| 289 | routine.cancel | Routines / Voice | Rutin iptal |
| 290 | routine.pause | Routines / Voice | Rutin duraklatılır |
| 291 | routine.resume | Routines / Voice | Rutin devam eder |
| 292 | Presence-trigger routine | Routines | Varlık tetikli rutin |
| 293 | Schedule-trigger routine | Routines | Zaman tetikli rutin |
| 294 | Condition-trigger routine | Routines | Koşul tetikli rutin |
| 295 | Routine panel in Cockpit | Web | Rutin paneli |
| 296 | Natural-language recurring routines | Routines / Intent | Doğal dille tekrar |
| 297 | "Her sabah 08:00..." | Routines / Intent | Zaman ifadesi anlaşılır |
| 298 | "Evden çıkınca..." | Routines / Intent | Varlık ifadesi anlaşılır |
| 299 | "Bilgisayar boşta kalınca..." | Routines / Intent | Boşta koşulu anlaşılır |

## J. PRESENCE / ACTIVE EYE / DISPLAY

| ID | FEATURE | SUBSYSTEM | INTENDED OUTCOME |
|---|---|---|---|
| 300 | Camera open | Presence | Kamera açılır |
| 301 | Camera close | Presence | Kamera kapanır |
| 302 | Camera reopen | Presence | Kamera yeniden açılır |
| 303 | Camera privacy state | Presence / Privacy | Kamera durumu görünür |
| 304 | PRESENT | Presence | Buradaymış durumu |
| 305 | AWAY | Presence | Uzaktaymış durumu |
| 306 | RETURNED | Presence | Dönmüş durumu |
| 307 | RESTING | Presence | Dinleniyor durumu erişilebilir |
| 308 | LIKELY_ASLEEP | Presence | Uyuyor olabilir durumu erişilebilir |
| 309 | UNKNOWN | Presence | Bilinmiyor durumu |
| 310 | Temporal confidence | Presence | Zamanla güven değişir |
| 311 | Input activity fusion | Presence | Klavye/fare kaynaştırılır |
| 312 | Time-of-day fusion | Presence | Saat bağlamı kaynaştırılır |
| 313 | Owner preference fusion | Presence | Sahip tercihi kaynaştırılır |
| 314 | Camera failure ≠ asleep | Presence | Kamera arızası uyku sayılmaz |
| 315 | UNKNOWN keeps display on | Ambient | Bilinmiyorken ekran kapanmaz |
| 316 | Keyboard wake | Ambient | Klavye uyandırır |
| 317 | Mouse wake | Ambient | Fare uyandırır |
| 318 | Display off | Ambient | Ekran kapanır |
| 319 | Display wake | Ambient | Ekran uyanır |
| 320 | Multiple-monitor power | Ambient | Çok monitör güç yönetimi |
| 321 | No topology/resolution modification | Ambient | Çözünürlük/topoloji değişmez |
| 322 | Alarm wake precedence | Ambient / Alarms | Alarm ekran kapatmayı yener |
| 323 | Holdoff after input | Ambient | Girdiden sonra bekleme |
| 324 | Holdoff after alarm | Ambient | Alarmdan sonra bekleme |
| 325 | Holdoff after explicit wake | Ambient | Açık uyandırmadan sonra bekleme |
| 326 | Device-local presence provider | Presence / Device | Cihazda varlık sağlayıcısı |
| 327 | Browser-independent camera provider | Presence / Device | Tarayıcısız kamera |
| 328 | No raw camera archive | Presence / Privacy | Ham görüntü saklanmaz |
| 329 | Structured perception only | Presence / Privacy | Yalnız yapısal algı |
| 330 | Presence history | Presence | Varlık geçmişi |
| 331 | Ambient policy UI | Web | Ortam politikası arayüzü |
| 332 | Auto display-off on/off | Web | Otomatik kapatma anahtarı |
| 333 | "Uyurken ekranı kapat" | Ambient / Intent | Uyku politikası çalışır |
| 334 | "Ben dönünce aç" | Ambient / Intent | Dönüşte açılır |

## K. MAIL / CALENDAR

| ID | FEATURE | SUBSYSTEM | INTENDED OUTCOME |
|---|---|---|---|
| 335 | IMAP read | Mail | Posta okunur |
| 336 | SMTP send | Mail | Posta gönderilir |
| 337 | CalDAV read | Calendar | Takvim okunur |
| 338 | Mail list | Mail | Posta listelenir |
| 339 | Mail search | Mail | Posta aranır |
| 340 | Mail summarize | Mail | Posta özetlenir |
| 341 | Thread summarize | Mail | Zincir özetlenir |
| 342 | Draft | Mail | Taslak yazılır |
| 343 | Reply draft | Mail | Yanıt taslağı |
| 344 | Forward draft | Mail | İletme taslağı |
| 345 | Send confirmation gate | Mail / Security | Gönderim onay kapısı |
| 346 | Reply References correctness | Mail | Yanıt zinciri kırılmaz |
| 347 | Attachment read | Mail | Ek okunur |
| 348 | Attachment save | Mail | Ek kaydedilir |
| 349 | Calendar list | Calendar | Etkinlikler listelenir |
| 350 | Calendar today | Calendar | Bugün |
| 351 | Calendar week | Calendar | Bu hafta |
| 352 | Create event proposal | Calendar | Etkinlik önerisi |
| 353 | Edit event proposal | Calendar | Düzenleme önerisi |
| 354 | Cancel event | Calendar | Etkinlik iptali |
| 355 | RSVP | Calendar | Katılım yanıtı |
| 356 | RRULE | Calendar | Tekrar kuralı |
| 357 | VALARM | Calendar | Hatırlatıcı bileşeni |
| 358 | Reminder | Calendar | Hatırlatma |
| 359 | Calendar index | Calendar | Takvim indeksi |
| 360 | Mail polling | Mail | Düzenli posta çekme |
| 361 | Calendar sync | Calendar | Takvim eşitleme |
| 362 | Morning mail summary | Briefing / Mail | Sabah posta özeti |
| 363 | Morning calendar summary | Briefing / Calendar | Sabah takvim özeti |
| 364 | "Maillerime bak" | Mail / Intent | Sesle posta |
| 365 | "Bu hafta ne var?" | Calendar / Intent | Sesle hafta |
| 366 | "Perşembe toplantısını iptal et" | Calendar / Intent | Sesle iptal |

## L. NOTIFICATIONS / OWNER REACHABILITY

| ID | FEATURE | SUBSYSTEM | INTENDED OUTCOME |
|---|---|---|---|
| 367 | Durable notification table | Notifications | Bildirim kalıcı saklanır |
| 368 | In-app inbox | Notifications / Web | Uygulama içi kutu |
| 369 | Desktop toast | Notifications / Device | Masaüstü bildirimi |
| 370 | Toast action buttons | Notifications / Device | Bildirimde eylem düğmesi |
| 371 | Device audio notification | Notifications / Device | Sesli bildirim |
| 372 | WebPush | Notifications | Tarayıcı push |
| 373 | FCM | Notifications | Android push |
| 374 | APNs | Notifications | iOS push |
| 375 | Delivery receipt | Notifications | Gerçek teslim makbuzu |
| 376 | Delivery retry | Notifications | Sınırlı yeniden deneme |
| 377 | Delivery history | Notifications | Teslim geçmişi |
| 378 | Priority | Notifications | Öncelik |
| 379 | Quiet hours | Notifications | Sessiz saatler |
| 380 | Notification grouping | Notifications | Gruplama |
| 381 | "Task completed" | Notifications | İş bitti bildirimi |
| 382 | "Task failed" | Notifications | İş başarısız bildirimi |
| 383 | "Owner approval required" | Notifications | Onay gerekiyor |
| 384 | "Rollback happened" | Notifications | Geri alma oldu |
| 385 | "Backup failed" | Notifications | Yedek başarısız |
| 386 | "Alarm failed" | Notifications | Alarm başarısız |
| 387 | "Research finished" | Notifications | Araştırma bitti |
| 388 | "SelfDev candidate ready" | Notifications | Aday hazır |
| 389 | Notification fallback ladder | Notifications | Teslim merdiveni |
| 390 | Fake provider must never report delivered | Notifications / QA | Sahte "iletildi" diyemez |

## M. ARTIFACT FACTORY

| ID | FEATURE | SUBSYSTEM | INTENDED OUTCOME |
|---|---|---|---|
| 391 | PDF | Artifacts | PDF üretilir |
| 392 | DOCX | Artifacts | DOCX üretilir |
| 393 | XLSX | Artifacts | XLSX üretilir |
| 394 | PPTX | Artifacts | PPTX üretilir |
| 395 | HTML | Artifacts | HTML üretilir |
| 396 | Markdown | Artifacts | MD üretilir |
| 397 | TXT | Artifacts | TXT üretilir |
| 398 | CSV | Artifacts | CSV üretilir |
| 399 | JSON | Artifacts | JSON üretilir |
| 400 | Image artifact | Artifacts | Görsel artefakt |
| 401 | Deterministic render | Artifacts | Aynı girdi aynı bayt |
| 402 | Independent read-back | Artifacts | Bağımsız geri okuma |
| 403 | Reopen validation | Artifacts | Yeniden açma doğrulaması |
| 404 | Spreadsheet formula validation | Artifacts | Formül bağımsız doğrulanır |
| 405 | Provenance | Artifacts | Köken kaydı |
| 406 | Actor provenance | Artifacts | Kim üretti |
| 407 | Library/runtime version provenance | Artifacts | Hangi kütüphane sürümü |
| 408 | Source manifest | Artifacts | Kaynak manifesti |
| 409 | Artifact versioning | Artifacts | Sürümleme |
| 410 | Artifact edit | Artifacts | Düzenleme |
| 411 | Artifact clone | Artifacts | Kopyalama |
| 412 | Artifact delete policy | Artifacts | Silme politikası |
| 413 | Artifact export to owner disk | Artifacts / Device | Sahibin diskine teslim |
| 414 | Artifact narration | Artifacts / Voice | Artefakt seslendirilir |
| 415 | Artifact compare | Artifacts | Karşılaştırma |
| 416 | Artifact diff | Artifacts | Fark gösterimi |

## N. APP FACTORY / CODE FACTORY

| ID | FEATURE | SUBSYSTEM | INTENDED OUTCOME |
|---|---|---|---|
| 417 | Fixed template generation | App Factory | Şablonla üretim çalışır |
| 418 | Task tracker | App Factory | Görev takip uygulaması |
| 419 | Static site | App Factory | Statik site |
| 420 | CLI tool | App Factory | Komut satırı aracı |
| 421 | Template manifest contract | App Factory / Device | Manifest sözleşmesi paylaşılır |
| 422 | General requirements parser | App Factory | Serbest gereksinim ayrıştırılır |
| 423 | Architecture planner | App Factory | Mimari planlanır |
| 424 | Project planner | App Factory | Proje planlanır |
| 425 | Model-backed code generation | App Factory | Model kod üretir |
| 426 | Multiple source files | App Factory | Çok dosyalı üretim |
| 427 | Database generation | App Factory | Veritabanı üretilir |
| 428 | API generation | App Factory | API üretilir |
| 429 | Frontend generation | App Factory | Arayüz üretilir |
| 430 | Auth generation | App Factory | Kimlik doğrulama üretilir |
| 431 | Test generation | App Factory | Test üretilir |
| 432 | Unit tests | App Factory | Birim testleri koşar |
| 433 | Integration tests | App Factory | Entegrasyon testleri koşar |
| 434 | Browser tests | App Factory | Tarayıcı testleri koşar |
| 435 | Failed test analysis | App Factory | Başarısız test analiz edilir |
| 436 | Automated bug fix | App Factory | Otomatik düzeltme |
| 437 | Retry loop | App Factory | Yeniden deneme döngüsü |
| 438 | Lint | App Factory | Statik denetim |
| 439 | Security scan | App Factory | Güvenlik taraması |
| 440 | Build | App Factory | Derleme |
| 441 | Package | App Factory | Paketleme |
| 442 | Launch | App Factory / Device | Çalıştırma |
| 443 | UI verification | App Factory / Device | Arayüz doğrulaması |
| 444 | Persistence verification | App Factory / Device | Kalıcılık doğrulaması |
| 445 | Log read-back | App Factory / Device | Log geri okuma |
| 446 | Release artifact | App Factory | Sürüm artefaktı |
| 447 | Project history | App Factory | Proje geçmişi |
| 448 | Resume development later | App Factory | Sonra devam edilir |
| 449 | Modify existing generated app | App Factory | Var olan uygulama değiştirilir |
| 450 | "Bu uygulamaya şu özelliği ekle" | App Factory / Intent | Özellik ekleme |
| 451 | "Bu bug'ı düzelt" | App Factory / Intent | Hata düzeltme |
| 452 | Arbitrary bounded app request | App Factory | Serbest ama sınırlı istek |

## O. NATIVE APP FACTORY

| ID | FEATURE | SUBSYSTEM | INTENDED OUTCOME |
|---|---|---|---|
| 453 | NativeAppSpec | Native Factory | Yerel uygulama şeması |
| 454 | WPF generation | Native Factory | WPF üretilir |
| 455 | EXE | Native Factory | EXE üretilir |
| 456 | Portable ZIP | Native Factory | Taşınabilir paket |
| 457 | MSIX | Native Factory | MSIX paketi |
| 458 | PE validation | Native Factory | PE bağımsız doğrulanır |
| 459 | Build identity | Native Factory | Derleme kimliği |
| 460 | Device build | Native Factory / Device | Cihazda derlenir |
| 461 | Cloud-triggered device build | Native Factory | Buluttan tetiklenir |
| 462 | App launch | Native Factory / Device | Üretilen uygulama çalışır |
| 463 | UI Automation | Native Factory / Device | UIA ile doğrulanır |
| 464 | Relaunch | Native Factory / Device | Yeniden çalıştırılır |
| 465 | Persistence | Native Factory / Device | Veri kalıcılığı doğrulanır |
| 466 | App log | Native Factory / Device | Uygulama logu okunur |
| 467 | Test count parsing | Native Factory | Test sayıları dürüstçe okunur |
| 468 | Native install | Native Factory / Device | Kurulum |
| 469 | Native uninstall | Native Factory / Device | Kaldırma |
| 470 | Native fix | Native Factory | Düzeltme turu |
| 471 | Native update | Native Factory | Güncelleme |
| 472 | Signing policy | Native Factory / Security | İmzalama politikası |
| 473 | Signing optional/test cert | Native Factory | Test sertifikası |
| 474 | Android project generation | Mobile Factory | Android projesi |
| 475 | APK | Mobile Factory | APK üretilir |
| 476 | AAB | Mobile Factory | AAB üretilir |
| 477 | Android emulator test | Mobile Factory | Emülatörde test |
| 478 | Android device test | Mobile Factory | Cihazda test |
| 479 | iOS explicit NOT_SUPPORTED without macOS | Mobile Factory | macOS yoksa açıkça desteklenmez |
| 480 | Generated app auto-update later | App Factory | Sonradan otomatik güncelleme |

## P. CREATIVE / IMAGE

| ID | FEATURE | SUBSYSTEM | INTENDED OUTCOME |
|---|---|---|---|
| 481 | Pillow raster edits | Creative | Raster düzenleme |
| 482 | Crop | Creative | Kırpma |
| 483 | Resize | Creative | Boyutlandırma |
| 484 | Rotate | Creative | Döndürme |
| 485 | Draw | Creative | Çizim |
| 486 | Text | Creative | Metin |
| 487 | Shapes | Creative | Şekiller |
| 488 | Composite | Creative | Birleştirme |
| 489 | Background removal | Creative | Arka plan kaldırma |
| 490 | Object removal | Creative | Nesne kaldırma |
| 491 | Object addition | Creative | Nesne ekleme |
| 492 | Image generation | Creative | Görsel üretimi |
| 493 | Image style transformation | Creative | Stil dönüşümü |
| 494 | Image enhancement | Creative | İyileştirme |
| 495 | Upscale | Creative | Büyütme |
| 496 | OCR | Creative / Vision | Metin çıkarma |
| 497 | Independent pixel validation | Creative | Piksel düzeyinde doğrulama |
| 498 | Visual semantic validation | Creative | Anlamsal görsel doğrulama |
| 499 | Paint open/display | Creative / Device | Paint açılır |
| 500 | Paint real UI operation | Creative / Device | Paint gerçekten sürülür |
| 501 | Photoshop detection | Creative / Device | Photoshop tespiti |
| 502 | Photoshop driver | Creative / Device | Photoshop sürücüsü |
| 503 | Illustrator detection | Creative / Device | Illustrator tespiti |
| 504 | Illustrator driver | Creative / Device | Illustrator sürücüsü |
| 505 | Figma integration | Creative | Figma entegrasyonu |
| 506 | Layer-aware editing | Creative | Katman farkındalığı |
| 507 | PSD support | Creative | PSD desteği |
| 508 | SVG support | Creative | SVG desteği |
| 509 | Export to owner disk | Creative / Device | Sahibin diskine çıktı |
| 510 | Creative history | Creative / Web | Yaratım geçmişi |
| 511 | Undo/redo | Creative | Geri al / yinele |
| 512 | "Bu fotoğrafı düzelt" | Creative / Intent | Doğal düzeltme isteği |

## Q. 3D / BLENDER / UNITY

| ID | FEATURE | SUBSYSTEM | INTENDED OUTCOME |
|---|---|---|---|
| 513 | Blender detection | 3D | Blender bulunur |
| 514 | Blender structured scene | 3D | Yapısal sahne |
| 515 | Scene plan JSON | 3D | Sahne planı |
| 516 | Blender Python driver | 3D | Python sürücüsü |
| 517 | Save .blend | 3D | Sahne kaydedilir |
| 518 | Render | 3D | Render alınır |
| 519 | Independent render verify | 3D | Render bağımsız doğrulanır |
| 520 | Scene inspect | 3D | Sahne incelenir |
| 521 | Production scene creation | 3D | Üretimde sahne oluşur |
| 522 | Modify existing scene | 3D | Var olan sahne değişir |
| 523 | Material control | 3D | Malzeme kontrolü |
| 524 | Lighting control | 3D | Işık kontrolü |
| 525 | Camera control | 3D | Kamera kontrolü |
| 526 | Animation | 3D | Animasyon |
| 527 | Export FBX/GLTF | 3D | Dışa aktarma |
| 528 | Unity detection | 3D / Unity | Unity bulunur |
| 529 | Unity licensing state | 3D / Unity | Lisans durumu dürüst |
| 530 | Unity project create | 3D / Unity | Proje oluşur |
| 531 | Unity scene create | 3D / Unity | Sahne oluşur |
| 532 | Unity build | 3D / Unity | Derleme |
| 533 | Unity test | 3D / Unity | Test |
| 534 | Unreal later/optional | 3D | İsteğe bağlı, sonra |

## R. EXECUTIVE AUTONOMY

| ID | FEATURE | SUBSYSTEM | INTENDED OUTCOME |
|---|---|---|---|
| 535 | Durable task graph | Executive | Dayanıklı görev grafiği |
| 536 | Step preconditions | Executive | Adım ön koşulu |
| 537 | Step postconditions | Executive | Adım son koşulu |
| 538 | Retry | Executive | Yeniden deneme |
| 539 | Compensation | Executive | Telafi gerçekten çalışır |
| 540 | Pause | Executive | Duraklat |
| 541 | Resume | Executive | Devam et |
| 542 | Cancel | Executive | İptal |
| 543 | Modify | Executive | Değiştir |
| 544 | Human approval step | Executive | İnsan onayı adımı |
| 545 | Task history | Executive | Görev geçmişi |
| 546 | Task explanation | Executive | Görev açıklaması |
| 547 | Research-report plan | Executive | Araştırma raporu planı |
| 548 | Folder-compare plan | Executive | Klasör karşılaştırma planı |
| 549 | Mail-sequence plan | Executive | Mail zinciri planı |
| 550 | General model planner | Executive | Model tabanlı genel planlayıcı |
| 551 | Dynamic plan generation | Executive | Dinamik plan üretimi |
| 552 | Dynamic step selection | Executive | Dinamik adım seçimi |
| 553 | Parallel steps | Executive | Paralel adımlar |
| 554 | Conditional branches | Executive | Koşullu dallar |
| 555 | Loop steps | Executive | Döngü adımları |
| 556 | Timeout policy | Executive | Zaman aşımı politikası |
| 557 | Recovery from partial failure | Executive | Kısmi arızadan kurtarma |
| 558 | Correct final status | Executive | Doğru nihai durum |
| 559 | No false "4/4 completed" | Executive | Yanlış başarı raporu olmaz |
| 560 | Task reconciliation | Executive | Görev mutabakatı |

## S. CAPABILITY GENESIS

| ID | FEATURE | SUBSYSTEM | INTENDED OUTCOME |
|---|---|---|---|
| 561 | Capability request | Genesis | Yetenek talebi alınır |
| 562 | Catalogue | Genesis | Arayüz kataloğu |
| 563 | Catalogue registration | Genesis | Kataloğa kayıt |
| 564 | Discovery | Genesis | Keşif |
| 565 | Interface inspection | Genesis | Arayüz incelemesi |
| 566 | Adapter generation | Genesis | Adaptör üretimi |
| 567 | Adapter tests | Genesis | Adaptör testleri |
| 568 | Sandbox | Genesis | Kum havuzu |
| 569 | Registration | Genesis | Yetenek kaydı |
| 570 | Activation | Genesis | Etkinleştirme |
| 571 | Versioning | Genesis | Sürümleme |
| 572 | Rollback | Genesis | Geri alma |
| 573 | Production capability use | Genesis | Üretimde kullanım |
| 574 | Genesis REST | Genesis / API | REST yüzeyi |
| 575 | Genesis Voice tool | Genesis / Voice | Sesli araç |
| 576 | Genesis Cockpit UI | Genesis / Web | Arayüz |
| 577 | Model-backed capability generation | Genesis | Model destekli üretim |
| 578 | Restricted app-only generation | Genesis | Kısıtlı üretim kipi |
| 579 | Security gate | Genesis / Security | Güvenlik kapısı |
| 580 | Owner approval | Genesis / Security | Sahip onayı |

## T. SELF-DEVELOPMENT ENGINE

| ID | FEATURE | SUBSYSTEM | INTENDED OUTCOME |
|---|---|---|---|
| 581 | Defect intake | SelfDev | Kusur girişi |
| 582 | Opportunity intake | SelfDev / Evolution | Fırsat girişi |
| 583 | Opportunity → defect bridge | SelfDev | Fırsat kusura dönüşür |
| 584 | Risk classification | SelfDev | Risk sınıflandırması |
| 585 | Promotion class | SelfDev | Terfi sınıfı tüketilir |
| 586 | Real git branch | SelfDev | Gerçek dal |
| 587 | Real git worktree | SelfDev | Gerçek worktree |
| 588 | Codebase analysis | SelfDev | Kod tabanı analizi |
| 589 | Architecture planning | SelfDev | Mimari planlama |
| 590 | Patch generation | SelfDev | Yama üretimi |
| 591 | New module generation | SelfDev | Yeni modül |
| 592 | Regression test generation | SelfDev | Regresyon testi üretimi |
| 593 | Targeted tests | SelfDev | Hedefli testler |
| 594 | Failure analysis | SelfDev | Başarısızlık analizi |
| 595 | Automated fix | SelfDev | Otomatik düzeltme |
| 596 | Retry | SelfDev | Yeniden deneme |
| 597 | Static review | SelfDev | Statik inceleme |
| 598 | Security review | SelfDev / Security | Güvenlik incelemesi |
| 599 | General code review | SelfDev | Bağımsız kod incelemesi |
| 600 | Full quality gate | SelfDev | Tam kalite kapısı |
| 601 | CI trigger | SelfDev / CI | CI tetiklenir |
| 602 | CI result read | SelfDev / CI | CI sonucu okunur |
| 603 | CI failure → code fix loop | SelfDev / CI | CI kırmızısı koda döner |
| 604 | Candidate commit | SelfDev | Aday commit |
| 605 | Candidate evidence | SelfDev | Aday kanıtı |
| 606 | Candidate history | SelfDev | Aday geçmişi |
| 607 | Candidate quarantine | SelfDev | Karantina |
| 608 | Candidate shadow | SelfDev | Gölge çalıştırma |
| 609 | Owner approval | SelfDev / Security | Sahip onayı yüzeyi |
| 610 | Blue/green release | Release | Mavi/yeşil sürüm |
| 611 | Rollback | Release | Geri alma |
| 612 | Last-known-good | Release | Son bilinen iyi |
| 613 | Post-release health | Release | Sürüm sonrası sağlık |
| 614 | Post-release rollback | Release | Sürüm sonrası geri alma |
| 615 | Scheduler | SelfDev | Zamanlayıcı |
| 616 | Attempt budget | SelfDev | Deneme bütçesi |
| 617 | Time budget | SelfDev | Süre bütçesi |
| 618 | Token/model budget | SelfDev | Model bütçesi |
| 619 | Disk budget | SelfDev | Disk bütçesi |
| 620 | Parallel candidate limit | SelfDev | Paralel aday sınırı |
| 621 | SelfDev Cockpit | SelfDev / Web | Arayüz |
| 622 | "Şu bug'ı kendin düzelt" | SelfDev / Intent | Sesle kusur ataması |
| 623 | "Şu özelliği kendine ekle" | SelfDev / Intent | Sesle özellik ataması |
| 624 | No autonomous high-risk promotion | SelfDev / Security | Yüksek risk otomatik terfi etmez |
| 625 | Low-risk AUTO_SAFE option later | SelfDev | Düşük riskli otomatik seçenek (sonra) |

## U. BACKUP / RECOVERY

| ID | FEATURE | SUBSYSTEM | INTENDED OUTCOME |
|---|---|---|---|
| 626 | PostgreSQL backup | Backup | Veritabanı yedeklenir |
| 627 | PostgreSQL role backup | Backup | Roller yedeklenir |
| 628 | MinIO backup | Backup | Nesneler yedeklenir |
| 629 | Config backup | Backup | Yapılandırma yedeklenir |
| 630 | Identity-root backup strategy | Backup / Security | Kimlik kökü stratejisi |
| 631 | Release metadata | Backup / Release | Sürüm meta verisi |
| 632 | Recovery metadata | Backup / Recovery | Kurtarma meta verisi |
| 633 | Encryption | Backup | Şifreleme |
| 634 | Integrity hash | Backup | Bütünlük hash'i |
| 635 | Retention | Backup | Saklama |
| 636 | Daily schedule | Backup | Günlük zamanlama |
| 637 | Weekly restore drill | Backup | Haftalık geri yükleme tatbikatı |
| 638 | Pre-migration backup | Backup / Release | Göç öncesi yedek |
| 639 | Restore script | Recovery | Geri yükleme betiği |
| 640 | PostgreSQL restore | Recovery | Veritabanı geri yüklenir |
| 641 | MinIO restore | Recovery | Nesneler geri yüklenir |
| 642 | Config restore | Recovery | Yapılandırma geri yüklenir |
| 643 | Release restore | Recovery | Sürüm geri yüklenir |
| 644 | Off-host repository | Backup | Host dışı depo |
| 645 | S3-compatible target | Backup | S3 uyumlu hedef |
| 646 | Backup health | Backup / Health | Yedek sağlığı görünür |
| 647 | Backup failure notification | Backup / Notifications | Yedek arızası bildirilir |
| 648 | Restore health | Recovery / Health | Geri yükleme sağlığı |
| 649 | RPO measurement | Recovery | RPO ölçülür |
| 650 | RTO measurement | Recovery | RTO ölçülür |
| 651 | Recovery supervisor | Recovery | Kurtarma denetçisi |
| 652 | Recovery systemd timer | Recovery | Denetçi zamanlayıcısı |
| 653 | Healthy→degraded detection | Recovery | Bozulma tespiti |
| 654 | Automatic rollback | Recovery | Otomatik geri alma |
| 655 | Recovery audit | Recovery | Kurtarma denetim kaydı |

## V. SECURITY / IDENTITY

| ID | FEATURE | SUBSYSTEM | INTENDED OUTCOME |
|---|---|---|---|
| 656 | Owner credential | Identity | Sahip kimlik bilgisi |
| 657 | Session authentication | Identity | Oturum kimlik doğrulaması |
| 658 | Absolute session lifetime | Identity | Mutlak oturum ömrü |
| 659 | Idle session lifetime | Identity | Boşta oturum ömrü |
| 660 | Panic revoke | Identity | Panik iptali |
| 661 | Device enrollment | Identity / Device | Cihaz kaydı |
| 662 | Device revoke | Identity / Device | Cihaz iptali |
| 663 | Device trust | Identity / Device | Cihaz güveni sunucuda |
| 664 | Speaker verification | Voice Identity | Konuşmacı doğrulaması |
| 665 | Speaker embedding | Voice Identity | Konuşmacı gömmesi |
| 666 | Sensitive-operation re-auth | Identity | Hassas işlemde yeniden doğrulama |
| 667 | Secret detection | Security | Sır tespiti |
| 668 | Secret redaction | Security | Sır maskeleme |
| 669 | DPAPI storage | Security | DPAPI saklama |
| 670 | Cloud credential separation | Security | Bulut kimlik ayrımı |
| 671 | Camera permission | Security / Privacy | Kamera izni |
| 672 | Browser permission | Security | Tarayıcı izni |
| 673 | Research permission | Security | Araştırma izni |
| 674 | File mutation permission | Security / Documents | Dosya değiştirme izni |
| 675 | Code promotion permission | Security / SelfDev | Kod terfi izni |
| 676 | Production deployment permission | Security / Release | Üretim dağıtım izni |
| 677 | High-risk owner gate | Security | Yüksek risk sahip kapısı |
| 678 | Permanent delete owner gate | Security | Kalıcı silme sahip kapısı |
| 679 | Security audit ledger | Security | Güvenlik denetim defteri |
| 680 | Security review of generated code | Security / SelfDev | Üretilen kod incelenir |
| 681 | No CAPTCHA bypass | Security | CAPTCHA çözülmez |
| 682 | No DRM bypass | Security | DRM aşılmaz |
| 683 | No secret logging | Security | Sır loglanmaz |
| 684 | No hidden camera | Security / Privacy | Gizli kamera yok |

## W. WEB / COCKPIT / UX

| ID | FEATURE | SUBSYSTEM | INTENDED OUTCOME |
|---|---|---|---|
| 685 | Global navigation | Web | Genel gezinme |
| 686 | Home dashboard | Web | Ana panel |
| 687 | Voice page | Web | Ses sayfası |
| 688 | Core page | Web | Living Core sayfası |
| 689 | Memory page | Web | Bellek sayfası |
| 690 | Tasks page | Web | Görevler sayfası |
| 691 | Research page | Web | Araştırma sayfası |
| 692 | Artifacts page | Web | Artefakt sayfası |
| 693 | Routines page | Web | Rutin sayfası |
| 694 | Alarm page | Web | Alarm sayfası |
| 695 | Device page | Web | Cihaz sayfası |
| 696 | Security page | Web | Güvenlik sayfası |
| 697 | SelfDev page | Web | SelfDev sayfası |
| 698 | Notifications page | Web | Bildirim sayfası |
| 699 | Settings page | Web | Ayarlar sayfası |
| 700 | Feature availability page | Web | Özellik durumu sayfası |
| 701 | "Neler yapabilirsin?" | Web / Voice | Yetenek listesi |
| 702 | Search | Web | Arama |
| 703 | Global command palette | Web | Komut paleti |
| 704 | Turkish error dictionary | Web / API | Türkçe hata sözlüğü |
| 705 | No Python errors shown to owner | Web / API | Ham istisna gösterilmez |
| 706 | Empty-state UX | Web | Boş durum tasarımı |
| 707 | Loading state | Web | Yükleniyor durumu |
| 708 | Retry state | Web | Yeniden dene durumu |
| 709 | Provider-blocked state | Web | Sağlayıcı engelli durumu |
| 710 | Permission-required state | Web | İzin gerekiyor durumu |
| 711 | Owner-action-required state | Web | Sahip eylemi gerekiyor durumu |
| 712 | Feature status badges | Web | Özellik durum rozetleri |
| 713 | PROVEN_REAL badges | Web | Kanıt rozetleri |
| 714 | Cockpit panel cleanup | Web | Ölü panel temizliği |
| 715 | Broken /creative/runs route fix | Web / Creative | Yaratıcı paneli düzelir |
| 716 | PWA navigation | Web | PWA gezinmesi |
| 717 | Dark fullscreen Living Core | Web | Tam ekran karanlık çekirdek |
| 718 | Gold/amber visual identity | Web | Altın/kehribar kimlik |
| 719 | State-driven animation | Web | Duruma bağlı animasyon |
| 720 | Minimal mode | Web | Minimal kip |
| 721 | Cockpit mode | Web | Kokpit kipi |
| 722 | Performance levels | Web | Performans seviyeleri |
| 723 | WebGL fallback | Web | WebGL yedeği |
| 724 | Accessibility | Web | Erişilebilirlik |
| 725 | Keyboard navigation | Web | Klavye gezinmesi |

## X. NATURAL LANGUAGE / INTENT ROUTING

| ID | FEATURE | SUBSYSTEM | INTENDED OUTCOME |
|---|---|---|---|
| 726 | "Saat kaç?" | Intent | Saat sorusu yönlenir |
| 727 | "Bunu hatırla" | Intent / Memory | Hatırlama yönlenir |
| 728 | "Bunu unut" | Intent / Memory | Unutma yönlenir |
| 729 | "Maillerime bak" | Intent / Mail | Posta yönlenir |
| 730 | "Bu hafta ne var" | Intent / Calendar | Takvim yönlenir |
| 731 | "Toplantıyı iptal et" | Intent / Calendar | İptal yönlenir |
| 732 | "Araştırmayı iptal et" | Intent / Research | Araştırma iptali yönlenir |
| 733 | "Sesini kıs" | Intent / Voice | Ses seviyesi yönlenir |
| 734 | "Neler yapabilirsin" | Intent | Yetenek sorusu yönlenir |
| 735 | "Ekran görüntüsü al" | Intent / Operator | Ekran görüntüsü yönlenir |
| 736 | "Dosyayı gönder" | Intent | Yanlış yönlenmez |
| 737 | "Bunu yazdır" | Intent | Yanlış yönlenmez |
| 738 | "Otomatik güncellemeleri kapat" | Intent | Yanlış yönlenmez |
| 739 | Wrong-route negatives | Intent / QA | Negatif örnekler test edilir |
| 740 | Semantic model router | Intent | Model tabanlı yönlendirme |
| 741 | Deterministic safety router | Intent / Security | Kritik fiiller deterministik |
| 742 | Tool registry-driven routing | Intent | Araç kaydından yönlendirme |
| 743 | Intent confidence | Intent | Güven skoru |
| 744 | Ambiguity clarification | Intent | Belirsizlikte soru sorulur |
| 745 | Reference resolution | Intent | "bunu/şunu" çözülür |
| 746 | Turkish paraphrases | Intent | Türkçe eşanlamlılar |
| 747 | ASR-noise variants | Intent | Tanıma gürültüsüne dayanıklılık |
| 748 | Routing corpus expansion | Intent / QA | Külliyat büyür |
| 749 | Route telemetry | Intent | Yönlendirme telemetrisi |
| 750 | Misroute auto-detection | Intent / QA | Yanlış yönlendirme otomatik bulunur |

---

## SPECIAL PRODUCT DECISIONS

- **MULTI-DEVICE / M29: DEFERRED.** Tasarlanmaz, uygulanmaz, öneri listesine girmez.
- Öncelik tek cihazlı sistemi mükemmelleştirmektir.
- Beklenen yüksek değerli temalar: güvenilirlik ve gerçek/sahte sözleşme eşitliği, kişisel
  bellek ve süreklilik, Dijital Operatör olgunluğu, tarayıcıdan bağımsız ses, varlık/ortam
  davranışı, sahibe ulaşılabilirlik, gerçek SelfDev entegrasyonu, genel App Factory,
  UX/keşfedilebilirlik, kurtarma olgunluğu.
- Bu sıralama mekanik olarak dayatılmaz; gerçek depo bağımlılıkları daha iyi bir sıra
  gösteriyorsa o sıra kullanılır (bkz. ROADMAP bağımlılık grafiği).
