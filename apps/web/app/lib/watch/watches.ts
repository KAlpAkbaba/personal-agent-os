/**
 * The client for `/v1/watches` (ADR: watch-page) and the Turkish the 'Nöbetler' rows are
 * made of.
 *
 * It sends what the owner pressed and reports what the Cloud Core answered; every rule (a
 * public http(s) address, 1..168 hours, at most 20 watches) is the server's, and a refusal is
 * shown in the server's own sentence (`WatchRefused.reason_tr`), never turned into a success.
 * Nothing here throws into the page: a failed request is a value, so the rest of /routines
 * renders whatever this section's fate.
 */

import { apiFetch } from "../session";

export const WATCHES_PATH = "/v1/watches";

/** The contract's `WatchView`, as `app.watch.routes` serialises it. */
export type Watch = {
  id: string;
  label: string;
  url: string;
  condition: string;
  every_hours: number;
  selector: string | null;
  created_at: string;
  last_read_at: string | null;
  last_outcome: string | null;
  last_value: number | null;
  consecutive_failures: number;
  /** Not in the contract yet: the last unreadable reading's own reason, said when present. */
  last_reason?: string | null;
};

/** What the add form holds: text boxes, so the hours are text until they are sent. */
export type WatchDraft = {
  url: string;
  label: string;
  kind: ConditionKind;
  value: string;
  every_hours: string;
};

export type ConditionKind = "changed" | "contains" | "number_below" | "number_above";

export const CONDITION_KINDS: { kind: ConditionKind; label: string }[] = [
  { kind: "changed", label: "değişince" },
  { kind: "contains", label: "şu metin geçince" },
  { kind: "number_below", label: "sayı şunun altına inerse" },
  { kind: "number_above", label: "sayı şunu geçerse" },
];

export const EMPTY_DRAFT: WatchDraft = {
  url: "",
  label: "",
  kind: "changed",
  value: "",
  every_hours: "6",
};

export const EMPTY_SENTENCE = 'Henüz nöbet yok. Sesle: "şu değişince bana söyle".';

export type Refusal = { ok: false; code: string; message: string };

// ------------------------------------------------------------------ the requests

async function refusal(response: Response): Promise<Refusal> {
  try {
    const body = (await response.json()) as { detail?: unknown };
    const detail = body.detail;
    if (typeof detail === "string") {
      return { ok: false, code: `http_${response.status}`, message: detail };
    }
    const fields = (detail ?? {}) as { code?: unknown; message?: unknown };
    return {
      ok: false,
      code: typeof fields.code === "string" ? fields.code : `http_${response.status}`,
      message: typeof fields.message === "string" ? fields.message : `HTTP ${response.status}`,
    };
  } catch {
    return { ok: false, code: `http_${response.status}`, message: `HTTP ${response.status}` };
  }
}

async function send(path: string, init?: RequestInit): Promise<{ ok: true; body: unknown } | Refusal> {
  let response: Response;
  try {
    response = await apiFetch(path, init);
  } catch {
    return { ok: false, code: "unreachable", message: "Cloud Core'a ulaşılamadı." };
  }
  if (!response.ok) return refusal(response);
  try {
    return { ok: true, body: await response.json() };
  } catch {
    return { ok: false, code: "body_invalid", message: "Cloud Core'un yanıtı okunamadı." };
  }
}

function watchPath(id: string): string {
  return `${WATCHES_PATH}/${encodeURIComponent(id)}`;
}

function deletedCount(body: unknown): number {
  const deleted = (body as { deleted?: unknown } | null)?.deleted;
  return typeof deleted === "number" ? deleted : 0;
}

export async function fetchWatches(): Promise<{ ok: true; items: Watch[] } | Refusal> {
  const result = await send(WATCHES_PATH);
  if (!result.ok) return result;
  const items = (result.body as { items?: unknown } | null)?.items;
  return { ok: true, items: Array.isArray(items) ? (items as Watch[]) : [] };
}

/** The one line a failed list is: "Nöbetler okunamadı: <the server's sentence>". */
export function loadFailedSentence(failure: Refusal): string {
  return `Nöbetler okunamadı: ${failure.message}`;
}

export function buildCondition(kind: ConditionKind, value: string): string {
  return kind === "changed" ? "changed" : `${kind}:${value.trim()}`;
}

/** Add one; the new row joins the list, a refusal stands beside the form. */
export async function addOne(
  items: Watch[],
  draft: WatchDraft,
): Promise<{ items: Watch[]; notice: string | null; refusal: string | null }> {
  const url = draft.url.trim();
  const hours = Number(draft.every_hours.trim());
  const result = await send(WATCHES_PATH, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      url,
      // An empty name is the page's domain, not a refusal the owner has to read.
      label: draft.label.trim() || domainOf(url),
      condition: buildCondition(draft.kind, draft.value),
      every_hours: Number.isFinite(hours) ? hours : null,
    }),
  });
  if (!result.ok) return { items, notice: null, refusal: result.message };
  const added = result.body as Watch;
  return {
    items: [...items, added],
    notice: `“${added.label}” nöbeti kuruldu; ilk okuma birkaç dakika içinde.`,
    refusal: null,
  };
}

/**
 * Kaldır: DELETE /v1/watches/{id}; the row leaves only when the Cloud Core says it is gone -
 * deleted now, or `not_found` (forgotten by voice, removed in another tab).
 */
export async function removeOne(
  items: Watch[],
  id: string,
): Promise<{ items: Watch[]; notice: string }> {
  const result = await send(watchPath(id), { method: "DELETE" });
  if (!result.ok && result.code === "not_found") {
    return { items: items.filter((row) => row.id !== id), notice: result.message };
  }
  if (!result.ok) return { items, notice: result.message };
  const gone = items.find((row) => row.id === id);
  return {
    items: items.filter((row) => row.id !== id),
    notice: gone ? `“${gone.label}” nöbeti kaldırıldı.` : "Nöbet kaldırıldı.",
  };
}

