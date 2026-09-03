"use client";

import Link from "next/link";
import { useCallback, useState } from "react";

import {
  type Citation,
  type Finding,
  type ReportSource,
  type ResearchReport,
  type Statement,
  citationsFor,
  formatWhen,
  importanceDots,
  labelBadge,
  sourceAnchorId,
  sourceClassLabel,
} from "../lib/research/model";

/**
 * The research report in the spec §3 presentation order:
 * Yönetici Özeti → Bulgular → Neden Önemli → Sonraki Sinyaller →
 * Ayrıntılar (collapsed) → Belirsizlikler → Kaynaklar.
 *
 * Presentational only: it receives the report the owner explicitly opened
 * and never fetches anything itself. Every statement carries its label as a
 * Turkish badge; `[eN]` chips jump to (and briefly highlight) the source row.
 */

const BADGE_STYLE: Record<string, { bg: string; fg: string }> = {
  source_fact: { bg: "rgba(52, 201, 142, 0.15)", fg: "var(--ok)" },
  model_inference: { bg: "rgba(90, 167, 232, 0.15)", fg: "var(--accent)" },
  recommendation: { bg: "rgba(232, 179, 57, 0.15)", fg: "var(--warn)" },
  uncertainty: { bg: "rgba(154, 163, 178, 0.18)", fg: "var(--muted)" },
};

export function LabelBadge({ label }: { label: string }) {
  const style = BADGE_STYLE[label] ?? BADGE_STYLE.uncertainty;
  return (
    <span
      className="badge"
      data-label={label}
      style={{ background: style.bg, color: style.fg, whiteSpace: "nowrap" }}
    >
      {labelBadge(label)}
    </span>
  );
}

function CitationChips({
  citations,
  onJump,
}: {
  citations: Citation[];
  onJump: (sourceId: string) => void;
}) {
  if (citations.length === 0) return null;
  return (
    <span style={{ display: "inline-flex", gap: "0.25rem", flexWrap: "wrap", marginLeft: "0.4rem" }}>
      {citations.map((c) => (
        <a
          key={c.id}
          href={`#${sourceAnchorId(c.id)}`}
          className="citation"
          data-source-id={c.id}
          title={
            c.source
              ? `${c.source.publisher ?? ""} ${c.source.title ?? c.source.url}`.trim()
              : "Kaynak listede bulunamadı"
          }
          onClick={(e) => {
            e.preventDefault();
            onJump(c.id);
          }}
          style={{
            fontSize: "0.75rem",
            fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
            color: c.source ? "var(--accent)" : "var(--fail)",
            textDecoration: c.source ? "none" : "line-through",
            border: "1px solid #232734",
            borderRadius: 6,
            padding: "0 0.35rem",
          }}
        >
          [{c.id}]
        </a>
      ))}
    </span>
  );
}

function StatementRow({
  statement,
  sources,
  onJump,
}: {
  statement: Statement;
  sources: ReportSource[];
  onJump: (id: string) => void;
}) {
  return (
    <li style={{ margin: "0.4rem 0", lineHeight: 1.5 }} className="statement">
      <LabelBadge label={statement.label} />{" "}
      <span>{statement.text}</span>
      <CitationChips citations={citationsFor(statement.evidence_ids, sources)} onJump={onJump} />
      {statement.provenance_note && (
        <span className="muted" style={{ marginLeft: "0.4rem" }}>
          ({statement.provenance_note})
        </span>
      )}
    </li>
  );
}

function StatementList({
  items,
  sources,
  onJump,
  empty,
}: {
  items: Statement[];
  sources: ReportSource[];
  onJump: (id: string) => void;
  empty: string;
}) {
  if (!items || items.length === 0) return <p className="muted">{empty}</p>;
  return (
    <ul style={{ paddingLeft: "1.1rem", margin: 0 }}>
      {items.map((s, i) => (
        <StatementRow key={i} statement={s} sources={sources} onJump={onJump} />
      ))}
    </ul>
  );
}

