# office-page — integration plan (integrator, cycle office-01)

## Choice: OWN DRAWING. No third party, no asset, no new dependency.

Decision: **adapt-nothing / build from scratch** — characters, desks, monitors and the
warning mark are drawn in code as inline SVG built from a small pixel grid
(`<rect>` per pixel run, `shape-rendering="crispEdges"`), animated with CSS keyframes.

Why not an asset pack:
- The card allows only CC0/MIT. Pixel-office packs on itch.io/OpenGameArt are mostly CC-BY,
  "free for personal use" or unclear; the few CC0 office tilesets lack a seated + typing + standing
  character with a stable licence text. Verifying each pack's licence per file is more risk than
  the page is worth, and "when in doubt choose our own drawing" is the card's rule.
- Eight characters need only: seated-still, seated-typing (2 frames), standing, warning mark. A
  hand-written 16x16 pixel map per pose is a few hundred bytes of data; a pack would be tens of KB
  plus a licence file to ship and keep honest.
- No network, no phone-home, no binary, no device access: nothing here can harm the device.
- Palette comes from `core.css` tokens (and a per-seat accent colour), so the office matches the
  Kokpit and respects its themes; a bitmap sprite could not.

Web search was deliberately not run: with the own-drawing decision there is no candidate whose
licence/maintenance/footprint is being compared. (Recorded so the decision can be revisited:
if the owner later wants richer art, adopt a CC0 pack then, with the licence file in
`apps/web/public/office/LICENSE-<pack>.txt` and a THIRD_PARTY entry.)

## THIRD_PARTY_COMPONENTS.md entry text (for the lead)

```
## Office page sprites (2026-10-01, office-page) - own drawing, no third party

The Kokpit "Ofis" page (/core/office) draws its desks and characters in code as inline SVG pixel
maps with CSS animation. No sprite sheet, tileset, font or library is used, vendored or fetched;
`apps/web/public/office/` holds no third-party file. Nothing to license, nothing that phones home.
If a pixel-art pack is ever adopted instead, only CC0/MIT, with its licence text beside the files.
```

## Seam and files (worker's area)

- `apps/web/app/core/office/officeApi.ts` — contract types + `fetchOffice()` via
  `apiFetch` from `../../lib/session` (house pattern: `approvals/approvalsApi.ts`;
  `OFFICE_PATH = "/v1/team/office"`; `PendingApproval` re-used from `../approvals/approvalsApi`).
- `officeModel.ts` — pure: contract -> seats drawn (pose `typing|seated|standing`, warning mark,
  label, aria-label with role + state, TR role names), top bar strings (`2/6`, `tahmini $X`,
  limit Açık / Bekliyor <saat> / Durdu, local start time), panel data (report lines sliced to 40,
  sha first 12), owner approval count.
- `officeSprites.ts` (pixel maps) + `OfficeScene.tsx` + `OfficePanel.tsx` + `page.tsx` +
  `office.css` (imports tokens; own class prefix `office-`; `@media (prefers-reduced-motion:
  reduce)` drops the animation class and shows a static "çalışıyor" badge; container
  `overflow-x:auto` for phone width, panel below via flex-wrap/grid).
- `useOfficePoll` hook: 5 s interval, cleared while `document.hidden` (visibilitychange),
  last good data kept on failure with a `bağlantı yok` note.
- Tests in `apps/web/tests/office/`.

## Test-harness constraint (important for the worker)

`apps/web/vitest.config.ts` is `environment: "node"`, **no jsdom** (no new dependency allowed).
Existing pattern: components rendered to static markup with `react-dom/server`, interaction logic
in pure controllers with fake ports. So:
- put the poll logic in a pure `createOfficePoller({fetch, setInterval, clearInterval,
  isHidden, onData, onError})` and test it with vitest fake timers + a fake visibility port;
- put click-selection in a pure reducer / `selectSeat` in the model, test the panel by rendering
  `OfficePanel` to markup with a given seat; the page's `useEffect` wiring stays thin.
- reduced motion: component takes a `reducedMotion` prop fed by a `matchMedia` hook; test renders
  with `true` and asserts no `office-typing` class and the "çalışıyor" badge present.

## Tests to add (acceptance mapping)
model: two working workers -> worker-1/2 typing + titles, `2/6`; returned -> standing + warning;
waiting -> seated still; owner seat waiting, task_id null, count; `tahmini` always with USD;
limit strings. panel: role/card/<=40 lines/branch/12-char sha (full in title). approvals list +
link `/core/approvals`. poller: 5 s, pauses hidden, failure keeps last + 'bağlantı yok'.
Mutations (sha256 + backup copy): remove 40-line cap; map returned -> waiting.

## Rollback
Delete `apps/web/app/core/office/`, `apps/web/tests/office/`, and the Kokpit nav link. Nothing
else is touched; no dependency or lockfile change.

## Footprint
Zero assets (`public/office/` can stay empty/absent). Estimated source < 25 KB, 8 SVGs of ~100
`<rect>` each rendered client-side; one fetch per 5 s of a small JSON; no timers while hidden.

## For the lead at merge
- Kokpit navigation link to `/core/office`: the Kokpit's nav file (see where `/core/approvals`
  is linked, e.g. `apps/web/app/core/CoreBar.tsx` / cockpit nav) — lead's.
- `docs/THIRD_PARTY_COMPONENTS.md`: the entry above.
