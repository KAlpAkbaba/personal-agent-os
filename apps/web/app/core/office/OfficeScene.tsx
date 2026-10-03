/**
 * The pixel office: one desk and one character per seat the API sends, each seat a button.
 * Pure rendering of `DrawnSeat`s - selection and motion preference come in as props.
 */

import { QUEUED_TR, type DrawnSeat } from "./officeModel";
import { ARMS_A, ARMS_B, DESK, SEATED, STANDING, WARNING, toRects } from "./officeSprites";

function Pixels({ map, x, y }: { map: readonly string[]; x: number; y: number }) {
  return (
    <g transform={`translate(${x} ${y})`}>
      {toRects(map).map((r) => (
        <rect key={`${r.x}-${r.y}`} x={r.x} y={r.y} width={r.w} height={r.h} fill={r.fill} />
      ))}
    </g>
  );
}

function Figure({ seat, reducedMotion }: { seat: DrawnSeat; reducedMotion: boolean }) {
  const typing = seat.pose === "typing";
  const animated = typing && !reducedMotion;
  return (
    <svg
      className={animated ? "office-figure office-typing" : "office-figure"}
      viewBox="0 0 40 28"
      shapeRendering="crispEdges"
      aria-hidden="true"
      focusable="false"
    >
      <Pixels map={DESK} x={16} y={12} />
      {seat.plain ? null : seat.pose === "standing" ? (
        <Pixels map={STANDING} x={1} y={10} />
      ) : (
        <>
          <Pixels map={SEATED} x={3} y={8} />
          {typing && (
            <g className="office-arms">
              <g className="office-arms-a">
                <Pixels map={ARMS_A} x={3} y={14} />
              </g>
              {animated && (
                <g className="office-arms-b">
                  <Pixels map={ARMS_B} x={3} y={14} />
                </g>
              )}
            </g>
          )}
        </>
      )}
      {seat.warning && (
        <g className="office-warning">
          <Pixels map={WARNING} x={4} y={0} />
        </g>
      )}
    </svg>
  );
}

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
            <span
              className="office-label"
              title={seat.label ?? undefined}
              aria-hidden="true"
              {...(seat.queued ? { style: { color: "var(--muted)" } } : {})}
            >
              {seat.label ?? " "}
            </span>
            <Figure seat={seat} reducedMotion={reducedMotion} />
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
            {seat.queued && (
              <span className="office-badge-static" aria-hidden="true">
                {QUEUED_TR}
              </span>
            )}
          </button>
        ))}
      </div>
    </div>
  );
}
