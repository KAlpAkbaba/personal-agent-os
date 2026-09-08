/**
 * The panel shell, and the reason the cockpit can be trusted panel by panel.
 *
 * Every panel renders through here, and here there are four distinct outcomes
 * with four distinct words:
 *
 *   loading  — "yükleniyor"        we have not asked yet
 *   failed   — "alınamadı: …"      we asked and could not find out
 *   absent   — "henüz yok"         this server has no such endpoint (404)
 *   empty    — the panel's own     we asked, and there is genuinely nothing
 *   loaded   — the rows
 *
 * Collapsing "failed" into "empty" is the panel-level version of animating
 * work that is not happening: it reads as a confident statement about the
 * world that was never actually checked. Keeping them apart is why every panel
 * must supply its own `empty` sentence. `absent` (M18.3) is the same argument
 * once more: a route another track is still building has told us nothing, and
 * an empty list would say it told us there is nothing.
 */

import type { Loaded } from "../../lib/cockpit/api";

export type PanelProps<T> = {
  title: string;
  state: Loaded<T>;
  /** Shown when the request succeeded and there is nothing to list. */
  empty: string;
  /** True when the loaded value holds nothing. */
  isEmpty: (value: T) => boolean;
  /** A short count or status shown beside the title. */
  badge?: (value: T) => string | null;
  children: (value: T) => React.ReactNode;
  /** Draws attention (a border) — used for things awaiting the owner. */
  attention?: (value: T) => boolean;
  id: string;
};

/**
 * The three not-yet-loaded outcomes in their words, and nothing for `ok`.
 *
 * Split out of `Panel` (M21) for the panels that draw their own section —
 * a bus line above a REST list — so "yükleniyor", "alınamadı" and "henüz
 * yok" are spelled once and mean the same thing under every title.
 */
export function LoadedNotice<T>({ state }: { state: Loaded<T> }) {
  return (
    <>
      {state.kind === "loading" && (
        <p className="panel-empty" data-panel-loading>
          yükleniyor…
        </p>
      )}

      {state.kind === "failed" && (
        // Never an empty list: we do not know whether it is empty.
        <p className="panel-unknown" data-panel-failed>
          Alınamadı: {state.error}
        </p>
      )}

      {state.kind === "absent" && (
        // A route that does not exist yet. Distinct from both "empty" and
        // "failed": the question could not be asked, so no answer is implied.
        <p className="panel-unknown" data-panel-absent>
          Henüz yok. {state.detail}
        </p>
      )}
    </>
  );
}

export default function Panel<T>({
  title,
  state,
  empty,
  isEmpty,
  badge,
  children,
  attention,
  id,
}: PanelProps<T>) {
  const highlighted = state.kind === "ok" && attention?.(state.value) === true;

  return (
    <section
      className={`panel ${highlighted ? "attention" : ""}`}
      data-panel={id}
      data-panel-state={state.kind}
      data-panel-empty={state.kind === "ok" ? (isEmpty(state.value) ? "yes" : "no") : ""}
    >
      <h3 className="panel-title">
        <span>{title}</span>
        {state.kind === "ok" && badge && (
          <span className="panel-count" data-panel-badge>
            {badge(state.value)}
          </span>
        )}
      </h3>

      <LoadedNotice state={state} />

      {state.kind === "ok" &&
        (isEmpty(state.value) ? (
          <p className="panel-empty" data-panel-empty-text>
            {empty}
          </p>
        ) : (
          children(state.value)
        ))}
    </section>
  );
}
