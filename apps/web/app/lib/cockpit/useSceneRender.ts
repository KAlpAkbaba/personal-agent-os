"use client";

/**
 * The images under the 3B Sahne rows (M25 spec §6).
 *
 * The render route is behind the owner session, exactly as M13's render
 * download is, so a bare `<img src="…/v1/scenes/x/render">` would be
 * answered with a 401 and the owner would read a broken picture as "no
 * render". This hook does what `app/artifacts/page.tsx` does for a
 * download: fetches the bytes WITH the session and hands the page a blob
 * URL, revoked when the row goes or the panel unmounts.
 *
 * Two honesty rules, and they are the whole of it:
 *
 * 1. **A render is fetched because the ROW said one exists.** Nothing here
 *    guesses from a step, a name or a previous answer; `has_render` is the
 *    only trigger, and a row that loses it loses its image.
 * 2. **A render that could not be fetched is said, not hidden.** The notice
 *    carries the failure in words and the row's own line stays: "there is a
 *    render and this page could not fetch it" is a different statement from
 *    "there is no render", and the owner is told which.
 */

import { useEffect, useMemo, useRef, useState } from "react";

import { UnauthorizedError } from "../session";
import {
  SCENE_RENDER_REVOKE_MS,
  type ScenePreviewProps,
  type SceneRow,
  fetchSceneRenderBlob,
} from "./scenes";

/** The ports the fetcher needs, so a test can drive it without a network or a browser. */
export type SceneRenderPorts = {
  fetchBlob: (sceneId: string) => Promise<Blob>;
  toUrl: (blob: Blob) => string;
  revoke: (url: string) => void;
};

/** The real ports: the session-gated fetch and the browser's own object URLs. */
export const sceneRenderPorts: SceneRenderPorts = {
  fetchBlob: fetchSceneRenderBlob,
  toUrl: (blob) => URL.createObjectURL(blob),
  revoke: (url) => URL.revokeObjectURL(url),
};

/** The key a row's image is cached under: the scene and the render's identity, so a NEW render replaces the old one. */
export function sceneRenderKey(row: Pick<SceneRow, "scene_id" | "render_sha256" | "updated_at">): string {
  return `${row.scene_id}#${row.render_sha256 ?? row.updated_at ?? ""}`;
}

/** "İndirilemedi: HTTP 503" — the render exists and this page could not fetch it, said as that. */
export function sceneRenderNotice(sceneId: string, err: unknown): string {
  const text = err instanceof Error ? err.message : String(err);
  return `Render alınamadı (${sceneId}): ${text}`;
}

/**
 * Fetch the render of every row that says it has one, once per render
 * identity, and hand the panel a `srcFor`.
 *
 * `URL.createObjectURL` does not exist on the server, so nothing is
 * fetched during SSR: the effect is the only place that runs, and the panel
 * renders the row without an image until the bytes arrive — which is the
 * honest intermediate state anyway.
 */
export function useSceneRender(rows: SceneRow[], ports: SceneRenderPorts = sceneRenderPorts): ScenePreviewProps {
  const [urls, setUrls] = useState<Record<string, string>>({});
  const [notice, setNotice] = useState<string | null>(null);
  // Every URL this hook ever made, so each is revoked exactly once.
  const made = useRef<Record<string, string>>({});
  const asked = useRef<Set<string>>(new Set());

  const wanted = useMemo(
    () => rows.filter((row) => row.has_render).map((row) => ({ id: row.scene_id, key: sceneRenderKey(row) })),
    [rows],
  );

  useEffect(() => {
    let live = true;
    for (const { id, key } of wanted) {
      if (asked.current.has(key)) continue;
      asked.current.add(key);
      void ports
        .fetchBlob(id)
        .then((blob) => {
          if (!live) return;
          const url = ports.toUrl(blob);
          made.current[key] = url;
          setUrls((prev) => ({ ...prev, [id]: url }));
        })
        .catch((err: unknown) => {
          if (!live || err instanceof UnauthorizedError) return;
          setNotice(sceneRenderNotice(id, err));
        });
    }
    return () => {
      live = false;
    };
  }, [wanted, ports]);

  // One revoke pass, on unmount: an image the owner is still looking at must
  // not have its bytes pulled out from under it by a re-render.
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
      srcFor: (sceneId: string) => urls[sceneId] ?? null,
      notice,
    }),
    [urls, notice],
  );
}

/** How long a blob URL is kept when a caller wants a bounded one; re-exported so the figure is spelled once. */
export { SCENE_RENDER_REVOKE_MS };