/** 'Hepsini unut': ONE request, every watch and every reading gone. */
export async function forgetEverything(
  items: Watch[] = [],
): Promise<{ items: Watch[]; notice: string }> {
  const result = await send(WATCHES_PATH, { method: "DELETE" });
  if (!result.ok) return { items, notice: result.message };
  return {
    items: [],
    notice: `${deletedCount(result.body)} nöbet unutuldu; okumalarıyla birlikte silindi.`,
  };
}

// ------------------------------------------------------------------ the Turkish

/** The page's domain without `www.`; an address that does not parse is shown as it is. */
export function domainOf(url: string): string {
  try {
    return new URL(url.trim()).hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
}

export function intervalSentence(hours: number): string {
  if (hours === 1) return "saatte bir";
  if (hours === 24) return "günde bir";
  if (hours === 168) return "haftada bir";
  return `${hours} saatte bir`;
}

/** The server's own number rules (`app.watch.compare._token_value`), so both read alike. */
function conditionNumber(raw: string): number | null {
  if (/^\d+$/.test(raw)) return Number(raw);
  if (/^\d{1,3}(?:\.\d{3})+,\d+$/.test(raw)) {
    const [whole, fraction] = raw.split(",");
    return Number(`${whole.replace(/\./g, "")}.${fraction}`);
  }
  if (/^\d+,\d+$/.test(raw)) return Number(raw.replace(",", "."));
  if (/^\d{1,3}(?:\.\d{3})+$/.test(raw)) return Number(raw.replace(/\./g, ""));
  if (/^\d+\.\d+$/.test(raw)) return Number(raw); // the API's own decimal ('19.99')
  return null;
}

function trNumber(value: number): string {
  return new Intl.NumberFormat("tr-TR", { maximumFractionDigits: 6 }).format(value);
}

// The genitive of the LAST spoken word: 'yirmi bin' -> bin'in, 'yüz elli' -> elli'nin.
const UNIT_GENITIVE = ["ın", "in", "nin", "ün", "ün", "in", "nın", "nin", "in", "un"];
const TEN_GENITIVE = ["", "un", "nin", "un", "ın", "nin", "ın", "in", "in", "ın"];

function wholeGenitive(n: number): string {
  if (n === 0) return "ın"; // sıfır
  if (n % 10 !== 0) return UNIT_GENITIVE[n % 10];
  if (Math.floor(n / 10) % 10 !== 0) return TEN_GENITIVE[Math.floor(n / 10) % 10];
  if (Math.floor(n / 100) % 10 !== 0) return "ün"; // yüz
  if (Math.floor(n / 1000) % 1000 !== 0) return "in"; // bin
  if (Math.floor(n / 1_000_000) % 1000 !== 0) return "un"; // milyon
  return "ın"; // milyar
}

function genitive(written: string): string {
  const [whole, fraction] = written.replace(/\./g, "").split(",");
  // '19,99' is read 'on dokuz virgül doksan dokuz': the fraction's last word decides.
  const last = fraction !== undefined ? Number(fraction) : Number(whole);
  return wholeGenitive(Number.isSafeInteger(last) ? last : 0);
}

function accusative(suffix: string): string {
  return suffix.replace(/n$/, "").replace(/^n/, "y");
}

/** `number_below:20000` -> "20.000'in altına inerse"; an unknown condition stays as it is. */
export function conditionSentence(condition: string): string {
  if (condition === "changed") return "değişince";
  const [kind, ...rest] = condition.split(":");
  const value = rest.join(":").trim();
  if (kind === "contains" && value) return `'${value}' geçince`;
  if ((kind === "number_below" || kind === "number_above") && value) {
    const number = conditionNumber(value);
    if (number === null) return condition;
    const written = trNumber(number);
    const suffix = genitive(written);
    return kind === "number_below"
      ? `${written}'${suffix} altına inerse`
      : `${written}'${accusative(suffix)} geçerse`;
  }
  return condition;
}

const OUTCOME: Record<string, string> = {
  same: "değişmemiş",
  changed: "değişmiş",
  condition_met: "koşul gerçekleşti",
  unreadable: "okunamadı",
};

const UNREADABLE_WHY = "sayfa açılmadı ya da aranan yer bulunamadı";

function when(iso: string, timeZone?: string): string {
  const at = new Date(iso);
  if (Number.isNaN(at.getTime())) return iso;
  const parts = new Intl.DateTimeFormat("tr-TR", {
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
    timeZone,
  }).formatToParts(at);
  const part = (type: string) => parts.find((p) => p.type === type)?.value ?? "";
  return `${part("day")} ${part("month")} ${part("hour")}:${part("minute")}`;
}

/** "Son okuma 4 Eki 12:30: değişmemiş (21.499)." - an unreadable one with why. */
export function lastReadingSentence(watch: Watch, timeZone?: string): string {
  if (!watch.last_read_at) return "Henüz okunmadı; ilk okuma birkaç dakika içinde.";
  const head = `Son okuma ${when(watch.last_read_at, timeZone)}`;
  const outcome = watch.last_outcome ?? "";
  if (outcome === "unreadable") {
    const times = watch.consecutive_failures > 1 ? `, art arda ${watch.consecutive_failures} kez` : "";
    return `${head}: okunamadı${times} (${watch.last_reason || UNREADABLE_WHY}).`;
  }
  const said = OUTCOME[outcome] ?? (outcome || "sonuç bilinmiyor");
  const value = watch.last_value !== null ? ` (${trNumber(watch.last_value)})` : "";
  return `${head}: ${said}${value}.`;
}
