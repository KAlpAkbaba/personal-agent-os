# Personal Agent OS — Claude Code ile Başlangıç

Tarih: 2026-08-31
Paket: Autonomous Build Package v1

Bu klasör, tek kullanıcıya ait, voice-first, bulut + yerel cihaz hibrit mimarisine sahip ve zamanla kendi kendini onarıp genişletebilen Personal Agent OS projesini Claude Code'a devretmek için hazırlanmıştır.

## En önemli karar

Geliştirme **yerel Windows bilgisayarda Claude Code ile** başlayacak. Ürünün kendisi ise hibrit çalışacak:

- **Cloud Core:** sürekli açık beyin, görevler, hafıza, artifact, zamanlama, self-healing/evolution kontrol düzlemi.
- **Windows Device Agent:** sahibin bilgisayarını ve masaüstü uygulamalarını kontrol eden yerel yürütücü.
- **Browser Agent:** mevcut Chrome/Edge oturumunu gerektiğinde kullanabilen yürütücü.
- **Voice Core:** STT, owner-speaker verification, realtime konuşma ve yüksek kaliteli Türkçe TTS/narration.
- **Mobile/Web:** cloud core'a bağlanan arayüzler.

Claude Code modelinin kendisi uzakta çalıştığı için **Claude Code ile kod yazmak için ekran kartı gerekmez**. GPU yalnızca ileride local STT/TTS, vision veya local LLM çalıştırmak istersek önem kazanır.

---

# 1. Ekrandaki Claude Code ile ne yapacaksın?

Ekran görüntündeki **Code** modunu kullan.

1. Bu ZIP'i örneğin aşağıdaki klasöre aç:
   `C:\AI\personal-agent-os`
2. Claude Code'da **Select repo...** seç.
3. `C:\AI\personal-agent-os` klasörünü seç.
4. Alt kısımdaki permission mode mümkünse **Auto** olarak kalsın.
5. Workspace trust/izin ekranı gelirse yalnızca bu proje klasörü için onayla.
6. `01_CLAUDE_START_PROMPT.txt` dosyasının tamamını Claude Code'a tek mesaj olarak ver.
7. Bundan sonra normal teknik seçimlerde Claude'un senden soru sormaması gerekir. Sadece `OWNER_ACTIONS_MINIMAL.md` içindeki gerçekten insan gerektiren adımlar için seni durdurabilir.

## İlk oturumda beklenen sonuç

Claude önce kod yazmaya saldırmamalı. Şu sırada ilerlemeli:

1. Tüm şartnameyi okumalı.
2. Bilgisayarın preflight envanterini çıkarmalı.
3. `docs/LOCAL_ENV_REPORT.md` üretmeli.
4. Eksik geliştirme araçlarını mümkün olduğunca kendisi kurmalı.
5. Git repository'yi hazırlamalı.
6. M-1 kabul kriterlerini bitirmeli.
7. M0 Foundation'a geçmeli.
8. Her milestone tamamlandığında testleri çalıştırmalı ve `state/BUILD_STATE.json` dosyasını güncellemelidir.

---

# 2. Geliştirme makinesi için önerilen yapı

## Claude Code + cloud voice kullanacaksan

Minimumdan ziyade rahat geliştirme için:

- Windows 11 64-bit
- 8 çekirdek sınıfı modern CPU veya üzeri
- 32 GB RAM minimum, **64 GB tercih**
- 1 TB NVMe minimum, 2 TB tercih
- GPU: **zorunlu değil**
- Docker Desktop + WSL2
- Git for Windows
- PowerShell 7
- .NET SDK
- Python
- Node.js LTS

## Local STT/TTS de kullanacaksan

- NVIDIA GPU tercih
- 8 GB VRAM ile local Whisper sınıfı STT mümkün
- 12 GB VRAM rahat başlangıç
- **16 GB+ VRAM önerilen**
- 24/32 GB VRAM: local voice + vision + daha büyük local modeller için ideal

İlk sürümde GPU satın almak zorunda değilsin. Önce cloud TTS/STT ile kaliteyi oturtup sonra local fallback eklemek daha doğrudur.

