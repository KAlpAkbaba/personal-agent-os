/**
 * The office's characters and furniture, drawn in code as flat vector shapes: friendly
 * screen-faced robots in amber and navy (the owner's reference, 2026-10-03) and the owner as
 * a person in an amber jacket. No image file, font or sprite sheet - nothing with a licence
 * to carry. Colours are CSS variables so `office.css` owns the palette (light and dark).
 *
 * One figure is 120 x 100 units: a desk in front, the character behind it (or standing
 * beside it when the seat's task came back), a speech bubble above when there is
 * something to say.
 */

import type { Pose } from "./officeModel";

/** Which character sits at a seat: the role decides the body and the prop it holds. */
export type Character =
  | "lead"
  | "researcher"
  | "integrator"
  | "worker"
  | "inspector"
  | "owner"
  | "none";

const WORKER = /^worker-\d+$/;

export function characterOf(seat: string, plain: boolean): Character {
  if (plain) return "none";
  if (WORKER.test(seat)) return "worker";
  if (seat === "lead" || seat === "researcher" || seat === "integrator") return seat;
  if (seat === "inspector" || seat === "owner") return seat;
  return "none";
}

/** Navy for the roles that judge and steer, amber for the ones that build and look. */
function bodyFill(character: Character): string {
  return character === "lead" || character === "integrator" || character === "inspector"
    ? "var(--office-navy)"
    : "var(--office-shirt)";
}

function bodyShade(character: Character): string {
  return character === "lead" || character === "integrator" || character === "inspector"
    ? "var(--office-navy-shade)"
    : "var(--office-shirt-shade)";
}

export function Desk({ working }: { working: boolean }) {
  return (
    <g className="office-desk">
      {/* monitor on the desk */}
      <rect x="66" y="40" width="34" height="24" rx="3" fill="var(--office-monitor)" />
      <rect
        x="69"
        y="43"
        width="28"
        height="17"
        rx="2"
        fill="var(--office-screen)"
        className={working ? "office-screen-on" : undefined}
        opacity={working ? 1 : 0.45}
      />
      <rect x="80" y="64" width="6" height="6" fill="var(--office-monitor)" />
      {/* the desk: top and front */}
      <rect x="8" y="70" width="104" height="9" rx="2" fill="var(--office-desk)" />
      <rect x="8" y="77" width="104" height="5" rx="1" fill="var(--office-desk-edge)" />
      <rect x="14" y="82" width="6" height="16" rx="1" fill="var(--office-desk-edge)" />
      <rect x="100" y="82" width="6" height="16" rx="1" fill="var(--office-desk-edge)" />
      {/* keyboard */}
      <rect x="62" y="67" width="30" height="4" rx="1.5" fill="var(--office-key)" />
    </g>
  );
}

function Chair() {
  return (
    <g className="office-chair">
      <rect x="22" y="34" width="38" height="40" rx="9" fill="var(--office-chair)" />
    </g>
  );
}

/** The screen face: two eyes and a smile; asleep (waiting) the eyes are short lines. */
function Face({ x, y, awake }: { x: number; y: number; awake: boolean }) {
  return (
    <g transform={`translate(${x} ${y})`}>
      <rect x="0" y="0" width="30" height="20" rx="8" fill="var(--office-face)" />
      {awake ? (
        <>
          <ellipse cx="9.5" cy="8.5" rx="3" ry="3.6" fill="var(--office-eye)" className="office-eye" />
          <ellipse cx="20.5" cy="8.5" rx="3" ry="3.6" fill="var(--office-eye)" className="office-eye" />
        </>
      ) : (
        <>
          <rect x="6.5" y="8" width="6" height="2" rx="1" fill="var(--office-eye)" />
          <rect x="17.5" y="8" width="6" height="2" rx="1" fill="var(--office-eye)" />
        </>
      )}
      <path d="M9.5 13.5 Q15 18 20.5 13.5" stroke="var(--office-eye)" strokeWidth="2" fill="none" strokeLinecap="round" />
    </g>
  );
}

/** A robot's head at (x, y): the rounded shell, ear discs, the antenna, the screen face. */
function RobotHead({ x, y, character, awake }: { x: number; y: number; character: Character; awake: boolean }) {
  const fill = bodyFill(character);
  const shade = bodyShade(character);
  return (
    <g transform={`translate(${x} ${y})`}>
      <line x1="20" y1="-7" x2="20" y2="1" stroke={shade} strokeWidth="2.5" />
      <circle cx="20" cy="-8" r="3" fill={character === "lead" ? "var(--office-gold)" : shade} />
      <circle cx="1" cy="14" r="4.5" fill={shade} />
      <circle cx="39" cy="14" r="4.5" fill={shade} />
      <rect x="2" y="0" width="36" height="28" rx="12" fill={fill} />
      <Face x={5} y={4} awake={awake} />
    </g>
  );
}

