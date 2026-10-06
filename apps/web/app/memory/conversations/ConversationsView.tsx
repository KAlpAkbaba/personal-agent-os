/**
 * The conversations page - markup only, a pure function of its props (no hooks), so the tests
 * render it with fixtures and press its buttons through the element tree.
 *
 * Text only: a line is what was said and who said it ('Sen', a consenting person's name, or
 * 'Konuşmacı N'). There is no audio to play, because none is kept.
 */

import type { ConversationDetail, ConversationItem, Person } from "./conversationsApi";

export type ConversationsViewProps = {
  list: ConversationItem[] | null;
  /** The server's sentence when the list could not be read. */
  error: string | null;
  query: string;
  selected: ConversationDetail | null;
  people: Person[];
  homeListen: boolean;
  notice: string | null;
  busy: boolean;
  onQuery: (q: string) => void;
  onSearch: () => void;
  onOpen: (id: string) => void;
  onDelete: (id: string) => void;
  onForgetAll: () => void;
  onHomeListen: (on: boolean) => void;
  onConsent: (personId: string) => void;
  onDeletePerson: (personId: string) => void;
  timeZone?: string;
};

function when(iso: string, timeZone?: string): string {
  return new Date(iso).toLocaleString("tr-TR", {
    timeZone,
    day: "numeric",
    month: "long",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function clock(iso: string, timeZone?: string): string {
  return new Date(iso).toLocaleTimeString("tr-TR", { timeZone, hour: "2-digit", minute: "2-digit" });
}

export default function ConversationsView({
  list,
  error,
  query,
  selected,
  people,
  homeListen,
  notice,
  busy,
  onQuery,
  onSearch,
  onOpen,
  onDelete,
  onForgetAll,
  onHomeListen,
  onConsent,
  onDeletePerson,
  timeZone,
}: ConversationsViewProps) {
  return (
    <>
      {error && <p role="alert">{error}</p>}
      {!error && list === null && <p className="muted">yükleniyor</p>}
      {notice && <p role="status">{notice}</p>}
      <p className="muted">Ses kaydı tutulmaz; yalnızca yazıya dökülmüş metin ve kimin söylediği.</p>
      <p>
        <button
          type="button"
          disabled={busy}
          aria-pressed={homeListen}
          onClick={() => onHomeListen(!homeListen)}
        >
          {homeListen ? "Evde dinle: açık" : "Evde dinle: kapalı"}
        </button>{" "}
        <span className="muted">Açıkken evde konuşmalar kendiliğinden yazılır (evde dinle).</span>
      </p>
      <p>
        <input
          type="search"
          aria-label="Konuşmalarda ara"
          value={query}
          onChange={(event) => onQuery(event.target.value)}
        />{" "}
        <button type="button" disabled={busy} onClick={onSearch}>
          Ara
        </button>{" "}
        <button type="button" disabled={busy || !list || list.length === 0} onClick={onForgetAll}>
          Hepsini unut
        </button>
      </p>
      {list && list.length === 0 && <p className="muted">Henüz yazılmış konuşma yok.</p>}
      {list && list.length > 0 && (
        <ul className="detail-list">
          {list.map((item) => (
            <li key={item.id} className="detail-row" data-conversation={item.id}>
              <span className="detail-head">
                {item.title ?? when(item.started_at, timeZone)}
                {item.ended_at === null ? " · sürüyor" : ""}
                {item.mode === "home" ? " · evde" : ""}
              </span>
              <span className="muted">
                {item.segment_count} satır · “{item.preview}”
              </span>
              <span>
                <button type="button" data-for={item.id} disabled={busy} onClick={() => onOpen(item.id)}>
                  Oku
                </button>{" "}
                <button type="button" data-for={item.id} disabled={busy} onClick={() => onDelete(item.id)}>
                  Sil
                </button>
              </span>
            </li>
          ))}
        </ul>
      )}
      {selected && (
        <section aria-label="Konuşma" data-selected={selected.id}>
          <h2>{selected.title ?? when(selected.started_at, timeZone)}</h2>
          <ol className="detail-list">
            {selected.segments.map((line) => (
              <li key={line.id} className="detail-row" data-speaker={line.speaker} data-owner={String(line.is_owner)}>
                <strong>{line.speaker}</strong>
                <span className="muted"> {clock(line.spoken_at, timeZone)} </span>
                <span>{line.text}</span>
              </li>
            ))}
          </ol>
        </section>
      )}
      <section aria-label="Kişiler">
        <h2>Tanınan sesler</h2>
        <p className="muted">
          Bir sesi ancak kişinin izni kaydedilince tanırım. İzin yoksa 'Konuşmacı N' kalır ve ses profili
          tutulmaz. Kişiyi silince profili ve adı tüm konuşmalardan kalkar.
        </p>
        {people.length === 0 && <p className="muted">Adı verilen kimse yok.</p>}
        <ul className="detail-list">
          {people.map((p) => (
            <li key={p.id} className="detail-row" data-person={p.id}>
              <span className="detail-head">{p.name}</span>
              <span className="muted">
                {p.consent ? "izin var" : "izin yok"}
                {p.has_profile ? " · ses profili var" : ""}
              </span>
              <span>
                {!p.consent && (
                  <button type="button" data-for={p.id} disabled={busy} onClick={() => onConsent(p.id)}>
                    İzin verdi
                  </button>
                )}{" "}
                <button type="button" data-for={p.id} disabled={busy} onClick={() => onDeletePerson(p.id)}>
                  Kişiyi sil
                </button>
              </span>
            </li>
          ))}
        </ul>
      </section>
    </>
  );
}
