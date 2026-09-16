"use client";

/**
 * B24: the shell every family page shares.
 *
 * Seven requirements (689, 693, 694, 696, 697, 698, 699) ask for a page each, and the
 * thing they have in common is worth writing once: the owner gate, a title that says what
 * the page is for, the panels rendered with req 714's hiding turned OFF — on a family's
 * own page "there are no alarms" is the answer, not noise — and the way back to the
 * cockpit's panel for the same family.
 *
 * `Rows` is the other half. Six of the seven pages are lists of things the API has served
 * for months with nothing in the product asking for them, and every one of those lists
 * needs the same four outcomes `Panel` already spells (`yükleniyor` / `alınamadı` /
 * `henüz yok` / the rows) plus a retry. Writing that seven times is how two of them end up
 * saying "no findings" for a request that failed.
 */

import Link from "next/link";

import OwnerGate from "./OwnerGate";
import type { Loaded } from "../lib/cockpit/api";
import Panel from "../core/panels/Panel";
import "../core/core.css";

export default function FamilyPage({
  id,
  title,
  lead,
  panel,
  children,
}: {
  /** `data-page`, and the anchor a test looks for. */
  id: string;
  title: string;
  lead: string;
  /** The cockpit panel this family also appears in, if it has one. */
  panel?: string;
  children: React.ReactNode;
}) {
  return (
    <OwnerGate>
      <main data-page={id}>
        <h1>{title}</h1>
        <p className="subtitle">{lead}</p>
        {children}
        {panel && (
          <p className="muted">
            <Link href={`/core/cockpit#${panel}`} data-page-cockpit={panel}>
              Kokpitteki paneli aç →
            </Link>
          </p>
        )}
      </main>
    </OwnerGate>
  );
}

/** A list of rows with the four outcomes kept apart, and a retry when one could help. */
export function Rows<T>({
  id,
  title,
  state,
  empty,
  badge,
  onRetry,
  children,
}: {
  id: string;
  title: string;
  state: Loaded<T[]>;
  empty: string;
  badge?: (rows: T[]) => string;
  onRetry?: () => void;
  children: (row: T) => React.ReactNode;
}) {
  return (
    <Panel<T[]>
      id={id}
      title={title}
      state={state}
      empty={empty}
      isEmpty={(rows) => rows.length === 0}
      badge={badge ?? ((rows) => `${rows.length}`)}
      onRetry={onRetry}
      // A family page's own list says "there is nothing" rather than vanishing: that
      // sentence is what the owner opened the page for (req 714 applies to the cockpit).
      always
    >
      {(rows) => <ul className="detail-list">{rows.map(children)}</ul>}
    </Panel>
  );
}

/** One row: a headline, then the facts that qualify it. */
export function Row({
  keyText,
  head,
  facts,
  tone,
}: {
  keyText: string;
  head: React.ReactNode;
  facts: (string | null)[];
  /** `bad` draws the row as something wrong; `wait` as something unfinished. */
  tone?: "bad" | "wait";
}) {
  const shown = facts.filter((fact): fact is string => fact !== null && fact !== "");
  return (
    <li key={keyText} className={tone ? `detail-row tone-${tone}` : "detail-row"} data-row={keyText}>
      <span className="detail-head">{head}</span>
      {shown.length > 0 && <span className="muted detail-facts">{shown.join(" · ")}</span>}
    </li>
  );
}
