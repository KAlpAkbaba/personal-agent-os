import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { createOfficePoller, type OfficeView } from "../officeApi";
import { twoWorkers } from "./fixtures";

function setup(fetchImpl: () => Promise<OfficeView>) {
  const state = { hidden: false };
  const fetch = vi.fn(fetchImpl);
  const onData = vi.fn();
  const onError = vi.fn();
  const poller = createOfficePoller({
    fetch,
    onData,
    onError,
    isHidden: () => state.hidden,
    setInterval: (fn, ms) => setInterval(fn, ms),
    clearInterval: (id) => clearInterval(id as ReturnType<typeof setInterval>),
  });
  return { state, fetch, onData, onError, poller };
}

beforeEach(() => vi.useFakeTimers());
afterEach(() => vi.useRealTimers());

describe("the office poller", () => {
  it("fetches at once and then every 5 seconds", async () => {
    const t = setup(async () => twoWorkers());
    t.poller.start();
    await vi.advanceTimersByTimeAsync(0);
    expect(t.fetch).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(4999);
    expect(t.fetch).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1);
    expect(t.fetch).toHaveBeenCalledTimes(2);
    expect(t.onData).toHaveBeenCalledTimes(2);
    t.poller.stop();
  });

  it("stops polling while the tab is hidden and resumes with a fetch when shown", async () => {
    const t = setup(async () => twoWorkers());
    t.poller.start();
    await vi.advanceTimersByTimeAsync(0);
    t.state.hidden = true;
    t.poller.visibilityChanged();
    await vi.advanceTimersByTimeAsync(30000);
    expect(t.fetch).toHaveBeenCalledTimes(1);
    t.state.hidden = false;
    t.poller.visibilityChanged();
    await vi.advanceTimersByTimeAsync(0);
    expect(t.fetch).toHaveBeenCalledTimes(2);
    await vi.advanceTimersByTimeAsync(5000);
    expect(t.fetch).toHaveBeenCalledTimes(3);
    t.poller.stop();
  });

  it("reports a failure and keeps polling", async () => {
    let fail = true;
    const t = setup(async () => {
      if (fail) throw new Error("kapalı");
      return twoWorkers();
    });
    t.poller.start();
    await vi.advanceTimersByTimeAsync(0);
    expect(t.onError).toHaveBeenCalledTimes(1);
    expect(t.onData).not.toHaveBeenCalled();
    fail = false;
    await vi.advanceTimersByTimeAsync(5000);
    expect(t.onData).toHaveBeenCalledTimes(1);
    t.poller.stop();
  });

  it("does nothing after stop", async () => {
    const t = setup(async () => twoWorkers());
    t.poller.start();
    await vi.advanceTimersByTimeAsync(0);
    t.poller.stop();
    await vi.advanceTimersByTimeAsync(20000);
    expect(t.fetch).toHaveBeenCalledTimes(1);
  });
});
