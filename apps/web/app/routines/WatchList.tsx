"use client";

/**
 * 'Nöbetler' on /routines: the owner's own standing watches, beside the routines the system
 * runs (not the Onay Merkezi or the Ofis - those are the team's pages).
 *
 * `WatchListView` is markup only, a pure function of its props, so the tests render it with
 * fixtures and press its buttons through the element tree; `WatchList` holds the state and
 * calls `lib/watch/watches.ts`. Every rule is the Cloud Core's: a refusal stands beside the
 * form in its own words, and a failed list is one line while the rest of the page renders.
 */

import { useCallback, useEffect, useState } from "react";

import {
  CONDITION_KINDS,
  EMPTY_DRAFT,
  EMPTY_SENTENCE,
  type ConditionKind,
  type Watch,
  type WatchDraft,
  addOne,
  conditionSentence,
  domainOf,
  fetchWatches,
  forgetEverything,
  intervalSentence,
  lastReadingSentence,
  loadFailedSentence,
  removeOne,
} from "../lib/watch/watches";

export type WatchListViewProps = {
  /** `null` while loading or when the list could not be read. */
  items: Watch[] | null;
  /** The one Turkish line a failed list is. */
  error: string | null;
  notice: string | null;
  /** The server's `reason_tr` for the last add it refused. */
  refusal: string | null;
  busy: boolean;
  draft: WatchDraft;
  onDraft: (draft: WatchDraft) => void;
  onAdd: () => void;
  onRemove: (id: string) => void;
  onForgetAll: () => void;
  timeZone?: string;
};

export function WatchListView({
  items,
  error,
  notice,
  refusal,
  busy,
  draft,
  onDraft,
  onAdd,
  onRemove,
  onForgetAll,
  timeZone,
}: WatchListViewProps) {
  const set = (patch: Partial<WatchDraft>) => onDraft({ ...draft, ...patch });
  return (
    <section id="watches" className="panel" data-panel="watches">
      <h3 className="panel-title">
        <span>Nöbetler</span>
        {items && (
          <span className="panel-count" data-panel-badge>
            {items.length}
          </span>
        )}
      </h3>
      {error && <p role="alert">{error}</p>}
      {!error && items === null && <p className="muted">yükleniyor</p>}
      {notice && <p role="status">{notice}</p>}
      {items && items.length === 0 && (
        <p className="panel-empty" data-watch-empty>
          {EMPTY_SENTENCE}
        </p>
      )}
      {items && items.length > 0 && (
        <ul className="detail-list">
          {items.map((watch) => (
            <li key={watch.id} className="detail-row" data-watch-row={watch.id}>
              <span className="detail-head">{watch.label}</span>
              <span className="muted detail-facts">
                {[domainOf(watch.url), conditionSentence(watch.condition), intervalSentence(watch.every_hours)].join(
                  " · ",
                )}
              </span>
              <span>{lastReadingSentence(watch, timeZone)}</span>
              <button type="button" disabled={busy} onClick={() => onRemove(watch.id)}>
                Kaldır
              </button>
            </li>
          ))}
        </ul>
      )}
      <form
        onSubmit={(event) => {
          event.preventDefault();
          onAdd();
        }}
      >
        <input
          aria-label="Adres"
          placeholder="https://…"
          value={draft.url}
          onChange={(event) => set({ url: event.target.value })}
        />
        <input
          aria-label="Ad"
          placeholder="Ad (boşsa alan adı)"
          value={draft.label}
          onChange={(event) => set({ label: event.target.value })}
        />
        <select
          aria-label="Koşul"
          value={draft.kind}
          onChange={(event) => set({ kind: event.target.value as ConditionKind })}
        >
          {CONDITION_KINDS.map((option) => (
            <option key={option.kind} value={option.kind}>
              {option.label}
            </option>
          ))}
        </select>
        <input
          aria-label="Değer"
          placeholder={draft.kind === "contains" ? "metin" : "sayı"}
          disabled={draft.kind === "changed"}
          value={draft.value}
          onChange={(event) => set({ value: event.target.value })}
        />
        <input
          aria-label="Kaç saatte bir"
          type="number"
          min={1}
          max={168}
          value={draft.every_hours}
          onChange={(event) => set({ every_hours: event.target.value })}
        />
        <button type="submit" disabled={busy || draft.url.trim() === ""}>
          Nöbet ekle
        </button>
        {refusal && (
          <p role="alert" data-watch-refusal>
            {refusal}
          </p>
        )}
      </form>
      <p>
        <button type="button" disabled={busy || !items || items.length === 0} onClick={onForgetAll}>
          Hepsini unut
        </button>
      </p>
    </section>
  );
}

export default function WatchList() {
  const [items, setItems] = useState<Watch[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [refusal, setRefusal] = useState<string | null>(null);
  const [draft, setDraft] = useState<WatchDraft>(EMPTY_DRAFT);
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(async () => {
    const result = await fetchWatches();
    if (result.ok) {
      setItems(result.items);
      setError(null);
    } else {
      setError(loadFailedSentence(result));
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const current = items ?? [];

  return (
    <WatchListView
      items={items}
      error={error}
      notice={notice}
      refusal={refusal}
      busy={busy}
      draft={draft}
      onDraft={setDraft}
      onAdd={() => {
        setBusy(true);
        void addOne(current, draft).then((next) => {
          setBusy(false);
          setItems(next.items);
          setNotice(next.notice);
          setRefusal(next.refusal);
          if (next.refusal === null) setDraft(EMPTY_DRAFT);
        });
      }}
      onRemove={(id) => {
        setBusy(true);
        void removeOne(current, id).then((next) => {
          setBusy(false);
          setItems(next.items);
          setNotice(next.notice);
        });
      }}
      onForgetAll={() => {
        setBusy(true);
        void forgetEverything(current).then((next) => {
          setBusy(false);
          setItems(next.items);
          setNotice(next.notice);
        });
      }}
    />
  );
}
