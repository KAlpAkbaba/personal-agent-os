/**
 * The Turkish pack question on the screen (pack-question-button; ADR-0249 plan D3).
 *
 * When the owner has set the local STT setting to `acik` / `olc` and Chrome answers
 * `downloadable`, the local mode holds `packQuestion` and waits for a CLICK: Chrome's
 * `install()` consumes the click's transient user activation, so the yes must reach
 * `LocalVoiceMode.answerPackQuestion(true)` inside the click handler's own stack - no
 * promise, no timer, no state update first.
 *
 * Pinned here:
 * - the markup (react-dom/server): the snapshot's own sentence, the two buttons, and
 *   nothing of it without a question, with the switch off, or (buttons) without a handler;
 * - the click through the REAL view, the REAL props builder of `VoiceControl.tsx` and the
 *   REAL `LocalVoiceMode` built from the fakes: `install()` has been called on the line
 *   after the handler returns, with no await and no tick in between.
 */

import Link from "next/link";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { buildLocalViewProps } from "../../app/core/VoiceControl";
import VoiceControlView, { type LocalModeViewProps } from "../../app/core/VoiceControlView";
import { VoiceSessionApi } from "../../app/lib/voice/api";
import { FakeCloudCore, FakeOnDevice, FakeSpeechRecognition, FakeSpeechSynthesis, fakeUtterance } from "../../app/lib/voice/fake";
import { LOCAL_TRANSPORT, type LocalModeSnapshot, LocalVoiceMode, OFF_SNAPSHOT } from "../../app/lib/voice/localMode";
import { VoiceStore, type VoiceStoreSnapshot } from "../../app/lib/voice/store";

/** A sentence the view cannot have typed itself: whatever is shown came from the snapshot. */
const MADE_UP = "Uydurma soru 7f3a: paket gelsin mi?";

function voiceSnapshot(): VoiceStoreSnapshot {
  const store = new VoiceStore({
    build: (() => {
      throw new Error("the render test must never build a rig");
    }) as never,
    unloadTarget: null,
  });
  return store.getServerSnapshot();
}

function local(enabled: boolean, snapshot: Partial<LocalModeSnapshot> = {}, onAnswerPack?: (yes: boolean) => void): LocalModeViewProps {
  return {
    enabled,
    snapshot: { ...OFF_SNAPSHOT, ...snapshot },
    onToggle: () => {},
    onStart: () => {},
    onStop: () => {},
    ...(onAnswerPack ? { onAnswerPack } : {}),
  };
}

function tree(localProps: LocalModeViewProps) {
  return <VoiceControlView voice={voiceSnapshot()} onConnect={() => {}} onDisconnect={() => {}} onReconnect={() => {}} local={localProps} />;
}

const render = (localProps: LocalModeViewProps): string => renderToStaticMarkup(tree(localProps));

const LISTENING: Partial<LocalModeSnapshot> = { state: "listening", listening: true, provider: "local-router", sessionId: "s" };

type ElementLike = { type: unknown; props: Record<string, unknown> };

function isElement(node: unknown): node is ElementLike {
  return !!node && typeof node === "object" && "props" in node && "type" in node;
}

/**
 * The element carrying every `attrs` pair, expanding function components by calling
 * them (tests/cockpit/apps-panel.test.tsx). The view's components hold no hooks; the
 * one that does - next/link, a plain anchor - is not expanded.
 */
function findByData(root: unknown, attrs: Record<string, string>): ElementLike | null {
  const queue: unknown[] = [root];
  let guard = 0;
  while (queue.length > 0 && guard++ < 10_000) {
    const node = queue.shift();
    if (Array.isArray(node)) {
      queue.push(...node);
      continue;
    }
    if (!isElement(node)) continue;
    if (Object.entries(attrs).every(([k, v]) => node.props[k] === v)) return node;
    if (node.type === Link) continue;
    if (typeof node.type === "function") {
      const draw = node.type as (props: Record<string, unknown>) => unknown;
      queue.push(draw(node.props));
      continue;
    }
    const kids = node.props.children;
    if (kids !== undefined) queue.push(kids);
  }
  return null;
}

function handlerOf(node: ElementLike | null): () => void {
  expect(node).not.toBeNull();
  const handler = node?.props.onClick as (() => void) | undefined;
  expect(typeof handler).toBe("function");
  return handler as () => void;
}

const YES = { "data-local-pack-answer": "yes" };
const NO = { "data-local-pack-answer": "no" };

