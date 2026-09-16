"use client";

/**
 * `⌘K` / `Ctrl+K` — B25 req 702 (search) and 703 (command palette).
 *
 * They are one component on purpose: the matrix makes 703 depend on 702, and a search box
 * that finds things you cannot then go to is half a feature. The palette IS the search.
 *
 * What it searches is everything this product can currently name, and all three sources are
 * lists the product already keeps, never a fourth list written here:
 *
 *   * the pages, from `NAV` — the same list the nav and the home index render;
 *   * the cockpit's panels, from `FAMILIES` — with the anchor each one now has (B24);
 *   * what the owner may SAY, from the Cloud Core's tool registry (req 701).
 *
 * The last of those is the reason this exists at all. The audit scored discoverability
 * 0.5/5: a voice-first system whose owner cannot find out what to say to it is a system
 * with a manual nobody wrote. Typing "alarm" here now answers both "where do I look" and
 * "what do I say".
 *
 * **The keyboard is the point** (req 725). Opening, moving, choosing and leaving are all
 * keys; the pointer is the alternative, not the requirement. Focus goes into the box on
 * open and returns to whatever had it on close, because a palette that drops focus on the
 * body leaves a keyboard user at the top of the document with no way back.
 */

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { NAV } from "./SiteNav";
import { FAMILIES } from "../lib/cockpit/families";
import {
  type CapabilityList,
  capabilityAnchor,
  fetchCapabilities,
} from "../lib/pages/capabilities";
import type { Loaded } from "../lib/cockpit/api";

/** How many results one section shows before it stops and says how many more there are. */
export const SECTION_LIMIT = 6;

export type PaletteKind = "page" | "panel" | "say";

export type PaletteItem = {
  kind: PaletteKind;
  /** Stable within a render, and the `id` the listbox points `aria-activedescendant` at. */
  id: string;
  label: string;
  hint: string;
  href: string;
  /** Every word this item can be found by, already lower-cased for `tr-TR`. */
  haystack: string;
};

export const KIND_LABEL: Record<PaletteKind, string> = {
  page: "Sayfalar",
  panel: "Paneller",
  say: "Söyleyebilecekleriniz",
};

const KIND_ORDER: PaletteKind[] = ["page", "panel", "say"];

function fold(text: string): string {
  return text.toLocaleLowerCase("tr-TR");
}

/**
 * Everything the palette can find, from the three lists the product already keeps.
 *
 * `capabilities` is `null` until the palette is first opened — a page that the owner never
 * opens the palette on should not fetch a hundred and thirty tool descriptions.
 */
export function paletteItems(capabilities: CapabilityList | null): PaletteItem[] {
  const items: PaletteItem[] = NAV.map((entry) => ({
    kind: "page" as const,
    id: `page:${entry.href}`,
    label: entry.label,
    hint: entry.hint,
    href: entry.href,
    haystack: fold(`${entry.label} ${entry.hint} ${entry.href}`),
  }));

  for (const family of FAMILIES) {
    // One panel can serve two families (research and its focus); the list is addressed by
    // panel, so the second mention would be a duplicate row the owner cannot tell apart.
    if (items.some((item) => item.id === `panel:${family.panel}`)) continue;
    items.push({
      kind: "panel",
      id: `panel:${family.panel}`,
      label: family.label,
      hint: `${family.page} · panel`,
      href: `${family.page}#${family.panel}`,
      haystack: fold(`${family.label} ${family.key} ${family.panel}`),
    });
  }

  // The one panel that belongs to no data family, and therefore cannot come from
  // `FAMILIES`. req 660's whole target is the word *bulunabilir* — findable — and a kill
  // switch reachable only by remembering which page it is on is not. Named here rather
  // than derived because there is nothing to derive it from.
  items.push({
    kind: "panel",
    id: "panel:panic",
    label: "Acil durdurma",
    hint: "/security · bütün oturumları iptal et",
    href: "/security#panic",
    haystack: fold("acil durdurma panik oturum iptal kill switch güvenlik panic"),
  });

  for (const row of capabilities?.rows ?? []) {
    items.push({
      kind: "say",
      id: `say:${row.name}`,
      // The owner's own sentence is the label when there is one; the summary otherwise.
      label: row.phrases[0] ?? row.summary,
      hint: `${row.familyTr} · ${row.summary}`,
      href: `/voice#${capabilityAnchor(row.name)}`,
      haystack: fold(`${row.phrases.join(" ")} ${row.summary} ${row.familyTr} ${row.name}`),
    });
  }

  return items;
}

