/**
 * B11 req 368/377/378: the Bildirimler panel, and the client under it.
 *
 * What is actually being tested is one distinction: **delivered is not read, and neither
 * is "nothing carried it"**. Before B11 the inbox read the fake push transport's memory,
 * so it was empty in production, gone on every restart, and said nothing at all about
 * whether the owner had been reached. A panel that collapses those three into "there are
 * notifications" would be the same lie in a nicer font.
 *
 * Rendered with `react-dom/server` like the rest of this suite; the panel is hook-free, so
 * the click is proven by walking the element tree to the button and invoking its handler.
 */

import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";

const apiFetch = vi.fn();

vi.mock("../../app/lib/session", () => ({
  apiFetch: (...args: unknown[]) => apiFetch(...args),
  UnauthorizedError: class UnauthorizedError extends Error {},
}));

import { NotificationsPanel } from "../../app/core/panels/CockpitPanels";
import {
  DELIVERY_TR,
  NOTIFICATION_ROWS_SHOWN,
  canMarkRead,
  channelLabel,
  deliveryLine,
  deliveryState,
  formatWhen,
  inboxBadge,
  needsAttention,
  priorityLabel,
} from "../../app/lib/cockpit/notification-rows";
import {
  NOTIFICATION_READ_IDLE,
  type NotificationReadState,
  runNotificationRead,
} from "../../app/lib/cockpit/useNotificationRead";
import {
  NOTIFICATIONS_HISTORY_PATH,
  NOTIFICATIONS_PATH,
  type NotificationClient,
  type NotificationRow,
  fetchInbox,
  fetchNotificationHistory,
  markNotificationRead,
  notificationReadPath,
  parseInbox,
  parseNotification,
} from "../../app/lib/cockpit/notifications";

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

beforeEach(() => {
  apiFetch.mockReset();
});

const ROW: NotificationRow = {
  id: "n1",
  kind: "backup.failed",
  title: "Yedek alınamadı",
  body: "Yedekleme başarısız oldu efendim (pagentos-backup.service).",
  priority: "urgent",
  group_key: "backup:pagentos-backup.service",
  created_at: "2026-09-13T02:00:00Z",
  delivered_at: null,
  delivered_via: null,
  read_at: null,
};

function row(patch: Partial<NotificationRow> = {}): NotificationRow {
  return { ...ROW, ...patch };
}

// -------------------------------------------------------------- the three states

describe("delivered, read and unreached are three different facts", () => {
  it("says nothing carried it when nothing did", () => {
    expect(deliveryState(row())).toBe("unreached");
    expect(deliveryLine(row())).toBe(DELIVERY_TR.unreached);
  });

  it("says delivered and not read when a channel carried it and nobody looked", () => {
    const delivered = row({ delivered_at: "2026-09-13T02:00:05Z", delivered_via: "toast" });

    expect(deliveryState(delivered)).toBe("delivered");
    expect(deliveryLine(delivered)).toBe("iletildi, okunmadı · masaüstü bildirimi");
  });

  it("says read only when the owner read it", () => {
    const read = row({ delivered_at: "2026-09-13T02:00:05Z", delivered_via: "toast", read_at: "2026-09-13T08:00:00Z" });

    expect(deliveryState(read)).toBe("read");
    expect(deliveryLine(read)).toContain(DELIVERY_TR.read);
  });

  it("names an unknown channel verbatim rather than dropping it", () => {
    expect(channelLabel("carrier_pigeon")).toBe("carrier_pigeon");
    expect(channelLabel(null)).toBeNull();
  });

  it("shows an unknown priority verbatim and treats a missing one as normal", () => {
    expect(priorityLabel("catastrophic")).toBe("catastrophic");
    expect(priorityLabel(null)).toBe("normal");
    expect(priorityLabel("urgent")).toBe("acil");
  });
});

// ------------------------------------------------------------------ the attention rule

describe("what the panel draws a border around", () => {
  it("is an urgent row nothing carried", () => {
    expect(needsAttention([row()])).toBe(true);
  });

  it("is not an urgent row that already interrupted the owner", () => {
    expect(needsAttention([row({ delivered_at: "2026-09-13T02:00:05Z", delivered_via: "toast" })])).toBe(false);
  });

  it("is not a normal row nothing carried - a panel that shouts about everything is ignored", () => {
    expect(needsAttention([row({ priority: "normal" })])).toBe(false);
  });
});

// ------------------------------------------------------------------------- the badge

describe("the unread badge", () => {
  it("is the server's own count, not the length of the capped list", () => {
    const inbox = parseInbox({ notifications: [ROW, { ...ROW, id: "n2" }], unread: 37 });

    expect(inbox.unread).toBe(37);
    expect(inboxBadge(inbox.unread)).toBe("37 okunmamış");
  });

  it("counts what we were given when the answer carried no count", () => {
    const inbox = parseInbox({ notifications: [ROW, { ...ROW, id: "n2", read_at: "2026-09-13T08:00:00Z" }] });

    expect(inbox.unread).toBe(1);
  });

  it("is absent when nothing is waiting", () => {
    expect(inboxBadge(0)).toBeNull();
  });
});