/** A person's head (the owner): skin, hair, a smile. */
function PersonHead({ x, y }: { x: number; y: number }) {
  return (
    <g transform={`translate(${x} ${y})`}>
      <circle cx="20" cy="15" r="13" fill="var(--office-skin)" />
      <path d="M7 13 Q8 0 20 1 Q33 0 33 13 Q28 6 20 7 Q12 6 7 13 Z" fill="var(--office-hair)" />
      <circle cx="15.5" cy="16" r="1.6" fill="var(--office-hair)" />
      <circle cx="24.5" cy="16" r="1.6" fill="var(--office-hair)" />
      <path d="M15 21 Q20 25 25 21" stroke="var(--office-hair)" strokeWidth="1.6" fill="none" strokeLinecap="round" />
    </g>
  );
}

/** What a role holds: the inspector's and the researcher's magnifier, the integrator's wrench. */
function Prop({ character }: { character: Character }) {
  if (character === "inspector" || character === "researcher") {
    return (
      <g className="office-prop">
        <circle cx="16" cy="50" r="6" fill="none" stroke="var(--office-prop)" strokeWidth="2.5" />
        <line x1="11.5" y1="54.5" x2="6" y2="61" stroke="var(--office-prop)" strokeWidth="3" strokeLinecap="round" />
      </g>
    );
  }
  if (character === "integrator") {
    return (
      <g className="office-prop">
        <path d="M8 62 L18 50" stroke="var(--office-prop)" strokeWidth="3.5" strokeLinecap="round" />
        <circle cx="19.5" cy="48.5" r="4" fill="none" stroke="var(--office-prop)" strokeWidth="2.5" />
      </g>
    );
  }
  return null;
}

/** Seated behind the desk; when typing the two arm frames alternate (CSS). */
function Seated({ character, pose }: { character: Character; pose: Pose }) {
  const typing = pose === "typing";
  const awake = pose !== "seated";
  const person = character === "owner";
  const fill = person ? "var(--office-shirt)" : bodyFill(character);
  const shade = person ? "var(--office-shirt-shade)" : bodyShade(character);
  return (
    <g>
      <Chair />
      {/* torso */}
      <rect x="26" y="46" width="30" height="26" rx="9" fill={fill} />
      {person ? (
        <path d="M37 46 L41 58 L45 46 Z" fill="var(--office-shirt-light)" />
      ) : (
        <circle cx="41" cy="56" r="3.2" fill={shade} />
      )}
      {person ? <PersonHead x={21} y={17} /> : <RobotHead x={21} y={18} character={character} awake={awake} />}
      {typing ? (
        <g className="office-arms">
          <g className="office-arms-a">
            <rect x="50" y="56" width="16" height="7" rx="3.5" fill={shade} />
            <rect x="52" y="62" width="14" height="7" rx="3.5" fill={shade} />
          </g>
          <g className="office-arms-b">
            <rect x="50" y="59" width="16" height="7" rx="3.5" fill={shade} />
            <rect x="52" y="59" width="14" height="7" rx="3.5" fill={shade} />
          </g>
        </g>
      ) : (
        <rect x="52" y="60" width="13" height="7" rx="3.5" fill={shade} />
      )}
      <Prop character={character} />
    </g>
  );
}

/** Standing beside the desk: the seat's task came back and waits for its next run. */
function Standing({ character }: { character: Character }) {
  const person = character === "owner";
  const fill = person ? "var(--office-shirt)" : bodyFill(character);
  const shade = person ? "var(--office-shirt-shade)" : bodyShade(character);
  return (
    <g>
      <rect x="4" y="44" width="28" height="26" rx="9" fill={fill} />
      <circle cx="18" cy="54" r="3" fill={shade} />
      <rect x="0" y="48" width="6" height="16" rx="3" fill={shade} />
      <rect x="30" y="48" width="6" height="16" rx="3" fill={shade} />
      <rect x="8" y="70" width="7" height="22" rx="3" fill="var(--office-trousers)" />
      <rect x="21" y="70" width="7" height="22" rx="3" fill="var(--office-trousers)" />
      {person ? <PersonHead x={-2} y={15} /> : <RobotHead x={-2} y={16} character={character} awake />}
    </g>
  );
}

