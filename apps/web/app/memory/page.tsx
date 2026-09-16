"use client";

/**
 * `/memory` — B24 req 689 ("57-60 ile aynı sayfa").
 *
 * The cockpit's Hafıza panel lists the AUDIT — what was written, corrected, contradicted
 * or forgotten. It has never shown a single memory. The owner of a system that remembers
 * things about them should be able to read what it remembers, and until this page the only
 * way was to ask by voice one item at a time.
 *
 * The list is the retrieval path itself: an empty box is the ordinary listing and a typed
 * word runs `hybrid_search` — the SAME ranking the assistant uses when it answers. So what
 * the owner sees here is what the system would actually have retrieved, not a separate
 * browse view that could disagree with it.
 *
 * Read-only, deliberately. Forgetting is a hard delete (req 36) and correcting rewrites
 * what the assistant believes; both belong to the voice path that gates them, and a button
 * on a list is exactly how one gets pressed by accident.
 */

import { useCallback, useState } from "react";

import FamilyPage, { Row, Rows } from "../components/FamilyPage";
import { fetchMemoryAudit } from "../lib/cockpit/api";
import { fetchEntities, fetchMemories } from "../lib/pages/detail";
import type { MemoryRow } from "../lib/pages/detail";
import {
  type MemoryControlProps,
  embeddingSentence,
  fetchEmbeddingStatus,
  memoryClient,
  reindexMemory,
  useMemoryControl,
} from "../lib/pages/memory";
import { useLoaded, useNow } from "../lib/pages/useLoaded";
import { MemoryPanel } from "../core/panels/CockpitPanels";

/** How each stored class reads to the person it is about. */
const CLASS_LABEL: Record<string, string> = {
  PREFERENCE: "tercih",
  FACT: "olgu",
  PROJECT: "proje",
  EPISODE: "olay",
  PROCEDURE: "yordam",
  IDENTITY: "kimlik",
};


/** B37 (req 58-60): the three things the owner may do to one memory, on its row. */
function MemoryRowControls({ row, control }: { row: MemoryRow; control: MemoryControlProps }) {
  const [confirmForget, setConfirmForget] = useState(false);
  const [correction, setCorrection] = useState<string | null>(null);
  const inFlight = control.busy !== null;
  const mine = control.busy?.id === row.memory_id;
  return (
    <div className="approval-pair" data-memory-controls={row.memory_id} data-memory-in-flight={mine ? "yes" : "no"}>
      <button
        type="button"
        className="core-chip"
        data-memory-action={row.pinned ? "unpin" : "pin"}
        data-memory-target={row.memory_id}
        disabled={inFlight}
        onClick={() => control.run(row.pinned ? "unpin" : "pin", row.memory_id)}
      >
        {row.pinned ? "Sabitlemeyi kaldır" : "Sabitle"}
      </button>
      {confirmForget ? (
        <>
          <button
            type="button"
            className="core-chip"
            data-memory-action="forget"
            data-memory-target={row.memory_id}
            disabled={inFlight}
            onClick={() => {
              setConfirmForget(false);
              control.run("forget", row.memory_id);
            }}
          >
            Evet, unut (geri alınamaz)
          </button>
          <button type="button" className="core-chip" data-memory-action="forget-cancel" onClick={() => setConfirmForget(false)}>
            Vazgeç
          </button>
        </>
      ) : (
        <button
          type="button"
          className="core-chip"
          data-memory-action="forget-ask"
          data-memory-target={row.memory_id}
          disabled={inFlight}
          onClick={() => setConfirmForget(true)}
        >
          Unut
        </button>
      )}
      {correction === null ? (
        <button
          type="button"
          className="core-chip"
          data-memory-action="correct-ask"
          data-memory-target={row.memory_id}
          disabled={inFlight}
          onClick={() => setCorrection(row.text ?? "")}
        >
          Düzelt
        </button>
      ) : (
        <form
          className="memory-correct"
          data-memory-correct={row.memory_id}
          onSubmit={(event) => {
            event.preventDefault();
            control.run("correct", row.memory_id, correction);
            setCorrection(null);
          }}
        >
          <input type="text" value={correction} onChange={(event) => setCorrection(event.target.value)} data-memory-correct-text />
          <button type="submit" className="core-chip" data-memory-action="correct" data-memory-target={row.memory_id} disabled={inFlight}>
            Kaydet
          </button>
          <button type="button" className="core-chip" onClick={() => setCorrection(null)}>
            Vazgeç
          </button>
        </form>
      )}
    </div>
  );
}