// -------------------------------------------------------------------------- the client

describe("the client reads the routes and nothing else", () => {
  it("asks the inbox route", async () => {
    apiFetch.mockResolvedValueOnce(json(200, { notifications: [ROW], unread: 1 }));

    const state = await fetchInbox();

    expect(apiFetch).toHaveBeenCalledWith(NOTIFICATIONS_PATH);
    expect(state.kind).toBe("ok");
    if (state.kind === "ok") expect(state.value.rows[0]?.title).toBe("Yedek alınamadı");
  });

  it("asks the history route, which includes what nothing carried", async () => {
    apiFetch.mockResolvedValueOnce(json(200, { history: [ROW] }));

    const state = await fetchNotificationHistory();

    expect(apiFetch).toHaveBeenCalledWith(NOTIFICATIONS_HISTORY_PATH);
    if (state.kind === "ok") expect(state.value[0]?.delivered_via).toBeNull();
  });

  it("says the route is absent rather than empty when this Cloud Core has none", async () => {
    apiFetch.mockResolvedValueOnce(json(404, { detail: "not found" }));

    expect((await fetchInbox()).kind).toBe("absent");
  });

  it("posts the read exactly once, to that id's own path", async () => {
    apiFetch.mockResolvedValueOnce(json(200, { ...ROW, read_at: "2026-09-13T08:00:00Z" }));

    const updated = await markNotificationRead("n1");

    expect(apiFetch).toHaveBeenCalledTimes(1);
    expect(apiFetch).toHaveBeenCalledWith(notificationReadPath("n1"), { method: "POST" });
    expect(updated?.read_at).toBe("2026-09-13T08:00:00Z");
  });

  it("throws rather than reporting a read the server refused", async () => {
    apiFetch.mockResolvedValueOnce(json(404, { detail: "unknown notification" }));

    await expect(markNotificationRead("gone")).rejects.toThrow("HTTP 404");
  });

  it("drops a row with no id, because that is not a notification", () => {
    expect(parseNotification({ title: "başlık" })).toBeNull();
    expect(parseInbox({ notifications: [{ title: "başlık" }, ROW] }).rows).toHaveLength(1);
  });
});

// --------------------------------------------------------------------------- the panel

type ElementLike = { type: unknown; props: Record<string, unknown> };

function isElement(node: unknown): node is ElementLike {
  return !!node && typeof node === "object" && "props" in node && "type" in node;
}

/**
 * Find the rendered element carrying `attr={value}`, expanding function components by
 * calling them - the panels here are hook-free, so calling one is exactly what React does.
 * The same helper the Posta panel's suite uses.
 */
function findByData(root: unknown, attr: string, value: string): ElementLike | null {
  const queue: unknown[] = [root];
  let guard = 0;
  while (queue.length > 0 && guard++ < 10_000) {
    const node = queue.shift();
    if (Array.isArray(node)) {
      queue.push(...node);
      continue;
    }
    if (!isElement(node)) continue;
    if (node.props[attr] === value) return node;
    if (typeof node.type === "function") {
      const render = node.type as (props: Record<string, unknown>) => unknown;
      queue.push(render(node.props));
      continue;
    }
    const kids = node.props.children;
    if (kids !== undefined) queue.push(kids);
  }
  return null;
}

function click(node: ElementLike | null): void {
  expect(node).not.toBeNull();
  const handler = node?.props.onClick as (() => void) | undefined;
  expect(typeof handler).toBe("function");
  handler?.();
}

function loaded(rows: NotificationRow[], unread = rows.length) {
  return { kind: "ok" as const, value: { rows, unread }, at: 0 };
}

