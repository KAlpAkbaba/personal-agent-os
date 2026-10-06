/**
 * The client for `/v1/conversations` (conversation-transcripts): the owner's conversations as
 * text, the people whose voices were named, and the standing 'evde dinle' switch.
 *
 * It sends what the owner pressed and reports what the Cloud Core answered; every rule (one
 * open conversation, consent before a voice profile, what a deleted person's lines read) is
 * the server's, and a refusal is surfaced with the server's own sentence.
 */

import { apiFetch } from "../../lib/session";

export const CONVERSATIONS_PATH = "/v1/conversations";

/** Exactly routes.py `_conversation` (without segments); the test reads that file. */
export type ConversationItem = {
  id: string;
  mode: string;
  title: string | null;
  started_at: string;
  ended_at: string | null;
  segment_count: number;
  preview: string;
};

/** Exactly routes.py `_segment`. */
export type Segment = {
  id: string;
  seq: number;
  spoken_at: string;
  text: string;
  speaker: string;
  is_owner: boolean;
  speaker_no: number | null;
  person_id: string | null;
};

export type ConversationDetail = ConversationItem & { segments: Segment[] };

/** Exactly routes.py `_person`. */
export type Person = {
  id: string;
  name: string;
  created_at: string;
  consent: boolean;
  consent_at: string | null;
  consent_note: string | null;
  has_profile: boolean;
};

export type Refusal = { ok: false; code: string; message: string };

async function refusal(response: Response): Promise<Refusal> {
  try {
    const body = (await response.json()) as { detail?: { code?: string; message?: string } };
    return {
      ok: false,
      code: body.detail?.code ?? `http_${response.status}`,
      message: body.detail?.message ?? `HTTP ${response.status}`,
    };
  } catch {
    return { ok: false, code: `http_${response.status}`, message: `HTTP ${response.status}` };
  }
}

const one = (id: string) => `${CONVERSATIONS_PATH}/${encodeURIComponent(id)}`;
const person = (id: string) => `${CONVERSATIONS_PATH}/people/${encodeURIComponent(id)}`;

export async function fetchConversations(
  q = "",
): Promise<{ ok: true; items: ConversationItem[] } | Refusal> {
  const needle = q.trim();
  const path = needle ? `${CONVERSATIONS_PATH}?q=${encodeURIComponent(needle)}` : CONVERSATIONS_PATH;
  const response = await apiFetch(path);
  if (!response.ok) return refusal(response);
  return { ok: true, items: ((await response.json()) as { items: ConversationItem[] }).items };
}

export async function fetchConversation(
  id: string,
): Promise<{ ok: true; conversation: ConversationDetail } | Refusal> {
  const response = await apiFetch(one(id));
  if (!response.ok) return refusal(response);
  return { ok: true, conversation: (await response.json()) as ConversationDetail };
}

async function deleted(path: string): Promise<{ ok: true; deleted: number } | Refusal> {
  const response = await apiFetch(path, { method: "DELETE" });
  if (!response.ok) return refusal(response);
  return { ok: true, deleted: ((await response.json()) as { deleted: number }).deleted };
}

export function deleteConversation(id: string) {
  return deleted(one(id));
}

/** 'unut': every conversation and every line. */
export function forgetAll() {
  return deleted(CONVERSATIONS_PATH);
}

export async function fetchPeople(): Promise<{ ok: true; people: Person[] } | Refusal> {
  const response = await apiFetch(`${CONVERSATIONS_PATH}/people`);
  if (!response.ok) return refusal(response);
  return { ok: true, people: ((await response.json()) as { items: Person[] }).items };
}

/** 'Ahmet izin verdi': the consent and its date are recorded by the server. */
export async function recordConsent(id: string): Promise<{ ok: true; person: Person } | Refusal> {
  const response = await apiFetch(`${person(id)}/consent`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({}),
  });
  if (!response.ok) return refusal(response);
  return { ok: true, person: (await response.json()) as Person };
}

/** The person, their voice profile and their name in every transcript go. */
export function deletePerson(id: string) {
  return deleted(person(id));
}

export async function fetchHomeListen(): Promise<{ ok: true; on: boolean } | Refusal> {
  const response = await apiFetch(`${CONVERSATIONS_PATH}/settings`);
  if (!response.ok) return refusal(response);
  return { ok: true, on: ((await response.json()) as { home_listen: boolean }).home_listen };
}

export async function setHomeListen(on: boolean): Promise<{ ok: true; on: boolean } | Refusal> {
  const response = await apiFetch(`${CONVERSATIONS_PATH}/settings`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ home_listen: on }),
  });
  if (!response.ok) return refusal(response);
  return { ok: true, on: ((await response.json()) as { home_listen: boolean }).home_listen };
}
