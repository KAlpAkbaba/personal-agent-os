"use client";

/**
 * B11 req 372: "enable push for this browser" — the one control the owner needs, once
 * the server has a VAPID key. Everything else (encryption, VAPID signing, the ladder's
 * push rung) happens without any web UI at all.
 *
 * Four honest states (task brief), checked in this order because each rules out the
 * ones after it:
 *
 * 1. `unsupported` — this browser has no Push API at all (no button can fix that).
 * 2. `no_server_key` — the Cloud Core has no VAPID key configured yet (an owner action
 *    on the server, `scripts/cloud/new-vapid-key.ps1` — not something clicking here does).
 * 3. `denied` — the owner (or a past visit) told this browser no; only the browser's own
 *    site-permission UI can undo that, so this state names the fact rather than offering
 *    a button that would silently do nothing.
 * 4. `subscribed` / `not_subscribed` — the actionable pair: a button to turn it on, or a
 *    button to turn it off.
 *
 * Permission is requested ONLY inside the "enable" button's own click handler
 * (`webpush.ts`'s module docstring) — nothing here calls `Notification.requestPermission`
 * on mount.
 */

import { useCallback, useEffect, useState } from "react";

import {
  type NotificationPermissionState,
  currentBrowserSubscription,
  deleteSubscription,
  fetchVapidPublicKey,
  listSubscriptions,
  notificationPermission,
  postSubscription,
  pushSupport,
  registerAndSubscribe,
  unsubscribeBrowser,
} from "../lib/cockpit/webpush";

type Phase =
  | { kind: "checking" }
  | { kind: "unsupported" }
  | { kind: "no_server_key" }
  | { kind: "denied" }
  | { kind: "subscribed" }
  | { kind: "not_subscribed"; publicKey: string }
  | { kind: "working" }
  | { kind: "error"; message: string };

async function resolvePhase(): Promise<Phase> {
  if (pushSupport() === "unsupported") return { kind: "unsupported" };
  const permission: NotificationPermissionState = notificationPermission();
  const [keyState, existing] = await Promise.all([fetchVapidPublicKey(), currentBrowserSubscription()]);
  if (!keyState.supported) return { kind: "no_server_key" };
  if (existing) return { kind: "subscribed" };
  if (permission === "denied") return { kind: "denied" };
  return { kind: "not_subscribed", publicKey: keyState.publicKey };
}

const PHASE_LABEL: Record<string, string> = {
  unsupported: "Bu tarayıcı push bildirimlerini desteklemiyor.",
  no_server_key: "Sunucuda henüz push anahtarı yapılandırılmamış (sahip eylemi).",
  denied: "Bu tarayıcıda bildirim izni reddedilmiş; geri açmak tarayıcının site izinleri menüsünden yapılır.",
  subscribed: "Bu tarayıcı için push bildirimleri açık.",
};

export function WebPushSettings() {
  const [phase, setPhase] = useState<Phase>({ kind: "checking" });

  const refresh = useCallback(() => {
    setPhase({ kind: "checking" });
    resolvePhase().then(setPhase, (error: unknown) =>
      setPhase({ kind: "error", message: error instanceof Error ? error.message : String(error) }),
    );
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const enable = useCallback(async () => {
    if (phase.kind !== "not_subscribed") return;
    setPhase({ kind: "working" });
    try {
      const subscription = await registerAndSubscribe(phase.publicKey);
      await postSubscription(subscription.toJSON(), navigator.userAgent);
      refresh();
    } catch (error) {
      const message = error instanceof Error && error.message === "permission_denied"
        ? "İzin verilmedi."
        : error instanceof Error
          ? error.message
          : String(error);
      setPhase({ kind: "error", message });
    }
  }, [phase, refresh]);

  const disable = useCallback(async () => {
    setPhase({ kind: "working" });
    try {
      await unsubscribeBrowser();
      // Best-effort server-side cleanup: this browser's own row, matched by asking the
      // server what it has and removing the one this browser can no longer reach for -
      // `unsubscribeBrowser` above already invalidated the endpoint locally, so even if
      // this fails the row will self-expire on the next push attempt (410 -> deleted,
      // app.webpush.service.send_to_all). The client is never handed the full endpoint
      // back (privacy: it behaves like a bearer credential), so it cannot match more
      // precisely than "remove everything this browser knows about."
      const rows = await listSubscriptions();
      await Promise.all(rows.map((row) => deleteSubscription(row.id).catch(() => undefined)));
      refresh();
    } catch (error) {
      setPhase({ kind: "error", message: error instanceof Error ? error.message : String(error) });
    }
  }, [refresh]);

  return (
    <section className="panel" data-panel="webpush-settings">
      <h3 className="panel-title">
        <span>Push bildirimleri</span>
        <span className="panel-count">yalnızca bu tarayıcı</span>
      </h3>
      <p className="muted">
        Tarayıcı kapalıyken bile bildirim almanın son basamağı (B11) — merdivenin sırası: toast
        → ses → push → gelen kutusu. Push, bildirim servisinin kabul ettiğini söyler; sahibin
        gördüğünü değil.
      </p>
      {phase.kind === "checking" && <p className="muted">Kontrol ediliyor…</p>}
      {phase.kind === "working" && <p className="muted">İşleniyor…</p>}
      {phase.kind === "error" && <p className="muted" data-webpush-error>{phase.message}</p>}
      {(phase.kind === "unsupported" || phase.kind === "no_server_key" || phase.kind === "denied" || phase.kind === "subscribed") && (
        <p className="muted" data-webpush-state={phase.kind}>
          {PHASE_LABEL[phase.kind]}
        </p>
      )}
      {phase.kind === "subscribed" && (
        <button type="button" className="core-chip" onClick={() => void disable()} data-webpush-disable>
          Kapat
        </button>
      )}
      {phase.kind === "not_subscribed" && (
        <button type="button" className="core-chip" onClick={() => void enable()} data-webpush-enable>
          Bildirimlere izin ver ve aç
        </button>
      )}
    </section>
  );
}
