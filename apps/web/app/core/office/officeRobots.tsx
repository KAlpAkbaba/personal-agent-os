/**
 * The office's characters and furniture, drawn in code as flat vector shapes: friendly
 * screen-faced robots in amber and navy (the owner's reference, 2026-10-03) and the owner - the
 * CTO - as a person in an amber jacket. No image file, font or sprite sheet - nothing with a
 * licence to carry. Colours are CSS variables so `office.css` owns the palette.
 *
 * One figure is 120 x 100 units: a desk in front, the character behind it (or standing beside
 * it when the seat's task came back), a speech bubble above when there is something to say.
 * The face shows the character's mood (officeMood.ts): focused, tired (half-shut eyes, a drop
 * of sweat, slower hands), angry (slanted brows, a red screen, steam), sad (a frown, a tear),
 * relaxed (smiling shut eyes, a mug of tea), happy. A seat that just took a new task walks in.
 */

import type { Pose } from "./officeModel";
import type { Mood } from "./officeMood";

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

export function Desk({ working, mood }: { working: boolean; mood: Mood }) {
  return (
    <g className="office-desk">
      {/* a second, smaller screen with a chart, then the main monitor */}
      <rect x="98" y="48" width="18" height="15" rx="2" fill="var(--office-monitor)" />
      <rect x="100" y="50" width="14" height="10" rx="1" fill="var(--office-screen-2)" opacity={working ? 1 : 0.4} />
      <path d="M101 58 L105 55 L108 57 L113 52" stroke="var(--office-neon)" strokeWidth="1.2" fill="none" opacity={working ? 1 : 0.4} />
      <rect x="66" y="40" width="34" height="24" rx="3" fill="var(--office-monitor)" />
      <rect
        x="69"
        y="43"
        width="28"
        height="17"
        rx="2"
        fill={mood === "angry" ? "var(--office-screen-error)" : "var(--office-screen)"}
        className={working ? "office-screen-on" : undefined}
        opacity={working || mood === "angry" ? 1 : 0.45}
      />
      {working && (
        <g className="office-code" stroke="var(--office-code)" strokeWidth="1.4" strokeLinecap="round">
          <line x1="72" y1="47" x2="86" y2="47" />
          <line x1="75" y1="51" x2="92" y2="51" />
          <line x1="75" y1="55" x2="84" y2="55" />
        </g>
      )}
      <rect x="80" y="64" width="6" height="6" fill="var(--office-monitor)" />
      {/* the desk: top, the light strip along its front, legs */}
      <rect x="8" y="70" width="104" height="9" rx="2" fill="var(--office-desk)" />
      <rect x="8" y="77" width="104" height="5" rx="1" fill="var(--office-desk-edge)" />
      <rect x="10" y="80.5" width="100" height="1.6" rx="0.8" fill="var(--office-neon)" className="office-desk-glow" opacity={working ? 1 : 0.35} />
      <rect x="14" y="82" width="6" height="16" rx="1" fill="var(--office-desk-edge)" />
      <rect x="100" y="82" width="6" height="16" rx="1" fill="var(--office-desk-edge)" />
      {/* keyboard */}
      <rect x="62" y="67" width="30" height="4" rx="1.5" fill="var(--office-key)" />
      {/* a mug of tea while waiting for work */}
      {mood === "relaxed" && (
        <g className="office-mug">
          <rect x="40" y="62" width="9" height="9" rx="2" fill="var(--office-mug)" />
          <path d="M49 64 Q53 66.5 49 69" stroke="var(--office-mug)" strokeWidth="1.6" fill="none" />
          <path className="office-steam-tea" d="M43 59 Q41 56 43 53 M46 59 Q44 56 46 53" stroke="var(--office-steam)" strokeWidth="1.2" fill="none" strokeLinecap="round" />
        </g>
      )}
    </g>
  );
}

