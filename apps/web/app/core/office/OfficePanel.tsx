/** The right panel for the clicked seat. Everything shown comes from `buildPanel`. */

import type { ModelId, ModelRole } from "./officeApi";
import { NEXT_RUN_NOTE, type Panel, type PanelModel } from "./officeModel";

export type ChooseModel = (role: ModelRole, model: ModelId) => void;

function ModelSelector({
  model,
  notice,
  onChoose,
}: {
  model: PanelModel;
  notice: string | null;
  onChoose?: ChooseModel;
}) {
  return (
    <section data-panel="model">
      <label>
        Model{" "}
        <select
          value={model.value}
          onChange={(event) => onChoose?.(model.role, event.target.value as ModelId)}
        >
          {model.options.map((option) => (
            <option key={option.id} value={option.id} disabled={option.disabled}>
              {option.name}
            </option>
          ))}
        </select>
      </label>
      {model.lowered && <p role="status">{model.lowered}</p>}
      {model.shared && <p className="muted">{model.shared}</p>}
      {model.refusal && <p className="muted">{model.refusal}</p>}
      <p className="muted">{NEXT_RUN_NOTE}</p>
      {notice && <p role="alert">{notice}</p>}
    </section>
  );
}

export default function OfficePanel({
  panel,
  modelNotice = null,
  onChooseModel,
}: {
  panel: Panel | null;
  modelNotice?: string | null;
  onChooseModel?: ChooseModel;
}) {
  if (!panel) {
    return (
      <aside className="office-panel" data-office="panel">
        <p className="muted">Ayrıntı için bir koltuğa tıklayın.</p>
      </aside>
    );
  }
  return (
    <aside className="office-panel" data-office="panel" data-panel-seat={panel.seat}>
      <h2>{panel.role}</h2>
      <p className="muted">Durum: {panel.stateText}</p>
      {panel.model && (
        <ModelSelector model={panel.model} notice={modelNotice} onChoose={onChooseModel} />
      )}
      {panel.runs.length > 0 && (
        <section data-panel="runs">
          <h3>Koşan işler ({panel.runs.length})</h3>
          <ol className="office-runs">
            {panel.runs.map((run, index) => (
              <li key={`${index}-${run.title}`}>
                {run.title} · <span className="muted">{run.since}</span>
              </li>
            ))}
          </ol>
        </section>
      )}
      {panel.task ? (
        <section data-panel="card">
          <p className="muted">İşin durumu: {panel.task.stateText}</p>
          <h3>{panel.task.title}</h3>
          {panel.task.since && <p className="muted">Başladı: {panel.task.since}</p>}
          {panel.reason && <p role="status">Neden: {panel.reason}</p>}
          {panel.outcome && <p>Son rapor: {panel.outcome}</p>}
          {/* The card text is written for the agents, in English; the owner read its "-> RED."
              as the task's verdict (ADR-0241 addendum). Closed, whole, never removed. */}
          <details>
            <summary>Ajanlar için yazılmış kart metni (İngilizce, teknik)</summary>
            <p className="muted">
              Bu metindeki RED / GREEN / PASS / FAIL sözcükleri ajanlara verilmiş test
              talimatlarıdır, işin sonucu değil. İşin sonucu yukarıdaki durumdur.
            </p>
            <p>
              <strong>Hedef</strong>
              <br />
              {panel.task.goal}
            </p>
            <p>
              <strong>Kabul</strong>
              <br />
              {panel.task.acceptance}
            </p>
            {panel.task.evidence && (
              <p>
                <strong>Beklenen kanıt</strong>
                <br />
                {panel.task.evidence}
              </p>
            )}
            {panel.agentNote && (
              <p>
                <strong>Proje Yöneticisinin notu</strong>
                <br />
                {panel.agentNote}
              </p>
            )}
          </details>
        </section>
      ) : (
        <p className="muted">Bu koltuğun şu an bir işi yok.</p>
      )}
      {panel.reportLines.length > 0 && (
        <section data-panel="report">
          <h3>Son rapor</h3>
          <pre className="office-report" tabIndex={0}>
            {panel.reportLines.join("\n")}
          </pre>
        </section>
      )}
      {panel.branch && (
        <p className="muted">
          Dal: <code>{panel.branch}</code>
        </p>
      )}
      {panel.sha && (
        <p className="muted">
          sha: <code title={panel.shaFull ?? undefined}>{panel.sha}</code>
        </p>
      )}
    </aside>
  );
}