/** What matches, in section order, each section capped. */
export function search(items: PaletteItem[], query: string): PaletteItem[] {
  const needle = fold(query.trim());
  const matched = needle === "" ? items : items.filter((item) => item.haystack.includes(needle));
  const out: PaletteItem[] = [];
  for (const kind of KIND_ORDER) {
    out.push(...matched.filter((item) => item.kind === kind).slice(0, SECTION_LIMIT));
  }
  return out;
}

/** True when a keystroke happened somewhere the owner is typing, so `/` means a slash. */
export function isTypingTarget(target: EventTarget | null): boolean {
  const node = target as HTMLElement | null;
  if (!node || typeof node.tagName !== "string") return false;
  const tag = node.tagName.toLowerCase();
  return tag === "input" || tag === "textarea" || tag === "select" || node.isContentEditable === true;
}

/** A keystroke, reduced to the four facts the decision below needs. */
export type PaletteKey = {
  key: string;
  ctrlKey?: boolean;
  metaKey?: boolean;
  /** True when the owner is typing into a field, so `/` is a slash and not a shortcut. */
  typing?: boolean;
};

export type PaletteAction =
  | { kind: "open" }
  | { kind: "close" }
  | { kind: "move"; active: number }
  | { kind: "choose"; index: number };

/**
 * The whole keyboard model, as one pure function (req 725).
 *
 * Pure because this suite runs in Node with no DOM, and a keyboard contract asserted by
 * reading the component's source for the word "ArrowDown" is not a contract — it is a
 * spelling check. Every key, in every state, is decided here and tested here; the
 * component only wires events to it and applies what comes back.
 *
 * Returning `null` means "not ours": the keystroke belongs to the page, or to the text
 * box, and swallowing it would break typing.
 */
export function paletteAction(
  event: PaletteKey,
  state: { open: boolean; count: number; active: number },
): PaletteAction | null {
  const key = event.key;
  if ((event.ctrlKey || event.metaKey) && key.toLowerCase() === "k") {
    return state.open ? { kind: "close" } : { kind: "open" };
  }
  if (!state.open) {
    // `/` is the second opener, and only where it is not a character the owner is typing.
    return key === "/" && !event.typing ? { kind: "open" } : null;
  }
  if (key === "Escape") return { kind: "close" };
  if (state.count === 0) return null;
  // The list wraps, so ↓ at the bottom is the top: a palette that stops dead at the last
  // row makes the owner reach for the pointer to get back.
  if (key === "ArrowDown") return { kind: "move", active: (state.active + 1) % state.count };
  if (key === "ArrowUp") {
    return { kind: "move", active: (state.active - 1 + state.count) % state.count };
  }
  if (key === "Enter") return { kind: "choose", index: Math.min(state.active, state.count - 1) };
  return null;
}

/**
 * Where focus goes when the palette closes.
 *
 * Not simply "back to whatever had it": the thing that most often had it is the palette's
 * OWN open button, and that button is unmounted while the palette is open. Focusing the
 * remembered node then focuses a detached element, which does nothing at all — measured in
 * a browser, where `document.activeElement` came back as `<body>` and a keyboard user was
 * left at the top of the document. A node that is no longer in the document is not a place
 * to return to; the freshly mounted button is.
 */
export function focusTargetAfterClose<T extends { isConnected: boolean }>(
  stored: T | null,
  fallback: T | null,
): T | null {
  return stored && stored.isConnected ? stored : fallback;
}

