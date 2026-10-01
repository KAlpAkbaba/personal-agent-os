# ADR (text; the lead numbers it) - Ofis page: own pixel drawing, pure model/poller, no DOM test library

Decision: the /core/office page draws desks and characters as inline SVG pixel maps (officeSprites.ts)
with CSS keyframes; no third-party art (see office-page-integration.md). The page is split so it is
testable under vitest's node environment without jsdom: officeModel.ts (pure contract -> drawn seats,
top bar, panel with the 40-line cap and 12-char sha), createOfficePoller in officeApi.ts (5 s interval,
timers/visibility as ports), OfficeView/OfficeScene/OfficePanel rendered to markup, page.tsx only wiring.
Reduced motion: the scene takes a prop (fed by matchMedia); the typing class and the second arm frame are
not rendered, a static "çalışıyor" badge is. The owner seat is always "waiting" and shows the approval count.
Why: no new dependency, licence-free, and every claim in the acceptance is checked on plain objects.
Rollback: delete apps/web/app/core/office, apps/web/tests/office and the Kokpit nav link.
