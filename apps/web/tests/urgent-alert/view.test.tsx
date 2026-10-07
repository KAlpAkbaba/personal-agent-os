/**
 * The phone alarm's page, rendered with fixtures (vitest runs in node: react-dom/server for
 * the markup, the button's own onClick taken from the element tree for a press).
 */

import { isValidElement, type ReactElement, type ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import UrgentAlertView, {
  type UrgentAlertViewProps,
} from "../../app/core/urgent-alert/UrgentAlertView";
import type { UrgentAlertStatus } from "../../app/core/urgent-alert/urgentAlertApi";

const CONNECTED: UrgentAlertStatus = {
  configured: true,
  open_receipts: 0,
  last_seen_at: "2026-10-07T12:01:00Z",
  last_outcome: "seen",
};

function props(over: Partial<UrgentAlertViewProps> = {}): UrgentAlertViewProps {
  return {
    status: CONNECTED,
    error: null,
    notice: null,
    busy: false,
    onTest: () => {},
    timeZone: "Europe/Istanbul",
    ...over,
  };
}

function html(over: Partial<UrgentAlertViewProps> = {}): string {
  return renderToStaticMarkup(<UrgentAlertView {...props(over)} />);
}

type Props = Record<string, unknown> & { children?: ReactNode };

function findButton(node: ReactNode): ReactElement<Props> | null {
  if (Array.isArray(node)) {
    for (const child of node) {
      const hit = findButton(child);
      if (hit) return hit;
    }
    return null;
  }
  if (!isValidElement(node)) return null;
  const element = node as ReactElement<Props>;
  if (element.type === "button") return element;
  return findButton(element.props.children);
}

describe("the phone alarm page", () => {
  it("says connected, shows 'görüldü HH:MM' and the test button", () => {
    const page = html();
    expect(page).toContain('data-urgent-alert-connected="yes"');
    expect(page).toContain(">bağlı<");
    expect(page).toContain("görüldü 15:01");
    expect(page).toContain("Önemli deneme bildirimi gönder");
  });

  it("has no test button while nothing is connected", () => {
    const page = html({
      status: { configured: false, open_receipts: 0, last_seen_at: null, last_outcome: null },
    });
    expect(page).toContain("bağlı değil");
    expect(page).not.toContain("<button");
    expect(page).not.toContain("Önemli deneme bildirimi gönder");
  });

  it("says 'görülmedi' when the last alarm rang out", () => {
    const page = html({
      status: { ...CONNECTED, last_seen_at: null, last_outcome: "unseen" },
    });
    expect(page).toContain("görülmedi");
  });

  it("the button calls onTest", () => {
    const onTest = vi.fn();
    const tree = UrgentAlertView(props({ onTest }));
    const button = findButton(tree);
    expect(button).not.toBeNull();
    (button!.props.onClick as () => void)();
    expect(onTest).toHaveBeenCalledTimes(1);
  });

  it("shows the server's sentence when the status cannot be read", () => {
    const page = html({ status: null, error: "HTTP 503" });
    expect(page).toContain('role="alert"');
    expect(page).toContain("HTTP 503");
    expect(page).not.toContain("<button");
  });
});
