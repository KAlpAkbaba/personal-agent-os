**Choice for `office-page`: our own drawing, no third party.** The plan is at `team/plans/office-page-integration.md`. I wrote only that file.

- **What we draw:** desks, characters and the warning mark are drawn in code as inline SVG pixel maps with CSS keyframes. This covers seated-still, seated-typing (2 frames) and standing. Colours come from the `core.css` tokens.
- **Licence:** none. No sprite sheet, tileset, font or library is used or fetched, and nothing phones home. There is no asset or licence file to ship, so `apps/web/public/office/` stays empty and `package.json` is untouched.
- **Why not a pack:** the card allows only CC0 or MIT. The office packs I know of are mostly CC-BY or "personal use". The few CC0 ones don't offer a seated + typing + standing character, and checking a pack's licence per file is more risk than the page is worth. I did not run a web search, because no candidate was ever compared. The plan notes that a CC0 pack can be adopted later with its licence text beside the files.
- **Footprint:** zero asset bytes, estimated source under 25 KB, one small JSON fetch every 5 s, no timers while the tab is hidden.
- **Risks:** none for the device. One constraint for the worker is below.

**Test constraint for the worker:** `apps/web/vitest.config.ts` runs in `node` with no jsdom, and we may add no dependency. So:
- The poller must be a pure `createOfficePoller({fetch, setInterval, clearInterval, isHidden, ...})`, tested with fake timers.
- Seat selection must be a pure function in `officeModel.ts`.
- Components are rendered to markup with `react-dom/server`; reduced motion is a prop.

The plan maps each acceptance test and both mutations (remove the 40-line cap; map `returned` to `waiting`) to this setup. It also lists the files in the worker's area: `officeApi.ts`, `officeModel.ts`, `officeSprites.ts`, `OfficeScene.tsx`, `OfficePanel.tsx`, `page.tsx`, `office.css`, and `apps/web/tests/office/`.

**For the lead at merge:**
1. Add this to `docs/THIRD_PARTY_COMPONENTS.md`:
   ```
   ## Office page sprites (2026-10-01, office-page) - own drawing, no third party
   The Kokpit "Ofis" page (/core/office) draws its desks and characters in code as
   inline SVG pixel maps with CSS animation. No sprite sheet, tileset, font or library
   is used, vendored or fetched; `apps/web/public/office/` holds no third-party file.
   Nothing to license, nothing that phones home. If a pixel-art pack is ever adopted
   instead, only CC0/MIT, with its licence text beside the files.
   ```
2. Add the Kokpit navigation link to `/core/office`. I did not find the nav file; check where `/core/approvals` is linked (possibly `CoreBar.tsx` or the cockpit nav).

**Rollback:** delete `apps/web/app/core/office/`, `apps/web/tests/office/` and the nav link. Nothing else changes.
