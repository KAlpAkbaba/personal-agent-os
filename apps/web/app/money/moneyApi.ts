/**
 * The client for `/v1/money` (money-ledger): JARVIS's own money ledger - never the bank.
 *
 * It sends what the owner pressed and reports what the Cloud Core answered; every rule (what a
 * bank mail says, when a spend is tentative, the Turkish amounts) is the server's, and a
 * refusal is surfaced with the server's own sentence, never turned into a success. Nothing
 * here moves money.
 */

import { apiFetch } from "../lib/session";

export const MONEY_PATH = "/v1/money";

/** One row of `money_entries` as `/v1/money` returns it (routes.py `_entry`). */
export type MoneyEntry = {
  id: string;
  direction: "out" | "in";
  amount_kurus: number;
  status: "tentative" | "confirmed" | "cancelled";
  source: string;
  method: "card" | "cash" | null;
  category: string | null;
  description: string;
  bank: string | null;
  occurred_at: string | null;
  confirmed_at: string | null;
};

export type MoneyQuestion = {
  id: string;
  amount_kurus: number | null;
  reason: string;
  asked_at: string | null;
  answer: "yes" | "no" | null;
  speech: string;
};

export type Money = {
  balances: { bank: string; balance_kurus: number; as_of: string | null }[];
  /** The sentence the voice says for "hesabımda ne kadar var" (with the mail's time). */
  balance_speech: string;
  month: { total_kurus: number; count: number; tentative: number; cash_kurus: number; speech: string };
  entries: MoneyEntry[];
  questions: MoneyQuestion[];
  categories: string[];
};

export type Refusal = { ok: false; code: string; message: string };
export type Done = { ok: true; message: string };

/** Kuruş -> "1.234,56 TL" / "750 TL", the way the bank writes it and the voice says it. */
export function amountText(kurus: number): string {
  const sign = kurus < 0 ? "-" : "";
  const abs = Math.abs(Math.trunc(kurus));
  const lira = Math.floor(abs / 100);
  const rest = abs % 100;
  const grouped = String(lira).replace(/\B(?=(\d{3})+(?!\d))/g, ".");
  return rest ? `${sign}${grouped},${String(rest).padStart(2, "0")} TL` : `${sign}${grouped} TL`;
}

async function refusal(response: Response): Promise<Refusal> {
  try {
    const body = (await response.json()) as { detail?: { code?: string; message?: string } };
    return {
      ok: false,
      code: body.detail?.code ?? `http_${response.status}`,
      message: body.detail?.message ?? `HTTP ${response.status}`,
    };
  } catch {
    return { ok: false, code: `http_${response.status}`, message: `HTTP ${response.status}` };
  }
}

function post(path: string, body: unknown): Promise<Response> {
  return apiFetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export async function fetchMoney(): Promise<{ ok: true; money: Money } | Refusal> {
  const response = await apiFetch(MONEY_PATH);
  if (!response.ok) return refusal(response);
  return { ok: true, money: (await response.json()) as Money };
}

export async function cancelEntry(id: string): Promise<Done | Refusal> {
  const response = await post(`${MONEY_PATH}/entries/${encodeURIComponent(id)}/cancel`, {});
  if (!response.ok) return refusal(response);
  const row = ((await response.json()) as { entry: MoneyEntry }).entry;
  return { ok: true, message: `${amountText(row.amount_kurus)} kaydını geri aldım.` };
}

export async function answerQuestion(
  id: string,
  answer: "yes" | "no",
  amount?: string,
): Promise<Done | Refusal> {
  const trimmed = amount?.trim();
  const response = await post(`${MONEY_PATH}/questions/${encodeURIComponent(id)}/answer`, {
    answer,
    ...(trimmed ? { amount: trimmed } : {}),
  });
  if (!response.ok) return refusal(response);
  const body = (await response.json()) as { entry: MoneyEntry | null };
  return {
    ok: true,
    message: body.entry
      ? `${amountText(body.entry.amount_kurus)} harcamayı deftere yazdım.`
      : "Peki, deftere bir şey yazmadım.",
  };
}

export async function bookCash(amount: string, category: string): Promise<Done | Refusal> {
  const response = await post(`${MONEY_PATH}/cash`, {
    amount: amount.trim(),
    category: category ? category : null,
  });
  if (!response.ok) return refusal(response);
  const row = ((await response.json()) as { entry: MoneyEntry }).entry;
  return { ok: true, message: `${amountText(row.amount_kurus)} nakit harcamayı deftere yazdım.` };
}
