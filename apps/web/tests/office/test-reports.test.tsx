import { isValidElement, type ReactElement, type ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../app/lib/session", () => ({
  API_BASE: "http://core.test:8001",
  apiFetch: vi.fn(),
  UnauthorizedError: class UnauthorizedError extends Error {},
}));

import { apiFetch } from "../../app/lib/session";
import {
  fetchTestReport,
  fetchTestReports,
  TEST_REPORTS_PATH,
  TestReportsList,
  type TestReport,
  type TestReportSummary,
} from "../../app/core/office/OfficeTestReports";

// The owner, 2026-10-06: "test ekibinin yaptığı işlemleri ve aldığı sonuçların girdi çıktı olarak
// raporlarını istiyorum incelemek için". The Ofis lists the test team's round reports (round,
// date, geçti / kaldı / koptu) under the test seats; each opens its Girdi / Beklenen / Çıktı text.

const REPORTS: TestReportSummary[] = [
  {
    round: "t202610070100",
    at: "2026-10-07T01:00:00.000000Z",
    staging_sha: "e".repeat(40),
    counts: { passed: 2, failed: 1, broke: 1 },
    unfinished: "",
  },
  {
    round: "t202610062300",
    at: "2026-10-06T23:00:00.000000Z",
    staging_sha: "",
    counts: { passed: 0, failed: 0, broke: 0 },
    unfinished: "kuyruk okunamadı",
  },
];

const OPENED: TestReport = {
  ...REPORTS[0],
  text: "# Test turu t202610070100\n\nGirdi:\n````\nGET /v1/system/health\n````\n\nÇıktı:\n````\n200\n````\n",
};

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

/** Every element of a rendered tree (no DOM in node: the elements a component returns). */
function elements(node: ReactNode): ReactElement<Record<string, unknown>>[] {
  if (Array.isArray(node)) return node.flatMap(elements);
  if (!isValidElement(node)) return [];
  const element = node as ReactElement<Record<string, unknown>>;
  return [element, ...elements(element.props.children as ReactNode)];
}

beforeEach(() => vi.mocked(apiFetch).mockReset());

describe("the Ofis' test reports", () => {
  it("lists each round with its date and its geçti / kaldı / koptu counts, a dead round marked", () => {
    const html = renderToStaticMarkup(<TestReportsList reports={REPORTS} opened={null} onOpen={() => {}} />);
    expect(html).toContain("Test raporları");
    expect(html.match(/data-test-report="/g)).toHaveLength(2);
    expect(html).toContain('data-test-report="t202610070100"');
    expect(html).toContain("2026-10-07 01:00");
    expect(html).toMatch(/2 geçti · 1 kaldı · 1 koptu/);
    expect(html).toMatch(/0 geçti · 0 kaldı · 0 koptu/);
    expect(html).toContain("yarım kaldı: kuyruk okunamadı");
    // newest first, as the Cloud Core sends them
    expect(html.indexOf("t202610070100")).toBeLessThan(html.indexOf("t202610062300"));
    expect(html).not.toContain("Girdi:");
  });

  it("an empty list says so", () => {
    const html = renderToStaticMarkup(<TestReportsList reports={[]} opened={null} onOpen={() => {}} />);
    expect(html).toContain("Henüz test raporu yok");
  });

  it("a round's button opens that round's report", () => {
    const onOpen = vi.fn();
    const tree = TestReportsList({ reports: REPORTS, opened: null, onOpen });
    const buttons = elements(tree).filter((e) => e.type === "button");
    expect(buttons).toHaveLength(2);
    (buttons[1].props.onClick as () => void)();
    expect(onOpen).toHaveBeenCalledWith("t202610062300");
  });

  it("an opened report shows its whole text, Girdi and Çıktı as written", () => {
    const html = renderToStaticMarkup(<TestReportsList reports={REPORTS} opened={OPENED} onOpen={() => {}} />);
    expect(html).toMatch(/<pre[^>]*class="office-test-report-text"[^>]*>/);
    expect(html).toContain("Girdi:");
    expect(html).toContain("GET /v1/system/health");
    expect(html).toContain("Çıktı:");
    expect(html).toContain('aria-pressed="true"');
  });

  it("reads the list and one report from the Cloud Core's owner route", async () => {
    vi.mocked(apiFetch).mockResolvedValueOnce(json({ reports: REPORTS }));
    expect(await fetchTestReports()).toEqual(REPORTS);
    expect(vi.mocked(apiFetch).mock.calls[0][0]).toBe(TEST_REPORTS_PATH);
    expect(TEST_REPORTS_PATH).toBe("/v1/team/test-reports");
    vi.mocked(apiFetch).mockResolvedValueOnce(json({ report: OPENED }));
    expect(await fetchTestReport("t202610070100")).toEqual(OPENED);
    expect(vi.mocked(apiFetch).mock.calls[1][0]).toBe("/v1/team/test-reports/t202610070100");
  });

  it("an unreachable or refusing Cloud Core is an empty list and no report, never an error", async () => {
    vi.mocked(apiFetch).mockRejectedValueOnce(new Error("down"));
    expect(await fetchTestReports()).toEqual([]);
    vi.mocked(apiFetch).mockResolvedValueOnce(json({ detail: "unauthorized" }, 401));
    expect(await fetchTestReports()).toEqual([]);
    vi.mocked(apiFetch).mockResolvedValueOnce(json({ detail: {} }, 404));
    expect(await fetchTestReport("t-yok")).toBeNull();
  });
});
