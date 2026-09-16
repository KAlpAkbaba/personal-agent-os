"use client";

/**
 * `/security` — B24 req 696.
 *
 * The constitution's rule is short: *security testing is allowed only for assets recorded
 * as owner/enrolled/authorized*. The API has enforced it since M-security, kept an
 * append-only authorization trail of every grant, every scope change and **every refusal**,
 * and none of it had a surface. An invariant the owner cannot watch being enforced is an
 * invariant they have to take on faith.
 *
 * Four questions, four sections: what am I allowed to test, what was tested, what was
 * found, and what was refused. The refusals are listed with the grants rather than
 * hidden behind a filter, because a system that only shows what it allowed is showing
 * half of an authorization record.
 *
 * B25 req 660 added the one control: the kill switch. `POST /v1/identity/panic` has existed
 * since B05 and the matrix's note on it was three words — *Arayüzde görünmüyor*. Nothing
 * else here changes scope: enrolling, suspending and revoking an asset are owner acts on
 * the surface that mints authority, never a button on a list.
 */

import { useCallback } from "react";

import FamilyPage, { Row, Rows } from "../components/FamilyPage";
import {
  fetchSecurityAssessments,
  fetchSecurityAssets,
  fetchSecurityAudit,
  fetchSecurityFindings,
} from "../lib/pages/detail";
import { useLoaded } from "../lib/pages/useLoaded";
import PanicControl from "./PanicControl";

/** The severities, worst first, so a page can be read top-down. */
const SEVERITY_ORDER = ["critical", "high", "medium", "low", "info"];

const SEVERITY_LABEL: Record<string, string> = {
  critical: "kritik",
  high: "yüksek",
  medium: "orta",
  low: "düşük",
  info: "bilgi",
};

const ASSET_STATUS_LABEL: Record<string, string> = {
  active: "etkin",
  suspended: "askıda",
  revoked: "iptal",
  expired: "süresi doldu",
};

function severityRank(severity: string | null): number {
  const index = SEVERITY_ORDER.indexOf(severity ?? "");
  return index === -1 ? SEVERITY_ORDER.length : index;
}

export default function SecurityPage() {
  const assets = useLoaded(useCallback(() => fetchSecurityAssets(), []));
  const findings = useLoaded(useCallback(() => fetchSecurityFindings(), []));
  const assessments = useLoaded(useCallback(() => fetchSecurityAssessments(), []));
  const audit = useLoaded(useCallback(() => fetchSecurityAudit(), []));

  const sortedFindings =
    findings.state.kind === "ok"
      ? {
          ...findings.state,
          value: [...findings.state.value].sort(
            (a, b) => severityRank(a.severity) - severityRank(b.severity),
          ),
        }
      : findings.state;

  return (
    <FamilyPage
      id="security"
      title="Güvenlik"
      lead="Hangi varlıkları sınamaya yetkiliyim, ne sınandı, ne bulundu — ve neyi reddettim."
    >
      {/* req 660: first, because the one moment it exists for is not a moment for scrolling. */}
      <PanicControl />

      <Rows
        id="security-assets"
        title="Yetkili varlıklar"
        state={assets.state}
        empty="Kayıtlı yetkili varlık yok. Yetkisiz hiçbir hedef sınanmaz."
        badge={(rows) => `${rows.filter((row) => row.status === "active").length} etkin / ${rows.length}`}
        onRetry={assets.refresh}
      >
        {(row) => (
          <Row
            key={row.asset_ref}
            keyText={row.asset_ref}
            tone={row.status === "revoked" || row.status === "expired" ? "bad" : undefined}
            head={row.name ?? row.asset_ref}
            facts={[
              row.asset_ref,
              row.kind,
              row.environment,
              ASSET_STATUS_LABEL[row.status ?? ""] ?? row.status,
              row.valid_until ? `bitiş: ${row.valid_until}` : null,
            ]}
          />
        )}
      </Rows>

      <Rows
        id="security-findings"
        title="Bulgular"
        state={sortedFindings}
        empty="Açık bulgu yok."
        badge={(rows) => `${rows.filter((row) => row.resolved_at === null).length} açık / ${rows.length}`}
        onRetry={findings.refresh}
      >
        {(row) => (
          <Row
            key={row.id}
            keyText={row.id}
            tone={
              row.resolved_at === null && (row.severity === "critical" || row.severity === "high")
                ? "bad"
                : undefined
            }
            head={row.title ?? row.id}
            facts={[
              SEVERITY_LABEL[row.severity ?? ""] ?? row.severity,
              row.resolved_at ? `çözüldü: ${row.resolved_at}` : "açık",
              row.created_at,
            ]}
          />
        )}
      </Rows>

      <Rows
        id="security-assessments"
        title="Değerlendirmeler"
        state={assessments.state}
        empty="Yürütülmüş değerlendirme yok."
        onRetry={assessments.refresh}
      >
        {(row) => (
          <Row
            key={row.id}
            keyText={row.id}
            head={row.target ?? row.testing_class ?? row.id}
            facts={[row.testing_class, row.status, row.completed_at ?? row.created_at]}
          />
        )}
      </Rows>

      <Rows
        id="security-audit"
        title="Yetki izi"
        state={audit.state}
        empty="Kayıtlı yetki olayı yok."
        badge={(rows) => `${rows.filter((row) => !row.allowed).length} ret / ${rows.length}`}
        onRetry={audit.refresh}
      >
        {(row) => (
          <Row
            key={row.id}
            keyText={row.id}
            // A refusal is not an error, so it is drawn as something unfinished rather
            // than something wrong: it is the rule working.
            tone={row.allowed ? undefined : "wait"}
            head={
              <>
                {row.allowed ? "izin" : "ret"} · {row.action ?? "işlem"}
              </>
            }
            facts={[row.asset_ref ?? row.requested_target, row.reason, row.created_at]}
          />
        )}
      </Rows>
    </FamilyPage>
  );
}
