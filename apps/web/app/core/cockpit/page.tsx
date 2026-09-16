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

import { updateAmbientPolicy } from "../../lib/cockpit/api";
import { useCallback, useMemo, useState } from "react";

import OwnerGate from "../../components/OwnerGate";
import { approvalClient } from "../../lib/cockpit/approvals";
import { appsClient } from "../../lib/cockpit/apps";
import { artifactClient, downloadRender } from "../../lib/cockpit/artifacts";
import { type CreativeRunRow, creativeClient } from "../../lib/cockpit/creative";
import { type ExecutiveRunRow, executiveClient } from "../../lib/cockpit/executive";
import { genesisClient } from "../../lib/cockpit/genesis";
import { type SceneRow, sceneClient } from "../../lib/cockpit/scenes";
import { useApprovalPair } from "../../lib/cockpit/useApprovalPair";
import { useAppsControl } from "../../lib/cockpit/useAppsControl";
import { useArtifactOpen } from "../../lib/cockpit/useArtifactOpen";
import { quietFamilies } from "../../lib/cockpit/families";
import { useCockpitData } from "../../lib/cockpit/useCockpitData";
import { useNotificationRead } from "../../lib/cockpit/useNotificationRead";
import { useRoutineControl } from "../../lib/cockpit/useRoutineControl";
import { useCreativeControl } from "../../lib/cockpit/useCreativeControl";
import { useCreativeImages } from "../../lib/cockpit/useCreativeImages";
import { useExecutiveControl } from "../../lib/cockpit/useExecutiveControl";
import { useExecutiveDetail } from "../../lib/cockpit/useExecutiveDetail";
import { useGenesisControl } from "../../lib/cockpit/useGenesisControl";
import { useSceneControl } from "../../lib/cockpit/useSceneControl";
import { useSceneRender } from "../../lib/cockpit/useSceneRender";
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
import { QuietFamilies } from "../panels/Panel";
import {
  AlarmsPanel,
  DevicesPanel,
  AmbientPanel,
  AppsPanel,
  ArtifactsPanel,
  CalendarPanel,
  CreativePanel,
  DigitalOperatorPanel,
  DocumentsPanel,
  EvolutionPanel,
  EvolutionSupervisorPanel,
  ExecutivePanel,
  GenesisPanel,
  GoalsPanel,
  HealthPanel,
  LedgerPanel,
  LessonsPanel,
  MailPanel,
  MemoryPanel,
  NativePanel,
  OwnerActionsPanel,
  ResearchPanel,
  RunningToolsPanel,
  ScenesPanel,
  ShadowReadyPanel,
  StateStreamPanel,
  NotificationsPanel,
  RoutinesPanel,
  VoiceQualificationPanel,
  WorldPanel,
} from "../panels/CockpitPanels";
import "../core.css";

/** A stable empty list, so the render hook's effect does not re-run on every poll. */
const EMPTY_SCENES: SceneRow[] = [];

/** The same, for the executive rows the detail hook reads. */
const EMPTY_RUNS: ExecutiveRunRow[] = [];

