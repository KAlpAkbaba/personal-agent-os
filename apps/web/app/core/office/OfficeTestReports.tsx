"use client";

/**
 * The Ofis' "Test raporları" (the owner, 2026-10-06: "test ekibinin yaptığı işlemleri ve aldığı
 * sonuçların girdi çıktı olarak raporlarını istiyorum incelemek için"): the test team's round
 * reports under the test seats - round, date, geçti / kaldı / koptu - each opening its whole
 * text, per tester and per step Girdi / Beklenen / Çıktı / Sonuç.
 *
 * scripts/testteam/test-round.ps1 POSTs the report at the end of every round; the Cloud Core
 * keeps the last 50 (services/api/app/team/test_reports.py, owner session only). An unreachable
 * or refusing Cloud Core is an empty list, never an error on the page.
 */

import { useCallback, useEffect, useState } from "react";

import { apiFetch } from "../../lib/session";

export const TEST_REPORTS_PATH = "/v1/team/test-reports";

export type TestReportCounts = { passed: number; failed: number; broke: number };

export type TestReportSummary = {
  round: string;
  /** The Cloud Core's stamp, UTC, microseconds: `2026-10-07T01:00:00.000000Z`. */
  at: string;
  staging_sha: string;
  counts: TestReportCounts;
  /** Why the round died; "" for a whole round. */
  unfinished: string;
};

export type TestReport = TestReportSummary & { text: string };

/** The list, newest first; [] when the Cloud Core cannot be read. */
export async function fetchTestReports(): Promise<TestReportSummary[]> {
  try {
    const response = await apiFetch(TEST_REPORTS_PATH);
    if (!response.ok) return [];
    const body = (await response.json()) as { reports?: TestReportSummary[] };
    return Array.isArray(body.reports) ? body.reports : [];
  } catch {
    return [];
  }
}

/** One round's report with its text; null when it cannot be read. */
export async function fetchTestReport(round: string): Promise<TestReport | null> {
  try {
    const response = await apiFetch(`${TEST_REPORTS_PATH}/${encodeURIComponent(round)}`);
    if (!response.ok) return null;
    const body = (await response.json()) as { report?: TestReport };
    return body.report ?? null;
  } catch {
    return null;
  }
}

/** "2026-10-07 01:00 UTC" from the Cloud Core's stamp. */
function shownDate(at: string): string {
  return /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/.test(at) ? `${at.slice(0, 10)} ${at.slice(11, 16)} UTC` : at;
}

export function countsText(counts: TestReportCounts): string {
  return `${counts.passed} geçti · ${counts.failed} kaldı · ${counts.broke} koptu`;
}

/** The list and the opened report; pure (no hooks), so the page's state lives in the container. */
export function TestReportsList({
  reports,
  opened,
  onOpen,
}: {
  reports: TestReportSummary[];
  opened: TestReport | null;
  onOpen: (round: string) => void;
}) {
  return (
    <section className="office-test-reports" aria-label="Test raporları">
      <h3>Test raporları</h3>
      {reports.length === 0 ? (
        <p className="office-test-reports-empty">Henüz test raporu yok.</p>
      ) : (
        <ul>
          {reports.map((report) => (
            <li key={report.round} data-test-report={report.round}>
              <button
                type="button"
                aria-pressed={opened?.round === report.round}
                onClick={() => onOpen(report.round)}
              >
                <span className="office-test-report-round">{report.round}</span>
                <span className="office-test-report-date">{shownDate(report.at)}</span>
                <span className="office-test-report-counts">{countsText(report.counts)}</span>
                {report.unfinished && (
                  <span className="office-test-report-unfinished">yarım kaldı: {report.unfinished}</span>
                )}
              </button>
            </li>
          ))}
        </ul>
      )}
      {opened && (
        <article className="office-test-report" aria-label={`Test turu ${opened.round} raporu`}>
          <pre className="office-test-report-text">{opened.text}</pre>
        </article>
      )}
    </section>
  );
}

/** The "Test raporları" list as the Ofis shows it: read once, re-read on every open. */
export default function OfficeTestReports() {
  const [reports, setReports] = useState<TestReportSummary[]>([]);
  const [opened, setOpened] = useState<TestReport | null>(null);
  useEffect(() => {
    let live = true;
    void fetchTestReports().then((list) => {
      if (live) setReports(list);
    });
    return () => {
      live = false;
    };
  }, []);
  const onOpen = useCallback(
    (round: string) => {
      if (opened?.round === round) {
        setOpened(null);
        return;
      }
      void fetchTestReport(round).then(setOpened);
    },
    [opened],
  );
  return <TestReportsList reports={reports} opened={opened} onOpen={onOpen} />;
}
