/**
 * The whole Ofis page body as a pure function of props: top bar, scene, panel, approvals.
 * `page.tsx` owns the data, the poll and the selection; this renders them.
 */

import Link from "next/link";

import OfficePanel, { type ChooseModel } from "./OfficePanel";
import OfficeProgress from "./OfficeProgress";
import OfficeScene from "./OfficeScene";
import type { ModelSetting, OfficeView as Office } from "./officeApi";
import { energyOf } from "./officeEnergy";
import { livenessLines } from "./officeLiveness";
import { buildOffice, buildPanel } from "./officeModel";
import type { TestSeat } from "./officeTestRoom";

export default function OfficeView({
  view,
  selected,
  offline,
  reducedMotion,
  onSelect,
  arriving = [],
  modelNotice = null,
  onChooseModel,
  onToggleFallback,
  testSeats,
}: {
  view: Office;
  selected: string | null;
  offline: boolean;
  reducedMotion: boolean;
  onSelect: (seat: string) => void;
  arriving?: string[];
  /** The server's sentence after a refused setting, under the selector. */
  modelNotice?: string | null;
  onChooseModel?: ChooseModel;
  /** Called with the whole setting, its fallback flipped. */
  onToggleFallback?: (next: ModelSetting) => void;
  /** The test team's five seats (officeTestRoom.tsx), seated on the same office floor as the software team. */
  testSeats?: TestSeat[];
}) {
  const office = buildOffice(view);
  const bar = office.topBar;
  const energy = energyOf(view);
  const panel = selected ? buildPanel(view, selected) : null;
  const liveness = livenessLines(view.agents, view.tasks);
  return (
    <div className="office" data-office="root">
      <div className="office-topbar" data-office="topbar">
        <span>
          Döngü: <strong>{bar.cycleId}</strong>
        </span>
        <span>Başlangıç: {bar.startedAt}</span>
        {bar.account && (
          <span className="office-account" data-office="account">
            Hesap: <strong>{bar.account}</strong>
          </span>
        )}
        <span>{bar.runningAgents}</span>
        <span>{bar.estimated}</span>
        <span>Max limit: {bar.limit}</span>
        <span>{bar.fable}</span>
        <span>{bar.all}</span>
        {view.models && (
          <button
            type="button"
            className="office-toggle"
            aria-pressed={view.models.fallback}
            onClick={() =>
              view.models && onToggleFallback?.({ ...view.models, fallback: !view.models.fallback })
            }
          >
            yedek model: {view.models.fallback ? "açık" : "kapalı"}
          </button>
        )}
        {bar.lowered && <span>{bar.lowered}</span>}
        {bar.asOf && <span className="muted">({bar.asOf})</span>}
        {/* the refusal is said beside the selector when one is open, else here */}
        {modelNotice && !panel?.model && <span role="alert">{modelNotice}</span>}
        {offline && (
          <span className="office-offline" role="status">
            bağlantı yok
          </span>
        )}
      </div>
      <OfficeProgress progress={view.progress} />
      <div className="office-main">
        <OfficeScene
          seats={office.seats}
          selected={selected}
          reducedMotion={reducedMotion}
          onSelect={onSelect}
          arriving={arriving}
          testSeats={testSeats}
        />
        <OfficePanel
          panel={panel}
          modelNotice={modelNotice}
          onChooseModel={onChooseModel}
        />
      </div>
      {liveness.length > 0 && (
        <ul className="office-liveness" data-office="liveness">
          {liveness.map(({ seat, liveness: l }) => (
            <li key={seat} data-kind={l.kind}>
              <strong>{seat}</strong>: {l.label}
            </li>
          ))}
        </ul>
      )}
      <div className="office-energy" data-office="energy" data-level={energy.level}>
        <div className="office-energy-meter">
          <span className="office-energy-text">{energy.text}</span>
          <span
            className="office-energy-track"
            role="meter"
            aria-label="Enerji: kullanım hakkının kalanı"
            aria-valuemin={0}
            aria-valuemax={100}
            {...(energy.percent !== null ? { "aria-valuenow": energy.percent } : {})}
          >
            <span className="office-energy-fill" style={{ width: `${energy.percent ?? 0}%` }} />
          </span>
        </div>
        <span className="office-energy-working">{energy.working}</span>
      </div>
      <section className="office-approvals" data-office="approvals">
        <h2>Onay Merkezi bekleyenleri</h2>
        {office.approvals.length === 0 ? (
          <p className="muted">Bekleyen onay yok.</p>
        ) : (
          <ul>
            {office.approvals.map((a) => (
              <li key={a.taskId}>
                {a.title} · <span className="muted">{a.gate}</span>
              </li>
            ))}
          </ul>
        )}
        <p>
          <Link href="/core/approvals">Onay Merkezi&apos;ni aç →</Link>
        </p>
      </section>
    </div>
  );
}
