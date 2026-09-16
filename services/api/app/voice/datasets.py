"""Turkish voice evaluation sets (VOICE_SPEC §8/§9).

A compact, representative, deterministic corpus used to prove the benchmark
harness runs offline and compares >= 2 providers. The categories mirror
VOICE_SPEC §8 (short / technical / long / number-date-currency / mixed TR-EN /
table narration) but at reduced counts.

The FULL owner A/B set (50 short + 30 technical + 20 long + 20 number/date +
20 mixed + 10 table) and blind human scoring require OWNER ACTION (real provider
keys + the owner listening); those larger sets and the listening loop are out of
scope for the deterministic gate and are documented as owner-gated in the report.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TTSCase:
    case_id: str
    category: str
    text: str


@dataclass(frozen=True, slots=True)
class STTCase:
    case_id: str
    category: str
    reference: str  # ground-truth transcript (label)


# ----------------------------------------------------------------- TTS eval set

TTS_CASES: tuple[TTSCase, ...] = (
    TTSCase("short-1", "short", "Bugün hava çok güzel."),
    TTSCase("short-2", "short", "Toplantı saat üçte başlıyor."),
    TTSCase("short-3", "short", "Lütfen raporu bana gönder."),
    TTSCase(
        "technical-1", "technical", "PostgreSQL veritabanında pgvector uzantısını etkinleştir."
    ),
    TTSCase("technical-2", "technical", "API isteği zaman aşımına uğradı ve yeniden denendi."),
    TTSCase("number-1", "number_date_currency", "Toplam tutar ₺1.250.000 olarak hesaplandı."),
    TTSCase("number-2", "number_date_currency", "Rapor 31.08.2026 tarihinde yayımlandı."),
    TTSCase("number-3", "number_date_currency", "Büyüme oranı %17,2 olarak açıklandı."),
    TTSCase(
        "mixed-1",
        "mixed_tr_en",
        "Deployment sırasında container image build aşaması başarısız oldu.",
    ),
    TTSCase(
        "mixed-2", "mixed_tr_en", "Bu feature flag production ortamında henüz enable edilmedi."
    ),
    TTSCase(
        "long-1",
        "long",
        "Yapay zekâ ajanları, karmaşık görevleri küçük adımlara bölerek "
        "planlar, araçları çağırır ve sonuçları değerlendirir. Bu döngü, "
        "istenen sonuca ulaşılana kadar tekrar eder.",
    ),
    TTSCase(
        "table-1",
        "table",
        "Tabloda üç bölge var: Kuzey bölgesi yüzde kırk, güney bölgesi yüzde "
        "otuz beş, doğu bölgesi yüzde yirmi beş pay aldı.",
    ),
)


# ----------------------------------------------------------------- STT eval set
# References are labelled transcripts; the benchmark synthesizes deterministic
# audio from each reference (offline) and measures how well providers recover it.

STT_CASES: tuple[STTCase, ...] = (
    STTCase("stt-short-1", "short", "merhaba nasılsın bugün"),
    STTCase("stt-short-2", "short", "raporu bana gönder lütfen"),
    STTCase("stt-cmd-1", "command", "ikinci maddeye geç"),
    STTCase("stt-cmd-2", "command", "burayı tekrar oku"),
    STTCase("stt-technical-1", "technical", "veritabanı bağlantısı zaman aşımına uğradı"),
    STTCase("stt-mixed-1", "mixed_tr_en", "container image build başarısız oldu"),
    STTCase("stt-noise-1", "noisy", "arka planda gürültü var ama komut anlaşıldı"),
)


# ------------------------------------------------------- M12 realtime eval sets
# ACCEPTANCE_TESTS §M12: mixed Turkish/English terminology benchmarked on at
# least this list, Turkish characters/phonetics, and a real hesitation set for
# the false-barge rate. These are the machine-readable half of
# docs/VOICE_TURKISH_EVAL_SET.md §M12; scoring them for real is owner-gated.

M12_TERMINOLOGY: tuple[str, ...] = (
    "PagentOS",
    "Tailscale",
    "Hetzner",
    "PostgreSQL",
    "PowerShell",
    "FortiGate",
    "OpenAI",
    "Claude",
    "Windows",
    "Kubernetes",
    "Redis",
    "Temporal",
)

TURKISH_PHONETICS: tuple[str, ...] = ("ı", "İ", "ğ", "ş", "ç", "ö", "ü")

M12_TERMINOLOGY_CASES: tuple[TTSCase, ...] = (
    TTSCase(
        "m12-term-1",
        "mixed_tr_en",
        "PagentOS, Tailscale üzerinden Hetzner'daki PostgreSQL veritabanına bağlanıyor.",
    ),
    TTSCase(
        "m12-term-2",
        "mixed_tr_en",
        "PowerShell betiği FortiGate yapılandırmasını Windows makinesinden okudu.",
    ),
    TTSCase(
        "m12-term-3", "mixed_tr_en", "OpenAI ve Claude modellerini aynı görevde karşılaştırdım."
    ),
    TTSCase(
        "m12-term-4",
        "mixed_tr_en",
        "Kubernetes kümesinde Redis önbellek, Temporal ise iş akışlarını yönetiyor.",
    ),
)

TURKISH_PHONETICS_CASES: tuple[TTSCase, ...] = (
    TTSCase("m12-phon-1", "phonetics", "Işık ılık, İstanbul'da ıslık çaldı."),
    TTSCase("m12-phon-2", "phonetics", "Ağaç yağmurda eğildi, dağ sisle örtüldü."),
    TTSCase("m12-phon-3", "phonetics", "Şişli'de şaşırtıcı bir çarşı gördüm."),
    TTSCase("m12-phon-4", "phonetics", "Çocuklar çiçekli bahçede koşuyor."),
    TTSCase("m12-phon-5", "phonetics", "Öğle vakti gölde ördekler yüzüyordu."),
    TTSCase("m12-phon-6", "phonetics", "Üzüm, üç gün üst üste güneş gördü."),
)

#: Turkish hesitation set (spec §5): natural pauses/fillers that a semantic
#: end-of-turn MUST NOT treat as the end of the owner's turn. ``cut_after``
#: marks where a naive silence detector would cut; the utterance continues.
HESITATION_CASES: tuple[STTCase, ...] = (
    STTCase("m12-hes-1", "hesitation", "şey... raporun ikinci bölümünü bir daha oku"),
    STTCase("m12-hes-2", "hesitation", "yani... aslında sadece OpenAI kısmına bak"),
    STTCase("m12-hes-3", "hesitation", "hani şu... Tailscale ayarını değiştirdiğimiz gün"),
    STTCase("m12-hes-4", "hesitation", "ııı... toplantıyı yarına al"),
    STTCase("m12-hes-5", "hesitation", "bir de... eee... özet geç ama maliyet kısmını atla"),
    STTCase("m12-hes-6", "hesitation", "PostgreSQL'e... yani veritabanına bakar mısın"),
)

M12_INTENT_UTTERANCES: tuple[STTCase, ...] = (
    STTCase("m12-int-stop", "command", "dur"),
    STTCase("m12-int-resume", "command", "devam"),
    STTCase("m12-int-repeat", "command", "tekrar oku"),
    STTCase("m12-int-item", "command", "ikinci maddeyi tekrar oku"),
    STTCase("m12-int-slower", "command", "biraz daha yavaş"),
    STTCase("m12-int-faster", "command", "biraz daha hızlı"),
    STTCase("m12-int-summary", "command", "özet geç"),
    STTCase("m12-int-detail", "command", "detaya gir"),
    STTCase("m12-int-skip", "command", "burayı atla"),
)


__all__ = [
    "HESITATION_CASES",
    "M12_INTENT_UTTERANCES",
    "M12_TERMINOLOGY",
    "M12_TERMINOLOGY_CASES",
    "STTCase",
    "STT_CASES",
    "TTSCase",
    "TTS_CASES",
    "TURKISH_PHONETICS",
    "TURKISH_PHONETICS_CASES",
]
