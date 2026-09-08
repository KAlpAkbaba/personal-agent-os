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
 *
 * M21 adds the one control that asks the Cloud Core to change the world
 * outside — the Approve/Discard pair under a pending draft or proposal — and
 * it is not a second authority surface for the same reason: the click asks
 * the Cloud Core to run the SAME gate the spoken "Gönder." / "Onayla." runs
 * (ADR-0084 §1), and the Cloud Core refuses on its own terms. The pair
 * decides nothing, reaches no provider, and is disabled with its reason in
 * words until the row was read back to the owner.
 */

import { useCallback, useMemo, useState } from "react";

import OwnerGate from "../../components/OwnerGate";
import { approvalClient } from "../../lib/cockpit/approvals";
import { artifactClient, downloadRender } from "../../lib/cockpit/artifacts";
import { useApprovalPair } from "../../lib/cockpit/useApprovalPair";
import { useArtifactOpen } from "../../lib/cockpit/useArtifactOpen";
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
  ArtifactsPanel,
  CalendarPanel,
  DigitalOperatorPanel,
  DocumentsPanel,
  EvolutionPanel,
  EvolutionSupervisorPanel,
  GoalsPanel,
  HealthPanel,
  LedgerPanel,
  LessonsPanel,
  MailPanel,
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

  // M21: the approval pair's one state, bound to the real client; every
  // answer reloads the pending lists so the panels show what the Cloud Core
  // now holds rather than what this page assumed it did.
  const approvals = useApprovalPair(approvalClient, refreshPanels);

  // M22 §4: "Aç" asks the Cloud Core to fetch and open an artifact on the
  // device, one at a time, and every answer reloads the list; a download
  // fetches the render's bytes through the owner session (M13's pattern)
  // and hands the browser a blob — a failure is said in words, and the link
  // itself stays.
  const artifactOpen = useArtifactOpen(artifactClient, refreshPanels);
  const [downloadNotice, setDownloadNotice] = useState<string | null>(null);
  const download = useCallback((artifactId: string, format: string) => {
    setDownloadNotice(null);
    void downloadRender(artifactId, format).catch((err: unknown) => {
      if (err instanceof UnauthorizedError) return;
      setDownloadNotice(`İndirilemedi (${format.toUpperCase()}): ${err instanceof Error ? err.message : String(err)}`);
    });
  }, []);

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
          {/* M21 §3: the drafts and proposals waiting for the owner, with the
              pair that asks the Cloud Core to run its gate on one — placed
              with the owner's actions, because that is what they are. */}
          <MailPanel pending={data.mailDrafts} truth={truth} now={now} pair={approvals.drafts} />
          <CalendarPanel pending={data.calendarProposals} truth={truth} now={now} pair={approvals.proposals} />
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
          {/* M22 §4: what the factory made — each render with the verdict
              the independent parser gave it, a download per valid render on
              the owner-session-gated route, and "Aç" for the device. */}
          <ArtifactsPanel
            artifacts={data.artifacts}
            truth={truth}
            now={now}
            open={artifactOpen}
            onDownload={download}
            notice={downloadNotice}
          />
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