function FindingCard({
  finding,
  sources,
  onJump,
}: {
  finding: Finding;
  sources: ReportSource[];
  onJump: (id: string) => void;
}) {
  return (
    <article
      className="panel finding"
      data-finding-id={finding.id}
      style={{ padding: "1rem 1.25rem", marginBottom: "0.75rem" }}
    >
      <div className="status-row" style={{ borderBottom: "none", padding: 0, alignItems: "flex-start" }}>
        <strong style={{ lineHeight: 1.4 }}>{finding.title}</strong>
        <span
          className="importance"
          aria-label={`Önem ${finding.importance}/5`}
          title={`Önem ${finding.importance}/5`}
          style={{ letterSpacing: "0.1em", color: "var(--warn)", whiteSpace: "nowrap", marginLeft: "0.75rem" }}
        >
          {importanceDots(finding.importance)}
        </span>
      </div>
      <p style={{ margin: "0.5rem 0", lineHeight: 1.5 }}>
        <LabelBadge label={finding.label} /> {finding.summary}
        <CitationChips citations={citationsFor(finding.evidence_ids, sources)} onJump={onJump} />
      </p>
      {finding.why_it_matters && (
        <p className="muted" style={{ margin: "0.25rem 0 0", lineHeight: 1.5 }}>
          <em>Neden önemli:</em> {finding.why_it_matters}
        </p>
      )}
      {finding.provenance_note && (
        <p className="muted" style={{ margin: "0.25rem 0 0" }}>
          Not: {finding.provenance_note}
        </p>
      )}
    </article>
  );
}

function SourceRow({ source, highlighted }: { source: ReportSource; highlighted: boolean }) {
  const href = source.final_url || source.url;
  return (
    <li
      id={sourceAnchorId(source.id)}
      className="source"
      data-source-id={source.id}
      style={{
        padding: "0.5rem 0.6rem",
        margin: "0.25rem 0",
        borderRadius: 8,
        border: highlighted ? "1px solid var(--accent)" : "1px solid transparent",
        background: highlighted ? "rgba(90, 167, 232, 0.12)" : "transparent",
        transition: "background 0.3s, border-color 0.3s",
        lineHeight: 1.5,
      }}
    >
      <code style={{ color: "var(--accent)" }}>[{source.id}]</code>{" "}
      <a
        href={href}
        target="_blank"
        rel="noreferrer noopener"
        style={{ color: "var(--text)", fontWeight: 600 }}
      >
        {source.title || href}
      </a>
      <div className="muted" style={{ display: "flex", gap: "0.5rem", flexWrap: "wrap", alignItems: "center" }}>
        {source.publisher && <span>{source.publisher}</span>}
        <span className="badge unknown" style={{ fontWeight: 500 }}>
          {sourceClassLabel(source.source_class)}
        </span>
        <span>yayın: {formatWhen(source.published_at)}</span>
        <span>alındı: {formatWhen(source.retrieved_at)}</span>
        {source.injection_suspected && (
          <span className="badge fail" title="Sayfa metninde talimat benzeri içerik tespit edildi; yalnızca alıntı olarak kullanıldı.">
            enjeksiyon şüphesi
          </span>
        )}
        {source.syndicated_of && (
          <span>
            aynı içerik: <code>[{source.syndicated_of}]</code> kaynağının kopyası
          </span>
        )}
      </div>
    </li>
  );
}

function SectionTitle({ id, children }: { id: string; children: React.ReactNode }) {
  return (
    <h2 id={id} style={{ fontSize: "1.1rem", margin: "1.5rem 0 0.5rem" }}>
      {children}
    </h2>
  );
}

export type ReportViewProps = {
  report: ResearchReport;
  artifactId?: string | null;
  onCopyJson?: () => void;
  copyState?: "idle" | "copied" | "failed";
};

