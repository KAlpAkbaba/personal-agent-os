"use client";

/**
 * The Onay Merkezi's third list, "Dene" (TEAM_PROTOCOL 3a, ADR-0258): every released task's
 * undecided trial - the sentence to say, the machine, what must be seen or heard - with Oldu
 * and Olmadı. Olmadı asks "Ne oldu?" first; the words are required, as on the server. Nothing
 * here writes a proof: "Oldu" is recorded and the lead writes the PROVEN_REAL line.
 */

import { useState } from "react";

import { decideTrial, type OpenTrial } from "./approvalsApi";

export const SAID_MAX_CHARS = 500;
const SAID_REQUIRED = "Ne olduğunu yazın; boş gönderilemez.";
const SAID_TOO_LONG = `En çok ${SAID_MAX_CHARS} karakter yazılabilir.`;

/** `open`: the two buttons; `asking`: the "Ne oldu?" box; `saved` / `failed`: the server took it. */
export type TrialRowState = {
  phase: "open" | "asking" | "saved" | "failed";
  alert?: string;
  said?: string;
  fixTaskId?: string | null;
};

/**
 * "MAIL" -> "MAIL'de": the locative by the last vowel and the last letter. A machine name is
 * a host name read as English, so it is lowered without the Turkish dotless i. A phrase (a
 * space or an apostrophe in it) was written for the owner and is shown as it is.
 */
export function machineLabel(machine: string): string {
  const name = machine.trim();
  if (/[\s']/.test(name)) return name;
  const lower = name.toLowerCase();
  const vowels = lower.match(/[aeıioöuü]/g);
  const last = vowels?.[vowels.length - 1];
  const back = last !== undefined && "aıou".includes(last);
  const hard = "fstkçşhp".includes(lower.slice(-1));
  return `${name}'${hard ? "t" : "d"}${back ? "a" : "e"}`;
}

/** The click: refuse missing or overlong words here, otherwise post and say what came back. */
export async function submitTrial(
  open: OpenTrial,
  verdict: "oldu" | "olmadi",
  said: string,
): Promise<TrialRowState> {
  const words = said.trim();
  const back: TrialRowState["phase"] = verdict === "olmadi" ? "asking" : "open";
  if (verdict === "olmadi" && words === "") return { phase: "asking", said, alert: SAID_REQUIRED };
  if (words.length > SAID_MAX_CHARS) return { phase: back, said, alert: SAID_TOO_LONG };
  try {
    const result = await decideTrial(open, verdict, words === "" ? null : words);
    if (!result.ok) return { phase: back, said, alert: result.message };
    return verdict === "oldu" ? { phase: "saved" } : { phase: "failed", fixTaskId: result.fix_task_id };
  } catch (failure) {
    return { phase: back, said, alert: failure instanceof Error ? failure.message : "gönderilemedi" };
  }
}

export function TrialRow({
  open,
  locked,
  initial,
}: {
  open: OpenTrial;
  locked: boolean;
  /** The row's starting state; the tests render each state through it. */
  initial?: TrialRowState;
}) {
  const [state, setState] = useState<TrialRowState>(initial ?? { phase: "open" });
  const [said, setSaid] = useState(initial?.said ?? "");
  const [busy, setBusy] = useState(false);
  const { trial } = open;
  const held = busy || locked;
  const key = `${open.task_id} ${trial.id}`;

  const send = async (verdict: "oldu" | "olmadi") => {
    setBusy(true);
    setState(await submitTrial(open, verdict, said));
    setBusy(false);
  };

  return (
    <li className="detail-row" data-trial={key}>
      <span className="detail-head">“{trial.sentence}”</span>
      <span>{machineLabel(trial.machine)}</span>
      <span>{trial.expect}</span>
      <small className="muted" style={{ flexBasis: "100%" }}>
        {open.title}
      </small>
      {state.phase === "saved" && <span role="status">Kaydedildi — kanıt satırını lead yazar</span>}
      {state.phase === "failed" && (
        <span role="status">Düzeltme işi kuyruğa girdi: {state.fixTaskId ?? "?"}</span>
      )}
      {state.phase === "open" && (
        <>
          <button type="button" disabled={held} onClick={() => void send("oldu")}>
            Oldu
          </button>
          <button type="button" disabled={held} onClick={() => setState({ phase: "asking" })}>
            Olmadı
          </button>
        </>
      )}
      {state.phase === "asking" && (
        <>
          <label>
            Ne oldu?
            <textarea
              aria-label={`Ne oldu? ${key}`}
              required
              maxLength={SAID_MAX_CHARS}
              value={said}
              onChange={(event) => setSaid(event.target.value)}
            />
          </label>
          <button type="button" disabled={held} onClick={() => void send("olmadi")}>
            Gönder
          </button>
          <button type="button" disabled={busy} onClick={() => setState({ phase: "open" })}>
            Vazgeç
          </button>
        </>
      )}
      {state.alert && <span role="alert">{state.alert}</span>}
    </li>
  );
}

export default function TrialsList({ trials, locked }: { trials: OpenTrial[]; locked: boolean }) {
  return (
    <section data-section="trials">
      <h2>Dene ({trials.length})</h2>
      {trials.length === 0 ? (
        <p className="muted">Denenecek bir şey yok.</p>
      ) : (
        <ul className="detail-list">
          {trials.map((open) => (
            <TrialRow key={`${open.task_id}-${open.trial.id}`} open={open} locked={locked} />
          ))}
        </ul>
      )}
    </section>
  );
}
