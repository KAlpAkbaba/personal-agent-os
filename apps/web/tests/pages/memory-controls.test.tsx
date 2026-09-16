/**
 * B37 (req 51-54, 57-60): the memory page's controls and the embedding status, as the
 * owner uses them - one call per press, the Cloud Core's own routes, the receipt's words.
 */

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";

const apiFetch = vi.fn();

vi.mock("../../app/lib/session", () => ({
  API_BASE: "http://core.test:8001",
  apiFetch: (...args: unknown[]) => apiFetch(...args),
  UnauthorizedError: class UnauthorizedError extends Error {},
}));

import MemoryPage from "../../app/memory/page";
import {
  MEMORY_ACTIONS,
  MEMORY_CONTROL_IDLE,
  MEMORY_EMBEDDING_PATH,
  MEMORY_OUTCOME_TR,
  type MemoryClient,
  type MemoryControlState,
  embeddingSentence,
  fetchEmbeddingStatus,
  memoryActionPath,
  memoryClient,
  memoryOutcomeText,
  parseEmbeddingStatus,
  parseMemoryReceipt,
  reindexMemory,
  runMemoryAction,
} from "../../app/lib/pages/memory";

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

beforeEach(() => {
  apiFetch.mockReset();
});

describe("the memory routes", () => {
  it("are the Cloud Core's own, with the id as a segment", () => {
    expect(MEMORY_ACTIONS).toEqual(["pin", "unpin", "forget", "correct"]);
    expect(memoryActionPath("m1", "pin")).toBe("/v1/memory/m1/pin");
    expect(memoryActionPath("m1", "unpin")).toBe("/v1/memory/m1/unpin");
    expect(memoryActionPath("m1", "forget")).toBe("/v1/memory/m1");
    expect(memoryActionPath("m 1", "correct")).toBe("/v1/memory/m%201/supersede");
    expect(MEMORY_EMBEDDING_PATH).toBe("/v1/memory/embedding");
  });

  it("POSTs pin/unpin, DELETEs a forget, and sends the correction as the supersede body", async () => {
    apiFetch.mockResolvedValueOnce(json(200, { memory_id: "m1", pinned: true }));
    await memoryClient.pin("m1");
    expect(apiFetch.mock.calls[0][0]).toBe("/v1/memory/m1/pin");
    expect((apiFetch.mock.calls[0][1] as RequestInit).method).toBe("POST");

    apiFetch.mockResolvedValueOnce(json(200, { memory_id: "m1", removed: { memories: 1 } }));
    const forgotten = await memoryClient.forget("m1");
    expect((apiFetch.mock.calls[1][1] as RequestInit).method).toBe("DELETE");
    expect(forgotten.removed).toBe(1);

    apiFetch.mockResolvedValueOnce(json(201, { memory_id: "m2" }));
    const corrected = await memoryClient.correct("m1", "Kahveyi sütsüz severim");
    const init = apiFetch.mock.calls[2][1] as RequestInit;
    expect(init.method).toBe("POST");
    expect(JSON.parse(String(init.body))).toEqual({ text: "Kahveyi sütsüz severim", reason: "owner_correction" });
    expect(corrected.memoryId).toBe("m2");
    expect(parseMemoryReceipt(null)).toEqual({ memoryId: null, pinned: null, removed: null });
  });

  it("runs one action at a time, speaks the outcome, and refuses an empty correction without a call", async () => {
    const client: MemoryClient = {
      pin: vi.fn(async () => ({ memoryId: "m1", pinned: true, removed: null })),
      unpin: vi.fn(async () => ({ memoryId: "m1", pinned: false, removed: null })),
      forget: vi.fn(async () => ({ memoryId: "m1", pinned: null, removed: 1 })),
      correct: vi.fn(async () => ({ memoryId: "m2", pinned: null, removed: null })),
    };
    let state: MemoryControlState = MEMORY_CONTROL_IDLE;
    const onSettled = vi.fn();
    const ports = { client, read: () => state, write: (next: MemoryControlState) => (state = next), onSettled, now: () => 7 };

    expect(await runMemoryAction(ports, "forget", "m1")).toBe(true);
    expect(client.forget).toHaveBeenCalledWith("m1");
    expect(state.outcome).toEqual({ action: "forget", id: "m1", ok: true, text: `${MEMORY_OUTCOME_TR.forget} · 1 kayıt`, at: 7 });
    expect(onSettled).toHaveBeenCalledTimes(1);

    expect(await runMemoryAction(ports, "correct", "m1", "   ")).toBe(true);
    expect(client.correct).not.toHaveBeenCalled();
    expect(state.outcome?.ok).toBe(false);
    expect(state.outcome?.text).toContain("yeni metni");

    state = { busy: { action: "pin", id: "m9" }, outcome: null };
    expect(await runMemoryAction(ports, "pin", "m1")).toBe(false);
    expect(client.pin).not.toHaveBeenCalled();
    expect(memoryOutcomeText("correct", { memoryId: "abcdef123456", pinned: null, removed: null })).toBe(
      `${MEMORY_OUTCOME_TR.correct} · yeni kayıt abcdef12`,
    );
  });
});

describe("the embedding status", () => {
  it("is read from the route, and says in as many words when a hash serves instead of meaning", async () => {
    apiFetch.mockResolvedValueOnce(
      json(200, {
        provider: { requested: "auto", active: "deterministic", model_id: "deterministic-ngram", semantic: false, fallback_reason: "auto: no OpenAI key configured, deterministic serves" },
        coverage: { memories: 12, embedded: 12, missing: 0 },
      }),
    );
    const loaded = await fetchEmbeddingStatus();
    expect(loaded.kind).toBe("ok");
    if (loaded.kind !== "ok") return;
    expect(loaded.value.semantic).toBe(false);
    const sentence = embeddingSentence(loaded.value);
    expect(sentence).toContain("anlamsal gömme yok");
    expect(sentence).toContain("sözcük karması");
    expect(sentence).toContain("12 hatıranın hepsi indekste");
    expect(sentence).toContain("no OpenAI key");

    const real = parseEmbeddingStatus({ provider: { active: "openai", model_id: "openai-text-embedding-3-small", semantic: true }, coverage: { memories: 12, embedded: 4, missing: 8 } });
    expect(embeddingSentence(real)).toBe("anlamsal gömme açık (openai-text-embedding-3-small) · 8 / 12 hatıra bu model için indekslenmemiş");

    apiFetch.mockResolvedValueOnce(json(200, { rows: 8, model_id: "openai-text-embedding-3-small" }));
    expect(await reindexMemory(true)).toBe(8);
    const init = apiFetch.mock.calls[1][1] as RequestInit;
    expect(apiFetch.mock.calls[1][0]).toBe("/v1/memory/reindex");
    expect(JSON.parse(String(init.body))).toEqual({ only_missing: true });
  });
});

describe("the memory page", () => {
  it("draws the controls on every row and the embedding section (read as source: the page renders behind the owner gate)", () => {
    const source = readFileSync(fileURLToPath(new URL("../../app/memory/page.tsx", import.meta.url)), "utf8");
    expect(source).toContain("<MemoryRowControls row={row} control={control} />");
    expect(source).toContain('id="memory-embedding"');
    expect(source).toContain('data-memory-reindex="missing"');
    expect(source).toContain("Evet, unut (geri alınamaz)");
    expect(source).toContain('data-memory-action="correct"');
    // A server render still succeeds: the gate, never a crash.
    expect(renderToStaticMarkup(<MemoryPage />)).toContain("<main>");
  });
});
