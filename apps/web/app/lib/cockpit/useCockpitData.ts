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
