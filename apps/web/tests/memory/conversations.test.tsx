/**
 * `/memory/conversations` - the owner reads, searches and deletes his conversations, and
 * decides about the people whose voices were named.
 *
 * No DOM (vitest runs in node): the view is rendered with react-dom/server and a PRESS is the
 * button's own onClick taken from the rendered element tree - the wiring the page ships. The
 * client is exercised against a mocked apiFetch, and its field names are held to the Cloud
 * Core's routes.py by reading that file (the two halves of one contract read each other).
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";

import { isValidElement, type ReactElement, type ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../app/lib/session", () => ({
  apiFetch: vi.fn(),
  UnauthorizedError: class UnauthorizedError extends Error {},
}));

import { apiFetch } from "../../app/lib/session";
import ConversationsView, {
  type ConversationsViewProps,
} from "../../app/memory/conversations/ConversationsView";
import {
  CONVERSATIONS_PATH,
  deleteConversation,
  deletePerson,
  fetchConversation,
  fetchConversations,
  forgetAll,
  recordConsent,
  setHomeListen,
  type ConversationDetail,
  type ConversationItem,
  type Person,
} from "../../app/memory/conversations/conversationsApi";

const fetchMock = vi.mocked(apiFetch);
const C1 = "aaaaaaaa-0000-4000-8000-000000000001";
const C2 = "aaaaaaaa-0000-4000-8000-000000000002";
const P1 = "bbbbbbbb-0000-4000-8000-000000000001";

function item(id: string, preview: string, over: Partial<ConversationItem> = {}): ConversationItem {
  return {
    id,
    mode: "manual",
    title: null,
    started_at: "2026-10-05T09:00:00+00:00",
    ended_at: "2026-10-05T09:20:00+00:00",
    segment_count: 3,
    preview,
    ...over,
  };
}

function detail(): ConversationDetail {
  return {
    ...item(C1, "Selam"),
    segments: [
      { id: "s1", seq: 1, spoken_at: "2026-10-05T09:00:01+00:00", text: "Selam", speaker: "Ahmet", is_owner: false, speaker_no: 1, person_id: P1 },
      { id: "s2", seq: 2, spoken_at: "2026-10-05T09:00:05+00:00", text: "Hoş geldin", speaker: "Sen", is_owner: true, speaker_no: null, person_id: null },
      { id: "s3", seq: 3, spoken_at: "2026-10-05T09:00:09+00:00", text: "Merhaba", speaker: "Konuşmacı 2", is_owner: false, speaker_no: 2, person_id: null },
    ],
  };
}

function people(): Person[] {
  return [
    { id: P1, name: "Ahmet", created_at: "2026-10-05T09:00:00+00:00", consent: true, consent_at: "2026-10-05T09:01:00+00:00", consent_note: null, has_profile: true },
    { id: "p2", name: "Ayşe", created_at: "2026-10-05T09:00:00+00:00", consent: false, consent_at: null, consent_note: null, has_profile: false },
  ];
}

type Props = Record<string, unknown> & { children?: ReactNode };

function props(over: Partial<ConversationsViewProps> = {}): ConversationsViewProps {
  return {
    list: [item(C1, "Selam"), item(C2, "Market listesi", { mode: "home", ended_at: null })],
    error: null,
    query: "",
    selected: null,
    people: people(),
    homeListen: false,
    notice: null,
    busy: false,
    onQuery: () => {},
    onSearch: () => {},
    onOpen: () => {},
    onDelete: () => {},
    onForgetAll: () => {},
    onHomeListen: () => {},
    onConsent: () => {},
    onDeletePerson: () => {},
    timeZone: "Europe/Istanbul",
    ...over,
  };
}

function html(over: Partial<ConversationsViewProps> = {}): string {
  return renderToStaticMarkup(<ConversationsView {...props(over)} />);
}

function elements(node: ReactNode): ReactElement<Props>[] {
  if (Array.isArray(node)) return node.flatMap(elements);
  if (!isValidElement<Props>(node)) return [];
  if (typeof node.type === "function") {
    return elements((node.type as (p: Props) => ReactNode)(node.props));
  }
  return [node, ...elements(node.props.children)];
}

function text(node: ReactNode): string {
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(text).join("");
  if (isValidElement<Props>(node)) return text(node.props.children);
  return "";
}

function button(over: Partial<ConversationsViewProps>, label: string, near?: string) {
  const found = elements(<ConversationsView {...props(over)} />).filter(
    (e) => e.type === "button" && text(e.props.children) === label,
  );
  const pick = near ? found.find((e) => e.props["data-for"] === near) : found[0];
  if (!pick) throw new Error(`no button '${label}' ${near ?? ""}`);
  return pick.props.onClick as () => void;
}

describe("ConversationsView", () => {
  it("lists conversations with their first line and marks the open one", () => {
    const page = html();
    expect(page).toContain("Selam");
    expect(page).toContain("Market listesi");
    expect(page).toContain("sürüyor");
    expect(page).toContain("evde dinle");
  });

  it("shows every line under its speaker, the owner's as Sen", () => {
    const page = html({ selected: detail() });
    expect(page).toMatch(/data-speaker="Ahmet"[^>]*>.*Ahmet/);
    expect(page).toContain('data-owner="true"');
    expect(page).toContain("Konuşmacı 2");
    expect(page.indexOf("Selam")).toBeLessThan(page.indexOf("Hoş geldin"));
  });

  it("says plainly that no audio is kept", () => {
    expect(html()).toContain("Ses kaydı tutulmaz");
  });

  it("presses: search, open, delete, forget all, evde dinle, consent, delete person", () => {
    const calls: string[] = [];
    const over: Partial<ConversationsViewProps> = {
      query: "tapu",
      onSearch: () => calls.push("search"),
      onOpen: (id) => calls.push(`open:${id}`),
      onDelete: (id) => calls.push(`delete:${id}`),
      onForgetAll: () => calls.push("forget"),
      onHomeListen: (on) => calls.push(`home:${on}`),
      onConsent: (id) => calls.push(`consent:${id}`),
      onDeletePerson: (id) => calls.push(`person:${id}`),
    };
    button(over, "Ara")();
    button(over, "Oku", C2)();
    button(over, "Sil", C1)();
    button(over, "Hepsini unut")();
    button(over, "Evde dinle: kapalı")();
    button(over, "İzin verdi", "p2")();
    button(over, "Kişiyi sil", P1)();
    expect(calls).toEqual([
      "search",
      `open:${C2}`,
      `delete:${C1}`,
      "forget",
      "home:true",
      "consent:p2",
      `person:${P1}`,
    ]);
  });

  it("offers consent only to a person without it", () => {
    const over = {};
    expect(() => button(over, "İzin verdi", P1)).toThrow();
    expect(html()).toContain("ses profili var");
    expect(html()).toContain("izin yok");
  });

  it("shows the server's sentence when the list could not be read", () => {
    const page = html({ list: null, error: "Oturum yok" });
    expect(page).toContain('role="alert"');
    expect(page).toContain("Oturum yok");
  });

  it("an empty list says so", () => {
    expect(html({ list: [] })).toContain("Henüz yazılmış konuşma yok");
  });
});

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("conversationsApi", () => {
  beforeEach(() => fetchMock.mockReset());

  it("searches with the query in the path and reads one conversation", async () => {
    fetchMock.mockResolvedValueOnce(json(200, { items: [item(C1, "tapu")] }));
    const listed = await fetchConversations("tapu dairesi");
    expect(listed.ok && listed.items[0].id).toBe(C1);
    expect(fetchMock.mock.calls[0][0]).toBe(`${CONVERSATIONS_PATH}?q=tapu%20dairesi`);
    fetchMock.mockResolvedValueOnce(json(200, detail()));
    const one = await fetchConversation(C1);
    expect(one.ok && one.conversation.segments.length).toBe(3);
  });

  it("deletes, forgets, consents, deletes a person and sets evde dinle with the right calls", async () => {
    fetchMock.mockImplementation(async () =>
      json(200, { deleted: 1, home_listen: true, ...people()[1] }),
    );
    await deleteConversation(C1);
    await forgetAll();
    await recordConsent("p2");
    await deletePerson(P1);
    await setHomeListen(true);
    const seen = fetchMock.mock.calls.map(([path, init]) => `${init?.method ?? "GET"} ${path}`);
    expect(seen).toEqual([
      `DELETE ${CONVERSATIONS_PATH}/${C1}`,
      `DELETE ${CONVERSATIONS_PATH}`,
      `POST ${CONVERSATIONS_PATH}/people/p2/consent`,
      `DELETE ${CONVERSATIONS_PATH}/people/${P1}`,
      `PUT ${CONVERSATIONS_PATH}/settings`,
    ]);
    expect(JSON.parse(String(fetchMock.mock.calls[4][1]?.body))).toEqual({ home_listen: true });
  });

  it("keeps a refusal a refusal, with the server's own sentence", async () => {
    fetchMock.mockResolvedValueOnce(
      json(404, { detail: { code: "not_found", message: "Bu konuşma yok; silinmiş olabilir." } }),
    );
    const result = await deleteConversation(C1);
    expect(result).toEqual({
      ok: false,
      code: "not_found",
      message: "Bu konuşma yok; silinmiş olabilir.",
    });
  });
});

describe("contract with routes.py", () => {
  const routes = readFileSync(
    join(__dirname, "../../../../services/api/app/conversations/routes.py"),
    "utf8",
  );

  function keysOf(fn: string): string[] {
    const body = routes.split(`def ${fn}(`)[1].split("\ndef ")[0];
    return [...body.matchAll(/^\s+"([a-z_]+)":/gm)].map((m) => m[1]).toSorted();
  }

  it("a segment, a conversation and a person carry exactly the fields the client reads", () => {
    expect(keysOf("_segment")).toEqual(Object.keys(detail().segments[0]).toSorted());
    expect(keysOf("_conversation")).toEqual(Object.keys(item(C1, "x")).toSorted());
    expect(keysOf("_person")).toEqual(Object.keys(people()[0]).toSorted());
  });
});
