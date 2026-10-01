/**
 * The office's art, drawn in code: pixel maps (one character per pixel) turned into merged
 * `<rect>` runs. No sprite sheet, tileset or font - there is no asset licence to carry
 * (team/plans/office-page-integration.md). Colours are CSS variables so `office.css` owns
 * the palette; a map character picks the variable.
 */

export type Rect = { x: number; y: number; w: number; h: number; fill: string };

/** map character -> CSS variable (see office.css) */
const PALETTE: Record<string, string> = {
  h: "var(--office-hair)",
  s: "var(--office-skin)",
  b: "var(--office-shirt)",
  p: "var(--office-trousers)",
  d: "var(--office-desk)",
  e: "var(--office-desk-edge)",
  m: "var(--office-monitor)",
  g: "var(--office-screen)",
  c: "var(--office-chair)",
  k: "var(--office-key)",
  w: "var(--office-warn)",
  t: "var(--office-warn-text)",
};

/** Horizontal runs of equal characters, one rect each. */
export function toRects(map: readonly string[]): Rect[] {
  const out: Rect[] = [];
  map.forEach((row, y) => {
    let x = 0;
    while (x < row.length) {
      const ch = row[x];
      let end = x;
      while (end + 1 < row.length && row[end + 1] === ch) end += 1;
      const fill = PALETTE[ch];
      if (fill) out.push({ x, y, w: end - x + 1, h: 1, fill });
      x = end + 1;
    }
  });
  return out;
}

/** 24 x 14: desk top, a monitor on it, the keyboard strip. */
export const DESK: readonly string[] = [
  "......mmmmmmmmmm........",
  "......mggggggggm........",
  "......mggggggggm........",
  "......mggggggggm........",
  "......mmmmmmmmmm........",
  ".........mmmm...........",
  "dddddddddddddddddddddddd",
  "dkkkkkkkkkkkk...........",
  "eeeeeeeeeeeeeeeeeeeeeeee",
  "e......................e",
  "e......................e",
  "e......................e",
  "e......................e",
  "e......................e",
];

/** 12 x 16 seated body (back to the viewer's left of the desk): head, torso, chair. */
export const SEATED: readonly string[] = [
  "....hhhh....",
  "...hhhhhh...",
  "...ssssss...",
  "...ssssss...",
  "....ssss....",
  "..bbbbbbbb..",
  ".bbbbbbbbbb.",
  ".bbbbbbbbbb.",
  ".bbbbbbbbbb.",
  "..bbbbbbbb..",
  "..pppppppp..",
  "..pppppppp..",
  "cccccccccccc",
  "cc........cc",
  "cc........cc",
  "cc........cc",
];

/** The two arm frames of the typing animation (12 x 3, overlaid on the seated body). */
export const ARMS_A: readonly string[] = ["ss........ss", ".ss......ss.", "..ss....ss.."];
export const ARMS_B: readonly string[] = ["..ss....ss..", ".ss......ss.", "ss........ss"];

/** 12 x 16 standing body beside the desk. */
export const STANDING: readonly string[] = [
  "....hhhh....",
  "...hhhhhh...",
  "...ssssss...",
  "...ssssss...",
  "....ssss....",
  "..bbbbbbbb..",
  ".sbbbbbbbbs.",
  ".sbbbbbbbbs.",
  ".sbbbbbbbbs.",
  "..bbbbbbbb..",
  "..pppppppp..",
  "..pppp.ppp..",
  "..ppp...pp..",
  "..ppp...pp..",
  "..ppp...pp..",
  ".kkkk...kkk.",
];

/** 7 x 9 warning mark: an amber triangle with an exclamation point. */
export const WARNING: readonly string[] = [
  "...w...",
  "..www..",
  "..wtw..",
  ".wwtww.",
  ".wwtww.",
  "wwwwwww",
  "wwwtwww",
  "wwwwwww",
  ".......",
];
