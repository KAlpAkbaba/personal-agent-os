/** The right panel for the clicked seat. Everything shown comes from `buildPanel`. */

import type { Panel } from "./officeModel";

export default function OfficePanel({ panel }: { panel: Panel | null }) {
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
      {panel.task ? (
        <section data-panel="card">
          <h3>{panel.task.title}</h3>
          <p className="muted">Durum: {panel.task.state}</p>
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
          {panel.reason && <p role="status">Neden: {panel.reason}</p>}
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
