/**
 * The office: a room with one desk and one character per seat the API sends, each seat a
 * button, and the room's furniture along its walls. Pure rendering of `DrawnSeat`s -
 * selection and motion preference come in as props. The characters are in officeRobots.tsx.
 */

import { QUEUED_TR, type DrawnSeat } from "./officeModel";
import { Figure, RoomDecor } from "./officeRobots";
import { TestSeatCells, type TestSeat } from "./officeTestRoom";

export default function OfficeScene({
  seats,
  selected,
  reducedMotion,
  onSelect,
  arriving = [],
  testSeats,
  now,
}: {
  seats: DrawnSeat[];
  selected: string | null;
  reducedMotion: boolean;
  onSelect: (seat: string) => void;
  /** Seats that just took a new task: their characters walk in (officeMood.arrivals). */
  arriving?: string[];
  /** The test team's five seats, seated on this same floor after the software seats. */
  testSeats?: TestSeat[];
  /** The clock the test seats' moods are read against (defaults to now). */
  now?: Date;
}) {
  return (
    <div className="office-scroll" data-office="scene">
      <div className="office-floor">
        {seats.map((seat) => (
          <button
            key={seat.seat}
            type="button"
            className="office-seat"
            data-seat={seat.seat}
            aria-pressed={selected === seat.seat}
            aria-label={seat.ariaLabel}
            data-state={seat.state}
            data-mood={seat.mood}
            data-warning={seat.warning}
            {...(seat.badge !== null ? { "data-count": seat.badge } : {})}
            onClick={() => onSelect(seat.seat)}
          >
            {/* cut with an ellipsis at the cell's width; the title attribute keeps it whole */}
            <span
              className="office-label"
              title={seat.label ?? undefined}
              aria-hidden="true"
              {...(seat.queued ? { style: { color: "var(--muted)" } } : {})}
            >
              {seat.label ?? " "}
            </span>
            <Figure
              seat={seat.seat}
              plain={seat.plain}
              pose={seat.pose}
              warning={seat.warning}
              animated={seat.pose === "typing" && !reducedMotion}
              mood={seat.mood}
              arriving={!reducedMotion && arriving.includes(seat.seat)}
            />
            <span className="office-name" aria-hidden="true">
              {seat.name}
              {seat.badge !== null && <span className="office-count"> · {seat.badge} onay</span>}
            </span>
            {seat.lowered && (
              <span className="office-lowered" aria-hidden="true">
                {seat.lowered}
              </span>
            )}
            {seat.state === "working" && (reducedMotion || seat.runCount !== null) && (
              <span className="office-badge-static" aria-hidden="true">
                çalışıyor
                {seat.runCount !== null && (
                  <span className="office-run-count"> {seat.runCount}</span>
                )}
              </span>
            )}
            {seat.queued && (
              <span className="office-badge-static" aria-hidden="true">
                {QUEUED_TR}
              </span>
            )}
          </button>
        ))}
        {testSeats && testSeats.length > 0 && (
          <TestSeatCells seats={testSeats} now={now ?? new Date()} animated={!reducedMotion} />
        )}
        <RoomDecor />
      </div>
    </div>
  );
}