describe("the panel", () => {
  it("prints the three delivery sentences, not one", () => {
    const html = renderToStaticMarkup(
      <NotificationsPanel
        state={loaded([
          row(),
          row({ id: "n2", priority: "normal", delivered_at: "2026-09-13T02:00:05Z", delivered_via: "toast" }),
          row({ id: "n3", priority: "low", delivered_at: "2026-09-13T02:00:05Z", delivered_via: "inbox", read_at: "2026-09-13T08:00:00Z" }),
        ])}
        onMarkRead={() => {}}
        busyId={null}
      />,
    );

    expect(html).toContain(DELIVERY_TR.unreached);
    expect(html).toContain(DELIVERY_TR.delivered);
    expect(html).toContain(DELIVERY_TR.read);
  });

  it("says there are no notifications rather than showing nothing", () => {
    const html = renderToStaticMarkup(
      <NotificationsPanel state={loaded([], 0)} onMarkRead={() => {}} busyId={null} />,
    );

    expect(html).toContain("Bildirim yok.");
  });

  it("says it could not find out rather than showing an empty inbox", () => {
    const html = renderToStaticMarkup(
      <NotificationsPanel state={{ kind: "failed", error: "boom" }} onMarkRead={() => {}} busyId={null} />,
    );

    expect(html).toContain("Alınamadı");
    expect(html).not.toContain("Bildirim yok.");
  });

  it("offers Okundu on an unread row and not on a read one", () => {
    const unread = renderToStaticMarkup(
      <NotificationsPanel state={loaded([row()])} onMarkRead={() => {}} busyId={null} />,
    );
    const read = renderToStaticMarkup(
      <NotificationsPanel
        state={loaded([row({ read_at: "2026-09-13T08:00:00Z" })], 0)}
        onMarkRead={() => {}}
        busyId={null}
      />,
    );

    expect(unread).toContain('data-notification-action="read"');
    expect(read).not.toContain('data-notification-action="read"');
    expect(canMarkRead(row())).toBe(true);
    expect(canMarkRead(row({ read_at: "2026-09-13T08:00:00Z" }))).toBe(false);
  });

  it("asks for exactly the row that was clicked, once", () => {
    const asked: string[] = [];
    const tree = (
      <NotificationsPanel
        state={loaded([row(), row({ id: "n2" })])}
        onMarkRead={(id) => asked.push(id)}
        busyId={null}
      />
    );

    click(findByData(tree, "data-notification-target", "n2"));

    expect(asked).toEqual(["n2"]);
  });

  it("presses nothing while another read is in flight", () => {
    const tree = (
      <NotificationsPanel state={loaded([row()])} onMarkRead={() => {}} busyId="n1" />
    );

    expect(findByData(tree, "data-notification-target", "n1")?.props.disabled).toBe(true);
  });

  it("shows no more rows than it says it does", () => {
    const many = Array.from({ length: NOTIFICATION_ROWS_SHOWN + 5 }, (_, i) => row({ id: `n${i}` }));
    const html = renderToStaticMarkup(
      <NotificationsPanel state={loaded(many)} onMarkRead={() => {}} busyId={null} />,
    );

    expect(html.split("data-notification-id=").length - 1).toBe(NOTIFICATION_ROWS_SHOWN);
  });
});

describe("the timestamp", () => {
  it("prints the token verbatim when it cannot be read, rather than an invalid date", () => {
    expect(formatWhen("not-a-time")).toBe("not-a-time");
    expect(formatWhen(null)).toBe("zaman bildirilmedi");
  });
});

// ------------------------------------------------------- the read call's two rules

function ports(client: NotificationClient, initial = NOTIFICATION_READ_IDLE) {
  let state = initial;
  const written: NotificationReadState[] = [];
  let refreshed = 0;
  return {
    ports: {
      client,
      read: () => state,
      write: (next: NotificationReadState) => {
        state = next;
        written.push(next);
      },
      onSettled: () => {
        refreshed += 1;
      },
    },
    written,
    refreshes: () => refreshed,
    state: () => state,
  };
}

describe("marking one read", () => {
  it("says which row is in flight while the call is out", async () => {
    // The call is held open by a promise this test resolves, so "in flight" is a real
    // moment rather than a mocked one.
    let release!: () => void;
    const held = new Promise<void>((resolve) => {
      release = resolve;
    });
    const client: NotificationClient = {
      markRead: vi.fn(async () => {
        await held;
        return null;
      }),
    };
    const harness = ports(client);

    const running = runNotificationRead(harness.ports, "n1");
    expect(harness.state().busyId).toBe("n1");
    release();
    await running;

    expect(harness.state()).toEqual(NOTIFICATION_READ_IDLE);
  });

  it("runs one at a time - a second click while one is out calls nothing", async () => {
    const client: NotificationClient = { markRead: vi.fn(async () => null) };
    const harness = ports(client, { busyId: "n1", error: null });

    expect(await runNotificationRead(harness.ports, "n2")).toBe(false);
    expect(client.markRead).not.toHaveBeenCalled();
  });

  it("says a failure rather than swallowing it, and does not claim the row is read", async () => {
    const client: NotificationClient = {
      markRead: vi.fn(async () => {
        throw new Error("HTTP 404");
      }),
    };
    const harness = ports(client);

    await runNotificationRead(harness.ports, "n1");

    expect(harness.state()).toEqual({ busyId: null, error: "HTTP 404" });
    expect(harness.refreshes()).toBe(0);
  });

  it("refreshes the panel only after a call that worked", async () => {
    const harness = ports({ markRead: vi.fn(async () => null) });

    await runNotificationRead(harness.ports, "n1");

    expect(harness.refreshes()).toBe(1);
  });
});
