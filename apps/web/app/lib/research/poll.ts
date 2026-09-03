/**
 * Polls `GET /v1/research/{task_id}` until the task is terminal.
 *
 * Kept out of React so the stop condition is testable with a fake scheduler:
 * the poller asks for the task, reports it, and re-arms itself only while
 * `isTerminal` says no. Transport errors are reported and polling continues;
 * an `UnauthorizedError` (session gone) stops it — OwnerGate takes over.
 */

import { UnauthorizedError } from "../session";
import { POLL_INTERVAL_MS, type ResearchTaskDetail, isTerminal } from "./model";

export type Scheduler = {
  set: (fn: () => void, ms: number) => unknown;
  clear: (handle: unknown) => void;
};

const defaultScheduler: Scheduler = {
  set: (fn, ms) => setTimeout(fn, ms),
  clear: (handle) => clearTimeout(handle as ReturnType<typeof setTimeout>),
};

export type PollerOptions = {
  fetchTask: (taskId: string) => Promise<ResearchTaskDetail>;
  onUpdate: (task: ResearchTaskDetail) => void;
  onError?: (err: unknown) => void;
  onStop?: (reason: "terminal" | "unauthorized" | "stopped") => void;
  intervalMs?: number;
  scheduler?: Scheduler;
  /** Poll immediately on start (default) or wait one interval first. */
  immediate?: boolean;
};

export class ResearchPoller {
  private readonly opts: PollerOptions;
  private readonly scheduler: Scheduler;
  private readonly intervalMs: number;
  private handle: unknown = null;
  private taskId: string | null = null;
  private generation = 0;

  constructor(opts: PollerOptions) {
    this.opts = opts;
    this.scheduler = opts.scheduler ?? defaultScheduler;
    this.intervalMs = opts.intervalMs ?? POLL_INTERVAL_MS;
  }

  get active(): boolean {
    return this.taskId !== null;
  }

  start(taskId: string): void {
    this.stop("stopped");
    this.taskId = taskId;
    const gen = ++this.generation;
    if (this.opts.immediate === false) this.arm(gen);
    else void this.tick(gen);
  }

  stop(reason: "terminal" | "unauthorized" | "stopped" = "stopped"): void {
    if (this.taskId === null) return;
    if (this.handle !== null) {
      this.scheduler.clear(this.handle);
      this.handle = null;
    }
    this.taskId = null;
    this.generation += 1;
    this.opts.onStop?.(reason);
  }

  private arm(gen: number): void {
    this.handle = this.scheduler.set(() => {
      this.handle = null;
      void this.tick(gen);
    }, this.intervalMs);
  }

  private async tick(gen: number): Promise<void> {
    const taskId = this.taskId;
    if (taskId === null || gen !== this.generation) return;
    try {
      const task = await this.opts.fetchTask(taskId);
      if (gen !== this.generation) return; // stopped or restarted meanwhile
      this.opts.onUpdate(task);
      if (isTerminal(task)) {
        this.stop("terminal");
        return;
      }
    } catch (err) {
      if (gen !== this.generation) return;
      if (err instanceof UnauthorizedError) {
        this.stop("unauthorized");
        return;
      }
      this.opts.onError?.(err);
    }
    this.arm(gen);
  }
}