function Chair() {
  return (
    <g className="office-chair">
      <rect x="22" y="34" width="38" height="40" rx="9" fill="var(--office-chair)" />
      <rect x="26" y="38" width="30" height="3" rx="1.5" fill="var(--office-neon)" opacity="0.55" />
    </g>
  );
}

/** The screen face: eyes and mouth by mood. */
function Face({ x, y, mood }: { x: number; y: number; mood: Mood }) {
  const eye = "var(--office-eye)";
  const screen = mood === "angry" ? "var(--office-face-angry)" : "var(--office-face)";
  return (
    <g transform={`translate(${x} ${y})`} className={`office-face office-mood-${mood}`}>
      <rect x="0" y="0" width="30" height="20" rx="8" fill={screen} />
      {mood === "focused" && (
        <>
          <ellipse cx="9.5" cy="8.5" rx="3" ry="3.6" fill={eye} className="office-eye" />
          <ellipse cx="20.5" cy="8.5" rx="3" ry="3.6" fill={eye} className="office-eye" />
          <path d="M10.5 14 Q15 17 19.5 14" stroke={eye} strokeWidth="2" fill="none" strokeLinecap="round" />
        </>
      )}
      {mood === "tired" && (
        <>
          <path d="M6.5 9.5 Q9.5 7.5 12.5 9.5 Z" fill={eye} />
          <path d="M17.5 9.5 Q20.5 7.5 23.5 9.5 Z" fill={eye} />
          <line x1="6" y1="8.3" x2="13" y2="8.3" stroke={eye} strokeWidth="1.2" />
          <line x1="17" y1="8.3" x2="24" y2="8.3" stroke={eye} strokeWidth="1.2" />
          <path d="M11 15 L19 15" stroke={eye} strokeWidth="2" strokeLinecap="round" />
        </>
      )}
      {mood === "angry" && (
        <>
          <line x1="5.5" y1="4" x2="12.5" y2="7" stroke={eye} strokeWidth="2" strokeLinecap="round" />
          <line x1="24.5" y1="4" x2="17.5" y2="7" stroke={eye} strokeWidth="2" strokeLinecap="round" />
          <ellipse cx="9.5" cy="10" rx="2.5" ry="2.2" fill={eye} />
          <ellipse cx="20.5" cy="10" rx="2.5" ry="2.2" fill={eye} />
          <path d="M10 17 Q15 13 20 17" stroke={eye} strokeWidth="2" fill="none" strokeLinecap="round" />
        </>
      )}
      {mood === "sad" && (
        <>
          <line x1="6" y1="6" x2="12" y2="4.5" stroke={eye} strokeWidth="1.6" strokeLinecap="round" />
          <line x1="24" y1="6" x2="18" y2="4.5" stroke={eye} strokeWidth="1.6" strokeLinecap="round" />
          <ellipse cx="9.5" cy="9.5" rx="2.6" ry="3" fill={eye} />
          <ellipse cx="20.5" cy="9.5" rx="2.6" ry="3" fill={eye} />
          <path d="M10.5 16.5 Q15 13.5 19.5 16.5" stroke={eye} strokeWidth="2" fill="none" strokeLinecap="round" />
          <path className="office-tear" d="M22.5 12.5 Q21 15 22.5 16.5 Q24 15 22.5 12.5 Z" fill="var(--office-tear)" />
        </>
      )}
      {mood === "sleepy" && (
        <>
          <path d="M6.5 8.5 Q9.5 11 12.5 8.5" stroke={eye} strokeWidth="2" fill="none" strokeLinecap="round" />
          <path d="M17.5 8.5 Q20.5 11 23.5 8.5" stroke={eye} strokeWidth="2" fill="none" strokeLinecap="round" />
          <ellipse cx="15" cy="15" rx="2" ry="1.6" fill={eye} />
        </>
      )}
      {mood === "waiting" && (
        <>
          <circle cx="9.5" cy="9" r="2.2" fill={eye} />
          <circle cx="20.5" cy="9" r="2.2" fill={eye} />
          <path d="M11 15 L19 15" stroke={eye} strokeWidth="2" strokeLinecap="round" />
        </>
      )}
      {(mood === "relaxed" || mood === "happy") && (
        <>
          <path d="M6.5 10 Q9.5 6 12.5 10" stroke={eye} strokeWidth="2" fill="none" strokeLinecap="round" />
          <path d="M17.5 10 Q20.5 6 23.5 10" stroke={eye} strokeWidth="2" fill="none" strokeLinecap="round" />
          <path d="M9.5 13.5 Q15 18.5 20.5 13.5" stroke={eye} strokeWidth="2" fill="none" strokeLinecap="round" />
        </>
      )}
    </g>
  );
}

