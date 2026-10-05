/**
 * The 'Ne demek istemiştin?' page, rendered with fixtures.
 *
 * No DOM here (vitest runs in node): the markup is asserted through react-dom/server, and a
 * PRESS is the button's own onClick taken from the rendered element tree and called - so
 * the test exercises the wiring the page ships, not a copy of it.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";

import { isValidElement, type ReactElement, type ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import MisheardView, { type MisheardViewProps } from "../../app/core/misheard/MisheardView";
import type { MisheardList } from "../../app/core/misheard/misheardApi";
import { three } from "./fixtures";

type Props = Record<string, unknown> & { children?: ReactNode };

function props(over: Partial<MisheardViewProps> = {}): MisheardViewProps {
  return {
    list: three(),
    error: null,
    drafts: {},
    notice: null,
    busy: false,
    onDraft: () => {},
    onSave: () => {},
    onDelete: () => {},
    onForgetAll: () => {},
    timeZone: "Europe/Istanbul",
    ...over,
  };
}

function html(over: Partial<MisheardViewProps> = {}): string {
  return renderToStaticMarkup(<MisheardView {...props(over)} />);
}

/** Every host element in the tree, function components expanded (they hold no hooks). */
function elements(node: ReactNode): ReactElement<Props>[] {
  if (Array.isArray(node)) return node.flatMap(elements);
  if (!isValidElement<Props>(node)) return [];
  if (typeof node.type === "function") {
    return elements((node.type as (p: Props) => ReactNode)(node.props));
  }
  return [node, ...elements(node.props.children)];
}

function text(node: ReactNode): string {
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(text).join("");
  if (isValidElement<Props>(node)) return text(node.props.children);
  return "";
}

function tree(over: Partial<MisheardViewProps> = {}): ReactElement<Props>[] {
  return elements(<MisheardView {...props(over)} />);
}

function buttons(all: ReactElement<Props>[], label: string): ReactElement<Props>[] {
  return all.filter((element) => element.type === "button" && text(element.props.children) === label);
}

function press(button: ReactElement<Props>): void {
  (button.props.onClick as () => void)();
}

describe("the list", () => {
  it("has three rows for three items, and the head says two are open", () => {
    const markup = html();
    expect(markup.match(/data-misheard-row=/g)).toHaveLength(3);
    expect(markup).toContain('data-misheard-open="2"');
    expect(markup).toContain("ışığı biraz kıs");
    expect(markup).toContain("Annemi değil, ablamı ara.");
    expect(markup).not.toMatch(/null|undefined/);
  });

  it("builds the retention sentence from retention_days, so the number is not typed in", () => {
    const seven = html({ list: three(7) });
    const retention = seven.match(/data-misheard-retention[^>]*>([^<]*)</)?.[1] ?? "";
    expect(retention).toContain("7");
    expect(retention).not.toContain("30");
    expect(html()).toMatch(/data-misheard-retention[^>]*>[^<]*30/);
  });

  it("an empty list says so in one sentence", () => {
    const empty: MisheardList = { items: [], open: 0, retention_days: 30 };
    const markup = html({ list: empty });
    expect(markup).toContain("data-misheard-empty");
    expect(markup).not.toContain("data-misheard-row=");
  });

  it("an answered row has no input; an open row has 'Ne demek istemiştin?'", () => {
    const all = tree();
    const inputs = all.filter((element) => element.type === "input");
    expect(inputs).toHaveLength(2);
    for (const input of inputs) expect(input.props["aria-label"]).toBe("Ne demek istemiştin?");
  });
});

describe("Kaydet", () => {
  const first = "aaaaaaaa-0000-4000-8000-000000000001";

  it("is disabled for an empty and for a 2001-character input", () => {
    for (const draft of ["", "a".repeat(2001)]) {
      const [kaydet] = buttons(tree({ drafts: { [first]: draft } }), "Kaydet");
      expect(kaydet.props.disabled, `${draft.length} characters`).toBe(true);
    }
    const [kaydet] = buttons(tree({ drafts: { [first]: "a".repeat(2000) } }), "Kaydet");
    expect(kaydet.props.disabled).toBe(false);
  });

  it("sends the typed words letter for letter, for that row", () => {
    const typed = "Şu ışığı söndür, ağabeyimin odasındakini değil";
    const onSave = vi.fn();
    const [kaydet] = buttons(tree({ drafts: { [first]: typed }, onSave }), "Kaydet");
    press(kaydet);
    expect(onSave).toHaveBeenCalledExactlyOnceWith(first, typed);
  });

  it("typing goes to that row's draft", () => {
    const onDraft = vi.fn();
    const [input] = tree({ onDraft }).filter((element) => element.type === "input");
    (input.props.onChange as (event: { target: { value: string } }) => void)({
      target: { value: "ğüşıöç" },
    });
    expect(onDraft).toHaveBeenCalledExactlyOnceWith(first, "ğüşıöç");
  });
});

describe("Sil", () => {
  it("sends that row's id", () => {
    const onDelete = vi.fn();
    const sil = buttons(tree({ onDelete }), "Sil");
    expect(sil).toHaveLength(3);
    press(sil[2]);
    expect(onDelete).toHaveBeenCalledExactlyOnceWith("aaaaaaaa-0000-4000-8000-000000000003");
  });
});

describe("Defteri unut", () => {
  it("is one press, no dialog", () => {
    const onForgetAll = vi.fn();
    const [unut] = buttons(tree({ onForgetAll }), "Defteri unut");
    press(unut);
    expect(onForgetAll).toHaveBeenCalledTimes(1);
    const dir = join(__dirname, "..", "..", "app", "core", "misheard");
    for (const file of ["MisheardView.tsx", "page.tsx", "misheardActions.ts"]) {
      const source = readFileSync(join(dir, file), "utf8");
      expect(source, file).not.toMatch(/confirm\(|prompt\(|<dialog/);
    }
  });

  it("then shows the deleted count the page was given", () => {
    expect(html({ notice: "Defter unutuldu: 4 cümle silindi." })).toContain(
      "Defter unutuldu: 4 cümle silindi.",
    );
  });
});

describe("a refusal", () => {
  it("shows the server's own sentence", () => {
    const markup = html({ list: null, error: "Defter şu an açılamıyor." });
    expect(markup).toContain("Defter şu an açılamıyor.");
    expect(markup).not.toContain("data-misheard-empty");
  });
});
