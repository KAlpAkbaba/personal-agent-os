"use client";

/**
 * `/routines` — B24 req 693.
 *
 * The requirement's note is "295 ile aynı sayfa": B14 gave the cockpit a routines panel
 * with pause and resume, and this is the page that panel points at. What the panel cannot
 * fit is the part the owner is most surprised by — a routine is a thing that runs whether
 * or not anyone is watching, and seven of them were created FOR them rather than by them.
 * So the page states the rules this Cloud Core runs routines under, from the subsystem's
 * own `/policy` route, beside the list.
 *
 * Pause and resume are the same two reversible controls B14 built, on the same client:
 * this page is not a second authority surface, it is the first surface wide enough to
 * read them on.
 */

import { useCallback } from "react";

import FamilyPage, { Row, Rows } from "../components/FamilyPage";
import { fetchPolicy } from "../lib/pages/detail";
import { useLoaded } from "../lib/pages/useLoaded";
import { fetchRoutines } from "../lib/cockpit/routines";
import { useRoutineControl } from "../lib/cockpit/useRoutineControl";
import { RoutinesPanel } from "../core/panels/CockpitPanels";

const ROUTINE_POLICY = "/v1/routines/policy";

export default function RoutinesPage() {
  const routines = useLoaded(useCallback(() => fetchRoutines(), []));
  const policy = useLoaded(useCallback(() => fetchPolicy(ROUTINE_POLICY), []));
  // Every answer reloads the list, so the rows show the state the Cloud Core now holds
  // rather than the state a click assumed it produced.
  const control = useRoutineControl(routines.refresh);

  return (
    <FamilyPage
      id="routines"
      title="Rutinler"
      lead="Bu sistemin kendi başına yaptığı işler, her birinin çalışıp çalışmadığı ve hangi kurallara göre."
      panel="routines"
    >
      <RoutinesPanel state={routines.state} control={control} always />

      <Rows
        id="routine-policy"
        title="Kurallar"
        state={policy.state}
        empty="Bu Cloud Core rutin kurallarını bildirmiyor."
        onRetry={policy.refresh}
      >
        {(entry) => (
          <Row key={entry.name} keyText={entry.name} head={entry.name} facts={[entry.value]} />
        )}
      </Rows>
    </FamilyPage>
  );
}
