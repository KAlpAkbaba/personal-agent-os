"use client";

/**
 * `/money` - 'Para defteri': JARVIS's own record of the owner's money (money-ledger).
 *
 * The same ledger the voice reads ("hesabımda ne kadar var", "bu ay markete ne harcadım");
 * the page holds the state and calls the client, everything shown is MoneyView's, everything
 * decided is the Cloud Core's. Every answer reloads, so the rows show what the Cloud Core now
 * holds. Nothing here moves money or reaches a bank.
 */

import { useCallback, useEffect, useState } from "react";

import FamilyPage from "../components/FamilyPage";
import MoneyView from "./MoneyView";
import {
  answerQuestion,
  bookCash,
  cancelEntry,
  fetchMoney,
  type Done,
  type Money,
  type Refusal,
} from "./moneyApi";

export default function MoneyPage() {
  const [money, setMoney] = useState<Money | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [cashAmount, setCashAmount] = useState("");
  const [cashCategory, setCashCategory] = useState("");

  const refresh = useCallback(async () => {
    const result = await fetchMoney();
    if (result.ok) {
      setMoney(result.money);
      setError(null);
    } else {
      setError(result.message);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const run = async (action: () => Promise<Done | Refusal>, after?: () => void) => {
    setBusy(true);
    const result = await action();
    setBusy(false);
    setNotice(result.message);
    if (result.ok) after?.();
    void refresh();
  };

  return (
    <FamilyPage
      id="money"
      title="Para defteri"
      lead="Bankanın bildirim postalarından bakiye ve harcamalar, konuşmalarda onayladığın alışverişler. Bankaya bağlanmaz, para göndermez. Sesle: 'hesabımda ne kadar var', 'bu ay markete ne harcadım'."
    >
      <MoneyView
        money={money}
        error={error}
        notice={notice}
        busy={busy}
        cashAmount={cashAmount}
        cashCategory={cashCategory}
        onCashAmount={setCashAmount}
        onCashCategory={setCashCategory}
        onCash={(amount, category) =>
          void run(
            () => bookCash(amount, category),
            () => setCashAmount(""),
          )
        }
        onCancel={(id) => void run(() => cancelEntry(id))}
        onAnswer={(id, answer) => void run(() => answerQuestion(id, answer))}
      />
    </FamilyPage>
  );
}