export default function ReportView({ report, artifactId, onCopyJson, copyState = "idle" }: ReportViewProps) {
  const [highlighted, setHighlighted] = useState<string | null>(null);
  const sources = report.sources ?? [];

  const jump = useCallback((sourceId: string) => {
    setHighlighted(sourceId);
    if (typeof document !== "undefined") {
      const el = document.getElementById(sourceAnchorId(sourceId));
      el?.scrollIntoView({ behavior: "smooth", block: "center" });
    }
  }, []);

  return (
    <section className="report" data-task-id={report.task_id}>
      <div className="status-row" style={{ borderBottom: "none", paddingBottom: 0, alignItems: "flex-start" }}>
        <div>
          <strong style={{ fontSize: "1.05rem" }}>{report.topic}</strong>
          <div className="muted">
            {report.window?.label ?? "pencere belirtilmedi"}
            {report.generated_at && <> · oluşturuldu {formatWhen(report.generated_at)}</>}
            {report.synthesis_provider && <> · sentez: {report.synthesis_provider}</>}
          </div>
        </div>
        <div style={{ display: "flex", gap: "0.5rem", flexWrap: "wrap", justifyContent: "flex-end" }}>
          {artifactId && (
            <Link
              href="/artifacts"
              className="badge ok"
              data-artifact-id={artifactId}
              title={`Artifact ${artifactId}`}
              style={{ textDecoration: "none" }}
            >
              Gelen kutusunda aç
            </Link>
          )}
          {onCopyJson && (
            <button
              onClick={onCopyJson}
              className="badge unknown"
              style={{ border: "none", cursor: "pointer", font: "inherit" }}
            >
              {copyState === "copied"
                ? "Kopyalandı"
                : copyState === "failed"
                  ? "Kopyalanamadı"
                  : "Rapor JSON'unu kopyala"}
            </button>
          )}
        </div>
      </div>

      <SectionTitle id="ozet">Yönetici Özeti</SectionTitle>
      <p style={{ lineHeight: 1.6, margin: 0 }}>{report.executive_summary}</p>

      <SectionTitle id="bulgular">Bulgular</SectionTitle>
      {report.findings.length === 0 ? (
        <p className="muted">Bu pencerede yeterli bulgu yok; ayrıntı için Belirsizlikler bölümüne bak.</p>
      ) : (
        report.findings.map((f) => (
          <FindingCard key={f.id} finding={f} sources={sources} onJump={jump} />
        ))
      )}

      <SectionTitle id="neden-onemli">Neden Önemli</SectionTitle>
      <StatementList items={report.why_it_matters} sources={sources} onJump={jump} empty="—" />

      <SectionTitle id="sonraki-sinyaller">Sonraki Sinyaller</SectionTitle>
      <StatementList items={report.watch_next} sources={sources} onJump={jump} empty="—" />

      <SectionTitle id="ayrintilar">Ayrıntılar</SectionTitle>
      {(report.details ?? []).length === 0 ? (
        <p className="muted">—</p>
      ) : (
        <details className="panel" style={{ padding: "0.75rem 1.25rem" }}>
          <summary style={{ cursor: "pointer" }}>
            {report.details.length} başlık — açmak için tıkla
          </summary>
          {report.details.map((section, i) => (
            <div key={i} style={{ marginTop: "0.75rem" }}>
              <h3 style={{ fontSize: "0.95rem", margin: "0 0 0.25rem" }}>{section.heading}</h3>
              <StatementList items={section.statements} sources={sources} onJump={jump} empty="—" />
            </div>
          ))}
        </details>
      )}

      <SectionTitle id="belirsizlikler">Belirsizlikler</SectionTitle>
      <StatementList items={report.uncertainty} sources={sources} onJump={jump} empty="Kayıtlı belirsizlik yok." />

      <SectionTitle id="kaynaklar">Kaynaklar</SectionTitle>
      {sources.length === 0 ? (
        <p className="muted">Kaynak yok.</p>
      ) : (
        <ol style={{ listStyle: "none", padding: 0, margin: 0 }}>
          {sources.map((s) => (
            <SourceRow key={s.id} source={s} highlighted={highlighted === s.id} />
          ))}
        </ol>
      )}

      {report.stats && (
        <p className="muted" style={{ marginTop: "1rem" }}>
          {report.stats.queries ?? 0} sorgu · {report.stats.discovered ?? 0} keşfedildi ·{" "}
          {report.stats.fetched ?? 0} getirildi · {report.stats.fetch_failed ?? 0} başarısız ·{" "}
          {report.stats.deduplicated ?? 0} yinelenen · {report.stats.evidence ?? 0} kanıt
        </p>
      )}
    </section>
  );
}
