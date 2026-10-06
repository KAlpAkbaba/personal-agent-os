/**
 * The Ofis' test team (the owner, 2026-10-03: "Test ekibi ve çalışan ekibi ayrı olsun; 4 test
 * ekibi çalışanı ve 1 proje yöneticisi olsun"; 2026-10-06: "Aynı ofiste olsunlar"): the Test
 * Proje Yöneticisi and four test çalışanları, in amber and teal, with the office's mood rules,
 * each seat's current job and the last breaking point - seated on the same office floor as the
 * software team, behind a "Test ekibi" divider (TestSeatCells, drawn by OfficeScene).
 *
 * The room reads the team's board and nothing else (scripts/testteam/test-round.ps1 posts the
 * notes): a tester's "iş: <job>" is its job now, its "sonuç: passed|failed|broke - ..." the
 * end of it, the test lead's "Danışman'a, ..." the last breaking-point report. Pure from the
 * notes; an unreachable board is five waiting seats, never an error on the page.
 */

import type { CSSProperties } from "react";

import { apiFetch } from "../../lib/session";
import { BOARD_PATH, BOARD_READ_LIMIT, type BoardNote } from "./officeBoard";
import { MOOD_TR, TIRED_AFTER_MIN, type Mood } from "./officeMood";
import { Figure } from "./officeRobots";

export const TEST_SEATS = ["test-lead", "tester-1", "tester-2", "tester-3", "tester-4"] as const;
export type TestSeatId = (typeof TEST_SEATS)[number];

export type TestSeatState = "working" | "waiting" | "failed" | "broke";

export type TestSeat = {
  seat: TestSeatId;
  state: TestSeatState;
  job: string | null;
  since: string | null;
  breaking: string | null;
};

const JOB = /^iş:\s*(.+)$/;
// "sonuç: <state> - <job> - <breaking>": the parts are split on " - " (a card id has hyphens).
const RESULT = /^sonuç:\s*(passed|failed|broke)\b(.*)$/;
const TO_ADVISOR = /^Danışman'a\b/;

/** Amber testers, a teal test lead: the test team's own colours on the office's figures. */
const PALETTE = {
  "--office-shirt": "#f4b13a",
  "--office-shirt-shade": "#d48f17",
  "--office-navy": "#14b8a6",
  "--office-navy-shade": "#0f766e",
} as CSSProperties;

function seatTitle(seat: TestSeatId): string {
  return seat === "test-lead" ? "Test Proje Yöneticisi" : `Test çalışanı ${seat.slice("tester-".length)}`;
}

/** The figure a test seat is drawn as: the lead's body for the test lead, a worker's for a tester. */
function figureSeat(seat: TestSeatId): string {
  return seat === "test-lead" ? "lead" : "worker-1";
}

function isTestSeat(seat: string): seat is TestSeatId {
  return (TEST_SEATS as readonly string[]).includes(seat);
}

/** The five seats from the board's notes (ids sort in writing order: the newest note wins). */
export function testRoomFromBoard(notes: BoardNote[], _now: Date): TestSeat[] {
  const seats = new Map<TestSeatId, TestSeat>(
    TEST_SEATS.map((seat) => [seat, { seat, state: "waiting", job: null, since: null, breaking: null }]),
  );
  const ordered = notes.toSorted((a, b) => (a.id < b.id ? -1 : a.id > b.id ? 1 : 0));
  for (const note of ordered) {
    if (!isTestSeat(note.seat)) continue;
    const seat = seats.get(note.seat);
    if (!seat) continue;
    const text = note.text.trim();
    const job = JOB.exec(text);
    if (job) {
      seat.state = "working";
      seat.job = job[1].trim();
      seat.since = note.at;
      continue;
    }
    const result = RESULT.exec(text);
    if (result) {
      const state = result[1];
      const [, jobPart, ...rest] = result[2].split(" - ");
      seat.state = state === "passed" ? "waiting" : (state as TestSeatState);
      if (jobPart?.trim()) seat.job = jobPart.trim();
      seat.since = null;
      if (state === "broke") seat.breaking = rest.length > 0 ? rest.join(" - ").trim() : text;
      continue;
    }
    if (TO_ADVISOR.test(text)) seat.breaking = text;
  }
  return TEST_SEATS.map((seat) => seats.get(seat) as TestSeat);
}

