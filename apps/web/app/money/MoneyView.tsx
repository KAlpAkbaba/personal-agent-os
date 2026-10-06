/**
 * The money ledger - markup only, a pure function of its props (no hooks), so the tests render
 * it with fixtures and press its buttons through the element tree.
 *
 * Four parts: the last known balance (with the time of the bank's mail), this month, the
 * questions JARVIS asked ("750 liralık bir harcama yaptınız mı?" with Evet / Hayır), and every
 * entry - tentative ones marked, cash marked, each with "Geri al" until it is taken back.
 * A cash spend can be written by hand. Nothing here moves money.
 */

import type { Money, MoneyEntry } from "./moneyApi";
import { amountText } from "./moneyApi";

export { amountText };

export type MoneyViewProps = {
  money: Money | null;
  /** The server's sentence when the ledger could not be read. */
  error: string | null;
  notice: string | null;
  busy: boolean;
  cashAmount: string;
  cashCategory: string;
  onCashAmount: (value: string) => void;
  onCashCategory: (value: string) => void;
  onCash: (amount: string, category: string) => void;
  onCancel: (id: string) => void;
  onAnswer: (id: string, answer: "yes" | "no") => void;
};

export const EMPTY_ENTRIES =
  "Bu ay defterde kayıt yok. Bankanın bildirim postaları ve konuşmalarda onayladığın harcamalar buraya gelir.";

const STATUS_LABEL: Record<MoneyEntry["status"], string> = {
  tentative: "geçici (banka postası bekleniyor)",
  confirmed: "kesin",
  cancelled: "geri alındı",
};

function entryLine(row: MoneyEntry): string {
  const parts = [
    `${row.direction === "in" ? "+" : ""}${amountText(row.amount_kurus)}`,
    row.description,
    row.category ?? "",
    row.method === "cash" ? "nakit" : "",
    row.bank ?? "",
  ].filter((part) => part !== "");
  return parts.join(" · ");
}

export default function MoneyView({
  money,
  error,
  notice,
  busy,
  cashAmount,
  cashCategory,
  onCashAmount,
  onCashCategory,
  onCash,
  onCancel,
  onAnswer,
}: MoneyViewProps) {
  const open = money?.questions.filter((q) => q.answer === null) ?? [];
  return (
    <>
      {error && <p role="alert">{error}</p>}
      {!error && money === null && <p className="muted">yükleniyor</p>}
      {notice && <p role="status">{notice}</p>}
      {money && (
        <>
          <section id="money-balance" aria-label="Bakiye">
            <h2>Bakiye</h2>
            <p>{money.balance_speech}</p>
            <p className="muted">{money.month.speech}</p>
          </section>
          {open.length > 0 && (
            <section id="money-questions" aria-label="Sorularım">
              <h2>Sorularım</h2>
              <ul>
                {open.map((q) => (
                  <li key={q.id} data-money-question={q.id}>
                    <span>{q.speech}</span>{" "}
                    <button
                      type="button"
                      data-money-answer="yes"
                      disabled={busy}
                      onClick={() => onAnswer(q.id, "yes")}
                    >
                      Evet
                    </button>{" "}
                    <button
                      type="button"
                      data-money-answer="no"
                      disabled={busy}
                      onClick={() => onAnswer(q.id, "no")}
                    >
                      Hayır
                    </button>
                  </li>
                ))}
              </ul>
            </section>
          )}
          <section id="money-entries" aria-label="Kayıtlar">
            <h2>Kayıtlar</h2>
            {money.entries.length === 0 ? (
              <p className="muted">{EMPTY_ENTRIES}</p>
            ) : (
              <ul>
                {money.entries.map((row) => (
                  <li key={row.id} data-money-entry={row.id} data-status={row.status}>
                    <span>{entryLine(row)}</span>{" "}
                    <span className="muted">({STATUS_LABEL[row.status]})</span>{" "}
                    {row.status !== "cancelled" && (
                      <button
                        type="button"
                        data-money-action="cancel"
                        disabled={busy}
                        onClick={() => onCancel(row.id)}
                      >
                        Geri al
                      </button>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </section>
        </>
      )}
      <section id="money-cash" aria-label="Nakit harcama">
        <h2>Nakit harcama</h2>
        <input
          aria-label="Tutar"
          data-money-draft="amount"
          value={cashAmount}
          maxLength={20}
          placeholder="ör. 200 ya da 1.234,56"
          onChange={(event) => onCashAmount(event.target.value)}
        />
        <select
          aria-label="Kategori"
          data-money-draft="category"
          value={cashCategory}
          onChange={(event) => onCashCategory(event.target.value)}
        >
          <option value="">kategori yok</option>
          {(money?.categories ?? []).map((category) => (
            <option key={category} value={category}>
              {category}
            </option>
          ))}
        </select>
        <button
          type="button"
          data-money-action="cash"
          disabled={busy || cashAmount.trim() === ""}
          onClick={() => onCash(cashAmount.trim(), cashCategory)}
        >
          Deftere yaz
        </button>
      </section>
    </>
  );
}
