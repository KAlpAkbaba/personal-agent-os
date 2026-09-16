"use client";

/**
 * `/alarms` — B24 req 694.
 *
 * The alarm family is the one the owner meets first every morning and the one the product
 * showed least: a cockpit panel listing what is scheduled NEXT, and nothing else. The two
 * questions an owner actually asks about an alarm are "did it ring?" and "why didn't it?",
 * and neither is answerable from the scheduled list — a recurring alarm reuses its row and
 * rewinds its terminal state when it re-schedules for tomorrow, so by construction the row
 * can only ever describe the next occurrence.
 *
 * B13 req 285 put every occurrence in the Activity Ledger for exactly this reason, and
 * `/v1/alarms/history` has served them since. Nothing in the product had ever asked.
 */

import { useCallback } from "react";

import FamilyPage, { Row, Rows } from "../components/FamilyPage";
import { fetchAlarms } from "../lib/cockpit/api";
import { fetchAlarmHistory, fetchPolicy } from "../lib/pages/detail";
import { useLoaded, useNow } from "../lib/pages/useLoaded";
import { AlarmsPanel } from "../core/panels/CockpitPanels";

const ALARM_POLICY = "/v1/alarms/policy";

/** What each ledger event means, in the words the owner would use. */
const EVENT_LABEL: Record<string, string> = {
  alarm_scheduled: "kuruldu",
  alarm_fired: "çaldı",
  alarm_snoozed: "ertelendi",
  alarm_stopped: "durduruldu",
  alarm_cancelled: "iptal edildi",
  alarm_missed: "kaçırıldı",
  alarm_failed: "çalamadı",
};

export default function AlarmsPage() {
  const now = useNow();
  const alarms = useLoaded(useCallback(() => fetchAlarms(), []));
  const history = useLoaded(useCallback(() => fetchAlarmHistory(), []));
  const policy = useLoaded(useCallback(() => fetchPolicy(ALARM_POLICY), []));

  return (
    <FamilyPage
      id="alarms"
      title="Alarmlar"
      lead="Kurulu olanlar, gerçekten ne olduğu ve bu sürümün alarm kuralları."
      panel="alarms"
    >
      <AlarmsPanel state={alarms.state} now={now} always />

      <Rows
        id="alarm-history"
        title="Geçmiş"
        state={history.state}
        empty="Kayıtlı alarm olayı yok."
        onRetry={history.refresh}
      >
        {(event) => (
          <Row
            key={event.event_id}
            keyText={event.event_id}
            tone={event.event_type === "alarm_missed" || event.event_type === "alarm_failed" ? "bad" : undefined}
            head={
              <>
                {EVENT_LABEL[event.event_type ?? ""] ?? event.event_type ?? "olay"}
                {event.is_test && <span className="muted"> (deneme)</span>}
              </>
            }
            facts={[event.occurred_at, event.summary, event.reason]}
          />
        )}
      </Rows>

      <Rows
        id="alarm-policy"
        title="Kurallar"
        state={policy.state}
        empty="Bu Cloud Core alarm kurallarını bildirmiyor."
        onRetry={policy.refresh}
      >
        {(entry) => (
          <Row key={entry.name} keyText={entry.name} head={entry.name} facts={[entry.value]} />
        )}
      </Rows>
    </FamilyPage>
  );
}
