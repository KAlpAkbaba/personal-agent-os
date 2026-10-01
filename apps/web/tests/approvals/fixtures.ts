/**
 * Proposal texts in the shapes the researcher writes (`.claude/agents/researcher.md`), and the
 * approvals views the Onay Merkezi is rendered from. Strings, not files: the page receives
 * `proposal_text` from the Cloud Core and never reads `team/proposals/`.
 */

import type { ApprovalsView, PendingApproval } from "../../app/core/approvals/approvalsApi";

/** The current rule: `## ` headings, a "Faydası — örneklerle" section with three pairs. */
export const WITH_BENEFIT = `# Öneri: Soniox'u Türkçe akışlı STT adayı olarak ÖLÇ

Tarih: 2026-10-01 · Araştırmacı · Durum: awaiting_owner
Roadmap: JARVIS tablosu "doğal konuşma" satırı; sıra 6.

## Ne
Soniox'un akışlı STT'sini mevcut sağlayıcı arayüzünün arkasında BİR aday olarak ekleyip
elimizdeki derlemle ölçmek. Sahibin cümlesi değişmez.

## Faydası — örneklerle
1. Bugün: "ışığı kapat" dediğimde mutfakta "ışığı kapa at" yazılıyor ve komut düşüyor.
   Bununla: aynı cümle gürültüde de doğru yazılır; komut ilk seferde çalışır.
2. Bugün: hangi sağlayıcının daha iyi olduğunu tahmin ediyoruz.
   Bununla: üç sağlayıcı aynı derlemde yan yana ölçülür
   ve tablo raporda durur.
3. Bugün: ölçüm için [fiyat sayfasına](https://soniox.com/pricing) elle bakıyorum.
   Bununla: maliyet ölçümle birlikte gelir.

Kazanç: yanlış anlama oranı derlemde sayıyla görülür.
Kazanmadığımız: TTS kalitesi değişmez.

## Neden şimdi
- Soniox: gerçek zamanlı 0,12 USD/saat ([fiyat](https://soniox.com/pricing),
  [Türkçe](https://soniox.com/speech-to-text/turkish)).
- **Kanıt ince:** bağımsız Türkçe WER karşılaştırması bulamadım.

## Nasıl
- Seam: \`providers.py\`'deki STT sağlayıcı arayüzü.
- Değişmeyen: router, ses hattı.

## Maliyet/risk
- Efor: küçük-orta.

## Deneme notu
Bu başlık kuralda yok; kendi adıyla kalmalı.

## Kanıt planı
PROVEN_PROXY: derlem tablosu. PROVEN_REAL: sahibin 20 cümlesi.

## Karar
Ölçelim mi?
`;

/** Written before the rule (the three ideas of 2026-10-01): no "Faydası" section. */
export const WITHOUT_BENEFIT = `# Öneri: Ev — Home Assistant'ı bağla

Tarih: 2026-10-01 · Araştırmacı

## Ne
Home Assistant'ı salt-okuma ile bağlamak.

## Neden şimdi
- 2026.9 sürümü çıktı.

## Karar
Yapalım mı?
`;

/** The older shape: `**Heading**:` lines instead of `## ` headings. */
export const BOLD_HEADINGS = `# Öneri: Anlatı

**Ne**: Ledger üzerinde tek bir konuşulan anlatı.
İkinci satır aynı paragrafın devamı.

**Faydası — örneklerle**:
- Bugün: "bu hafta ne oldu" diye soruyorum, yanıt yok.
- Bununla: 30 saniyelik özet gelir.

**Neden şimdi**:
- Parçalar mevcut.
- **Kanıt ince:** bu bir liste öğesi, başlık değil.

**Maliyet/risk:** orta.

**Efor**: bu satır bilinen bir bölüm değil, metin olarak kalır.

**Karar**: Yapalım mı?
`;

/** What a proposal must never be able to do to the page. */
export const HOSTILE = `# Öneri: <script>alert("başlık")</script>

## Ne
Metin <script>alert(1)</script> ve <img src=x onerror=alert(2)> içeriyor.
Bağlantı: [tıkla](javascript:void0) ve [veri](data:text/html;base64,PHNjcmlwdD4=).
- liste <b>kalın</b> [x](JaVaScRiPt:void0)
`;

export function approval(over: Partial<PendingApproval> = {}): PendingApproval {
  return {
    task_id: "stt-soniox-olcum",
    title: "Soniox'u ölç",
    gate: "fikir",
    state: "awaiting_owner",
    goal: "Türkçe STT adayını ölçmek",
    acceptance: "",
    proposal: "team/proposals/2026-10-01-stt-soniox-olcum.md",
    proposal_text: WITH_BENEFIT,
    sha: null,
    reports: [],
    updated_at: "2026-10-01T12:00:00Z",
    ...over,
  };
}

export function view(over: Partial<ApprovalsView> = {}): ApprovalsView {
  return {
    approvals: [
      approval(),
      approval({ task_id: "ev-home-assistant", title: "Ev", proposal_text: WITHOUT_BENEFIT }),
    ],
    cycle_report: null,
    cycle_running: false,
    ...over,
  };
}
