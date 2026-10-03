/**
 * The measurement page's state, outside React: the server's answer, the chosen place, the
 * take in progress and the last sentence to show. The page subscribes with
 * `useSyncExternalStore`; tests drive it directly with fakes.
 *
 * A take is recorded as done only from the server's answer: a refused or failed upload
 * leaves the sentence not done and shows the server's sentence.
 */

import {
  type DeleteResult,
  type Measurement,
  type Place,
  type PutResult,
  deleteAllRecordings,
  deleteRecording,
  fetchMeasurement,
  putRecording,
} from "./api";
import { CHROME_NOT_RUN_TR, LIVE_REASON_TR, SAVED_TR, deletedText, nextIndex } from "./model";
import { type RecorderDeps, type Take, type TakeOptions, startTake } from "./recorder";

export type MeasurePhase = "idle" | "recording" | "uploading";

export type MeasureSnapshot = {
  data: Measurement | null;
  loadError: string | null;
  place: Place;
  phase: MeasurePhase;
  /** The sentence being recorded (or just uploaded); null when idle. */
  target: number | null;
  notice: string | null;
  busy: boolean;
};

export type MeasureApi = {
  fetch: typeof fetchMeasurement;
  put: typeof putRecording;
  remove: typeof deleteRecording;
  removeAll: typeof deleteAllRecordings;
};

export type MeasureSessionDeps = {
  /** Fresh browser seams for one take (a new probe microphone each time). */
  recorder: () => Omit<RecorderDeps, "upload">;
  /** The device and constraints the voice rig would open the microphone with. */
  mic: () => Pick<TakeOptions, "deviceId" | "constraints">;
  /** True while the tab's voice session holds the microphone. */
  live: () => boolean;
  api?: MeasureApi;
};

const REAL_API: MeasureApi = {
  fetch: fetchMeasurement,
  put: putRecording,
  remove: deleteRecording,
  removeAll: deleteAllRecordings,
};

const INITIAL: MeasureSnapshot = {
  data: null,
  loadError: null,
  place: "ev",
  phase: "idle",
  target: null,
  notice: null,
  busy: false,
};

export class MeasureSession {
  private snapshot: MeasureSnapshot = INITIAL;
  private readonly listeners = new Set<() => void>();
  private take: Take | null = null;
  private readonly api: MeasureApi;

  constructor(private readonly deps: MeasureSessionDeps) {
    this.api = deps.api ?? REAL_API;
  }

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  };

  getSnapshot = (): MeasureSnapshot => this.snapshot;

  private set(patch: Partial<MeasureSnapshot>): void {
    this.snapshot = { ...this.snapshot, ...patch };
    for (const listener of this.listeners) listener();
  }

  async load(): Promise<void> {
    const result = await this.api.fetch();
    if (result.ok) {
      const places = result.measurement.places;
      const place = places.includes(this.snapshot.place) ? this.snapshot.place : (places[0] ?? "ev");
      this.set({ data: result.measurement, loadError: null, place });
    } else {
      this.set({ loadError: result.message });
    }
  }

  setPlace(place: Place): void {
    if (this.snapshot.phase !== "idle") return;
    this.set({ place, notice: null });
  }

  /** Record `index`, or the next undone sentence of the chosen place. */
  async start(index?: number): Promise<void> {
    const { data, phase, place } = this.snapshot;
    if (!data || phase !== "idle") return;
    if (this.deps.live()) {
      this.set({ notice: LIVE_REASON_TR });
      return;
    }
    const target = index ?? nextIndex(data, place);
    if (target === null) return;
    const upload = (where: Place, at: number, body: Parameters<MeasureApi["put"]>[2]): Promise<PutResult> => {
      this.set({ phase: "uploading" });
      return this.api.put(where, at, body);
    };
    const take = startTake(
      { ...this.deps.recorder(), upload },
      { place, index: target, maxSeconds: data.max_seconds, ...this.deps.mic() },
    );
    this.take = take;
    this.set({ phase: "recording", target, notice: null });
    const outcome = await take.done;
    this.take = null;
    if (outcome.ok) {
      const current = this.snapshot.data ?? data;
      const recordings = current.recordings.filter(
        (item) => !(item.place === outcome.item.place && item.index === outcome.item.index),
      );
      this.set({
        data: { ...current, recordings: [...recordings, outcome.item] },
        phase: "idle",
        target: null,
        notice: outcome.chromeRan ? SAVED_TR : CHROME_NOT_RUN_TR,
      });
    } else {
      this.set({ phase: "idle", target: null, notice: outcome.message });
    }
    await this.load();
  }

  stop(): void {
    this.take?.stop();
  }

  retake(index: number): Promise<void> {
    return this.start(index);
  }

  async remove(index: number): Promise<void> {
    await this.deleting(() => this.api.remove(this.snapshot.place, index), () => "Kayıt silindi.");
  }

  /** One press, no second confirmation (owner rule 2026-09-18). */
  async removeAll(): Promise<void> {
    await this.deleting(() => this.api.removeAll(), deletedText);
  }

  private async deleting(call: () => Promise<DeleteResult>, said: (count: number) => string): Promise<void> {
    if (this.snapshot.phase !== "idle" || this.snapshot.busy) return;
    this.set({ busy: true });
    const result = await call();
    this.set({ busy: false, notice: result.ok ? said(result.deleted) : result.message });
    await this.load();
  }

  /** The page is going away: a take in progress is dropped, not uploaded. */
  dispose(): void {
    this.take?.cancel();
  }
}
