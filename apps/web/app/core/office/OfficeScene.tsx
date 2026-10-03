/**
 * The office: a room with one desk and one character per seat the API sends, each seat a
 * button, and the room's furniture along its walls. Pure rendering of `DrawnSeat`s -
 * selection and motion preference come in as props. The characters are in officeRobots.tsx.
 */

import type { DrawnSeat } from "./officeModel";
import { Figure, RoomDecor } from "./officeRobots";

export default function OfficeScene({
  seats,
  selected,
  reducedMotion,
  onSelect,
}: {
  seats: DrawnSeat[];
  selected: string | null;
  reducedMotion: boolean;
  onSelect: (seat: string) => void;
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
            data-warning={seat.warning}
            {...(seat.badge !== null ? { "data-count": seat.badge } : {})}
            onClick={() => onSelect(seat.seat)}
          >
            {/* cut with an ellipsis at the cell's width; the title attribute keeps it whole */}
            <span className="office-label" title={seat.label ?? undefined} aria-hidden="true">
              {seat.label ?? " "}
            </span>
            <Figure
              seat={seat.seat}
              plain={seat.plain}
              pose={seat.pose}
              warning={seat.warning}
              animated={seat.pose === "typing" && !reducedMotion}
            />
            <span className="office-name" aria-hidden="true">
              {seat.name}
              {seat.badge !== null && <span className="office-count"> · {seat.badge} onay</span>}
            </span>
            {seat.state === "working" && (reducedMotion || seat.runCount !== null) && (
              <span className="office-badge-static" aria-hidden="true">
                çalışıyor
                {seat.runCount !== null && (
                  <span className="office-run-count"> {seat.runCount}</span>
                )}
              </span>
            )}
          </button>
        ))}
        <RoomDecor />
      </div>
    </div>
  );
}