describe("VoiceControlView: the pack question (markup)", () => {
  it("draws the snapshot's own sentence and the two Turkish buttons", () => {
    const html = render(local(true, { ...LISTENING, packQuestion: MADE_UP }, () => {}));
    expect(html).toMatch(/data-local-pack-question[^>]*>Uydurma soru 7f3a: paket gelsin mi\?</);
    expect(html).toMatch(/<button[^>]*data-local-pack-answer="yes"[^>]*>Evet, indir<\/button>/);
    expect(html).toMatch(/<button[^>]*data-local-pack-answer="no"[^>]*>Hayır<\/button>/);
    for (const answer of ["yes", "no"]) {
      const button = html.match(new RegExp(`<button[^>]*data-local-pack-answer="${answer}"[^>]*>`))?.[0] ?? "";
      expect(button).toContain('type="button"');
      expect(button).toContain('class="core-chip"');
    }
  });

  it("no question: none of the three markers", () => {
    const html = render(local(true, { ...LISTENING, packQuestion: null }, () => {}));
    expect(html).not.toContain("data-local-pack-question");
    expect(html).not.toContain("data-local-pack-answer");
  });

  it("the local switch off: none, even with a question held", () => {
    const html = render(local(false, { ...LISTENING, packQuestion: MADE_UP }, () => {}));
    expect(html).not.toContain(MADE_UP);
    expect(html).not.toContain("data-local-pack-question");
    expect(html).not.toContain("data-local-pack-answer");
  });

  it("a question with no handler: the sentence, and never a button that does nothing", () => {
    const html = render(local(true, { ...LISTENING, packQuestion: MADE_UP }));
    expect(html).toContain(MADE_UP);
    expect(html).toContain("data-local-pack-question");
    expect(html).not.toContain("data-local-pack-answer");
    expect(html).not.toContain("Evet, indir");
  });

  it("with the question shown the start/stop button and the listening indicator stay", () => {
    const html = render(local(true, { ...LISTENING, packQuestion: MADE_UP }, () => {}));
    expect(html).toContain('data-local-listening="yes"');
    expect(html).toContain('data-local-action="stop"');
    const idle = render(local(true, { packQuestion: MADE_UP }, () => {}));
    expect(idle).toContain('data-local-listening="no"');
    expect(idle).toContain('data-local-action="start"');
  });

  it("two buttons, two different answers: the handler receives exactly true and false", () => {
    const received: boolean[] = [];
    const props = buildLocalViewProps({
      localVoice: true,
      local: { ...OFF_SNAPSHOT, ...LISTENING, packQuestion: MADE_UP },
      start: async () => {},
      stop: async () => {},
      answerPackQuestion: (yes) => void received.push(yes),
      setLocalVoice: () => {},
      disconnectPaid: () => {},
    });
    const view = tree(props);
    handlerOf(findByData(view, YES))();
    handlerOf(findByData(view, NO))();
    expect(received).toEqual([true, false]);
    expect(new Set(received)).toEqual(new Set([true, false]));
  });
});

/** A local mode built from the fakes exactly as tests/voice/local-stt-engine.test.ts builds it. */
function realMode() {
  const core = new FakeCloudCore({
    provider: "local-router",
    transport: LOCAL_TRANSPORT,
    resolveIntents: () => [{ intent: "clock_query", klass: "query", capability: null, tool: "clock.now" }],
    toolResponses: { "clock.now": { result: { speech: "Saat on iki efendim." } } },
  });
  const recognition = new FakeSpeechRecognition();
  const synthesis = new FakeSpeechSynthesis();
  const onDevice = new FakeOnDevice("downloadable", true);
  let ids = 0;
  const mode = new LocalVoiceMode({
    api: new VoiceSessionApi(core.fetcher),
    recognition: () => recognition,
    synthesis: () => synthesis,
    utterance: fakeUtterance,
    now: () => 1000 + ids,
    newId: () => `id${(ids += 1)}`,
    setTimer: () => ({}),
    clearTimer: () => {},
    onDevice: () => onDevice,
    phrase: () => (text: string, boost: number) => ({ phrase: text, boost }),
    sttSetting: () => "acik",
    phraseSources: async () => ({ deviceAliases: [], capabilityPhrases: [] }),
  });
  /** What VoiceControl draws for this mode now: the real props builder, the real view. */
  const view = () =>
    tree(
      buildLocalViewProps({
        localVoice: true,
        local: mode.getSnapshot(),
        start: () => mode.start(),
        stop: () => mode.stop(),
        answerPackQuestion: (yes) => mode.answerPackQuestion(yes),
        setLocalVoice: () => {},
        disconnectPaid: () => {},
      }),
    );
  return { mode, onDevice, view };
}

describe("the click reaches Chrome's install() inside the click", () => {
  it("yes: install() is called before the handler returns, once, for tr-TR on the device; the question goes", async () => {
    const { mode, onDevice, view } = realMode();
    await mode.start();
    expect(mode.getSnapshot().packQuestion).not.toBeNull();
    expect(renderToStaticMarkup(view())).toContain(mode.getSnapshot().packQuestion as string);
    const yes = handlerOf(findByData(view(), YES));
    expect(onDevice.installCalls).toEqual([]);
    yes();
    // The synchronous rule: no await and no tick between the click and this line.
    expect(onDevice.installCalls).toEqual([{ langs: ["tr-TR"], processLocally: true }]);
    expect(mode.getSnapshot().packQuestion).toBeNull();
    const after = renderToStaticMarkup(view());
    expect(after).not.toContain("data-local-pack-answer");
    expect(after).not.toContain("data-local-pack-question");
    expect(findByData(view(), YES)).toBeNull();
  });

  it("no: nothing is installed, the question goes, and the refusal is recorded", async () => {
    const { mode, onDevice, view } = realMode();
    await mode.start();
    handlerOf(findByData(view(), NO))();
    expect(onDevice.installCalls).toEqual([]);
    expect(mode.getSnapshot().packQuestion).toBeNull();
    expect(mode.getSnapshot().sttFallback).toBe("pack-declined");
    expect(renderToStaticMarkup(view())).not.toContain("data-local-pack-answer");
  });

  it("a stale yes, clicked again after the question is gone, installs nothing", async () => {
    const { mode, onDevice, view } = realMode();
    await mode.start();
    const yes = handlerOf(findByData(view(), YES));
    yes();
    const count = onDevice.installCalls.length;
    expect(count).toBe(1);
    yes();
    expect(onDevice.installCalls).toHaveLength(count);

    const declined = realMode();
    await declined.mode.start();
    const staleYes = handlerOf(findByData(declined.view(), YES));
    handlerOf(findByData(declined.view(), NO))();
    staleYes();
    expect(declined.onDevice.installCalls).toEqual([]);
  });
});