/** A speech bubble with one sign: "!" when the seat's task came back, "…" when it waits for a run. */
export function Bubble({ sign }: { sign: "!" | "?" }) {
  return (
    <g className="office-warning">
      <rect x="44" y="0" width="24" height="18" rx="8" fill="var(--office-bubble)" />
      <path d="M50 17 L48 24 L57 17 Z" fill="var(--office-bubble)" />
      <text
        x="56"
        y="14"
        textAnchor="middle"
        fontSize="14"
        fontWeight="700"
        fill="var(--office-bubble-text)"
      >
        {sign}
      </text>
    </g>
  );
}

export function Figure({
  seat,
  plain,
  pose,
  warning,
  animated,
}: {
  seat: string;
  plain: boolean;
  pose: Pose;
  warning: boolean;
  animated: boolean;
}) {
  const character = characterOf(seat, plain);
  const classes = ["office-figure", `office-character-${character}`];
  if (animated) classes.push("office-typing");
  return (
    <svg className={classes.join(" ")} viewBox="0 0 120 100" aria-hidden="true" focusable="false">
      {character === "none" ? null : pose === "standing" ? <Standing character={character} /> : <Seated character={character} pose={pose} />}
      <Desk working={pose === "typing"} />
      {warning && <Bubble sign="!" />}
    </svg>
  );
}

/** The room's furniture along its walls and floor: decoration only, never a seat. */
export function RoomDecor() {
  return (
    <div className="office-decor" aria-hidden="true">
      <svg className="office-decor-shelf" viewBox="0 0 60 40">
        <rect x="0" y="0" width="60" height="40" rx="3" fill="var(--office-desk-edge)" />
        <rect x="3" y="3" width="54" height="15" fill="var(--office-desk)" />
        <rect x="3" y="22" width="54" height="15" fill="var(--office-desk)" />
        {[6, 12, 18, 30, 36, 44].map((x, i) => (
          <rect key={`a${x}`} x={x} y="5" width="5" height="13" fill={i % 2 ? "var(--office-navy)" : "var(--office-shirt)"} />
        ))}
        {[8, 16, 24, 38, 48].map((x, i) => (
          <rect key={`b${x}`} x={x} y="24" width="6" height="13" fill={i % 2 ? "var(--office-shirt)" : "var(--office-screen)"} />
        ))}
      </svg>
      <svg className="office-decor-board" viewBox="0 0 56 40">
        <rect x="0" y="0" width="56" height="40" rx="3" fill="var(--office-monitor)" />
        <rect x="4" y="4" width="48" height="32" rx="2" fill="var(--office-board)" />
        <path d="M10 28 L20 18 L28 24 L42 10" stroke="var(--office-shirt)" strokeWidth="2.5" fill="none" />
      </svg>
      <svg className="office-decor-plant office-decor-plant-a" viewBox="0 0 32 40">
        <path d="M16 22 Q4 18 3 6 Q12 10 16 22 Q20 4 29 4 Q28 18 16 22 Z" fill="var(--office-leaf)" />
        <rect x="7" y="22" width="18" height="16" rx="3" fill="var(--office-pot)" />
      </svg>
      <svg className="office-decor-plant office-decor-plant-b" viewBox="0 0 32 40">
        <path d="M16 22 Q4 18 3 6 Q12 10 16 22 Q20 4 29 4 Q28 18 16 22 Z" fill="var(--office-leaf)" />
        <rect x="7" y="22" width="18" height="16" rx="3" fill="var(--office-pot)" />
      </svg>
      <svg className="office-decor-cooler" viewBox="0 0 24 44">
        <rect x="4" y="0" width="16" height="18" rx="6" fill="var(--office-screen)" opacity="0.8" />
        <rect x="2" y="18" width="20" height="26" rx="3" fill="var(--office-key)" />
      </svg>
      <svg className="office-decor-sofa" viewBox="0 0 70 34">
        <rect x="0" y="6" width="70" height="20" rx="6" fill="var(--office-navy)" />
        <rect x="6" y="0" width="58" height="14" rx="5" fill="var(--office-navy-shade)" />
        <rect x="4" y="26" width="6" height="8" fill="var(--office-navy-shade)" />
        <rect x="60" y="26" width="6" height="8" fill="var(--office-navy-shade)" />
      </svg>
    </div>
  );
}
