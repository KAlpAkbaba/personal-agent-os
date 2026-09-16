"use client";

/**
 * `/availability` — B24 req 700: what this system has, and what it is doing about it.
 *
 * The page answers two different questions and keeps them apart on purpose, because
 * requirement 78 already settled which one may be answered from which source:
 *
 * * **Şimdi.** Whether a family has anything in it right now is MEASURED — the same
 *   `useCockpitData` loop the cockpit's panels read, so this page and the panels can
 *   never disagree. This is also where req 714's hidden panels go: a family that draws
 *   no panel is listed here with the reason, so "where did the alarms panel go" has an
 *   address instead of being a mystery.
 * * **Kayıt.** What the FEATURE MATRIX records — status and proof class per requirement
 *   — generated from the document itself (`scripts/web/sync-feature-matrix.mjs`) so the
 *   badges cannot drift from it. Answering "is it working" from this half would be the
 *   mistake 78's note names: an answer read from a hand-maintained document is only as
 *   true as the document.
 *
 * The record is 750 rows, so it opens closed: per-section counts, `<details>` for the
 * rows, and a search that goes across sections. Nothing here is a control — this page
 * decides nothing and asks nothing of the Cloud Core.
 */

import Link from "next/link";
import { useMemo, useState } from "react";

import OwnerGate from "../components/OwnerGate";
import { ImplBadge, ProofBadge } from "../components/FeatureBadge";
import {
  type FamilyQuality,
  QUALITY_DETAIL,
  QUALITY_LABEL,
  familyQualities,
} from "../lib/cockpit/families";
import { useCockpitData } from "../lib/cockpit/useCockpitData";
import { IMPL_LABEL, IMPL_TONE } from "../lib/features/badges";
import {
  IMPL_CLASSES,
  type ImplClass,
  MATRIX_BASE,
  MATRIX_VERSION,
  type MatrixRow,
  ROWS,
  SECTIONS,
} from "../lib/features/matrix.generated";

/** The order the measured families are listed in: what needs looking at first. */
const QUALITY_ORDER: FamilyQuality[] = ["failed", "ready", "empty", "absent", "loading"];

/** How many matching rows a search shows before it asks for a narrower word. */
const SEARCH_LIMIT = 60;

function implCounts(rows: readonly MatrixRow[]): { impl: ImplClass; count: number }[] {
  return IMPL_CLASSES.map((impl) => ({
    impl,
    count: rows.filter((row) => row.impl === impl).length,
  })).filter((entry) => entry.count > 0);
}

function FeatureRow({ row }: { row: MatrixRow }) {
  return (
    <li className="feature-row" data-feature-id={row.id}>
      <span className="feature-id">{row.id}</span>
      <span className="feature-name">{row.feature}</span>
      <ImplBadge impl={row.impl} />
      <ProofBadge proof={row.proof} />
      {row.batch !== "—" && <span className="muted feature-batch">{row.batch}</span>}
      {row.owner && (
        <span className="feature-owner" data-feature-owner>
          sahip eylemi
        </span>
      )}
    </li>
  );
}

function Availability() {
  const { data } = useCockpitData();
  const [query, setQuery] = useState("");

  const measured = useMemo(() => familyQualities(data), [data]);
  const byQuality = useMemo(
    () =>
      QUALITY_ORDER.map((quality) => ({
        quality,
        entries: measured.filter((entry) => entry.quality === quality),
      })).filter((group) => group.entries.length > 0),
    [measured],
  );

  const needle = query.trim().toLocaleLowerCase("tr-TR");
  const matches = useMemo(() => {
    if (needle.length < 2) return null;
    return ROWS.filter(
      (row) =>
        row.feature.toLocaleLowerCase("tr-TR").includes(needle) || String(row.id) === needle,
    );
  }, [needle]);

  const totals = useMemo(() => implCounts(ROWS), []);

  return (
    <main data-page="availability">
      <h1>Özellik durumu</h1>
      <p className="subtitle">
        Solda ölçüm, sağda kayıt. Bir ailenin şu an dolu olup olmadığı ölçülür; ne durumda
        sayıldığı ve nasıl kanıtlandığı ise kayıttan okunur.
      </p>

      {/* ---------------------------------------------------------------- measured */}
      <section className="panel" data-availability="live">
        <h2>Şimdi — {measured.length} aile</h2>
        <p className="muted">
          Kokpit panellerinin okuduğu aynı veri. Boş ya da bu sürümde olmayan bir aile
          panel doğurmaz (B24 req 714); burada adıyla durur.
        </p>
        {byQuality.map((group) => (
          <div key={group.quality} className="family-group" data-family-quality={group.quality}>
            <h3>
              {QUALITY_LABEL[group.quality]}
              <span className="panel-count">{group.entries.length}</span>
            </h3>
            <p className="muted">{QUALITY_DETAIL[group.quality]}</p>
            <ul className="family-list">
              {group.entries.map(({ family, quality }) => (
                <li key={family.key} data-family={family.key} data-quality={quality}>
                  <Link href={quality === "ready" ? `${family.page}#${family.panel}` : family.page}>
                    {family.label}
                  </Link>
                  <span className="muted"> · {family.page}</span>
                </li>
              ))}
            </ul>
          </div>
        ))}
      </section>

      {/* ------------------------------------------------------------------ record */}
      <section className="panel" data-availability="record">
        <h2>Kayıt — {ROWS.length} gereksinim</h2>
        <p className="muted">
          FEATURE MATRIX {MATRIX_VERSION} · taban {MATRIX_BASE}. Rozetler belgenin kendi
          sınıflarıdır; bu dosya belgeden üretilir.
        </p>

        <ul className="impl-totals" data-impl-totals>
          {totals.map((entry) => (
            <li key={entry.impl} className={`tone-${IMPL_TONE[entry.impl]}`} data-impl={entry.impl}>
              <strong>{entry.count}</strong> {IMPL_LABEL[entry.impl]}
            </li>
          ))}
        </ul>

        <label className="feature-search">
          <span>Ara</span>
          <input
            type="search"
            value={query}
            placeholder="özellik adı ya da numara"
            onChange={(event) => setQuery(event.target.value)}
            data-feature-search
          />
        </label>

        {matches !== null ? (
          <div data-search-results={matches.length}>
            <p className="muted">
              {matches.length === 0
                ? "Eşleşen gereksinim yok."
                : `${matches.length} eşleşme${matches.length > SEARCH_LIMIT ? ` — ilk ${SEARCH_LIMIT} gösteriliyor` : ""}.`}
            </p>
            <ul className="feature-list">
              {matches.slice(0, SEARCH_LIMIT).map((row) => (
                <FeatureRow key={row.id} row={row} />
              ))}
            </ul>
          </div>
        ) : (
          SECTIONS.map((section) => {
            const rows = ROWS.filter((row) => row.section === section.key);
            const counts = implCounts(rows);
            return (
              <details key={section.key} className="feature-section" data-section={section.key}>
                <summary>
                  <span className="feature-id">{section.key}</span> {section.title}
                  <span className="muted">
                    {" "}
                    {section.from}–{section.to} ·{" "}
                    {counts.map((entry) => `${entry.count} ${IMPL_LABEL[entry.impl]}`).join(" · ")}
                  </span>
                </summary>
                <ul className="feature-list">
                  {rows.map((row) => (
                    <FeatureRow key={row.id} row={row} />
                  ))}
                </ul>
              </details>
            );
          })
        )}
      </section>
    </main>
  );
}

export default function AvailabilityPage() {
  return (
    <OwnerGate>
      <Availability />
    </OwnerGate>
  );
}
