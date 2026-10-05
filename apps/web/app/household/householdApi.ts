/**
 * The client for `/v1/household` (home-stock-list): the house's stock and the shopping list.
 *
 * It sends what the owner pressed and reports what the Cloud Core answered; every rule (a name
 * that cannot be an item, the three levels, the rhythm) is the server's, and a refusal is
 * surfaced with the server's own sentence, never turned into a success.
 */

import { apiFetch } from "../lib/session";

export const HOUSEHOLD_PATH = "/v1/household";

export type Level = "var" | "azaldı" | "bitti";
export const LEVELS: readonly Level[] = ["var", "azaldı", "bitti"];

/** One row of `household_items` as `/v1/household` returns it (routes.py `_view`). */
export type HouseholdItem = {
  id: string;
  name: string;
  level: Level | null;
  on_list: boolean;
  list_quantity: string | null;
  usual_quantity: string | null;
  updated_at: string | null;
  depleted_at: string | null;
  restocked_at: string | null;
  cycle_days: number | null;
  predicted_out_at: string | null;
};

export type Household = {
  items: HouseholdItem[];
  list: HouseholdItem[];
  soon: HouseholdItem[];
  /** The sentence the voice says for "ne almam lazım". */
  speech: string;
};

export type Refusal = { ok: false; code: string; message: string };
export type Done = { ok: true; message: string };

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

export async function fetchHousehold(): Promise<{ ok: true; household: Household } | Refusal> {
  const response = await apiFetch(HOUSEHOLD_PATH);
  if (!response.ok) return refusal(response);
  return { ok: true, household: (await response.json()) as Household };
}

export async function setLevel(name: string, level: Level): Promise<Done | Refusal> {
  const response = await post(`${HOUSEHOLD_PATH}/items`, { name, level });
  if (!response.ok) return refusal(response);
  return { ok: true, message: ((await response.json()) as { speech: string }).speech };
}

export async function addToList(name: string, quantity: string): Promise<Done | Refusal> {
  const trimmed = quantity.trim();
  const response = await post(`${HOUSEHOLD_PATH}/list`, {
    name,
    quantity: trimmed ? trimmed : null,
  });
  if (!response.ok) return refusal(response);
  return { ok: true, message: ((await response.json()) as { speech: string }).speech };
}

export async function removeFromList(id: string): Promise<Done | Refusal> {
  const response = await apiFetch(`${HOUSEHOLD_PATH}/list/${encodeURIComponent(id)}`, {
    method: "DELETE",
  });
  if (!response.ok) return refusal(response);
  const item = ((await response.json()) as { item: HouseholdItem }).item;
  return { ok: true, message: `Listeden çıkardım: ${item.name}.` };
}

export async function forgetItem(id: string): Promise<Done | Refusal> {
  const response = await apiFetch(`${HOUSEHOLD_PATH}/items/${encodeURIComponent(id)}`, {
    method: "DELETE",
  });
  if (!response.ok) return refusal(response);
  return { ok: true, message: "Ürünü ve geçmişini sildim." };
}
