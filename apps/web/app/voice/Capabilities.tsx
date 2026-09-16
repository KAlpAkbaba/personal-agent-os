"use client";

/**
 * B25 req 701: what you can say, on the page where you say it.
 *
 * The audit's lowest score was discoverability, 0.5 out of 5, and this is the middle of it:
 * a voice-first system whose owner has no way to find out what to say to it has a manual
 * nobody wrote. The answer existed — a hundred and thirty tool descriptions, in Turkish,
 * most of them quoting the owner's own sentences so the model could recognise them — and
 * the only reader was the model.
 *
 * So nothing here is written. The list, the grouping and the example sentences all come
 * from `GET /v1/voice/capabilities`, which derives them from the Cloud Core's tool
 * registry. What the owner is told to say is literally what the assistant was told to hear.
 *
 * It lives on `/voice` rather than on a fifteenth page because that is where the owner
 * already goes to talk, and the command palette (req 702/703) searches the same list from
 * everywhere else.
 */

import { useCallback, useState } from "react";

import { LoadedNotice } from "../core/panels/Panel";
import { byFamily, capabilityAnchor, fetchCapabilities } from "../lib/pages/capabilities";
import { useLoaded } from "../lib/pages/useLoaded";

export default function Capabilities() {
  const list = useLoaded(useCallback(() => fetchCapabilities(), []));
  const [only, setOnly] = useState<string | null>(null);

  const value = list.state.kind === "ok" ? list.state.value : null;
  const groups = value ? byFamily(value) : [];
  const shown = only ? groups.filter((group) => group.family.family === only) : groups;

  return (
    <section className="panel" data-panel="capabilities" id="capabilities">
      <h3 className="panel-title">
        <span>Neler diyebilirsiniz?</span>
        {value && (
          <span className="panel-count" data-panel-badge>
            {value.rows.length}
          </span>
        )}
      </h3>

      <LoadedNotice state={list.state} onRetry={list.refresh} />

      {value && (
        <>
          {/* The assistant's own spoken answer, so the page and the voice agree. */}
          <p className="muted" data-capability-speech>
            {value.speech}
          </p>

          <div className="capability-families">
            <button
              type="button"
              className="core-chip"
              aria-pressed={only === null}
              data-capability-family="all"
              onClick={() => setOnly(null)}
            >
              Hepsi
            </button>
            {value.families.map((family) => (
              <button
                key={family.family}
                type="button"
                className="core-chip"
                aria-pressed={only === family.family}
                data-capability-family={family.family}
                onClick={() => setOnly(only === family.family ? null : family.family)}
              >
                {family.familyTr} <span className="muted">{family.count}</span>
              </button>
            ))}
          </div>

          {shown.map((group) => (
            <div key={group.family.family} className="capability-group">
              <h4>{group.family.familyTr}</h4>
              <ul className="capability-list">
                {group.rows.map((row) => (
                  <li key={row.name} id={capabilityAnchor(row.name)} data-capability={row.name}>
                    {row.phrases.length > 0 ? (
                      <span className="capability-phrases">
                        {row.phrases.map((phrase) => (
                          <q key={phrase} data-capability-phrase>
                            {phrase}
                          </q>
                        ))}
                      </span>
                    ) : (
                      // No quoted example in the description: the summary is the only
                      // honest thing to show, and inventing a sentence here would be a
                      // phrase the assistant was never told to listen for.
                      <span className="capability-phrases" data-capability-noexample>
                        {row.summary}
                      </span>
                    )}
                    <span className="muted capability-summary">{row.summary}</span>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </>
      )}
    </section>
  );
}
