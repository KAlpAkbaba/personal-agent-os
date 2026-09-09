"use client";

/**
 * The before/after pictures under the Yaratıcı rows (M27 spec §6).
 *
 * The image route is behind the owner session, exactly as M13's download and
 * M25's render are, so a bare `<img src="…/v1/creative/runs/x/image/after">`
 * would be answered with a 401 and the owner would read two broken pictures
 * as "nothing was made". M25 shipped that bug once and had to learn it; this
 * family has two images per row, so the same mistake would be twice as loud.
 * This hook does what `useSceneRender` does: fetches the bytes WITH the
 * session and hands the page a blob URL, revoked when the panel unmounts.
 *
 * Two honesty rules, and they are the whole of it:
 *
 * 1. **A picture is fetched because the ROW said one exists.** Nothing here
 *    guesses from a step, a name or a previous answer; `has_before` /
 *    `has_after` are the only triggers, and a row that loses one loses that
 *    image.
 * 2. **A picture that could not be fetched is said, not hidden.** The notice
 *    carries the failure in words and the row's own line stays: "there is an
 *    output and this page could not fetch it" is a different statement from
 *    "there is no output", and the owner is told which.
 */

import { useEffect, useMemo, useRef, useState } from "react";

import { UnauthorizedError } from "../session";
import {
  type CreativeImageSide,
  type CreativePreviewProps,
  type CreativeRunRow,
  fetchCreativeImageBlob,
} from "./creative";
import { creativeRowSides } from "./creative-rows";

/** The ports the fetcher needs, so a test can drive it without a network or a browser. */
export type CreativeImagePorts = {
  fetchBlob: (runId: string, side: CreativeImageSide) => Promise<Blob>;
  toUrl: (blob: Blob) => string;
  revoke: (url: string) => void;
};

/** The real ports: the session-gated fetch and the browser's own object URLs. */
export const creativeImagePorts: CreativeImagePorts = {
  fetchBlob: fetchCreativeImageBlob,
  toUrl: (blob) => URL.createObjectURL(blob),
  revoke: (url) => URL.revokeObjectURL(url),
};

/** The key one picture is cached under: the run, the side and that side's identity, so NEW bytes replace the old ones. */
export function creativeImageKey(
  row: Pick<CreativeRunRow, "run_id" | "before_sha256" | "after_sha256" | "updated_at">,
  side: CreativeImageSide,
): string {
  const sha = side === "before" ? row.before_sha256 : row.after_sha256;
  return `${row.run_id}#${side}#${sha ?? row.updated_at ?? ""}`;
}

/** The key the panel looks an image up by. Kept in one place so the writer and the reader cannot drift. */
export function creativeSrcKey(runId: string, side: CreativeImageSide): string {
  return `${runId}#${side}`;
}

/** "Görsel alınamadı (r1 · sonra): HTTP 503" — the picture exists and this page could not fetch it, said as that. */
export function creativeImageNotice(runId: string, side: CreativeImageSide, err: unknown): string {
  const text = err instanceof Error ? err.message : String(err);
  const word = side === "before" ? "önce" : "sonra";
  return `Görsel alınamadı (${runId} · ${word}): ${text}`;
}

/**
 * Fetch every picture the rows say exists, once per image identity, and hand
 * the panel a `srcFor`.
 *
 * `URL.createObjectURL` does not exist on the server, so nothing is fetched
 * during SSR: the effect is the only place that runs, and the panel renders
 * the row without its images until the bytes arrive — which is the honest
 * intermediate state anyway.
 */
export function useCreativeImages(
  rows: CreativeRunRow[],
  ports: CreativeImagePorts = creativeImagePorts,
): CreativePreviewProps {
  const [urls, setUrls] = useState<Record<string, string>>({});
  const [notice, setNotice] = useState<string | null>(null);
  // Every URL this hook ever made, so each is revoked exactly once.
  const made = useRef<Record<string, string>>({});
  const asked = useRef<Set<string>>(new Set());

  const wanted = useMemo(
    () =>
      rows.flatMap((row) =>
        creativeRowSides(row).map((side) => ({
          id: row.run_id,
          side,
          key: creativeImageKey(row, side),
        })),
      ),
    [rows],
  );

  useEffect(() => {
    let live = true;
    for (const { id, side, key } of wanted) {
      if (asked.current.has(key)) continue;
      asked.current.add(key);
      void ports
        .fetchBlob(id, side)
        .then((blob) => {
          if (!live) return;
          const url = ports.toUrl(blob);
          made.current[key] = url;
          setUrls((prev) => ({ ...prev, [creativeSrcKey(id, side)]: url }));
        })
        .catch((err: unknown) => {
          if (!live || err instanceof UnauthorizedError) return;
          setNotice(creativeImageNotice(id, side, err));
        });
    }
    return () => {
      live = false;
    };
  }, [wanted, ports]);

  // One revoke pass, on unmount: a picture the owner is still looking at
  // must not have its bytes pulled out from under it by a re-render.
  const revoke = useRef(ports.revoke);
  useEffect(() => {
    revoke.current = ports.revoke;
  }, [ports]);
  useEffect(() => {
    const urlsMade = made.current;
    return () => {
      for (const url of Object.values(urlsMade)) revoke.current(url);
    };
  }, []);

  return useMemo(
    () => ({
      srcFor: (runId: string, side: CreativeImageSide) => urls[creativeSrcKey(runId, side)] ?? null,
      notice,
    }),
    [urls, notice],
  );
}