Detay: `docs/LOCAL_DEVELOPMENT_AND_HARDWARE.md`

---

# 3. Bulutta ne kullanılacak?

Önerilen ilk production topolojisi:

- **GitHub Private Repository** — source of truth
- **GitHub Actions** — build/test/release
- **GitHub Container Registry (GHCR)** — container image'ları
- **Hetzner Cloud Nuremberg (NBG1)** — ana Linux cloud VM
- **Ubuntu LTS** — cloud işletim sistemi
- **Docker Compose** — ilk sürüm orchestration; Kubernetes yok
- **Tailscale** — telefon, PC ve cloud arasında private overlay network
- **Hetzner Object Storage NBG1** — artifact/backuplar için S3-compatible storage
- **PostgreSQL + pgvector** — ana veri ve memory index
- **Redis** — cache/ephemeral state
- **Temporal** — durable workflow ve uzun süren agent görevleri
- **OpenTelemetry + Prometheus + Grafana + Loki** — health, metric, log ve self-healing telemetry
- **Anthropic Claude Agent SDK** — ilk Evolution/Software Engineer backend'i
- **Playwright MCP** — browser kontrol katmanı
- **Microsoft UFO/UFO3 yaklaşımı** — Windows multi-application/device agent referansı ve entegrasyon adayı
- **Voice Provider Router** — ElevenLabs / Azure Speech / OpenAI Realtime arasında benchmark ile routing

İlk cloud VM için 8 vCPU / 16 GB RAM sınıfı rahat başlangıçtır. GPU cloud sunucusu gerekmiyor.

Detay: `docs/CLOUD_INFRASTRUCTURE.md`

---

# 4. Manuel müdahale hangi noktalarda kalacak?

Normal geliştirme, test, bug fix, release ve rollback mümkün olduğunca otomatik olacaktır.

İnsan gerektirebilecek tek seferlik konular:

- Windows UAC gerektiren kurulum
- Gerekirse restart
- GitHub/Hetzner/Tailscale gibi hesaba ilk login
- API key veya ödeme yöntemi oluşturma
- Mikrofon/kamera/notification OS izinleri
- Voice enrollment sırasında birkaç dakikalık ses örneği
- Şirket sunucularının ilk kez `authorized asset` olarak enrollment edilmesi

Bunların dışında Claude routine kararlar için seni durdurmamalı.

---

# 5. Önemli geliştirme prensibi

Claude'a hiçbir zaman “hepsini tek seferde tamamla” mantığı verilmemiştir.

Akış:

`M-1 Environment -> M0 Foundation -> M1 Cloud/Device -> M2 Browser -> M3 Research/Artifacts -> M4 Voice -> M5 Memory -> M6 Self-Healing -> M7 Evolution -> M8 Authorized Security -> M9 Native Mobile`

Her milestone kendi deterministic acceptance testlerinden geçmeden sonraki milestone tamamlanmış sayılmaz.

---

# 6. İlk başarı senaryosu

İlk büyük dikey kesit şu komutu uçtan uca çalıştırmalıdır:

> “Son üç günde yapay zekâ ajanlarıyla ilgili önemli gelişmeleri araştır.”

Sonuç:

1. Task oluşturulur.
2. Cloud araştırması yapılır; gerekirse browser/device devreye girer.
3. Kaynaklar doğrulanır.
4. Yönetici özeti + detaylı rapor hazırlanır.
5. Artifact cloud'da kaydedilir.
6. Bilgisayardan verilmişse bilgisayara da senkronlanabilir.
7. Agent sadece “Rapor hazır.” der ve bekler.
8. “Oku” denince Türkçe TTS ile yönetici özeti okunur.
9. “Devam” denince detaylar okunur.
10. “Gönder” denince PDF/DOCX gibi format telefon/web arayüzünde sunulur.
11. “Bilgisayarda aç” denince Windows agent ilgili dosyayı açar.

Bu davranış `Task -> Artifact -> Presentation` temel modeliyle gerçekleştirilir.
