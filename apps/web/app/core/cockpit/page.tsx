"use client";

/**
 * `/core/cockpit` — Cognitive Cockpit Mode.
 *
 * The Core, plus the panels that answer what the Core cannot. The Core says
 * what is happening *now*, from the UI-state bus; the panels say what *exists*,
 * from the read-only REST surfaces. They poll on different clocks because they
 * answer different questions, and mixing them would either hammer nine
 * endpoints once a second or leave the Core a second behind.
 *
 * Every panel here is read-only about the system's own work. Approving a goal
 * or a SHADOW_READY candidate is an owner action on the surface that owns it
 * (ADR-0053 §5) — the cockpit shows that something is waiting, and stops there.
 *
 * The single exception, M18.2, is not an exception to that rule: choosing
 * which completed report the conversation is about is the owner's own act, not
 * the renderer approving work or setting policy. Clicking a research row says
 * "this one", by id.
 */

import { useCallback, useMemo, useState } from "react";

import OwnerGate from "../../components/OwnerGate";
import { useCockpitData } from "../../lib/cockpit/useCockpitData";
import { selectResearchFocus } from "../../lib/research/api";
import { UnauthorizedError } from "../../lib/session";
import { alarmView, displayView, eyeView, presenceView, releaseView } from "../../lib/uistate/ambient";
import {
  alarmClaim,
  displayClaim,
  eyeClaim,
  presenceClaim,
  releaseClaim,
} from "../../lib/uistate/truth";
import { useCoreState } from "../../lib/uistate/useCoreState";
import { visualFor } from "../../lib/uistate/visual";
import { voiceOverlayFrom } from "../../lib/uistate/voice-overlay";
import { useVoiceLevels, useVoiceSession } from "../../lib/voice/useVoiceSession";
import AmbientBand from "../AmbientBand";
import ChannelReadout from "../ChannelReadout";
import CoreBar from "../CoreBar";
import CoreView from "../CoreView";
import EyeControl from "../EyeControl";
import StateReadout from "../StateReadout";
import VoiceControl from "../VoiceControl";
import { useCorePreferences } from "../usePreferences";
import {
  AlarmsPanel,
  AmbientPanel,
  DigitalOperatorPanel,
  DocumentsPanel,
  EvolutionPanel,
  EvolutionSupervisorPanel,
  GoalsPanel,
  HealthPanel,
  LedgerPanel,
  LessonsPanel,
  MemoryPanel,
  OwnerActionsPanel,
  ResearchPanel,
  RunningToolsPanel,
  ShadowReadyPanel,
  StateStreamPanel,
  VoiceQualificationPanel,
  WorldPanel,
} from "../panels/CockpitPanels";
import "../core.css";

function Cockpit() {
  const { truth, now, refresh } = useCoreState();
  const { data, refresh: refreshPanels } = useCockpitData();
  const { tier, setTier, force2d, setForce2d } = useCorePreferences();
  const [focusNotice, setFocusNotice] = useState<string | null>(null);

  // "Bunu anlat." has to mean this report; the click is what says which.
  const chooseFocus = useCallback(
    async (taskId: string) => {
      try {
        const selection = await selectResearchFocus(taskId);
        setFocusNotice(selection.notice);
        // The refreshed focus reaches the panel through the normal loader,
        // so there is one source of truth for what the focus is.
        refreshPanels();
      } catch (err) {
        if (err instanceof UnauthorizedError) return;
        setFocusNotice(err instanceof Error ? err.message : String(err));
      }
    },
    [refreshPanels],
  );

  // The tab's one voice session (ADR-0061): its real states overlay the bus
  // body, labelled as this device's own observation.
  const { voice } = useVoiceSession();
  const levels = useVoiceLevels(voice.controller.state);
  const overlay = useMemo(() => voiceOverlayFrom(voice.controller, levels), [voice.controller, levels]);
  const intent = useMemo(() => visualFor(truth, now, overlay), [truth, now, overlay]);
  const eye = useMemo(() => eyeView(eyeClaim(truth, now)), [truth, now]);
  const presence = useMemo(() => presenceView(presenceClaim(truth, now)), [truth, now]);
  const release = useMemo(() => releaseView(releaseClaim(truth, now)), [truth, now]);
  const display = useMemo(() => displayView(displayClaim(truth, now)), [truth, now]);
  const alarm = useMemo(() => alarmView(alarmClaim(truth, now)), [truth, now]);

  return (
    <div className="core-page">
      <CoreBar
        mode="cockpit"
        connection={truth.connection}
        tier={tier}
        onTier={setTier}
        force2d={force2d}
        onForce2d={setForce2d}
        onRefresh={() => {
          refresh();
          refreshPanels();
        }}
      />

      <div className="cockpit" data-core-mode="cockpit">
        <div className="cockpit-core">
          <CoreView intent={intent} tier={tier} force2d={force2d} />
          <StateReadout intent={intent} />
          {/* M18.1: the cockpit is the detailed mode - it also prints the
              channels the geometry was drawn from. */}
          <ChannelReadout intent={intent} />
          <section className="ambient-band" aria-label="Ses oturumu">
            <VoiceControl />
          </section>
          <AmbientBand
            eye={eye}
            presence={presence}
            release={release}
            display={display}
            alarm={alarm}
          />
          <section className="ambient-band" aria-label="Göz kontrolü">
            <EyeControl eye={eye} />
          </section>
        </div>

        <div className="cockpit-panels">
          {/* What needs the owner comes first: it is the only thing here that
              is blocked on a human rather than on the system. */}
          <OwnerActionsPanel
            briefings={data.briefings}
            goals={data.goals}
            shadowReady={data.shadowReady}
            now={now}
          />
          <RunningToolsPanel truth={truth} now={now} />
          {/* M18.3: what is set to wake the owner, and what the screens are
              doing. Both are read-only here; the renderer owns no policy. */}
          <AlarmsPanel state={data.alarms} now={now} />
          <AmbientPanel policy={data.ambientPolicy} devices={data.devices} />
          {/* ADR-0080: whether the owner's words still route where they say. */}
          <VoiceQualificationPanel state={data.voiceQualification} now={now} />
          <ShadowReadyPanel state={data.shadowReady} />
          {/* ADR-0081: what the supervisor found, what is being built, what is running. */}
          <EvolutionSupervisorPanel state={data.evolutionSupervisor} now={now} />
          {/* M19 §4: what the operator is doing on the owner's desktop — the
              step, the capability, the window it observed — from the state
              feed the Core reads, on the Core's clock rather than this page's. */}
          <DigitalOperatorPanel truth={truth} now={now} />
          {/* M20 §3: which of the owner's documents the Core is reading, which
              it read before, and what its last answer cited — from the bus,
              never from a file. The owner's files stay on the owner's machine. */}
          <DocumentsPanel truth={truth} now={now} />
          <GoalsPanel state={data.goals} now={now} />
          <ResearchPanel
            state={data.research}
            focus={data.researchFocus}
            now={now}
            onSelect={(taskId) => void chooseFocus(taskId)}
            notice={focusNotice}
          />
          <MemoryPanel state={data.memory} now={now} />
          <WorldPanel state={data.world} />
          <EvolutionPanel state={data.opportunities} />
          <LessonsPanel state={data.lessons} />
          <HealthPanel state={data.health} />
          <LedgerPanel state={data.ledger} now={now} />
          <StateStreamPanel truth={truth} now={now} />
        </div>
      </div>
    </div>
  );
}

export default function CockpitPage() {
  return (
    <OwnerGate>
      <Cockpit />
    </OwnerGate>
  );
}
