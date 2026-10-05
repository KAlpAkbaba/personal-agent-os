/**
 * The measurement page's view: hook-free, so tests render it with fixtures and press its
 * buttons by calling their handlers. Everything shown comes from the server's answer and
 * the session's snapshot - no sentence text is written here.
 */

import type { Place } from "../../lib/voice/measure/api";
import {
  LIVE_REASON_TR,
  PLACE_LABEL,
  RECORDING_TR,
  UPLOADING_TR,
  allDoneText,
  chromeText,
  counterText,
  doneIndices,
  headLines,
  nextIndex,
  recordingFor,
  secondsText,
} from "../../lib/voice/measure/model";
import type { MeasureSession, MeasureSnapshot } from "../../lib/voice/measure/session";

export type MeasureViewProps = {
  snapshot: MeasureSnapshot;
  /** The tab's voice session holds the microphone. */
  live: boolean;
  onPlace: (place: Place) => void;
  onStart: () => void;
  onStop: () => void;
  onRetake: (index: number) => void;
  onDelete: (index: number) => void;
  onDeleteAll: () => void;
};

/** The wiring the page ships: every press goes to the session. */
export function bindView(session: MeasureSession, snapshot: MeasureSnapshot, live: boolean): MeasureViewProps {
  return {
    snapshot,
    live,
    onPlace: (place) => session.setPlace(place),
    onStart: () => void session.start(),
    onStop: () => session.stop(),
    onRetake: (index) => void session.retake(index),
    onDelete: (index) => void session.remove(index),
    onDeleteAll: () => void session.removeAll(),
  };
}

export default function MeasureView(props: MeasureViewProps) {
  const { snapshot, live } = props;
  const { data, place, phase } = snapshot;
  if (!data) {
    return <p className="muted">{snapshot.loadError ?? "Yükleniyor…"}</p>;
  }
  const total = data.sentences.length;
  const done = doneIndices(data, place);
  const currentIndex = phase === "idle" ? nextIndex(data, place) : snapshot.target;
  const current = data.sentences.find((sentence) => sentence.index === currentIndex) ?? null;
  const idle = phase === "idle";

  return (
    <section>
      <ul data-measure-head="">
        {headLines(data).map((line) => (
          <li key={line}>{line}</li>
        ))}
      </ul>
      {snapshot.loadError ? <p className="error">{snapshot.loadError}</p> : null}

      <div role="group" aria-label="Kayıt yeri" style={{ display: "flex", gap: 8, margin: "12px 0" }}>
        {data.places.map((option) => (
          <button
            key={option}
            type="button"
            aria-pressed={option === place}
            disabled={!idle}
            onClick={() => props.onPlace(option)}
          >
            {PLACE_LABEL[option]}
          </button>
        ))}
      </div>

      {current ? (
        <div data-measure-current={current.index} style={{ margin: "16px 0" }}>
          <p className="muted">{counterText(current.index, total)}</p>
          <p style={{ fontSize: "1.8em", lineHeight: 1.3, margin: "8px 0" }}>{current.text}</p>
        </div>
      ) : (
        <p>{allDoneText(total, place)}</p>
      )}

      <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
        {phase === "recording" ? (
          <button type="button" onClick={props.onStop}>
            Bitir
          </button>
        ) : (
          <button type="button" disabled={live || !idle || !current || snapshot.busy} onClick={props.onStart}>
            Kaydı başlat
          </button>
        )}
        {phase === "recording" ? <span>{RECORDING_TR}</span> : null}
        {phase === "uploading" ? <span>{UPLOADING_TR}</span> : null}
      </div>
      {live ? <p data-measure-live="">{LIVE_REASON_TR}</p> : null}
      {snapshot.notice ? (
        <p role="status" data-measure-notice="">
          {snapshot.notice}
        </p>
      ) : null}

      <ol style={{ paddingLeft: 24 }}>
        {data.sentences.map((sentence) => {
          const item = recordingFor(data, place, sentence.index);
          const isDone = done.has(sentence.index);
          return (
            <li key={sentence.index} data-measure-row={sentence.index} data-measure-done={isDone}>
              <span>{sentence.text}</span>{" "}
              <span className="muted">{isDone ? "kaydedildi" : "kaydedilmedi"}</span>
              {item ? (
                <>
                  {" · "}
                  <span>{secondsText(item.audio_ms)}</span>
                  {" · "}
                  <span>{chromeText(item)}</span>{" "}
                  <button
                    type="button"
                    disabled={live || !idle || snapshot.busy}
                    onClick={() => props.onRetake(sentence.index)}
                  >
                    Tekrar
                  </button>{" "}
                  <button type="button" disabled={!idle || snapshot.busy} onClick={() => props.onDelete(sentence.index)}>
                    Sil
                  </button>
                </>
              ) : null}
            </li>
          );
        })}
      </ol>

      <button type="button" disabled={!idle || snapshot.busy} onClick={props.onDeleteAll}>
        Ölçüm kayıtlarını sil
      </button>
    </section>
  );
}
