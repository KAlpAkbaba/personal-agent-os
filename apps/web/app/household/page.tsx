"use client";

/**
 * `/household` - 'Ev stoğu': what the house is running out of and what to buy (home-stock-list).
 *
 * The same store the voice keeps ("tuvalet kağıdı azaldı", "ne almam lazım"); the page holds
 * the state and calls the client, everything shown is HouseholdView's, everything decided is
 * the Cloud Core's. Every answer reloads, so the rows show what the Cloud Core now holds.
 */

import { useCallback, useEffect, useState } from "react";

import FamilyPage from "../components/FamilyPage";
import HouseholdView from "./HouseholdView";
import {
  addToList,
  fetchHousehold,
  forgetItem,
  removeFromList,
  setLevel,
  type Done,
  type Household,
  type Refusal,
} from "./householdApi";

export default function HouseholdPage() {
  const [household, setHousehold] = useState<Household | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [draftName, setDraftName] = useState("");
  const [draftQuantity, setDraftQuantity] = useState("");

  const refresh = useCallback(async () => {
    const result = await fetchHousehold();
    if (result.ok) {
      setHousehold(result.household);
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
      id="household"
      title="Ev stoğu"
      lead="Evde azalan, biten ve alınacak olanlar. Sesle de söyleyebilirsin: 'tuvalet kağıdı azaldı', 'ne almam lazım'."
    >
      <HouseholdView
        household={household}
        error={error}
        notice={notice}
        busy={busy}
        draftName={draftName}
        draftQuantity={draftQuantity}
        onDraftName={setDraftName}
        onDraftQuantity={setDraftQuantity}
        onAdd={(name, quantity) =>
          void run(
            () => addToList(name, quantity),
            () => {
              setDraftName("");
              setDraftQuantity("");
            },
          )
        }
        onLevel={(name, level) => void run(() => setLevel(name, level))}
        onRemove={(id) => void run(() => removeFromList(id))}
        onForget={(id) => void run(() => forgetItem(id))}
      />
    </FamilyPage>
  );
}