/** The office's mood rules (officeMood.ts) on a test seat. */
export function testMoodOf(seat: TestSeat, now: Date): Mood {
  if (seat.state === "failed") return "angry";
  if (seat.state === "broke") return "sad";
  if (seat.state === "working") {
    const since = seat.since ? new Date(seat.since).getTime() : Number.NaN;
    if (Number.isNaN(since)) return "focused";
    return (now.getTime() - since) / 60000 >= TIRED_AFTER_MIN ? "tired" : "focused";
  }
  return "relaxed";
}

const STATE_TR: Record<TestSeatState, string> = {
  working: "test ediyor",
  waiting: "iş bekliyor",
  failed: "hata buldu",
  broke: "kopma noktası buldu",
};

/** The spoken name of a test seat: "Test çalışanı 1: iş bekliyor, dinleniyor". */
export function testSeatAriaLabel(seat: TestSeat, now: Date): string {
  const base = `${seatTitle(seat.seat)}: ${STATE_TR[seat.state]}, ${MOOD_TR[testMoodOf(seat, now)]}`;
  const job = seat.job ? `, iş: ${seat.job}` : "";
  const breaking = seat.breaking ? `, son kopma noktası: ${seat.breaking}` : "";
  return base + job + breaking;
}

/**
 * The test team inside the office floor (the owner, 2026-10-06: "Aynı ofiste olsunlar"): a
 * full-row "Test ekibi" divider, then the five seats drawn like the software seats - the job
 * above, the figure in the test team's amber and teal, the white name card, a breaking point
 * under it. Rendered by OfficeScene inside `.office-floor`, never as a room of its own.
 */
export function TestSeatCells({ seats, now, animated }: { seats: TestSeat[]; now: Date; animated: boolean }) {
  return (
    <>
      <div className="office-floor-divider" data-office="test-team">
        <span>Test ekibi</span>
      </div>
      {seats.map((seat) => {
        const mood = testMoodOf(seat, now);
        const working = seat.state === "working";
        const label = seat.job ?? "iş bekliyor";
        return (
          <div
            key={seat.seat}
            className="office-seat office-test-seat"
            role="group"
            style={PALETTE}
            data-test-seat={seat.seat}
            data-state={seat.state}
            data-mood={mood}
            aria-label={testSeatAriaLabel(seat, now)}
          >
            <span className="office-label" title={label} aria-hidden="true">
              {label}
            </span>
            <Figure
              seat={figureSeat(seat.seat)}
              plain={false}
              pose={working ? "typing" : "seated"}
              warning={seat.state === "failed"}
              animated={animated && working}
              mood={mood}
            />
            <span className="office-name" aria-hidden="true">
              {seatTitle(seat.seat)}
            </span>
            {seat.breaking && (
              <span className="office-test-breaking" title={seat.breaking} aria-hidden="true">
                Son kopma noktası: {seat.breaking}
              </span>
            )}
          </div>
        );
      })}
    </>
  );
}

/** The room now; an unreachable or refusing board is five waiting seats. */
export async function fetchTestRoom(now: Date = new Date()): Promise<TestSeat[]> {
  try {
    const response = await apiFetch(`${BOARD_PATH}?limit=${BOARD_READ_LIMIT}`);
    if (!response.ok) return testRoomFromBoard([], now);
    const body = (await response.json()) as { notes?: BoardNote[] };
    return testRoomFromBoard(Array.isArray(body.notes) ? body.notes : [], now);
  } catch {
    return testRoomFromBoard([], now);
  }
}
