import { describe, expect, it, vi } from "vitest";

import type { ResearchTaskDetail } from "../../app/lib/research/model";
import { ResearchPoller, type Scheduler } from "../../app/lib/research/poll";
import { UnauthorizedError } from "../../app/lib/session";
import { taskAt } from "./fixtures";

/** A scheduler whose timers only fire when the test says so. */
class ManualScheduler implements Scheduler {
  pending: Array<{ id: number; fn: () => void; ms: number }> = [];
  private next = 1;
  set = (fn: () => void, ms: number) => {
    const id = this.next++;
    this.pending.push({ id, fn, ms });
    return id;
  };
  clear = (handle: unknown) => {
    this.pending = this.pending.filter((p) => p.id !== handle);
  };
  fire(): void {
    const [head, ...rest] = this.pending;
    this.pending = rest;
    head?.fn();
  }
}

const settle = () => new Promise<void>((resolve) => setTimeout(resolve, 0));

function harness(sequence: Array<ResearchTaskDetail | Error>, intervalMs = 3000) {
  const scheduler = new ManualScheduler();
  const fetchTask = vi.fn(async () => {
    const next = sequence.shift();
    if (!next) throw new Error("sequence exhausted");
    if (next instanceof Error) throw next;
    return next;
  });
  const onUpdate = vi.fn();
  const onError = vi.fn();
  const onStop = vi.fn();
  const poller = new ResearchPoller({ fetchTask, onUpdate, onError, onStop, scheduler, intervalMs });
  return { scheduler, fetchTask, onUpdate, onError, onStop, poller };
}

describe("ResearchPoller", () => {
  it("polls every 3 s and stops at the first terminal state", async () => {
    const h = harness([taskAt("discovering"), taskAt("fetching"), taskAt("ready")]);
    h.poller.start("task-1");
    await settle();
    expect(h.fetchTask).toHaveBeenCalledTimes(1);
    expect(h.scheduler.pending).toHaveLength(1);
    expect(h.scheduler.pending[0].ms).toBe(3000);

    h.scheduler.fire();
    await settle();
    expect(h.fetchTask).toHaveBeenCalledTimes(2);
    expect(h.poller.active).toBe(true);

    h.scheduler.fire();
    await settle();
    expect(h.fetchTask).toHaveBeenCalledTimes(3);
    expect(h.onUpdate).toHaveBeenLastCalledWith(expect.objectContaining({ stage: "ready" }));
    expect(h.poller.active).toBe(false);
    expect(h.scheduler.pending).toHaveLength(0);
    expect(h.onStop).toHaveBeenCalledWith("terminal");
  });

  it.each(["failed", "cancelled"])("stops on %s without re-arming", async (stage) => {
    const h = harness([taskAt(stage)]);
    h.poller.start("task-1");
    await settle();
    expect(h.fetchTask).toHaveBeenCalledTimes(1);
    expect(h.scheduler.pending).toHaveLength(0);
    expect(h.poller.active).toBe(false);
    expect(h.onStop).toHaveBeenCalledWith("terminal");
  });

  it("stops when the task status is terminal even if the stage field lags", async () => {
    const h = harness([taskAt("persisting", { status: "READY" })]);
    h.poller.start("task-1");
    await settle();
    expect(h.poller.active).toBe(false);
  });

  it("keeps polling through transient errors but stops on an expired session", async () => {
    const h = harness([new Error("ECONNRESET"), taskAt("ranking"), new UnauthorizedError("/v1/research/task-1")]);
    h.poller.start("task-1");
    await settle();
    expect(h.onError).toHaveBeenCalledTimes(1);
    expect(h.poller.active).toBe(true);
    expect(h.scheduler.pending).toHaveLength(1);

    h.scheduler.fire();
    await settle();
    expect(h.onUpdate).toHaveBeenCalledTimes(1);

    h.scheduler.fire();
    await settle();
    expect(h.poller.active).toBe(false);
    expect(h.onStop).toHaveBeenCalledWith("unauthorized");
    expect(h.onError).toHaveBeenCalledTimes(1); // the 401 is not reported as an error
  });

  it("stop() clears the pending timer and ignores an in-flight answer; restart tracks the new task", async () => {
    const { promise: slow, resolve: release } = Promise.withResolvers<ResearchTaskDetail>();
    const scheduler = new ManualScheduler();
    const fetchTask = vi.fn((taskId: string) =>
      taskId === "task-1" ? slow : Promise.resolve(taskAt("ready", { task_id: taskId })),
    );
    const onUpdate = vi.fn();
    const onStop = vi.fn();
    const poller = new ResearchPoller({ fetchTask, onUpdate, onStop, scheduler });

    poller.start("task-1");
    poller.stop();
    expect(poller.active).toBe(false);
    expect(onStop).toHaveBeenCalledWith("stopped");
    release(taskAt("fetching"));
    await settle();
    expect(onUpdate).not.toHaveBeenCalled(); // the late answer for a stopped poll is dropped
    expect(scheduler.pending).toHaveLength(0);

    poller.start("task-2");
    await settle();
    expect(fetchTask).toHaveBeenLastCalledWith("task-2");
    expect(onUpdate).toHaveBeenCalledWith(expect.objectContaining({ task_id: "task-2", stage: "ready" }));
    expect(poller.active).toBe(false);
  });
});
