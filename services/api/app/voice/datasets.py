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
    TTSCase("technical-1", "technical",
            "PostgreSQL veritabanında pgvector uzantısını etkinleştir."),
    TTSCase("technical-2", "technical",
            "API isteği zaman aşımına uğradı ve yeniden denendi."),
    TTSCase("number-1", "number_date_currency", "Toplam tutar ₺1.250.000 olarak hesaplandı."),
    TTSCase("number-2", "number_date_currency", "Rapor 31.08.2026 tarihinde yayımlandı."),
    TTSCase("number-3", "number_date_currency", "Büyüme oranı %17,2 olarak açıklandı."),
    TTSCase("mixed-1", "mixed_tr_en",
            "Deployment sırasında container image build aşaması başarısız oldu."),
    TTSCase("mixed-2", "mixed_tr_en",
            "Bu feature flag production ortamında henüz enable edilmedi."),
    TTSCase("long-1", "long",
            "Yapay zekâ ajanları, karmaşık görevleri küçük adımlara bölerek "
            "planlar, araçları çağırır ve sonuçları değerlendirir. Bu döngü, "
            "istenen sonuca ulaşılana kadar tekrar eder."),
    TTSCase("table-1", "table",
            "Tabloda üç bölge var: Kuzey bölgesi yüzde kırk, güney bölgesi yüzde "
            "otuz beş, doğu bölgesi yüzde yirmi beş pay aldı."),
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


__all__ = ["STTCase", "STT_CASES", "TTSCase", "TTS_CASES"]
