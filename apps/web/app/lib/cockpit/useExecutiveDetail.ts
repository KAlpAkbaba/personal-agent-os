"use client";

/**
 * The sentence under a Görevler row (M26 spec §3, §6).
 *
 * The list route says which runs exist and how they ended; the run's own
 * route (`GET /v1/executive/runs/{id}`) answers what the workflow's
 * `explain` query answers — "the current step in one sentence: what it is
 * doing and what it waits for" — and, for a run that ended `partial`, which
 * steps did not verify. That sentence changes with every step, so it is
 * fetched per run rather than carried on a list the cockpit polls every
 * fifteen seconds.
 *
 * Three rules, and they are the whole of it:
 *
 * 1. **Only unfinished runs are asked about.** The spec bounds a system to
 *    two active runs at once (§4), so this is at most two extra GETs per
 *    poll — and an ended run's "current step" is not a question with an
 *    answer.
 * 2. **A run is re-asked when the LIST says it moved.** The key carries the
 *    row's step and state, so a run standing on `s3` is asked once and a
 *    run that reached `s4` is asked again. Nothing polls behind the list's
 *    back.
 * 3. **What the route did not say is not said.** A run with no detail, or
 *    one whose fetch failed, simply has no sentence under it — the failure
 *    is stated in the notice instead. This page never writes the
 *    explanation it would have expected.
 */

import { useEffect, useMemo, useRef, useState } from "react";

import { UnauthorizedError } from "../session";
import { isOk } from "./api";
import {
  type ExecutiveDetailProps,
  type ExecutiveRunDetail,
  type ExecutiveRunRow,
  fetchExecutiveRun,
} from "./executive";
import { rowIsActive } from "./executive-rows";

/** The ports the fetcher needs, so a test can drive it without a network. */
export type ExecutiveDetailPorts = {
  fetchDetail: (runId: string) => Promise<ExecutiveRunDetail | null>;
};

/**
 * The real port: the run's own route through the owner session. A route
 * that is not on this Cloud Core yet, or a body with no run in it, answers
 * `null` — which the panel renders as no sentence, never as an empty one.
 */
export const executiveDetailPorts: ExecutiveDetailPorts = {
  fetchDetail: async (runId) => {
    const loaded = await fetchExecutiveRun(runId);
    return isOk(loaded) ? loaded.value : null;
  },
};

/** The key one run's detail is cached under: the run, the step it is on and its state. */
export function executiveDetailKey(row: Pick<ExecutiveRunRow, "run_id" | "step" | "state">): string {
  return `${row.run_id}#${row.step ?? ""}#${row.state ?? ""}`;
}

/** "Ayrıntı alınamadı (r1): HTTP 503" — the run exists and this page could not ask about it, said as that. */
export function executiveDetailNotice(runId: string, err: unknown): string {
  const text = err instanceof Error ? err.message : String(err);
  return `Ayrıntı alınamadı (${runId}): ${text}`;
}

/**
 * Fetch the detail of every unfinished run, once per (run, step, state),
 * and hand the panel a `detailFor`.
 */
export function useExecutiveDetail(
  rows: ExecutiveRunRow[],
  ports: ExecutiveDetailPorts = executiveDetailPorts,
): ExecutiveDetailProps {
  const [details, setDetails] = useState<Record<string, ExecutiveRunDetail>>({});
  const [notice, setNotice] = useState<string | null>(null);
  const asked = useRef<Set<string>>(new Set());

  const wanted = useMemo(
    () => rows.filter(rowIsActive).map((row) => ({ id: row.run_id, key: executiveDetailKey(row) })),
    [rows],
  );

  useEffect(() => {
    let live = true;
    for (const { id, key } of wanted) {
      if (asked.current.has(key)) continue;
      asked.current.add(key);
      void ports
        .fetchDetail(id)
        .then((detail) => {
          if (!live || detail === null) return;
          setDetails((prev) => ({ ...prev, [id]: detail }));
        })
        .catch((err: unknown) => {
          if (!live || err instanceof UnauthorizedError) return;
          setNotice(executiveDetailNotice(id, err));
        });
    }
    return () => {
      live = false;
    };
  }, [wanted, ports]);

  return useMemo(
    () => ({
      detailFor: (runId: string) => details[runId] ?? null,
      notice,
    }),
    [details, notice],
  );
}