/** What a mood adds above the head: steam when angry, a drop of sweat when tired, Zz when dozing. */
function MoodMark({ x, y, mood }: { x: number; y: number; mood: Mood }) {
  if (mood === "sleepy") {
    return (
      <g className="office-zz" transform={`translate(${x} ${y})`} fill="var(--office-label)">
        <text x="30" y="2" fontSize="9" fontWeight="700">
          Z
        </text>
        <text x="37" y="-5" fontSize="7" fontWeight="700">
          z
        </text>
      </g>
    );
  }
  if (mood === "angry") {
    return (
      <g className="office-steam" transform={`translate(${x} ${y})`} fill="var(--office-steam-angry)">
        <circle cx="2" cy="0" r="3.2" />
        <circle cx="7" cy="-4" r="2.4" />
        <circle cx="36" cy="0" r="3.2" />
        <circle cx="31" cy="-4" r="2.4" />
      </g>
    );
  }
  if (mood === "tired") {
    return (
      <path
        className="office-sweat"
        transform={`translate(${x} ${y})`}
        d="M36 2 Q33 7 36 9 Q39 7 36 2 Z"
        fill="var(--office-tear)"
      />
    );
  }
  return null;
}

/** A robot's head at (x, y): the rounded shell, ear discs, the antenna, the screen face. */
function RobotHead({ x, y, character, mood }: { x: number; y: number; character: Character; mood: Mood }) {
  const fill = bodyFill(character);
  const shade = bodyShade(character);
  return (
    <g transform={`translate(${x} ${y})`}>
      <line x1="20" y1="-7" x2="20" y2="1" stroke={shade} strokeWidth="2.5" />
      <circle
        cx="20"
        cy="-8"
        r="3"
        className="office-antenna"
        fill={character === "lead" ? "var(--office-gold)" : mood === "angry" ? "var(--office-steam-angry)" : shade}
      />
      <circle cx="1" cy="14" r="4.5" fill={shade} />
      <circle cx="39" cy="14" r="4.5" fill={shade} />
      <rect x="2" y="0" width="36" height="28" rx="12" fill={fill} />
      <Face x={5} y={4} mood={mood} />
    </g>
  );
}

