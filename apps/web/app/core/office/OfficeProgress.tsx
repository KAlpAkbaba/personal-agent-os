/**
 * The İlerleme strip under the Ofis's top bar (office-progress): three percents with thin bars;
 * a click (the native <details>) opens the panel with the JARVIS rows by state and the order's
 * next step. A section the server could not read is "okunamadı", never a made-up number.
 * Styled inline in the office's dark palette so the strip needs no stylesheet change.
 */

import type { CSSProperties } from "react";

import type { JarvisState, OfficeProgress as Progress, StepState } from "./officeApi";

/** The proof (proof-from-test-rounds-and-trials): additive, so an older server does not send it;
 * `null` is a server that could not read the JARVIS table. Typed here, beside its only reader. */
export type ProofRow = {
  name: string;
  state: JarvisState;
  staging: { round: string; sha: string; at: string; passed: number; failed: number } | null;
  staging_proven: boolean;
  trial: { task_id: string | null; trial_id: string | null; verdict: string; at: string | null } | null;
  real_proven: boolean;
};

export type ProgressProof = {
  counted: number;
  staging_proven: number;
  real_proven: number;
  percent_staging: number | null;
  percent_real: number | null;
  release: string | null;
  rows: ProofRow[];
  /** Tasks and round rows that name no JARVIS row: left out of the share, counted here. */
  outside?: { count: number; unknown: string[] };
  rule: string;
};

export type ProgressWithProof = Progress & { proof?: ProgressProof | null };

// The model: the server's answer as the owner reads it - one Turkish line, three bars, the
// JARVIS rows grouped by state and the order's next open step. Pure, and in this file because
// a sibling `officeProgress.ts` would collide with this one on a case-insensitive disk.

export type ProgressBar = { key: string; label: string; percent: number | null };

export type ProgressModel = {
  line: string;
  bars: ProgressBar[];
  groups: { state: JarvisState; title: string; rows: string[] }[];
  /** The JARVIS rows each proof holds; empty for an answer without the proof. */
  proven: { key: string; title: string; rows: string[] }[];
  proofRule: string | null;
  next: string | null;
  rule: string;
  asOf: string | null;
};

const GROUPS: { state: JarvisState; title: string }[] = [
  { state: "have", title: "Var" },
  { state: "partial", title: "Yarım" },
  { state: "missing", title: "Yok" },
  { state: "unknown", title: "Okunamadı (yapılmamış sayıldı)" },
  { state: "never", title: "Hedef değil (asla / donanım)" },
];

const STEP_WORD: Record<StepState, string> = { done: "bitti", partial: "yarım", open: "açık" };

const pct = (value: number | null | undefined) =>
  typeof value === "number" && Number.isFinite(value) ? `%${value}` : null;

export function buildProgress(progress: ProgressWithProof | null | undefined): ProgressModel | null {
  if (!progress) return null;
  const { jarvis, order, v1 } = progress;
  // An answer that predates the proof keeps its old line; one that has it (even null) reads the
  // JARVIS target as three numbers and calls the v1.0 matrix what it is now: the old list.
  const withProof = progress.proof !== undefined;
  const proof = progress.proof ?? null;
  const jarvisPct = pct(jarvis?.percent);
  const orderPct = pct(order?.percent);
  const done = pct(v1?.percent_done);
  const real = pct(v1?.percent_proven_real);
  const v1Name = withProof ? "eski v1.0 listesi" : "v1.0 listesi";
  const v1Text = done
    ? `${v1Name} ${done} yapıldı${real ? `, ${real} gerçekte kanıtlı` : ""}`
    : `${v1Name} okunamadı`;
  const proofText = proof
    ? `staging'de kanıtlı ${pct(proof.percent_staging) ?? "okunamadı"} · ` +
      `gerçekte kanıtlı ${pct(proof.percent_real) ?? "okunamadı"}` +
      (proof.outside ? ` · satır dışı: ${proof.outside.count}` : "")
    : "kanıt okunamadı";
  const jarvisText = withProof
    ? `JARVIS hedefi: yapıldı ${jarvisPct ?? "okunamadı"} · ${proofText}`
    : `JARVIS hedefi ${jarvisPct ?? "okunamadı"}`;
  const line = [jarvisText, `Sıralı plan ${orderPct ?? "okunamadı"}`, v1Text].join(" · ");
  const proofRows = proof?.rows ?? [];
  const proven = withProof
    ? [
        { key: "staging", title: "Staging'de kanıtlı", rows: proofRows.filter((r) => r.staging_proven) },
        { key: "real", title: "Gerçekte kanıtlı", rows: proofRows.filter((r) => r.real_proven) },
      ].map((g) => ({ ...g, rows: g.rows.map((r) => r.name) }))
    : [];
  const rows = jarvis?.rows ?? [];
  const groups = GROUPS.map((g) => ({
    ...g,
    rows: rows.filter((r) => r.state === g.state).map((r) => r.name),
  })).filter((g) => g.rows.length > 0);
  const step = order?.next;
  return {
    line,
    bars: [
      { key: "jarvis", label: "JARVIS hedefi", percent: jarvis?.percent ?? null },
      ...(withProof
        ? [
            { key: "staging", label: "JARVIS hedefi staging'de kanıtlı", percent: proof?.percent_staging ?? null },
            { key: "real", label: "JARVIS hedefi gerçekte kanıtlı", percent: proof?.percent_real ?? null },
          ]
        : []),
      { key: "order", label: "Sıralı plan", percent: order?.percent ?? null },
      { key: "v1", label: v1Name, percent: v1?.percent_done ?? null },
    ],
    groups,
    proven,
    proofRule: proof?.rule ?? null,
    next: step ? `${step.n}. ${step.title} (${STEP_WORD[step.state]})` : null,
    rule: progress.rule,
    asOf: progress.as_of ? progress.as_of.slice(0, 7) : null,
  };
}

