/**
 * The panel shell, and the reason the cockpit can be trusted panel by panel.
 *
 * Every panel renders through here, and here there are four distinct outcomes
 * with four distinct words:
 *
 *   loading  — "yükleniyor"        we have not asked yet
 *   failed   — "alınamadı: …"      we asked and could not find out
 *   absent   — (nothing)           this server has no such endpoint (404)
 *   empty    — (nothing)           we asked, and there is genuinely nothing
 *   loaded   — the rows
 *
 * Collapsing "failed" into "empty" is the panel-level version of animating
 * work that is not happening: it reads as a confident statement about the
 * world that was never actually checked. Keeping them apart is why every panel
 * must supply its own `empty` sentence. `absent` (M18.3) is the same argument
 * once more: a route another track is still building has told us nothing, and
 * an empty list would say it told us there is nothing.
 *
 * **B24 req 714.** The two outcomes with nothing to draw now draw NOTHING. The
 * audit counted thirteen of the twenty-seven panels empty at once, and thirteen
 * titles over thirteen "henüz yok" sentences is a page that reads as a system
 * doing nothing. The sentences did not become untrue — they moved to
 * `/availability`, which lists all 27 families with what each answered, so a
 * panel that is not on the page has an address rather than a mystery. `empty`
 * and `isEmpty` stay in this API because the availability page and the tests
 * both need to know what the panel WOULD have said.
 */

import Link from "next/link";

import type { Loaded } from "../../lib/cockpit/api";
import { familyQuality, isQuiet } from "../../lib/cockpit/families";
import { FAILURE_HINT, FAILURE_LABEL } from "../../lib/errors/failure";

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
  /** req 708: a retry the panel's owner supplies; the control only appears when it can help. */
  onRetry?: () => void;
  id: string;
  /**
   * B24: turns req 714's hiding OFF.
   *
   * On the cockpit, twenty-seven panels compete for one column and an empty one is noise.
   * On the family's OWN page (`/alarms`, `/routines`, …) the same emptiness is the answer
   * the owner came for, and a page that hid its only content would be a worse dead end
   * than the empty panel ever was. A prop rather than a context because these components
   * are also called as plain functions by their tests, where hooks cannot run.
   */
  always?: boolean;
};

/**
 * The three not-yet-loaded outcomes in their words, and nothing for `ok`.
 *
 * Split out of `Panel` (M21) for the panels that draw their own section —
 * a bus line above a REST list — so "yükleniyor", "alınamadı" and "henüz
 * yok" are spelled once and mean the same thing under every title.
 */
export function LoadedNotice<T>({
  state,
  onRetry,
}: {
  state: Loaded<T>;
  /** req 708: shown only for a failure that could plausibly clear on its own. */
  onRetry?: () => void;
}) {
  return (
    <>
      {state.kind === "loading" && (
        <p className="panel-empty" data-panel-loading>
          yükleniyor…
        </p>
      )}

      {state.kind === "failed" && (
        // Never an empty list: we do not know whether it is empty.
        //
        // B22 req 708-711: and WHICH failure it is decides the words and the control. A
        // provider that is not configured, a permission that is not granted and a decision
        // that is the owner's are three waits, not three errors, and none of them is
        // helped by a retry button. `failure` is absent only for a panel constructed by
        // hand in a test, which keeps the old line.
        <p
          className="panel-unknown"
          data-panel-failed
          data-failure-kind={state.failure?.kind ?? "failed"}
          data-failure-class={state.failure?.errorClass ?? ""}
        >
          {state.failure
            ? `${FAILURE_LABEL[state.failure.kind]}: ${state.failure.message}`
            : `Alınamadı: ${state.error}`}
          {state.failure && state.failure.kind !== "failed" && (
            <span className="panel-hint" data-failure-hint>
              {" "}
              {FAILURE_HINT[state.failure.kind]}
            </span>
          )}
          {onRetry && state.failure?.retryable && (
            <button
              type="button"
              className="panel-retry"
              data-panel-retry
              onClick={onRetry}
            >
              Tekrar dene
            </button>
          )}
        </p>
      )}

      {state.kind === "absent" && (
        // A route that does not exist yet. Distinct from both "empty" and
        // "failed": the question could not be asked, so no answer is implied.
        //
        // B24 req 714: a panel whose ONLY content would be this line does not
        // render at all (see `Panel` and `quietSection` below). This branch is
        // still reached by the mixed panels, where a live bus caption is worth
        // showing even though the REST family is not on this Cloud Core.
        <p className="panel-unknown" data-panel-absent>
          Henüz yok. {state.detail}
        </p>
      )}
    </>
  );
}

/**
 * B24 req 714, for the panels that draw their own `<section>`.
 *
 * Nine panels pair a live bus caption with a REST list, so they cannot use
 * `Panel` and cannot use its rule either: a build running RIGHT NOW is worth a
 * panel even though the list of finished builds is empty. `told` is that
 * panel's own answer to "has the bus said anything about this family", and the
 * panel goes quiet only when both halves are silent.
 */
export function quietSection<T>(
  state: Loaded<T>,
  isEmpty: (value: T) => boolean,
  told: boolean,
): boolean {
  return !told && isQuiet(familyQuality(state, isEmpty));
}

/**
 * The one line the cockpit keeps when families go quiet.
 *
 * Without it, req 714 would trade thirteen misleading panels for a page that
 * silently omits thirteen things — which is the same failure wearing the other
 * coat. It names them and links to the page that explains each one.
 */
export function QuietFamilies({ labels }: { labels: readonly string[] }) {
  if (labels.length === 0) return null;
  return (
    <p className="panel-quiet" data-panel-quiet={labels.length}>
      {labels.length} aile şu an sessiz: {labels.join(", ")}.{" "}
      <Link href="/availability" data-quiet-link>
        Özellik durumu
      </Link>
    </p>
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
  onRetry,
  id,
  always = false,
}: PanelProps<T>) {
  const highlighted = state.kind === "ok" && attention?.(state.value) === true;

  // req 714: an empty family produces no panel. `failed` and `loading` are never
  // quiet — one is a question we could not get answered and the other is a
  // question still in flight, and hiding either would claim an answer we do not
  // have. On the family's own page nothing hides at all.
  if (!always && isQuiet(familyQuality(state, isEmpty))) return null;

  return (
    <section
      // B24 req 700: `/availability` links to `#<panel>`, so the id has to be a real
      // anchor and not only a data attribute. It was neither until this batch — B23's
      // "addressable at #devices" was addressable in the test and nowhere else.
      id={id}
      className={`panel ${highlighted ? "attention" : ""}`}
      data-panel={id}
      data-panel-state={state.kind}
      data-panel-failure={state.kind === "failed" ? (state.failure?.kind ?? "failed") : ""}
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

      <LoadedNotice state={state} onRetry={onRetry} />

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
