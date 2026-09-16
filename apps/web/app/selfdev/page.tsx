"use client";

/**
 * `/selfdev` — B24 req 697 ("621 ile aynı sayfa").
 *
 * The self-development engine is the subsystem the constitution is strictest about: the
 * path is *gap → spec → isolated branch → code → tests → review → build → sandbox →
 * canary → metrics → promote or rollback*, and never "the model edits production source
 * and restarts". Four cockpit panels each showed one stage of that path, in four different
 * places, with nothing saying they were the same pipeline.
 *
 * This page is the pipeline in its order: what the system noticed it could not do, what it
 * built for it, what came out of the supervisor's own scanning, what is finished and
 * waiting on the owner, and what it learned. The owner gate at the end — approving a
 * SHADOW_READY candidate — stays where it is: this page shows that something is waiting
 * and stops there (ADR-0053 §5).
 */

import { useCallback } from "react";

import FamilyPage, { Row, Rows } from "../components/FamilyPage";
import {
  fetchEvolutionSupervisor,
  fetchLessons,
  fetchOpportunities,
  fetchShadowReady,
} from "../lib/cockpit/api";
import { approvalClient, fetchPendingCandidates } from "../lib/cockpit/approvals";
import { fetchGenesisCatalogue } from "../lib/cockpit/genesis";
import { useApprovalPair } from "../lib/cockpit/useApprovalPair";
import { fetchCapabilityGaps, fetchPolicy, fetchSkillVersions } from "../lib/pages/detail";
import { useLoaded, useNow } from "../lib/pages/useLoaded";
import {
  EvolutionPanel,
  EvolutionSupervisorPanel,
  GenesisCataloguePanel,
  LessonsPanel,
  SelfDevQueuePanel,
  ShadowReadyPanel,
} from "../core/panels/CockpitPanels";

const EVOLUTION_POLICY = "/v1/evolution/policy";

export default function SelfDevPage() {
  const now = useNow();
  const gaps = useLoaded(useCallback(() => fetchCapabilityGaps(), []));
  const versions = useLoaded(useCallback(() => fetchSkillVersions(), []));
  const supervisor = useLoaded(useCallback(() => fetchEvolutionSupervisor(), []));
  const opportunities = useLoaded(useCallback(() => fetchOpportunities(), []));
  const shadowReady = useLoaded(useCallback(() => fetchShadowReady(), []));
  const lessons = useLoaded(useCallback(() => fetchLessons(), []));
  const policy = useLoaded(useCallback(() => fetchPolicy(EVOLUTION_POLICY), []));
  // B35 (req 609, 621): the candidates awaiting the owner, and the pair that records
  // the decision. The list reloads after every answer, so what the Cloud Core now
  // holds is what the page shows.
  const candidates = useLoaded(useCallback(() => fetchPendingCandidates(), []));
  const approvals = useApprovalPair(approvalClient, candidates.refresh);
  // B36 (req 562/563/576): the interfaces the owner registered for Capability Genesis.
  const catalogue = useLoaded(useCallback(() => fetchGenesisCatalogue(), []));

  return (
    <FamilyPage
      id="selfdev"
      title="Kendini geliştirme"
      lead="Yapamadığını fark ettiği andan, sizin onayınızı beklediği ana kadar — aynı boru hattı, sırasıyla."
      panel="evolution-supervisor"
    >
      <Rows
        id="capability-gaps"
        title="1 · Eksik yetenekler"
        state={gaps.state}
        empty="Yapamadığı bir şeye rastlamadı."
        badge={(rows) => `${rows.filter((row) => row.resolved_at === null).length} açık / ${rows.length}`}
        onRetry={gaps.refresh}
      >
        {(row) => (
          <Row
            key={row.id}
            keyText={row.id}
            tone={row.resolved_at === null ? "wait" : undefined}
            head={row.requested_capability ?? row.id}
            facts={[row.status, row.resolution, row.resolved_at ?? row.created_at]}
          />
        )}
      </Rows>

      <Rows
        id="skill-versions"
        title="2 · Üretilen sürümler"
        state={versions.state}
        empty="Üretilmiş yetenek sürümü yok."
        onRetry={versions.refresh}
      >
        {(row) => (
          <Row
            key={row.id}
            keyText={row.id}
            tone={row.rejected_reason ? "bad" : undefined}
            head={`${row.capability_id ?? "yetenek"} v${row.version ?? "?"}`}
            facts={[row.status, row.rejected_reason, row.registered_at]}
          />
        )}
      </Rows>

      <SelfDevQueuePanel state={candidates.state} now={now} pair={approvals.candidates} always />
      <EvolutionSupervisorPanel state={supervisor.state} now={now} always />
      <EvolutionPanel state={opportunities.state} always />
      <ShadowReadyPanel state={shadowReady.state} always />
      <GenesisCataloguePanel state={catalogue.state} always />
      <LessonsPanel state={lessons.state} always />

      <Rows
        id="evolution-policy"
        title="Kurallar"
        state={policy.state}
        empty="Bu Cloud Core evrim kurallarını bildirmiyor."
        onRetry={policy.refresh}
      >
        {(entry) => (
          <Row key={entry.name} keyText={entry.name} head={entry.name} facts={[entry.value]} />
        )}
      </Rows>
    </FamilyPage>
  );
}
