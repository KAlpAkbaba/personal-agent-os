"use client";

/**
 * B25 req 701: what the owner may SAY, read from the Cloud Core's own tool registry.
 *
 * The client half of `GET /v1/voice/capabilities`. Nothing here writes the list down: the
 * summaries and the example phrases are the server's, derived from the tool descriptions
 * the model itself was given, so the sentence this product tells the owner to say is
 * literally the sentence the assistant was told to listen for.
 */

import { type Loaded, load } from "../cockpit/api";

export type CapabilityRow = {
  /** The tool's own name (`alarm.snooze`) — the anchor, never shown as a label. */
  name: string;
  family: string;
  familyTr: string;
  summary: string;
  /** Things the owner can say, in the words the tool description quotes. */
  phrases: string[];
};

export type CapabilityFamily = { family: string; familyTr: string; count: number };

export type CapabilityList = {
  rows: CapabilityRow[];
  families: CapabilityFamily[];
  /** What the assistant would SAY if asked out loud. */
  speech: string;
};

function obj(raw: unknown): Record<string, unknown> {
  return raw && typeof raw === "object" ? (raw as Record<string, unknown>) : {};
}

function str(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function strings(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
}

export function parseCapabilities(raw: unknown): CapabilityList {
  const body = obj(raw);
  const rows = (Array.isArray(body.capabilities) ? body.capabilities : [])
    .map(obj)
    .map((row) => ({
      name: str(row.name),
      family: str(row.family),
      familyTr: str(row.family_tr) || str(row.family),
      summary: str(row.summary),
      phrases: strings(row.phrases),
    }))
    .filter((row) => row.name !== "");
  const families = (Array.isArray(body.families) ? body.families : [])
    .map(obj)
    .map((row) => ({
      family: str(row.family),
      familyTr: str(row.family_tr) || str(row.family),
      count: typeof row.count === "number" ? row.count : 0,
    }))
    .filter((row) => row.family !== "");
  return { rows, families, speech: str(body.speech) };
}

export const fetchCapabilities = (): Promise<Loaded<CapabilityList>> =>
  load<CapabilityList>("/v1/voice/capabilities", parseCapabilities);

/** The anchor a capability gets on the page that lists them all. */
export function capabilityAnchor(name: string): string {
  return `cap-${name.replace(".", "-")}`;
}

/**
 * Group the flat list the way the owner meets it, families in the order the server gave.
 *
 * The server sorts families by their Turkish name, which is the order a Turkish reader
 * scans; re-sorting here by the English key would quietly undo that.
 */
export function byFamily(list: CapabilityList): { family: CapabilityFamily; rows: CapabilityRow[] }[] {
  return list.families
    .map((family) => ({
      family,
      rows: list.rows.filter((row) => row.family === family.family),
    }))
    .filter((group) => group.rows.length > 0);
}
