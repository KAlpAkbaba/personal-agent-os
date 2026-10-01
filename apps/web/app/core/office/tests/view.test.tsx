import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";

const apiFetch = vi.fn();
vi.mock("../../../lib/session", () => ({
  API_BASE: "http://core.test:8001",
  apiFetch: (...args: unknown[]) => apiFetch(...args),
  UnauthorizedError: class UnauthorizedError extends Error {},
}));

import { OFFICE_PATH, fetchOffice } from "../officeApi";
import OfficeView from "../OfficeView";
import { twoWorkers } from "./fixtures";

function render(over: Partial<Parameters<typeof OfficeView>[0]> = {}) {
  return renderToStaticMarkup(
    <OfficeView
      view={twoWorkers()}
      selected={null}
      offline={false}
      reducedMotion={false}
      onSelect={() => {}}
      {...over}
    />,
  );
}

beforeEach(() => apiFetch.mockReset());

describe("the office fetch", () => {
  it("asks the contract's route and returns the body", async () => {
    apiFetch.mockResolvedValue(new Response(JSON.stringify(twoWorkers()), { status: 200 }));
    const view = await fetchOffice();
    expect(apiFetch).toHaveBeenCalledWith(OFFICE_PATH);
    expect(OFFICE_PATH).toBe("/v1/team/office");
    expect(view.cycle.cycle_id).toBe("cycle-2026-10-01");
  });

  it("throws on a refusal, so the page keeps the last office", async () => {
    apiFetch.mockResolvedValue(new Response("{}", { status: 503 }));
    await expect(fetchOffice()).rejects.toThrow();
  });
});

describe("the office view", () => {
  it("draws eight seats as buttons with aria-pressed and a role+state label", () => {
    const html = render({ selected: "worker-1" });
    expect(html.match(/data-seat="/g)).toHaveLength(8);
    expect(html).toContain('aria-label="Çalışan 1, çalışıyor"');
    expect(html).toMatch(/data-seat="worker-1" aria-pressed="true"/);
    expect(html).toMatch(/data-seat="lead" aria-pressed="false"/);
  });

  it("puts the task title above a working head and types", () => {
    const html = render();
    expect(html).toContain("Birinci iş");
    expect(html).toContain("office-typing");
    expect(html).toContain("2/6");
    expect(html).toContain("tahmini $3.50");
    expect(html).toContain("cycle-2026-10-01");
  });

  it("marks a returned seat with the warning", () => {
    expect(render()).toContain('data-warning="true"');
  });

  it("renders no animation class under reduced motion, with the static badge instead", () => {
    const html = render({ reducedMotion: true });
    expect(html).not.toContain("office-typing");
    expect(html).toContain("çalışıyor");
    expect(html).toContain("office-badge-static");
  });

  it("shows the clicked seat's panel: card, 40 lines, branch, 12-char sha with full title", () => {
    const html = render({ selected: "worker-1" });
    expect(html).toContain("birinci hedef");
    expect(html).toContain("birinci kabul");
    expect(html).toContain("satır 40");
    expect(html).not.toContain("satır 41");
    expect(html).toContain("team/cycle/worker-one");
    expect(html).toContain(">0123456789ab<");
    expect(html).toContain('title="0123456789abcdef0123456789abcdef01234567"');
  });

  it("asks for a click when no seat is selected", () => {
    expect(render()).toContain("koltuğa tıklayın");
  });

  it("lists the approvals with their gate, a link to /core/approvals, and the owner's count", () => {
    const html = render();
    expect(html).toContain("Fikir bir");
    expect(html).toContain("Yayın onayı");
    expect(html).toContain('href="/core/approvals"');
    expect(html).toMatch(/data-seat="owner"[\s\S]*?data-count="2"/);
  });

  it("says bağlantı yok on a failed fetch but still draws the last office", () => {
    const html = render({ offline: true });
    expect(html).toContain("bağlantı yok");
    expect(html).toContain("Birinci iş");
    expect(render()).not.toContain("bağlantı yok");
  });
});