/** A person's head (the CTO): skin, hair, a smile. */
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
function Seated({ character, pose, mood }: { character: Character; pose: Pose; mood: Mood }) {
  const typing = pose === "typing";
  const person = character === "owner";
  const fill = person ? "var(--office-shirt)" : bodyFill(character);
  const shade = person ? "var(--office-shirt-shade)" : bodyShade(character);
  return (
    <g className="office-body">
      <Chair />
      {/* torso */}
      <rect x="26" y="46" width="30" height="26" rx="9" fill={fill} />
      {person ? (
        <path d="M37 46 L41 58 L45 46 Z" fill="var(--office-shirt-light)" />
      ) : (
        <circle cx="41" cy="56" r="3.2" fill={shade} className="office-core" />
      )}
      {person ? <PersonHead x={21} y={17} /> : <RobotHead x={21} y={18} character={character} mood={mood} />}
      {!person && <MoodMark x={21} y={8} mood={mood} />}
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
function Standing({ character, mood }: { character: Character; mood: Mood }) {
  const person = character === "owner";
  const fill = person ? "var(--office-shirt)" : bodyFill(character);
  const shade = person ? "var(--office-shirt-shade)" : bodyShade(character);
  const angry = mood === "angry";
  return (
    <g className="office-body">
      <rect x="4" y="44" width="28" height="26" rx="9" fill={fill} />
      <circle cx="18" cy="54" r="3" fill={shade} className="office-core" />
      {angry ? (
        // fists on the hips
        <>
          <path d="M4 50 L-3 58 L4 64" stroke={shade} strokeWidth="5" fill="none" strokeLinecap="round" strokeLinejoin="round" />
          <path d="M32 50 L39 58 L32 64" stroke={shade} strokeWidth="5" fill="none" strokeLinecap="round" strokeLinejoin="round" />
        </>
      ) : (
        <>
          <rect x="0" y="48" width="6" height="16" rx="3" fill={shade} />
          <rect x="30" y="48" width="6" height="16" rx="3" fill={shade} />
        </>
      )}
      <rect x="8" y="70" width="7" height="22" rx="3" fill="var(--office-trousers)" />
      <rect x="21" y="70" width="7" height="22" rx="3" fill="var(--office-trousers)" />
      {person ? <PersonHead x={-2} y={15} /> : <RobotHead x={-2} y={16} character={character} mood={mood} />}
      {!person && <MoodMark x={-2} y={6} mood={mood} />}
    </g>
  );
}

/** A speech bubble with one sign: "!" when the seat's task came back. */
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
  mood = "focused",
  arriving = false,
}: {
  seat: string;
  plain: boolean;
  pose: Pose;
  warning: boolean;
  animated: boolean;
  mood?: Mood;
  arriving?: boolean;
}) {
  const character = characterOf(seat, plain);
  const classes = ["office-figure", `office-character-${character}`, `office-feels-${mood}`];
  if (animated) classes.push("office-typing");
  if (animated && mood === "tired") classes.push("office-tired");
  if (arriving) classes.push("office-arriving");
  return (
    <svg className={classes.join(" ")} viewBox="0 0 120 100" aria-hidden="true" focusable="false">
      {character === "none" ? null : pose === "standing" ? (
        <Standing character={character} mood={mood} />
      ) : (
        <Seated character={character} pose={pose} mood={mood} />
      )}
      <Desk working={pose === "typing"} mood={mood} />
      {warning && <Bubble sign="!" />}
    </svg>
  );
}

/** The room: a night-city window, a wall screen with live bars, a server rack, a coffee
 * machine, plants, a sofa and a small cleaning robot. Decoration only, never a seat. */
