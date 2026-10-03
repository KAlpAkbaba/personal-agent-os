import type { MisheardItem, MisheardList } from "../../app/core/misheard/misheardApi";

export function item(over: Partial<MisheardItem> = {}): MisheardItem {
  return {
    id: "11111111-1111-4111-8111-111111111111",
    heard_at: "2026-10-03T07:05:00+00:00",
    sentence: "yarın sabah şeyi hatırlat bana",
    mode: "paid",
    engine: "gpt-4o-transcribe",
    device_id: "22222222-2222-4222-8222-222222222222",
    band: "medium",
    confidence: 0.61,
    reason: "no_intent",
    resolved_intent: null,
    tool: null,
    session_id: "33333333-3333-4333-8333-333333333333",
    meant: null,
    answered_at: null,
    expires_at: "2026-11-02T07:05:00+00:00",
    ...over,
  };
}

/** Three items, the second answered: two are open. */
export function three(retention_days = 30): MisheardList {
  return {
    items: [
      item({ id: "aaaaaaaa-0000-4000-8000-000000000001", sentence: "ışığı biraz kıs" }),
      item({
        id: "aaaaaaaa-0000-4000-8000-000000000002",
        sentence: "annemi ara dedim",
        reason: "objected",
        meant: "Annemi değil, ablamı ara.",
        answered_at: "2026-10-03T08:00:00+00:00",
      }),
      item({
        id: "aaaaaaaa-0000-4000-8000-000000000003",
        sentence: "postayı gönder",
        mode: "local",
        engine: null,
        device_id: null,
        band: null,
        confidence: null,
        reason: "tool_failed",
        tool: "mail.send",
      }),
    ],
    open: 2,
    retention_days,
  };
}
