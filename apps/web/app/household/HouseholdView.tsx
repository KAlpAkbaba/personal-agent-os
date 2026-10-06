/**
 * The house's stock and the shopping list - markup only, a pure function of its props (no
 * hooks), so the tests render it with fixtures and press its buttons through the element tree.
 *
 * Three parts: the list (what to buy, low and out first, with "Aldım" and "Listeden çıkar"),
 * "yakında bitebilir" (the items whose rhythm says they run out soon), and every item with its
 * level buttons and its rhythm ("genelde 21 günde bir").
 */

import type { Household, HouseholdItem, Level } from "./householdApi";
import { LEVELS } from "./householdApi";

export type HouseholdViewProps = {
  household: Household | null;
  /** The server's sentence when the stock could not be read. */
  error: string | null;
  notice: string | null;
  busy: boolean;
  draftName: string;
  draftQuantity: string;
  onDraftName: (text: string) => void;
  onDraftQuantity: (text: string) => void;
  onAdd: (name: string, quantity: string) => void;
  onLevel: (name: string, level: Level) => void;
  onRemove: (id: string) => void;
  onForget: (id: string) => void;
};

export const EMPTY_LIST = "Alışveriş listesi boş.";
export const EMPTY_STOCK = "Henüz hiçbir ürün yok. 'Tuvalet kağıdı azaldı' demen yeter.";

const LEVEL_LABEL: Record<Level, string> = { var: "Var", azaldı: "Azaldı", bitti: "Bitti" };

export function entryText(item: HouseholdItem): string {
  const name = item.list_quantity ? `${item.list_quantity} ${item.name}` : item.name;
  return item.level === "azaldı" || item.level === "bitti" ? `${name} (${item.level})` : name;
}

export function rhythmText(item: HouseholdItem): string | null {
  if (item.cycle_days === null) return null;
  return `genelde ${Math.round(item.cycle_days)} günde bir biter`;
}

export default function HouseholdView({
  household,
  error,
  notice,
  busy,
  draftName,
  draftQuantity,
  onDraftName,
  onDraftQuantity,
  onAdd,
  onLevel,
  onRemove,
  onForget,
}: HouseholdViewProps) {
  return (
    <>
      {error && <p role="alert">{error}</p>}
      {!error && household === null && <p className="muted">yükleniyor</p>}
      {notice && <p role="status">{notice}</p>}
      <section id="household-add" aria-label="Listeye ekle">
        <h2>Listeye ekle</h2>
        <input
          aria-label="Ürün"
          data-household-draft="name"
          value={draftName}
          maxLength={60}
          placeholder="ör. tuvalet kağıdı"
          onChange={(event) => onDraftName(event.target.value)}
        />
        <input
          aria-label="Miktar"
          data-household-draft="quantity"
          value={draftQuantity}
          maxLength={40}
          placeholder="ör. iki paket"
          onChange={(event) => onDraftQuantity(event.target.value)}
        />
        <button
          type="button"
          data-household-action="add"
          disabled={busy || draftName.trim() === ""}
          onClick={() => onAdd(draftName.trim(), draftQuantity)}
        >
          Ekle
        </button>
      </section>
      {household && (
        <>
          <section id="household-list" aria-label="Alışveriş listesi">
            <h2>Alışveriş listesi</h2>
            {household.list.length === 0 ? (
              <p className="muted">{EMPTY_LIST}</p>
            ) : (
              <ul>
                {household.list.map((item) => (
                  <li key={item.id} data-household-list={item.id}>
                    <span>{entryText(item)}</span>{" "}
                    <button
                      type="button"
                      data-household-action="bought"
                      disabled={busy}
                      onClick={() => onLevel(item.name, "var")}
                    >
                      Aldım
                    </button>{" "}
                    <button
                      type="button"
                      data-household-action="remove"
                      disabled={busy}
                      onClick={() => onRemove(item.id)}
                    >
                      Listeden çıkar
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </section>
          {household.soon.length > 0 && (
            <section id="household-soon" aria-label="Yakında bitebilir">
              <h2>Yakında bitebilir</h2>
              <ul>
                {household.soon.map((item) => (
                  <li key={item.id} data-household-soon={item.id}>
                    {item.name}
                    {rhythmText(item) ? ` - ${rhythmText(item)}` : ""}
                  </li>
                ))}
              </ul>
            </section>
          )}
          <section id="household-stock" aria-label="Evdeki ürünler">
            <h2>Evdeki ürünler</h2>
            {household.items.length === 0 ? (
              <p className="muted">{EMPTY_STOCK}</p>
            ) : (
              <ul>
                {household.items.map((item) => (
                  <li key={item.id} data-household-item={item.id} data-level={item.level ?? ""}>
                    <strong>{item.name}</strong>
                    {rhythmText(item) && <span className="muted"> ({rhythmText(item)})</span>}{" "}
                    {LEVELS.map((level) => (
                      <button
                        key={level}
                        type="button"
                        data-household-level={level}
                        aria-pressed={item.level === level}
                        disabled={busy}
                        onClick={() => onLevel(item.name, level)}
                      >
                        {LEVEL_LABEL[level]}
                      </button>
                    ))}{" "}
                    <button
                      type="button"
                      data-household-action="forget"
                      disabled={busy}
                      onClick={() => onForget(item.id)}
                    >
                      Sil
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </section>
        </>
      )}
    </>
  );
}