export function RoomDecor() {
  return (
    <div className="office-decor" aria-hidden="true">
      <svg className="office-decor-window" viewBox="0 0 90 46">
        <rect x="0" y="0" width="90" height="46" rx="3" fill="var(--office-frame)" />
        <rect x="3" y="3" width="84" height="40" rx="2" fill="var(--office-sky)" />
        <circle cx="74" cy="12" r="4" fill="var(--office-moon)" />
        {[
          [6, 22, 10],
          [17, 14, 12],
          [30, 26, 9],
          [40, 10, 13],
          [54, 20, 10],
          [65, 16, 9],
          [75, 28, 10],
        ].map(([x, top, w]) => (
          <g key={`b${x}`}>
            <rect x={x} y={top} width={w} height={43 - top} fill="var(--office-city)" />
            {Array.from({ length: Math.floor((40 - top) / 5) }, (_, row) => (
              <rect
                key={row}
                x={x + 2}
                y={top + 3 + row * 5}
                width="2"
                height="2"
                fill="var(--office-city-light)"
                className={(x + row) % 3 === 0 ? "office-city-blink" : undefined}
              />
            ))}
          </g>
        ))}
        <line x1="45" y1="3" x2="45" y2="43" stroke="var(--office-frame)" strokeWidth="2" />
      </svg>
      <svg className="office-decor-screen" viewBox="0 0 90 46">
        <rect x="0" y="0" width="90" height="46" rx="3" fill="var(--office-monitor)" />
        <rect x="3" y="3" width="84" height="40" rx="2" fill="var(--office-board)" />
        <text x="7" y="11" fontSize="6" fontWeight="700" fill="var(--office-neon)">EKİP</text>
        {[0, 1, 2, 3, 4, 5].map((i) => (
          <rect key={i} x={10 + i * 12} y="18" width="7" height="22" rx="1" fill={i % 2 ? "var(--office-shirt)" : "var(--office-neon)"} className={`office-bar office-bar-${i}`} />
        ))}
        <path d="M8 36 L22 28 L36 32 L52 20 L66 24 L82 14" stroke="var(--office-gold)" strokeWidth="1.5" fill="none" className="office-line" />
      </svg>
      <svg className="office-decor-rack" viewBox="0 0 30 60">
        <rect x="0" y="0" width="30" height="60" rx="3" fill="var(--office-rack)" />
        {[0, 1, 2, 3, 4, 5].map((i) => (
          <g key={i}>
            <rect x="3" y={4 + i * 9} width="24" height="7" rx="1" fill="var(--office-rack-unit)" />
            <circle cx="7" cy={7.5 + i * 9} r="1.3" fill="var(--office-led-green)" className={`office-led office-led-${i % 3}`} />
            <circle cx="11" cy={7.5 + i * 9} r="1.3" fill={i % 2 ? "var(--office-led-blue)" : "var(--office-led-green)"} className={`office-led office-led-${(i + 1) % 3}`} />
            <rect x="15" y={6.5 + i * 9} width="9" height="2" rx="1" fill="var(--office-rack-slot)" />
          </g>
        ))}
      </svg>
      <svg className="office-decor-plant office-decor-plant-a" viewBox="0 0 32 40">
        <path d="M16 22 Q4 18 3 6 Q12 10 16 22 Q20 4 29 4 Q28 18 16 22 Z" fill="var(--office-leaf)" />
        <rect x="7" y="22" width="18" height="16" rx="3" fill="var(--office-pot)" />
      </svg>
      <svg className="office-decor-plant office-decor-plant-b" viewBox="0 0 32 40">
        <path d="M16 22 Q4 18 3 6 Q12 10 16 22 Q20 4 29 4 Q28 18 16 22 Z" fill="var(--office-leaf)" />
        <rect x="7" y="22" width="18" height="16" rx="3" fill="var(--office-pot)" />
      </svg>
      <svg className="office-decor-coffee" viewBox="0 0 28 44">
        <rect x="2" y="0" width="24" height="40" rx="4" fill="var(--office-rack)" />
        <rect x="6" y="5" width="16" height="7" rx="1.5" fill="var(--office-neon)" opacity="0.8" />
        <rect x="9" y="18" width="10" height="10" rx="1" fill="var(--office-rack-unit)" />
        <rect x="11" y="23" width="6" height="5" rx="1" fill="var(--office-mug)" />
        <rect x="0" y="40" width="28" height="4" rx="1" fill="var(--office-rack-unit)" />
      </svg>
      <svg className="office-decor-sofa" viewBox="0 0 70 34">
        <rect x="0" y="6" width="70" height="20" rx="6" fill="var(--office-navy)" />
        <rect x="6" y="0" width="58" height="14" rx="5" fill="var(--office-navy-shade)" />
        <rect x="4" y="26" width="6" height="8" fill="var(--office-navy-shade)" />
        <rect x="60" y="26" width="6" height="8" fill="var(--office-navy-shade)" />
      </svg>
      <svg className="office-decor-vacuum" viewBox="0 0 24 12">
        <ellipse cx="12" cy="8" rx="11" ry="4" fill="var(--office-rack)" />
        <ellipse cx="12" cy="6" rx="9" ry="3" fill="var(--office-key)" />
        <circle cx="12" cy="5.5" r="1.4" fill="var(--office-neon)" className="office-led office-led-0" />
      </svg>
    </div>
  );
}