/** The same again, for the creative rows the image hook reads. */
const EMPTY_CREATIVE: CreativeRunRow[] = [];

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
  // B11 req 368: the only write this panel can make, and it leaves the process.
  const notificationRead = useNotificationRead(refreshPanels);
  // B14 req 295: pause and resume, the two reversible controls over a routine.
  const routineControl = useRoutineControl(refreshPanels);
  const approvals = useApprovalPair(approvalClient, refreshPanels);

  // M22 §4: "Aç" asks the Cloud Core to fetch and open an artifact on the
  // device, one at a time, and every answer reloads the list; a download
  // fetches the render's bytes through the owner session (M13's pattern)
  // and hands the browser a blob — a failure is said in words, and the link
  // itself stays.
  const artifactOpen = useArtifactOpen(artifactClient, refreshPanels);
  // M23 §6: the three chips ask the Cloud Core for the device's bounded
  // `project.run` / `project.stop` / `project.test`, one call at a time, and
  // every answer reloads the list so the rows show the state the Cloud Core
  // now holds — never the state this page assumed a click produced.
  const appsControl = useAppsControl(appsClient, refreshPanels);
  // M24 §8: "Onayla" and "Vazgeç" ask the Cloud Core for its own
  // `capability.approve` / `capability.cancel` on one run, one call at a
  // time, and every answer reloads the list so the rows show the state the
  // Cloud Core now holds — never the state this page assumed a click
  // produced. The routes land on the Cloud Core track (ADR-0087 §8); until
  // they do, the panel says "henüz yok".
  const genesisControl = useGenesisControl(genesisClient, refreshPanels);
  // M25 §6: "Render al" and "Sahneyi oku" ask the Cloud Core for the
  // device's bounded `scene.render` / `scene.inspect` on one scene, one
  // call at a time, and every answer reloads the list so the rows show what
  // the tool now holds — never what this page assumed a click produced. The
  // routes land on the Cloud Core track (ADR-0088 §8); until they do, the
  // panel says "henüz yok". `sceneTool` is read from the rows only so an
  // `unavailable` Unity is worded with its licence rather than generically.
  const sceneRows = useMemo(
    () => (data.scenes.kind === "ok" ? data.scenes.value : EMPTY_SCENES),
    [data.scenes],
  );
  const sceneTool = useCallback(
    (sceneId: string) => sceneRows.find((row) => row.scene_id === sceneId)?.tool ?? null,
    [sceneRows],
  );
  const sceneControl = useSceneControl(sceneClient, refreshPanels, sceneTool);
  // M26 §6: "Duraklat", "Devam" and "İptal" ask the Cloud Core for the
  // workflow's own pause / resume / cancel signals on one run, one call at a
  // time, and every answer reloads the list so the rows show the state the
  // Cloud Core now holds — never the state this page assumed a click
  // produced. The current step's sentence comes from the run's OWN route,
  // asked only for the runs that have not ended (≤ 2 at once, spec §4). The
  // routes land on the Cloud Core track (ADR-0089 §8); until they do, the
  // panel says "henüz yok".
  const executiveRows = useMemo(
    () => (data.executiveRuns.kind === "ok" ? data.executiveRuns.value : EMPTY_RUNS),
    [data.executiveRuns],
  );
  const executiveControl = useExecutiveControl(executiveClient, refreshPanels);
  const executiveDetails = useExecutiveDetail(executiveRows);
  // The render route is owner-session gated, so the images are FETCHED with
  // the session and handed to the rows as blobs; a bare <img src> would 401.
  const scenePreview = useSceneRender(sceneRows);
  // M27 §6: "Dışa aktar" and "Karşılaştır" ask the Cloud Core for its own
  // `creative.export` and its comparison over the device's
  // `creative.export_check` on one run, one call at a time, and every answer
  // reloads the list so the rows show what the Cloud Core now holds — never
  // what this page assumed a click produced. The routes land on the Cloud
  // Core track (ADR-0093); until they do, the panel says "henüz yok".
  // `creativeTool` is read from the rows only so an `unavailable` Photoshop
  // is worded as "kurulu değil" rather than generically.
  const creativeRows = useMemo(
    () => (data.creativeRuns.kind === "ok" ? data.creativeRuns.value : EMPTY_CREATIVE),
    [data.creativeRuns],
  );
  const creativeTool = useCallback(
    (runId: string) => creativeRows.find((row) => row.run_id === runId)?.tool ?? null,
    [creativeRows],
  );
  const creativeControl = useCreativeControl(creativeClient, refreshPanels, creativeTool);
  // The image route is owner-session gated too, so the before/after pictures
  // are FETCHED with the session and handed to the rows as blobs; a bare
  // <img src> would 401 twice per row.
  const creativePreview = useCreativeImages(creativeRows);
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
  // req 714: which families answered with nothing, from the same loaded data the
  // panels themselves read - never a second count of the same thing.
  const quiet = useMemo(() => quietFamilies(data).map((family) => family.label), [data]);

  return (
    // B25 req 724: a landmark and a heading. The densest page in the product had neither,
    // so a screen reader entered twenty-seven panels with nothing to enter AT. The heading
    // is visually hidden: the Core's own stage is the title here.
    <main className="core-page">
      <h1 className="visually-hidden">Kokpit</h1>
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
          {/* B24 req 714: an empty family draws no panel, and this is why the page
              does not simply lose it. The audit counted thirteen empty panels at
              once; hiding thirteen things silently would trade one lie for
              another, so they are named here and explained on /availability. */}
          <QuietFamilies labels={quiet} />
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
          {/* B11 req 368/377: what the system tried to tell the owner while they were
              NOT looking at this page, and whether anything actually carried it. Placed
              with the owner's own surfaces: reading one is their act on their record. */}
          <NotificationsPanel
            state={data.notifications}
            onMarkRead={notificationRead.markRead}
            busyId={notificationRead.busyId}
          />
          <RunningToolsPanel truth={truth} now={now} />
          {/* M18.3: what is set to wake the owner, and what the screens are
              doing. Both are read-only here; the renderer owns no policy. */}
          <AlarmsPanel state={data.alarms} now={now} />
          {/* B23 req 695: the device family had a client, a parser and a slot in
              CockpitData since M18.3, and appeared only as a line about SCREENS inside
              Ekran/Ortam. This is the device surface, addressable at #devices. */}
          <DevicesPanel devices={data.devices} now={now} />
          {/* B14 req 295: what this system does on its own, and whether each of them is
              actually running. Beside the alarms, because a recurring alarm IS a routine -
              the panel is where the owner finds out that seven of them were created for
              them rather than by them. */}
          <RoutinesPanel state={data.routines} control={routineControl} />
          <AmbientPanel
            policy={data.ambientPolicy}
            devices={data.devices}
            onToggle={(field, value) => void updateAmbientPolicy(field, value).then(refreshPanels)}
          />
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
          <DocumentsPanel truth={truth} now={now} pending={data.documentMutations} pair={approvals.mutations} />
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
          {/* M23 §6: what the App Factory made — each project's state, the
              port while it runs (a link for the owner's own browser), the
              last test counts, and the chips for the device's bounded
              process. */}
          <AppsPanel apps={data.apps} truth={truth} now={now} control={appsControl} />
          {/* M24 §8: the capabilities the assistant is acquiring for the
              owner — each run's state, the error when it failed, "Onayla"
              only while one waits for the owner, "Vazgeç" while one runs. */}
          <GenesisPanel runs={data.genesisRuns} truth={truth} now={now} control={genesisControl} />
          {/* M25 §6: the 3D scenes the assistant made — each scene's tool
              and step, what the last inspection read back, the last render
              as an image through the owner session, and the two chips for
              the device's bounded editor run. A tool that cannot be driven
              gets no chips and says why. */}
          <ScenesPanel
            scenes={data.scenes}
            truth={truth}
            now={now}
            control={sceneControl}
            preview={scenePreview}
          />
          {/* M26 §6: the multi-step jobs the assistant is carrying — each
              run's state, the step it is on with the route's own sentence
              for it, how many steps are done, what a partly finished run is
              missing, and the three chips the owner stops and resumes a run
              with. */}
          <ExecutivePanel
            runs={data.executiveRuns}
            truth={truth}
            now={now}
            control={executiveControl}
            details={executiveDetails}
          />
          {/* M27 §6: the pictures the assistant made — each run's
              application, the operation it is on and the step it reached,
              what the comparison measured, the owner's original beside what
              was produced from it through the owner session, and the two
              chips for the Cloud Core's export and comparison. An
              application that is not installed gets no chips and says so. */}
          <CreativePanel
            runs={data.creativeRuns}
            truth={truth}
            now={now}
            control={creativeControl}
            preview={creativePreview}
          />
          {/* M28 §6: the applications the assistant compiled for the owner
              — each build's target and stack, the step it reached, and for
              a build that produced something the artefact's name, size and
              hash and what a reader that did NOT build it concluded. No
              controls: starting a compiler and installing a package are
              asked for by voice through the ONE router, which gates them. */}
          <NativePanel builds={data.nativeBuilds} truth={truth} now={now} />
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
    </main>
  );
}

export default function CockpitPage() {
  return (
    <OwnerGate>
      <Cockpit />
    </OwnerGate>
  );
}
