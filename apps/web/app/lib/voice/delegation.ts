/**
 * The GPT-Live delegation bridge (dialect `openai-live`; measurement only, `?ses=live`).
 *
 * GPT-Live keeps talking and hands the work back to us as a delegation. The bridge
 * gives that work to the relay by the SAME road local mode's sentences take
 * (ADR-0173: an `utterance` event, the deterministic router's `resolved_intents`, then
 * `/tool-calls` per tool), and returns the receipted answer as the commentary of the
 * SAME delegation id.
 *
 * Shared contract with the gpt-live-provider card (verbatim there):
 * - the delegation goes to the relay as an `utterance` event with payload
 *   `{source: 'delegation', delegation_id: '<vendor id>'}`;
 * - its result goes back ONLY as `session.commentary.append` under the same id; a result
 *   without an id, for an unknown id or for a cancelled delegation is never sent;
 * - a cancelled delegation is written to the relay as `delegation_cancelled`
 *   (payload `{delegation_id}`). Until the relay knows that kind it travels as a `state`
 *   event with `payload.event = 'delegation_cancelled'` (audited, the FSM untouched).
 *
 * ADR-0063 (honest speech): what is said comes from the relay's receipted answer
 * (`speechOf`, local mode's own rule); a failed tool never yields a success sentence.
 * A barge-in does not cancel a delegation (the vendor: "interrupting the spoken
 * conversation leaves backend work running"); a change of mind is a new delegation.
 */

import type { VoiceSessionApi } from "./api";
import type { ClientEvent, EventsResponse, ToolCallResponse } from "./contract";
import { MAX_EVENT_TEXT_CHARS } from "./contract";
import { NOT_UNDERSTOOD_TR, TOOL_FAILED_TR, saidFrames, speechOf, toolOf } from "./localMode";

export const DELEGATION_SOURCE = "delegation";
export const DELEGATION_CANCELLED_EVENT = "delegation_cancelled";
/** `/tool-calls` ids are `live-<delegation id>-<n>`: idempotent per delegation and tool. */
export const DELEGATION_CALL_ID_PREFIX = "live-";

export type DelegationState = "bekliyor" | "bitti" | "iptal";

export type DelegationBridgeDeps = {
  api: Pick<VoiceSessionApi, "events" | "toolCall">;
  sessionId: string;
  /** Say `text` as the commentary of `delegationId` (the transport's data channel). */
  commentary: (delegationId: string, text: string) => void;
  /** Session-relative milliseconds for `t_ms`. */
  clock: () => number;
  turn: () => number;
  log?: (op: string) => void;
};

export class DelegationBridge {
  private readonly states = new Map<string, DelegationState>();
  /** The owner's words since the last delegation (the vendor does not send the task text). */
  private heard = "";

  constructor(private readonly deps: DelegationBridgeDeps) {}

  stateOf(delegationId: string): DelegationState | undefined {
    return this.states.get(delegationId);
  }

  noteOwnerText(delta: string): void {
    // Bounded to what one utterance event may carry; the newest words win.
    this.heard = (this.heard + delta).slice(-MAX_EVENT_TEXT_CHARS);
  }

  /** Run one delegation to its spoken result. Never throws. */
  async onDelegation(delegationId: string): Promise<void> {
    if (!delegationId) {
      this.log("delegation.no_id");
      return;
    }
    if (this.states.has(delegationId)) {
      this.log(`delegation.duplicate ${delegationId}`);
      return;
    }
    this.states.set(delegationId, "bekliyor");
    const text = this.heard.trim();
    this.heard = "";
    if (!text) {
      this.deliver(delegationId, NOT_UNDERSTOOD_TR);
      return;
    }
    let answer: EventsResponse;
    try {
      answer = await this.deps.api.events(this.deps.sessionId, [
        {
          kind: "utterance",
          t_ms: this.tMs(),
          turn: this.deps.turn(),
          text,
          payload: { source: DELEGATION_SOURCE, delegation_id: delegationId },
        },
      ]);
    } catch (error) {
      this.log(`delegation.utterance_failed ${delegationId} ${messageOf(error)}`);
      this.deliver(delegationId, TOOL_FAILED_TR);
      return;
    }
    const tools: string[] = [];
    for (const intent of answer.resolved_intents ?? []) {
      const tool = toolOf(intent);
      if (tool && !tools.includes(tool)) tools.push(tool);
    }
    const lines = saidFrames(answer.pending_sideband);
    if (tools.length === 0 && lines.length === 0) {
      this.deliver(delegationId, NOT_UNDERSTOOD_TR);
      return;
    }
    for (const [index, name] of tools.entries()) {
      // A cancelled delegation runs no further tools.
      if (this.states.get(delegationId) !== "bekliyor") break;
      let response: ToolCallResponse;
      try {
        response = await this.deps.api.toolCall(this.deps.sessionId, {
          call_id: `${DELEGATION_CALL_ID_PREFIX}${delegationId}-${index + 1}`,
          name,
          arguments: {},
        });
      } catch (error) {
        this.log(`delegation.tool_failed ${delegationId} ${name} ${messageOf(error)}`);
        lines.push(TOOL_FAILED_TR);
        continue;
      }
      const speech = speechOf(response);
      if (speech) lines.push(speech);
    }
    this.deliver(delegationId, lines.length ? lines.join(" ") : TOOL_FAILED_TR);
  }

  /**
   * Send `text` as the result of `delegationId` - only for a delegation this bridge
   * started and that is still waiting. Returns whether it was sent.
   */
  deliver(delegationId: string, text: string): boolean {
    if (!delegationId || !this.states.has(delegationId)) {
      this.log(`delegation.unknown_result ${delegationId ?? ""}`);
      return false;
    }
    if (this.states.get(delegationId) === "iptal") {
      this.log(`delegation.late_result ${delegationId}`);
      return false;
    }
    if (this.states.get(delegationId) === "bitti") return false;
    this.states.set(delegationId, "bitti");
    this.deps.commentary(delegationId, text);
    this.log(`delegation.done ${delegationId}`);
    return true;
  }

  /** Cancel one waiting delegation and write `delegation_cancelled`; anything else is a no-op. */
  async cancel(delegationId: string, reason: string): Promise<void> {
    if (this.states.get(delegationId) !== "bekliyor") return;
    this.states.set(delegationId, "iptal");
    this.log(`delegation.cancelled ${delegationId} ${reason}`);
    try {
      await this.deps.api.events(this.deps.sessionId, [this.cancelledEvent(delegationId, reason)]);
    } catch (error) {
      this.log(`delegation.cancel_report_failed ${delegationId} ${messageOf(error)}`);
    }
  }

  /** The session or the leg ended: every waiting delegation is cancelled. */
  async cancelAll(reason: string): Promise<void> {
    const waiting = [...this.states].filter(([, state]) => state === "bekliyor").map(([id]) => id);
    for (const id of waiting) await this.cancel(id, reason);
    this.heard = "";
  }

  private cancelledEvent(delegationId: string, reason: string): ClientEvent {
    return {
      kind: "state",
      t_ms: this.tMs(),
      turn: this.deps.turn(),
      payload: { event: DELEGATION_CANCELLED_EVENT, delegation_id: delegationId, reason },
    };
  }

  private tMs(): number {
    return Math.max(0, Math.round(this.deps.clock()));
  }

  private log(op: string): void {
    this.deps.log?.(op);
  }
}

function messageOf(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}