const strip: CSSProperties = {
  padding: "0.5rem 0.9rem",
  color: "#fff",
  background: "#11183a",
  borderTop: "1px solid #3b4470",
};
const summary: CSSProperties = {
  display: "flex",
  flexWrap: "wrap",
  alignItems: "center",
  gap: "0.4rem 1rem",
  cursor: "pointer",
  fontWeight: 700,
  letterSpacing: "0.02em",
};
const bars: CSSProperties = { display: "flex", flexWrap: "wrap", gap: "0.4rem 1rem", width: "100%" };
const bar: CSSProperties = { display: "flex", flexDirection: "column", gap: "0.2rem", flex: "1 1 8rem", minWidth: 0 };
const track: CSSProperties = {
  display: "block",
  height: "0.35rem",
  overflow: "hidden",
  background: "rgba(255, 255, 255, 0.15)",
  borderRadius: "999px",
};
const fill: CSSProperties = { display: "block", height: "100%", background: "#38d9ff" };
const panel: CSSProperties = { display: "flex", flexWrap: "wrap", gap: "0.6rem 1.5rem", marginTop: "0.6rem" };
const group: CSSProperties = { flex: "1 1 12rem", minWidth: 0, overflowWrap: "anywhere" };
const muted: CSSProperties = { color: "#a9b0c0", fontWeight: 400 };

export default function OfficeProgress({ progress }: { progress: ProgressWithProof | null | undefined }) {
  const model = buildProgress(progress);
  if (!model) return null;
  return (
    <details className="office-progress" data-office="progress" style={strip}>
      <summary style={summary}>
        <span>İlerleme</span>
        <span style={{ fontWeight: 400, overflowWrap: "anywhere" }}>{model.line}</span>
        <span style={bars}>
          {model.bars.map((b) => (
            <span key={b.key} style={bar}>
              <span
                style={track}
                role="meter"
                aria-label={`${b.label}: tamamlanan`}
                aria-valuemin={0}
                aria-valuemax={100}
                {...(b.percent !== null ? { "aria-valuenow": b.percent } : {})}
              >
                <span style={{ ...fill, width: `${b.percent ?? 0}%` }} />
              </span>
            </span>
          ))}
        </span>
      </summary>
      <div style={panel}>
        {model.groups.map((g) => (
          <section key={g.state} style={group}>
            <h3 style={{ margin: "0 0 0.25rem", fontSize: "0.95rem" }}>
              {g.title} <span style={muted}>({g.rows.length})</span>
            </h3>
            <ul style={{ margin: 0, paddingLeft: "1.1rem" }}>
              {g.rows.map((name) => (
                <li key={name}>{name}</li>
              ))}
            </ul>
          </section>
        ))}
        {model.proven.map((g) => (
          <section key={g.key} style={group} data-office-proof={g.key}>
            <h3 style={{ margin: "0 0 0.25rem", fontSize: "0.95rem" }}>
              {g.title} <span style={muted}>({g.rows.length})</span>
            </h3>
            <ul style={{ margin: 0, paddingLeft: "1.1rem" }}>
              {g.rows.map((name) => (
                <li key={name}>{name}</li>
              ))}
            </ul>
          </section>
        ))}
        <p style={{ flexBasis: "100%", margin: 0 }}>
          {model.next ? `Sıradaki adım: ${model.next}` : "Sıralı planın açık adımı yok."}
        </p>
        <p style={{ ...muted, flexBasis: "100%", margin: 0, fontSize: "0.85rem" }}>
          {model.rule}
          {model.asOf ? ` Kaynak: yayın ${model.asOf}.` : ""}
          {model.proofRule ? ` ${model.proofRule}` : ""}
        </p>
      </div>
    </details>
  );
}