export default function MemoryPage() {
  const now = useNow();
  const [query, setQuery] = useState("");
  // The committed query, not the box: a request per keystroke would run the ranking
  // repeatedly on the server for words nobody finished typing.
  const [asked, setAsked] = useState("");

  const memories = useLoaded(useCallback(() => fetchMemories(asked), [asked]));
  const entities = useLoaded(useCallback(() => fetchEntities(), []));
  const audit = useLoaded(useCallback(() => fetchMemoryAudit(), []));
  // B37 (req 57-60): the controls reload the list and the audit after every answer.
  const refreshAll = useCallback(() => {
    memories.refresh();
    audit.refresh();
  }, [memories, audit]);
  const control = useMemoryControl(memoryClient, refreshAll);
  // B37 (req 51-54): which embedder serves retrieval, and how much it covers.
  const embedding = useLoaded(useCallback(() => fetchEmbeddingStatus(), []));
  const [reindexed, setReindexed] = useState<string | null>(null);

  return (
    <FamilyPage
      id="memory"
      title="Hafıza"
      lead="Sistemin sizin hakkınızda tuttukları, aralarındaki varlıklar ve bu kayda ne yapıldığı."
      panel="memory"
    >
      <form
        className="panel memory-search"
        onSubmit={(event) => {
          event.preventDefault();
          setAsked(query);
        }}
      >
        <label>
          <span>Ara</span>
          <input
            type="search"
            value={query}
            placeholder="boş bırakırsan hepsi"
            onChange={(event) => setQuery(event.target.value)}
            data-memory-search
          />
        </label>
        <button type="submit">Ara</button>
        {asked !== "" && (
          <button
            type="button"
            onClick={() => {
              setQuery("");
              setAsked("");
            }}
          >
            Temizle
          </button>
        )}
      </form>

      <Rows
        id="memories"
        title={asked ? `“${asked}” için hatırlananlar` : "Hatırlananlar"}
        state={memories.state}
        empty={asked ? "Bu sözcük için hatırlanan bir şey yok." : "Henüz hiçbir şey hatırlanmıyor."}
        onRetry={memories.refresh}
      >
        {(row) => (
          <li key={row.memory_id} className="detail-row" data-row={row.memory_id} data-memory-pinned={row.pinned ? "yes" : "no"}>
            <span className="detail-head">{row.text ?? row.key ?? row.memory_id}</span>
            <span className="muted detail-facts">
              {[
                CLASS_LABEL[row.memory_class ?? ""] ?? row.memory_class,
                row.explicit ? "siz söylediniz" : "konuşmadan çıkarıldı",
                row.pinned ? "sabitli" : null,
                row.stage,
                row.occurred_at,
              ]
                .filter((fact): fact is string => fact !== null && fact !== "")
                .join(" · ")}
            </span>
            <MemoryRowControls row={row} control={control} />
          </li>
        )}
      </Rows>
      {control.outcome && (
        <p
          className={`approval-outcome ${control.outcome.ok ? "muted" : "panel-unknown"}`}
          data-memory-outcome={control.outcome.action}
          data-memory-ok={control.outcome.ok ? "yes" : "no"}
          data-memory-target={control.outcome.id}
        >
          {control.outcome.text}
        </p>
      )}

      <section className="panel" id="memory-embedding" data-panel="memory-embedding" data-panel-state={embedding.state.kind}>
        <h3 className="panel-title">
          <span>Anlamsal indeks</span>
        </h3>
        {embedding.state.kind === "ok" ? (
          <p className="muted" data-memory-embedding={embedding.state.value.active ?? ""} data-memory-semantic={embedding.state.value.semantic ? "yes" : "no"}>
            {embeddingSentence(embedding.state.value)}
          </p>
        ) : (
          <p className="panel-empty">{embedding.state.kind === "loading" ? "Yükleniyor." : "Gömme durumu okunamadı."}</p>
        )}
        <div className="approval-pair">
          <button
            type="button"
            className="core-chip"
            data-memory-reindex="missing"
            onClick={() => {
              void reindexMemory(true)
                .then((rows) => {
                  setReindexed(`${rows} hatıra bu model için indekslendi.`);
                  embedding.refresh();
                })
                .catch((err: unknown) => setReindexed(err instanceof Error ? err.message : String(err)));
            }}
          >
            Eksikleri indeksle
          </button>
          <button
            type="button"
            className="core-chip"
            data-memory-reindex="all"
            onClick={() => {
              void reindexMemory(false)
                .then((rows) => {
                  setReindexed(`${rows} hatıra yeniden indekslendi.`);
                  embedding.refresh();
                })
                .catch((err: unknown) => setReindexed(err instanceof Error ? err.message : String(err)));
            }}
          >
            Hepsini yeniden indeksle
          </button>
        </div>
        {reindexed && (
          <p className="muted" data-memory-reindexed>
            {reindexed}
          </p>
        )}
      </section>

      <Rows
        id="memory-entities"
        title="Varlıklar"
        state={entities.state}
        empty="Kayıtlı varlık yok."
        onRetry={entities.refresh}
      >
        {(row) => (
          <Row
            key={row.entity_id}
            keyText={row.entity_id}
            head={row.name ?? row.entity_id}
            facts={[row.kind]}
          />
        )}
      </Rows>

      <MemoryPanel state={audit.state} now={now} always />
    </FamilyPage>
  );
}
