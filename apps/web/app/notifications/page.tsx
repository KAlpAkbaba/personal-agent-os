"use client";

/**
 * `/notifications` — B24 req 698 ("368 ile aynı sayfa").
 *
 * B11 made the inbox durable and gave the cockpit a panel for it. What it also built, and
 * what nothing in the product ever called, is `fetchNotificationHistory` — req 377's
 * delivery record, including the rows **no channel carried**. That last part is the reason
 * this page exists: "we never reached you about this" is the half of a delivery history
 * that matters, and it was fetchable, parsed and unreachable.
 *
 * The inbox says what is waiting. The history says what happened to everything the system
 * ever tried to tell the owner — which ladder rung carried it, or that the ladder ran out.
 */

import { useCallback } from "react";

import FamilyPage, { Row, Rows } from "../components/FamilyPage";
import { fetchInbox, fetchNotificationHistory } from "../lib/cockpit/notifications";
import { useNotificationRead } from "../lib/cockpit/useNotificationRead";
import { useLoaded } from "../lib/pages/useLoaded";
import { NotificationsPanel } from "../core/panels/CockpitPanels";

export default function NotificationsPage() {
  const inbox = useLoaded(useCallback(() => fetchInbox(), []));
  const history = useLoaded(useCallback(() => fetchNotificationHistory(), []));
  const read = useNotificationRead(inbox.refresh);

  return (
    <FamilyPage
      id="notifications"
      title="Bildirimler"
      lead="Bekleyenler, ve sistemin size ulaşmak için ne denediği — hangi kanalın taşıdığı ya da hiçbirinin taşımadığı."
      panel="notifications"
    >
      <NotificationsPanel state={inbox.state} onMarkRead={read.markRead} busyId={read.busyId} always />

      <Rows
        id="notification-history"
        title="Ulaştırma kaydı"
        state={history.state}
        empty="Kayıtlı ulaştırma denemesi yok."
        badge={(rows) => `${rows.filter((row) => row.delivered_via === null).length} taşınmadı / ${rows.length}`}
        onRetry={history.refresh}
      >
        {(row) => (
          <Row
            key={row.id}
            keyText={row.id}
            // A row nothing carried is the one worth finding, so it is the one drawn as
            // wrong — not the one quietly listed with the rest.
            tone={row.delivered_via === null ? "bad" : undefined}
            head={row.title ?? row.kind ?? "bildirim"}
            facts={[
              row.created_at,
              row.priority,
              row.delivered_via ? `taşıyan: ${row.delivered_via}` : "hiçbir kanal taşımadı",
              row.read_at ? "okundu" : null,
            ]}
          />
        )}
      </Rows>
    </FamilyPage>
  );
}
