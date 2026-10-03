/**
 * The 'Ne demek istemiştin?' list - markup only, a pure function of its props (no hooks), so
 * the tests render it with fixtures and press its buttons through the element tree.
 *
 * Each row: the sentence as the recogniser wrote it, when, the mode, the machine and band when
 * known, and why it is here. Never audio, never a transcript beyond that one sentence.
 */

import type { MisheardList } from "./misheardApi";
import {
  EMPTY_SENTENCE,
  canSave,
  openSentence,
  retentionSentence,
  rowView,
} from "./misheardModel";

export type MisheardViewProps = {
  list: MisheardList | null;
  /** The server's sentence when the list could not be read. */
  error: string | null;
  drafts: Record<string, string>;
  notice: string | null;
  busy: boolean;
  onDraft: (id: string, text: string) => void;
  onSave: (id: string, text: string) => void;
  onDelete: (id: string) => void;
  onForgetAll: () => void;
  timeZone?: string;
};

export default function MisheardView({
  list,
  error,
  drafts,
  notice,
  busy,
  onDraft,
  onSave,
  onDelete,
  onForgetAll,
  timeZone,
}: MisheardViewProps) {
  return (
    <>
      {error && <p role="alert">{error}</p>}
      {!error && list === null && <p className="muted">yükleniyor</p>}
      {notice && <p role="status">{notice}</p>}
      {list && (
        <>
          <p data-misheard-open={list.open}>{openSentence(list.open)}</p>
          <p className="muted" data-misheard-retention>
            {retentionSentence(list.retention_days)}
          </p>
          <p>
            <button type="button" disabled={busy || list.items.length === 0} onClick={onForgetAll}>
              Defteri unut
            </button>
          </p>
          {list.items.length === 0 && (
            <p className="muted" data-misheard-empty>
              {EMPTY_SENTENCE}
            </p>
          )}
          {list.items.length > 0 && (
            <ul className="detail-list">
              {list.items.map((item) => {
                const row = rowView(item, timeZone);
                const draft = drafts[row.id] ?? "";
                const facts = [row.when, row.mode, row.machine, row.engine, row.band, row.tool].filter(
                  (fact): fact is string => fact !== null,
                );
                return (
                  <li key={row.id} className="detail-row" data-misheard-row={row.id}>
                    <span className="detail-head">“{row.sentence}”</span>
                    <span className="muted">{facts.join(" · ")}</span>
                    <span>{row.reason}</span>
                    {row.meant !== null ? (
                      <span data-misheard-meant>Demek istediğin: {row.meant}</span>
                    ) : (
                      <span>
                        <input
                          aria-label="Ne demek istemiştin?"
                          placeholder="Ne demek istemiştin?"
                          value={draft}
                          onChange={(event) => onDraft(row.id, event.target.value)}
                        />
                        <button
                          type="button"
                          disabled={busy || !canSave(draft)}
                          onClick={() => onSave(row.id, draft)}
                        >
                          Kaydet
                        </button>
                      </span>
                    )}
                    <button type="button" disabled={busy} onClick={() => onDelete(row.id)}>
                      Sil
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </>
      )}
    </>
  );
}
