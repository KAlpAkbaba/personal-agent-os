/**
 * The Ofis in plain Turkish (the owner, 2026-10-07: "tüm işlemleri yönetici özeti olarak görmek
 * istiyorum ... test kısmında hiçbir şey anlamıyorum ... sesli konuşmayı test ediyorum gibi bir
 * özet yeterli"). Every label the Ofis shows the owner above or under a seat comes from here: a
 * test job is what it tests, never its family slug or job id; a breaking point is one sentence
 * from its numbers; a software seat is its card's summary or the head of its title. The
 * technical text stays in the seat's detail (click) for whoever wants it. Pure functions.
 */

/**
 * A scenario's id or family (scripts/testteam/scenarios/*.json, the test lead's plan) -> what
 * the job tests, in the owner's words. A family under scripts/testteam/scenarios/ without a
 * sentence here fails office-plain.test.tsx.
 */
export const FAMILY_PLAIN_TR: Readonly<Record<string, string>> = {
  saglik: "Sistemin yoğun yükte ayakta kalmasını test ediyor",
  health: "Sistemin yoğun yükte ayakta kalmasını test ediyor",
  nobet: "Nöbetleri test ediyor",
  watches: "Nöbetleri test ediyor",
  "alarm-saat-ifadeleri": "Alarm kurarken söylenen saatleri test ediyor",
  alarm: "Alarmları test ediyor",
  "konusma-devami": "Konuşmanın kaldığı yerden sürmesini test ediyor",
  "dil-dayanikliligi": "Bozuk ve eksik Türkçe cümleleri anlamayı test ediyor",
  "yanlis-duyulan": "Yanlış duyulan sözlerin listesini test ediyor",
  misheard: "Yanlış duyulan sözlerin listesini test ediyor",
  "ekilmis-hata": "Hata bulunca düzeltme yolunun işlediğini test ediyor",
  "planted-fail": "Hata bulunca düzeltme yolunun işlediğini test ediyor",
  "ev-stoku": "Evdeki stok listesini test ediyor",
};

export const UNKNOWN_FAMILY_TR = "Yeni bir özelliği test ediyor";

/** A test job as the board writes it: "<family> (<job id>)", optionally ": <summary_tr>". */
export type ParsedJob = {
  family: string;
  id: string | null;
  summary: string | null;
};

const JOB_TEXT = /^([^\s(:]+)\s*(?:\(([^)]*)\))?\s*(?::\s*(.+))?$/;

export function parseJob(job: string): ParsedJob {
  const m = JOB_TEXT.exec(job.trim());
  if (!m) return { family: job.trim(), id: null, summary: null };
  return {
    family: m[1],
    id: m[2]?.trim() || null,
    summary: m[3]?.trim() || null,
  };
}

/** The label above a test seat: the job's own summary_tr, else its family's sentence. */
export function plainJobLabel(job: string): string {
  const { family, summary } = parseJob(job);
  if (summary) return summary;
  return FAMILY_PLAIN_TR[family.toLowerCase()] ?? UNKNOWN_FAMILY_TR;
}

// The accusative after a number ("5'i", "2'yi", "40'ı"), from the word the number is read with.
const ONES = ["", "'i", "'yi", "'ü", "'ü", "'i", "'yı", "'yi", "'i", "'u"];
const TENS = ["", "'u", "'yi", "'u", "'ı", "'yi", "'ı", "'i", "'i", "'ı"];

export function accusativeOfNumber(n: number): string {
  const v = Math.abs(Math.trunc(n));
  if (v === 0) return `${n}'ı`;
  if (v % 10 !== 0) return `${n}${ONES[v % 10]}`;
  if (v % 100 !== 0) return `${n}${TENS[(v / 10) % 10]}`;
  if (v % 1000 !== 0) return `${n}'ü`; // yüz
  if (v % 1_000_000 !== 0) return `${n}'i`; // bin
  return `${n}'u`; // milyon
}

const LOAD = /yük\s+(\d+)/;
const ERRORS = /(\d+)\s+hata\s*\/\s*(\d+)/;
const P95 = /p95\s+(\d+)\s*ms/;
const NOTHING_BROKE = /kırılma bulunmadı/;
const ADVISOR_HEAD = /^Danışman'a,\s*(?:test turu\s+[^:]*:\s*)?/;

/**
 * A breaking point as one plain sentence from its numbers:
 * "Aynı anda 32 istekte cevap 13 saniyeye çıkıyor; hata yok, yavaşlıyor", or with errors
 * "...; 32 istekten 5'i hata veriyor". Text without numbers keeps its first sentence.
 */
export function plainBreaking(text: string): string {
  const raw = text.trim();
  if (NOTHING_BROKE.test(raw)) return "Bu turda sistem zorlanmadı, kırılma bulunmadı";
  const load = LOAD.exec(raw);
  if (!load) return firstSentence(raw.replace(ADVISOR_HEAD, "").replace(/^kopma:\s*/, ""));
  const parts: string[] = [];
  const p95 = P95.exec(raw);
  if (p95) {
    const ms = Number(p95[1]);
    parts.push(ms < 1000 ? "cevap 1 saniyenin altında kalıyor" : `cevap ${Math.round(ms / 1000)} saniyeye çıkıyor`);
  } else {
    parts.push("zorlanıyor");
  }
  const errors = ERRORS.exec(raw);
  let tail = "";
  if (errors) {
    const failed = Number(errors[1]);
    const total = Number(errors[2]);
    tail = failed > 0 ? `; ${total} istekten ${accusativeOfNumber(failed)} hata veriyor` : "; hata yok, yavaşlıyor";
  }
  return `Aynı anda ${load[1]} istekte ${parts.join(" ")}${tail}`;
}

function firstSentence(text: string): string {
  const cut = /^(.+?[.!?])(\s|$)/.exec(text);
  return (cut ? cut[1] : text).trim();
}

/**
 * A software seat's label: the card's summary_tr when it has one, else its Turkish title cut
 * at the first ':' or '(' to one short sentence (the whole title when the cut leaves nothing).
 */
export function plainSoftwareLabel(title: string, summaryTr?: string | null): string {
  if (summaryTr && summaryTr.trim()) return summaryTr.trim();
  const cut = title.split(/[:(]/, 1)[0].trim();
  return cut || title.trim();
}