export default function CommandPalette() {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const [caps, setCaps] = useState<Loaded<CapabilityList> | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);
  const openButtonRef = useRef<HTMLButtonElement | null>(null);
  const restoreTo = useRef<HTMLElement | null>(null);
  const wasOpen = useRef(false);

  const items = useMemo(
    () => paletteItems(caps?.kind === "ok" ? caps.value : null),
    [caps],
  );
  const results = useMemo(() => search(items, query), [items, query]);

  const close = useCallback(() => {
    setOpen(false);
    setQuery("");
    setActive(0);
    // Focus is restored in the effect below, AFTER the re-render: the element it usually
    // goes back to is this component's own button, which does not exist until then.
  }, []);

  const show = useCallback(() => {
    restoreTo.current = (document.activeElement as HTMLElement | null) ?? null;
    setOpen(true);
  }, []);

  // The one global shortcut, plus `/` when the owner is not typing into something. Only
  // the two that can happen with the palette CLOSED are handled here; the rest belong to
  // the dialog, so a stray Escape on a page never looks like a palette key.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const action = paletteAction(
        {
          key: event.key,
          ctrlKey: event.ctrlKey,
          metaKey: event.metaKey,
          typing: isTypingTarget(event.target),
        },
        { open, count: 0, active: 0 },
      );
      if (action?.kind === "open") {
        event.preventDefault();
        show();
      } else if (action?.kind === "close") {
        event.preventDefault();
        close();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, show, close]);

  // Ask for the capability list once, the first time the palette is actually opened.
  useEffect(() => {
    if (!open || caps !== null) return;
    let alive = true;
    void fetchCapabilities().then((next) => {
      if (alive) setCaps(next);
    });
    return () => {
      alive = false;
    };
  }, [open, caps]);

  useEffect(() => {
    if (open) {
      wasOpen.current = true;
      inputRef.current?.focus();
      return;
    }
    if (!wasOpen.current) return;
    wasOpen.current = false;
    // Back to whatever the owner was on. Dropping focus on the body would leave a keyboard
    // user at the top of the document with nothing to say where they had been.
    focusTargetAfterClose<HTMLElement>(restoreTo.current, openButtonRef.current)?.focus();
  }, [open]);

  useEffect(() => {
    setActive(0);
  }, [query]);

  if (!open) {
    return (
      <button
        ref={openButtonRef}
        type="button"
        className="palette-open"
        data-palette-open
        onClick={show}
        aria-keyshortcuts="Control+K Meta+K"
      >
        Ara <kbd>Ctrl</kbd>+<kbd>K</kbd>
      </button>
    );
  }

  const current = results[Math.min(active, Math.max(results.length - 1, 0))];

  const choose = (item: PaletteItem | undefined) => {
    if (!item) return;
    close();
    router.push(item.href);
  };

  return (
    <div
      className="palette-backdrop"
      data-palette
      // A click outside closes; the keyboard's way out is Escape, below.
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) close();
      }}
    >
      <div
        className="palette"
        role="dialog"
        aria-modal="true"
        aria-label="Komut paleti"
        onKeyDown={(event) => {
          const action = paletteAction(
            { key: event.key, ctrlKey: event.ctrlKey, metaKey: event.metaKey, typing: true },
            { open: true, count: results.length, active },
          );
          if (!action) return;
          event.preventDefault();
          if (action.kind === "close") close();
          else if (action.kind === "move") setActive(action.active);
          else if (action.kind === "choose") choose(results[action.index]);
        }}
      >
        <input
          ref={inputRef}
          type="text"
          className="palette-input"
          data-palette-input
          value={query}
          placeholder="Sayfa, panel ya da söyleyebileceğiniz bir şey"
          aria-label="Ara"
          role="combobox"
          aria-expanded="true"
          aria-controls="palette-results"
          aria-autocomplete="list"
          aria-activedescendant={current ? current.id : undefined}
          onChange={(event) => setQuery(event.target.value)}
        />

        <ul className="palette-results" id="palette-results" role="listbox" aria-label="Sonuçlar">
          {results.length === 0 && (
            <li className="palette-empty" data-palette-empty>
              Eşleşen bir şey yok.
            </li>
          )}
          {results.map((item, index) => {
            // The section header belongs to the FIRST item of its kind. Derived from the
            // neighbour rather than from a variable carried across the map: a value
            // reassigned during render is a value that can disagree with itself.
            const header =
              index === 0 || results[index - 1].kind !== item.kind ? KIND_LABEL[item.kind] : null;
            const selected = item === current;
            return (
              <li key={item.id} className="palette-group">
                {header && (
                  <p className="palette-section" data-palette-section={item.kind}>
                    {header}
                  </p>
                )}
                <div
                  id={item.id}
                  role="option"
                  aria-selected={selected}
                  data-palette-item={item.kind}
                  data-palette-current={selected ? "yes" : "no"}
                  className={selected ? "palette-item current" : "palette-item"}
                  onMouseEnter={() => setActive(index)}
                  onClick={() => choose(item)}
                >
                  <Link href={item.href} tabIndex={-1} onClick={(event) => event.preventDefault()}>
                    {item.label}
                  </Link>
                  <span className="muted"> {item.hint}</span>
                </div>
              </li>
            );
          })}
        </ul>

        <p className="palette-foot muted">
          <kbd>↑</kbd> <kbd>↓</kbd> gez · <kbd>Enter</kbd> aç · <kbd>Esc</kbd> kapat
          {caps?.kind === "failed" && " · yetenek listesi alınamadı"}
          {caps?.kind === "absent" && " · bu Cloud Core yetenek listesini sunmuyor"}
        </p>
      </div>
    </div>
  );
}
