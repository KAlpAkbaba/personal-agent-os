/**
 * 'Para defteri' (money-ledger): the view rendered with fixtures, its buttons pressed through
 * the element tree, the real client driven with the session stubbed, and the client's entry
 * fields read against the server's `_entry` so the two halves cannot drift.
 *
 * No DOM here (vitest runs in node): the markup is asserted through react-dom/server.
 */

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { isValidElement, type ReactElement, type ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";

const apiFetch = vi.fn();

vi.mock("../../app/lib/session", () => ({
  API_BASE: "http://core.test:8001",
  apiFetch: (...args: unknown[]) => apiFetch(...args),
  UnauthorizedError: class UnauthorizedError extends Error {},
}));

import MoneyView, { EMPTY_ENTRIES, amountText, type MoneyViewProps } from "../../app/money/MoneyView";
import {
  answerQuestion,
  bookCash,
  cancelEntry,
  fetchMoney,
  type Money,
  type MoneyEntry,
} from "../../app/money/moneyApi";

type Props = Record<string, unknown> & { children?: ReactNode };

function entry(over: Partial<MoneyEntry>): MoneyEntry {
  return {
    id: "00000000-0000-0000-0000-000000000001",
    direction: "out",
    amount_kurus: 75000,
    status: "tentative",
    source: "conversation",
    method: "card",
    category: "giyim",
    description: "Konuşmadan",
    bank: null,
    occurred_at: "2026-10-06T11:00:00+00:00",
    confirmed_at: null,
    ...over,
  };
}

const JACKET = entry({});
const MARKET = entry({
  id: "00000000-0000-0000-0000-000000000002",
  amount_kurus: 123456,
  status: "confirmed",
  source: "bank",
  category: "market",
  description: "MIGROS ATASEHIR",
  bank: "Garanti BBVA",
});
const CASH = entry({
  id: "00000000-0000-0000-0000-000000000003",
  amount_kurus: 20000,
  status: "confirmed",
  source: "voice",
  method: "cash",
  category: "market",
});

function money(over: Partial<Money> = {}): Money {
  return {
    balances: [{ bank: "Yapı Kredi", balance_kurus: 250000, as_of: "2026-10-06T11:00:00+00:00" }],
    balance_speech: "Son bilinen bakiye: Yapı Kredi hesabınızda 2.500 TL (bugün 14:00 postasına göre) efendim.",
    month: { total_kurus: 218456, count: 3, tentative: 1, cash_kurus: 20000, speech: "Bu ay 2.184,56 TL harcadınız." },
    entries: [JACKET, MARKET, CASH],
    questions: [
      {
        id: "q-1",
        amount_kurus: 75000,
        reason: "no_acceptance",
        asked_at: "2026-10-06T11:05:00+00:00",
        answer: null,
        speech: "750 liralık bir harcama yaptınız mı?",
      },
    ],
    categories: ["market", "yemek", "akaryakıt", "eczane", "giyim", "ulaşım", "fatura"],
    ...over,
  };
}

function props(over: Partial<MoneyViewProps> = {}): MoneyViewProps {
  return {
    money: money(),
    error: null,
    notice: null,
    busy: false,
    cashAmount: "",
    cashCategory: "",
    onCashAmount: () => {},
    onCashCategory: () => {},
    onCash: () => {},
    onCancel: () => {},
    onAnswer: () => {},
    ...over,
  };
}

function html(over: Partial<MoneyViewProps> = {}): string {
  return renderToStaticMarkup(<MoneyView {...props(over)} />);
}

function elements(node: ReactNode): ReactElement<Props>[] {
  if (Array.isArray(node)) return node.flatMap(elements);
  if (!isValidElement<Props>(node)) return [];
  if (typeof node.type === "function") {
    return elements((node.type as (p: Props) => ReactNode)(node.props));
  }
  return [node, ...elements(node.props.children)];
}

function buttons(over: Partial<MoneyViewProps>, attr: string, value: string) {
  return elements(<MoneyView {...props(over)} />).filter(
    (el) => el.type === "button" && el.props[attr] === value,
  );
}

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

beforeEach(() => {
  apiFetch.mockReset();
});

describe("the money view", () => {
  it("says the balance with its time, the month, and every entry in Turkish amounts", () => {
    const markup = html();
    expect(markup).toContain("2.500 TL (bugün 14:00 postasına göre)");
    expect(markup).toContain("Bu ay 2.184,56 TL harcadınız.");
    expect(markup).toContain("750 TL");
    expect(markup).toContain("1.234,56 TL");
    expect(markup).toContain("geçici");
    expect(markup).toContain("nakit");
    expect(markup).toContain("Garanti BBVA");
  });

  it("formats kuruş the Turkish way", () => {
    expect(amountText(75000)).toBe("750 TL");
    expect(amountText(123456)).toBe("1.234,56 TL");
    expect(amountText(99)).toBe("0,99 TL");
    expect(amountText(100000000)).toBe("1.000.000 TL");
  });

  it("asks the open question with yes and no, and takes back only a row not cancelled", () => {
    const onAnswer = vi.fn();
    (buttons({ onAnswer }, "data-money-answer", "yes")[0].props.onClick as () => void)();
    expect(onAnswer).toHaveBeenCalledWith("q-1", "yes");
    (buttons({ onAnswer }, "data-money-answer", "no")[0].props.onClick as () => void)();
    expect(onAnswer).toHaveBeenLastCalledWith("q-1", "no");
    const onCancel = vi.fn();
    const cancels = buttons({ onCancel }, "data-money-action", "cancel");
    expect(cancels).toHaveLength(3);
    (cancels[0].props.onClick as () => void)();
    expect(onCancel).toHaveBeenCalledWith(JACKET.id);
    const gone = money({ entries: [entry({ status: "cancelled" })], questions: [] });
    expect(buttons({ money: gone }, "data-money-action", "cancel")).toHaveLength(0);
    expect(html({ money: gone })).toContain("geri alındı");
  });

  it("books cash only with an amount", () => {
    const onCash = vi.fn();
    expect(buttons({ onCash }, "data-money-action", "cash")[0].props.disabled).toBe(true);
    const add = buttons({ onCash, cashAmount: " 200 ", cashCategory: "market" }, "data-money-action", "cash")[0];
    (add.props.onClick as () => void)();
    expect(onCash).toHaveBeenCalledWith("200", "market");
  });

  it("shows the server's refusal and the empty ledger plainly", () => {
    expect(html({ money: null, error: "Oturum yok." })).toContain('role="alert">Oturum yok.');
    expect(html({ money: money({ entries: [], questions: [] }) })).toContain(EMPTY_ENTRIES);
    expect(html({ money: null })).toContain("yükleniyor");
  });
});

describe("the money client", () => {
  it("reads /v1/money", async () => {
    apiFetch.mockResolvedValueOnce(json(200, money()));
    const result = await fetchMoney();
    expect(apiFetch).toHaveBeenCalledWith("/v1/money");
    expect(result.ok && result.money.entries).toHaveLength(3);
  });

  it("posts a cancel, an answer and a cash spend", async () => {
    apiFetch.mockResolvedValueOnce(json(200, { entry: entry({ status: "cancelled" }) }));
    expect((await cancelEntry("a/b")).ok).toBe(true);
    expect(apiFetch.mock.calls[0][0]).toBe("/v1/money/entries/a%2Fb/cancel");

    apiFetch.mockResolvedValueOnce(json(200, { question: {}, entry: JACKET }));
    await answerQuestion("q-1", "yes", "700");
    expect(apiFetch.mock.calls[1][0]).toBe("/v1/money/questions/q-1/answer");
    expect(JSON.parse(apiFetch.mock.calls[1][1].body)).toEqual({ answer: "yes", amount: "700" });

    apiFetch.mockResolvedValueOnce(json(200, { entry: CASH }));
    const cash = await bookCash("200", "");
    expect(JSON.parse(apiFetch.mock.calls[2][1].body)).toEqual({ amount: "200", category: null });
    expect(cash).toEqual({ ok: true, message: "200 TL nakit harcamayı deftere yazdım." });
  });

  it("surfaces a refusal with the server's sentence", async () => {
    apiFetch.mockResolvedValueOnce(
      json(422, { detail: { code: "money_refused", message: "Tutarı anlayamadım." } }),
    );
    expect(await bookCash("yok", "")).toEqual({
      ok: false,
      code: "money_refused",
      message: "Tutarı anlayamadım.",
    });
  });
});

describe("the contract with routes.py", () => {
  it("the client's entry fields are exactly the server's _entry keys", () => {
    const routes = readFileSync(
      fileURLToPath(new URL("../../../../services/api/app/money/routes.py", import.meta.url)),
      "utf8",
    );
    const body = routes.slice(routes.indexOf("def _entry("), routes.indexOf("def _question("));
    const serverKeys = [...body.matchAll(/^\s+"([a-z_]+)":/gm)].map((m) => m[1]).toSorted();
    expect(serverKeys).toEqual(Object.keys(JACKET).toSorted());
  });
});
