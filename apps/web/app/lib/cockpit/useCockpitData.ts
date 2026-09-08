"use client";

/**
 * The cockpit's slow loop.
 *
 * Deliberately much slower than the Core's: these panels answer "what exists",
 * which changes on human timescales, while the Core answers "what is happening
 * now". Polling nine endpoints at the Core's rate would put the renderer on the
 * critical path of the very system it is describing, which the constitution
 * forbids.
 *
 * Requests are issued together and each result is stored independently, so one
 * failing endpoint leaves the other eight panels truthful rather than blanking
 * the page.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import type { FocusState } from "../research/focus";
import {
  type AmbientPolicy,
  type DeviceStatus,
  type EvolutionSupervisorStatus,
  type Goal,
  type Health,
  type LedgerEvent,
  type Lesson,
  type Loaded,
  type MemoryAuditEvent,
  type Opportunity,
  type PendingBriefing,
  type ResearchTask,
  type ShadowReady,
  type VoiceQualification,
  type WakeAlarm,
  type World,
  fetchAlarms,
  fetchAmbientPolicy,
  fetchDeviceStatus,
  fetchEvolutionSupervisor,
  fetchGoals,
  fetchHealth,
  fetchLedgerEvents,
  fetchLessons,
  fetchMemoryAudit,
  fetchOpportunities,
  fetchPendingBriefings,
  fetchResearchFocus,
  fetchResearchTasks,
  fetchShadowReady,
  fetchVoiceQualification,
  fetchWorld,
} from "./api";
import { type PendingDraft, type PendingProposal, fetchPendingDrafts, fetchPendingProposals } from "./approvals";
import { type AppProjectRow, fetchApps } from "./apps";
import { type ArtifactRow, fetchArtifacts } from "./artifacts";

const POLL_VISIBLE_MS = 15_000;

export type CockpitData = {
  research: Loaded<ResearchTask[]>;
  /** M18.2: which completed report the owner is talking about. */
  researchFocus: Loaded<FocusState>;
  goals: Loaded<Goal[]>;
  opportunities: Loaded<Opportunity[]>;
  shadowReady: Loaded<ShadowReady>;
  world: Loaded<World>;
  ledger: Loaded<LedgerEvent[]>;
  briefings: Loaded<PendingBriefing[]>;
  memory: Loaded<MemoryAuditEvent[]>;
  lessons: Loaded<Lesson[]>;
  health: Loaded<Health>;
  // M18.3. Their routes land on another track; until then they answer
  // "absent", which the panels render as "henüz yok" rather than as empty.
  alarms: Loaded<WakeAlarm[]>;
  ambientPolicy: Loaded<AmbientPolicy>;
  devices: Loaded<DeviceStatus[]>;
  /** ADR-0080: the Owner Utterance Suite's latest recorded result. */
  voiceQualification: Loaded<VoiceQualification>;
  /** ADR-0081: the Evolution Supervisor's picture, from rows alone. */
  evolutionSupervisor: Loaded<EvolutionSupervisorStatus>;
  // M19: there is deliberately no `operator` entry here. The Digital Operator
  // publishes its transitions to the UI-state bus and has no status route;
  // `DigitalOperatorPanel` reads `CoreTruth` (the Core's own feed and clock),
  // like `RunningToolsPanel` and `StateStreamPanel` do.
  /**
   * M21 §3: the drafts and proposals waiting for the owner, from the two
   * pending routes. The Cloud Core half lands on a parallel track; until it
   * does both answer "absent", which the panels say in words. The activity
   * itself (reading, preparing) is on the bus, not here.
   */
  mailDrafts: Loaded<PendingDraft[]>;
  calendarProposals: Loaded<PendingProposal[]>;
  /**
   * M22 §4: the artifacts the factory made, from M13's list route, with each
   * render's validation state once the Cloud Core half publishes it. The
   * making itself (rendering, checking) is on the bus, not here.
   */
  artifacts: Loaded<ArtifactRow[]>;
  /**
   * M23 §6: the projects the App Factory made, from `/v1/apps`, each with
   * its state, the port its bounded process is bound to while it runs, and
   * the counts its last test run gave. The Cloud Core half lands on a
   * parallel track; until it does the route answers "absent", which the
   * panel says in words. The building itself is on the bus, not here.
   */
  apps: Loaded<AppProjectRow[]>;
};

const INITIAL: CockpitData = {
  research: { kind: "loading" },
  researchFocus: { kind: "loading" },
  goals: { kind: "loading" },
  opportunities: { kind: "loading" },
  shadowReady: { kind: "loading" },
  world: { kind: "loading" },
  ledger: { kind: "loading" },
  briefings: { kind: "loading" },
  memory: { kind: "loading" },
  lessons: { kind: "loading" },
  health: { kind: "loading" },
  alarms: { kind: "loading" },
  ambientPolicy: { kind: "loading" },
  devices: { kind: "loading" },
  voiceQualification: { kind: "loading" },
  evolutionSupervisor: { kind: "loading" },
  mailDrafts: { kind: "loading" },
  calendarProposals: { kind: "loading" },
  artifacts: { kind: "loading" },
  apps: { kind: "loading" },
};

export function useCockpitData(enabled = true): { data: CockpitData; refresh: () => void } {
  const [data, setData] = useState<CockpitData>(INITIAL);
  const inFlight = useRef(false);
  const stopped = useRef(false);

  const refresh = useCallback(async () => {
    if (inFlight.current || stopped.current) return;
    inFlight.current = true;
    try {
      const [
        research,
        researchFocus,
        goals,
        opportunities,
        shadowReady,
        world,
        ledger,
        briefings,
        memory,
        lessons,
        health,
        alarms,
        ambientPolicy,
        devices,
        voiceQualification,
        evolutionSupervisor,
        mailDrafts,
        calendarProposals,
        artifacts,
        apps,
      ] = await Promise.all([
        fetchResearchTasks(),
        // Same refresh, no extra timer: the focus changes when the list does.
        fetchResearchFocus(),
        fetchGoals(),
        fetchOpportunities(),
        fetchShadowReady(),
        fetchWorld(),
        fetchLedgerEvents(),
        fetchPendingBriefings(),
        fetchMemoryAudit(),
        fetchLessons(),
        fetchHealth(),
        fetchAlarms(),
        fetchAmbientPolicy(),
        fetchDeviceStatus(),
        fetchVoiceQualification(),
        fetchEvolutionSupervisor(),
        fetchPendingDrafts(),
        fetchPendingProposals(),
        fetchArtifacts(),
        fetchApps(),
      ]);
      if (stopped.current) return;
      setData({
        research,
        researchFocus,
        goals,
        opportunities,
        shadowReady,
        world,
        ledger,
        briefings,
        memory,
        lessons,
        health,
        alarms,
        ambientPolicy,
        devices,
        voiceQualification,
        evolutionSupervisor,
        mailDrafts,
        calendarProposals,
        artifacts,
        apps,
      });
    } finally {
      inFlight.current = false;
    }
  }, []);

  useEffect(() => {
    if (!enabled) return;
    stopped.current = false;
    void refresh();
    const id = setInterval(() => {
      // A hidden cockpit is not worth ten requests every fifteen seconds.
      if (typeof document !== "undefined" && document.hidden) return;
      void refresh();
    }, POLL_VISIBLE_MS);
    return () => {
      stopped.current = true;
      clearInterval(id);
    };
  }, [enabled, refresh]);

  return { data, refresh: () => void refresh() };
}
