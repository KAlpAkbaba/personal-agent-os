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
 * Every panel here is read-only. Approving a goal or a SHADOW_READY candidate
 * is an owner action on the surface that owns it (ADR-0053 §5) — the cockpit
 * shows that something is waiting, and stops there.
 */

import { useMemo } from "react";

import OwnerGate from "../../components/OwnerGate";
import { useCockpitData } from "../../lib/cockpit/useCockpitData";
import { eyeView, presenceView, releaseView } from "../../lib/uistate/ambient";
import { eyeClaim, presenceClaim, releaseClaim } from "../../lib/uistate/truth";
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
  EvolutionPanel,
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
  WorldPanel,
} from "../panels/CockpitPanels";
import "../core.css";

function Cockpit() {
  const { truth, now, refresh } = useCoreState();
  const { data, refresh: refreshPanels } = useCockpitData();
  const { tier, setTier, force2d, setForce2d } = useCorePreferences();

  // The tab's one voice session (ADR-0061): its real states overlay the bus
  // body, labelled as this device's own observation.
  const { voice } = useVoiceSession();
  const levels = useVoiceLevels(voice.controller.state);
  const overlay = useMemo(() => voiceOverlayFrom(voice.controller, levels), [voice.controller, levels]);
  const intent = useMemo(() => visualFor(truth, now, overlay), [truth, now, overlay]);
  const eye = useMemo(() => eyeView(eyeClaim(truth, now)), [truth, now]);
  const presence = useMemo(() => presenceView(presenceClaim(truth, now)), [truth, now]);
  const release = useMemo(() => releaseView(releaseClaim(truth, now)), [truth, now]);

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
          <AmbientBand eye={eye} presence={presence} release={release} />
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
          <ShadowReadyPanel state={data.shadowReady} />
          <GoalsPanel state={data.goals} now={now} />
          <ResearchPanel state={data.research} now={now} />
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
